"""Unofficial async Python client for Librus Synergia (Polish e-gradebook).

Two layers:

* `Librus` - high-level, typed: `await librus.grades()` -> `list[GradeData]`.
* `LibrusApiClient` - low-level: one method per endpoint, raw JSON back;
  pair it with the pure functions in `librus_synergia.parsers`.
"""

from . import parsers
from .changes import Changes, ChangeTracker, SeenIds, TimetableChange
from .client import LibrusApiClient, LibrusSessionData
from .exceptions import (
    LibrusAccountActionRequiredError,
    LibrusAuthError,
    LibrusCaptchaRequiredError,
    LibrusConnectionError,
    LibrusError,
    LibrusInvalidCredentialsError,
    LibrusServerMaintenanceError,
    LibrusSessionExpiredError,
    LibrusUnexpectedResponseError,
)
from .librus import Librus
from .parsers import parse_grade_value

__version__ = "0.3.16"

__all__ = [
    "Librus",
    "ChangeTracker",
    "Changes",
    "SeenIds",
    "TimetableChange",
    "LibrusApiClient",
    "LibrusSessionData",
    "parsers",
    "parse_grade_value",
    "LibrusError",
    "LibrusConnectionError",
    "LibrusServerMaintenanceError",
    "LibrusAuthError",
    "LibrusInvalidCredentialsError",
    "LibrusSessionExpiredError",
    "LibrusCaptchaRequiredError",
    "LibrusAccountActionRequiredError",
    "LibrusUnexpectedResponseError",
]
