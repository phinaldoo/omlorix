from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda *writer_args, **writer_kwargs: SimpleNamespace()
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda *reader_args, **reader_kwargs: SimpleNamespace()
    )
    sys.modules["zstandard"] = fake_zstandard


from app.auth import token as auth_token


SECRET = "t" * 64


@pytest.fixture(autouse=True)
def _stub_terms_policy(monkeypatch):
    """Keep these tests focused on 2FA session policy instead of legal settings DB reads."""
    monkeypatch.setattr(
        auth_token,
        "get_terms_of_service_policy",
        lambda *_args, **_kwargs: {
            "revision": 1,
            "accepted_current_revision": True,
            "require_current_revision_for_access": False,
        },
    )


def _refresh_jwt(user_id: str = "user-1", **claims) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            **claims,
        },
        SECRET,
        algorithm="HS512",
    )


def _request():
    return SimpleNamespace(cookies={}, headers={}, client=SimpleNamespace(host="203.0.113.10"))


def _user(user_id: str = "user-1"):
    return SimpleNamespace(
        id=user_id,
        role="user",
        is_active=True,
        deleted_at=None,
        lock={},
        account_type="regular",
        temporary_expires_at=None,
        group_id="default",
    )


def _required_policy(**overrides):
    policy = {
        "required": True,
        "mode": "setup",
        "provider": "totp",
        "version": "2fa-v1:forced",
    }
    policy.update(overrides)
    return policy


def test_session_2fa_policy_rejects_missing_or_stale_claim(monkeypatch):
    user = _user()
    monkeypatch.setattr(auth_token, "get_login_2fa_session_policy", lambda checked_user, db: _required_policy())

    with pytest.raises(HTTPException) as exc_info:
        auth_token.ensure_session_satisfies_current_2fa_policy(user, {}, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "type": "twofa_policy_required",
        "status": "otp_setup",
        "provider": "totp",
        "mode": "setup",
    }


def test_session_2fa_policy_accepts_matching_claim(monkeypatch):
    user = _user()
    monkeypatch.setattr(auth_token, "get_login_2fa_session_policy", lambda checked_user, db: _required_policy())

    auth_token.ensure_session_satisfies_current_2fa_policy(
        user,
        {
            "twofa_satisfied": True,
            "twofa_provider": "totp",
            "twofa_policy_version": "2fa-v1:forced",
        },
        object(),
    )


