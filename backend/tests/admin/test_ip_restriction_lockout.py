from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _otel_test_stubs import install_otel_stubs

install_otel_stubs()

from app.admin.settings import utils as admin_utils
from app.auth import models as auth_models
from app.settings.defaults import DEFAULT_SETTINGS
from app.utils import ip_restrictions


class _DummyDB:
    """Minimal database stand-in for validation paths that do not persist."""

    def commit(self) -> None:
        return None

    def refresh(self, _record) -> None:
        return None


def test_ip_restrictions_environment_bypass(monkeypatch):
    """The break-glass environment flag must bypass database-backed IP policy."""
    monkeypatch.setenv("OMLORIX_DISABLE_IP_RESTRICTIONS", "true")

    assert ip_restrictions.ip_restrictions_disabled_by_environment() is True


def test_blocked_ip_check_respects_environment_bypass(monkeypatch):
    """Auth-layer IP-ban checks must also honor the break-glass flag."""
    monkeypatch.setenv("OMLORIX_DISABLE_IP_RESTRICTIONS", "true")
    monkeypatch.setattr(auth_models, "get_blocked_ip", lambda *_args, **_kwargs: pytest.fail("DB should not be queried"))

    assert auth_models.check_blocked_ip_address("198.51.100.10", object()) is False


def test_security_ip_policy_rejects_blocking_current_admin(monkeypatch):
    """Admins should not be able to save an exact-IP block that locks them out."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "block_specific_ip": ["198.51.100.10"],
                "only_allow_specific_ip": False,
                "allow_specific_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert "block list contains your current admin IP address" in excinfo.value.detail


def test_security_ip_policy_rejects_allowlist_without_current_admin(monkeypatch):
    """Enabling exact-IP allow-only mode must include the current admin IP."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "only_allow_specific_ip": True,
                "allow_specific_ip": ["203.0.113.25"],
                "block_specific_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert "not in the allow list" in excinfo.value.detail


def test_security_ip_policy_ignores_stale_exact_allowlist_when_exact_rules_disabled(monkeypatch):
    """Disabled exact-IP rules should not reject saves because stale list data exists."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    admin_utils._assert_security_ip_policy_keeps_admin_access(
        {
            "enable_ip_restrictions": True,
            "enable_ip_address_restrictions": False,
            "ip_address_restriction_mode": "allowlist",
            "only_allow_specific_ip": True,
            "allow_specific_ip": ["203.0.113.25"],
            "block_specific_ip": [],
        },
        request_client_ip="198.51.100.10",
        db=object(),
    )


def test_security_ip_policy_rejects_country_allowlist_without_current_admin_country(monkeypatch):
    """Country allow-only mode must include the current admin IP country."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    async def fake_country(*_args, **_kwargs):
        return "DE"

    monkeypatch.setattr(admin_utils, "get_country_by_ip", fake_country)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "check_ip_location_provider": "db-ip-free",
                "only_allow_specific_ip": False,
                "allow_specific_ip": [],
                "block_specific_ip": [],
                "only_allow_ip_from_specific_countries": True,
                "allow_country_ip": ["US"],
                "block_country_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert "current admin IP country is not in the allow list" in excinfo.value.detail


