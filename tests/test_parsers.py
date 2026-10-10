"""Parser tests. Every payload shape here mirrors a response confirmed
against a real Librus account (see docs/)."""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import pytest

from librus_synergia import parsers
from librus_synergia.models import FreeDayData, LessonData
from librus_synergia.parsers import parse_grade_value


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", 5.0),
        ("4+", 4.5),
        ("3-", 2.75),
        ("1", 1.0),
        ("bz", None),
        ("np", None),
        ("+", None),
        ("-", None),
        ("", None),
        ("6+", 6.5),
        ("0", None),
        ("85", None),
        ("17,5", None),
        ("0+", None),
        ("0-", None),
    ],
)
def test_parse_grade_value(raw: str, expected: float | None) -> None:
    assert parse_grade_value(raw) == expected


def test_timetable_is_list_of_period_slots_with_string_ids() -> None:
    payload = {
        "Timetable": {
            "2026-09-01": [
                [],
                [
                    {
                        "LessonNo": "2",
                        "HourFrom": "08:55",
                        "HourTo": "09:40",
                        "Subject": {"Id": "41999"},
                        "Teacher": {"Id": "1001"},
                        "Classroom": {"Id": "12"},
                    }
                ],
                [
                    {"LessonNo": "3", "Subject": {"Id": "1"}, "IsCanceled": True},
                    {"LessonNo": "3", "Subject": {"Id": "2"}, "IsSubstitutionClass": True},
                ],
            ],
            "2026-09-06": [[], [], []],
        }
    }
    timetable = parsers.merge_timetables(payload)
    day = timetable[date(2026, 9, 1)]
    assert [lesson.subject_id for lesson in day] == [41999, 1, 2]
    assert day[0].lesson_no == 2
    assert day[0].teacher_id == 1001
    assert day[1].is_canceled and day[2].is_substitution
    assert timetable[date(2026, 9, 6)] == []


def test_kindergarten_timetable_entries() -> None:
    payload = {
        "timetableEntries": [
            {
                "date": "2026-09-01",
                "startTime": "08:00",
                "endTime": "08:30",
                "activityTypeIdentifier": "LID-A",
                "classroomIdentifier": "LID-R",
                "teachers": ["LID-T1", "LID-T2"],
                "type": "planned",
            }
        ]
    }
    (lesson,) = parsers.merge_timetables(payload)[date(2026, 9, 1)]
    assert lesson.lesson_no is None
    assert lesson.subject_id == "LID-A"
    assert lesson.teacher_ids == ("LID-T1", "LID-T2")


def _kg_entry(ident: str, start: str, end: str, teacher: str, kind: str, **extra: Any) -> dict:
    return {
        "identifier": ident,
        "date": "2026-10-12",
        "startTime": start,
        "endTime": end,
        "activityTypeIdentifier": "LID-A",
        "classroomIdentifier": "LID-R",
        "teachers": [teacher],
        "type": kind,
        **extra,
    }


def test_kindergarten_substitution_replaces_the_substituted_block() -> None:
    # Shape seen live 2026-10-10: the replaced block comes as "substituted",
    # split into two "substitution" entries by other teachers.
    original = _kg_entry("LID-O", "10:00", "13:00", "LID-T1", "substituted")
    payload = {
        "timetableEntries": [
            {**original, "substitutions": [{"identifier": "LID-S1"}, {"identifier": "LID-S2"}]},
            _kg_entry(
                "LID-S1",
                "10:00",
                "11:00",
                "LID-T2",
                "substitution",
                substitutedLesson=original,
                substitutionType="LID-X",
                comments="Choroba",
            ),
            _kg_entry(
                "LID-S2", "11:00", "13:00", "LID-T3", "substitution", substitutedLesson=original
            ),
            _kg_entry("LID-C", "12:20", "12:50", "LID-T4", "cancelled", comments="Wycieczka"),
        ]
    }
    first, second, cancelled = parsers.merge_timetables(payload)[date(2026, 10, 12)]
    assert (first.hour_from, first.teacher_id, first.is_substitution) == ("10:00", "LID-T2", True)
    assert first.original is not None
    assert first.original.teacher_id == "LID-T1"
    assert (first.original.hour_from, first.original.hour_to) == ("10:00", "13:00")
    assert first.original.date == "2026-10-12" and first.original.lesson_no is None
    assert first.substitution_note == "Choroba" and not first.is_canceled
    assert second.teacher_id == "LID-T3" and second.substitution_note is None
    assert cancelled.is_canceled and not cancelled.is_substitution


