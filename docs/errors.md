# Errors and status codes

| Status | Where | Meaning | What to do | Exception |
|---|---|---|---|---|
| 401 | any data endpoint | ✅ The session died, often before the expected ~24 h. The password is still fine. | Log in again once and retry. `Librus` does this automatically. | `LibrusSessionExpiredError(status_code=401)` |
| 401 + `Insufficient scopes` | `SchoolInfo`, `Duties`, `WhatsNew`, `Reports`, ... | ✅ The account may not use this endpoint at all. Not a dead session - logging in again doesn't help. | Treat as not available; don't log in again. | `LibrusUnexpectedResponseError(status_code=401)` |
| 403 | `Timetables` | ✅ The school hasn't published this class's timetable yet (Synergia's own web UI says "nie został jeszcze opublikowany"), **or** it is a kindergarten account. Logging in again does not help. | Treat as an empty timetable. | `LibrusSessionExpiredError(status_code=403)` |
| 403 | other endpoints | ✅ The module is not available to this account type (e.g. `Substitutions`, `TeacherFreeDays`, or `Attendances/Types` on a messages-only preschool login). | Treat as empty. | same |
| 404 | `AttendanceTypes`, some mailboxes | Wrong path, or the mailbox doesn't exist for this account. | — | `LibrusUnexpectedResponseError` |
| 503 | any | ✅ Maintenance. Seen on `Me` too. Windows observed so far were short: the next poll 20 minutes later worked. | Retry later. | `LibrusServerMaintenanceError` |
| 200 + HTML | any | The response isn't JSON (often a login page). | — | `LibrusUnexpectedResponseError` |

File downloads (homework attachments, school documents, message
attachments):

| Case | Meaning | Exception |
|---|---|---|
| The Synergia page answers 200 with its logged-out page ("Brak dostępu" or the login form) instead of redirecting | ✅ The web session died while the API session still works. A fresh login fixes it. | `LibrusSessionExpiredError` |
| The page answers 200 with any other page, or no redirect at all | Not a session problem (e.g. the file is gone). | `LibrusUnexpectedResponseError` |
| The page redirects anywhere but `sandbox.librus.pl` | Synergia sends a dead session to its login page. | `LibrusSessionExpiredError` |
| A message attachment's `downloadLink` isn't on `sandbox.librus.pl` | Not followed. | `LibrusUnexpectedResponseError` |
| A school document path that isn't on `https://synergia.librus.pl` | Refused before any request, so the session cookies never go elsewhere. | `ValueError` |
| The whole download took longer than 150 s (`DOWNLOAD_TIMEOUT_SECONDS`) | The sandbox is too slow; try again later. | `LibrusConnectionError` |

Login failures:

| Case | Exception |
|---|---|
| The credentials POST returned no `goTo` (❓ assumed wrong password) | `LibrusInvalidCredentialsError` |
| A captcha marker appeared in the login flow | `LibrusCaptchaRequiredError` |
| Network, DNS or timeout error | `LibrusConnectionError` |

All of these inherit from `LibrusError`. Authentication problems inherit
from `LibrusAuthError`.

## Be gentle

These are real children's school accounts. Do not retry in a loop, do not
fire dozens of parallel requests, and cache lookups for a day. Librus may
have abuse heuristics, and nobody has tested where their limits are.
