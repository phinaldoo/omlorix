import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.model_schemas import apply_model_mcp_schema_values, get_model_schema_tools_section
from app.llm.utils import _build_model_select_connections
from app.mcp.utils import (
    ALLOW_ALL_USER_MCPS,
    _classify_mcp_attachment,
    _filter_servers_for_settings,
    build_connection_provider_mcp_value,
    get_mcp_server_options_for_user,
    list_mcp_mention_connectors,
    get_model_allowed_connection_providers,
    model_allows_custom_user_mcp_servers,
)


def test_mcp_svg_attachment_is_classified_as_text_document():
    """MCP SVG output must not enter provider-native image input paths."""
    assert _classify_mcp_attachment("image/svg+xml", "image") == "document"
    assert _classify_mcp_attachment("image/svg+xml; charset=utf-8", "image") == "document"
    assert _classify_mcp_attachment("image/png", "image") == "image"


def _field_map(schema):
    return {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }


def test_model_tools_schema_includes_connection_options_and_custom_toggle():
    admin_servers = [
        SimpleNamespace(id="admin-1", name="Admin Search", enabled=True),
    ]
    connection_catalog = [
        {"provider": "github", "title": "GitHub"},
        {"provider": "notion", "title": "Notion"},
    ]

    with patch("app.tools.utils.list_available_tool_options", return_value=[]), patch(
        "app.mcp.models.list_mcp_servers", return_value=admin_servers
    ), patch(
        "app.tools.websearch.models.list_websearch_providers_with_types", return_value=[]
    ), patch(
        "app.connections.service.list_managed_connection_mcp_catalog", return_value=connection_catalog
    ):
        schema = get_model_schema_tools_section(db=None)

    fields = _field_map(schema)
    tools_field = fields["tools"]
    allowed_field = fields["settings.allowed_mcp_servers"]
    toggle_field = fields["settings.allow_custom_user_mcp_servers"]
    option_values = {option.value for option in allowed_field.options}

    assert tools_field.multiple is True
    assert tools_field.searchable is True
    assert "admin-1" in option_values
    assert build_connection_provider_mcp_value("github") in option_values
    assert build_connection_provider_mcp_value("notion") in option_values
    assert toggle_field.type == "boolean"
    assert toggle_field.value is True


def test_apply_model_mcp_schema_values_preserves_legacy_allow_all_user_flag():
    with patch("app.tools.utils.list_available_tool_options", return_value=[]), patch(
        "app.mcp.models.list_mcp_servers", return_value=[]
    ), patch(
        "app.tools.websearch.models.list_websearch_providers_with_types", return_value=[]
    ), patch(
        "app.connections.service.list_managed_connection_mcp_catalog",
        return_value=[{"provider": "github", "title": "GitHub"}],
    ):
        schema = get_model_schema_tools_section(db=None)
        apply_model_mcp_schema_values(
            schema,
            {
                "allowed_mcp_servers": ["admin-2", ALLOW_ALL_USER_MCPS],
            },
        )

    fields = _field_map(schema)

    assert fields["settings.allowed_mcp_servers"].value == sorted(
        [
            "admin-2",
            build_connection_provider_mcp_value("github"),
        ]
    )
    assert fields["settings.allow_custom_user_mcp_servers"].value is True
    assert model_allows_custom_user_mcp_servers({"allowed_mcp_servers": ["admin-2"]}) is False


def test_apply_model_mcp_schema_values_drops_unavailable_connection_provider_values():
    with patch("app.tools.utils.list_available_tool_options", return_value=[]), patch(
        "app.mcp.models.list_mcp_servers", return_value=[]
    ), patch(
        "app.tools.websearch.models.list_websearch_providers_with_types", return_value=[]
    ), patch(
        "app.connections.service.list_managed_connection_mcp_catalog",
        return_value=[{"provider": "github", "title": "GitHub"}],
    ):
        schema = get_model_schema_tools_section(db=None)
        apply_model_mcp_schema_values(
            schema,
            {
                "allowed_mcp_servers": [
                    "admin-server-id",
                    build_connection_provider_mcp_value("github"),
                    build_connection_provider_mcp_value("slack"),
                ],
            },
        )

    fields = _field_map(schema)

    assert fields["settings.allowed_mcp_servers"].value == [
        build_connection_provider_mcp_value("github"),
        "admin-server-id",
    ]


