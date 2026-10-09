"""Tests for the low-level LibrusApiClient against a mocked aiohttp session."""

from __future__ import annotations

import asyncio
import time
from http.cookies import SimpleCookie

import aiohttp
import pytest
from yarl import URL

from librus_synergia.client import LibrusApiClient, LibrusSessionData, _with_download_deadline
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
    LibrusConnectionError,
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
            mocked.get(
                SYNERGIA_REFRESH_TOKEN_URL,
                status=200,
                text_data="",
                cookies={"oauth_token": "renewed"},
            )
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
    """The flow confirmed live: open the CSTryToDownload page, POST
    CSCheckKey until `ready`, then GET CSDownload."""
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/78", status=302, headers={"Location": location}
            )
            mocked.get(
                location, text_data="<html>loading</html>", headers={"Content-Type": "text/html"}
            )
            mocked.post_sequence(
                SANDBOX_URL,
                {"json_data": {"status": "not_downloaded_yet"}},
                {"json_data": {"status": "ready"}},
            )
            mocked.get(
                SANDBOX_URL,
                body=b"\xff\xd8\xff",
                headers={
                    "Content-Type": "image/jpeg",
                    "Content-Disposition": 'attachment; filename="zdjecie.jpeg"',
                },
            )
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_homework_attachment("78")

            assert file.filename == "zdjecie.jpeg"
            assert file.content_type == "image/jpeg"
            assert mocked.get_calls[location] == 1
            assert mocked.post_calls[SANDBOX_URL] == 2
            assert mocked.get_calls[SANDBOX_URL] == 1


async def test_download_homework_attachment_download_failed(monkeypatch) -> None:
    """`download_failed` on every key: three fresh keys, then the error."""
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/81", status=302, headers={"Location": location}
            )
            mocked.get(location, text_data="<html>loading</html>")
            mocked.post(SANDBOX_URL, json_data={"status": "download_failed"})
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="download_failed"):
                await client.async_download_homework_attachment("81")

            assert mocked.get_calls[f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/81"] == 3


async def test_download_homework_attachment_retries_a_failed_key(monkeypatch) -> None:
    """Seen live: one key fails, a fresh key for the same file works."""
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/83", status=302, headers={"Location": location}
            )
            mocked.get(location, text_data="<html>loading</html>")
            mocked.post_sequence(
                SANDBOX_URL,
                {"json_data": {"status": "download_failed"}},
                {"json_data": {"status": "ready"}},
            )
            mocked.get(SANDBOX_URL, body=b"%PDF", headers={"Content-Type": "application/pdf"})
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_homework_attachment("83")

            assert file.content == b"%PDF"
            assert mocked.get_calls[f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/83"] == 2


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


async def test_download_homework_attachment_reports_a_timeout(monkeypatch) -> None:
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/82", status=302, headers={"Location": location}
            )
            mocked.get(location, text_data="<html>loading</html>")
            mocked.post(SANDBOX_URL, json_data={"status": "not_downloaded_yet"})
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="still wasn't ready after 15"):
                await client.async_download_homework_attachment("82")

            assert mocked.post_calls[SANDBOX_URL] == 15


async def test_download_homework_attachment_page_without_redirect_is_session_expiry() -> None:
    """Found live: the web session (DZIENNIKSID) died while the API still
    worked - the page answers 200 instead of redirecting to the file."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/81", text_data="<html>Brak dostępu</html>"
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("81")


async def test_download_school_file() -> None:
    link = "https://sandbox.librus.pl/GetFile/DOCKEY"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                "https://synergia.librus.pl/pliki_szkoly/pobierz/5",
                status=302,
                headers={"Location": link},
            )
            mocked.get(link, text_data="<html>wait</html>", headers={"Content-Type": "text/html"})
            mocked.get(
                f"{link}/get",
                body=b"%PDF doc",
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="regulamin.pdf"',
                },
            )
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_school_file("/pliki_szkoly/pobierz/5")

            assert (file.filename, file.content) == ("regulamin.pdf", b"%PDF doc")


async def test_insufficient_scopes_is_not_a_dead_session() -> None:
    """A 401 saying "Insufficient scopes" (e.g. `SchoolInfo`) can't be fixed
    by logging in again, so it must not look like an expired session."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{DATA_BASE_URL}/SchoolInfo",
                status=401,
                json_data={"Status": "Error", "Message": "Insufficient scopes"},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError) as err:
                await client._async_request("SchoolInfo")

    assert not isinstance(err.value, LibrusSessionExpiredError)
    assert err.value.status_code == 401


