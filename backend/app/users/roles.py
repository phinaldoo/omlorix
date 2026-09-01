"""Canonical Omlorix account-role definitions and authorization helpers.

Keeping role semantics in one module prevents individual features from
accidentally treating the owner as a normal user or inventing a second path
that can grant administrative authority.
"""

from __future__ import annotations

from typing import Any


OWNER_ROLE = "owner"
ADMIN_ROLE = "admin"
USER_ROLE = "user"
PENDING_ROLE = "pending"

# The owner can use every endpoint and feature that is available to admins.
ADMINISTRATIVE_ROLES = frozenset({OWNER_ROLE, ADMIN_ROLE})

# The owner role is bootstrapped for the first account and is intentionally
# absent here. It must never be assigned through ordinary account-management,
# import, directory-sync, SSO, or SCIM inputs.
ASSIGNABLE_ROLES = frozenset({ADMIN_ROLE, USER_ROLE, PENDING_ROLE})
EXTERNALLY_ASSIGNABLE_ROLES = frozenset({USER_ROLE, PENDING_ROLE})


def normalize_role(role: Any) -> str:
    """Return a normalized account-role value suitable for comparisons."""

    return str(role or "").strip().lower()


def is_owner_role(role: Any) -> bool:
    """Return whether ``role`` is the protected instance-owner role."""

    return normalize_role(role) == OWNER_ROLE


def is_admin_role(role: Any) -> bool:
    """Return whether ``role`` has access to administrative capabilities."""

    return normalize_role(role) in ADMINISTRATIVE_ROLES


def normalize_external_role(role: Any, *, default: str = USER_ROLE) -> str:
    """Restrict untrusted or federated role input to non-administrative roles.

    Administrative authority is granted only by the owner through Omlorix's
    audited role-change endpoint. Existing configuration that still contains
    ``admin`` therefore degrades safely to the requested non-privileged
    default instead of silently preserving an authorization bypass.
    """

    normalized = normalize_role(role)
    if normalized in EXTERNALLY_ASSIGNABLE_ROLES:
        return normalized
    normalized_default = normalize_role(default)
    if normalized_default in EXTERNALLY_ASSIGNABLE_ROLES:
        return normalized_default
    return USER_ROLE