def test_filter_servers_for_settings_distinguishes_admin_connections_and_custom_user_mcps():
    servers = [
        SimpleNamespace(id="admin-1", owner_type="admin", managed_connection_id=None),
        SimpleNamespace(id="admin-2", owner_type="admin", managed_connection_id=None),
        SimpleNamespace(id="user-conn-github", owner_type="user", managed_connection_id="conn-github"),
        SimpleNamespace(id="user-conn-notion", owner_type="user", managed_connection_id="conn-notion"),
        SimpleNamespace(id="user-custom", owner_type="user", managed_connection_id=None),
    ]
    settings = {
        "allowed_mcp_servers": [
            "admin-2",
            build_connection_provider_mcp_value("github"),
        ],
        "allow_custom_user_mcp_servers": False,
    }

    with patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"conn-github": "github", "conn-notion": "notion"},
    ):
        filtered = _filter_servers_for_settings(
            db=None,
            servers=servers,
            model_settings=settings,
            apply_request_selection=False,
        )

    assert [server.id for server in filtered] == ["admin-2", "user-conn-github"]


def test_missing_or_empty_mcp_selection_disables_all_servers_by_default():
    """Every runtime request must explicitly opt in to each MCP server."""
    servers = [
        SimpleNamespace(id="admin-1", owner_type="admin", managed_connection_id=None),
        SimpleNamespace(id="admin-2", owner_type="admin", managed_connection_id=None),
    ]

    assert _filter_servers_for_settings(None, servers, {}) == []
    assert _filter_servers_for_settings(
        None,
        servers,
        {"enabled_mcp_servers": []},
    ) == []
    assert [server.id for server in _filter_servers_for_settings(
        None,
        servers,
        {"enabled_mcp_servers": ["admin-2"]},
    )] == ["admin-2"]


def test_mcp_server_selector_defaults_to_no_available_server():
    """The selector must visually represent request-level opt-in access."""
    from app.llm.model_schemas import get_parameter_basic_schema

    available_servers = [
        {"value": "admin-1", "label": "Admin (Server)"},
        {"value": "personal-1", "label": "Personal (Server)"},
    ]
    with patch(
        "app.users.models.get_user",
        return_value=SimpleNamespace(group_id="group-1"),
    ), patch(
        "app.groups.models.get_group",
        return_value=SimpleNamespace(settings={}),
    ), patch("app.mcp.utils.get_mcp_server_options_for_user", return_value=available_servers):
        schema = get_parameter_basic_schema(
            db=object(),
            user_id="user-1",
            project_id=None,
            tool_names=["mcp"],
            enabled_tools_value=["mcp"],
            model_settings={},
        )

    fields = _field_map(schema)
    assert fields["settings.enabled_tools"].searchable is True
    assert fields["settings.enabled_mcp_servers"].value == []


def test_mcp_mention_connectors_list_all_eligible_choices_without_request_selection():
    """Mention discovery must ignore a prior request allowlist without leaking details."""
    servers = [
        SimpleNamespace(
            id="admin-1",
            name="Notion",
            icon="notion",
            description="Search specs",
            managed_connection_id=None,
        ),
        SimpleNamespace(
            id="admin-2",
            name="GitHub",
            icon="",
            description=None,
            managed_connection_id="connection-2",
        ),
    ]
    with patch("app.mcp.utils.list_accessible_mcp_servers", return_value=servers), patch(
        "app.mcp.utils._filter_servers_for_settings", return_value=servers
    ) as filter_servers, patch(
        "app.mcp.utils._managed_connection_provider_map",
        return_value={"connection-2": "github"},
    ):
        result = list_mcp_mention_connectors(
            object(),
            "user-1",
            model_settings={"enabled_mcp_servers": ["admin-1"]},
        )

    assert result == [
        {
            "id": "admin-1",
            "name": "Notion",
            "provider": "",
            "icon": "notion",
            "description": "Search specs",
        },
        {
            "id": "admin-2",
            "name": "GitHub",
            "provider": "github",
            "icon": "",
            "description": "",
        },
    ]
    filtered_settings = filter_servers.call_args.args[2]
    assert "enabled_mcp_servers" not in filtered_settings
    assert filter_servers.call_args.kwargs["apply_request_selection"] is False


