"""The `Librus` facade: shared relogins, remembered login failures,
cancelled siblings, request limits, caches and the change-only fetch."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from librus_synergia import Librus
from librus_synergia._dates import school_today
from librus_synergia.const import (
    API_OAUTH_AUTHORIZATION_URL,
    DATA_BASE_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
)
from librus_synergia.exceptions import (
    LibrusConnectionError,
    LibrusError,
    LibrusInvalidCredentialsError,
    LibrusSessionExpiredError,
)
from librus_synergia.librus import (
    LOGIN_FAILURE_BACKOFF_SECONDS,
    RECHECK_SECONDS,
    _gather_all,
)

from .helpers import MockedSession, mock_successful_login


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


def _mock_change_endpoints(mocked: MockedSession) -> None:
    """Everything `fetch_changes(include_messages=False)` asks for."""
    mocked.get(_url("Grades"), json_data={"Grades": []})
    mocked.get(_url("Notes"), json_data={"Notes": []})
    mocked.get(_url("Attendances"), json_data={"Attendances": []})
    mocked.get(_url("Attendances/Types"), json_data={"Types": []})
    mocked.get(_url("Timetables"), json_data={"Timetable": {}})
    mocked.get(_url("HomeWorks"), json_data={"HomeWorks": []})
    mocked.get(_url("SchoolNotices"), json_data={"SchoolNotices": []})
    mocked.get(_url("Subjects"), json_data={"Subjects": []})


# --- B2: relogin stampede, remembered login failure --------------------------


async def test_requests_rejected_together_share_one_relogin() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            librus = Librus("1234567u", "pw", session=session)
            attempts: dict[str, int] = {}

            def fetch(name: str):
                async def run() -> str:
                    attempts[name] = attempts.get(name, 0) + 1
                    await asyncio.sleep(0)  # let the other request start too
                    if attempts[name] == 1:
                        raise LibrusSessionExpiredError("dead", status_code=401)
                    return name

                return run

            results = await asyncio.gather(*(librus._call(fetch(n)) for n in ("a", "b", "c")))

            assert results == ["a", "b", "c"]
            # The first login, then ONE relogin for all three rejections.
            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 2


async def test_failed_login_is_not_retried_right_away(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.post(API_OAUTH_AUTHORIZATION_URL, json_data={"status": "error"})
            librus = Librus("1234567u", "pw", session=session)

            with pytest.raises(LibrusInvalidCredentialsError):
                await librus.me()
            with pytest.raises(LibrusInvalidCredentialsError):
                await librus.notes()
            assert mocked.post_calls[API_OAUTH_AUTHORIZATION_URL] == 1

            clock.now += LOGIN_FAILURE_BACKOFF_SECONDS
            with pytest.raises(LibrusInvalidCredentialsError):
                await librus.me()
            assert mocked.post_calls[API_OAUTH_AUTHORIZATION_URL] == 2


async def test_failed_relogin_is_shared_with_waiting_requests(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            librus = Librus("1234567u", "pw", session=session)
            await librus.login()
            mocked.post(API_OAUTH_AUTHORIZATION_URL, json_data={"status": "error"})

            async def rejected() -> str:
                await asyncio.sleep(0)
                raise LibrusSessionExpiredError("dead", status_code=401)

            results = await asyncio.gather(
                librus._call(rejected), librus._call(rejected), return_exceptions=True
            )

            assert all(isinstance(r, LibrusInvalidCredentialsError) for r in results)
            assert mocked.post_calls[API_OAUTH_AUTHORIZATION_URL] == 2  # first login + one


# --- B3: no orphaned requests --------------------------------------------------


async def test_gather_cancels_the_rest_and_raises_the_error_itself() -> None:
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def failing() -> None:
        await asyncio.sleep(0)
        raise LibrusConnectionError("down")

    with pytest.raises(LibrusConnectionError, match="down"):
        await _gather_all(slow(), failing())
    assert cancelled.is_set()


async def test_gather_returns_results_in_order() -> None:
    async def value(v: int) -> int:
        await asyncio.sleep(0)
        return v

    assert await _gather_all(value(1), value(2), value(3)) == [1, 2, 3]


# --- B5: failed sections ---------------------------------------------------


async def test_fetch_changes_marks_failed_sections() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_change_endpoints(mocked)
            mocked.get(_url("Grades"), status=502, text_data="bad gateway")
            librus = Librus("1234567u", "pw", session=session)

            data = await librus.fetch_changes(include_messages=False)

    assert "grades" in data.failed_sections
    assert {"messages", "unread_messages_by_mailbox"} <= data.failed_sections
    assert "notes" not in data.failed_sections


# --- B10: Wiadomości bootstrap after a login -----------------------------------


async def test_messages_bootstrap_again_after_a_password_login() -> None:
    from librus_synergia.const import MESSAGES_BASE_URL, MESSAGES_BOOTSTRAP_URL

    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(f"{MESSAGES_BASE_URL}/inbox/messages", json_data={"data": []})
            librus = Librus("1234567u", "pw", session=session)
            await librus.messages()
            await librus.messages()
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1
            await librus.login(force=True)
            await librus.messages()
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 2


async def test_concurrent_message_calls_bootstrap_once() -> None:
    from librus_synergia.const import MESSAGES_BASE_URL, MESSAGES_BOOTSTRAP_URL

    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
            mocked.get(f"{MESSAGES_BASE_URL}/inbox/messages", json_data={"data": []})
            mocked.get(f"{MESSAGES_BASE_URL}/outbox/messages", json_data={"data": []})
            librus = Librus("1234567u", "pw", session=session)
            await asyncio.gather(librus.messages(), librus.messages("outbox"))
            assert mocked.get_calls[MESSAGES_BOOTSTRAP_URL] == 1


# --- B11: today in Poland ------------------------------------------------------


def test_school_today_is_polish_time() -> None:
    assert school_today() == datetime.now(ZoneInfo("Europe/Warsaw")).date()


# --- B13: kindergarten search repeated after a day -------------------------------


async def test_kindergarten_search_that_found_nothing_is_repeated_after_a_day(
    clock: _Clock,
) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Timetables"), status=403, json_data={})
            mocked.get(_url("Me"), json_data={"Me": {"User": {}, "Account": {}}})
            mocked.get(_url("Auth/TokenInfo"), status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)

            assert await librus.timetable() == {}
            assert await librus.timetable() == {}
            assert mocked.get_calls[_url("Me")] == 1

            clock.now += RECHECK_SECONDS
            assert await librus.timetable() == {}
            assert mocked.get_calls[_url("Me")] == 2


# --- O1: at most six requests at a time --------------------------------------------


async def test_no_more_than_six_requests_at_once() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            librus = Librus("1234567u", "pw", session=session)
            running = peak = 0

            async def fetch() -> None:
                nonlocal running, peak
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.01)
                running -= 1

            await asyncio.gather(*(librus._call(fetch) for _ in range(20)))
    assert peak == 6


async def test_own_session_limits_connections_per_host() -> None:
    async with Librus("1234567u", "pw") as librus:
        connector = librus.client._session.connector
        assert isinstance(connector, aiohttp.TCPConnector)
        assert connector.limit_per_host == 6


# --- O2: reference-data cache --------------------------------------------------------


async def test_reference_cache_is_off_by_default() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Subjects"), json_data={"Subjects": [{"Id": 1, "Name": "Mat"}]})
            librus = Librus("1234567u", "pw", session=session)
            await librus.subjects()
            await librus.subjects()
            assert mocked.get_calls[_url("Subjects")] == 2


async def test_reference_cache_keeps_lookups_for_the_ttl(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Subjects"), json_data={"Subjects": [{"Id": 1, "Name": "Mat"}]})
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            assert await librus.subjects() == {1: "Mat"}
            await librus.subjects()
            assert mocked.get_calls[_url("Subjects")] == 1
            clock.now += RECHECK_SECONDS
            await librus.subjects()
            assert mocked.get_calls[_url("Subjects")] == 2


async def test_reference_cache_falls_back_to_the_old_copy_on_failure(clock: _Clock) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get_sequence(
                _url("Subjects"),
                {"json_data": {"Subjects": [{"Id": 1, "Name": "Mat"}]}},
                {"status": 502, "text_data": ""},
            )
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.subjects()
            clock.now += RECHECK_SECONDS
            assert await librus.subjects() == {1: "Mat"}


async def test_unknown_subject_fetches_the_cached_lookup_again() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_change_endpoints(mocked)
            mocked.get_sequence(
                _url("Subjects"),
                {"json_data": {"Subjects": [{"Id": 1, "Name": "Mat"}]}},
                {"json_data": {"Subjects": [{"Id": 1, "Name": "Mat"}, {"Id": 9, "Name": "Bio"}]}},
            )
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            await librus.fetch_changes(include_messages=False)
            assert mocked.get_calls[_url("Subjects")] == 1

            # A grade in a subject the cached list doesn't know.
            mocked.get(
                _url("Grades"),
                json_data={"Grades": [{"Id": 5, "Grade": "5", "Subject": {"Id": 9}}]},
            )
            data = await librus.fetch_changes(include_messages=False)
            assert mocked.get_calls[_url("Subjects")] == 2
            assert data.subjects[9] == "Bio"

            # Known now: no further fetch.
            await librus.fetch_changes(include_messages=False)
            assert mocked.get_calls[_url("Subjects")] == 2


async def test_id_librus_never_knows_does_not_refetch_every_time() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_change_endpoints(mocked)
            mocked.get(_url("Subjects"), json_data={"Subjects": [{"Id": 1, "Name": "Mat"}]})
            mocked.get(
                _url("Grades"),
                json_data={"Grades": [{"Id": 5, "Grade": "5", "Subject": {"Id": 9}}]},
            )
            librus = Librus("1234567u", "pw", session=session, cache_reference_data=True)
            for _ in range(4):
                await librus.fetch_changes(include_messages=False)
            # The first fetch, one early refetch, then no more.
            assert mocked.get_calls[_url("Subjects")] == 2


# --- O3: refusals and the point-grades switch ----------------------------------------


async def test_point_grades_switched_off_by_the_school_are_not_requested() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(
                _url("Units"),
                json_data={"Units": [{"GradesSettings": {"PointGradesEnabled": False}}]},
            )
            librus = Librus("1234567u", "pw", session=session)
            assert await librus.point_grades() == []
            assert _url("PointGrades") not in mocked.get_calls


@pytest.mark.parametrize(
    ("method", "endpoint"),
    [
        ("justifications", "Justifications"),
        ("school_trips", "SchoolTrips"),
        ("school_files", "SchoolFiles"),
        ("parent_teacher_conferences", "ParentTeacherConferences"),
        ("text_grades", "BaseTextGrades"),
        ("behaviour_grades", "BehaviourGrades/Points"),
    ],
)
async def test_refused_module_is_not_asked_again_for_a_day(
    clock: _Clock, method: str, endpoint: str
) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url(endpoint), status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            assert await getattr(librus, method)() == []
            assert await getattr(librus, method)() == []
            assert mocked.get_calls[_url(endpoint)] == 1
            clock.now += RECHECK_SECONDS
            await getattr(librus, method)()
            assert mocked.get_calls[_url(endpoint)] == 2


async def test_module_server_error_is_still_raised() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("SchoolTrips"), status=500, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusError):
                await librus.school_trips()


# --- O6: fetch_changes asks only for what changes need ------------------------------


async def test_fetch_changes_asks_only_for_what_change_tracking_needs() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            _mock_change_endpoints(mocked)
            librus = Librus("1234567u", "pw", session=session)
            data = await librus.fetch_changes(include_messages=False)
    data_calls = {url for url in mocked.get_calls if url.startswith(DATA_BASE_URL)}
    assert data_calls == {
        _url(e)
        for e in (
            "Grades",
            "Notes",
            "Attendances",
            "Attendances/Types",
            "Timetables",
            "HomeWorks",
            "SchoolNotices",
            "Subjects",
        )
    }
    assert data.failed_sections == {"messages", "unread_messages_by_mailbox"}


# --- O10: comment lookups only when needed ---------------------------------------------


async def test_grades_without_comments_skip_the_comments_list() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(_url("Grades"), json_data={"Grades": [{"Id": 1, "Grade": "5"}]})
            librus = Librus("1234567u", "pw", session=session)
            (grade,) = await librus.grades()
            assert _url("Grades/Comments") not in mocked.get_calls
    assert grade.comments == []


async def test_failed_comment_lookup_keeps_the_grades() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.get(
                _url("Grades"), json_data={"Grades": [{"Id": 1, "Grade": "5", "Comments": [3]}]}
            )
            mocked.get(_url("Grades/Comments"), status=500, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            (grade,) = await librus.grades()
    assert grade.value == "5"


# --- tzdata missing: a warning, once -------------------------------------------------


def test_missing_time_zone_data_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from zoneinfo import ZoneInfoNotFoundError

    from librus_synergia import _dates

    def no_zone(name: str) -> ZoneInfo:
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(_dates, "ZoneInfo", no_zone)
    _dates._school_zone.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="librus_synergia._dates"):
            assert school_today() == datetime.now().date()
            school_today()
        warnings = [r for r in caplog.records if "tzdata" in r.getMessage()]
        assert len(warnings) == 1
    finally:
        _dates._school_zone.cache_clear()
