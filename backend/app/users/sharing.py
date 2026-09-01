from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.groups.models import get_group, get_group_children
from app.users.models import User


DEFAULT_PUBLIC_USER_DISCOVERY_LIMIT = 50
MAX_PUBLIC_USER_DISCOVERY_LIMIT = 100
_PUBLIC_USER_SEARCH_MAX_LENGTH = 100


def _build_public_user_display_name(user: User) -> str:
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    if user.first_name:
        return str(user.first_name)
    if user.last_name:
        return str(user.last_name)
    return "Unknown"


def _normalize_search_query(value: str | None) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > _PUBLIC_USER_SEARCH_MAX_LENGTH:
        normalized = normalized[:_PUBLIC_USER_SEARCH_MAX_LENGTH]
    return normalized.lower()


def _is_user_publicly_discoverable(user: User, requesting_user_id: str) -> bool:
    if user.id == requesting_user_id:
        return False
    if not user.is_active or user.role == "pending" or user.deleted_at is not None:
        return False

    settings = user.settings or {}
    security_settings = settings.get("security", {})
    profile_visibility = security_settings.get("profile_visibility", "private")
    return profile_visibility == "public"


def get_allowed_public_user_group_ids(db: Session, requesting_user: User) -> set[str]:
    """Return groups in the requester's lineage that may be used for sharing discovery."""
    requesting_group_id = str(getattr(requesting_user, "group_id", "") or "").strip()
    if not requesting_group_id:
        return set()

    allowed_group_ids = {requesting_group_id}

    seen_ancestors = {requesting_group_id}
    cursor = get_group(db, requesting_group_id)
    while cursor and cursor.parent_id:
        parent_id = str(cursor.parent_id or "").strip()
        if not parent_id or parent_id in seen_ancestors:
            break
        seen_ancestors.add(parent_id)
        allowed_group_ids.add(parent_id)
        cursor = get_group(db, parent_id)

    pending_group_ids = [requesting_group_id]
    seen_descendants = {requesting_group_id}
    while pending_group_ids:
        group_id = pending_group_ids.pop()
        for child_group in get_group_children(db, group_id):
            child_group_id = str(getattr(child_group, "id", "") or "").strip()
            if not child_group_id or child_group_id in seen_descendants:
                continue
            seen_descendants.add(child_group_id)
            allowed_group_ids.add(child_group_id)
            pending_group_ids.append(child_group_id)

    return allowed_group_ids


def _public_user_candidate_query(db: Session, requesting_user: User):
    allowed_group_ids = get_allowed_public_user_group_ids(db, requesting_user)
    return (
        db.query(User)
        .filter(
            User.group_id.in_(sorted(allowed_group_ids)),
            User.id != requesting_user.id,
            User.is_active == True,
            User.role != "pending",
            User.deleted_at.is_(None),
        )
    )


def get_public_users_for_sharing(
    db: Session,
    requesting_user: User,
    *,
    q: str | None = None,
    limit: int = DEFAULT_PUBLIC_USER_DISCOVERY_LIMIT,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    """Return publicly discoverable users for sharing UIs within the requester's allowed scope."""
    normalized_query = _normalize_search_query(q)
    candidate_rows = _public_user_candidate_query(db, requesting_user).order_by(
        User.last_active_at.desc(),
        User.email.asc(),
    ).all()
    matching_rows = []
    for user in candidate_rows:
        if not _is_user_publicly_discoverable(user, requesting_user.id):
            continue
        if normalized_query:
            haystack = _build_public_user_display_name(user).lower()
            if normalized_query not in haystack:
                continue
        matching_rows.append(user)

    total = len(matching_rows)
    paged_rows = matching_rows[offset:offset + limit]

    public_users = [
        {
            "id": user.id,
            "display_name": _build_public_user_display_name(user),
        }
        for user in paged_rows
    ]
    meta = {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(public_users) < total,
    }
    return public_users, meta


def resolve_invitable_users_for_sharing(
    db: Session,
    requesting_user: User,
    requested_user_ids: Sequence[str] | None,
) -> list[User]:
    """Resolve invited users while enforcing the same discovery scope server-side."""
    normalized_user_ids: list[str] = []
    seen_user_ids: set[str] = set()
    for raw_user_id in requested_user_ids or []:
        invited_user_id = str(raw_user_id or "").strip()
        if not invited_user_id or invited_user_id == requesting_user.id or invited_user_id in seen_user_ids:
            continue
        seen_user_ids.add(invited_user_id)
        normalized_user_ids.append(invited_user_id)

    if not normalized_user_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one user to invite")

    invited_users = _public_user_candidate_query(db, requesting_user).filter(User.id.in_(normalized_user_ids)).all()
    invited_users_by_id = {
        invited_user.id: invited_user
        for invited_user in invited_users
        if _is_user_publicly_discoverable(invited_user, requesting_user.id)
    }
    resolved_users = [invited_users_by_id[user_id] for user_id in normalized_user_ids if user_id in invited_users_by_id]

    if not resolved_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more selected users are no longer available to invite",
        )

    return resolved_users
