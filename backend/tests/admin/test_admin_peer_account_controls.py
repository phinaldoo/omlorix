from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

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


class _CountQuery:
    def __init__(self, count: int):
        self._count = count

    def filter(self, *args, **kwargs):
        return self

    def count(self):
        return self._count


class _CountDb:
    def __init__(self, count: int):
        self._count = count

    def query(self, *args, **kwargs):
        return _CountQuery(self._count)


def _request():
    return SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.10"),
        headers={"user-agent": "pytest"},
    )


def _admin(user_id: str = "admin-1"):
    return SimpleNamespace(id=user_id, role="admin", is_active=True)


def _owner():
    return SimpleNamespace(id="owner-1", role="owner", is_active=True)


def _assert_owner_required(exc_info):
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only the owner can modify administrator accounts."


@pytest.fixture(autouse=True)
def _satisfy_sensitive_action_boundary(monkeypatch):
    """Keep these hierarchy tests focused on administrator authorization."""
    monkeypatch.setattr(admin_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(
        admin_router,
        "require_sensitive_action_auth",
        lambda *_args: None,
    )


def test_admin_cannot_deactivate_peer_admin(monkeypatch):
    mutation_calls: list[dict] = []
    target = _admin("admin-2")
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)
    monkeypatch.setattr(
        admin_router,
        "set_user_activation_status",
        lambda db, user_id, is_active: mutation_calls.append(
            {"db": db, "user_id": user_id, "is_active": is_active}
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.activate_user_route(
            payload=SimpleNamespace(user_id=target.id, value=False),
            request=_request(),
            db=_CountDb(3),
            db_log=object(),
            admin_user=_admin(),
        )

    _assert_owner_required(exc_info)
    assert mutation_calls == []


def test_owner_can_deactivate_peer_admin_when_another_administrator_remains(monkeypatch):
    mutation_calls: list[dict] = []
    audit_calls: list[dict] = []
    target = _admin("admin-2")
    db = _CountDb(2)
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)
    monkeypatch.setattr(
        admin_router,
        "set_user_activation_status",
        lambda db, user_id, is_active: mutation_calls.append(
            {"db": db, "user_id": user_id, "is_active": is_active}
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    result = admin_router.activate_user_route(
        payload=SimpleNamespace(user_id=target.id, value=False),
        request=_request(),
        db=db,
        db_log=object(),
        admin_user=_owner(),
    )

    assert result == {"status": "success"}
    assert mutation_calls == [{"db": db, "user_id": target.id, "is_active": False}]
    assert audit_calls[0]["action"] == "ACTIVATE_USER"


def test_owner_can_soft_delete_peer_admin(monkeypatch):
    """Owner authorization must reach the protected persistence boundary."""

    delete_calls: list[dict] = []
    target = _admin("admin-2")
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)
    monkeypatch.setattr(
        admin_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {
            "mode": "retain",
            "retention_days": None,
            "delete_immediately": False,
        },
    )
    monkeypatch.setattr(
        admin_router,
        "delete_user",
        lambda *args, **kwargs: delete_calls.append(kwargs),
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **_kwargs: None)

    result = admin_router.delete_user_route(
        payload=SimpleNamespace(user_id=target.id),
        request=_request(),
        db=_CountDb(2),
        db_log=object(),
        admin_user=_owner(),
    )

    assert result == {"status": "success"}
    assert delete_calls == [
        {
            "check_self_deletion": False,
            "allow_administrative_target": True,
        }
    ]


def test_reported_admin_demotion_then_password_takeover_chain_is_blocked(monkeypatch):
    """Regression test for the original peer-admin takeover report."""

    role_mutations: list[tuple[str, str]] = []
    password_mutations: list[dict] = []
    target = _admin("admin-2")
    db = _CountDb(3)

    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)

    def mutate_role(user_id, role, _db):
        role_mutations.append((user_id, role))
        target.role = role

    monkeypatch.setattr(admin_router, "change_user_role", mutate_role)
    monkeypatch.setattr(
        admin_router,
        "admin_update_user_profile",
        lambda payload, _db: password_mutations.append(payload),
    )

    with pytest.raises(HTTPException) as demotion_error:
        admin_router.change_role_route(
            payload=SimpleNamespace(
                user_id=target.id,
                role="user",
                reason="takeover",
            ),
            request=_request(),
            db=db,
            db_log=object(),
            admin_user=_admin(),
        )
    _assert_owner_required(demotion_error)

    # The target remains an administrator, so the follow-on reset stays
    # protected as well.
    assert target.role == "admin"
    with pytest.raises(HTTPException) as password_error:
        admin_router.admin_update_user_profile_route(
            payload=SimpleNamespace(
                user_id=target.id,
                password="attacker-password",
                reason="takeover",
            ),
            request=_request(),
            db=db,
            db_log=object(),
            admin_user=_admin(),
        )
    _assert_owner_required(password_error)
    assert role_mutations == []
    assert password_mutations == []


def test_owner_can_demote_admin_and_then_manage_the_former_admin(monkeypatch):
    """Keep the intended break-glass recovery workflow available to the owner."""

    role_mutations: list[tuple[str, str]] = []
    password_mutations: list[dict] = []
    audit_calls: list[dict] = []
    target = _admin("admin-2")
    db = _CountDb(2)

    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)

    def mutate_role(user_id, role, _db):
        role_mutations.append((user_id, role))
        target.role = role

    monkeypatch.setattr(admin_router, "change_user_role", mutate_role)
    monkeypatch.setattr(
        admin_router,
        "admin_update_user_profile",
        lambda payload, _db, **_kwargs: (
            password_mutations.append(payload)
            or {"updated_fields": ["password"], "changes": []}
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    role_result = admin_router.change_role_route(
        payload=SimpleNamespace(
            user_id=target.id,
            role="user",
            reason="account recovery",
        ),
        request=_request(),
        db=db,
        db_log=object(),
        admin_user=_owner(),
    )
    password_result = admin_router.admin_update_user_profile_route(
        payload=SimpleNamespace(
            user_id=target.id,
            password="recovery-password",
            reason="account recovery",
        ),
        request=_request(),
        db=db,
        db_log=object(),
        admin_user=_owner(),
    )

    assert role_result == {"status": "success"}
    assert password_result == {"status": "success"}
    assert role_mutations == [(target.id, "user")]
    assert len(password_mutations) == 1
    assert [entry["action"] for entry in audit_calls] == [
        "CHANGE_USER_ROLE",
        "UPDATE_USER_PROFILE",
    ]


def test_admin_cannot_promote_user_to_admin(monkeypatch):
    mutation_calls: list[tuple[str, str]] = []
    target = SimpleNamespace(id="user-1", role="user", is_active=True)
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)
    monkeypatch.setattr(
        admin_router,
        "change_user_role",
        lambda user_id, role, _db: mutation_calls.append((user_id, role)),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.change_role_route(
            payload=SimpleNamespace(
                user_id=target.id,
                role="admin",
                reason="privilege grant",
            ),
            request=_request(),
            db=_CountDb(2),
            db_log=object(),
            admin_user=_admin(),
        )

    _assert_owner_required(exc_info)
    assert mutation_calls == []


def test_owner_role_cannot_be_assigned_through_role_change_endpoint(monkeypatch):
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, _user_id: pytest.fail("invalid roles must fail before lookup"),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.change_role_route(
            payload=SimpleNamespace(
                user_id="user-1",
                role="owner",
                reason="create another owner",
            ),
            request=_request(),
            db=_CountDb(2),
            db_log=object(),
            admin_user=_owner(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid role."


def test_admin_cannot_modify_owner_account():
    with pytest.raises(HTTPException) as exc_info:
        admin_router._ensure_admin_account_change_allowed(
            db=_CountDb(2),
            admin_user=_admin(),
            target_user=_owner(),
            new_active=False,
        )

    assert exc_info.value.status_code == 403
    assert (
        exc_info.value.detail
        == "The owner account cannot be modified by another administrator."
    )


def test_admin_reset_user_twofa_route_blocks_peer_admin_resets(monkeypatch):
    mutation_calls: list[tuple[str, object]] = []
    target = _admin("admin-2")
    monkeypatch.setattr(admin_router, "get_user", lambda _db, _user_id: target)
    monkeypatch.setattr(
        admin_router,
        "clear_user_twofa_state",
        lambda user_id, db: mutation_calls.append((user_id, db)),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.admin_reset_user_twofa_route(
            payload=SimpleNamespace(user_id=target.id, reason="lockout"),
            request=_request(),
            db=object(),
            db_log=object(),
            admin_user=_admin(),
        )

    _assert_owner_required(exc_info)
    assert mutation_calls == []


def test_last_active_administrator_guard_includes_owner():
    with pytest.raises(HTTPException) as exc_info:
        admin_router._ensure_last_active_admin_not_removed(
            db=_CountDb(1),
            target_user=_owner(),
            new_active=False,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Cannot remove or deactivate the last active admin."