def test_kindergarten_substituted_block_without_replacement_is_cancelled() -> None:
    payload = {"timetableEntries": [_kg_entry("LID-O", "07:00", "09:00", "LID-T1", "substituted")]}
    (lesson,) = parsers.merge_timetables(payload)[date(2026, 10, 12)]
    assert lesson.is_canceled and not lesson.is_substitution


def test_attendance_ids_may_be_prefixed_strings() -> None:
    payload = {
        "Attendances": [
            {"Id": 5, "Lesson": {"Id": 7}, "Type": {"Id": 1}, "Date": "2026-09-02"},
            {"Id": "t41685", "Lesson": {"Id": 8}, "Type": {"Id": 100}},
        ]
    }
    first, second = parsers.parse_attendances(payload)
    assert first.id == 5 and first.type_id == 1
    assert second.id == "t41685"


def test_attendance_types_presence_and_excused() -> None:
    payload = {
        "Types": [
            {"Id": 1, "Name": "Nieobecność", "IsPresenceKind": False},
            {"Id": 2, "Name": "Spóźnienie", "IsPresenceKind": True},
            {"Id": 3, "Name": "Nieobecność uspr.", "IsPresenceKind": False},
        ]
    }
    types = parsers.parse_attendance_types(payload)
    assert not types[1].is_presence_kind and not types[1].is_excused_absence
    assert types[2].is_presence_kind
    assert types[3].is_excused_absence


def test_id_name_map_handles_null_first_name_and_category_name() -> None:
    teachers = parsers.parse_id_name_map(
        {"Users": [{"Id": 1, "FirstName": None, "LastName": "Sekretariat"}]}, ("Users",)
    )
    assert teachers == {1: "Sekretariat"}
    categories = parsers.parse_id_name_map(
        {"Categories": [{"Id": 4, "CategoryName": "Kultura osobista"}]}, ("Categories",)
    )
    assert categories == {4: "Kultura osobista"}


@pytest.mark.parametrize(
    ("positive", "sentiment"), [(0, "negative"), (1, "positive"), (2, "neutral")]
)
def test_note_sentiment(positive: int, sentiment: str) -> None:
    (note,) = parsers.parse_notes({"Notes": [{"Id": 1, "Text": "x", "Positive": positive}]})
    assert note.sentiment == sentiment


def test_grade_comments_resolved_by_id() -> None:
    grades = parsers.parse_grades(
        {"Grades": [{"Id": 1, "Grade": "5", "Subject": {"Id": 9}, "Comments": [{"Id": 77}]}]},
        parsers.parse_comment_text_map({"Comments": [{"Id": 77, "Text": "Brawo"}]}),
    )
    assert grades[0].comments == ["Brawo"]


def test_grade_teacher_from_added_by() -> None:
    added, missing = parsers.parse_grades(
        {
            "Grades": [
                {"Id": 1, "Grade": "5", "AddedBy": {"Id": 1603930, "Url": ".../Users/1603930"}},
                {"Id": 2, "Grade": "4"},
            ]
        }
    )
    assert added.teacher_id == 1603930
    assert missing.teacher_id is None


def test_grade_improvement_points_at_earlier_grade() -> None:
    old, new, plain = parsers.parse_grades(
        {
            "Grades": [
                {"Id": 10, "Grade": "1"},
                {"Id": 11, "Grade": "4", "Improvement": {"Id": 10, "Url": ".../Grades/10"}},
                {"Id": 12, "Grade": "5", "Improvement": None},
            ]
        }
    )
    assert new.improves_id == 10
    assert old.improves_id is None
    assert plain.improves_id is None


def test_school_notice_id_is_string() -> None:
    (notice,) = parsers.parse_school_notices(
        {"SchoolNotices": [{"Id": "LID-NBOARD-NOTICE-9093", "Subject": "Apel", "Content": "..."}]}
    )
    assert notice.id == "LID-NBOARD-NOTICE-9093"


def test_message_content_truncated_mid_character_keeps_prefix() -> None:
    raw = "rozwijając".encode()
    truncated = base64.b64encode(raw[:-1]).decode("ascii")  # cut inside "ą"
    assert parsers.decode_message_content(truncated).startswith("rozwija")


def test_message_content_cdata_and_liblink_flattened() -> None:
    body = (
        '<Message><Content><![CDATA[Dzień dobry,<br>zobacz <a href="https://liblink.pl/x" '
        'title="Link został skonwertowany">strona</a></p>]]></Content></Message>'
    )
    text = parsers.decode_message_content(_b64(body))
    assert "<" not in text
    assert text.startswith("Dzień dobry,\nzobacz")
    assert "strona" in text


