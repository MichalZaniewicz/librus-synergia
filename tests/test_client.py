"""Tests for the low-level LibrusApiClient against a mocked aiohttp session."""

from __future__ import annotations

import time
from http.cookies import SimpleCookie

import aiohttp
import pytest
from yarl import URL

from librus_synergia.client import LibrusApiClient, LibrusSessionData
from librus_synergia.const import (
    API_OAUTH_AUTHORIZATION_URL,
    DATA_BASE_URL,
    MESSAGES_BASE_URL,
    SANDBOX_URL,
    SYNERGIA_HOMEWORK_ATTACHMENT_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    SYNERGIA_REFRESH_TOKEN_URL,
)
from librus_synergia.exceptions import (
    LibrusCaptchaRequiredError,
    LibrusInvalidCredentialsError,
    LibrusServerMaintenanceError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)

from .helpers import AUTHORIZATION_REDIRECT_URL, MockedSession, mock_successful_login


@pytest.mark.asyncio
async def test_login_success_sets_session_valid() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            assert client.session_age_seconds is None  # never logged in yet
            session_data = await client.async_login("correct-password")

    assert client.is_session_valid()
    assert session_data.logged_in_at > 0
    # BUG FIX (live feedback - richer diagnostics): session_age_seconds
    # should now be a small, real, non-negative number just after login.
    assert client.session_age_seconds is not None
    assert 0 <= client.session_age_seconds < 5
    cookie_names = {c["name"] for c in session_data.cookies}
    assert "oauth_token" in cookie_names


@pytest.mark.asyncio
async def test_login_rejected_credentials_raises() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                SYNERGIA_PORTAL_LOGIN_URL,
                status=302,
                headers={"Location": AUTHORIZATION_REDIRECT_URL},
            )
            mocked.get(AUTHORIZATION_REDIRECT_URL, status=200, text_data="")
            mocked.post(API_OAUTH_AUTHORIZATION_URL, status=200, json_data={"status": "error"})
            client = LibrusApiClient(session, "1234567u")
            with pytest.raises(LibrusInvalidCredentialsError):
                await client.async_login("wrong-password")

    assert not client.is_session_valid()


@pytest.mark.asyncio
async def test_login_captcha_marker_raises() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                SYNERGIA_PORTAL_LOGIN_URL,
                status=302,
                headers={"Location": AUTHORIZATION_REDIRECT_URL},
            )
            mocked.get(AUTHORIZATION_REDIRECT_URL, status=200, text_data="")
            mocked.post(
                API_OAUTH_AUTHORIZATION_URL,
                status=200,
                json_data={"status": "error", "message": "please solve the recaptcha"},
            )
            client = LibrusApiClient(session, "1234567u")
            with pytest.raises(LibrusCaptchaRequiredError):
                await client.async_login("whatever")


@pytest.mark.asyncio
async def test_get_grades_after_login() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(
                f"{DATA_BASE_URL}/Grades",
                status=200,
                json_data={"Grades": [{"Id": 1, "Grade": "5", "Category": {"Id": 10}}]},
            )
            payload = await client.async_get_grades()

    assert payload["Grades"][0]["Grade"] == "5"


@pytest.mark.asyncio
async def test_data_fetch_session_rejected_raises_session_expired() -> None:
    """CONFIRMED live (2026-09-05): a data endpoint can reject an
    already-established session (HTTP 401/403) - e.g. Librus's real session
    lifetime running shorter than our own conservative elapsed-time
    estimate. This must raise LibrusSessionExpiredError, NOT
    LibrusInvalidCredentialsError - the coordinator treats the two very
    differently (force a silent re-login + retry vs. surface Home
    Assistant's reauth flow to the user)."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(f"{DATA_BASE_URL}/Grades", status=401, json_data={})
            with pytest.raises(LibrusSessionExpiredError):
                await client.async_get_grades()


@pytest.mark.asyncio
async def test_data_fetch_session_rejected_with_non_json_body_still_raises_session_expired() -> (
    None
):
    """BUG FIX (code review): `_async_request_url` used to call
    `_async_read_json` (JSON-parse the body) BEFORE checking `response.
    status in (401, 403)`. A dead-session 401/403 can come back with a
    non-JSON (HTML/plain-text) body - previously that made `_async_read_json`
    raise LibrusUnexpectedResponseError first, so LibrusSessionExpiredError
    never fired at all and the coordinator's forced-relogin-and-retry-once
    recovery (which depends on catching THIS exception type) was silently
    skipped. The 401/403 check must run first and must not depend on a
    successfully-parsed payload."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(
                f"{DATA_BASE_URL}/Grades",
                status=401,
                text_data="<html>session expired, please log in again</html>",
            )
            with pytest.raises(LibrusSessionExpiredError) as exc_info:
                await client.async_get_grades()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_maintenance_response_raises() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(f"{DATA_BASE_URL}/Grades", status=503, text_data="")
            with pytest.raises(LibrusServerMaintenanceError):
                await client.async_get_grades()


