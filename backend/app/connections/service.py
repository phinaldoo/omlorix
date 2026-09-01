from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.connections.errors import ConnectionRefreshReauthRequiredError, ConnectionRefreshRetryableError
from app.connections.github import (
    GITHUB_MCP_SERVER_URL,
    build_github_oauth_revocation_url,
    complete_github_oauth,
    github_oauth_is_configured,
    revoke_github_oauth_grant,
    start_github_oauth,
)
from app.connections.google import (
    GOOGLE_OAUTH_REVOCATION_URL,
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_OAUTH_USERINFO_URL,
    GOOGLE_PROVIDER_CAPABILITIES,
    GOOGLE_WORKSPACE_MCP_COMMAND,
    complete_google_oauth,
    google_oauth_is_configured,
    revoke_google_token,
    start_google_oauth,
)
from app.connections.models import (
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_GOOGLE_CALENDAR,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_NOTION,
    PROVIDER_SLACK,
    UserConnection,
    create_user_connection,
    delete_user_connection,
    get_user_connection,
    get_user_connection_by_provider,
    list_user_connections,
    serialize_user_connection,
    update_user_connection,
)
from app.connections.notion import (
    NOTION_MCP_ISSUER_URL,
    NOTION_MCP_SERVER_URL,
    build_notion_revocation_endpoint,
    complete_notion_oauth,
    refresh_notion_tokens,
    revoke_notion_token,
    start_notion_oauth,
)
from app.connections.slack import (
    SLACK_MCP_SERVER_URL,
    SLACK_OAUTH_REVOCATION_URL,
    SLACK_OAUTH_USER_TOKEN_URL,
    complete_slack_oauth,
    refresh_slack_tokens,
    revoke_slack_token,
    slack_oauth_is_configured,
    start_slack_oauth,
)
from app.connections.schemas import ConnectionCreateRequest, ConnectionUpdateRequest
from app.connections.policy import (
    FILE_STORAGE_CONNECTION_PROVIDERS,
    ensure_group_allows_connection_provider,
    group_allows_connection_provider,
    group_enabled_connections,
)
from app.mcp.models import MCPServer, OWNER_USER, TRANSPORT_STDIO, TRANSPORT_STREAMABLE_HTTP, create_mcp_server, delete_mcp_server, update_mcp_server
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.settings.utils import get_public_url


_CONNECTIONS_ROUTE = "/workspace/connections"
_NOTION_REFRESH_LEEWAY_SECONDS = 300
_SLACK_REFRESH_LEEWAY_SECONDS = 300
logger = logging.getLogger(__name__)

_PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    PROVIDER_NOTION: {
        "provider": PROVIDER_NOTION,
        "title": "Notion",
        "description": "Connect your Notion workspace through the hosted MCP server and make it available inside chats.",
        "category": "Knowledge Base",
        "badge": "OAuth + MCP",
        "setup_mode": "oauth",
        "default_display_name": "Notion",
        "default_namespace": "notion",
        "default_timeout_seconds": 45,
        "server_url": NOTION_MCP_SERVER_URL,
    },
    PROVIDER_GITHUB: {
        "provider": PROVIDER_GITHUB,
        "title": "GitHub",
        "description": "Connect your GitHub account through the hosted GitHub MCP server and use repositories, issues, pull requests, and more inside chats.",
        "category": "Developer Tools",
        "badge": "PAT + MCP",
        "setup_mode": "choice",
        "default_display_name": "GitHub",
        "default_namespace": "github",
        "default_timeout_seconds": 45,
        "server_url": GITHUB_MCP_SERVER_URL,
    },
    PROVIDER_GMAIL: {
        "provider": PROVIDER_GMAIL,
        "title": "Gmail",
        "description": "Connect Gmail to search threads, read messages, draft replies, and send email from inside chats.",
        "category": "Productivity",
        "badge": "OAuth + MCP",
        "setup_mode": "oauth",
        "default_display_name": "Gmail",
        "default_namespace": "gmail",
        "default_timeout_seconds": 45,
    },
    PROVIDER_GOOGLE_CALENDAR: {
        "provider": PROVIDER_GOOGLE_CALENDAR,
        "title": "Google Calendar",
        "description": "Connect Google Calendar to view schedules, create events, and manage availability inside chats.",
        "category": "Productivity",
        "badge": "OAuth + MCP",
        "setup_mode": "oauth",
        "default_display_name": "Google Calendar",
        "default_namespace": "google_calendar",
        "default_timeout_seconds": 45,
    },
    PROVIDER_SLACK: {
        "provider": PROVIDER_SLACK,
        "title": "Slack",
        "description": "Connect Slack through the hosted Slack MCP server to search conversations, read history, work with canvases, and post messages inside chats.",
        "category": "Communication",
        "badge": "OAuth + MCP",
        "setup_mode": "oauth",
        "default_display_name": "Slack",
        "default_namespace": "slack",
        "default_timeout_seconds": 45,
        "server_url": SLACK_MCP_SERVER_URL,
    },
    PROVIDER_GOOGLE_DRIVE: {
        "provider": PROVIDER_GOOGLE_DRIVE,
        "title": "Google Drive",
        "description": "Connect Google Drive to browse and import files directly in chats.",
        "category": "Storage",
        "badge": "OAuth",
        "setup_mode": "oauth",
        "default_display_name": "Google Drive",
        "default_namespace": "google_drive",
        "default_timeout_seconds": 45,
        "managed_mcp": False,
        # File-source adapters are intentionally separate from MCP. They are
        # surfaced by the chat file dropdown, but they must never become LLM
        # tools or appear in the model's MCP connection selector.
        "connection_type": "file_source_adapter",
    },
}