def test_parse_message_with_attachments() -> None:
    message = parsers.parse_message(
        {
            "data": {
                "senderFirstName": "Anna",
                "senderLastName": "Nowak",
                "topic": "Zebranie",
                "Message": _b64("<![CDATA[Treść]]>"),
                "sendDate": "2026-09-01 10:00:00",
                "readDate": None,
                "attachments": [{"id": 5, "filename": "plan.pdf"}],
            }
        },
        "inbox",
        "123",
    )
    assert message is not None
    assert message.sender_name == "Anna Nowak"
    assert message.content == "Treść"
    assert message.attachments[0].filename == "plan.pdf"


def test_unread_counts_per_mailbox() -> None:
    count, by_mailbox, messages = parsers.parse_messages(
        {"data": {"inbox": 3, "alerts": "1", "notes": None}},
        {
            "data": [
                {"messageId": "9", "senderName": "Szkoła", "topic": "t", "content": _b64("hej")}
            ]
        },
    )
    assert count == 3
    assert by_mailbox["alerts"] == 1 and by_mailbox["notes"] == 0
    assert messages[0].content == "hej"


def test_lesson_subjects() -> None:
    assert parsers.parse_lesson_subjects(
        {"Lessons": [{"Id": 10, "Subject": {"Id": 42}}, {"Id": 11}]}
    ) == {10: 42}


def test_empty_payloads_are_safe() -> None:
    assert parsers.parse_grades({}) == []
    assert parsers.merge_timetables({}) == {}
    assert parsers.parse_lucky_number({}) is None
    assert parsers.parse_school({}) is None


def test_behaviour_grade_classic_scale_and_points() -> None:
    """The classic scale lives in BehaviourGrade.Id with ShortName/Text
    empty - the shape seen live on a monthly "bdb" (2026-10-05)."""
    grades = parsers.parse_behaviour_grades(
        {
            "Grades": [
                {
                    "Id": 1,
                    "ShortName": "",
                    "Text": None,
                    "BehaviourGrade": {"Id": 2, "Url": "x"},
                    "Category": {"Id": 9},
                    "AddDate": "2026-10-05 08:20:25",
                    "Comments": [{"Id": 5}],
                },
                {"Id": 2, "Value": 5.0, "ShortName": "", "AddDate": "2026-10-01"},
                {"Id": 3, "Value": -2.0, "ShortName": "", "AddDate": "2026-10-02"},
            ]
        },
        {5: "Ocena zachowania miesiąc za IX/26."},
    )
    classic, plus, minus = grades
    assert classic.grade_id == 2
    assert classic.display == "bdb"
    assert classic.name == "bardzo dobre"
    assert classic.text == ""
    assert classic.comments == ["Ocena zachowania miesiąc za IX/26."]
    assert plus.grade_id is None
    assert plus.display == "+5"
    assert plus.name is None
    assert minus.display == "-2"


STUDENT_INFO_PAGE = """
<table class="decorated form">
  <thead><tr><td colspan="2">Uczeń</td></tr></thead>
  <tbody>
    <tr><th class="big">Imi&#281; i nazwisko ucznia</th><td>Jan Kowalski</td></tr>
    <tr><th class="big">Klasa</th><td>7 d</td></tr>
    <tr><th class="big">Nr w dzienniku</th><td>
        25
    </td></tr>
    <tr><th class="big">Wychowawca</th><td>Anna Nowak</td></tr>
  </tbody>
</table>
"""


def test_student_number_from_info_page() -> None:
    assert parsers.parse_student_number(STUDENT_INFO_PAGE) == 25


def test_student_number_missing_or_blank() -> None:
    assert parsers.parse_student_number("<html><body>Brak</body></html>") is None
    blank = STUDENT_INFO_PAGE.replace("25", "&nbsp;")
    assert parsers.parse_student_number(blank) is None


POINT_CATEGORIES = {
    "Categories": [
        {
            "Id": 1,
            "Name": "Sprawdzian",
            "Weight": 3,
            "CountToTheAverage": True,
            "ValueFrom": 0,
            "ValueTo": 20,
        },
        {
            "Id": 2,
            "Name": "Kartkówka",
            "Weight": 1,
            "CountToTheAverage": True,
            "ValueFrom": 0,
            "ValueTo": 10,
        },
        {
            "Id": 3,
            "Name": "Dodatkowe",
            "Weight": 1,
            "CountToTheAverage": False,
            "ValueFrom": 0,
            "ValueTo": 5,
        },
    ]
}
POINT_GRADES = {
    "Grades": [
        {
            "Id": 11,
            "Grade": "17",
            "GradeValue": 17,
            "Category": {"Id": 1},
            "Subject": {"Id": 100},
            "Semester": 1,
            "AddDate": "2026-10-01 10:00:00",
            "AddedBy": {"Id": 7},
        },
        {
            "Id": 12,
            "Grade": "5,5",
            "Category": {"Id": 2},
            "Subject": {"Id": 100},
            "Semester": 1,
            "AddDate": "2026-10-02 10:00:00",
        },
        {
            "Id": 13,
            "Grade": "5",
            "GradeValue": 5,
            "Category": {"Id": 3},
            "Subject": {"Id": 100},
            "Semester": 1,
            "AddDate": "2026-10-03 10:00:00",
        },
        {"Grade": "no id"},
    ]
}


