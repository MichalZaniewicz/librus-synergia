"""Pure parsers turning raw Librus JSON payloads into typed models.

No I/O here: every function takes the dict returned by one of
`LibrusApiClient.async_get_*` and returns dataclasses from `.models`.
Every quirk handled below was confirmed against a real account - see
`docs/` for the endpoint-by-endpoint notes.
"""

from __future__ import annotations

import base64
import re
from datetime import date, timedelta
from html import unescape as html_unescape
from typing import Any

from .models import (
    AttachmentData,
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
    JustificationData,
    LessonData,
    LessonTopicData,
    LuckyNumberData,
    MeData,
    MessageData,
    NoteData,
    OriginalLessonData,
    ParentTeacherConferenceData,
    PointGradeCategoryData,
    PointGradeData,
    SchoolData,
    SchoolFileData,
    SchoolNoticeData,
    SchoolTripData,
    TextGradeData,
)

LID_USER_PREFIX = "LID-AUTH-USER-"


def parse_grade_value(value: str) -> float | None:
    """Convert a Librus grade string ("5+", "4-", "3", "bz"...) to a number.

    The "+"/"-" modifiers (+0.5 / -0.25) follow the convention used by most
    third-party Polish gradebook average calculators. CONFIRMED live
    (2026-09-05) via the `Grades/Types` reference endpoint that every
    non-numeric value Librus actually uses (`bz`, `np`, `nk`, `uł`, `nł`,
    `zl`, `nz`, `zw`, `uc`, `nu`, bare `+`/`-`) correctly falls through to
    returning None here and is excluded from the average.

    The +0.5 half is now CONFIRMED correct (2026-09-22): a raw probe of
    Librus's own `/Grades` API (real `4+`/`5+` grades that appeared live,
    2026-09) showed it carries NO numeric value for a modified grade at
    all - only the string (`"Grade": "4+"`), so this function computing
    4.5 from that string is not, by itself, proof of anything (it's the
    same assumed convention checking itself). The real independent check
    was cross-referencing the real Librus app's own displayed average for
    that `4+` grade - also 4.5, confirmed by the account owner. The -0.25
    half is CONFIRMED the same way (2026-09-28): a subject with a real `6`
    and `4-` shows 4.88 in the Librus app, i.e. (6 + 3.75) / 2.
    """
    value = value.strip()
    if not value:
        return None
    modifier = 0.0
    if value.endswith("+"):
        modifier = 0.5
        value = value[:-1]
    elif value.endswith("-"):
        modifier = -0.25
        value = value[:-1]
    try:
        number = float(value.replace(",", ".")) + modifier
    except ValueError:
        return None
    # Only the 1-6 scale is a grade here. A school grading in points or
    # percent (e.g. "85") would otherwise drag every average far off -
    # those belong in `PointGrades` (see `parse_point_grades`).
    if not 0 < number <= 6.5:
        return None
    return number


def parse_me(payload: dict[str, Any]) -> MeData:
    me = payload.get("Me") or {}
    account = me.get("Account") or {}
    # CONFIRMED live: `Account` is the LOGIN's own identity, which for a
    # child's account under a parent-managed portal is the PARENT's name
    # (e.g. Account.FirstName/LastName was the parent, while `User` was the
    # actual student) - `MeData` is meant to represent the student, so read
    # the name from `User`, keeping only the id from `Account`.
    student = me.get("User") or {}
    return MeData(
        account_id=account.get("Id"),
        first_name=student.get("FirstName", ""),
        last_name=student.get("LastName", ""),
    )


def parse_grade_categories(payload: dict[str, Any]) -> dict[int, GradeCategoryData]:
    items = payload.get("Categories")
    if not isinstance(items, list):
        return {}
    result: dict[int, GradeCategoryData] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        item_id = int(item["Id"])
        # BUG FIX (code review): `bool(item.get("CountToTheAverage", True))`
        # only applied the `True` default when the KEY was absent - an
        # explicit JSON `null` resolved to `bool(None)` == False. Same
        # "present but null" failure mode already hit and fixed once for
        # Users[].FirstName (see const.py's/CLAUDE.md's "Empirically
        # confirmed" notes) - an explicit null must default to True
        # ("counts unless explicitly told False"), same as a missing key.
        raw_count_to_average = item.get("CountToTheAverage")
        count_to_average = True if raw_count_to_average is None else bool(raw_count_to_average)
        # BUG FIX (code review): `int(item.get("Weight") or 1)` silently
        # coerced a legitimate API `Weight: 0` to `1` via Python's
        # falsy-zero evaluation (`0 or 1` == `1`) - only a genuinely
        # absent/None Weight should default to 1.
        raw_weight = item.get("Weight")
        weight = int(raw_weight) if raw_weight is not None else 1
        result[item_id] = GradeCategoryData(
            id=item_id,
            name=item.get("Name", ""),
            count_to_average=count_to_average,
            weight=weight,
        )
    return result


def parse_comment_text_map(payload: dict[str, Any] | None) -> dict[int, str]:
    """Parses a `{"Comments": [{"Id", "Text"}, ...]}`-shaped payload (used
    by both `Grades/Comments` and `BehaviourGrades/Points/Comments`,
    CONFIRMED live 2026-09-06 to share this shape) into an id->text map."""
    if not payload:
        return {}
    items = payload.get("Comments")
    if not isinstance(items, list):
        return {}
    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        text = item.get("Text")
        if text:
            result[int(item["Id"])] = text
    return result


