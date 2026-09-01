from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp import utils as mcp_utils
from app.mcp.schemas import CreateMCPServerRequest, MCPAppToolCallRequest


def _server(server_id: str = "server-1", **overrides):
    """Build the minimum server shape needed by MCP runtime unit tests."""
    values = {
        "id": server_id,
        "name": "Docs",
        "namespace": "docs",
        "enabled": True,
        "allowed_tools": [],
        "updated_at": "2026-01-01T00:00:00Z",
        "status": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _read_resource_with_contents(monkeypatch, contents):
    class FakeSession:
        async def read_resource(self, _uri, meta=None):
            assert meta is None or isinstance(meta, dict)
            return SimpleNamespace(contents=contents)

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeSession()

    monkeypatch.setattr(mcp_utils, "_mcp_session", fake_session)
    return asyncio.run(
        mcp_utils._read_server_resource_async(
            _server(timeout_seconds=1),
            "docs://requested",
        )
    )


@pytest.mark.parametrize(
    ("content", "expected_text", "expected_blob"),
    [
        (
            SimpleNamespace(uri="docs://plain", mimeType="text/plain", text="Readable docs"),
            "Readable docs",
            None,
        ),
        (
            SimpleNamespace(uri="docs://json", mimeType="application/json", text='{"ok":true}'),
            '{"ok":true}',
            None,
        ),
        (
            SimpleNamespace(uri="docs://empty", mimeType="text/plain", text=""),
            "",
            None,
        ),
        (
            SimpleNamespace(uri="asset://binary", mimeType="application/octet-stream", blob="AAEC"),
            None,
            "AAEC",
        ),
        (
            SimpleNamespace(uri="ui://app", mimeType="text/html;profile=mcp-app", text="<html></html>"),
            "<html></html>",
            None,
        ),
    ],
)
def test_general_mcp_resource_reader_accepts_bounded_standard_content(
    monkeypatch,
    content,
    expected_text,
    expected_blob,
):
    """General resource reads preserve text/blob content without weakening apps."""
    resource = _read_resource_with_contents(monkeypatch, [content])

    assert resource["uri"] == content.uri
    assert resource["mime_type"] == content.mimeType
    assert resource["text"] == expected_text
    assert resource["blob"] == expected_blob


@pytest.mark.parametrize(
    ("contents", "expected_code"),
    [
        ([SimpleNamespace()] * (mcp_utils._MCP_MAX_RESOURCE_CONTENTS + 1), "mcp_resource_too_many_contents"),
        (
            [SimpleNamespace(mimeType="text/plain", text="x" * (mcp_utils._MCP_MAX_RESOURCE_TEXT_BYTES + 1))],
            "mcp_resource_too_large",
        ),
        ([SimpleNamespace(mimeType="application/octet-stream", blob="%%%")], "mcp_resource_content_unsupported"),
        ([SimpleNamespace(uri="docs://empty")], "mcp_resource_content_unsupported"),
    ],
)
def test_general_mcp_resource_reader_rejects_bounded_invalid_content(
    monkeypatch,
    contents,
    expected_code,
):
    with pytest.raises(mcp_utils.MCPResourceContentError) as raised:
        _read_resource_with_contents(monkeypatch, contents)

    assert raised.value.code == expected_code


def test_resource_content_error_keeps_healthy_server_up(monkeypatch):
    """A server response rejected by local content policy is not downtime."""
    server = _server()
    captured_status: dict[str, object] = {}

    def rejected_content(coroutine):
        coroutine.close()
        raise ExceptionGroup(
            "resource rejected",
            [
                mcp_utils.MCPResourceContentError(
                    "MCP resource did not return supported text or binary content.",
                    "mcp_resource_content_unsupported",
                )
            ],
        )

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", rejected_content)
    monkeypatch.setattr(
        mcp_utils,
        "_set_server_status",
        lambda _db, _server, **kwargs: captured_status.update(kwargs),
    )

    with pytest.raises(HTTPException) as raised:
        mcp_utils.read_server_resource(object(), server, "docs://empty")

    assert raised.value.status_code == 422
    assert raised.value.headers["X-Omlorix-MCP-Error-Code"] == "mcp_resource_content_unsupported"
    assert captured_status == {"available": "up"}


def test_mcp_app_callers_keep_the_html_resource_boundary():
    """General text must never become executable MCP app-frame content."""
    server = _server()
    token_payload = {"resource_uri": "ui://app"}
    plain_resource = {
        "uri": "ui://app",
        "mime_type": "text/plain; profile=html",
        "text": "not executable",
    }

    with patch(
        "app.mcp.utils._authorize_mcp_app_bridge",
        return_value=(server, token_payload),
    ), patch("app.mcp.utils.read_server_resource", return_value=plain_resource):
        with pytest.raises(HTTPException) as raised:
            mcp_utils.read_mcp_app_resource_payload(
                object(),
                user_id="user-1",
                server_id=server.id,
                uri="ui://app",
                app_access_token="scoped-token",
            )

        app_payload = mcp_utils._build_mcp_app_payload(
            object(),
            user_id="user-1",
            binding={
                "server": server,
                "tool_name": "show_docs",
                "title": "Docs",
                "description": "Show docs.",
                "input_schema": {},
                "output_schema": {},
                "ui": {"resource_uri": "ui://app"},
                "meta": {},
            },
            access_server_ids=[server.id],
            public_name="mcp_docs_show_docs",
            arguments={},
        )

    assert raised.value.status_code == 403
    assert app_payload is None
    assert mcp_utils._is_mcp_app_mime_type("text/html; charset=utf-8") is True
    assert mcp_utils._is_mcp_app_mime_type("text/html+skybridge; charset=utf-8") is True
    assert mcp_utils._is_mcp_app_mime_type("text/plain; profile=html") is False
    assert mcp_utils._extract_embedded_mcp_app_resource_from_result(
        {
            "raw": {
                "content": [
                    {
                        "type": "resource",
                        "resource": plain_resource,
                    }
                ]
            }
        }
    ) is None


def test_public_tool_names_are_stable_and_collision_resistant():
    """User-editable namespaces cannot cause one server to shadow another."""
    first = _server("server-1")
    second = _server("server-2")

    first_name = mcp_utils._build_public_tool_name(first, "search")

    assert first_name == mcp_utils._build_public_tool_name(first, "search")
    assert first_name != mcp_utils._build_public_tool_name(second, "search")
    assert len(first_name) <= 64


def test_truncated_public_tool_name_preserves_full_digest(monkeypatch):
    """Provider length limits must never truncate the collision digest."""
    server = _server("server-1")
    tool_name = "search" * 20
    monkeypatch.setattr(mcp_utils, "_server_namespace", lambda _server: "namespace" * 20)
    digest = mcp_utils.hashlib.sha256(
        f"{server.id}:{tool_name}".encode("utf-8")
    ).hexdigest()[:8]

    public_name = mcp_utils._build_public_tool_name(server, tool_name)

    assert len(public_name) <= 64
    assert public_name.startswith("mcp_")
    assert public_name.endswith(f"_{digest}")


def test_mcp_app_visibility_separates_model_and_app_only_tools():
    """Respect the stable MCP Apps visibility contract in both directions."""
    app_only = {"ui": {"visibility": ["app"]}}
    model_only = {"ui": {"visibility": ["model"]}}

    assert mcp_utils._mcp_tool_visible_to(app_only, "app") is True
    assert mcp_utils._mcp_tool_visible_to(app_only, "model") is False
    assert mcp_utils._mcp_tool_visible_to(model_only, "model") is True
    assert mcp_utils._mcp_tool_visible_to(model_only, "app") is False


def test_destructive_tool_annotations_never_count_as_model_approval():
    """Explicitly destructive tools stay out of automatic model execution."""
    destructive = {"annotations": {"destructiveHint": True}}
    read_only = {"annotations": {"readOnlyHint": True, "destructiveHint": False}}

    assert mcp_utils._mcp_tool_requires_user_approval(destructive) is True
    assert mcp_utils._mcp_tool_requires_user_approval(read_only) is False


def test_managed_google_mcp_filters_tools_by_declared_capability():
    """Omlorix must fail closed if a shared worker returns another product's tools."""
    server = _server(
        owner_type="user",
        managed_connection_id="connection-gmail",
    )
    capability_key = mcp_utils.GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY
    discovered = [
        {
            "tool_name": "search_gmail_messages",
            "meta": {capability_key: ["gmail"]},
        },
        {
            "tool_name": "list_calendar_events",
            "meta": {capability_key: ["calendar"]},
        },
        {
            "tool_name": "future_calendar_availability_tool",
            "meta": {capability_key: ["calendar"]},
        },
        {"tool_name": "legacy_unclassified_tool", "meta": {}},
    ]

    with patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-gmail": "gmail"},
    ):
        filtered = mcp_utils._filter_managed_google_workspace_tools(
            object(),
            server,
            discovered,
        )

    assert [tool["tool_name"] for tool in filtered] == ["search_gmail_messages"]


