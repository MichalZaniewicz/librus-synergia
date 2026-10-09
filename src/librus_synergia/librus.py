"""High-level, typed interface: log in once, get parsed models back.

    async with Librus("1234567u", "password") as librus:
        for grade in await librus.grades():
            print(grade.subject_id, grade.value)

Wraps `LibrusApiClient` (raw JSON) + `parsers` (JSON -> dataclasses) and
handles the session lifecycle: logging in lazily, re-logging in once when
Librus drops the session early, and bootstrapping the separate Wiadomości
(messages) session on first use.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from functools import partial
from types import TracebackType
from typing import Any, TypeVar, cast

import aiohttp

from . import parsers
from ._dates import school_today
from .client import (
    DEFAULT_REQUEST_TIMEOUT,
    LibrusApiClient,
    LibrusSessionData,
    OnSessionUpdate,
    _with_download_deadline,
)
from .exceptions import LibrusError, LibrusSessionExpiredError, LibrusUnexpectedResponseError
from .models import (
    AttachmentFileData,
    AttendanceData,
    AttendanceTypeData,
    BehaviourGradeData,
    ClassData,
    DescriptiveGradeData,
    FreeDayData,
    FullMessageData,
    GradeCategoryData,
    GradeData,
    GradingSystemData,
    HomeworkAssignmentData,
    HomeworkEventData,
    JustificationData,
    LessonData,
    LessonTopicData,
    LibrusData,
    LuckyNumberData,
    MeData,
    MessageData,
    NoteData,
    ParentTeacherConferenceData,
    PointGradeData,
    SchoolData,
    SchoolFileData,
    SchoolNoticeData,
    SchoolTripData,
    StandingLessonData,
    TextGradeData,
)

T = TypeVar("T")

# How long a definite "not available to this account" answer (and the
# `Auth/Subjects` lookup, the `Units` settings, and a kindergarten search
# that found nothing) is kept before asking Librus again.
RECHECK_SECONDS = 24 * 3600
# After a failed login, calls that would need the password again within
# this many seconds get the same error instead of trying it again.
LOGIN_FAILURE_BACKOFF_SECONDS = 60
# Requests one `Librus` instance runs at the same time.
MAX_CONCURRENT_REQUESTS = 6


def _now() -> float:
    """Monotonic clock for the caches below (a function so tests can move it)."""
    return time.monotonic()


def _is_refusal(err: LibrusError) -> bool:
    """Whether Librus definitely refused a module for this account: a 403,
    or a 401 "Insufficient scopes" / 404 / 405 (`LibrusUnexpectedResponseError`).
    A network error, a 5xx or a dead session is not a refusal."""
    if isinstance(err, LibrusSessionExpiredError):
        return err.status_code == 403
    if isinstance(err, LibrusUnexpectedResponseError):
        return err.status_code in (401, 403, 404, 405)
    return False


def _fresh(at: float | None, seconds: float = RECHECK_SECONDS) -> bool:
    """Whether a cache entry stamped at `at` is younger than `seconds`."""
    return at is not None and _now() - at < seconds


def week_start_of(day: date) -> date:
    """The Monday of `day`'s week - what Librus's `Timetables` expects."""
    return day - timedelta(days=day.weekday())


def _first_error(group: BaseExceptionGroup[BaseException]) -> BaseException:
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            return _first_error(error)
        return error
    return group  # pragma: no cover - a group is never empty


async def _gather_all(*awaitables: Awaitable[Any]) -> list[Any]:
    """Like `asyncio.gather`, but the first failure cancels the rest (a
    `TaskGroup`) - no orphaned requests keep running - and is raised as
    itself, not wrapped in an `ExceptionGroup`."""

    async def run(awaitable: Awaitable[Any]) -> Any:
        return await awaitable

    error: BaseException | None = None
    tasks: list[asyncio.Task[Any]] = []
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(run(awaitable)) for awaitable in awaitables]
    except BaseExceptionGroup as errors:
        error = _first_error(errors)
    if error is not None:
        raise error
    return [task.result() for task in tasks]


async def _gather2[A, B](first: Awaitable[A], second: Awaitable[B]) -> tuple[A, B]:
    a, b = await _gather_all(first, second)
    return cast(A, a), cast(B, b)


async def _empty_payload() -> dict[str, Any]:
    return {}


def _references_comments(items: Any) -> bool:
    """Whether any item's `Comments` holds an id that needs the separate
    comments endpoint (an embedded `{"Text": ...}` doesn't)."""
    if not isinstance(items, list):
        return False
    for item in items:
        comments = item.get("Comments") if isinstance(item, dict) else None
        if isinstance(comments, list) and any(
            not (isinstance(entry, dict) and entry.get("Text")) for entry in comments
        ):
            return True
    return False


def _has_unknown(ids: list[Any], known: dict[Any, Any], ignore: set[Any]) -> bool:
    """Whether an id in `ids` is missing from `known` (and not in `ignore`)."""
    for value in ids:
        if value is None or value in known or value in ignore:
            continue
        number = parsers.as_int(value)
        if number is not None and (number in known or number in ignore):
            continue
        return True
    return False


class Librus:
    """A signed-in Librus Synergia account (one parent/student login).

    Pass `session` to reuse your own `aiohttp.ClientSession` (it must not be
    shared with another account - see `LibrusApiClient`). Otherwise one is
    created the first time it's needed (the first request, or the first
    access to `client` / `session_data`) and closed by `close()` /
    `__aexit__`; it allows `MAX_CONCURRENT_REQUESTS` connections to one host.

    Pass `session_data` (from a previous run's `librus.session_data`) to
    resume without a fresh login and keep Librus's long-lived device cookie,
    which is what keeps logins captcha-free over time.

    `request_timeout` limits every data and login request (default 30 s, 10
    s to connect; None = the session's own). At most
    `max_concurrent_requests` requests run at once.

    `cache_reference_data=True` keeps lookups that rarely change (subjects,
    teachers, classrooms, categories, attendance types, the `Lessons` map,
    school and class) for `reference_ttl` seconds (default a day) instead of
    fetching them every time; `fetch_all()` fetches one again early when the
    data mentions an id it doesn't know. Off by default.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
        session_data: LibrusSessionData | None = None,
        on_session_update: OnSessionUpdate | None = None,
        request_timeout: aiohttp.ClientTimeout | None = DEFAULT_REQUEST_TIMEOUT,
        max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
        cache_reference_data: bool = False,
        reference_ttl: float = RECHECK_SECONDS,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._owns_session = session is None
        self._session_data = session_data
        self._on_session_update = on_session_update
        self._request_timeout = request_timeout
        self._max_concurrent = max_concurrent_requests
        self._client: LibrusApiClient | None = None
        self._login_lock = asyncio.Lock()
        # The last failed login, kept for LOGIN_FAILURE_BACKOFF_SECONDS.
        self._login_failure: LibrusError | None = None
        self._login_failed_at: float | None = None
        self._requests = asyncio.Semaphore(max_concurrent_requests)
        # Wiadomości: whether the module is there, the client's
        # `login_count` it was bootstrapped for, and how many bootstraps ran.
        self._messages_lock = asyncio.Lock()
        self._messages_available: bool | None = None
        self._messages_login = -1
        self._messages_bootstraps = 0
        # Kindergarten accounts (see `kindergartener_id`): a found child is
        # kept; a search that found nothing is repeated after a day.
        self._kindergarten_lock = asyncio.Lock()
        self._kindergarten_checked_at: float | None = None
        # The child's LID for the new descriptive grading (see
        # `student_identifier`), and whether that module answers at all.
        # A refusal is kept for `RECHECK_SECONDS` (the time it was seen).
        self._student_identifier_lock = asyncio.Lock()
        self._student_identifier: str | None = None
        self._student_identifier_checked = False
        self._student_identifier_refused_at: float | None = None
        self._partial_grades_unavailable_at: float | None = None
        # Subject LID -> subject id (`Auth/Subjects`), refreshed daily.
        self._auth_subjects: dict[str, int] | None = None
        self._auth_subjects_at: float | None = None
        # A static school setting - read once per instance (a refusal keeps
        # the default for `RECHECK_SECONDS`).
        self._grading_system: GradingSystemData | None = None
        self._grading_system_refused_at: float | None = None
        self._kindergarten_lid: str | None = None
        self._kindergarten_group_id: str | None = None
        # Optional modules the account was refused (name -> when), kept for
        # `RECHECK_SECONDS`.
        self._refused_at: dict[str, float] = {}
        # `Units.GradesSettings.PointGradesEnabled`, read once a day.
        self._point_grades_flag: bool | None = None
        self._units_at: float | None = None
        # Reference data (`cache_reference_data`): key -> (time, value), and
        # ids that stayed unknown even right after fetching a key again.
        self._cache_reference_data = cache_reference_data
        self._reference_ttl = reference_ttl
        # key -> (time, value, load number): the number tells a copy loaded
        # during the current snapshot from an older one.
        self._reference_cache: dict[str, tuple[float, Any, int]] = {}
        self._reference_loads = 0
        self._still_unknown: dict[str, set[Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Librus:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
            self._client = None
            self._messages_available = None
            self._messages_login = -1

    @property
    def client(self) -> LibrusApiClient:
        """The underlying low-level client, for endpoints without a
        high-level method here."""
        if self._client is None:
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(limit_per_host=self._max_concurrent)
                )
            self._client = LibrusApiClient(
                self._session,
                self._username,
                on_session_update=self._on_session_update,
                request_timeout=self._request_timeout,
            )
            self._client.import_session(self._session_data)
        return self._client

    @property
    def session_data(self) -> LibrusSessionData:
        """The current session's cookies, to persist and pass back as
        `session_data=` next time."""
        return self.client.export_session()

    async def login(self, *, force: bool = False) -> None:
        """Sign in now (normally not needed - every call signs in lazily).
        Raises `LibrusAuthError` subclasses on bad credentials/captcha, and
        `LibrusConnectionError` when Librus can't be reached.

        A failed login is remembered for `LOGIN_FAILURE_BACKOFF_SECONDS`
        (60 s): until then, a call that would need the password again gets
        the same error without another attempt."""
        await self._sign_in(force=force)

    async def _sign_in(self, *, force: bool, since: int | None = None) -> None:
        """`login()`. With `since` (a `login_count` read before a request
        was rejected), a forced login is skipped when another one already
        happened meanwhile - every request that hit the same dead session
        then retries on that one login instead of logging in again."""
        client = self.client
        async with self._login_lock:
            if since is not None and client.login_count != since:
                return
            needs_password = force or not client.is_session_valid()
            if (
                needs_password
                and self._login_failure is not None
                and _fresh(self._login_failed_at, LOGIN_FAILURE_BACKOFF_SECONDS)
            ):
                raise self._login_failure
            try:
                await client.async_ensure_session_valid(self._password, force=force)
            except LibrusError as err:
                self._login_failure, self._login_failed_at = err, _now()
                raise
            self._login_failure = None

    async def _limited(self, fetch: Callable[[], Awaitable[T]]) -> T:
        """Run one request within the `max_concurrent_requests` limit."""
        async with self._requests:
            return await fetch()

    async def _call(self, fetch: Callable[[], Awaitable[T]]) -> T:
        """Run one API call, re-logging in once if Librus rejected the
        session (HTTP 401) earlier than expected. Requests rejected by the
        same dead session share one login."""
        await self.login()
        since = self.client.login_count
        try:
            return await self._limited(fetch)
        except LibrusSessionExpiredError as err:
            if err.status_code == 403:
                raise
            await self._sign_in(force=True, since=since)
            return await self._limited(fetch)

    async def _messages_ready(self) -> bool:
        """Bootstrap the Wiadomości session when it wasn't yet, or a password
        login happened since (that replaces the session it depends on).
        False when the school has no messages module."""
        async with self._messages_lock:
            if self._messages_available is None or self._messages_login != self.client.login_count:
                await self._bootstrap_messages_locked()
            return bool(self._messages_available)

    async def _bootstrap_messages(self, *, since: int) -> None:
        """Bootstrap again, unless another call already did since `since`
        (a count of bootstraps)."""
        async with self._messages_lock:
            if self._messages_bootstraps == since:
                await self._bootstrap_messages_locked()

    async def _bootstrap_messages_locked(self) -> None:
        login = self.client.login_count
        self._messages_available = await self.client.async_bootstrap_messages()
        self._messages_login = login
        self._messages_bootstraps += 1

    async def _call_messages(self, fetch: Callable[[], Awaitable[T]]) -> T | None:
        """Like `_call`, for the separate Wiadomości session. Returns None
        when the school has no messages module. The Wiadomości session dies
        independently (and more often) than the main one, so a rejected
        session (`LibrusSessionExpiredError`) or an HTTP error status first
        gets the Wiadomości session alone set up again (one request) and a
        retry; only if that fails too, a fresh password login + bootstrap +
        retry. A network error or timeout (including a download's deadline,
        a 429 and a 502/503/504), a 404 and an odd answer without an error
        status (not JSON, a link outside the sandbox, a failed download key)
        are raised as they are - a fresh login can't fix them."""
        await self.login()
        rebootstrapped = relogged = False
        while True:
            if not await self._messages_ready():
                return None
            since_login = self.client.login_count
            since_bootstrap = self._messages_bootstraps
            try:
                return await self._limited(fetch)
            except LibrusSessionExpiredError:
                if relogged:
                    raise
            except LibrusUnexpectedResponseError as err:
                # A 404 means "no such mailbox for this account" (confirmed
                # live for substitutions/alerts), not a dead session. A 401
                # "Insufficient scopes" (only raised for the data gateway)
                # can't change with a fresh login either.
                if err.status_code is None or err.status_code in (401, 404) or relogged:
                    raise
            if not rebootstrapped:
                rebootstrapped = True
                await self._bootstrap_messages(since=since_bootstrap)
            else:
                relogged = True
                await self._sign_in(force=True, since=since_login)

    # ------------------------------------------------------------------
    # Caches of rarely changing data
    # ------------------------------------------------------------------

    async def _reference(self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        """`load()`, kept for `reference_ttl` when `cache_reference_data` is
        on. A failed refresh falls back to the expired copy, if any."""
        if not self._cache_reference_data:
            return await load()
        entry = self._reference_cache.get(key)
        if entry is not None and _fresh(entry[0], self._reference_ttl):
            return cast(T, entry[1])
        try:
            value = await load()
        except LibrusError:
            if entry is None:
                raise
            return cast(T, entry[1])
        self._store_reference(key, value)
        return value

    def _store_reference(self, key: str, value: Any) -> None:
        self._reference_loads += 1
        self._reference_cache[key] = (_now(), value, self._reference_loads)

    def _forget_references(self, *keys: str) -> None:
        for key in keys:
            self._reference_cache.pop(key, None)
            self._still_unknown.pop(key, None)

    async def _module(self, name: str, load: Callable[[], Awaitable[T]], empty: T) -> T:
        """An optional module: when Librus refuses it (403, 404, 405 or a 401
        "Insufficient scopes"), `empty` comes back and Librus isn't asked
        again for `RECHECK_SECONDS`. Other errors are raised."""
        if _fresh(self._refused_at.get(name)):
            return empty
        try:
            result = await load()
        except LibrusError as err:
            if not _is_refusal(err):
                raise
            self._refused_at[name] = _now()
            return empty
        self._refused_at.pop(name, None)
        return result

    # ------------------------------------------------------------------
    # Identity / school
    # ------------------------------------------------------------------

    async def me(self) -> MeData:
        return parsers.parse_me(await self._call(self.client.async_get_me))

    async def student_number(self) -> int | None:
        """Class register number ("Nr w dzienniku"): from the student's own
        `Users` record (`Me.Account.UserId`), falling back to Synergia's
        `informacja` web page."""
        me = await self._call(self.client.async_get_me)
        user_id = ((me.get("Me") or {}).get("Account") or {}).get("UserId")
        if user_id:
            try:
                number = parsers.parse_user_class_register_number(
                    await self._call(lambda: self.client.async_get_user(user_id))
                )
            except LibrusError:
                number = None
            if number is not None:
                return number
        return parsers.parse_student_number(
            await self._call(self.client.async_get_student_info_page)
        )

    async def school(self) -> SchoolData | None:
        return await self._reference(
            "school", lambda: self._parsed(self.client.async_get_schools, parsers.parse_school)
        )

    async def school_class(self) -> ClassData | None:
        """The class. For a kindergarten account (once detected), the
        kindergarten group instead."""
        return await self._reference("school_class", self._load_school_class)

    async def _load_school_class(self) -> ClassData | None:
        if self._kindergarten_group_id is not None:
            group_id = self._kindergarten_group_id
            group = parsers.parse_kindergarten_group(
                await self._probe(lambda: self.client.async_get_kindergarten_group(group_id))
            )
            if group is not None:
                return group
        return parsers.parse_class(await self._call(self.client.async_get_classes))

    async def _parsed(
        self, fetch: Callable[[], Awaitable[dict[str, Any]]], parse: Callable[[dict[str, Any]], T]
    ) -> T:
        return parse(await self._call(fetch))

    async def subjects(self) -> dict[int | str, str]:
        """Subject id -> name. Grades/lessons carry only the id. Includes
        kindergarten activity names once a kindergarten account is detected."""
        return await self._reference("subjects", self._load_subjects)

    async def _load_subjects(self) -> dict[int | str, str]:
        payload = await self._call(self.client.async_get_subjects)
        result: dict[int | str, str] = {
            k: v for k, v in parsers.parse_id_name_map(payload, ("Subjects",)).items()
        }
        if self._kindergarten_lid is not None:
            result.update(
                parsers.parse_kindergarten_activity_types(
                    await self._probe(self.client.async_get_kindergarten_activity_types)
                )
            )
        return result

    async def teachers(self) -> dict[int | str, str]:
        return await self._reference("teachers", self._load_teachers)

    async def _load_teachers(self) -> dict[int | str, str]:
        payload = await self._call(self.client.async_get_teachers)
        result: dict[int | str, str] = {
            k: v for k, v in parsers.parse_id_name_map(payload, ("Users", "Teachers")).items()
        }
        if self._kindergarten_lid is not None:
            result.update(parsers.parse_kindergarten_teachers(payload))
        return result

    async def classrooms(self) -> dict[int | str, str]:
        return await self._reference("classrooms", self._load_classrooms)

    async def _load_classrooms(self) -> dict[int | str, str]:
        payload = await self._call(self.client.async_get_classrooms)
        result: dict[int | str, str] = {
            k: v for k, v in parsers.parse_id_name_map(payload, ("Classrooms",)).items()
        }
        if self._kindergarten_lid is not None:
            result.update(
                parsers.parse_kindergarten_classrooms(
                    await self._probe(self.client.async_get_kindergarten_classrooms)
                )
            )
        return result

    # ------------------------------------------------------------------
    # Kindergarten (przedszkole) accounts
    # ------------------------------------------------------------------

    async def _try(self, fetch: Callable[[], Awaitable[Any]]) -> dict[str, Any] | None:
        """One best-effort request: None when it failed (any Librus error,
        or an answer that isn't a JSON object)."""
        try:
            result = await self._call(fetch)
        except LibrusError:
            return None
        return result if isinstance(result, dict) else None

    async def _probe(self, fetch: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
        """One best-effort request: any Librus error becomes `{}`."""
        return await self._try(fetch) or {}

    async def kindergartener_id(self) -> str | None:
        """The child's `LID-AUTH-USER-...` identifier on a kindergarten
        account, or None for a regular one.

        Kindergarten accounts get HTTP 403 from `Timetables`; their timetable
        lives in a separate API keyed by this identifier. `timetable()` calls
        this automatically after such a 403, so you rarely need it directly.
        Candidates come from `Me`, `Auth/TokenInfo` (+ `Auth/UserInfo`) and
        `Users/<id>`; the one whose kindergarten timetable has entries wins.
        A found child is kept for this instance; a search that found nothing
        (or whose requests failed) is repeated at most once a day
        (`RECHECK_SECONDS`). Never raises a `LibrusError`."""
        async with self._kindergarten_lock:
            if self._kindergarten_lid is not None:
                return self._kindergarten_lid
            if _fresh(self._kindergarten_checked_at):
                return None
            self._kindergarten_checked_at = _now()
            candidates: dict[str, None] = {}

            def add(values: list[str]) -> None:
                for value in values:
                    candidates.setdefault(value, None)

            me_payload = await self._probe(self.client.async_get_me)
            raw_me = me_payload.get("Me")
            me: dict[str, Any] = raw_me if isinstance(raw_me, dict) else {}
            add(parsers.collect_lid_user_identifiers(me.get("User")))
            add(parsers.collect_lid_user_identifiers(me))

            token_lid = parsers.extract_token_user_identifier(
                await self._probe(self.client.async_get_token_info)
            )
            if token_lid:
                add([token_lid])
                add(
                    parsers.collect_lid_user_identifiers(
                        await self._probe(lambda: self.client.async_get_user_info(token_lid))
                    )
                )

            raw_account = me.get("Account")
            account: dict[str, Any] = raw_account if isinstance(raw_account, dict) else {}
            for numeric_id in dict.fromkeys(
                value
                for value in (account.get("UserId"), account.get("Id"))
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            ):
                add(
                    parsers.collect_lid_user_identifiers(
                        await self._probe(partial(self.client.async_get_user, numeric_id))
                    )
                )

            today = school_today()
            for lid in list(candidates)[:6]:
                payload = await self._probe(
                    partial(
                        self.client.async_get_kindergarten_timetable,
                        lid,
                        today - timedelta(days=30),
                        today + timedelta(days=60),
                    )
                )
                entries = payload.get("timetableEntries")
                if not isinstance(entries, list) or not entries:
                    continue
                self._kindergarten_lid = lid
                child = await self._probe(partial(self.client.async_get_kindergartener, lid))
                child_data = child.get("data")
                group_id = (
                    child_data.get("groupIdentifier") if isinstance(child_data, dict) else None
                )
                self._kindergarten_group_id = (
                    group_id if isinstance(group_id, str) and group_id else None
                )
                # Cached lookups were made without the kindergarten names.
                self._forget_references("subjects", "teachers", "classrooms", "school_class")
                break
            return self._kindergarten_lid

    # ------------------------------------------------------------------
    # Grades / behaviour
    # ------------------------------------------------------------------

    async def grades(self) -> list[GradeData]:
        """All grades, with teacher comments resolved to text (the comments
        list is only fetched when a grade has a comment; if that lookup
        fails, the grades still come back, just without comment text). Turn
        a grade's `value` ("4+", "bz", ...) into a number with
        `parsers.parse_grade_value`."""
        grades = await self._call(self.client.async_get_grades)
        comments: dict[str, Any] = {}
        if _references_comments(grades.get("Grades")):
            comments = await self._probe(self.client.async_get_grade_comments)
        return parsers.parse_grades(grades, parsers.parse_comment_text_map(comments))

    async def grade_categories(self) -> dict[int, GradeCategoryData]:
        return await self._reference(
            "grade_categories",
            lambda: self._parsed(
                self.client.async_get_grade_categories, parsers.parse_grade_categories
            ),
        )

    async def descriptive_grades(self) -> list[DescriptiveGradeData]:
        """Descriptive grades with their skill names and comments. Each
        lookup is only fetched when a grade needs it, and either may fail
        without losing the grades."""
        payload = await self._call(self.client.async_get_descriptive_grades)
        items = payload.get("Grades")
        if not items:
            return []
        needs_skills = isinstance(items, list) and any(
            isinstance(item, dict) and item.get("Skill") for item in items
        )
        skills, comments = await _gather2(
            self._probe(self.client.async_get_descriptive_grade_skills)
            if needs_skills
            else _empty_payload(),
            self._probe(self.client.async_get_descriptive_grade_comments)
            if _references_comments(items)
            else _empty_payload(),
        )
        return parsers.parse_descriptive_grades(
            payload,
            parsers.parse_descriptive_skills(skills),
            parsers.parse_comment_text_map(comments),
        )

    async def student_identifier(self) -> str | None:
        """The child's LID (`Auth/UserInfo/<token user>` ->
        `IdentifierOfStudentAssignedWithUser`); None when Librus doesn't
        give one. The answer is kept for this instance once Librus has
        actually answered both lookups. A definite refusal (403, 404, 405 or
        a 401 "Insufficient scopes") is kept for a day (`RECHECK_SECONDS`);
        a failed lookup (network, 5xx, ...) is tried again on the next
        call. Never raises a `LibrusError`."""
        async with self._student_identifier_lock:
            if self._student_identifier_checked:
                return self._student_identifier
            if _fresh(self._student_identifier_refused_at):
                return None
            try:
                token_info = await self._call(self.client.async_get_token_info)
                token_lid = parsers.extract_token_user_identifier(token_info)
                if token_lid:
                    user_info = await self._call(lambda: self.client.async_get_user_info(token_lid))
                    self._student_identifier = parsers.extract_student_identifier(user_info)
            except LibrusError as err:
                if _is_refusal(err):
                    self._student_identifier_refused_at = _now()
                return None
            self._student_identifier_refused_at = None
            self._student_identifier_checked = True
            return self._student_identifier

    async def partial_grades(self) -> list[DescriptiveGradeData]:
        """Grades from the new descriptive grading (grade 1 at some schools
        from 2026), as `DescriptiveGradeData` with `source="partial"` -
        see `parsers.parse_partial_grades`. Empty when Librus has none or
        no child LID is known. When the module isn't available to this
        account (HTTP 401 "Insufficient scopes", 403, 404 or 405), that is
        remembered and this instance asks again only after a day
        (`RECHECK_SECONDS`). The `Auth/Subjects` lookup is kept for a day
        too."""
        if _fresh(self._partial_grades_unavailable_at):
            return []
        student = await self.student_identifier()
        if not student:
            return []
        try:
            payload = await self._call(lambda: self.client.async_get_partial_grades(student))
        except LibrusError as err:
            # `_call` already retried a real 401 once; a 403 or an
            # "Insufficient scopes" 401 means the module isn't there.
            if not _is_refusal(err):
                raise
            self._partial_grades_unavailable_at = _now()
            return []
        self._partial_grades_unavailable_at = None
        if not payload.get("data"):
            return []
        return parsers.parse_partial_grades(payload, await self._subjects_by_lid())

    async def _subjects_by_lid(self) -> dict[str, int]:
        """`Auth/Subjects` (subject LID -> subject id), kept for
        `RECHECK_SECONDS`. A failed lookup gives `{}` and isn't kept."""
        if self._auth_subjects is None or not _fresh(self._auth_subjects_at):
            payload = await self._try(self.client.async_get_auth_subjects)
            if payload is None:
                return self._auth_subjects or {}
            self._auth_subjects = parsers.parse_auth_subjects(payload)
            self._auth_subjects_at = _now()
        return self._auth_subjects

    async def grading_system(self) -> GradingSystemData:
        """The school's grade scale settings ("+" / "-" values, whether 0
        counts). Pass it to `parsers.parse_grade_value`. Read once per
        instance (a failed read is tried again next time). When the school
        refuses it (403, 404, 405 or a 401 "Insufficient scopes"), the
        defaults (`GradingSystemData()`) are returned and Librus is asked
        again only after a day (`RECHECK_SECONDS`)."""
        if self._grading_system is not None:
            return self._grading_system
        if _fresh(self._grading_system_refused_at):
            return GradingSystemData()
        try:
            payload = await self._call(self.client.async_get_grading_system)
        except LibrusError as err:
            if not _is_refusal(err):
                raise
            self._grading_system_refused_at = _now()
            return GradingSystemData()
        self._grading_system = parsers.parse_grading_system(payload)
        return self._grading_system

    async def point_grades_enabled(self) -> bool | None:
        """`Units.GradesSettings.PointGradesEnabled` (see
        `parsers.point_grades_enabled`): None when unknown. Read once a day;
        a failed read keeps the last answer."""
        if not _fresh(self._units_at):
            payload = await self._try(self.client.async_get_units)
            if payload is not None:
                self._point_grades_flag = parsers.point_grades_enabled(payload)
                self._units_at = _now()
        return self._point_grades_flag

    async def point_grades(self) -> list[PointGradeData]:
        """Point grades (schools grading in points or percent), with each
        category's maximum and weight. Average them with
        `parsers.point_grades_percentage`. Empty without a request when the
        school has point grades switched off (`point_grades_enabled()`), and
        when Librus refuses the module (remembered for a day)."""
        if await self.point_grades_enabled() is False:
            return []

        async def load() -> list[PointGradeData]:
            grades = await self._call(self.client.async_get_point_grades)
            if not grades.get("Grades"):
                return []
            no_categories: dict[str, Any] = {}
            categories = await self._module(
                "PointGrades/Categories",
                lambda: self._call(self.client.async_get_point_grade_categories),
                no_categories,
            )
            return parsers.parse_point_grades(
                grades, parsers.parse_point_grade_categories(categories)
            )

        return await self._module("PointGrades", load, [])

    async def behaviour_grades(self) -> list[BehaviourGradeData]:
        """Formal behaviour grades. Comments are only fetched when a grade
        has one. Empty when Librus refuses the module (remembered for a
        day)."""

        async def load() -> list[BehaviourGradeData]:
            points = await self._call(self.client.async_get_behaviour_grade_points)
            comments: dict[str, Any] = {}
            if _references_comments(points.get("Grades")):
                comments = await self._probe(self.client.async_get_behaviour_grade_point_comments)
            return parsers.parse_behaviour_grades(points, parsers.parse_comment_text_map(comments))

        return await self._module("BehaviourGrades", load, [])

    async def notes(self) -> list[NoteData]:
        """Behaviour notes ("uwagi"), with `sentiment` resolved."""
        return parsers.parse_notes(await self._call(self.client.async_get_notes))

    async def note_categories(self) -> dict[int, str]:
        return await self._reference(
            "note_categories",
            lambda: self._parsed(
                self.client.async_get_note_categories,
                lambda payload: parsers.parse_id_name_map(payload, ("Categories",)),
            ),
        )

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------

    async def attendances(self) -> list[AttendanceData]:
        return parsers.parse_attendances(await self._call(self.client.async_get_attendances))

    async def attendance_types(self) -> dict[int, AttendanceTypeData]:
        """Type id -> type. `is_presence_kind` tells a real absence from a
        present/late mark."""
        return await self._reference(
            "attendance_types",
            lambda: self._parsed(
                self.client.async_get_attendance_types, parsers.parse_attendance_types
            ),
        )

    # ------------------------------------------------------------------
    # Timetable / agenda
    # ------------------------------------------------------------------

    async def timetable(self, week_of: date | None = None) -> dict[date, list[LessonData]]:
        """Lessons for the week containing `week_of` (default: this week, in
        Poland's time zone).

        Librus answers HTTP 403 both when a school hasn't published the
        timetable yet and for kindergarten accounts. After a 403 this looks
        for a kindergarten child (see `kindergartener_id`, at most once a
        day while none is found) and, if one is found, returns the
        kindergarten timetable (time blocks, no lesson numbers). Otherwise
        it returns `{}`."""
        start = week_start_of(week_of or school_today())
        if self._kindergarten_lid is None:
            try:
                payload = await self._call(lambda: self.client.async_get_timetable(start))
                return parsers.merge_timetables(payload)
            except LibrusSessionExpiredError as err:
                if err.status_code != 403:
                    raise
            if await self.kindergartener_id() is None:
                return {}
        lid = self._kindergarten_lid
        assert lid is not None
        try:
            payload = await self._call(
                lambda: self.client.async_get_kindergarten_timetable(
                    lid, start, start + timedelta(days=6)
                )
            )
        except LibrusSessionExpiredError as err:
            if err.status_code == 403:
                return {}
            raise
        return parsers.merge_timetables(payload)

    async def agenda(self) -> list[HomeworkEventData]:
        """Terminarz: tests, trips, meetings, ... (`HomeWorks` endpoint).
        Resolve `category_id` via `agenda_categories()`."""
        return parsers.parse_homeworks(await self._call(self.client.async_get_homeworks))

    async def agenda_categories(self) -> dict[int, str]:
        return await self._reference(
            "agenda_categories",
            lambda: self._parsed(
                self.client.async_get_homework_categories,
                lambda payload: parsers.parse_id_name_map(payload, ("Categories",)),
            ),
        )

    async def homework(self) -> list[HomeworkAssignmentData]:
        """Real homework assignments ("zadania domowe")."""
        payload = await self._call(self.client.async_get_homework_assignments)
        return parsers.parse_homework_assignments(payload)

    async def free_days(self) -> list[FreeDayData]:
        school, klass = await _gather2(
            self._call(self.client.async_get_school_free_days),
            self._call(self.client.async_get_class_free_days),
        )
        return parsers.parse_free_days(school, "SchoolFreeDays") + parsers.parse_free_days(
            klass, "ClassFreeDays"
        )

    async def text_grades(self) -> list[TextGradeData]:
        """Text grades (`BaseTextGrades`) - free-text grades that `grades()`
        doesn't contain - with their category names (only fetched when there
        are grades). Empty when Librus refuses the module (remembered for a
        day)."""

        async def load() -> list[TextGradeData]:
            grades = await self._call(self.client.async_get_base_text_grades)
            if not grades.get("Grades"):
                return []
            categories = await self._probe(self.client.async_get_text_grade_categories)
            return parsers.parse_text_grades(
                grades, parsers.parse_text_grade_categories(categories)
            )

        return await self._module("BaseTextGrades", load, [])

    async def _lesson_subjects(self) -> dict[int, int]:
        """lesson id -> subject id (`Lessons`)."""
        return await self._reference(
            "lesson_subjects",
            lambda: self._parsed(self.client.async_get_lessons, parsers.parse_lesson_subjects),
        )

    async def lesson_topics(self) -> list[LessonTopicData]:
        """Lessons held with their topics (`Realizations`), newest first,
        with the subject resolved through `Lessons`."""
        topics, lessons = await _gather2(
            self._call(self.client.async_get_realizations), self._lesson_subjects()
        )
        return parsers.parse_realizations(topics, lessons)

    async def standing_timetable(self) -> list[StandingLessonData]:
        """The standing weekly plan (`TimetableEntries`), with the subject
        resolved through `Lessons`. `parsers.plan_differences()` compares
        it with real weeks from `timetable()`."""
        entries, lessons = await _gather2(
            self._call(self.client.async_get_timetable_entries), self._lesson_subjects()
        )
        return parsers.parse_timetable_entries(entries, lessons)

    async def school_trips(self) -> list[SchoolTripData]:
        """Empty when Librus refuses the module (remembered for a day)."""
        return await self._module(
            "SchoolTrips",
            lambda: self._parsed(self.client.async_get_school_trips, parsers.parse_school_trips),
            [],
        )

    async def school_files(self) -> list[SchoolFileData]:
        """Documents the school shares with parents. Empty when Librus
        refuses the module (remembered for a day)."""
        return await self._module(
            "SchoolFiles",
            lambda: self._parsed(self.client.async_get_school_files, parsers.parse_school_files),
            [],
        )

    async def homework_categories(self) -> dict[int, str]:
        """Homework assignment categories (`HomeworkAssignmentData.category_id`)."""
        return await self._reference(
            "homework_assignment_categories",
            lambda: self._parsed(
                self.client.async_get_homework_assignment_categories,
                lambda payload: parsers.parse_id_name_map(payload, ("Categories",)),
            ),
        )

    async def download_attachment(
        self, attachment_id: str, message_id: str
    ) -> AttachmentFileData | None:
        """Download a message attachment (ids from `message()`), without
        opening the message. None when the school has no messages module.
        A rejected session gets the Wiadomości session set up again (and, if
        that isn't enough, one fresh login) + a retry; a timeout or an odd
        answer doesn't. The whole call, logins and retries included, gives
        up with `LibrusConnectionError` after `DOWNLOAD_TIMEOUT_SECONDS`."""
        return await _with_download_deadline(
            self._call_messages(
                lambda: self.client.async_download_message_attachment(attachment_id, message_id)
            ),
            f"attachment-{attachment_id}",
        )

    async def download_homework_attachment(self, attachment_id: str) -> AttachmentFileData:
        """Download a homework-assignment attachment (ids from
        `homework()[].attachments`). Uses the main Synergia
        session, not Wiadomości. The whole call, a relogin + retry
        included, gives up after `DOWNLOAD_TIMEOUT_SECONDS`."""
        return await _with_download_deadline(
            self._call(lambda: self.client.async_download_homework_attachment(attachment_id)),
            f"homework-file-{attachment_id}",
        )

    async def download_school_file(self, download_path: str) -> AttachmentFileData:
        """Download a school document (`school_files()[].download_path`).
        Uses the main Synergia session. The whole call, a relogin + retry
        included, gives up after `DOWNLOAD_TIMEOUT_SECONDS`."""
        return await _with_download_deadline(
            self._call(lambda: self.client.async_download_school_file(download_path)),
            "school-file",
        )

    async def justifications(self) -> list[JustificationData]:
        """Absence justifications the parent submitted, newest first, with
        their status. `parsers.justified_dates()` turns them into the days
        already covered. Empty when Librus refuses the module (remembered
        for a day)."""
        return await self._module(
            "Justifications",
            lambda: self._parsed(
                self.client.async_get_justifications, parsers.parse_justifications
            ),
            [],
        )

    async def parent_teacher_conferences(self) -> list[ParentTeacherConferenceData]:
        """Empty when Librus refuses the module (remembered for a day)."""
        return await self._module(
            "ParentTeacherConferences",
            lambda: self._parsed(
                self.client.async_get_parent_teacher_conferences,
                parsers.parse_parent_teacher_conferences,
            ),
            [],
        )

    # ------------------------------------------------------------------
    # Announcements / lucky number
    # ------------------------------------------------------------------

    async def announcements(self) -> list[SchoolNoticeData]:
        """Tablica ogłoszeń (school notice board)."""
        payload = await self._call(self.client.async_get_school_notices)
        return parsers.parse_school_notices(payload)

    async def lucky_number(self) -> LuckyNumberData | None:
        payload = await self._call(self.client.async_get_lucky_number)
        return parsers.parse_lucky_number(payload)

    # ------------------------------------------------------------------
    # Messages (Wiadomości)
    # ------------------------------------------------------------------

    async def unread_messages(self) -> dict[str, int]:
        """Unread count per mailbox ("inbox", "alerts", ...). Empty if the
        school has no messages module."""
        payload = await self._call_messages(self.client.async_get_unread_messages_count)
        if payload is None:
            return {}
        _, by_mailbox, _ = parsers.parse_messages(payload, {})
        return by_mailbox

    async def messages(self, mailbox: str = "inbox", *, limit: int = 10) -> list[MessageData]:
        """Recent messages. `content` is Librus's own truncated preview.
        Listing does NOT mark anything read. A mailbox this account doesn't
        have (Librus answers 404) comes back empty. Besides the inbox and the
        secondary boxes, `"outbox"` (sent, with `receiver_name`) and
        `"archive/inbox"` (past school years) work the same way."""
        try:
            payload = await self._call_messages(
                lambda: self.client.async_get_messages(mailbox, limit=limit)
            )
        except LibrusUnexpectedResponseError as err:
            if err.status_code == 404:
                return []
            raise
        return [] if payload is None else parsers.parse_message_list(payload, mailbox)

    async def message(self, message_id: str, mailbox: str = "inbox") -> FullMessageData | None:
        """One message's full body. **Marks the message read** on Librus,
        exactly like opening it in the app."""
        payload = await self._call_messages(
            lambda: self.client.async_get_message(mailbox, message_id)
        )
        return None if payload is None else parsers.parse_message(payload, mailbox, message_id)

    # ------------------------------------------------------------------
    # Everything at once
    # ------------------------------------------------------------------

    @staticmethod
    async def _optional(failed: set[str], section: str, awaitable: Awaitable[T], default: T) -> T:
        """`awaitable`, or `default` (with `section` added to `failed`) when
        it fails with a `LibrusError`."""
        try:
            return await awaitable
        except LibrusError:
            failed.add(section)
            return default

    async def _messages_snapshot(
        self, failed: set[str]
    ) -> tuple[dict[str, int], list[MessageData]]:
        unread = await self._optional(
            failed, "unread_messages_by_mailbox", self.unread_messages(), {}
        )
        messages = await self._optional(failed, "messages", self.messages(), [])
        if not self._messages_available:
            # No messages module (or no answer): nothing to compare against.
            failed.update({"messages", "unread_messages_by_mailbox"})
        return unread, messages

    async def fetch_all(self, *, include_messages: bool = True) -> LibrusData:
        """Everything in one snapshot, with this week's and next week's
        timetable. Optional modules a school doesn't use - or that fail this
        time - come back empty instead of failing the whole call; their
        names are in `LibrusData.failed_sections`. Only a failed login or a
        failed `me()` fails the call (and cancels the requests still
        running).

        About 35 requests (fewer with `cache_reference_data=True`), at most
        `max_concurrent_requests` at a time. Don't call it more often than
        every few minutes - see "Be gentle" in the docs; for "what's new"
        polling, `fetch_changes()` asks for much less."""
        await self.login()
        failed: set[str] = set()
        started = self._reference_loads

        def optional(section: str, awaitable: Awaitable[T], default: T) -> Awaitable[T]:
            return self._optional(failed, section, awaitable, default)

        this_week_start = week_start_of(school_today())
        (
            me,
            grades,
            categories,
            notes,
            attendances,
            attendance_types,
            this_week,
            next_week,
            agenda,
            notices,
        ) = await _gather_all(
            self.me(),
            optional("grades", self.grades(), []),
            optional("grade_categories", self.grade_categories(), {}),
            optional("notes", self.notes(), []),
            optional("attendances", self.attendances(), []),
            optional("attendance_types", self.attendance_types(), {}),
            optional("timetable", self.timetable(this_week_start), {}),
            optional("timetable", self.timetable(this_week_start + timedelta(days=7)), {}),
            optional("homeworks", self.agenda(), []),
            optional("school_notices", self.announcements(), []),
        )
        (
            lucky,
            subjects,
            teachers,
            classrooms,
            school,
            school_class,
            agenda_categories,
            note_categories,
            free_days,
            homework,
            behaviour,
            descriptive,
            partial_grades,
            grading,
            point,
            justifications,
            text_grades,
            topics_payload,
            trips,
            files,
            homework_categories,
            conferences,
            lesson_subjects,
            entries_payload,
            messages_part,
        ) = await _gather_all(
            optional("lucky_number", self.lucky_number(), None),
            optional("subjects", self.subjects(), {}),
            optional("teachers", self.teachers(), {}),
            optional("classrooms", self.classrooms(), {}),
            optional("school", self.school(), None),
            optional("school_class", self.school_class(), None),
            optional("homework_categories", self.agenda_categories(), {}),
            optional("note_categories", self.note_categories(), {}),
            optional("free_days", self.free_days(), []),
            optional("homework_assignments", self.homework(), []),
            optional("behaviour_grades", self.behaviour_grades(), []),
            optional("descriptive_grades", self.descriptive_grades(), []),
            optional("descriptive_grades", self.partial_grades(), []),
            optional("grading_system", self.grading_system(), GradingSystemData()),
            optional("point_grades", self.point_grades(), []),
            optional("justifications", self.justifications(), []),
            optional("text_grades", self.text_grades(), []),
            optional("lesson_topics", self._call(self.client.async_get_realizations), {}),
            optional("school_trips", self.school_trips(), []),
            optional("school_files", self.school_files(), []),
            optional("homework_assignment_categories", self.homework_categories(), {}),
            optional("parent_teacher_conferences", self.parent_teacher_conferences(), []),
            optional("lesson_subjects", self._lesson_subjects(), {}),
            optional("standing_timetable", self._call(self.client.async_get_timetable_entries), {}),
            # Messages run alongside the rest instead of after it.
            self._messages_snapshot(failed) if include_messages else _no_messages(failed),
        )
        if "lesson_subjects" in failed:
            failed.update({"lesson_topics", "standing_timetable"})
        unread, messages = messages_part
        data = LibrusData(
            me=me,
            grades=grades,
            grade_categories=categories,
            notes=notes,
            attendances=attendances,
            attendance_types=attendance_types,
            timetable={**this_week, **next_week},
            homeworks=agenda,
            school_notices=notices,
            lucky_number=lucky,
            subjects=subjects,
            teachers=teachers,
            classrooms=classrooms,
            school=school,
            school_class=school_class,
            free_days=free_days,
            homework_categories=agenda_categories,
            note_categories=note_categories,
            homework_assignments=homework,
            behaviour_grades=behaviour,
            descriptive_grades=descriptive + partial_grades,
            grading_system=grading,
            point_grades=point,
            justifications=justifications,
            text_grades=text_grades,
            school_trips=trips,
            school_files=files,
            homework_assignment_categories=homework_categories,
            parent_teacher_conferences=conferences,
            lesson_subjects=lesson_subjects,
            messages_available=bool(include_messages and self._messages_available),
            unread_messages_by_mailbox=unread,
            unread_message_count=unread.get("inbox", 0),
            messages=messages,
            failed_sections=failed,
        )
        await self._refresh_unknown_references(data, started)
        # After the refresh above, which may have brought new lesson ids.
        data.lesson_topics = parsers.parse_realizations(topics_payload, data.lesson_subjects)
        data.standing_timetable = parsers.parse_timetable_entries(
            entries_payload, data.lesson_subjects
        )
        return data

    async def fetch_changes(self, *, include_messages: bool = True) -> LibrusData:
        """Only what `ChangeTracker` compares - grades, notes,
        announcements, the agenda, attendances (with their types), this and
        next week's timetable and the latest inbox messages - plus subject
        names, to say what a change is about. About 10 requests instead of
        `fetch_all()`'s ~35, so it suits frequent "what's new" polling.

        Every other field keeps its default (empty) value and is not listed
        in `failed_sections`; `me` is an empty `MeData`. Sections that fail
        are in `failed_sections`, so `ChangeTracker` skips them."""
        await self.login()
        failed: set[str] = set()
        started = self._reference_loads

        def optional(section: str, awaitable: Awaitable[T], default: T) -> Awaitable[T]:
            return self._optional(failed, section, awaitable, default)

        this_week_start = week_start_of(school_today())
        (
            grades,
            notes,
            attendances,
            attendance_types,
            this_week,
            next_week,
            agenda,
            notices,
            subjects,
            messages_part,
        ) = await _gather_all(
            optional("grades", self.grades(), []),
            optional("notes", self.notes(), []),
            optional("attendances", self.attendances(), []),
            optional("attendance_types", self.attendance_types(), {}),
            optional("timetable", self.timetable(this_week_start), {}),
            optional("timetable", self.timetable(this_week_start + timedelta(days=7)), {}),
            optional("homeworks", self.agenda(), []),
            optional("school_notices", self.announcements(), []),
            optional("subjects", self.subjects(), {}),
            self._messages_snapshot(failed) if include_messages else _no_messages(failed),
        )
        unread, messages = messages_part
        data = LibrusData(
            me=MeData(account_id=None, first_name="", last_name=""),
            grades=grades,
            grade_categories={},
            notes=notes,
            attendances=attendances,
            attendance_types=attendance_types,
            timetable={**this_week, **next_week},
            homeworks=agenda,
            school_notices=notices,
            lucky_number=None,
            subjects=subjects,
            teachers={},
            classrooms={},
            messages_available=bool(include_messages and self._messages_available),
            unread_messages_by_mailbox=unread,
            unread_message_count=unread.get("inbox", 0),
            messages=messages,
            failed_sections=failed,
        )
        await self._refresh_unknown_references(data, started, only=("subjects", "attendance_types"))
        return data

    async def _refresh_unknown_references(
        self, data: LibrusData, started: int, only: tuple[str, ...] | None = None
    ) -> None:
        """With `cache_reference_data`, fetch a cached lookup again (once)
        when the snapshot mentions an id it doesn't know - a new subject,
        teacher, category, ... - instead of waiting out `reference_ttl`."""
        if not self._cache_reference_data:
            return
        lessons = [lesson for day in data.timetable.values() for lesson in day]
        # cache key -> (LibrusData field, loader, ids the snapshot uses)
        checks: dict[str, tuple[str, Callable[[], Awaitable[Any]], list[Any]]] = {
            "subjects": (
                "subjects",
                self._load_subjects,
                [g.subject_id for g in data.grades]
                + [h.subject_id for h in data.homeworks]
                + [lesson.subject_id for lesson in lessons]
                + list(data.lesson_subjects.values()),
            ),
            "teachers": (
                "teachers",
                self._load_teachers,
                [g.teacher_id for g in data.grades]
                + [n.teacher_id for n in data.notes]
                + [lesson.teacher_id for lesson in lessons],
            ),
            "classrooms": (
                "classrooms",
                self._load_classrooms,
                [lesson.classroom_id for lesson in lessons],
            ),
            "grade_categories": (
                "grade_categories",
                lambda: self._parsed(
                    self.client.async_get_grade_categories, parsers.parse_grade_categories
                ),
                [g.category_id for g in data.grades],
            ),
            "attendance_types": (
                "attendance_types",
                lambda: self._parsed(
                    self.client.async_get_attendance_types, parsers.parse_attendance_types
                ),
                [a.type_id for a in data.attendances],
            ),
            "lesson_subjects": (
                "lesson_subjects",
                lambda: self._parsed(self.client.async_get_lessons, parsers.parse_lesson_subjects),
                [a.lesson_id for a in data.attendances],
            ),
            "note_categories": (
                "note_categories",
                lambda: self._parsed(
                    self.client.async_get_note_categories,
                    lambda payload: parsers.parse_id_name_map(payload, ("Categories",)),
                ),
                [n.category_id for n in data.notes],
            ),
            "agenda_categories": (
                "homework_categories",
                lambda: self._parsed(
                    self.client.async_get_homework_categories,
                    lambda payload: parsers.parse_id_name_map(payload, ("Categories",)),
                ),
                [h.category_id for h in data.homeworks],
            ),
        }
        stale: list[tuple[str, str, Callable[[], Awaitable[Any]], list[Any]]] = []
        for key, (field_name, load, ids) in checks.items():
            if only is not None and key not in only:
                continue
            entry = self._reference_cache.get(key)
            if field_name in data.failed_sections or entry is None or entry[2] > started:
                continue  # failed, not cached, or fetched by this very call
            known: dict[Any, Any] = getattr(data, field_name)
            if _has_unknown(ids, known, self._still_unknown.get(key, set())):
                stale.append((key, field_name, load, ids))
        if not stale:
            return

        async def refetch(key: str, field_name: str, load: Callable[[], Awaitable[Any]]) -> None:
            try:
                value = await load()
            except LibrusError:
                return  # keep the cached copy
            self._store_reference(key, value)
            setattr(data, field_name, value)

        await _gather_all(*(refetch(key, field_name, load) for key, field_name, load, _ in stale))
        for key, field_name, _, ids in stale:
            known = getattr(data, field_name)
            # Ids Librus still doesn't know don't trigger another fetch.
            self._still_unknown[key] = {
                value
                for value in ids
                if value is not None and value not in known and parsers.as_int(value) not in known
            }


async def _no_messages(failed: set[str]) -> tuple[dict[str, int], list[MessageData]]:
    """Stands in for the messages part of a snapshot when messages weren't
    asked for."""
    failed.update({"messages", "unread_messages_by_mailbox"})
    return {}, []
