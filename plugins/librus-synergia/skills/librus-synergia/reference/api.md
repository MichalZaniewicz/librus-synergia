# librus-synergia API reference

All methods are `async` methods of `Librus` unless marked otherwise.
Everything is importable from `librus_synergia` (`Librus`, `ChangeTracker`,
`SeenIds`, `Changes`, `TimetableChange`, `LibrusSessionData`,
`LibrusApiClient`, `parse_grade_value`, `parsers`, the exceptions); the
dataclasses live in `librus_synergia.models`.

## Constructor

```python
Librus(
    username, password, *,
    session=None,                 # your aiohttp.ClientSession (one per account!)
    session_data=None,            # LibrusSessionData from a previous run
    on_session_update=None,       # callback(LibrusSessionData), sync or async
    request_timeout=...,          # aiohttp.ClientTimeout, default 30 s (10 s connect); None = session's
    max_concurrent_requests=6,
    cache_reference_data=False,   # keep lookups for reference_ttl seconds
    reference_ttl=86400,
)
```

`librus.session_data` (property) - current cookies to persist.
`librus.client` (property) - the low-level `LibrusApiClient`.
`await librus.login(force=False)`, `await librus.close()`.

## Methods

| Method | Returns |
|---|---|
| `me()` | `MeData` - the student (not the parent login) |
| `student_number()` | `int | None` - number in the class register |
| `school()` | `SchoolData | None` |
| `school_class()` | `ClassData | None` - class, tutor, semester dates |
| `subjects()`, `teachers()`, `classrooms()` | `dict[int | str, str]` id -> name |
| `grades()` | `list[GradeData]`, teacher comments resolved |
| `grade_categories()` | `dict[int, GradeCategoryData]` - names, weights, counts-to-average |
| `grading_system()` | `GradingSystemData` - pass to `parse_grade_value` |
| `text_grades()` | `list[TextGradeData]` - free-text grades (not in `grades()`) |
| `descriptive_grades()` | `list[DescriptiveGradeData]` |
| `partial_grades()` | `list[DescriptiveGradeData]` - new grade-1 descriptive grading (`source="partial"`) |
| `point_grades()`, `point_grades_enabled()` | `list[PointGradeData]`; `bool | None` |
| `behaviour_grades()` | `list[BehaviourGradeData]` - ocena zachowania |
| `notes()`, `note_categories()` | `list[NoteData]` (uwagi); `dict[int, str]` |
| `attendances()`, `attendance_types()` | `list[AttendanceData]`; `dict[int, AttendanceTypeData]` |
| `justifications()` | `list[JustificationData]` - absence excuses and their status |
| `timetable(week_of=None)` | `dict[date, list[LessonData]]` - the week containing `week_of` (default this week, Polish time); `{}` when the school hasn't published it |
| `standing_timetable()` | `list[StandingLessonData]` - the regular weekly plan |
| `plan_changes()` | `list[PlanChangeData]` - every cancellation/substitution this school year |
| `lesson_topics()` | `list[LessonTopicData]` - lessons held, with topics |
| `agenda()`, `agenda_categories()` | `list[HomeworkEventData]` (terminarz: tests, trips, meetings); `dict[int, str]` |
| `homework()`, `homework_categories()` | `list[HomeworkAssignmentData]` (zadania domowe); `dict[int, str]` |
| `free_days()` | `list[FreeDayData]` |
| `parent_teacher_conferences()` | `list[ParentTeacherConferenceData]` |
| `school_trips()`, `school_files()` | `list[SchoolTripData]`; `list[SchoolFileData]` |
| `library_lendings()` | `list[LibraryLendingData]` |
| `announcements()` | `list[SchoolNoticeData]` (tablica ogłoszeń) |
| `mark_announcement_read(id)` | `None` - **marks the notice read** |
| `lucky_number()` | `LuckyNumberData | None` |
| `unread_messages()` | `dict[str, int]` - unread count per mailbox (`inbox`, `notes`, `alerts`...) |
| `messages(mailbox="inbox", *, limit=10)` | `list[MessageData]` - previews, never marks read; `"outbox"` = sent, `"archive/inbox"` = past years |
| `message(id, mailbox="inbox")` | `FullMessageData | None` - full body, **marks it read** |
| `message_recipients()` | `list[MessageRecipientData]` |
| `send_message(account_ids, topic, text)` | `SentMessageData` - **sends**, never retried |
| `reply_to_message(id, text)` | `SentMessageData` - **sends** a reply in the thread |
| `withdraw_message(id)` | `None` |
| `download_attachment(attachment_id, message_id)` | `AttachmentFileData | None` (`None` when the school has no messages module); doesn't open the message |
| `download_homework_attachment(attachment_id)` | `AttachmentFileData` |
| `download_school_file(download_path)` | `AttachmentFileData` - path from `SchoolFileData.download_path` |
| `kindergartener_id()` | `str | None` - set on kindergarten accounts |
| `student_identifier()` | `str | None` - child's `LID-AUTH-USER-...` |
| `fetch_all(include_messages=True)` | `LibrusData` - full snapshot (~35 requests) |
| `fetch_changes(include_messages=True)` | `LibrusData` - only what `ChangeTracker` compares (~10 requests) |

## Models (fields)

Dates are ISO strings (`"2026-10-13"`, datetimes `"2026-10-13 08:00:00"`)
unless a field is a `date`. Ids are `int`, except where noted in
[gotchas.md](gotchas.md).