def test_managed_google_mcp_discovery_persists_only_provider_tools(monkeypatch):
    """Status and discovery caches must contain the same filtered tool set."""
    server = _server(
        owner_type="user",
        managed_connection_id="connection-calendar",
    )
    capability_key = mcp_utils.GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY
    discovered = [
        {"tool_name": "list_calendar_events", "meta": {capability_key: ["calendar"]}},
        {"tool_name": "search_gmail_messages", "meta": {capability_key: ["gmail"]}},
    ]
    captured_status: dict[str, object] = {}

    def run_discovery(coroutine):
        coroutine.close()
        return discovered, {"protocol_version": "2026-01-01", "ttl_ms": 0}

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", run_discovery)
    monkeypatch.setattr(
        mcp_utils,
        "_set_server_status",
        lambda _db, _server, **kwargs: captured_status.update(kwargs),
    )
    monkeypatch.setattr(
        mcp_utils,
        "_managed_connection_provider_map",
        lambda _db, _servers: {"connection-calendar": "google_calendar"},
    )
    mcp_utils._DISCOVERY_CACHE.clear()

    filtered = mcp_utils.discover_server_tools(object(), server)

    assert [tool["tool_name"] for tool in filtered] == ["list_calendar_events"]
    assert [tool["tool_name"] for tool in captured_status["tools"]] == ["list_calendar_events"]