def test_mcp_server_selector_uses_server_label_for_every_backing_type():
    """Ownership must not leak into the conversation-facing server labels."""
    servers = [
        SimpleNamespace(id="admin-1", name="Admin MCP", owner_type="admin", managed_connection_id=None),
        SimpleNamespace(id="personal-1", name="Personal MCP", owner_type="user", managed_connection_id=None),
        SimpleNamespace(id="connection-1", name="Gmail", owner_type="user", managed_connection_id="connection-1"),
    ]

    with patch("app.mcp.utils.list_accessible_mcp_servers", return_value=servers), patch(
        "app.mcp.utils._filter_servers_for_settings", return_value=servers
    ):
        options = get_mcp_server_options_for_user(object(), "user-1")

    assert [option["label"] for option in options] == [
        "Admin MCP (Server)",
        "Personal MCP (Server)",
        "Gmail (Server)",
    ]


def test_model_allowed_connection_providers_matches_runtime_default_and_restrictions():
    catalog = [
        {"provider": "github", "title": "GitHub"},
        {"provider": "notion", "title": "Notion"},
    ]

    with patch(
        "app.connections.service.list_managed_connection_mcp_catalog",
        return_value=catalog,
    ):
        assert get_model_allowed_connection_providers({}) == {"github", "notion"}
        assert get_model_allowed_connection_providers(
            {
                "allowed_mcp_servers": [
                    "admin-server-id",
                    build_connection_provider_mcp_value("github"),
                ]
            }
        ) == {"github"}


def test_model_select_connections_require_mcp_and_follow_unrestricted_default():
    group_catalog = [
        {"provider": "github", "title": "GitHub"},
        {"provider": "notion", "title": "Notion"},
    ]

    with patch(
        "app.connections.service.list_managed_connection_mcp_catalog",
        return_value=group_catalog,
    ):
        assert _build_model_select_connections({}, ["mcp"], group_catalog) == group_catalog
        assert _build_model_select_connections({}, ["web_search"], group_catalog) == []


