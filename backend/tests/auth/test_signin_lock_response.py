import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils


def _request():
    return SimpleNamespace(headers={"User-Agent": "pytest"})


def _locked_response():
    return {
        "is_locked": True,
        "lock_until": datetime.now(timezone.utc) + timedelta(minutes=15),
        "type": "wrong_sign_in_attempts",
        "reason": "Too many failed sign-in attempts",
    }


def _enable_failed_signin_locking(monkeypatch):
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda _request, _db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda _ip, _db: False)
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: page == "security" and key == "enable_block_user_after_wrong_signin",
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)


def test_signin_locked_account_matches_unknown_identifier_response(monkeypatch):
    _enable_failed_signin_locking(monkeypatch)

    monkeypatch.setattr(auth_utils, "check_failed_signin_attempts", lambda _identifier, _db: _locked_response())
    locked_result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="locked@example.com", password="password"),
        object(),
    )

    monkeypatch.setattr(auth_utils, "check_failed_signin_attempts", lambda _identifier, _db: False)
    monkeypatch.setattr(
        auth_utils,
        "get_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404)),
    )
    monkeypatch.setattr(auth_utils, "get_ldap_provider", lambda _db: SimpleNamespace(is_enabled=lambda: False))
    unknown_result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="missing@example.com", password="password"),
        object(),
    )

    assert locked_result == unknown_result == {"status": "InvalidCredentials"}
    assert "expires" not in locked_result
    assert "type" not in locked_result
    assert "reason" not in locked_result


def test_signin_new_lock_after_failed_password_returns_generic_response(monkeypatch):
    _enable_failed_signin_locking(monkeypatch)
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        hashed_password="hashed-password",
        role="user",
    )
    lock_checks = iter([False, _locked_response()])

    monkeypatch.setattr(auth_utils, "check_failed_signin_attempts", lambda _identifier, _db: next(lock_checks))
    monkeypatch.setattr(auth_utils, "get_user", lambda *args, **kwargs: user)
    monkeypatch.setattr(auth_utils, "verify_password_with_migration", lambda _password, _hash: (False, False))
    monkeypatch.setattr(auth_utils, "get_ldap_provider", lambda _db: SimpleNamespace(is_enabled=lambda: False))
    monkeypatch.setattr(auth_utils, "increment_user_wrong_sign_in_attempts", lambda _db, _user_id: None)

    result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="user@example.com", password="wrong-password"),
        object(),
    )

    assert result == {"status": "InvalidCredentials"}
    assert "expires" not in result
    assert "type" not in result
    assert "reason" not in result


def test_check_failed_signin_attempts_resolves_linked_ldap_directory_identifier(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com", role="user")
    lock_calls = []

    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_settings_value",
        lambda _db, path, values, **kwargs: user if path == ("ldap_login", "last_login_identifier") and values == ["jdoe"] else None,
    )
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda user_id, page, key, _db: user_id == "user-1" and page == "ldap_login" and key == "linked")
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda _db, _user_id: False)
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: 3 if key == "block_user_after_wrong_signin_attempts" else 1,
    )
    monkeypatch.setattr(auth_utils, "get_user_wrong_sign_in_attempts", lambda _db, _user_id: 3)
    monkeypatch.setattr(auth_utils, "lock_user", lambda _db, user_id, *_args: lock_calls.append(user_id))
    monkeypatch.setattr(auth_utils, "_notify_suspicious_auth_activity", lambda *args, **kwargs: None)

    result = auth_utils.check_failed_signin_attempts("jdoe", object())

    assert result["is_locked"] is True
    assert result["type"] == "wrong_sign_in_attempts"
    assert lock_calls == ["user-1"]


