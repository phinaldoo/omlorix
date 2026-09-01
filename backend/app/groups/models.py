from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict
import uuid

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, func
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.groups.settings_validation import sanitize_group_settings_for_storage
from app.users.models import User
from app.utils.export_versions import matches_export_version


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (
        Index("ix_id", "id"),
        Index("ix_groups_parent_id", "parent_id"),
        UniqueConstraint("name", name="uq_groups_name"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    kind = Column(String, nullable=False, default="standard")
    parent_id = Column(String, ForeignKey("groups.id"), nullable=True)
    settings = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class GroupManager(Base):
    __tablename__ = "group_managers"
    __table_args__ = (
        Index("ix_group_managers_group_id", "group_id"),
        Index("ix_group_managers_user_id", "user_id"),
        UniqueConstraint("group_id", "user_id", name="uq_group_managers_group_user"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False, default="manager")
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


def _normalize_group_name(name: str | None) -> str | None:
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    return normalized or None


def _normalize_group_kind(value: str | None) -> str:
    candidate = str(value or "standard").strip().lower()
    return candidate or "standard"


def create_group(
    db,
    group_id: str | None,
    name: str,
    settings,
    *,
    parent_id: str | None = None,
    kind: str = "standard",
    commit: bool = True,
) -> Group:
    normalized_name = _normalize_group_name(name)
    if not normalized_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name is required")

    normalized_parent_id = str(parent_id or "").strip() or None
    if normalized_parent_id:
        parent = get_group(db, normalized_parent_id)
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent group not found")
    now = datetime.now(timezone.utc)
    payload = {
        "name": normalized_name,
        "kind": _normalize_group_kind(kind),
        "parent_id": normalized_parent_id,
        "settings": settings,
        "created_at": now,
        "updated_at": now,
    }
    if group_id:
        payload["id"] = group_id

    group = Group(**payload)
    db.add(group)
    if commit:
        db.commit()
    else:
        # A flushed group can receive its initial manager assignments in the
        # same transaction, so a validation failure does not leave an orphaned
        # group behind.
        db.flush()
    db.refresh(group)
    return group


def get_group(db, group_id: str):
    if not group_id:
        return None
    return db.query(Group).filter(Group.id == group_id).first()


def list_all_groups(db):
    return db.query(Group).order_by(func.lower(Group.name).asc()).all()


def get_group_children(db, group_id: str) -> list[Group]:
    return db.query(Group).filter(Group.parent_id == group_id).order_by(func.lower(Group.name).asc()).all()


def get_group_by_name(db, name: str):
    normalized = _normalize_group_name(name)
    if not normalized:
        return None
    lowered = normalized.lower()
    return db.query(Group).filter(func.lower(Group.name) == lowered).first()


def group_name_exists(db, name: str, exclude_id: str | None = None) -> bool:
    normalized = _normalize_group_name(name)
    if not normalized:
        return False
    lowered = normalized.lower()
    query = db.query(Group.id).filter(func.lower(Group.name) == lowered)
    if exclude_id:
        query = query.filter(Group.id != exclude_id)
    return query.first() is not None


def delete_group(group_id: str, db) -> Dict[str, str]:
    from app.settings.utils import get_value_by_page_and_key

    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    if db.query(Group.id).filter(Group.parent_id == group_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group has child groups. Reassign or delete them first.",
        )

    default_group_id = get_value_by_page_and_key("login_general", "default_user_group", db) or "default"
    if group.id == default_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Default group cannot be deleted. Change the default group first.",
        )

    default_group = db.query(Group).filter(Group.id == default_group_id).first()
    if not default_group:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configured default group is missing",
        )

    db.query(User).filter(User.group_id == group_id).update(
        {User.group_id: default_group_id},
        synchronize_session=False,
    )
    db.query(GroupManager).filter(GroupManager.group_id == group_id).delete(synchronize_session=False)
    db.delete(group)
    db.commit()
    return {"status": "deleted", "id": group_id}


def group_exists(db, group_id: str) -> bool:
    if not group_id:
        return False
    return get_group(db, group_id) is not None


def list_groups(db):
    return db.query(Group).order_by(func.lower(Group.name).asc()).all()


def list_group_managers(db, group_id: str) -> list[GroupManager]:
    return (
        db.query(GroupManager)
        .filter(GroupManager.group_id == group_id)
        .order_by(func.lower(GroupManager.role).asc(), GroupManager.created_at.asc())
        .all()
    )


def get_group_manager(db, group_id: str, user_id: str) -> GroupManager | None:
    return (
        db.query(GroupManager)
        .filter(GroupManager.group_id == group_id, GroupManager.user_id == user_id)
        .first()
    )


def _eligible_group_manager_query(db):
    """Build the shared eligibility predicate for delegated managers."""

    return (
        db.query(GroupManager)
        .join(User, User.id == GroupManager.user_id)
        .filter(
            User.account_type == "regular",
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.role != "pending",
        )
    )


def _uses_postgresql(db) -> bool:
    """Return whether row-level PostgreSQL locking is available for a session."""

    try:
        bind = db.get_bind()
    except (AttributeError, TypeError):
        # A few model-level tests intentionally use minimal session doubles.
        # They do not support database locks, so preserve their SQLite-like
        # behavior while production PostgreSQL sessions take the locks below.
        return False
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


def count_eligible_group_owners(db, group_id: str) -> int:
    """Count usable owners while serializing concurrent owner mutations."""

    if _uses_postgresql(db):
        db.query(Group.id).filter(Group.id == group_id).with_for_update().one()
    return (
        _eligible_group_manager_query(db)
        .filter(GroupManager.group_id == group_id, GroupManager.role == "owner")
        .count()
    )


def ensure_user_can_become_ineligible_manager(db, user_id: str) -> None:
    """Reject lifecycle changes that would strand a group without an owner."""

    user_query = db.query(User).filter(User.id == user_id)
    if _uses_postgresql(db):
        # Assignment paths take the same user-first lock before locking a
        # group. Holding it across the ownership check prevents a concurrent
        # promotion from committing between this check and the lifecycle
        # update that makes the user ineligible.
        user_query = user_query.with_for_update()
    user = user_query.first()
    if not user or (
        getattr(user, "account_type", "regular") != "regular"
        or not bool(getattr(user, "is_active", True))
        or getattr(user, "deleted_at", None) is not None
        or getattr(user, "role", "user") == "pending"
    ):
        # An already-ineligible assignment is not contributing to the invariant,
        # so deleting its stale row cannot strand the group.
        return

    owned_group_ids = sorted([
        group_id
        for (group_id,) in (
            db.query(GroupManager.group_id)
            .filter(GroupManager.user_id == user_id, GroupManager.role == "owner")
            .all()
        )
    ])
    stranded = [
        group_id
        for group_id in owned_group_ids
        if count_eligible_group_owners(db, group_id) <= 1
    ]
    if stranded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reassign ownership before deactivating or deleting this group's final active owner",
        )