def test_parse_point_grades_resolves_categories() -> None:
    categories = parsers.parse_point_grade_categories(POINT_CATEGORIES)
    grades = parsers.parse_point_grades(POINT_GRADES, categories)

    assert [g.id for g in grades] == [11, 12, 13]
    first, second, third = grades
    assert (first.points, first.max_points, first.weight) == (17.0, 20.0, 3.0)
    assert first.category == "Sprawdzian"
    assert first.teacher_id == 7
    assert first.percentage == 85.0
    # No GradeValue: the points come from the text, decimal comma included.
    assert second.points == 5.5
    assert third.counts_to_average is False


def test_point_grades_percentage_is_weighted() -> None:
    grades = parsers.parse_point_grades(
        POINT_GRADES, parsers.parse_point_grade_categories(POINT_CATEGORIES)
    )
    # (17*3 + 5.5*1) / (20*3 + 10*1); the "Dodatkowe" grade doesn't count.
    assert parsers.point_grades_percentage(grades) == round(100 * 56.5 / 70, 1)
    assert parsers.point_grades_percentage([]) is None


def test_point_grades_without_categories_have_no_maximum() -> None:
    grades = parsers.parse_point_grades(POINT_GRADES)
    assert grades[0].max_points is None
    assert grades[0].percentage is None
    assert parsers.point_grades_percentage(grades) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"Units": [{"GradesSettings": {"PointGradesEnabled": False}}]}, False),
        (
            {
                "Units": [
                    {"GradesSettings": {"PointGradesEnabled": False}},
                    {"GradesSettings": {"PointGradesEnabled": True}},
                ]
            },
            True,
        ),
        ({"Unit": {"GradesSettings": {"PointGradesEnabled": True}}}, True),
        ({"Units": []}, None),
        ({}, None),
    ],
)
def test_point_grades_enabled(payload: dict, expected: bool | None) -> None:
    assert parsers.point_grades_enabled(payload) is expected


def test_substituted_lesson_keeps_the_original() -> None:
    payload = {
        "Timetable": {
            "2026-09-29": [
                [
                    {
                        "LessonNo": "6",
                        "HourFrom": "12:45",
                        "HourTo": "13:30",
                        "Subject": {"Id": "42001"},
                        "Teacher": {"Id": "100"},
                        "Classroom": {"Id": "17300"},
                        "IsSubstitutionClass": True,
                        "IsCanceled": False,
                        "SubstitutionNote": None,
                        "OrgDate": "2026-09-29",
                        "OrgLessonNo": "6",
                        "OrgHourFrom": "12:45",
                        "OrgHourTo": "13:30",
                        "OrgSubject": {"Id": "42005"},
                        "OrgTeacher": {"Id": "200"},
                        "OrgClassroom": {"Id": "17292"},
                    },
                    {
                        "LessonNo": "6",
                        "HourFrom": "12:45",
                        "HourTo": "13:30",
                        "Subject": {"Id": "1"},
                    },
                ]
            ]
        }
    }
    lessons = parsers.merge_timetables(payload)[date(2026, 9, 29)]

    substituted, plain = lessons
    assert substituted.original is not None
    assert substituted.original.subject_id == 42005
    assert substituted.original.teacher_id == 200
    assert substituted.original.lesson_no == 6
    assert substituted.room_changed is True
    assert substituted.substitution_note is None
    assert plain.original is None
    assert plain.room_changed is False


def test_room_change_needs_both_rooms() -> None:
    lesson = parsers.parse_lesson({"IsSubstitutionClass": True, "OrgClassroom": {"Id": "5"}})
    assert lesson.original is not None
    assert lesson.room_changed is False