def connection_provider_oauth_is_configured(db, provider: str) -> bool:
    """Return whether a provider's OAuth flow can be started on this instance.

    Notion uses the MCP server's dynamic client registration, so it does not
    need administrator-managed client credentials. The other OAuth providers
    are ready only after their corresponding login/OAuth settings are enabled
    and contain both a client ID and client secret.
    """
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == PROVIDER_NOTION:
        return True
    if normalized_provider == PROVIDER_GITHUB:
        return github_oauth_is_configured(db)
    if normalized_provider in {
        PROVIDER_GMAIL,
        PROVIDER_GOOGLE_CALENDAR,
        PROVIDER_GOOGLE_DRIVE,
    }:
        return google_oauth_is_configured(db)
    if normalized_provider == PROVIDER_SLACK:
        return slack_oauth_is_configured(db)
    return False


def connection_provider_is_setup(
    db,
    provider: str,
    *,
    oauth_ready: bool | None = None,
) -> bool:
    """Return whether the instance has completed the provider's global setup.

    Managed providers are administrator-enabled integrations. Even when a
    provider also accepts a personal token, it is not advertised until its
    global OAuth client is enabled and complete. This keeps partially
    configured integrations out of every user-facing catalog.
    """
    normalized_provider = str(provider or "").strip().lower()
    meta = _PROVIDER_CATALOG.get(normalized_provider)
    if not meta:
        return False

    setup_mode = str(meta.get("setup_mode") or "").strip().lower()
    if setup_mode in {"oauth", "choice"}:
        return (
            connection_provider_oauth_is_configured(db, normalized_provider)
            if oauth_ready is None
            else oauth_ready
        )
    return setup_mode == "token"


def list_managed_connection_mcp_catalog(db=None) -> list[dict[str, str]]:
    """List connection-backed MCP providers that can be configured.

    Passing a database session applies instance OAuth readiness. The optional
    session keeps configuration-independent callers, such as legacy model
    setting normalization, able to resolve the complete stable provider set.
    """
    items: list[dict[str, str]] = []
    for provider, meta in _PROVIDER_CATALOG.items():
        if meta.get("managed_mcp", True) is False:
            continue
        if db is not None and not connection_provider_is_setup(db, provider):
            continue
        title = str(meta.get("title") or provider).strip() or provider
        items.append(
            {
                "provider": provider,
                "title": title,
            }
        )
    items.sort(key=lambda item: item["title"].lower())
    return items


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask(value: str | None, *, keep: int = 6) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= keep:
        return text
    return f"{text[:keep]}..."


def ensure_connections_enabled(user_id: str, db) -> None:
    # Workspace connections have their own provider allow-list. The separate
    # enable_mcp flag controls only personal, user-created MCP servers.
    enabled = bool(group_enabled_connections(user_id, db))
    logger.info("connections.policy_check user=%s enabled=%s", _mask(user_id), enabled)
    if not enabled:
        raise HTTPException(status_code=403, detail="Connections are disabled for your group.")


def _group_enabled_connections(user_id: str, db) -> list[str]:
    return group_enabled_connections(user_id, db)


def _group_allows_provider(user_id: str, db, *, provider: str) -> bool:
    return group_allows_connection_provider(user_id, db, provider=provider)


def _group_allows_connection_management(user_id: str, db, *, connection) -> bool:
    """Authorize mutations using the provider stored on the connection row."""

    stored_provider = str(getattr(connection, "provider", "") or "").strip().lower()
    return _group_allows_provider(user_id, db, provider=stored_provider)


def _normalize_return_path(value: str | None) -> str:
    if not isinstance(value, str):
        return _CONNECTIONS_ROUTE
    trimmed = value.strip()
    if not trimmed or not trimmed.startswith("/") or trimmed.startswith("//") or "\\" in trimmed:
        return _CONNECTIONS_ROUTE
    return trimmed[:512]


def _provider_meta(provider: str) -> dict[str, Any]:
    meta = _PROVIDER_CATALOG.get(str(provider or "").strip().lower())
    if not meta:
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    return meta


def _assert_connection_oauth_url_allowed(db, url: str, *, provider: str) -> None:
    _assert_connection_url_allowed(
        db,
        url=url,
        feature=f"{provider} connection OAuth initialization",
    )


