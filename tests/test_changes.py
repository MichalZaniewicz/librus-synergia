"""Tests for ChangeTracker."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from librus_synergia.changes import ChangeTracker, SeenIds
from librus_synergia.models import (
    AttendanceData,
    AttendanceTypeData,
    GradeData,
    LessonData,
    LibrusData,
    MeData,
    MessageData,
)

TODAY = date(2026, 9, 28)


def _grade(grade_id: int, value: str = "5") -> GradeData:
    return GradeData(
        id=grade_id,
        value=value,
        category_id=None,
        subject_id=1,
        semester=1,
        add_date="2026-09-20",
        is_semester_proposition=False,
        is_final_proposition=False,
    )


def _lesson(**kwargs: object) -> LessonData:
    base: dict[str, object] = dict(
        lesson_no=2,
        hour_from="08:55",
        hour_to="09:40",
        subject_id=7,
        teacher_id=1,
        classroom_id=1,
        is_canceled=False,
        is_substitution=False,
    )
    base.update(kwargs)
    return LessonData(**base)  # type: ignore[arg-type]


def _data(**kwargs: object) -> LibrusData:
    base: dict[str, object] = dict(
        me=MeData(account_id=1, first_name="Jan", last_name="K"),
        grades=[],
        grade_categories={},
        notes=[],
        attendances=[],
        attendance_types={
            1: AttendanceTypeData(id=1, name="Nieobecność", is_presence_kind=False),
            2: AttendanceTypeData(id=2, name="Spóźnienie", is_presence_kind=True),
        },
        timetable={},
        homeworks=[],
        school_notices=[],
        lucky_number=None,
        subjects={},
        teachers={},
        classrooms={},
    )
    base.update(kwargs)
    return LibrusData(**base)  # type: ignore[arg-type]


def test_first_update_seeds_silently() -> None:
    tracker = ChangeTracker()
    changes = tracker.update(_data(grades=[_grade(1), _grade(2)]), today=TODAY)
    assert not changes
    assert tracker.is_seeded
    assert tracker.seen.grades == {"1", "2"}


def test_only_new_items_are_reported() -> None:
    tracker = ChangeTracker()
    tracker.update(_data(grades=[_grade(1)]), today=TODAY)
    changes = tracker.update(_data(grades=[_grade(1), _grade(2, "4+")]), today=TODAY)
    assert [g.id for g in changes.grades] == [2]
    assert changes
    assert not tracker.update(_data(grades=[_grade(1), _grade(2)]), today=TODAY)


def test_item_that_drops_out_and_returns_is_not_reported_again() -> None:
    tracker = ChangeTracker()
    tracker.update(_data(grades=[_grade(1)]), today=TODAY)
    tracker.update(_data(grades=[]), today=TODAY)
    assert not tracker.update(_data(grades=[_grade(1)]), today=TODAY)


def test_absences_exclude_presence_kinds() -> None:
    tracker = ChangeTracker()
    tracker.update(_data(), today=TODAY)
    absent = AttendanceData(
        id="t41685", lesson_id=1, lesson_no=1, date="2026-09-28", semester=1, type_id=1
    )
    late = replace(absent, id=6, type_id=2)
    changes = tracker.update(_data(attendances=[absent, late]), today=TODAY)
    assert [a.id for a in changes.absences] == ["t41685"]


def test_timetable_changes_from_today_on() -> None:
    tracker = ChangeTracker()
    tracker.update(_data(), today=TODAY)
    changes = tracker.update(
        _data(
            timetable={
                date(2026, 9, 25): [_lesson(is_canceled=True)],
                TODAY: [_lesson(), _lesson(lesson_no=3, is_substitution=True)],
                date(2026, 9, 29): [_lesson(is_canceled=True)],
            }
        ),
        today=TODAY,
    )
    assert [(c.date, c.kind, c.lesson.lesson_no) for c in changes.timetable_changes] == [
        (TODAY, "substitution", 3),
        (date(2026, 9, 29), "canceled", 2),
    ]


def test_seen_ids_survive_json_round_trip() -> None:
    tracker = ChangeTracker()
    message = MessageData(
        id="99",
        sender_name="Szkoła",
        topic="t",
        content="c",
        send_date=None,
        read_date=None,
        has_attachment=False,
    )
    tracker.update(_data(grades=[_grade(1)], messages=[message]), today=TODAY)
    saved = json.loads(json.dumps(tracker.seen.to_dict()))
    restored = ChangeTracker(SeenIds.from_dict(saved))
    assert restored.is_seeded
    changes = restored.update(_data(grades=[_grade(1), _grade(2)], messages=[message]), today=TODAY)
    assert [g.id for g in changes.grades] == [2]
    assert not changes.messages