@pytest.mark.asyncio
async def test_non_json_response_raises_unexpected() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(f"{DATA_BASE_URL}/Grades", status=200, text_data="<html>not json</html>")
            with pytest.raises(LibrusUnexpectedResponseError):
                await client.async_get_grades()


@pytest.mark.asyncio
async def test_messages_list_bare_array_response_is_normalized() -> None:
    """BUG FIX (2026-09-06, found live): confirmed a real Wiadomości
    mailbox's list endpoint ("substitutions"/"alerts") can return a bare
    JSON array instead of the {"data": [...]} envelope every other
    endpoint in this client uses - a real request against the live
    account raised LibrusUnexpectedResponseError("Expected a JSON object,
    got list"), which (before coordinator.py isolated the two fetches)
    silently wiped out the otherwise-working inbox unread-count/message
    data too via a shared asyncio.gather(). _async_read_json must
    normalize a bare list into {"data": [...]} instead of raising."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            await client.async_login("correct-password")

            mocked.get(
                f"{MESSAGES_BASE_URL}/substitutions/messages",
                status=200,
                json_data=[{"messageId": "1", "topic": "Zmiana w planie"}],
            )
            payload = await client.async_get_messages(mailbox="substitutions", limit=10)

    assert payload == {"data": [{"messageId": "1", "topic": "Zmiana w planie"}]}


@pytest.mark.asyncio
async def test_import_session_restores_validity_without_network() -> None:
    """A restored session (e.g. after a HA restart) should be considered
    valid without any request being made, as long as it hasn't aged out."""

    async with aiohttp.ClientSession() as session:
        client = LibrusApiClient(session, "1234567u")
        assert not client.is_session_valid()
        client.import_session(
            LibrusSessionData(
                cookies=[
                    {"name": "oauth_token", "value": "restored", "domain": "synergia.librus.pl"}
                ],
                logged_in_at=time.time(),
            )
        )
        assert client.is_session_valid()


async def test_device_cookie_on_subpath_survives_export_and_import() -> None:
    """CONFIRMED live: DeviceCookie is set with `Path=/OAuth`. It must be
    exported (a root-URL cookie filter used to drop it) and restored with
    its path, so it's sent to the OAuth login endpoints again."""
    async with aiohttp.ClientSession() as session:
        client = LibrusApiClient(session, "1234567u")
        cookie: SimpleCookie = SimpleCookie()
        cookie["DeviceCookie"] = "device123"
        cookie["DeviceCookie"]["path"] = "/OAuth"
        session.cookie_jar.update_cookies(cookie, response_url=URL("https://api.librus.pl/OAuth"))
        session.cookie_jar.update_cookies(
            {"oauth_token": "tok"}, response_url=URL("https://synergia.librus.pl/")
        )
        exported = client.export_session()

    assert {
        "name": "DeviceCookie",
        "value": "device123",
        "domain": "api.librus.pl",
        "path": "/OAuth",
    } in exported.cookies
    assert {"name": "oauth_token", "value": "tok", "domain": "synergia.librus.pl"} in (
        exported.cookies
    )

    async with aiohttp.ClientSession() as session:
        LibrusApiClient(session, "1234567u").import_session(exported)
        sent = session.cookie_jar.filter_cookies(
            URL("https://api.librus.pl/OAuth/Authorization?client_id=46")
        )
        assert sent["DeviceCookie"].value == "device123"
        assert "DeviceCookie" not in session.cookie_jar.filter_cookies(
            URL("https://synergia.librus.pl/")
        )


async def test_async_close_closes_the_underlying_session() -> None:
    """`async_close` must close whatever session this client was given -
    the caller (see `custom_components/librus_synergia/__init__.py`) relies
    on this to release a per-entry session on unload, instead of leaking one
    aiohttp session per reload."""
    session = aiohttp.ClientSession()
    client = LibrusApiClient(session, "1234567u")
    assert not session.closed
    await client.async_close()
    assert session.closed