JUSTIFICATIONS = {
    "status": "OK",
    "message": "Pobrano listę usprawiedliwień",
    "data": [
        {
            "id": 3,
            "messageFromParent": "Proszę o usprawiedliwienie.",
            "postDate": "2026-09-06 22:17:53",
            "justificationStatus": "accept",
            "lessons": [{"number": 8, "date": "2026-09-04"}],
            "attachment": False,
            "dateFrom": "2026-09-04",
            "dateTo": "2026-09-04",
            "justifiedAbsences": 1,
            "notifiedTeachers": [{"name": "Anna Nowak"}],
        },
        {
            "id": 5,
            "messageFromParent": "Choroba.",
            "postDate": "2026-09-15 09:23:45",
            "justificationStatus": "new",
            "lessons": [],
            "attachment": True,
            "dateFrom": "2026-09-14",
            "dateTo": "2026-09-16",
            "justifiedAbsences": 0,
            "notifiedTeachers": [],
        },
        {
            "id": 7,
            "messageFromParent": "x",
            "postDate": "2026-09-20 08:00:00",
            "justificationStatus": "rejected",
            "lessons": [],
            "dateFrom": "2026-09-19",
            "dateTo": "2026-09-19",
        },
        {"messageFromParent": "no id"},
    ],
}


def test_parse_justifications_newest_first_with_status() -> None:
    items = parsers.parse_justifications(JUSTIFICATIONS)

    assert [j.id for j in items] == [7, 5, 3]
    rejected, pending, accepted = items
    assert accepted.is_accepted and not accepted.is_pending
    assert accepted.lessons == [("2026-09-04", 8)]
    assert accepted.teachers == ["Anna Nowak"]
    assert accepted.justified_absences == 1
    assert pending.is_pending and pending.has_attachment
    assert rejected.is_rejected


def test_justified_dates_skip_rejected() -> None:
    days = parsers.justified_dates(parsers.parse_justifications(JUSTIFICATIONS))
    assert days == {"2026-09-04", "2026-09-14", "2026-09-15", "2026-09-16"}


def test_parse_text_grades_with_categories() -> None:
    categories = parsers.parse_text_grade_categories(
        {"Categories": [{"Id": 5, "Name": "zadanie", "CountToTheAverage": True}]}
    )
    grades = parsers.parse_text_grades(
        {
            "Grades": [
                {
                    "Id": 1,
                    "Grade": "Bardzo dobrze opanowany\n      materiał",
                    "Subject": {"Id": 9},
                    "Lesson": {"Id": 3},
                    "Category": {"Id": 5},
                    "AddedBy": {"Id": 7},
                    "Date": "2026-09-18",
                    "AddDate": "2026-09-18 11:25:36",
                    "Semester": 1,
                    "ShowInGradesView": True,
                },
                {"Id": 2, "Grade": "ukryta", "ShowInGradesView": False},
            ]
        },
        categories,
    )
    assert len(grades) == 1
    grade = grades[0]
    assert grade.value == "Bardzo dobrze opanowany materiał"
    assert (grade.subject_id, grade.category, grade.teacher_id) == (9, "zadanie", 7)
    assert grade.counts_to_average is True


def test_parse_realizations_resolves_subject() -> None:
    topics = parsers.parse_realizations(
        {
            "Realizations": [
                {
                    "Id": "t1",
                    "Lesson": {"Id": 3},
                    "LessonNumber": 2,
                    "LessonNo": 2,
                    "Date": "2026-09-15",
                    "Topic": " Ułamki zwykłe ",
                    "IsTrip": False,
                    "AddedBy": {"Id": 7},
                },
                {
                    "Id": "t2",
                    "Lesson": {"Id": 4},
                    "LessonNumber": 5,
                    "Date": "2026-09-16",
                    "Topic": "Wyjście",
                    "IsTrip": True,
                },
            ]
        },
        {3: 100},
    )
    assert [t.id for t in topics] == ["t2", "t1"]
    assert topics[1].topic == "Ułamki zwykłe"
    assert topics[1].subject_id == 100
    assert topics[0].lesson_no == 5 and topics[0].is_trip and topics[0].subject_id is None


def test_parse_school_trips_and_files() -> None:
    trips = parsers.parse_school_trips(
        {
            "Data": [
                {
                    "id": 2,
                    "destination": "Muzeum",
                    "route": "Szkoła - Muzeum",
                    "locomotion": "autokar",
                    "termFrom": "2026-10-20",
                    "termTo": "2026-10-20",
                    "coordinatorName": "Nowak Anna",
                },
                {
                    "id": 1,
                    "destination": "Kino",
                    "termFrom": "2026-09-23",
                    "creatorName": "Jan",
                    "creatorLastName": "Kowal",
                },
            ]
        }
    )
    assert [t.id for t in trips] == [1, 2]
    assert trips[0].coordinator == "Jan Kowal" and trips[0].date_to == "2026-09-23"
    assert trips[1].transport == "autokar"
    files = parsers.parse_school_files(
        {
            "Data": [
                {
                    "id": "17613",
                    "displayName": "Regulamin",
                    "addedOnDate": "2026-09-04 11:26:03",
                    "downloadUrl": "/pliki_szkoly/pobierz/1",
                },
                {"id": "9", "displayName": "Zepsuty", "failed": True},
            ]
        }
    )
    assert [(f.id, f.name, f.download_path) for f in files] == [
        ("17613", "Regulamin", "/pliki_szkoly/pobierz/1")
    ]


