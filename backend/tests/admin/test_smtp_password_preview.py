from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils
from app.auth.password_policy import MINIMUM_SECURE_PASSWORD_LENGTH
from app.auth.social import APPLE_PRIVATE_KEY_ERROR_DETAIL
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings import utils as settings_utils
from app.settings.models import SENSITIVE_SETTING_RESPONSE_MASK
from app.utils.helpers import _mask_secret_preview
from app.utils.schemas import _mask_preview


def _find_field(schema_payload: dict, field_key: str) -> dict:
    for section in schema_payload["sections"]:
        for field in section.get("fields", []):
            if field.get("key") == field_key:
                return field
    raise AssertionError(f"Field {field_key!r} not found")


class _DummyDB:
    def commit(self) -> None:
        return None

    def refresh(self, _record) -> None:
        return None


def test_smtp_password_preview_shows_at_most_three_characters_for_long_secret(monkeypatch):
    def get_settings_page(_db, page):
        return SimpleNamespace(data={"smtp_password": "supersecret"}) if page == "login_general" else None

    def get_settings_page_data(_db, page, **_kwargs):
        return {"smtp_password": "supersecret"} if page == "login_general" else {}

    monkeypatch.setattr(admin_utils, "get_settings_page", get_settings_page)
    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)
    monkeypatch.setattr(admin_utils, "_get_group_options", lambda _db: [{"value": "default", "label": "Default"}])
    monkeypatch.setattr(admin_utils, "_set_schema_field_options", lambda *_args, **_kwargs: False)

    response = admin_utils.get_admin_settings_schema_response("login_general", include_values=True, db=object())

    smtp_password_field = _find_field(response, "smtp_password")
    assert smtp_password_field["placeholder"] == "sup..."
    assert "smtp_password" not in response["values"]


def test_smtp_password_preview_hides_short_secret_prefixes(monkeypatch):
    def get_settings_page(_db, page):
        return SimpleNamespace(data={"smtp_password": "secret"}) if page == "login_general" else None

    def get_settings_page_data(_db, page, **_kwargs):
        return {"smtp_password": "secret"} if page == "login_general" else {}

    monkeypatch.setattr(admin_utils, "get_settings_page", get_settings_page)
    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)
    monkeypatch.setattr(admin_utils, "_get_group_options", lambda _db: [{"value": "default", "label": "Default"}])
    monkeypatch.setattr(admin_utils, "_set_schema_field_options", lambda *_args, **_kwargs: False)

    response = admin_utils.get_admin_settings_schema_response("login_general", include_values=True, db=object())

    smtp_password_field = _find_field(response, "smtp_password")
    assert smtp_password_field["placeholder"] == "Enter SMTP password"
    assert (
        smtp_password_field["i18n_placeholder"]
        == "schema_login_general_smtp_password_placeholder"
    )
    assert "smtp_password" not in response["values"]


def test_login_general_schema_clamps_legacy_stored_password_floor(monkeypatch):
    """An unsafe persisted value must not make the admin schema unreadable."""

    stored_login_settings = {
        **DEFAULT_SETTINGS["login_general"],
        "minimum_password_length": 1,
    }

    def get_settings_page_data(_db, page, **_kwargs):
        return stored_login_settings if page == "login_general" else {}

    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)
    monkeypatch.setattr(
        admin_utils,
        "_get_group_options",
        lambda _db: [{"value": "default", "label": "Default"}],
    )

    response = admin_utils.get_admin_settings_schema_response(
        "login_general",
        include_values=True,
        db=object(),
    )

    assert (
        response["values"]["minimum_password_length"]
        == MINIMUM_SECURE_PASSWORD_LENGTH
    )
    assert (
        _find_field(response, "minimum_password_length")["value"]
        == MINIMUM_SECURE_PASSWORD_LENGTH
    )


def test_secret_preview_helpers_do_not_expose_short_values_or_more_than_three_characters():
    assert _mask_secret_preview("abcdef") is None
    assert _mask_secret_preview("abcdefg") == "abc..."
    assert _mask_secret_preview("abcdefg", visible_chars=6) == "abc..."
    assert _mask_preview("abcdef") is None
    assert _mask_preview("abcdefg", visible_chars=6) == "abc..."


def test_masked_secret_detection_requires_a_non_empty_prefix():
    assert admin_utils._is_masked_preview("sup...", "supersecret") is True
    assert admin_utils._is_masked_preview("...", "secret") is False


def test_backend_masked_auth_secret_fields_are_marked_for_masked_placeholders():
    schema_fields = {
        "login_social": (
            admin_utils.login_social_schema,
            {
                "google_client_secret",
                "github_client_secret",
                "slack_client_secret",
                "microsoft_client_secret",
                "apple_private_key",
            },
        ),
        "login_enterprise_sso": (
            admin_utils.login_enterprise_sso_schema,
            {
                "oidc_client_secret",
                "scim_bearer_token",
            },
        ),
        "login_ldap": (admin_utils.login_ldap_schema, {"ldap_bind_password"}),
    }

    for page_key, (schema, secret_keys) in schema_fields.items():
        fields_by_key = {
            field.key: field
            for section in schema.sections
            for field in section.fields
        }
        for field_key in secret_keys:
            field = fields_by_key[field_key]
            assert field.input_type == "password", (
                f"{page_key}.{field_key} should be a password input"
            )
            assert field.redact_value is True, (
                f"{page_key}.{field_key} should redact submitted values"
            )
            assert field.masked_placeholder is True, (
                f"{page_key}.{field_key} should preserve unchanged masked blanks"
            )

    apple_private_key_field = {
        field.key: field
        for section in admin_utils.login_social_schema.sections
        for field in section.fields
    }["apple_private_key"]
    assert apple_private_key_field.type == "textarea"
    assert apple_private_key_field.rows == 6


