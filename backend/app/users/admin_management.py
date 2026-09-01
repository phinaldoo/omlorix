"""Administrative user creation and list serialization helpers."""

import logging
from typing import Any, Dict

from app.users.init import (
    update_user_settings,
)
from app.users.models import (
    create_user,
    query_admin_users_page,
    user_exists_by_email,
)
from app.users.roles import normalize_external_role
from app.users.external_management import (
    is_externally_managed,
)
from app.settings.utils import (
    get_value_by_page_and_key,
)

from app.logging.models import create_authentication_log
from app.groups.models import Group
from app.auth.utils import hash_password

logger = logging.getLogger(__name__)


def _assert_password_policy(*args, **kwargs):
    """Resolve the shared password policy lazily to avoid a façade cycle."""
    from app.users import utils as user_utils

    return user_utils._assert_password_policy(*args, **kwargs)


# -------------------
# Admin: create single user
# -------------------
def create_user_via_admin(user, db, db_log):
    """Create a single user as admin.

    - Validates with `UserCreate` (email and length rules)
    - Skips public signup constraints (IP/domain/signup enabled)
    - First ever user becomes owner; otherwise use a non-admin default role
    """
    # Existence check
    if user_exists_by_email(db, user.email):
        return {"status": "emailAlreadyExists"}

    _assert_password_policy(user.password, db)

    force_password_change = bool(getattr(user, "has_to_change_password", False))

    # Hash password
    hashed_password = hash_password(user.password)

    user_role = normalize_external_role(
        get_value_by_page_and_key("login_general", "default_user_role", db)
    )

    # Determine group_id (admin may supply; otherwise fall back to default)
    try:
        supplied_group_id = getattr(user, "group_id", None)
    except Exception:
        supplied_group_id = None
    default_group_id = get_value_by_page_and_key(
        "login_general", "default_user_group", db
    )
    group_id = supplied_group_id or default_group_id

    user = create_user(
        db,
        user.email,
        hashed_password,
        user.first_name,
        user.last_name,
        user_role,
        group_id,
    )

    if force_password_change:
        update_user_settings(user.id, "security", "has_to_change_password", True, db)

    # Optional: log action
    create_authentication_log(
        db_log, "signup", "info", "Admin created user", user.id, "-", "-"
    )

    return {
        "status": "success",
        "user": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role,
        },
    }


def update_user_location(user_id, location, db):
    update_user_settings(user_id, "general", "location", location, db)
    return {"status": "success"}


def _admin_user_matches_search(user, search: str | None) -> bool:
    """Return whether a user should be included in an admin user search page."""
    if not search:
        return True
    search_lower = search.lower()
    email_match = search_lower in (user.email or "").lower()
    name_match = (
        search_lower in f"{user.first_name or ''} {user.last_name or ''}".lower()
    )
    return email_match or name_match


def _admin_user_summary(user, group_lookup: Dict[str, str]) -> Dict[str, Any]:
    """Serialize the compact user shape shared by admin user tables and pickers."""
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        "group_name": group_lookup.get(user.group_id, user.group_id),
        "is_active": user.is_active,
        "externally_managed": is_externally_managed(user),
        "external_auth_provider": getattr(user, "external_auth_provider", None),
        "created_at": user.created_at,
        "last_active_at": user.last_active_at,
    }


def _group_lookup_for_users(db, users) -> dict[str, str]:
    """Load only groups referenced by the current bounded user page."""
    group_ids = {
        str(user.group_id)
        for user in users
        if str(getattr(user, "group_id", "") or "").strip()
    }
    if not group_ids:
        return {}
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all()
    return {str(group.id): group.name for group in groups}


def get_user_list(db, search: str = None, limit: int = None, offset: int = 0):
    """Return a database-filtered, bounded admin user list."""
    safe_offset = max(int(offset or 0), 0)
    safe_limit = None if limit is None else max(int(limit), 0)
    if safe_limit == 0:
        return []

    users, _total = query_admin_users_page(
        db,
        search=search,
        limit=safe_limit,
        offset=safe_offset,
    )
    group_lookup = _group_lookup_for_users(db, users)
    return [_admin_user_summary(user, group_lookup) for user in users]


def get_user_list_page(
    db, search: str = None, limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    """Return one admin user picker page plus enough metadata for lazy scrolling."""
    safe_offset = max(int(offset or 0), 0)
    safe_limit = max(int(limit or 0), 0)
    page_users, total = query_admin_users_page(
        db,
        search=search,
        limit=safe_limit,
        offset=safe_offset,
    )
    group_lookup = _group_lookup_for_users(db, page_users)

    return {
        "users": [_admin_user_summary(user, group_lookup) for user in page_users],
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": safe_offset + len(page_users) < total,
    }
