from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.settings.defaults import DEFAULT_SETTINGS
from app.settings import models as settings_models
from app.settings.models import Settings
from app.settings import utils as settings_utils
from app.middleware import ip_restriction
from app.settings.public_urls import normalize_public_url
from app.settings.validation import SETTINGS_PAGE_MODELS
from app.admin.settings.schema_categories.general import general_schema
from app.admin.settings.schema_categories.login_customization import (
    LoginCustomizationSettings,
)
from app.admin.settings.schema_categories.login_general import (
    LoginGeneralSettings,
    login_general_schema,
)
from app.admin.settings.schema_categories.security import (
    SecuritySettings,
    security_schema,
)


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Settings.__table__.create(bind=engine)
    return sessionmaker(bind=engine)()


def _insert_page(db, page_name: str, data: dict) -> None:
    db.add(
        Settings(
            page_name=page_name,
            data=data,
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


@pytest.mark.parametrize(
    ("page_name", "legacy_data"),
    [
        ("data_governance", {"disclosure_public": True}),
        ("stock_tool", {"provider": "yfinance"}),
    ],
)
def test_removed_settings_pages_are_pruned(monkeypatch, page_name, legacy_data):
    assert page_name not in DEFAULT_SETTINGS
    assert page_name not in SETTINGS_PAGE_MODELS

    db = _db_session()
    _insert_page(db, page_name, legacy_data)
    monkeypatch.setattr(settings_models, "DEFAULT_SETTINGS", {})
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())

    settings_models.initialize_settings(db)

    assert db.query(Settings).filter(Settings.page_name == page_name).first() is None


def test_required_passkey_policy_is_pruned_from_settings(monkeypatch):
    """Startup and admin schemas must expose only optional passkey enablement."""
    assert "require_passkeys" not in DEFAULT_SETTINGS["login_general"]
    assert "require_passkeys" not in LoginGeneralSettings.model_fields

    passkey_section = next(
        section
        for section in login_general_schema.sections
        if section.i18n_title == "schema_login_passkeys_sec0_title"
    )
    assert [field.key for field in passkey_section.fields] == ["enable_passkeys"]

    # Simulate a database created while the removed setting still existed.
    # The normal settings synchronization must discard the obsolete value.
    db = _db_session()
    legacy_login_data = {
        **DEFAULT_SETTINGS["login_general"],
        "require_passkeys": True,
    }
    _insert_page(db, "login_general", legacy_login_data)
    monkeypatch.setattr(
        settings_models,
        "DEFAULT_SETTINGS",
        {"login_general": DEFAULT_SETTINGS["login_general"]},
    )
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())

    settings_models.initialize_settings(db)

    stored = db.query(Settings).filter(Settings.page_name == "login_general").one()
    assert "require_passkeys" not in stored.data


def test_new_server_passkey_default_is_enabled_and_explicit_opt_out_survives_sync(monkeypatch):
    """Seed passkeys on new servers without changing an existing opt-out."""
    assert DEFAULT_SETTINGS["login_general"]["enable_passkeys"] is True
    assert LoginGeneralSettings().enable_passkeys is True

    # A fresh settings table receives the canonical default during startup.
    monkeypatch.setattr(
        settings_models,
        "DEFAULT_SETTINGS",
        {"login_general": DEFAULT_SETTINGS["login_general"]},
    )
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())
    db = _db_session()
    settings_models.initialize_settings(db)
    stored = db.query(Settings).filter(Settings.page_name == "login_general").one()
    assert stored.data["enable_passkeys"] is True

    # Startup synchronization only adds missing keys. An administrator's
    # explicit false value remains authoritative on an existing server.
    existing_db = _db_session()
    existing_data = DEFAULT_SETTINGS["login_general"].copy()
    existing_data["enable_passkeys"] = False
    _insert_page(existing_db, "login_general", existing_data)
    settings_models.initialize_settings(existing_db)
    existing_stored = (
        existing_db.query(Settings)
        .filter(Settings.page_name == "login_general")
        .one()
    )
    assert existing_stored.data["enable_passkeys"] is False


