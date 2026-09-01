from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.admin.users import router as admin_router
from app.users import router as users_router
from app.users import utils as user_utils


class _DeleteQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def delete(self, *, synchronize_session=False):
        return 0


class _DeleteDb:
    def __init__(self, events):
        self.events = events

    def query(self, _model):
        return _DeleteQuery()

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))


def test_immediate_soft_delete_fences_queued_audit_state_before_commit(monkeypatch):
    events = []
    db = _DeleteDb(events)
    user = SimpleNamespace(
        id="user-1",
        role="user",
        account_type="regular",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    deleted_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(user_utils, "get_user", lambda *_args: user)
    monkeypatch.setattr(
        user_utils,
        "get_user_deletion_policy",
        lambda _db: {
            "mode": "retain",
            "effect": "deactivation",
            "restorable": True,
            "retention_days": None,
            "purge_scheduled_at": None,
        },
    )
    monkeypatch.setattr(
        user_utils,
        "get_auth_log_user_deletion_retention_policy",
        lambda _db: {
            "mode": "retain",
            "retention_days": None,
            "delete_immediately": False,
        },
    )
    monkeypatch.setattr(
        user_utils,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {
            "mode": "delete_instantly",
            "retention_days": None,
            "delete_immediately": True,
        },
    )
    monkeypatch.setattr(
        user_utils,
        "soft_delete_user",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="user-1",
            deleted_at=deleted_at,
            deletion_scheduled_for=None,
        ),
    )
    monkeypatch.setattr(user_utils, "delete_authentication_all", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        user_utils,
        "invalidate_user_password_reset_tokens",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        user_utils,
        "delete_user_transient_auth_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(user_utils, "revoke_user_sessions", lambda *_args: None)
    monkeypatch.setattr(user_utils, "_apply_auth_log_retention", lambda *_args: None)
    monkeypatch.setattr(
        user_utils,
        "_apply_audit_log_retention",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated post-commit audit outage")
        ),
    )

    import app.email.models as email_models
    import app.email.service as email_service
    import app.workers.events as worker_events
    import app.workers.models as worker_models

    monkeypatch.setattr(email_models, "cancel_user_email", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(email_service, "enqueue_security_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_models,
        "erase_user_audit_event_state",
        lambda _db, *, user_id, commit: events.append(
            ("audit_fenced", user_id, commit)
        ),
    )
    monkeypatch.setattr(
        worker_events,
        "enqueue_audit_erasure",
        lambda _db, *, user_id, boundary_id, commit: events.append(
            ("audit_cleanup_enqueued", user_id, boundary_id, commit)
        ),
    )

    with pytest.raises(RuntimeError, match="post-commit audit outage"):
        user_utils.delete_user(
            db,
            object(),
            user.id,
            check_self_deletion=False,
        )

    assert events[0] == ("audit_fenced", "user-1", False)
    assert events[1] == (
        "audit_cleanup_enqueued",
        "user-1",
        deleted_at.isoformat(),
        False,
    )
    assert events[2] == ("commit",)


def test_user_deletion_no_longer_reads_or_purges_backups_by_requester(monkeypatch):
    """Permanent user erasure must leave full-system backup jobs untouched."""

    hard_delete_calls = []
    monkeypatch.setattr(user_utils, "get_user_group_setting_value", lambda *args: True)
    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: "delete_instantly" if key == "user_deletion_mode" else None,
    )
    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda _db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "hard_delete_user",
        lambda _db, user_id, **_kwargs: hard_delete_calls.append(user_id),
    )
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
    monkeypatch.setattr(user_utils, "cancel_auth_log_deletions_for_user", lambda *args: None)
    monkeypatch.setattr(user_utils, "cancel_audit_log_deletions_for_user", lambda *args: None)

    result = user_utils.delete_user(object(), object(), "user-1")

    assert result["status"] == "success"
    assert hard_delete_calls == ["user-1"]


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


