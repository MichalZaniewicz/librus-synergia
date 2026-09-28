"""Detect what's new between two snapshots of one account.

    tracker = ChangeTracker()               # or ChangeTracker(SeenIds.from_dict(saved))
    tracker.update(await librus.fetch_all())  # first call: remembers everything, returns nothing
    ...
    changes = tracker.update(await librus.fetch_all())
    for grade in changes.grades:
        print("New grade:", grade.value)
    save(tracker.seen.to_dict())            # plain JSON-able dict

The first update only records what already exists; announcing a whole
school year of grades as "new" on first run is never what anyone wants.
Ids are remembered as a union (not replaced), so an item that drops out of
Librus's window and later reappears isn't reported twice. This mirrors the
new-item events of the Home Assistant integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Literal

from .models import (
    AttendanceData,
    GradeData,
    HomeworkEventData,
    LessonData,
    LibrusData,
    MessageData,
    NoteData,
    SchoolNoticeData,
)


@dataclass(slots=True)
class TimetableChange:
    """A lesson that is cancelled or moved to a substitution."""

    date: date
    lesson: LessonData
    kind: Literal["canceled", "substitution"]


@dataclass(slots=True)
class SeenIds:
    """Everything already reported, per kind. Persist it with `to_dict()`
    to keep "new" meaning new across restarts."""

    grades: set[str] = field(default_factory=set)
    notes: set[str] = field(default_factory=set)
    announcements: set[str] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)
    agenda: set[str] = field(default_factory=set)
    absences: set[str] = field(default_factory=set)
    timetable_changes: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, list[str]]:
        return {f.name: sorted(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeenIds:
        return cls(**{f.name: {str(v) for v in data.get(f.name, ())} for f in fields(cls)})


@dataclass(slots=True)
class Changes:
    """New items since the previous `ChangeTracker.update`."""

    grades: list[GradeData] = field(default_factory=list)
    notes: list[NoteData] = field(default_factory=list)
    announcements: list[SchoolNoticeData] = field(default_factory=list)
    messages: list[MessageData] = field(default_factory=list)
    agenda: list[HomeworkEventData] = field(default_factory=list)
    absences: list[AttendanceData] = field(default_factory=list)
    timetable_changes: list[TimetableChange] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


def timetable_changes(data: LibrusData, today: date | None = None) -> dict[str, TimetableChange]:
    """Cancelled/substitution lessons from `today` on, keyed by a stable
    signature (date, lesson number, kind, subject)."""
    today = today or date.today()
    result: dict[str, TimetableChange] = {}
    for day, lessons in data.timetable.items():
        if day < today:
            continue
        for lesson in lessons:
            if not (lesson.is_canceled or lesson.is_substitution):
                continue
            kind: Literal["canceled", "substitution"] = (
                "canceled" if lesson.is_canceled else "substitution"
            )
            key = f"{day.isoformat()}|{lesson.lesson_no}|{kind}|{lesson.subject_id}"
            result[key] = TimetableChange(date=day, lesson=lesson, kind=kind)
    return result


def absences(data: LibrusData) -> list[AttendanceData]:
    """Real absences only (excused or not) - attendance records whose type
    is not a presence kind. Late arrivals count as present."""
    result = []
    for record in data.attendances:
        kind = data.attendance_types.get(record.type_id) if record.type_id is not None else None  # type: ignore[arg-type]
        if kind is not None and not kind.is_presence_kind:
            result.append(record)
    return result


class ChangeTracker:
    """Remembers what's been seen and reports what's new."""

    def __init__(self, seen: SeenIds | None = None) -> None:
        self._seen = seen

    @property
    def seen(self) -> SeenIds:
        return self._seen if self._seen is not None else SeenIds()

    @property
    def is_seeded(self) -> bool:
        return self._seen is not None

    def update(self, data: LibrusData, *, today: date | None = None) -> Changes:
        current: dict[str, dict[str, Any]] = {
            "grades": {str(g.id): g for g in data.grades},
            "notes": {str(n.id): n for n in data.notes},
            "announcements": {str(n.id): n for n in data.school_notices},
            "messages": {str(m.id): m for m in data.messages},
            "agenda": {str(h.id): h for h in data.homeworks},
            "absences": {str(a.id): a for a in absences(data)},
            "timetable_changes": timetable_changes(data, today),
        }
        if self._seen is None:
            self._seen = SeenIds(**{kind: set(items) for kind, items in current.items()})
            return Changes()
        changes = Changes()
        for kind, items in current.items():
            known: set[str] = getattr(self._seen, kind)
            getattr(changes, kind).extend(item for key, item in items.items() if key not in known)
            known |= set(items)
        return changes
