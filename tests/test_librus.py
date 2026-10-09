"""Tests for the high-level `Librus` wrapper."""

from __future__ import annotations

import asyncio
from datetime import date

import aiohttp
import pytest

from librus_synergia import Librus
from librus_synergia.const import (
    DATA_BASE_URL,
    MESSAGES_BASE_URL,
    MESSAGES_BOOTSTRAP_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    SYNERGIA_STUDENT_INFO_URL,
)
from librus_synergia.exceptions import (
    LibrusConnectionError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)
from librus_synergia.librus import RECHECK_SECONDS

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
            assert librus._kindergarten_checked_at is None


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
            mocked.get(f"{DATA_BASE_URL}/Me", json_data={"Me": {"Account": {}}})
            mocked.get(SYNERGIA_STUDENT_INFO_URL, text_data=STUDENT_INFO_PAGE)
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_number() == 25


async def test_student_number_from_the_students_user_record() -> None:
    """`Users/{Me.Account.UserId}.ClassRegisterNumber` first - no web page."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Me", json_data={"Me": {"Account": {"UserId": 77}}})
            mocked.get(
                f"{DATA_BASE_URL}/Users/77",
                json_data={"User": {"Id": 77, "ClassRegisterNumber": 12}},
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_number() == 12
            assert SYNERGIA_STUDENT_INFO_URL not in mocked.get_calls


async def test_student_number_redirect_relogs_in() -> None:
    """A redirect to the login page is a dead session: log in again once."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Me", json_data={"Me": {"Account": {}}})
            mocked.get_sequence(
                SYNERGIA_STUDENT_INFO_URL,
                {"status": 302, "text_data": ""},
                {"text_data": STUDENT_INFO_PAGE},
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_number() == 25
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2


async def test_descriptive_grades_named_by_skill() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(
                f"{DATA_BASE_URL}/DescriptiveGrades",
                json_data={
                    "Grades": [
                        {
                            "Id": 1,
                            "Skill": {"Id": 501},
                            "Grade": 3,
                            "Map": "6",
                            "Comments": [{"Id": 44}],
                        }
                    ]
                },
            )
            mocked.get(
                f"{DATA_BASE_URL}/DescriptiveGrades/Skills",
                json_data={"Skills": [{"Id": 501, "Name": "Rytmika"}]},
            )
            mocked.get(
                f"{DATA_BASE_URL}/DescriptiveGrades/Comments",
                json_data={"Comments": [{"Id": 44, "Text": "Brawo"}]},
            )
            librus = Librus("1234567u", "pw", session=session)
            (grade,) = await librus.descriptive_grades()
    assert (grade.value, grade.skill, grade.comments) == ("6", "Rytmika", ["Brawo"])


async def test_no_descriptive_grades_skips_the_skills_list() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/DescriptiveGrades", json_data={"Grades": []})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.descriptive_grades() == []
            assert f"{DATA_BASE_URL}/DescriptiveGrades/Skills" not in mocked.get_calls
            assert f"{DATA_BASE_URL}/DescriptiveGrades/Comments" not in mocked.get_calls


async def test_partial_grades_and_grading_system() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(
                f"{DATA_BASE_URL}/Auth/TokenInfo", json_data={"UserIdentifier": "LID-AUTH-USER-1"}
            )
            mocked.get(
                f"{DATA_BASE_URL}/Auth/UserInfo/LID-AUTH-USER-1",
                json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"},
            )
            mocked.post(
                f"{DATA_BASE_URL}/Auth/DescriptiveGradingSystem/PartialGrades/Student/LID-AUTH-USER-2",
                json_data={
                    "data": [{"gradeId": 1, "subjectId": "LID-S", "scaleValue": {"value": "B"}}]
                },
            )
            mocked.get(
                f"{DATA_BASE_URL}/Auth/Subjects",
                json_data={"data": [{"identifier": "LID-S", "numericIdentifier": 9}]},
            )
            mocked.get(
                f"{DATA_BASE_URL}/GradingSystem", json_data={"plusValue": 0.3, "minusValue": 0.3}
            )
            librus = Librus("1234567u", "pw", session=session)
            (grade,) = await librus.partial_grades()
            grading = await librus.grading_system()
            await librus.partial_grades()  # the child's LID is looked up once
            assert mocked.get_calls[f"{DATA_BASE_URL}/Auth/TokenInfo"] == 1
    assert (grade.id, grade.subject_id, grade.value) == ("p1", 9, "B")
    assert (grading.plus_value, grading.minus_value) == (0.3, 0.3)


async def test_no_child_identifier_means_no_partial_grades() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/Auth/TokenInfo", status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.partial_grades() == []


TOKEN_INFO = f"{DATA_BASE_URL}/Auth/TokenInfo"
USER_INFO = f"{DATA_BASE_URL}/Auth/UserInfo/LID-AUTH-USER-1"
PARTIAL_GRADES = (
    f"{DATA_BASE_URL}/Auth/DescriptiveGradingSystem/PartialGrades/Student/LID-AUTH-USER-2"
)


async def test_failed_child_identifier_lookup_is_tried_again() -> None:
    """A transient failure (here a 500) must not be remembered as "no LID"."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                TOKEN_INFO,
                {"status": 500, "json_data": {}},
                {"json_data": {"UserIdentifier": "LID-AUTH-USER-1"}},
            )
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_identifier() is None
            assert await librus.student_identifier() == "LID-AUTH-USER-2"
            assert await librus.student_identifier() == "LID-AUTH-USER-2"
            assert mocked.get_calls[TOKEN_INFO] == 2


async def test_definite_no_child_identifier_is_remembered() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(USER_INFO, json_data={"UserInfo": {}})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_identifier() is None
            assert await librus.student_identifier() is None
            assert mocked.get_calls[TOKEN_INFO] == 1
            assert mocked.get_calls[USER_INFO] == 1


async def test_concurrent_child_identifier_lookups_ask_once() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            librus = Librus("1234567u", "pw", session=session)
            results = await asyncio.gather(*(librus.student_identifier() for _ in range(3)))
            assert results == ["LID-AUTH-USER-2"] * 3
            assert mocked.get_calls[TOKEN_INFO] == 1


@pytest.mark.parametrize("status", [403, 404, 405])
async def test_unavailable_partial_grades_are_not_asked_again(status: int) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            mocked.post(PARTIAL_GRADES, status=status, json_data={"Status": "Error"})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.partial_grades() == []
            assert await librus.partial_grades() == []
            assert mocked.post_calls[PARTIAL_GRADES] == 1
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1


async def test_partial_grades_server_error_is_raised_and_tried_again() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            mocked.post(PARTIAL_GRADES, status=500, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            for _ in range(2):
                with pytest.raises(LibrusUnexpectedResponseError):
                    await librus.partial_grades()
            assert mocked.post_calls[PARTIAL_GRADES] == 2


async def test_grading_system_is_read_once() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/GradingSystem", json_data={"plusValue": 0.3})
            librus = Librus("1234567u", "pw", session=session)
            first = await librus.grading_system()
            assert await librus.grading_system() is first
            assert mocked.get_calls[f"{DATA_BASE_URL}/GradingSystem"] == 1


async def test_insufficient_scopes_does_not_log_in_again() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(
                f"{DATA_BASE_URL}/SchoolInfo",
                status=401,
                text_data="Insufficient scopes",
            )
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusUnexpectedResponseError):
                await librus._call(lambda: librus.client._async_request("SchoolInfo"))
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1


class _Clock:
    """Stands in for `librus_synergia.librus._now`."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("librus_synergia.librus._now", fake)
    return fake


ATTACHMENT = f"{MESSAGES_BASE_URL}/attachments/55/messages/99"
SANDBOX_LINK = "https://sandbox.librus.pl/GetFile/KEY"


async def test_message_attachment_timeout_does_not_log_in_again(monkeypatch) -> None:
    """Hitting the download deadline is not a dead session: no relogin, no
    second download."""
    monkeypatch.setattr("librus_synergia.client.DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(ATTACHMENT, json_data={"data": {"downloadLink": SANDBOX_LINK}})
            mocked.get(SANDBOX_LINK, text_data="<html>wait</html>")
            mocked.get(
                f"{SANDBOX_LINK}/get",
                text_data="<html>wait</html>",
                headers={"Content-Type": "text/html"},
            )
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusConnectionError, match="attachment-55"):
                await librus.download_attachment("55", "99")

            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1
            assert mocked.get_calls[ATTACHMENT] == 1


async def test_message_attachment_odd_answer_does_not_log_in_again() -> None:
    """An answer without an error status (here a link outside the sandbox)
    can't be fixed by logging in again."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(
                ATTACHMENT, json_data={"data": {"downloadLink": "https://example.invalid/X"}}
            )
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusUnexpectedResponseError):
                await librus.download_attachment("55", "99")

            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1
            assert mocked.get_calls[ATTACHMENT] == 1


async def test_message_attachment_rejected_session_bootstraps_again() -> None:
    """A rejected Wiadomości session is first set up again on its own (one
    request) - no password login."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get_sequence(
                ATTACHMENT,
                {"status": 401, "text_data": ""},
                {"json_data": {"data": {"downloadLink": SANDBOX_LINK}}},
            )
            mocked.get(SANDBOX_LINK, text_data="<html>wait</html>")
            mocked.get(
                f"{SANDBOX_LINK}/get", body=b"%PDF", headers={"Content-Type": "application/pdf"}
            )
            librus = Librus("1234567u", "pw", session=session)

            file = await librus.download_attachment("55", "99")

            assert file is not None and file.content == b"%PDF"
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 2