def test_login_passkey_policy_defaults_to_enabled_when_key_is_missing(monkeypatch):
    """Runtime policy reads use the same enabled default as fresh settings."""
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page_data",
        lambda _db, _page: {},
    )

    assert settings_utils.get_login_passkey_policy(object()) == {
        "enable_passkeys": True
    }


def test_bypass_ban_settings_are_removed_and_pruned(monkeypatch):
    """Removed automatic-ban controls must disappear from every settings layer."""

    removed_keys = {
        "block_user_for_bypass_attempt",
        "block_user_for_bypass_attempt_days",
    }
    assert removed_keys.isdisjoint(DEFAULT_SETTINGS["security"])
    assert removed_keys.isdisjoint(SecuritySettings.model_fields)
    assert removed_keys.isdisjoint(
        field.key for section in security_schema.sections for field in section.fields
    )

    # Startup synchronization also deletes values stored by an older build,
    # so exports and later partial updates cannot preserve hidden controls.
    db = _db_session()
    legacy_security_data = {
        **DEFAULT_SETTINGS["security"],
        "block_user_for_bypass_attempt": True,
        "block_user_for_bypass_attempt_days": 7,
    }
    _insert_page(db, "security", legacy_security_data)
    monkeypatch.setattr(
        settings_models,
        "DEFAULT_SETTINGS",
        {"security": DEFAULT_SETTINGS["security"]},
    )
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())

    settings_models.initialize_settings(db)

    stored = db.query(Settings).filter(Settings.page_name == "security").one()
    assert removed_keys.isdisjoint(stored.data)


def test_disabled_ip_restrictions_remain_disabled_with_populated_policy_lists(monkeypatch):
    """Settings reads and startup must preserve explicit IP-policy toggles."""
    db = _db_session()
    security_data = DEFAULT_SETTINGS["security"].copy()
    security_data.update(
        {
            "enable_ip_restrictions": False,
            "enable_ip_address_restrictions": False,
            "enable_ip_country_restrictions": False,
            "block_specific_ip": ["203.0.113.9"],
            "block_country_ip": ["DE"],
        }
    )
    _insert_page(db, "security", security_data)

    # Keep the startup check focused on the security page. The production
    # defaults are reused so initialization still exercises its real merge and
    # persistence behavior.
    monkeypatch.setattr(
        settings_models,
        "DEFAULT_SETTINGS",
        {"security": DEFAULT_SETTINGS["security"]},
    )
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())

    page_data = settings_models.get_settings_page_data(db, "security")
    assert page_data["enable_ip_restrictions"] is False
    assert page_data["enable_ip_address_restrictions"] is False
    assert page_data["enable_ip_country_restrictions"] is False

    # End the read transaction before initialize_settings opens its explicit
    # startup transaction on the same session.
    db.commit()
    settings_models.initialize_settings(db)

    stored = db.query(Settings).filter(Settings.page_name == "security").first()
    assert stored is not None
    assert stored.data["enable_ip_restrictions"] is False
    assert stored.data["enable_ip_address_restrictions"] is False
    assert stored.data["enable_ip_country_restrictions"] is False
    assert stored.data["block_specific_ip"] == ["203.0.113.9"]
    assert stored.data["block_country_ip"] == ["DE"]

    assert asyncio.run(ip_restriction.is_ip_allowed("203.0.113.9", db)) is True


