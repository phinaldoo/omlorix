from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
import re
import secrets
import string
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.session_store import revoke_user_sessions
from app.auth.utils import hash_password
from app.database import AuditSessionLocal
from app.groups.init import get_group_settings
from app.groups.models import Group, GroupManager, add_group_manager, get_group, get_group_manager, list_groups
from app.groups.sensitive import filter_settings_for_response
from app.logging.models import create_audit_log
from app.admin.groups.models import update_group_values
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User, build_user_email_match, create_user, get_user, normalize_utc_datetime
from app.users.roles import is_admin_role


logger = logging.getLogger(__name__)


ROLE_CAPABILITIES: dict[str, set[str]] = {
    "owner": {"view_group", "view_members", "manage_settings", "promote_members", "manage_temporary_accounts"},
    "manager": {"view_group", "view_members", "manage_settings", "manage_temporary_accounts"},
    # Coordinators can manage temporary access without changing the group's
    # policy or delegating management to other users.
    "coordinator": {"view_group", "view_members", "manage_temporary_accounts"},
}

ROLE_PRIORITY = {"coordinator": 1, "manager": 2, "owner": 3}

MANAGER_EDITABLE_RULES: dict[str, dict[str, Any]] = {
    "context.enable_group_context": {"mode": "free"},
    "context.group_context": {"mode": "free"},
    "chat.show_chat_box_warning": {"mode": "free"},
    "chat.chat_box_warning_message": {"mode": "free"},
    "chat.allow_temporary_chat": {"mode": "free"},
    "files.allow_file_uploads": {"mode": "free"},
    "files.max_files_upload_count": {"mode": "free"},
    "files.max_user_files_size_gb": {"mode": "free"},
    "temporary_accounts.enabled": {"mode": "free"},
    "temporary_accounts.max_active_accounts": {"mode": "free"},
    "temporary_accounts.credential_length": {"mode": "free"},
    # Managers edit the selected group's own policy. Parent groups define
    # hierarchy and management reach, not a policy ceiling.
    "projects.enable_projects": {"mode": "free"},
    "projects.allow_project_share": {"mode": "free"},
    "todo.enabled_todo": {"mode": "free"},
    "todo.allow_todo_list_share": {"mode": "free"},
    "notes.enabled_notes": {"mode": "free"},
    "notes.allow_notes_share": {"mode": "free"},
    "memories.enabled_memories": {"mode": "free"},
    "skills.enabled_skills": {"mode": "free"},
    "skills.allow_skill_share": {"mode": "free"},
    "prompts.enabled_prompts": {"mode": "free"},
    "prompts.allow_prompt_share": {"mode": "free"},
    "bookmarks.enabled_bookmarks": {"mode": "free"},
    "bookmarks.allow_bookmark_share": {"mode": "free"},
    "agents.allow_agents": {"mode": "free"},
    "agents.allow_agent_share": {"mode": "free"},
    "automations.enabled_automations": {"mode": "free"},
    "sharing.enable_chat_sharing": {"mode": "free"},
    "sharing.enable_artifact_sharing": {"mode": "free"},
}

MAX_TEMP_ACCOUNTS = 100
MAX_TEMP_EXPIRY_HOURS = 24 * 30
MAX_GROUP_DETAIL_ITEMS = 500
DEFAULT_GROUP_DETAIL_PAGE_SIZE = 100
_GROUP_AUDIT_ACTOR_MAX_LENGTH = 64
_GROUP_AUDIT_ACTION_MAX_LENGTH = 128
_GROUP_AUDIT_TARGET_MAX_LENGTH = 128


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_group_audit_value(value: Any, max_length: int) -> str | None:
    normalized = " ".join(str(value or "").split())
    return normalized[:max_length] or None