def test_mcp_app_bridge_requires_token_and_scopes_resource_and_tool():
    from fastapi import HTTPException

    from app.mcp import utils as mcp_utils

    token_secret = "x" * 32
    db = object()
    server = SimpleNamespace(
        id="server-1",
        owner_type="admin",
        owner_user_id=None,
        name="Admin MCP",
        enabled=True,
        allowed_tools=[],
        transport="stdio",
        headers={},
    )

    with patch("app.mcp.utils.get_jwt_material", return_value=(token_secret, "HS512")), patch(
        "app.mcp.utils.get_accessible_mcp_server_for_user", return_value=server
    ), patch(
        "app.mcp.utils.get_redis_client", return_value=None
    ), patch(
        "app.mcp.utils.discover_server_tools",
        return_value=[
            {"tool_name": "safe_tool", "input_schema": {}},
            {"tool_name": "dangerous_tool", "input_schema": {}},
        ],
    ), patch(
        "app.mcp.utils.list_server_resources",
        return_value=[
            {"uri": "ui://app", "mime_type": "text/html;profile=mcp-app"},
            {"uri": "secret://token", "mime_type": "text/plain"},
        ],
    ), patch(
        "app.mcp.utils.read_server_resource",
        return_value={"uri": "ui://app", "mime_type": "text/html;profile=mcp-app", "text": "<html></html>"},
    ), patch(
        "app.mcp.utils.call_mcp_tool",
        return_value={"text": "ok", "structured_content": None, "is_error": False, "raw": {}},
    ) as call_tool, patch(
        "app.mcp.utils._enforce_mcp_app_tool_rate_limit",
    ) as enforce_rate_limit:
        token = mcp_utils._build_mcp_app_access_token(
            db,
            user_id="user-1",
            server_id="server-1",
            resource_uri="ui://app",
            tool_name="safe_tool",
            access_server_ids=["server-1"],
            tool_call_id="call-1",
        )
        token_payload = mcp_utils._verify_mcp_app_access_token(
            db,
            user_id="user-1",
            server_id="server-1",
            app_access_token=token,
            tool_call_id="call-1",
        )
        assert token_payload["exp"] > token_payload["iat"]
        assert token_payload["jti"]
        assert token_payload["nonce"]
        assert token_payload["tool_call_id"] == "call-1"

        with pytest.raises(HTTPException) as missing_token:
            mcp_utils.list_mcp_app_tools_payload(db, user_id="user-1", server_id="server-1")
        assert missing_token.value.status_code == 403

        with pytest.raises(HTTPException) as malformed_token:
            mcp_utils.list_mcp_app_tools_payload(
                db,
                user_id="user-1",
                server_id="server-1",
                tool_call_id="call-1",
                app_access_token="not-ascii.å",
            )
        assert malformed_token.value.status_code == 403

        with pytest.raises(HTTPException) as wrong_tool_call_id:
            mcp_utils.list_mcp_app_tools_payload(
                db,
                user_id="user-1",
                server_id="server-1",
                tool_call_id="call-2",
                app_access_token=token,
            )
        assert wrong_tool_call_id.value.status_code == 403

        tools = mcp_utils.list_mcp_app_tools_payload(
            db,
            user_id="user-1",
            server_id="server-1",
            tool_call_id="call-1",
            app_access_token=token,
        )
        assert [tool["name"] for tool in tools["tools"]] == ["safe_tool"]

        resources = mcp_utils.list_mcp_app_resources_payload(
            db,
            user_id="user-1",
            server_id="server-1",
            tool_call_id="call-1",
            app_access_token=token,
        )
        assert [resource["uri"] for resource in resources["resources"]] == ["ui://app"]

        with pytest.raises(HTTPException) as wrong_resource:
            mcp_utils.read_mcp_app_resource_payload(
                db,
                user_id="user-1",
                server_id="server-1",
                uri="secret://token",
                tool_call_id="call-1",
                app_access_token=token,
            )
        assert wrong_resource.value.status_code == 403

        with pytest.raises(HTTPException) as wrong_tool:
            mcp_utils.call_mcp_app_tool_payload(
                db,
                user_id="user-1",
                group_id="group-1",
                server_id="server-1",
                tool_name="dangerous_tool",
                arguments={},
                tool_call_id="call-1",
                app_access_token=token,
            )
        assert wrong_tool.value.status_code == 403

        result = mcp_utils.call_mcp_app_tool_payload(
            db,
            user_id="user-1",
            group_id="group-1",
            server_id="server-1",
            tool_name="safe_tool",
            arguments={"ok": True},
            tool_call_id="call-1",
            app_access_token=token,
        )
        assert result["content"] == [{"type": "text", "text": "ok"}]
        second_result = mcp_utils.call_mcp_app_tool_payload(
            db,
            user_id="user-1",
            group_id="group-1",
            server_id="server-1",
            tool_name="safe_tool",
            arguments={"ok": True},
            tool_call_id="call-1",
            app_access_token=token,
        )
        assert second_result["content"] == [{"type": "text", "text": "ok"}]
        assert call_tool.call_count == 2
        assert enforce_rate_limit.call_count == 2


