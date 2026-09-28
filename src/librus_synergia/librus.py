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
from types import TracebackType
from typing import Any, TypeVar

import aiohttp

from . import parsers
from .client import LibrusApiClient, LibrusSessionData, OnSessionUpdate
from .exceptions import LibrusError, LibrusSessionExpiredError
from .models import (
    AttendanceData,
    AttendanceTypeData,
    BehaviourGradeData,
    ClassData,
    DescriptiveGradeData,
    FreeDayData,
    FullMessageData,
    GradeCategoryData,
    GradeData,
    HomeworkAssignmentData,
    HomeworkEventData,
    LessonData,
    LibrusData,
    LuckyNumberData,
    MeData,
    MessageData,
    NoteData,
    ParentTeacherConferenceData,
    SchoolData,
    SchoolNoticeData,
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

    async def school(self) -> SchoolData | None:
        return parsers.parse_school(await self._call(self.client.async_get_schools))

    async def school_class(self) -> ClassData | None:
        return parsers.parse_class(await self._call(self.client.async_get_classes))

    async def subjects(self) -> dict[int, str]:
        """Subject id -> name. Grades/lessons carry only the id."""
        payload = await self._call(self.client.async_get_subjects)
        return parsers.parse_id_name_map(payload, ("Subjects",))

    async def teachers(self) -> dict[int, str]:
        payload = await self._call(self.client.async_get_teachers)
        return parsers.parse_id_name_map(payload, ("Users", "Teachers"))

    async def classrooms(self) -> dict[int, str]:
        payload = await self._call(self.client.async_get_classrooms)
        return parsers.parse_id_name_map(payload, ("Classrooms",))

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
        payload = await self._call(self.client.async_get_descriptive_grades)
        return parsers.parse_descriptive_grades(payload)

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
        Returns `{}` when the school hasn't published the timetable yet
        (Librus answers that case with HTTP 403)."""
        start = week_start_of(week_of or date.today())
        try:
            payload = await self._call(lambda: self.client.async_get_timetable(start))
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
        Listing does NOT mark anything read."""
        payload = await self._call_messages(
            lambda: self.client.async_get_messages(mailbox, limit=limit)
        )
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
            conferences,
            lessons_payload,
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
            optional(self.parent_teacher_conferences(), []),
            optional(self._call(self.client.async_get_lessons), {}),
        )
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
            descriptive_grades=descriptive,
            parent_teacher_conferences=conferences,
            lesson_subjects=parsers.parse_lesson_subjects(lessons_payload),
        )
        if include_messages:
            unread = await optional(self.unread_messages(), {})
            data.messages_available = bool(self._messages_available)
            data.unread_messages_by_mailbox = unread
            data.unread_message_count = unread.get("inbox", 0)
            data.messages = await optional(self.messages(), [])
        return data
