# Examples

Complete scripts. Credentials come from `LIBRUS_LOGIN` / `LIBRUS_PASSWORD`;
session and state files are written next to the script and must stay
private (they grant access to the account).

## 1. Summary with a saved session

```python
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from librus_synergia import Librus, LibrusSessionData, parse_grade_value

SESSION = Path("session.json")


def load_session() -> LibrusSessionData | None:
    return LibrusSessionData(**json.loads(SESSION.read_text())) if SESSION.exists() else None


def save_session(data: LibrusSessionData) -> None:
    SESSION.write_text(json.dumps(asdict(data)))
    SESSION.chmod(0o600)


async def main() -> None:
    async with Librus(
        os.environ["LIBRUS_LOGIN"],
        os.environ["LIBRUS_PASSWORD"],
        session_data=load_session(),
        on_session_update=save_session,
    ) as librus:
        me = await librus.me()
        subjects = await librus.subjects()
        grading = await librus.grading_system()
        print(f"Student: {me.display_name}")

        latest = sorted(await librus.grades(), key=lambda g: g.add_date or "", reverse=True)[:5]
        for grade in latest:
            number = parse_grade_value(grade.value, grading)
            subject = subjects.get(grade.subject_id, "?") if grade.subject_id is not None else "?"
            print(f"{grade.add_date}  {subject:<25} {grade.value}"
                  + ("" if number is None else f" ({number})"))

        lucky = await librus.lucky_number()
        if lucky:
            print(f"Lucky number: {lucky.number} (for {lucky.day})")


asyncio.run(main())
```

## 2. Weighted average per subject

```python
import asyncio
import os
from collections import defaultdict

from librus_synergia import Librus, parse_grade_value


async def main() -> None:
    async with Librus(os.environ["LIBRUS_LOGIN"], os.environ["LIBRUS_PASSWORD"]) as librus:
        subjects = await librus.subjects()
        categories = await librus.grade_categories()
        grading = await librus.grading_system()

        sums: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])  # subject -> [sum, weight]
        for grade in await librus.grades():
            if grade.subject_id is None or (
                grade.is_semester_proposition or grade.is_final_proposition
                or grade.is_semester or grade.is_final
            ):
                continue
            value = parse_grade_value(grade.value, grading)
            category = categories.get(grade.category_id) if grade.category_id is not None else None
            if value is None or (category is not None and not category.count_to_average):
                continue
            weight = (category.weight if category and category.weight else 1.0)
            sums[grade.subject_id][0] += value * weight
            sums[grade.subject_id][1] += weight

        for subject_id, (total, weight) in sorted(sums.items(), key=lambda kv: subjects.get(kv[0], "")):
            print(f"{subjects.get(subject_id, subject_id)}: {total / weight:.2f}")


asyncio.run(main())
```

## 3. Notification bot (polling for changes)

```python
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from librus_synergia import (
    ChangeTracker,
    Librus,
    LibrusAuthError,
    LibrusConnectionError,
    LibrusError,
    LibrusSessionData,
    SeenIds,
)

SESSION = Path("session.json")
SEEN = Path("seen.json")
INTERVAL = 15 * 60  # be gentle: real children's accounts


def notify(text: str) -> None:
    print(text)  # send to Telegram, ntfy, e-mail...


def save_session(data: LibrusSessionData) -> None:
    SESSION.write_text(json.dumps(asdict(data)))
    SESSION.chmod(0o600)


async def main() -> None:
    session = LibrusSessionData(**json.loads(SESSION.read_text())) if SESSION.exists() else None
    tracker = ChangeTracker(SeenIds.from_dict(json.loads(SEEN.read_text())) if SEEN.exists() else None)
    async with Librus(
        os.environ["LIBRUS_LOGIN"],
        os.environ["LIBRUS_PASSWORD"],
        session_data=session,
        on_session_update=save_session,
        cache_reference_data=True,
    ) as librus:
        while True:
            try:
                data = await librus.fetch_changes()
            except LibrusAuthError as err:
                notify(f"Librus login failed, stopping: {err}")
                return
            except LibrusConnectionError as err:
                print(f"Librus not reachable, trying later: {err}")
            except LibrusError as err:
                print(f"Unexpected answer from Librus, trying later: {err}")
            else:
                changes = tracker.update(data)  # the first run only remembers
                for grade in changes.grades:
                    subject = data.subjects.get(grade.subject_id, "?") if grade.subject_id is not None else "?"
                    notify(f"New grade: {subject} {grade.value}")
                for message in changes.messages:
                    notify(f"New message from {message.sender_name}: {message.topic}")
                for change in changes.timetable_changes:
                    notify(f"{change.date}: lesson {change.lesson.lesson_no} {change.kind}")
                SEEN.write_text(json.dumps(tracker.seen.to_dict()))
            await asyncio.sleep(INTERVAL)


asyncio.run(main())
```

