# Changelog

## 0.3.19

### Fixed
- **A Wiadomości outage no longer turns into password logins.** Only a
  rejected session with HTTP 401 can lead to a password login (after one
  extra bootstrap and retry). A 5xx is raised straight away; a 403 or
  another 4xx gets one extra bootstrap and is then raised. When a forced
  login didn't fix a call, Wiadomości calls in the next 10 minutes raise
  the error instead of logging in again.
- **One "Brak dostępu" no longer switches messages off for days.** A
  bootstrap that said the school has no messages module is asked again
  after 5 minutes (three times in a row), then hourly - a session kept alive
  through `/refreshToken` never logs in again, so the old "ask again after
  the next login" could take days. A "Brak dostępu" right after a 401 now
  goes on to the password login instead of reporting "no messages module".
- **`session_data` after `close()`** gives the cookies the session had when
  it closed (it used to open a new, never-closed session and return the
  cookies passed to the constructor). Reading it never opens a session.
- **A kindergarten search that couldn't finish isn't remembered as "nothing
  found".** The day-long "nothing found" mark is set only after every
  request was answered (a refusal counts as an answer). A search whose
  requests failed is tried again after 5 minutes, and until then
  `timetable()` raises that request's error - so `fetch_all()` lists the
  timetable in `failed_sections` and `ChangeTracker` doesn't seed an empty
  week (and later report lessons cancelled long ago as new). A cancelled
  search leaves nothing behind.
- **`max_concurrent_requests` below 1 raises `ValueError`** (0 used to hang
  every request forever).
- **Parsers no longer crash on an odd nested value.** A reference like
  `"Category": {"Id": 5}` that comes back as a string, a list or a number
  now just has no id, and a list field that isn't a list counts as empty
  (grades, notes, attendances, lessons, agenda, homework, behaviour, point,
  text and descriptive grades, conferences, `Me`, class, justifications,
  messages, ...). `decode_message_content` returns `""` for anything that
  isn't a string, `parse_comment_text_map` and `parse_student_number` accept
  odd input. The Home Assistant integration calls these parsers directly,
  so one odd record used to fail its whole update.
- **A null name no longer becomes "None"**: a message sender with a null
  first or last name, a null student name in `Me`, a null school name and a
  null class symbol become `""`.
- **`tzdata` is a dependency everywhere** (it was Windows-only), so minimal
  containers (Alpine, ...) without a system time zone database still get
  "today" in Poland. When the data is missing anyway, a warning is logged
  once instead of silently using the machine's date.
- **A download redirect to `http://sandbox.librus.pl` is not followed** (the
  session cookies would travel unencrypted); it is
  `LibrusUnexpectedResponseError`. Only `https://sandbox.librus.pl` is
  followed, as for message attachments.
- **A reference lookup that just failed isn't asked again in the same
  call**: when `cache_reference_data` served an expired copy because the
  refresh failed, the "unknown id" check no longer repeats the failing
  request.

### Changed
- **File downloads have their own limit of 2 at a time**
  (`MAX_CONCURRENT_DOWNLOADS`), outside `max_concurrent_requests`, so
  downloads waiting on the sandbox (up to 150 s) never hold up ordinary
  requests.
- `fetch_changes()` returns grades without comment texts (change tracking
  doesn't compare them) and never fetches the comments list.

### Performance
- **Comment texts are kept.** `grades()`, `behaviour_grades()` and
  `descriptive_grades()` fetch a comments list again only when an item
  refers to a comment id it hasn't been asked for, or after a day (a failed
  refresh keeps the old texts). The descriptive-grade skills list (~330 KB)
  is kept the same way.
- With `cache_reference_data=True`, text-grade and point-grade categories,
  free days (2 requests) and the standing plan (`TimetableEntries`) are kept
  too.
- The CLI's `--watch` turns `cache_reference_data` on.

## 0.3.18

### Fixed
- **Timeouts no longer escape as a bare `TimeoutError`.** Every request -
  data, the login steps, `refreshToken`, the Wiadomości bootstrap, the
  "Informacje" page, downloads - raises `LibrusConnectionError` on a network
  error or a timeout (`async_refresh_session` returns False instead).
- **No more relogin stampede.** Requests that hit the same dead session wait
  for one login instead of each logging in. A failed login is remembered for
  60 s: calls in that time that would need the password get the same error
  without sending it again. A failed login also leaves the client "not
  logged in", so the next call doesn't trust half-replaced cookies.
- **One failure no longer leaves requests running.** `fetch_all()` and the
  paired lookups use a `TaskGroup`: when one part fails for good, the rest
  are cancelled, and the error is raised as itself (not an
  `ExceptionGroup`).