def add_group_manager(db, *, group_id: str, user_id: str, role: str = "manager") -> GroupManager:
    # Preserve the established not-found precedence while the locked lookup
    # below protects against deletion after this initial validation.
    if not get_group(db, group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    user_query = db.query(User).filter(User.id == user_id)
    if _uses_postgresql(db):
        # Use the shared user-then-group lock order used by lifecycle checks
        # and complete assignment replacement.
        user_query = user_query.with_for_update()
    user = user_query.first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    group_query = db.query(Group).filter(Group.id == group_id)
    if _uses_postgresql(db):
        group_query = group_query.with_for_update()
    if not group_query.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if (
        user.account_type != "regular"
        or not user.is_active
        or user.deleted_at is not None
        or user.role == "pending"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active, approved, permanent users can manage groups",
        )
    existing = get_group_manager(db, group_id, user_id)
    if existing:
        existing.role = str(role or "manager").strip().lower() or "manager"
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    now = datetime.now(timezone.utc)
    manager = GroupManager(
        group_id=group_id,
        user_id=user_id,
        role=str(role or "manager").strip().lower() or "manager",
        created_at=now,
        updated_at=now,
    )
    db.add(manager)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = get_group_manager(db, group_id, user_id)
        if existing:
            return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group manager already exists but could not be loaded",
        )
    db.refresh(manager)
    return manager


def remove_group_manager(db, *, group_id: str, user_id: str) -> bool:
    manager = get_group_manager(db, group_id, user_id)
    if not manager:
        return False
    db.delete(manager)
    db.commit()
    return True


