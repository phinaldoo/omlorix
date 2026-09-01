from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils
from app.admin.settings.schema_categories.login_enterprise_sso import (
    LoginEnterpriseSSOSettings,
    login_enterprise_sso_schema,
)
from app.admin.settings.schema_categories.login_general import LoginGeneralSettings
from app.admin.settings.schema_categories.login_ldap import LoginLDAPSettings
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings.models import SENSITIVE_SETTING_RESPONSE_MASK


class _DummyDB:
    """Provide the transaction hooks used by the admin settings update helper."""

    def commit(self) -> None:
        return None

    def refresh(self, _record) -> None:
        return None


def _enterprise_sso_schema_fields() -> dict[str, object]:
    """Return every control rendered by the Enterprise SSO settings page."""

    return {
        field.key: field
        for section in login_enterprise_sso_schema.sections
        for field in section.fields
    }


def test_enterprise_sso_defaults_model_and_ui_expose_the_same_settings_contract():
    """Do not leave persisted Enterprise SSO behavior outside the admin UI."""

    default_keys = set(DEFAULT_SETTINGS["login_enterprise_sso"])
    model_keys = set(LoginEnterpriseSSOSettings.model_fields)
    schema_keys = set(_enterprise_sso_schema_fields())

    assert model_keys == default_keys
    assert schema_keys == default_keys


def test_previous_scim_token_placeholder_has_a_stable_translation_key():
    field = _enterprise_sso_schema_fields()["scim_previous_bearer_token"]

    assert (
        field.i18n_placeholder
        == "schema_login_enterprise_sso_scim_previous_bearer_token_placeholder"
    )


def test_enterprise_sso_sections_follow_the_operator_workflow():
    """Group the single provider configurations before provisioning."""

    hierarchy = [
        (section.group_title, section.title)
        for section in login_enterprise_sso_schema.sections
    ]

    assert hierarchy == [
        ("Identity Providers", "SAML 2.0"),
        ("Identity Providers", "OpenID Connect"),
        ("User Provisioning", "SCIM 2.0"),
    ]

    sections_by_title = {
        section.title: section for section in login_enterprise_sso_schema.sections
    }
    assert (
        sections_by_title["SCIM 2.0"].i18n_description
        == "schema_login_enterprise_sso_scim_desc"
    )
    fields_by_section = {
        section.title: {field.key for field in section.fields}
        for section in login_enterprise_sso_schema.sections
    }
    assert "saml_advanced_settings" in fields_by_section["SAML 2.0"]
    assert "saml_identity_policy" in fields_by_section["SAML 2.0"]
    assert "oidc_advanced_settings" in fields_by_section["OpenID Connect"]
    assert "oidc_identity_policy" in fields_by_section["OpenID Connect"]


def test_enterprise_sso_model_validates_oidc_scopes_and_allowed_domains():
    """The single OIDC configuration normalizes scopes and access domains."""

    settings = LoginEnterpriseSSOSettings(
        oidc_scopes=["openid", "email", "email", "profile"],
        oidc_allowed_domains=[" Example.COM ", "example.com"],
    ).model_dump()

    assert settings["oidc_scopes"] == ["openid", "email", "profile"]
    assert settings["oidc_allowed_domains"] == ["example.com"]


@pytest.mark.parametrize(
    "payload",
    (
        {"oidc_scopes": ["email", "profile"]},
        {"oidc_allowed_domains": ["not a domain"]},
        {"oidc_discovery_url": "file:///etc/passwd"},
        {"domain_routing": []},
        {"oidc_configurations": []},
        {"saml_configurations": []},
        {"enable_trusted_headers": True},
    ),
)
def test_enterprise_sso_model_rejects_invalid_provider_values(payload):
    """Invalid scopes, domains, and endpoint schemes fail closed."""

    with pytest.raises(ValidationError):
        LoginEnterpriseSSOSettings(**payload)