def test_group_policy_only_disables_personal_mcp_servers():
    """Allowed admin and managed servers survive the personal MCP toggle."""
    admin_server = _server("admin-1", owner_type="admin", managed_connection_id=None)
    personal_server = _server("personal-1", owner_type="user", managed_connection_id=None)
    managed_server = _server("managed-1", owner_type="user", managed_connection_id="connection-1")

    def list_servers(_db, *, owner_type, **_kwargs):
        if owner_type == "admin":
            return [admin_server]
        return [personal_server, managed_server]

    with patch("app.mcp.utils._ensure_group_mcp_enabled", return_value=False), patch(
        "app.mcp.utils.list_mcp_servers", side_effect=list_servers
    ), patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-1": "gmail"},
    ), patch(
        "app.mcp.utils.group_allows_connection_provider", return_value=True
    ):
        servers = mcp_utils.list_accessible_mcp_servers(
            object(),
            "user-1",
            model_settings={"enabled_mcp_servers": ["admin-1"]},
            access_server_ids=["admin-1"],
        )

    assert [server.id for server in servers] == ["admin-1", "managed-1"]


def test_file_source_adapter_servers_never_enter_llm_mcp_runtime():
    """A stale managed Drive server cannot be selected as an LLM tool source."""
    stale_drive_server = _server(
        "drive-server",
        owner_type="user",
        managed_connection_id="connection-drive",
    )

    def list_servers(_db, *, owner_type, **_kwargs):
        if owner_type == "admin":
            return []
        return [stale_drive_server]

    with patch("app.mcp.utils._ensure_group_mcp_enabled", return_value=True), patch(
        "app.mcp.utils.list_mcp_servers", side_effect=list_servers
    ), patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-drive": "google_drive"},
    ), patch(
        "app.mcp.utils._all_managed_connection_provider_keys",
        return_value={"gmail", "google_calendar"},
    ), patch(
        "app.mcp.utils.group_allows_connection_provider", return_value=True
    ):
        servers = mcp_utils.list_accessible_mcp_servers(object(), "user-1")

    assert servers == []


def test_file_source_provider_is_removed_from_stale_model_allow_list():
    """Old explicit model settings cannot re-enable a file-only provider."""
    with patch(
        "app.mcp.utils._all_managed_connection_provider_keys",
        return_value={"gmail", "google_calendar"},
    ):
        providers = mcp_utils._allowed_model_connection_providers(
            {
                "allowed_mcp_servers": [
                    mcp_utils.build_connection_provider_mcp_value("google_drive"),
                ],
            }
        )

    assert providers == set()


def test_managed_mcp_server_is_removed_when_group_revokes_provider():
    """Connection-provider revocation must take effect at the MCP boundary."""
    db = object()
    admin_server = _server("admin-1", owner_type="admin", managed_connection_id=None)
    personal_server = _server("personal-1", owner_type="user", managed_connection_id=None)
    managed_server = _server("managed-1", owner_type="user", managed_connection_id="connection-1")

    def list_servers(_db, *, owner_type, **_kwargs):
        if owner_type == "admin":
            return [admin_server]
        return [personal_server, managed_server]

    with patch("app.mcp.utils._ensure_group_mcp_enabled", return_value=True), patch(
        "app.mcp.utils.list_mcp_servers", side_effect=list_servers
    ), patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-1": "gmail"},
    ), patch(
        "app.mcp.utils.group_allows_connection_provider", return_value=False
    ) as provider_policy:
        servers = mcp_utils.list_accessible_mcp_servers(db, "user-1")

    assert [server.id for server in servers] == ["admin-1", "personal-1"]
    provider_policy.assert_called_once_with("user-1", db, provider="gmail")


def test_call_mcp_tool_enforces_allowlist_at_execution_boundary():
    """A stale schema or direct caller cannot invoke an unadvertised tool."""
    server = _server(allowed_tools=["search"])
    with patch("app.mcp.utils._prepare_server_for_runtime", return_value=server), patch(
        "app.mcp.utils._run_async"
    ) as run_async:
        with pytest.raises(ValueError, match="not allowed"):
            mcp_utils.call_mcp_tool(object(), server, "delete_everything", {})

    run_async.assert_not_called()


def test_call_mcp_tool_rejects_stale_google_capability_at_execution_boundary():
    """A stale model schema cannot bypass managed Google discovery filtering."""
    server = _server(
        owner_type="user",
        managed_connection_id="connection-gmail",
    )
    capability_key = mcp_utils.GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY
    with patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-gmail": "gmail"},
    ), patch(
        "app.mcp.utils.discover_server_tools",
        return_value=[
            {
                "tool_name": "search_gmail_messages",
                "meta": {capability_key: ["gmail"]},
            },
        ],
    ), patch("app.mcp.utils._run_async") as run_async:
        with pytest.raises(ValueError, match="not available"):
            mcp_utils.call_mcp_tool(
                object(),
                server,
                "list_calendar_events",
                {},
            )

    run_async.assert_not_called()