def test_security_ip_policy_reports_missing_location_provider(monkeypatch):
    """A missing provider should produce an actionable error instead of a generic lookup failure."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "check_ip_location_provider": "",
                "only_allow_ip_from_specific_countries": True,
                "allow_country_ip": ["DE"],
                "block_country_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "ip_country_provider_not_configured"
    assert "no IP location provider is configured" in excinfo.value.detail["message"]


@pytest.mark.parametrize("invalid_country_code", ["Germany", "ZZ"])
def test_security_settings_update_reports_the_invalid_country_code(
    monkeypatch,
    invalid_country_code,
):
    """Admin updates should return a structured, actionable country-code error."""
    settings_record = SimpleNamespace(
        data=DEFAULT_SETTINGS["security"].copy(),
        updated_at=None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "security" else None,
    )

    with pytest.raises(HTTPException) as excinfo:
        admin_utils.update_admin_settings_values_for_page(
            page="security",
            payload={"allow_country_ip": [invalid_country_code]},
            db=_DummyDB(),
            request_client_ip="198.51.100.10",
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == {
        "code": "ip_country_code_invalid",
        "message": (
            f"Invalid country code: {invalid_country_code}. "
            "Use a two-letter ISO 3166-1 code such as DE or US."
        ),
        "country_code": invalid_country_code,
    }


@pytest.mark.parametrize("invalid_ip_address", ["not-an-ip", "192.168.1.999"])
def test_security_settings_update_reports_the_invalid_ip_address(
    monkeypatch,
    invalid_ip_address,
):
    """Admin updates should identify the invalid exact-IP policy entry."""
    settings_record = SimpleNamespace(
        data=DEFAULT_SETTINGS["security"].copy(),
        updated_at=None,
    )
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "security" else None,
    )

    with pytest.raises(HTTPException) as excinfo:
        admin_utils.update_admin_settings_values_for_page(
            page="security",
            payload={"allow_specific_ip": [invalid_ip_address]},
            db=_DummyDB(),
            request_client_ip="198.51.100.10",
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == {
        "code": "ip_address_invalid",
        "message": (
            f"Invalid IP address: {invalid_ip_address}. "
            "Use a valid IPv4 or IPv6 address, such as "
            "203.0.113.10 or 2001:db8::1."
        ),
        "ip_address": invalid_ip_address,
    }


def test_security_ip_policy_reports_missing_location_provider_api_key(monkeypatch):
    """A selected key-based provider should identify its missing credential directly."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)
    monkeypatch.setattr(admin_utils, "_get_api_key_settings", lambda _db: {})

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "check_ip_location_provider": "ipinfo",
                "only_allow_ip_from_specific_countries": True,
                "allow_country_ip": ["DE"],
                "block_country_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "ip_country_provider_api_key_missing"
    assert excinfo.value.detail["provider"] == "IP Info"
    assert "API key is not configured" in excinfo.value.detail["message"]


def test_security_ip_policy_uses_incoming_location_provider_api_key(monkeypatch):
    """A newly entered API key should be usable by the lockout check before persistence."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)
    monkeypatch.setattr(admin_utils, "_get_api_key_settings", lambda _db: {})
    captured_lookup = {}

    async def fake_country(*_args, **kwargs):
        captured_lookup.update(kwargs)
        return "DE"

    monkeypatch.setattr(admin_utils, "get_country_by_ip", fake_country)

    admin_utils._assert_security_ip_policy_keeps_admin_access(
        {
            "enable_ip_restrictions": True,
            "check_ip_location_provider": "ipinfo",
            "only_allow_ip_from_specific_countries": True,
            "allow_country_ip": ["DE"],
            "block_country_ip": [],
        },
        request_client_ip="198.51.100.10",
        db=object(),
        api_key_updates={"ipinfo": "new-api-key"},
    )

    assert captured_lookup == {
        "provider_override": "ipinfo",
        "token_override": "new-api-key",
    }


def test_security_ip_policy_reports_configured_provider_lookup_failure(monkeypatch):
    """A configured provider failure should suggest provider, network, and proxy checks."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    async def fake_country(*_args, **_kwargs):
        return "Unknown"

    monkeypatch.setattr(admin_utils, "get_country_by_ip", fake_country)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "check_ip_location_provider": "db-ip-free",
                "only_allow_ip_from_specific_countries": True,
                "allow_country_ip": ["DE"],
                "block_country_ip": [],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "ip_country_lookup_failed"
    assert excinfo.value.detail["provider"] == "DB-IP (Free)"
    assert "trusted proxy settings" in excinfo.value.detail["message"]


def test_security_ip_policy_rejects_blocking_current_admin_country(monkeypatch):
    """Country block lists must not include the current admin IP country."""
    monkeypatch.delenv("OMLORIX_DISABLE_IP_RESTRICTIONS", raising=False)

    async def fake_country(*_args, **_kwargs):
        return "DE"

    monkeypatch.setattr(admin_utils, "get_country_by_ip", fake_country)

    with pytest.raises(HTTPException) as excinfo:
        admin_utils._assert_security_ip_policy_keeps_admin_access(
            {
                "enable_ip_restrictions": True,
                "check_ip_location_provider": "db-ip-free",
                "only_allow_specific_ip": False,
                "allow_specific_ip": [],
                "block_specific_ip": [],
                "only_allow_ip_from_specific_countries": False,
                "allow_country_ip": [],
                "block_country_ip": ["DE"],
            },
            request_client_ip="198.51.100.10",
            db=object(),
        )

    assert excinfo.value.status_code == 409
    assert "country block list contains your current admin IP country" in excinfo.value.detail