- **A failing `refreshToken` isn't retried on every call** - at most every
  30 minutes. A refresh only counts when the answer sets a new
  `oauth_token` cookie.
- **`ChangeTracker` no longer seeds a kind whose fetch failed.** New
  `LibrusData.failed_sections` (filled by `fetch_all()`/`fetch_changes()`):
  a failed kind is skipped - neither reported nor remembered - and seeded
  silently the first time its data arrives. Absences need both attendances
  and their types. `SeenIds` gained `seeded` (saved by `to_dict()`); a dict
  saved by an older version counts every kind as seeded.
- **Bodies in another charset, or with odd bytes, no longer crash.** Error
  pages, login answers, the "Informacje" page and the bootstrap are read in
  the charset they name, with bad bytes replaced; JSON in a non-UTF-8
  charset is still decoded.
- **Parsers skip records without a usable id** instead of crashing on
  `int()` (grades, categories, comments, notes, attendance types, agenda,
  homework, behaviour and descriptive grades, conferences, free days, id-name
  lookups, the lesson map). Attendance ids like `"t123"` still work. A null
  `Grade`, `content`, `Message`, `topic`, `Text`, `Content` becomes `""`
  (not `"None"` or a crash), and a category `Weight` given as a string
  (`"2"`, `"1,5"`) is read; an unreadable one counts as 1.
- **Login steps 1 and 2 read and release their responses.**
- **CLI `--watch` keeps going** through network errors, timeouts and odd
  answers (prints the error, tries again next time); it stops only when the
  login fails. The session file is written readable by the owner only
  (mode 0600).
- **A rejected Wiadomości session is first only bootstrapped again** (one
  request) and retried; a password login follows only if that fails too.
  Any password login makes the next Wiadomości call bootstrap again, and
  concurrent calls share one bootstrap.
- **"Today" is today in Poland** (`Europe/Warsaw`) for the default
  timetable week, the kindergarten search and `ChangeTracker`'s timetable
  changes. `tzdata` is now a dependency on Windows.
- **A kindergarten search that found nothing is repeated after a day**
  (it used to run once per instance).

### Changed
- **HTTP 429, 502 and 504 raise `LibrusConnectionError`** with
  `status_code` (502/504 used to be `LibrusUnexpectedResponseError`).
  `LibrusConnectionError` gained an optional `status_code`; 503 is still
  `LibrusServerMaintenanceError`, now with `status_code=503`. 401/403/404
  are unchanged. Neither logs in again.
- **Optional modules Librus refuses come back empty from `Librus`** and
  aren't asked for again for a day: `point_grades()` (+ categories),
  `parent_teacher_conferences()`, `text_grades()`, `school_trips()`,
  `school_files()`, `justifications()`, `behaviour_grades()`. Other errors
  are still raised.
- `grades()` and `behaviour_grades()` keep the grades when the comment
  lookup fails (without comment text).
- `GradeCategoryData.weight` is typed `float` (still an `int` for a whole
  weight).
- New `LibrusApiClient.login_count`, `Librus.point_grades_enabled()`.
- `LibrusAccountActionRequiredError` is documented as reserved: nothing
  raises it yet.

### Performance
- **Default request timeout**: 30 s per data/login request, 10 s to connect
  (`request_timeout=`; None leaves it to the session). Downloads keep their
  own 150 s deadline.
- **At most 6 requests at a time** per `Librus` (`max_concurrent_requests`);
  a session the library creates allows 6 connections per host.
- **`Librus.fetch_changes()`**: only what `ChangeTracker` compares (~10
  requests instead of ~35). The CLI's `--watch` uses it.
- **`cache_reference_data=True`** keeps subjects, teachers, classrooms,
  categories, attendance types, the lesson map, school and class for
  `reference_ttl` (default a day), fetching one again early when the data
  mentions an unknown id.
- `point_grades()` doesn't ask when `Units` says point grades are off, and
  fetches the categories only when there are grades; text-grade categories
  likewise.
- Comment lists (grades, behaviour and descriptive grades) are fetched only
  when an item refers to a comment; the descriptive skills list only when a
  grade has a skill.
- Messages run alongside the rest of `fetch_all()` instead of after it.
- Small error bodies (up to 64 KB) are read before raising, so the
  connection can be reused.
- JSON is decoded straight from bytes (with `orjson` when it's installed).

## 0.3.17

### Fixed
- **A download page answering 200 without a redirect is an expired session
  again**, unless it plainly says the file isn't there ("nie znaleziono",
  "nie istnieje", "not found") and isn't the logged-out page. A download is
  started by a person, so a wrong guess costs one login. The page is now read
  in the charset it names, with ASCII-only markers, so an odd encoding
  doesn't hide "Brak dostępu".
