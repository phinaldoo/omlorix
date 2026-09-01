from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import token as auth_token


JWT_SECRET = "r" * 64


def _refresh_token() -> str:
    return auth_token.jwt.encode(
        {
            "sub": "user-1",
            "type": "refresh",
            "exp": (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
        },
        JWT_SECRET,
        algorithm="HS512",
    )


def _request(*, client_host: str = "203.0.113.10", forwarded_for: str | None = None):
    headers = {}
    if forwarded_for:
        headers["x-forwarded-for"] = forwarded_for
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers=headers,
        cookies={},
    )


def _user(**overrides):
    values = {
        "id": "user-1",
        "deleted_at": None,
        "is_active": True,
        "account_type": "regular",
        "temporary_expires_at": None,
        "lock": {},
        "role": "user",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def refresh_runtime(monkeypatch):
    refresh_token = _refresh_token()
    auth_entry = SimpleNamespace(id="session-1", access_token="old-access-token")

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (JWT_SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda *_args: (refresh_token, None))
    monkeypatch.setattr(auth_token, "session_token_exists", lambda *_args: True)
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="current",
            authentication=auth_entry,
        ),
    )
    monkeypatch.setattr(auth_token, "cache_refresh_token", lambda *_args: None)
    monkeypatch.setattr(auth_token, "cache_access_token", lambda *_args: None)
    monkeypatch.setattr(auth_token, "get_client_ip", lambda *_args: None)
    monkeypatch.setattr(
        auth_token,
        "create_access_token",
        lambda *_args, **_kwargs: pytest.fail("refresh should reject before minting an access token"),
    )

    return SimpleNamespace(db=SimpleNamespace(), db_log=SimpleNamespace(), response=SimpleNamespace())


@pytest.mark.parametrize(
    ("user", "expected_status"),
    [
        (_user(deleted_at=datetime.now(timezone.utc)), 410),
        (_user(role="pending"), 409),
    ],
)
def test_refresh_uses_shared_runtime_check_for_blocked_account_states(monkeypatch, refresh_runtime, user, expected_status):
    monkeypatch.setattr(auth_token, "get_user", lambda *_args: user)

    def fake_ensure_user_runtime_auth_allowed(checked_user, _db, **_kwargs):
        assert checked_user is user
        raise HTTPException(status_code=expected_status)

    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", fake_ensure_user_runtime_auth_allowed)

    with pytest.raises(HTTPException) as exc_info:
        auth_token.get_access_token_by_refresh_token(
            _request(),
            refresh_runtime.response,
            refresh_runtime.db,
            refresh_runtime.db_log,
        )

    assert exc_info.value.status_code == expected_status


def test_refresh_runtime_check_uses_request_ip(monkeypatch, refresh_runtime):
    user = _user()
    runtime_check = {}

    def fake_ensure_user_runtime_auth_allowed(checked_user, _db, **kwargs):
        runtime_check["user"] = checked_user
        runtime_check["ip_address"] = kwargs.get("ip_address")
        runtime_check["event_source"] = kwargs.get("event_source")
        raise HTTPException(status_code=403)

    monkeypatch.setattr(auth_token, "get_user", lambda *_args: user)
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: "203.0.113.77")
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", fake_ensure_user_runtime_auth_allowed)

    with pytest.raises(HTTPException) as exc_info:
        auth_token.get_access_token_by_refresh_token(
            _request(client_host="127.0.0.1", forwarded_for="203.0.113.77"),
            refresh_runtime.response,
            refresh_runtime.db,
            refresh_runtime.db_log,
        )

    assert exc_info.value.status_code == 403
    assert runtime_check == {
        "user": user,
        "ip_address": "203.0.113.77",
        "event_source": "refresh_token",
    }