## 4. Today's and tomorrow's lessons, with changes

```python
import asyncio
import os
from datetime import date, timedelta

from librus_synergia import Librus


async def main() -> None:
    async with Librus(os.environ["LIBRUS_LOGIN"], os.environ["LIBRUS_PASSWORD"]) as librus:
        subjects = await librus.subjects()
        rooms = await librus.classrooms()
        today = date.today()
        week = await librus.timetable(today)
        tomorrow = today + timedelta(days=1)
        if tomorrow not in week:  # tomorrow is in the next week
            week |= await librus.timetable(tomorrow)

        for day in (today, tomorrow):
            print(day.strftime("%A %d.%m"))
            for lesson in sorted(week.get(day, []), key=lambda l: (l.lesson_no or 0, l.hour_from or "")):
                name = subjects.get(lesson.subject_id, "?") if lesson.subject_id is not None else "?"
                room = rooms.get(lesson.classroom_id, "") if lesson.classroom_id is not None else ""
                note = ""
                if lesson.is_canceled:
                    note = " (cancelled)"
                elif lesson.is_substitution:
                    note = " (substitution)"
                elif lesson.room_changed:
                    note = " (room change)"
                print(f"  {lesson.lesson_no}. {lesson.hour_from}-{lesson.hour_to} {name} {room}{note}")


asyncio.run(main())
```

## 5. Messages: previews, then one full message and its files

```python
import asyncio
import os
from pathlib import Path

from librus_synergia import Librus


async def main() -> None:
    async with Librus(os.environ["LIBRUS_LOGIN"], os.environ["LIBRUS_PASSWORD"]) as librus:
        unread = await librus.unread_messages()
        print(f"Unread in inbox: {unread.get('inbox', 0)}")
        previews = await librus.messages("inbox", limit=10)  # never marks anything read
        for m in previews:
            print(("* " if m.read_date is None else "  ") + f"{m.send_date} {m.sender_name}: {m.topic}")

        if previews and input("Open the newest one (marks it read)? [y/N] ").lower() == "y":
            full = await librus.message(previews[0].id)  # marks it read in Librus
            if full:
                print(full.content)
                for attachment in full.attachments:
                    file = await librus.download_attachment(attachment.id, full.id)
                    if file is not None:
                        Path(file.filename or attachment.id).write_bytes(file.content)


asyncio.run(main())
```

## 6. Several children

```python
import asyncio
import os

from librus_synergia import Librus


async def summary(login: str, password: str) -> str:
    # One Librus instance (and so one aiohttp session) per account.
    async with Librus(login, password) as librus:
        me = await librus.me()
        lucky = await librus.lucky_number()
        return f"{me.display_name}: lucky number {lucky.number if lucky else '-'}"


async def main() -> None:
    accounts = [
        (os.environ["LIBRUS_LOGIN_1"], os.environ["LIBRUS_PASSWORD_1"]),
        (os.environ["LIBRUS_LOGIN_2"], os.environ["LIBRUS_PASSWORD_2"]),
    ]
    for line in await asyncio.gather(*(summary(*a) for a in accounts)):
        print(line)


asyncio.run(main())
```

## 7. Everything to JSON

```python
import asyncio
import json
import os
from dataclasses import asdict

from librus_synergia import Librus


async def main() -> None:
    async with Librus(os.environ["LIBRUS_LOGIN"], os.environ["LIBRUS_PASSWORD"]) as librus:
        data = await librus.fetch_all()  # ~35 requests: don't run it in a loop
        if data.failed_sections:
            print("Couldn't fetch:", ", ".join(sorted(data.failed_sections)))
        with open("librus.json", "w", encoding="utf-8") as f:
            json.dump(asdict(data), f, ensure_ascii=False, indent=2, default=str)


asyncio.run(main())
```

The command line does the same: `librus-synergia --json > librus.json`.