def replace_group_manager_assignments(
    db,
    *,
    group_id: str,
    owner_user_ids: list[str],
    manager_user_ids: list[str],
    coordinator_user_ids: list[str],
    commit: bool = True,
) -> dict[str, object]:
    """Replace every direct manager assignment for a group in one transaction.

    The admin group form submits all three role buckets together. Treating the
    payload as a complete replacement makes removals and role changes
    deterministic while still allowing an owner swap without briefly leaving
    the group ownerless.
    """

    if not get_group(db, group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    assignments_by_role = {
        "owner": [str(user_id).strip() for user_id in owner_user_ids],
        "manager": [str(user_id).strip() for user_id in manager_user_ids],
        "coordinator": [str(user_id).strip() for user_id in coordinator_user_ids],
    }
    selected_role_by_user: dict[str, str] = {}
    for role, user_ids in assignments_by_role.items():
        for user_id in user_ids:
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Manager user IDs must not be empty",
                )
            if user_id in selected_role_by_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user can only have one management role in a group",
                )
            selected_role_by_user[user_id] = role

    selected_user_ids = set(selected_role_by_user)
    selected_users = []
    if selected_user_ids:
        selected_users_query = (
            db.query(User)
            .filter(User.id.in_(selected_user_ids))
            .order_by(User.id.asc())
        )
        if _uses_postgresql(db):
            # Lock every selected user in stable order before the group lock.
            # A concurrent lifecycle transition must either finish first and
            # fail eligibility below, or wait until this assignment commits.
            selected_users_query = selected_users_query.with_for_update()
        selected_users = selected_users_query.all()

    group_query = db.query(Group).filter(Group.id == group_id)
    if _uses_postgresql(db):
        group_query = group_query.with_for_update()
    group = group_query.first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    users_by_id = {user.id: user for user in selected_users}
    missing_user_ids = sorted(selected_user_ids.difference(users_by_id))
    if missing_user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more selected manager users no longer exist",
        )

    ineligible_user_ids = sorted(
        user_id
        for user_id, user in users_by_id.items()
        if (
            user.account_type != "regular"
            or not user.is_active
            or user.deleted_at is not None
            or user.role == "pending"
        )
    )
    if ineligible_user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active, approved, permanent users can manage groups",
        )

    existing_assignments = (
        db.query(GroupManager)
        .filter(GroupManager.group_id == group_id)
        .all()
    )
    existing_by_user = {
        assignment.user_id: assignment for assignment in existing_assignments
    }
    had_eligible_owner = count_eligible_group_owners(db, group_id) > 0
    # A legacy or newly created group may have no owner. Once ownership has
    # been established, however, the complete replacement must retain at least
    # one eligible owner. An owner swap remains valid because it is evaluated
    # against the final submitted state.
    if had_eligible_owner and not assignments_by_role["owner"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A group must keep at least one active owner",
        )

    now = datetime.now(timezone.utc)
    for assignment in existing_assignments:
        next_role = selected_role_by_user.get(assignment.user_id)
        if next_role is None:
            db.delete(assignment)
            continue
        if assignment.role != next_role:
            assignment.role = next_role
            assignment.updated_at = now

    for user_id, role in selected_role_by_user.items():
        if user_id in existing_by_user:
            continue
        db.add(
            GroupManager(
                group_id=group_id,
                user_id=user_id,
                role=role,
                created_at=now,
                updated_at=now,
            )
        )

    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "owner_user_ids": list(assignments_by_role["owner"]),
        "manager_user_ids": list(assignments_by_role["manager"]),
        "coordinator_user_ids": list(assignments_by_role["coordinator"]),
        "total": len(selected_role_by_user),
    }


current_group_export_version = 1.0


def export_groups(db):
    groups = db.query(Group).order_by(Group.created_at.asc()).all()
    export_data = []

    for group in groups:
        export_data.append(
            {
                "id": group.id,
                "name": group.name,
                "parent_id": group.parent_id,
                # Exports are self-contained snapshots even when a test
                # fixture or manually inserted row is still sparse.
                "settings": sanitize_group_settings_for_storage(group.settings or {}),
                "created_at": group.created_at.isoformat() if group.created_at else None,
                "updated_at": group.updated_at.isoformat() if group.updated_at else None,
            }
        )

    managers = db.query(GroupManager).order_by(GroupManager.created_at.asc()).all()
    manager_data = [
        {
            "group_id": manager.group_id,
            "user_id": manager.user_id,
            "role": manager.role,
            "created_at": manager.created_at.isoformat() if manager.created_at else None,
            "updated_at": manager.updated_at.isoformat() if manager.updated_at else None,
        }
        for manager in managers
    ]

    return {
        "export_type": "group",
        "export_version": current_group_export_version,
        "data": {
            "groups": export_data,
            "group_managers": manager_data,
        },
    }


