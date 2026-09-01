import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections.errors import ConnectionRefreshReauthRequiredError, ConnectionRefreshRetryableError
from app.connections import service
from app.connections import slack as slack_module


class FakeResponse:
    def __init__(self, status_code, payload, *, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": "application/json"}
        self.reason_phrase = text or ""

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def post(self, *args, **kwargs):
        return self._response


class _Query:
    def __init__(self, connection):
        self._connection = connection

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._connection


class _Db:
    def __init__(self, connection):
        self._connection = connection

    def query(self, *_args, **_kwargs):
        return _Query(self._connection)


def _connection(provider=service.PROVIDER_SLACK):
    return SimpleNamespace(
        id="conn-1",
        user_id="user-1",
        provider=provider,
        secrets={
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "expires_at": 0,
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_endpoint": "https://example.com/token",
            "scopes": ["scope"],
        },
        status={"state": "connected", "last_error": "", "tool_count": 0, "tool_names": []},
        auth_mode="oauth",
        enabled=True,
        connected_at=None,
        mcp_server_id=None,
    )


def _patch_connection_updates(monkeypatch, connection):
    def fake_update_user_connection(_db, _connection_id, **kwargs):
        if "secrets" in kwargs:
            connection.secrets = kwargs["secrets"]
        if "status" in kwargs:
            connection.status = kwargs["status"]
        return connection

    monkeypatch.setattr(service, "update_user_connection", fake_update_user_connection)
    monkeypatch.setattr(service, "_upsert_connection_mcp_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_assert_connection_url_allowed", lambda *_args, **_kwargs: None)


def test_refresh_connection_keeps_tokens_for_retryable_refresh_errors(monkeypatch):
    connection = _connection()
    _patch_connection_updates(monkeypatch, connection)

    def fake_refresh(_secrets):
        raise ConnectionRefreshRetryableError(
            "Failed to refresh Slack access token: temporarily_unavailable",
            status_code=503,
        )

    monkeypatch.setattr(service, "refresh_slack_tokens", fake_refresh)

    with pytest.raises(HTTPException) as exc_info:
        service._refresh_connection_if_needed(db=object(), connection=connection, force=True)

    assert exc_info.value.status_code == 503
    assert connection.secrets["access_token"] == "old-access-token"
    assert connection.secrets["refresh_token"] == "old-refresh-token"
    assert connection.status["state"] == "error"
    assert connection.status["last_error"] == "Failed to refresh Slack access token: temporarily_unavailable"


def test_refresh_connection_clears_tokens_for_reauth_required_errors(monkeypatch):
    connection = _connection()
    _patch_connection_updates(monkeypatch, connection)

    monkeypatch.setattr(
        service,
        "refresh_slack_tokens",
        lambda _secrets: (_ for _ in ()).throw(
            ConnectionRefreshReauthRequiredError("Slack access expired. Reconnect the account.")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        service._refresh_connection_if_needed(db=object(), connection=connection, force=True)

    assert exc_info.value.status_code == 400
    assert connection.secrets["access_token"] is None
    assert connection.secrets["refresh_token"] is None
    assert connection.status["state"] == "reauthorization_required"
    assert connection.status["last_error"] == "Slack access expired. Reconnect the account."


def test_slack_invalid_refresh_token_requires_reauthorization(monkeypatch):
    response = FakeResponse(
        400,
        {"ok": False, "error": "invalid_refresh_token"},
        text="invalid_refresh_token",
    )
    monkeypatch.setattr(slack_module.httpx, "Client", lambda *args, **kwargs: FakeClient(response))

    with pytest.raises(ConnectionRefreshReauthRequiredError):
        slack_module.refresh_slack_tokens(
            {
                "refresh_token": "refresh-token",
                "client_id": "client-id",
                "client_secret": "client-secret",
            }
        )


def test_prepare_managed_mcp_server_for_runtime_uses_scoped_connection_server_after_refresh_error(monkeypatch):
    connection = _connection()
    connection.mcp_server_id = "server-foreign"
    current_server = SimpleNamespace(id="server-current", managed_connection_id=connection.id)
    owned_server = SimpleNamespace(id="server-owned")

    monkeypatch.setattr(
        service,
        "_refresh_connection_if_needed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(status_code=400, detail="reauth required")),
    )
    monkeypatch.setattr(service, "_get_managed_mcp_server_for_connection", lambda *_args, **_kwargs: owned_server)

    resolved = service.prepare_managed_mcp_server_for_runtime(_Db(connection), current_server)

    assert resolved is owned_server
