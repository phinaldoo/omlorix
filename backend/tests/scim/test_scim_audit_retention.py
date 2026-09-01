from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

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

from app.scim import router as scim_router


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    def commit(self):
        self.commits += 1

    def refresh(self, item):
        self.refreshed.append(item)

    def add(self, _item):
        return None

    def flush(self):
        return None


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.20", 12345),
        }
    )


@pytest.mark.parametrize("token", ["current-token", "previous-token"])
def test_scim_auth_accepts_current_and_rotation_tokens(monkeypatch, token):
    """Allow a zero-downtime SCIM credential rotation window."""

    monkeypatch.setattr(
        scim_router,
        "_get_scim_settings",
        lambda _db: {
            "enable_scim": True,
            "scim_bearer_token": "current-token",
            "scim_previous_bearer_token": "previous-token",
        },
    )
    monkeypatch.setattr(
        scim_router,
        "_audit_scim_auth_rejection",
        lambda *_args, **_kwargs: pytest.fail("valid SCIM credentials must not emit a rejection event"),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/scim/v2/ServiceProviderConfig",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )

    settings = scim_router._require_scim_auth(request, db=object())

    assert settings["scim_bearer_token"] == "current-token"


def test_scim_auth_rejects_token_outside_rotation_window(monkeypatch):
    """Reject bearer credentials other than the configured rotation pair."""

    audit_calls = []
    audit_ip_databases = []
    closed = []
    request_db = object()
    monkeypatch.setattr(
        scim_router,
        "_get_scim_settings",
        lambda _db: {
            "enable_scim": True,
            "scim_bearer_token": "current-token",
            "scim_previous_bearer_token": "previous-token",
        },
    )
    monkeypatch.setattr(
        scim_router,
        "AuditSessionLocal",
        lambda: SimpleNamespace(close=lambda: closed.append(True)),
    )
    monkeypatch.setattr(
        scim_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(
        scim_router,
        "get_audit_request_ip",
        lambda _request, db: audit_ip_databases.append(db) or "203.0.113.20",
    )
    path = "/api/v1/scim/v2/" + ("x" * 300)
    request = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": path,
            "headers": [
                (b"authorization", b"Bearer rejected-token"),
                (b"user-agent", ("u" * 400).encode()),
            ],
            "client": ("203.0.113.20", 12345),
        }
    )

    with pytest.raises(scim_router.ScimException) as exc:
        scim_router._require_scim_auth(request, db=request_db)

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "SCIM_AUTHENTICATION_REJECTED"
    assert audit_calls[0]["reason"] == "invalid_bearer_token"
    assert audit_calls[0]["category"] == "scim"
    assert audit_calls[0]["details"] == {
        "method": "DELETE",
        "route": path[:256],
    }
    assert audit_calls[0]["ip_address"] == "203.0.113.20"
    assert audit_calls[0]["user_agent"] == "u" * 255
    assert "rejected-token" not in repr(audit_calls)
    assert audit_ip_databases == [request_db]
    assert closed == [True]


@pytest.mark.parametrize(
    "headers",
    [[], [(b"authorization", b"Basic credential-must-not-be-logged")]],
)
def test_scim_missing_bearer_audit_failure_preserves_401(monkeypatch, headers):
    """Audit outages cannot disclose credential state or turn denial into a 500."""

    audit_calls = []
    audit_ip_databases = []
    closed = []
    request_db = object()
    monkeypatch.setattr(
        scim_router,
        "_get_scim_settings",
        lambda _db: {
            "enable_scim": True,
            "scim_bearer_token": "current-token",
        },
    )
    monkeypatch.setattr(
        scim_router,
        "AuditSessionLocal",
        lambda: SimpleNamespace(close=lambda: closed.append(True)),
    )

    def fail_audit(**kwargs):
        audit_calls.append(kwargs)
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(scim_router, "create_audit_log", fail_audit)
    monkeypatch.setattr(
        scim_router,
        "get_audit_request_ip",
        lambda _request, db: audit_ip_databases.append(db) or "203.0.113.20",
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/scim/v2/ServiceProviderConfig",
            "headers": headers,
        }
    )

    with pytest.raises(scim_router.ScimException) as exc:
        scim_router._require_scim_auth(request, db=request_db)

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert audit_calls[0]["reason"] == "missing_bearer_token"
    assert "credential-must-not-be-logged" not in repr(audit_calls)
    assert audit_ip_databases == [request_db]
    assert closed == [True]