def _audit_group_capability_denial(
    user: User,
    group_id: str,
    capability: str,
    *,
    attempted_action: str | None = None,
    target_user_id: str | None = None,
    account_user_id: str | None = None,
) -> None:
    """Record one bounded denial event without changing the 403 response."""

    details = {
        "reason": "missing_group_capability",
        "attempted_action": _bounded_group_audit_value(
            attempted_action or capability,
            _GROUP_AUDIT_ACTION_MAX_LENGTH,
        ),
        "required_capability": _bounded_group_audit_value(
            capability,
            _GROUP_AUDIT_ACTION_MAX_LENGTH,
        ),
        "group_id": _bounded_group_audit_value(
            group_id,
            _GROUP_AUDIT_TARGET_MAX_LENGTH,
        ),
    }
    if target_user_id:
        details["target_user_id"] = _bounded_group_audit_value(
            target_user_id,
            _GROUP_AUDIT_TARGET_MAX_LENGTH,
        )
    if account_user_id:
        details["account_user_id"] = _bounded_group_audit_value(
            account_user_id,
            _GROUP_AUDIT_TARGET_MAX_LENGTH,
        )

    try:
        audit_db = AuditSessionLocal()
        try:
            create_audit_log(
                db_log=audit_db,
                user_id=_bounded_group_audit_value(
                    getattr(user, "id", None),
                    _GROUP_AUDIT_ACTOR_MAX_LENGTH,
                ),
                action="GROUP_MANAGEMENT_ACCESS_DENIED",
                reason="missing_group_capability",
                details=details,
                category="group",
            )
        finally:
            audit_db.close()
    except Exception:
        logger.exception("Failed to record denied delegated group-management access")


def _serialize_user(user: User) -> dict[str, Any]:
    status_label = "active"
    if user.deleted_at is not None:
        status_label = "deleted"
    elif not user.is_active:
        status_label = "inactive"
    elif user.role == "pending":
        status_label = "pending"
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "group_id": user.group_id,
        "role": user.role,
        "account_type": getattr(user, "account_type", "regular"),
        "temporary_expires_at": normalize_utc_datetime(getattr(user, "temporary_expires_at", None)),
        "deleted_at": normalize_utc_datetime(getattr(user, "deleted_at", None)),
        "deletion_scheduled_for": normalize_utc_datetime(
            getattr(user, "deletion_scheduled_for", None)
        ),
        "is_active": bool(user.is_active),
        "status": status_label,
    }


def _serialize_manager_entry(db: Session, manager: GroupManager) -> dict[str, Any]:
    user = get_user(db, manager.user_id, None)
    if user is None:
        return {
            "user": None,
            "role": manager.role,
            "capabilities": sorted(ROLE_CAPABILITIES.get(manager.role, set())),
        }
    return {
        "user": _serialize_user(user),
        "role": manager.role,
        "capabilities": sorted(ROLE_CAPABILITIES.get(manager.role, set())),
    }


def _manager_entries(db: Session, group_id: str, *, offset: int, limit: int) -> list[dict[str, Any]]:
    """Serialize manager assignments and users with one joined query."""

    rows = (
        db.query(GroupManager, User)
        .outerjoin(User, User.id == GroupManager.user_id)
        .filter(GroupManager.group_id == group_id)
        .order_by(func.lower(GroupManager.role).asc(), GroupManager.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "user": _serialize_user(manager_user) if manager_user else None,
            "role": manager.role,
            "capabilities": sorted(ROLE_CAPABILITIES.get(manager.role, set())),
        }
        for manager, manager_user in rows
    ]


def _group_path(group: Group, db: Session) -> list[str]:
    path: list[str] = []
    seen: set[str] = set()
    cursor = group
    while cursor:
        if cursor.id in seen:
            break
        seen.add(cursor.id)
        path.append(cursor.name)
        if not cursor.parent_id:
            break
        cursor = get_group(db, cursor.parent_id)
        if not cursor:
            break
    return list(reversed(path))


def _is_descendant_or_same(db: Session, descendant_group_id: str, ancestor_group_id: str) -> bool:
    if descendant_group_id == ancestor_group_id:
        return True
    seen: set[str] = set()
    cursor = get_group(db, descendant_group_id)
    while cursor and cursor.parent_id:
        if cursor.id in seen:
            break
        seen.add(cursor.id)
        if cursor.parent_id == ancestor_group_id:
            return True
        cursor = get_group(db, cursor.parent_id)
    return False


