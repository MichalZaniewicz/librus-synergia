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
6 are tested, once per `Librus` instance):

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