async def test_unknown_http_method_is_refused() -> None:
    async with aiohttp.ClientSession() as session:
        client = LibrusApiClient(session, "1234567u")
        with pytest.raises(ValueError, match="PUT"):
            await client._async_request_url(f"{DATA_BASE_URL}/Me", method="PUT")


async def test_download_page_without_redirect_and_without_logout_is_unexpected() -> None:
    """Only Synergia's logged-out page means a dead web session; any other
    page without a redirect (a missing file, an error) is not fixed by a
    fresh login."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/84",
                text_data='<html><a href="/wyloguj">Wyloguj</a> Nie znaleziono pliku</html>',
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError) as err:
                await client.async_download_homework_attachment("84")

    assert not isinstance(err.value, LibrusSessionExpiredError)


async def test_download_page_with_the_login_form_is_session_expiry() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/85",
                text_data='<form><input type="password" name="pass"><button>Zaloguj</button></form>',
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("85")


async def test_download_redirect_to_a_lookalike_host_is_not_the_sandbox() -> None:
    lookalike = "https://sandbox.librus.pl.example.invalid/GetFile/X"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/86",
                status=302,
                headers={"Location": lookalike},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("86")

            assert lookalike not in mocked.get_calls


@pytest.mark.parametrize(
    "download_path",
    [
        "https://example.invalid/pliki_szkoly/pobierz/5",
        "//example.invalid/pliki_szkoly/pobierz/5",
        "http://synergia.librus.pl/pliki_szkoly/pobierz/5",
        "https://[::1/pliki_szkoly/pobierz/5",
    ],
)
async def test_download_school_file_refuses_other_hosts(download_path: str) -> None:
    """Refused before any request (the mocked session would fail on one),
    as a `LibrusError` - not a bare `ValueError`."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session):
            client = LibrusApiClient(session, "1234567u")
            with pytest.raises(LibrusUnexpectedResponseError, match="Not a Synergia download path"):
                await client.async_download_school_file(download_path)


async def test_download_school_file_accepts_a_full_synergia_url() -> None:
    link = "https://sandbox.librus.pl/GetFile/DOCKEY2"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                "https://synergia.librus.pl/pliki_szkoly/pobierz/6",
                status=302,
                headers={"Location": link},
            )
            mocked.get(link, text_data="<html>wait</html>", headers={"Content-Type": "text/html"})
            mocked.get(f"{link}/get", body=b"%PDF", headers={"Content-Type": "application/pdf"})
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_school_file(
                "https://synergia.librus.pl/pliki_szkoly/pobierz/6"
            )

    assert (file.filename, file.content) == ("school-file-6", b"%PDF")


async def test_check_key_answer_that_is_not_an_object_is_a_failed_key(monkeypatch) -> None:
    """A JSON list (or string) from `CSCheckKey` counts as a failed key,
    like an answer that isn't JSON - not an AttributeError."""
    monkeypatch.setattr("librus_synergia.client.asyncio.sleep", _no_sleep)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/87", status=302, headers={"Location": location}
            )
            mocked.get(location, text_data="<html>loading</html>")
            mocked.post_sequence(
                SANDBOX_URL,
                {"json_data": ["ready"]},
                {"json_data": {"status": "ready"}},
            )
            mocked.get(SANDBOX_URL, body=b"%PDF", headers={"Content-Type": "application/pdf"})
            client = LibrusApiClient(session, "1234567u")

            file = await client.async_download_homework_attachment("87")

            assert file.content == b"%PDF"
            assert mocked.get_calls[f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/87"] == 2


async def test_download_gives_up_after_the_overall_deadline(monkeypatch) -> None:
    monkeypatch.setattr("librus_synergia.client.DOWNLOAD_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("librus_synergia.client._sandbox_poll_delay", lambda _attempt: 5)
    location = f"{SANDBOX_URL}?action=CSTryToDownload&singleUseKey=w9_123_abc"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/88", status=302, headers={"Location": location}
            )
            mocked.get(location, text_data="<html>loading</html>")
            mocked.post(SANDBOX_URL, json_data={"status": "not_downloaded_yet"})
            client = LibrusApiClient(session, "1234567u")

            started = time.monotonic()
            with pytest.raises(LibrusConnectionError, match="homework-file-88"):
                await client.async_download_homework_attachment("88")

    assert time.monotonic() - started < 2


async def test_message_attachment_link_outside_the_sandbox_is_refused() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{MESSAGES_BASE_URL}/attachments/9/messages/10",
                json_data={"data": {"downloadLink": "https://example.invalid/GetFile/X"}},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="outside the sandbox"):
                await client.async_download_message_attachment("9", "10")

            assert "https://example.invalid/GetFile/X" not in mocked.get_calls


