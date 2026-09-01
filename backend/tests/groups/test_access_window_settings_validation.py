import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.admin.groups.schemas import FIELD_SCHEMA_BY_KEY
from app.groups.settings_validation import (
    sanitize_group_settings_for_storage,
    validate_group_settings,
)


def test_group_settings_reject_enabled_blocklist_without_restrictions():
    settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    settings["access_windows"]["enabled"] = True
    settings["access_windows"]["mode"] = "blocklist"
    settings["access_windows"]["rules"] = []

    with pytest.raises(ValueError, match="settings.access_windows"):
        validate_group_settings(settings)


def test_group_settings_reject_malformed_access_window_rules():
    with pytest.raises(ValueError, match=r"settings\.access_windows\.rules"):
        sanitize_group_settings_for_storage(
            {
                "access_windows": {
                    "rules": [
                        {
                            "start": "bad",
                            "end": "09:00",
                            "days": [0, 1, 2],
                            "label": "Broken",
                        }
                    ]
                }
            }
        )


def test_partial_payload_is_completed_into_an_independent_snapshot():
    sanitized = sanitize_group_settings_for_storage(
        {
            "access_windows": {
                "blocked_message": "Come back later",
            }
        }
    )

    expected = deepcopy(DEFAULT_GROUP_SETTINGS)
    expected["access_windows"]["blocked_message"] = "Come back later"
    assert sanitized == expected


def test_access_window_validation_drops_unknown_fields():
    settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    settings["access_windows"]["unsupported"] = True

    validated = validate_group_settings(settings)

    expected = deepcopy(DEFAULT_GROUP_SETTINGS)
    assert validated == expected


def test_group_schema_timezone_field_is_select_with_all_timezone_options():
    field = FIELD_SCHEMA_BY_KEY["settings.access_windows.timezone"]
    option_values = [option.value for option in field.options or []]

    assert field.type == "select"
    assert option_values[0] == "UTC"
    assert "Europe/Berlin" in option_values
    assert "America/New_York" in option_values
    assert "Pacific/Auckland" in option_values
    assert len(option_values) > 100