def test_oauth_step_up_challenge_preserves_requested_scope_union():
    """A 403 insufficient_scope challenge is queued for the next reconnect."""
    challenge_error = RuntimeError("permission upgrade required")
    challenge_error.response = SimpleNamespace(
        status_code=403,
        headers={
            "WWW-Authenticate": (
                'Bearer error="insufficient_scope", '
                'scope="documents.read documents.write"'
            )
        },
    )
    server = _server(
        auth_mode="oauth",
        oauth={
            "scope": "profile documents.read",
            "pending_scopes": ["calendar.read"],
        },
    )

    class FakeDb:
        def add(self, _server):
            pass

        def commit(self):
            pass

        def refresh(self, _server):
            pass

    scopes = mcp_utils._oauth_step_up_scopes_from_exception(challenge_error)
    mcp_utils._record_oauth_step_up_scopes(FakeDb(), server, scopes)

    assert scopes == ["documents.read", "documents.write"]
    assert server.oauth["pending_scopes"] == [
        "profile",
        "documents.read",
        "calendar.read",
        "documents.write",
    ]


def test_mcp_authentication_failure_unwraps_task_group_for_github():
    """A nested HTTP 401 becomes an actionable GitHub token message."""
    server = _server(
        owner_type="user",
        managed_connection_id="connection-github",
        name="GitHub",
    )
    failure = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [mcp_utils.MCPHTTPAuthenticationError(401)],
    )

    with patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-github": "github"},
    ):
        message, code = mcp_utils._mcp_error_details(object(), server, failure)

    assert code == "github_token_invalid"
    assert message == "GitHub token is invalid or expired. Reconnect GitHub with a new token."
    assert "TaskGroup" not in message


def test_mcp_unknown_failure_never_reaches_status_or_http_detail(monkeypatch):
    """Unknown provider diagnostics stay server-side at the discovery boundary."""
    server = _server(owner_type="admin", name="Shared company search")
    captured_status: dict[str, object] = {}
    provider_detail = (
        "upstream rejected client_secret=not-covered-by-generic-redaction "
        "for employee@example.test at https://internal.example.test/debug"
    )

    def failed_discovery(coroutine):
        coroutine.close()
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError(provider_detail)],
        )

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", failed_discovery)
    monkeypatch.setattr(
        mcp_utils,
        "_set_server_status",
        lambda _db, _server, **kwargs: captured_status.update(kwargs),
    )
    mcp_utils._DISCOVERY_CACHE.clear()

    with pytest.raises(HTTPException) as raised:
        mcp_utils.discover_server_tools(object(), server, use_cache=False)

    expected_message = (
        "Could not connect to Shared company search. "
        "Check the connection credentials and try again."
    )
    assert raised.value.detail == expected_message
    assert raised.value.headers["X-Omlorix-MCP-Error-Code"] == "mcp_connection_failed"
    assert captured_status["error"] == expected_message
    assert captured_status["error_code"] == "mcp_connection_failed"
    exposed_text = f"{raised.value.detail} {captured_status['error']}"
    assert "client_secret" not in exposed_text
    assert "employee@example.test" not in exposed_text
    assert "internal.example.test" not in exposed_text


def test_mcp_discovery_persists_safe_auth_error(monkeypatch):
    """Connection status and HTTP responses share the same safe error text."""
    server = _server(
        owner_type="user",
        managed_connection_id="connection-github",
        name="GitHub",
    )
    captured_status: dict[str, object] = {}

    def failed_discovery(coroutine):
        coroutine.close()
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [mcp_utils.MCPHTTPAuthenticationError(401)],
        )

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", failed_discovery)
    monkeypatch.setattr(
        mcp_utils,
        "_set_server_status",
        lambda _db, _server, **kwargs: captured_status.update(kwargs),
    )
    monkeypatch.setattr(
        mcp_utils,
        "_managed_connection_provider_map",
        lambda _db, _servers: {"connection-github": "github"},
    )
    mcp_utils._DISCOVERY_CACHE.clear()

    with pytest.raises(HTTPException) as raised:
        mcp_utils.discover_server_tools(object(), server, use_cache=False)

    assert raised.value.status_code == 400
    assert raised.value.detail == "GitHub token is invalid or expired. Reconnect GitHub with a new token."
    assert raised.value.headers["X-Omlorix-MCP-Error-Code"] == "github_token_invalid"
    assert captured_status["error"] == raised.value.detail
    assert captured_status["error_code"] == "github_token_invalid"


def test_mcp_tool_auth_failure_uses_safe_error_for_chat_output(monkeypatch):
    """Chat tool execution receives the actionable message, not TaskGroup text."""
    from app.tools.errors import SafeToolExecutionError

    server = _server(
        owner_type="user",
        managed_connection_id="connection-github",
        name="GitHub",
    )

    def failed_call(coroutine):
        coroutine.close()
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [mcp_utils.MCPHTTPAuthenticationError(401)],
        )

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", failed_call)
    monkeypatch.setattr(mcp_utils, "_set_server_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mcp_utils,
        "_managed_connection_provider_map",
        lambda _db, _servers: {"connection-github": "github"},
    )

    with pytest.raises(SafeToolExecutionError) as raised:
        mcp_utils.call_mcp_tool(object(), server, "search_code", {})

    assert raised.value.code == "github_token_invalid"
    assert raised.value.safe_message == "GitHub token is invalid or expired. Reconnect GitHub with a new token."
    assert "TaskGroup" not in str(raised.value)


