from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.init import (
    _coerce_llm_access_permissions_value,
    _sync_with_defaults,
)
from app.users.schemas import (
    DetectedLocaleDefaults,
    LLMAccessPermissions,
    UpdateUserSettingsSelect,
)
from app.users.utils import _sanitize_user_archive_settings


RETIRED_LOCALE_FIELDS = {"currency", "date_format", "time_format", "week_start"}


def test_non_boolean_llm_permissions_fall_back_to_secure_field_defaults():
    """Legacy truthy values must not grant access during settings sync."""

    normalized, changed = _coerce_llm_access_permissions_value(
        {
            "first_name": True,
            "language": False,
            "country": "false",
            "timezone": 1,
            "location": None,
        }
    )

    assert changed is True
    assert normalized == {
        "first_name": True,
        "language": False,
        "country": False,
        "timezone": False,
        "location": False,
    }


def test_retired_locale_settings_are_absent_and_legacy_state_is_pruned():
    """Removed metadata must not survive schemas, settings sync, or archives."""

    assert RETIRED_LOCALE_FIELDS.isdisjoint(DEFAULT_USER_SETTINGS["general"])
    assert RETIRED_LOCALE_FIELDS.isdisjoint(
        DEFAULT_USER_SETTINGS["security"][
            "allow_llm_to_access_personal_information"
        ]
    )
    assert RETIRED_LOCALE_FIELDS.isdisjoint(DetectedLocaleDefaults.model_fields)
    assert RETIRED_LOCALE_FIELDS.isdisjoint(UpdateUserSettingsSelect.model_fields)
    assert RETIRED_LOCALE_FIELDS.isdisjoint(LLMAccessPermissions.model_fields)

    changed, merged = _sync_with_defaults(
        {
            "general": {field_name: "legacy" for field_name in RETIRED_LOCALE_FIELDS},
            "security": {
                "allow_llm_to_access_personal_information": {
                    field_name: True for field_name in RETIRED_LOCALE_FIELDS
                }
            },
        }
    )
    assert changed is True
    assert RETIRED_LOCALE_FIELDS.isdisjoint(merged["general"])
    assert RETIRED_LOCALE_FIELDS.isdisjoint(
        merged["security"]["allow_llm_to_access_personal_information"]
    )

    sanitized = _sanitize_user_archive_settings(
        {
            "general": {
                **{field_name: "legacy" for field_name in RETIRED_LOCALE_FIELDS},
                "language": "en",
            },
            "security": {
                "allow_llm_to_access_personal_information": {
                    **{field_name: True for field_name in RETIRED_LOCALE_FIELDS},
                    "language": True,
                }
            },
        }
    )
    assert sanitized["general"] == {"language": "en"}
    assert sanitized["security"]["allow_llm_to_access_personal_information"] == {
        "language": True
    }
