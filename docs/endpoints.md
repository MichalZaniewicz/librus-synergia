# Data endpoints

```
GET https://synergia.librus.pl/gateway/api/2.0/<Endpoint>
```

These requests are authenticated **only by the session cookies** from
[the login flow](authentication.md). Send no `Authorization` header.
Responses are JSON with one root key named after the resource, often
alongside `Resources`/`Url` metadata that can be ignored.

Two things apply to every endpoint:

- **Id types are inconsistent.** Most `Id`s are ints, but Timetable lesson
  ids are strings (`"41999"`), SchoolNotices ids are strings
  (`"LID-NBOARD-NOTICE-..."`) and some Attendance ids are prefixed strings
  (`"t41685"`). Normalize before you compare them.
- **References are objects, not ids.** A grade's subject arrives as
  `{"Id": 42, "Url": ".../Subjects/42"}`. Fetch the lookup endpoints
  (Subjects, Users, Classrooms, categories) once a day and join on your
  side.

## Identity and school

| Endpoint | Root key | Notes |
|---|---|---|
| `Me` | `Me` | ✅ `Me.Account` is **the login's owner** (the parent, on a parent login); `Me.User` is **the student**. Use `User` for the child's name. |
| `Schools` | `School` | ✅ Name, town, street, `BuildingNumber`, `PostCode`, `Email`, `PhoneNumber`, `NameHeadTeacher`/`SurnameHeadTeacher`. |
| `Classes` | `Class` | ✅ `Number`+`Symbol` (e.g. 7+"d"), `ClassTutor.Id`, `BeginSchoolYear`, `EndFirstSemester`, `EndSchoolYear`. |
| `Units` | — | ✅ School configuration: `GradesSettings.{Standard,Point,Descriptive}GradesEnabled`, bell schedule (`LessonsRange`), behaviour-points settings. |
| `VirtualClasses` | `VirtualClasses` | ✅ Reachable, empty on tested accounts. |
| `Users/{Me.Account.UserId}` | `User` | ✅ The **student's** own user record (live 2026-10-07): `Id`, `AccountId` (a `LID-AUTH-USER-...`), `AccountNumericIdentifier`, `FirstName`, `LastName`, `Class.Id`/`UUID`, `Unit.Id`, **`ClassRegisterNumber`** (the class register number), `IsEmployee`, `GroupId`. `Users/{Me.Account.Id}` is 404 - `Account.Id` is the parent's login account. |
| `Auth/TokenInfo`, `Auth/UserInfo/<lid>` | — | Identity of the token's user; used only to find the child on [kindergarten accounts](kindergarten.md). |
| `UserProfile` | `UserProfile` | ✅ `ClassNumber` (e.g. 7), `AccountType` ("parent"), `Town`, `State`, `UnitType` ("Szkoła podstawowa"). |
| `Root` | `Resources` | ✅ An index of every module, with its URL. On a tested parent account it listed (among others) `Realizations`, `SchoolTrips`, `SchoolFiles`, `TimetableEntries`, `BaseTextGrades`, `Calendars`, `Colors`, `Surveys`, `SpecialAchievement`, `EbiblioLendings`, `StudentInsurances`, `PushChanges`, `PushDevices`, `SilentNight`, `NotificationCenterDeferrals` - being listed doesn't mean readable (see the 403/404s below). `Me` also carries `Refresh: 900` and lists `Me/PeriodicGradeAverages`, `Me/BehaviourDescriptiveGrades`. |
| *web page* `synergia.librus.pl/informacja` | — | ✅ Not part of the API: an HTML page with the student's details, including a `<th>Nr w dzienniku</th><td>25</td>` row. Only a **fallback** for the class register number - the JSON `Users/{Me.Account.UserId}.ClassRegisterNumber` above is the source. Opens with the same cookie session as the API (confirmed live 2026-10-07; a dead session redirects to the login page). Parsed by `parse_student_number`. |

## Lookups (cache ~24 h)