def import_groups(db, payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid import payload. Expected an object.",
        )

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")

    if export_type != "group":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export_type '{export_type}'.",
        )

    if not matches_export_version(export_version, current_group_export_version):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{current_group_export_version}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. Missing 'data' object.",
        )

    raw_groups = data_block.get("groups")
    if not isinstance(raw_groups, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. 'groups' must be a list.",
        )

    raw_managers = data_block.get("group_managers", [])
    if not isinstance(raw_managers, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. 'group_managers' must be a list.",
        )

    created: list[dict[str, str]] = []
    errors: list[dict[str, object]] = []
    pending_parent_links: list[tuple[str, str]] = []

    for index, group_entry in enumerate(raw_groups):
        if not isinstance(group_entry, dict):
            errors.append({"index": index, "error": "Group entry must be an object."})
            continue

        group_id = group_entry.get("id")
        if group_id is not None and (not isinstance(group_id, str) or not group_id.strip()):
            errors.append({"index": index, "error": "Group id must be a non-empty string when provided."})
            continue

        if group_id:
            existing_by_id = db.query(Group).filter(Group.id == group_id).first()
            if existing_by_id:
                errors.append({"index": index, "id": group_id, "error": "Group id already exists."})
                continue

        name = group_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append({"index": index, "error": "Group name is required."})
            continue

        if get_group_by_name(db, name.strip()):
            errors.append({"index": index, "name": name, "error": "Group name already exists."})
            continue

        raw_settings = group_entry.get("settings") or {}
        if not isinstance(raw_settings, dict):
            errors.append({"index": index, "name": name, "error": "Group settings must be an object."})
            continue

        parent_id = str(group_entry.get("parent_id") or "").strip() or None
        try:
            settings = sanitize_group_settings_for_storage(raw_settings)
            group_obj = create_group(
                db,
                group_id.strip() if isinstance(group_id, str) else None,
                name.strip(),
                settings,
                parent_id=None,
            )
            if parent_id:
                pending_parent_links.append((group_obj.id, parent_id))
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})
            continue

        created.append({
            "id": group_obj.id,
            "name": group_obj.name,
        })

    linkage_errors = []
    for group_id, parent_id in pending_parent_links:
        group = get_group(db, group_id)
        if group:
            parent_group = get_group(db, parent_id)
            if parent_group:
                group.parent_id = parent_id
            else:
                linkage_errors.append({
                    "group_id": group_id,
                    "parent_id": parent_id,
                    "error": f"Parent group with id '{parent_id}' not found for child group '{group_id}'"
                })
        else:
            linkage_errors.append({
                "group_id": group_id,
                "parent_id": parent_id,
                "error": f"Child group with id '{group_id}' not found"
            })
    
    if pending_parent_links:
        db.commit()

    imported_managers: list[dict[str, str]] = []
    manager_errors: list[dict[str, object]] = []
    created_group_ids = {entry["id"] for entry in created}
    for index, manager_entry in enumerate(raw_managers):
        if not isinstance(manager_entry, dict):
            manager_errors.append({"index": index, "error": "Manager entry must be an object."})
            continue
        group_id = str(manager_entry.get("group_id") or "").strip()
        user_id = str(manager_entry.get("user_id") or "").strip()
        role = str(manager_entry.get("role") or "").strip().lower()
        if not group_id or not user_id or role not in {"owner", "manager", "coordinator"}:
            manager_errors.append({"index": index, "error": "Manager entry has an invalid group, user, or role."})
            continue
        if group_id not in created_group_ids:
            manager_errors.append({"index": index, "group_id": group_id, "error": "Imported group was not created."})
            continue
        try:
            manager = add_group_manager(db, group_id=group_id, user_id=user_id, role=role)
        except HTTPException as exc:
            manager_errors.append({"index": index, "group_id": group_id, "user_id": user_id, "error": exc.detail})
            continue
        imported_managers.append({"group_id": manager.group_id, "user_id": manager.user_id, "role": manager.role})

    return {
        "created": created,
        "errors": errors,
        "linkage_errors": linkage_errors,
        "imported_managers": imported_managers,
        "manager_errors": manager_errors,
    }
