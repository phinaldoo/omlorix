import pytest
from pydantic import ValidationError
from zoneinfo import available_timezones

from app.users.schemas import DetectedLocaleDefaults
from app.users.timezones import (
    SUPPORTED_USER_TIMEZONE_OPTIONS,
    SUPPORTED_USER_TIMEZONE_VALUES,
    normalize_user_timezone,
)


def test_user_timezone_options_include_every_installed_iana_timezone():
    """The schema catalog must not regress to a small hand-maintained subset."""

    expected_values = available_timezones() | {"UTC"}
    option_values = {str(option["value"]) for option in SUPPORTED_USER_TIMEZONE_OPTIONS}

    assert option_values == expected_values
    assert SUPPORTED_USER_TIMEZONE_VALUES == frozenset(expected_values)
    assert all(option["translatable"] is False for option in SUPPORTED_USER_TIMEZONE_OPTIONS)


@pytest.mark.parametrize("timezone_name", ["UTC", "Europe/Paris", "Asia/Tokyo", "Australia/Sydney"])
def test_user_timezone_normalization_accepts_valid_iana_timezones(timezone_name):
    assert normalize_user_timezone(timezone_name) == timezone_name
    assert DetectedLocaleDefaults(timezone=timezone_name).timezone == timezone_name


def test_user_timezone_validation_rejects_unknown_identifiers():
    with pytest.raises(ValueError, match="Unsupported timezone value"):
        normalize_user_timezone("Mars/Olympus_Mons")

    with pytest.raises(ValidationError, match="Unsupported timezone value"):
        DetectedLocaleDefaults(timezone="Mars/Olympus_Mons")
