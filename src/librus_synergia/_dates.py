"""Dates in the school's own time zone."""

from __future__ import annotations

import logging
from datetime import date, datetime
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import SCHOOL_TIMEZONE

_LOGGER = logging.getLogger(__name__)


@cache
def _school_zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(SCHOOL_TIMEZONE)
    except ZoneInfoNotFoundError:  # no time zone database (and no `tzdata`)
        # Cached, so this is logged once per process.
        _LOGGER.warning(
            "No time zone data for %s (install the `tzdata` package); "
            "using this machine's local date as 'today' instead",
            SCHOOL_TIMEZONE,
        )
        return None


def school_today() -> date:
    """Today in Poland (`SCHOOL_TIMEZONE`), whatever the machine's zone is.
    Falls back to the local date (with a one-time warning in the log) when
    no time zone database is available - `tzdata` is a dependency, so this
    only happens when it was left out on purpose."""
    zone = _school_zone()
    return date.today() if zone is None else datetime.now(zone).date()
