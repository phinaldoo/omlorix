from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.connections import service
from app.connections.schemas import ConnectionCreateRequest


def _set_oauth_readiness(monkeypatch, *, google=False, github=False, slack=False):
    """Set deterministic provider readiness without reading settings storage."""
    monkeypatch.setattr(service, "google_oauth_is_configured", lambda _db: google)
    monkeypatch.setattr(service, "github_oauth_is_configured", lambda _db: github)
    monkeypatch.setattr(service, "slack_oauth_is_configured", lambda _db: slack)


def test_provider_setup_requires_global_oauth_for_every_managed_connection(monkeypatch):
    _set_oauth_readiness(monkeypatch)
    db = object()

    # Notion owns its dynamic OAuth client registration. GitHub's PAT option is
    # available only after the administrator completes GitHub OAuth setup.
    assert service.connection_provider_is_setup(db, "notion") is True
    assert service.connection_provider_is_setup(db, "github") is False

    assert service.connection_provider_is_setup(db, "gmail") is False
    assert service.connection_provider_is_setup(db, "google_calendar") is False
    assert service.connection_provider_is_setup(db, "google_drive") is False
    assert service.connection_provider_is_setup(db, "onedrive") is False
    assert service.connection_provider_is_setup(db, "sharepoint") is False
    assert service.connection_provider_is_setup(db, "slack") is False

    _set_oauth_readiness(monkeypatch, github=True)
    assert service.connection_provider_is_setup(db, "github") is True


def test_managed_mcp_catalog_omits_unconfigured_oauth_providers(monkeypatch):
    _set_oauth_readiness(monkeypatch)

    configured_items = service.list_managed_connection_mcp_catalog(object())
    configured_providers = {item["provider"] for item in configured_items}

    assert configured_providers == {"notion"}
    # Configuration-independent normalization still has the complete stable
    # provider key set needed to interpret existing model settings.
    assert {item["provider"] for item in service.list_managed_connection_mcp_catalog()} == {
        "github",
        "gmail",
        "google_calendar",
        "notion",
        "slack",
    }


def test_workspace_catalog_hides_unavailable_new_connections(monkeypatch):
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "_group_enabled_connections",
        lambda *_args: list(service._PROVIDER_CATALOG),
    )
    monkeypatch.setattr(service, "_group_allows_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "list_user_connections", lambda *_args: [])
    monkeypatch.setattr(
        service,
        "connection_provider_is_setup",
        lambda _db, provider, **_kwargs: provider == "notion",
    )
    monkeypatch.setattr(
        service,
        "connection_provider_oauth_is_configured",
        lambda _db, provider: provider == "notion",
    )

    payload = service.list_connections_catalog_payload(object(), "user-1")

    assert [item["provider"] for item in payload["items"]] == ["notion"]
    assert payload["items"][0] == {
        "provider": "notion",
        "setup_mode": "oauth",
        "connection_type": "mcp",
        "connection": None,
    }


def test_workspace_catalog_hides_existing_connection_when_global_setup_is_removed(monkeypatch):
    existing = SimpleNamespace(
        id="connection-1",
        provider="gmail",
        secrets={},
    )
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(service, "_group_enabled_connections", lambda *_args: ["gmail"])
    monkeypatch.setattr(service, "list_user_connections", lambda *_args: [existing])
    monkeypatch.setattr(
        service,
        "connection_provider_is_setup",
        lambda _db, _provider, **_kwargs: False,
    )
    monkeypatch.setattr(
        service,
        "connection_provider_oauth_is_configured",
        lambda _db, _provider: False,
    )
    monkeypatch.setattr(
        service,
        "_serialize_catalog_connection_payload",
        lambda connection, **_kwargs: {"id": connection.id, "provider": connection.provider},
    )

    payload = service.list_connections_catalog_payload(object(), "user-1")

    assert payload["items"] == []


def test_file_source_catalog_item_is_not_an_llm_connection(monkeypatch):
    """File adapters advertise the picker surface, never the model surface."""
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(service, "_group_enabled_connections", lambda *_args: ["google_drive"])
    monkeypatch.setattr(service, "_group_allows_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "list_user_connections", lambda *_args: [])
    monkeypatch.setattr(service, "connection_provider_is_setup", lambda _db, _provider, **_kwargs: True)
    monkeypatch.setattr(service, "connection_provider_oauth_is_configured", lambda _db, _provider: True)

    payload = service.list_connections_catalog_payload(object(), "user-1")

    assert len(payload["items"]) == 1
    drive = payload["items"][0]
    assert drive["provider"] == "google_drive"
    assert drive["connection_type"] == "file_source_adapter"
    assert set(drive) == {"provider", "setup_mode", "connection_type", "connection"}


def test_catalog_connection_omits_internal_status_and_backing_server_data():
    """The general catalog exposes state, not operational diagnostics."""
    connection = SimpleNamespace(
        id="connection-1",
        provider="github",
        enabled=True,
        auth_mode="pat",
        secrets={"access_token": "secret"},
        mcp_server_id="private-mcp-server-id",
        status={
            "state": "error",
            "last_error": "unhandled errors in a TaskGroup (private detail)",
            "last_error_code": "",
            "tool_count": 47,
            "tool_names": ["private_tool_name"],
            "checked_at": "2026-08-05T12:42:39Z",
            "last_sync_at": "2026-08-05T12:42:39Z",
        },
        connected_at=None,
        created_at=None,
        updated_at=None,
    )

    payload = service._serialize_catalog_connection_payload(
        connection,
        catalog_provider="github",
    )

    assert payload == {
        "id": "connection-1",
        "enabled": True,
        "connected": True,
        "state": "error",
        "error_code": "github_token_invalid",
    }
    serialized = str(payload)
    assert "secret" not in serialized
    assert "private_tool_name" not in serialized
    assert "private-mcp-server-id" not in serialized
    assert "2026-08-05" not in serialized


def test_file_source_tool_preview_is_rejected(monkeypatch):
    """The tools endpoint cannot be used to turn a file adapter into MCP."""
    connection = SimpleNamespace(provider="google_drive")
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(service, "get_user_connection", lambda *_args: connection)
    monkeypatch.setattr(service, "_group_allows_provider", lambda *_args, **_kwargs: True)

    with pytest.raises(HTTPException, match="file source adapter") as error:
        service.preview_connection_tools_payload(object(), user_id="user-1", connection_id="drive-1")

    assert error.value.status_code == 404


def test_unconfigured_github_cannot_be_connected_through_direct_endpoints(monkeypatch):
    """A stale client cannot bypass the catalog with OAuth or a PAT request."""
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "ensure_group_allows_connection_provider",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(service, "connection_provider_is_setup", lambda *_args, **_kwargs: False)

    with pytest.raises(HTTPException) as oauth_error:
        service.start_connection_oauth(object(), user_id="user-1", provider="github")
    assert oauth_error.value.status_code == 404

    with pytest.raises(HTTPException) as token_error:
        service.create_connection_payload(
            object(),
            user_id="user-1",
            provider="github",
            payload=ConnectionCreateRequest(access_token="github-token"),
        )
    assert token_error.value.status_code == 404
