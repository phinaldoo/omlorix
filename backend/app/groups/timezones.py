"""Timezone helpers shared by group access-window schema and runtime code."""

from zoneinfo import available_timezones


def get_access_window_timezone_options() -> list[str]:
    """Return every available IANA timezone with UTC pinned to the top."""
    timezones = sorted(available_timezones())
    return ["UTC", *[timezone_name for timezone_name in timezones if timezone_name != "UTC"]]


COMMON_TIMEZONES = get_access_window_timezone_options()