def test_selected_scim_without_token_remains_unavailable(monkeypatch):
    """SCIM activation can be saved without exposing an unauthenticated API."""

    monkeypatch.setattr(
        scim_router,
        "_get_scim_settings",
        lambda _db: {"enable_scim": True, "scim_bearer_token": None},
    )

    with pytest.raises(scim_router.ScimException) as exc:
        scim_router._require_scim_auth(
            _request("GET", "/api/v1/scim/v2/ServiceProviderConfig"),
            db=object(),
        )

    assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_patch_user_route_audits_security_relevant_changes(monkeypatch):
    user = SimpleNamespace(id="user-1")
    snapshots = [
        {
            "user_id": "user-1",
            "active": True,
            "role": "user",
            "group_id": "group-a",
            "scim_group_ids": ["group-a"],
            "external_id": "ext-1",
        },
        {
            "user_id": "user-1",
            "active": False,
            "role": "pending",
            "group_id": "group-b",
            "scim_group_ids": ["group-b"],
            "external_id": "ext-1",
        },
    ]
    audit_calls = []

    monkeypatch.setattr(scim_router, "_find_scim_user", lambda db, user_id: user)
    monkeypatch.setattr(scim_router, "_scim_user_audit_snapshot", lambda db, item: snapshots.pop(0))
    monkeypatch.setattr(scim_router, "_apply_patch_to_user", lambda *args, **kwargs: user)
    monkeypatch.setattr(scim_router, "_user_to_scim_resource", lambda *args, **kwargs: {"id": "user-1"})
    monkeypatch.setattr(
        scim_router,
        "_audit_scim_event",
        lambda db_log, request, action, details: audit_calls.append(
            {"action": action, "details": details}
        ),
    )

    scim_router.patch_user_route(
        "user-1",
        _request("PATCH", "/api/v1/scim/v2/Users/user-1"),
        {"Operations": [{"op": "replace", "path": "roles", "value": [{"value": "pending"}]}]},
        db=_FakeDb(),
        db_log=object(),
        settings={},
    )

    assert audit_calls == [
        {
            "action": "SCIM_USER_PATCHED",
            "details": {
                "user_id": "user-1",
                "operation_count": 1,
                "changes": {
                    "active": {"old": True, "new": False},
                    "role": {"old": "user", "new": "pending"},
                    "group_id": {"old": "group-a", "new": "group-b"},
                    "scim_group_ids": {"old": ["group-a"], "new": ["group-b"]},
                },
            },
        }
    ]


def test_scim_cannot_grant_or_mutate_administrative_roles():
    assert scim_router._extract_role(
        {"roles": [{"value": "admin"}]},
        {"scim_default_role": "admin"},
    ) == "user"
    assert scim_router._extract_role(
        {"roles": [{"value": "owner"}]},
        {"scim_default_role": "owner"},
    ) == "user"

    with pytest.raises(scim_router.ScimException) as exc_info:
        scim_router._ensure_scim_user_mutable(
            SimpleNamespace(id="admin-1", role="admin"),
        )

    assert exc_info.value.status_code == 403
    assert "scimType" not in exc_info.value.payload


def test_manager_ineligibility_uses_rfc_mutability_status_in_update_paths(monkeypatch):
    """Every SCIM user update path pairs mutability with HTTP 400."""

    monkeypatch.setattr(
        scim_router,
        "ensure_user_can_become_ineligible_manager",
        lambda *_args: (_ for _ in ()).throw(
            HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reassign group ownership first",
            )
        ),
    )

    def user():
        return SimpleNamespace(
            id="owner-1",
            email="owner@example.com",
            first_name="Owner",
            last_name="User",
            role="user",
            is_active=True,
            deleted_at=None,
        )

    update_calls = [
        lambda: scim_router._apply_scim_user_payload(
            object(),
            user(),
            {"active": False},
            {},
        ),
        lambda: scim_router._apply_patch_to_user(
            object(),
            user(),
            [{"op": "replace", "path": "active", "value": False}],
            {},
        ),
        lambda: scim_router._apply_patch_to_user(
            object(),
            user(),
            [{"op": "replace", "path": "roles", "value": [{"value": "pending"}]}],
            {},
        ),
    ]

    for update_call in update_calls:
        with pytest.raises(scim_router.ScimException) as exc_info:
            update_call()
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.payload["status"] == "400"
        assert exc_info.value.payload["scimType"] == "mutability"


