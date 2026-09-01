import pytest

from app.groups.settings_validation import sanitize_group_settings_for_storage


@pytest.mark.parametrize(
    ("field_name", "fractional_value"),
    [
        ("max_active_accounts", 1.5),
        ("credential_length", 16.5),
    ],
)
def test_temporary_account_limits_reject_fractional_values(
    field_name: str,
    fractional_value: float,
) -> None:
    """Reject fractional counts and lengths before group settings are stored."""
    with pytest.raises(ValueError, match="must be an integer"):
        sanitize_group_settings_for_storage(
            {"temporary_accounts": {field_name: fractional_value}}
        )


def test_temporary_account_limits_normalize_integral_values() -> None:
    """Persist integral numeric representations as actual integers."""
    settings = sanitize_group_settings_for_storage(
        {
            "temporary_accounts": {
                "max_active_accounts": 25.0,
                "credential_length": "32",
            }
        }
    )

    assert settings["temporary_accounts"]["max_active_accounts"] == 25
    assert type(settings["temporary_accounts"]["max_active_accounts"]) is int
    assert settings["temporary_accounts"]["credential_length"] == 32
    assert type(settings["temporary_accounts"]["credential_length"]) is int
