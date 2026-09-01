from __future__ import annotations

from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


PACIFIC_TIMEZONE: Final[str] = "America/Los_Angeles"

TIMEZONE_ALIASES: Final[dict[str, str]] = {
    "US/Pacific": PACIFIC_TIMEZONE,
}

def get_supported_user_timezone_values() -> tuple[str, ...]:
    """Return every IANA timezone installed with the application.

    UTC is pinned to the top to match the frontend selector. The remaining
    identifiers stay alphabetized so API-rendered settings schemas are stable.
    """

    timezones = sorted(available_timezones())
    return ("UTC", *(timezone_name for timezone_name in timezones if timezone_name != "UTC"))


SUPPORTED_USER_TIMEZONE_VALUES_ORDERED: Final[tuple[str, ...]] = get_supported_user_timezone_values()

# IANA identifiers are standardized proper names rather than translatable UI
# copy. Marking them non-translatable avoids generating hundreds of unstable
# translation keys in settings schemas.
SUPPORTED_USER_TIMEZONE_OPTIONS: Final[tuple[dict[str, str | bool], ...]] = tuple(
    {
        "value": timezone_name,
        "label": timezone_name,
        "translatable": False,
    }
    for timezone_name in SUPPORTED_USER_TIMEZONE_VALUES_ORDERED
)

SUPPORTED_USER_TIMEZONE_VALUES: Final[frozenset[str]] = frozenset(
    SUPPORTED_USER_TIMEZONE_VALUES_ORDERED
)


def normalize_timezone_identifier(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""

    mapped = TIMEZONE_ALIASES.get(normalized, normalized)
    try:
        ZoneInfo(mapped)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unsupported timezone value.") from exc

    return mapped


def normalize_user_timezone(value: str | None) -> str:
    """Normalize any valid IANA timezone accepted by the application runtime."""

    mapped = normalize_timezone_identifier(value)
    if not mapped:
        return ""
    return mapped