@pytest.mark.parametrize(
    "toggle",
    (
        "enable_saml",
        "enable_oidc",
        "enable_scim",
    ),
)
def test_enterprise_sso_primary_toggles_do_not_require_ready_configuration(toggle):
    """Administrators can reveal and save every Enterprise SSO setup workflow."""

    settings = LoginEnterpriseSSOSettings(**{toggle: True})

    assert getattr(settings, toggle) is True


def test_enterprise_sso_nested_toggles_do_not_require_ready_configuration():
    """SAML request signing may be selected before credentials exist."""

    settings = LoginEnterpriseSSOSettings(
        saml_advanced_settings={"sign_authn_requests": True},
    )

    assert settings.saml_advanced_settings.sign_authn_requests is True


def test_automatic_role_defaults_reject_administrative_roles():
    """External/JIT provisioning must never be a second admin-grant path."""

    with pytest.raises(ValidationError):
        LoginGeneralSettings(default_user_role="admin")
    with pytest.raises(ValidationError):
        LoginLDAPSettings(ldap_default_role="admin")

    for field_name in (
        "scim_default_role",
        "saml_default_role",
        "oidc_default_role",
    ):
        with pytest.raises(ValidationError):
            LoginEnterpriseSSOSettings(**{field_name: "admin"})


def test_admin_enterprise_sso_schema_masks_provider_secrets(monkeypatch):
    """Return the single provider configurations without disclosing secrets."""

    def get_settings_page_data(_db, page, **_kwargs):
        if page != "login_enterprise_sso":
            return {}
        # This helper returns decrypted settings to server-side callers. The
        # schema response must mask secrets before crossing the API boundary.
        return {
            "oidc_client_id": "oidc-client-id",
            "oidc_client_secret": "oidc-client-secret-plaintext",
            "saml_advanced_settings": {
                "idp_entity_id": "urn:example:idp",
                "sp_private_key": "saml-private-key-plaintext",
            },
        }

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        get_settings_page_data,
    )
    monkeypatch.setattr(admin_utils, "_get_group_options", lambda _db: [])

    response = admin_utils.get_admin_settings_schema_response(
        "login_enterprise_sso",
        include_values=True,
        db=object(),
    )

    assert (
        response["values"]["saml_advanced_settings"]["sp_private_key"]
        == SENSITIVE_SETTING_RESPONSE_MASK
    )
    assert response["values"]["oidc_client_id"] == "oidc-client-id"
    assert "oidc_client_secret" not in response["values"]
    assert "oidc-client-secret-plaintext" not in json.dumps(response)
    assert "saml-private-key-plaintext" not in json.dumps(response)


def test_legacy_enabled_saml_can_be_read_and_repaired_by_partial_update(monkeypatch):
    """Migrate the former shared entity ID before strict provider validation."""

    legacy_values = deepcopy(DEFAULT_SETTINGS["login_enterprise_sso"])
    legacy_values.update(
        {
            "enable_saml": True,
            "saml_entity_id": "urn:legacy:saml-entity",
            "saml_sso_url": "https://idp.example.com/sso",
            "saml_x509_cert": "legacy-idp-certificate",
        }
    )
    legacy_values.pop("saml_advanced_settings", None)

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: legacy_values
        if page == "login_enterprise_sso"
        else {},
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_group_options",
        lambda _db: [{"value": "default", "label": "Default"}],
    )

    response = admin_utils.get_admin_settings_schema_response(
        "login_enterprise_sso", include_values=True, db=object()
    )

    assert response["values"]["enable_saml"] is True
    assert (
        response["values"]["saml_advanced_settings"]["idp_entity_id"]
        == "urn:legacy:saml-entity"
    )

    settings_record = SimpleNamespace(
        data=legacy_values,
        page_name="login_enterprise_sso",
        updated_at=None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record
        if page == "login_enterprise_sso"
        else None,
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)

    admin_utils.update_admin_settings_values_for_page(
        page="login_enterprise_sso",
        payload={"saml_button_text": "Use SSO"},
        db=_DummyDB(),
    )

    assert settings_record.data["enable_saml"] is True
    assert (
        settings_record.data["saml_advanced_settings"]["idp_entity_id"]
        == "urn:legacy:saml-entity"
    )
    assert settings_record.data["saml_button_text"] == "Use SSO"