def _assert_connection_url_allowed(db, *, url: str, feature: str) -> None:
    try:
        assert_url_allowed(
            db,
            url=url,
            feature=feature,
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _mark_connection_policy_blocked(db, connection, error_message: str):
    status = _coerce_connection_status(connection)
    status.update(
        {
            "state": "error" if _is_connection_connected(connection) else "not_connected",
            "last_error": error_message,
            "last_sync_at": _utcnow().isoformat(),
        }
    )
    connection = update_user_connection(db, connection.id, status=status)
    _upsert_connection_mcp_server(db, connection)
    return connection


def _coerce_connection_status(connection, mcp_server=None) -> dict[str, Any]:
    payload = deepcopy(connection.status if isinstance(connection.status, dict) else {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("state", "connected" if _is_connection_connected(connection) else "not_connected")
    payload.setdefault("last_error", "")
    payload.setdefault("last_error_code", "")
    payload.setdefault("tool_count", 0)
    payload.setdefault("tool_names", [])
    payload.setdefault("checked_at", None)
    payload.setdefault("connected_at", connection.connected_at.isoformat() if connection.connected_at else None)
    payload.setdefault("last_sync_at", None)
    if mcp_server and isinstance(getattr(mcp_server, "status", None), dict):
        server_status = mcp_server.status
        payload["tool_count"] = max(int(server_status.get("tool_count") or 0), 0)
        payload["tool_names"] = list(server_status.get("tool_names") or [])
        payload["checked_at"] = server_status.get("checked_at")
        # The MCP server status is the live operational source of truth. Copy
        # empty values too so a successful retry clears a previous token error.
        if "last_error" in server_status:
            payload["last_error"] = str(server_status.get("last_error") or "").strip()
        if "last_error_code" in server_status:
            payload["last_error_code"] = str(server_status.get("last_error_code") or "").strip()[:100]
        available = str(server_status.get("available") or "").strip().lower()
        if available == "down" and _is_connection_connected(connection):
            payload["state"] = "error"
        elif _is_connection_connected(connection):
            payload["state"] = "connected"
    return payload


def _serialize_connection_payload(connection, *, mcp_server=None) -> dict[str, Any]:
    payload = serialize_user_connection(connection)
    payload["status"] = _coerce_connection_status(connection, mcp_server=mcp_server)
    return payload


def _serialize_catalog_connection_payload(connection, *, catalog_provider: str, mcp_server=None) -> dict[str, Any]:
    """Return only connection state required by the workspace catalog.

    The full serializer is reserved for explicit create/update/tool-preview
    responses.  A catalog refresh must not disclose the backing MCP identity,
    raw provider errors, credential mode, tool inventory, or lifecycle dates.
    """
    status = _coerce_connection_status(connection, mcp_server=mcp_server)
    state = str(status.get("state") or "not_connected").strip().lower()
    if state not in {"not_connected", "connected", "error", "reauthorization_required"}:
        state = "connected" if _is_connection_connected(connection) else "not_connected"

    public_error_codes = {
        "github_token_invalid",
        "github_access_denied",
        "mcp_authentication_failed",
        "mcp_access_denied",
        "mcp_connection_failed",
    }
    error_code = str(status.get("last_error_code") or "").strip()
    if not error_code and str(catalog_provider or "").strip().lower() == PROVIDER_GITHUB:
        # Preserve the actionable message for legacy rows without returning
        # the opaque SDK exception text that was stored by older releases.
        raw_error = str(status.get("last_error") or "")
        if "taskgroup" in raw_error.lower() or "server returned an error response" in raw_error.lower():
            error_code = "github_token_invalid"
    if error_code not in public_error_codes:
        error_code = "mcp_connection_failed" if state == "error" else ""

    payload = {
        "id": str(connection.id),
        "enabled": bool(connection.enabled),
        "connected": _is_connection_connected(connection),
        "state": state,
    }
    if error_code:
        payload["error_code"] = error_code
    return payload


def _connection_auth_mode(connection) -> str:
    """Return the user's credential type without exposing server settings."""
    mode = str(getattr(connection, "auth_mode", "") or "").strip().lower()
    return mode if mode in {"oauth", "pat"} else "oauth"


def _connection_secrets(connection) -> dict[str, Any]:
    return connection.secrets if isinstance(connection.secrets, dict) else {}


def _is_connection_connected(connection) -> bool:
    access_token = str(_connection_secrets(connection).get("access_token") or "").strip()
    return bool(access_token)


def _mcp_headers_for_connection(connection) -> dict[str, str]:
    if connection.provider not in {PROVIDER_NOTION, PROVIDER_GITHUB, PROVIDER_SLACK}:
        return {}
    access_token = str(_connection_secrets(connection).get("access_token") or "").strip()
    return {"Authorization": f"Bearer {access_token}"} if access_token else {}


def _mcp_env_for_connection(connection) -> dict[str, str]:
    if connection.provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR}:
        secrets = _connection_secrets(connection)
        client_id = str(secrets.get("client_id") or "").strip()
        client_secret = str(secrets.get("client_secret") or "").strip()
        refresh_token = str(secrets.get("refresh_token") or "").strip()
        capabilities = GOOGLE_PROVIDER_CAPABILITIES.get(connection.provider) or []
        if not client_id or not client_secret or not refresh_token or not capabilities:
            return {}
        return {
            "GOOGLE_WORKSPACE_CLIENT_ID": client_id,
            "GOOGLE_WORKSPACE_CLIENT_SECRET": client_secret,
            "GOOGLE_WORKSPACE_REFRESH_TOKEN": refresh_token,
            "GOOGLE_WORKSPACE_ENABLED_CAPABILITIES": ",".join(capabilities),
        }
    return {}


def _mcp_command_for_connection(connection) -> str | None:
    if connection.provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR}:
        return GOOGLE_WORKSPACE_MCP_COMMAND
    return None


def _mcp_args_for_connection(connection) -> list[str]:
    return []


def _mcp_transport_for_connection(connection) -> str:
    if connection.provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR}:
        return TRANSPORT_STDIO
    return TRANSPORT_STREAMABLE_HTTP


def _mcp_url_for_connection(connection) -> str | None:
    meta = _provider_meta(connection.provider)
    return meta.get("server_url")