def test_scim_patch_reactivation_uses_guarded_restore_before_mutation(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="Before",
        last_name="User",
        role="user",
        is_active=False,
        deleted_at=datetime.now(timezone.utc),
    )
    calls = []

    def guarded_restore(db, user_id, *, allow_already_active, commit):
        assert db is fake_db
        assert user_id == "user-1"
        assert allow_already_active is True
        assert commit is False
        assert user.first_name == "Before"
        calls.append("restore")
        user.deleted_at = None
        user.is_active = True
        return user

    fake_db = _FakeDb()
    monkeypatch.setattr(scim_router, "restore_user_state", guarded_restore)

    result = scim_router._apply_patch_to_user(
        fake_db,
        user,
        [
            {"op": "replace", "path": "name.givenName", "value": "After"},
            {"op": "replace", "path": "active", "value": True},
        ],
        {},
    )

    assert result is user
    assert calls == ["restore"]
    assert user.deleted_at is None
    assert user.is_active is True
    assert user.first_name == "After"
    assert scim_router._scim_patch_final_active(
        [{"op": "replace", "value": None}],
        initial_active=False,
    ) is True


def test_scim_patch_uses_final_active_state_and_cancels_inactive_work(monkeypatch):
    deleted_at = datetime.now(timezone.utc)
    scheduled_for = datetime.now(timezone.utc)
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="Before",
        last_name="User",
        role="user",
        is_active=False,
        deleted_at=deleted_at,
        deletion_scheduled_for=scheduled_for,
    )
    cancellations = []
    monkeypatch.setattr(
        scim_router,
        "restore_user_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an intermediate active=true restored the account")
        ),
    )
    monkeypatch.setattr(
        scim_router,
        "ensure_user_can_become_ineligible_manager",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "cancel_user_worker_jobs",
        lambda db, *, user_id, commit: cancellations.append(
            (db, user_id, commit)
        ),
    )
    fake_db = _FakeDb()
    operations = [
        {"op": "replace", "path": "active", "value": True},
        {"op": "replace", "path": "active", "value": False},
    ]

    result = scim_router._apply_patch_to_user(fake_db, user, operations, {})

    assert result is user
    assert scim_router._scim_patch_final_active(
        operations,
        initial_active=False,
    ) is False
    assert user.is_active is False
    assert user.deleted_at is deleted_at
    assert user.deletion_scheduled_for is scheduled_for
    assert cancellations == [(fake_db, "user-1", False)]


def test_scim_ineligible_final_state_cancels_work_once_for_put_and_patch(monkeypatch):
    cancellations = []
    monkeypatch.setattr(
        scim_router,
        "ensure_user_can_become_ineligible_manager",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "restore_user_state",
        lambda _db, _user_id, **_kwargs: current_user,
    )
    monkeypatch.setattr(
        scim_router,
        "_extract_role",
        lambda *_args, **_kwargs: "pending",
    )
    monkeypatch.setattr(
        scim_router,
        "mark_user_externally_managed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "_validate_external_id_uniqueness",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "_upsert_scim_user_link",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "_scim_user_link",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scim_router,
        "_sync_groups_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        scim_router,
        "cancel_user_worker_jobs",
        lambda db, *, user_id, commit: cancellations.append(
            (db, user_id, commit)
        ),
    )
    fake_db = _FakeDb()

    for apply_update, expected_active in (
        (
            lambda user: scim_router._apply_scim_user_payload(
                fake_db,
                user,
                {"active": False, "roles": [{"value": "pending"}]},
                {},
            ),
            False,
        ),
        (
            lambda user: scim_router._apply_patch_to_user(
                fake_db,
                user,
                [
                    {
                        "op": "replace",
                        "path": "roles",
                        "value": [{"value": "pending"}],
                    }
                ],
                {},
            ),
            True,
        ),
        (
            lambda user: scim_router._apply_patch_to_user(
                fake_db,
                user,
                [
                    {
                        "op": "replace",
                        "value": {
                            "active": False,
                            "roles": [{"value": "pending"}],
                        },
                    }
                ],
                {},
            ),
            False,
        ),
    ):
        current_user = SimpleNamespace(
            id="user-1",
            email="user@example.com",
            first_name="Before",
            last_name="User",
            role="user",
            group_id="group-1",
            is_active=True,
            deleted_at=None,
        )
        cancellations.clear()

        result = apply_update(current_user)

        assert result is current_user
        assert current_user.is_active is expected_active
        assert current_user.role == "pending"
        assert cancellations == [(fake_db, "user-1", False)]


