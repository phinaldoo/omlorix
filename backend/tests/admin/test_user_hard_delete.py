from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

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

from app.admin.users import router as admin_router
from app.admin.users.schemas import AdminUserIdRequest
from app.auth.models import Authentication
from app.users import utils as user_utils


class _FakeQuery:
    def __init__(self, rows=None):
        self._rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, authentications=None):
        self.authentications = authentications or []
        self.commits = 0

    def query(self, model):
        if model is Authentication:
            return _FakeQuery(self.authentications)
        return _FakeQuery([])

    def delete(self, _row):
        return None

    def commit(self):
        self.commits += 1


def test_admin_hard_delete_route_uses_shared_delete_workflow(monkeypatch):
    delete_calls = []
    audit_calls = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(id=user_id, role="admin", is_active=True),
    )

    monkeypatch.setattr(
        admin_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        admin_router,
        "delete_user",
        lambda db, db_log, user_id, *, check_self_deletion=True, force_hard_delete=False,
        allow_administrative_target=False: delete_calls.append(
            {
                "db": db,
                "db_log": db_log,
                "user_id": user_id,
                "check_self_deletion": check_self_deletion,
                "force_hard_delete": force_hard_delete,
                "allow_administrative_target": allow_administrative_target,
            }
        ),
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))
    monkeypatch.setattr(admin_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(admin_router, "require_sensitive_action_auth", lambda *_args: None)

    db = object()
    db_log = object()
    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.25"),
        headers={"user-agent": "pytest"},
    )
    admin_user = SimpleNamespace(id="owner-1", role="owner")

    response = admin_router.hard_delete_user_route(
        AdminUserIdRequest(user_id="user-2"),
        request,
        db=db,
        db_log=db_log,
        admin_user=admin_user,
        token="access-token",
    )

    assert response == {"status": "success"}
    assert delete_calls == [
        {
            "db": db,
            "db_log": db_log,
            "user_id": "user-2",
            "check_self_deletion": False,
            "force_hard_delete": True,
            "allow_administrative_target": True,
        }
    ]
    assert audit_calls[0]["action"] == "HARD_DELETE_USER"
    assert audit_calls[0]["details"] == {"user_id": "user-2"}


def test_admin_hard_delete_route_pseudonymizes_deleted_user_reference_for_immediate_audit_erasure(monkeypatch):
    audit_calls = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(id=user_id, role="user", is_active=True),
    )

    monkeypatch.setattr(
        admin_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_instantly", "retention_days": None, "delete_immediately": True},
    )
    monkeypatch.setattr(admin_router, "delete_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))
    monkeypatch.setattr(admin_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(admin_router, "require_sensitive_action_auth", lambda *_args: None)

    admin_router.hard_delete_user_route(
        AdminUserIdRequest(user_id="user-2"),
        SimpleNamespace(
            client=SimpleNamespace(host="203.0.113.25"),
            headers={"user-agent": "pytest"},
        ),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
        token="access-token",
    )

    assert len(audit_calls) == 1
    assert audit_calls[0]["details"]["user_id"].startswith("deleted-user:")
    assert audit_calls[0]["details"]["user_id"] != "user-2"


def test_delete_user_force_hard_delete_bypasses_retention_mode(monkeypatch):
    hard_delete_calls = []
    soft_delete_calls = []
    auth_cancel_calls = []
    audit_cancel_calls = []

    def fake_get_value_by_page_and_key(page, key, _db):
        values = {
            ("users", "user_deletion_mode"): "retain",
            ("security", "auth_logs_retention_after_user_delete_mode"): "retain",
            ("security", "audit_logs_retention_after_user_delete_mode"): "retain",
        }
        return values.get((page, key))

    monkeypatch.setattr(user_utils, "get_value_by_page_and_key", fake_get_value_by_page_and_key)
    monkeypatch.setattr(
        user_utils,
        "get_auth_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        user_utils,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda _db, user_id: SimpleNamespace(
            id=user_id,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "hard_delete_user",
        lambda db, user_id, *, allow_administrative_target=False: hard_delete_calls.append(
            {
                "db": db,
                "user_id": user_id,
                "allow_administrative_target": allow_administrative_target,
            }
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "soft_delete_user",
        lambda db, user_id, scheduled_for=None, *,
        allow_administrative_target=False: soft_delete_calls.append(
            {
                "db": db,
                "user_id": user_id,
                "scheduled_for": scheduled_for,
                "allow_administrative_target": allow_administrative_target,
            }
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "cancel_auth_log_deletions_for_user",
        lambda db_log, user_id: auth_cancel_calls.append({"db_log": db_log, "user_id": user_id}),
    )
    monkeypatch.setattr(
        user_utils,
        "cancel_audit_log_deletions_for_user",
        lambda db_log, user_id: audit_cancel_calls.append({"db_log": db_log, "user_id": user_id}),
    )

    db = _FakeDb()
    db_log = object()

    response = user_utils.delete_user(
        db,
        db_log,
        "user-7",
        check_self_deletion=False,
        force_hard_delete=True,
    )

    assert response == {
        "status": "success",
        "account_deletion": {
            "mode": "delete_instantly",
            "effect": "erasure",
            "restorable": False,
            "retention_days": None,
            "purge_scheduled_at": None,
        },
    }
    assert hard_delete_calls == [
        {
            "db": db,
            "user_id": "user-7",
            "allow_administrative_target": False,
        }
    ]
    assert soft_delete_calls == []
    assert auth_cancel_calls == [{"db_log": db_log, "user_id": "user-7"}]
    assert audit_cancel_calls == [{"db_log": db_log, "user_id": "user-7"}]
    # The hard-delete persistence helper owns its transaction; the shared
    # workflow must not add a redundant commit after it returns.
    assert db.commits == 0