def test_bridge_does_not_advertise_destructive_tools_to_models():
    """Model schemas must not expose tools that require a missing approval flow."""
    server = _server(owner_type="admin", managed_connection_id=None)
    discovered = [
        {"tool_name": "search", "description": "Search safely."},
        {
            "tool_name": "delete_everything",
            "description": "Delete remote data.",
            "annotations": {"destructiveHint": True},
        },
    ]
    with patch("app.mcp.utils.list_accessible_mcp_servers", return_value=[server]), patch(
        "app.mcp.utils._filter_servers_for_settings", return_value=[server]
    ), patch("app.mcp.utils.discover_server_tools", return_value=discovered):
        names, schemas = mcp_utils.build_mcp_bridge_tools(
            object(),
            user_id="user-1",
            model_settings={},
        )

    assert names == [mcp_utils._build_public_tool_name(server, "search")]
    assert [schema["name"] for schema in schemas] == names


def test_provider_bundle_always_uses_omlorix_bridge():
    """Provider selection cannot bypass Omlorix's bridge policies."""
    server = _server(
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer secret"},
        allowed_tools=["search", "delete_everything"],
        owner_type="admin",
        managed_connection_id=None,
    )
    with patch("app.mcp.utils.list_accessible_mcp_servers", return_value=[server]), patch(
        "app.mcp.utils._prepare_server_for_runtime", return_value=server
    ), patch(
        "app.mcp.utils.discover_server_tools",
        return_value=[
            {"tool_name": "search", "ui": {"visibility": ["model", "app"]}},
            {
                "tool_name": "delete_everything",
                "ui": {"visibility": ["model", "app"]},
                "annotations": {"destructiveHint": True},
            },
        ],
    ):
        bundle = mcp_utils.build_mcp_provider_bundle(
            object(),
            provider="openai_responses",
            user_id="user-1",
            model_settings={"enabled_mcp_servers": [server.id]},
        )

    expected_name = mcp_utils._build_public_tool_name(server, "search")
    assert bundle == {
        "bridge_tool_names": [expected_name],
        "bridge_tool_schemas": [
            {
                "name": expected_name,
                "description": "[MCP: Docs] Tool exposed by the connected MCP server.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    }


def test_paginated_discovery_collects_all_pages_and_rejects_cursor_loops():
    """Discovery follows MCP cursors while retaining a hard loop bound."""
    async def collect_pages():
        async def method(cursor=None):
            if cursor is None:
                return SimpleNamespace(tools=["one"], nextCursor="page-2")
            return SimpleNamespace(tools=["two"], nextCursor=None)

        return await mcp_utils._collect_paginated_items(
            method,
            item_attribute="tools",
            timeout_seconds=1,
        )

    async def collect_loop():
        async def method(cursor=None):
            return SimpleNamespace(tools=[], nextCursor="same")

        return await mcp_utils._collect_paginated_items(
            method,
            item_attribute="tools",
            timeout_seconds=1,
        )

    assert asyncio.run(collect_pages()) == ["one", "two"]
    with pytest.raises(ValueError, match="repeated pagination cursor"):
        asyncio.run(collect_loop())


def test_paginated_discovery_combines_cache_hints_conservatively():
    """The merged list must honor the strictest hint from every page."""
    metadata = {}

    async def collect_pages():
        async def method(cursor=None):
            if cursor is None:
                return SimpleNamespace(
                    tools=["one"],
                    nextCursor="page-2",
                    ttl_ms=60_000,
                    cache_scope="public",
                )
            return SimpleNamespace(
                tools=["two"],
                nextCursor=None,
                ttl_ms=0,
                cache_scope="private",
            )

        return await mcp_utils._collect_paginated_items(
            method,
            item_attribute="tools",
            timeout_seconds=1,
            response_metadata=metadata,
        )

    assert asyncio.run(collect_pages()) == ["one", "two"]
    assert metadata == {"ttl_ms": 0.0, "cache_scope": "private"}


def test_operational_status_does_not_change_discovery_cache_identity():
    """Health updates must not invalidate a configuration-keyed cache entry."""
    server = _server(status={"checked_at": "old"})
    before = mcp_utils._discovery_cache_key(server)
    server.status = {"checked_at": "new", "available": "up"}

    assert mcp_utils._discovery_cache_key(server) == before


def test_subscription_change_invalidates_every_cached_server_generation():
    """A modern list-change event must evict stale entries after configuration edits."""
    mcp_utils._DISCOVERY_CACHE.clear()
    mcp_utils._DISCOVERY_CACHE.update(
        {
            "server-1:first": {"expires_at": 100, "tools": ["old"]},
            "server-1:second": {"expires_at": 200, "tools": ["newer"]},
            "server-2:first": {"expires_at": 100, "tools": ["other"]},
        }
    )

    mcp_utils._invalidate_server_discovery_cache("server-1")

    assert list(mcp_utils._DISCOVERY_CACHE) == ["server-2:first"]


def test_trace_context_is_forwarded_only_through_standard_w3c_fields(monkeypatch):
    """MCP request metadata carries trace context without arbitrary propagator data."""
    def inject(carrier):
        carrier.update(
            {
                "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                "tracestate": "vendor=value",
                "x-untrusted": "drop-me",
            }
        )

    monkeypatch.setattr("opentelemetry.propagate.inject", inject)

    assert mcp_utils._mcp_request_meta() == {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        "tracestate": "vendor=value",
    }


def test_mcp_request_schemas_bound_remote_urls_and_argument_size():
    """Reject malformed endpoints and request bodies before transport work."""
    with pytest.raises(ValidationError):
        CreateMCPServerRequest(
            owner_type="user",
            name="Unsafe",
            transport="streamable_http",
            url="file:///etc/passwd",
        )

    with pytest.raises(ValidationError):
        CreateMCPServerRequest(
            owner_type="user",
            name="Embedded secret",
            transport="streamable_http",
            url="https://user:password@mcp.example.com/mcp",
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateMCPServerRequest(
            owner_type="admin",
            name="Conflicting remote endpoint",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            command="/usr/local/bin/example-worker",
        )

    with pytest.raises(ValidationError):
        CreateMCPServerRequest(
            owner_type="admin",
            name="Conflicting stdio endpoint",
            transport="stdio",
            url="https://mcp.example.com/mcp",
            command="/usr/local/bin/example-worker",
        )

    with pytest.raises(ValidationError):
        MCPAppToolCallRequest(
            server_id="server-1",
            tool_name="search",
            arguments={"value": "x" * 1_000_001},
        )


def test_json_schema_2020_12_rejects_unresolved_external_references():
    """External references are never fetched or treated as permissive schemas."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://schemas.example.com/search.json",
        "$defs": {
            "mode": {"enum": ["quick", "complete"]},
        },
        "oneOf": [
            {"$ref": "#/$defs/mode"},
            {"$ref": "https://schemas.example.com/shared.json#/$defs/mode"},
        ],
        "unevaluatedProperties": False,
    }

    with pytest.raises(ValueError, match="unresolved external reference"):
        mcp_utils._sanitize_schema(schema)


def test_json_schema_2020_12_preserves_internal_references_and_composition():
    """Safe document-local references and modern composition remain unchanged."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"mode": {"enum": ["quick", "complete"]}},
        "oneOf": [{"$ref": "#/$defs/mode"}, {"type": "null"}],
        "unevaluatedProperties": False,
    }

    sanitized = mcp_utils._sanitize_schema(schema)

    assert sanitized == schema
    assert sanitized is not schema


def test_json_schema_depth_is_bounded():
    """Untrusted recursive-looking schemas cannot exhaust the Python stack."""
    schema: dict = {}
    cursor = schema
    for _ in range(mcp_utils._MCP_MAX_SCHEMA_DEPTH + 1):
        cursor["allOf"] = [{}]
        cursor = cursor["allOf"][0]

    with pytest.raises(ValueError, match="maximum nesting depth"):
        mcp_utils._sanitize_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "not-a-json-schema-type"},
        {"properties": []},
        {"type": "number", "minimum": float("nan")},
    ],
)
def test_invalid_json_schemas_are_rejected(schema):
    """MCP discovery must reject malformed schemas at the trust boundary."""
    with pytest.raises(ValueError, match="schema"):
        mcp_utils._sanitize_schema(schema)


