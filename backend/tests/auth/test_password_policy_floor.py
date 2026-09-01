"""Regression coverage for the non-negotiable password length floor."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.admin.settings.schema_categories.login_general import (
    LoginGeneralSettings,
    login_general_schema,
)
from app.auth.password_policy import (
    MINIMUM_SECURE_PASSWORD_LENGTH,
    effective_minimum_password_length,
    normalize_stored_login_general_settings,
)
from app.settings.validation import validate_settings_page_values


@pytest.mark.parametrize("configured", [None, "", 0, 1, 9, "invalid"])
def test_unsafe_or_missing_values_are_clamped(configured) -> None:
    assert effective_minimum_password_length(configured) == MINIMUM_SECURE_PASSWORD_LENGTH


def test_stronger_configured_value_is_preserved() -> None:
    assert effective_minimum_password_length(16) == 16


def test_admin_payload_rejects_an_unsafe_minimum() -> None:
    with pytest.raises(ValidationError):
        LoginGeneralSettings(minimum_password_length=1)


def test_stored_unsafe_minimum_is_clamped_before_partial_update_validation() -> None:
    """Legacy storage must not block reading or changing unrelated settings."""

    validated = validate_settings_page_values(
        "login_general",
        {"enable_signup": False},
        current_values={"minimum_password_length": 1},
    )

    assert validated["enable_signup"] is False
    assert validated["minimum_password_length"] == MINIMUM_SECURE_PASSWORD_LENGTH


def test_stored_settings_normalization_does_not_mutate_the_source() -> None:
    """Read normalization returns a safe copy of the persisted mapping."""

    stored = {"minimum_password_length": 1, "enable_signup": True}

    normalized = normalize_stored_login_general_settings(stored)

    assert normalized["minimum_password_length"] == MINIMUM_SECURE_PASSWORD_LENGTH
    assert stored["minimum_password_length"] == 1


def test_admin_schema_advertises_the_same_floor() -> None:
    field = next(
        field
        for section in login_general_schema.sections
        for field in section.fields
        if field.key == "minimum_password_length"
    )
    assert field.attributes.min == MINIMUM_SECURE_PASSWORD_LENGTH
