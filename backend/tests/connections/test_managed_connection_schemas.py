from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.connections import service
from app.connections.models import serialize_user_connection
from app.connections.schemas import ConnectionCreateRequest, ConnectionUpdateRequest


@pytest.mark.parametrize(
    "legacy_field,legacy_value",
    [
        ("display_name", "Personal Notion"),
        ("settings", {"namespace": "personal", "timeout_seconds": 600}),
    ],
)
def test_connection_requests_reject_removed_server_settings(legacy_field, legacy_value):
    """Removed settings cannot silently return through direct API clients."""
    with pytest.raises(ValidationError):
        ConnectionUpdateRequest.model_validate(
            {
                "enabled": True,
                legacy_field: legacy_value,
            }
        )


def test_connection_response_contains_only_user_owned_connection_state():
    """Provider-owned MCP identity and timing stay out of user responses."""
    connection = SimpleNamespace(
        id="connection-1",
        provider="notion",
        enabled=True,
        auth_mode="oauth",
        secrets={"access_token": "secret"},
        status={},
        mcp_server_id="server-1",
        connected_at=None,
        created_at=None,
        updated_at=None,
    )

    payload = serialize_user_connection(connection)

    assert payload["auth_mode"] == "oauth"
    assert "display_name" not in payload
    assert "settings" not in payload
    assert "namespace" not in payload
    assert "timeout_seconds" not in payload


def test_manual_connection_create_accepts_only_access_token():
    payload = ConnectionCreateRequest.model_validate({"access_token": "github-token"})

    assert payload.model_dump() == {"access_token": "github-token"}


def test_token_rotation_keeps_an_omitted_enabled_field_unchanged(monkeypatch):
    """A token-only update must not re-enable a disabled GitHub connection."""
    connection = SimpleNamespace(
        id="connection-1",
        provider="github",
        enabled=False,
        auth_mode="pat",
        secrets={},
        status={},
        mcp_server_id=None,
    )
    update_calls = []

    def update_connection(_db, _connection_id, **updates):
        update_calls.append(updates)
        for key, value in updates.items():
            setattr(connection, key, value)
        return connection

    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args: None)
    monkeypatch.setattr(service, "get_user_connection", lambda *_args: connection)
    monkeypatch.setattr(service, "_group_allows_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "update_user_connection", update_connection)
    monkeypatch.setattr(service, "_is_connection_connected", lambda *_args: False)
    monkeypatch.setattr(service, "_upsert_connection_mcp_server", lambda *_args: None)
    monkeypatch.setattr(service, "_coerce_connection_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        service,
        "_serialize_connection_payload",
        lambda current, **_kwargs: {"enabled": current.enabled, "auth_mode": current.auth_mode},
    )

    payload = ConnectionUpdateRequest(access_token="github-token")
    response = service.update_connection_payload(
        object(),
        user_id="user-1",
        connection_id=connection.id,
        payload=payload,
    )

    assert "enabled" not in payload.model_fields_set
    assert all("enabled" not in updates for updates in update_calls)
    assert response["enabled"] is False
    assert response["auth_mode"] == "pat"