def test_refresh_rejects_session_when_current_2fa_policy_is_unsatisfied(monkeypatch):
    refresh_token = _refresh_jwt()

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (refresh_token, 1))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(
            state="current",
            authentication=SimpleNamespace(id="session-1", access_token="old-access"),
        ),
    )
    monkeypatch.setattr(auth_token, "get_user", lambda db, user_id: _user(user_id))
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)
    monkeypatch.setattr(auth_token, "get_login_2fa_session_policy", lambda checked_user, db: _required_policy())
    monkeypatch.setattr(
        auth_token,
        "create_access_token",
        lambda *args, **kwargs: pytest.fail("refresh should reject before issuing a new access token"),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_token.get_access_token_by_refresh_token(_request(), SimpleNamespace(), object(), object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["type"] == "twofa_policy_required"


def test_refresh_preserves_current_2fa_satisfaction_claims(monkeypatch):
    refresh_token = _refresh_jwt(
        twofa_satisfied=True,
        twofa_provider="email",
        twofa_policy_version="2fa-v1:email-verify",
    )
    minted = {}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (refresh_token, 2))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(
            state="current",
            authentication=SimpleNamespace(id="session-1", access_token="old-access"),
        ),
    )
    monkeypatch.setattr(auth_token, "get_user", lambda db, user_id: _user(user_id))
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)
    monkeypatch.setattr(
        auth_token,
        "get_login_2fa_session_policy",
        lambda checked_user, db: _required_policy(mode="verify", provider="email", version="2fa-v1:email-verify"),
    )
    monkeypatch.setattr(
        auth_token,
        "build_login_2fa_session_claims",
        lambda checked_user, db: {
            "twofa_satisfied": True,
            "twofa_provider": "email",
            "twofa_policy_version": "2fa-v1:email-verify",
        },
    )
    def fake_create_access_token(data, db):
        minted["access"] = data
        return "new-access"

    def fake_create_refresh_token(data, db):
        minted["refresh"] = data
        return "new-refresh"

    monkeypatch.setattr(auth_token, "create_access_token", fake_create_access_token)
    monkeypatch.setattr(auth_token, "create_refresh_token", fake_create_refresh_token)
    monkeypatch.setattr(auth_token, "rotate_authentication_tokens", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "update_last_active_user", lambda *args: None)
    monkeypatch.setattr(auth_token, "create_authentication_log", lambda *args: None)
    monkeypatch.setattr(auth_token, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(auth_token, "get_value_by_page_and_key", lambda *args: True)
    monkeypatch.setattr(auth_token, "set_refresh_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_token, "set_access_token_cookie", lambda *args: None)
    monkeypatch.setattr(auth_token, "set_active_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_token, "clear_legacy_refresh_cookie", lambda *args: None)

    payload = auth_token.get_access_token_by_refresh_token(_request(), SimpleNamespace(), object(), object())

    assert payload["session_authenticated"] is True
    assert minted["access"] == {
        "sub": "user-1",
        "sid": "session-1",
        "type": "access",
        "twofa_satisfied": True,
        "twofa_provider": "email",
        "twofa_policy_version": "2fa-v1:email-verify",
    }
    assert minted["refresh"]["type"] == "refresh"
    assert minted["refresh"]["twofa_policy_version"] == "2fa-v1:email-verify"


def test_rotate_current_session_tokens_with_2fa_claims_returns_new_access_and_updates_slot(monkeypatch):
    request = _request()
    request.cookies = {"omlorix_active_slot": "3"}
    response = SimpleNamespace()
    user = _user()
    calls = {}

    monkeypatch.setattr(
        auth_token,
        "get_authentication",
        lambda db, user_id, token, token_type: SimpleNamespace(
            id="session-1",
            refresh_token="old-refresh",
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "build_login_2fa_session_claims",
        lambda checked_user, db: {
            "twofa_satisfied": True,
            "twofa_provider": "totp",
            "twofa_policy_version": "2fa-v1:totp",
        },
    )
    def fake_create_access_token(data, db):
        calls["access_claims"] = data
        return "new-access"

    def fake_create_refresh_token(data, db):
        calls["refresh_claims"] = data
        return "new-refresh"

    monkeypatch.setattr(auth_token, "create_access_token", fake_create_access_token)
    monkeypatch.setattr(auth_token, "create_refresh_token", fake_create_refresh_token)
    monkeypatch.setattr(
        auth_token,
        "_refresh_token_expiry",
        lambda token, db: datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    monkeypatch.setattr(
        auth_token,
        "rotate_authentication_tokens",
        lambda db, user_id, old_refresh, new_access, new_refresh, **kwargs: calls.setdefault(
            "rotation",
            (user_id, old_refresh, new_access, new_refresh, kwargs),
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "set_refresh_slot_cookie",
        lambda response, slot, refresh_token, db, request: calls.setdefault(
            "refresh_cookie",
            (slot, refresh_token),
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "set_access_token_cookie",
        lambda response, access_token, db, request: calls.setdefault("access_cookie", access_token),
    )
    monkeypatch.setattr(
        auth_token,
        "set_active_slot_cookie",
        lambda response, slot, db, request: calls.setdefault("active_cookie", slot),
    )
    monkeypatch.setattr(
        auth_token,
        "clear_legacy_refresh_cookie",
        lambda *args: pytest.fail("legacy cookie should not be cleared when it is absent"),
    )

    payload = auth_token.rotate_current_session_tokens_with_2fa_claims(
        request,
        response,
        object(),
        user,
        "old-access",
    )

    assert payload == {
        "session_authenticated": True,
        "active_account_slot": 3,
    }
    assert calls["rotation"][:4] == ("user-1", "old-refresh", "new-access", "new-refresh")
    assert calls["rotation"][4]["session_id"] == "session-1"
    assert calls["access_claims"]["twofa_policy_version"] == "2fa-v1:totp"
    assert calls["access_claims"]["sid"] == "session-1"
    assert calls["refresh_claims"]["twofa_provider"] == "totp"
    assert calls["refresh_cookie"] == (3, "new-refresh")
    assert calls["access_cookie"] == "new-access"
    assert calls["active_cookie"] == 3
