"""Messages without needless password logins, rechecked "no messages module"
answers, `session_data` after `close()`, kindergarten searches that couldn't
finish, request and download limits, kept comment/skill lookups, reference
data that failed to load."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import aiohttp
import pytest

from librus_synergia import Librus, cli
from librus_synergia.client import LibrusSessionData
from librus_synergia.const import (
    API_OAUTH_AUTHORIZATION_URL,
    DATA_BASE_URL,
    MESSAGES_BASE_URL,
    MESSAGES_BOOTSTRAP_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
)
from librus_synergia.exceptions import (
    LibrusError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)
from librus_synergia.librus import (
    KINDERGARTEN_RETRY_SECONDS,
    MESSAGES_DENIED_QUICK_SECONDS,
    MESSAGES_DENIED_RECHECK_SECONDS,
    MESSAGES_RELOGIN_BACKOFF_SECONDS,
    RECHECK_SECONDS,
)

from .helpers import MockedSession, mock_successful_login

INBOX = f"{MESSAGES_BASE_URL}/inbox/messages"
DENIED = "<html>Brak dostępu</html>"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("librus_synergia.librus._now", fake)
    return fake


def _url(endpoint: str) -> str:
    return f"{DATA_BASE_URL}/{endpoint}"


def _logins(mocked: MockedSession) -> int:
    return mocked.post_calls.get(API_OAUTH_AUTHORIZATION_URL, 0)


# --- 1: Wiadomości errors and password logins ------------------------------------


async def test_messages_server_error_never_logs_in_again() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(INBOX, status=500, text_data="")
            librus = Librus("1234567u", "pw", session=session)

            for _ in range(3):
                with pytest.raises(LibrusUnexpectedResponseError):
                    await librus.messages()

            assert _logins(mocked) == 1  # only the first login
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1
            assert mocked.get_calls[INBOX] == 3


async def test_messages_403_bootstraps_once_but_never_logs_in() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(INBOX, status=403, text_data="")
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusSessionExpiredError):
                await librus.messages()

            assert _logins(mocked) == 1
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 2
            assert mocked.get_calls[INBOX] == 2


async def test_messages_other_4xx_bootstraps_once_but_never_logs_in() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(INBOX, status=400, text_data="")
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusUnexpectedResponseError):
                await librus.messages()

            assert _logins(mocked) == 1
            assert mocked.get_calls[INBOX] == 2


async def test_relogin_that_did_not_help_is_not_repeated_for_a_while(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(INBOX, status=401, text_data="")
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusSessionExpiredError):
                await librus.messages()
            assert _logins(mocked) == 2  # first login + one forced

            with pytest.raises(LibrusSessionExpiredError):
                await librus.messages()
            assert _logins(mocked) == 2  # rebootstrap only, no password

            clock.now += MESSAGES_RELOGIN_BACKOFF_SECONDS
            with pytest.raises(LibrusSessionExpiredError):
                await librus.messages()
            assert _logins(mocked) == 3


async def test_relogin_that_helped_keeps_relogins_allowed() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get_sequence(
                INBOX,
                {"status": 401, "text_data": ""},
                {"status": 401, "text_data": ""},
                {"json_data": {"data": []}},
                {"status": 401, "text_data": ""},
                {"status": 401, "text_data": ""},
                {"json_data": {"data": []}},
            )
            librus = Librus("1234567u", "pw", session=session)

            assert await librus.messages() == []
            assert await librus.messages() == []
            assert _logins(mocked) == 3


# --- 2: "Brak dostępu" is asked again -----------------------------------------------


async def test_denied_bootstrap_is_asked_again_without_a_new_login(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                MESSAGES_BOOTSTRAP_URL,
                {"text_data": DENIED},
                {"text_data": "<html>ok</html>"},
            )
            mocked.get(INBOX, json_data={"data": [{"messageId": "1", "topic": "Hej"}]})
            librus = Librus("1234567u", "pw", session=session)

            assert await librus.messages() == []
            assert await librus.messages() == []
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1

            clock.now += MESSAGES_DENIED_QUICK_SECONDS
            (message,) = await librus.messages()
            assert message.topic == "Hej"
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 2
            assert _logins(mocked) == 1


async def test_denied_bootstrap_is_asked_hourly_after_three_quick_rechecks(
    clock: _Clock,
) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data=DENIED)
            librus = Librus("1234567u", "pw", session=session)

            await librus.messages()
            for _ in range(3):
                clock.now += MESSAGES_DENIED_QUICK_SECONDS
                await librus.messages()
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 4

            clock.now += MESSAGES_DENIED_QUICK_SECONDS
            await librus.messages()
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 4

            clock.now += MESSAGES_DENIED_RECHECK_SECONDS
            await librus.messages()
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 5


async def test_denied_bootstrap_right_after_a_401_logs_in_again() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                MESSAGES_BOOTSTRAP_URL,
                {"text_data": "<html>ok</html>"},
                {"text_data": DENIED},
                {"text_data": "<html>ok</html>"},
            )
            mocked.get_sequence(
                INBOX,
                {"status": 401, "text_data": ""},
                {"json_data": {"data": [{"messageId": "1", "topic": "Hej"}]}},
            )
            librus = Librus("1234567u", "pw", session=session)

            (message,) = await librus.messages()

            assert message.topic == "Hej"
            assert _logins(mocked) == 2
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 3


# --- 3: session_data after close() --------------------------------------------------


async def test_session_data_after_close_keeps_the_cookies_without_a_new_session() -> None:
    librus = Librus("1234567u", "pw")
    librus.client.import_session(
        LibrusSessionData(
            cookies=[{"name": "oauth_token", "value": "abc", "domain": "synergia.librus.pl"}],
            logged_in_at=123.0,
        )
    )
    await librus.close()

    data = librus.session_data

    assert librus._session is None and librus._client is None
    assert data.logged_in_at == 123.0
    assert {"name": "oauth_token", "value": "abc", "domain": "synergia.librus.pl"} in data.cookies


async def test_session_data_before_any_request_does_not_open_a_session() -> None:
    saved = LibrusSessionData(cookies=[], logged_in_at=5.0)
    librus = Librus("1234567u", "pw", session_data=saved)
    assert librus.session_data is saved
    assert librus._session is None
    assert Librus("1234567u", "pw").session_data == LibrusSessionData()


# --- 4: a kindergarten search that couldn't finish ---------------------------------


async def test_unfinished_kindergarten_search_fails_the_timetable(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Timetables"), status=403, json_data={})
            mocked.get(_url("Me"), status=500, json_data={})
            mocked.get(_url("Auth/TokenInfo"), status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusUnexpectedResponseError):
                await librus.timetable(date(2026, 9, 7))
            assert librus._kindergarten_checked_at is None
            assert await librus.kindergartener_id() is None  # never raises

            # Not asked again right away...
            with pytest.raises(LibrusUnexpectedResponseError):
                await librus.timetable(date(2026, 9, 7))
            assert mocked.get_calls[_url("Me")] == 1

            # ...but soon, and a finished search is then remembered.
            clock.now += KINDERGARTEN_RETRY_SECONDS
            mocked.get(_url("Me"), json_data={"Me": {"User": {}, "Account": {}}})
            assert await librus.timetable(date(2026, 9, 7)) == {}
            assert librus._kindergarten_checked_at is not None


async def test_unfinished_kindergarten_search_marks_the_timetable_failed() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            for endpoint, payload in (
                ("Grades", {"Grades": []}),
                ("Notes", {"Notes": []}),
                ("Attendances", {"Attendances": []}),
                ("Attendances/Types", {"Types": []}),
                ("HomeWorks", {"HomeWorks": []}),
                ("SchoolNotices", {"SchoolNotices": []}),
                ("Subjects", {"Subjects": []}),
            ):
                mocked.get(_url(endpoint), json_data=payload)
            mocked.get(_url("Timetables"), status=403, json_data={})
            mocked.get(_url("Me"), status=500, json_data={})
            mocked.get(_url("Auth/TokenInfo"), status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)

            data = await librus.fetch_changes(include_messages=False)

    assert "timetable" in data.failed_sections


async def test_cancelled_kindergarten_search_is_not_remembered() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Timetables"), status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            started = asyncio.Event()

            async def hanging_me() -> dict[str, Any]:
                started.set()
                await asyncio.sleep(60)
                return {}

            librus.client.async_get_me = hanging_me  # type: ignore[method-assign]
            task = asyncio.create_task(librus.timetable(date(2026, 9, 7)))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert librus._kindergarten_checked_at is None
            assert librus._kindergarten_failure is None


# --- 5: request limit --------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1])
def test_request_limit_must_be_at_least_one(limit: int) -> None:
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        Librus("1234567u", "pw", max_concurrent_requests=limit)


# --- 10: kept comment texts and skill names --------------------------------------------


def _grades(*comment_ids: int) -> dict[str, Any]:
    return {
        "Grades": [
            {"Id": n, "Grade": "5", "Subject": {"Id": 1}, "Comments": [{"Id": c}]}
            for n, c in enumerate(comment_ids, start=1)
        ]
    }


async def test_grade_comments_are_kept_until_a_new_id_appears(clock: _Clock) -> None:
    comments = {"Comments": [{"Id": 3, "Text": "Brawo"}, {"Id": 4, "Text": "Super"}]}
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Grades"), json_data=_grades(3))
            mocked.get(_url("Grades/Comments"), json_data=comments)
            librus = Librus("1234567u", "pw", session=session)

            (grade,) = await librus.grades()
            await librus.grades()
            assert grade.comments == ["Brawo"]
            assert mocked.get_calls[_url("Grades/Comments")] == 1

            # An id the kept copy has never been asked for: fetched again.
            mocked.get(_url("Grades"), json_data=_grades(3, 5))
            await librus.grades()
            assert mocked.get_calls[_url("Grades/Comments")] == 2
            # Still unknown to Librus: not fetched again for it.
            await librus.grades()
            assert mocked.get_calls[_url("Grades/Comments")] == 2

            clock.now += RECHECK_SECONDS
            await librus.grades()
            assert mocked.get_calls[_url("Grades/Comments")] == 3


async def test_failed_comment_refresh_keeps_the_old_texts(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Grades"), json_data=_grades(3))
            mocked.get(_url("Grades/Comments"), json_data={"Comments": [{"Id": 3, "Text": "A"}]})
            librus = Librus("1234567u", "pw", session=session)
            await librus.grades()

            clock.now += RECHECK_SECONDS
            mocked.get(_url("Grades/Comments"), status=500, json_data={})
            (grade,) = await librus.grades()

    assert grade.comments == ["A"]


async def test_fetch_changes_never_fetches_comment_texts() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            for endpoint, payload in (
                ("Grades", _grades(3)),
                ("Notes", {"Notes": []}),
                ("Attendances", {"Attendances": []}),
                ("Attendances/Types", {"Types": []}),
                ("Timetables", {"Timetable": {}}),
                ("HomeWorks", {"HomeWorks": []}),
                ("SchoolNotices", {"SchoolNotices": []}),
                ("Subjects", {"Subjects": []}),
            ):
                mocked.get(_url(endpoint), json_data=payload)
            librus = Librus("1234567u", "pw", session=session)

            data = await librus.fetch_changes(include_messages=False)

            assert _url("Grades/Comments") not in mocked.get_calls
    (grade,) = data.grades
    assert grade.comments == []


async def test_descriptive_skills_are_kept() -> None:
    grades = {"Grades": [{"Id": 1, "Map": "6", "Skill": {"Id": 7}, "Subject": {"Id": 2}}]}
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("DescriptiveGrades"), json_data=grades)
            mocked.get(
                _url("DescriptiveGrades/Skills"), json_data={"Skills": [{"Id": 7, "Name": "Śpiew"}]}
            )
            librus = Librus("1234567u", "pw", session=session)

            (grade,) = await librus.descriptive_grades()
            await librus.descriptive_grades()

            assert grade.skill == "Śpiew"
            assert mocked.get_calls[_url("DescriptiveGrades/Skills")] == 1


async def test_more_lookups_are_kept_with_cache_reference_data() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("SchoolFreeDays"), json_data={"SchoolFreeDays": []})
            mocked.get(_url("ClassFreeDays"), json_data={"ClassFreeDays": []})
            mocked.get(_url("TimetableEntries"), json_data={"TimetableEntries": []})
            mocked.get(_url("Lessons"), json_data={"Lessons": []})
            mocked.get(_url("BaseTextGrades"), json_data={"Grades": [{"Id": 1, "Grade": "ok"}]})
            mocked.get(_url("TextGrades/Categories"), json_data={"Categories": []})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)

            for _ in range(2):
                await librus.free_days()
                await librus.standing_timetable()
                await librus.text_grades()

            for endpoint in (
                "SchoolFreeDays",
                "ClassFreeDays",
                "TimetableEntries",
                "TextGrades/Categories",
            ):
                assert mocked.get_calls[_url(endpoint)] == 1, endpoint
            assert mocked.get_calls[_url("BaseTextGrades")] == 2


async def test_point_grade_categories_are_kept_with_cache_reference_data() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Units"), json_data={})
            mocked.get(_url("PointGrades"), json_data={"Grades": [{"Id": 1, "Grade": "8"}]})
            mocked.get(_url("PointGrades/Categories"), json_data={"Categories": []})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)

            await librus.point_grades()
            await librus.point_grades()

            assert mocked.get_calls[_url("PointGrades/Categories")] == 1


async def test_cli_watch_keeps_reference_data(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)
            self.session_data = LibrusSessionData()

        async def __aenter__(self) -> _Recorder:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    async def fake_watch(args: Any, librus: Any) -> int:
        return 0

    monkeypatch.setattr(cli, "Librus", _Recorder)
    monkeypatch.setattr(cli, "_watch", fake_watch)
    args = cli.argparse.Namespace(watch=True, session=None)

    assert await cli._run(args, "1234567u", "pw") == 0
    assert seen["cache_reference_data"] is True


# --- 11: a reference load that failed isn't retried in the same call ---------------------


async def test_failed_reference_load_is_not_asked_again_in_the_same_call(
    clock: _Clock,
) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            for endpoint, payload in (
                ("Grades", {"Grades": [{"Id": 1, "Grade": "5", "Subject": {"Id": 99}}]}),
                ("Notes", {"Notes": []}),
                ("Attendances", {"Attendances": []}),
                ("Attendances/Types", {"Types": []}),
                ("Timetables", {"Timetable": {}}),
                ("HomeWorks", {"HomeWorks": []}),
                ("SchoolNotices", {"SchoolNotices": []}),
            ):
                mocked.get(_url(endpoint), json_data=payload)
            mocked.get_sequence(
                _url("Subjects"),
                {"json_data": {"Subjects": [{"Id": 1, "Name": "Mat"}]}},
                {"status": 500, "json_data": {}},
            )
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.subjects()

            clock.now += RECHECK_SECONDS
            data = await librus.fetch_changes(include_messages=False)

            assert data.subjects == {1: "Mat"}  # the expired copy
            assert mocked.get_calls[_url("Subjects")] == 2  # not a third time


# --- 12: downloads have their own limit ---------------------------------------------------


async def test_downloads_have_their_own_small_limit() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Notes"), json_data={"Notes": []})
            librus = Librus("1234567u", "pw", session=session, max_concurrent_requests=1)
            release = asyncio.Event()
            running = peak = 0

            async def slow_download(attachment_id: str) -> Any:
                nonlocal running, peak
                running += 1
                peak = max(peak, running)
                await release.wait()
                running -= 1
                return attachment_id

            librus.client.async_download_homework_attachment = slow_download  # type: ignore[method-assign]
            downloads = [
                asyncio.create_task(librus.download_homework_attachment(str(n))) for n in range(4)
            ]
            for _ in range(10):
                await asyncio.sleep(0)

            # Downloads waiting on the sandbox don't block ordinary requests,
            # even with a single request slot.
            assert await asyncio.wait_for(librus.notes(), 1) == []
            assert peak == 2

            release.set()
            assert await asyncio.gather(*downloads) == ["0", "1", "2", "3"]


async def test_kindergarten_search_error_is_a_librus_error() -> None:
    """The error `timetable()` raises after an unfinished search is the
    request's own `LibrusError`, so callers' `except LibrusError` holds."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Timetables"), status=403, json_data={})
            mocked.get(_url("Me"), status=500, json_data={})
            mocked.get(_url("Auth/TokenInfo"), status=500, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusError):
                await librus.timetable()
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1