def test_tool_discovery_preserves_boolean_output_schema(monkeypatch):
    """The valid deny-all output schema must not become a permissive object."""
    tool = SimpleNamespace(
        name="always_fails",
        description="Never produces a valid structured result.",
        inputSchema={"type": "object"},
        outputSchema=False,
        annotations=None,
        meta=None,
    )

    class FakeClient:
        protocol_version = "2026-07-28"

        async def list_tools(self, cursor=None, **_kwargs):
            return SimpleNamespace(
                tools=[tool],
                nextCursor=None,
                ttl_ms=0,
                cache_scope="private",
            )

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeClient()

    monkeypatch.setattr(mcp_utils, "_mcp_session", fake_session)

    tools, _metadata = asyncio.run(
        mcp_utils._discover_server_tools_async(_server(timeout_seconds=1))
    )

    assert tools[0]["output_schema"] is False


@pytest.mark.parametrize("structured_value", [False, 0, "", [], {}])
def test_tool_calls_preserve_falsy_structured_content(monkeypatch, structured_value):
    """All JSON values permitted by MCP survive the bridge unchanged."""
    result = SimpleNamespace(
        content=[],
        structured_content=structured_value,
        is_error=False,
    )

    class FakeClient:
        async def call_tool(self, _name, _arguments, **_kwargs):
            return result

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeClient()

    monkeypatch.setattr(mcp_utils, "_mcp_session", fake_session)

    payload = asyncio.run(
        mcp_utils._call_server_tool_async(_server(), "search", {"query": "MCP"})
    )

    assert payload["structured_content"] == structured_value
    assert payload["raw"]["structured_content"] == structured_value
    assert mcp_utils._normalize_structured_content_for_mcp_app(structured_value) == structured_value


def test_tool_result_metadata_is_preserved_for_client_apps(monkeypatch):
    """MCP result metadata reaches app clients without entering model text."""
    result = SimpleNamespace(
        content=[],
        structured_content=None,
        is_error=False,
        meta={"ui/session": {"view": "details"}},
    )

    class FakeClient:
        async def call_tool(self, _name, _arguments, **_kwargs):
            return result

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeClient()

    monkeypatch.setattr(mcp_utils, "_mcp_session", fake_session)

    payload = asyncio.run(mcp_utils._call_server_tool_async(_server(), "search", {}))

    assert payload["meta"] == {"ui/session": {"view": "details"}}
    assert payload["raw"]["meta"] == payload["meta"]
    assert "text" not in payload


