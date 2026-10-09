"""Async client for Librus's Synergia/API gateway.

See const.py's module docstring for why this is a cookie/login-form flow
(reverse-engineered from `emsi/librus_pyapi`, MIT) rather than the dead
OAuth password grant szkolny-android documented. Confirmed live on
2026-09-05 to complete without a captcha challenge for a normal login.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urljoin

import aiohttp
from yarl import URL

from .const import (
    API_OAUTH_AUTHORIZATION_URL,
    API_OAUTH_AUTHORIZATION_WITH_SCOPE_URL,
    ASSUMED_SESSION_LIFETIME_SECONDS,
    DATA_BASE_URL,
    ENDPOINT_ATTENDANCE_TYPES,
    ENDPOINT_ATTENDANCES,
    ENDPOINT_BASE_TEXT_GRADES,
    ENDPOINT_BEHAVIOUR_GRADES_POINTS,
    ENDPOINT_BEHAVIOUR_GRADES_POINTS_CATEGORIES,
    ENDPOINT_BEHAVIOUR_GRADES_POINTS_COMMENTS,
    ENDPOINT_CLASS_FREE_DAYS,
    ENDPOINT_CLASSES,
    ENDPOINT_CLASSROOMS,
    ENDPOINT_DESCRIPTIVE_GRADES,
    ENDPOINT_GRADE_CATEGORIES,
    ENDPOINT_GRADE_COMMENTS,
    ENDPOINT_GRADE_TYPES,
    ENDPOINT_GRADES,
    ENDPOINT_HOMEWORK_ASSIGNMENT_CATEGORIES,
    ENDPOINT_HOMEWORK_ASSIGNMENTS,
    ENDPOINT_HOMEWORK_CATEGORIES,
    ENDPOINT_HOMEWORKS,
    ENDPOINT_JUSTIFICATIONS,
    ENDPOINT_LESSONS,
    ENDPOINT_LUCKY_NUMBERS,
    ENDPOINT_ME,
    ENDPOINT_NOTE_CATEGORIES,
    ENDPOINT_NOTES,
    ENDPOINT_PARENT_TEACHER_CONFERENCES,
    ENDPOINT_POINT_GRADE_CATEGORIES,
    ENDPOINT_POINT_GRADES,
    ENDPOINT_REALIZATIONS,
    ENDPOINT_SCHOOL_FILES,
    ENDPOINT_SCHOOL_FREE_DAYS,
    ENDPOINT_SCHOOL_NOTICES,
    ENDPOINT_SCHOOL_TRIPS,
    ENDPOINT_SCHOOLS,
    ENDPOINT_SUBJECTS,
    ENDPOINT_TEACHERS,
    ENDPOINT_TEXT_GRADE_CATEGORIES,
    ENDPOINT_TEXT_GRADES,
    ENDPOINT_TIMETABLE_ENTRIES,
    ENDPOINT_TIMETABLES,
    ENDPOINT_UNITS,
    ENDPOINT_VIRTUAL_CLASSES,
    KINDERGARTENS_BASE_URL,
    LOGIN_HEADERS,
    MAX_OAUTH_REDIRECTS,
    MESSAGES_ACCESS_DENIED_MARKER,
    MESSAGES_BASE_URL,
    MESSAGES_BOOTSTRAP_URL,
    OAUTH_TOKEN_COOKIE,
    PERSISTED_COOKIE_NAMES,
    SANDBOX_URL,
    SESSION_EXPIRY_SAFETY_MARGIN_SECONDS,
    SESSION_REFRESH_AFTER_SECONDS,
    SYNERGIA_DOMAIN,
    SYNERGIA_HOMEWORK_ATTACHMENT_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    SYNERGIA_REFRESH_TOKEN_URL,
    SYNERGIA_STUDENT_INFO_URL,
    USER_AGENT,
)
from .exceptions import (
    LibrusCaptchaRequiredError,
    LibrusConnectionError,
    LibrusInvalidCredentialsError,
    LibrusServerMaintenanceError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)
from .models import AttachmentFileData

_CAPTCHA_MARKERS = ("captcha", "recaptcha", "g-recaptcha", "hcaptcha")


@dataclass(slots=True)
class LibrusSessionData:
    """A snapshot of the current session, for the caller to persist across
    process restarts (see `LibrusApiClient.import_session`). Re-sending the same cookies (especially the
    long-lived DeviceCookie) on a later login is believed to be why a normal
    login skips captcha/2FA - see const.py's module docstring."""

    cookies: list[dict[str, str]] = field(default_factory=list)
    logged_in_at: float = 0.0  # epoch seconds