def test_scim_deactivation_cancels_durable_jobs_in_same_transaction(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.workers.models import (
        DurableWorkerJob,
        JOB_CANCELLED,
        JOB_PENDING,
        JOB_PROCESSING,
        QUEUE_FILES,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[DurableWorkerJob.__table__])
    session = sessionmaker(bind=engine)()
    session.add_all(
        (
            DurableWorkerJob(
                id="queued-job",
                queue=QUEUE_FILES,
                kind="extract_text",
                user_id="user-1",
                status=JOB_PENDING,
                payload={"secret": "queued"},
                idempotency_key="queued-job",
            ),
            DurableWorkerJob(
                id="processing-job",
                queue=QUEUE_FILES,
                kind="extract_text",
                user_id="user-1",
                status=JOB_PROCESSING,
                payload={"secret": "processing"},
                idempotency_key="processing-job",
            ),
        )
    )
    session.commit()

    class ScimDb:
        def execute(self, *args, **kwargs):
            return session.execute(*args, **kwargs)

        def add(self, _item):
            return None

        def flush(self):
            session.flush()

    monkeypatch.setattr(
        scim_router,
        "ensure_user_can_become_ineligible_manager",
        lambda *_args, **_kwargs: None,
    )
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="User",
        last_name="One",
        role="user",
        is_active=True,
        deleted_at=None,
    )

    scim_router._apply_patch_to_user(
        ScimDb(),
        user,
        [{"op": "replace", "path": "active", "value": False}],
        {},
    )

    # Both the SCIM state and durable cancellation are pending in this one DB
    # transaction; committing publishes them together.
    assert user.is_active is False
    session.commit()
    queued = session.get(DurableWorkerJob, "queued-job")
    processing = session.get(DurableWorkerJob, "processing-job")
    assert queued.status == JOB_CANCELLED
    assert queued.cancel_requested is True
    assert queued.payload is None
    assert queued.error_code == "account_state_changed"
    assert processing.status == JOB_PROCESSING
    assert processing.cancel_requested is True
    assert processing.payload is None
    session.close()


def test_scim_active_response_idempotently_cancels_log_retention(monkeypatch):
    calls = []
    user = SimpleNamespace(id="user-1", is_active=True, deleted_at=None)
    monkeypatch.setattr(
        scim_router,
        "cancel_auth_log_deletions_for_user",
        lambda db, user_id: calls.append(("auth", db, user_id)),
    )
    monkeypatch.setattr(
        scim_router,
        "cancel_audit_log_deletions_for_user",
        lambda db, user_id: calls.append(("audit", db, user_id)),
    )

    scim_router._cancel_scim_active_user_retention("audit-db", user)

    assert calls == [
        ("auth", "audit-db", "user-1"),
        ("audit", "audit-db", "user-1"),
    ]


def test_patch_group_route_audits_member_changes(monkeypatch):
    group = SimpleNamespace(id="group-1")
    snapshots = [
        {
            "group_id": "group-1",
            "display_name": "Engineering",
            "external_id": "ext-group",
            "member_user_ids": ["user-1"],
        },
        {
            "group_id": "group-1",
            "display_name": "Engineering",
            "external_id": "ext-group",
            "member_user_ids": ["user-1", "user-2"],
        },
    ]
    audit_calls = []

    monkeypatch.setattr(scim_router, "_find_scim_group", lambda db, group_id: group)
    monkeypatch.setattr(scim_router, "_scim_group_audit_snapshot", lambda db, item: snapshots.pop(0))
    monkeypatch.setattr(scim_router, "_apply_patch_to_group", lambda *args, **kwargs: group)
    monkeypatch.setattr(scim_router, "_group_to_scim_resource", lambda *args, **kwargs: {"id": "group-1"})
    monkeypatch.setattr(
        scim_router,
        "_audit_scim_event",
        lambda db_log, request, action, details: audit_calls.append(
            {"action": action, "details": details}
        ),
    )

    scim_router.patch_group_route(
        "group-1",
        _request("PATCH", "/api/v1/scim/v2/Groups/group-1"),
        {"Operations": [{"op": "add", "path": "members", "value": [{"value": "user-2"}]}]},
        db=_FakeDb(),
        db_log=object(),
        settings={},
    )

    assert audit_calls == [
        {
            "action": "SCIM_GROUP_PATCHED",
            "details": {
                "group_id": "group-1",
                "operation_count": 1,
                "changes": {
                    "member_user_ids": {"old": ["user-1"], "new": ["user-1", "user-2"]},
                },
            },
        }
    ]


