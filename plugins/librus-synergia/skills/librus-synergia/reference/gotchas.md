# Traps of the Librus API (and how the library handles them)

Most of these were confirmed on real accounts. The library already handles
them - this file is what your code must still know.

## Ids

- Look names up with the id **exactly as the record gives it**:
  `subjects.get(lesson.subject_id)`. The lookups are `dict[int | str, str]`.
- Most ids are `int`. Exceptions: `SchoolNoticeData.id` and
  `MessageData.id` are `str`; `AttendanceData.id` can be a string like
  `"t41685"`; on kindergarten accounts subject/teacher/room ids are
  `LID-...` strings. Don't `int()` ids yourself.
- Timetable ids arrive from Librus as strings (`"41999"`) while other
  endpoints use ints; the parsers normalise them, so lookups match.

## Who is who

- `me()` is the **student** (`Me.User`). The login is often the parent's
  account; don't greet the parent with the child's name or the other way
  round.
- One `Librus` instance per child. Two children = two instances, two
  `aiohttp` sessions, two saved sessions.

## Timetable

- `timetable()` covers **one week** (the week containing `week_of`,
  default this week in Polish time). Fetch other weeks with `week_of=`.
- A day's list can hold **several lessons with the same `lesson_no`**
  (split groups, a support teacher). Group by `lesson_no` / `hour_from`
  rather than assuming one lesson per period.
- Cancelled lessons stay in the list with `is_canceled=True`. A
  substitution has `is_substitution=True`, `original` (what it replaces:
  subject, teacher, room, date, period) and maybe `substitution_note`;
  `room_changed` flags a new room. A lesson moved from another day has
  `original.date` set to that day.
- `{}` means the school hasn't published the timetable (Librus answers 403),
  not an error.
- Kindergarten accounts: the library switches to the kindergarten timetable
  on its own; lessons are time blocks with `lesson_no=None` - sort by
  `hour_from`.
- `plan_changes()` covers the whole school year (cancellations,
  substitutions); `timetable()` only the requested week.

## Grades

- `value` is a string: "5", "5+", "4-", "6", but also "bz", "np", "nk",
  "zw", a bare "+" or "-". `parse_grade_value()` returns `None` for marks
  that aren't grades - skip those in averages.
- `+` and `-` values are a school setting: pass `grading_system()` to
  `parse_grade_value()`.
- Exclude `is_semester_proposition`, `is_final_proposition`, `is_semester`,
  `is_final` grades from a running average - they are (proposed) report
  card grades, not ordinary grades.
- Weighted average: weight = `grade_categories()[grade.category_id].weight`,
  skip categories with `count_to_average=False`. Librus's own average is
  weighted.
- A correction ("poprawa") is a new grade with `improves_id` pointing at the
  old one; the old grade is still in the list.
- Text grades (`text_grades()`), point grades (`point_grades()`) and
  descriptive grades (`descriptive_grades()`) are separate lists, not in
  `grades()`. A point grade has `points`, `max_points` and `percentage`;
  `point_grades_percentage()` averages a subject's point grades.
- A descriptive grade's `value` can be empty: Librus doesn't always send
  the grade shown to parents, only a scale range. Show its `skill` and
  `comments` then.

## Attendance

- `type_id` points at `attendance_types()`. An absence is a type with
  `is_presence_kind=False`; "late" (spóźnienie) is a presence kind.
- `AttendanceTypeData.is_excused_absence` is based on the type name
  ("uspr."); Librus has no separate flag.
- Most records are plain presences - count absences, not records.

## Messages (Wiadomości)

- Messages live in a separate Librus system; the library opens its session
  on its own. A school without the module gives empty results
  (`unread_messages()` is `{}`, `messages()` is `[]`, `message()` is `None`;
  `LibrusData.messages_available` is False).
- `messages()` previews are **shortened** by Librus. `message(id, mailbox)`
  gives the full text but **marks the message read** - never call it in a
  background loop.
- Mailboxes: `inbox`, `outbox` (sent), `archive/inbox` (past school years),
  plus `substitutions`, `alerts`, `justifications` at some schools. A
  mailbox the account doesn't have returns `[]`.
- Attachments: `download_attachment(attachment_id, message_id)` works
  without opening the message, but not for archived messages (Librus
  refuses).
- A reply's `SentMessageData.message_id` is `None` - Librus doesn't return
  one. A `LibrusMessageSendError` with `sent=None` means "maybe sent":
  check `messages("outbox")` before sending again.

## Announcements

- `was_read` turns true once the notice is opened anywhere (app, website),
  so lists of only unread notices are usually empty. `content` is the full
  text.
- `mark_announcement_read()` on a notice that's already read is fine.

## Other

- `lucky_number().day` can be the **next** school day - compare it with
  today before saying "today's number".
- Free days: `date_from` / `date_to` are inclusive. `free_days()` returns
  school-wide days and the student's class only.
- `library_lendings()`: for free textbooks `due_date` is not a real
  deadline (books stay until the end of the year).
- Behaviour grade: show `display` / `name` ("bdb", "bardzo dobre"); some
  schools use points instead.
- "Today" is today in Poland (Europe/Warsaw), whatever the machine's zone.
- A module Librus refuses for the account (403/404) comes back as an empty
  list and isn't asked for again for a day. `fetch_all()` /
  `fetch_changes()` list sections that failed in `failed_sections` - don't
  treat a failed section as "no data".
- There's no conditional polling (no ETag / Last-Modified); every poll
  downloads full lists, so poll rarely.