OnSessionUpdate = Callable[[LibrusSessionData], "Awaitable[None] | None"]


class LibrusApiClient:
    """Low-level async client for Librus Synergia's JSON gateway.

    Every `async_get_*` method returns the raw decoded JSON of one endpoint;
    turn it into typed models with the functions in `librus_synergia.parsers`,
    or use the high-level `librus_synergia.Librus` wrapper which does both.

    The caller owns the `aiohttp.ClientSession`. Authentication lives
    entirely in that session's cookie jar (Librus uses cookies, not a bearer
    token), so **give every account its own session**: two clients sharing
    one session also share one `oauth_token` cookie slot, and one account's
    requests will silently return another account's data.

    The password is never stored - pass it to `async_login` /
    `async_ensure_session_valid` whenever a (re)login may be needed. A
    session lasts roughly a day and there is no refresh token, so
    unattended long-running use needs the password available.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        *,
        on_session_update: OnSessionUpdate | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._on_session_update = on_session_update
        self._logged_in_at: float = 0.0

    @property
    def username(self) -> str:
        return self._username

    async def async_close(self) -> None:
        """Release the underlying session (via `detach()`, which also works
        for sessions whose `close()` is wrapped by a host framework, e.g.
        Home Assistant's `async_create_clientsession`). Only call this when
        the session was created for this client alone."""
        self._session.detach()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def import_session(self, session_data: LibrusSessionData | None) -> None:
        """Re-inject previously-persisted cookies into the shared session's
        cookie jar, e.g. right after a process restart."""
        if session_data is None:
            return
        self._logged_in_at = session_data.logged_in_at
        for cookie in session_data.cookies:
            morsel: SimpleCookie = SimpleCookie()
            morsel[cookie["name"]] = cookie["value"]
            morsel[cookie["name"]]["path"] = cookie.get("path", "/")
            self._session.cookie_jar.update_cookies(
                morsel, response_url=URL(f"https://{cookie['domain']}/")
            )

    def export_session(self) -> LibrusSessionData:
        """The cookies worth persisting (see `import_session`)."""
        return self._export_session()

    def _export_session(self) -> LibrusSessionData:
        # Walk the jar directly rather than `filter_cookies(https://domain/)`:
        # CONFIRMED live (2026-09-28) that DeviceCookie is set with
        # `Path=/OAuth`, so a root-URL filter silently leaves out exactly
        # the cookie that matters most.
        exported: list[dict[str, str]] = []
        for morsel in self._session.cookie_jar:
            domain = morsel["domain"].lstrip(".")
            if morsel.key not in PERSISTED_COOKIE_NAMES.get(domain, ()):
                continue
            cookie = {"name": morsel.key, "value": morsel.value, "domain": domain}
            if morsel["path"] not in ("", "/"):
                cookie["path"] = morsel["path"]
            exported.append(cookie)
        return LibrusSessionData(cookies=exported, logged_in_at=self._logged_in_at)

    def is_session_valid(self) -> bool:
        if self._logged_in_at <= 0:
            return False
        age = time.time() - self._logged_in_at
        return age < (ASSUMED_SESSION_LIFETIME_SECONDS - SESSION_EXPIRY_SAFETY_MARGIN_SECONDS)

    @property
    def session_age_seconds(self) -> float | None:
        """Seconds since the last successful login, or `None` if this
        client has never logged in."""
        if self._logged_in_at <= 0:
            return None
        return time.time() - self._logged_in_at

    async def async_ensure_session_valid(self, password: str, *, force: bool = False) -> None:
        """Log in if the assumed session lifetime has elapsed, or always if
        `force=True` - use `force` to recover from a
        `LibrusSessionExpiredError` (Librus dropped the session earlier
        than the elapsed-time estimate expected)."""
        if force:
            await self.async_login(password)
            return
        age = self.session_age_seconds
        if (
            age is not None
            and age >= SESSION_REFRESH_AFTER_SECONDS
            and await self.async_refresh_session()
        ):
            return
        if self.is_session_valid():
            return
        await self.async_login(password)

    async def async_refresh_session(self) -> bool:
        """Renew the `oauth_token` cookie without a password login
        (`synergia.librus.pl/refreshToken`). True when Librus answered 200
        and the cookie is still there; the session then counts as fresh
        and is persisted via `on_session_update`. False (never raises) when
        it didn't work - the caller logs in as usual."""
        if self._logged_in_at <= 0:
            return False
        try:
            async with self._session.get(
                SYNERGIA_REFRESH_TOKEN_URL,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=False,
            ) as response:
                await response.read()
                status = response.status
        except aiohttp.ClientError:
            return False
        jar_cookies = self._session.cookie_jar.filter_cookies(URL(f"https://{SYNERGIA_DOMAIN}/"))
        if status != 200 or OAUTH_TOKEN_COOKIE not in jar_cookies:
            return False
        self._logged_in_at = time.time()
        if self._on_session_update is not None:
            result = self._on_session_update(self._export_session())
            if result is not None:
                await result
        return True

    async def async_login(self, password: str) -> LibrusSessionData:
        """Run the full login handshake (portalRodzina -> Authorization form
        POST -> manual redirect chain), confirming a session cookie was set.

        Raises one of this library's exceptions on failure. On success,
        persists the resulting cookies via `on_session_update` (if set) and
        returns them too.
        """
        try:
            resp = await self._session.get(SYNERGIA_PORTAL_LOGIN_URL, allow_redirects=False)
            authorization_url = resp.headers.get("Location")
            if not authorization_url:
                raise LibrusUnexpectedResponseError(
                    "Login step 1 (portalRodzina) returned no redirect Location."
                )

            # Sets API-side session cookies; the response body isn't used.
            await self._session.get(authorization_url, allow_redirects=False)

            data = {"action": "login", "login": self._username, "pass": password}
            resp = await self._session.post(
                API_OAUTH_AUTHORIZATION_URL, data=data, headers=LOGIN_HEADERS
            )
            text = await resp.text()
            self._raise_if_captcha(text, "login form response")
            try:
                login_response = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as err:
                raise LibrusUnexpectedResponseError(
                    f"Login response wasn't JSON: {text[:200]!r}"
                ) from err
            go_to = login_response.get("goTo") if isinstance(login_response, dict) else None
            if not go_to:
                # UNVERIFIED trigger condition for a genuinely wrong
                # password - see exceptions.py's module docstring.
                raise LibrusInvalidCredentialsError(f"Login rejected: {login_response!r}")

            current_url = urljoin(API_OAUTH_AUTHORIZATION_WITH_SCOPE_URL, go_to)
            for _ in range(MAX_OAUTH_REDIRECTS):
                resp = await self._session.get(current_url, allow_redirects=False)
                text = await resp.text()
                self._raise_if_captcha(text, "OAuth redirect chain")
                location = resp.headers.get("Location")
                if not location:
                    break
                current_url = urljoin(str(resp.url), location)
            else:
                raise LibrusUnexpectedResponseError("OAuth redirect chain exceeded the hop limit.")
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err

        jar_cookies = self._session.cookie_jar.filter_cookies(URL(f"https://{SYNERGIA_DOMAIN}/"))
        if OAUTH_TOKEN_COOKIE not in jar_cookies:
            raise LibrusUnexpectedResponseError(
                "Login appeared to finish but no oauth_token session cookie was set."
            )

        self._logged_in_at = time.time()
        session_data = self._export_session()
        if self._on_session_update is not None:
            result = self._on_session_update(session_data)
            if result is not None:
                await result
        return session_data

    @staticmethod
    def _raise_if_captcha(text: str, where: str) -> None:
        low = text.lower()
        if any(marker in low for marker in _CAPTCHA_MARKERS):
            raise LibrusCaptchaRequiredError(f"Captcha marker seen in {where}.")

    # ------------------------------------------------------------------
    # Data endpoints
    # ------------------------------------------------------------------

    async def _async_request(
        self,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        array_envelope_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._async_request_url(
            f"{DATA_BASE_URL}/{endpoint}",
            params=params,
            array_envelope_key=array_envelope_key,
        )

    async def _async_request_url(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        array_envelope_key: str | None = None,
    ) -> dict[str, Any]:
        """Same as `_async_request` but for a fully-formed URL, not just an
        endpoint under the Synergia gateway - needed for the separate
        Wiadomości subsystem, which lives on its own domain.

        `array_envelope_key`: some endpoints (confirmed live so far: the
        Wiadomości secondary-mailbox list endpoints) return a bare JSON
        array instead of the usual `{"<key>": [...]}` envelope. Only pass
        this for an endpoint that has actually been observed doing that -
        it tells `_async_read_json` which key to wrap the array under.
        Every other endpoint keeps the original strict behavior (a bare
        list is treated as an unexpected response, not silently guessed
        at) so a *different* endpoint that unexpectedly returns a list
        someday fails loudly instead of being mis-keyed under "data".
        """
        try:
            async with self._session.get(
                url, headers={"User-Agent": USER_AGENT}, params=params
            ) as response:
                if response.status == 503:
                    raise LibrusServerMaintenanceError(
                        f"Librus is under maintenance (HTTP 503) on {url}."
                    )
                if response.status in (401, 403):
                    # BUG FIX (code review): this check used to run AFTER
                    # `_async_read_json` below, which JSON-parses the body
                    # first - a dead-session 401/403 can come back with a
                    # non-JSON (HTML/plain-text) body, and that raised
                    # LibrusUnexpectedResponseError before this branch (and
                    # the confirmed-403 "timetable not published" detection
                    # that depends on `status_code`, and the coordinator's
                    # forced-relogin-and-retry-once recovery that depends on
                    # this exception TYPE at all) ever got a chance to run.
                    # Check the status first and don't depend on a
                    # successfully-parsed payload - `status_code` alone is
                    # enough context for LibrusSessionExpiredError.
                    #
                    # Distinct from LibrusInvalidCredentialsError (which
                    # means the login handshake itself was rejected) - this
                    # means an already-established session died mid-cycle,
                    # which the stored password can very likely fix without
                    # asking the user anything. See LibrusSessionExpiredError.
                    # status_code is carried through so a caller can tell a
                    # genuine 401 apart from a 403 that might mean something
                    # else entirely for a specific endpoint (e.g. Timetables'
                    # "not published yet", not an auth problem at all).
                    raise LibrusSessionExpiredError(
                        f"Session rejected on {url} (HTTP {response.status}).",
                        status_code=response.status,
                    )
                payload = await self._async_read_json(
                    response, array_envelope_key=array_envelope_key
                )
                if response.status >= 400:
                    raise LibrusUnexpectedResponseError(
                        f"HTTP {response.status} from {url}: {payload!r}",
                        status_code=response.status,
                    )
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err
        return payload

    @staticmethod
    async def _async_read_json(
        response: aiohttp.ClientResponse, *, array_envelope_key: str | None = None
    ) -> dict[str, Any]:
        try:
            data = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError) as err:
            text = await response.text()
            # An error page (e.g. a 404 for a mailbox this account doesn't
            # have) keeps its status so callers can tell it apart.
            raise LibrusUnexpectedResponseError(
                f"Non-JSON response (HTTP {response.status}): {text[:200]!r}",
                status_code=response.status if response.status >= 400 else None,
            ) from err
        if isinstance(data, list):
            # CONFIRMED live (2026-09-06): at least one Wiadomości mailbox's
            # list endpoint ("substitutions" and/or "alerts") returns a
            # bare JSON array instead of the {"data": [...]} envelope every
            # other endpoint in this client uses - normalize instead of
            # raising, so one differently-shaped secondary mailbox doesn't
            # take the whole request down (was surfacing as
            # LibrusUnexpectedResponseError: "Expected a JSON object, got
            # list", which - before coordinator.py isolated the two
            # fetches - silently wiped out the otherwise-working inbox
            # unread-count/message-list data too, via a shared
            # asyncio.gather()).
            #
            # Only the ONE confirmed endpoint (via async_get_messages'
            # explicit array_envelope_key="data") gets this treatment. Any
            # other endpoint that unexpectedly returns a bare list falls
            # through to the "Expected a JSON object" error below instead of
            # being silently (and possibly wrongly - e.g. Grades/Comments
            # uses a "Comments" key, not "data") wrapped under "data".
            if array_envelope_key is not None:
                return {array_envelope_key: data}
            raise LibrusUnexpectedResponseError(
                f"Expected a JSON object, got {type(data).__name__}"
            )
        if not isinstance(data, dict):
            raise LibrusUnexpectedResponseError(
                f"Expected a JSON object, got {type(data).__name__}"
            )
        return data

    async def async_get_me(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_ME)

    async def async_get_grades(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_GRADES)

    async def async_get_grade_categories(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_GRADE_CATEGORIES)

    async def async_get_notes(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_NOTES)

    async def async_get_attendances(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_ATTENDANCES)

    async def async_get_attendance_types(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_ATTENDANCE_TYPES)

    async def async_get_timetable(self, week_start: date) -> dict[str, Any]:
        return await self._async_request(
            ENDPOINT_TIMETABLES, params={"weekStart": week_start.isoformat()}
        )

    async def async_get_token_info(self) -> dict[str, Any]:
        """Fetch the current Synergia token identity used by the web UI."""
        return await self._async_request("Auth/TokenInfo")

    async def async_get_user_info(self, identifier: str) -> dict[str, Any]:
        """Fetch the auth user profile for one Librus LID identifier."""
        return await self._async_request(f"Auth/UserInfo/{identifier}")

    async def async_get_user(self, user_id: int | str) -> dict[str, Any]:
        """Fetch one user record from the standard Synergia Users API."""
        return await self._async_request(f"Users/{user_id}")

    async def async_get_kindergartener(self, identifier: str) -> dict[str, Any]:
        """Fetch kindergarten child metadata used by the Synergia web UI."""
        return await self._async_request(f"Auth/Users/Kindergarteners/{identifier}")

    async def async_get_kindergarten_timetable(
        self, kindergartener_identifier: str, date_from: date, date_to: date
    ) -> dict[str, Any]:
        """Fetch the kindergarten timetable used by Synergia's web UI.

        This is a different API family from the standard `/Timetables`
        endpoint. It returns `timetableEntries`.
        """
        endpoint = f"{KINDERGARTENS_BASE_URL}/timetable/kindergarteners/{kindergartener_identifier}"
        return await self._async_request_url(
            endpoint,
            params={
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
            },
        )

    async def async_get_kindergarten_group(self, group_identifier: str) -> dict[str, Any]:
        """Fetch kindergarten group metadata (name and tutor LIDs)."""
        return await self._async_request_url(f"{KINDERGARTENS_BASE_URL}/groups/{group_identifier}")

    async def async_get_kindergarten_activity_types(self) -> dict[str, Any]:
        """Fetch kindergarten activity-type names."""
        return await self._async_request_url(f"{KINDERGARTENS_BASE_URL}/activities-types")

    async def async_get_kindergarten_classrooms(self) -> dict[str, Any]:
        """Fetch classroom identifiers/symbols used by kindergarten lessons."""
        return await self._async_request("Auth/Classrooms")

    async def async_get_homeworks(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_HOMEWORKS)

    async def async_get_school_notices(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SCHOOL_NOTICES)

    async def async_get_lucky_number(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_LUCKY_NUMBERS)

    async def async_get_subjects(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SUBJECTS)

    async def async_get_teachers(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_TEACHERS)

    async def async_get_classrooms(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_CLASSROOMS)

    async def async_get_lessons(self) -> dict[str, Any]:
        """Global lesson_id -> Subject/Teacher/Class lookup - see
        const.py's note on ENDPOINT_LESSONS for why this exists (resolving
        an Attendances record's subject)."""
        return await self._async_request(ENDPOINT_LESSONS)

    async def async_get_homework_assignments(self) -> dict[str, Any]:
        """See const.py's note on ENDPOINT_HOMEWORK_ASSIGNMENTS."""
        return await self._async_request(ENDPOINT_HOMEWORK_ASSIGNMENTS)

    async def async_get_schools(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SCHOOLS)

    async def async_get_classes(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_CLASSES)

    async def async_get_virtual_classes(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_VIRTUAL_CLASSES)

    async def async_get_school_free_days(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SCHOOL_FREE_DAYS)

    async def async_get_class_free_days(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_CLASS_FREE_DAYS)

    async def async_get_homework_categories(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_HOMEWORK_CATEGORIES)

    async def async_get_parent_teacher_conferences(self) -> dict[str, Any]:
        """Parent-teacher meetings. The same meetings usually also come
        through `HomeWorks` (the agenda) - see ParentTeacherConferenceData."""
        return await self._async_request(ENDPOINT_PARENT_TEACHER_CONFERENCES)

    async def async_get_grade_types(self) -> dict[str, Any]:
        """Reference data confirming every valid `Grade` value string Librus
        uses (numeric 1-6 with +/- modifiers, plus non-numeric status marks
        like "bz"/"np"/"zw") - not currently consumed anywhere, kept for
        diagnostics/future use now that it's confirmed real."""
        return await self._async_request(ENDPOINT_GRADE_TYPES)

    async def async_get_note_categories(self) -> dict[str, Any]:
        """Behaviour note categories (confirmed live, populated)."""
        return await self._async_request(ENDPOINT_NOTE_CATEGORIES)

    async def async_get_behaviour_grade_points(self) -> dict[str, Any]:
        """ "Ocena zachowania" (formal behaviour grade) - distinct from
        Notes ("uwagi"). Confirmed live: a classic-scale grade comes as
        `BehaviourGrade.Id`, a points school uses `Value`/`ShortName`."""
        return await self._async_request(ENDPOINT_BEHAVIOUR_GRADES_POINTS)

    async def async_get_behaviour_grade_point_categories(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_BEHAVIOUR_GRADES_POINTS_CATEGORIES)

    async def async_get_behaviour_grade_point_comments(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_BEHAVIOUR_GRADES_POINTS_COMMENTS)

    async def async_get_grade_comments(self) -> dict[str, Any]:
        """CONFIRMED live to be a SEPARATE endpoint from /Grades - see
        const.py's ENDPOINT_GRADE_COMMENTS. `parsers.parse_grades` resolves
        each grade's comment ids against it."""
        return await self._async_request(ENDPOINT_GRADE_COMMENTS)

    async def async_get_units(self) -> dict[str, Any]:
        """School/unit configuration (which grade systems are enabled, bell
        schedule, behaviour-points settings). Confirmed live; see
        `parsers.point_grades_enabled`."""
        return await self._async_request(ENDPOINT_UNITS)

    async def async_get_point_grades(self) -> dict[str, Any]:
        """Point grades (e.g. 17/20, or a 0-100 scale), for schools with
        Units' GradesSettings.PointGradesEnabled. See `parse_point_grades`."""
        return await self._async_request(ENDPOINT_POINT_GRADES)

    async def async_get_base_text_grades(self) -> dict[str, Any]:
        """Text grades (`BaseTextGrades`), see `parse_text_grades`."""
        return await self._async_request(ENDPOINT_BASE_TEXT_GRADES)

    async def async_get_text_grade_categories(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_TEXT_GRADE_CATEGORIES)

    async def async_get_realizations(self) -> dict[str, Any]:
        """Lessons held with their topics, see `parse_realizations`."""
        return await self._async_request(ENDPOINT_REALIZATIONS)

    async def async_get_school_trips(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SCHOOL_TRIPS)

    async def async_get_school_files(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_SCHOOL_FILES)

    async def async_get_timetable_entries(self) -> dict[str, Any]:
        """The standing weekly plan, see `parse_timetable_entries`."""
        return await self._async_request(ENDPOINT_TIMETABLE_ENTRIES)

    async def async_get_homework_assignment_categories(self) -> dict[str, Any]:
        return await self._async_request(ENDPOINT_HOMEWORK_ASSIGNMENT_CATEGORIES)

    async def async_get_justifications(self) -> dict[str, Any]:
        """Absence justifications submitted by the parent, with their status.
        See `parse_justifications`."""
        return await self._async_request(ENDPOINT_JUSTIFICATIONS)

    async def async_get_point_grade_categories(self) -> dict[str, Any]:
        """Point-grade categories: maximum points, weight, counts to the
        average. See `parse_point_grade_categories`."""
        return await self._async_request(ENDPOINT_POINT_GRADE_CATEGORIES)

    async def async_get_descriptive_grades(self) -> dict[str, Any]:
        """Descriptive grades, for schools with Units'
        GradesSettings.DescriptiveGradesEnabled."""
        return await self._async_request(ENDPOINT_DESCRIPTIVE_GRADES)

    async def async_get_text_grades(self) -> dict[str, Any]:
        """The older `TextGrades` endpoint. Real text grades on the tested
        account came only through `BaseTextGrades` - see
        `async_get_base_text_grades`."""
        return await self._async_request(ENDPOINT_TEXT_GRADES)

    # ------------------------------------------------------------------
    # Wiadomości (messages) - a separate subsystem, own domain/session.
    #
    # IMPORTANT: `async_get_message` below (fetching one message's full
    # body via `/{mailbox}/messages/{id}`) is CONFIRMED live (2026-09-06)
    # to mark the message read server-side in the real Librus inbox - the
    # `readDate` field flips from null to a real timestamp immediately
    # after one GET, on a message that stayed unread across many prior
    # LIST-endpoint polls. Listing and counting never mark anything read,
    # so routine polling should use only those; call `async_get_message`
    # only on a user's deliberate action, the same as opening a message in
    # the Librus app.
    # ------------------------------------------------------------------

    async def async_get_student_info_page(self) -> str:
        """HTML of Synergia's "Informacje" web page - see
        `parsers.parse_student_number` (a fallback; the class register number
        is in JSON via `async_get_user`). A redirect (to the login page) or a
        401/403 means the session is gone, same as on an API endpoint."""
        try:
            async with self._session.get(
                SYNERGIA_STUDENT_INFO_URL,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=False,
            ) as response:
                if response.status in (301, 302, 303, 307, 401, 403):
                    raise LibrusSessionExpiredError(
                        f"Session rejected on {SYNERGIA_STUDENT_INFO_URL} (HTTP {response.status}).",
                        status_code=response.status,
                    )
                if response.status == 503:
                    raise LibrusServerMaintenanceError(
                        f"Librus is under maintenance (HTTP 503) on {SYNERGIA_STUDENT_INFO_URL}."
                    )
                if response.status != 200:
                    raise LibrusUnexpectedResponseError(
                        f"HTTP {response.status} from {SYNERGIA_STUDENT_INFO_URL}."
                    )
                return await response.text()
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err

    async def async_bootstrap_messages(self) -> bool:
        """One-time-per-login bootstrap for the Wiadomości subsystem.

        Returns False (not an error) if this account's school doesn't have
        the messages module enabled - some don't. Caller decides how often
        to call this (`Librus` does it once per login); this method does no caching.
        """
        try:
            async with self._session.get(
                MESSAGES_BOOTSTRAP_URL, headers={"User-Agent": USER_AGENT}
            ) as response:
                text = await response.text()
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err
        self._raise_if_captcha(text, "messages bootstrap")
        return MESSAGES_ACCESS_DENIED_MARKER not in text

    async def async_get_unread_messages_count(self, mailbox: str = "inbox") -> dict[str, Any]:
        return await self._async_request_url(f"{MESSAGES_BASE_URL}/{mailbox}/unreadMessagesCount")

    async def async_get_messages(
        self, mailbox: str = "inbox", *, limit: int = 10, unread_only: bool = False
    ) -> dict[str, Any]:
        params = {"limit": str(limit)}
        if unread_only:
            params["unreadOnly"] = "1"
        # CONFIRMED live (2026-09-06): at least one secondary mailbox
        # ("substitutions"/"alerts") returns a bare JSON array here instead
        # of the {"data": [...]} envelope every other mailbox uses -
        # array_envelope_key normalizes that one confirmed case without
        # guessing the same for every other endpoint in the client.
        return await self._async_request_url(
            f"{MESSAGES_BASE_URL}/{mailbox}/messages",
            params=params,
            array_envelope_key="data",
        )

    async def async_get_message(self, mailbox: str, message_id: str) -> dict[str, Any]:
        """Fetch ONE message's full, untruncated body.

        CONFIRMED live (2026-09-06): real response root key is `"data"`,
        full content lives in a base64-encoded `"Message"` field (note the
        capital M - distinct from the list endpoint's lowercase `content`
        key). See the big comment above this method's section for why this
        marks the message read and must only be called from deliberate
        user action.

        Also CONFIRMED live (2026-09-06): the decoded `Message` field isn't plain text - it's
        wrapped in a tiny XML shell,
        `<Message><Content><![CDATA[the real text...]]></Content></Message>`.
        `parsers.decode_message_content` strips this; do not decode
        this field any other way or the literal XML markup leaks into the
        UI.
        """
        return await self._async_request_url(f"{MESSAGES_BASE_URL}/{mailbox}/messages/{message_id}")

    async def async_download_message_attachment(
        self, attachment_id: str, message_id: str, *, max_wait_attempts: int = 5
    ) -> AttachmentFileData:
        """Download one message attachment without opening the message.
        CONFIRMED live 2026-10-07: `attachments/<id>/messages/<msg>` gives a
        `sandbox.librus.pl/GetFile/...` link; the link itself is an HTML
        waiting page and `<link>/get` the file. The attachment ids come from
        `async_get_message` (which marks an unread message read)."""
        payload = await self._async_request_url(
            f"{MESSAGES_BASE_URL}/attachments/{attachment_id}/messages/{message_id}"
        )
        link = (
            (payload.get("data") or {}).get("downloadLink")
            if isinstance(payload.get("data"), dict)
            else None
        )
        if not link:
            raise LibrusUnexpectedResponseError(f"No download link for attachment {attachment_id}")
        return await self._async_fetch_sandbox_file(
            link, f"attachment-{attachment_id}", max_wait_attempts=max_wait_attempts
        )

    async def async_download_homework_attachment(
        self, attachment_id: str, *, max_wait_attempts: int = 10
    ) -> AttachmentFileData:
        """Download one homework-assignment attachment (ids from
        `HomeworkAssignmentData.attachments`). Per szkolny-android's
        `LibrusSynergiaHomeworkGetAttachment.kt`: Synergia's
        `homework/downloadFile/<id>` redirects to a sandbox.librus.pl link.
        CONFIRMED live 2026-10-09: a `CSTryToDownload&singleUseKey=...` link
        (see `_async_fetch_single_use_key_file`); a `GetFile` link, as for
        message attachments, is handled too."""
        url = f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/{attachment_id}"
        try:
            async with self._session.get(
                url, headers={"User-Agent": USER_AGENT}, allow_redirects=False
            ) as response:
                status = response.status
                location = response.headers.get("Location")
                await response.read()
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err
        if status in (401, 403):
            raise LibrusSessionExpiredError(
                f"Session rejected on {url} (HTTP {status}).", status_code=status
            )
        if status == 503:
            raise LibrusServerMaintenanceError(f"Librus is under maintenance (HTTP 503) on {url}.")
        if not location:
            raise LibrusUnexpectedResponseError(
                f"No download link for homework file {attachment_id}"
            )
        link = urljoin(url, location)
        if "CSDownloadFailed" in link:
            raise LibrusUnexpectedResponseError(f"Homework file {attachment_id} not found")
        if "sandbox.librus.pl" not in link:
            # Synergia sends a dead session to its login page.
            raise LibrusSessionExpiredError(
                f"Session rejected on {url} (redirected to {link}).", status_code=status
            )
        fallback_name = f"homework-file-{attachment_id}"
        key = _SINGLE_USE_KEY_RE.search(link)
        if key is None:
            return await self._async_fetch_sandbox_file(
                link, fallback_name, max_wait_attempts=max_wait_attempts
            )
        return await self._async_fetch_single_use_key_file(
            link, key.group(1), fallback_name, max_wait_attempts=max_wait_attempts
        )

    async def _async_fetch_sandbox_file(
        self, link: str, fallback_name: str, *, max_wait_attempts: int
    ) -> AttachmentFileData:
        """A `sandbox.librus.pl/GetFile/...` link is an HTML waiting page;
        `<link>/get` returns the file once it's ready."""
        headers = {"User-Agent": USER_AGENT, "Referer": link}
        try:
            async with self._session.get(link, headers=headers) as response:
                await response.read()
            for attempt in range(max_wait_attempts):
                async with self._session.get(f"{link}/get", headers=headers) as response:
                    content_type = response.headers.get("Content-Type", "")
                    body = await response.read()
                    if response.status == 200 and "text/html" not in content_type:
                        return _file_data(response, body, fallback_name)
                if attempt + 1 < max_wait_attempts:
                    await asyncio.sleep(2)
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err
        raise LibrusUnexpectedResponseError(f"{fallback_name} wasn't ready to download")

    async def _async_fetch_single_use_key_file(
        self, link: str, key: str, fallback_name: str, *, max_wait_attempts: int
    ) -> AttachmentFileData:
        """The `CSTryToDownload` sandbox flow. CONFIRMED live 2026-10-09 (a
        homework attachment), following sandbox.librus.pl's own
        `try_to_download_by_key.js`: open the `CSTryToDownload` page, then
        POST `CSCheckKey` with the key in the form body every few seconds
        until `status` is `ready` (`not_downloaded_yet` meanwhile,
        `download_failed` on failure), then GET `CSDownload`."""
        headers = {"User-Agent": USER_AGENT, "Referer": link}
        try:
            async with self._session.get(link, headers=headers) as response:
                await response.read()
            for attempt in range(max_wait_attempts):
                async with self._session.post(
                    SANDBOX_URL,
                    params={"action": "CSCheckKey"},
                    data={"singleUseKey": key},
                    headers=headers,
                ) as response:
                    try:
                        status = (await response.json(content_type=None) or {}).get("status")
                    except ValueError:
                        status = None
                if status == "ready":
                    async with self._session.get(
                        SANDBOX_URL,
                        params={"action": "CSDownload", "singleUseKey": key},
                        headers=headers,
                    ) as response:
                        body = await response.read()
                        content_type = response.headers.get("Content-Type", "")
                        if response.status == 200 and "text/html" not in content_type:
                            return _file_data(response, body, fallback_name)
                    break
                if status != "not_downloaded_yet":
                    break
                if attempt + 1 < max_wait_attempts:
                    await asyncio.sleep(_SANDBOX_POLL_SECONDS)
        except aiohttp.ClientError as err:
            raise LibrusConnectionError(str(err)) from err
        raise LibrusUnexpectedResponseError(f"{fallback_name} wasn't ready to download")


# Librus's own page polls every 5 s at first, then every 10 s.
_SANDBOX_POLL_SECONDS = 3

_SINGLE_USE_KEY_RE = re.compile(r"singleUseKey=([0-9A-Za-z_\-]+)")


def _file_data(
    response: aiohttp.ClientResponse, body: bytes, fallback_name: str
) -> AttachmentFileData:
    content_type = response.headers.get("Content-Type", "")
    return AttachmentFileData(
        filename=_attachment_filename(response.headers.get("Content-Disposition")) or fallback_name,
        content_type=content_type.split(";")[0].strip() or "application/octet-stream",
        content=body,
    )


def _attachment_filename(disposition: str | None) -> str | None:
    """The file name from a `Content-Disposition` header (RFC 5987
    `filename*=` first, then `filename=`), without any path."""
    if not disposition:
        return None
    from email.message import Message  # noqa: PLC0415

    message = Message()
    message["content-disposition"] = disposition
    name = message.get_filename()
    if not name:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip() or None