async def test_message_attachment_still_rejected_logs_in_again() -> None:
    """When setting up the Wiadomości session again doesn't help, one
    password login (and a bootstrap for it) follows."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get_sequence(
                ATTACHMENT,
                {"status": 401, "text_data": ""},
                {"status": 401, "text_data": ""},
                {"json_data": {"data": {"downloadLink": SANDBOX_LINK}}},
            )
            mocked.get(SANDBOX_LINK, text_data="<html>wait</html>")
            mocked.get(
                f"{SANDBOX_LINK}/get", body=b"%PDF", headers={"Content-Type": "application/pdf"}
            )
            librus = Librus("1234567u", "pw", session=session)

            file = await librus.download_attachment("55", "99")

            assert file is not None and file.content == b"%PDF"
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 3


async def test_messages_rejected_three_times_is_raised() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(f"{MESSAGES_BASE_URL}/inbox/messages", status=401, text_data="")
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusSessionExpiredError):
                await librus.messages()

            assert mocked.get_calls[f"{MESSAGES_BASE_URL}/inbox/messages"] == 3
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2


async def test_download_deadline_covers_the_login(monkeypatch) -> None:
    """The whole high-level download - logins included - stops at the
    deadline."""
    monkeypatch.setattr("librus_synergia.client.DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    librus = Librus("1234567u", "pw", session=aiohttp.ClientSession())

    async def slow_login(*, force: bool = False) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(librus, "login", slow_login)
    try:
        with pytest.raises(LibrusConnectionError, match="within 0.05 s"):
            await librus.download_school_file("/pliki_szkoly/pobierz/5")
    finally:
        await librus.client._session.close()


@pytest.mark.parametrize("status", [403, 404])
async def test_partial_grades_refusal_is_asked_again_after_a_day(clock, status: int) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            mocked.post(PARTIAL_GRADES, status=status, json_data={"Status": "Error"})
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.partial_grades() == []
            clock.now += RECHECK_SECONDS - 1
            assert await librus.partial_grades() == []
            assert mocked.post_calls[PARTIAL_GRADES] == 1
            clock.now += 2
            assert await librus.partial_grades() == []
            assert mocked.post_calls[PARTIAL_GRADES] == 2


async def test_child_identifier_refusal_is_kept_for_a_day(clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                TOKEN_INFO,
                {"status": 403, "json_data": {}},
                {"json_data": {"UserIdentifier": "LID-AUTH-USER-1"}},
            )
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.student_identifier() is None
            assert await librus.student_identifier() is None
            assert mocked.get_calls[TOKEN_INFO] == 1
            clock.now += RECHECK_SECONDS
            assert await librus.student_identifier() == "LID-AUTH-USER-2"
            assert mocked.get_calls[TOKEN_INFO] == 2


async def test_grading_system_refusal_gives_the_default_for_a_day(clock) -> None:
    grading_url = f"{DATA_BASE_URL}/GradingSystem"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                grading_url,
                {"status": 404, "json_data": {}},
                {"json_data": {"plusValue": 0.3}},
            )
            librus = Librus("1234567u", "pw", session=session)
            assert (await librus.grading_system()).plus_value == 0.5
            assert (await librus.grading_system()).plus_value == 0.5
            assert mocked.get_calls[grading_url] == 1
            clock.now += RECHECK_SECONDS
            assert (await librus.grading_system()).plus_value == 0.3
            assert mocked.get_calls[grading_url] == 2


async def test_grading_system_server_error_is_raised() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(f"{DATA_BASE_URL}/GradingSystem", status=500, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusUnexpectedResponseError):
                await librus.grading_system()


async def test_auth_subjects_are_kept_for_a_day(clock) -> None:
    subjects_url = f"{DATA_BASE_URL}/Auth/Subjects"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(TOKEN_INFO, json_data={"UserIdentifier": "LID-AUTH-USER-1"})
            mocked.get(
                USER_INFO, json_data={"IdentifierOfStudentAssignedWithUser": "LID-AUTH-USER-2"}
            )
            mocked.post(
                PARTIAL_GRADES,
                json_data={"data": [{"gradeId": 1, "subjectId": "LID-S", "scaleValue": {}}]},
            )
            mocked.get(
                subjects_url,
                json_data={"data": [{"identifier": "LID-S", "numericIdentifier": 9}]},
            )
            librus = Librus("1234567u", "pw", session=session)
            for _ in range(2):
                (grade,) = await librus.partial_grades()
                assert grade.subject_id == 9
            assert mocked.get_calls[subjects_url] == 1
            clock.now += RECHECK_SECONDS
            await librus.partial_grades()
            assert mocked.get_calls[subjects_url] == 2