def _management_entry_for_user(
    db: Session,
    user: User,
    group_id: str,
    capability: str | None = None,
) -> dict[str, Any] | None:
    """Return deterministic effective delegation for a group.

    A user may have assignments on both an ancestor and a descendant.  The
    broadest assignment that grants the requested capability must win so the
    result never depends on which row happened to be created first.
    """
    if is_admin_role(user.role):
        return {
            "role": "owner",
            "capabilities": sorted(ROLE_CAPABILITIES["owner"]),
            "source_group_id": group_id,
        }

    entries = (
        db.query(GroupManager)
        .filter(GroupManager.user_id == user.id)
        .order_by(GroupManager.created_at.asc())
        .all()
    )
    matching: list[GroupManager] = []
    for entry in entries:
        if not _is_descendant_or_same(db, group_id, entry.group_id):
            continue
        if capability and capability not in ROLE_CAPABILITIES.get(entry.role, set()):
            continue
        matching.append(entry)

    if not matching:
        return None

    # A matching ancestor is always broader than another matching assignment.
    # The stable id fallback also makes malformed/cyclic hierarchies predictable.
    broadest = matching[0]
    for candidate in matching[1:]:
        if _is_descendant_or_same(db, broadest.group_id, candidate.group_id):
            broadest = candidate
        elif not _is_descendant_or_same(db, candidate.group_id, broadest.group_id):
            broadest = min((broadest, candidate), key=lambda item: str(item.id))

    effective_role = max(matching, key=lambda item: ROLE_PRIORITY.get(item.role, 0)).role
    effective_capabilities = set().union(
        *(ROLE_CAPABILITIES.get(item.role, set()) for item in matching)
    )
    return {
        "role": effective_role,
        "capabilities": sorted(effective_capabilities),
        "source_group_id": broadest.group_id,
    }


def require_group_capability(
    db: Session,
    user: User,
    group_id: str,
    capability: str,
    *,
    attempted_action: str | None = None,
    target_user_id: str | None = None,
    account_user_id: str | None = None,
) -> dict[str, Any]:
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    entry = _management_entry_for_user(db, user, group_id, capability)
    if not entry or capability not in set(entry["capabilities"]):
        _audit_group_capability_denial(
            user,
            group_id,
            capability,
            attempted_action=attempted_action,
            target_user_id=target_user_id,
            account_user_id=account_user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to manage this group")
    return entry


def _ensure_user_within_management_scope(
    db: Session,
    *,
    acting_user: User,
    management_entry: dict[str, Any],
    target_user: User,
) -> None:
    if is_admin_role(acting_user.role):
        return

    source_group_id = str(management_entry.get("source_group_id") or "").strip()
    if not source_group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delegated managers cannot manage users outside their assigned scope",
        )

    if not _is_descendant_or_same(db, target_user.group_id, source_group_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage users who already belong to your managed group scope",
        )


