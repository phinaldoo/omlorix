import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils


def _user():
    return SimpleNamespace(
        id="user-1",
        email="user@example.com",
        hashed_password="hashed-password",
        role="user",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        group_id="group-1",
    )


def _locked_response():
    return {
        "is_locked": True,
        "lock_until": datetime.now(timezone.utc) + timedelta(minutes=15),
        "type": "manual",
        "reason": "Review required",
    }


def _request():
    return SimpleNamespace(headers={"User-Agent": "pytest"}, client=SimpleNamespace(host="203.0.113.10"))


def test_login_eligibility_blocks_locked_user_before_access_windows(monkeypatch):
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: _locked_response())
    monkeypatch.setattr(
        auth_utils,
        "is_group_accessible_now",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access windows should not run")),
    )

    result = auth_utils.validate_user_login_eligibility(_user(), object())

    assert result["status"] == "lock"
    assert result["expires"] > 0
    assert result["type"] == "manual"
    assert result["reason"] == "Review required"


def test_password_signin_rejects_locked_user_when_failed_attempt_locking_is_disabled(monkeypatch):
    user = _user()
    issued_sessions = []

    def fake_setting(page, key, db):
        if (page, key) == ("security", "enable_block_user_after_wrong_signin"):
            return False
        if (page, key) == ("login_general", "enable_signin"):
            return True
        return None

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda ip, db: False)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(auth_utils, "get_user", lambda *args, **kwargs: user)
    monkeypatch.setattr(auth_utils, "verify_password_with_migration", lambda password, hashed: (True, False))
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: _locked_response())
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_issue_authenticated_session", lambda *args, **kwargs: issued_sessions.append(True))

    result = auth_utils.signin(
        object(),
        object(),
        _request(),
        SimpleNamespace(email="user@example.com", password="correct-password"),
        object(),
    )

    assert result["status"] == "lock"
    assert issued_sessions == []


def test_passkey_finish_checks_shared_login_eligibility_before_session_issuance():
    router_path = Path(__file__).resolve().parents[2] / "app" / "auth" / "router.py"
    module = ast.parse(router_path.read_text())
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "passkey_finish_authentication"
    )
    call_lines = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_lines.setdefault(node.func.id, node.lineno)

    assert call_lines["validate_user_login_eligibility"] < call_lines["_issue_authenticated_session"]


def test_session_issuance_has_defensive_login_eligibility_guard(monkeypatch):
    logs = []

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: {"status": "lock", "expires": 1})
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: logs.append(args))
    monkeypatch.setattr(
        auth_utils,
        "resolve_slot_assignment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("slot assignment should not run")),
    )

    result = auth_utils._issue_authenticated_session(
        db=object(),
        db_log=object(),
        request=_request(),
        response=object(),
        user=_user(),
        log_event="signin",
        success_message="ok",
    )

    assert result == {"status": "lock", "expires": 1}
    assert logs


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    (("email", "changed@example.com"), ("hashed_password", "changed-hash")),
)
def test_password_session_issuance_rejects_stale_identity_proof(
    monkeypatch,
    changed_field,
    changed_value,
):
    from app.auth import token as auth_token

    verified_user = _user()
    locked_user = _user()
    setattr(locked_user, changed_field, changed_value)
    logs = []

    monkeypatch.setattr(
        auth_utils,
        "_client_ip_from_request",
        lambda *_args: "203.0.113.10",
    )
    monkeypatch.setattr(
        auth_utils,
        "_enforce_session_auth_authority",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        auth_utils,
        "validate_user_login_eligibility",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        auth_utils,
        "resolve_slot_assignment",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda **_kwargs: "refresh")
    monkeypatch.setattr(auth_token, "create_access_token", lambda **_kwargs: "access")
    monkeypatch.setattr(
        auth_utils,
        "_lock_user_for_session_issuance",
        lambda *_args: locked_user,
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication",
        lambda *_args, **_kwargs: pytest.fail("a stale proof must not create a session"),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda *args, **_kwargs: logs.append(args),
    )
    monkeypatch.setattr(
        auth_utils,
        "record_auth_login_attempt_metric",
        lambda *_args, **_kwargs: None,
    )

    result = auth_utils._issue_authenticated_session(
        db=object(),
        db_log=object(),
        request=_request(),
        response=object(),
        user=verified_user,
        log_event="signin",
        success_message="ok",
        password_proof_binding=(
            verified_user.email,
            verified_user.hashed_password,
        ),
    )

    assert result == {"status": "InvalidCredentials"}
    assert logs


def test_final_session_guard_parses_persisted_timed_lock():
    user = _user()
    user.lock = {
        "is_locked": True,
        "lock_until": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        "type": "manual",
        "reason": "Review required",
    }

    result = auth_utils._current_user_row_login_eligibility(user)

    assert result["status"] == "lock"
    assert result["expires"] > 0
    assert result["type"] == "manual"
    assert result["reason"] == "Review required"


def test_session_issuance_allows_only_sso_for_managed_accounts(monkeypatch):
    user = _user()
    user.auth_management_mode = "external"
    logs = []
    monkeypatch.setattr(
        auth_utils,
        "_client_ip_from_request",
        lambda *_args: "203.0.113.10",
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda *args, **_kwargs: logs.append(args),
    )
    monkeypatch.setattr(
        auth_utils,
        "record_auth_login_attempt_metric",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auth_utils,
        "validate_user_login_eligibility",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("managed local sessions must fail before eligibility checks")
        ),
    )

    with pytest.raises(Exception) as exc_info:
        auth_utils._issue_authenticated_session(
            db=object(),
            db_log=object(),
            request=_request(),
            response=object(),
            user=user,
            log_event="social_signin",
            success_message="ok",
        )

    assert getattr(exc_info.value, "status_code", None) == 403
    assert logs