# --- 13: a kindergarten search never costs a password login ------------------------------

CHILD_LID = "LID-AUTH-USER-1-CHILD"
KG_TIMETABLE = (
    f"https://synergia.librus.pl/gateway/ms/kindergartens/timetable/kindergarteners/{CHILD_LID}"
)


def _mock_search_start(mocked: MockedSession) -> None:
    """A Timetables 403 that starts a search with one candidate child."""
    mocked.get(_url("Timetables"), status=403, json_data={})
    mocked.get(_url("Me"), json_data={"Me": {"User": {"Id": CHILD_LID}, "Account": {}}})
    mocked.get(_url("Auth/TokenInfo"), status=403, json_data={})


async def test_kindergarten_service_401_is_an_answer(clock: _Clock) -> None:
    """A regular school (timetable not published -> 403) whose kindergarten
    probe keeps answering 401: one search, no password login, remembered."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_search_start(mocked)
            mocked.get(KG_TIMETABLE, status=401, json_data={})
            librus = Librus("1234567u", "pw", session=session)

            assert await librus.timetable(date(2026, 9, 7)) == {}
            assert librus._kindergarten_checked_at is not None
            clock.now += KINDERGARTEN_RETRY_SECONDS
            assert await librus.timetable(date(2026, 9, 7)) == {}

            assert _logins(mocked) == 1  # only the first, ordinary login
            assert mocked.get_calls[KG_TIMETABLE] == 1
            assert mocked.get_calls[_url("Me")] == 1


@pytest.mark.parametrize("status", [500, 503])
async def test_kindergarten_service_server_error_is_not_an_answer(status: int) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_search_start(mocked)
            mocked.get(KG_TIMETABLE, status=status, json_data={})
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusError):
                await librus.timetable(date(2026, 9, 7))

            assert librus._kindergarten_checked_at is None
            assert _logins(mocked) == 1
            assert mocked.get_calls[KG_TIMETABLE] == 1  # no retry


async def test_dead_main_session_in_a_search_is_left_to_data_calls() -> None:
    """A dead-session 401 on `Me` during a search isn't "nothing found" and
    doesn't log in; the next ordinary call logs in again as usual."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Timetables"), status=403, json_data={})
            mocked.get(_url("Me"), status=401, json_data={})
            mocked.get(_url("Auth/TokenInfo"), status=401, json_data={})
            mocked.get_sequence(
                _url("Notes"), {"status": 401, "json_data": {}}, {"json_data": {"Notes": []}}
            )
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusSessionExpiredError):
                await librus.timetable(date(2026, 9, 7))
            assert librus._kindergarten_checked_at is None
            assert _logins(mocked) == 1

            assert await librus.notes() == []
            assert _logins(mocked) == 2