- `MeData`: account_id, first_name, last_name; `display_name`
- `SchoolData`: name, town, street, building_number, post_code, head_teacher_name, email, phone_number
- `ClassData`: number, symbol, tutor_id, begin_school_year, end_first_semester, end_school_year; `display_name` ("7d")
- `GradeData`: id, value ("5+", "bz"...), category_id, subject_id, semester, add_date, is_semester_proposition, is_final_proposition, is_semester, is_final, comments (list of str), teacher_id, improves_id (the grade this one corrects)
- `GradeCategoryData`: id, name, count_to_average, weight
- `GradingSystemData`: plus_value, minus_value, count_zero
- `TextGradeData`: id, value, subject_id, lesson_id, category_id, category, teacher_id, date, add_date, semester, counts_to_average
- `DescriptiveGradeData`: id, subject_id, value, skill_id, category_id, add_date, skill, teacher_id, date, semester, comment_ids, comments, source, teacher_lid, requirements
- `PointGradeData`: id, subject_id, value, points, max_points, category_id, category, weight, counts_to_average, semester, add_date, teacher_id; `percentage`
- `BehaviourGradeData`: id, value, short_name, semester, category_id, teacher_id, add_date, text, comments, grade_id; `display`, `name`
- `NoteData`: id, text, category_id, teacher_id, date, positive; `sentiment` ("positive"/"negative"/"neutral")
- `AttendanceData`: id (`int` or `str`), lesson_id, lesson_no, date, semester, type_id
- `AttendanceTypeData`: id, name, is_presence_kind; `is_excused_absence`
- `JustificationData`: id, status, message, posted, date_from, date_to, lessons, justified_absences, has_attachment, teachers; `is_accepted`, `is_pending`, `is_rejected`
- `LessonData`: lesson_no (`None` on kindergarten accounts), hour_from, hour_to, subject_id, teacher_id, classroom_id, is_canceled, is_substitution, teacher_ids, original (`OriginalLessonData` - what a substitution replaces), substitution_note; `room_changed`
- `OriginalLessonData`: date, lesson_no, hour_from, hour_to, subject_id, teacher_id, classroom_id
- `StandingLessonData`: id, lesson_id, day_of_week, lesson_no, date_from, date_to, classroom_id, classroom, subject_id
- `PlanChangeData`: id, original_date, original_lesson_no, original_subject_id, original_teacher_id, original_classroom, cancelled, shifted, date, lesson_no, subject_id, teacher_id, classroom; `kind`
- `LessonTopicData`: id, date, lesson_no, lesson_id, topic, is_trip, teacher_id, subject_id
- `HomeworkEventData` (agenda entry): id, date, content, category_id, subject_id, time_from
- `HomeworkAssignmentData`: id, topic, text, teacher_id, date, due_date, category_id, lesson_id, attachments
- `FreeDayData`: id, name (a class entry: its type's name, e.g. "Wycieczka"), date_from, date_to, type_id, lesson_no_from, lesson_no_to; `whole_day` (False when only some lessons are off)
- `ParentTeacherConferenceData`: id, topic, teacher_id, date, time
- `SchoolTripData`: id, destination, route, transport, date_from, date_to, coordinator
- `SchoolFileData`: id, name, added, download_path
- `LibraryLendingData`: id, title, author, kind, lent_on, due_date, extendable
- `SchoolNoticeData`: id (`str`), subject, content (full text), start_date, end_date, creation_date, was_read
- `LuckyNumberData`: day, number
- `MessageData`: id (`str`), sender_name, topic, content (shortened preview), send_date, read_date (`None` = unread), has_attachment, mailbox, receiver_name (sent messages)
- `FullMessageData`: id, mailbox, sender_name, topic, content, send_date, read_date, attachments (`AttachmentData`: id, filename), receivers (`MessageReceiverData`: name, group, read_date), sender_id, no_reply, can_withdraw, withdrawn
- `MessageRecipientData`: account_id, name, group, group_name, subjects, available, return_at
- `SentMessageData`: message_id (may be `None` - a reply has none), status
- `AttachmentFileData`: filename, content_type, content (bytes)
- `LibrusData` (`fetch_all` / `fetch_changes`): me, grades, grade_categories, notes, attendances, attendance_types, timetable, homeworks (agenda), homework_assignments, school_notices, lucky_number, subjects, teachers, classrooms, messages_available, unread_message_count, unread_messages_by_mailbox, messages, school, school_class, free_days, behaviour_grades, descriptive_grades, point_grades, grading_system, justifications, text_grades, lesson_topics, school_trips, school_files, library_lendings, plan_changes, parent_teacher_conferences, standing_timetable, sent_messages, archived_messages, the category lookups, and `failed_sections` (what couldn't be fetched)

## Change tracking

```python
tracker = ChangeTracker(seen: SeenIds | None = None)
changes: Changes = tracker.update(data: LibrusData)
tracker.seen.to_dict()  /  SeenIds.from_dict(d)
tracker.is_seeded
```

`Changes`: grades, notes, announcements, messages, agenda, absences,
timetable_changes (`TimetableChange`: date, lesson, kind = "canceled" |
"substitution"). `bool(changes)` is True when anything is new.

## Parsers (pure functions, `librus_synergia.parsers`)

- `parse_grade_value(value, grading=None)` - also exported at the top level.
- `point_grades_percentage(grades)` - weighted earned/possible percentage.
- `merge_timetables(*payloads)` - raw `Timetables` JSON -> `{date: [LessonData]}`.
- `plan_differences(timetable, standing, free_days=None)` - how fetched weeks differ from the standing plan (`PlanDifferenceData`).
- `justified_dates(justifications)` - days already covered by an excuse.
- One `parse_*` function per endpoint, for raw JSON from `librus.client`.
