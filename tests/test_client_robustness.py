"""LibrusApiClient: timeouts, retry-later statuses, odd charsets, the
session refresh and request hygiene."""

from __future__ import annotations

import json
import time
from http.cookies import SimpleCookie

import aiohttp
import pytest
from yarl import URL

from librus_synergia.client import DEFAULT_REQUEST_TIMEOUT, LibrusApiClient, LibrusSessionData
from librus_synergia.const import (
    API_OAUTH_AUTHORIZATION_URL,
    DATA_BASE_URL,
    MESSAGES_BOOTSTRAP_URL,
    REFRESH_RETRY_AFTER_SECONDS,
    SYNERGIA_HOMEWORK_ATTACHMENT_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    SYNERGIA_REFRESH_TOKEN_URL,
    SYNERGIA_STUDENT_INFO_URL,
)
from librus_synergia.exceptions import (
    LibrusConnectionError,
    LibrusInvalidCredentialsError,
    LibrusServerMaintenanceError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)

from .helpers import AUTHORIZATION_REDIRECT_URL, MockedSession, mock_successful_login

GRADES_URL = f"{DATA_BASE_URL}/Grades"


def _fresh_client(session: aiohttp.ClientSession, **kwargs: object) -> LibrusApiClient:
    client = LibrusApiClient(session, "1234567u", **kwargs)  # type: ignore[arg-type]
    client.import_session(LibrusSessionData(cookies=[], logged_in_at=time.time()))
    return client


# --- B1: timeouts ------------------------------------------------------


async def test_data_request_timeout_is_a_connection_error() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, read_error=TimeoutError())
            client = _fresh_client(session)
            with pytest.raises(LibrusConnectionError, match="timed out") as err:
                await client.async_get_grades()
    assert err.value.status_code is None


async def test_login_timeout_is_a_connection_error_and_resets_the_session() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(SYNERGIA_PORTAL_LOGIN_URL, read_error=TimeoutError())
            client = _fresh_client(session)
            assert client.is_session_valid()
            with pytest.raises(LibrusConnectionError):
                await client.async_login("pw")
    # A failed login may have replaced cookies: the old session is not
    # assumed valid any more.
    assert not client.is_session_valid()
    assert client.login_count == 0


async def test_failed_credentials_also_reset_the_session() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.post(API_OAUTH_AUTHORIZATION_URL, json_data={"status": "error"})
            client = _fresh_client(session)
            with pytest.raises(LibrusInvalidCredentialsError):
                await client.async_login("pw")
    assert not client.is_session_valid()


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.async_bootstrap_messages(),
        lambda c: c.async_get_student_info_page(),
    ],
)
async def test_other_requests_time_out_as_connection_errors(call) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(MESSAGES_BOOTSTRAP_URL, read_error=TimeoutError())
            mocked.get(SYNERGIA_STUDENT_INFO_URL, read_error=TimeoutError())
            with pytest.raises(LibrusConnectionError):
                await call(_fresh_client(session))


async def test_refresh_timeout_returns_false() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(SYNERGIA_REFRESH_TOKEN_URL, read_error=TimeoutError())
            client = _fresh_client(session)
            assert await client.async_refresh_session() is False


# --- B4: refreshToken ----------------------------------------------------


async def test_refresh_without_a_new_cookie_is_a_failure_and_not_retried_soon() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(SYNERGIA_REFRESH_TOKEN_URL, status=200, text_data="")
            session.cookie_jar.update_cookies(
                SimpleCookie("oauth_token=abc"), URL("https://synergia.librus.pl/")
            )
            client = LibrusApiClient(session, "1234567u")
            client.import_session(
                LibrusSessionData(cookies=[], logged_in_at=time.time() - 3 * 3600)
            )

            # 200 without Set-Cookie: not a renewal. The session is still
            # young enough, so no login either.
            await client.async_ensure_session_valid("pw")
            await client.async_ensure_session_valid("pw")
            assert mocked.get_calls[SYNERGIA_REFRESH_TOKEN_URL] == 1
            assert client.session_age_seconds is not None and client.session_age_seconds > 3600

            # 30 min later it is tried again.
            assert client._refresh_failed_at is not None
            client._refresh_failed_at -= REFRESH_RETRY_AFTER_SECONDS + 1
            await client.async_ensure_session_valid("pw")
            assert mocked.get_calls[SYNERGIA_REFRESH_TOKEN_URL] == 2


# --- B6: odd charsets -------------------------------------------------------


async def test_student_info_page_with_odd_bytes_does_not_fail() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                SYNERGIA_STUDENT_INFO_URL,
                body="<th>Nr w dzienniku</th><td>7</td> żółw".encode("cp1250") + b"\xff\xfe",
                headers={"Content-Type": "text/html; charset=windows-1250"},
            )
            page = await _fresh_client(session).async_get_student_info_page()
    assert "żółw" in page