def test_delete_user_route_uses_retention_deletion_path(monkeypatch):
    db = _FakeDb()
    user = SimpleNamespace(id="user-1", deleted_at=None)
    purge_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    retention_calls = []
    membership_calls = []
    audit_calls = []

    monkeypatch.setattr(scim_router, "_find_scim_user", lambda db, user_id: user)
    monkeypatch.setattr(
        scim_router,
        "_scim_user_audit_snapshot",
        lambda db, item: {
            "user_id": "user-1",
            "active": True,
            "role": "user",
            "group_id": "group-a",
            "scim_group_ids": ["group-a"],
            "external_id": "ext-1",
        },
    )
    monkeypatch.setattr(scim_router, "_sync_groups_enabled", lambda settings: True)
    monkeypatch.setattr(
        scim_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_after_days", "retention_days": 30, "delete_immediately": False},
    )
    monkeypatch.setattr(
        scim_router,
        "_replace_user_memberships",
        lambda db, item, groups, settings: membership_calls.append(
            {"user_id": item.id, "groups": groups}
        ),
    )

    def fake_delete_user_with_retention(db_arg, db_log_arg, user_id, *, check_self_deletion=True):
        retention_calls.append(
            {
                "db": db_arg,
                "db_log": db_log_arg,
                "user_id": user_id,
                "check_self_deletion": check_self_deletion,
            }
        )
        return {
            "status": "success",
            "account_deletion": {
                "effect": "scheduled_deletion",
                "purge_scheduled_at": purge_at,
            },
        }

    monkeypatch.setattr(scim_router, "delete_user_with_retention", fake_delete_user_with_retention)
    monkeypatch.setattr(
        scim_router,
        "_audit_scim_event",
        lambda db_log, request, action, details: audit_calls.append(
            {"action": action, "details": details}
        ),
    )

    scim_router.delete_user_route(
        "user-1",
        _request("DELETE", "/api/v1/scim/v2/Users/user-1"),
        db=db,
        db_log="audit-db",
        settings={},
    )

    assert membership_calls == [{"user_id": "user-1", "groups": []}]
    assert retention_calls == [
        {
            "db": db,
            "db_log": "audit-db",
            "user_id": "user-1",
            "check_self_deletion": False,
        }
    ]
    assert audit_calls == [
        {
            "action": "SCIM_USER_DELETED",
            "details": {
                "user_id": "user-1",
                "changes": {
                    "active": {"old": True, "new": False},
                    "scim_group_ids": {"old": ["group-a"], "new": []},
                },
                "account_deletion": {
                    "effect": "scheduled_deletion",
                    "purge_scheduled_at": purge_at,
                },
            },
        }
    ]


def test_delete_user_route_pseudonymizes_deleted_user_reference_for_immediate_erasure(monkeypatch):
    user = SimpleNamespace(id="user-1", deleted_at=None)
    audit_calls = []

    monkeypatch.setattr(scim_router, "_find_scim_user", lambda db, user_id: user)
    monkeypatch.setattr(
        scim_router,
        "_scim_user_audit_snapshot",
        lambda db, item: {
            "user_id": "user-1",
            "active": True,
            "role": "user",
            "group_id": "group-a",
            "scim_group_ids": [],
            "external_id": "ext-1",
        },
    )
    monkeypatch.setattr(scim_router, "_sync_groups_enabled", lambda settings: False)
    monkeypatch.setattr(
        scim_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_instantly", "retention_days": None, "delete_immediately": True},
    )
    monkeypatch.setattr(
        scim_router,
        "delete_user_with_retention",
        lambda *args, **kwargs: {
            "status": "success",
            "account_deletion": {"effect": "erasure", "purge_scheduled_at": None},
        },
    )
    monkeypatch.setattr(
        scim_router,
        "_audit_scim_event",
        lambda db_log, request, action, details: audit_calls.append(
            {"action": action, "details": details}
        ),
    )

    scim_router.delete_user_route(
        "user-1",
        _request("DELETE", "/api/v1/scim/v2/Users/user-1"),
        db=_FakeDb(),
        db_log="audit-db",
        settings={},
    )

    assert len(audit_calls) == 1
    assert audit_calls[0]["details"]["user_id"].startswith("deleted-user:")
    assert audit_calls[0]["details"]["user_id"] != "user-1"
