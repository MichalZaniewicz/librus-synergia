"""Shared test doubles: a directly-mocked aiohttp session.

Mocks `aiohttp.ClientSession.get`/`.post` (a dict of URL -> canned
response) rather than using `aioresponses`, whose last release doesn't
support aiohttp>=3.11.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from urllib.parse import urljoin

import aiohttp
from yarl import URL

from librus_synergia.const import (
    API_OAUTH_AUTHORIZATION_URL,
    API_OAUTH_AUTHORIZATION_WITH_SCOPE_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
)

AUTHORIZATION_REDIRECT_URL = (
    "https://api.librus.pl/OAuth/Authorization?client_id=46&response_type=code"
    "&scope=mydata&state=fakestate123"
)


class FakeResponse:
    """A minimal stand-in for `aiohttp.ClientResponse` - usable directly as
    its own async context manager, matching what `async with session.get(
    ...) as response:` needs."""

    def __init__(
        self,
        *,
        status: int = 200,
        json_data: Any = None,
        text_data: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.url = URL("https://example.invalid/")
        self._text = json.dumps(json_data) if json_data is not None else text_data

    async def json(self, content_type: str | None = "application/json") -> Any:
        # Mirrors real aiohttp: content_type=None bypasses the content-type
        # check but still parses the body as JSON, raising a plain
        # ValueError (json.JSONDecodeError) if it isn't valid JSON.
        return json.loads(self._text)

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def __await__(self):
        # client.py uses both `resp = await session.get(...)` and
        # `async with session.get(...) as resp:` in different places -
        # real aiohttp's request context manager supports both, so this
        # fake must too.
        async def _self() -> FakeResponse:
            return self

        return _self().__await__()


class MockedSession:
    """Patches `session.get`/`session.post` to look up a canned
    `FakeResponse` by exact URL, registered ahead of time via `.get()`/
    `.post()` - the same shape the old aioresponses-based tests used."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._gets: dict[str, FakeResponse] = {}
        self._posts: dict[str, FakeResponse] = {}
        self._queued: dict[str, list[FakeResponse]] = {}
        self.get_calls: dict[str, int] = {}
        self._patches = [
            patch.object(session, "get", side_effect=self._handle_get),
            patch.object(session, "post", side_effect=self._handle_post),
        ]

    def get(self, url: str, **kwargs: Any) -> None:
        self._gets[url] = FakeResponse(**kwargs)
        self._queued.pop(url, None)

    def get_sequence(self, url: str, *responses: dict[str, Any]) -> None:
        """Answer successive GETs of `url` with each response in turn (the
        last one repeats)."""
        self._queued[url] = [FakeResponse(**r) for r in responses]
        self.get_calls.setdefault(url, 0)

    def post(self, url: str, **kwargs: Any) -> None:
        self._posts[url] = FakeResponse(**kwargs)

    def _handle_get(self, url: Any, **_kwargs: Any) -> FakeResponse:
        self.get_calls[str(url)] = self.get_calls.get(str(url), 0) + 1
        queue = self._queued.get(str(url))
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        response = self._gets.get(str(url))
        if response is None:
            raise AssertionError(f"Unexpected GET {url}")
        return response

    def _handle_post(self, url: Any, **_kwargs: Any) -> FakeResponse:
        response = self._posts.get(str(url))
        if response is None:
            raise AssertionError(f"Unexpected POST {url}")
        return response

    def __enter__(self) -> MockedSession:
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        for p in self._patches:
            p.stop()


def mock_successful_login(session: aiohttp.ClientSession, mocked: MockedSession) -> None:
    mocked.get(
        SYNERGIA_PORTAL_LOGIN_URL,
        status=302,
        headers={"Location": AUTHORIZATION_REDIRECT_URL},
    )
    mocked.get(AUTHORIZATION_REDIRECT_URL, status=200, text_data="")
    mocked.post(
        API_OAUTH_AUTHORIZATION_URL,
        status=200,
        json_data={"status": "ok", "goTo": "/OAuth/Authorization/2FA?client_id=46"},
    )
    hop1 = urljoin(API_OAUTH_AUTHORIZATION_WITH_SCOPE_URL, "/OAuth/Authorization/2FA?client_id=46")
    hop2 = "https://api.librus.pl/OAuth/Authorization/PerformLogin?client_id=46"
    final = "https://synergia.librus.pl/loguj/portalRodzina?code=abc&state=fakestate123"
    mocked.get(hop1, status=302, headers={"Location": hop2})
    mocked.get(hop2, status=302, headers={"Location": final})
    mocked.get(final, status=200, text_data="<html>logged in</html>")
    # The fake responses above carry no real Set-Cookie handling - seed the
    # jar directly to exercise the client's post-login cookie-jar read
    # (confirmed live against the real Librus servers on 2026-09-05 that a
    # genuine response DOES populate this).
    session.cookie_jar.update_cookies(
        {"oauth_token": "faketoken123"}, response_url=URL("https://synergia.librus.pl/")
    )