def test_incomplete_provider_activation_is_preserved_on_admin_page(monkeypatch):
    """Do not silently undo an administrator's saved activation choice."""

    legacy_values = deepcopy(DEFAULT_SETTINGS["login_enterprise_sso"])
    legacy_values["enable_saml"] = True
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: legacy_values
        if page == "login_enterprise_sso"
        else {},
    )
    monkeypatch.setattr(admin_utils, "_get_group_options", lambda _db: [])

    response = admin_utils.get_admin_settings_schema_response(
        "login_enterprise_sso", include_values=True, db=object()
    )

    assert response["values"]["enable_saml"] is True


def test_admin_enterprise_sso_update_preserves_masked_client_secret(monkeypatch):
    """Keep the encrypted OIDC secret when an admin edits the client ID."""

    settings_record = SimpleNamespace(
        data=deepcopy(DEFAULT_SETTINGS["login_enterprise_sso"]),
        page_name="login_enterprise_sso",
        updated_at=None,
    )
    settings_record.data["oidc_client_id"] = "old-client-id"
    settings_record.data["oidc_client_secret"] = "enc:v1:stored-client-secret"

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "login_enterprise_sso" else None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: settings_record.data
        if page == "login_enterprise_sso"
        else {},
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_group_options",
        lambda _db: [{"value": "default", "label": "Default"}],
    )
    # Treat the fixture ciphertext as valid so the production encryption
    # helper can verify and preserve it without requiring a test key.
    monkeypatch.setattr("app.settings.models.decrypt_value", lambda value: value)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)
    changed_keys = admin_utils.update_admin_settings_values_for_page(
        page="login_enterprise_sso",
        payload={
            "oidc_client_id": "new-client-id",
            "oidc_client_secret": SENSITIVE_SETTING_RESPONSE_MASK,
        },
        db=_DummyDB(),
    )

    assert "oidc_client_id" in changed_keys
    assert settings_record.data["oidc_client_id"] == "new-client-id"
    assert settings_record.data["oidc_client_secret"] == "enc:v1:stored-client-secret"


def test_admin_enterprise_sso_oidc_configuration_persists_end_to_end(monkeypatch):
    """The one OIDC provider configuration persists as ordinary settings."""

    settings_record = SimpleNamespace(
        data=deepcopy(DEFAULT_SETTINGS["login_enterprise_sso"]),
        page_name="login_enterprise_sso",
        updated_at=None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "login_enterprise_sso" else None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, page, **_kwargs: settings_record.data
        if page == "login_enterprise_sso"
        else {},
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_group_options",
        lambda _db: [{"value": "default", "label": "Default"}],
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)

    monkeypatch.setattr("app.settings.models.encrypt_value", lambda value: f"encrypted-{value}")
    monkeypatch.setattr("app.settings.models.decrypt_value", lambda value: value)

    changes = admin_utils.update_admin_settings_values_for_page(
        page="login_enterprise_sso",
        payload={
            "enable_oidc": True,
            "oidc_client_id": "client-id",
            "oidc_client_secret": "enc:v1:client-secret",
            "oidc_discovery_url": "https://idp.example.com/.well-known/openid-configuration",
            "oidc_scopes": ["openid", "email", "groups"],
        },
        db=_DummyDB(),
    )

    assert set(changes) == {
        "enable_oidc",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_discovery_url",
        "oidc_scopes",
    }
    assert settings_record.data["oidc_scopes"] == [
        "openid",
        "email",
        "groups",
    ]
