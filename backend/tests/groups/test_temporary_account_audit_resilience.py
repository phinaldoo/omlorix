"""Ensure one-time credentials survive an audit-store outage."""

import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.requests import Request

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

from app.groups import management_router
from app.groups.schemas import (
    CreateTemporaryAccountsPayload,
    GroupMemberPromotionResult,
    PromoteGroupMemberPayload,
)


def test_delegated_group_router_does_not_expose_membership_mutations():
    """Regular-user membership changes belong exclusively to admin APIs."""

    delegated_paths = {route.path for route in management_router.group_management_router.routes}

    assert not any("/members" in path for path in delegated_paths)


def test_delegated_group_router_exposes_only_upward_manager_mutation():
    """Manager removal and arbitrary assignment routes must stay admin-only."""

    delegated_routes = {
        (route.path, frozenset(route.methods or []))
        for route in management_router.group_management_router.routes
    }

    assert (
        "/api/v1/group-management/groups/{group_id}/manager-candidates",
        frozenset({"GET"}),
    ) in delegated_routes
    assert (
        "/api/v1/group-management/groups/{group_id}/manager-promotions",
        frozenset({"POST"}),
    ) in delegated_routes
    assert not any(
        path.endswith("/managers") or "/managers/" in path
        for path, _methods in delegated_routes
    )


def test_temporary_expiry_schema_matches_720_hour_service_limit():
    assert "prefix" not in CreateTemporaryAccountsPayload.model_fields
    assert CreateTemporaryAccountsPayload(count=1, expiry_hours=720).expiry_hours == 720
    with pytest.raises(ValidationError):
        CreateTemporaryAccountsPayload(count=1, expiry_hours=721)


def test_successful_creation_is_returned_when_separate_audit_store_fails(monkeypatch):
    expected = {
        "created": [{"id": "temporary-1", "email": "temp@example.com", "password": "one-time"}],
        "expires_at": None,
    }
    monkeypatch.setattr(management_router, "create_temporary_accounts", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(
        management_router,
        "create_audit_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    monkeypatch.setattr(management_router, "get_audit_request_ip", lambda *_args: None)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    result = management_router.managed_group_create_temporary_accounts_route(
        "group-1",
        CreateTemporaryAccountsPayload(count=1, expiry_hours=8),
        request,
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="manager-1"),
    )

    assert result["created"][0]["password"] == "one-time"
    assert result["audit_logged"] is False


def test_successful_promotion_is_returned_when_separate_audit_store_fails(monkeypatch):
    """A committed promotion must remain successful if audit storage is down."""

    expected = {
        "user": {
            "id": "member-1",
            "email": "member@example.com",
            "first_name": "Group",
            "last_name": "Member",
            "status": "active",
        },
        "role": "manager",
        "capabilities": ["view_group", "view_members", "manage_settings"],
    }
    audit_context = {
        "action": "PROMOTE_GROUP_MEMBER",
        "details": {"group_id": "group-1", "target_user": "member-1"},
    }
    monkeypatch.setattr(
        management_router,
        "promote_group_member_with_audit_context",
        lambda *_args, **_kwargs: (expected, audit_context),
    )
    monkeypatch.setattr(
        management_router,
        "create_audit_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    monkeypatch.setattr(management_router, "get_audit_request_ip", lambda *_args: None)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    result = management_router.managed_group_promote_member_route(
        "group-1",
        PromoteGroupMemberPayload(user_id="member-1", role="manager"),
        request,
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="owner-1"),
    )

    assert result["role"] == "manager"
    assert result["audit_logged"] is False
    assert GroupMemberPromotionResult.model_validate(result).audit_logged is False


def test_successful_revocation_is_returned_when_separate_audit_store_fails(monkeypatch):
    """A committed revocation must not be reported as a failed operation."""

    expected = {
        "status": "revoked",
        "user_id": "temporary-1",
        "group_id": "group-1",
        "retention_mode": "delete_after_days",
        "deletion_scheduled_for": None,
    }
    monkeypatch.setattr(
        management_router,
        "revoke_temporary_account",
        lambda *_args, **_kwargs: expected,
    )
    monkeypatch.setattr(
        management_router,
        "create_audit_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    monkeypatch.setattr(management_router, "get_audit_request_ip", lambda *_args: None)
    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": []})

    result = management_router.managed_group_revoke_temporary_account_route(
        "temporary-1",
        request,
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="manager-1"),
    )

    assert result["status"] == "revoked"
    assert result["audit_logged"] is False