def managed_groups_for_user(db: Session, user: User) -> list[dict[str, Any]]:
    """List manageable groups with aggregate counts in a bounded query set."""

    groups = list_groups(db)
    group_ids = [group.id for group in groups]
    groups_by_id = {group.id: group for group in groups}
    assignments = [] if is_admin_role(user.role) else (
        db.query(GroupManager)
        .filter(GroupManager.user_id == user.id)
        .order_by(GroupManager.created_at.asc())
        .all()
    )

    def is_descendant_in_memory(descendant_id: str, ancestor_id: str) -> bool:
        """Resolve hierarchy containment without issuing per-group queries."""

        seen: set[str] = set()
        cursor_id: str | None = descendant_id
        while cursor_id and cursor_id not in seen:
            if cursor_id == ancestor_id:
                return True
            seen.add(cursor_id)
            cursor = groups_by_id.get(cursor_id)
            cursor_id = cursor.parent_id if cursor else None
        return False

    path_cache: dict[str, list[str]] = {}

    def path_for(group_id: str, seen: set[str] | None = None) -> list[str]:
        """Build a path with memoization and cycle protection."""

        if group_id in path_cache:
            return path_cache[group_id]
        current_seen = set(seen or set())
        if group_id in current_seen:
            return []
        current_seen.add(group_id)
        group = groups_by_id.get(group_id)
        if not group:
            return []
        parent_path = path_for(group.parent_id, current_seen) if group.parent_id else []
        path_cache[group_id] = [*parent_path, group.name]
        return path_cache[group_id]

    def entry_for(group_id: str) -> dict[str, Any] | None:
        """Compute effective role and capabilities from the prefetched rows."""

        if is_admin_role(user.role):
            return {"role": "owner", "capabilities": sorted(ROLE_CAPABILITIES["owner"])}
        matching = [
            assignment
            for assignment in assignments
            if is_descendant_in_memory(group_id, assignment.group_id)
        ]
        if not matching:
            return None
        return {
            "role": max(matching, key=lambda item: ROLE_PRIORITY.get(item.role, 0)).role,
            "capabilities": sorted(set().union(
                *(ROLE_CAPABILITIES.get(item.role, set()) for item in matching)
            )),
        }

    now = _now()
    temporary_counts = dict(
        db.query(User.group_id, func.count(User.id))
        .filter(
            User.group_id.in_(group_ids),
            User.account_type == "temporary",
            User.is_active.is_(True),
            ((User.temporary_expires_at.is_(None)) | (User.temporary_expires_at > now)),
        )
        .group_by(User.group_id)
        .all()
    ) if group_ids else {}
    direct_member_counts = dict(
        db.query(User.group_id, func.count(User.id))
        .filter(
            User.group_id.in_(group_ids),
            User.account_type == "regular",
        )
        .group_by(User.group_id)
        .all()
    ) if group_ids else {}
    result: list[dict[str, Any]] = []
    for group in groups:
        entry = entry_for(group.id)
        if not entry:
            continue
        result.append(
            {
                "id": group.id,
                "name": group.name,
                "path": path_for(group.id),
                "role": entry["role"],
                "capabilities": entry["capabilities"],
                "direct_member_count": int(direct_member_counts.get(group.id, 0)),
                "temporary_account_count": int(temporary_counts.get(group.id, 0)),
            }
        )
    return result


def has_managed_groups_for_user(db: Session, user: User) -> bool:
    """Return whether the user-settings navigation should expose group management.

    The chat bootstrap only needs an availability flag, so avoid building the
    complete managed-group payload (including hierarchy paths and temporary
    account counts) before the user actually opens that settings page.
    """

    if is_admin_role(getattr(user, "role", "user")):
        return db.query(Group.id).first() is not None

    return (
        db.query(GroupManager.id)
        # Joining the group keeps this flag consistent with
        # ``managed_groups_for_user`` if an invalid legacy assignment exists.
        .join(Group, Group.id == GroupManager.group_id)
        .filter(GroupManager.user_id == user.id)
        .first()
        is not None
    )


def _count_active_temporary_accounts(db: Session, group_id: str) -> int:
    now = _now()
    return (
        db.query(User.id)
        .filter(
            User.group_id == group_id,
            User.account_type == "temporary",
            User.is_active.is_(True),
            ((User.temporary_expires_at.is_(None)) | (User.temporary_expires_at > now)),
        )
        .count()
    )


