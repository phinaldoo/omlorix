from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp import utils as mcp_utils  # noqa: E402
from app.mcp.models import OWNER_ADMIN, TRANSPORT_STREAMABLE_HTTP, serialize_mcp_server  # noqa: E402
from app.mcp.schemas import (  # noqa: E402
    MCPServerDetail,
    UpdateMCPServerRequest,
)


def _server(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": "server-1",
        "owner_type": OWNER_ADMIN,
        "owner_user_id": None,
        "name": "MCP",
        "icon": "",
        "description": None,
        "namespace": None,
        "transport": TRANSPORT_STREAMABLE_HTTP,
        "enabled": True,
        "url": "https://mcp.example.com",
        "command": None,
        "args": [],
        "headers": {"Authorization": "Bearer secret"},
        "auth_mode": "headers",
        "oauth": {},
        "env": {"API_KEY": "secret"},
        "allowed_tools": [],
        "timeout_seconds": 30,
        "managed_connection_id": None,
        "status": {},
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_mcp_serializer_never_returns_plaintext_secrets():
    payload = serialize_mcp_server(_server())

    assert payload["headers"] == {}
    assert payload["secret_summary"]["header_count"] == 1
    assert payload["secret_summary"]["secrets_included"] is False
    assert "Bearer secret" not in str(payload)
    assert "API_KEY" not in str(payload)
    detail = MCPServerDetail.model_validate(payload)
    assert detail.secret_summary.updated_at == payload["secret_summary"]["updated_at"]


def test_admin_export_redacts_mcp_secrets(monkeypatch):
    monkeypatch.setattr(
        mcp_utils,
        "list_mcp_servers",
        lambda *_args, **_kwargs: [
            _server(
                oauth={"access_token": "oauth-secret"},
                status={"last_error": "runtime-only"},
            )
        ],
    )
    created_payloads = []

    def fake_create(_db, payload):
        created_payloads.append(payload.model_dump())
        return {
            "id": "restored-server",
            "name": payload.name,
            "transport": payload.transport,
            "enabled": payload.enabled,
        }

    monkeypatch.setattr(mcp_utils, "create_admin_mcp_server", fake_create)

    bundle = mcp_utils.export_admin_servers_bundle(object())

    assert bundle["secrets_included"] is False
    exported_server = bundle["data"]["servers"][0]
    assert exported_server["id"] == "server-1"
    assert exported_server["headers"] == {}
    assert "secret_summary" not in exported_server
    assert {
        "oauth",
        "env",
        "status",
        "command",
        "args",
    }.isdisjoint(exported_server)
    assert "Bearer secret" not in str(bundle)
    assert "oauth-secret" not in str(bundle)
    assert "API_KEY" not in str(bundle)

    result = mcp_utils.import_admin_servers_bundle(object(), bundle)

    assert result == {
        "created": [
            {
                "id": "restored-server",
                "name": "MCP",
                "transport": TRANSPORT_STREAMABLE_HTTP,
                "enabled": True,
            }
        ],
        "errors": [],
    }
    assert created_payloads[0]["owner_type"] == OWNER_ADMIN
    assert created_payloads[0]["url"] == "https://mcp.example.com"
    assert "id" not in created_payloads[0]


def test_update_payload_omits_unset_secret_maps():
    payload = UpdateMCPServerRequest(
        name="MCP",
        transport=TRANSPORT_STREAMABLE_HTTP,
        url="https://mcp.example.com",
    )

    updates = mcp_utils._build_mcp_server_update_payload(payload)

    assert "headers" not in updates


def test_explicit_empty_update_payload_can_clear_secret_maps():
    payload = UpdateMCPServerRequest(
        name="MCP",
        transport=TRANSPORT_STREAMABLE_HTTP,
        url="https://mcp.example.com",
        headers={},
    )

    updates = mcp_utils._build_mcp_server_update_payload(payload)

    assert updates["headers"] == {}


def test_omitted_test_secret_maps_reuse_existing_server_values():
    payload = UpdateMCPServerRequest(
        name="MCP",
        transport=TRANSPORT_STREAMABLE_HTTP,
        url="https://mcp.example.com",
    )

    headers = mcp_utils.resolve_mcp_headers_from_payload(payload, _server())

    assert headers == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    ("draft_url", "draft_auth_mode"),
    [
        ("https://attacker.example.com", "oauth"),
        ("https://mcp.example.com", "headers"),
    ],
)
def test_test_draft_never_reuses_saved_credentials_outside_original_scope(
    draft_url,
    draft_auth_mode,
):
    """Changing either credential scope dimension clears redacted secrets."""

    saved = _server(
        auth_mode="oauth",
        oauth={"access_token": "saved-oauth-token"},
    )
    payload = UpdateMCPServerRequest(
        name="MCP",
        transport=TRANSPORT_STREAMABLE_HTTP,
        url=draft_url,
        auth_mode=draft_auth_mode,
    )

    assert mcp_utils.resolve_mcp_headers_from_payload(payload, saved) == {}
    assert mcp_utils.resolve_mcp_oauth_from_payload(payload, saved) == {}


def test_test_draft_reuses_saved_oauth_only_for_identical_scope():
    """An unchanged saved OAuth server remains testable with redacted fields."""

    saved = _server(
        auth_mode="oauth",
        oauth={"access_token": "saved-oauth-token"},
    )
    payload = UpdateMCPServerRequest(
        name="MCP",
        transport=TRANSPORT_STREAMABLE_HTTP,
        url="https://mcp.example.com",
        auth_mode="oauth",
    )

    assert mcp_utils.resolve_mcp_headers_from_payload(payload, saved) == {
        "Authorization": "Bearer secret"
    }
    assert mcp_utils.resolve_mcp_oauth_from_payload(payload, saved) == {
        "access_token": "saved-oauth-token"
    }
