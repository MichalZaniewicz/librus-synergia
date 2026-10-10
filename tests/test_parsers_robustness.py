"""Parsers: records without a usable id are skipped, nulls don't crash."""

from __future__ import annotations

import base64

from librus_synergia import parsers

BAD_IDS = [{"Id": "abc"}, {"Id": None}, {"Id": [1]}, {}, "not a dict"]


def test_grades_skip_records_without_a_usable_id() -> None:
    grades = parsers.parse_grades({"Grades": [*BAD_IDS, {"Id": "12", "Grade": None}]})
    assert [(g.id, g.value) for g in grades] == [(12, "")]


def test_grade_zero_value_is_kept() -> None:
    (grade,) = parsers.parse_grades({"Grades": [{"Id": 1, "Grade": 0}]})
    assert grade.value == "0"


def test_grade_categories_skip_bad_ids_and_read_weights_safely() -> None:
    categories = parsers.parse_grade_categories(
        {
            "Categories": [
                *BAD_IDS,
                {"Id": 1, "Weight": "2"},
                {"Id": 2, "Weight": "1,5"},
                {"Id": 3, "Weight": "heavy"},
                {"Id": 4, "Weight": None, "Name": None},
                {"Id": "5", "Weight": 3.0},
            ]
        }
    )
    assert {k: c.weight for k, c in categories.items()} == {1: 2, 2: 1.5, 3: 1, 4: 1, 5: 3}
    assert isinstance(categories[1].weight, int)
    assert categories[4].name == ""


def test_comment_map_skips_bad_ids_and_non_text() -> None:
    comments = parsers.parse_comment_text_map(
        {"Comments": [*BAD_IDS, {"Id": 1, "Text": "ok"}, {"Id": 2, "Text": 5}]}
    )
    assert comments == {1: "ok"}


def test_other_lists_skip_bad_ids() -> None:
    assert parsers.parse_notes({"Notes": [*BAD_IDS, {"Id": 3, "Text": None}]})[0].text == ""
    assert len(parsers.parse_notes({"Notes": BAD_IDS})) == 0
    assert list(parsers.parse_attendance_types({"Types": [*BAD_IDS, {"Id": 1}]})) == [1]
    (event,) = parsers.parse_homeworks({"HomeWorks": [*BAD_IDS, {"Id": 2, "Content": None}]})
    assert event.content == ""
    (homework,) = parsers.parse_homework_assignments(
        {"HomeWorkAssignments": [*BAD_IDS, {"Id": 3, "Topic": None}]}
    )
    assert homework.topic == ""
    assert len(parsers.parse_behaviour_grades({"Grades": [*BAD_IDS, {"Id": 4}]})) == 1
    assert len(parsers.parse_descriptive_grades({"Grades": [*BAD_IDS, {"Id": 5}]})) == 1
    assert (
        len(
            parsers.parse_parent_teacher_conferences(
                {"ParentTeacherConferences": [*BAD_IDS, {"Id": 6}]}
            )
        )
        == 1
    )
    free = parsers.parse_free_days(
        {"SchoolFreeDays": [*BAD_IDS, {"Id": 7, "DateFrom": "2026-12-24", "DateTo": "2026-12-26"}]},
        "SchoolFreeDays",
    )
    assert [f.id for f in free] == [7]
    assert parsers.parse_id_name_map(
        {"Subjects": [*BAD_IDS, {"Id": 8, "Name": "Mat"}]}, ("Subjects",)
    ) == {8: "Mat"}
    assert parsers.parse_lesson_subjects(
        {
            "Lessons": [
                *BAD_IDS,
                {"Id": 9, "Subject": {"Id": "x"}},
                {"Id": 10, "Subject": {"Id": "4"}},
            ]
        }
    ) == {10: 4}


def test_attendance_ids_with_letters_still_work() -> None:
    (record,) = parsers.parse_attendances({"Attendances": [{"Id": "t123", "Type": {"Id": 1}}]})
    assert record.id == "t123"


def test_null_message_content_and_topic() -> None:
    (message,) = parsers.parse_message_list(
        {"data": [{"messageId": "1", "content": None, "topic": None}]}, "inbox"
    )
    assert (message.content, message.topic) == ("", "")
    full = parsers.parse_message({"data": {"Message": None, "topic": None}}, "inbox", "1")
    assert full is not None and full.content == "" and full.topic == ""
    body = base64.b64encode(b"<Message><Content><![CDATA[Hej]]></Content></Message>").decode()
    full = parsers.parse_message({"data": {"Message": body}}, "inbox", "2")
    assert full is not None and full.content == "Hej"


