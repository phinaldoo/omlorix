from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import hashlib
import hmac
import json
import logging
import math
import mimetypes
import re
import secrets
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import ValidationError

from app.connections.google import (
    GOOGLE_PROVIDER_CAPABILITIES,
    GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY,
)
from app.auth.jwt_material import get_jwt_material
from app.connections.models import UserConnection
from app.connections.policy import group_allows_connection_provider
from app.database import SessionLocal
from app.groups.init import get_user_group_setting_value
from app.mcp.models import (
    MCPServer,
    OWNER_ADMIN,
    OWNER_USER,
    TRANSPORT_SSE,
    TRANSPORT_STDIO,
    create_mcp_server,
    delete_mcp_server,
    get_mcp_server,
    list_mcp_servers,
    serialize_mcp_server,
    serialize_mcp_server_export,
    update_mcp_server,
)
from app.mcp.schemas import CreateMCPServerRequest, UpdateMCPServerRequest
from app.network.policy import (
    OutboundRequestBlockedError,
    assert_public_http_url_allowed,
    assert_url_allowed,
)
from app.network.outbound_http import public_async_httpx2_transport
from app.redis_client import get_redis_client


logger = logging.getLogger(__name__)

current_admin_mcp_server_export_version = 2.0

ALLOW_ALL_USER_MCPS = "__allow_all_user_mcps__"
CONNECTION_PROVIDER_MCP_PREFIX = "__connection_provider__:"
_DISCOVERY_TTL_SECONDS = 300
_DISCOVERY_CACHE_MAX_ENTRIES = 512
_DISCOVERY_CACHE: dict[str, dict[str, Any]] = {}
_DISCOVERY_CACHE_LOCK = threading.RLock()
_SUBSCRIPTION_LISTENERS: dict[str, dict[str, Any]] = {}
_SUBSCRIPTION_LISTENERS_LOCK = threading.RLock()
_MAX_SUBSCRIPTION_LISTENERS = 128
_NOTION_STREAMABLE_HOST = "mcp.notion.com"
_NOTION_SSE_PATH = "/sse"
_STREAMABLE_HTTP_NOTION_FALLBACK_ERRORS = (
    "405 method not allowed",
    "attempted to exit cancel scope in a different task",
)
_MCP_APPS_PROTOCOL_VERSION = "2026-01-26"
_MCP_APPS_RESOURCE_MIME_TYPES = {
    "text/html;profile=mcp-app",
    "text/html+skybridge",
}
_MCP_APP_TOKEN_VERSION = 1
_MCP_APP_TOKEN_TTL_SECONDS = 600
_MCP_APP_TOKEN_REFRESH_WINDOW_SECONDS = 24 * 60 * 60
_MCP_APP_FRAME_TTL_SECONDS = 300
_MCP_APP_FRAME_CACHE_MAX_ENTRIES = 128
_MCP_APP_SANDBOX_PROXY_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{border:0;height:100%;margin:0;overflow:hidden;padding:0}
iframe{border:0;display:block;height:100%;width:100%}
</style>
</head>
<body>
<script>
(function () {
    'use strict';
    var view = null;

    function send(message) {
        parent.postMessage(message, '*');
    }

    addEventListener('message', function (event) {
        var message = event.data;
        if (!message || typeof message !== 'object' || message.jsonrpc !== '2.0') {
            return;
        }

        if (event.source === parent) {
            if (message.method === 'ui/notifications/sandbox-resource-ready') {
                var params = message.params || {};
                var url = String(params.url || '');
                if (!url) {
                    return;
                }
                if (view) {
                    view.remove();
                }
                view = document.createElement('iframe');
                view.setAttribute('sandbox', String(params.sandbox || 'allow-scripts'));
                view.setAttribute('allow', String(params.allow || 'fullscreen *'));
                view.setAttribute('title', String(params.title || 'MCP App'));
                view.src = url;
                document.body.appendChild(view);
                return;
            }
            if (view && view.contentWindow) {
                view.contentWindow.postMessage(message, '*');
            }
            return;
        }

        if (view && event.source === view.contentWindow) {
            var method = String(message.method || '');
            if (method.indexOf('ui/notifications/sandbox-') === 0) {
                return;
            }
            send(message);
        }
    });

    send({
        jsonrpc: '2.0',
        method: 'ui/notifications/sandbox-proxy-ready',
        params: {}
    });
})();
</script>
</body>
</html>
"""
_MCP_APP_FRAME_CACHE_MAX_BYTES = 64 * 1024 * 1024
_MCP_APP_FRAME_REDIS_PREFIX = "mcp:app:frame:"
_MCP_APP_CONSUMED_JTIS: dict[str, int] = {}
_MCP_APP_CONSUMED_JTIS_LOCK = threading.Lock()
_MCP_APP_FRAME_CACHE: dict[str, dict[str, Any]] = {}
_MCP_APP_FRAME_CACHE_LOCK = threading.Lock()
_MCP_MAX_PAGES = 100
_MCP_MAX_LIST_ITEMS = 5_000
_MCP_MAX_TOOL_RESULT_BYTES = 16 * 1024 * 1024
_MCP_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MCP_MAX_ATTACHMENTS = 20
_MCP_MAX_RESOURCE_CONTENTS = 100
_MCP_MAX_RESOURCE_TEXT_BYTES = 5_000_000


class MCPHTTPAuthenticationError(RuntimeError):
    """Represent an MCP HTTP authentication rejection without exposing secrets.

    The MCP SDK normally converts a 401/403 response into an internal JSON-RPC
    error.  When that happens inside an anyio task group, the useful HTTP status
    is hidden behind ``ExceptionGroup`` and the UI receives only a generic task
    group string.  Keeping the status and challenge on a small local exception
    lets the runtime produce a stable, user-safe message while preserving the
    original exception for server-side diagnostics.
    """

    def __init__(self, status_code: int, challenge: str = "") -> None:
        self.status_code = int(status_code)
        self.response = SimpleNamespace(
            status_code=self.status_code,
            headers={"WWW-Authenticate": str(challenge or "")} if challenge else {},
        )
        super().__init__(f"MCP server rejected authentication (HTTP {self.status_code}).")


class MCPResourceContentError(ValueError):
    """Represent a bounded resource-content rejection after a successful read."""

    def __init__(self, message: str, code: str) -> None:
        self.code = str(code or "mcp_resource_content_unsupported")
        super().__init__(message)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _packaged_stdio_commands() -> set[str]:
    from app.connections.google import GOOGLE_WORKSPACE_MCP_COMMAND

    return {
        GOOGLE_WORKSPACE_MCP_COMMAND,
    }


def _stdio_launch_parameters(server: MCPServer) -> tuple[str, list[str]]:
    """Return launch parameters only for an Omlorix-managed packaged worker.

    Public MCP configuration supports remote transports exclusively. Stdio is
    retained as a private implementation detail for connection-backed workers
    such as Google Workspace, and cannot be enabled through environment flags.
    """
    command = str(getattr(server, "command", None) or "").strip()
    args = [str(item) for item in (getattr(server, "args", None) or [])]
    owner_type = str(getattr(server, "owner_type", None) or "").strip().lower()
    managed_connection_id = str(getattr(server, "managed_connection_id", None) or "").strip()
    if command not in _packaged_stdio_commands() or owner_type != OWNER_USER or not managed_connection_id:
        raise ValueError("Only Omlorix-managed packaged stdio workers may be launched.")
    if args:
        raise ValueError("Omlorix-managed packaged stdio workers do not accept configurable arguments.")
    return command, []


def _slugify(value: str | None, fallback: str = "mcp") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    cleaned = []
    last_was_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            last_was_sep = False
            continue
        if char in {"_", "-", ".", " ", "/"} and not last_was_sep:
            cleaned.append("_")
            last_was_sep = True
    result = "".join(cleaned).strip("_")
    return result[:32] or fallback


def _server_namespace(server: MCPServer) -> str:
    return _slugify(getattr(server, "namespace", None) or getattr(server, "name", None), fallback="mcp")


def _build_public_tool_name(server: MCPServer, tool_name: str) -> str:
    """Build a stable, collision-resistant provider tool name.

    Namespaces are user-editable and therefore cannot be treated as unique.
    Including a short server-id digest prevents two otherwise identical MCP
    servers from shadowing each other during schema generation and execution.
    """
    namespace = _server_namespace(server)
    raw_tool_name = _slugify(tool_name, fallback="tool")
    digest = hashlib.sha256(f"{server.id}:{tool_name}".encode("utf-8")).hexdigest()[:8]
    prefix = "mcp_"
    suffix = f"_{digest}"
    base = f"{prefix}{namespace}_{raw_tool_name}{suffix}"
    if len(base) <= 64:
        return base
    name_budget = 64 - len(prefix) - len(suffix) - 1
    trimmed_namespace = namespace[: min(32, name_budget - 1)]
    trimmed_tool = raw_tool_name[: name_budget - len(trimmed_namespace)]
    return f"{prefix}{trimmed_namespace}_{trimmed_tool}{suffix}"


_MCP_MAX_SCHEMA_DEPTH = 64
_MCP_MAX_SCHEMA_NODES = 20_000
_MCP_MAX_SCHEMA_BYTES = 1_000_000


def _sanitize_schema(value: Any, *, force_schema: bool = False) -> Any:
    """Validate and copy a bounded JSON Schema document without rewriting it.

    MCP 2026 permits composition, conditionals, definitions, and unrestricted
    output schemas. Older Omlorix code inserted object types into nested schemas
    and removed ``$id``/``$schema``, changing their meaning. This validator now
    preserves every JSON keyword while deliberately never dereferencing schema
    resources over the network. Internal fragment references remain intact;
    external and relative references are rejected because Omlorix has no trusted
    operator-provided schema registry. Validation is performed against the
    schema's declared dialect, defaulting to JSON Schema 2020-12 as required by
    MCP. ``force_schema`` remains only as a source-compatible argument for
    internal callers from older releases.
    """
    del force_schema
    nodes_seen = 0

    def _copy_json(item: Any, depth: int) -> Any:
        nonlocal nodes_seen
        nodes_seen += 1
        if nodes_seen > _MCP_MAX_SCHEMA_NODES:
            raise ValueError("MCP tool schema contains too many values.")
        if depth > _MCP_MAX_SCHEMA_DEPTH:
            raise ValueError("MCP tool schema exceeds the maximum nesting depth.")
        if isinstance(item, dict):
            copied: dict[str, Any] = {}
            for raw_key, child in item.items():
                if not isinstance(raw_key, str):
                    raise ValueError("MCP tool schema keys must be strings.")
                if raw_key in {"$ref", "$dynamicRef"} and isinstance(child, str):
                    # MCP forbids automatic network dereferencing. Omlorix has no
                    # operator-provided schema registry, so only references that
                    # resolve within the current document can be validated
                    # safely; reject external/relative resources explicitly.
                    if child and not child.startswith("#"):
                        raise ValueError(
                            "MCP tool schema contains an unresolved external reference."
                        )
                copied[raw_key] = _copy_json(child, depth + 1)
            return copied
        if isinstance(item, list):
            return [_copy_json(child, depth + 1) for child in item]
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("MCP tool schema must contain finite JSON numbers.")
        if item is None or isinstance(item, (str, int, float, bool)):
            return deepcopy(item)
        raise ValueError("MCP tool schema must contain only JSON values.")

    sanitized = _copy_json(value, 0)
    encoded_size = len(
        json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    if encoded_size > _MCP_MAX_SCHEMA_BYTES:
        raise ValueError("MCP tool schema exceeds the maximum allowed size.")

    # Import lazily because schema discovery is the only MCP path that needs the
    # validator. ``validator_for`` honors an explicit supported dialect, while
    # the explicit lookup prevents silently treating an unknown dialect as the
    # default validator.
    from jsonschema import validators
    from jsonschema.exceptions import SchemaError

    dialect = sanitized.get("$schema") if isinstance(sanitized, dict) else None
    if dialect:
        validator_type = validators.validator_for(sanitized, default=None)
        if validator_type is None:
            raise ValueError(f"MCP tool schema uses an unsupported dialect: {dialect}")
    else:
        from jsonschema import Draft202012Validator

        validator_type = Draft202012Validator
    try:
        validator_type.check_schema(sanitized)
    except SchemaError as exc:
        path = "/".join(str(part) for part in exc.path)
        location = f" at {path}" if path else ""
        raise ValueError(f"MCP tool schema is invalid{location}: {exc.message}") from exc
    return sanitized


def _decode_mcp_blob(value: Any, *, strict: bool = False) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if value is None:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    # Base64 expands binary data by roughly 4/3. Reject oversized payloads
    # before decoding so an untrusted MCP server cannot force a large
    # allocation in every API worker.
    if len(text) > ((_MCP_MAX_ATTACHMENT_BYTES + 2) // 3) * 4:
        raise ValueError("MCP attachment exceeds the maximum allowed size.")
    padding = "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text + padding, validate=strict)
    except Exception:
        return None


def _classify_mcp_attachment(mime_type: str, raw_type: str) -> str:
    """Map an MCP attachment to Omlorix's semantic attachment categories.

    SVG is an XML text document for model-context purposes. This exception must
    precede the generic ``image/*`` branch so MCP output follows the same path
    as user uploads and files generated by code execution.
    """
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    normalized_type = str(raw_type or "").strip().lower()
    if normalized_mime == "image/svg+xml":
        return "document"
    if normalized_mime.startswith("image/") or normalized_type == "image":
        return "image"
    if normalized_mime.startswith("audio/") or normalized_type == "audio":
        return "audio"
    if normalized_mime.startswith("video/") or normalized_type == "video":
        return "video"
    return "document"


def _guess_mcp_attachment_name(kind: str, mime_type: str, item: Any, index: int) -> str:
    resource = getattr(item, "resource", None)
    for attr_name in ("file_name", "filename", "name", "title"):
        value = getattr(item, attr_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if resource is not None:
            resource_value = getattr(resource, attr_name, None)
            if isinstance(resource_value, str) and resource_value.strip():
                return resource_value.strip()
    extension = mimetypes.guess_extension(mime_type or "") or {
        "image": ".png",
        "audio": ".mp3",
        "video": ".mp4",
        "document": ".bin",
    }.get(kind, ".bin")
    return f"mcp_{kind}_{index}{extension}"


def _normalize_mcp_attachment(item: Any, index: int) -> dict[str, Any] | None:
    raw_type = str(getattr(item, "type", None) or "").strip().lower()
    resource = getattr(item, "resource", None)
    mime_type = str(
        getattr(item, "mimeType", None)
        or getattr(item, "mime_type", None)
        or getattr(resource, "mimeType", None)
        or getattr(resource, "mime_type", None)
        or ""
    ).strip().lower()
    if not mime_type and raw_type not in {"image", "audio", "video", "resource"}:
        return None
    binary_value = None
    for attr_name in ("data", "blob", "base64"):
        binary_value = _decode_mcp_blob(getattr(item, attr_name, None))
        if binary_value:
            break
        if resource is not None:
            binary_value = _decode_mcp_blob(getattr(resource, attr_name, None))
        if binary_value:
            break
    if not binary_value:
        return None
    kind = _classify_mcp_attachment(mime_type, raw_type)
    file_name = _guess_mcp_attachment_name(kind, mime_type, item, index)
    return {
        "kind": kind,
        "mime_type": mime_type or "application/octet-stream",
        "file_name": file_name,
        "source_type": raw_type or kind,
        "data": binary_value,
    }


def _persist_mcp_attachments(db, user_id: str | None, attachments: list[dict[str, Any]]) -> dict[str, list[str]]:
    persisted = {
        "images": [],
        "videos": [],
        "audios": [],
        "documents": [],
    }
    if not user_id or not attachments:
        return persisted
    from app.files.utils import (
        get_file_category,
        persist_generated_file_bytes,
        resolve_user_file_upload_limits,
    )

    # MCP-generated files consume the same storage as uploads. Resolve and pass
    # the group limits so tool output cannot bypass upload disablement, file
    # count limits, or the user's aggregate storage quota.
    try:
        max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(
            db,
            str(user_id),
        )
    except HTTPException:
        # The remote action has already completed. Do not turn a successful
        # external mutation into a retry-prone tool failure merely because its
        # optional files cannot be stored locally.
        logger.info("MCP attachments omitted by user file-storage policy user=%s", user_id)
        return persisted

    target_map = {
        "image": "images",
        "video": "videos",
        "audio": "audios",
        "document": "documents",
    }
    for attachment in attachments:
        file_bytes = attachment.get("data")
        if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
            continue
        mime_type = str(attachment.get("mime_type") or "application/octet-stream").strip().lower()
        category = get_file_category(mime_type)
        if category == "unknown":
            category = str(attachment.get("kind") or "document").strip().lower()
        if category not in target_map:
            category = "document"
        original_name = str(attachment.get("file_name") or "").strip() or _guess_mcp_attachment_name(category, mime_type, attachment, 1)
        try:
            file_record = persist_generated_file_bytes(
                db,
                user_id=str(user_id),
                original_filename=original_name,
                file_bytes=bytes(file_bytes),
                file_type=mime_type,
                file_category=category,
                meta={
                    "original_filename": original_name,
                    "origin": "assistant",
                    "mcp": True,
                    "mime_type": mime_type,
                    "mcp_source_type": attachment.get("source_type"),
                },
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_user_storage_limit_bytes,
            )
        except HTTPException:
            logger.info("MCP attachment omitted by user file-storage quota user=%s", user_id)
            continue
        persisted[target_map[category]].append(file_record.id)
    return persisted


def _format_mcp_attachment_summary(persisted: dict[str, list[str]]) -> str:
    labels = {
        "images": "image",
        "videos": "video",
        "audios": "audio",
        "documents": "document",
    }
    parts: list[str] = []
    for key, label in labels.items():
        count = len(persisted.get(key) or [])
        if count <= 0:
            continue
        suffix = "" if count == 1 else "s"
        parts.append(f"{count} {label}{suffix}")
    if not parts:
        return ""
    return "Returned attachments: " + ", ".join(parts) + "."

@asynccontextmanager
async def _open_stdio_client(server: MCPServer):
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    launch_command, launch_args = _stdio_launch_parameters(server)
    params = StdioServerParameters(
        command=launch_command,
        args=launch_args,
        env=dict(server.env or {}),
    )
    # Client(mode="auto") performs server/discover for 2026-era servers and
    # transparently falls back to initialize for older stdio implementations.
    async with _new_mcp_client(stdio_client(params), server) as client:
        yield client


def _server_timeout_seconds(server: MCPServer) -> float:
    try:
        timeout_seconds = float(getattr(server, "timeout_seconds", 30) or 30)
    except (TypeError, ValueError):
        timeout_seconds = 30.0
    return max(timeout_seconds, 1.0)


def _new_mcp_client(transport, server: MCPServer):
    """Create one MCP v2 client with modern negotiation and legacy fallback.

    Ordinary tool/resource/prompt operations use request-scoped connections.
    The SDK cache is therefore disabled here; Omlorix's process cache persists
    across those short client lifetimes and applies the server's advertised
    TTL below. A separate bounded listener handles server subscriptions.
    """
    from mcp.client import Client, advertise
    from mcp.types import Implementation

    from app.mcp.tasks import TasksClientExtension

    async def decline_elicitation(_context, _params):
        """Answer MRTR elicitation safely when no interactive form is attached.

        Omlorix tool calls execute inside an LLM provider request, so there is no
        safe way to pause that request and collect new secrets or form values.
        MCP explicitly permits clients/users to decline. Returning the typed
        response lets a server continue or clean up instead of treating the
        request shape as unsupported.
        """
        from mcp.types import ElicitResult

        return ElicitResult(action="decline")

    async def list_no_roots(_context):
        """Expose no local roots; Omlorix never grants MCP filesystem access."""
        from mcp.types import ListRootsResult

        return ListRootsResult(roots=[])

    return Client(
        transport,
        mode="auto",
        client_info=Implementation(name="Omlorix", version="1.0"),
        read_timeout_seconds=_server_timeout_seconds(server),
        elicitation_callback=decline_elicitation,
        list_roots_callback=list_no_roots,
        extensions=[
            advertise(
                "io.modelcontextprotocol/ui",
                {"mimeTypes": ["text/html;profile=mcp-app"]},
            ),
            TasksClientExtension(),
        ],
        cache=None,
    )


def _mcp_request_meta() -> dict[str, str] | None:
    """Inject the active W3C trace context into MCP request metadata.

    OpenTelemetry's global propagator is configured by Omlorix's telemetry
    bootstrap. When tracing is disabled or there is no active span, ``inject``
    leaves the carrier empty and the request remains unchanged.
    """
    try:
        from opentelemetry.propagate import inject

        carrier: dict[str, str] = {}
        inject(carrier)
    except Exception:
        logger.debug("Unable to inject MCP trace context", exc_info=True)
        return None
    allowed_keys = {"traceparent", "tracestate", "baggage"}
    metadata = {
        key: value
        for key, value in carrier.items()
        if key.lower() in allowed_keys and isinstance(value, str) and value
    }
    return metadata or None


def _should_ignore_streamable_http_teardown_error(server: MCPServer, exc: Exception) -> bool:
    parsed = urlparse(str(getattr(server, "url", "") or ""))
    if parsed.netloc.lower() != _NOTION_STREAMABLE_HOST:
        return False
    message = str(exc).strip().lower()
    return "attempted to exit cancel scope in a different task" in message


def _assert_mcp_url_allowed(server: MCPServer, *, url: str | None = None, feature: str) -> None:
    session = SessionLocal()
    try:
        target_url = url or getattr(server, "url", None)
        if getattr(server, "owner_type", None) == OWNER_USER:
            assert_public_http_url_allowed(
                session,
                url=target_url,
                feature=feature,
            )
        else:
            assert_url_allowed(
                session,
                url=target_url,
                feature=feature,
            )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    finally:
        session.close()


def _mcp_request_policy_hook(server: MCPServer, *, feature: str):
    async def _hook(request):
        _assert_mcp_url_allowed(server, url=str(request.url), feature=feature)

    return _hook


async def _mcp_authentication_response_hook(response) -> None:
    """Capture HTTP auth failures before the MCP SDK hides their status.

    Only the status code and the standard authentication challenge are retained;
    response bodies can contain provider data and must not become connection
    status text or chat output.
    """
    if int(getattr(response, "status_code", 0) or 0) not in {401, 403}:
        return
    headers = getattr(response, "headers", {})
    challenge = ""
    try:
        challenge = str(headers.get("WWW-Authenticate") or "")
    except Exception:
        challenge = ""
    raise MCPHTTPAuthenticationError(int(response.status_code), challenge)


def _mcp_public_httpx2_transport(server: MCPServer, *, feature: str):
    """Return the SSRF-hardened httpx2 transport for user-owned MCP URLs."""
    if getattr(server, "owner_type", None) != OWNER_USER:
        return None
    return public_async_httpx2_transport(feature=feature)


def _mcp_httpx2_client_factory(server: MCPServer, *, feature: str):
    """Build the client factory expected by MCP v2's legacy SSE transport."""
    import httpx2

    request_hook = _mcp_request_policy_hook(server, feature=feature)

    def _factory(headers=None, timeout=None, auth=None):
        public_transport = _mcp_public_httpx2_transport(server, feature=feature)
        return httpx2.AsyncClient(
            headers=headers,
            timeout=timeout or httpx2.Timeout(
                _server_timeout_seconds(server),
                read=max(_server_timeout_seconds(server), 300.0),
            ),
            auth=auth,
            follow_redirects=True,
            transport=public_transport,
            trust_env=public_transport is None,
            event_hooks={
                "request": [request_hook],
                "response": [_mcp_authentication_response_hook],
            },
        )

    return _factory


@asynccontextmanager
async def _open_streamable_http_client(server: MCPServer):
    import httpx2
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    _assert_mcp_url_allowed(server, feature="MCP HTTP transport")
    headers = dict(server.headers or {})
    timeout = _server_timeout_seconds(server)
    client_connected = False
    public_transport = _mcp_public_httpx2_transport(server, feature="MCP HTTP transport")
    try:
        async with httpx2.AsyncClient(
            headers=headers,
            follow_redirects=True,
            # The SDK owns request timeouts. A longer transport read timeout
            # keeps an otherwise healthy SSE response stream from being cut at
            # httpx2's five-second default.
            timeout=httpx2.Timeout(timeout, read=max(timeout, 300.0)),
            transport=public_transport,
            trust_env=public_transport is None,
            event_hooks={
                "request": [_mcp_request_policy_hook(server, feature="MCP HTTP transport")],
                "response": [_mcp_authentication_response_hook],
            },
        ) as http_client:
            transport = streamable_http_client(
                server.url,
                http_client=http_client,
            )
            async with _new_mcp_client(transport, server) as client:
                client_connected = True
                yield client
    except Exception as exc:
        if client_connected and _should_ignore_streamable_http_teardown_error(server, exc):
            logger.warning(
                "MCP streamable HTTP cleanup failed for %s; ignoring known shutdown error=%s",
                getattr(server, "id", None),
                exc,
            )
            return
        if client_connected:
            raise
        if not _should_fallback_streamable_http_to_sse(server, exc):
            raise
        fallback_url = _fallback_sse_url(server.url)
        _assert_mcp_url_allowed(
            server,
            url=fallback_url,
            feature="MCP SSE fallback transport",
        )
        logger.warning(
            "MCP streamable HTTP failed for %s; retrying with SSE url=%s error=%s",
            getattr(server, "id", None),
            fallback_url,
            exc,
        )
        fallback_transport = sse_client(
            fallback_url,
            headers=headers,
            timeout=timeout,
            httpx_client_factory=_mcp_httpx2_client_factory(
                server,
                feature="MCP SSE fallback transport",
            ),
        )
        async with _new_mcp_client(fallback_transport, server) as client:
            yield client


@asynccontextmanager
async def _open_sse_client(server: MCPServer):
    from mcp.client.sse import sse_client

    _assert_mcp_url_allowed(server, feature="MCP SSE transport")
    transport = sse_client(
        server.url,
        headers=dict(server.headers or {}),
        timeout=_server_timeout_seconds(server),
        httpx_client_factory=_mcp_httpx2_client_factory(
            server,
            feature="MCP SSE transport",
        ),
    )
    async with _new_mcp_client(transport, server) as client:
        yield client


@asynccontextmanager
async def _mcp_session(server: MCPServer):
    if server.transport == TRANSPORT_STDIO:
        async with _open_stdio_client(server) as session:
            yield session
            return
    if server.transport == TRANSPORT_SSE:
        async with _open_sse_client(server) as session:
            yield session
            return
    async with _open_streamable_http_client(server) as session:
        yield session
        return


def _should_fallback_streamable_http_to_sse(server: MCPServer, exc: Exception) -> bool:
    parsed = urlparse(str(getattr(server, "url", "") or ""))
    if parsed.netloc.lower() != _NOTION_STREAMABLE_HOST:
        return False
    message = str(exc).strip().lower()
    return any(token in message for token in _STREAMABLE_HTTP_NOTION_FALLBACK_ERRORS)


def _fallback_sse_url(url: str | None) -> str:
    from app.connections.notion import NOTION_MCP_SSE_URL

    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return NOTION_MCP_SSE_URL
    return parsed._replace(path=_NOTION_SSE_PATH, params="", query="", fragment="").geturl()


async def _collect_paginated_items(
    method,
    *,
    item_attribute: str,
    timeout_seconds: float,
    alternate_item_attribute: str | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> list[Any]:
    """Collect a bounded MCP cursor-paginated result set.

    MCP SDK versions have used both camelCase and snake_case response fields.
    This helper accepts either shape and also bounds page traversal so a broken
    server cannot create an infinite cursor loop.
    """
    items: list[Any] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(_MCP_MAX_PAGES):
        awaitable = method(cursor=cursor) if cursor else method()
        response = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        if response_metadata is not None:
            page_ttl = (
                getattr(response, "ttl_ms", None)
                if hasattr(response, "ttl_ms")
                else getattr(response, "ttlMs", None)
            )
            if page_ttl is not None:
                try:
                    normalized_ttl = max(float(page_ttl), 0.0)
                except (TypeError, ValueError):
                    normalized_ttl = 0.0
                previous_ttl = response_metadata.get("ttl_ms")
                response_metadata["ttl_ms"] = (
                    normalized_ttl
                    if previous_ttl is None
                    else min(float(previous_ttl), normalized_ttl)
                )

            page_scope = str(
                getattr(response, "cache_scope", None)
                if hasattr(response, "cache_scope")
                else getattr(response, "cacheScope", None)
                or ""
            ).strip().lower()
            if page_scope in {"public", "private"}:
                # A merged paginated result is private if any constituent page
                # is private. This prevents a later page from weakening the
                # aggregate cache policy advertised by an earlier page.
                previous_scope = response_metadata.get("cache_scope")
                response_metadata["cache_scope"] = (
                    "private"
                    if "private" in {previous_scope, page_scope}
                    else "public"
                )
        page_items = getattr(response, item_attribute, None)
        if page_items is None and alternate_item_attribute:
            page_items = getattr(response, alternate_item_attribute, None)
        items.extend(list(page_items or []))
        if len(items) > _MCP_MAX_LIST_ITEMS:
            raise ValueError("MCP server returned too many list items.")
        next_cursor = str(
            getattr(response, "nextCursor", None)
            or getattr(response, "next_cursor", None)
            or ""
        ).strip()
        if not next_cursor:
            return items
        if next_cursor in seen_cursors:
            raise ValueError("MCP server returned a repeated pagination cursor.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("MCP server exceeded the maximum pagination depth.")


async def _discover_server_tools_async(
    server: MCPServer,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    async with _mcp_session(server) as session:
        response_metadata: dict[str, Any] = {}
        request_meta = _mcp_request_meta()

        async def list_tools_page(cursor=None):
            return await session.list_tools(cursor=cursor, meta=request_meta)

        items = await _collect_paginated_items(
            list_tools_page,
            item_attribute="tools",
            timeout_seconds=_server_timeout_seconds(server),
            response_metadata=response_metadata,
        )
        tools: list[dict[str, Any]] = []
        for item in items:
            payload = _model_to_dict(item)
            tool_name = str(getattr(item, "name", "") or "").strip()
            if not tool_name:
                continue
            description = str(getattr(item, "description", "") or "").strip() or None
            input_schema_value = getattr(item, "inputSchema", None)
            if input_schema_value is None:
                input_schema_value = getattr(item, "input_schema", None)
            if input_schema_value is None:
                input_schema_value = payload.get("inputSchema")
            if input_schema_value is None:
                input_schema_value = payload.get("input_schema")
            if input_schema_value is None:
                input_schema_value = {"type": "object", "properties": {}}
            input_schema = _sanitize_schema(input_schema_value)

            # Boolean schemas are valid JSON Schema. In particular, ``false``
            # is the deny-all schema and must not be replaced by a truthiness
            # fallback to the permissive empty schema.
            output_schema_value = getattr(item, "outputSchema", None)
            if output_schema_value is None:
                output_schema_value = getattr(item, "output_schema", None)
            if output_schema_value is None:
                output_schema_value = payload.get("outputSchema")
            if output_schema_value is None:
                output_schema_value = payload.get("output_schema")
            if output_schema_value is None:
                output_schema_value = {}
            output_schema = _sanitize_schema(output_schema_value)
            annotations = _to_jsonable(getattr(item, "annotations", None) or payload.get("annotations") or {})
            if not isinstance(annotations, dict):
                annotations = {}
            tool_ui = _extract_mcp_tool_ui_meta(item)
            tools.append(
                {
                    "tool_name": tool_name,
                    "description": description,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                    "annotations": annotations,
                    "meta": tool_ui.get("raw_meta") or {},
                    "ui": {
                        "resource_uri": tool_ui.get("resource_uri"),
                        "visibility": tool_ui.get("visibility") or ["model", "app"],
                    },
                    "title": tool_ui.get("title"),
                }
            )
        protocol_version = str(getattr(session, "protocol_version", None) or "")
        response_metadata["protocol_version"] = protocol_version
        # Legacy results have no cache hints. Preserve Omlorix's historical
        # five-minute private cache for those servers, while modern servers'
        # explicit ttlMs (including zero) always wins.
        if protocol_version != "2026-07-28":
            response_metadata["ttl_ms"] = _DISCOVERY_TTL_SECONDS * 1000
            response_metadata["cache_scope"] = "private"
        return tools, response_metadata


async def _list_server_resources_async(server: MCPServer) -> list[dict[str, Any]]:
    async with _mcp_session(server) as session:
        request_meta = _mcp_request_meta()

        async def list_resources_page(cursor=None):
            return await session.list_resources(cursor=cursor, meta=request_meta)

        items = await _collect_paginated_items(
            list_resources_page,
            item_attribute="resources",
            timeout_seconds=_server_timeout_seconds(server),
        )
        resources: list[dict[str, Any]] = []
        for item in items:
            payload = _model_to_dict(item)
            uri = str(
                payload.get("uri")
                or getattr(item, "uri", None)
                or ""
            ).strip()
            if not uri:
                continue
            resources.append(
                {
                    "uri": uri,
                    "name": str(payload.get("name") or getattr(item, "name", None) or "").strip() or None,
                    "title": str(payload.get("title") or getattr(item, "title", None) or "").strip() or None,
                    "description": str(payload.get("description") or getattr(item, "description", None) or "").strip() or None,
                    "mime_type": str(payload.get("mimeType") or payload.get("mime_type") or getattr(item, "mimeType", None) or getattr(item, "mime_type", None) or "").strip() or None,
                    "size": payload.get("size") or getattr(item, "size", None),
                    "meta": _extract_mcp_resource_ui_meta(item),
                }
            )
        return resources


def _is_mcp_app_mime_type(mime_type: str | None) -> bool:
    normalized = str(mime_type or "").strip().lower()
    mime_essence = normalized.split(";", 1)[0].strip()
    app_mime_essences = {
        value.split(";", 1)[0].strip()
        for value in _MCP_APPS_RESOURCE_MIME_TYPES
    }
    return (
        normalized in _MCP_APPS_RESOURCE_MIME_TYPES
        or mime_essence in app_mime_essences | {"application/xhtml+xml"}
    )


async def _read_server_resource_async(server: MCPServer, uri: str) -> dict[str, Any]:
    async with _mcp_session(server) as session:
        response = await asyncio.wait_for(
            session.read_resource(uri, meta=_mcp_request_meta()),
            timeout=_server_timeout_seconds(server),
        )
        items = getattr(response, "contents", None) or []
        if len(items) > _MCP_MAX_RESOURCE_CONTENTS:
            raise MCPResourceContentError(
                "MCP resource returned too many content blocks.",
                "mcp_resource_too_many_contents",
            )
        for item in items:
            payload = _model_to_dict(item)
            mime_type = str(
                payload.get("mimeType")
                or payload.get("mime_type")
                or getattr(item, "mimeType", None)
                or getattr(item, "mime_type", None)
                or ""
            ).strip() or None
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                text = payload.get("text")
            blob_value = None
            for attr_name in ("blob", "base64", "data"):
                raw_value = getattr(item, attr_name, None)
                if raw_value is None:
                    raw_value = payload.get(attr_name)
                if raw_value is None:
                    continue
                encoded_value = str(raw_value or "").strip()
                if encoded_value.startswith("data:") and "," in encoded_value:
                    encoded_value = encoded_value.split(",", 1)[1]
                if not encoded_value:
                    blob_value = b""
                    break
                try:
                    blob_value = _decode_mcp_blob(raw_value, strict=True)
                except ValueError as exc:
                    raise MCPResourceContentError(
                        "MCP resource exceeds the maximum allowed size.",
                        "mcp_resource_too_large",
                    ) from exc
                if blob_value is not None:
                    break
            if not isinstance(text, str) and blob_value is not None and (
                _is_mcp_app_mime_type(mime_type)
                or str(uri or "").strip().lower().startswith("ui://")
            ):
                try:
                    text = blob_value.decode("utf-8")
                except UnicodeDecodeError:
                    text = blob_value.decode("utf-8", errors="replace")
            if not isinstance(text, str) and blob_value is None:
                continue
            text_size = len(text.encode("utf-8")) if isinstance(text, str) else 0
            blob_size = len(blob_value) if blob_value is not None else 0
            if max(text_size, blob_size) > _MCP_MAX_RESOURCE_TEXT_BYTES:
                raise MCPResourceContentError(
                    "MCP resource exceeds the maximum allowed size.",
                    "mcp_resource_too_large",
                )
            return {
                "uri": str(payload.get("uri") or getattr(item, "uri", None) or uri).strip() or uri,
                "name": str(payload.get("name") or getattr(item, "name", None) or "").strip() or None,
                "title": str(payload.get("title") or getattr(item, "title", None) or "").strip() or None,
                "mime_type": mime_type,
                "text": text if isinstance(text, str) else None,
                "blob": base64.b64encode(blob_value).decode("ascii") if blob_value is not None else None,
                "meta": _extract_mcp_resource_ui_meta(item),
            }

        raise MCPResourceContentError(
            "MCP resource did not return supported text or binary content.",
            "mcp_resource_content_unsupported",
        )


async def _list_server_resource_templates_async(server: MCPServer) -> dict[str, Any]:
    async with _mcp_session(server) as session:
        if not hasattr(session, "list_resource_templates"):
            return {"resourceTemplates": []}
        request_meta = _mcp_request_meta()

        async def list_resource_templates_page(cursor=None):
            return await session.list_resource_templates(cursor=cursor, meta=request_meta)

        items = await _collect_paginated_items(
            list_resource_templates_page,
            item_attribute="resourceTemplates",
            alternate_item_attribute="resource_templates",
            timeout_seconds=_server_timeout_seconds(server),
        )
        return {"resourceTemplates": [_to_jsonable(item) for item in items]}


async def _list_server_prompts_async(server: MCPServer) -> dict[str, Any]:
    async with _mcp_session(server) as session:
        if not hasattr(session, "list_prompts"):
            return {"prompts": []}
        request_meta = _mcp_request_meta()

        async def list_prompts_page(cursor=None):
            return await session.list_prompts(cursor=cursor, meta=request_meta)

        items = await _collect_paginated_items(
            list_prompts_page,
            item_attribute="prompts",
            timeout_seconds=_server_timeout_seconds(server),
        )
        return {"prompts": [_to_jsonable(item) for item in items]}


async def _call_server_tool_async(server: MCPServer, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _mcp_session(server) as session:
        request_meta = _mcp_request_meta()
        if str(getattr(session, "protocol_version", "") or "") == "2026-07-28":
            # MCP v2's x-mcp-header parameters are materialized into
            # Mcp-Param-* transport headers by the SDK, but only after the
            # current client has absorbed the tool's schema. Connections are
            # intentionally request-scoped, so the earlier discovery client is
            # gone; list every bounded page on this session before execution.
            async def list_tools_page(cursor=None):
                return await session.list_tools(cursor=cursor, meta=request_meta)

            await _collect_paginated_items(
                list_tools_page,
                item_attribute="tools",
                timeout_seconds=_server_timeout_seconds(server),
            )
        # The SDK applies ``read_timeout_seconds`` to each protocol round trip.
        # Do not wrap the whole operation in one ordinary request timeout:
        # Tasks may legitimately poll far longer and enforce their own TTL-based
        # deadline in the Tasks extension resolver.
        result = await session.call_tool(
            tool_name,
            arguments or {},
            read_timeout_seconds=_server_timeout_seconds(server),
            meta=request_meta,
        )

    content_items = getattr(result, "content", None) or []
    if len(content_items) > _MCP_MAX_ATTACHMENTS + 100:
        raise ValueError("MCP tool result contains too many content blocks.")
    # Do not use truthiness here: MCP 2026 explicitly permits scalar and falsy
    # JSON values such as false, 0, an empty string, or an empty list.
    structured_content_value = getattr(result, "structured_content", None)
    if structured_content_value is None:
        structured_content_value = getattr(result, "structuredContent", None)
    # Result metadata is client-application data in MCP. Preserve it for the
    # MCP Apps host while keeping it out of the text returned to the model.
    result_meta = _extract_meta_object(result)
    text_parts: list[str] = []
    structured_parts: list[Any] = []
    attachments: list[dict[str, Any]] = []
    attachment_bytes = 0
    for index, item in enumerate(content_items, start=1):
        text_value = getattr(item, "text", None)
        if isinstance(text_value, str) and text_value.strip():
            text_parts.append(text_value)
            continue
        attachment = _normalize_mcp_attachment(item, index)
        if attachment is not None:
            if len(attachments) >= _MCP_MAX_ATTACHMENTS:
                raise ValueError("MCP tool result contains too many attachments.")
            attachment_bytes += len(attachment.get("data") or b"")
            if attachment_bytes > _MCP_MAX_TOOL_RESULT_BYTES:
                raise ValueError("MCP tool attachments exceed the maximum allowed size.")
            attachments.append(attachment)
            structured_parts.append(
                {
                    "type": attachment["kind"],
                    "file_name": attachment["file_name"],
                    "mime_type": attachment["mime_type"],
                }
            )
            continue
        data_value = getattr(item, "data", None)
        if data_value is not None:
            structured_parts.append(data_value)
            continue
        if hasattr(item, "model_dump"):
            structured_parts.append(item.model_dump())
            continue
        if hasattr(item, "dict"):
            structured_parts.append(item.dict())
            continue
        structured_parts.append(str(item))

    payload: dict[str, Any] = {
        "is_error": bool(getattr(result, "isError", False) or getattr(result, "is_error", False)),
        "raw": {
            "content": structured_parts if structured_parts else None,
        },
    }
    if structured_content_value is not None:
        payload["raw"]["structured_content"] = structured_content_value
    if result_meta:
        payload["raw"]["meta"] = result_meta
        payload["meta"] = result_meta
    if text_parts:
        payload["text"] = "\n\n".join(part for part in text_parts if part)
    if structured_content_value is not None:
        payload["structured_content"] = structured_content_value
    elif structured_parts:
        payload["structured_content"] = structured_parts
    if attachments:
        payload["attachments"] = attachments
    encoded_size = len(json.dumps(_to_jsonable(payload), ensure_ascii=False, default=str).encode("utf-8"))
    if encoded_size > _MCP_MAX_TOOL_RESULT_BYTES:
        raise ValueError("MCP tool result exceeds the maximum allowed size.")
    return payload


def _run_async(coro):
    """Run a coroutine from synchronous code, including inside a live loop.

    ``asyncio.run`` cannot be nested. Some MCP call sites are reached from
    async FastAPI/provider code through a synchronous compatibility boundary,
    so a short-lived worker thread owns the secondary event loop in that case.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[Any] = []
    failure: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=runner, name="mcp-async-bridge")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0] if result else None


def _iter_exception_tree(exc: BaseException) -> Iterable[BaseException]:
    """Yield an exception and its causes, including nested task-group errors.

    Python's ``ExceptionGroup`` is the reason provider HTTP status codes were
    previously lost. Walking both group children and regular cause/context
    links keeps all MCP error handling on the same safe path.
    """
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        yield current

        children = getattr(current, "exceptions", None)
        if isinstance(children, tuple):
            pending.extend(reversed([child for child in children if isinstance(child, BaseException)]))
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)


def _mcp_exception_status(exc: BaseException) -> int:
    """Return the first HTTP status embedded in an MCP exception tree."""
    for current in _iter_exception_tree(exc):
        response = getattr(current, "response", None)
        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code:
            return status_code
        try:
            status_code = int(getattr(current, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code:
            return status_code
    return 0


def _oauth_step_up_scopes_from_exception(exc: BaseException) -> list[str]:
    """Extract a bounded RFC 6750 insufficient-scope challenge, if present."""
    for current in _iter_exception_tree(exc):
        response = getattr(current, "response", None)
        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
            challenge = str(response.headers.get("WWW-Authenticate") or "")
        except Exception:
            status_code = 0
            challenge = ""
        if status_code == 403 and "insufficient_scope" in challenge.lower():
            try:
                from mcp.client.auth.utils import extract_scope_from_www_auth

                raw_scope = extract_scope_from_www_auth(response)
            except Exception:
                raw_scope = None
            return list(
                dict.fromkeys(
                    scope
                    for scope in str(raw_scope or "").split()
                    if scope and len(scope) <= 256
                )
            )[:100]
    return []


def _mcp_error_details(db, server: MCPServer, exc: BaseException) -> tuple[str, str]:
    """Convert an MCP failure into a safe message and stable frontend code.

    Provider tokens and remote response bodies never belong in a connection
    status or chat transcript. Known authentication statuses receive an
    actionable message. Every unknown failure receives a generic fallback;
    arbitrary provider and SDK exception text remains in server-side logs only.
    """
    server_name = str(getattr(server, "name", "MCP server") or "MCP server").strip()
    server_name = server_name[:120] or "MCP server"
    managed_connection_id = str(getattr(server, "managed_connection_id", "") or "").strip()
    provider = ""
    if managed_connection_id:
        try:
            provider = str(
                _managed_connection_provider_map(db, [server]).get(managed_connection_id) or ""
            ).strip().lower()
        except Exception:
            # Error rendering must not replace the original connection failure
            # with a database lookup failure.
            provider = ""

    status_code = _mcp_exception_status(exc)
    if status_code == 401 and provider == "github":
        return (
            "GitHub token is invalid or expired. Reconnect GitHub with a new token.",
            "github_token_invalid",
        )
    if status_code == 403 and provider == "github":
        return (
            "GitHub denied this token. Check its permissions, organization approval, or SSO authorization.",
            "github_access_denied",
        )
    if status_code == 401:
        return (
            f"{server_name} authentication failed. Reconnect the connection and try again.",
            "mcp_authentication_failed",
        )
    if status_code == 403:
        return (
            f"{server_name} denied access. Check the connection permissions.",
            "mcp_access_denied",
        )

    # Exception messages are attacker-controlled at the MCP boundary. Even a
    # redaction helper cannot enumerate every credential format, provider
    # response, internal URL, or tenant detail a remote server may include.
    # The calling paths already log the original exception with a traceback,
    # so the user-visible and persisted surfaces deliberately stay static.
    return (
        f"Could not connect to {server_name}. Check the connection credentials and try again.",
        "mcp_connection_failed",
    )


def _record_oauth_step_up_scopes(db, server: MCPServer, scopes: Iterable[str]) -> None:
    """Persist the union needed by the next user-approved OAuth reconnect."""
    if str(getattr(server, "auth_mode", "") or "") != "oauth":
        return
    oauth = deepcopy(server.oauth if isinstance(server.oauth, dict) else {})
    pending = oauth.get("pending_scopes")
    existing_pending = pending if isinstance(pending, list) else []
    oauth["pending_scopes"] = list(
        dict.fromkeys(
            [
                *str(oauth.get("scope") or "").split(),
                *(str(item or "").strip() for item in existing_pending),
                *(str(item or "").strip() for item in scopes),
            ]
        )
    )
    oauth["pending_scopes"] = [item for item in oauth["pending_scopes"] if item]
    server.oauth = oauth
    db.add(server)
    db.commit()
    db.refresh(server)


def _set_server_status(
    db,
    server: MCPServer,
    *,
    available: str,
    tools: Iterable[dict[str, Any]] | None = None,
    error: str = "",
    error_code: str = "",
) -> None:
    from app.llmstats.models import sanitize_provider_error_message

    if not getattr(server, "id", None):
        return
    tool_names = []
    if tools:
        tool_names = [str(item.get("tool_name") or "").strip() for item in tools if isinstance(item, dict)]
        tool_names = [name for name in tool_names if name]
    status = {
        "available": available,
        "checked_at": _utcnow().isoformat(),
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "last_error": sanitize_provider_error_message(error),
        # Error codes are stable, non-sensitive identifiers used by the
        # frontend to localize provider-specific guidance.
        "last_error_code": str(error_code or "").strip()[:100],
    }
    # Availability is operational state, not a configuration edit. Persist it
    # without changing updated_at; otherwise every health check invalidates the
    # discovery cache and makes the advertised TTL ineffective.
    server.status = status
    db.add(server)
    db.commit()
    db.refresh(server)


def _prepare_server_for_runtime(db, server: MCPServer) -> MCPServer:
    managed_connection_id = str(getattr(server, "managed_connection_id", None) or "").strip()
    prepared_server = server
    if managed_connection_id:
        try:
            from app.connections.service import prepare_managed_mcp_server_for_runtime

            prepared_server = prepare_managed_mcp_server_for_runtime(db, server)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to prepare managed MCP server %s for runtime", getattr(server, "id", None))
            prepared_server = server

    # Generic remote-server OAuth is independent of connection-backed MCP
    # providers. It refreshes issuer-bound credentials and injects the bearer
    # token only into a runtime copy, so API serialization and exports can never
    # leak it through the ordinary headers field.
    from app.mcp.oauth import prepare_oauth_server_for_runtime

    return prepare_oauth_server_for_runtime(db, prepared_server)


def _discovery_cache_key(server: MCPServer) -> str:
    return f"{server.id}:{server.updated_at}"


def _prune_discovery_cache(now: float) -> None:
    """Discard expired and oldest discovery entries from the process cache."""
    with _DISCOVERY_CACHE_LOCK:
        expired = [key for key, value in _DISCOVERY_CACHE.items() if float(value.get("expires_at") or 0) <= now]
        for key in expired:
            _DISCOVERY_CACHE.pop(key, None)
        overflow = len(_DISCOVERY_CACHE) - _DISCOVERY_CACHE_MAX_ENTRIES
        if overflow > 0:
            oldest = sorted(
                _DISCOVERY_CACHE,
                key=lambda key: float(_DISCOVERY_CACHE[key].get("expires_at") or 0),
            )[:overflow]
            for key in oldest:
                _DISCOVERY_CACHE.pop(key, None)


def _invalidate_server_discovery_cache(server_id: str) -> None:
    """Evict every configuration generation cached for one MCP server."""
    prefix = f"{str(server_id or '').strip()}:"
    if prefix == ":":
        return
    with _DISCOVERY_CACHE_LOCK:
        for key in [candidate for candidate in _DISCOVERY_CACHE if candidate.startswith(prefix)]:
            _DISCOVERY_CACHE.pop(key, None)


def stop_mcp_subscription_listener(server_id: str) -> None:
    """Request shutdown of a process-local MCP change listener, if present."""
    normalized_id = str(server_id or "").strip()
    with _SUBSCRIPTION_LISTENERS_LOCK:
        entry = _SUBSCRIPTION_LISTENERS.pop(normalized_id, None)
    if not entry:
        return
    entry["stop_requested"].set()
    loop = entry.get("loop")
    stop_event = entry.get("stop_event")
    if loop is not None and stop_event is not None:
        try:
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            pass


async def _listen_for_mcp_changes(
    server_snapshot: Any,
    server_id: str,
    listener_key: str,
    stop_requested: threading.Event,
) -> None:
    """Keep one modern subscription open and invalidate discovery on events."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    with _SUBSCRIPTION_LISTENERS_LOCK:
        entry = _SUBSCRIPTION_LISTENERS.get(server_id)
        if entry is None or entry.get("key") != listener_key:
            return
        entry["loop"] = loop
        entry["stop_event"] = stop_event
    if stop_requested.is_set():
        stop_event.set()

    async with _mcp_session(server_snapshot) as client:
        async with client.listen(
            tools_list_changed=True,
            prompts_list_changed=True,
            resources_list_changed=True,
        ) as subscription:
            stop_task = asyncio.create_task(stop_event.wait())
            event_task = asyncio.create_task(anext(subscription))
            try:
                while True:
                    done, _pending = await asyncio.wait(
                        {stop_task, event_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_task in done:
                        break
                    try:
                        event_task.result()
                    except StopAsyncIteration:
                        break
                    _invalidate_server_discovery_cache(server_id)
                    event_task = asyncio.create_task(anext(subscription))
            finally:
                for task in (stop_task, event_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(stop_task, event_task, return_exceptions=True)


def _start_mcp_subscription_listener(
    persisted_server: MCPServer,
    runtime_server: Any,
    *,
    protocol_version: str,
) -> None:
    """Start a bounded daemon listener for a persisted modern MCP server."""
    if protocol_version != "2026-07-28":
        return
    try:
        from sqlalchemy import inspect as sqlalchemy_inspect

        if not sqlalchemy_inspect(persisted_server).persistent:
            return
    except Exception:
        return

    server_id = str(persisted_server.id)
    listener_key = _discovery_cache_key(persisted_server)
    values = {
        column.name: deepcopy(getattr(runtime_server, column.name))
        for column in MCPServer.__table__.columns
    }
    server_snapshot = SimpleNamespace(**values)
    stop_requested = threading.Event()

    with _SUBSCRIPTION_LISTENERS_LOCK:
        current = _SUBSCRIPTION_LISTENERS.get(server_id)
        if current and current.get("key") == listener_key and current["thread"].is_alive():
            return
        if current:
            stop_mcp_subscription_listener(server_id)
        if len(_SUBSCRIPTION_LISTENERS) >= _MAX_SUBSCRIPTION_LISTENERS:
            logger.debug("MCP subscription listener limit reached; relying on discovery TTL")
            return

        def runner() -> None:
            try:
                asyncio.run(
                    _listen_for_mcp_changes(
                        server_snapshot,
                        server_id,
                        listener_key,
                        stop_requested,
                    )
                )
            except Exception:
                # Subscriptions are an optional freshness optimization. Servers
                # may reject the method even on the modern protocol; TTL caching
                # remains the fallback and should not mark the server unhealthy.
                logger.debug("MCP subscription listener ended for %s", server_id, exc_info=True)
            finally:
                with _SUBSCRIPTION_LISTENERS_LOCK:
                    entry = _SUBSCRIPTION_LISTENERS.get(server_id)
                    if entry and entry.get("key") == listener_key:
                        _SUBSCRIPTION_LISTENERS.pop(server_id, None)

        thread = threading.Thread(
            target=runner,
            name=f"mcp-subscription-{server_id[:12]}",
            daemon=True,
        )
        _SUBSCRIPTION_LISTENERS[server_id] = {
            "key": listener_key,
            "thread": thread,
            "stop_requested": stop_requested,
        }
        thread.start()


def _model_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            dumped = value.dict()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    return {}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _to_jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _to_jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _extract_meta_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        meta = value.get("_meta")
        if isinstance(meta, dict):
            return dict(meta)
        meta = value.get("meta")
        if isinstance(meta, dict):
            return dict(meta)
        return dict(value)
    dumped = _model_to_dict(value)
    meta = dumped.get("_meta")
    if isinstance(meta, dict):
        return dict(meta)
    meta = dumped.get("meta")
    if isinstance(meta, dict):
        return dict(meta)
    attr_meta = getattr(value, "_meta", None)
    if isinstance(attr_meta, dict):
        return dict(attr_meta)
    attr_meta = getattr(value, "meta", None)
    if isinstance(attr_meta, dict):
        return dict(attr_meta)
    return {}


def _normalize_mcp_ui_csp(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    field_map = {
        "baseUriDomains": "baseUriDomains",
        "base_uri_domains": "baseUriDomains",
        "resourceDomains": "resourceDomains",
        "resource_domains": "resourceDomains",
        "connectDomains": "connectDomains",
        "connect_domains": "connectDomains",
        "frameDomains": "frameDomains",
        "frame_domains": "frameDomains",
    }
    normalized: dict[str, list[str]] = {}
    for raw_key, target_key in field_map.items():
        raw_val = value.get(raw_key)
        if raw_val is None:
            continue
        items = raw_val if isinstance(raw_val, list) else [raw_val]
        cleaned = [str(item or "").strip() for item in items if str(item or "").strip()]
        if cleaned:
            normalized[target_key] = cleaned
    return normalized


def _normalize_mcp_ui_permissions(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key in ("camera", "microphone", "geolocation", "clipboardWrite", "clipboard_write"):
        if value.get(key) is None:
            continue
        target_key = "clipboardWrite" if key == "clipboard_write" else key
        normalized[target_key] = {}
    return normalized


def _extract_mcp_tool_ui_meta(value: Any) -> dict[str, Any]:
    payload = _model_to_dict(value)
    meta = _extract_meta_object(value)
    ui_meta = meta.get("ui")
    if not isinstance(ui_meta, dict):
        ui_meta = {}

    resource_uri = (
        ui_meta.get("resourceUri")
        or meta.get("ui/resourceUri")
        or meta.get("openai/outputTemplate")
    )
    if not isinstance(resource_uri, str):
        resource_uri = ""
    resource_uri = resource_uri.strip()

    visibility = ui_meta.get("visibility")
    if not isinstance(visibility, list):
        visibility = []
    normalized_visibility = [str(item or "").strip() for item in visibility if str(item or "").strip()]

    if not normalized_visibility:
        openai_widget_accessible = meta.get("openai/widgetAccessible")
        openai_visibility = str(meta.get("openai/visibility") or "").strip().lower()
        if openai_widget_accessible is not False:
            normalized_visibility.append("app")
        if openai_visibility != "private":
            normalized_visibility.append("model")
    normalized_visibility = list(dict.fromkeys(normalized_visibility))

    return {
        "resource_uri": resource_uri or None,
        "visibility": normalized_visibility or ["model", "app"],
        "raw_meta": meta,
        "title": str(payload.get("title") or getattr(value, "title", None) or "").strip() or None,
    }


def _extract_mcp_resource_ui_meta(value: Any) -> dict[str, Any]:
    meta = _extract_meta_object(value)
    ui_meta = meta.get("ui")
    if not isinstance(ui_meta, dict):
        ui_meta = {}
    csp = _normalize_mcp_ui_csp(ui_meta.get("csp") or meta.get("openai/widgetCSP"))
    permissions = _normalize_mcp_ui_permissions(ui_meta.get("permissions"))
    domain = str(ui_meta.get("domain") or meta.get("openai/widgetDomain") or "").strip() or None
    prefers_border = ui_meta.get("prefersBorder")
    if prefers_border is None:
        openai_prefers_border = meta.get("openai/widgetPrefersBorder")
        if isinstance(openai_prefers_border, bool):
            prefers_border = openai_prefers_border

    normalized: dict[str, Any] = {}
    if csp:
        normalized["csp"] = csp
    if permissions:
        normalized["permissions"] = permissions
    if domain:
        normalized["domain"] = domain
    if isinstance(prefers_border, bool):
        normalized["prefersBorder"] = prefers_border
    return normalized


def _managed_server_required_capabilities(db, server: MCPServer) -> set[str] | None:
    """Return the Google capability boundary for a managed MCP server.

    Generic MCP servers do not carry Omlorix capability metadata and keep their
    normal discovery behavior. Gmail and Google Calendar are different because
    they share one worker executable, so the backing connection provider is the
    trusted source of which capability may be exposed.
    """
    managed_connection_id = str(getattr(server, "managed_connection_id", "") or "").strip()
    if not managed_connection_id:
        return None
    provider = _managed_connection_provider_map(db, [server]).get(managed_connection_id)
    required = GOOGLE_PROVIDER_CAPABILITIES.get(provider or "")
    if required is None:
        return None
    return {
        str(capability or "").strip().lower()
        for capability in required
        if str(capability or "").strip()
    }


def _tool_declared_capabilities(tool: dict[str, Any]) -> set[str]:
    """Read the capability declaration emitted by the bundled Google worker."""
    metadata = tool.get("meta") if isinstance(tool, dict) else None
    if not isinstance(metadata, dict):
        return set()
    raw_capabilities = metadata.get(GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY)
    if isinstance(raw_capabilities, str):
        raw_values = [raw_capabilities]
    elif isinstance(raw_capabilities, (list, tuple, set)):
        raw_values = raw_capabilities
    else:
        return set()
    return {
        str(capability or "").strip().lower()
        for capability in raw_values
        if str(capability or "").strip()
    }


def _filter_managed_google_workspace_tools(
    db,
    server: MCPServer,
    tools: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fail closed when a shared Google worker advertises another product.

    Applying this before caching or status persistence keeps model tools, MCP
    Apps, tool selectors, and connection health on the same provider-scoped
    tool set.
    """
    required_capabilities = _managed_server_required_capabilities(db, server)
    normalized_tools = [tool for tool in tools if isinstance(tool, dict)]
    if required_capabilities is None:
        return normalized_tools

    filtered_tools: list[dict[str, Any]] = []
    omitted_count = 0
    for tool in normalized_tools:
        if required_capabilities.intersection(_tool_declared_capabilities(tool)):
            filtered_tools.append(tool)
        else:
            omitted_count += 1
    if omitted_count:
        logger.warning(
            "Filtered %s incompatible Google Workspace MCP tools from server=%s",
            omitted_count,
            str(getattr(server, "id", "") or "").strip(),
        )
    return filtered_tools


def _ensure_managed_google_tool_is_exposed(
    db,
    server: MCPServer,
    tool_name: str,
) -> None:
    """Reject stale or direct calls to a tool outside the connection scope."""
    if _managed_server_required_capabilities(db, server) is None:
        return
    try:
        discovered_tools = discover_server_tools(db, server)
    except HTTPException as exc:
        from app.tools.errors import SafeToolExecutionError

        error_code = str((exc.headers or {}).get("X-Omlorix-MCP-Error-Code") or "mcp_tool_discovery_failed")
        error_message = str(exc.detail or "The MCP server's available tools could not be discovered.")
        raise SafeToolExecutionError(
            code=error_code,
            safe_message=error_message,
            detail=error_message,
            allow_same_response_retry=False,
        ) from exc
    available_tool_names = {
        str(tool.get("tool_name") or "").strip()
        for tool in discovered_tools
        if isinstance(tool, dict)
    }
    if str(tool_name or "").strip() not in available_tool_names:
        raise ValueError(
            f"Tool '{tool_name}' is not available for managed Google Workspace connection '{server.name}'."
        )


def discover_server_tools(db, server: MCPServer, *, use_cache: bool = True) -> list[dict[str, Any]]:
    persisted_server = server
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    if not bool(getattr(runtime_server, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"MCP server '{runtime_server.name}' is disabled.")
    now = _utcnow().timestamp()
    _prune_discovery_cache(now)
    cache_key = _discovery_cache_key(persisted_server)
    cached_tools: list[dict[str, Any]] | None = None
    with _DISCOVERY_CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(cache_key) if use_cache else None
        if cached and cached.get("expires_at", 0) > now:
            cached_tools = list(cached.get("tools") or [])
    if cached_tools is not None:
        return _filter_managed_google_workspace_tools(db, persisted_server, cached_tools)
    try:
        tools, cache_metadata = _run_async(_discover_server_tools_async(runtime_server))
        tools = _filter_managed_google_workspace_tools(db, persisted_server, tools)
        _set_server_status(db, persisted_server, available="up", tools=tools)
        # Recompute after persistence in case a database/ORM implementation
        # refreshes configuration timestamps as part of the status write.
        try:
            ttl_seconds = max(float(cache_metadata.get("ttl_ms") or 0) / 1000.0, 0.0)
        except (TypeError, ValueError):
            ttl_seconds = 0.0
        # Match the SDK's safety cap so an untrusted server cannot make stale
        # tool definitions effectively permanent in Omlorix.
        ttl_seconds = min(ttl_seconds, 24 * 60 * 60)
        cache_key = _discovery_cache_key(persisted_server)
        with _DISCOVERY_CACHE_LOCK:
            if ttl_seconds > 0:
                _DISCOVERY_CACHE[cache_key] = {
                    "expires_at": now + ttl_seconds,
                    "tools": tools,
                    "cache_scope": (
                        cache_metadata.get("cache_scope")
                        if cache_metadata.get("cache_scope") in {"private", "public"}
                        else "private"
                    ),
                }
            else:
                _DISCOVERY_CACHE.pop(cache_key, None)
            _prune_discovery_cache(now)
        if ttl_seconds > 0:
            _start_mcp_subscription_listener(
                persisted_server,
                runtime_server,
                protocol_version=str(cache_metadata.get("protocol_version") or ""),
            )
        return tools
    except Exception as exc:
        logger.warning("Failed to discover MCP tools for %s", persisted_server.id, exc_info=True)
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(
            db,
            persisted_server,
            available="down",
            error=error_message,
            error_code=error_code,
        )
        raise HTTPException(
            status_code=400,
            detail=error_message,
            headers={"X-Omlorix-MCP-Error-Code": error_code},
        ) from exc


def list_server_resources(db, server: MCPServer) -> list[dict[str, Any]]:
    persisted_server = server
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    if not bool(getattr(runtime_server, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"MCP server '{runtime_server.name}' is disabled.")
    try:
        resources = _run_async(_list_server_resources_async(runtime_server))
        _set_server_status(db, persisted_server, available="up")
        return resources
    except Exception as exc:
        logger.warning("Failed to list MCP resources for %s", persisted_server.id, exc_info=True)
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(db, persisted_server, available="down", error=error_message, error_code=error_code)
        raise HTTPException(
            status_code=400,
            detail=error_message,
            headers={"X-Omlorix-MCP-Error-Code": error_code},
        ) from exc


def read_server_resource(db, server: MCPServer, uri: str) -> dict[str, Any]:
    persisted_server = server
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    if not bool(getattr(runtime_server, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"MCP server '{runtime_server.name}' is disabled.")
    try:
        payload = _run_async(_read_server_resource_async(runtime_server, uri))
        _set_server_status(db, persisted_server, available="up")
        return payload
    except Exception as exc:
        logger.warning("Failed to read MCP resource %s on %s", uri, persisted_server.id, exc_info=True)
        content_error = next(
            (
                current
                for current in _iter_exception_tree(exc)
                if isinstance(current, MCPResourceContentError)
            ),
            None,
        )
        if content_error is not None:
            # The server completed the MCP request successfully. Content and
            # size-policy rejections must not masquerade as lost connectivity.
            _set_server_status(db, persisted_server, available="up")
            raise HTTPException(
                status_code=422,
                detail=str(content_error),
                headers={"X-Omlorix-MCP-Error-Code": content_error.code},
            ) from exc
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(db, persisted_server, available="down", error=error_message, error_code=error_code)
        raise HTTPException(
            status_code=400,
            detail=error_message,
            headers={"X-Omlorix-MCP-Error-Code": error_code},
        ) from exc


def list_server_resource_templates(db, server: MCPServer) -> dict[str, Any]:
    persisted_server = server
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    if not bool(getattr(runtime_server, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"MCP server '{runtime_server.name}' is disabled.")
    try:
        payload = _run_async(_list_server_resource_templates_async(runtime_server))
        _set_server_status(db, persisted_server, available="up")
        return payload if isinstance(payload, dict) else {"resourceTemplates": []}
    except Exception as exc:
        logger.warning("Failed to list MCP resource templates for %s", persisted_server.id, exc_info=True)
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(db, persisted_server, available="down", error=error_message, error_code=error_code)
        raise HTTPException(
            status_code=400,
            detail=error_message,
            headers={"X-Omlorix-MCP-Error-Code": error_code},
        ) from exc


def list_server_prompts(db, server: MCPServer) -> dict[str, Any]:
    persisted_server = server
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    if not bool(getattr(runtime_server, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"MCP server '{runtime_server.name}' is disabled.")
    try:
        payload = _run_async(_list_server_prompts_async(runtime_server))
        _set_server_status(db, persisted_server, available="up")
        return payload if isinstance(payload, dict) else {"prompts": []}
    except Exception as exc:
        logger.warning("Failed to list MCP prompts for %s", persisted_server.id, exc_info=True)
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(db, persisted_server, available="down", error=error_message, error_code=error_code)
        raise HTTPException(
            status_code=400,
            detail=error_message,
            headers={"X-Omlorix-MCP-Error-Code": error_code},
        ) from exc


def call_mcp_tool(db, server: MCPServer, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    persisted_server = server
    if not bool(getattr(persisted_server, "enabled", True)):
        raise ValueError(f"MCP server '{persisted_server.name}' is disabled.")
    if not _is_mcp_tool_allowed(persisted_server, tool_name):
        raise ValueError(f"Tool '{tool_name}' is not allowed for MCP server '{persisted_server.name}'.")
    _ensure_managed_google_tool_is_exposed(db, persisted_server, tool_name)
    runtime_server = _prepare_server_for_runtime(db, persisted_server)
    try:
        payload = _run_async(_call_server_tool_async(runtime_server, tool_name, arguments or {}))
        status = "warning" if payload.get("is_error") else "up"
        _set_server_status(db, persisted_server, available=status)
        return payload
    except Exception as exc:
        logger.warning("Failed to call MCP tool %s on %s", tool_name, persisted_server.id, exc_info=True)
        step_up_scopes = _oauth_step_up_scopes_from_exception(exc)
        if step_up_scopes:
            _record_oauth_step_up_scopes(db, persisted_server, step_up_scopes)
            _set_server_status(
                db,
                persisted_server,
                available="warning",
                error="OAuth permission upgrade required.",
                error_code="oauth_permission_upgrade_required",
            )
            raise ValueError(
                f"MCP server '{persisted_server.name}' requires additional OAuth permissions; reconnect OAuth and retry."
            ) from exc
        error_message, error_code = _mcp_error_details(db, persisted_server, exc)
        _set_server_status(
            db,
            persisted_server,
            available="down",
            error=error_message,
            error_code=error_code,
        )
        if error_code in {
            "github_token_invalid",
            "github_access_denied",
            "mcp_authentication_failed",
            "mcp_access_denied",
        }:
            from app.tools.errors import SafeToolExecutionError

            raise SafeToolExecutionError(
                code=error_code,
                safe_message=error_message,
                detail=error_message,
                allow_same_response_retry=False,
            ) from exc
        raise ValueError(f"MCP server '{persisted_server.name}' failed to execute '{tool_name}'.") from exc


def _ensure_group_mcp_enabled(user_id: str | None, db) -> bool:
    if not user_id:
        return False
    return bool(get_user_group_setting_value(user_id, "tools_mcp", "enable_mcp", db))


def require_group_mcp_enabled(user_id: str | None, db) -> None:
    """Enforce the group MCP feature gate for every user-facing MCP surface."""
    if not _ensure_group_mcp_enabled(user_id, db):
        raise HTTPException(status_code=403, detail="MCP integrations are disabled for your group.")


def list_accessible_mcp_servers(
    db,
    user_id: str | None,
    *,
    only_enabled: bool = True,
    model_settings: dict[str, Any] | None = None,
    access_server_ids: Iterable[str] | None = None,
) -> list[MCPServer]:
    """Return servers the caller may use before model-level narrowing.

    Administrator-owned servers are deployment capabilities. Once an admin
    assigns one to a model, every user of that model may use it. The group MCP
    toggle intentionally controls only personal servers created by that user.
    Managed workspace connections remain governed by their separate provider
    allow-list and are therefore not coupled to the personal-server toggle.
    """
    servers: list[MCPServer] = list_mcp_servers(
        db,
        owner_type=OWNER_ADMIN,
        enabled_only=only_enabled,
    )
    if not user_id:
        return servers

    personal_servers_enabled = _ensure_group_mcp_enabled(user_id, db)
    user_servers = list_mcp_servers(
        db,
        owner_type=OWNER_USER,
        owner_user_id=user_id,
        enabled_only=only_enabled,
    )
    # Managed servers are authorized through the connection provider policy,
    # not through the personal MCP toggle. Rechecking the provider here makes
    # group-policy revocation effective immediately for discovery, model tool
    # binding, MCP Apps, and execution paths that all consume this list.
    managed_provider_map = _managed_connection_provider_map(db, user_servers)
    llm_managed_providers = _all_managed_connection_provider_keys()
    for server in user_servers:
        managed_connection_id = str(getattr(server, "managed_connection_id", "") or "").strip()
        if not managed_connection_id:
            if personal_servers_enabled:
                servers.append(server)
            continue

        provider = managed_provider_map.get(managed_connection_id)
        # A stale database row must not turn a file-source adapter back into
        # an MCP server. The provider catalog is the authoritative boundary
        # for which managed connections are actually LLM-capable.
        if (
            provider
            and provider in llm_managed_providers
            and group_allows_connection_provider(user_id, db, provider=provider)
        ):
            servers.append(server)
    return servers


def _selected_server_ids(model_settings: dict[str, Any] | None) -> set[str]:
    """Return the explicit MCP server selection, if one was supplied.

    Keeping the values normalized here ensures a malformed value can never
    broaden MCP access.
    """
    if not isinstance(model_settings, dict):
        return set()
    raw_value = model_settings.get("enabled_mcp_servers")
    if raw_value is None:
        return set()
    if isinstance(raw_value, str):
        values = [raw_value]
    elif isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        return set()
    result: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text:
            result.add(text)
    return result


def _has_explicit_server_selection(model_settings: dict[str, Any] | None) -> bool:
    """Whether the request explicitly chose an MCP server set."""
    return (
        isinstance(model_settings, dict)
        and model_settings.get("enabled_mcp_servers") is not None
    )


def build_connection_provider_mcp_value(provider: str | None) -> str:
    return f"{CONNECTION_PROVIDER_MCP_PREFIX}{str(provider or '').strip().lower()}"


def parse_connection_provider_mcp_value(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text.startswith(CONNECTION_PROVIDER_MCP_PREFIX):
        return None
    provider = text[len(CONNECTION_PROVIDER_MCP_PREFIX):].strip()
    return provider or None


def _raw_allowed_model_mcp_entries(model_settings: dict[str, Any] | None) -> set[str]:
    if not isinstance(model_settings, dict):
        return set()
    raw_value = model_settings.get("allowed_mcp_servers")
    if raw_value is None:
        return set()
    if isinstance(raw_value, str):
        values = [raw_value]
    elif isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        return set()
    result: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text:
            result.add(text)
    return result


def _allowed_model_mcp_entries(model_settings: dict[str, Any] | None) -> set[str]:
    return {
        entry for entry in _raw_allowed_model_mcp_entries(model_settings)
        if entry != ALLOW_ALL_USER_MCPS
    }


def _all_managed_connection_provider_keys() -> set[str]:
    from app.connections.service import list_managed_connection_mcp_catalog

    return {
        str(item.get("provider") or "").strip().lower()
        for item in list_managed_connection_mcp_catalog()
        if str(item.get("provider") or "").strip()
    }


def get_model_allowed_mcp_selector_values(model_settings: dict[str, Any] | None) -> list[str]:
    values = set(_allowed_model_mcp_entries(model_settings))
    raw_entries = _raw_allowed_model_mcp_entries(model_settings)
    if ALLOW_ALL_USER_MCPS in raw_entries:
        values.update(build_connection_provider_mcp_value(provider) for provider in _all_managed_connection_provider_keys())
    values = list(values)
    values.sort()
    return values


def model_allows_custom_user_mcp_servers(model_settings: dict[str, Any] | None) -> bool:
    if isinstance(model_settings, dict) and "allow_custom_user_mcp_servers" in model_settings:
        return bool(model_settings.get("allow_custom_user_mcp_servers"))
    raw_entries = _raw_allowed_model_mcp_entries(model_settings)
    if not raw_entries:
        return True
    return ALLOW_ALL_USER_MCPS in raw_entries


def _allowed_model_connection_providers(model_settings: dict[str, Any] | None) -> set[str]:
    raw_entries = _raw_allowed_model_mcp_entries(model_settings)
    if ALLOW_ALL_USER_MCPS in raw_entries:
        return _all_managed_connection_provider_keys()
    providers: set[str] = set()
    llm_managed_providers = _all_managed_connection_provider_keys()
    for entry in _allowed_model_mcp_entries(model_settings):
        provider = parse_connection_provider_mcp_value(entry)
        if provider and provider in llm_managed_providers:
            providers.add(provider)
    return providers


def get_model_allowed_connection_providers(model_settings: dict[str, Any] | None) -> set[str]:
    """Return the managed connection providers that a model permits.

    An empty ``allowed_mcp_servers`` value intentionally means unrestricted in
    the model editor and in ``_filter_servers_for_settings``.  Keeping that
    legacy/default behavior here gives model-list consumers the same answer as
    MCP execution without exposing the underlying selector values or server
    identifiers to the frontend.
    """
    if not _raw_allowed_model_mcp_entries(model_settings):
        return _all_managed_connection_provider_keys()
    return _allowed_model_connection_providers(model_settings)


def _explicit_model_mcp_server_ids(model_settings: dict[str, Any] | None) -> set[str]:
    allowed_entries = _allowed_model_mcp_entries(model_settings)
    explicit_ids = {
        item for item in allowed_entries
        if parse_connection_provider_mcp_value(item) is None
    }
    explicit_ids.update(_selected_server_ids(model_settings))
    return explicit_ids


def _normalize_explicit_server_ids(access_server_ids: Iterable[str] | None) -> set[str]:
    result: set[str] = set()
    if access_server_ids is None:
        return result
    values = [access_server_ids] if isinstance(access_server_ids, str) else access_server_ids
    for item in values:
        text = str(item or "").strip()
        if text:
            result.add(text)
    return result


def _managed_connection_provider_map(db, servers: list[MCPServer]) -> dict[str, str]:
    managed_connection_ids = {
        str(getattr(server, "managed_connection_id", "") or "").strip()
        for server in servers
        if str(getattr(server, "managed_connection_id", "") or "").strip()
    }
    if not managed_connection_ids:
        return {}
    rows = (
        db.query(UserConnection.id, UserConnection.provider)
        .filter(UserConnection.id.in_(managed_connection_ids))
        .all()
    )
    result: dict[str, str] = {}
    for connection_id, provider in rows:
        normalized_id = str(connection_id or "").strip()
        normalized_provider = str(provider or "").strip().lower()
        if normalized_id and normalized_provider:
            result[normalized_id] = normalized_provider
    return result


def _filter_servers_for_settings(
    db,
    servers: list[MCPServer],
    model_settings: dict[str, Any] | None,
    *,
    apply_request_selection: bool = True,
) -> list[MCPServer]:
    """Apply model policy and, for runtime calls, the request allowlist.

    Discovery UIs pass ``apply_request_selection=False`` so they can display
    every eligible choice. Runtime callers use the secure default: a missing or
    empty request selection exposes no MCP server.
    """
    raw_allowed_entries = _raw_allowed_model_mcp_entries(model_settings)
    allowed_entries = _allowed_model_mcp_entries(model_settings)
    has_admin_or_connection_restriction = bool(raw_allowed_entries)
    allowed_admin_ids = {
        entry for entry in allowed_entries
        if parse_connection_provider_mcp_value(entry) is None
    }
    allowed_connection_providers = _allowed_model_connection_providers(model_settings)
    allow_custom_user_mcps = model_allows_custom_user_mcp_servers(model_settings)
    managed_connection_providers = _managed_connection_provider_map(db, servers) if servers else {}

    if has_admin_or_connection_restriction or not allow_custom_user_mcps:
        filtered_servers: list[MCPServer] = []
        for server in servers:
            if server.owner_type == OWNER_ADMIN:
                if not has_admin_or_connection_restriction or server.id in allowed_admin_ids:
                    filtered_servers.append(server)
                continue

            if server.owner_type != OWNER_USER:
                continue

            managed_connection_id = str(getattr(server, "managed_connection_id", "") or "").strip()
            if managed_connection_id:
                provider = managed_connection_providers.get(managed_connection_id)
                if not has_admin_or_connection_restriction or (provider and provider in allowed_connection_providers):
                    filtered_servers.append(server)
                continue

            if allow_custom_user_mcps:
                filtered_servers.append(server)
        servers = filtered_servers
    if not apply_request_selection:
        return servers
    if not _has_explicit_server_selection(model_settings):
        return []
    selected_ids = _selected_server_ids(model_settings)
    return [server for server in servers if server.id in selected_ids]


def get_mcp_server_options_for_user(
    db,
    user_id: str | None,
    *,
    model_settings: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return [
        {
            "value": server.id,
            # Ownership and backing details are implementation details. The
            # conversation selector presents every eligible integration as a
            # server, regardless of whether it is admin-, personal-, or
            # connection-backed.
            "label": f"{server.name} (Server)",
        }
        for server in _filter_servers_for_settings(
            db,
            list_accessible_mcp_servers(
                db,
                user_id,
                only_enabled=True,
                model_settings=model_settings,
            ),
            model_settings,
            apply_request_selection=False,
        )
    ]


def list_mcp_mention_connectors(
    db,
    user_id: str | None,
    *,
    model_settings: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return model-eligible MCP servers without exposing connection details.

    ``enabled_mcp_servers`` is deliberately removed before filtering. It is a
    request-scoped choice, while the mention menu must list every server the
    user may choose for the next request. Administrator allowlists, connection
    provider policy, group policy, ownership, and the server's enabled state
    still flow through the ordinary MCP authorization helpers.
    """
    if not user_id:
        return []

    eligibility_settings = dict(model_settings or {})
    eligibility_settings.pop("enabled_mcp_servers", None)
    servers = _filter_servers_for_settings(
        db,
        list_accessible_mcp_servers(
            db,
            user_id,
            only_enabled=True,
            model_settings=eligibility_settings,
        ),
        eligibility_settings,
        apply_request_selection=False,
    )
    managed_connection_providers = _managed_connection_provider_map(db, servers)
    return [
        {
            "id": str(server.id),
            "name": str(server.name or "MCP Server"),
            # Managed servers intentionally keep provider credentials and
            # backing connection IDs private. The stable provider key is safe
            # presentation metadata and lets chat reuse the exact icon shown
            # on the user's Connections workspace card.
            "provider": managed_connection_providers.get(
                str(getattr(server, "managed_connection_id", "") or "").strip(),
                "",
            ),
            "icon": str(server.icon or ""),
            "description": str(server.description or ""),
        }
        for server in servers
    ]


def build_mcp_bridge_tools(db, *, user_id: str | None, model_settings: dict[str, Any] | None) -> tuple[list[str], list[dict[str, Any]]]:
    if not user_id:
        return [], []
    servers = _filter_servers_for_settings(
        db,
        list_accessible_mcp_servers(
            db,
            user_id,
            only_enabled=True,
            model_settings=model_settings,
        ),
        model_settings,
    )
    public_names: list[str] = []
    tool_schemas: list[dict[str, Any]] = []
    for server in servers:
        try:
            tools = discover_server_tools(db, server)
        except HTTPException:
            continue
        for tool in tools:
            tool_name = str(tool.get("tool_name") or "").strip()
            if (
                not tool_name
                or not _is_mcp_tool_allowed(server, tool_name)
                or not _mcp_tool_visible_to(tool, "model")
                or _mcp_tool_requires_user_approval(tool)
            ):
                continue
            public_name = _build_public_tool_name(server, tool_name)
            description = tool.get("description") or ""
            server_prefix = f"[MCP: {server.name}]"
            if description:
                description = f"{server_prefix} {description}"[:2000]
            else:
                description = f"{server_prefix} Tool exposed by the connected MCP server."
            public_names.append(public_name)
            tool_schemas.append(
                {
                    "name": public_name,
                    "description": description,
                    "parameters": _sanitize_schema(tool.get("input_schema") or {"type": "object", "properties": {}}),
                }
            )
    return public_names, tool_schemas


def build_mcp_provider_bundle(db, *, provider: str, user_id: str | None, model_settings: dict[str, Any] | None) -> dict[str, Any]:
    """Build provider-neutral function tools backed by the Omlorix MCP bridge.

    MCP execution always stays inside Omlorix so admission policies, audit
    logging, attachment quotas, MCP Apps, and error redaction apply uniformly
    regardless of which model provider handles the conversation.
    """
    result = {
        "bridge_tool_names": [],
        "bridge_tool_schemas": [],
    }
    if not user_id:
        return result
    names, schemas = build_mcp_bridge_tools(
        db,
        user_id=user_id,
        model_settings=model_settings,
    )
    result["bridge_tool_names"] = names
    result["bridge_tool_schemas"] = schemas
    return result


def resolve_mcp_tool_binding(db, *, user_id: str | None, public_name: str, model_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user_id or not public_name:
        return None
    servers = _filter_servers_for_settings(
        db,
        list_accessible_mcp_servers(
            db,
            user_id,
            only_enabled=True,
            model_settings=model_settings,
        ),
        model_settings,
    )
    for server in servers:
        try:
            tools = discover_server_tools(db, server)
        except HTTPException:
            continue
        for tool in tools:
            tool_name = str(tool.get("tool_name") or "").strip()
            if (
                not tool_name
                or not _is_mcp_tool_allowed(server, tool_name)
                or not _mcp_tool_visible_to(tool, "model")
                or _mcp_tool_requires_user_approval(tool)
            ):
                continue
            if _build_public_tool_name(server, tool_name) == public_name:
                return {
                    "server": server,
                    "tool_name": tool_name,
                    "description": tool.get("description"),
                    "input_schema": tool.get("input_schema") or {},
                    "output_schema": tool.get("output_schema") or {},
                    "meta": deepcopy(tool.get("meta") or {}),
                    "ui": deepcopy(tool.get("ui") or {}),
                    "title": tool.get("title"),
                }
    return None


def _build_mcp_tool_result_content_blocks(result: dict[str, Any], *, text_fallback: str) -> list[dict[str, Any]]:
    raw_content = (((result or {}).get("raw") or {}).get("content") or [])
    blocks: list[dict[str, Any]] = []
    if isinstance(raw_content, list):
        for item in raw_content:
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type") or "").strip().lower()
            if block_type in {"text", "image", "audio", "resource", "resource_link"}:
                blocks.append(deepcopy(item))
    if blocks:
        return blocks
    if text_fallback:
        return [{"type": "text", "text": text_fallback}]
    return []


def _normalize_structured_content_for_mcp_app(value: Any) -> Any:
    """Preserve the unrestricted JSON value defined by MCP 2026."""
    if value is None:
        return None
    return deepcopy(value)


def _merge_mcp_resource_ui_meta(*values: dict[str, Any] | None) -> dict[str, Any]:
    """Merge UI metadata from MCP resource wrappers and nested resources."""
    merged: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if key == "csp" and isinstance(item, dict):
                csp = merged.setdefault("csp", {})
                if isinstance(csp, dict):
                    for csp_key, csp_values in item.items():
                        existing = csp.get(csp_key)
                        existing_list = existing if isinstance(existing, list) else ([] if existing is None else [existing])
                        next_list = csp_values if isinstance(csp_values, list) else [csp_values]
                        csp[csp_key] = list(
                            dict.fromkeys(
                                [
                                    str(candidate or "").strip()
                                    for candidate in [*existing_list, *next_list]
                                    if str(candidate or "").strip()
                                ]
                            )
                        )
                continue
            merged[key] = deepcopy(item)
    return merged


def _extract_embedded_mcp_app_resource_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Return embedded MCP app HTML and UI metadata from a tool result.

    Some MCP app servers return their UI as a resource block directly in the
    tool result instead of advertising a separate `ui://` output template. In
    that shape the resource block can also carry `_meta`/`openai/widgetCSP`.
    Keeping the metadata with the HTML is important because the browser iframe
    uses it to allow app-owned scripts, styles, fonts, and API endpoints.
    """
    raw_content = (((result or {}).get("raw") or {}).get("content") or [])
    if not isinstance(raw_content, list):
        return None

    for block in raw_content:
        if not isinstance(block, dict):
            continue

        resource = block.get("resource")
        block_ui_meta = _extract_mcp_resource_ui_meta(block)
        resource_mime = ""
        if isinstance(resource, dict):
            resource_ui_meta = _extract_mcp_resource_ui_meta(resource)
            resource_mime = str(
                resource.get("mimeType")
                or resource.get("mime_type")
                or ""
            ).strip().lower()
            resource_text = resource.get("text")
            if isinstance(resource_text, str) and resource_text and (
                _is_mcp_app_mime_type(resource_mime)
            ):
                return {
                    "text": resource_text,
                    "mime_type": resource_mime or None,
                    "meta": _merge_mcp_resource_ui_meta(block_ui_meta, resource_ui_meta),
                }
            for key in ("blob", "base64", "data"):
                decoded = _decode_mcp_blob(resource.get(key))
                if decoded and _is_mcp_app_mime_type(resource_mime):
                    try:
                        text = decoded.decode("utf-8")
                    except UnicodeDecodeError:
                        text = decoded.decode("utf-8", errors="replace")
                    return {
                        "text": text,
                        "mime_type": resource_mime or None,
                        "meta": _merge_mcp_resource_ui_meta(block_ui_meta, resource_ui_meta),
                    }

        block_mime = str(
            block.get("mimeType")
            or block.get("mime_type")
            or ""
        ).strip().lower()
        block_text = block.get("text")
        if isinstance(block_text, str) and block_text and (
            _is_mcp_app_mime_type(block_mime)
        ):
            return {
                "text": block_text,
                "mime_type": block_mime or None,
                "meta": _extract_mcp_resource_ui_meta(block),
            }
        for key in ("blob", "base64", "data"):
            decoded = _decode_mcp_blob(block.get(key))
            if decoded and _is_mcp_app_mime_type(block_mime):
                try:
                    text = decoded.decode("utf-8")
                except UnicodeDecodeError:
                    text = decoded.decode("utf-8", errors="replace")
                return {
                    "text": text,
                    "mime_type": block_mime or None,
                    "meta": _extract_mcp_resource_ui_meta(block),
                }

    return None


def _extract_embedded_html_from_mcp_result(result: dict[str, Any]) -> str | None:
    """Return embedded MCP app HTML from a tool result, if one exists."""
    resource = _extract_embedded_mcp_app_resource_from_result(result)
    if not isinstance(resource, dict):
        return None
    text = resource.get("text")
    return text if isinstance(text, str) and text else None


def _render_mcp_app_widget_shell(app_payload: dict[str, Any]) -> str:
    resource_title = str(
        app_payload.get("resource_title")
        or app_payload.get("tool_info", {}).get("title")
        or app_payload.get("tool_name")
        or "MCP App"
    ).strip() or "MCP App"
    subtitle = str(app_payload.get("server_name") or "").strip()
    safe_resource_title = escape(resource_title)
    safe_subtitle = escape(subtitle)
    return (
        '<div class="mcp-app-widget-shell" data-role="mcp-app-shell">'
        f'<div class="mcp-app-widget-shell-header"><span class="mcp-app-widget-shell-title">{safe_resource_title}</span>'
        f'<span class="mcp-app-widget-shell-subtitle">{safe_subtitle}</span></div>'
        '<div class="mcp-app-widget-shell-body">Loading interactive app…</div>'
        "</div>"
    )


def _mcp_app_token_secret(db) -> str:
    """Use environment-owned signing material for MCP app bridge tokens."""
    secret, _algorithm = get_jwt_material()
    return secret


def _base64url_json(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_base64url_json(value: str) -> dict[str, Any]:
    padded = value + ("=" * (-len(value) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MCP app token payload must be an object.")
    return payload


def _sign_mcp_app_token_payload(payload: dict[str, Any], secret: str) -> str:
    encoded_payload = _base64url_json(payload)
    signature = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def _mcp_app_token_jti_cache_key(jti: str) -> str:
    return f"mcp:app:token:consumed:{hashlib.sha256(jti.encode('utf-8')).hexdigest()}"


def _prune_local_mcp_app_consumed_jtis(now: int) -> None:
    expired = [jti for jti, expires_at in _MCP_APP_CONSUMED_JTIS.items() if expires_at <= now]
    for jti in expired:
        _MCP_APP_CONSUMED_JTIS.pop(jti, None)


def _mcp_app_token_was_consumed(jti: str, *, now: int | None = None) -> bool:
    normalized_jti = str(jti or "").strip()
    if not normalized_jti:
        return False
    client = get_redis_client()
    if client is not None:
        try:
            return bool(client.exists(_mcp_app_token_jti_cache_key(normalized_jti)))
        except Exception:
            logger.warning("Failed to check MCP app token replay cache", exc_info=True)
    current_time = int(now or time.time())
    with _MCP_APP_CONSUMED_JTIS_LOCK:
        _prune_local_mcp_app_consumed_jtis(current_time)
        return int(_MCP_APP_CONSUMED_JTIS.get(normalized_jti) or 0) > current_time


def _consume_mcp_app_access_token(token_payload: dict[str, Any]) -> None:
    jti = str(token_payload.get("jti") or "").strip()
    if not jti:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    try:
        exp = int(token_payload.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    now = int(time.time())
    ttl_seconds = max(exp - now, 1)
    client = get_redis_client()
    if client is not None:
        try:
            stored = client.set(_mcp_app_token_jti_cache_key(jti), "1", nx=True, ex=ttl_seconds)
            if not stored:
                raise HTTPException(status_code=403, detail="MCP app access token was already used.")
            return
        except HTTPException:
            raise
        except Exception:
            logger.warning("Failed to update MCP app token replay cache", exc_info=True)

    with _MCP_APP_CONSUMED_JTIS_LOCK:
        _prune_local_mcp_app_consumed_jtis(now)
        if int(_MCP_APP_CONSUMED_JTIS.get(jti) or 0) > now:
            raise HTTPException(status_code=403, detail="MCP app access token was already used.")
        _MCP_APP_CONSUMED_JTIS[jti] = now + ttl_seconds


def _build_mcp_app_access_token(
    db,
    *,
    user_id: str | None,
    server_id: str,
    resource_uri: str | None,
    tool_name: str,
    access_server_ids: Iterable[str] | None,
    tool_names: Iterable[str] | None = None,
    tool_call_id: str | None = None,
    original_issued_at: int | None = None,
) -> str:
    issued_at = int(time.time())
    scoped_tool_names = {
        str(item or "").strip()
        for item in (tool_names or [])
        if str(item or "").strip()
    }
    if str(tool_name or "").strip():
        scoped_tool_names.add(str(tool_name).strip())
    payload = {
        "v": _MCP_APP_TOKEN_VERSION,
        "user_id": str(user_id or ""),
        "server_id": str(server_id or ""),
        "resource_uri": str(resource_uri or ""),
        "tool_names": sorted(scoped_tool_names),
        "tool_call_id": str(tool_call_id or ""),
        "access_server_ids": sorted(_normalize_explicit_server_ids(access_server_ids) | {str(server_id or "")}),
        "iat": issued_at,
        "orig_iat": int(original_issued_at or issued_at),
        "exp": issued_at + _MCP_APP_TOKEN_TTL_SECONDS,
        "jti": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(16),
    }
    return _sign_mcp_app_token_payload(payload, _mcp_app_token_secret(db))


def _verify_mcp_app_access_token(
    db,
    *,
    user_id: str | None,
    server_id: str,
    app_access_token: str | None,
    tool_call_id: str | None = None,
    allow_expired: bool = False,
) -> dict[str, Any]:
    token = str(app_access_token or "").strip()
    if not token or "." not in token:
        raise HTTPException(status_code=403, detail="MCP app access token is required.")
    encoded_payload, encoded_signature = token.rsplit(".", 1)
    try:
        encoded_payload_bytes = encoded_payload.encode("ascii")
        encoded_signature.encode("ascii")
    except UnicodeEncodeError as exc:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.") from exc
    expected = hmac.new(
        _mcp_app_token_secret(db).encode("utf-8"),
        encoded_payload_bytes,
        hashlib.sha256,
    ).digest()
    expected_signature = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    if not hmac.compare_digest(encoded_signature, expected_signature):
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    try:
        payload = _decode_base64url_json(encoded_payload)
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.") from exc
    if payload.get("v") != _MCP_APP_TOKEN_VERSION:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    if str(payload.get("user_id") or "") != str(user_id or ""):
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    if str(payload.get("server_id") or "") != str(server_id or "").strip():
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    try:
        issued_at = int(payload.get("iat") or 0)
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.") from exc
    now = int(time.time())
    if issued_at <= 0 or expires_at <= issued_at:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    if expires_at <= now and not allow_expired:
        raise HTTPException(status_code=403, detail="MCP app access token expired.")
    if issued_at > now + 60:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    jti = str(payload.get("jti") or "").strip()
    nonce = str(payload.get("nonce") or "").strip()
    if not jti or not nonce:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    expected_tool_call_id = str(payload.get("tool_call_id") or "").strip()
    if expected_tool_call_id and expected_tool_call_id != str(tool_call_id or "").strip():
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.")
    if _mcp_app_token_was_consumed(jti, now=now):
        raise HTTPException(status_code=403, detail="MCP app access token was already used.")
    return payload


def _mcp_app_token_access_server_ids(token_payload: dict[str, Any]) -> set[str]:
    return _normalize_explicit_server_ids(token_payload.get("access_server_ids") or [])


def _mcp_app_token_resource_uri(token_payload: dict[str, Any]) -> str:
    return str(token_payload.get("resource_uri") or "").strip()


def _mcp_app_token_tool_names(token_payload: dict[str, Any]) -> set[str]:
    value = token_payload.get("tool_names")
    if not isinstance(value, list):
        return set()
    return {str(item or "").strip() for item in value if str(item or "").strip()}


def _authorize_mcp_app_bridge(
    db,
    *,
    user_id: str | None,
    server_id: str,
    app_access_token: str | None,
    tool_call_id: str | None = None,
) -> tuple[MCPServer, dict[str, Any]]:
    token_payload = _verify_mcp_app_access_token(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    token_server_ids = _mcp_app_token_access_server_ids(token_payload)
    normalized_id = str(server_id or "").strip()
    if normalized_id not in token_server_ids:
        raise HTTPException(status_code=403, detail="MCP app access token is not scoped to this server.")
    server = get_accessible_mcp_server_for_user(
        db,
        user_id=user_id,
        server_id=normalized_id,
        access_server_ids=token_server_ids,
    )
    return server, token_payload


def refresh_mcp_app_access_token_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    app_access_token: str | None,
    tool_call_id: str | None = None,
) -> dict[str, str]:
    """Refresh an MCP app token without broadening its original scope."""
    token_payload = _verify_mcp_app_access_token(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
        allow_expired=True,
    )
    token_server_ids = _mcp_app_token_access_server_ids(token_payload)
    normalized_id = str(server_id or "").strip()
    if normalized_id not in token_server_ids:
        raise HTTPException(status_code=403, detail="MCP app access token is not scoped to this server.")
    get_accessible_mcp_server_for_user(
        db,
        user_id=user_id,
        server_id=normalized_id,
        access_server_ids=token_server_ids,
    )
    try:
        original_issued_at = int(token_payload.get("orig_iat") or token_payload.get("iat") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Invalid MCP app access token.") from exc
    if original_issued_at + _MCP_APP_TOKEN_REFRESH_WINDOW_SECONDS <= int(time.time()):
        raise HTTPException(status_code=403, detail="MCP app access token can no longer be refreshed.")
    # Refresh is a rotation operation. Consuming the old JTI prevents callers
    # from minting an arbitrary number of parallel execution tokens.
    _consume_mcp_app_access_token(token_payload)
    tool_names = sorted(_mcp_app_token_tool_names(token_payload))
    return {
        "app_access_token": _build_mcp_app_access_token(
            db,
            user_id=user_id,
            server_id=normalized_id,
            resource_uri=_mcp_app_token_resource_uri(token_payload),
            tool_name="",
            access_server_ids=token_server_ids,
            tool_names=tool_names,
            tool_call_id=str(token_payload.get("tool_call_id") or tool_call_id or "").strip() or None,
            original_issued_at=original_issued_at,
        )
    }


_MCP_APP_CSP_KEYWORD_SOURCES = {"'self'", "'none'"}
_MCP_APP_CSP_SCHEME_SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*:$", re.IGNORECASE)
_MCP_APP_CSP_HOST_SOURCE_PATTERN = re.compile(
    r"^(?:(?:[a-z][a-z0-9+.-]*):\/\/)?"
    r"(?:\*|\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)*|(?:[a-z0-9-]+|\[[0-9a-f:.]+\])(?:\.[a-z0-9-]+)*)"
    r"(?::(?:\*|[0-9]{1,5}))?(?:\/[^\s;,\"'<>]*)?$",
    re.IGNORECASE,
)
_MCP_APP_CSP_META_PATTERN = re.compile(
    r"<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(?:\"content-security-policy\"|'content-security-policy'|content-security-policy))[^>]*>",
    re.IGNORECASE,
)


def _is_safe_mcp_app_csp_source(value: Any) -> bool:
    source = str(value or "").strip()
    if source in _MCP_APP_CSP_KEYWORD_SOURCES:
        return True
    if not source or re.search(r"[\s;,\"'<>]", source):
        return False
    return bool(
        _MCP_APP_CSP_SCHEME_SOURCE_PATTERN.match(source)
        or _MCP_APP_CSP_HOST_SOURCE_PATTERN.match(source)
    )


def _normalize_mcp_app_csp_sources(value: Any) -> list[str]:
    items = value if isinstance(value, list) else ([] if value is None else [value])
    cleaned: list[str] = []
    for item in items:
        source = str(item or "").strip()
        if source and _is_safe_mcp_app_csp_source(source) and source not in cleaned:
            cleaned.append(source)
    return cleaned


def _mcp_app_frame_csp_from_meta(meta: dict[str, Any] | None) -> str:
    """Build the CSP that governs the sandboxed MCP app document."""
    ui_meta = meta if isinstance(meta, dict) else {}
    csp = ui_meta.get("csp") if isinstance(ui_meta.get("csp"), dict) else {}
    resource_domains = _normalize_mcp_app_csp_sources(
        csp.get("resourceDomains") or csp.get("resource_domains")
    )
    connect_domains = _normalize_mcp_app_csp_sources(
        csp.get("connectDomains") or csp.get("connect_domains")
    )
    frame_domains = _normalize_mcp_app_csp_sources(
        csp.get("frameDomains") or csp.get("frame_domains")
    )
    base_uri_domains = _normalize_mcp_app_csp_sources(
        csp.get("baseUriDomains") or csp.get("base_uri_domains")
    )
    resource_src = " ".join(["'self'", "data:", "blob:", *resource_domains])
    script_src = " ".join(["'self'", "'unsafe-inline'", "blob:", *resource_domains])
    style_src = " ".join(["'self'", "'unsafe-inline'", *resource_domains])
    connect_src = " ".join(connect_domains) if connect_domains else "'none'"
    frame_src = " ".join(frame_domains) if frame_domains else "'none'"
    base_uri = " ".join(base_uri_domains) if base_uri_domains else "'none'"
    return "; ".join(
        [
            # Match the frontend iframe sandbox so direct navigation to the
            # short-lived frame URL keeps the same isolated-origin boundary.
            "sandbox allow-scripts allow-forms allow-popups allow-downloads",
            "default-src 'none'",
            "object-src 'none'",
            f"script-src {script_src}",
            f"style-src {style_src}",
            f"img-src {resource_src}",
            f"font-src {resource_src}",
            f"media-src {resource_src}",
            f"connect-src {connect_src}",
            f"frame-src {frame_src}",
            f"child-src {frame_src}",
            "worker-src blob:",
            "frame-ancestors 'self'",
            f"base-uri {base_uri}",
            "form-action 'none'",
        ]
    )


def _strip_mcp_app_html_csp_meta_tags(html: str) -> str:
    """Remove app-provided CSP meta tags so the host policy is authoritative."""
    return _MCP_APP_CSP_META_PATTERN.sub("", str(html or ""))


def _mcp_app_meta_csp_from_header_csp(csp: str) -> str:
    """Return the CSP subset that is valid in an HTML meta tag.

    Directives such as frame-ancestors and sandbox only work in HTTP headers.
    Keeping them in the response header preserves the protection, while
    removing them from the injected meta tag avoids noisy browser console
    warnings.
    """
    directives = []
    for directive in str(csp or "").split(";"):
        text = directive.strip()
        if not text:
            continue
        name = text.split(None, 1)[0].lower()
        if name in {"frame-ancestors", "sandbox"}:
            continue
        directives.append(text)
    return "; ".join(directives)


def _inject_mcp_app_head_markup(html: str, additions: str) -> str:
    source = str(html or "")
    if not source:
        return source
    head_match = re.search(r"<head[^>]*>", source, flags=re.IGNORECASE)
    if head_match:
        index = head_match.end()
        return f"{source[:index]}{additions}{source[index:]}"
    html_match = re.search(r"<html[^>]*>", source, flags=re.IGNORECASE)
    if html_match:
        index = html_match.end()
        return f"{source[:index]}<head>{additions}</head>{source[index:]}"
    return f"<head>{additions}</head>{source}"


def _build_mcp_app_frame_html(html: str, csp: str) -> str:
    html_without_app_csp = _strip_mcp_app_html_csp_meta_tags(html)
    meta_csp = _mcp_app_meta_csp_from_header_csp(csp)
    additions = (
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(meta_csp, quote=True)}">'
    )
    return _inject_mcp_app_head_markup(html_without_app_csp, additions)


def _prune_local_mcp_app_frames(now: int) -> None:
    expired_ids = [
        frame_id
        for frame_id, frame in _MCP_APP_FRAME_CACHE.items()
        if int(frame.get("expires_at") or 0) <= now
    ]
    for frame_id in expired_ids:
        _MCP_APP_FRAME_CACHE.pop(frame_id, None)


def _store_mcp_app_frame(frame_id: str, frame: dict[str, Any]) -> None:
    client = get_redis_client()
    if client is not None:
        try:
            client.setex(
                f"{_MCP_APP_FRAME_REDIS_PREFIX}{frame_id}",
                _MCP_APP_FRAME_TTL_SECONDS,
                json.dumps(frame, ensure_ascii=False),
            )
            return
        except Exception:
            logger.warning("Failed to store MCP app frame in Redis", exc_info=True)

    now = int(time.time())
    with _MCP_APP_FRAME_CACHE_LOCK:
        _prune_local_mcp_app_frames(now)
        frame_size = len(str(frame.get("html") or "").encode("utf-8"))
        cached_size = sum(
            len(str(item.get("html") or "").encode("utf-8"))
            for item in _MCP_APP_FRAME_CACHE.values()
        )
        while _MCP_APP_FRAME_CACHE and (
            len(_MCP_APP_FRAME_CACHE) >= _MCP_APP_FRAME_CACHE_MAX_ENTRIES
            or cached_size + frame_size > _MCP_APP_FRAME_CACHE_MAX_BYTES
        ):
            oldest_id = min(
                _MCP_APP_FRAME_CACHE,
                key=lambda key: int(_MCP_APP_FRAME_CACHE[key].get("expires_at") or 0),
            )
            removed = _MCP_APP_FRAME_CACHE.pop(oldest_id, None) or {}
            cached_size -= len(str(removed.get("html") or "").encode("utf-8"))
        _MCP_APP_FRAME_CACHE[frame_id] = {
            **frame,
            "expires_at": now + _MCP_APP_FRAME_TTL_SECONDS,
        }


def _load_mcp_app_frame(frame_id: str) -> dict[str, Any] | None:
    normalized_id = str(frame_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,160}", normalized_id):
        return None

    client = get_redis_client()
    if client is not None:
        try:
            raw = client.get(f"{_MCP_APP_FRAME_REDIS_PREFIX}{normalized_id}")
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else None
        except Exception:
            logger.warning("Failed to read MCP app frame from Redis", exc_info=True)

    now = int(time.time())
    with _MCP_APP_FRAME_CACHE_LOCK:
        _prune_local_mcp_app_frames(now)
        frame = _MCP_APP_FRAME_CACHE.get(normalized_id)
        return deepcopy(frame) if isinstance(frame, dict) else None


def create_mcp_app_frame_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    html: str,
    resource_meta: dict[str, Any] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, str]:
    """Create a short-lived iframe document for approved MCP app HTML."""
    _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    frame_id = secrets.token_urlsafe(32)
    csp = _mcp_app_frame_csp_from_meta(resource_meta if isinstance(resource_meta, dict) else {})
    frame_html = _build_mcp_app_frame_html(html, csp)
    _store_mcp_app_frame(
        frame_id,
        {
            "html": frame_html,
            "csp": csp,
        },
    )
    return {
        "frame_id": frame_id,
        "frame_url": f"/api/v1/llm/mcp/apps/frame/{frame_id}",
    }


def get_mcp_app_frame_payload(frame_id: str) -> dict[str, Any]:
    """Return a stored MCP app frame document and the headers it must carry."""
    frame = _load_mcp_app_frame(frame_id)
    if not isinstance(frame, dict):
        raise HTTPException(status_code=404, detail="MCP app frame expired.")
    html = str(frame.get("html") or "")
    csp = str(frame.get("csp") or "").strip()
    if not html or not csp:
        raise HTTPException(status_code=404, detail="MCP app frame expired.")
    return {
        "html": html,
        "headers": {
            "Content-Security-Policy": csp,
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "Expires": "0",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
        },
    }


def get_mcp_app_sandbox_proxy_payload() -> dict[str, Any]:
    """Return the trusted bridge document used to host an MCP app iframe.

    The proxy must be an ordinary HTTP document rather than a ``data:`` URL.
    Safari inherits the embedding page's CSP for data documents, so Omlorix's
    production ``script-src 'self'`` policy blocks an inline data-URL bootstrap
    before it can initialize the MCP Apps postMessage bridge.

    The outer proxy is trusted Omlorix code and may retain its same origin. The
    MCP server's untrusted HTML is loaded from a separate, short-lived frame URL
    into a nested iframe whose sandbox deliberately omits ``allow-same-origin``.
    """
    csp = "; ".join(
        [
            "default-src 'none'",
            "object-src 'none'",
            "script-src 'unsafe-inline'",
            "style-src 'unsafe-inline'",
            "img-src 'none'",
            "font-src 'none'",
            "media-src 'none'",
            "connect-src 'none'",
            "frame-src 'self'",
            "child-src 'self'",
            "worker-src 'none'",
            "frame-ancestors 'self'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )
    return {
        "html": _MCP_APP_SANDBOX_PROXY_HTML,
        "headers": {
            "Content-Security-Policy": csp,
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "Expires": "0",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    }


def _build_mcp_app_payload(
    db,
    *,
    user_id: str | None,
    binding: dict[str, Any],
    access_server_ids: Iterable[str] | None,
    public_name: str,
    arguments: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
    text_output: str = "",
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    resource_uri = str(((binding.get("ui") or {}).get("resource_uri") or "")).strip()
    embedded_resource = None
    embedded_html = None
    if not resource_uri and isinstance(result, dict):
        embedded_resource = _extract_embedded_mcp_app_resource_from_result(result)
        if isinstance(embedded_resource, dict) and isinstance(embedded_resource.get("text"), str):
            embedded_html = embedded_resource["text"]
    if not resource_uri and not embedded_html:
        return None

    resource: dict[str, Any] | None = None
    if resource_uri:
        try:
            resource = read_server_resource(db, binding["server"], resource_uri)
            if not _is_mcp_app_mime_type(resource.get("mime_type")):
                logger.warning(
                    "Refused non-HTML MCP app resource server=%s tool=%s resource=%s",
                    getattr(binding.get("server"), "id", None),
                    binding.get("tool_name"),
                    resource_uri,
                )
                return None
        except Exception:
            logger.warning(
                "Failed to load MCP app resource server=%s tool=%s resource=%s",
                getattr(binding.get("server"), "id", None),
                binding.get("tool_name"),
                resource_uri,
                exc_info=True,
            )
            return None

    structured_content = None
    if isinstance(result, dict):
        structured_content = _normalize_structured_content_for_mcp_app(result.get("structured_content"))
    result_meta = deepcopy((result or {}).get("meta") or {})
    if not isinstance(result_meta, dict):
        result_meta = {}
    # Omlorix's routing stamp shares the MCP result metadata object with any
    # app-specific metadata returned by the server. Its namespaced key cannot
    # collide with standard MCP Apps fields.
    result_meta["omlorix/mcp"] = {
        "serverId": binding["server"].id,
        "toolName": binding["tool_name"],
        "publicName": public_name,
    }
    app_payload = {
        "server_id": binding["server"].id,
        "access_server_ids": sorted(_normalize_explicit_server_ids(access_server_ids) | {binding["server"].id}),
        "server_name": binding["server"].name,
        "resource_uri": (resource or {}).get("uri") or resource_uri or None,
        "resource_title": (resource or {}).get("title") or (resource or {}).get("name") or binding.get("title") or binding.get("tool_name"),
        "resource_mime_type": (resource or {}).get("mime_type") or (embedded_resource or {}).get("mime_type") or "text/html;profile=mcp-app",
        "resource_meta": deepcopy((resource or {}).get("meta") or (embedded_resource or {}).get("meta") or {}),
        "embedded_html": embedded_html,
        "tool_name": binding["tool_name"],
        "public_name": public_name,
        "tool_info": {
            "name": binding["tool_name"],
            "title": binding.get("title"),
            "description": binding.get("description"),
            "inputSchema": deepcopy(binding.get("input_schema") or {}),
            "outputSchema": deepcopy(binding.get("output_schema") or {}),
            "_meta": deepcopy(binding.get("meta") or {}),
        },
        "tool_input": deepcopy(arguments or {}),
        "tool_result": {
            "content": _build_mcp_tool_result_content_blocks(result or {}, text_fallback=text_output),
            "structuredContent": structured_content,
            "isError": bool((result or {}).get("is_error")),
            "_meta": result_meta,
        },
    }
    app_visible_tools: list[str] = []
    try:
        app_visible_tools = [
            str(tool.get("tool_name") or "").strip()
            for tool in discover_server_tools(db, binding["server"])
            if str(tool.get("tool_name") or "").strip()
            and _is_mcp_tool_allowed(binding["server"], str(tool.get("tool_name") or "").strip())
            and _mcp_tool_visible_to(tool, "app")
        ]
    except Exception:
        # Rendering an already-completed result should still succeed when a
        # follow-up discovery request fails. No callable scope is safer than a
        # broader or guessed scope.
        logger.warning(
            "Failed to build MCP app tool scope server=%s",
            getattr(binding.get("server"), "id", None),
            exc_info=True,
        )
    app_payload["app_access_token"] = _build_mcp_app_access_token(
        db,
        user_id=user_id,
        server_id=binding["server"].id,
        resource_uri=app_payload.get("resource_uri"),
        tool_name="",
        access_server_ids=app_payload.get("access_server_ids") or [],
        tool_names=app_visible_tools,
        tool_call_id=tool_call_id,
    )
    if tool_call_id:
        app_payload["tool_call_id"] = str(tool_call_id)
    return app_payload


def _build_mcp_app_widget_data(
    db,
    *,
    user_id: str | None,
    binding: dict[str, Any],
    access_server_ids: Iterable[str] | None,
    public_name: str,
    arguments: dict[str, Any] | None,
    result: dict[str, Any],
    text_output: str,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    app_payload = _build_mcp_app_payload(
        db,
        user_id=user_id,
        binding=binding,
        access_server_ids=access_server_ids,
        public_name=public_name,
        arguments=arguments,
        result=result,
        text_output=text_output,
        tool_call_id=tool_call_id,
    )
    if not app_payload:
        return None
    return {
        "type": "mcp_app",
        "html": _render_mcp_app_widget_shell(app_payload),
        "app": app_payload,
        "model_context": (
            deepcopy(app_payload.get("tool_result", {}).get("structuredContent"))
            if app_payload.get("tool_result", {}).get("structuredContent") is not None
            else deepcopy(app_payload.get("tool_result", {}).get("content"))
        ),
    }


def build_mcp_tool_stream_meta(
    db,
    *,
    user_id: str | None,
    public_name: str,
    model_settings: dict[str, Any] | None,
    arguments: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    binding = resolve_mcp_tool_binding(db, user_id=user_id, public_name=public_name, model_settings=model_settings)
    if not binding:
        return None
    app_payload = _build_mcp_app_payload(
        db,
        user_id=user_id,
        binding=binding,
        access_server_ids=[binding["server"].id],
        public_name=public_name,
        arguments=arguments or {},
        result=None,
        text_output="",
        tool_call_id=tool_call_id,
    )
    if not app_payload:
        return None
    return {
        "widget_type": "mcp_app",
        "mcp_app": app_payload,
    }


def execute_mcp_tool_by_public_name(
    db,
    *,
    user_id: str | None,
    public_name: str,
    arguments: dict[str, Any] | None,
    model_settings: dict[str, Any] | None,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    binding = resolve_mcp_tool_binding(db, user_id=user_id, public_name=public_name, model_settings=model_settings)
    if not binding:
        return None
    result = call_mcp_tool(db, binding["server"], binding["tool_name"], arguments or {})
    persisted_attachments = _persist_mcp_attachments(db, user_id, result.get("attachments") or [])
    text = str(result.get("text") or "").strip()
    if not text and result.get("structured_content") is not None:
        try:
            text = json.dumps(result.get("structured_content"), ensure_ascii=False, indent=2)
        except TypeError:
            text = str(result.get("structured_content"))
    attachment_summary = _format_mcp_attachment_summary(persisted_attachments)
    if attachment_summary:
        text = f"{text}\n\n{attachment_summary}".strip() if text else attachment_summary
    if not text:
        logger.warning(
            "MCP tool %s on %s returned no direct text output; falling back to serialized payload structured=%s attachments=%s",
            binding["tool_name"],
            binding["server"].id,
            result.get("structured_content") is not None,
            sum(len(items) for items in persisted_attachments.values()),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
    widget = _build_mcp_app_widget_data(
        db,
        user_id=user_id,
        binding=binding,
        access_server_ids=[binding["server"].id],
        public_name=public_name,
        arguments=arguments or {},
        result=result,
        text_output=text,
        tool_call_id=tool_call_id,
    )
    return {
        "content": text,
        "documents": persisted_attachments.get("documents") or [],
        "images": persisted_attachments.get("images") or [],
        "videos": persisted_attachments.get("videos") or [],
        "audios": persisted_attachments.get("audios") or [],
        "widget": widget,
        "meta": {
            "mcp": {
                "server_id": binding["server"].id,
                "server_name": binding["server"].name,
                "tool_name": binding["tool_name"],
                "public_name": public_name,
                "is_error": bool(result.get("is_error")),
                "attachment_count": sum(len(items) for items in persisted_attachments.values()),
            }
        },
    }


def get_accessible_mcp_server_for_user(
    db,
    *,
    user_id: str | None,
    server_id: str,
    model_settings: dict[str, Any] | None = None,
    access_server_ids: Iterable[str] | None = None,
) -> MCPServer:
    normalized_id = str(server_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    for server in list_accessible_mcp_servers(
        db,
        user_id,
        only_enabled=True,
        model_settings=model_settings,
        access_server_ids=access_server_ids,
    ):
        if str(getattr(server, "id", "")).strip() == normalized_id:
            return _prepare_server_for_runtime(db, server)
    raise HTTPException(status_code=404, detail="MCP server not found.")


def _is_mcp_tool_allowed(server: MCPServer, tool_name: str) -> bool:
    allowed_tools = {name for name in (server.allowed_tools or []) if isinstance(name, str) and name.strip()}
    if not allowed_tools:
        return True
    return str(tool_name or "").strip() in allowed_tools


def _mcp_tool_requires_user_approval(tool: dict[str, Any]) -> bool:
    """Return whether a discovered tool needs an approval flow Omlorix lacks.

    MCP annotations are descriptive hints rather than authorization. Omlorix
    therefore never treats ``destructiveHint`` as approval; it uses the hint
    only to keep explicitly destructive tools out of automatic model execution
    until a host-side, invocation-bound confirmation flow exists. MCP Apps may
    still inspect the annotation for their own interactive user experiences.
    """
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    return annotations.get("destructiveHint") is True


def _mcp_tool_visible_to(tool: dict[str, Any], audience: str) -> bool:
    """Apply the MCP Apps model/app visibility contract to a discovered tool."""
    ui = tool.get("ui") if isinstance(tool.get("ui"), dict) else {}
    raw_visibility = ui.get("visibility")
    if not isinstance(raw_visibility, list) or not raw_visibility:
        raw_visibility = ["model", "app"]
    visibility = {
        str(item or "").strip().lower()
        for item in raw_visibility
        if str(item or "").strip()
    }
    return str(audience or "").strip().lower() in visibility


def _require_mcp_app_visible_tool(db, server: MCPServer, tool_name: str) -> None:
    """Reject app-originated calls to tools hidden from MCP Apps."""
    normalized_name = str(tool_name or "").strip()
    try:
        discovered_tools = discover_server_tools(db, server, use_cache=False)
    except HTTPException as exc:
        from app.tools.errors import SafeToolExecutionError

        error_code = str((exc.headers or {}).get("X-Omlorix-MCP-Error-Code") or "mcp_tool_discovery_failed")
        error_message = str(exc.detail or "The MCP server's available tools could not be discovered.")
        raise SafeToolExecutionError(
            code=error_code,
            safe_message=error_message,
            detail=error_message,
            allow_same_response_retry=False,
        ) from exc
    for tool in discovered_tools:
        if str(tool.get("tool_name") or "").strip() != normalized_name:
            continue
        if _mcp_tool_visible_to(tool, "app"):
            return
        break
    raise HTTPException(status_code=403, detail="Tool is not available to MCP apps.")


def _enforce_mcp_app_tool_rate_limit(
    db,
    *,
    user_id: str | None,
    group_id: str | None,
    server: MCPServer,
    tool_name: str,
) -> None:
    """Route MCP App calls through the shared fail-closed admission policy."""
    from app.tools.helper import enforce_tool_rate_limit_or_raise

    enforce_tool_rate_limit_or_raise(
        db,
        user_id=str(user_id or ""),
        group_id=group_id,
        tool_name=_build_public_tool_name(server, tool_name),
    )


def list_mcp_app_tools_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, token_payload = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    scoped_tool_names = _mcp_app_token_tool_names(token_payload)
    tools_payload: list[dict[str, Any]] = []
    for tool in discover_server_tools(db, server, use_cache=False):
        tool_name = str(tool.get("tool_name") or "").strip()
        if (
            not tool_name
            or tool_name not in scoped_tool_names
            or not _is_mcp_tool_allowed(server, tool_name)
            or not _mcp_tool_visible_to(tool, "app")
        ):
            continue
        tools_payload.append(
            {
                "name": tool_name,
                "description": tool.get("description"),
                "title": tool.get("title"),
                "inputSchema": deepcopy(tool.get("input_schema") or {"type": "object", "properties": {}}),
                "outputSchema": deepcopy(tool.get("output_schema") or {}),
                "annotations": deepcopy(tool.get("annotations") or {}),
                "_meta": deepcopy(tool.get("meta") or {}),
            }
        )
    return {"tools": tools_payload}


def list_mcp_app_resources_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, token_payload = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    scoped_resource_uri = _mcp_app_token_resource_uri(token_payload)
    resources_payload: list[dict[str, Any]] = []
    if not scoped_resource_uri:
        return {"resources": resources_payload}
    for resource in list_server_resources(db, server):
        resource_uri = str(resource.get("uri") or "").strip()
        if resource_uri != scoped_resource_uri:
            continue
        if not _is_mcp_app_mime_type(resource.get("mime_type")):
            continue
        resources_payload.append(
            {
                "uri": resource.get("uri"),
                "name": resource.get("name"),
                "title": resource.get("title"),
                "description": resource.get("description"),
                "mimeType": resource.get("mime_type"),
                "size": resource.get("size"),
                "_meta": {"ui": deepcopy(resource.get("meta") or {})},
            }
        )
    return {"resources": resources_payload}


def read_mcp_app_resource_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    uri: str,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, token_payload = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    scoped_resource_uri = _mcp_app_token_resource_uri(token_payload)
    requested_uri = str(uri or "").strip()
    if not scoped_resource_uri or requested_uri != scoped_resource_uri:
        raise HTTPException(status_code=403, detail="MCP app access token is not scoped to this resource.")
    resource = read_server_resource(db, server, requested_uri)
    if not _is_mcp_app_mime_type(resource.get("mime_type")):
        raise HTTPException(status_code=403, detail="MCP app resource must be an app HTML resource.")
    return {
        "contents": [
            {
                "uri": resource.get("uri") or requested_uri,
                "mimeType": resource.get("mime_type") or "text/html;profile=mcp-app",
                "text": resource.get("text") or "",
                "blob": resource.get("blob"),
                "_meta": {"ui": deepcopy(resource.get("meta") or {})},
            }
        ]
    }


def list_mcp_app_resource_templates_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, _ = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    return list_server_resource_templates(db, server)


def list_mcp_app_prompts_payload(
    db,
    *,
    user_id: str | None,
    server_id: str,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, _ = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    return list_server_prompts(db, server)


def call_mcp_app_tool_payload(
    db,
    *,
    user_id: str | None,
    group_id: str | None,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    access_server_ids: Iterable[str] | None = None,
    app_access_token: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    server, token_payload = _authorize_mcp_app_bridge(
        db,
        user_id=user_id,
        server_id=server_id,
        app_access_token=app_access_token,
        tool_call_id=tool_call_id,
    )
    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required.")
    if normalized_tool_name not in _mcp_app_token_tool_names(token_payload):
        raise HTTPException(status_code=403, detail="MCP app access token is not scoped to this tool.")
    if not _is_mcp_tool_allowed(server, normalized_tool_name):
        raise HTTPException(status_code=403, detail="Tool is not allowed for this MCP server.")
    _require_mcp_app_visible_tool(db, server, normalized_tool_name)
    _enforce_mcp_app_tool_rate_limit(
        db,
        user_id=user_id,
        group_id=group_id,
        server=server,
        tool_name=normalized_tool_name,
    )

    result = call_mcp_tool(db, server, normalized_tool_name, arguments or {})
    text_output = str(result.get("text") or "").strip()
    if not text_output and result.get("structured_content") is not None:
        try:
            text_output = json.dumps(result.get("structured_content"), ensure_ascii=False, indent=2)
        except TypeError:
            text_output = str(result.get("structured_content"))

    response_payload = {
        "content": _build_mcp_tool_result_content_blocks(result, text_fallback=text_output),
        "structuredContent": _normalize_structured_content_for_mcp_app(result.get("structured_content")),
        "isError": bool(result.get("is_error")),
    }
    if isinstance(result.get("meta"), dict) and result["meta"]:
        response_payload["_meta"] = deepcopy(result["meta"])
    return response_payload


def create_admin_mcp_server(
    db,
    payload: CreateMCPServerRequest,
) -> dict[str, Any]:
    """Create an administrator-owned remote MCP server."""
    if payload.owner_type != OWNER_ADMIN:
        raise HTTPException(status_code=400, detail="Admin endpoints only create admin-owned MCP servers.")
    server = create_mcp_server(
        db,
        owner_type=OWNER_ADMIN,
        owner_user_id=None,
        command=None,
        args=[],
        env={},
        **payload.model_dump(exclude={"owner_type"}),
    )
    return serialize_mcp_server(server)


def create_user_mcp_server(db, user_id: str, payload: CreateMCPServerRequest) -> dict[str, Any]:
    """Create a user-owned remote MCP server."""
    require_group_mcp_enabled(user_id, db)
    owner_type = payload.owner_type
    if owner_type != OWNER_USER:
        raise HTTPException(status_code=400, detail="User endpoints only create personal MCP servers.")
    server = create_mcp_server(
        db,
        owner_type=OWNER_USER,
        owner_user_id=user_id,
        command=None,
        args=[],
        env={},
        **payload.model_dump(exclude={"owner_type"}),
    )
    return serialize_mcp_server(server)


def _build_mcp_server_update_payload(payload: UpdateMCPServerRequest) -> dict[str, Any]:
    return payload.model_dump(exclude_unset=True)


def resolve_mcp_headers_from_payload(
    payload: Any,
    existing_server: MCPServer | None = None,
) -> dict[str, str]:
    """Resolve submitted headers without moving saved secrets to a new scope.

    Explicit headers belong to the current request and may be used as supplied.
    Redacted saved headers, however, are reused only when both the endpoint and
    authentication mode still match the persisted server.
    """

    field_name = "headers"
    fields_set = set(getattr(payload, "model_fields_set", set()) or set())
    if field_name in fields_set:
        raw_value = getattr(payload, field_name, None)
        return deepcopy(raw_value) if isinstance(raw_value, dict) else {}
    if not mcp_test_payload_matches_saved_credential_scope(payload, existing_server):
        return {}
    existing_value = getattr(existing_server, field_name, None) if existing_server is not None else None
    return deepcopy(existing_value) if isinstance(existing_value, dict) else {}


def mcp_test_payload_matches_saved_credential_scope(
    payload: Any,
    existing_server: MCPServer | None,
) -> bool:
    """Return whether saved credentials remain scoped to the test draft."""

    if existing_server is None:
        return False
    saved_url = str(getattr(existing_server, "url", None) or "").strip()
    draft_url = str(getattr(payload, "url", None) or "").strip()
    saved_auth_mode = str(getattr(existing_server, "auth_mode", None) or "headers").strip().lower()
    draft_auth_mode = str(getattr(payload, "auth_mode", None) or "headers").strip().lower()
    return saved_url == draft_url and saved_auth_mode == draft_auth_mode


def resolve_mcp_oauth_from_payload(
    payload: Any,
    existing_server: MCPServer | None = None,
) -> dict[str, Any]:
    """Reuse saved OAuth material only for the same endpoint and auth mode."""

    draft_auth_mode = str(getattr(payload, "auth_mode", None) or "headers").strip().lower()
    if draft_auth_mode != "oauth":
        return {}
    if not mcp_test_payload_matches_saved_credential_scope(payload, existing_server):
        return {}
    existing_oauth = getattr(existing_server, "oauth", None)
    return deepcopy(existing_oauth) if isinstance(existing_oauth, dict) else {}


def list_admin_servers_payload(db) -> list[dict[str, Any]]:
    return [serialize_mcp_server(server) for server in list_mcp_servers(db, owner_type=OWNER_ADMIN)]


def export_admin_servers_bundle(db) -> dict[str, Any]:
    servers = [
        serialize_mcp_server_export(server)
        for server in list_mcp_servers(db, owner_type=OWNER_ADMIN)
    ]
    return {
        "export_type": "mcp_server",
        "export_version": current_admin_mcp_server_export_version,
        "secrets_included": False,
        "data": {
            "servers": servers,
        },
    }


def import_admin_servers_bundle(db, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid import payload. Expected an object.")

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "mcp_server":
        raise HTTPException(status_code=400, detail=f"Unsupported export_type '{export_type}'.")
    if export_version != current_admin_mcp_server_export_version:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{current_admin_mcp_server_export_version}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(status_code=400, detail="Invalid export payload. Missing 'data' object.")

    raw_servers = data_block.get("servers")
    if not isinstance(raw_servers, list):
        raise HTTPException(status_code=400, detail="Invalid export payload. 'servers' must be a list.")

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, server_entry in enumerate(raw_servers):
        if not isinstance(server_entry, dict):
            errors.append({"index": index, "error": "Server entry must be an object."})
            continue

        name = str(server_entry.get("name") or "").strip()
        try:
            request_payload = CreateMCPServerRequest.model_validate(
                {
                    # The source ID is portable relationship metadata only.
                    # Creation must always allocate a fresh destination ID.
                    **{
                        key: value
                        for key, value in server_entry.items()
                        if key != "id"
                    },
                    "owner_type": OWNER_ADMIN,
                }
            )
            created_server = create_admin_mcp_server(db, request_payload)
            created.append(
                {
                    "id": created_server.get("id"),
                    "name": created_server.get("name"),
                    "transport": created_server.get("transport"),
                    "enabled": created_server.get("enabled"),
                }
            )
        except ValidationError as exc:
            errors.append({"index": index, "name": name, "error": exc.errors()})
        except HTTPException as exc:
            errors.append({"index": index, "name": name, "error": exc.detail})
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})

    return {"created": created, "errors": errors}


def list_user_servers_payload(db, user_id: str) -> list[dict[str, Any]]:
    require_group_mcp_enabled(user_id, db)
    return [serialize_mcp_server(server) for server in list_mcp_servers(db, owner_type=OWNER_USER, owner_user_id=user_id, include_managed=False)]


def get_admin_server_payload(db, server_id: str) -> dict[str, Any]:
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_ADMIN:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    return serialize_mcp_server(server)


def get_user_server_payload(db, user_id: str, server_id: str) -> dict[str, Any]:
    require_group_mcp_enabled(user_id, db)
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_USER or server.owner_user_id != user_id or server.managed_connection_id:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    return serialize_mcp_server(server)


def update_admin_server_payload(db, server_id: str, payload: UpdateMCPServerRequest) -> dict[str, Any]:
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_ADMIN:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    updated = update_mcp_server(db, server_id, **_build_mcp_server_update_payload(payload))
    return serialize_mcp_server(updated)


def update_user_server_payload(db, user_id: str, server_id: str, payload: UpdateMCPServerRequest) -> dict[str, Any]:
    require_group_mcp_enabled(user_id, db)
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_USER or server.owner_user_id != user_id or server.managed_connection_id:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    updated = update_mcp_server(db, server_id, **_build_mcp_server_update_payload(payload))
    return serialize_mcp_server(updated)


def delete_admin_server_payload(db, server_id: str) -> None:
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_ADMIN:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    from app.automations.models import remove_mcp_server_from_automations

    remove_mcp_server_from_automations(db, server_id, commit=False)
    delete_mcp_server(db, server_id)


def delete_user_server_payload(db, user_id: str, server_id: str) -> None:
    require_group_mcp_enabled(user_id, db)
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_USER or server.owner_user_id != user_id or server.managed_connection_id:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    from app.automations.models import remove_mcp_server_from_automations

    remove_mcp_server_from_automations(db, server_id, commit=False)
    delete_mcp_server(db, server_id)


def preview_server_tools(db, server: MCPServer) -> list[dict[str, Any]]:
    tools = discover_server_tools(db, server, use_cache=False)
    descriptors: list[dict[str, Any]] = []
    server_id = str(getattr(server, "id", None) or "preview")
    for tool in tools:
        tool_name = str(tool.get("tool_name") or "").strip()
        if not tool_name:
            continue
        descriptors.append(
            {
                "public_name": _build_public_tool_name(server, tool_name),
                "tool_name": tool_name,
                "description": tool.get("description"),
                "server_id": server_id,
                "server_name": server.name,
                "transport": server.transport,
                "input_schema": _sanitize_schema(tool.get("input_schema") or {}),
            }
        )
    return descriptors
