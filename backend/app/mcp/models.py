from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm.attributes import flag_modified

from app.database import Base
from app.utils.icon_security import require_safe_icon_input
from app.utils.sqlalchemy_encryption import EncryptedJSON


OWNER_ADMIN = "admin"
OWNER_USER = "user"
TRANSPORT_STREAMABLE_HTTP = "streamable_http"
TRANSPORT_SSE = "sse"
TRANSPORT_STDIO = "stdio"
VALID_OWNERS = {OWNER_ADMIN, OWNER_USER}
VALID_TRANSPORTS = {TRANSPORT_STREAMABLE_HTTP, TRANSPORT_SSE, TRANSPORT_STDIO}
AUTH_HEADERS = "headers"
AUTH_OAUTH = "oauth"
VALID_AUTH_MODES = {AUTH_HEADERS, AUTH_OAUTH}


class MCPServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint("owner_type IN ('admin', 'user')", name="ck_mcp_servers_owner_type"),
        CheckConstraint(
            "transport IN ('streamable_http', 'sse', 'stdio')",
            name="ck_mcp_servers_transport",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 600",
            name="ck_mcp_servers_timeout_seconds",
        ),
        CheckConstraint(
            "auth_mode IN ('headers', 'oauth')",
            name="ck_mcp_servers_auth_mode",
        ),
        CheckConstraint(
            "(owner_type = 'admin' AND owner_user_id IS NULL) "
            "OR (owner_type = 'user' AND owner_user_id IS NOT NULL "
            "AND length(trim(owner_user_id)) > 0)",
            name="ck_mcp_servers_owner_user",
        ),
        CheckConstraint(
            "(transport = 'stdio' AND command IS NOT NULL AND length(trim(command)) > 0 AND url IS NULL) "
            "OR (transport IN ('streamable_http', 'sse') AND url IS NOT NULL "
            "AND length(trim(url)) > 0 AND command IS NULL)",
            name="ck_mcp_servers_transport_endpoint",
        ),
        CheckConstraint(
            "transport != 'stdio' OR (owner_type = 'user' "
            "AND managed_connection_id IS NOT NULL "
            "AND length(trim(managed_connection_id)) > 0)",
            name="ck_mcp_servers_stdio_managed",
        ),
        Index("ix_mcp_servers_owner_type", "owner_type"),
        Index("ix_mcp_servers_owner_user_id", "owner_user_id"),
        Index("ix_mcp_servers_enabled", "enabled"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    owner_type = Column(String, nullable=False)
    owner_user_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    icon = Column(Text, nullable=True, default="")
    description = Column(String, nullable=True)
    namespace = Column(String, nullable=True)
    transport = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    url = Column(String, nullable=True)
    command = Column(String, nullable=True)
    args = Column(JSON, nullable=False, default=list)
    headers = Column(EncryptedJSON, nullable=True)
    auth_mode = Column(String, nullable=False, default=AUTH_HEADERS)
    oauth = Column(EncryptedJSON, nullable=True)
    env = Column(EncryptedJSON, nullable=True)
    allowed_tools = Column(JSON, nullable=False, default=list)
    timeout_seconds = Column(Integer, nullable=False, default=30)
    managed_connection_id = Column(String, nullable=True)
    status = Column(JSON, nullable=False, default=lambda: {"available": "unknown", "tool_count": 0})
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class MCPOAuthState(Base):
    """Short-lived, encrypted state for a remote MCP authorization redirect."""

    __tablename__ = "mcp_oauth_states"
    __table_args__ = (
        Index("ix_mcp_oauth_states_server_id", "server_id"),
        Index("ix_mcp_oauth_states_user_id", "user_id"),
    )

    state = Column(String, primary_key=True, unique=True, nullable=False)
    server_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    return_path = Column(String, nullable=False)
    redirect_uri = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    secrets = Column(EncryptedJSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)


def _normalize_json_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_secret_map(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if item is None:
            continue
        value_text = str(item).strip()
        if value_text:
            result[key_text] = value_text
    return result


def _normalize_status(value) -> dict:
    base = {
        "available": "unknown",
        "checked_at": None,
        "tool_count": 0,
        "tool_names": [],
        "last_error": "",
        "last_error_code": "",
    }
    if not isinstance(value, dict):
        return base
    normalized = dict(base)
    available = str(value.get("available") or "unknown").strip().lower()
    if available in {"up", "down", "unknown", "warning"}:
        normalized["available"] = available
    normalized["checked_at"] = value.get("checked_at")
    try:
        normalized["tool_count"] = max(int(value.get("tool_count") or 0), 0)
    except (TypeError, ValueError):
        normalized["tool_count"] = 0
    normalized["tool_names"] = _normalize_json_list(value.get("tool_names"))
    normalized["last_error"] = str(value.get("last_error") or "").strip()
    normalized["last_error_code"] = str(value.get("last_error_code") or "").strip()[:100]
    return normalized


def create_mcp_server(db, *, owner_type: str, owner_user_id: str | None, name: str, icon: str | None = None, description: str | None, namespace: str | None, transport: str, enabled: bool, url: str | None, command: str | None, args, headers, env, allowed_tools, timeout_seconds: int, status: dict | None = None, managed_connection_id: str | None = None, auth_mode: str = AUTH_HEADERS):
    """Persist an MCP server while enforcing the internal stdio boundary."""
    owner_value = str(owner_type or "").strip().lower()
    if owner_value not in VALID_OWNERS:
        raise HTTPException(status_code=400, detail="Invalid MCP server owner type.")

    transport_value = str(transport or "").strip().lower()
    if transport_value not in VALID_TRANSPORTS:
        raise HTTPException(status_code=400, detail="Invalid MCP server transport.")
    auth_mode_value = str(auth_mode or AUTH_HEADERS).strip().lower()
    if auth_mode_value not in VALID_AUTH_MODES:
        raise HTTPException(status_code=400, detail="Invalid MCP server authentication mode.")
    if transport_value == TRANSPORT_STDIO and auth_mode_value != AUTH_HEADERS:
        raise HTTPException(status_code=400, detail="Stdio MCP servers cannot use OAuth.")

    managed_connection_value = str(managed_connection_id or "").strip() or None
    # Stdio is not a configurable MCP transport. It exists only so Omlorix can
    # represent packaged workers created by the managed-connections service.
    if transport_value == TRANSPORT_STDIO and (
        owner_value != OWNER_USER or managed_connection_value is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Stdio MCP transport is reserved for Omlorix-managed connections.",
        )

    if owner_value == OWNER_ADMIN:
        owner_user_id = None
    else:
        owner_user_id = str(owner_user_id or "").strip() or None
        if owner_user_id is None:
            raise HTTPException(status_code=400, detail="User-owned MCP servers require an owner.")

    server = MCPServer(
        owner_type=owner_value,
        owner_user_id=owner_user_id,
        name=str(name or "").strip(),
        icon=require_safe_icon_input(icon, fallback=""),
        description=str(description or "").strip() or None,
        namespace=str(namespace or "").strip() or None,
        transport=transport_value,
        enabled=bool(enabled),
        url=str(url or "").strip() or None,
        command=str(command or "").strip() or None,
        args=_normalize_json_list(args),
        headers=_normalize_secret_map(headers),
        auth_mode=auth_mode_value,
        oauth={},
        env=_normalize_secret_map(env),
        allowed_tools=_normalize_json_list(allowed_tools),
        timeout_seconds=max(1, min(int(timeout_seconds or 30), 600)),
        managed_connection_id=managed_connection_value,
        status=_normalize_status(status),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    if not server.name:
        raise HTTPException(status_code=400, detail="MCP server name is required.")
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def get_mcp_server(db, server_id: str) -> MCPServer:
    server = db.query(MCPServer).filter(MCPServer.id == str(server_id or "").strip()).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    return server


def update_mcp_server(db, server_id: str, **updates) -> MCPServer:
    """Update an MCP server and preserve the managed-only stdio boundary."""
    server = get_mcp_server(db, server_id)
    previous_url = server.url
    previous_auth_mode = str(getattr(server, "auth_mode", AUTH_HEADERS) or AUTH_HEADERS)
    if "owner_type" in updates:
        owner_value = str(updates.get("owner_type") or "").strip().lower()
        if owner_value not in VALID_OWNERS:
            raise HTTPException(status_code=400, detail="Invalid MCP server owner type.")
        server.owner_type = owner_value
        if owner_value == OWNER_ADMIN:
            server.owner_user_id = None
    if "owner_user_id" in updates and server.owner_type == OWNER_USER:
        owner_user_id = str(updates.get("owner_user_id") or "").strip() or None
        if owner_user_id is None:
            raise HTTPException(status_code=400, detail="User-owned MCP servers require an owner.")
        server.owner_user_id = owner_user_id
    if "name" in updates:
        server.name = str(updates.get("name") or "").strip()
        if not server.name:
            raise HTTPException(status_code=400, detail="MCP server name is required.")
    if "icon" in updates:
        server.icon = require_safe_icon_input(updates.get("icon"), fallback="")
    if "description" in updates:
        server.description = str(updates.get("description") or "").strip() or None
    if "namespace" in updates:
        server.namespace = str(updates.get("namespace") or "").strip() or None
    if "transport" in updates:
        transport_value = str(updates.get("transport") or "").strip().lower()
        if transport_value not in VALID_TRANSPORTS:
            raise HTTPException(status_code=400, detail="Invalid MCP server transport.")
        server.transport = transport_value
    if "enabled" in updates:
        server.enabled = bool(updates.get("enabled"))
    if "url" in updates:
        server.url = str(updates.get("url") or "").strip() or None
    if "command" in updates:
        server.command = str(updates.get("command") or "").strip() or None
    if "args" in updates:
        server.args = _normalize_json_list(updates.get("args"))
        flag_modified(server, "args")
    if "headers" in updates:
        server.headers = _normalize_secret_map(updates.get("headers"))
    if "auth_mode" in updates:
        auth_mode_value = str(updates.get("auth_mode") or AUTH_HEADERS).strip().lower()
        if auth_mode_value not in VALID_AUTH_MODES:
            raise HTTPException(status_code=400, detail="Invalid MCP server authentication mode.")
        if server.transport == TRANSPORT_STDIO and auth_mode_value != AUTH_HEADERS:
            raise HTTPException(status_code=400, detail="Stdio MCP servers cannot use OAuth.")
        server.auth_mode = auth_mode_value
        if auth_mode_value != AUTH_OAUTH:
            server.oauth = {}
    if "oauth" in updates:
        server.oauth = deepcopy(updates.get("oauth") if isinstance(updates.get("oauth"), dict) else {})
    if "env" in updates:
        server.env = _normalize_secret_map(updates.get("env"))
    if "allowed_tools" in updates:
        server.allowed_tools = _normalize_json_list(updates.get("allowed_tools"))
        flag_modified(server, "allowed_tools")
    if "timeout_seconds" in updates:
        server.timeout_seconds = max(1, min(int(updates.get("timeout_seconds") or 30), 600))
    if "managed_connection_id" in updates:
        server.managed_connection_id = str(updates.get("managed_connection_id") or "").strip() or None
    if "status" in updates:
        server.status = _normalize_status(updates.get("status"))
        flag_modified(server, "status")

    # OAuth credentials are issued for one protected resource and authorization
    # server. Never carry them across an endpoint or authentication-mode change;
    # doing so could send a bearer token to an unrelated host after an edit.
    if (
        previous_url != server.url
        or previous_auth_mode != str(server.auth_mode or AUTH_HEADERS)
    ) and "oauth" not in updates:
        server.oauth = {}

    # Validate the final object, rather than relying on update-field ordering.
    # PATCH requests may change transport without explicitly changing auth_mode.
    if server.transport == TRANSPORT_STDIO and server.auth_mode != AUTH_HEADERS:
        raise HTTPException(status_code=400, detail="Stdio MCP servers cannot use OAuth.")
    if server.transport == TRANSPORT_STDIO and (
        server.owner_type != OWNER_USER
        or not str(server.managed_connection_id or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Stdio MCP transport is reserved for Omlorix-managed connections.",
        )
    server.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(server)
    # Configuration changes invalidate the process-local listener snapshot.
    # A subsequent discovery starts a fresh listener only when appropriate.
    try:
        from app.mcp.utils import stop_mcp_subscription_listener

        stop_mcp_subscription_listener(server.id)
    except Exception:
        pass
    return server


def delete_mcp_server(db, server_id: str) -> None:
    server = get_mcp_server(db, server_id)
    deleted_server_id = server.id
    # Redirect states deliberately have no database foreign key so migrations
    # work across the supported schemas. Remove them explicitly with the server.
    db.query(MCPOAuthState).filter(MCPOAuthState.server_id == server.id).delete(
        synchronize_session=False
    )
    db.delete(server)
    db.commit()
    # A listener is process-local and may still hold an open transport after the
    # database row is gone. Stop it explicitly instead of waiting for network IO.
    try:
        from app.mcp.utils import stop_mcp_subscription_listener

        stop_mcp_subscription_listener(deleted_server_id)
    except Exception:
        pass


def list_mcp_servers(db, *, owner_type: str | None = None, owner_user_id: str | None = None, enabled_only: bool = False, include_managed: bool = True) -> list[MCPServer]:
    query = db.query(MCPServer)
    if owner_type:
        query = query.filter(MCPServer.owner_type == str(owner_type).strip().lower())
    if owner_user_id is not None:
        query = query.filter(MCPServer.owner_user_id == str(owner_user_id).strip())
    if enabled_only:
        query = query.filter(MCPServer.enabled.is_(True))
    if not include_managed:
        query = query.filter(MCPServer.managed_connection_id.is_(None))
    return query.order_by(MCPServer.name.asc(), MCPServer.created_at.asc()).all()


def serialize_mcp_server(server: MCPServer) -> dict:
    status = deepcopy(server.status) if isinstance(server.status, dict) else {}
    payload = {
        "id": server.id,
        "owner_type": server.owner_type,
        "owner_user_id": server.owner_user_id,
        "name": server.name,
        "icon": server.icon or "",
        "description": server.description,
        "namespace": server.namespace,
        "transport": server.transport,
        "enabled": bool(server.enabled),
        "url": server.url,
        "allowed_tools": list(server.allowed_tools or []),
        "auth_mode": str(getattr(server, "auth_mode", AUTH_HEADERS) or AUTH_HEADERS),
        "timeout_seconds": int(server.timeout_seconds or 30),
        "status": status,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
    }
    header_count = len(server.headers or {}) if isinstance(server.headers, dict) else 0
    payload["headers"] = {}
    payload["secret_summary"] = {
        "header_count": header_count,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
        "secrets_included": False,
        "oauth_connected": bool(
            isinstance(getattr(server, "oauth", None), dict) and server.oauth.get("access_token")
        ),
        "oauth_issuer": str((getattr(server, "oauth", None) or {}).get("issuer") or "").strip() or None,
    }
    return payload


def serialize_mcp_server_export(server: MCPServer) -> dict:
    """Serialize the portable, remote-only MCP configuration contract.

    Runtime status, encrypted secrets, and the internal stdio representation
    are deliberately absent. The source ID is a non-secret portability
    reference used to remap automation selections when the server is recreated
    with a new ID during restore.
    """

    return {
        "id": server.id,
        "name": server.name,
        "icon": server.icon or "",
        "description": server.description,
        "namespace": server.namespace,
        "transport": server.transport,
        "enabled": bool(server.enabled),
        "url": server.url,
        "headers": {},
        "auth_mode": str(getattr(server, "auth_mode", AUTH_HEADERS) or AUTH_HEADERS),
        "allowed_tools": list(server.allowed_tools or []),
        "timeout_seconds": int(server.timeout_seconds or 30),
    }
