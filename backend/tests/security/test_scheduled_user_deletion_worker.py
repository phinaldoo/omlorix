import sys
import threading
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.logging import worker as retention_worker
from app.users import models as user_models


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def limit(self, _value):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, users):
        self._users = users
        self.rollbacks = 0
        self.commits = 0

    def query(self, _model):
        return _FakeQuery(self._users)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


def test_process_scheduled_user_deletions_emits_audit_events_without_overriding_auth_log_retention(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        deleted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        deletion_scheduled_for=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )
    db = _FakeDb([user])
    audit_events = []
    hard_delete_calls = []
    auth_log_delete_calls = []

    monkeypatch.setattr(
        retention_worker,
        "_write_scheduled_user_deletion_audit_event",
        lambda action, **kwargs: audit_events.append({"action": action, **kwargs}),
    )
    monkeypatch.setattr(
        user_models,
        "hard_delete_user",
        lambda db_arg, user_id, *,
        allow_administrative_target=False: hard_delete_calls.append(
            {
                "db": db_arg,
                "user_id": user_id,
                "allow_administrative_target": allow_administrative_target,
            }
        )
        or True,
    )
    monkeypatch.setattr(
        retention_worker,
        "delete_authentication_logs_for_user",
        lambda *args, **kwargs: auth_log_delete_calls.append((args, kwargs)),
    )

    assert retention_worker._process_scheduled_user_deletions(db, threading.Event()) is True

    assert hard_delete_calls == [
        {
            "db": db,
            "user_id": "user-1",
            "allow_administrative_target": True,
        }
    ]
    assert auth_log_delete_calls == []
    assert [event["action"] for event in audit_events] == [
        "SCHEDULED_HARD_DELETE_USER_STARTED",
        "SCHEDULED_HARD_DELETE_USER_COMPLETED",
    ]
    assert db.rollbacks == 0


def test_process_scheduled_user_deletions_leaves_user_pending_when_start_audit_log_fails(monkeypatch):
    user = SimpleNamespace(
        id="user-2",
        deleted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        deletion_scheduled_for=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )
    db = _FakeDb([user])
    hard_delete_calls = []

    def fail_start_event(action, **kwargs):
        if action == "SCHEDULED_HARD_DELETE_USER_STARTED":
            raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr(retention_worker, "_write_scheduled_user_deletion_audit_event", fail_start_event)
    monkeypatch.setattr(
        user_models,
        "hard_delete_user",
        lambda db_arg, user_id, **_kwargs: hard_delete_calls.append(
            {"db": db_arg, "user_id": user_id}
        )
        or True,
    )

    assert retention_worker._process_scheduled_user_deletions(db, threading.Event()) is False

    assert hard_delete_calls == []
    assert db.rollbacks == 0


def test_expired_temporary_accounts_enter_retention_and_revoke_sessions(monkeypatch):
    from app.auth import models as auth_models
    from app.auth import session_store
    from app.groups import temporary_account_retention

    expired_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    scheduled_for = datetime(2026, 7, 31, tzinfo=timezone.utc)
    user = SimpleNamespace(
        id="temporary-1",
        group_id="group-1",
        account_type="temporary",
        temporary_expires_at=expired_at,
        deleted_at=None,
        deletion_scheduled_for=None,
    )
    db = _FakeDb([user])
    audit_events = []
    authentication_deletes = []
    revoked_sessions = []

    def mark(account, _db, *, lifecycle_at):
        account.deleted_at = lifecycle_at
        account.deletion_scheduled_for = scheduled_for
        return {
            "mode": "delete_after_days",
            "purge_scheduled_at": scheduled_for,
        }

    monkeypatch.setattr(temporary_account_retention, "mark_temporary_account_for_retention", mark)
    monkeypatch.setattr(
        retention_worker,
        "_write_temporary_account_expiry_audit_event",
        lambda **kwargs: audit_events.append(kwargs),
    )
    monkeypatch.setattr(
        auth_models,
        "delete_authentication_all",
        lambda db_arg, user_id, **kwargs: authentication_deletes.append((db_arg, user_id, kwargs)),
    )
    monkeypatch.setattr(session_store, "revoke_user_sessions", revoked_sessions.append)

    assert retention_worker._process_expired_temporary_accounts(db, threading.Event()) is True

    assert user.deleted_at == expired_at
    assert user.deletion_scheduled_for == scheduled_for
    assert db.commits == 1
    assert authentication_deletes == [(
        db,
        "temporary-1",
        {"commit": False, "revoke_cached": False},
    )]
    assert revoked_sessions == ["temporary-1"]
    assert audit_events[0]["retention_mode"] == "delete_after_days"


def test_expired_temporary_account_waits_when_expiry_audit_fails(monkeypatch):
    from app.auth import models as auth_models
    from app.groups import temporary_account_retention

    user = SimpleNamespace(
        id="temporary-2",
        group_id="group-1",
        account_type="temporary",
        temporary_expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        deleted_at=None,
        deletion_scheduled_for=None,
    )
    db = _FakeDb([user])
    authentication_deletes = []

    monkeypatch.setattr(
        temporary_account_retention,
        "mark_temporary_account_for_retention",
        lambda account, _db, *, lifecycle_at: {
            "mode": "retain",
            "purge_scheduled_at": None,
        },
    )
    monkeypatch.setattr(
        retention_worker,
        "_write_temporary_account_expiry_audit_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    monkeypatch.setattr(
        auth_models,
        "delete_authentication_all",
        lambda *args, **kwargs: authentication_deletes.append((args, kwargs)),
    )

    assert retention_worker._process_expired_temporary_accounts(db, threading.Event()) is False

    assert db.commits == 0
    assert db.rollbacks == 1
    assert authentication_deletes == []
