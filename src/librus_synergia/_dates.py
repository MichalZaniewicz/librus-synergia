"""Dates in the school's own time zone."""

from __future__ import annotations

from datetime import date, datetime
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import SCHOOL_TIMEZONE


@cache
def _school_zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(SCHOOL_TIMEZONE)
    except ZoneInfoNotFoundError:  # no time zone database (and no `tzdata`)
        return None


def school_today() -> date:
    """Today in Poland (`SCHOOL_TIMEZONE`), whatever the machine's zone is.
    Falls back to the local date when no time zone database is available."""
    zone = _school_zone()
    return date.today() if zone is None else datetime.now(zone).date()
