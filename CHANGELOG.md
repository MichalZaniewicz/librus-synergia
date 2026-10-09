# Changelog

## 0.3.14

### Added
- **The school's own `+` and `-` values.** `Librus.grading_system()` /
  `parse_grading_system()` read `GradingSystem` (what `+` adds, what `-`
  takes away, whether `0` counts), and `parse_grade_value(value, grading)`
  uses them. Without it the values stay +0.5 / -0.25, which is what the
  tested school uses. `LibrusData.grading_system` carries it.
- **The new descriptive grading for grade 1** (some schools from 2026):
  `Librus.partial_grades()`, `LibrusApiClient.async_get_partial_grades()`
  (a POST), `parse_partial_grades()`, `parse_auth_subjects()`
  (`Auth/Subjects`), `extract_student_identifier()` and
  `Librus.student_identifier()`. They come back as `DescriptiveGradeData`
  with `source="partial"`, a string id (`"p<gradeId>"`), `teacher_lid` and
  `requirements`; `fetch_all()` adds them to `descriptive_grades`. The
  endpoint is confirmed reachable, but the grade fields are known only from
  another client's code - no such grade has been seen yet.
- **Read receipts for sent messages.** `FullMessageData.receivers` lists the
  recipients of a sent message (`outbox/messages/<id>`) with when each one
  read it (`MessageReceiverData.read_date`).

### Fixed
- A descriptive grade without `Map` / `RealGradeValue` no longer takes its
  value from `Grade`: that field is the grade's range on the scale (1-3, seen
  in `DescriptiveGrades/Types`), so a "6" would have shown as "3". The value
  is empty in that case.

## 0.3.13

