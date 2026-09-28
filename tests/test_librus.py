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
)

from .helpers import MockedSession, mock_successful_login

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


async def test_unpublished_timetable_is_empty_not_an_error() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Timetables", status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.timetable(date(2026, 9, 3)) == {}
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1


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
