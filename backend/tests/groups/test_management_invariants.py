"""Focused regressions for delegated group-management invariants."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

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

from app.database import Base
from app.groups.management import (
    managed_groups_for_user,
    promote_group_member,
    require_group_capability,
)
from app.groups import management as group_management
import app.groups.models as group_models
from app.groups.models import (
    Group,
    GroupManager,
    add_group_manager,
    ensure_user_can_become_ineligible_manager,
    export_groups,
    import_groups,
)
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User, set_user_activation_status, soft_delete_user


def _session():
    """Create the minimal relational schema required by these tests."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, Group.__table__, GroupManager.__table__],
    )
    return sessionmaker(bind=engine)()


def _group(group_id: str, *, parent_id: str | None = None) -> Group:
    now = datetime.now(timezone.utc)
    return Group(
        id=group_id,
        name=group_id.title(),
        kind="standard",
        parent_id=parent_id,
        settings={},
        created_at=now,
        updated_at=now,
    )


def _user(user_id: str, *, group_id: str = "leaf", **overrides) -> User:
    now = datetime.now(timezone.utc)
    values = {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "hashed_password": "hashed",
        "first_name": user_id,
        "last_name": "User",
        "role": "user",
        "group_id": group_id,
        "account_type": "regular",
        "is_active": True,
        "settings": deepcopy(DEFAULT_USER_SETTINGS),
        "created_at": now,
        "last_active_at": now,
    }
    values.update(overrides)
    return User(**values)


def _assignment(group_id: str, user_id: str, role: str, created_at: datetime) -> GroupManager:
    return GroupManager(
        group_id=group_id,
        user_id=user_id,
        role=role,
        created_at=created_at,
        updated_at=created_at,
    )


def test_postgresql_lifecycle_guard_locks_user_before_checking_assignments():
    """Serialize eligibility changes against concurrent manager assignment."""

    ineligible_user = SimpleNamespace(
        account_type="temporary",
        is_active=True,
        deleted_at=None,
        role="user",
    )
    user_query = MagicMock()
    user_query.filter.return_value = user_query
    user_query.with_for_update.return_value = user_query
    user_query.first.return_value = ineligible_user
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.query.return_value = user_query

    ensure_user_can_become_ineligible_manager(db, "user-1")

    user_query.with_for_update.assert_called_once_with()
    user_query.first.assert_called_once_with()


def test_postgresql_assignment_uses_the_same_user_then_group_lock_order(monkeypatch):
    """Make promotion serialize with lifecycle changes without a lock cycle."""

    lock_order: list[str] = []
    user = SimpleNamespace(
        id="user-1",
        account_type="regular",
        is_active=True,
        deleted_at=None,
        role="user",
    )
    group = SimpleNamespace(id="group-1")

    user_query = MagicMock()
    user_query.filter.return_value = user_query
    user_query.with_for_update.side_effect = lambda: lock_order.append("user") or user_query
    user_query.first.return_value = user

    group_query = MagicMock()
    group_query.filter.return_value = group_query
    group_query.with_for_update.side_effect = lambda: lock_order.append("group") or group_query
    group_query.first.return_value = group

    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.query.side_effect = lambda model: user_query if model is User else group_query
    monkeypatch.setattr(group_models, "get_group", lambda *_args: group)
    monkeypatch.setattr(group_models, "get_group_manager", lambda *_args: None)

    add_group_manager(db, group_id=group.id, user_id=user.id, role="owner")

    assert lock_order == ["user", "group"]


@pytest.mark.parametrize("broader_created_first", [True, False])
def test_equal_capability_assignments_always_use_broadest_scope(broader_created_first):
    db = _session()
    now = datetime.now(timezone.utc)
    actor = _user("actor")
    db.add_all([_group("root"), _group("child", parent_id="root"), _group("leaf", parent_id="child"), actor])
    earlier, later = now, now + timedelta(seconds=1)
    db.add_all(
        [
            _assignment("root", actor.id, "manager", earlier if broader_created_first else later),
            _assignment("child", actor.id, "manager", later if broader_created_first else earlier),
        ]
    )
    db.commit()

    effective = require_group_capability(db, actor, "leaf", "manage_temporary_accounts")

    assert effective["source_group_id"] == "root"


