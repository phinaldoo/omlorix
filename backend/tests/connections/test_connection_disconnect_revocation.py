import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections import service
from app.connections.models import (
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_NOTION,
    PROVIDER_SLACK,
)


def _connection(
    *,
    provider: str,
    secrets: dict | None = None,
    auth_mode: str = "oauth",
    mcp_server_id: str | None = None,
):
    return SimpleNamespace(
        id="conn-1",
        user_id="user-1",
        provider=provider,
        enabled=True,
        auth_mode=auth_mode,
        secrets=secrets or {},
        status={},
        mcp_server_id=mcp_server_id,
        connected_at=None,
    )


def _patch_delete_dependencies(monkeypatch, connection):
    monkeypatch.setattr(service, "ensure_connections_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "get_user_connection", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(service, "_group_allows_provider", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_assert_connection_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_get_managed_mcp_server_for_connection", lambda *_args, **_kwargs: None)


def test_delete_connection_payload_revokes_google_refresh_token_before_local_delete(monkeypatch):
    connection = _connection(
        provider=PROVIDER_GMAIL,
        secrets={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        },
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(service, "revoke_google_token", lambda token: calls.append(("revoke", token)))
    monkeypatch.setattr(service, "delete_user_connection", lambda _db, connection_id: calls.append(("delete", connection_id)))

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert calls == [("revoke", "refresh-token"), ("delete", "conn-1")]
    assert result["provider_revocation"]["state"] == "revoked"
    assert result["provider_revocation"]["successes"] == ["refresh_token"]
    assert result["provider_revocation"]["failures"] == []


def test_delete_connection_payload_records_slack_revocation_failure_and_still_deletes(monkeypatch):
    connection = _connection(
        provider=PROVIDER_SLACK,
        secrets={
            "access_token": "access-token",
        },
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []

    def _raise_revocation_error(_token: str) -> None:
        raise ValueError("revocation failed")

    monkeypatch.setattr(service, "revoke_slack_token", _raise_revocation_error)
    monkeypatch.setattr(service, "delete_user_connection", lambda _db, connection_id: calls.append(("delete", connection_id)))

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert calls == [("delete", "conn-1")]
    assert result["provider_revocation"]["attempted"] is True
    assert result["provider_revocation"]["state"] == "failed"
    assert result["provider_revocation"]["failures"] == [
        {"target": "access_token", "reason": "ValueError"}
    ]


def test_delete_connection_payload_revokes_github_oauth_grant(monkeypatch):
    connection = _connection(
        provider=PROVIDER_GITHUB,
        secrets={
            "access_token": "access-token",
        },
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(service, "build_github_oauth_revocation_url", lambda _db: "https://api.github.com/applications/test/grant")
    monkeypatch.setattr(
        service,
        "revoke_github_oauth_grant",
        lambda _db, *, access_token: calls.append(("revoke", access_token)),
    )
    monkeypatch.setattr(service, "delete_user_connection", lambda _db, connection_id: calls.append(("delete", connection_id)))

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert calls == [("revoke", "access-token"), ("delete", "conn-1")]
    assert result["provider_revocation"]["state"] == "revoked"
    assert result["provider_revocation"]["successes"] == ["oauth_grant"]


def test_delete_connection_payload_rejects_imported_notion_revocation_endpoint_ssrf(monkeypatch):
    connection = _connection(
        provider=PROVIDER_NOTION,
        secrets={
            "access_token": "access-token",
            "client_id": "client-id",
            "revocation_endpoint": "http://127.0.0.1/internal/revoke",
            "token_endpoint": "http://127.0.0.1/oauth/token",
            "issuer": "http://127.0.0.1",
        },
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []
    checked_urls: list[str] = []

    def _record_checked_url(_db, *, url: str, feature: str) -> None:
        checked_urls.append(url)

    def _record_revoke(secrets, *, token: str) -> None:
        calls.append(("revoke", service.build_notion_revocation_endpoint(secrets)))

    monkeypatch.setattr(service, "_assert_connection_url_allowed", _record_checked_url)
    monkeypatch.setattr(service, "revoke_notion_token", _record_revoke)
    monkeypatch.setattr(
        service,
        "delete_user_connection",
        lambda _db, connection_id: calls.append(("delete", connection_id)),
    )

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert checked_urls == ["https://mcp.notion.com/revoke"]
    assert calls == [("revoke", "https://mcp.notion.com/revoke"), ("delete", "conn-1")]
    assert result["provider_revocation"]["state"] == "revoked"


def test_delete_connection_payload_uses_trusted_notion_token_endpoint(monkeypatch):
    connection = _connection(
        provider=PROVIDER_NOTION,
        secrets={
            "access_token": "access-token",
            "client_id": "client-id",
            "token_endpoint": "https://mcp.notion.com/oauth/token",
        },
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []
    checked_urls: list[str] = []

    def _record_revoke(secrets, *, token: str) -> None:
        calls.append(("revoke", service.build_notion_revocation_endpoint(secrets)))

    monkeypatch.setattr(
        service,
        "_assert_connection_url_allowed",
        lambda _db, *, url, feature: checked_urls.append(url),
    )
    monkeypatch.setattr(service, "revoke_notion_token", _record_revoke)
    monkeypatch.setattr(
        service,
        "delete_user_connection",
        lambda _db, connection_id: calls.append(("delete", connection_id)),
    )

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert checked_urls == ["https://mcp.notion.com/oauth/revoke"]
    assert calls == [("revoke", "https://mcp.notion.com/oauth/revoke"), ("delete", "conn-1")]
    assert result["provider_revocation"]["state"] == "revoked"


def test_delete_connection_payload_deletes_scoped_managed_server(monkeypatch):
    connection = _connection(
        provider=PROVIDER_GMAIL,
        mcp_server_id="server-foreign",
    )
    _patch_delete_dependencies(monkeypatch, connection)

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "_get_managed_mcp_server_for_connection",
        lambda *_args, **_kwargs: SimpleNamespace(id="server-owned"),
    )
    monkeypatch.setattr(service, "delete_mcp_server", lambda _db, server_id: calls.append(("server", server_id)))
    monkeypatch.setattr(service, "delete_user_connection", lambda _db, connection_id: calls.append(("delete", connection_id)))
    monkeypatch.setattr(
        "app.automations.models.remove_mcp_server_from_automations",
        lambda _db, server_id, commit: calls.append(("automation", server_id)) or 2,
    )

    result = service.delete_connection_payload(db=object(), user_id="user-1", connection_id="conn-1")

    assert calls == [
        ("automation", "server-owned"),
        ("server", "server-owned"),
        ("delete", "conn-1"),
    ]
    assert result["provider_revocation"]["state"] == "not_needed"
    assert result["automation_references_removed"] == 2
