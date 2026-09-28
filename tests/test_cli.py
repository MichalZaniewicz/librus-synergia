"""Tests for the command-line output."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from librus_synergia import cli
from librus_synergia.changes import Changes

from .test_changes import _data, _grade, _lesson


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "librus-synergia" in capsys.readouterr().out


def test_interval_floor() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--watch", "--interval", "1"])


def test_summary_resolves_subject_names(capsys: pytest.CaptureFixture[str]) -> None:
    tomorrow = date.today() + timedelta(days=1)
    data = _data(
        grades=[_grade(1, "4+")],
        subjects={1: "Matematyka", 7: "Fizyka"},
        timetable={tomorrow: [_lesson(is_canceled=True)]},
    )
    cli._print_summary(data)
    out = capsys.readouterr().out
    assert "Jan K" in out
    assert "Matematyka" in out and "4+  (4.5)" in out
    assert "Fizyka (odwołana)" in out


def test_changes_output(capsys: pytest.CaptureFixture[str]) -> None:
    data = _data(subjects={1: "Matematyka"})
    changes = Changes(grades=[_grade(2, "6")])
    cli._print_changes(data, changes)
    assert "Nowa ocena: Matematyka 6" in capsys.readouterr().out


def test_json_is_valid() -> None:
    data = _data(grades=[_grade(1)], timetable={date(2026, 9, 28): [_lesson()]})
    payload = json.loads(cli._to_json(data))
    assert payload["grades"][0]["value"] == "5"
    assert "2026-09-28" in payload["timetable"]
