# librus-synergia

<p align="center">
  <a href="https://pypi.org/project/librus-synergia/"><img alt="PyPI" src="https://img.shields.io/pypi/v/librus-synergia"></a>
  <a href="https://pypi.org/project/librus-synergia/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/librus-synergia"></a>
  <a href="https://github.com/MichalZaniewicz/librus-synergia/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MichalZaniewicz/librus-synergia/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/MichalZaniewicz/librus-synergia"></a>
</p>

An unofficial, async, fully typed Python client for [Librus Synergia](https://synergia.librus.pl/), the Polish school e-register ("e-dziennik"). It covers grades, attendance, timetable, agenda, homework, behaviour notes, announcements, the lucky number and private messages.

> [!TIP]
> ⭐ **Useful?** A star helps other parents and developers find it.
>
> ☕ Want to say thanks another way? You can [buy me a coffee](https://buymeacoffee.com/zanula).

[![Star this repo](https://img.shields.io/github/stars/MichalZaniewicz/librus-synergia?style=for-the-badge&logo=github&label=STAR%20THIS%20REPO&labelColor=555555&color=ffc107)](https://github.com/MichalZaniewicz/librus-synergia) [![Buy me a coffee](https://img.shields.io/badge/BUY%20ME%20A%20COFFEE-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/zanula)

This is the engine behind the [Librus Synergia Home Assistant integration](https://github.com/MichalZaniewicz/ha-librus-synergia), extracted so anyone can use it: in scripts, bots, dashboards or their own apps.

## Why another Librus library?

- **Uses the current login flow.** The old OAuth password grant (`client_id=28`) has returned `unsupported_grant_type` since 2026. This library uses the flow Librus's own web portal uses, with no captcha on a normal login.
- **Parses real responses, not guesses.** The parsers are built from real account responses and cover the traps: timetables are nested period slots, ids are sometimes ints and sometimes strings, `FirstName` can be `null`, message bodies are truncated base64 wrapped in CDATA, and more. See [the API notes](docs/).
- **Runs unattended.** It persists the session and the long-lived device cookie, logs in again automatically when Librus drops the session early, and treats an unpublished timetable (HTTP 403) as "no timetable yet" rather than an error.
- **Async and typed.** It uses `aiohttp`, returns dataclasses, and passes `mypy --strict`.

## Install

```bash
pip install librus-synergia
```

Requires Python 3.12+.

## Quick start

```python
import asyncio
from librus_synergia import Librus, parse_grade_value

async def main():
    async with Librus("1234567u", "password") as librus:
        me = await librus.me()
        subjects = await librus.subjects()          # {subject_id: "Matematyka", ...}

        for grade in await librus.grades():
            print(subjects.get(grade.subject_id), grade.value, parse_grade_value(grade.value))

        for day, lessons in (await librus.timetable()).items():
            print(day, [subjects.get(l.subject_id) for l in lessons])

asyncio.run(main())
```

See [`examples/quickstart.py`](examples/quickstart.py) for a runnable version that also saves the session between runs.

## What you can fetch

| Method | Returns |
|---|---|
| `me()` | the student (`Me.User`), not the parent login |
| `grades()`, `grade_categories()` | grades with teacher comments resolved; weights and categories |
| `descriptive_grades()`, `behaviour_grades()` | descriptive grades; formal behaviour grade (ocena zachowania) |
| `notes()`, `note_categories()` | behaviour notes (uwagi) with positive/negative/neutral `sentiment` |
| `attendances()`, `attendance_types()` | attendance records; types with `is_presence_kind` / `is_excused_absence` |
| `timetable(week_of=None)` | `{date: [LessonData]}` for one week, including parallel groups, cancellations and substitutions |
| `agenda()`, `agenda_categories()` | terminarz: tests, quizzes, trips, parent meetings |
| `homework()` | homework assignments (zadania domowe) |
| `free_days()`, `parent_teacher_conferences()` | days off; conferences |
| `announcements()` | school notice board (tablica ogłoszeń) |
| `lucky_number()` | szczęśliwy numerek, with the day it applies to |
| `unread_messages()`, `messages(mailbox, limit)` | unread count per mailbox; message previews (listing never marks read) |
| `message(id, mailbox)` | the full message body, which **marks it read** like opening it in the app |
| `subjects()`, `teachers()`, `classrooms()` | id → name lookups |
| `school()`, `school_class()` | school details; class, homeroom teacher and semester dates |
| `fetch_all()` | everything above as one `LibrusData` snapshot |

Every method logs in lazily and retries once after a fresh login if the session has expired.

## Keeping the session between runs

Librus sessions last about a day and there is no refresh token. Persist `librus.session_data` and pass it back to avoid logging in on every run. It also keeps Librus's year-long device cookie, which is what keeps logins captcha-free:

```python
from dataclasses import asdict
saved = asdict(librus.session_data)            # store it (e.g. JSON)
librus = Librus(login, password, session_data=LibrusSessionData(**saved))
```

⚠️ Session data grants access to the account while it is valid. Store it like a password.

## Multiple children / accounts

Use **one `Librus` instance and one `aiohttp.ClientSession` per account**. The login lives in the cookie jar, and two accounts sharing one session will silently get each other's data. If you pass your own session, don't share it between accounts.

## Low-level client

`Librus` covers the common cases. For anything else, `librus.client` (a `LibrusApiClient`) has one method per endpoint, returning raw JSON. You can pair it with the pure functions in `librus_synergia.parsers`:

```python
from librus_synergia import parsers

raw = await librus.client.async_get_units()                 # no high-level wrapper yet
week = parsers.merge_timetables(await librus.client.async_get_timetable(date(2026, 9, 7)))
```

## Unofficial Librus API notes

The [`docs/`](docs/) folder describes the private API itself: the login flow, every endpoint's shape, and what each status code really means. Every claim is marked as confirmed against a real account, taken from another client's source, or still unverified. It's language-agnostic, so it's useful even if you're not writing Python.

## Errors

Everything raises a subclass of `LibrusError`:

- `LibrusAuthError`: `LibrusInvalidCredentialsError`, `LibrusCaptchaRequiredError`, `LibrusAccountActionRequiredError`, `LibrusSessionExpiredError` (carries `status_code`)
- `LibrusConnectionError`: `LibrusServerMaintenanceError` (HTTP 503)
- `LibrusUnexpectedResponseError`: a response shape we didn't expect

See [docs/errors.md](docs/errors.md).

## Please be gentle

These are real children's school accounts. Cache lookups (`subjects()`, `teachers()` and similar change rarely), don't poll more than every ~15 minutes, and don't retry in a loop.

## Credits

The login flow is based on [emsi/librus_pyapi](https://github.com/emsi/librus_pyapi) (MIT). Some endpoint and field names were cross-checked against [szkolny-eu/szkolny-android](https://github.com/szkolny-eu/szkolny-android). No code was copied from it.

## Disclaimer

Not affiliated with or endorsed by Librus. This uses a private API that can change at any time and may be against Librus's Terms of Service. Use it with your own account, at your own risk.

## License

MIT
