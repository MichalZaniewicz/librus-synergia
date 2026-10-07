"""Parser tests. Every payload shape here mirrors a response confirmed
against a real Librus account (see docs/)."""

from __future__ import annotations

import base64
from datetime import date

import pytest

from librus_synergia import parsers
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
                    "Grade": "Bardzo dobrze opanowany materiał",
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