def test_group_capability_denial_audit_is_bounded_and_contains_only_safe_context(monkeypatch):
    audit_calls = []
    closed = []
    actor = SimpleNamespace(id="actor-" + ("a" * 100), role="user")
    group_id = "group-" + ("g" * 200)
    target_user_id = "target-" + ("t" * 200)

    monkeypatch.setattr(group_management, "get_group", lambda *_args: SimpleNamespace(id=group_id))
    monkeypatch.setattr(group_management, "_management_entry_for_user", lambda *_args: None)
    monkeypatch.setattr(
        group_management,
        "AuditSessionLocal",
        lambda: SimpleNamespace(close=lambda: closed.append(True)),
    )
    monkeypatch.setattr(
        group_management,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc_info:
        require_group_capability(
            object(),
            actor,
            group_id,
            "promote_members",
            attempted_action="promote_group_member-" + ("x" * 200),
            target_user_id=target_user_id,
        )

    assert exc_info.value.status_code == 403
    assert len(audit_calls) == 1
    audit = audit_calls[0]
    assert audit["action"] == "GROUP_MANAGEMENT_ACCESS_DENIED"
    assert audit["reason"] == "missing_group_capability"
    assert len(audit["user_id"]) == 64
    assert len(audit["details"]["attempted_action"]) == 128
    assert len(audit["details"]["group_id"]) == 128
    assert len(audit["details"]["target_user_id"]) == 128
    assert set(audit["details"]) == {
        "reason",
        "attempted_action",
        "required_capability",
        "group_id",
        "target_user_id",
    }
    assert "settings" not in repr(audit).lower()
    assert "credential" not in repr(audit).lower()
    assert closed == [True]


def test_group_capability_audit_failure_preserves_403_and_account_target(monkeypatch):
    audit_calls = []
    closed = []
    actor = SimpleNamespace(id="manager-1", role="user")

    monkeypatch.setattr(group_management, "get_group", lambda *_args: SimpleNamespace(id="group-1"))
    monkeypatch.setattr(group_management, "_management_entry_for_user", lambda *_args: None)
    monkeypatch.setattr(
        group_management,
        "AuditSessionLocal",
        lambda: SimpleNamespace(close=lambda: closed.append(True)),
    )

    def fail_audit(**kwargs):
        audit_calls.append(kwargs)
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(group_management, "create_audit_log", fail_audit)

    with pytest.raises(HTTPException) as exc_info:
        require_group_capability(
            object(),
            actor,
            "group-1",
            "manage_temporary_accounts",
            attempted_action="revoke_temporary_account",
            account_user_id="temporary-1",
        )

    assert exc_info.value.status_code == 403
    assert audit_calls[0]["details"]["account_user_id"] == "temporary-1"
    assert closed == [True]


def test_managed_group_summary_includes_direct_regular_member_count():
    db = _session()
    admin = _user("admin", role="admin", group_id="root")
    regular = _user("regular", group_id="root")
    temporary = _user("temporary", group_id="root", account_type="temporary")
    db.add_all([_group("root"), admin, regular, temporary])
    db.commit()

    groups = managed_groups_for_user(db, admin)

    assert groups[0]["direct_member_count"] == 2
    assert groups[0]["temporary_account_count"] == 1


def test_group_roles_are_promotion_only_and_final_owner_stays_active():
    db = _session()
    owner = _user("owner")
    admin = _user("admin", role="admin")
    db.add_all([_group("leaf"), owner, admin])
    db.add(_assignment("leaf", owner.id, "owner", datetime.now(timezone.utc)))
    db.commit()

    with pytest.raises(HTTPException) as demotion_error:
        promote_group_member(db, admin, "leaf", owner.id, "manager")
    assert demotion_error.value.status_code == 409

    with pytest.raises(HTTPException) as lifecycle_error:
        set_user_activation_status(db, owner.id, False)
    assert lifecycle_error.value.status_code == 409
    assert owner.is_active is True

    with pytest.raises(HTTPException) as deletion_error:
        soft_delete_user(db, owner.id)
    assert deletion_error.value.status_code == 409
    assert owner.deleted_at is None

    second_owner = _user("second-owner")
    db.add(second_owner)
    db.add(_assignment("leaf", second_owner.id, "owner", datetime.now(timezone.utc)))
    db.commit()

    with pytest.raises(HTTPException) as second_demotion_error:
        promote_group_member(db, admin, "leaf", owner.id, "manager")
    assert second_demotion_error.value.status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"account_type": "temporary"},
        {"is_active": False},
        {"role": "pending"},
        {"deleted_at": datetime.now(timezone.utc)},
    ],
)
def test_ineligible_users_cannot_be_promoted(overrides):
    db = _session()
    admin = _user("admin", role="admin")
    target = _user("target", **overrides)
    db.add_all([_group("leaf"), admin, target])
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        promote_group_member(db, admin, "leaf", target.id, "owner")

    assert exc_info.value.status_code == 400
    assert db.query(GroupManager).filter(GroupManager.user_id == target.id).count() == 0