def test_initialize_settings_migrates_legacy_combined_model_domains(monkeypatch):
    """Startup preserves voice settings while splitting the legacy models row."""
    db = _db_session()
    legacy_models_data = {
        **DEFAULT_SETTINGS["models"],
        **DEFAULT_SETTINGS["dictation"],
        **DEFAULT_SETTINGS["read_aloud"],
        **DEFAULT_SETTINGS["realtime"],
        "default_model": "public-model",
        "transcription_enabled": True,
        "transcription_provider_id": "speech-provider",
        "read_aloud_provider_id": "tts-provider",
        "realtime_enabled": True,
        "realtime_provider_id": "live-provider",
    }
    _insert_page(db, "models", legacy_models_data)
    split_defaults = {
        page_name: DEFAULT_SETTINGS[page_name]
        for page_name in ("models", "dictation", "read_aloud", "realtime")
    }
    monkeypatch.setattr(settings_models, "DEFAULT_SETTINGS", split_defaults)
    monkeypatch.setenv("ENCRYPTION_KEY", settings_models.Fernet.generate_key().decode())

    settings_models.initialize_settings(db)

    stored_pages = {
        row.page_name: row.data
        for row in db.query(Settings).all()
    }
    assert set(stored_pages["models"]) == set(DEFAULT_SETTINGS["models"])
    assert stored_pages["models"]["default_model"] == "public-model"
    assert stored_pages["dictation"]["transcription_enabled"] is True
    assert stored_pages["dictation"]["transcription_provider_id"] == "speech-provider"
    assert stored_pages["read_aloud"]["read_aloud_provider_id"] == "tts-provider"
    assert stored_pages["realtime"]["realtime_enabled"] is True
    assert stored_pages["realtime"]["realtime_provider_id"] == "live-provider"


def test_update_page_key_value_rejects_invalid_schema_value(monkeypatch):
    monkeypatch.setattr(settings_utils, "invalidate_settings_cache", lambda: None)
    db = _db_session()
    _insert_page(db, "login_general", DEFAULT_SETTINGS["login_general"].copy())

    with pytest.raises(HTTPException) as exc_info:
        settings_utils.update_page_key_value_by_page_and_key(
            "login_general",
            "otp_length",
            0,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "login_general.otp_length" in str(exc_info.value.detail)

    stored = db.query(Settings).filter(Settings.page_name == "login_general").first()
    assert stored is not None
    assert stored.data["otp_length"] == DEFAULT_SETTINGS["login_general"]["otp_length"]


def test_general_public_urls_use_string_list_and_normalize_updated_origins(monkeypatch):
    """The admin field and update path share the ordered list representation."""
    monkeypatch.setattr(settings_utils, "invalidate_settings_cache", lambda: None)
    db = _db_session()
    _insert_page(db, "general", DEFAULT_SETTINGS["general"].copy())

    public_url_field = next(
        field
        for section in general_schema.sections
        for field in section.fields
        if field.key == "public_url"
    )
    assert public_url_field.type == "string_list"
    assert public_url_field.metadata == {
        "ordered": True,
        "primary_first": True,
    }

    settings_utils.update_page_key_value_by_page_and_key(
        "general",
        "public_url",
        [
            "HTTPS://example.com:443/path",
            "   ",
            "https//example.com",
            "https://example.com/duplicate",
        ],
        db,
    )

    stored = db.query(Settings).filter(Settings.page_name == "general").first()
    assert stored.data["public_url"] == ["https://example.com"]


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://[2001:db8::1]", "https://[2001:db8::1]"),
        ("http://[2001:db8::1]:8080/path", "http://[2001:db8::1]:8080"),
    ],
)
def test_public_url_normalization_preserves_ipv6_brackets(raw_url, expected):
    """IPv6 literal origins remain syntactically valid after normalization."""
    assert normalize_public_url(raw_url) == expected


def test_login_customization_defaults_to_classic_design():
    """Fresh settings and schema fallbacks must use the single-column layout."""
    assert DEFAULT_SETTINGS["login_customization"]["login_design"] == "classic"

    settings_without_design = DEFAULT_SETTINGS["login_customization"].copy()
    settings_without_design.pop("login_design")

    validated = LoginCustomizationSettings.model_validate(settings_without_design)

    assert validated.login_design == "classic"


