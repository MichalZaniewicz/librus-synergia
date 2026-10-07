# Changelog

## 0.3.3

### Added
- Point grades: `Librus.point_grades()`, `LibrusApiClient.
  async_get_point_grade_categories()`, `parsers.parse_point_grades()` /
  `parse_point_grade_categories()` / `point_grades_percentage()` (weighted
  earned/possible), `PointGradeData` (with `percentage`) and
  `LibrusData.point_grades` (filled by `fetch_all`). Field names per
  szkolny-android's reference parser - not yet seen in a live payload.
- `parsers.point_grades_enabled(units_payload)`: whether the school grades
  in points (`Units` -> `GradesSettings.PointGradesEnabled`).

### Fixed
- `parse_grade_value` only accepts the 1-6 scale: a number outside it
  (a point or percent grade such as "85") is no longer counted as a grade
  of 85 in averages.

## 0.3.2

### Added
- `Librus.student_number()` / `LibrusApiClient.async_get_student_info_page()`
  + `parsers.parse_student_number()`: the class register number ("Nr w
  dzienniku"). The JSON API doesn't carry it; it comes from Synergia's
  `informacja` web page, which opens with the same session (confirmed live).
- `GradeData.improves_id`: for a correction ("poprawa"), the id of the
  earlier grade it improves (`Grades[].Improvement.Id`, per
  szkolny-android's reference parser - not yet seen in a live payload).

## 0.3.1

### Fixed
- **Classic behaviour grades (wz/bdb/db/popr/ndp/ng) were empty.** The
  grade lives in `BehaviourGrades/Points[].BehaviourGrade.Id`, which wasn't
  read - a real "bdb" came through with an empty `ShortName`/`Text`. New
  `BehaviourGradeData.grade_id`, plus `display` ("bdb", or the points) and
  `name` ("bardzo dobre") properties and the `BEHAVIOUR_GRADE_TYPES` table.

## 0.3.0

### Added
- **`GradeData.teacher_id`**: who added the grade, from `Grades[].AddedBy.Id`
  (a `Users` id, so resolve it with `teachers()`). `None` when the field is
  missing.

## 0.2.0

### Added
- **`ChangeTracker`** reports what's new between snapshots: grades, behaviour notes, announcements, messages, agenda entries, absences and timetable changes. The first update seeds silently, and the seen ids serialize to JSON so "new" survives restarts.
- **Kindergarten accounts in `Librus`.** After a `Timetables` 403, `timetable()` finds the child once and uses the kindergarten timetable API. The lookups include kindergarten activities, rooms and the group.
- **Command line**: `librus-synergia` (or `python -m librus_synergia`) prints an account summary, `--json` dumps everything, and `--watch` checks periodically and prints what's new.

### Changed
- `subjects()`, `teachers()` and `classrooms()` return `dict[int | str, str]`, because kindergarten ids are strings.

## 0.1.0

First release, extracted from the [ha-librus-synergia](https://github.com/MichalZaniewicz/ha-librus-synergia) Home Assistant integration (v0.7.8).

- `Librus`: a high-level typed client with lazy login, one automatic re-login after an early session expiry, and a separate Wiadomości session that bootstraps itself and retries once.
- `LibrusApiClient`: the low-level client, one method per endpoint, returning raw JSON.
- `librus_synergia.parsers`: pure JSON → dataclass parsers.
- `docs/`: unofficial notes on the Librus API.
- Session export includes `DeviceCookie`, which Librus sets under `Path=/OAuth`, and import restores its path.
- A mailbox the account doesn't have (HTTP 404) comes back as an empty list, without a pointless re-login.
