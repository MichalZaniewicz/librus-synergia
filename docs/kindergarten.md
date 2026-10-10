# Kindergarten accounts

Preschool (przedszkole) accounts use a different timetable API. For these
accounts the regular `Timetables` endpoint returns **403**.

## Timetable

```
GET https://synergia.librus.pl/gateway/ms/kindergartens/timetable/kindergarteners/<child LID>
    ?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
```

```json
{"timetableEntries": [
  {"date": "2026-09-01", "startTime": "08:00", "endTime": "08:30",
   "activityTypeIdentifier": "LID-...", "classroomIdentifier": "LID-...",
   "teachers": ["LID-..."], "type": "planned"}
]}
```

- There are no lesson numbers, only time blocks.
- All references are LID strings.
- `merge_timetables` accepts this payload directly.

## Finding the child's LID

❓ The child identifier (`LID-AUTH-USER-...`) is not in one obvious place.
Candidates, in the order `Librus.kindergartener_id()` tries them (at most
6 are tested; a found child is kept for the `Librus` instance, and a search
that found nothing with every request answered - any 4xx counts as an
answer, a 401 from the kindergarten service too - is repeated at most once
a day. A search whose requests failed (network, timeout, 5xx, a dead
session on the main gateway, a failed login) or that was cancelled isn't
remembered as "nothing found": it is tried again after 5 minutes, and
meanwhile `timetable()` raises that request's error, so `fetch_all()` lists
the timetable in `failed_sections` instead of passing an empty week as
real. Each probe is one plain request: a search never forces a password
login, so a probe that keeps answering 401 - a regular school whose
unpublished timetable also answers 403 - costs no logins; a dead main
session is left to the next ordinary call, which logs in again as usual):

1. `LID-AUTH-USER-...` strings in `Me.User`, then anywhere in `Me`.
2. `Auth/TokenInfo`, then `Auth/UserInfo/<lid>`.
3. `Users/<Me.Account.UserId>` and `Users/<Me.Account.Id>`.

The right candidate is the one whose kindergarten timetable returns
entries. Which source actually yields the child LID has not been confirmed
yet.

## Lookups

| Request | Notes |
|---|---|
| `GET gateway/ms/kindergartens/activities-types` | `activitiesTypes[]`: `identifier`, `name`. These play the role of subjects. |
| `GET gateway/api/2.0/Auth/Users/Kindergarteners/<child LID>` | `data.groupIdentifier`: the group LID used below. |
| `GET gateway/api/2.0/Auth/Classrooms` | `data[]`: `identifier`, `symbol` (`"1"`), `name` (`"sala 1"`). Prefer `name`; the parser adds "sala " to a bare number. |
| `GET gateway/ms/kindergartens/groups/<group LID>` | `name`, `tutors`. |
| `GET gateway/api/2.0/Users` | `Users[].AccountId` matches the teacher LIDs. |
