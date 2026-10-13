---
name: librus-synergia
description: Use when writing or fixing Python code that uses the librus-synergia library (import librus_synergia) - the unofficial async client for Librus Synergia, the Polish school e-register (e-dziennik). Covers login and saved sessions, the Librus facade, data models, polling for new grades/messages, errors and the API's traps.
---

# librus-synergia

Unofficial, async, fully typed Python client for Librus Synergia (grades,
attendance, timetable, agenda, homework, behaviour notes, announcements, the
lucky number, private messages). Python 3.12+, `aiohttp`, dataclass results.

```bash
pip install librus-synergia
```

Before writing code, read the reference file that fits the task:

- [reference/api.md](reference/api.md) - every `Librus` method and the fields of the models it returns.
- [reference/gotchas.md](reference/gotchas.md) - the traps of the Librus API (ids, timetable shape, messages, kindergarten accounts, point grades...). Read it before parsing or comparing data.
- [reference/examples.md](reference/examples.md) - complete scripts: summary, saved session, polling for changes, timetable, messages, downloads.

The full API notes (login flow, every endpoint's JSON shape, status codes)
are at https://michalzaniewicz.github.io/librus-synergia/.

## The basic pattern

Use the high-level `Librus` facade. Every method logs in lazily on first use
and logs in once more if Librus drops the session early.

```python
import asyncio
import os
from librus_synergia import Librus, parse_grade_value

async def main() -> None:
    async with Librus(os.environ["LIBRUS_LOGIN"], os.environ["LIBRUS_PASSWORD"]) as librus:
        me = await librus.me()                    # the STUDENT, not the parent login
        subjects = await librus.subjects()        # {subject_id: "Matematyka", ...}
        grading = await librus.grading_system()   # the school's +/- values
        for grade in await librus.grades():
            subject = subjects.get(grade.subject_id, "?") if grade.subject_id is not None else "?"
            print(subject, grade.value, parse_grade_value(grade.value, grading))

asyncio.run(main())
```

Rules that matter in every program:

- **Never hardcode credentials.** Read them from the environment
  (`LIBRUS_LOGIN` / `LIBRUS_PASSWORD`, the names the CLI uses) or a secret
  store. Don't print or log the password, session data or personal data.
- **One `Librus` instance and one `aiohttp.ClientSession` per account.** The
  login lives in the cookie jar; two accounts sharing a session silently get
  each other's data. Don't share a session you pass in.
- **Use `async with`** (or `await librus.close()`), so the session the
  library opened is closed.
- **Resolve ids with the lookups**: `subjects()`, `teachers()`,
  `classrooms()`, `grade_categories()`, `attendance_types()`,
  `note_categories()`, `agenda_categories()`. Data records only carry ids.
- **`parse_grade_value(value, grading)`** turns "5+", "4-", "3" into a number
  and returns `None` for marks that aren't grades ("bz", "np", "+", "nk"...).
  Pass `await librus.grading_system()`: schools set their own +/- values.
  Grades with `GradeCategoryData.count_to_average` False don't count; weights
  are `GradeCategoryData.weight`.

## Keep the session between runs

A password login every run is slow and looks unusual to Librus. Save
`librus.session_data` and pass it back. It also carries Librus's year-long
device cookie, which keeps logins captcha-free.

```python
import json
from dataclasses import asdict
from librus_synergia import Librus, LibrusSessionData

def save_session(data: LibrusSessionData) -> None:   # called after every login/renewal
    path.write_text(json.dumps(asdict(data)))
    path.chmod(0o600)

saved = json.loads(path.read_text()) if path.exists() else None
librus = Librus(login, password,
                session_data=LibrusSessionData(**saved) if saved else None,
                on_session_update=save_session)
```

Session data grants access to the account while it's valid: store it like a
password (file mode 0600, never in git). The library renews a session older
than 2 hours through Librus's `refreshToken`, so a long-running program
rarely needs the password again.

## Polling for what's new

For bots and notifications use `fetch_changes()` (about 10 requests) with
`ChangeTracker`, not `fetch_all()` (about 35 requests):

```python
from librus_synergia import ChangeTracker, SeenIds

tracker = ChangeTracker(SeenIds.from_dict(saved_seen) if saved_seen else None)
changes = tracker.update(await librus.fetch_changes())
for grade in changes.grades: ...
save(tracker.seen.to_dict())   # persist, so "new" survives restarts
```

The first `update()` only remembers what exists (nothing is reported), so a
whole school year isn't "new". A kind whose fetch failed
(`data.failed_sections`) is skipped, neither reported nor seeded. `Changes`
has `grades`, `notes`, `announcements`, `messages`, `agenda`, `absences` and
`timetable_changes` (cancelled lessons, substitutions).

**Be gentle: these are real children's school accounts.** Poll at most every
~15 minutes, pass `cache_reference_data=True` to `Librus(...)` in
long-running programs (keeps lookups for a day), never retry in a loop, never
fire dozens of parallel requests.

## Calls with side effects

Everything is read-only except these - call them only on a person's
explicit action:

- `message(id, mailbox)` returns the full body and **marks the message read**
  in Librus. `messages(mailbox, limit=10)` (previews) never marks anything.
- `mark_announcement_read(id)` marks a school notice read.
- `send_message(account_ids, topic, text)`, `reply_to_message(id, text)`,
  `withdraw_message(id)` write to real teachers. A send is never retried; on
  `LibrusMessageSendError`, `err.sent` is `False` (not sent) or `None`
  (unknown - check the outbox before trying again).

## Errors

Everything the library raises for a Librus problem is a `LibrusError`:

| Exception | Meaning | What to do |
|---|---|---|
| `LibrusInvalidCredentialsError` | the login was rejected | stop; don't retry the password in a loop (the library remembers a failed login for 60 s) |
| `LibrusCaptchaRequiredError` | Librus wants a captcha | stop; log in once on the website, keep `session_data` |
| `LibrusSessionExpiredError` | the session died (`status_code`) | the facade already retried once; a 403 on a module means "not available" |
| `LibrusConnectionError` (incl. `LibrusServerMaintenanceError`) | network, timeout, 429/502/503/504 | try again later |
| `LibrusUnexpectedResponseError` | an odd answer or HTTP error (`status_code`) | a 404/403 module usually means the school doesn't use it |

Modules the account is refused (403/404) come back empty from the facade
and aren't asked for again for a day.

## Below the facade

`librus.client` (`LibrusApiClient`) has a raw-JSON method for every endpoint
the library uses; it doesn't log in on its own (`await librus.login()`
first). Pair it with the pure functions in `librus_synergia.parsers`. Prefer
the facade whenever it has a method.

## Command line

```bash
librus-synergia                              # summary
librus-synergia --json > librus.json         # everything as JSON
librus-synergia --watch --state seen.json    # print what's new every 15 min
```

Add `--session session.json` to reuse the login. Credentials come from
`LIBRUS_LOGIN` / `LIBRUS_PASSWORD` or a prompt.