| Endpoint | Root key | Notes |
|---|---|---|
| `Subjects` | `Subjects` | ✅ `Id`, `Name`, `Short`. |
| `Users` | `Users` | ✅ The teachers lookup. ✅ `FirstName` can be **`null`** (present but null, e.g. for secretariat accounts), so `dict.get("FirstName", "")` is not enough. |
| `Classrooms` | `Classrooms` | ✅ `Id`, `Name`, `Symbol`. |
| `Lessons` | `Lessons` | ✅ `lesson id → {Subject, Teacher, Class}`. The only way to map `Attendances[].Lesson.Id` to a subject (Attendances carry no subject). |
| `Grades/Categories` | `Categories` | ✅ `Name`, `Weight`, `CountToTheAverage`. |
| `Grades/Types` | — | ✅ The complete list of values `Grade` can take (see below). |
| `Attendances/Types` | `Types` | ✅ Note: **not** top-level `AttendanceTypes`, which returns 404. |
| `HomeWorks/Categories` | `Categories` | ✅ Agenda categories: Sprawdzian, Kartkówka, Wycieczka, Zebranie z rodzicami, ... (per school). |
| `Notes/Categories` | `Categories` | ✅ Uses **`CategoryName`**, not `Name`. |
| `BehaviourGrades/Points/Categories` | `Categories` | ✅ |

## Grades

