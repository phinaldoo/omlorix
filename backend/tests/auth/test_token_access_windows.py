import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.auth import token as auth_token


class _RuntimeDb:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    def commit(self):
        self.commits += 1

    def refresh(self, user):
        self.refreshed.append(user)


def test_runtime_token_auth_clears_expired_user_lock(monkeypatch):
    expired_until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={
            "is_locked": True,
            "lock_until": expired_until,
            "type": "wrong_sign_in_attempts",
            "reason": "Too many failed sign-in attempts",
        },
        role="user",
        group_id="group-id",
    )
    db = _RuntimeDb()

    monkeypatch.setattr(auth_token, "is_group_accessible_now", lambda group_id, db, is_admin=False: {"accessible": True})

    auth_token.ensure_user_runtime_auth_allowed(user, db)

    assert user.lock == {"is_locked": False, "lock_until": None, "type": "", "reason": ""}
    assert db.commits == 1
    assert db.refreshed == [user]


def test_runtime_token_auth_rejects_unexpired_user_lock(monkeypatch):
    active_until = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={
            "is_locked": True,
            "lock_until": active_until,
            "type": "wrong_sign_in_attempts",
            "reason": "Too many failed sign-in attempts",
        },
        role="user",
        group_id="group-id",
    )

    monkeypatch.setattr(
        auth_token,
        "is_group_accessible_now",
        lambda group_id, db, is_admin=False: pytest.fail("locked users should be rejected before access windows"),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_token.ensure_user_runtime_auth_allowed(user, _RuntimeDb())

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail == "User is locked"


def test_runtime_token_auth_blocks_closed_access_window(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={},
        role="user",
        group_id="group-id",
    )

    next_allowed_at = datetime(2026, 5, 18, 8, 30, tzinfo=timezone.utc).isoformat()
    monkeypatch.setattr(
        auth_token,
        "is_group_accessible_now",
        lambda group_id, db, is_admin=False: {
            "accessible": False,
            "reason": "outside_allowed_window",
            "next_allowed_at": next_allowed_at,
            "blocked_message": "Come back later",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_token.ensure_user_runtime_auth_allowed(user, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "type": "access_time_blocked",
        "message": "Come back later",
        "reason": "outside_allowed_window",
        "next_allowed_at": next_allowed_at,
        "blocked_message": "Come back later",
    }


def test_runtime_token_auth_uses_default_access_window_message(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={},
        role="user",
        group_id="group-id",
    )

    monkeypatch.setattr(
        auth_token,
        "is_group_accessible_now",
        lambda group_id, db, is_admin=False: {"accessible": False},
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_token.ensure_user_runtime_auth_allowed(user, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["type"] == "access_time_blocked"
    assert exc_info.value.detail["message"] == "Access is not allowed at this time"
    assert exc_info.value.detail["next_allowed_at"] is None
    assert exc_info.value.detail["blocked_message"] is None


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_runtime_token_auth_marks_administrators_for_access_window_allowance(
    monkeypatch,
    role,
):
    user = SimpleNamespace(
        id=f"{role}-id",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={},
        role=role,
        group_id="group-id",
    )
    calls = []

    def fake_access_window(group_id, db, is_admin=False):
        calls.append(is_admin)
        return {"accessible": True}

    monkeypatch.setattr(auth_token, "is_group_accessible_now", fake_access_window)

    auth_token.ensure_user_runtime_auth_allowed(user, object())

    assert calls == [True]