def test_server_setup_persists_ordered_public_urls_and_returns_primary(monkeypatch):
    """Completing setup stores every origin and identifies the first as primary."""
    monkeypatch.setattr(settings_utils, "invalidate_settings_cache", lambda: None)
    db = _db_session()
    login_general = DEFAULT_SETTINGS["login_general"].copy()
    login_general["show_privacy_notice_link"] = True
    login_general["show_terms_of_service_link"] = True
    _insert_page(db, "login_general", login_general)

    result = settings_utils.complete_server_setup(
        "Omlorix",
        [
            "https://primary.example/app",
            "http://localhost:3000/setup",
        ],
        "pending",
        db,
    )

    general = db.query(Settings).filter(Settings.page_name == "general").one()
    assert general.data["public_url"] == [
        "https://primary.example",
        "http://localhost:3000",
    ]
    stored_login = db.query(Settings).filter(Settings.page_name == "login_general").one()
    assert stored_login.data["show_privacy_notice_link"] is True
    assert stored_login.data["show_terms_of_service_link"] is True
    assert result == {
        "status": "success",
        "public_urls": [
            "https://primary.example",
            "http://localhost:3000",
        ],
        "primary_public_url": "https://primary.example",
    }


def test_auth_log_cleanup_schema_hides_irrelevant_fields():
    auth_log_fields = {
        field.key: field
        for section in security_schema.sections
        if section.i18n_title == "schema_security_sec2_title"
        for field in section.fields
    }

    assert auth_log_fields["auth_logs_cleanup_mode"].dependency == "auth_logs_auto_cleanup_enabled"
    assert auth_log_fields["auth_logs_cleanup_mode"].dependency_value is True
    assert auth_log_fields["auth_logs_max_age_days"].dependency == "auth_logs_cleanup_mode"
    assert auth_log_fields["auth_logs_max_age_days"].dependency_value == "age"
    assert auth_log_fields["auth_logs_max_age_days"].dependency2 == "auth_logs_auto_cleanup_enabled"
    assert auth_log_fields["auth_logs_max_age_days"].dependency2_value is True
    assert auth_log_fields["auth_logs_max_count"].dependency == "auth_logs_cleanup_mode"
    assert auth_log_fields["auth_logs_max_count"].dependency_value == "count"
    assert auth_log_fields["auth_logs_cleanup_interval_seconds"].dependency == "auth_logs_auto_cleanup_enabled"
    assert auth_log_fields["auth_logs_cleanup_interval_seconds"].dependency_value is True


def test_post_deletion_audit_retention_is_a_supported_validated_admin_setting():
    fields = {
        field.key: field
        for section in security_schema.sections
        for field in section.fields
    }

    mode = fields["audit_logs_retention_after_user_delete_mode"]
    days = fields["audit_logs_retention_delete_after_days"]
    assert {option.value for option in mode.options} == {
        "delete_instantly",
        "delete_after_days",
        "retain",
    }
    assert days.dependency == "audit_logs_retention_after_user_delete_mode"
    assert days.dependency_value == "delete_after_days"
    assert days.attributes.min == 0
    assert days.attributes.max == 3650

    with pytest.raises(Exception):
        SecuritySettings(audit_logs_retention_after_user_delete_mode="unsupported")
    with pytest.raises(Exception):
        SecuritySettings(audit_logs_retention_delete_after_days=3651)


