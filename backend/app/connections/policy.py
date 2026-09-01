from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.connections.models import (
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_GOOGLE_CALENDAR,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_NOTION,
    PROVIDER_SLACK,
)


FILE_STORAGE_CONNECTION_PROVIDERS = {PROVIDER_GOOGLE_DRIVE}


WORKSPACE_CONNECTION_PROVIDER_OPTIONS: list[dict[str, str]] = [
    {"value": PROVIDER_NOTION, "label": "Notion"},
    {"value": PROVIDER_GITHUB, "label": "GitHub"},
    {"value": PROVIDER_GMAIL, "label": "Gmail"},
    {"value": PROVIDER_GOOGLE_CALENDAR, "label": "Google Calendar"},
    {"value": PROVIDER_GOOGLE_DRIVE, "label": "Google Drive"},
    {"value": PROVIDER_SLACK, "label": "Slack"},
]


def normalize_enabled_connections(value: Any) -> list[str]:
    """Normalize stored connection-provider settings to a deduplicated allow-list.

    Accepts a single provider string or an iterable of providers. Unknown,
    blank, and duplicate values are discarded. Invalid input types normalize to
    an empty list.
    """
    if value is None:
        return []

    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []

    allowed_values = {str(entry["value"] or "").strip().lower() for entry in WORKSPACE_CONNECTION_PROVIDER_OPTIONS}
    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw_items:
        provider = str(item or "").strip().lower()
        if not provider or provider not in allowed_values or provider in seen:
            continue
        normalized.append(provider)
        seen.add(provider)
    return normalized


def group_enabled_connections(user_id: str, db) -> list[str]:
    from app.groups.init import get_user_group_setting_value

    value = get_user_group_setting_value(user_id, "tools_mcp", "enabled_connections", db)
    return normalize_enabled_connections(value)


def group_has_enabled_workspace_connections(user_id: str, db) -> bool:
    """Return whether the effective group policy permits a managed provider.

    This is the browser-bootstrap capability for the managed Workspace
    Connections catalog. It deliberately does not consult ``enable_mcp``, which
    authorizes personal user-created MCP endpoints. Storage providers count only
    when the group's additional storage opt-in is enabled.
    """
    from app.groups.init import get_user_group_setting_value

    enabled = group_enabled_connections(user_id, db)
    if any(provider not in FILE_STORAGE_CONNECTION_PROVIDERS for provider in enabled):
        return True
    if not any(provider in FILE_STORAGE_CONNECTION_PROVIDERS for provider in enabled):
        return False
    return bool(
        get_user_group_setting_value(
            user_id,
            "tools_mcp",
            "allow_file_storage_connections",
            db,
        )
    )


def group_allows_connection_provider(user_id: str, db, *, provider: str) -> bool:
    from app.groups.init import get_user_group_setting_value

    normalized = str(provider or "").strip().lower()
    enabled = group_enabled_connections(user_id, db)
    if not enabled:
        return False
    if normalized not in enabled:
        return False
    if normalized in FILE_STORAGE_CONNECTION_PROVIDERS:
        allow_files = bool(get_user_group_setting_value(user_id, "tools_mcp", "allow_file_storage_connections", db))
        return bool(allow_files)
    return True


def ensure_group_allows_connection_provider(user_id: str, db, *, provider: str) -> None:
    """Enforce the connection-specific provider policy.

    The personal MCP toggle is intentionally not consulted here. Workspace
    connections are controlled independently by ``enabled_connections`` and,
    for storage providers, the file-storage opt-in.
    """
    if not group_allows_connection_provider(user_id, db, provider=provider):
        raise HTTPException(status_code=403, detail="Connection provider is not enabled for your group.")
