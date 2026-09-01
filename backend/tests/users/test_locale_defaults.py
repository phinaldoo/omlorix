from unittest.mock import patch

from app.users.schemas import DetectedLocaleDefaults
from app.users.utils import dismiss_user_welcome_card, initialize_user_locale_defaults


def test_locale_defaults_fill_blank_browser_detected_preferences():
    db = object()
    current = {
        "general": {
            "language": "",
            "country": "",
            "timezone": "",
        }
    }

    with patch("app.users.utils.get_user_settings", return_value=current), patch(
        "app.users.utils.update_user_settings_bulk",
        return_value={
            "general": {
                "language": "de",
                "country": "de",
                "timezone": "Europe/Berlin",
            }
        },
    ) as update_settings:
        result = initialize_user_locale_defaults(
            db,
            "user-1",
            language="de",
            country="de",
            timezone="Europe/Berlin",
        )

    update_settings.assert_called_once_with(
        "user-1",
        {
            "general": {
                "language": "de",
                "country": "de",
                "timezone": "Europe/Berlin",
            }
        },
        db,
    )
    assert result["updated"]["general"]["timezone"] == "Europe/Berlin"


def test_locale_defaults_never_overwrite_existing_user_choices():
    current = {
        "general": {
            "language": "fr",
            "country": "fr",
            "timezone": "Europe/Paris",
        }
    }

    with patch("app.users.utils.get_user_settings", return_value=current), patch(
        "app.users.utils.update_user_settings_bulk"
    ) as update_settings:
        result = initialize_user_locale_defaults(
            object(),
            "user-1",
            language="de",
            country="de",
            timezone="Europe/Berlin",
        )

    update_settings.assert_not_called()
    assert result == {"status": "success", "updated": {}}


def test_detected_locale_schema_normalizes_timezone_aliases():
    payload = DetectedLocaleDefaults(
        language="en",
        country="us",
        timezone="US/Pacific",
    )

    assert payload.timezone == "America/Los_Angeles"


def test_welcome_card_dismissal_is_persisted():
    db = object()
    with patch("app.users.utils.update_user_settings_bulk", return_value={}) as update_settings:
        result = dismiss_user_welcome_card(db, "user-1")

    update_settings.assert_called_once_with(
        "user-1",
        {"states": {"welcome_card_dismissed": True}},
        db,
    )
    assert result == {"status": "success"}