def test_class_register_number_from_user_record() -> None:
    assert parsers.parse_user_class_register_number({"User": {"ClassRegisterNumber": 25}}) == 25
    assert parsers.parse_user_class_register_number({}) is None


_ENTRIES = {
    "TimetableEntries": [
        # Monday: lesson 1 maths in room 10, lesson 2 Polish.
        {
            "Id": 1,
            "Lesson": {"Id": 501},
            "DayOfTheWeek": 1,
            "LessonNo": 1,
            "DateFrom": "2026-09-01",
            "DateTo": "2027-06-25",
            "Classroom": {"Id": 10, "Name": "10"},
        },
        {
            "Id": 2,
            "Lesson": {"Id": 502},
            "DayOfTheWeek": 1,
            "LessonNo": 2,
            "DateFrom": "2026-09-01",
            "DateTo": "2027-06-25",
            "Classroom": {"Id": 11, "Symbol": "11"},
        },
        # An old version of lesson 3, no longer valid in October.
        {
            "Id": 3,
            "Lesson": {"Id": 503},
            "DayOfTheWeek": 1,
            "LessonNo": 3,
            "DateFrom": "2026-09-01",
            "DateTo": "2026-09-06",
            "Classroom": None,
        },
        # Tuesday: lesson 1 Polish.
        {
            "Id": 4,
            "Lesson": {"Id": 502},
            "DayOfTheWeek": "2",
            "LessonNo": "1",
            "DateFrom": "2026-09-01",
            "DateTo": "2027-06-25",
            "Classroom": {"Id": 11, "Name": "11"},
        },
    ]
}
_LESSON_SUBJECTS = {501: 100, 502: 200, 503: 300}


def _lesson(no: int, subject: int, room: int | None, *, canceled: bool = False) -> LessonData:
    return LessonData(
        lesson_no=no,
        hour_from=None,
        hour_to=None,
        subject_id=subject,
        teacher_id=None,
        classroom_id=room,
        is_canceled=canceled,
        is_substitution=False,
    )


def test_parse_timetable_entries() -> None:
    entries = parsers.parse_timetable_entries(_ENTRIES, _LESSON_SUBJECTS)
    assert [(e.day_of_week, e.lesson_no, e.subject_id) for e in entries] == [
        (1, 1, 100),
        (1, 2, 200),
        (1, 3, 300),
        (2, 1, 200),
    ]
    assert entries[1].classroom == "11"
    assert entries[2].classroom is None
    assert not entries[2].valid_on(date(2026, 10, 5))
    assert entries[0].valid_on(date(2026, 10, 5))


def test_plan_differences() -> None:
    standing = parsers.parse_timetable_entries(_ENTRIES, _LESSON_SUBJECTS)
    monday, tuesday = date(2026, 10, 5), date(2026, 10, 6)
    timetable = {
        # Lesson 1 in another room, lesson 2 cancelled, an extra lesson 4.
        monday: [_lesson(1, 100, 12), _lesson(2, 200, 11, canceled=True), _lesson(4, 100, 10)],
        # Tuesday has a plan but no lessons: a day off.
        tuesday: [],
        date(2026, 10, 10): [],
    }
    free = [FreeDayData(id=1, name="Dzień Edukacji", date_from="2026-10-06", date_to="2026-10-06")]
    diffs = parsers.plan_differences(timetable, standing, free)
    assert [(d.date, d.lesson_no, d.kind) for d in diffs] == [
        (monday, 1, "room"),
        (monday, 2, "cancelled"),
        (monday, 4, "extra"),
        (tuesday, None, "no_lessons"),
    ]
    assert diffs[0].planned_classroom == "10"
    assert diffs[3].free_day == "Dzień Edukacji"
    # Matching weeks and an empty plan give nothing.
    same = {monday: [_lesson(1, 100, 10), _lesson(2, 200, 11)]}
    assert parsers.plan_differences(same, standing) == []
    assert parsers.plan_differences(timetable, []) == []


def test_plan_differences_subject_and_missing() -> None:
    standing = parsers.parse_timetable_entries(_ENTRIES, _LESSON_SUBJECTS)
    monday = date(2026, 10, 5)
    diffs = parsers.plan_differences({monday: [_lesson(1, 300, 10)]}, standing)
    assert [(d.lesson_no, d.kind, d.planned_subject_id, d.subject_id) for d in diffs] == [
        (1, "subject", 100, 300),
        (2, "missing", 200, None),
    ]


