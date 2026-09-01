from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

from _otel_test_stubs import install_otel_stubs

install_otel_stubs()

from app.admin import router as admin_root_router
from app.admin.users import router as admin_router
from app.admin.users.schemas import AdminUserChatMessagesRequest, AdminUserChatsRequest
from app.groups import management as group_management
from app.users import utils as users_utils


def _build_user() -> SimpleNamespace:
    return SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="Ada",
        last_name="Lovelace",
        group_id="group-1",
        role="user",
        is_active=True,
        settings={"secret": {"wrong_sign_in_attempts": 4}},
        lock={"is_locked": True, "lock_until": "2026-01-01T00:00:00+00:00", "type": "manual", "reason": "Review"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def test_get_admin_user_profile_omits_sensitive_sections_by_default(monkeypatch):
    user = _build_user()
    user.auth_management_mode = "external"
    user.external_auth_provider = "oidc"
    monkeypatch.setattr(users_utils, "get_user", lambda _db, _user_id: user)
    monkeypatch.setattr(users_utils, "get_group", lambda _db, _group_id: SimpleNamespace(name="Support"))

    profile = users_utils.get_admin_user_profile("user-1", object())

    expected_metadata = {
        "id": "user-1",
        "email": "user@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "group_id": "group-1",
        "group_name": "Support",
        "role": "user",
        "is_active": True,
        "externally_managed": True,
        "external_auth_provider": "oidc",
    }
    assert {key: profile[key] for key in expected_metadata} == expected_metadata
    assert {
        "settings",
        "wrong_sign_in_attempts",
        "lock",
        "created_at",
        "last_active_at",
    }.isdisjoint(profile)


def test_get_admin_user_profile_includes_requested_sections(monkeypatch):
    monkeypatch.setattr(users_utils, "get_user", lambda _db, _user_id: _build_user())
    monkeypatch.setattr(users_utils, "get_group", lambda _db, _group_id: SimpleNamespace(name="Support"))

    profile = users_utils.get_admin_user_profile(
        "user-1",
        object(),
        include_sensitive_profile=True,
        include_security=True,
        include_activity=True,
    )

    assert profile["wrong_sign_in_attempts"] == 4
    assert profile["lock"]["is_locked"] is True
    assert profile["created_at"] == "2026-01-01T00:00:00+00:00"
    assert profile["last_active_at"] == "2026-01-02T00:00:00+00:00"


def test_admin_read_user_profile_route_requires_reason_for_sensitive_reads(monkeypatch):
    monkeypatch.setattr(admin_router, "get_admin_user_profile", lambda *_args, **_kwargs: {})

    with pytest.raises(HTTPException) as excinfo:
        admin_router.admin_read_user_profile_route(
            payload=SimpleNamespace(
                user_id="user-1",
                include_sensitive_profile=True,
                include_security=False,
                include_activity=False,
                reason=None,
            ),
            request=SimpleNamespace(
                client=SimpleNamespace(host="198.51.100.10"),
                headers={"user-agent": "pytest"},
            ),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "An admin access reason is required to view sensitive profile details."


def test_admin_read_user_profile_route_excludes_unrequested_fields_from_response():
    profile_route = next(
        route
        for route in admin_root_router.admin_router.routes
        if str(getattr(route, "path", "")).endswith("/user/profile")
        and "POST" in getattr(route, "methods", set())
    )

    assert profile_route.response_model_exclude_none is True


def test_admin_read_user_profile_route_audits_viewed_categories(monkeypatch):
    audit_calls: list[dict] = []
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: _build_user())
    monkeypatch.setattr(
        admin_router,
        "get_admin_user_profile",
        lambda *_args, **_kwargs: {
            "id": "user-1",
            "email": "user@example.com",
            "role": "user",
            "is_active": True,
            "wrong_sign_in_attempts": 4,
        },
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = admin_router.admin_read_user_profile_route(
        payload=SimpleNamespace(
            user_id="user-1",
            include_sensitive_profile=True,
            include_security=True,
            include_activity=False,
            reason="Investigating lockout report",
        ),
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response["id"] == "user-1"
    assert len(audit_calls) == 1
    assert audit_calls[0]["reason"] == "Investigating lockout report"
    assert audit_calls[0]["details"] == {
        "target_user": "user-1",
        "viewed_categories": ["basic_profile", "sensitive_profile", "account_security"],
    }
    assert audit_calls[0]["ip_address"] == "198.51.100.10"


def test_admin_read_user_profile_route_audits_forwarded_client_ip(monkeypatch):
    audit_calls: list[dict] = []
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: _build_user())
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")
    monkeypatch.setattr(
        admin_router,
        "get_admin_user_profile",
        lambda *_args, **_kwargs: {
            "id": "user-1",
            "email": "user@example.com",
            "role": "user",
            "is_active": True,
        },
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    admin_router.admin_read_user_profile_route(
        payload=SimpleNamespace(
            user_id="user-1",
            include_sensitive_profile=False,
            include_security=False,
            include_activity=False,
            reason="Reviewing support ticket",
        ),
        request=SimpleNamespace(
            client=SimpleNamespace(host="172.18.0.4"),
            headers={
                "user-agent": "pytest",
                "x-forwarded-for": "203.0.113.10, 172.18.0.2",
            },
        ),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert audit_calls[0]["ip_address"] == "203.0.113.10"


def test_change_role_route_audits_old_and_new_role(monkeypatch):
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, _user_id: SimpleNamespace(id="user-1", role="user", is_active=True),
    )
    monkeypatch.setattr(admin_router, "change_user_role", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    result = admin_router.change_role_route(
        payload=SimpleNamespace(user_id="user-1", role="admin", reason="Temporary admin cover"),
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="owner-1", role="owner"),
    )

    assert result == {"status": "success"}
    assert audit_calls[0]["action"] == "CHANGE_USER_ROLE"
    assert audit_calls[0]["reason"] == "Temporary admin cover"
    assert audit_calls[0]["details"] == {
        "user_id": "user-1",
        "target_user": "user-1",
        "role": "admin",
        "old_role": "user",
        "new_role": "admin",
    }


def test_admin_update_user_profile_route_audits_field_changes(monkeypatch):
    audit_calls: list[dict] = []
    changes = [{"field": "group_id", "old": "group-1", "new": "group-2"}]
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: _build_user())
    monkeypatch.setattr(
        admin_router,
        "admin_update_user_profile",
        lambda *_args, **_kwargs: {"status": "success", "updated_fields": ["group_id"], "changes": changes},
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    result = admin_router.admin_update_user_profile_route(
        payload=SimpleNamespace(user_id="user-1", reason="Move to new class", group_id="group-2"),
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result == {"status": "success"}
    assert audit_calls[0]["action"] == "UPDATE_USER_PROFILE"
    assert audit_calls[0]["reason"] == "Move to new class"
    assert audit_calls[0]["details"] == {
        "target_user": "user-1",
        "updated_fields": ["group_id"],
        "changes": changes,
    }


def test_admin_update_user_profile_returns_group_change_context(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="Ada",
        last_name="Lovelace",
        group_id="group-1",
        settings={},
        lock=None,
    )
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = user
    monkeypatch.setattr(users_utils, "get_group", lambda _db, _group_id: SimpleNamespace(id="group-2"))

    result = users_utils.admin_update_user_profile(
        SimpleNamespace(
            user_id="user-1",
            email=None,
            first_name=None,
            last_name=None,
            group_id="group-2",
            password=None,
            wrong_sign_in_attempts=None,
            lock=None,
        ),
        db,
    )

    assert result["updated_fields"] == ["group_id"]
    assert result["changes"] == [{"field": "group_id", "old": "group-1", "new": "group-2"}]


def test_promote_group_member_audit_context_distinguishes_role_update(monkeypatch):
    acting_user = SimpleNamespace(id="manager-1", role="user")
    target_user = SimpleNamespace(
        id="user-1",
        account_type="regular",
        is_active=True,
        deleted_at=None,
        role="user",
        group_id="group-1",
    )
    existing_manager = SimpleNamespace(role="coordinator")
    updated_manager = SimpleNamespace(role="owner")

    monkeypatch.setattr(
        group_management,
        "require_group_capability",
        lambda *_args, **_kwargs: {
            "source_group_id": "department-1",
            "role": "owner",
            "capabilities": ["promote_members"],
        },
    )
    monkeypatch.setattr(group_management, "get_user", lambda *_args, **_kwargs: target_user)
    monkeypatch.setattr(group_management, "get_group_manager", lambda *_args: existing_manager)
    monkeypatch.setattr(group_management, "add_group_manager", lambda *_args, **_kwargs: updated_manager)
    monkeypatch.setattr(group_management, "_serialize_manager_entry", lambda *_args: {"role": "owner"})

    result, audit_context = group_management.promote_group_member_with_audit_context(
        object(),
        acting_user,
        "group-1",
        "user-1",
        "owner",
    )

    assert result == {"role": "owner"}
    assert audit_context == {
        "action": "PROMOTE_GROUP_MANAGER_ROLE",
        "details": {
            "group_id": "group-1",
            "target_user": "user-1",
            "manager_user_id": "user-1",
            "scope_group_id": "department-1",
            "scope_role": "owner",
            "role": "owner",
            "old_role": "coordinator",
            "new_role": "owner",
        },
    }


def test_admin_get_user_chats_includes_archived_chats(monkeypatch):
    list_calls = []
    audit_calls = []

    def fake_list_chats(**kwargs):
        list_calls.append(kwargs)
        return [SimpleNamespace(id="archived-chat")]

    monkeypatch.setattr(admin_router, "list_chats", fake_list_chats)
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    db = object()

    result = admin_router.admin_get_user_chats_route(
        payload=AdminUserChatsRequest(
            user_id="user-1",
            reason="moderation review",
        ),
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=db,
        db_log=object(),
        user=SimpleNamespace(id="admin-1"),
    )

    assert result == [SimpleNamespace(id="archived-chat")]
    assert list_calls == [
        {
            "user_id": "user-1",
            "db": db,
            "include_archived": True,
        }
    ]
    assert audit_calls[0]["action"] == "GET_CHATS_FOR_USER"
    assert audit_calls[0]["details"] == {"target_user": "user-1"}


def test_admin_get_user_chat_messages_uses_constant_audit_action(monkeypatch):
    """Keep target identifiers in details so action remains aggregatable."""
    audit_calls = []
    monkeypatch.setattr(admin_router, "get_chat_messages", lambda **_kwargs: [])
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    admin_router.admin_get_user_chat_messages_route(
        payload=AdminUserChatMessagesRequest(
            user_id="user-1",
            chat_id="chat-1",
            reason="moderation review",
        ),
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="admin-1"),
    )

    assert audit_calls[0]["action"] == "GET_CHAT_MESSAGES_FOR_USER"
    assert audit_calls[0]["details"] == {
        "target_user": "user-1",
        "chat_id": "chat-1",
    }