| Endpoint | Root key | Notes |
|---|---|---|
| `Grades` | `Grades` | ✅ `Grade` (string; `parse_grade_value` reads only the 1-6 scale and ignores anything outside it, e.g. a stray "85"), `Subject.Id`, `Category.Id`, `Semester`, `AddDate`, `IsSemesterProposition`, `IsFinalProposition`, `IsSemester`, `IsFinal`, `Comments` (❓ the four semester/final flags have only ever been `false`: no proposed or final grade issued yet). ✅ `AddedBy.Id` is the teacher who added the grade (a `Users` id): on a real account all 15 grades resolved to the subject's own teacher. 📖 `Improvement.Id` on a correction ("poprawa") points at the earlier grade it improves; the earlier grade stays in the list. |
| `Grades/Comments` | `Comments` | ✅ `[{"Id", "Text"}]`. `Grades[].Comments` is a list of **ids into this endpoint**, not embedded text. ✅ Real teacher comments on a real account resolve this way. ❓ Whether each list item is a bare id or an `{"Id": ...}` object was not captured, so accept both. |
| `DescriptiveGrades` | `Grades` | ✅ Grades in skills-based subjects (seen live 2026-10-09: music in grade 1 of a primary school). **The shown grade is `Map`** (`"6"`, same as `RealGradeValue`) - `Grade` holds something else (`3` for that `"6"`), so don't read it as the grade. Also `Subject.Id`, `Lesson.Id`, `Student.Id`, `Skill.Id` (into `DescriptiveGrades/Skills`), `AddedBy.Id` (the teacher), `Date`, `AddDate`, `Semester`, `Comments` (a list of `{"Id"}` into `DescriptiveGrades/Comments`). They don't count towards the average. Only when `Units` enables them. |
| `DescriptiveGrades/Skills` | `Skills` | ✅ Every skill of the whole school (~1100 entries, ~330 KB): `Id`, `Name` ("Ekspresja muzyczna. Śpiew" - Synergia shows it as the grade's "Kategoria"), `Subject.Id`, `Weight`, `CountToTheAverage` (false), `Color.Id`, sometimes `Teacher.Id`. Fetch it rarely. |
| `DescriptiveGrades/Comments`, `/Phrases`, `/Text`, `/SubjectCompetences`, `/Types` | - | 📖 Listed in `DescriptiveGrades`' `Resources`; shapes not seen yet. |
| `PointGrades` | `Grades` | ✅ Reachable, empty on tested accounts (their school has `PointGradesEnabled: false`). 📖 `Grade` (the text shown), `GradeValue` (the points), `Category.Id`, `Subject.Id`, `Semester`, `AddDate`, `AddedBy.Id`. The maximum lives on the category. Parsed by `parse_point_grades`; `point_grades_percentage` gives the weighted earned/possible percentage. |
| `PointGrades/Categories` | `Categories` | 📖 `Name`, `Weight`, `CountToTheAverage`, `ValueFrom`, `ValueTo` (the maximum points). |
| `TextGrades` | — | ✅ Reachable, empty on tested accounts. |
| `BaseTextGrades` | `Grades` | ✅ **Text grades** that `Grades` doesn't contain (one real entry, 2026-09-18): `Grade` (free text; ✅ can contain the teacher's line breaks and indentation, e.g. `"...sesja I\n      80%"` - `parse_text_grades` collapses the whitespace), `Subject.Id`, `Lesson.Id`, `Category.Id` (into `TextGrades/Categories`), `AddedBy.Id`, `Student.Id`, `Date`, `AddDate`, `Semester`, `ShowInGradesView`. szkolny-android reads its "descriptive grades" from here. |
| `TextGrades/Categories` | `Categories` | ✅ `Name`, `Short`, `Color.Id` (into `Colors`), `Weight`, `CountToTheAverage`, `Standard`, `IsReadOnly`, `BlockAnyGrades`, `ObligationToPerform` (71 on a tested school). |
| `Colors` | `Colors` | ✅ `Id`, `RGB` ("F0E68C"), `Name` ("khaki") - the colours category `Color.Id`s point at. |
| `DescriptiveTextGrades`, `DescriptiveTextGrades/Skills`, `Grades/Scales` | `Grades` / `Skills` / `Scales` | ✅ Reachable, empty on the tested account. |
| `Grades/CategoriesAverages` | — | ✅ HTTP 403 for a parent account. |
| `Grades/Averages`, `PointGrades/Averages` | — | ✅ Reachable, but on the tested school both answer only `{"Status": "Disabled"}` - the school has its averages switched off. ❓ The shape when enabled is unknown. |
| `BehaviourGrades` | `Grades` | ✅ Reachable, empty on the tested account (its behaviour grade is in `BehaviourGrades/Points`). |
| `BehaviourGrades/Types` | `Types` | ✅ `Id` (string "1".."6"), `Name` ("wzorowe".."naganne"), `Shortcut` ("wz".."ng") - the names behind `BehaviourGrade.Id`. |
| `BehaviourGrades/Points/Comments` | `Comments` | ✅ `[{"Id", "Text"}]`, same shape as `Grades/Comments`; the live monthly grade's comment ("Ocena zachowania miesiąc za IX/26") resolved this way. A behaviour grade's `Comments` ids point here. |
| `BehaviourGrades/SystemProposal` | — | ✅ HTTP 403 for a parent account on the tested school (a proposed behaviour grade, per third-party OpenAPI notes). |
| `BehaviourGrades/Points` | `Grades` | The formal behaviour grade ("ocena zachowania"). Points schools use `Value`/`ShortName`. ✅ A classic-scale grade comes with `ShortName`/`Text` empty and no `Value` (seen live 2026-10-05 on a monthly grade, "Ocena zachowania miesiąc za IX/26" in `Comments`); ✅ the grade itself is `BehaviourGrade.Id` (1 wz, 2 bdb, 3 db, 4 popr, 5 ndp, 6 ng; names in `BehaviourGrades/Types`) - the live monthly grade was a "bdb" read this way. Also `Category`, `Semester`, `AddDate`, `Comments` (ids into `BehaviourGrades/Points/Comments`). |

### Grade values

✅ `Grades/Types` lists every possible value: the numbers `1`–`6`, each
optionally with `+`/`-`, plus non-numeric marks that must be **excluded
from averages**: `bz` (brak zadania), `np` (nieprzygotowany), `nk`
(nieklasyfikowany), `uł`/`nł`, `zl`/`nz`, `zw` (zwolniony), `uc`/`nu`, and
a bare `+`/`-`.

✅ The API gives **no numeric value** for a modified grade, only the
string `"4+"`. ✅ The `+` counts as **+0.5** and the `-` as **−0.25**:
a real `4+` showed as 4.5, and a subject with a real `6` and `4-` showed
an average of 4.88 ((6 + 3.75) / 2) in Librus's own app. See
`parse_grade_value`.