# --- 14: a refused cached module isn't asked on every call -------------------------------


async def test_refused_point_grade_categories_are_remembered(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Units"), json_data={})
            mocked.get(_url("PointGrades"), json_data={"Grades": [{"Id": 1, "Grade": "8"}]})
            mocked.get(_url("PointGrades/Categories"), json_data={"Categories": []})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.point_grades()

            # The kept copy expires, then Librus refuses the module.
            clock.now += RECHECK_SECONDS
            mocked.get(_url("PointGrades/Categories"), status=403, json_data={})
            for _ in range(3):
                assert len(await librus.point_grades()) == 1

            assert mocked.get_calls[_url("PointGrades/Categories")] == 2


async def test_expired_point_grade_categories_survive_a_server_error(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Units"), json_data={})
            mocked.get(_url("PointGrades"), json_data={"Grades": [{"Id": 1, "Grade": "8"}]})
            mocked.get(_url("PointGrades/Categories"), json_data={"Categories": []})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.point_grades()

            clock.now += RECHECK_SECONDS
            mocked.get(_url("PointGrades/Categories"), status=500, json_data={})
            for _ in range(2):
                assert len(await librus.point_grades()) == 1

            # A transient error isn't a refusal: asked again each time.
            assert mocked.get_calls[_url("PointGrades/Categories")] == 3


async def test_refused_text_grade_categories_are_remembered(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("BaseTextGrades"), json_data={"Grades": [{"Id": 1, "Grade": "ok"}]})
            mocked.get(_url("TextGrades/Categories"), json_data={"Categories": []})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.text_grades()

            clock.now += RECHECK_SECONDS
            mocked.get(_url("TextGrades/Categories"), status=403, json_data={})
            for _ in range(3):
                assert len(await librus.text_grades()) == 1

            assert mocked.get_calls[_url("TextGrades/Categories")] == 2