### Fixed
- **Descriptive grades show the real grade.** `DescriptiveGradeData.value`
  now comes from `Map` (falling back to `RealGradeValue`): the `Grade` field
  read before holds something else - a "6" in Synergia came through as `3`
  (seen live, ha-librus-synergia #13).

### Added
- `DescriptiveGradeData.skill` (the skill name, e.g. "Ekspresja muzyczna.
  Śpiew"), `comments` (the teacher's comments), `teacher_id`, `date`,
  `semester` and `comment_ids`. `parse_descriptive_skills()`,
  `LibrusApiClient.async_get_descriptive_grade_skills()` and
  `async_get_descriptive_grade_comments()` (`DescriptiveGrades/Skills`,
  `DescriptiveGrades/Comments`); `Librus.descriptive_grades()` fills in skill
  names and comments (fetching both only when there are grades).

## 0.3.12

### Fixed
- **A homework file download that the sandbox rejects is retried with a
  fresh key.** Found live: now and then Librus's sandbox answers a download
  key with `download_failed` (or something that isn't JSON), while a new
  key for the same file works a moment later. `download_homework_attachment`
  now tries up to three keys before giving up.

## 0.3.11

### Fixed
- **The first download of a homework file no longer gives up too early.**
  Found live: the sandbox can take more than 30 seconds to prepare a file
  the first time (later downloads are quick). `download_homework_attachment`
  now waits up to about two minutes, like Librus's own download page.
- A failed homework download says why: still not ready after all the
  checks, the sandbox's own status (e.g. `download_failed`), or what
  `CSDownload` answered.

## 0.3.10

### Fixed
- **Homework attachment download** now works (confirmed live with a real
  file): after Synergia's redirect it opens the sandbox's
  `CSTryToDownload` page, POSTs `CSCheckKey` until the file is ready and
  GETs `CSDownload`. 0.3.9 polled with GET and never got the file.
- `HomeworkAssigmentFiles` items are confirmed to be `{Id, Name, Url}`.

## 0.3.9

### Added
- **Homework attachments:** `HomeworkAssignmentData.attachments` (from
  `HomeworkAssigmentFiles`, parsed defensively - no real example seen yet)
  and `Librus.download_homework_attachment(attachment_id)` /
  `client.async_download_homework_attachment` (Synergia's
  `homework/downloadFile` redirect to sandbox.librus.pl, both the `GetFile`
  and the `singleUseKey` flow). Not tried live.
- **Sent messages:** `MessageData.receiver_name`, filled for
  `messages("outbox")`. `messages("archive/inbox")` lists past school years.
- `LibrusData.sent_messages` / `archived_messages` (left empty by
  `fetch_all()`).

### Fixed
- A non-JSON error page (e.g. an HTML 404) now carries its HTTP status in
  `LibrusUnexpectedResponseError.status_code`, so a missing mailbox is
  recognised as such.

## 0.3.8

No code changes.

### Changed
- **The PyPI page:** the README's trailer (now a 4.4 MB WebP) and a new
  "Don't want to read? Watch the video" section with a 2.5-minute demo. On
  PyPI, which can't play video, the player becomes a clickable thumbnail;
  relative links become absolute and the GitHub-only `[!TIP]` marker is
  dropped (`hatch-fancy-pypi-readme` at build time).

## 0.3.7

### Added
- **The standing weekly plan:** `Librus.standing_timetable()`
  (`TimetableEntries`): every lesson slot by weekday and lesson number, with
  the dates it is valid for, its room, and the subject resolved through
  `Lessons`. `StandingLessonData`, `parse_timetable_entries`, and
  `LibrusData.standing_timetable` in `fetch_all()`.
- **How a week differs from the plan:** `parsers.plan_differences(timetable,
  standing, free_days)` compares real weeks with the standing plan, slot by
  slot: `cancelled`, `missing`, `extra`, `subject`, `room`, and `no_lessons`
  for a weekday without lessons (with the free day's name when Librus has
  one). `PlanDifferenceData`.

## 0.3.6

### Fixed
- Text grades (`text_grades()`, `TextGradeData.value`): the line breaks and
  indentation Librus keeps in the teacher's text are collapsed to single
  spaces ("diagnoza GWO - sesja I 80%" instead of a line break followed by six
  spaces).

## 0.3.5

### Added
- **Text grades:** `Librus.text_grades()` (`BaseTextGrades` with category
  names from `TextGrades/Categories`) - free-text grades that `grades()`
  never contained. `TextGradeData`, `parse_text_grades`,
  `parse_text_grade_categories`.
- **Lesson topics:** `Librus.lesson_topics()` (`Realizations`): every lesson
  held with its topic, date, lesson number, whether it was a trip, and the
  subject resolved through `Lessons`. `LessonTopicData`, `parse_realizations`.
- **School trips and documents:** `Librus.school_trips()` (`SchoolTrips`) and
  `Librus.school_files()` (`SchoolFiles`). `SchoolTripData`, `SchoolFileData`.
- **Message attachments:** `Librus.download_attachment(attachment_id,
  message_id)` / `LibrusApiClient.async_download_message_attachment()`
  download a file (name, type, bytes) without opening the message.
- **Session refresh:** `LibrusApiClient.async_refresh_session()`
  (`synergia.librus.pl/refreshToken`); `async_ensure_session_valid` now
  refreshes a session older than 2 hours instead of waiting for it to expire
  and logging in with the password again.
- `HomeworkAssignmentData.category_id` / `lesson_id`, `Librus.homework_categories()`
  (`HomeWorkAssignments/Categories`).
- `fetch_all()` fills `LibrusData.text_grades`, `lesson_topics`,
  `school_trips`, `school_files` and `homework_assignment_categories`.

### Changed
- `Librus.student_number()` reads `ClassRegisterNumber` from the student's
  own `Users` record first and only falls back to the `informacja` web page.

All of it confirmed on a real account (2026-10-07).

## 0.3.4

### Added
- Absence justifications: `Librus.justifications()`,
  `LibrusApiClient.async_get_justifications()`, `parsers.parse_justifications()`
  (`JustificationData` with `is_accepted` / `is_pending` / `is_rejected`),
  `parsers.justified_dates()` and `LibrusData.justifications` (filled by
  `fetch_all`). Confirmed live; only the "accept" status seen so far.
- `LessonData.original` (`OriginalLessonData`: date, lesson number, hours,
  subject, teacher and classroom as originally planned - the `Org*` fields
  of a substituted lesson, confirmed live), `LessonData.substitution_note`
  and `LessonData.room_changed`.

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