✅ Values seen on a real account so far: `2`, `4`, `5`, `6`, `4+`, `5+`,
`4-`, `6-`, and a bare `+` filed under an "aktywność" (class activity)
category. That bare `+` is a mark, not a grade: a subject whose only entry
is a `+` has **no average**.

## Behaviour notes (uwagi)

| Endpoint | Root key | Notes |
|---|---|---|
| `Notes` | `Notes` | 📖 `Text`, `Category.Id`, `Teacher.Id`, `Date`, `Positive`: **`0` = negative, `1` = positive, `2` = neutral**. |

## Attendance

| Endpoint | Root key | Notes |
|---|---|---|
| `Attendances` | `Attendances` | ✅ `Lesson.Id`, `LessonNo`, `Date`, `Semester`, `Type.Id`. ✅ Most records are ordinary *presence* marks, so do not count records as absences. ✅ `Id` can be a string like `"t41685"`. ✅ On a real account, all 151 records' `Lesson.Id`s resolved against `Lessons`. |

✅ `Attendances/Types` on a real account:

| Id | Name | `IsPresenceKind` |
|---|---|---|
| 1 | Nieobecność | false |
| 2 | Spóźnienie | **true** (late, but present) |
| 3 | Nieobecność uspr. | false |
| 4 | Zwolnienie | true |
| 100 | Obecność | true |
| 1685 | Pobyt w sanatorium | true |

✅ Schools add **their own types** on top of the standard ones (the
sanatorium stay above has a school-specific id). Don't hard-code ids;
always read `IsPresenceKind` from this endpoint.

There is no "excused" flag. Excused absences can only be recognized by
`uspr.` in the type name.

| Endpoint | Root key | Notes |
|---|---|---|
| `Justifications` | `data` | ✅ The absence justifications the parent submitted, with a lowercase JSON envelope unlike the rest of the API: `{"status": "OK", "message": ..., "data": [...]}`. Each item: `id` (int), `messageFromParent`, `postDate` ("YYYY-MM-DD HH:MM:SS"), `justificationStatus` (✅ `"accept"` seen; other values not seen yet), `dateFrom`, `dateTo`, `lessons` (`[{"number", "date"}]`, can be empty), `justifiedAbsences` (int), `attachment` (bool), `notifiedTeachers` (`[{"name"}]`). Parsed by `parse_justifications` (`JustificationData`, newest first); `justified_dates` lists the days covered by a justification that wasn't rejected. |

✅ Attendance can be **missing for whole subjects**. On one real account a
subject taught twice a week had only 2 records after a month, both
absences, while every other subject had a record for each lesson. Some
teachers apparently don't take attendance in Librus. A per-subject
percentage computed from a handful of records says nothing useful.

## Timetable

```
GET Timetables?weekStart=YYYY-MM-DD      (a Monday)
```

✅ The shape is **date → list of period slots → list of lessons**. It is
*not* a flat list per day:

```json
{"Timetable": {
  "2026-09-01": [
    [],
    [{"LessonNo": "2", "HourFrom": "08:55", "HourTo": "09:40",
      "Subject": {"Id": "41999"}, "Teacher": {"Id": "1001"}, "Classroom": {"Id": "12"},
      "IsCanceled": false, "IsSubstitutionClass": false}],
    [{...group A...}, {...group B...}]
  ]
}}
```

- Every day has the same number of slots. Empty slots are `[]`.
- A slot can hold **several lessons** when a class is split into groups
  (for example two language groups at the same time).
- Ids inside lessons are **strings**.
- ✅ **HTTP 403** here can mean *the school hasn't published the timetable
  yet*, which is not a session problem (see [errors](errors.md)).
- ✅ `Substitutions` returns 403 for parent/student accounts. Substitutions
  only show up as `IsSubstitutionClass` on lessons.
- ✅ A substitution lesson often has **no classroom**, even when the regular
  lesson in that slot has one.