def test_mcp_app_token_rejects_expired_payload():
    from app.mcp import utils as mcp_utils

    now = int(time.time())
    payload = {
        "v": 1,
        "user_id": "user-1",
        "server_id": "server-1",
        "resource_uri": "ui://app",
        "tool_names": ["safe_tool"],
        "access_server_ids": ["server-1"],
        "iat": now - 20,
        "exp": now - 1,
        "jti": "expired-jti",
        "nonce": "expired-nonce",
    }
    token = mcp_utils._sign_mcp_app_token_payload(payload, "secret")

    with patch("app.mcp.utils.get_jwt_material", return_value=("secret", "HS512")), patch(
        "app.mcp.utils.get_redis_client", return_value=None
    ), pytest.raises(HTTPException) as exc:
        mcp_utils._verify_mcp_app_access_token(
            object(),
            user_id="user-1",
            server_id="server-1",
            app_access_token=token,
            tool_call_id="call-1",
        )

    assert exc.value.status_code == 403


def test_mcp_app_token_refreshes_expired_signed_payload_without_expanding_scope():
    from app.mcp import utils as mcp_utils

    now = int(time.time())
    payload = {
        "v": 1,
        "user_id": "user-1",
        "server_id": "server-1",
        "resource_uri": "ui://app",
        "tool_names": ["safe_tool"],
        "tool_call_id": "call-1",
        "access_server_ids": ["server-1", "server-2"],
        "iat": now - 120,
        "exp": now - 1,
        "jti": "expired-refresh-jti",
        "nonce": "expired-refresh-nonce",
    }
    token = mcp_utils._sign_mcp_app_token_payload(payload, "secret")
    server = SimpleNamespace(id="server-1", name="Admin MCP")

    with patch("app.mcp.utils.get_jwt_material", return_value=("secret", "HS512")), patch(
        "app.mcp.utils.get_redis_client", return_value=None
    ), patch(
        "app.mcp.utils.get_accessible_mcp_server_for_user", return_value=server
    ) as get_server:
        refreshed = mcp_utils.refresh_mcp_app_access_token_payload(
            object(),
            user_id="user-1",
            server_id="server-1",
            app_access_token=token,
            tool_call_id="call-1",
        )
        refreshed_payload = mcp_utils._verify_mcp_app_access_token(
            object(),
            user_id="user-1",
            server_id="server-1",
            app_access_token=refreshed["app_access_token"],
            tool_call_id="call-1",
        )
        with pytest.raises(HTTPException) as replay:
            mcp_utils._verify_mcp_app_access_token(
                object(),
                user_id="user-1",
                server_id="server-1",
                app_access_token=token,
                tool_call_id="call-1",
                allow_expired=True,
            )

    get_server.assert_called_once()
    assert refreshed_payload["resource_uri"] == "ui://app"
    assert refreshed_payload["tool_names"] == ["safe_tool"]
    assert refreshed_payload["access_server_ids"] == ["server-1", "server-2"]
    assert refreshed_payload["orig_iat"] == payload["iat"]
    assert refreshed_payload["exp"] > now
    assert replay.value.status_code == 403


