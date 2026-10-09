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
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from functools import partial
from types import TracebackType
from typing import Any, TypeVar

import aiohttp

from . import parsers
from .client import LibrusApiClient, LibrusSessionData, OnSessionUpdate
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


def week_start_of(day: date) -> date:
    """The Monday of `day`'s week - what Librus's `Timetables` expects."""
    return day - timedelta(days=day.weekday())


class Librus:
    """A signed-in Librus Synergia account (one parent/student login).

    Pass `session` to reuse your own `aiohttp.ClientSession` (it must not be
    shared with another account - see `LibrusApiClient`). Otherwise one is
    created on `__aenter__` and closed on `__aexit__`/`close()`.

    Pass `session_data` (from a previous run's `librus.session_data`) to
    resume without a fresh login and keep Librus's long-lived device cookie,
    which is what keeps logins captcha-free over time.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
        session_data: LibrusSessionData | None = None,
        on_session_update: OnSessionUpdate | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._owns_session = session is None
        self._session_data = session_data
        self._on_session_update = on_session_update
        self._client: LibrusApiClient | None = None
        self._messages_available: bool | None = None
        self._login_lock = asyncio.Lock()
        # Kindergarten accounts (see `kindergartener_id`): discovered at most
        # once per instance.
        self._kindergarten_lock = asyncio.Lock()
        self._kindergarten_checked = False
        self._student_identifier: str | None = None
        self._student_identifier_checked = False
        self._kindergarten_lid: str | None = None
        self._kindergarten_group_id: str | None = None

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

    @property
    def client(self) -> LibrusApiClient:
        """The underlying low-level client, for endpoints without a
        high-level method here."""
        if self._client is None:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            self._client = LibrusApiClient(
                self._session, self._username, on_session_update=self._on_session_update
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
        Raises `LibrusAuthError` subclasses on bad credentials/captcha."""
        async with self._login_lock:
            await self.client.async_ensure_session_valid(self._password, force=force)
        if force:
            self._messages_available = None

    async def _call(self, fetch: Callable[[], Awaitable[T]]) -> T:
        """Run one API call, re-logging in once if Librus rejected the
        session (HTTP 401) earlier than expected."""
        await self.login()
        try:
            return await fetch()
        except LibrusSessionExpiredError as err:
            if err.status_code == 403:
                raise
            await self.login(force=True)
            return await fetch()

    async def _call_messages(self, fetch: Callable[[], Awaitable[T]]) -> T | None:
        """Like `_call`, for the separate Wiadomości session. Returns None
        when the school has no messages module. The Wiadomości session dies
        independently (and more often) than the main one, so a failure gets
        one fresh bootstrap + retry."""
        await self.login()
        for attempt in range(2):
            if self._messages_available is None:
                self._messages_available = await self.client.async_bootstrap_messages()
            if not self._messages_available:
                return None
            try:
                return await fetch()
            except LibrusUnexpectedResponseError as err:
                # A 404 means "no such mailbox for this account" (confirmed
                # live for substitutions/alerts), not a dead session.
                if err.status_code == 404 or attempt:
                    raise
                self._messages_available = None
                await self.login(force=True)
            except LibrusError:
                if attempt:
                    raise
                self._messages_available = None
                await self.login(force=True)
        return None  # pragma: no cover - loop always returns or raises

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
        return parsers.parse_school(await self._call(self.client.async_get_schools))

    async def school_class(self) -> ClassData | None:
        """The class. For a kindergarten account (once detected), the
        kindergarten group instead."""
        if self._kindergarten_group_id is not None:
            group_id = self._kindergarten_group_id
            group = parsers.parse_kindergarten_group(
                await self._probe(lambda: self.client.async_get_kindergarten_group(group_id))
            )
            if group is not None:
                return group
        return parsers.parse_class(await self._call(self.client.async_get_classes))

    async def subjects(self) -> dict[int | str, str]:
        """Subject id -> name. Grades/lessons carry only the id. Includes
        kindergarten activity names once a kindergarten account is detected."""
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
        payload = await self._call(self.client.async_get_teachers)
        result: dict[int | str, str] = {
            k: v for k, v in parsers.parse_id_name_map(payload, ("Users", "Teachers")).items()
        }
        if self._kindergarten_lid is not None:
            result.update(parsers.parse_kindergarten_teachers(payload))
        return result

    async def classrooms(self) -> dict[int | str, str]:
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

    async def _probe(self, fetch: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
        """One best-effort request: any Librus error becomes `{}`."""
        try:
            result = await self._call(fetch)
        except LibrusError:
            return {}
        return result if isinstance(result, dict) else {}

    async def kindergartener_id(self) -> str | None:
        """The child's `LID-AUTH-USER-...` identifier on a kindergarten
        account, or None for a regular one.

        Kindergarten accounts get HTTP 403 from `Timetables`; their timetable
        lives in a separate API keyed by this identifier. `timetable()` calls
        this automatically after such a 403, so you rarely need it directly.
        Candidates come from `Me`, `Auth/TokenInfo` (+ `Auth/UserInfo`) and
        `Users/<id>`; the one whose kindergarten timetable has entries wins.
        Runs at most once per `Librus` instance and never raises."""
        async with self._kindergarten_lock:
            if self._kindergarten_checked:
                return self._kindergarten_lid
            self._kindergarten_checked = True
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

            today = date.today()
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
                break
            return self._kindergarten_lid

    # ------------------------------------------------------------------
    # Grades / behaviour
    # ------------------------------------------------------------------

    async def grades(self) -> list[GradeData]:
        """All grades, with teacher comments resolved to text. Turn a
        grade's `value` ("4+", "bz", ...) into a number with
        `parsers.parse_grade_value`."""
        grades, comments = await asyncio.gather(
            self._call(self.client.async_get_grades),
            self._call(self.client.async_get_grade_comments),
        )
        return parsers.parse_grades(grades, parsers.parse_comment_text_map(comments))

    async def grade_categories(self) -> dict[int, GradeCategoryData]:
        payload = await self._call(self.client.async_get_grade_categories)
        return parsers.parse_grade_categories(payload)

    async def descriptive_grades(self) -> list[DescriptiveGradeData]:
        """Descriptive grades with their skill names and comments. Both
        lookups are only fetched when there are grades, and either may fail
        without losing the grades."""
        payload = await self._call(self.client.async_get_descriptive_grades)
        if not payload.get("Grades"):
            return []

        async def lookup(fetch: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
            try:
                return await self._call(fetch)
            except LibrusError:
                return {}

        skills, comments = await asyncio.gather(
            lookup(self.client.async_get_descriptive_grade_skills),
            lookup(self.client.async_get_descriptive_grade_comments),
        )
        return parsers.parse_descriptive_grades(
            payload,
            parsers.parse_descriptive_skills(skills),
            parsers.parse_comment_text_map(comments),
        )

    async def student_identifier(self) -> str | None:
        """The child's LID (`Auth/UserInfo/<token user>` ->
        `IdentifierOfStudentAssignedWithUser`), looked up once; None when
        Librus doesn't give one."""
        if not self._student_identifier_checked:
            self._student_identifier_checked = True
            token_lid = parsers.extract_token_user_identifier(
                await self._probe(self.client.async_get_token_info)
            )
            if token_lid:
                self._student_identifier = parsers.extract_student_identifier(
                    await self._probe(lambda: self.client.async_get_user_info(token_lid))
                )
        return self._student_identifier

    async def partial_grades(self) -> list[DescriptiveGradeData]:
        """Grades from the new descriptive grading (grade 1 at some schools
        from 2026), as `DescriptiveGradeData` with `source="partial"` -
        see `parsers.parse_partial_grades`. Empty when Librus has none or
        no child LID is known."""
        student = await self.student_identifier()
        if not student:
            return []
        payload = await self._call(lambda: self.client.async_get_partial_grades(student))
        if not payload.get("data"):
            return []
        subjects = parsers.parse_auth_subjects(
            await self._probe(self.client.async_get_auth_subjects)
        )
        return parsers.parse_partial_grades(payload, subjects)

    async def grading_system(self) -> GradingSystemData:
        """The school's grade scale settings ("+" / "-" values, whether 0
        counts). Pass it to `parsers.parse_grade_value`."""
        return parsers.parse_grading_system(await self._call(self.client.async_get_grading_system))

    async def point_grades(self) -> list[PointGradeData]:
        """Point grades (schools grading in points or percent), with each
        category's maximum and weight. Average them with
        `parsers.point_grades_percentage`."""
        grades, categories = await asyncio.gather(
            self._call(self.client.async_get_point_grades),
            self._call(self.client.async_get_point_grade_categories),
        )
        return parsers.parse_point_grades(grades, parsers.parse_point_grade_categories(categories))

    async def behaviour_grades(self) -> list[BehaviourGradeData]:
        points, comments = await asyncio.gather(
            self._call(self.client.async_get_behaviour_grade_points),
            self._call(self.client.async_get_behaviour_grade_point_comments),
        )
        return parsers.parse_behaviour_grades(points, parsers.parse_comment_text_map(comments))

    async def notes(self) -> list[NoteData]:
        """Behaviour notes ("uwagi"), with `sentiment` resolved."""
        return parsers.parse_notes(await self._call(self.client.async_get_notes))

    async def note_categories(self) -> dict[int, str]:
        payload = await self._call(self.client.async_get_note_categories)
        return parsers.parse_id_name_map(payload, ("Categories",))

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------

    async def attendances(self) -> list[AttendanceData]:
        return parsers.parse_attendances(await self._call(self.client.async_get_attendances))

    async def attendance_types(self) -> dict[int, AttendanceTypeData]:
        """Type id -> type. `is_presence_kind` tells a real absence from a
        present/late mark."""
        payload = await self._call(self.client.async_get_attendance_types)
        return parsers.parse_attendance_types(payload)

    # ------------------------------------------------------------------
    # Timetable / agenda
    # ------------------------------------------------------------------

    async def timetable(self, week_of: date | None = None) -> dict[date, list[LessonData]]:
        """Lessons for the week containing `week_of` (default: this week).

        Librus answers HTTP 403 both when a school hasn't published the
        timetable yet and for kindergarten accounts. After a 403 this looks
        for a kindergarten child once (see `kindergartener_id`) and, if one
        is found, returns the kindergarten timetable (time blocks, no lesson
        numbers). Otherwise it returns `{}`."""
        start = week_start_of(week_of or date.today())
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
        payload = await self._call(self.client.async_get_homework_categories)
        return parsers.parse_id_name_map(payload, ("Categories",))

    async def homework(self) -> list[HomeworkAssignmentData]:
        """Real homework assignments ("zadania domowe")."""
        payload = await self._call(self.client.async_get_homework_assignments)
        return parsers.parse_homework_assignments(payload)

    async def free_days(self) -> list[FreeDayData]:
        school, klass = await asyncio.gather(
            self._call(self.client.async_get_school_free_days),
            self._call(self.client.async_get_class_free_days),
        )
        return parsers.parse_free_days(school, "SchoolFreeDays") + parsers.parse_free_days(
            klass, "ClassFreeDays"
        )

    async def text_grades(self) -> list[TextGradeData]:
        """Text grades (`BaseTextGrades`) - free-text grades that `grades()`
        doesn't contain - with their category names."""
        grades, categories = await asyncio.gather(
            self._call(self.client.async_get_base_text_grades),
            self._call(self.client.async_get_text_grade_categories),
        )
        return parsers.parse_text_grades(grades, parsers.parse_text_grade_categories(categories))

    async def lesson_topics(self) -> list[LessonTopicData]:
        """Lessons held with their topics (`Realizations`), newest first,
        with the subject resolved through `Lessons`."""
        topics, lessons = await asyncio.gather(
            self._call(self.client.async_get_realizations),
            self._call(self.client.async_get_lessons),
        )
        return parsers.parse_realizations(topics, parsers.parse_lesson_subjects(lessons))

    async def standing_timetable(self) -> list[StandingLessonData]:
        """The standing weekly plan (`TimetableEntries`), with the subject
        resolved through `Lessons`. `parsers.plan_differences()` compares
        it with real weeks from `timetable()`."""
        entries, lessons = await asyncio.gather(
            self._call(self.client.async_get_timetable_entries),
            self._call(self.client.async_get_lessons),
        )
        return parsers.parse_timetable_entries(entries, parsers.parse_lesson_subjects(lessons))

    async def school_trips(self) -> list[SchoolTripData]:
        return parsers.parse_school_trips(await self._call(self.client.async_get_school_trips))

    async def school_files(self) -> list[SchoolFileData]:
        """Documents the school shares with parents."""
        return parsers.parse_school_files(await self._call(self.client.async_get_school_files))

    async def homework_categories(self) -> dict[int, str]:
        """Homework assignment categories (`HomeworkAssignmentData.category_id`)."""
        payload = await self._call(self.client.async_get_homework_assignment_categories)
        return parsers.parse_id_name_map(payload, ("Categories",))

    async def download_attachment(
        self, attachment_id: str, message_id: str
    ) -> AttachmentFileData | None:
        """Download a message attachment (ids from `message()`), without
        opening the message. None when the school has no messages module."""
        return await self._call_messages(
            lambda: self.client.async_download_message_attachment(attachment_id, message_id)
        )

    async def download_homework_attachment(self, attachment_id: str) -> AttachmentFileData:
        """Download a homework-assignment attachment (ids from
        `homework_assignments()[].attachments`). Uses the main Synergia
        session, not Wiadomości."""
        return await self._call(
            lambda: self.client.async_download_homework_attachment(attachment_id)
        )

    async def download_school_file(self, download_path: str) -> AttachmentFileData:
        """Download a school document (`school_files()[].download_path`).
        Uses the main Synergia session."""
        return await self._call(lambda: self.client.async_download_school_file(download_path))

    async def justifications(self) -> list[JustificationData]:
        """Absence justifications the parent submitted, newest first, with
        their status. `parsers.justified_dates()` turns them into the days
        already covered."""
        payload = await self._call(self.client.async_get_justifications)
        return parsers.parse_justifications(payload)

    async def parent_teacher_conferences(self) -> list[ParentTeacherConferenceData]:
        payload = await self._call(self.client.async_get_parent_teacher_conferences)
        return parsers.parse_parent_teacher_conferences(payload)

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

    async def fetch_all(self, *, include_messages: bool = True) -> LibrusData:
        """Everything in one snapshot, with this week's and next week's
        timetable. Optional modules a school doesn't use come back empty
        instead of failing the whole call."""
        await self.login()
        today = date.today()

        async def optional(coro: Awaitable[Any], default: Any) -> Any:
            try:
                return await coro
            except LibrusError:
                return default

        this_week_start = week_start_of(today)
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
        ) = await asyncio.gather(
            self.me(),
            optional(self.grades(), []),
            optional(self.grade_categories(), {}),
            optional(self.notes(), []),
            optional(self.attendances(), []),
            optional(self.attendance_types(), {}),
            optional(self.timetable(this_week_start), {}),
            optional(self.timetable(this_week_start + timedelta(days=7)), {}),
            optional(self.agenda(), []),
            optional(self.announcements(), []),
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
            partial,
            grading,
            point,
            justifications,
            text_grades,
            topics_payload,
            trips,
            files,
            homework_categories,
            conferences,
            lessons_payload,
            entries_payload,
        ) = await asyncio.gather(
            optional(self.lucky_number(), None),
            optional(self.subjects(), {}),
            optional(self.teachers(), {}),
            optional(self.classrooms(), {}),
            optional(self.school(), None),
            optional(self.school_class(), None),
            optional(self.agenda_categories(), {}),
            optional(self.note_categories(), {}),
            optional(self.free_days(), []),
            optional(self.homework(), []),
            optional(self.behaviour_grades(), []),
            optional(self.descriptive_grades(), []),
            optional(self.partial_grades(), []),
            optional(self.grading_system(), GradingSystemData()),
            optional(self.point_grades(), []),
            optional(self.justifications(), []),
            optional(self.text_grades(), []),
            optional(self._call(self.client.async_get_realizations), {}),
            optional(self.school_trips(), []),
            optional(self.school_files(), []),
            optional(self.homework_categories(), {}),
            optional(self.parent_teacher_conferences(), []),
            optional(self._call(self.client.async_get_lessons), {}),
            optional(self._call(self.client.async_get_timetable_entries), {}),
        )
        lesson_subjects = parsers.parse_lesson_subjects(lessons_payload)
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
            descriptive_grades=descriptive + partial,
            grading_system=grading,
            point_grades=point,
            justifications=justifications,
            text_grades=text_grades,
            lesson_topics=parsers.parse_realizations(topics_payload, lesson_subjects),
            school_trips=trips,
            school_files=files,
            homework_assignment_categories=homework_categories,
            parent_teacher_conferences=conferences,
            lesson_subjects=lesson_subjects,
            standing_timetable=parsers.parse_timetable_entries(entries_payload, lesson_subjects),
        )
        if include_messages:
            unread = await optional(self.unread_messages(), {})
            data.messages_available = bool(self._messages_available)
            data.unread_messages_by_mailbox = unread
            data.unread_message_count = unread.get("inbox", 0)
            data.messages = await optional(self.messages(), [])
        return data
