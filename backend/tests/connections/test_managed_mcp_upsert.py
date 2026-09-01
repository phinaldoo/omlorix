from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections import service


class _Query:
    def __init__(self, *, exact_server=None, fallback_server=None, filter_calls=0):
        self._exact_server = exact_server
        self._fallback_server = fallback_server
        self._filter_calls = filter_calls

    def filter(self, *_args, **_kwargs):
        return _Query(
            exact_server=self._exact_server,
            fallback_server=self._fallback_server,
            filter_calls=self._filter_calls + 1,
        )

    def first(self):
        if self._filter_calls >= 2:
            return self._exact_server
        return self._fallback_server


class _Db:
    def __init__(self, *, exact_server=None, fallback_server=None):
        self._exact_server = exact_server
        self._fallback_server = fallback_server

    def query(self, *_args, **_kwargs):
        return _Query(
            exact_server=self._exact_server,
            fallback_server=self._fallback_server,
        )


def _connection(**overrides):
    values = {
        "id": "conn-1",
        "provider": service.PROVIDER_GITHUB,
        "user_id": "user-1",
        "enabled": True,
        "mcp_server_id": None,
        "auth_mode": "pat",
        "secrets": {"access_token": "token"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_managed_connection_create_supplies_required_mcp_defaults(monkeypatch):
    captured: dict[str, object] = {}
    update_calls: list[tuple[str, dict[str, object]]] = []

    def fake_create_mcp_server(_db, *, allowed_tools, **kwargs):
        captured.update(kwargs)
        captured["allowed_tools"] = allowed_tools
        return SimpleNamespace(id="server-1")

    def fake_update_user_connection(_db, connection_id, **kwargs):
        update_calls.append((connection_id, kwargs))
        return SimpleNamespace(id=connection_id, **kwargs)

    monkeypatch.setattr(service, "create_mcp_server", fake_create_mcp_server)
    monkeypatch.setattr(service, "update_user_connection", fake_update_user_connection)

    server = service._upsert_connection_mcp_server(_Db(), _connection())

    assert server.id == "server-1"
    assert captured["allowed_tools"] == []
    assert captured["name"] == "GitHub"
    assert captured["namespace"] == "github"
    assert captured["timeout_seconds"] == 45
    assert update_calls == [("conn-1", {"mcp_server_id": "server-1"})]


def test_managed_connection_ignores_legacy_user_server_customizations(monkeypatch):
    """Managed MCP identity and timing always come from the provider catalog."""
    captured: dict[str, object] = {}

    def fake_create_mcp_server(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="server-1")

    monkeypatch.setattr(service, "create_mcp_server", fake_create_mcp_server)
    monkeypatch.setattr(
        service,
        "update_user_connection",
        lambda _db, connection_id, **_kwargs: SimpleNamespace(id=connection_id),
    )

    legacy_connection = _connection()
    legacy_connection.display_name = "Personal GitHub"
    legacy_connection.config = {
        "namespace": "personal",
        "timeout_seconds": 600,
    }

    service._upsert_connection_mcp_server(_Db(), legacy_connection)

    assert captured["name"] == "GitHub"
    assert captured["namespace"] == "github"
    assert captured["timeout_seconds"] == 45


def test_managed_connection_update_supplies_required_mcp_defaults(monkeypatch):
    captured: dict[str, object] = {}
    existing_server = SimpleNamespace(id="server-1")

    def fake_update_mcp_server(_db, server_id, *, allowed_tools, **kwargs):
        captured.update(kwargs)
        captured["server_id"] = server_id
        captured["allowed_tools"] = allowed_tools
        return existing_server

    monkeypatch.setattr(service, "update_mcp_server", fake_update_mcp_server)

    server = service._upsert_connection_mcp_server(
        _Db(exact_server=existing_server),
        _connection(mcp_server_id="server-1"),
    )

    assert server is existing_server
    assert captured["server_id"] == "server-1"
    assert captured["allowed_tools"] == []


def test_google_workspace_worker_env_uses_comma_delimited_capabilities():
    """The managed connection must emit the delimiter format parsed by its worker."""
    connection = _connection(
        provider=service.PROVIDER_GMAIL,
        secrets={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        },
    )

    env = service._mcp_env_for_connection(connection)

    assert env["GOOGLE_WORKSPACE_ENABLED_CAPABILITIES"] == "gmail"


def test_get_managed_mcp_server_for_connection_falls_back_to_owned_server():
    fallback_server = SimpleNamespace(id="server-owned")

    server = service._get_managed_mcp_server_for_connection(
        _Db(exact_server=None, fallback_server=fallback_server),
        _connection(mcp_server_id="server-foreign"),
    )

    assert server is fallback_server