def test_modern_tool_call_lists_schema_on_same_client_before_execution(monkeypatch):
    """The SDK needs the live schema to emit x-mcp-header transport headers."""
    calls = []

    class FakeClient:
        protocol_version = "2026-07-28"

        async def list_tools(self, cursor=None, **_kwargs):
            calls.append(("list", cursor))
            return SimpleNamespace(tools=[], nextCursor=None)

        async def call_tool(self, name, arguments, **_kwargs):
            calls.append(("call", name, arguments))
            return SimpleNamespace(content=[], structured_content=None, is_error=False)

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeClient()

    monkeypatch.setattr(mcp_utils, "_mcp_session", fake_session)

    asyncio.run(mcp_utils._call_server_tool_async(_server(), "search", {"query": "MCP"}))

    assert calls == [("list", None), ("call", "search", {"query": "MCP"})]


def test_modern_discovery_respects_zero_ttl(monkeypatch):
    """A modern server can require discovery on every new connection."""
    server = _server()
    tools = [{"tool_name": "search"}]

    def run_discovery(coroutine):
        coroutine.close()
        return tools, {
            "protocol_version": "2026-07-28",
            "ttl_ms": 0,
            "cache_scope": "private",
        }

    monkeypatch.setattr(mcp_utils, "_prepare_server_for_runtime", lambda _db, value: value)
    monkeypatch.setattr(mcp_utils, "_run_async", run_discovery)
    monkeypatch.setattr(mcp_utils, "_set_server_status", lambda *_args, **_kwargs: None)
    mcp_utils._DISCOVERY_CACHE.clear()

    assert mcp_utils.discover_server_tools(object(), server) == tools
    assert mcp_utils._DISCOVERY_CACHE == {}


def test_v2_client_negotiates_modern_protocol_and_calls_tool():
    """Exercise Omlorix's real high-level client against an in-process v2 server."""
    from mcp.server import MCPServer

    async def connect_and_call():
        fixture_server = MCPServer("Omlorix integration fixture")

        @fixture_server.tool()
        def echo(value: str) -> str:
            """Return a value so negotiation, listing, and execution are covered."""
            return value

        async with mcp_utils._new_mcp_client(
            fixture_server,
            _server(timeout_seconds=5),
        ) as client:
            listed = await client.list_tools()
            called = await client.call_tool("echo", {"value": "hello"})
            return client.protocol_version, listed, called

    protocol_version, listed, called = asyncio.run(connect_and_call())

    assert protocol_version == "2026-07-28"
    assert [tool.name for tool in listed.tools] == ["echo"]
    assert called.is_error is False
    assert called.content[0].text == "hello"


def test_v2_client_advertises_standard_mcp_apps_identifier():
    """UI-capable servers must see the standardized Apps extension ID."""
    from mcp.server import MCPServer

    async def capabilities():
        async with mcp_utils._new_mcp_client(
            MCPServer("Omlorix capability fixture"),
            _server(timeout_seconds=5),
        ) as client:
            return client.session._build_capabilities("2026-07-28").model_dump(
                by_alias=True,
                exclude_none=True,
            )

    advertised = asyncio.run(capabilities())

    assert "io.modelcontextprotocol/ui" in advertised["extensions"]
    assert "io.modelcontextprotocol/apps" not in advertised["extensions"]
    assert "io.modelcontextprotocol/tasks" in advertised["extensions"]
    assert advertised["elicitation"] == {"form": {}, "url": {}}
    assert advertised["roots"] == {"listChanged": True}


def test_tasks_extension_polls_created_task_to_tool_result(monkeypatch):
    """A task handle is resolved through tasks/get into the original result."""
    from app.mcp.tasks import CreateTaskResult, resolve_task
    from mcp.client.extension import ClaimContext

    completed = {
        "taskId": "task-1",
        "status": "completed",
        "createdAt": "2026-08-01T10:00:00Z",
        "lastUpdatedAt": "2026-08-01T10:00:01Z",
        "ttlMs": 60_000,
        "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": "finished"}],
            "isError": False,
        },
    }

    class FakeSession:
        async def send_request(self, request, result_type, **_kwargs):
            assert request.method == "tasks/get"
            return result_type.validate_python(completed)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.mcp.tasks.asyncio.sleep", no_sleep)
    created = CreateTaskResult.model_validate(
        {
            "resultType": "task",
            "taskId": "task-1",
            "status": "working",
            "createdAt": "2026-08-01T10:00:00Z",
            "lastUpdatedAt": "2026-08-01T10:00:00Z",
            "ttlMs": 60_000,
            "pollIntervalMs": 1,
        }
    )

    result = asyncio.run(
        resolve_task(
            created,
            ClaimContext(
                session=FakeSession(),
                tool_name="build",
                read_timeout_seconds=5,
            ),
        )
    )

    assert result.is_error is False
    assert result.content[0].text == "finished"