def test_signin_failed_ldap_directory_login_increments_linked_user_attempts(monkeypatch):
    _enable_failed_signin_locking(monkeypatch)
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        hashed_password="hashed-password",
        role="user",
    )
    increment_calls = []
    lock_checks = []

    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_settings_value",
        lambda _db, path, values, **kwargs: user if path == ("ldap_login", "last_login_identifier") and values == ["jdoe"] else None,
    )

    def fake_user_setting_value(user_id, page, key, _db):
        if user_id == "user-1" and page == "ldap_login" and key == "linked":
            return True
        return False

    monkeypatch.setattr(auth_utils, "get_user_setting_value", fake_user_setting_value)
    monkeypatch.setattr(
        auth_utils,
        "get_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404)),
    )
    monkeypatch.setattr(auth_utils, "verify_password_with_migration", lambda _password, _hash: (False, False))
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda _db, _user_id: False)
    monkeypatch.setattr(auth_utils, "get_user_wrong_sign_in_attempts", lambda _db, _user_id: 0)
    monkeypatch.setattr(auth_utils, "increment_user_wrong_sign_in_attempts", lambda _db, user_id: increment_calls.append(user_id))
    monkeypatch.setattr(auth_utils, "lock_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "_notify_suspicious_auth_activity", lambda *args, **kwargs: None)

    def fake_setting(page, key, _db):
        if (page, key) == ("security", "enable_block_user_after_wrong_signin"):
            return True
        if key == "block_user_after_wrong_signin_attempts":
            return 5
        if key == "block_user_after_wrong_signin_attempts_time_hours":
            return 24
        return False

    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)

    class _FakeLdapProvider:
        def is_enabled(self):
            return True

        def authenticate(self, _identifier, _password):
            raise HTTPException(status_code=401, detail="invalid_credentials")

    monkeypatch.setattr(auth_utils, "get_ldap_provider", lambda _db: _FakeLdapProvider())

    original_check_failed_signin_attempts = auth_utils.check_failed_signin_attempts

    def tracked_check_failed_signin_attempts(identifier, db):
        lock_checks.append(identifier)
        return original_check_failed_signin_attempts(identifier, db)

    monkeypatch.setattr(auth_utils, "check_failed_signin_attempts", tracked_check_failed_signin_attempts)

    result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="jdoe", password="wrong-password"),
        object(),
    )

    assert result == {"status": "InvalidCredentials"}
    assert increment_calls == ["user-1"]
    assert lock_checks == ["jdoe", "user@example.com"]


def test_disabled_signin_does_not_oracle_valid_non_admin_password(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        hashed_password="hashed-password",
        role="user",
    )
    completed = []

    def fake_setting(page, key, _db):
        if (page, key) == ("security", "enable_block_user_after_wrong_signin"):
            return False
        if (page, key) == ("login_general", "enable_signin"):
            return False
        return None

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda _request, _db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda _ip, _db: False)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(auth_utils, "get_user", lambda *args, **kwargs: user)
    monkeypatch.setattr(auth_utils, "verify_password_with_migration", lambda _password, _hash: (True, False))
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "record_auth_login_attempt_metric", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "_complete_signin_for_user",
        lambda *args, **kwargs: completed.append(True) or {"status": "success"},
    )

    result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="user@example.com", password="correct-password"),
        object(),
    )

    assert result == {"status": "InvalidCredentials"}
    assert completed == []


def test_disabled_signin_still_allows_admin_password_login(monkeypatch):
    admin = SimpleNamespace(
        id="admin-1",
        email="admin@example.com",
        hashed_password="hashed-password",
        role="admin",
    )
    completed = []

    def fake_setting(page, key, _db):
        if (page, key) == ("security", "enable_block_user_after_wrong_signin"):
            return False
        if (page, key) == ("login_general", "enable_signin"):
            return False
        return None

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda _request, _db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda _ip, _db: False)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(auth_utils, "get_user", lambda *args, **kwargs: admin)
    monkeypatch.setattr(auth_utils, "verify_password_with_migration", lambda _password, _hash: (True, False))
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "_complete_signin_for_user",
        lambda *args, **kwargs: completed.append(kwargs.get("log_event")) or {"status": "success"},
    )

    result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="admin@example.com", password="correct-password", admin_only=True),
        object(),
    )

    assert result == {"status": "success"}
    assert completed == ["signin"]
