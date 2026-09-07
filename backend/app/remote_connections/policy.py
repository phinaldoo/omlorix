"""Group-policy enforcement for user-owned SSH and ACP connections."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.groups.init import get_user_group_setting_value


def get_remote_connection_permissions(db, user_id: str) -> tuple[bool, bool]:
    """Return effective ``(SSH, custom ACP)`` permissions for one user.

    Custom ACP agents always use a user-owned SSH connection. Applying the SSH
    permission as a parent gate here keeps every caller consistent even if a
    group's stored ACP preference remains enabled while SSH is turned off.
    """
    allow_ssh = bool(
        get_user_group_setting_value(
            str(user_id), "tools_mcp", "allow_ssh_connections", db
        )
    )
    allow_custom_acp = allow_ssh and bool(
        get_user_group_setting_value(
            str(user_id), "tools_mcp", "allow_custom_acp_connections", db
        )
    )
    return allow_ssh, allow_custom_acp


def require_ssh_connections(db, user_id: str) -> None:
    """Raise a policy-safe 403 when user-owned SSH connections are disabled."""
    allow_ssh, _allow_custom_acp = get_remote_connection_permissions(db, user_id)
    if not allow_ssh:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSH connections are disabled for your group.",
        )


def require_custom_acp_connections(db, user_id: str) -> None:
    """Raise a 403 unless both SSH and custom ACP connections are enabled."""
    _allow_ssh, allow_custom_acp = get_remote_connection_permissions(db, user_id)
    if not allow_custom_acp:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Custom ACP connections are disabled for your group.",
        )