- ✅ A substitution lesson also carries **the original lesson**: `OrgDate`,
  `OrgLessonNo`, `OrgHourFrom`, `OrgHourTo`, `OrgSubject`, `OrgTeacher`,
  `OrgClassroom` (refs with `Id`), plus `SubstitutionNote` (null so far) and
  `SubstitutionClassUrl`. Comparing `Classroom` with `OrgClassroom` shows a
  **room change**; `OrgSubject`/`OrgTeacher` say what and who was replaced.
  Parsed into `LessonData.original` / `LessonData.room_changed`.
- ✅ `TimetableEntries` (root `TimetableEntries`) is the **standing weekly plan**, not dated weeks: `Id`, `Lesson.Id`, `DayOfTheWeek` (1 = Monday), `LessonNo`, `DateFrom`/`DateTo`, `Classroom` with `Id`/`Symbol`/`Name` inline (60 entries on a tested class). ✅ Each entry is valid only between its `DateFrom` and `DateTo`: when the plan changes during the year, the old and the new version of a slot are both listed (on the tested class, ranges such as 1-2 Sept, 3-6 Sept, from 7 Sept, and from late June). ✅ Every `Lesson.Id` was one of the student's own `Lessons` (no other groups' lessons), and once filtered by date the plan matched the real `Timetables` weeks slot for slot, room included; the only differences were cancelled lessons. `parse_timetable_entries` / `plan_differences` (live check 2026-10-08).
- ✅ `Timetables/OtherActivitiesRegister?dateFrom=&dateTo=&hideOutdatedEntries=false` answers `{"data": [...]}` (extracurricular activities); empty on the tested account.
- ✅ **`Realizations`** = the **lessons held, with their topics** (168 entries a month into the year): `Id` (a `t`-prefixed string), `Lesson.Id`, `LessonNo`, `Date`, **`Topic`**, `IsTrip`, `CountInStatistics`, `CountInRPN`, `AddedBy.Id`. The JSON counterpart of the `zrealizowane_lekcje` web page. Also `Realizations/TypesOfDays` (`Dzień powszedni`, `Święto`), `/TypesOfClasses`, `/ThematicTeaching`, `/FilledByTeacher`.
- ✅ `PlannedLessons` is reachable and empty on the tested account.
- ✅ Other lesson fields seen: `Lesson`, `Class`, `DateFrom`, `DateTo`,
  `DayNo`, `TimetableEntry`, `VirtualClass`/`VirtualClassName` (on lessons
  for a virtual class / group).

## Agenda, homework, free days