def test_tasks_extension_deduplicates_eventually_consistent_input_requests(monkeypatch):
    """Repeated task input keys are answered once while an update propagates."""
    from app.mcp.tasks import CreateTaskResult, resolve_task
    from mcp.client.extension import ClaimContext
    from mcp_types import ElicitResult, Result

    input_required = {
        "resultType": "complete",
        "taskId": "task-input",
        "status": "input_required",
        "createdAt": "2026-08-01T10:00:00Z",
        "lastUpdatedAt": "2026-08-01T10:00:01Z",
        "ttlMs": 60_000,
        "pollIntervalMs": 100,
        "inputRequests": {
            "name": {
                "method": "elicitation/create",
                "params": {
                    "mode": "form",
                    "message": "Name?",
                    "requestedSchema": {"type": "object"},
                },
            }
        },
    }
    completed = {
        "resultType": "complete",
        "taskId": "task-input",
        "status": "completed",
        "createdAt": "2026-08-01T10:00:00Z",
        "lastUpdatedAt": "2026-08-01T10:00:02Z",
        "ttlMs": 60_000,
        "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": "finished"}],
            "isError": False,
        },
    }

    class FakeSession:
        def __init__(self):
            self.get_results = [input_required, input_required, completed]
            self.dispatched = []
            self.update_count = 0

        async def send_request(self, request, result_type, **_kwargs):
            if request.method == "tasks/update":
                self.update_count += 1
                return Result()
            assert request.method == "tasks/get"
            return result_type.validate_python(self.get_results.pop(0))

        async def dispatch_input_request(self, _context, request):
            self.dispatched.append(request)
            return ElicitResult(action="decline")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.mcp.tasks.asyncio.sleep", no_sleep)
    session = FakeSession()
    created = CreateTaskResult.model_validate(
        {
            "resultType": "task",
            "taskId": "task-input",
            "status": "working",
            "createdAt": "2026-08-01T10:00:00Z",
            "lastUpdatedAt": "2026-08-01T10:00:00Z",
            "ttlMs": 60_000,
            "pollIntervalMs": 100,
        }
    )

    result = asyncio.run(
        resolve_task(
            created,
            ClaimContext(
                session=session,
                tool_name="build",
                read_timeout_seconds=5,
            ),
        )
    )

    assert result.content[0].text == "finished"
    assert len(session.dispatched) == 1
    assert session.update_count == 1


def test_task_ttl_is_optional_and_waits_use_a_bounded_default():
    """Omitted ttlMs remains nullable while still receiving an overall budget."""
    from app.mcp.tasks import (
        CreateTaskResult,
        TaskDescriptor,
        _DEFAULT_TASK_WAIT_BUDGET_SECONDS,
        _task_wait_budget_seconds,
    )

    common = {
        "taskId": "task-no-ttl",
        "status": "working",
        "createdAt": "2026-08-01T10:00:00Z",
        "lastUpdatedAt": "2026-08-01T10:00:00Z",
    }

    assert CreateTaskResult.model_validate(common).ttl_ms is None
    assert TaskDescriptor.model_validate(common).ttl_ms is None
    assert _task_wait_budget_seconds(None) == _DEFAULT_TASK_WAIT_BUDGET_SECONDS


def test_task_wait_deadline_cancels_an_expired_task(monkeypatch):
    """Repeated polling and input states cannot outlive the advertised TTL."""
    from app.mcp.tasks import CreateTaskResult, resolve_task
    from mcp.client.extension import ClaimContext
    from mcp.types import Result

    class FakeSession:
        def __init__(self):
            self.cancelled = False

        async def send_request(self, request, result_type, **_kwargs):
            if request.method == "tasks/cancel":
                self.cancelled = True
                return Result()
            raise AssertionError("an expired task must not be polled")

    monkeypatch.setattr(
        "app.mcp.tasks.time",
        SimpleNamespace(monotonic=lambda: 100.0),
    )
    created = CreateTaskResult.model_validate(
        {
            "taskId": "task-expired",
            "status": "working",
            "createdAt": "2026-08-01T10:00:00Z",
            "lastUpdatedAt": "2026-08-01T10:00:00Z",
            "ttlMs": 0,
        }
    )
    session = FakeSession()

    with pytest.raises(TimeoutError, match="advertised wait budget"):
        asyncio.run(
            resolve_task(
                created,
                ClaimContext(
                    session=session,
                    tool_name="build",
                    read_timeout_seconds=5,
                ),
            )
        )

    assert session.cancelled is True


def test_task_deadline_extends_only_for_a_larger_reported_ttl():
    """A server may extend task lifetime without creating a sliding deadline."""
    from app.mcp.tasks import TaskDescriptor, _extend_task_deadline

    task = TaskDescriptor.model_validate(
        {
            "taskId": "task-extension",
            "status": "working",
            "createdAt": "2026-08-01T10:00:00Z",
            "lastUpdatedAt": "2026-08-01T10:00:01Z",
            "ttlMs": 20_000,
        }
    )

    largest, deadline = _extend_task_deadline(
        task,
        wait_started_at=100.0,
        largest_ttl_ms=10_000,
        deadline=110.0,
    )
    assert (largest, deadline) == (20_000, 120.0)
    assert _extend_task_deadline(
        task,
        wait_started_at=105.0,
        largest_ttl_ms=largest,
        deadline=deadline,
    ) == (20_000, 120.0)
