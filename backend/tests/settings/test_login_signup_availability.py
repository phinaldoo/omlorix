"""Tests for sign-up availability in the public login bootstrap payload."""

from app.settings import utils as settings_utils
from app.utils import utils as app_utils


def _stub_login_settings_dependencies(
    monkeypatch,
    *,
    enable_signup: bool,
    terms_signup_available: bool,
) -> None:
    """Provide the minimal settings state needed to exercise login bootstrap."""
    monkeypatch.setattr(
        settings_utils,
        "get_page_settings_by_page",
        lambda page, db: {"page_name": page, "data": {}},
    )
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page_data",
        lambda db, page: {
            "enable_signin": True,
            "enable_signup": enable_signup,
            "enable_password_reset": False,
        }
        if page == "login_general"
        else {},
    )
    monkeypatch.setattr(settings_utils, "is_password_reset_ready", lambda db: False)
    monkeypatch.setattr(settings_utils, "is_twofa_email_ready", lambda db: False)
    monkeypatch.setattr(
        settings_utils,
        "get_value_by_page_and_key",
        lambda page, key, db: "Omlorix" if (page, key) == ("general", "application_name") else None,
    )
    monkeypatch.setattr(
        settings_utils,
        "get_login_passkey_policy",
        lambda db: {"enable_passkeys": False},
    )
    monkeypatch.setattr(
        app_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "signup_available": terms_signup_available,
            "require_current_revision_for_signup": terms_signup_available,
        },
    )


def test_login_setup_keeps_signup_enabled_when_terms_acceptance_is_optional(monkeypatch):
    """Optional terms must not suppress an explicitly enabled registration form."""
    _stub_login_settings_dependencies(
        monkeypatch,
        enable_signup=True,
        terms_signup_available=False,
    )

    payload = settings_utils.get_login_settings(object())

    assert payload["enable_signup"] is True


def test_login_setup_hides_signup_when_administrator_disables_it(monkeypatch):
    """Terms policy must not override an explicit administrator opt-out."""
    _stub_login_settings_dependencies(
        monkeypatch,
        enable_signup=False,
        terms_signup_available=True,
    )

    payload = settings_utils.get_login_settings(object())

    assert payload["enable_signup"] is False
