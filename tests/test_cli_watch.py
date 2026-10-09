"""`librus-synergia --watch`: keeps going through outages, stops on a
failed login; the session file is private."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

import pytest

from librus_synergia import cli
from librus_synergia.client import LibrusSessionData
from librus_synergia.exceptions import (
    LibrusConnectionError,
    LibrusInvalidCredentialsError,
    LibrusUnexpectedResponseError,
)

from .test_changes import _data, _grade


class _FakeLibrus:
    def __init__(self, results: list[object]) -> None:
        self._results = results
        self.calls = 0
        self.session_data = LibrusSessionData(cookies=[], logged_in_at=1.0)

    async def fetch_changes(self) -> object:
        result = self._results[self.calls]
        self.calls += 1
        if isinstance(result, BaseException):
            raise result
        return result


async def test_watch_keeps_going_through_outages_and_stops_on_a_failed_login(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cli.asyncio, "sleep", no_sleep)
    librus = _FakeLibrus(
        [
            LibrusConnectionError("down"),
            _data(grades=[_grade(1)], subjects={1: "Matematyka"}),
            LibrusUnexpectedResponseError("odd"),
            _data(grades=[_grade(1), _grade(2, "6")], subjects={1: "Matematyka"}),
            LibrusInvalidCredentialsError("bad password"),
        ]
    )
    args = argparse.Namespace(interval=5, state=tmp_path / "state.json", session=None)

    with pytest.raises(LibrusInvalidCredentialsError):
        await cli._watch(args, librus)  # type: ignore[arg-type]

    assert librus.calls == 5
    out, err = capsys.readouterr()
    assert "down" in err and "odd" in err
    assert "Nowa ocena: Matematyka 6" in out
    assert (tmp_path / "state.json").exists()


def test_session_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)
    cli._save_json(path, {"cookies": []}, private=True)
    assert path.read_text(encoding="utf-8") == '{"cookies": []}'
    if sys.platform != "win32":  # Windows has no owner-only mode bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