def _get_managed_mcp_server_for_connection(db, connection):
    query = db.query(MCPServer).filter(
        MCPServer.managed_connection_id == connection.id,
        MCPServer.owner_type == OWNER_USER,
        MCPServer.owner_user_id == connection.user_id,
    )
    if connection.mcp_server_id:
        server = (
            query.filter(
                MCPServer.id == connection.mcp_server_id,
            ).first()
        )
        if server is not None:
            return server
    return query.first()


def _upsert_connection_mcp_server(db, connection):
    """Build a user-authenticated MCP record from global provider defaults."""
    meta = _provider_meta(connection.provider)
    if meta.get("managed_mcp") is False:
        return None
    headers = _mcp_headers_for_connection(connection)
    env = _mcp_env_for_connection(connection)
    enabled = bool(connection.enabled and _is_connection_connected(connection))

    server = _get_managed_mcp_server_for_connection(db, connection)

    payload = {
        "owner_type": OWNER_USER,
        "owner_user_id": connection.user_id,
        "name": meta["default_display_name"],
        "description": meta["description"],
        "namespace": meta["default_namespace"],
        "transport": _mcp_transport_for_connection(connection),
        "enabled": enabled,
        "url": _mcp_url_for_connection(connection),
        "command": _mcp_command_for_connection(connection),
        "args": _mcp_args_for_connection(connection),
        "headers": headers,
        "env": env,
        "allowed_tools": [],
        "timeout_seconds": meta["default_timeout_seconds"],
        "managed_connection_id": connection.id,
    }

    if server is None:
        logger.info(
            "connections.mcp.create connection=%s user=%s provider=%s enabled=%s namespace=%s",
            _mask(connection.id),
            _mask(connection.user_id),
            connection.provider,
            enabled,
            meta["default_namespace"],
        )
        server = create_mcp_server(db, **payload, status={"available": "unknown", "tool_count": 0})
        update_user_connection(db, connection.id, mcp_server_id=server.id)
        return server

    logger.info(
        "connections.mcp.update connection=%s server=%s user=%s provider=%s enabled=%s namespace=%s",
        _mask(connection.id),
        _mask(getattr(server, "id", None)),
        _mask(connection.user_id),
        connection.provider,
        enabled,
        meta["default_namespace"],
    )
    updated = update_mcp_server(db, server.id, **payload)
    if connection.mcp_server_id != updated.id:
        update_user_connection(db, connection.id, mcp_server_id=updated.id)
    return updated


def _mark_connection_needs_reauth(db, connection, error_message: str):
    logger.warning(
        "connections.reauth_required connection=%s user=%s provider=%s error=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        connection.provider,
        error_message,
    )
    secrets = _connection_secrets(connection)
    secrets.update(
        {
            "access_token": None,
            "refresh_token": None,
            "expires_at": None,
        }
    )
    status = _coerce_connection_status(connection)
    status.update(
        {
            "state": "reauthorization_required",
            "last_error": error_message,
            "last_sync_at": _utcnow().isoformat(),
        }
    )
    connection = update_user_connection(db, connection.id, secrets=secrets, status=status)
    _upsert_connection_mcp_server(db, connection)
    return connection


def _mark_connection_refresh_error(db, connection, error_message: str):
    logger.warning(
        "connections.refresh.retryable_error connection=%s user=%s provider=%s error=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        connection.provider,
        error_message,
    )
    status = _coerce_connection_status(connection)
    status.update(
        {
            "state": "error" if _is_connection_connected(connection) else "not_connected",
            "last_error": error_message,
            "last_sync_at": _utcnow().isoformat(),
        }
    )
    connection = update_user_connection(db, connection.id, status=status)
    _upsert_connection_mcp_server(db, connection)
    return connection