# A nested reference ("Category": {"Id": 5}) that comes back as something
# else must not crash the whole parse - it just has no id.
ODD_REFS: list[object] = ["x", [1, 2], 7, True]


def test_odd_nested_references_do_not_crash_any_parser() -> None:
    for odd in ODD_REFS:
        ref = {
            "Category": odd,
            "Subject": odd,
            "AddedBy": odd,
            "Teacher": odd,
            "Lesson": odd,
            "Type": odd,
            "Classroom": odd,
        }
        (grade,) = parsers.parse_grades({"Grades": [{"Id": 1, "Grade": "5", **ref}]})
        assert grade.category_id is None and grade.subject_id is None
        assert grade.teacher_id is None
        (note,) = parsers.parse_notes({"Notes": [{"Id": 1, **ref}]})
        assert note.category_id is None and note.teacher_id is None
        (attendance,) = parsers.parse_attendances({"Attendances": [{"Id": 1, **ref}]})
        assert attendance.lesson_id is None and attendance.type_id is None
        lesson = parsers.parse_lesson(ref)
        assert lesson.subject_id is None and lesson.classroom_id is None
        (event,) = parsers.parse_homeworks({"HomeWorks": [{"Id": 1, **ref}]})
        assert event.category_id is None
        (assignment,) = parsers.parse_homework_assignments(
            {"HomeWorkAssignments": [{"Id": 1, **ref}]}
        )
        assert assignment.lesson_id is None and assignment.teacher_id is None
        (behaviour,) = parsers.parse_behaviour_grades({"Grades": [{"Id": 1, **ref}]})
        assert behaviour.category_id is None
        (point,) = parsers.parse_point_grades({"Grades": [{"Id": 1, **ref}]})
        assert point.subject_id is None
        (conference,) = parsers.parse_parent_teacher_conferences(
            {"ParentTeacherConferences": [{"Id": 1, **ref}]}
        )
        assert conference.teacher_id is None
        me = parsers.parse_me({"Me": {"Account": odd, "User": odd}})
        assert me.account_id is None and me.first_name == ""
        assert parsers.parse_me({"Me": odd}).last_name == ""
        klass = parsers.parse_class({"Class": {"Symbol": "7d", "ClassTutor": odd}})
        assert klass is not None and klass.tutor_id is None


def test_odd_lists_do_not_crash() -> None:
    assert parsers.parse_text_grades({"Grades": 5}) == []
    assert parsers.parse_realizations({"Realizations": {"a": 1}}) == []
    assert parsers.parse_school_trips({"Data": 3}) == []
    (justification,) = parsers.parse_justifications(
        {"data": [{"id": 1, "lessons": 4, "notifiedTeachers": "x"}]}
    )
    assert justification.lessons == [] and justification.teachers == []
    (grade,) = parsers.parse_descriptive_grades({"Grades": [{"Id": 1, "Comments": 9}]})
    assert grade.comment_ids == []
    message = parsers.parse_message(
        {"data": {"attachments": 1, "receivers": {"a": 1}, "Message": 5}}, "inbox", "9"
    )
    assert message is not None
    assert message.attachments == [] and message.receivers == [] and message.content == ""


def test_non_string_message_content_is_empty() -> None:
    for odd in (123, {"a": 1}, ["x"], None):
        assert parsers.decode_message_content(odd) == ""  # type: ignore[arg-type]
    (message,) = parsers.parse_message_list({"data": [{"messageId": 1, "content": 42}]}, "inbox")
    assert message.content == ""


def test_odd_comment_payload_and_info_page_do_not_crash() -> None:
    assert parsers.parse_comment_text_map(["x"]) == {}  # type: ignore[arg-type]
    assert parsers.parse_student_number(None) is None  # type: ignore[arg-type]


def test_null_names_never_become_none() -> None:
    assert parsers.resolve_sender_name({"senderFirstName": None, "senderLastName": "Nowak"}) == (
        "Nowak"
    )
    assert parsers.resolve_sender_name({"senderName": None, "senderFirstName": "Anna"}) == "Anna"
    me = parsers.parse_me({"Me": {"User": {"FirstName": None, "LastName": "Kowalska"}}})
    assert (me.first_name, me.last_name) == ("", "Kowalska")
    school = parsers.parse_school({"School": {"Name": None}})
    assert school is not None and school.name == ""
    klass = parsers.parse_class({"Class": {"Symbol": None}})
    assert klass is not None and klass.symbol == ""