def test_login_enterprise_sso_default_group_fields_use_group_select_options(monkeypatch):
    group_options = [
        {"value": "default", "label": "Default"},
        {"value": "engineering", "label": "Engineering"},
    ]

    def get_settings_page_data(_db, page, **_kwargs):
        if page == "login_enterprise_sso":
            return {
                "scim_default_group": "engineering",
                "saml_default_group": "missing-group",
                "oidc_default_group": "default",
            }
        return {}

    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)
    monkeypatch.setattr(admin_utils, "_get_group_options", lambda _db: group_options)

    response = admin_utils.get_admin_settings_schema_response(
        "login_enterprise_sso",
        include_values=True,
        db=object(),
    )

    default_group_fields = (
        "scim_default_group",
        "saml_default_group",
        "oidc_default_group",
    )
    serialized_group_options = [
        {**option, "translatable": True}
        for option in group_options
    ]
    for field_key in default_group_fields:
        field = _find_field(response, field_key)
        assert field["type"] == "select"
        assert field["options"] == serialized_group_options

    assert response["values"]["scim_default_group"] == "engineering"
    assert "saml_default_group" not in response["values"]
    assert _find_field(response, "saml_default_group").get("value") is None


def test_login_enterprise_sso_update_rejects_missing_default_group(monkeypatch):
    monkeypatch.setattr(
        admin_utils,
        "_get_group_options",
        lambda _db: [{"value": "default", "label": "Default"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_enterprise_sso",
            payload={"saml_default_group": "missing-group"},
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Selected group for 'saml_default_group' does not exist."


def test_login_social_secret_placeholders_do_not_preview_encrypted_values(monkeypatch):
    def get_settings_page(_db, page):
        if page == "login_social":
            return SimpleNamespace(
                data={
                    "google_client_secret": "enc:v1:ciphertext",
                    "apple_private_key": "enc:v1:apple-ciphertext",
                }
            )
        return None

    def get_settings_page_data(_db, page, **_kwargs):
        if page == "login_social":
            return {
                "google_client_secret": "enc:v1:ciphertext",
                "apple_private_key": "enc:v1:apple-ciphertext",
            }
        return {}

    monkeypatch.setattr(admin_utils, "get_settings_page", get_settings_page)
    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)

    response = admin_utils.get_admin_settings_schema_response("login_social", include_values=True, db=object())

    google_secret_field = _find_field(response, "google_client_secret")
    apple_private_key_field = _find_field(response, "apple_private_key")
    assert google_secret_field["placeholder"] == SENSITIVE_SETTING_RESPONSE_MASK
    assert apple_private_key_field["placeholder"] == SENSITIVE_SETTING_RESPONSE_MASK
    assert "google_client_secret" not in response["values"]
    assert "apple_private_key" not in response["values"]


def test_login_social_update_rejects_malformed_apple_private_key(monkeypatch):
    settings_record = SimpleNamespace(data=DEFAULT_SETTINGS["login_social"].copy(), updated_at=None)

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "login_social" else None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: settings_record.data.copy() if page == "login_social" else {},
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_social",
            payload={
                "enable_apple_login": True,
                "apple_client_id": "com.example.service",
                "apple_team_id": "TEAMID1234",
                "apple_key_id": "KEYID12345",
                "apple_private_key": "base64-key-body-only",
            },
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == APPLE_PRIVATE_KEY_ERROR_DETAIL
    assert settings_record.data["apple_private_key"] == ""


def test_login_social_update_rejects_enabled_apple_login_without_private_key(monkeypatch):
    settings_record = SimpleNamespace(data=DEFAULT_SETTINGS["login_social"].copy(), updated_at=None)

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "login_social" else None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: settings_record.data.copy() if page == "login_social" else {},
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_social",
            payload={
                "enable_apple_login": True,
                "apple_client_id": "com.example.service",
                "apple_team_id": "TEAMID1234",
                "apple_key_id": "KEYID12345",
                "apple_private_key": "",
            },
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == APPLE_PRIVATE_KEY_ERROR_DETAIL
    assert settings_record.data["enable_apple_login"] is False


def test_key_value_update_rejects_enabled_apple_login_without_private_key(monkeypatch):
    settings_record = SimpleNamespace(data=DEFAULT_SETTINGS["login_social"].copy(), page_name="login_social")

    monkeypatch.setattr(settings_utils, "get_settings_page", lambda _db, page: settings_record)
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: settings_record.data.copy() if page == "login_social" else {},
    )

    with pytest.raises(HTTPException) as exc_info:
        settings_utils.update_page_key_value_by_page_and_key(
            "login_social",
            "enable_apple_login",
            True,
            _DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == APPLE_PRIVATE_KEY_ERROR_DETAIL
    assert settings_record.data["enable_apple_login"] is False