def _direct_regular_members(
    db: Session,
    group_id: str,
    *,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a bounded, read-only roster page for a managed group."""

    members = (
        db.query(User)
        .filter(User.group_id == group_id, User.account_type == "regular")
        # Names are encrypted at rest and therefore cannot be meaningfully
        # sorted by the database. Email is canonical plaintext and gives every
        # page a stable order without decrypting an unbounded result set.
        .order_by(User.email.asc(), User.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_user(member) for member in members]


def _temporary_accounts(db: Session, group_id: str, *, offset: int, limit: int) -> list[dict[str, Any]]:
    now = _now()
    accounts = (
        db.query(User)
        .filter(User.group_id == group_id, User.account_type == "temporary")
        .order_by(User.created_at.desc(), User.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    result: list[dict[str, Any]] = []
    for account in accounts:
        expires_at = normalize_utc_datetime(getattr(account, "temporary_expires_at", None))
        status_label = "active"
        # Revocation is an explicit manager action and therefore takes
        # precedence over time-based expiry in the management UI.
        if not account.is_active:
            status_label = "revoked"
        elif expires_at and expires_at <= now:
            status_label = "expired"
        result.append(
            {
                **_serialize_user(account),
                "status": status_label,
            }
        )
    return result


def managed_group_details(
    db: Session,
    user: User,
    group_id: str,
    *,
    manager_offset: int = 0,
    member_offset: int = 0,
    temporary_offset: int = 0,
    limit: int = DEFAULT_GROUP_DETAIL_PAGE_SIZE,
) -> dict[str, Any]:
    """Return bounded manager, member, and temporary-account details."""

    require_group_capability(
        db,
        user,
        group_id,
        "view_group",
        attempted_action="view_group_details",
    )
    entry = _management_entry_for_user(db, user, group_id)
    assert entry is not None
    group = get_group(db, group_id)
    assert group is not None
    editable_setting_paths = MANAGER_EDITABLE_RULES.keys()
    bounded_limit = max(1, min(int(limit), MAX_GROUP_DETAIL_ITEMS))
    offsets = {
        "managers": max(0, int(manager_offset)),
        "members": max(0, int(member_offset)),
        "temporary_accounts": max(0, int(temporary_offset)),
    }
    totals = {
        "managers": db.query(GroupManager.id).filter(GroupManager.group_id == group.id).count(),
        "members": db.query(User.id).filter(
            User.group_id == group.id,
            User.account_type == "regular",
        ).count(),
        "temporary_accounts": db.query(User.id).filter(User.group_id == group.id, User.account_type == "temporary").count(),
    }
    return {
        "group": {
            "id": group.id,
            "name": group.name,
            "parent_id": group.parent_id,
            "path": _group_path(group, db),
            "role": entry["role"],
            "capabilities": entry["capabilities"],
        },
        "settings": filter_settings_for_response(
            get_group_settings(group.id, db),
            editable_setting_paths,
        ),
        "editable_rules": MANAGER_EDITABLE_RULES,
        "managers": _manager_entries(db, group.id, offset=offsets["managers"], limit=bounded_limit),
        "members": _direct_regular_members(
            db,
            group.id,
            offset=offsets["members"],
            limit=bounded_limit,
        ),
        "temporary_accounts": _temporary_accounts(
            db,
            group.id,
            offset=offsets["temporary_accounts"],
            limit=bounded_limit,
        ),
        "pagination": {
            key: {
                "offset": offsets[key],
                "limit": bounded_limit,
                "total": totals[key],
                "has_more": offsets[key] + bounded_limit < totals[key],
            }
            for key in totals
        },
    }


def _ensure_manager_setting_is_editable(page_name: str, key_name: str) -> None:
    """Reject fields outside the deliberately limited delegated settings set."""

    if f"{page_name}.{key_name}" not in MANAGER_EDITABLE_RULES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Managers cannot modify {page_name}.{key_name}")


def update_managed_group_settings(
    db: Session,
    acting_user: User,
    group_id: str,
    *,
    settings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    require_group_capability(
        db,
        acting_user,
        group_id,
        "manage_settings",
        attempted_action="update_group_settings",
    )
    if settings is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")

    if isinstance(settings, dict):
        for page_name, page_values in settings.items():
            if not isinstance(page_values, dict):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{page_name} must be an object")
            for key_name in page_values:
                _ensure_manager_setting_is_editable(page_name, key_name)
    return update_group_values(
        group_id,
        name=None,
        settings=settings,
        db=db,
    )


def promote_group_member(db: Session, acting_user: User, group_id: str, user_id: str, role: str) -> dict[str, Any]:
    """Promote one direct member without permitting lateral or downward changes."""

    result, _audit_context = promote_group_member_with_audit_context(
        db,
        acting_user,
        group_id,
        user_id,
        role,
    )
    return result


def list_group_promotion_candidates(
    db: Session,
    acting_user: User,
    group_id: str,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return one bounded page of direct members eligible for role promotion."""

    require_group_capability(
        db,
        acting_user,
        group_id,
        "promote_members",
        attempted_action="list_manager_candidates",
    )
    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(int(limit), MAX_GROUP_DETAIL_ITEMS))
    base_query = db.query(User).filter(
        User.group_id == group_id,
        User.account_type == "regular",
    )
    total = base_query.count()
    users = (
        # Use the same stable ordering as the read-only Members tab. Ordering
        # by encrypted name columns would effectively sort ciphertext.
        base_query.order_by(User.email.asc(), User.id.asc())
        .offset(safe_offset)
        .limit(safe_limit)
        .all()
    )
    user_ids = [member.id for member in users]
    roles_by_user_id = {
        manager.user_id: manager.role
        for manager in (
            db.query(GroupManager)
            .filter(
                GroupManager.group_id == group_id,
                GroupManager.user_id.in_(user_ids),
            )
            .all()
            if user_ids
            else []
        )
    }
    items: list[dict[str, Any]] = []
    for member in users:
        serialized = _serialize_user(member)
        current_role = roles_by_user_id.get(member.id)
        items.append(
            {
                "id": member.id,
                "email": member.email,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "status": serialized["status"],
                "current_role": current_role,
                "eligible": bool(
                    member.is_active
                    and member.deleted_at is None
                    and member.role != "pending"
                    and ROLE_PRIORITY.get(current_role or "", 0) < ROLE_PRIORITY["owner"]
                ),
            }
        )
    return {
        "items": items,
        "offset": safe_offset,
        "limit": safe_limit,
        "total": total,
        "has_more": safe_offset + safe_limit < total,
    }