def _allow_sensitive_action(monkeypatch, router_module) -> None:
    monkeypatch.setattr(router_module, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(
        router_module,
        "require_sensitive_action_auth",
        lambda *_args: None,
    )


def test_user_delete_route_audits_failed_deletion_attempt(monkeypatch):
    audit_entries = []
    failure = HTTPException(
        status_code=500,
        detail=(
            "Account deletion was not completed because backup artifacts "
            "could not be purged. Please resolve backup artifact erasure "
            "and retry."
        ),
    )

    monkeypatch.setattr(
        users_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(users_router, "delete_user", lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(users_router, "create_audit_log", lambda **kwargs: audit_entries.append(kwargs))
    _allow_sensitive_action(monkeypatch, users_router)

    with pytest.raises(HTTPException) as exc_info:
        users_router.delete_user_route(
            _request("DELETE", "/api/v1/users/delete"),
            db=object(),
            db_log=object(),
            user=SimpleNamespace(id="user-1"),
            token="access-token",
        )

    assert exc_info.value is failure
    assert len(audit_entries) == 1
    entry = audit_entries[0]
    assert entry["user_id"] == "user-1"
    assert entry["action"] == "DELETE_ACCOUNT"
    assert entry["details"] == {"status": "failed", "detail": failure.detail}
    assert entry["ip_address"] == "203.0.113.10"
    assert entry["user_agent"] == "pytest"
    assert entry["category"] == "user"


def test_user_delete_route_skips_success_audit_when_immediate_erasure_is_enabled(monkeypatch):
    audit_entries = []

    monkeypatch.setattr(
        users_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_instantly", "retention_days": None, "delete_immediately": True},
    )
    monkeypatch.setattr(
        users_router,
        "delete_user",
        lambda *args, **kwargs: {
            "status": "success",
            "account_deletion": {"effect": "erasure", "purge_scheduled_at": None},
        },
    )
    monkeypatch.setattr(users_router, "create_audit_log", lambda **kwargs: audit_entries.append(kwargs))
    _allow_sensitive_action(monkeypatch, users_router)

    result = users_router.delete_user_route(
        _request("DELETE", "/api/v1/users/delete"),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
        token="access-token",
    )

    assert result["status"] == "success"
    assert audit_entries == []


def test_admin_delete_route_audits_failed_deletion_attempt(monkeypatch):
    audit_entries = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(id=user_id, role="user", is_active=True),
    )
    failure = HTTPException(
        status_code=500,
        detail=(
            "Account deletion was not completed because backup artifacts "
            "could not be purged. Please resolve backup artifact erasure "
            "and retry."
        ),
    )

    monkeypatch.setattr(
        admin_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(admin_router, "delete_user", lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_entries.append(kwargs))
    _allow_sensitive_action(monkeypatch, admin_router)

    with pytest.raises(HTTPException) as exc_info:
        admin_router.delete_user_route(
            payload=SimpleNamespace(user_id="user-2"),
            request=_request("POST", "/api/v1/admin/user/delete"),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
            token="access-token",
        )

    assert exc_info.value is failure
    assert len(audit_entries) == 1
    entry = audit_entries[0]
    assert entry["user_id"] == "admin-1"
    assert entry["action"] == "DELETE_USER"
    assert entry["details"] == {
        "user_id": "user-2",
        "status": "failed",
        "detail": failure.detail,
    }
    assert entry["ip_address"] == "203.0.113.10"
    assert entry["user_agent"] == "pytest"
    assert entry["category"] == "admin"


def test_admin_delete_route_pseudonymizes_deleted_user_reference_for_immediate_erasure(monkeypatch):
    audit_entries = []
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
    monkeypatch.setattr(
        admin_router,
        "delete_user",
        lambda *args, **kwargs: {"status": "success", "account_deletion": {"effect": "erasure"}},
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_entries.append(kwargs))
    _allow_sensitive_action(monkeypatch, admin_router)

    result = admin_router.delete_user_route(
        payload=SimpleNamespace(user_id="user-2"),
        request=_request("POST", "/api/v1/admin/user/delete"),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
        token="access-token",
    )

    assert result == {"status": "success"}
    assert len(audit_entries) == 1
    assert audit_entries[0]["details"]["user_id"].startswith("deleted-user:")
    assert audit_entries[0]["details"]["user_id"] != "user-2"