def test_outbox_message_has_receiver() -> None:
    payload = {
        "data": [
            {
                "messageId": "5",
                "receiverName": "Anna Nowak",
                "topic": "Usprawiedliwienie",
                "content": base64.b64encode("Dzień dobry".encode()).decode(),
                "sendDate": "2026-10-01 08:00:00",
            },
            {"messageId": "6", "receiverFirstName": "Jan", "receiverLastName": "Kowalski"},
            {"messageId": "7", "senderName": "Szkoła"},
        ]
    }
    messages = parsers.parse_message_list(payload, "outbox")
    assert [m.receiver_name for m in messages] == ["Anna Nowak", "Jan Kowalski", None]
    assert messages[0].content == "Dzień dobry"
    assert messages[0].mailbox == "outbox"


def test_homework_assignment_attachments_defensive() -> None:
    payload = {
        "HomeWorkAssignments": [
            {
                "Id": 1,
                "Topic": "Wypracowanie",
                "HomeworkAssigmentFiles": [
                    {"Id": 10, "Name": "polecenie.pdf"},
                    {"Id": "11", "FileName": "karta.docx"},
                    12,
                    {"Name": "no id"},
                    None,
                ],
            },
            {"Id": 2, "Topic": "Bez plików", "HomeworkAssigmentFiles": []},
        ]
    }
    first, second = parsers.parse_homework_assignments(payload)
    assert [(a.id, a.filename) for a in first.attachments] == [
        ("10", "polecenie.pdf"),
        ("11", "karta.docx"),
        ("12", None),
    ]
    assert second.attachments == []


def test_parse_descriptive_grades_reads_map_skill_and_teacher() -> None:
    """Shape CONFIRMED live 2026-10-09: the shown grade is `Map`, not
    `Grade` (3 for a "6")."""
    skills = parsers.parse_descriptive_skills(
        {
            "Skills": [
                {"Id": 501, "Name": "Ekspresja muzyczna. Śpiew", "Subject": {"Id": 9}},
                {"Id": 502, "Name": "Rytmika", "Subject": {"Id": 9}},
            ]
        }
    )
    assert skills == {501: "Ekspresja muzyczna. Śpiew", 502: "Rytmika"}
    grades = parsers.parse_descriptive_grades(
        {
            "Grades": [
                {
                    "Id": 1,
                    "Lesson": {"Id": 3},
                    "Subject": {"Id": 9},
                    "Skill": {"Id": 501},
                    "AddedBy": {"Id": 7},
                    "Grade": 3,
                    "Map": "6",
                    "RealGradeValue": "6",
                    "Date": "2026-09-30",
                    "AddDate": "2026-09-30 13:37:00",
                    "Semester": 1,
                    "Comments": [{"Id": 44}],
                },
                {"Id": 2, "Skill": {"Id": 999}, "Grade": 4, "RealGradeValue": "5"},
                {"Id": 3, "Grade": 3},
            ]
        },
        skills,
        parsers.parse_comment_text_map(
            {
                "Comments": [
                    {
                        "Id": 44,
                        "AddedBy": {"Id": 7},
                        "Grade": {"Id": 1},
                        "Text": "Mazurek - cztery zwrotki",
                    }
                ]
            }
        ),
    )
    first, second, third = grades
    assert first.value == "6"
    assert (first.subject_id, first.skill_id, first.skill) == (9, 501, "Ekspresja muzyczna. Śpiew")
    assert (first.teacher_id, first.date, first.semester) == (7, "2026-09-30", 1)
    assert (first.comment_ids, first.comments) == ([44], ["Mazurek - cztery zwrotki"])
    # No Map -> RealGradeValue; an unknown skill keeps its id only.
    assert (second.value, second.skill, second.comment_ids) == ("5", None, [])
    # `Grade` alone is the range on the scale, not the grade: no value.
    assert third.value == ""


def test_grade_value_follows_the_schools_grading_system() -> None:
    grading = parsers.parse_grading_system(
        {"countZero": True, "plusValue": 0.25, "minusValue": 0.5}
    )
    assert (grading.plus_value, grading.minus_value, grading.count_zero) == (0.25, 0.5, True)
    assert parse_grade_value("4+", grading) == 4.25
    assert parse_grade_value("4-", grading) == 3.5
    assert parse_grade_value("0", grading) == 0.0
    # Without settings: +0.5 / -0.25 and no zero (the tested school).
    assert parse_grade_value("4+") == 4.5
    assert parse_grade_value("4-") == 3.75
    assert parse_grade_value("0") is None
    # Odd values keep the defaults.
    assert parsers.parse_grading_system({"plusValue": "x"}).plus_value == 0.5
    assert parsers.parse_grading_system({"plusValue": "nan"}).plus_value == 0.5
    # A modifier on 0 is never a grade, even where 0 counts.
    assert parse_grade_value("0+", grading) is None
    assert parse_grade_value("0-", grading) is None


