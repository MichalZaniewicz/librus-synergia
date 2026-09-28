"""Print a short summary for one Librus account.

    LIBRUS_LOGIN=1234567u LIBRUS_PASSWORD=... python examples/quickstart.py

The session (cookies) is saved to session.json so the next run reuses it
instead of logging in again. That file lets anyone act as this account
until the session expires - keep it private.
"""

import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from librus_synergia import Librus, LibrusSessionData, parse_grade_value

SESSION_FILE = Path("session.json")


def load_session() -> LibrusSessionData | None:
    if not SESSION_FILE.exists():
        return None
    return LibrusSessionData(**json.loads(SESSION_FILE.read_text()))


async def main() -> None:
    async with Librus(
        os.environ["LIBRUS_LOGIN"],
        os.environ["LIBRUS_PASSWORD"],
        session_data=load_session(),
    ) as librus:
        me = await librus.me()
        subjects = await librus.subjects()
        print(f"Uczeń: {me.display_name}")

        print("\nOstatnie oceny:")
        grades = sorted(await librus.grades(), key=lambda g: g.add_date or "", reverse=True)
        for grade in grades[:5]:
            value = parse_grade_value(grade.value)
            print(f"  {grade.add_date}  {subjects.get(grade.subject_id, '?'):<25} {grade.value}"
                  + ("" if value is None else f"  ({value})"))

        print("\nPlan na ten tydzień:")
        for day, lessons in sorted((await librus.timetable()).items()):
            names = [subjects.get(lesson.subject_id, "?") for lesson in lessons]
            print(f"  {day:%a %d.%m}: {', '.join(names) or '—'}")

        lucky = await librus.lucky_number()
        if lucky:
            print(f"\nSzczęśliwy numerek: {lucky.number} ({lucky.day})")

        unread = await librus.unread_messages()
        print(f"Nieprzeczytane wiadomości: {unread.get('inbox', 0)}")

        SESSION_FILE.write_text(json.dumps(asdict(librus.session_data)))


asyncio.run(main())
