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
| `Grades` | `Grades` | ✅ `Grade` (string), `Subject.Id`, `Category.Id`, `Semester`, `AddDate`, `IsSemesterProposition`, `IsFinalProposition`, `IsSemester`, `IsFinal`, `Comments`. |
| `Grades/Comments` | `Comments` | 📖 `[{"Id", "Text"}]`. `Grades[].Comments` is a list of **ids into this endpoint**, not embedded text. |
| `DescriptiveGrades` | `Grades` | 📖 `Subject`, `Skill`, `Category`, `Grade`, `AddDate`. Only when `Units` enables them. |
| `PointGrades`, `TextGrades` | — | ✅ Reachable, empty on tested accounts. |
| `BehaviourGrades/Points` | `Grades` | 📖 The formal behaviour grade ("ocena zachowania"): `Value`, `ShortName`, `Category`, `Semester`, `Comments` (ids into `BehaviourGrades/Points/Comments`). |

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

## Behaviour notes (uwagi)

| Endpoint | Root key | Notes |
|---|---|---|
| `Notes` | `Notes` | 📖 `Text`, `Category.Id`, `Teacher.Id`, `Date`, `Positive`: **`0` = negative, `1` = positive, `2` = neutral**. |

## Attendance

| Endpoint | Root key | Notes |
|---|---|---|
| `Attendances` | `Attendances` | ✅ `Lesson.Id`, `LessonNo`, `Date`, `Semester`, `Type.Id`. ✅ Most records are ordinary *presence* marks, so do not count records as absences. ✅ `Id` can be a string like `"t41685"`. |

✅ `Attendances/Types` on a real account:

| Id | Name | `IsPresenceKind` |
|---|---|---|
| 1 | Nieobecność | false |
| 2 | Spóźnienie | **true** (late, but present) |
| 3 | Nieobecność uspr. | false |
| 4 | Zwolnienie | true |
| 100 | Obecność | true |

There is no "excused" flag. Excused absences can only be recognized by
`uspr.` in the type name.

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

## Agenda, homework, free days

| Endpoint | Root key | Notes |
|---|---|---|
| `HomeWorks` | `HomeWorks` | ✅ **The agenda (terminarz)**, despite the name: tests, trips, parent meetings. `Category.Id`, `Subject.Id`, `Date`, `TimeFrom`, `Content`. Some teachers file a quiz under the "Inne" category and say "kartkówka" only in `Content`, so match both fields. |
| `HomeWorkAssignments` | `HomeWorkAssignments` | ✅ Real homework: `Topic`, `Text`, `Teacher.Id`, `Date`, `DueDate`. **No `Subject` field.** |
| `SchoolFreeDays` | `SchoolFreeDays` | ✅ `Name`, `DateFrom`, `DateTo`. |
| `ClassFreeDays` | `ClassFreeDays` | ✅ Same shape. Empty on tested accounts. |
| `ParentTeacherConferences` | `ParentTeacherConferences` | 📖 `Topic`, `Teacher.Id`, `Date`, `Time`. ✅ Real parent meetings on tested accounts came through `HomeWorks` instead. |

## Announcements and lucky number

| Endpoint | Root key | Notes |
|---|---|---|
| `SchoolNotices` | `SchoolNotices` | ✅ `Subject`, `Content` (full text, not truncated), `StartDate`, `EndDate`, `CreationDate`, `WasRead`. ✅ `Id` is a **string**. |
| `LuckyNumbers` | `LuckyNumber` | ✅ `{"LuckyNumber": {"LuckyNumber": 13, "LuckyNumberDay": "2026-09-08"}}`. ✅ The number for the **next** school day can appear a day early, so check `LuckyNumberDay`. |

The student's own class-register number is **not exposed anywhere** in
the API. To answer "is it my number?", ask the user for it.