def test_grading_system_reads_numbers_given_as_strings_and_sizes() -> None:
    grading = parsers.parse_grading_system({"plusValue": "0,75", "minusValue": "-0.5"})
    assert (grading.plus_value, grading.minus_value) == (0.75, 0.5)
    # A negative "+" still adds.
    assert parsers.parse_grading_system({"plusValue": -0.5}).plus_value == 0.5


def test_student_identifier_and_auth_subjects() -> None:
    assert (
        parsers.extract_student_identifier({"IdentifierOfStudentAssignedWithUser": "LID-1"})
        == "LID-1"
    )
    assert (
        parsers.extract_student_identifier(
            {"UserInfo": {"IdentifierOfStudentAssignedWithUser": "LID-2"}}
        )
        == "LID-2"
    )
    assert parsers.extract_student_identifier({}) is None
    subjects = parsers.parse_auth_subjects(
        {
            "data": [
                {"identifier": "LID-S-9", "numericIdentifier": 9, "name": "Muzyka"},
                {"identifier": None},
            ]
        }
    )
    assert subjects == {"LID-S-9": 9}


def test_parse_partial_grades() -> None:
    """The new descriptive grading - item fields from another client's code
    (no real grade seen yet), made-up values."""
    grades = parsers.parse_partial_grades(
        {
            "data": [
                {
                    "gradeId": 501,
                    "teacherId": "LID-T-1",
                    "area": {"id": 3, "name": "Muzyka i ruch", "color": "#fff"},
                    "subjectId": "LID-S-9",
                    "scaleValue": {"id": 2, "value": "W"},
                    "content": "Śpiewa czysto",
                    "comments": ["Brawo"],
                    "implementedRequirements": [{"id": 1, "name": "Śpiewa piosenki"}],
                    "date": "2026-10-01",
                    "semester": 1,
                    "addDate": "2026-10-01 10:00:00",
                },
                {"noGradeId": True},
            ],
            "pagination": {"limit": 50, "page": 1, "total": 1},
        },
        {"LID-S-9": 9},
    )
    (grade,) = grades
    assert (grade.id, grade.source, grade.subject_id, grade.value) == ("p501", "partial", 9, "W")
    assert (grade.skill, grade.teacher_lid, grade.date, grade.semester) == (
        "Muzyka i ruch",
        "LID-T-1",
        "2026-10-01",
        1,
    )
    assert grade.comments == ["Śpiewa czysto", "Brawo"]
    assert grade.requirements == ["Śpiewa piosenki"]


def test_parse_partial_grades_odd_shapes() -> None:
    """One comment object instead of a list, a number as the subject id, a
    zero on the scale - none of it may be lost or crash."""
    first, second, third = parsers.parse_partial_grades(
        {
            "data": [
                {
                    "gradeId": 1,
                    "subjectId": "9",
                    "scaleValue": {"value": 0},
                    "comments": {"content": "Do poprawy"},
                },
                {"gradeId": 2, "subjectId": 7, "scaleValue": {"value": None}, "comments": 5},
                {"gradeId": 3, "subjectId": "LID-UNKNOWN", "comments": "Brawo"},
            ]
        },
        {"LID-S-9": 9},
    )
    assert (first.subject_id, first.value, first.comments) == (9, "0", ["Do poprawy"])
    assert (second.subject_id, second.value, second.comments) == (7, "", [])
    assert (third.subject_id, third.value, third.comments) == (None, "", ["Brawo"])


def test_parse_partial_grades_ignores_bools() -> None:
    """`True` is not subject 1 and not the grade "True"."""
    (grade,) = parsers.parse_partial_grades(
        {"data": [{"gradeId": 4, "subjectId": True, "scaleValue": {"value": False}}]}
    )
    assert (grade.subject_id, grade.value) == (None, "")


def test_parse_message_receivers() -> None:
    message = parsers.parse_message(
        {
            "data": {
                "topic": "Pytanie",
                "Message": "",
                "receivers": [
                    {
                        "firstName": "Anna",
                        "lastName": "Nowak",
                        "group": "nauczyciel",
                        "readed": "2026-10-02 08:00:00",
                    },
                    {
                        "firstName": "Jan",
                        "lastName": "Kowalski",
                        "group": "nauczyciel",
                        "readed": "",
                    },
                ],
            }
        },
        "outbox",
        "7",
    )
    assert message is not None
    assert [(r.name, r.read_date) for r in message.receivers] == [
        ("Anna Nowak", "2026-10-02 08:00:00"),
        ("Jan Kowalski", None),
    ]