def test_only_direct_group_members_can_be_promoted():
    """Inherited owners cannot pull users from other groups into a role."""

    db = _session()
    admin = _user("admin", role="admin")
    target = _user("target", group_id="other-group")
    db.add_all([_group("leaf"), _group("other-group"), admin, target])
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        promote_group_member(db, admin, "leaf", target.id, "coordinator")

    assert exc_info.value.status_code == 400
    assert db.query(GroupManager).filter(GroupManager.user_id == target.id).count() == 0


def test_member_can_progress_through_roles_but_never_move_sideways_or_down():
    """Enforce monotonic role priority independently of the frontend selects."""

    db = _session()
    admin = _user("admin", role="admin")
    target = _user("target")
    db.add_all([_group("leaf"), admin, target])
    db.commit()

    promoted = promote_group_member(db, admin, "leaf", target.id, "coordinator")
    assert promoted["role"] == "coordinator"
    with pytest.raises(HTTPException) as lateral_error:
        promote_group_member(db, admin, "leaf", target.id, "coordinator")
    assert lateral_error.value.status_code == 409

    assert promote_group_member(db, admin, "leaf", target.id, "manager")["role"] == "manager"
    assert promote_group_member(db, admin, "leaf", target.id, "owner")["role"] == "owner"
    with pytest.raises(HTTPException) as downward_error:
        promote_group_member(db, admin, "leaf", target.id, "manager")
    assert downward_error.value.status_code == 409


def test_group_export_and_import_preserves_manager_roles():
    source = _session()
    owner = _user("owner")
    coordinator = _user("coordinator")
    source.add_all([_group("leaf"), owner, coordinator])
    now = datetime.now(timezone.utc)
    source.add_all([
        _assignment("leaf", owner.id, "owner", now),
        _assignment("leaf", coordinator.id, "coordinator", now),
    ])
    source.commit()
    payload = export_groups(source)
    assert "description" not in payload["data"]["groups"][0]
    exported_coordinator = next(
        entry
        for entry in payload["data"]["group_managers"]
        if entry["user_id"] == coordinator.id
    )
    assert exported_coordinator["role"] == "coordinator"

    target = _session()
    target.add_all([_user("owner"), _user("coordinator")])
    target.commit()
    result = import_groups(target, payload)

    assert result["manager_errors"] == []
    assert {
        (entry.group_id, entry.user_id, entry.role)
        for entry in target.query(GroupManager).all()
    } == {
        ("leaf", "owner", "owner"),
        ("leaf", "coordinator", "coordinator"),
    }


@pytest.mark.parametrize("version", [None, True, 0.9, 2.0, "1.0"])
def test_group_import_rejects_unsupported_export_versions(version):
    """Only the numeric version 1.0 snapshot contract is importable."""
    payload = {
        "export_type": "group",
        "export_version": version,
        "data": {"groups": [], "group_managers": []},
    }

    with pytest.raises(HTTPException) as exc_info:
        import_groups(_session(), payload)

    assert exc_info.value.status_code == 400
    assert "Expected '1.0'" in exc_info.value.detail


def test_group_import_accepts_browser_normalized_integer_version():
    """Parsed JSON may normalize the supported decimal version to an integer."""

    result = import_groups(
        _session(),
        {
            "export_type": "group",
            "export_version": 1,
            "data": {"groups": [], "group_managers": []},
        },
    )

    assert result["created"] == []
    assert result["errors"] == []


def test_group_import_rejects_removed_teacher_role():
    """The snapshot importer must not translate retired role names."""
    source = _session()
    source.add_all([_group("leaf"), _user("coordinator")])
    source.commit()
    payload = export_groups(source)
    payload["data"]["group_managers"] = [
        {"group_id": "leaf", "user_id": "coordinator", "role": "teacher"}
    ]

    target = _session()
    target.add(_user("coordinator"))
    target.commit()

    result = import_groups(target, payload)

    assert result["imported_managers"] == []
    assert result["manager_errors"] == [
        {"index": 0, "error": "Manager entry has an invalid group, user, or role."}
    ]
