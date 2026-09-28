"""Command line: `librus-synergia` / `python -m librus_synergia`.

    librus-synergia                 # summary of the account
    librus-synergia --json          # everything, as JSON
    librus-synergia --watch         # check every 15 min, print what's new

Credentials come from LIBRUS_LOGIN / LIBRUS_PASSWORD, or are asked for.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import getpass
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .changes import Changes, ChangeTracker, SeenIds
from .client import LibrusSessionData
from .exceptions import LibrusAuthError, LibrusError
from .librus import Librus
from .models import LibrusData
from .parsers import parse_grade_value


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _to_json(data: LibrusData) -> str:
    payload = dataclasses.asdict(data)
    payload["timetable"] = {
        day.isoformat(): lessons for day, lessons in payload["timetable"].items()
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


_PL_DAYS = ("pon", "wt", "śr", "czw", "pt", "sob", "nd")


def _name(lookup: dict[Any, str], key: Any) -> str:
    return lookup.get(key, "?") if key is not None else "?"


def _print_summary(data: LibrusData) -> None:
    print(f"Uczeń: {data.me.display_name}")
    if data.school_class:
        print(f"Klasa: {data.school_class.display_name}")

    print("\nOstatnie oceny:")
    grades = sorted(data.grades, key=lambda g: g.add_date or "", reverse=True)[:8]
    for g in grades or []:
        value = parse_grade_value(g.value)
        suffix = "" if value is None else f"  ({value:g})"
        print(
            f"  {(g.add_date or '')[:10]}  {_name(data.subjects, g.subject_id):<28} {g.value}{suffix}"
        )
    if not grades:
        print("  -")

    today = date.today()
    print("\nPlan (najbliższe dni):")
    upcoming = [(d, lessons) for d, lessons in sorted(data.timetable.items()) if d >= today][:3]
    for day, lessons in upcoming:
        names = []
        for lesson in lessons:
            name = _name(data.subjects, lesson.subject_id)
            if lesson.is_canceled:
                name += " (odwołana)"
            elif lesson.is_substitution:
                name += " (zastępstwo)"
            names.append(name)
        print(f"  {_PL_DAYS[day.weekday()]} {day:%d.%m}: {', '.join(names) or '-'}")
    if not upcoming:
        print("  -")

    absent = sum(
        1
        for a in data.attendances
        if a.type_id in data.attendance_types
        and not data.attendance_types[a.type_id].is_presence_kind
    )
    print(f"\nNieobecności: {absent}")
    if data.lucky_number:
        print(f"Szczęśliwy numerek: {data.lucky_number.number} ({data.lucky_number.day})")
    print(f"Nieprzeczytane wiadomości: {data.unread_message_count}")


def _print_changes(data: LibrusData, changes: Changes) -> None:
    stamp = datetime.now().strftime("%H:%M")
    for g in changes.grades:
        print(f"[{stamp}] Nowa ocena: {_name(data.subjects, g.subject_id)} {g.value}")
    for n in changes.notes:
        print(f"[{stamp}] Nowa uwaga ({n.sentiment or '?'}): {n.text[:80]}")
    for notice in changes.announcements:
        print(f"[{stamp}] Nowe ogłoszenie: {notice.subject}")
    for m in changes.messages:
        print(f"[{stamp}] Nowa wiadomość od {m.sender_name}: {m.topic}")
    for h in changes.agenda:
        print(f"[{stamp}] Terminarz {h.date}: {(h.content or '')[:80]}")
    for absence in changes.absences:
        print(f"[{stamp}] Nieobecność: {absence.date} (lekcja {absence.lesson_no})")
    for c in changes.timetable_changes:
        kind = "odwołana" if c.kind == "canceled" else "zastępstwo"
        print(
            f"[{stamp}] Zmiana w planie {c.date:%d.%m}: "
            f"{_name(data.subjects, c.lesson.subject_id)} - {kind}"
        )


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _save_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")


async def _run(args: argparse.Namespace, login: str, password: str) -> int:
    saved_session = _load_json(args.session)
    session_data = LibrusSessionData(**saved_session) if saved_session else None
    async with Librus(login, password, session_data=session_data) as librus:
        try:
            if not args.watch:
                data = await librus.fetch_all()
                if args.json:
                    print(_to_json(data))
                else:
                    _print_summary(data)
                return 0

            saved_state = _load_json(args.state)
            tracker = ChangeTracker(SeenIds.from_dict(saved_state) if saved_state else None)
            print(f"Sprawdzam co {args.interval} min. Ctrl+C kończy.", file=sys.stderr)
            while True:
                data = await librus.fetch_all()
                was_seeded = tracker.is_seeded
                changes = tracker.update(data)
                if not was_seeded:
                    print(
                        "Zapamiętano obecny stan - od teraz pokazuję tylko nowości.",
                        file=sys.stderr,
                    )
                _print_changes(data, changes)
                _save_json(args.state, tracker.seen.to_dict())
                _save_json(args.session, dataclasses.asdict(librus.session_data))
                await asyncio.sleep(args.interval * 60)
        except LibrusAuthError as err:
            print(f"Logowanie nie powiodło się: {err}", file=sys.stderr)
            return 2
        except LibrusError as err:
            print(f"Błąd Librusa: {err}", file=sys.stderr)
            return 1
        finally:
            _save_json(args.session, dataclasses.asdict(librus.session_data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="librus-synergia",
        description="Unofficial Librus Synergia client. Credentials: LIBRUS_LOGIN / "
        "LIBRUS_PASSWORD environment variables, or asked for interactively.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print all data as JSON")
    mode.add_argument("--watch", action="store_true", help="keep checking and print what's new")
    parser.add_argument(
        "--interval", type=int, default=15, metavar="MIN", help="--watch interval (default 15)"
    )
    parser.add_argument(
        "--state",
        type=Path,
        metavar="FILE",
        help="--watch: remember what was already shown across runs",
    )
    parser.add_argument(
        "--session",
        type=Path,
        metavar="FILE",
        help="reuse and save the login session (keep this file private)",
    )
    args = parser.parse_args(argv)
    if args.interval < 5:
        parser.error("--interval must be at least 5 minutes (be gentle with Librus)")

    login = os.environ.get("LIBRUS_LOGIN") or input("Login Librus: ")
    password = os.environ.get("LIBRUS_PASSWORD") or getpass.getpass("Hasło: ")
    try:
        return asyncio.run(_run(args, login, password))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