def test_mcp_app_frame_payload_serves_html_with_authoritative_csp():
    from app.mcp import utils as mcp_utils

    now = int(time.time())
    payload = {
        "v": 1,
        "user_id": "user-1",
        "server_id": "server-1",
        "resource_uri": "ui://app",
        "tool_names": ["safe_tool"],
        "tool_call_id": "call-1",
        "access_server_ids": ["server-1"],
        "iat": now - 1,
        "exp": now + 120,
        "jti": "frame-jti",
        "nonce": "frame-nonce",
    }
    token = mcp_utils._sign_mcp_app_token_payload(payload, "secret")
    server = SimpleNamespace(id="server-1", name="Admin MCP")
    html = (
        '<html><head><meta http-equiv="Content-Security-Policy" '
        'content="default-src none; script-src none"></head>'
        "<body><script>window.ready = true;</script></body></html>"
    )

    with patch("app.mcp.utils.get_jwt_material", return_value=("secret", "HS512")), patch(
        "app.mcp.utils.get_redis_client", return_value=None
    ), patch("app.mcp.utils.get_accessible_mcp_server_for_user", return_value=server):
        frame = mcp_utils.create_mcp_app_frame_payload(
            object(),
            user_id="user-1",
            server_id="server-1",
            html=html,
            resource_meta={
                "csp": {
                    "resourceDomains": ["https://esm.sh", "https://evil.example; script-src *"],
                    "connectDomains": ["https://api.example.com"],
                }
            },
            app_access_token=token,
            tool_call_id="call-1",
        )
        served = mcp_utils.get_mcp_app_frame_payload(frame["frame_id"])

    assert frame["frame_url"].endswith(frame["frame_id"])
    assert "script-src none" not in served["html"]
    assert "Content-Security-Policy" in served["html"]
    assert "frame-ancestors" not in served["html"]
    assert "sandbox" not in served["html"]
    assert "sandbox allow-scripts allow-forms allow-popups allow-downloads" in served["headers"]["Content-Security-Policy"]
    assert "allow-same-origin" not in served["headers"]["Content-Security-Policy"]
    assert "https://esm.sh" in served["headers"]["Content-Security-Policy"]
    assert "https://api.example.com" in served["headers"]["Content-Security-Policy"]
    assert "https://evil.example;" not in served["headers"]["Content-Security-Policy"]
    assert "'unsafe-inline'" in served["headers"]["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in served["headers"]["Content-Security-Policy"]
    assert served["headers"]["Referrer-Policy"] == "no-referrer"
    assert served["headers"]["X-Frame-Options"] == "SAMEORIGIN"


def test_mcp_app_sandbox_proxy_uses_http_response_csp_and_frame_url():
    """The proxy must run from HTTP and navigate the nested app by URL.

    Keeping the bridge script out of a data URL prevents Safari from applying
    Omlorix's stricter parent-page script policy to the proxy bootstrap.
    """
    from app.mcp import utils as mcp_utils

    served = mcp_utils.get_mcp_app_sandbox_proxy_payload()
    html = served["html"]
    csp = served["headers"]["Content-Security-Policy"]

    assert "sandbox-proxy-ready" in html
    assert "view.src = url" in html
    assert "view.srcdoc" not in html
    assert "script-src 'unsafe-inline'" in csp
    assert "frame-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "data:" not in csp
    assert served["headers"]["Cache-Control"] == "no-store, private"
    assert served["headers"]["X-Frame-Options"] == "SAMEORIGIN"
    assert served["headers"]["Cross-Origin-Resource-Policy"] == "same-origin"


def test_mcp_app_payload_preserves_embedded_resource_csp_meta():
    from app.mcp import utils as mcp_utils

    server = SimpleNamespace(id="server-1", name="Drawing MCP")
    binding = {
        "server": server,
        "tool_name": "draw",
        "title": "Draw",
        "description": "Render a drawing app.",
        "input_schema": {},
        "output_schema": {},
        "ui": {},
        "meta": {},
    }
    result = {
        "structured_content": {"ok": True},
        "raw": {
            "content": [
                {
                    "type": "resource",
                    "_meta": {
                        "openai/widgetCSP": {
                            "resource_domains": ["https://esm.sh"],
                            "connect_domains": ["https://api.example.com"],
                        }
                    },
                    "resource": {
                        "mimeType": "text/html;profile=mcp-app",
                        "text": "<html><body><script>window.ready = true;</script></body></html>",
                    },
                }
            ]
        },
    }

    with patch("app.mcp.utils.get_jwt_material", return_value=("secret", "HS512")):
        payload = mcp_utils._build_mcp_app_payload(
            object(),
            user_id="user-1",
            binding=binding,
            access_server_ids=["server-1"],
            public_name="mcp_drawing_draw",
            arguments={},
            result=result,
            text_output="",
        )

    assert payload is not None
    assert payload["embedded_html"].startswith("<html>")
    assert payload["resource_meta"]["csp"] == {
        "resourceDomains": ["https://esm.sh"],
        "connectDomains": ["https://api.example.com"],
    }
