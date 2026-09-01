import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.auth import token as auth_token
from app.auth import utils as auth_utils


def test_login_eligibility_rejects_temporary_account_without_expiry(monkeypatch):
    user = SimpleNamespace(
        role="user",
        deleted_at=None,
        is_active=True,
        account_type="temporary",
        temporary_expires_at=None,
    )

    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)

    assert auth_utils.validate_user_login_eligibility(user, object()) == {"status": "temporary_expired"}


def test_runtime_token_auth_rejects_temporary_account_without_expiry():
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="temporary",
        temporary_expires_at=None,
        lock={},
        role="user",
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_token.ensure_user_runtime_auth_allowed(user, object())

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail == "Temporary account has expired"


def test_refresh_token_rejects_temporary_account_without_expiry(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="temporary",
        temporary_expires_at=None,
        lock={},
        role="user",
    )

    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: ("refresh-token", None))
    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: ("secret", "HS512"))
    monkeypatch.setattr(
        auth_token.jwt,
        "decode",
        lambda token, secret, algorithms: {"sub": "user-id", "type": "refresh", "exp": 9999999999},
    )
    monkeypatch.setattr(auth_token, "session_token_exists", lambda user_id, token, token_type: True)
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(
            state="current",
            authentication=SimpleNamespace(id="session-1"),
        ),
    )
    monkeypatch.setattr(auth_token, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)

    with pytest.raises(HTTPException) as exc_info:
        auth_token.get_access_token_by_refresh_token(
            SimpleNamespace(client=None, cookies={}),
            SimpleNamespace(),
            object(),
            object(),
        )

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail == "Temporary account has expired"
