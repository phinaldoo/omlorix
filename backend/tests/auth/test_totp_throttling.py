import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import twofa
from app.auth import twofa_provider


def test_totp_throttle_settings_are_registered_defaults():
    from app.users.defaults import DEFAULT_USER_SETTINGS

    secret_defaults = DEFAULT_USER_SETTINGS["secret"]

    assert secret_defaults["2fa_totp_attempts"] == 0
    assert secret_defaults["2fa_totp_locked_until"] == ""
    assert secret_defaults["2fa_totp_ip_hash"] == ""
    assert secret_defaults["2fa_totp_ip_attempts"] == 0
    assert secret_defaults["2fa_totp_ip_locked_until"] == ""


def test_totp_login_locks_after_repeated_failures(monkeypatch):
    user = SimpleNamespace(id="user-id")
    settings = {
        ("secret", "2fa_secret"): "totp-secret",
        ("secret", "2fa_totp_attempts"): 0,
        ("secret", "2fa_totp_ip_attempts"): 0,
    }
    updates = []

    def get_user_setting_value(user_id, page, key, db):
        return settings.get((page, key), "")

    def update_user_settings(user_id, page, key, value, db):
        settings[(page, key)] = value
        updates.append((page, key, value))

    monkeypatch.setattr(twofa_provider, "get_user_setting_value", get_user_setting_value)
    monkeypatch.setattr(twofa_provider, "update_user_settings", update_user_settings)
    monkeypatch.setattr(
        twofa_provider,
        "get_global_2fa_config",
        lambda db: twofa_provider.TwoFAConfig(
            provider="totp",
            otp_length=6,
            otp_ttl_seconds=300,
            otp_resend_cooldown_seconds=30,
            otp_max_attempts=2,
        ),
    )
    monkeypatch.setattr(twofa_provider, "_get_login_general_value", lambda db, key, default=None: True if key == "enable_2fa" else default)
    monkeypatch.setattr(twofa_provider, "resolve_user_2fa_provider", lambda user, db: "totp")
    monkeypatch.setattr(twofa_provider, "ensure_provider_alignment", lambda *args, **kwargs: None)
    monkeypatch.setattr(twofa_provider, "_is_user_enrolled_for_provider", lambda *args, **kwargs: True)
    monkeypatch.setattr(twofa_provider, "_verify_totp_code", lambda secret, code: False)

    first_result = twofa_provider.evaluate_login_2fa(user, "000000", None, None, object(), client_ip="203.0.113.1")
    second_result = twofa_provider.evaluate_login_2fa(user, "000000", None, None, object(), client_ip="203.0.113.1")

    assert first_result == {"status": "otp_invalid", "provider": "totp"}
    assert second_result == {"status": "otp_locked", "provider": "totp"}
    assert ("secret", "2fa_totp_locked_until") in {(page, key) for page, key, value in updates}
    assert ("secret", "2fa_totp_ip_locked_until") in {(page, key) for page, key, value in updates}


def test_clear_user_twofa_state_clears_totp_throttle_settings(monkeypatch):
    from unittest.mock import Mock

    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        account_type="regular",
        auth_management_mode="local",
        settings={},
    )
    settings = {
        "login_2fa": {"enable_2fa": True, "provider": "totp"},
        "secret": {
            "2fa_secret": "enrolled",
            "2fa_secret_pending": "pending",
            "2fa_totp_attempts": 4,
            "2fa_totp_locked_until": "later",
            "2fa_totp_ip_hash": "hash",
            "2fa_totp_ip_attempts": 4,
            "2fa_totp_ip_locked_until": "later",
            "2fa_otp_hash": "otp",
        },
    }
    db = Mock()
    delete_sessions = Mock()
    clear_transient = Mock(return_value=3)
    monkeypatch.setattr(
        twofa_provider,
        "_locked_user_settings",
        lambda _user_id, _db: (user, settings),
    )
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        twofa_provider,
        "delete_authentication_all",
        delete_sessions,
    )
    monkeypatch.setattr(
        twofa_provider,
        "delete_user_transient_auth_state",
        clear_transient,
    )
    monkeypatch.setattr(
        twofa_provider,
        "_send_twofa_deactivated_email",
        lambda _user, _db: "queued",
    )
    revoked = []
    monkeypatch.setattr(twofa_provider, "revoke_user_sessions", revoked.append)

    result = twofa_provider.clear_user_twofa_state(user.id, db)

    assert settings["login_2fa"] == {"enable_2fa": False, "provider": ""}
    for key in (
        "2fa_secret",
        "2fa_secret_pending",
        "2fa_totp_locked_until",
        "2fa_totp_ip_hash",
        "2fa_totp_ip_locked_until",
        "2fa_otp_hash",
    ):
        assert settings["secret"][key] == ""
    assert settings["secret"]["2fa_totp_attempts"] == 0
    assert settings["secret"]["2fa_totp_ip_attempts"] == 0
    delete_sessions.assert_called_once_with(
        db,
        user.id,
        commit=False,
        revoke_cached=False,
    )
    clear_transient.assert_called_once_with(db, user.id, commit=False)
    db.commit.assert_called_once_with()
    assert revoked == [user.id]
    assert result == {"status": "success", "security_notification": "queued"}


def test_legacy_totp_setup_path_uses_throttled_verify_flow(monkeypatch):
    user = SimpleNamespace(id="user-id", email="user@example.com", group_id="group-id")
    recorded = {}

    def fake_verify_setup(user_arg, otp_code, action, otp_destination, db, provider=None, client_ip=None):
        recorded["user_id"] = user_arg.id
        recorded["otp_code"] = otp_code
        recorded["action"] = action
        recorded["provider"] = provider
        recorded["client_ip"] = client_ip
        return {"status": "otp_locked", "provider": "totp"}

    monkeypatch.setattr(twofa, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(twofa, "verify_setup", fake_verify_setup)

    from app.users import init as users_init

    monkeypatch.setattr(
        users_init,
        "get_user_setting_value",
        lambda user_id, page, key, db: "pending-secret" if (page, key) == ("secret", "2fa_secret_pending") else "",
    )

    result = twofa.setup_twofa(
        "pending-secret",
        "000000",
        user.id,
        user.email,
        user.group_id,
        object(),
        otp_action="setup",
        client_ip="203.0.113.1",
    )

    assert result == {"status": "otp_locked", "provider": "totp"}
    assert recorded == {
        "user_id": "user-id",
        "otp_code": "000000",
        "action": "setup",
        "provider": "totp",
        "client_ip": "203.0.113.1",
    }


def test_totp_verification_rejects_adjacent_time_windows(monkeypatch):
    class FakeTOTP:
        def __init__(self, secret):
            self.secret = secret

        def verify(self, code, **kwargs):
            valid_window = int(kwargs.get("valid_window") or 0)
            if code == "current-code":
                return True
            return valid_window >= 1 and code in {"previous-code", "future-code"}

    monkeypatch.setattr(twofa_provider.pyotp.totp, "TOTP", FakeTOTP)

    assert twofa_provider._verify_totp_code("secret", "current-code") is True
    assert twofa_provider._verify_totp_code("secret", "previous-code") is False
    assert twofa_provider._verify_totp_code("secret", "future-code") is False