def _refresh_connection_if_needed(db, connection, *, force: bool = False):
    if connection.provider not in {PROVIDER_NOTION, PROVIDER_SLACK}:
        return connection
    if not _is_connection_connected(connection):
        return connection
    secrets = _connection_secrets(connection)
    expires_at_raw = secrets.get("expires_at")
    try:
        expires_at = int(expires_at_raw) if expires_at_raw is not None else None
    except (TypeError, ValueError):
        expires_at = None
    now_timestamp = int(_utcnow().timestamp())
    logger.info(
        "connections.refresh.check connection=%s user=%s provider=%s force=%s expires_at=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        connection.provider,
        force,
        expires_at,
    )
    if not force and expires_at is None:
        return connection
    leeway_seconds = (
        _NOTION_REFRESH_LEEWAY_SECONDS
        if connection.provider == PROVIDER_NOTION
        else _SLACK_REFRESH_LEEWAY_SECONDS
    )
    if not force and expires_at and expires_at > now_timestamp + leeway_seconds:
        return connection
    refresh_target = (
        str(secrets.get("token_endpoint") or "").strip()
        or (
            f"{NOTION_MCP_ISSUER_URL.rstrip('/')}/token"
            if connection.provider == PROVIDER_NOTION
            else SLACK_OAUTH_USER_TOKEN_URL
        )
    )
    try:
        # Validate issuer base URL for consistency with bootstrap/completion flows
        if connection.provider == PROVIDER_NOTION:
            issuer_base = NOTION_MCP_ISSUER_URL.rstrip('/')
            _assert_connection_url_allowed(
                db,
                url=issuer_base,
                feature=f"{connection.provider} connection token refresh",
            )
        else:
            _assert_connection_url_allowed(
                db,
                url=refresh_target,
                feature=f"{connection.provider} connection token refresh",
            )
        if connection.provider == PROVIDER_NOTION:
            refreshed = refresh_notion_tokens(secrets)
        else:
            refreshed = refresh_slack_tokens(secrets)
    except HTTPException as exc:
        _mark_connection_policy_blocked(db, connection, str(exc.detail))
        raise
    except ConnectionRefreshReauthRequiredError as exc:
        _mark_connection_needs_reauth(db, connection, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionRefreshRetryableError as exc:
        _mark_connection_refresh_error(db, connection, str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        _mark_connection_refresh_error(db, connection, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "connections.refresh.success connection=%s user=%s provider=%s new_expires_at=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        connection.provider,
        refreshed.get("expires_at"),
    )
    status = _coerce_connection_status(connection)
    status.update(
        {
            "state": "connected",
            "last_error": "",
            "last_sync_at": _utcnow().isoformat(),
        }
    )
    connection = update_user_connection(db, connection.id, secrets=refreshed, status=status)
    _upsert_connection_mcp_server(db, connection)
    return connection


def _connected_status_payload(*, state: str = "connected") -> dict[str, Any]:
    now = _utcnow().isoformat()
    return {
        "state": state,
        "last_error": "",
        "tool_count": 0,
        "tool_names": [],
        "checked_at": None,
        "connected_at": now,
        "last_sync_at": now,
    }


def _delete_revocation_result(*, provider: str, supported: bool, attempted: bool = False) -> dict[str, Any]:
    return {
        "provider": provider,
        "supported": supported,
        "attempted": attempted,
        "state": "unsupported" if not supported else ("revoked" if attempted else "not_needed"),
        "successes": [],
        "failures": [],
    }


def _record_delete_revocation_failure(result: dict[str, Any], *, target: str, reason: str) -> None:
    result["failures"].append(
        {
            "target": target,
            "reason": reason,
        }
    )


def _finalize_delete_revocation_result(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("supported"):
        result["state"] = "unsupported"
        return result
    if result.get("failures") and result.get("successes"):
        result["state"] = "partial"
    elif result.get("failures"):
        result["state"] = "failed"
    elif result.get("attempted"):
        result["state"] = "revoked"
    else:
        result["state"] = "not_needed"
    return result


def _revoke_connection_before_delete(db, connection) -> dict[str, Any]:
    provider = str(connection.provider or "").strip().lower()
    result = _delete_revocation_result(provider=provider, supported=True)
    secrets = _connection_secrets(connection)
    access_token = str(secrets.get("access_token") or "").strip()
    refresh_token = str(secrets.get("refresh_token") or "").strip()
    auth_mode = _connection_auth_mode(connection)

    def attempt(*, target: str, url_or_builder, revoke_call) -> None:
        result["attempted"] = True
        try:
            url = url_or_builder() if callable(url_or_builder) else url_or_builder
            _assert_connection_url_allowed(
                db,
                url=url,
                feature=f"{provider} connection token revocation",
            )
            revoke_call()
        except Exception as exc:
            logger.warning(
                "connections.delete.revocation.failed connection=%s user=%s provider=%s target=%s error_type=%s",
                _mask(connection.id),
                _mask(connection.user_id),
                provider,
                target,
                type(exc).__name__,
            )
            _record_delete_revocation_failure(result, target=target, reason=type(exc).__name__)
            return
        logger.info(
            "connections.delete.revocation.success connection=%s user=%s provider=%s target=%s",
            _mask(connection.id),
            _mask(connection.user_id),
            provider,
            target,
        )
        result["successes"].append(target)

    if provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR, PROVIDER_GOOGLE_DRIVE}:
        token = refresh_token or access_token
        if token:
            attempt(
                target="refresh_token" if refresh_token else "access_token",
                url_or_builder=GOOGLE_OAUTH_REVOCATION_URL,
                revoke_call=lambda: revoke_google_token(token),
            )
        return _finalize_delete_revocation_result(result)

    if provider == PROVIDER_SLACK:
        revocation_targets: list[tuple[str, str]] = []
        if refresh_token:
            revocation_targets.append(("refresh_token", refresh_token))
        if access_token and access_token != refresh_token:
            revocation_targets.append(("access_token", access_token))
        for target_name, token_value in revocation_targets:
            attempt(
                target=target_name,
                url_or_builder=SLACK_OAUTH_REVOCATION_URL,
                revoke_call=lambda token_value=token_value: revoke_slack_token(token_value),
            )
        return _finalize_delete_revocation_result(result)

    if provider == PROVIDER_NOTION:
        token = refresh_token or access_token
        if token:
            attempt(
                target="refresh_token" if refresh_token else "access_token",
                url_or_builder=lambda: build_notion_revocation_endpoint(secrets),
                revoke_call=lambda: revoke_notion_token(secrets, token=token),
            )
        return _finalize_delete_revocation_result(result)

    if provider == PROVIDER_GITHUB:
        if auth_mode != "oauth":
            return _finalize_delete_revocation_result(
                _delete_revocation_result(provider=provider, supported=False)
            )
        if access_token:
            attempt(
                target="oauth_grant",
                url_or_builder=lambda: build_github_oauth_revocation_url(db),
                revoke_call=lambda: revoke_github_oauth_grant(db, access_token=access_token),
            )
        return _finalize_delete_revocation_result(result)

    return _finalize_delete_revocation_result(
        _delete_revocation_result(provider=provider, supported=False)
    )


def _create_or_update_manual_connection(
    db,
    *,
    user_id: str,
    provider: str,
    access_token: str,
):
    """Create or reconnect a manual-token connection using provider defaults."""
    secrets = {
        "access_token": str(access_token or "").strip(),
        "refresh_token": None,
        "expires_at": None,
        "scopes": [],
    }
    status = _connected_status_payload()
    existing = get_user_connection_by_provider(db, user_id, provider)
    if existing is None:
        logger.info("connections.manual.create user=%s provider=%s", _mask(user_id), provider)
        connection = create_user_connection(
            db,
            user_id=user_id,
            provider=provider,
            enabled=True,
            auth_mode="pat",
            secrets=secrets,
            status=status,
            connected_at=_utcnow(),
        )
    else:
        logger.info(
            "connections.manual.update_existing user=%s provider=%s connection=%s",
            _mask(user_id),
            provider,
            _mask(existing.id),
        )
        connection = update_user_connection(
            db,
            existing.id,
            enabled=True,
            auth_mode="pat",
            secrets=secrets,
            status=status,
            connected_at=_utcnow(),
        )
    server = _upsert_connection_mcp_server(db, connection)
    return update_user_connection(
        db,
        connection.id,
        mcp_server_id=server.id,
        status=_coerce_connection_status(connection, mcp_server=server),
    )


def prepare_managed_mcp_server_for_runtime(db, server):
    managed_connection_id = str(getattr(server, "managed_connection_id", None) or "").strip()
    if not managed_connection_id:
        return server
    connection = db.query(UserConnection).filter(UserConnection.id == managed_connection_id).first()
    if not connection:
        return server
    try:
        connection = _refresh_connection_if_needed(db, connection)
    except HTTPException:
        return _get_managed_mcp_server_for_connection(db, connection) or server
    return _get_managed_mcp_server_for_connection(db, connection) or server


def list_connections_catalog_payload(db, user_id: str) -> dict[str, Any]:
    ensure_connections_enabled(user_id, db)
    enabled_providers = _group_enabled_connections(user_id, db)
    if not enabled_providers:
        return {"items": []}
    connections = list_user_connections(db, user_id)
    logger.info(
        "connections.catalog_payload user=%s count=%s providers=%s",
        _mask(user_id),
        len(connections),
        [connection.provider for connection in connections],
    )
    connection_map = {connection.provider: connection for connection in connections}
    items: list[dict[str, Any]] = []
    for provider, meta in _PROVIDER_CATALOG.items():
        if enabled_providers and provider not in enabled_providers:
            continue
        if provider in FILE_STORAGE_CONNECTION_PROVIDERS and not _group_allows_provider(user_id, db, provider=provider):
            continue
        connection = connection_map.get(provider)
        oauth_ready = connection_provider_oauth_is_configured(db, provider)

        # Provider cards are an instance capability catalog, not a record of
        # dormant user rows. Hide incomplete global integrations consistently,
        # including when an old user connection still exists in storage.
        if not connection_provider_is_setup(
            db,
            provider,
            oauth_ready=oauth_ready,
        ):
            continue
        mcp_server = None
        if connection and _is_connection_connected(connection):
            try:
                mcp_server = _upsert_connection_mcp_server(db, connection)
                connection = get_user_connection(db, user_id, connection.id)
            except Exception:
                mcp_server = None
        items.append(
            {
                "provider": provider,
                "setup_mode": meta["setup_mode"],
                "connection_type": meta.get("connection_type", "mcp"),
                "connection": _serialize_catalog_connection_payload(connection, catalog_provider=provider, mcp_server=mcp_server) if connection else None,
            }
        )
    return {"items": items}


def start_connection_oauth(db, *, user_id: str, provider: str, return_path: str | None = None) -> str:
    ensure_connections_enabled(user_id, db)
    normalized_provider = str(provider or "").strip().lower()
    ensure_group_allows_connection_provider(user_id, db, provider=normalized_provider)
    if normalized_provider not in {
        PROVIDER_NOTION,
        PROVIDER_GITHUB,
        PROVIDER_GMAIL,
        PROVIDER_GOOGLE_CALENDAR,
        PROVIDER_GOOGLE_DRIVE,
        PROVIDER_SLACK,
    }:
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    if not connection_provider_is_setup(db, normalized_provider):
        # Match the hidden catalog behavior for direct/stale client requests.
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    normalized_return_path = _normalize_return_path(return_path)
    public_url = get_public_url(db).rstrip("/")
    redirect_uri = f"{public_url}/api/v1/connections/oauth/{normalized_provider}/callback"
    logger.info(
        "connections.start provider=%s user=%s return_path=%s public_url=%s redirect_uri=%s",
        normalized_provider,
        _mask(user_id),
        normalized_return_path,
        public_url,
        redirect_uri,
    )
    if normalized_provider == PROVIDER_NOTION:
        _assert_connection_url_allowed(
            db,
            url=NOTION_MCP_ISSUER_URL,
            feature="notion connection OAuth bootstrap",
        )
        oauth_url = start_notion_oauth(
            db,
            user_id=user_id,
            return_path=normalized_return_path,
            redirect_uri=redirect_uri,
            origin=public_url,
        )
    elif normalized_provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR, PROVIDER_GOOGLE_DRIVE}:
        oauth_url = start_google_oauth(
            db,
            provider=normalized_provider,
            user_id=user_id,
            return_path=normalized_return_path,
            redirect_uri=redirect_uri,
        )
    elif normalized_provider == PROVIDER_SLACK:
        oauth_url = start_slack_oauth(
            db,
            user_id=user_id,
            return_path=normalized_return_path,
            redirect_uri=redirect_uri,
        )
    else:
        oauth_url = start_github_oauth(
            db,
            user_id=user_id,
            return_path=normalized_return_path,
            redirect_uri=redirect_uri,
        )

    _assert_connection_oauth_url_allowed(
        db,
        oauth_url,
        provider=normalized_provider,
    )
    return oauth_url


def create_connection_payload(db, *, user_id: str, provider: str, payload: ConnectionCreateRequest) -> dict[str, Any]:
    """Connect GitHub with a user token and global server configuration."""
    ensure_connections_enabled(user_id, db)
    normalized_provider = str(provider or "").strip().lower()
    ensure_group_allows_connection_provider(user_id, db, provider=normalized_provider)
    if normalized_provider != PROVIDER_GITHUB:
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    if not connection_provider_is_setup(db, normalized_provider):
        # A personal token is an alternate authentication method, not a way to
        # bypass the administrator's managed-provider configuration.
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    connection = _create_or_update_manual_connection(
        db,
        user_id=user_id,
        provider=normalized_provider,
        access_token=payload.access_token,
    )
    logger.info(
        "connections.manual.persisted user=%s provider=%s connection=%s connected=%s",
        _mask(user_id),
        normalized_provider,
        _mask(connection.id),
        _is_connection_connected(connection),
    )
    server = _get_managed_mcp_server_for_connection(db, connection)
    return _serialize_connection_payload(connection, mcp_server=server)


def complete_connection_oauth(
    db,
    *,
    provider: str,
    state: str,
    code: str,
    authorization_issuer: str | None = None,
) -> dict[str, Any]:
    """Complete a provider OAuth flow, including MCP issuer validation."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {
        PROVIDER_NOTION,
        PROVIDER_GITHUB,
        PROVIDER_GMAIL,
        PROVIDER_GOOGLE_CALENDAR,
        PROVIDER_GOOGLE_DRIVE,
        PROVIDER_SLACK,
    }:
        raise HTTPException(status_code=404, detail="Connection provider not found.")
    logger.info(
        "connections.complete.begin provider=%s state=%s code_len=%s",
        normalized_provider,
        _mask(state),
        len(str(code or "")),
    )
    if normalized_provider == PROVIDER_NOTION:
        _assert_connection_url_allowed(
            db,
            url=NOTION_MCP_ISSUER_URL,
            feature="notion connection OAuth completion",
        )
        oauth_result = complete_notion_oauth(
            db,
            state=state,
            code=code,
            authorization_issuer=authorization_issuer,
        )
    elif normalized_provider in {PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR, PROVIDER_GOOGLE_DRIVE}:
        _assert_connection_url_allowed(
            db,
            url=GOOGLE_OAUTH_TOKEN_URL,
            feature=f"{normalized_provider} connection OAuth completion",
        )
        _assert_connection_url_allowed(
            db,
            url=GOOGLE_OAUTH_USERINFO_URL,
            feature=f"{normalized_provider} connection profile lookup",
        )
        oauth_result = complete_google_oauth(db, provider=normalized_provider, state=state, code=code)
    elif normalized_provider == PROVIDER_SLACK:
        _assert_connection_url_allowed(
            db,
            url=SLACK_OAUTH_USER_TOKEN_URL,
            feature="slack connection OAuth completion",
        )
        oauth_result = complete_slack_oauth(db, state=state, code=code)
    else:
        oauth_result = complete_github_oauth(db, state=state, code=code)
    user_id = str(oauth_result.get("user_id") or "").strip()
    logger.info(
        "connections.complete.oauth_result provider=%s state=%s user=%s return_path=%s has_access=%s has_refresh=%s",
        normalized_provider,
        _mask(state),
        _mask(user_id),
        oauth_result.get("return_path"),
        bool((oauth_result.get("secrets") or {}).get("access_token")),
        bool((oauth_result.get("secrets") or {}).get("refresh_token")),
    )
    if not user_id:
        raise HTTPException(status_code=400, detail="OAuth completion did not resolve a user.")
    ensure_group_allows_connection_provider(user_id, db, provider=normalized_provider)
    connection_provider = normalized_provider
    existing = get_user_connection_by_provider(db, user_id, connection_provider)
    if existing is None:
        logger.info("connections.complete.create user=%s provider=%s", _mask(user_id), connection_provider)
        connection = create_user_connection(
            db,
            user_id=user_id,
            provider=connection_provider,
            enabled=True,
            auth_mode="oauth",
            secrets=oauth_result["secrets"],
            status=oauth_result["status"],
            connected_at=_utcnow(),
        )
    else:
        logger.info(
            "connections.complete.update_existing user=%s provider=%s connection=%s",
            _mask(user_id),
            connection_provider,
            _mask(existing.id),
        )
        connection = update_user_connection(
            db,
            existing.id,
            enabled=True,
            auth_mode="oauth",
            secrets=oauth_result["secrets"],
            status=oauth_result["status"],
            connected_at=_utcnow(),
        )
    logger.info(
        "connections.complete.persisted user=%s provider=%s connection=%s connected=%s",
        _mask(user_id),
        connection_provider,
        _mask(connection.id),
        _is_connection_connected(connection),
    )
    server = _upsert_connection_mcp_server(db, connection)
    update_kwargs: dict[str, Any] = {"status": _coerce_connection_status(connection, mcp_server=server)}
    if server is not None:
        update_kwargs["mcp_server_id"] = server.id
    connection = update_user_connection(db, connection.id, **update_kwargs)
    logger.info(
        "connections.complete.done user=%s provider=%s connection=%s server=%s",
        _mask(user_id),
        connection_provider,
        _mask(connection.id),
        _mask(getattr(server, "id", None)),
    )
    return {
        "return_path": _normalize_return_path(str(oauth_result.get("return_path") or _CONNECTIONS_ROUTE)),
        "connection": _serialize_catalog_connection_payload(connection, catalog_provider=normalized_provider, mcp_server=server),
    }


def update_connection_payload(db, *, user_id: str, connection_id: str, payload: ConnectionUpdateRequest) -> dict[str, Any]:
    """Update only the per-user state supported by managed connections."""
    ensure_connections_enabled(user_id, db)
    connection = get_user_connection(db, user_id, connection_id)
    if not _group_allows_connection_management(user_id, db, connection=connection):
        raise HTTPException(status_code=403, detail="Connection provider is not enabled for your group.")
    # ``enabled`` defaults to True in the request schema for backwards
    # compatibility. Only persist it when the client actually sent the field,
    # otherwise rotating a GitHub token would accidentally re-enable a disabled
    # connection.
    updates: dict[str, Any] = {}
    if "enabled" in payload.model_fields_set:
        updates["enabled"] = payload.enabled
    access_token = str(payload.access_token or "").strip()
    if access_token:
        if connection.provider != PROVIDER_GITHUB:
            raise HTTPException(status_code=400, detail="This connection does not support manual token updates.")
        updates["auth_mode"] = "pat"
        secrets = _connection_secrets(connection)
        secrets.update(
            {
                "access_token": access_token,
                "refresh_token": None,
                "expires_at": None,
                "scopes": [],
            }
        )
        updates["secrets"] = secrets
        updates["status"] = _connected_status_payload()
    connection = update_user_connection(
        db,
        connection.id,
        **updates,
    )
    if _is_connection_connected(connection):
        connection = _refresh_connection_if_needed(db, connection)
    server = _upsert_connection_mcp_server(db, connection)
    update_kwargs: dict[str, Any] = {"status": _coerce_connection_status(connection, mcp_server=server)}
    if server is not None:
        update_kwargs["mcp_server_id"] = server.id
    connection = update_user_connection(db, connection.id, **update_kwargs)
    return _serialize_connection_payload(connection, mcp_server=server)


def delete_connection_payload(db, *, user_id: str, connection_id: str) -> dict[str, Any]:
    ensure_connections_enabled(user_id, db)
    connection = get_user_connection(db, user_id, connection_id)
    if not _group_allows_connection_management(user_id, db, connection=connection):
        raise HTTPException(status_code=403, detail="Connection provider is not enabled for your group.")
    revocation = _revoke_connection_before_delete(db, connection)
    server = _get_managed_mcp_server_for_connection(db, connection)
    automation_references_removed = 0
    if server is not None:
        from app.automations.models import remove_mcp_server_from_automations

        automation_references_removed = remove_mcp_server_from_automations(
            db,
            server.id,
            commit=False,
        )
        delete_mcp_server(db, server.id)
    delete_user_connection(db, connection.id)
    return {
        "provider_revocation": revocation,
        "automation_references_removed": automation_references_removed,
    }


def preview_connection_tools_payload(db, *, user_id: str, connection_id: str) -> dict[str, Any]:
    ensure_connections_enabled(user_id, db)
    connection = get_user_connection(db, user_id, connection_id)
    if not _group_allows_provider(user_id, db, provider=connection.provider):
        raise HTTPException(status_code=403, detail="Connection provider is not enabled for your group.")
    meta = _provider_meta(connection.provider)
    if meta.get("managed_mcp") is False:
        # Keep the API honest for stale clients or direct callers. A file
        # source adapter has a separate chat file-picker surface and does not
        # expose tools that an LLM can call.
        raise HTTPException(
            status_code=404,
            detail="This connection is a file source adapter and does not expose LLM tools.",
        )
    server = _get_managed_mcp_server_for_connection(db, connection)
    if not server:
        return {
            "connection": _serialize_connection_payload(connection),
            "tools": [],
            "source": "bridge",
        }
    if not _is_connection_connected(connection):
        raise HTTPException(status_code=400, detail="Connect this provider before previewing its tools.")
    connection = _refresh_connection_if_needed(db, connection)
    server = _upsert_connection_mcp_server(db, connection)
    from app.mcp.utils import preview_server_tools

    tools = preview_server_tools(db, server)
    connection = update_user_connection(db, connection.id, status=_coerce_connection_status(connection, mcp_server=server))
    return {
        "connection": _serialize_connection_payload(connection, mcp_server=server),
        "tools": tools,
        "source": "bridge",
    }


def build_callback_redirect_url(db, *, return_path: str, status: str, provider: str, error: str | None = None) -> str:
    public_url = get_public_url(db).rstrip("/")
    path = _normalize_return_path(return_path)
    query = f"connection_provider={quote(provider)}&connection_status={quote(status)}"
    if error:
        query += f"&connection_error={quote(error)}"
    separator = "&" if "?" in path else "?"
    return f"{public_url}{path}{separator}{query}"