async def test_bootstrap_with_invalid_utf8_does_not_fail() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(MESSAGES_BOOTSTRAP_URL, body=b"<html>ok \xff\xfe</html>")
            assert await _fresh_client(session).async_bootstrap_messages() is True


async def test_non_json_error_page_with_invalid_utf8_is_unexpected() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=500, body=b"<html>\xff oops</html>")
            with pytest.raises(LibrusUnexpectedResponseError) as err:
                await _fresh_client(session).async_get_grades()
    assert err.value.status_code == 500


async def test_login_response_with_invalid_utf8_is_unexpected_not_a_crash() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.post(API_OAUTH_AUTHORIZATION_URL, body=b"\xff\xfe not json")
            with pytest.raises(LibrusUnexpectedResponseError):
                await _fresh_client(session).async_login("pw")


async def test_json_in_another_charset_is_decoded() -> None:
    payload = {"Grades": [{"Id": 1, "Grade": "5", "Comments": []}], "Note": "żółć"}
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                GRADES_URL,
                body=json.dumps(payload, ensure_ascii=False).encode("cp1250"),
                headers={"Content-Type": "application/json; charset=windows-1250"},
            )
            result = await _fresh_client(session).async_get_grades()
    assert result == payload


async def test_json_with_a_utf8_bom_is_decoded() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, body=b"\xef\xbb\xbf" + b'{"Grades": []}')
            assert await _fresh_client(session).async_get_grades() == {"Grades": []}


# --- B8: login bodies are read ---------------------------------------------


async def test_login_reads_every_response_body() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            step1 = mocked._gets[SYNERGIA_PORTAL_LOGIN_URL]
            step2 = mocked._gets[AUTHORIZATION_REDIRECT_URL]
            await LibrusApiClient(session, "1234567u").async_login("pw")
    assert step1.reads >= 1
    assert step2.reads >= 1


# --- B12: retry-later statuses ---------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 504])
async def test_retry_later_statuses_are_connection_errors(status: int) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=status, text_data="<html>busy</html>")
            with pytest.raises(LibrusConnectionError) as err:
                await _fresh_client(session).async_get_grades()
    assert err.value.status_code == status
    assert not isinstance(err.value, LibrusServerMaintenanceError)


async def test_503_is_still_maintenance_and_carries_its_status() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=503, text_data="")
            with pytest.raises(LibrusServerMaintenanceError) as err:
                await _fresh_client(session).async_get_grades()
    assert err.value.status_code == 503


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, LibrusSessionExpiredError),
        (403, LibrusSessionExpiredError),
        (404, LibrusUnexpectedResponseError),
    ],
)
async def test_401_403_404_are_unchanged(status: int, error: type[Exception]) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=status, json_data={})
            with pytest.raises(error) as err:
                await _fresh_client(session).async_get_grades()
    assert err.value.status_code == status  # type: ignore[attr-defined]


async def test_login_during_maintenance_is_a_connection_error() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            mocked.post(API_OAUTH_AUTHORIZATION_URL, status=502, text_data="<html>bad</html>")
            with pytest.raises(LibrusConnectionError) as err:
                await LibrusApiClient(session, "1234567u").async_login("pw")
    assert err.value.status_code == 502


# --- O5 / O8: request timeout, error bodies --------------------------------


async def test_requests_carry_the_default_timeout() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, json_data={"Grades": []})
            await _fresh_client(session).async_get_grades()
            assert mocked.kwargs[GRADES_URL]["timeout"] is DEFAULT_REQUEST_TIMEOUT
            assert DEFAULT_REQUEST_TIMEOUT.total == 30
            assert DEFAULT_REQUEST_TIMEOUT.sock_connect == 10


async def test_request_timeout_can_be_changed_or_left_to_the_session() -> None:
    custom = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, json_data={"Grades": []})
            await _fresh_client(session, request_timeout=custom).async_get_grades()
            assert mocked.kwargs[GRADES_URL]["timeout"] is custom
            await _fresh_client(session, request_timeout=None).async_get_grades()
            assert "timeout" not in mocked.kwargs[GRADES_URL]


async def test_small_error_body_is_read_before_raising() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=503, text_data="down", headers={"Content-Length": "4"})
            response = mocked._gets[GRADES_URL]
            with pytest.raises(LibrusServerMaintenanceError):
                await _fresh_client(session).async_get_grades()
    assert response.reads == 1


async def test_large_error_body_is_left_unread() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(GRADES_URL, status=503, text_data="x", headers={"Content-Length": "999999"})
            response = mocked._gets[GRADES_URL]
            with pytest.raises(LibrusServerMaintenanceError):
                await _fresh_client(session).async_get_grades()
    assert response.reads == 0


async def test_plain_http_sandbox_redirect_is_not_followed() -> None:
    link = "http://sandbox.librus.pl/GetFile/KEY"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/77", status=302, headers={"Location": link}
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="https"):
                await client.async_download_homework_attachment("77")

            assert link not in mocked.get_calls
