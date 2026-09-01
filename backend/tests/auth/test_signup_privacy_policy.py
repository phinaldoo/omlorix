import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.auth import utils as auth_utils
from app.utils import utils as app_utils


def _request():
    return SimpleNamespace(
        headers={"User-Agent": "pytest"},
        client=SimpleNamespace(host="203.0.113.10"),
    )


def _signup_payload(**overrides):
    payload = {
        "email": "new.user@example.com",
        "password": "CorrectHorseBatteryStaple1!",
        "first_name": "New",
        "last_name": "User",
        "privacy_policy_accepted": True,
        "privacy_policy_revision": 5,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _patch_signup_basics(monkeypatch):
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda ip, db: False)
    monkeypatch.setattr(auth_utils, "_is_new_account_registration_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: False)
    monkeypatch.setattr(
        auth_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 1,
            "signup_available": False,
            "require_current_revision_for_signup": False,
        },
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "create_admin_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(
        auth_utils,
        "create_user",
        lambda *args, **kwargs: SimpleNamespace(id="user-1", role="user"),
    )

    def fake_setting(page, key, db):
        settings = {
            ("login_general", "specific_signup_domain"): None,
            ("login_general", "minimum_password_length"): 0,
            ("login_general", "minimum_special_characters"): 0,
            ("login_general", "minimum_uppercase_characters"): 0,
            ("login_general", "minimum_lowercase_characters"): 0,
            ("login_general", "minimum_number_characters"): 0,
            ("login_general", "default_user_role"): "user",
            ("login_general", "default_user_group"): None,
        }
        return settings.get((page, key))

    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)


def test_signup_ignores_privacy_policy_fields(monkeypatch):
    _patch_signup_basics(monkeypatch)
    monkeypatch.setattr(
        app_utils,
        "get_privacy_policy_notice_policy",
        lambda db: {
            "notice_mode": "modal",
            "revision": 7,
            "should_show_notice": True,
        },
    )

    result = auth_utils.signup(object(), object(), _request(), _signup_payload(), object())

    assert result == {"status": "success"}


def test_pending_signup_notification_is_linked_to_created_user(monkeypatch):
    _patch_signup_basics(monkeypatch)
    notification_calls = []
    monkeypatch.setattr(
        auth_utils,
        "create_user",
        lambda *args, **kwargs: SimpleNamespace(
            id="pending-user-1",
            role="pending",
            first_name="New",
            last_name="User",
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_admin_notification",
        lambda *args, **kwargs: notification_calls.append((args, kwargs)),
    )

    result = auth_utils.signup(
        object(),
        object(),
        _request(),
        _signup_payload(email="pending.user@example.com"),
        object(),
    )

    assert result == {"status": "success"}
    assert len(notification_calls) == 1
    args, kwargs = notification_calls[0]
    assert args[1:] == (
        "user_pending",
        "New pending user signup: pending.user@example.com",
    )
    assert kwargs["user_id"] == "pending-user-1"
    assert kwargs["details"]["email"] == "pending.user@example.com"


def test_disabled_signup_is_rejected_without_ip_ban_settings(monkeypatch):
    """The backend gate rejects stale clients without creating an IP ban."""

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda ip, db: False)
    monkeypatch.setattr(auth_utils, "_is_new_account_registration_enabled", lambda db: False)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_notify_suspicious_auth_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: pytest.fail("disabled signup must not read an IP-ban setting"),
    )

    result = auth_utils.signup(object(), object(), _request(), _signup_payload(), object())

    assert result == {"status": "error"}


def test_password_policy_is_enforced_without_ip_ban_settings(monkeypatch):
    """Server-side password validation rejects input without ban side effects."""

    _patch_signup_basics(monkeypatch)
    requested_settings = []
    settings = {
        ("login_general", "specific_signup_domain"): None,
        ("login_general", "minimum_password_length"): 12,
        ("login_general", "minimum_special_characters"): 1,
        ("login_general", "minimum_uppercase_characters"): 1,
        ("login_general", "minimum_lowercase_characters"): 1,
        ("login_general", "minimum_number_characters"): 1,
    }

    def fake_setting(page, key, db):
        requested_settings.append((page, key))
        return settings.get((page, key))

    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)

    result = auth_utils.signup(
        object(),
        object(),
        _request(),
        _signup_payload(password="TooShort1!"),
        object(),
    )

    assert result == {"status": "passwordPolicyFailed"}
    assert all(page != "security" for page, _key in requested_settings)