- **A message attachment download no longer logs in again after a timeout or
  an odd answer.** `Librus.download_attachment` (and every Wiadomości call)
  retries with a fresh login only when the session was rejected or an HTTP
  error status came back - not on a network error, the download deadline, or
  an answer without an error status (a failed download key, a link outside
  the sandbox, a body that isn't JSON).
- **The 150 s download deadline now covers the whole `Librus` call** -
  logins and a retry included - for message attachments, homework files and
  school documents.
- **"Insufficient scopes" is only recognised on the data gateway**, and a
  401 whose body can't be read (a cut-off connection, a timeout, a body over
  64 KB) stays an expired session.
- **Invalid links no longer escape as `ValueError`.** A redirect or a
  `downloadLink` that isn't a valid URL is `LibrusUnexpectedResponseError`,
  and a message attachment's `downloadLink` must be `https`.
- **`parse_partial_grades` ignores a bool** given as `subjectId` or as the
  scale value (`True` is not subject 1 or the grade "True").

### Changed
- **`async_download_school_file` raises `LibrusUnexpectedResponseError`**
  (was `ValueError`) for a path that isn't on `https://synergia.librus.pl`
  or isn't a valid URL - still before any request. Catch `LibrusError`
  instead of `ValueError`.
- **Refusals are remembered for a day, not for good.**
  `Librus.partial_grades()` asks again 24 h after a 403/404/405 (or
  "Insufficient scopes"); `Librus.student_identifier()` now also remembers
  such a refusal of its lookup for 24 h; `Librus.grading_system()` returns
  the defaults on such a refusal and asks again after 24 h (other errors are
  still raised). The `Auth/Subjects` lookup for partial grades is kept for
  24 h instead of being fetched every time.
- Download timeouts say whether the library's own deadline or aiohttp's
  timeout fired.

## 0.3.16

### Fixed
- **"Insufficient scopes" is no longer an expired session.** A 401 whose body
  says `Insufficient scopes` (endpoints the account may not use, e.g.
  `SchoolInfo`, `Duties`) now raises `LibrusUnexpectedResponseError`
  (`status_code=401`), so callers don't log in again on every poll. A plain
  401 is still `LibrusSessionExpiredError`.
- **A download page without a redirect is an expired session only when it is
  the logged-out page** ("Brak dostępu" or the login form). Any other page is
  `LibrusUnexpectedResponseError`.
- **Downloads only follow `sandbox.librus.pl`**, compared by host (a
  look-alike host is no longer accepted), also for a message attachment's
  `downloadLink`.
- **A `CSCheckKey` answer that is JSON but not an object** (a list, a string)
  counts as a failed key instead of crashing.
- **`parse_partial_grades`**: one comment given on its own (not in a list) is
  read, other odd comment values are ignored, a numeric `subjectId` is used as
  the subject id, and a scale value of `0` stays `"0"`.
- **`parse_grade_value`**: `"0+"` and `"0-"` are not grades (None).
- **`parse_grading_system`**: numbers sent as strings (`"0.5"`) are read, and
  `plusValue` is taken as a size like `minusValue`.
- **`Librus.student_identifier()`** no longer remembers a failed lookup as
  "no child LID" (only a real answer is kept), and concurrent calls ask
  Librus once.

### Changed
- **Downloads give up after 150 s** (`DOWNLOAD_TIMEOUT_SECONDS`) with
  `LibrusConnectionError`. The worst case used to be about six minutes.
- **`async_download_school_file` only accepts a path or URL on
  `https://synergia.librus.pl`**; anything else raises `ValueError`, so the
  session cookies never go to another host.
- **`Librus.grading_system()` is read once per instance**, and
  **`Librus.partial_grades()` remembers** a 403/404/405 (or "Insufficient
  scopes") as "module not available" and stops asking.
- `LibrusApiClient._async_request_url` refuses an HTTP method other than GET
  or POST (`ValueError`) instead of silently sending a GET.

## 0.3.15

### Added
- **Download a school document.** `LibrusApiClient.async_download_school_file()`
  / `Librus.download_school_file()` fetch a document from `SchoolFiles`
  (`/pliki_szkoly/pobierz/<id>` redirects to the sandbox, checked live). The
  link alone needs a logged-in Synergia session, so it doesn't work in a
  browser that isn't logged in.

### Fixed
- **A homework file download after Synergia's web session expired.** The API
  session can keep working while the web session behind
  `homework/downloadFile` has died; the page then answers 200 instead of
  redirecting to the file. That is now reported as an expired session
  (`LibrusSessionExpiredError`), so a fresh login fixes it, instead of a
  "no download link" error (seen live).

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