def test_security_network_restriction_schema_dependencies_are_present():
    security_fields = {
        field.key: field
        for section in security_schema.sections
        if section.i18n_title == "schema_security_sec1_title"
        for field in section.fields
    }

    assert security_fields["enable_ip_address_restrictions"].dependency == "enable_ip_restrictions"
    assert security_fields["ip_address_restriction_mode"].dependency == "enable_ip_address_restrictions"
    assert security_fields["ip_address_restriction_mode"].dependency2 == "enable_ip_restrictions"
    assert security_fields["allow_specific_ip"].dependency == "ip_address_restriction_mode"
    assert security_fields["allow_specific_ip"].dependency_value == "allowlist"
    assert security_fields["allow_specific_ip"].dependency2 == "enable_ip_restrictions"
    assert security_fields["allow_specific_ip"].dependency3 == "enable_ip_address_restrictions"
    assert security_fields["block_specific_ip"].dependency == "ip_address_restriction_mode"
    assert security_fields["block_specific_ip"].dependency_value == "blocklist"
    assert security_fields["block_specific_ip"].dependency2 == "enable_ip_restrictions"
    assert security_fields["block_specific_ip"].dependency3 == "enable_ip_address_restrictions"
    assert security_fields["enable_ip_country_restrictions"].dependency == "enable_ip_restrictions"
    assert security_fields["ip_country_restriction_mode"].dependency == "enable_ip_country_restrictions"
    assert security_fields["ip_country_restriction_mode"].dependency2 == "enable_ip_restrictions"
    assert security_fields["allow_country_ip"].dependency == "ip_country_restriction_mode"
    assert security_fields["allow_country_ip"].dependency_value == "allowlist"
    assert security_fields["allow_country_ip"].dependency2 == "enable_ip_restrictions"
    assert security_fields["allow_country_ip"].dependency3 == "enable_ip_country_restrictions"
    assert security_fields["block_country_ip"].dependency == "ip_country_restriction_mode"
    assert security_fields["block_country_ip"].dependency_value == "blocklist"
    assert security_fields["block_country_ip"].dependency2 == "enable_ip_restrictions"
    assert security_fields["block_country_ip"].dependency3 == "enable_ip_country_restrictions"
    assert security_fields["allow_ip_if_no_country_found"].dependency == "ip_country_restriction_mode"
    assert security_fields["allow_ip_if_no_country_found"].dependency_value == "allowlist"
    assert security_fields["allow_ip_if_no_country_found"].dependency2 == "enable_ip_restrictions"
    assert security_fields["allow_ip_if_no_country_found"].dependency3 == "enable_ip_country_restrictions"
    assert security_fields["check_ip_location_provider"].dependency == "enable_ip_country_restrictions"
    assert security_fields["check_ip_location_provider"].dependency2 == "enable_ip_restrictions"
    assert security_fields["ipinfo"].dependency == "check_ip_location_provider"
    assert security_fields["ipinfo"].dependency_value == ["ipinfo"]
    assert security_fields["ipinfo"].dependency2 == "enable_ip_restrictions"
    assert security_fields["ipinfo"].dependency2_value is True
    assert security_fields["ipinfo"].dependency3 == "enable_ip_country_restrictions"
    assert security_fields["ipinfo"].input_type == "password"
    assert security_fields["ipinfo"].redact_value is True
    assert security_fields["ipinfo"].masked_placeholder is True
    assert security_fields["ipstack"].dependency == "check_ip_location_provider"
    assert security_fields["ipstack"].dependency_value == ["ipstack"]
    assert security_fields["ipstack"].dependency2 == "enable_ip_restrictions"
    assert security_fields["ipstack"].dependency2_value is True
    assert security_fields["ipstack"].dependency3 == "enable_ip_country_restrictions"
    assert security_fields["ipstack"].input_type == "password"
    assert security_fields["ipstack"].redact_value is True
    assert security_fields["ipstack"].masked_placeholder is True
    assert "only_allow_specific_ip" not in security_fields
    assert "only_allow_ip_from_specific_countries" not in security_fields
    security_section = next(
        section
        for section in security_schema.sections
        if section.i18n_title == "schema_security_sec1_title"
    )
    assert [field.key for field in security_section.fields[-2:]] == [
        "trust_proxy_headers",
        "trusted_proxies",
    ]
    assert security_fields["trusted_proxies"].dependency == "trust_proxy_headers"
    assert security_fields["trusted_proxies"].dependency_value is True