def resolve_comment_ids(raw: Any, comment_text_by_id: dict[int, str]) -> list[str]:
    """Resolves a per-grade/-behaviour-grade `Comments` field against a
    `parse_comment_text_map` lookup.

    CONFIRMED (2026-09-06) via szkolny-android's reference parsers that
    this field is a list of ids into the separate Comments endpoint, NOT
    embedded `{"Text": ...}` objects as previously assumed here - but the
    exact per-id shape (bare int vs. `{"Id": ...}`) is still unconfirmed
    (empty on this account either way), so both are handled, plus the old
    embedded-`Text` shape as a fallback in case that turns out right after
    all.
    """
    if not isinstance(raw, list):
        return []
    resolved: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            if entry.get("Text"):
                resolved.append(str(entry["Text"]))
                continue
            comment_id = entry.get("Id")
        else:
            comment_id = entry
        if comment_id is None:
            continue
        try:
            text = comment_text_by_id.get(int(comment_id))
        except (TypeError, ValueError):
            text = None
        if text:
            resolved.append(text)
    return resolved


def parse_grades(
    payload: dict[str, Any], comment_text_by_id: dict[int, str] | None = None
) -> list[GradeData]:
    items = payload.get("Grades")
    if not isinstance(items, list):
        return []
    grades: list[GradeData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        category = item.get("Category") or {}
        subject = item.get("Subject") or {}
        added_by = item.get("AddedBy") or {}
        improvement = item.get("Improvement")
        comments = resolve_comment_ids(item.get("Comments"), comment_text_by_id or {})
        grades.append(
            GradeData(
                id=int(item["Id"]),
                value=str(item.get("Grade", "")),
                category_id=category.get("Id"),
                subject_id=subject.get("Id"),
                semester=item.get("Semester"),
                add_date=item.get("AddDate"),
                is_semester_proposition=bool(item.get("IsSemesterProposition")),
                is_final_proposition=bool(item.get("IsFinalProposition")),
                # See GradeData.is_semester/is_final's own docstring - the
                # ACTUAL semester/year grade, distinct from the proposed
                # one above. Not confirmed live yet (no real semester-end
                # data on the test account), but confirmed via the
                # reference parser's own field names.
                is_semester=bool(item.get("IsSemester")),
                is_final=bool(item.get("IsFinal")),
                comments=comments,
                teacher_id=as_int(added_by.get("Id")) if isinstance(added_by, dict) else None,
                improves_id=(
                    as_int(improvement.get("Id")) if isinstance(improvement, dict) else None
                ),
            )
        )
    return grades


def parse_notes(payload: dict[str, Any]) -> list[NoteData]:
    items = payload.get("Notes")
    if not isinstance(items, list):
        return []
    notes: list[NoteData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        category = item.get("Category") or {}
        teacher = item.get("Teacher") or {}
        notes.append(
            NoteData(
                id=int(item["Id"]),
                text=item.get("Text", ""),
                category_id=category.get("Id"),
                teacher_id=teacher.get("Id"),
                date=item.get("Date"),
                positive=item.get("Positive"),
            )
        )
    return notes


def parse_attendances(payload: dict[str, Any]) -> list[AttendanceData]:
    items = payload.get("Attendances")
    if not isinstance(items, list):
        return []
    attendances: list[AttendanceData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        lesson = item.get("Lesson") or {}
        type_ = item.get("Type") or {}
        raw_id = item["Id"]
        try:
            item_id: int | str = int(raw_id)
        except (TypeError, ValueError):
            # CONFIRMED live: some records use a "t"-prefixed id (e.g.
            # "t41685") instead of a plain numeric one - see AttendanceData.
            item_id = str(raw_id)
        raw_type_id = type_.get("Id")
        type_id: int | str | None
        if raw_type_id is None:
            type_id = None
        else:
            try:
                type_id = int(raw_type_id)
            except (TypeError, ValueError):
                # BUG FIX (code review): same defensive fallback as the
                # sibling `id` field above - not confirmed live for Type.Id
                # specifically, but this API has already proven the record's
                # own Id can be "t"-prefixed, so Type.Id could plausibly do
                # the same someday. A raw str here just misses
                # attendance_types.get(...) (keyed by int) and is treated
                # as an unknown type, instead of crashing the whole
                # coordinator update.
                type_id = str(raw_type_id)
        attendances.append(
            AttendanceData(
                id=item_id,
                lesson_id=lesson.get("Id"),
                lesson_no=item.get("LessonNo"),
                date=item.get("Date"),
                semester=item.get("Semester"),
                type_id=type_id,
            )
        )
    return attendances


def parse_attendance_types(payload: dict[str, Any]) -> dict[int, AttendanceTypeData]:
    # CONFIRMED live: the response root key is "Types" (matching the
    # Attendances/Types endpoint path), not "AttendanceTypes". `IsPresenceKind`
    # is real - see AttendanceTypeData's docstring.
    items = payload.get("Types")
    if not isinstance(items, list):
        return {}
    result: dict[int, AttendanceTypeData] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        item_id = int(item["Id"])
        name = item.get("Name") or item.get("Short") or item.get("Shortcut") or ""
        result[item_id] = AttendanceTypeData(
            id=item_id, name=name, is_presence_kind=bool(item.get("IsPresenceKind"))
        )
    return result


def as_int(value: Any) -> int | None:
    """Coerce an id to int. CONFIRMED live: Timetables returns Subject/
    Teacher/Classroom/Lesson ids as STRINGS ("41999"), unlike every other
    endpoint (Grades, Attendances, ...) which use plain ints - normalize
    here so lookups against `subjects`/`teachers`/`classrooms` (keyed by
    int) work regardless of which endpoint an id came from."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_lesson(raw: dict[str, Any]) -> LessonData:
    subject = raw.get("Subject") or {}
    teacher = raw.get("Teacher") or {}
    classroom = raw.get("Classroom") or {}
    teacher_id = as_int(teacher.get("Id"))
    return LessonData(
        lesson_no=as_int(raw.get("LessonNo")),
        hour_from=raw.get("HourFrom"),
        hour_to=raw.get("HourTo"),
        subject_id=as_int(subject.get("Id")),
        teacher_id=teacher_id,
        teacher_ids=(teacher_id,) if teacher_id is not None else (),
        classroom_id=as_int(classroom.get("Id")),
        is_canceled=bool(raw.get("IsCanceled")),
        is_substitution=bool(raw.get("IsSubstitutionClass")),
        original=_parse_original_lesson(raw),
        substitution_note=raw.get("SubstitutionNote") or None,
    )


def _parse_original_lesson(raw: dict[str, Any]) -> OriginalLessonData | None:
    """The `Org*` fields of a substituted/moved lesson (None when absent)."""
    if not any(key.startswith("Org") for key in raw):
        return None

    def ref(key: str) -> int | None:
        value = raw.get(key)
        return as_int(value.get("Id")) if isinstance(value, dict) else None

    return OriginalLessonData(
        date=raw.get("OrgDate"),
        lesson_no=as_int(raw.get("OrgLessonNo")),
        hour_from=raw.get("OrgHourFrom"),
        hour_to=raw.get("OrgHourTo"),
        subject_id=ref("OrgSubject"),
        teacher_id=ref("OrgTeacher"),
        classroom_id=ref("OrgClassroom"),
    )


def merge_kindergarten_entries(entries: list[Any], result: dict[date, list[LessonData]]) -> None:
    """Add kindergarten `timetableEntries` (PR #8) to `result`.

    Unlike `Timetables`, identifiers are LID strings
    (`activityTypeIdentifier`, `classroomIdentifier`, `teachers[]`) and
    there's no lesson number - entries are plain time blocks, so
    `lesson_no` stays None."""
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        try:
            day = date.fromisoformat(str(raw.get("date"))[:10])
        except ValueError:
            continue
        activity_id = raw.get("activityTypeIdentifier")
        classroom_id = raw.get("classroomIdentifier")
        raw_teachers = raw.get("teachers")
        teacher_ids = tuple(
            str(value)
            for value in (raw_teachers if isinstance(raw_teachers, list) else ())
            if isinstance(value, (str, int)) and str(value)
        )
        # Only "planned" has been seen live; the rest is a best guess.
        entry_type = str(raw.get("type") or "planned").lower()
        result.setdefault(day, []).append(
            LessonData(
                lesson_no=None,
                hour_from=raw.get("startTime"),
                hour_to=raw.get("endTime"),
                subject_id=str(activity_id) if activity_id is not None else None,
                teacher_id=teacher_ids[0] if teacher_ids else None,
                classroom_id=str(classroom_id) if classroom_id is not None else None,
                is_canceled="cancel" in entry_type,
                is_substitution="substitut" in entry_type,
                teacher_ids=teacher_ids,
            )
        )


def merge_timetables(*payloads: dict[str, Any]) -> dict[date, list[LessonData]]:
    """Merge one or more `Timetables?weekStart=...` responses into a single
    date-keyed dict of lessons.

    CONFIRMED live: each date maps to a list of PERIOD SLOTS (one per
    lesson-number, always the same length even on days with no school),
    each itself a list of 0+ lesson dicts (more than one when a period is
    split into parallel groups, e.g. two language classes at once) - NOT a
    flat list of lessons per day as the reverse-engineered spec assumed.

    Also accepts the kindergarten API's `timetableEntries` payload (see
    `_merge_kindergarten_entries`), so the calendar/coordinator don't need
    to know which kind of account they're serving.
    """
    result: dict[date, list[LessonData]] = {}
    for payload in payloads:
        entries = payload.get("timetableEntries")
        if isinstance(entries, list):
            merge_kindergarten_entries(entries, result)
            continue
        timetable = payload.get("Timetable")
        if not isinstance(timetable, dict):
            continue
        for date_str, day_slots in timetable.items():
            if not isinstance(day_slots, list):
                continue
            try:
                day = date.fromisoformat(date_str)
            except (TypeError, ValueError):
                continue
            lessons: list[LessonData] = []
            for slot in day_slots:
                if not isinstance(slot, list):
                    continue
                lessons.extend(parse_lesson(lesson) for lesson in slot if isinstance(lesson, dict))
            result[day] = lessons
    return result


def collect_lid_user_identifiers(value: Any) -> list[str]:
    """Every `LID-AUTH-USER-...` string anywhere in a payload, in order."""
    found: list[str] = []
    if isinstance(value, str):
        if value.startswith(LID_USER_PREFIX):
            found.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            found.extend(collect_lid_user_identifiers(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(collect_lid_user_identifiers(nested))
    return list(dict.fromkeys(found))


def extract_token_user_identifier(payload: dict[str, Any]) -> str | None:
    for key in ("UserIdentifier", "userIdentifier", "Identifier", "identifier"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(LID_USER_PREFIX):
            return value
    return None


def parse_kindergarten_activity_types(payload: dict[str, Any]) -> dict[int | str, str]:
    """`kindergartens/activities-types` -> identifier: name ("Religia"...)."""
    items = payload.get("activitiesTypes")
    if not isinstance(items, list):
        return {}
    result: dict[int | str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = item.get("identifier")
        name = item.get("name")
        if isinstance(identifier, str) and identifier and isinstance(name, str) and name:
            result[identifier] = name
    return result


def parse_kindergarten_teachers(payload: dict[str, Any]) -> dict[int | str, str]:
    """`Users` keyed by `AccountId` - what kindergarten `teachers[]` holds."""
    items = payload.get("Users")
    if not isinstance(items, list):
        return {}
    result: dict[int | str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = item.get("AccountId")
        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool):
            continue
        name = f"{item.get('FirstName') or ''} {item.get('LastName') or ''}".strip()
        if name:
            result[str(identifier)] = name
    return result


def parse_kindergarten_classrooms(payload: dict[str, Any]) -> dict[int | str, str]:
    """`Auth/Classrooms` -> identifier: room name.

    Prefers `name` ("sala 1") over the bare `symbol` ("1"). A purely numeric
    value gets a ``sala`` prefix, since the cards show the room verbatim and
    a bare number reads as meaningless; anything else ("s. 1", "12a", "Sala
    gimnastyczna") is kept exactly as Librus returns it.
    """
    items = payload.get("data")
    if not isinstance(items, list):
        return {}
    result: dict[int | str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = item.get("identifier")
        room = item.get("name") or item.get("symbol")
        if not isinstance(identifier, (str, int)) or not room:
            continue
        room = str(room).strip()
        if not room:
            continue
        if room.isdigit():
            room = f"sala {room}"
        result[str(identifier)] = room
    return result


def parse_kindergarten_group(payload: dict[str, Any]) -> ClassData | None:
    """A kindergarten group mapped onto the class model (name + first tutor)."""
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None
    tutors = payload.get("tutors")
    tutor_id = (
        next(
            (value for value in tutors if isinstance(value, str) and value),
            None,
        )
        if isinstance(tutors, list)
        else None
    )
    return ClassData(
        number=None,
        symbol=name,
        tutor_id=tutor_id,
        begin_school_year=None,
        end_first_semester=None,
        end_school_year=None,
    )


def parse_homeworks(payload: dict[str, Any]) -> list[HomeworkEventData]:
    items = payload.get("HomeWorks")
    if not isinstance(items, list):
        return []
    events: list[HomeworkEventData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        category = item.get("Category") or {}
        subject = item.get("Subject") or {}
        events.append(
            HomeworkEventData(
                id=int(item["Id"]),
                date=item.get("Date"),
                content=item.get("Content", ""),
                category_id=category.get("Id"),
                subject_id=subject.get("Id"),
                time_from=item.get("TimeFrom"),
            )
        )
    return events


def parse_homework_assignments(payload: dict[str, Any]) -> list[HomeworkAssignmentData]:
    """Real homework assignments ("Zadania domowe") - distinct from the
    general `HomeWorks` agenda feed above (`parse_homeworks`), which
    covers tests/trips/etc. too. Fields CONFIRMED (2026-09-06) via
    szkolny-android's `LibrusApiHomework.kt`. Notably NO `Subject` field
    appears in the reference parser - unlike the general agenda feed,
    there's no subject to resolve here. Still empty on this account, so
    unverified against a real populated example."""
    items = payload.get("HomeWorkAssignments")
    if not isinstance(items, list):
        return []
    assignments: list[HomeworkAssignmentData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        teacher = item.get("Teacher") or {}
        assignments.append(
            HomeworkAssignmentData(
                id=int(item["Id"]),
                topic=item.get("Topic", ""),
                text=item.get("Text", ""),
                teacher_id=teacher.get("Id"),
                date=item.get("Date"),
                due_date=item.get("DueDate"),
                category_id=as_int((item.get("Category") or {}).get("Id")),
                lesson_id=as_int((item.get("Lesson") or {}).get("Id")),
            )
        )
    return assignments


def parse_behaviour_grades(
    payload: dict[str, Any], comment_text_by_id: dict[int, str] | None = None
) -> list[BehaviourGradeData]:
    """A formal "ocena zachowania" (behaviour grade) - distinct from Notes
    ("uwagi", free-text remarks). Fields via szkolny-android's
    `LibrusApiBehaviourGrades.kt`; the classic-scale grade is in
    `BehaviourGrade.Id` (see `BEHAVIOUR_GRADE_TYPES`), found live when a
    real "bdb" came through with empty `ShortName`/`Text`."""
    items = payload.get("Grades")
    if not isinstance(items, list):
        return []
    grades: list[BehaviourGradeData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        category = item.get("Category") or {}
        added_by = item.get("AddedBy") or {}
        behaviour_grade = item.get("BehaviourGrade")
        comments = resolve_comment_ids(item.get("Comments"), comment_text_by_id or {})
        grades.append(
            BehaviourGradeData(
                id=int(item["Id"]),
                value=item.get("Value"),
                short_name=item.get("ShortName") or "",
                semester=item.get("Semester"),
                category_id=category.get("Id"),
                teacher_id=added_by.get("Id"),
                add_date=item.get("AddDate"),
                text=item.get("Text") or "",
                comments=comments,
                grade_id=as_int(behaviour_grade.get("Id"))
                if isinstance(behaviour_grade, dict)
                else None,
            )
        )
    return grades


def parse_descriptive_grades(payload: dict[str, Any]) -> list[DescriptiveGradeData]:
    """An alternate, non-numeric grading system - CONFIRMED (via the
    `Units` endpoint) to be enabled for this school, unlike `PointGrades`.
    Fields CONFIRMED (2026-09-06) via szkolny-android's
    `LibrusApiDescriptiveGrades.kt`. `Skill`/`Category` are kept as raw ids
    - their own name-lookup endpoints (`DescriptiveGrades/Skills`,
    `/Types`) weren't probed this session, so no name to resolve them to
    yet. Still empty on this account, so unverified against a real
    populated example."""
    items = payload.get("Grades")
    if not isinstance(items, list):
        return []
    grades: list[DescriptiveGradeData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        subject = item.get("Subject") or {}
        skill = item.get("Skill") or {}
        category = item.get("Category") or {}
        grades.append(
            DescriptiveGradeData(
                id=int(item["Id"]),
                subject_id=subject.get("Id"),
                value=item.get("Grade", ""),
                skill_id=skill.get("Id"),
                category_id=category.get("Id"),
                add_date=item.get("AddDate"),
            )
        )
    return grades


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def parse_point_grade_categories(payload: dict[str, Any]) -> dict[int, PointGradeCategoryData]:
    """`PointGrades/Categories` -> {id: category}. Fields per
    szkolny-android's `LibrusApiPointGradeCategories.kt` (`Name`, `Weight`,
    `CountToTheAverage`, `ValueFrom`, `ValueTo`); not seen live."""
    items = payload.get("Categories")
    if not isinstance(items, list):
        return {}
    categories: dict[int, PointGradeCategoryData] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        category_id = as_int(item["Id"])
        if category_id is None:
            continue
        counts = item.get("CountToTheAverage")
        categories[category_id] = PointGradeCategoryData(
            id=category_id,
            name=item.get("Name") or "",
            weight=_to_float(item.get("Weight")),
            counts_to_average=True if counts is None else bool(counts),
            value_from=_to_float(item.get("ValueFrom")),
            value_to=_to_float(item.get("ValueTo")),
        )
    return categories


def parse_point_grades(
    payload: dict[str, Any], categories: dict[int, PointGradeCategoryData] | None = None
) -> list[PointGradeData]:
    """`PointGrades` -> point grades, with the maximum, weight and category
    name taken from `categories` (`parse_point_grade_categories`). Fields per
    szkolny-android's `LibrusApiPointGrades.kt` (`Grade`, `GradeValue`,
    `Category`, `Subject`, `Semester`, `AddDate`, `AddedBy`); not seen
    live. `GradeValue` is the points; `Grade` the text shown in Synergia."""
    items = payload.get("Grades")
    if not isinstance(items, list):
        return []
    categories = categories or {}
    grades: list[PointGradeData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        grade_id = as_int(item["Id"])
        if grade_id is None:
            continue
        category_id = as_int((item.get("Category") or {}).get("Id"))
        category = categories.get(category_id) if category_id is not None else None
        value = str(item.get("Grade") or "")
        points = _to_float(item.get("GradeValue"))
        if points is None:
            points = _to_float(value)
        grades.append(
            PointGradeData(
                id=grade_id,
                subject_id=as_int((item.get("Subject") or {}).get("Id")),
                value=value,
                points=points,
                max_points=category.value_to if category else None,
                category_id=category_id,
                category=category.name if category else None,
                weight=category.weight if category else None,
                counts_to_average=category.counts_to_average if category else True,
                semester=as_int(item.get("Semester")),
                add_date=item.get("AddDate"),
                teacher_id=as_int((item.get("AddedBy") or {}).get("Id")),
            )
        )
    return grades


def _ref(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    return as_int(value.get("Id")) if isinstance(value, dict) else None


def parse_text_grade_categories(payload: dict[str, Any]) -> dict[int, tuple[str, bool]]:
    """`TextGrades/Categories` -> {id: (name, counts to the average)}."""
    result: dict[int, tuple[str, bool]] = {}
    for item in payload.get("Categories") or []:
        if not isinstance(item, dict):
            continue
        category_id = as_int(item.get("Id"))
        if category_id is not None:
            result[category_id] = (str(item.get("Name") or ""), bool(item.get("CountToTheAverage")))
    return result


def parse_text_grades(
    payload: dict[str, Any], categories: dict[int, tuple[str, bool]] | None = None
) -> list[TextGradeData]:
    """`BaseTextGrades` -> text grades (newest first). CONFIRMED live
    2026-10-07; grades with `ShowInGradesView: false` are skipped."""
    categories = categories or {}
    result: list[TextGradeData] = []
    for item in payload.get("Grades") or []:
        if not isinstance(item, dict) or item.get("ShowInGradesView") is False:
            continue
        grade_id = as_int(item.get("Id"))
        if grade_id is None:
            continue
        category_id = _ref(item, "Category")
        category = categories.get(category_id) if category_id is not None else None
        result.append(
            TextGradeData(
                id=grade_id,
                # Librus keeps the teacher's line breaks and indentation
                # ("...sesja I\n      80%", seen live) - collapse them.
                value=" ".join(str(item.get("Grade") or "").split()),
                subject_id=_ref(item, "Subject"),
                lesson_id=_ref(item, "Lesson"),
                category_id=category_id,
                category=category[0] if category else None,
                teacher_id=_ref(item, "AddedBy"),
                date=item.get("Date"),
                add_date=item.get("AddDate"),
                semester=as_int(item.get("Semester")),
                counts_to_average=category[1] if category else False,
            )
        )
    result.sort(key=lambda g: g.add_date or g.date or "", reverse=True)
    return result


def parse_realizations(
    payload: dict[str, Any], lesson_subjects: dict[int, int] | None = None
) -> list[LessonTopicData]:
    """`Realizations` -> lessons held with their topics, newest first.
    `lesson_subjects` (`parse_lesson_subjects`) fills in the subject.
    CONFIRMED live 2026-10-07."""
    lesson_subjects = lesson_subjects or {}
    result: list[LessonTopicData] = []
    for item in payload.get("Realizations") or []:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        lesson_id = _ref(item, "Lesson")
        lesson_no = as_int(item.get("LessonNo"))
        if lesson_no is None:
            lesson_no = as_int(item.get("LessonNumber"))
        result.append(
            LessonTopicData(
                id=str(item["Id"]),
                date=item.get("Date"),
                lesson_no=lesson_no,
                lesson_id=lesson_id,
                topic=str(item.get("Topic") or "").strip(),
                is_trip=bool(item.get("IsTrip")),
                teacher_id=_ref(item, "AddedBy"),
                subject_id=lesson_subjects.get(lesson_id) if lesson_id is not None else None,
            )
        )
    result.sort(key=lambda t: ((t.date or ""), t.lesson_no or 0), reverse=True)
    return result


def parse_school_trips(payload: dict[str, Any]) -> list[SchoolTripData]:
    """`SchoolTrips` -> trips, soonest first. Lowercase fields inside a
    `Data` list. CONFIRMED live 2026-10-07."""
    result: list[SchoolTripData] = []
    for item in payload.get("Data") or []:
        if not isinstance(item, dict):
            continue
        trip_id = as_int(item.get("id"))
        if trip_id is None:
            continue
        creator = " ".join(
            str(part) for part in (item.get("creatorName"), item.get("creatorLastName")) if part
        )
        result.append(
            SchoolTripData(
                id=trip_id,
                destination=str(item.get("destination") or "").strip(),
                route=str(item.get("route") or "").strip(),
                transport=str(item.get("locomotion") or "").strip(),
                date_from=item.get("termFrom"),
                date_to=item.get("termTo") or item.get("termFrom"),
                coordinator=item.get("coordinatorName") or creator or None,
            )
        )
    result.sort(key=lambda t: t.date_from or "")
    return result


def parse_school_files(payload: dict[str, Any]) -> list[SchoolFileData]:
    """`SchoolFiles` -> documents shared by the school, newest first.
    CONFIRMED live 2026-10-07."""
    result: list[SchoolFileData] = []
    for item in payload.get("Data") or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        if item.get("failed") is True:
            continue
        result.append(
            SchoolFileData(
                id=str(item["id"]),
                name=str(item.get("displayName") or "").strip(),
                added=item.get("addedOnDate"),
                download_path=item.get("downloadUrl"),
            )
        )
    result.sort(key=lambda f: f.added or "", reverse=True)
    return result


def parse_user_class_register_number(payload: dict[str, Any]) -> int | None:
    """`Users/{Me.Account.UserId}` -> the student's class register number
    (`User.ClassRegisterNumber`). CONFIRMED live 2026-10-07."""
    user = payload.get("User")
    if not isinstance(user, dict):
        return None
    return as_int(user.get("ClassRegisterNumber"))


def parse_justifications(payload: dict[str, Any]) -> list[JustificationData]:
    """`Justifications` -> the parent's absence justifications, newest
    first. CONFIRMED live 2026-10-07 (lowercase envelope, `data` list)."""
    items = payload.get("data")
    if not isinstance(items, list):
        return []
    result: list[JustificationData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        justification_id = as_int(item["id"])
        if justification_id is None:
            continue
        lessons = [
            (lesson.get("date"), as_int(lesson.get("number")))
            for lesson in item.get("lessons") or []
            if isinstance(lesson, dict)
        ]
        teachers = [
            str(t["name"])
            for t in item.get("notifiedTeachers") or []
            if isinstance(t, dict) and t.get("name")
        ]
        result.append(
            JustificationData(
                id=justification_id,
                status=str(item.get("justificationStatus") or ""),
                message=str(item.get("messageFromParent") or ""),
                posted=item.get("postDate"),
                date_from=item.get("dateFrom"),
                date_to=item.get("dateTo"),
                lessons=lessons,
                justified_absences=as_int(item.get("justifiedAbsences")) or 0,
                has_attachment=bool(item.get("attachment")),
                teachers=teachers,
            )
        )
    result.sort(key=lambda j: j.posted or "", reverse=True)
    return result


def justified_dates(justifications: list[JustificationData]) -> set[str]:
    """Every date ("YYYY-MM-DD") covered by a justification that wasn't
    rejected - the days a parent has already sent one for."""
    days: set[str] = set()
    for item in justifications:
        if item.is_rejected:
            continue
        for day, _number in item.lessons:
            if day:
                days.add(day[:10])
        try:
            start = date.fromisoformat((item.date_from or "")[:10])
            end = date.fromisoformat((item.date_to or item.date_from or "")[:10])
        except ValueError:
            continue
        if (end - start).days > 366:
            continue
        current = start
        while current <= end:
            days.add(current.isoformat())
            current += timedelta(days=1)
    return days


def point_grades_percentage(grades: list[PointGradeData]) -> float | None:
    """Points earned as a percentage of points possible - weighted by the
    category weight (1 when unknown), counting only grades whose category
    counts towards the average and whose maximum is known. The usual way a
    point-graded school averages; None when nothing counts."""
    earned = possible = 0.0
    for grade in grades:
        if not grade.counts_to_average or grade.points is None or not grade.max_points:
            continue
        weight = grade.weight if grade.weight else 1.0
        earned += grade.points * weight
        possible += grade.max_points * weight
    if not possible:
        return None
    return round(100 * earned / possible, 1)


def point_grades_enabled(units_payload: dict[str, Any]) -> bool | None:
    """Whether the school grades in points, from `Units`
    (`GradesSettings.PointGradesEnabled`). Searched anywhere in the payload,
    since a school can list several units: True if any unit has it on, False
    if every unit that says has it off, None if no unit says."""
    found: list[bool] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "PointGradesEnabled" and isinstance(value, bool):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(units_payload)
    if not found:
        return None
    return any(found)


def parse_parent_teacher_conferences(payload: dict[str, Any]) -> list[ParentTeacherConferenceData]:
    """Fields CONFIRMED (2026-09-06) via szkolny-android's
    `LibrusApiPtMeetings.kt`. Live-verified separately that this kind of
    meeting already surfaces through `HomeWorks` too - see
    `ParentTeacherConferenceData`'s docstring. Never seen populated here."""
    items = payload.get("ParentTeacherConferences")
    if not isinstance(items, list):
        return []
    conferences: list[ParentTeacherConferenceData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        teacher = item.get("Teacher") or {}
        conferences.append(
            ParentTeacherConferenceData(
                id=int(item["Id"]),
                topic=item.get("Topic", ""),
                teacher_id=teacher.get("Id"),
                date=item.get("Date"),
                time=item.get("Time"),
            )
        )
    return conferences


def parse_school_notices(payload: dict[str, Any]) -> list[SchoolNoticeData]:
    items = payload.get("SchoolNotices")
    if not isinstance(items, list):
        return []
    notices: list[SchoolNoticeData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        notices.append(
            SchoolNoticeData(
                id=str(item["Id"]),
                subject=item.get("Subject", ""),
                content=item.get("Content", ""),
                start_date=item.get("StartDate"),
                end_date=item.get("EndDate"),
                creation_date=item.get("CreationDate"),
                was_read=bool(item.get("WasRead")),
            )
        )
    return notices


def parse_lucky_number(payload: dict[str, Any]) -> LuckyNumberData | None:
    raw = payload.get("LuckyNumber")
    if not isinstance(raw, dict) or raw.get("LuckyNumber") is None:
        return None
    try:
        number = int(raw["LuckyNumber"])
    except (TypeError, ValueError):
        return None
    return LuckyNumberData(day=raw.get("LuckyNumberDay"), number=number)


# CONFIRMED live (2026-09-06): the single-message endpoint's `Message`
# field (LibrusApiClient.async_get_message), once base64-decoded, is NOT
# plain text - it's a tiny XML wrapper,
# `<Message><Content><![CDATA[the real text...]]></Content></Message>`,
# and the real content lives inside the CDATA section. Found live: a
# card's expanded message view showed the literal
# "<Message><Content><![CDATA[" prefix leaking into the display. The list
# endpoint's `content` field does NOT do this (confirmed plain text, no
# wrapper) - decode_message_content is shared by both, so this regex is a
# no-op there (it simply won't match).
_MESSAGE_XML_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
# Defensive fallback for a CDATA section missing its closing "]]>" - e.g.
# if a future endpoint truncates this XML-wrapped text the way the list
# endpoint's plain-text `content` is known to (unconfirmed whether
# async_get_message's response can be truncated at all, but showing "cut
# off mid-sentence" beats showing raw XML markup either way).
_MESSAGE_XML_CDATA_OPEN_RE = re.compile(r"<!\[CDATA\[(.*)$", re.DOTALL)

# CONFIRMED live (2026-09-10): Librus rewrites every link in a message
# body into an <a href="https://liblink.pl/..." title="Link został
# skonwertowany...">...</a> tag (its own "link converter"), and the list
# endpoint's content can carry other light HTML (<br>, <p>). Rendered as
# plain text in the Wiadomości card that reads as raw tag soup. Flatten
# it: keep the link (its visible text, or the href), turn <br>/</p> into
# newlines, drop the rest, unescape entities.
_A_TAG_RE = re.compile(r'<a\b[^>]*?\bhref="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"</(?:p|div|li|h[1-6])>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def flatten_message_html(text: str) -> str:
    if "<" not in text:
        return text

    def _anchor(match: re.Match[str]) -> str:
        href = match.group(1).strip()
        inner = _ANY_TAG_RE.sub("", match.group(2)).strip()
        if not inner or inner == href:
            return href
        return f"{inner} ({href})"

    text = _A_TAG_RE.sub(_anchor, text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)
    text = html_unescape(text)
    return _MULTI_NL_RE.sub("\n\n", text).strip()


def decode_message_content(raw: str) -> str:
    """The list endpoint's `content` field (and the single-message
    endpoint's `Message` field - see `LibrusApiClient.async_get_message`)
    are base64-encoded (CONFIRMED live - decoding several real messages
    produced readable Polish text). Falls back to the raw string if the
    payload isn't valid base64 at all, rather than raising and losing the
    whole messages feature over one bad entry. Public (not
    underscore-prefixed) - shared with `services.py`'s `get_message`
    handler, which decodes the full-content field the same way.

    CONFIRMED live (2026-09-06): Librus truncates this field to a fixed
    BYTE length, which can land mid-multi-byte UTF-8 character (e.g. a
    Polish "ą"/"ę"/"ń") - a plain `.decode("utf-8")` then raises
    UnicodeDecodeError on an otherwise-valid message, and previously this
    fell all the way back to the raw, still-base64-encoded string (visible
    in the Wiadomości card as an unbroken hash-like blob causing horizontal
    scroll). Retry with `errors="ignore"` first, which just drops the
    incomplete trailing bytes and keeps the readable prefix - matches how
    every OTHER truncated message already reads (cut off mid-word, not
    mid-character).
    """
    try:
        decoded_bytes = base64.b64decode(raw)
    except ValueError:
        return raw
    try:
        text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = decoded_bytes.decode("utf-8", errors="ignore")

    if (match := _MESSAGE_XML_CDATA_RE.search(text)) is not None:
        text = match.group(1)
    elif (match := _MESSAGE_XML_CDATA_OPEN_RE.search(text)) is not None:
        text = match.group(1)
    return flatten_message_html(text)


# CONFIRMED live: the unread-count response is a per-mailbox breakdown
# ({"data": {"inbox": N, "notes": N, "alerts": N, "substitutions": N,
# "absences": N, "justifications": N, "trash": N, "archiveInbox": N, ...}}),
# not a flat number. The non-"archive*" ones are surfaced - "substitutions"
# in particular is likely the single most actionable one for a parent
# (schedule changes), and it costs nothing extra since this whole response
# is already being fetched for the plain inbox count.
_MESSAGE_MAILBOXES = (
    "inbox",
    "notes",
    "alerts",
    "substitutions",
    "absences",
    "justifications",
    "trash",
)


def resolve_sender_name(payload: dict[str, Any]) -> str:
    """Resolve a message's sender display name from a `senderName` field,
    falling back to `senderFirstName`/`senderLastName` combined when it's
    absent/empty - CONFIRMED live to be the identical shape on both the
    mailbox list endpoints and the single full-message endpoint.
    """
    return payload.get("senderName") or (
        f"{payload.get('senderFirstName', '')} {payload.get('senderLastName', '')}".strip()
    )


def parse_message_list(list_payload: dict[str, Any], mailbox: str) -> list[MessageData]:
    """Shared by every mailbox's list endpoint - inbox, substitutions,
    alerts, ... all share the same response shape."""
    items = list_payload.get("data")
    if not isinstance(items, list):
        return []
    messages: list[MessageData] = []
    for item in items:
        if not isinstance(item, dict) or item.get("messageId") is None:
            continue
        sender_name = resolve_sender_name(item)
        messages.append(
            MessageData(
                id=str(item["messageId"]),
                sender_name=sender_name,
                topic=item.get("topic", ""),
                content=decode_message_content(item.get("content", "")),
                send_date=item.get("sendDate"),
                read_date=item.get("readDate"),
                has_attachment=bool(item.get("isAnyFileAttached")),
                mailbox=mailbox,
            )
        )
    return messages


def parse_messages(
    unread_payload: dict[str, Any], list_payload: dict[str, Any]
) -> tuple[int, dict[str, int], list[MessageData]]:
    unread_by_mailbox: dict[str, int] = {}
    unread_data = unread_payload.get("data")
    if isinstance(unread_data, dict):
        for mailbox in _MESSAGE_MAILBOXES:
            try:
                unread_by_mailbox[mailbox] = int(unread_data.get(mailbox) or 0)
            except (TypeError, ValueError):
                unread_by_mailbox[mailbox] = 0
    unread_count = unread_by_mailbox.get("inbox", 0)
    return unread_count, unread_by_mailbox, parse_message_list(list_payload, "inbox")


def parse_school(payload: dict[str, Any]) -> SchoolData | None:
    school = payload.get("School")
    if not isinstance(school, dict):
        return None
    head_first = school.get("NameHeadTeacher") or ""
    head_last = school.get("SurnameHeadTeacher") or ""
    head_name = f"{head_first} {head_last}".strip() or None
    return SchoolData(
        name=school.get("Name", ""),
        town=school.get("Town"),
        street=school.get("Street"),
        building_number=school.get("BuildingNumber"),
        post_code=school.get("PostCode"),
        head_teacher_name=head_name,
        email=school.get("Email"),
        phone_number=school.get("PhoneNumber"),
    )


def parse_class(payload: dict[str, Any]) -> ClassData | None:
    cls = payload.get("Class")
    if not isinstance(cls, dict):
        return None
    tutor = cls.get("ClassTutor") or {}
    return ClassData(
        number=cls.get("Number"),
        symbol=cls.get("Symbol", ""),
        tutor_id=tutor.get("Id"),
        begin_school_year=cls.get("BeginSchoolYear"),
        end_first_semester=cls.get("EndFirstSemester"),
        end_school_year=cls.get("EndSchoolYear"),
    )


def parse_free_days(payload: dict[str, Any], root_key: str) -> list[FreeDayData]:
    items = payload.get(root_key)
    if not isinstance(items, list):
        return []
    free_days: list[FreeDayData] = []
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("Id") is None
            or not item.get("DateFrom")
            or not item.get("DateTo")
        ):
            continue
        free_days.append(
            FreeDayData(
                id=int(item["Id"]),
                name=item.get("Name", ""),
                date_from=item["DateFrom"],
                date_to=item["DateTo"],
            )
        )
    return free_days


def parse_id_name_map(payload: dict[str, Any], list_keys: tuple[str, ...]) -> dict[int, str]:
    items: Any = None
    for key in list_keys:
        if key in payload:
            items = payload[key]
            break
    if not isinstance(items, list):
        return {}
    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        # CONFIRMED live: some Users entries (school admin/secretariat
        # accounts) have FirstName explicitly `null`, not just absent - `or
        # ""` is required here, `.get(key, "")` alone does NOT catch a
        # present-but-None value.
        first = item.get("FirstName") or ""
        last = item.get("LastName") or ""
        # CONFIRMED live: Notes/Categories uses "CategoryName", not "Name"
        # like every other id-name lookup this helper is used for.
        name = item.get("Name") or item.get("CategoryName") or f"{first} {last}".strip()
        if name:
            result[int(item["Id"])] = name
    return result


def parse_lesson_subjects(payload: dict[str, Any]) -> dict[int, int]:
    """lesson_id -> subject_id, from `Lessons` (CONFIRMED live 2026-09-23 -
    see const.py's ENDPOINT_LESSONS note). Used to resolve which subject an
    `AttendanceData.lesson_id` belongs to."""
    items = payload.get("Lessons")
    if not isinstance(items, list):
        return {}
    result: dict[int, int] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        subject = item.get("Subject") or {}
        subject_id = subject.get("Id")
        if subject_id is not None:
            result[int(item["Id"])] = int(subject_id)
    return result


def parse_message(payload: dict[str, Any], mailbox: str, message_id: str) -> FullMessageData | None:
    """Parse `LibrusApiClient.async_get_message`'s response. The body lives
    in a base64 `Message` field wrapped in a small XML/CDATA shell -
    `decode_message_content` handles both layers."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    attachments = [
        AttachmentData(id=str(a["id"]), filename=a.get("filename"))
        for a in data.get("attachments") or []
        if isinstance(a, dict) and a.get("id") is not None
    ]
    return FullMessageData(
        id=str(message_id),
        mailbox=mailbox,
        sender_name=resolve_sender_name(data),
        topic=data.get("topic", ""),
        content=decode_message_content(data.get("Message", "")),
        send_date=data.get("sendDate"),
        read_date=data.get("readDate"),
        attachments=attachments,
    )


_INFO_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_INFO_CELL_RE = re.compile(r"<(th|td)[^>]*>(.*?)</\1>", re.S | re.I)


def parse_student_number(page: str) -> int | None:
    """The class register number ("Nr w dzienniku") from Synergia's
    "Informacje" web page (`LibrusApiClient.async_get_student_info_page`).

    The page is a `<th>label</th><td>value</td>` table; the row is found by
    its label rather than by position (szkolny-android uses the 3rd row,
    the live page has an empty header row first). None when the row is
    missing or not a number."""
    for row in _INFO_ROW_RE.findall(page):
        cells = {
            tag.lower(): html_unescape(_ANY_TAG_RE.sub(" ", body)).strip()
            for tag, body in _INFO_CELL_RE.findall(row)
        }
        label = " ".join(cells.get("th", "").split()).lower()
        if label.startswith("nr w dzienniku"):
            value = cells.get("td", "").split()
            return int(value[0]) if value and value[0].isdigit() else None
    return None
