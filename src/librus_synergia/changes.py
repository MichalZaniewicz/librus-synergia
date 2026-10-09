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

Each kind is seeded and compared on its own. A kind whose data couldn't be
fetched (`LibrusData.failed_sections`, filled by `Librus.fetch_all()` and
`Librus.fetch_changes()`) is skipped that time - neither reported nor
remembered - and seeded silently the first time its data does arrive.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Literal

from ._dates import school_today
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

# The `LibrusData` fields each kind is built from: when any of them failed
# to fetch, the kind is skipped for that update.
KIND_SECTIONS: dict[str, frozenset[str]] = {
    "grades": frozenset({"grades"}),
    "notes": frozenset({"notes"}),
    "announcements": frozenset({"school_notices"}),
    "messages": frozenset({"messages"}),
    "agenda": frozenset({"homeworks"}),
    "absences": frozenset({"attendances", "attendance_types"}),
    "timetable_changes": frozenset({"timetable"}),
}


@dataclass(slots=True)
class TimetableChange:
    """A lesson that is cancelled or moved to a substitution."""

    date: date
    lesson: LessonData
    kind: Literal["canceled", "substitution"]


@dataclass(slots=True)
class SeenIds:
    """Everything already reported, per kind. Persist it with `to_dict()`
    to keep "new" meaning new across restarts.

    `seeded` names the kinds that have been seeded (their first data
    recorded). None - the default, and what a dict saved by an older version
    gives - means every kind is."""

    grades: set[str] = field(default_factory=set)
    notes: set[str] = field(default_factory=set)
    announcements: set[str] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)
    agenda: set[str] = field(default_factory=set)
    absences: set[str] = field(default_factory=set)
    timetable_changes: set[str] = field(default_factory=set)
    seeded: set[str] | None = None

    def is_kind_seeded(self, kind: str) -> bool:
        return self.seeded is None or kind in self.seeded

    def mark_seeded(self, kind: str) -> None:
        if self.seeded is not None:
            self.seeded.add(kind)

    def to_dict(self) -> dict[str, list[str]]:
        result = {f.name: sorted(getattr(self, f.name)) for f in fields(self) if f.name != "seeded"}
        if self.seeded is not None:
            result["seeded"] = sorted(self.seeded)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeenIds:
        raw_seeded = data.get("seeded")
        return cls(
            **{
                f.name: {str(v) for v in data.get(f.name, ())}
                for f in fields(cls)
                if f.name != "seeded"
            },
            seeded=None if raw_seeded is None else {str(v) for v in raw_seeded},
        )


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
    """Cancelled/substitution lessons from `today` on (default: today in
    Poland), keyed by a stable signature (date, lesson number, kind,
    subject)."""
    today = today or school_today()
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
        """Whether `update` has run at least once (or saved ids were given).
        Single kinds may still be unseeded - see `SeenIds.seeded`."""
        return self._seen is not None

    def update(self, data: LibrusData, *, today: date | None = None) -> Changes:
        failed = data.failed_sections
        builders: dict[str, Callable[[], dict[str, Any]]] = {
            "grades": lambda: {str(g.id): g for g in data.grades},
            "notes": lambda: {str(n.id): n for n in data.notes},
            "announcements": lambda: {str(n.id): n for n in data.school_notices},
            "messages": lambda: {str(m.id): m for m in data.messages},
            "agenda": lambda: {str(h.id): h for h in data.homeworks},
            "absences": lambda: {str(a.id): a for a in absences(data)},
            "timetable_changes": lambda: timetable_changes(data, today),
        }
        if self._seen is None:
            self._seen = SeenIds(seeded=set())
        seen = self._seen
        changes = Changes()
        for kind, build in builders.items():
            if KIND_SECTIONS[kind] & failed:
                continue  # no data this time: nothing to compare or remember
            items: dict[str, Any] = build()
            known: set[str] = getattr(seen, kind)
            if seen.is_kind_seeded(kind):
                getattr(changes, kind).extend(
                    item for key, item in items.items() if key not in known
                )
            else:
                seen.mark_seeded(kind)
            known |= set(items)
        return changes