| Endpoint | Root key | Notes |
|---|---|---|
| `HomeWorks` | `HomeWorks` | ✅ **The agenda (terminarz)**, despite the name: tests, trips, parent meetings. `Category.Id`, `Subject.Id`, `Date`, `TimeFrom`, `Content`. Some teachers file a quiz under the "Inne" category and say "kartkówka" only in `Content`, so match both fields. ✅ School-wide entries (parent meetings, assemblies) have no subject. |
| `HomeWorkAssignments` | `HomeWorkAssignments` | ✅ Real homework: `Topic`, `Text`, `Teacher.Id`, `Date`, `DueDate`, `Lesson.Id`, `Category.Id` (only sometimes), `MustSendAttachFile`, `SendFilePossible`, `AddedFiles`, `HomeworkAssigmentFiles` (sic, a list - empty on all 7 real assignments), `StudentsWhoRead`/`StudentsWhoMarkedAsDone`. The response lists a `HomeWorkAssignments/Attachment` resource. ✅ Each `HomeworkAssigmentFiles` item is `{"Id", "Name", "Url"}`; `Url` points at `HomeWorkAssignments/Attachment/<homework id>-<file id>` (✅ through the gateway it answers `{"Id", "Name", "DownloadUrl"}`, with the file id alone it's a 400). Parsed by `parse_homework_attachments`. ✅ **Download** (live 2026-10-09): `https://synergia.librus.pl/homework/downloadFile/<file id>` redirects (302) to `sandbox.librus.pl/index.php?action=CSTryToDownload&singleUseKey=<key>`, an HTML page whose script POSTs `action=CSCheckKey` with `singleUseKey` in the form body every 5-10 s (`not_downloaded_yet`, then `ready`, or `download_failed`), then GETs `action=CSDownload&singleUseKey=<key>`, which returns the file. `download_homework_attachment` does the same (a real JPEG came back in about 4 s). ✅ The first download of a file can stay `not_downloaded_yet` for more than 30 s (a retry minutes later was quick), so the library waits up to about two minutes, as Librus's own page does (it offers to give up only after 15 checks). ✅ Now and then a key gets `download_failed` (or an answer that isn't JSON) at once, while a fresh key for the same file a moment later works - seen when the same file was being fetched from another session at about the same time - so the library retries with up to three keys. **No `Subject` field.** ✅ The subject can be recovered from the teacher's lessons in `Timetables` when that teacher teaches only one subject (all 7 real assignments on a tested account resolved this way). |
| `SchoolFreeDays` | `SchoolFreeDays` | ✅ `Name`, `DateFrom`, `DateTo`. |
| `Calendars` | `Calendars` | ✅ The class calendars (`[{"Id": "<class id>"}]`). |
| `Calendars/{classId}?year=&month=` | `Calendar` | ✅ One month's **ids** of `HomeWorks`, `Substitutions`, `ParentTeacherConferences`, `SchoolFreeDays`, `ClassFreeDays`, `TeacherFreeDays` (refs only, no content), with `Pages.Prev`/`Next`. A cheap way to narrow the agenda by month. |
| `Calendars/ClassFreeDays/Types` | `Types` | ✅ `Name` of class free-day types (e.g. "Wycieczka", "Próbny egzamin ósmoklasisty."). |
| `Calendars/TeacherFreeDays` | — | ✅ HTTP 403 for a parent account (like `TeacherFreeDays`). |
| `HomeWorkAssignments/Categories` | `Categories` | ✅ `CategoryName` and `Teacher.Id` - each teacher's own homework categories (96 on a tested school). |
| `SchoolTrips` | `Data` | ✅ **School trips** of the class (camelCase): `id`, `destination`, `route`, `locomotion`, `termFrom`, `termTo`, `creatorName`/`creatorLastName`, `coordinatorName`. |
| `SchoolFiles` | `Data` | ✅ **Documents the school shares** with parents: `id`, `displayName`, `addedOnDate`, `downloadUrl` (a Synergia web path, `/pliki_szkoly/pobierz/<id>`), `iconUrl`, `fileStatus`. |
| `Surveys` | — | ✅ HTTP 404 on the tested account (lists `Surveys/Details`). |
| `ClassFreeDays` | `ClassFreeDays` | ✅ Same shape. Empty on tested accounts. |
| `ParentTeacherConferences` | `ParentTeacherConferences` | ✅ `Id`, `Topic`, `Teacher.Id` (the class tutor on the tested account), `Date`, `Time` (`"17:00:00"`). ✅ **The same meeting also appears in `HomeWorks`**, under a "Zebranie z rodzicami" category, with the same date and `TimeFrom` but different wording. Merging both sources gives duplicates, so match on date and time. |

✅ `NotificationCenter` (listed in third-party OpenAPI notes) answers with
no JSON at all on Gateway 2.0 - it most likely lives on the newer
`api.librus.pl/3.0` API, which this library doesn't use.

## Announcements and lucky number

| Endpoint | Root key | Notes |
|---|---|---|
| `SchoolNotices` | `SchoolNotices` | ✅ `Subject`, `Content` (full text, not truncated), `StartDate`, `EndDate`, `CreationDate`, `WasRead`. ✅ `Id` is a **string**. |
| `LuckyNumbers` | `LuckyNumber` | ✅ `{"LuckyNumber": {"LuckyNumber": 13, "LuckyNumberDay": "2026-09-08"}}`. ✅ The number for the **next** school day can appear a day early, so check `LuckyNumberDay`. |

The student's own class-register number is in the JSON API: ✅
`Users/{Me.Account.UserId}.ClassRegisterNumber` (see
[Identity and school](#identity-and-school)). `Librus.student_number()` reads
it from there (the `informacja` web page is only a fallback), so "is it my
number?" needs no input from the user.