def promote_group_member_with_audit_context(
    db: Session,
    acting_user: User,
    group_id: str,
    user_id: str,
    role: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote a direct member and return the corresponding audit context."""

    entry = require_group_capability(
        db,
        acting_user,
        group_id,
        "promote_members",
        attempted_action="promote_group_member",
        target_user_id=user_id,
    )
    normalized_role = str(role or "coordinator").strip().lower()
    if normalized_role not in ROLE_CAPABILITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid manager role")
    target_user = get_user(db, user_id=str(user_id))
    if target_user.group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only direct members of this group can be promoted",
        )
    if (
        target_user.account_type != "regular"
        or not target_user.is_active
        or target_user.deleted_at is not None
        or target_user.role == "pending"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active, approved, permanent users can manage groups",
        )
    existing_manager = get_group_manager(db, group_id, target_user.id)
    old_role = existing_manager.role if existing_manager else None
    if ROLE_PRIORITY[normalized_role] <= ROLE_PRIORITY.get(old_role or "", 0):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Members can only be promoted to a higher group role",
        )
    manager = add_group_manager(db, group_id=group_id, user_id=target_user.id, role=normalized_role)
    result = _serialize_manager_entry(db, manager)
    audit_context = {
        "action": "PROMOTE_GROUP_MANAGER_ROLE" if existing_manager else "PROMOTE_GROUP_MEMBER",
        "details": {
            "group_id": group_id,
            "target_user": target_user.id,
            "manager_user_id": target_user.id,
            "scope_group_id": entry.get("source_group_id") or group_id,
            "scope_role": entry.get("role"),
            "role": manager.role,
            "old_role": old_role,
            "new_role": manager.role,
        },
    }
    return result, audit_context


def _sanitize_account_fragment(value: str) -> str:
    """Create a short ASCII email fragment from a group display name."""

    cleaned = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    # Keeping the group portion short materially reduces what users need to
    # type while retaining enough of the group name to recognize the account.
    return (cleaned or "group")[:24]


def _generate_secret(length: int, allowed_chars: str = string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(allowed_chars) for _ in range(max(16, min(int(length), 64))))


def _generate_unique_temporary_email(db: Session, group_name: str) -> str:
    """Generate ``group.ab12@temporary.local`` with collision retries.

    The 32-character alphabet yields more than one million four-character
    combinations. Ambiguous characters are excluded because people type these
    addresses manually.
    """

    base = _sanitize_account_fragment(group_name)
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    for _attempt in range(1000):
        short_code = "".join(secrets.choice(alphabet) for _ in range(4))
        email = f"{base}.{short_code}@temporary.local"
        existing = db.query(User.id).filter(build_user_email_match(email)).first()
        if not existing:
            return email
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not generate a unique temporary account email",
    )


def create_temporary_accounts(
    db: Session,
    acting_user: User,
    group_id: str,
    *,
    count: int,
    expiry_hours: int | None,
) -> dict[str, Any]:
    require_group_capability(
        db,
        acting_user,
        group_id,
        "manage_temporary_accounts",
        attempted_action="create_temporary_accounts",
    )
    # PostgreSQL row locking serializes limit checks for a group. SQLite does
    # not implement row-level FOR UPDATE, so tests and lightweight local setups
    # continue without the lock while production PostgreSQL remains safe.
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.query(Group.id).filter(Group.id == group_id).with_for_update().one()
    group_settings = get_group_settings(group_id, db)
    temp_settings = group_settings.get("temporary_accounts", {})
    if not bool(temp_settings.get("enabled")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Temporary accounts are disabled for this group")

    try:
        requested_count = int(count)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Count must be a valid integer")
    if requested_count < 1 or requested_count > MAX_TEMP_ACCOUNTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Count must be between 1 and {MAX_TEMP_ACCOUNTS}")

    active_count = _count_active_temporary_accounts(db, group_id)
    max_active = int(temp_settings.get("max_active_accounts") or 50)
    if active_count + requested_count > max_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Creating these accounts would exceed the group's temporary-account limit",
        )

    # Eight hours is a creation-form default, not group policy. Managers may
    # choose any duration accepted by the API for each generated batch.
    expiry_value = expiry_hours if expiry_hours not in (None, "") else 8
    try:
        expiry_delta_hours = int(expiry_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expiry hours must be a valid integer")
    if expiry_delta_hours < 1 or expiry_delta_hours > MAX_TEMP_EXPIRY_HOURS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expiry hours must be between 1 and {MAX_TEMP_EXPIRY_HOURS}",
        )
    credential_length = max(16, int(temp_settings.get("credential_length") or 24))
    group = get_group(db, group_id)
    group_name = str(group.name or "Group").strip()
    expires_at = _now() + timedelta(hours=expiry_delta_hours)

    created_credentials: list[dict[str, Any]] = []
    try:
        for _index in range(requested_count):
            synthetic_email = _generate_unique_temporary_email(db, group_name)
            secret = _generate_secret(credential_length)
            short_code = synthetic_email.partition("@")[0].rsplit(".", 1)[-1].upper()
            display_name = f"{group_name} {short_code}"

            user = create_user(
                db,
                synthetic_email,
                hash_password(secret),
                display_name,
                "",
                "user",
                group_id,
                account_type="temporary",
                temporary_expires_at=expires_at,
                provisioned_by_user_id=acting_user.id,
                commit=False,
                refresh=False,
            )
            settings = deepcopy(user.settings) if isinstance(user.settings, dict) else deepcopy(DEFAULT_USER_SETTINGS)
            user.settings = settings
            created_credentials.append(
                {
                    "id": user.id,
                    "email": synthetic_email,
                    "password": secret,
                    "display_name": display_name,
                    "expires_at": expires_at,
                }
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "created": created_credentials,
        "expires_at": expires_at,
    }


def revoke_temporary_account(db: Session, acting_user: User, account_user_id: str) -> dict[str, Any]:
    account = get_user(db, account_user_id)
    if account.account_type != "temporary":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a temporary account")
    entry = require_group_capability(
        db,
        acting_user,
        account.group_id,
        "manage_temporary_accounts",
        attempted_action="revoke_temporary_account",
        account_user_id=account.id,
    )
    _ensure_user_within_management_scope(db, acting_user=acting_user, management_entry=entry, target_user=account)
    from app.auth.models import delete_authentication_all
    from app.groups.temporary_account_retention import mark_temporary_account_for_retention

    lifecycle_at = _now()
    expires_at = normalize_utc_datetime(account.temporary_expires_at)
    if expires_at is not None and expires_at < lifecycle_at:
        # Revoking an already-expired account must not restart its retention
        # window. This also keeps the endpoint and expiry worker idempotent if
        # they observe the same account concurrently.
        lifecycle_at = expires_at
    try:
        policy = mark_temporary_account_for_retention(
            account,
            db,
            lifecycle_at=lifecycle_at,
        )
        account.is_active = False
        # Remove persisted sessions in the same database transaction. Cache
        # revocation happens only after commit so a rollback cannot leave the
        # database and session cache disagreeing about the account state.
        delete_authentication_all(
            db,
            account.id,
            commit=False,
            revoke_cached=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    revoke_user_sessions(account.id)
    return {
        "status": "revoked",
        "user_id": account.id,
        "group_id": account.group_id,
        "retention_mode": policy["mode"],
        "deletion_scheduled_for": policy["purge_scheduled_at"],
    }