@pytest.mark.parametrize(
    "link", ["http://sandbox.librus.pl/GetFile/X", "https://[::1/GetFile/X", "https://:abc"]
)
async def test_message_attachment_link_must_be_https_and_valid(link: str) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{MESSAGES_BASE_URL}/attachments/9/messages/11",
                json_data={"data": {"downloadLink": link}},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="outside the sandbox"):
                await client.async_download_message_attachment("9", "11")

            assert link not in mocked.get_calls


async def test_download_page_without_redirect_is_session_expiry_unless_not_found() -> None:
    """Any 200 page without a redirect counts as an expired web session
    (a wrong guess costs one login) - here a page with no known text."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/89",
                text_data="<html><h1>Synergia</h1>Coś poszło nie tak</html>",
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError) as err:
                await client.async_download_homework_attachment("89")

    assert err.value.status_code == 200


async def test_not_found_text_on_the_logged_out_page_is_still_session_expiry() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/90",
                text_data="<html>Brak dostępu - strona nie istnieje</html>",
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("90")


async def test_download_page_is_decoded_with_its_own_charset() -> None:
    """A not-found page in ISO-8859-2 is read with its charset; a page
    naming an unknown charset (with bytes that aren't UTF-8) doesn't crash
    either."""
    body = "<html>Nie znaleziono pliku. Spróbuj później</html>".encode("iso-8859-2")
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/91",
                body=body,
                headers={"Content-Type": "text/html; charset=iso-8859-2"},
            )
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/92",
                body="<html>Brak dostępu</html>".encode("iso-8859-2"),
                headers={"Content-Type": "text/html; charset=bogus-charset"},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="File not found") as err:
                await client.async_download_homework_attachment("91")
            assert not isinstance(err.value, LibrusSessionExpiredError)
            assert "Spróbuj później" in str(err.value)

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_download_homework_attachment("92")


async def test_download_redirect_to_an_invalid_link_is_unexpected() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{SYNERGIA_HOMEWORK_ATTACHMENT_URL}/93",
                status=302,
                headers={"Location": "https://[::1/GetFile/X"},
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusUnexpectedResponseError, match="Invalid download link"):
                await client.async_download_homework_attachment("93")


async def test_insufficient_scopes_only_counts_on_the_data_gateway() -> None:
    """Off the data gateway (here Wiadomości) a 401 stays a dead session."""
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(
                f"{MESSAGES_BASE_URL}/inbox/unreadMessagesCount",
                status=401,
                text_data="Insufficient scopes",
            )
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError):
                await client.async_get_unread_messages_count()


@pytest.mark.parametrize(
    "response",
    [
        {"read_error": aiohttp.ClientPayloadError("cut off")},
        {"read_error": TimeoutError()},
        {"text_data": "Insufficient scopes", "headers": {"Content-Length": str(10**6)}},
    ],
)
async def test_unreadable_401_body_is_a_dead_session(response: dict) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            mocked.get(f"{DATA_BASE_URL}/Grades", status=401, **response)
            client = LibrusApiClient(session, "1234567u")

            with pytest.raises(LibrusSessionExpiredError) as err:
                await client.async_get_grades()

    assert err.value.status_code == 401


async def test_download_deadline_is_a_parameter() -> None:
    async def slow() -> str:
        await asyncio.sleep(5)
        return "never"

    with pytest.raises(LibrusConnectionError, match="wasn't downloaded within 0.01 s"):
        await _with_download_deadline(slow(), "file-1", 0.01)


async def test_aiohttp_timeout_is_told_apart_from_the_deadline() -> None:
    async def aiohttp_timeout() -> str:
        raise TimeoutError

    with pytest.raises(LibrusConnectionError, match="the connection timed out") as err:
        await _with_download_deadline(aiohttp_timeout(), "file-2", 10)

    assert "within" not in str(err.value)
