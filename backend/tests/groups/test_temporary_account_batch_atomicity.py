import os
import re
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())


from app.database import Base
from app.groups import management
from app.groups.models import Group
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Group.__table__, User.__table__])
    return sessionmaker(bind=engine)()


def _seed_acting_user(db):
    now = datetime.now(timezone.utc)
    db.add(
        Group(
            id="group-1",
            name="Test Group",
            kind="standard",
            parent_id=None,
            settings={},
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        User(
            id="manager-1",
            email="manager@example.com",
            hashed_password="hashed-manager",
            first_name="Manager",
            last_name="User",
            role="user",
            group_id="group-1",
            settings=deepcopy(DEFAULT_USER_SETTINGS),
            created_at=now,
            last_active_at=now,
        )
    )
    db.commit()
    return SimpleNamespace(id="manager-1", role="user")


def _temporary_settings():
    return {
        "temporary_accounts": {
            "enabled": True,
            "max_active_accounts": 50,
            "credential_length": 24,
        }
    }


def test_create_temporary_accounts_rolls_back_entire_batch_on_failure(monkeypatch):
    db = _session()
    acting_user = _seed_acting_user(db)

    monkeypatch.setattr(management, "require_group_capability", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(management, "get_group_settings", lambda *_args, **_kwargs: _temporary_settings())
    monkeypatch.setattr(management, "hash_password", lambda secret: f"hashed-{secret}")
    monkeypatch.setattr(
        management,
        "_generate_unique_temporary_email",
        lambda *_args, **_kwargs: "duplicate@temporary.local",
    )

    with pytest.raises(IntegrityError):
        management.create_temporary_accounts(
            db,
            acting_user,
            "group-1",
            count=2,
            expiry_hours=8,
        )

    temporary_users = db.query(User).filter(User.account_type == "temporary").all()
    assert temporary_users == []


def test_create_temporary_accounts_commits_full_batch_with_default_welcome_state(monkeypatch):
    db = _session()
    acting_user = _seed_acting_user(db)

    monkeypatch.setattr(management, "require_group_capability", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(management, "get_group_settings", lambda *_args, **_kwargs: _temporary_settings())
    monkeypatch.setattr(management, "hash_password", lambda secret: f"hashed-{secret}")

    result = management.create_temporary_accounts(
        db,
        acting_user,
        "group-1",
        count=2,
        expiry_hours=8,
    )

    assert [entry["id"] for entry in result["created"]]

    temporary_users = (
        db.query(User)
        .filter(User.group_id == "group-1", User.account_type == "temporary")
        .order_by(User.email.asc())
        .all()
    )
    assert len(temporary_users) == 2
    assert all(
        re.fullmatch(r"testgroup\.[23456789abcdefghjkmnpqrstuvwxyz]{4}@temporary\.local", user.email)
        for user in temporary_users
    )
    assert len({user.email for user in temporary_users}) == 2
    assert all(user.settings["states"]["welcome_card_dismissed"] is False for user in temporary_users)


def test_missing_expiry_defaults_to_eight_hours(monkeypatch):
    db = _session()
    acting_user = _seed_acting_user(db)
    fixed_now = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(management, "require_group_capability", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(management, "get_group_settings", lambda *_args, **_kwargs: _temporary_settings())
    monkeypatch.setattr(management, "_now", lambda: fixed_now)
    monkeypatch.setattr(management, "hash_password", lambda secret: f"hashed-{secret}")

    result = management.create_temporary_accounts(
        db,
        acting_user,
        "group-1",
        count=1,
        expiry_hours=None,
    )

    assert result["expires_at"] == fixed_now + timedelta(hours=8)
    assert result["created"][0]["expires_at"] == fixed_now + timedelta(hours=8)


def test_revoke_temporary_account_schedules_retention_and_revokes_sessions(monkeypatch):
    """Revocation must update data lifecycle and authentication atomically."""

    original_expiry = datetime(2026, 7, 1, tzinfo=timezone.utc)
    scheduled_for = datetime(2026, 7, 31, tzinfo=timezone.utc)
    account = SimpleNamespace(
        id="temporary-1",
        group_id="group-1",
        account_type="temporary",
        temporary_expires_at=original_expiry,
        deleted_at=None,
        deletion_scheduled_for=None,
        is_active=True,
    )

    class _LifecycleDb:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    db = _LifecycleDb()
    authentication_deletes = []
    session_revocations = []

    monkeypatch.setattr(management, "get_user", lambda *_args, **_kwargs: account)
    monkeypatch.setattr(
        management,
        "require_group_capability",
        lambda *_args, **_kwargs: {"source_group_id": "group-1"},
    )
    monkeypatch.setattr(
        management,
        "_ensure_user_within_management_scope",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(management, "revoke_user_sessions", session_revocations.append)

    from app.auth import models as auth_models
    from app.groups import temporary_account_retention

    def mark_for_retention(target, _db, *, lifecycle_at):
        target.deleted_at = lifecycle_at
        target.deletion_scheduled_for = scheduled_for
        return {"mode": "delete_after_days", "purge_scheduled_at": scheduled_for}

    monkeypatch.setattr(
        temporary_account_retention,
        "mark_temporary_account_for_retention",
        mark_for_retention,
    )
    monkeypatch.setattr(
        auth_models,
        "delete_authentication_all",
        lambda db_arg, user_id, **kwargs: authentication_deletes.append(
            (db_arg, user_id, kwargs)
        ),
    )

    result = management.revoke_temporary_account(
        db,
        SimpleNamespace(id="manager-1", role="user"),
        account.id,
    )

    assert account.is_active is False
    assert account.temporary_expires_at == original_expiry
    assert account.deleted_at == original_expiry
    assert account.deletion_scheduled_for == scheduled_for
    assert db.commits == 1
    assert db.rollbacks == 0
    assert authentication_deletes == [
        (db, account.id, {"commit": False, "revoke_cached": False})
    ]
    assert session_revocations == [account.id]
    assert result == {
        "status": "revoked",
        "user_id": account.id,
        "group_id": account.group_id,
        "retention_mode": "delete_after_days",
        "deletion_scheduled_for": scheduled_for,
    }
