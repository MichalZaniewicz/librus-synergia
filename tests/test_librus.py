"""Tests for the high-level `Librus` wrapper."""

from __future__ import annotations

from datetime import date

import aiohttp

from librus_synergia import Librus
from librus_synergia.const import (
    DATA_BASE_URL,
    MESSAGES_BASE_URL,
    MESSAGES_BOOTSTRAP_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    SYNERGIA_STUDENT_INFO_URL,
)

from .helpers import MockedSession, mock_successful_login
from .test_parsers import STUDENT_INFO_PAGE

GRADES = {"Grades": [{"Id": 1, "Grade": "5+", "Subject": {"Id": 9}, "Comments": [{"Id": 3}]}]}
COMMENTS = {"Comments": [{"Id": 3, "Text": "Świetnie"}]}


async def test_grades_logs_in_lazily_and_resolves_comments() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Grades", json_data=GRADES)
            mocked.get(f"{DATA_BASE_URL}/Grades/Comments", json_data=COMMENTS)
            librus = Librus("1234567u", "pw", session=session)
            (grade,) = await librus.grades()
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1
    assert grade.value == "5+"
    assert grade.comments == ["Świetnie"]
    assert librus.session_data.logged_in_at > 0


async def test_expired_session_relogs_in_once_and_retries() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                f"{DATA_BASE_URL}/Me",
                {"status": 401, "json_data": {}},
                {"json_data": {"Me": {"User": {"FirstName": "Jan", "LastName": "Kowalski"}}}},
            )
            librus = Librus("1234567u", "pw", session=session)
            me = await librus.me()
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2
    assert me.display_name == "Jan Kowalski"


async def test_messages_module_disabled_returns_empty() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>Brak dostępu</html>")
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.messages() == []
            assert await librus.unread_messages() == {}


async def test_missing_mailbox_is_empty_without_relogin() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(
                f"{MESSAGES_BASE_URL}/substitutions/messages", status=404, json_data={"data": []}
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.messages("substitutions") == []
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1


async def test_messages_listed() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(
                f"{MESSAGES_BASE_URL}/inbox/messages",
                json_data={"data": [{"messageId": "1", "senderName": "Szkoła", "topic": "Hej"}]},
            )
            librus = Librus("1234567u", "pw", session=session)
            (message,) = await librus.messages()
    assert message.topic == "Hej"
    assert message.mailbox == "inbox"


async def test_context_manager_closes_own_session() -> None:
    async with Librus("1234567u", "pw") as librus:
        session = librus.client._session
    assert session.closed


CHILD_LID = "LID-AUTH-USER-1-CHILD"
KG = "https://synergia.librus.pl/gateway/ms/kindergartens"


async def test_regular_account_never_probes_kindergarten() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Timetables", json_data={"Timetable": {}})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.timetable(date(2026, 9, 3)) == {}
            # Any kindergarten request would hit an unregistered URL and fail.
            assert librus._kindergarten_checked is False


async def test_kindergarten_account_found_after_timetable_403() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Timetables", status=403, json_data={})
            mocked.get(
                f"{DATA_BASE_URL}/Me",
                json_data={"Me": {"User": {"FirstName": "Ala", "Id": CHILD_LID}, "Account": {}}},
            )
            mocked.get(f"{DATA_BASE_URL}/Auth/TokenInfo", status=403, json_data={})
            mocked.get(
                f"{KG}/timetable/kindergarteners/{CHILD_LID}",
                json_data={
                    "timetableEntries": [
                        {
                            "date": "2026-09-01",
                            "startTime": "08:00",
                            "endTime": "08:30",
                            "activityTypeIdentifier": "LID-ACT-1",
                            "type": "planned",
                        }
                    ]
                },
            )
            mocked.get(
                f"{DATA_BASE_URL}/Auth/Users/Kindergarteners/{CHILD_LID}",
                json_data={"data": {"groupIdentifier": "LID-GROUP-1"}},
            )
            mocked.get(f"{DATA_BASE_URL}/Subjects", json_data={"Subjects": []})
            mocked.get(
                f"{KG}/activities-types",
                json_data={"activitiesTypes": [{"identifier": "LID-ACT-1", "name": "Rytmika"}]},
            )
            librus = Librus("1234567u", "pw", session=session)
            week = await librus.timetable(date(2026, 9, 1))
            assert await librus.kindergartener_id() == CHILD_LID
            subjects = await librus.subjects()
    (lesson,) = week[date(2026, 9, 1)]
    assert lesson.lesson_no is None
    assert subjects[lesson.subject_id] == "Rytmika"


async def test_forbidden_timetable_without_kindergarten_is_empty() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Timetables", status=403, json_data={})
            mocked.get(f"{DATA_BASE_URL}/Me", json_data={"Me": {"User": {}, "Account": {}}})
            mocked.get(f"{DATA_BASE_URL}/Auth/TokenInfo", status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.timetable(date(2026, 9, 1)) == {}
            assert await librus.timetable(date(2026, 9, 8)) == {}
            # Discovery ran once, not on every call.
            assert mocked.get_calls[f"{DATA_BASE_URL}/Auth/TokenInfo"] == 1


async def test_student_number_from_web_page() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(SYNERGIA_STUDENT_INFO_URL, text_data=STUDENT_INFO_PAGE)
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_number() == 25


async def test_student_number_redirect_relogs_in() -> None:
    """A redirect to the login page is a dead session: log in again once."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                SYNERGIA_STUDENT_INFO_URL,
                {"status": 302, "text_data": ""},
                {"text_data": STUDENT_INFO_PAGE},
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_number() == 25
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2