async def test_old_session_is_refreshed_without_a_login() -> None:
    """Past SESSION_REFRESH_AFTER_SECONDS, /refreshToken renews the session
    (no password login) and the session counts as fresh again."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(SYNERGIA_REFRESH_TOKEN_URL, status=200, text_data="")
            updates: list[LibrusSessionData] = []
            client = LibrusApiClient(session, "1234567u", on_session_update=updates.append)
            session.cookie_jar.update_cookies(
                SimpleCookie("oauth_token=abc"), URL("https://synergia.librus.pl/")
            )
            client.import_session(
                LibrusSessionData(cookies=[], logged_in_at=time.time() - 3 * 3600)
            )

            await client.async_ensure_session_valid("pw")

            assert mocked.get_calls[SYNERGIA_REFRESH_TOKEN_URL] == 1
            assert SYNERGIA_PORTAL_LOGIN_URL not in mocked.get_calls
            assert client.session_age_seconds is not None and client.session_age_seconds < 5
            assert len(updates) == 1


async def test_failed_refresh_falls_back_to_login_when_expired() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(SYNERGIA_REFRESH_TOKEN_URL, status=302, text_data="")
            mock_successful_login(session, mocked)
            client = LibrusApiClient(session, "1234567u")
            client.import_session(
                LibrusSessionData(cookies=[], logged_in_at=time.time() - 30 * 3600)
            )
            session.cookie_jar.update_cookies(
                SimpleCookie("oauth_token=abc"), URL("https://synergia.librus.pl/")
            )

            await client.async_ensure_session_valid("pw")

            assert mocked.get_calls[SYNERGIA_PORTAL_LOGIN_URL] == 1


async def test_download_message_attachment() -> None:
    link = "https://sandbox.librus.pl/GetFile/KEY"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{MESSAGES_BASE_URL}/attachments/55/messages/99",
                json_data={"data": {"status": "ok", "downloadLink": link}},
            )
            mocked.get(link, text_data="<html>wait</html>", headers={"Content-Type": "text/html"})
            mocked.get(
                f"{link}/get",
                body=b"%PDF-1.4 test",
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="Plan lekcji.pdf"',
                },
            )
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_message_attachment("55", "99")

            assert file.filename == "Plan lekcji.pdf"
            assert file.content_type == "application/pdf"
            assert file.content == b"%PDF-1.4 test"


async def test_download_homework_attachment_getfile_link() -> None:
    link = "https://sandbox.librus.pl/GetFile/HWKEY"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/77", status=302, headers={"Location": link}
            )
            mocked.get(link, text_data="<html>wait</html>", headers={"Content-Type": "text/html"})
            mocked.get(
                f"{link}/get",
                body=b"PK zip",
                headers={
                    "Content-Type": "application/zip",
                    "Content-Disposition": 'attachment; filename="zadanie.zip"',
                },
            )
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_homework_attachment("77")

            assert file.filename == "zadanie.zip"
            assert file.content == b"PK zip"


async def test_download_homework_attachment_single_use_key(monkeypatch) -> None:
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSDownload&singleUseKey=abc_123"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/78", status=302, headers={"Location": location}
            )
            mocked.get_sequence(
                SANDBOX_URL,
                {"json_data": {"status": "not_downloaded_yet"}},
                {"json_data": {"status": "ready"}},
            )
            mocked.post(
                SANDBOX_URL,
                body=b"%PDF",
                headers={"Content-Type": "application/pdf"},
            )
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_homework_attachment("78")

            assert file.filename == "homework-file-78"
            assert file.content_type == "application/pdf"
            assert mocked.get_calls[SANDBOX_URL] == 2


async def test_download_homework_attachment_login_redirect_is_session_expiry() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/79",
                status=302,
                headers={"Location": "/loguj"},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("79")


async def test_download_homework_attachment_failed() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/80",
                status=302,
                headers={"Location": f"{SANDBOX_URL}?action=CSDownloadFailed"},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError):
                await client.async_download_homework_attachment("80")


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_non_json_error_page_keeps_status() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{MESSAGES_BASE_URL}/alerts/messages", status=404, text_data="<html>404</html>"
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError) as err:
                await client.async_get_messages(mailbox="alerts")

            assert err.value.status_code == 404
