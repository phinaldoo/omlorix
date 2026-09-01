from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthToken
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base
from app.mcp.models import (
    AUTH_OAUTH,
    MCPOAuthState,
    MCPServer,
    OWNER_ADMIN,
    OWNER_USER,
    create_mcp_server,
    delete_mcp_server,
    serialize_mcp_server,
    update_mcp_server,
)
from app.mcp.oauth import (
    _refresh_server_oauth,
    _save_state,
    abort_mcp_oauth,
    build_client_metadata,
    complete_mcp_oauth,
    discover_oauth_metadata,
    prepare_oauth_server_for_runtime,
)


@pytest.fixture()
def db(monkeypatch):
    """Create only the two MCP tables needed for OAuth persistence tests."""
    from app.utils import encryption

    monkeypatch.setattr(encryption, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption, "_CIPHER_SUITE", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[MCPServer.__table__, MCPOAuthState.__table__],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _oauth_server(db) -> MCPServer:
    """Persist a minimal personal OAuth server through production validation."""
    return create_mcp_server(
        db,
        owner_type=OWNER_USER,
        owner_user_id="user-1",
        name="Docs",
        description=None,
        namespace="docs",
        transport="streamable_http",
        enabled=True,
        url="https://mcp.example.com/mcp",
        command=None,
        args=[],
        headers={"X-Tenant": "one"},
        auth_mode=AUTH_OAUTH,
        env={},
        allowed_tools=[],
        timeout_seconds=30,
    )


def _admin_oauth_server(db) -> MCPServer:
    """Persist a minimal administrator-owned OAuth server."""
    return create_mcp_server(
        db,
        owner_type=OWNER_ADMIN,
        owner_user_id=None,
        name="Shared Docs",
        description=None,
        namespace="shared_docs",
        transport="streamable_http",
        enabled=True,
        url="https://mcp.example.com/mcp",
        command=None,
        args=[],
        headers={},
        auth_mode=AUTH_OAUTH,
        env={},
        allowed_tools=[],
        timeout_seconds=30,
    )


def _oauth_state_secrets(server: MCPServer) -> dict:
    """Return complete deterministic secrets for callback completion tests."""
    return {
        "code_verifier": "verifier",
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "authorization_response_iss_parameter_supported": False,
        "resource": server.url,
        "client_info": {
            "client_id": "omlorix",
            "redirect_uris": ["https://chat.example.com/api/v1/llm/mcp/oauth/callback"],
            "token_endpoint_auth_method": "none",
        },
    }


def _callback_request(*, cookie: str = "") -> Request:
    """Build a minimal top-level OAuth callback request for cookie tests."""
    headers = Headers({"cookie": cookie} if cookie else {})
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "path": "/api/v1/llm/mcp/oauth/callback",
            "client": ("127.0.0.1", 12345),
            "headers": headers.raw,
        }
    )


def test_oauth_client_metadata_is_a_web_client():
    """The public metadata document must describe the browser redirect flow."""
    metadata = build_client_metadata(
        public_url="https://chat.example.com",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
    )

    assert metadata.application_type == "web"
    assert metadata.token_endpoint_auth_method == "none"
    assert metadata.grant_types == ["authorization_code", "refresh_token"]


def test_oauth_discovery_probes_stateless_mcp_post_for_www_auth(monkeypatch):
    """Challenge discovery uses server/discover, not the removed HTTP GET stream."""
    import asyncio
    from app.mcp import oauth as oauth_module

    calls = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return FakeResponse(status_code=401)

        async def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            if url.endswith("/protected-resource"):
                return FakeResponse(
                    {
                        "resource": "https://mcp.example.com/mcp",
                        "authorization_servers": ["https://auth.example.com"],
                    }
                )
            return FakeResponse(
                {
                    "issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                }
            )

    monkeypatch.setattr(oauth_module.httpx2, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        oauth_module, "public_async_httpx2_transport", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        oauth_module, "_assert_endpoint_allowed", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        oauth_module,
        "extract_resource_metadata_from_www_auth",
        lambda _response: "https://mcp.example.com/protected-resource",
    )
    monkeypatch.setattr(
        oauth_module, "extract_scope_from_www_auth", lambda _response: "tools:read"
    )
    monkeypatch.setattr(
        oauth_module,
        "build_protected_resource_metadata_discovery_urls",
        lambda *_args: ["https://mcp.example.com/protected-resource"],
    )
    monkeypatch.setattr(
        oauth_module,
        "build_oauth_authorization_server_metadata_discovery_urls",
        lambda *_args: [
            "https://auth.example.com/.well-known/oauth-authorization-server"
        ],
    )

    protected, metadata, scope = asyncio.run(
        discover_oauth_metadata(object(), "https://mcp.example.com/mcp")
    )

    method, _url, request = calls[0]
    assert method == "post"
    assert request["json"]["method"] == "server/discover"
    assert request["headers"]["MCP-Protocol-Version"] == "2026-07-28"
    assert str(protected.resource) == "https://mcp.example.com/mcp"
    assert str(metadata.issuer) == "https://auth.example.com"
    assert scope == "tools:read"


def test_runtime_bearer_header_never_mutates_persisted_headers(db):
    """OAuth tokens belong only on the ephemeral runtime server copy."""
    server = _oauth_server(db)
    server.oauth = {
        "access_token": "secret-access-token",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
    }
    db.commit()

    prepared = prepare_oauth_server_for_runtime(db, server)

    assert prepared.headers == {
        "X-Tenant": "one",
        "Authorization": "Bearer secret-access-token",
    }
    assert server.headers == {"X-Tenant": "one"}
    serialized = serialize_mcp_server(server)
    assert serialized["headers"] == {}
    assert "secret-access-token" not in str(serialized)


def test_endpoint_change_clears_issuer_bound_oauth_credentials(db):
    """A token cannot survive an edit that points the server at a new resource."""
    server = _oauth_server(db)
    server.oauth = {"access_token": "token", "issuer": "https://auth.example.com"}
    db.commit()

    updated = update_mcp_server(
        db,
        server.id,
        url="https://other.example.com/mcp",
    )

    assert updated.oauth == {}


def test_callback_issuer_mismatch_consumes_single_use_state(db):
    """RFC 9207 mismatch fails closed before any token endpoint request."""
    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={
            "code_verifier": "verifier",
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "resource": server.url,
            "client_info": {
                "client_id": "omlorix",
                "redirect_uris": [
                    "https://chat.example.com/api/v1/llm/mcp/oauth/callback"
                ],
                "token_endpoint_auth_method": "none",
            },
        },
    )

    with pytest.raises(Exception, match="iss mismatch"):
        import asyncio

        asyncio.run(
            complete_mcp_oauth(
                db,
                state=state,
                code="authorization-code",
                authorization_issuer="https://attacker.example.com",
                expected_user_id="user-1",
                expected_user_is_admin=False,
            )
        )

    assert db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is None


def test_callback_rejects_a_different_authenticated_user_before_exchange(
    db, monkeypatch
):
    """A copied state cannot connect another user's provider account."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={},
    )

    async def unexpected_exchange(*_args, **_kwargs):
        raise AssertionError(
            "a mismatched user must not exchange the authorization code"
        )

    monkeypatch.setattr(oauth_module, "_exchange_token", unexpected_exchange)

    with pytest.raises(ValueError, match="another user"):
        asyncio.run(
            complete_mcp_oauth(
                db,
                state=state,
                code="victim-authorization-code",
                authorization_issuer=None,
                expected_user_id="user-2",
                expected_user_is_admin=False,
            )
        )

    assert server.oauth == {}
    # A callback from the wrong account cannot cancel the initiating user's
    # transaction; the correct browser may still complete it before expiry.
    assert (
        db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is not None
    )


def test_callback_rechecks_personal_server_ownership_before_exchange(db, monkeypatch):
    """Ownership changes during the redirect invalidate personal OAuth."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={},
    )
    server.owner_user_id = "user-2"
    db.commit()

    async def unexpected_exchange(*_args, **_kwargs):
        raise AssertionError("a server with changed ownership must not exchange a code")

    monkeypatch.setattr(oauth_module, "_exchange_token", unexpected_exchange)

    with pytest.raises(ValueError, match="no longer belongs"):
        asyncio.run(
            complete_mcp_oauth(
                db,
                state=state,
                code="authorization-code",
                authorization_issuer=None,
                expected_user_id="user-1",
                expected_user_is_admin=False,
            )
        )

    assert server.oauth == {}


def test_callback_rechecks_current_admin_role_before_exchange(db, monkeypatch):
    """A demoted administrator cannot finish an admin-server OAuth flow."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _admin_oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="admin-1",
        return_path="/admin/mcp-settings",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={},
    )

    async def unexpected_exchange(*_args, **_kwargs):
        raise AssertionError("a demoted administrator must not exchange a code")

    monkeypatch.setattr(oauth_module, "_exchange_token", unexpected_exchange)

    with pytest.raises(ValueError, match="Administrative permission"):
        asyncio.run(
            complete_mcp_oauth(
                db,
                state=state,
                code="authorization-code",
                authorization_issuer=None,
                expected_user_id="admin-1",
                expected_user_is_admin=False,
            )
        )

    assert server.oauth == {}


def test_callback_allows_the_initiating_user_and_current_owner(db, monkeypatch):
    """The legitimate personal OAuth callback still stores issuer-bound tokens."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload=_oauth_state_secrets(server),
    )

    async def exchange(*_args, **_kwargs):
        return OAuthToken.model_validate(
            {
                "access_token": "legitimate-access-token",
                "refresh_token": "legitimate-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(oauth_module, "_exchange_token", exchange)

    completed_server, completed_user_id, return_path = asyncio.run(
        complete_mcp_oauth(
            db,
            state=state,
            code="authorization-code",
            authorization_issuer=None,
            expected_user_id="user-1",
            expected_user_is_admin=False,
        )
    )

    assert completed_server.oauth["access_token"] == "legitimate-access-token"
    assert completed_user_id == "user-1"
    assert return_path == "/workspace/connections"
    assert db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is None


def test_callback_cookie_is_state_scoped_short_lived_and_samesite_lax(monkeypatch):
    """Strict global auth cookies do not break the cross-site OAuth return."""
    from app.llm import router as llm_router

    monkeypatch.setattr(llm_router, "should_secure_auth_cookie", lambda *_args: True)
    response = Response()
    llm_router._set_mcp_oauth_callback_cookie(
        response,
        state="oauth-state",
        access_token="access-token",
        db=object(),
        request=_callback_request(),
    )

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=600" in cookie
    assert "path=/api/v1/llm/mcp/oauth/callback" in cookie


def test_callback_prefers_the_state_specific_lax_cookie(monkeypatch):
    """Callback authentication uses the short-lived token set at OAuth start."""
    from app.llm import router as llm_router

    state = "oauth-state"
    cookie_name = llm_router._mcp_oauth_callback_cookie_name(state)
    request = _callback_request(cookie=f"{cookie_name}=callback-access-token")
    observed = {}

    def fake_verified_user(_request, credentials, _db):
        observed["token"] = credentials.credentials
        return SimpleNamespace(id="user-1", role="user")

    monkeypatch.setattr(llm_router, "verified_user", fake_verified_user)

    user = llm_router._verified_mcp_oauth_callback_user(
        request,
        state=state,
        credentials=None,
        db=object(),
    )

    assert user.id == "user-1"
    assert observed["token"] == "callback-access-token"


def test_personal_oauth_start_sets_the_state_specific_callback_cookie(db, monkeypatch):
    """The authenticated start route prepares its browser for a strict-cookie return."""
    import asyncio
    from app.llm import router as llm_router

    server = _oauth_server(db)
    state = "state-from-start"
    authorization_url = f"https://auth.example.com/authorize?state={state}"

    async def fake_start(*_args, **_kwargs):
        return authorization_url

    monkeypatch.setattr(llm_router, "start_mcp_oauth", fake_start)
    monkeypatch.setattr(
        llm_router, "get_public_url", lambda *_args: "https://chat.example.com"
    )
    monkeypatch.setattr(llm_router, "require_group_mcp_enabled", lambda *_args: None)
    monkeypatch.setattr(llm_router, "_audit_llm_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "should_secure_auth_cookie", lambda *_args: True)
    response = Response()

    result = asyncio.run(
        llm_router.start_user_mcp_oauth_route(
            server.id,
            _callback_request(),
            response,
            db=db,
            db_log=object(),
            access_token="initiating-access-token",
            user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert result == {"authorization_url": authorization_url}
    cookie = response.headers["set-cookie"].lower()
    assert llm_router._mcp_oauth_callback_cookie_name(state) in cookie
    assert "samesite=lax" in cookie


def test_success_callback_passes_current_identity_and_clears_cookie(db, monkeypatch):
    """The route wires current-user authorization into the token exchange."""
    import asyncio
    from app.llm import router as llm_router

    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={},
    )
    observed = {}

    monkeypatch.setattr(
        llm_router,
        "_verified_mcp_oauth_callback_user",
        lambda *_args, **_kwargs: SimpleNamespace(id="user-1", role="user"),
    )
    monkeypatch.setattr(llm_router, "_audit_llm_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "should_secure_auth_cookie", lambda *_args: True)

    async def fake_complete(*_args, **kwargs):
        observed.update(kwargs)
        return server, "user-1", "/workspace/connections"

    monkeypatch.setattr(llm_router, "complete_mcp_oauth", fake_complete)

    response = asyncio.run(
        llm_router.complete_mcp_oauth_route(
            _callback_request(),
            code="authorization-code",
            state=state,
            iss=None,
            error=None,
            db=db,
            db_log=object(),
            credentials=None,
        )
    )

    assert observed["expected_user_id"] == "user-1"
    assert observed["expected_user_is_admin"] is False
    assert response.status_code == 302
    assert "mcp_oauth_status=connected" in response.headers["location"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_denied_callback_requires_advertised_issuer_and_consumes_state(db):
    """RFC 9207 issuer binding also applies when the authorization is denied."""
    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "authorization_response_iss_parameter_supported": True,
        },
    )

    with pytest.raises(Exception, match="iss"):
        abort_mcp_oauth(
            db,
            state,
            expected_user_id="user-1",
            expected_user_is_admin=False,
        )

    assert db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is None


def test_denied_callback_rejects_a_different_authenticated_user(db):
    """A copied state cannot be cancelled through another user's browser."""
    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/api/v1/llm/mcp/oauth/callback",
        payload={},
        secrets_payload={},
    )

    with pytest.raises(ValueError, match="another user"):
        abort_mcp_oauth(
            db,
            state,
            expected_user_id="user-2",
            expected_user_is_admin=False,
        )

    assert (
        db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is not None
    )


def test_refresh_reuses_credentials_stored_by_another_worker(db, monkeypatch):
    """A worker waiting for the row lock must not rotate a refreshed token twice."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _oauth_server(db)
    fresh = {
        "access_token": "fresh-access-token",
        "refresh_token": "fresh-refresh-token",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
    }
    server.oauth = fresh
    db.commit()

    async def unexpected_exchange(*_args, **_kwargs):
        raise AssertionError("fresh credentials must be reused")

    monkeypatch.setattr(oauth_module, "_exchange_token", unexpected_exchange)
    stale = {
        "access_token": "expired-access-token",
        "refresh_token": "old-refresh-token",
        "expires_at": 0,
    }

    assert asyncio.run(_refresh_server_oauth(db, server, stale)) == fresh


def test_runtime_oauth_refresh_is_safe_inside_an_active_event_loop(db, monkeypatch):
    """The synchronous runtime boundary delegates refresh to a worker loop."""
    import asyncio
    from app.mcp import oauth as oauth_module

    server = _oauth_server(db)
    server.oauth = {
        "access_token": "expired-access-token",
        "refresh_token": "refresh-token",
        "expires_at": 0,
    }
    db.commit()

    async def refreshed(*_args, **_kwargs):
        return {
            "access_token": "fresh-access-token",
            "refresh_token": "refresh-token",
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
        }

    monkeypatch.setattr(oauth_module, "_refresh_server_oauth", refreshed)

    async def prepare_inside_loop():
        return prepare_oauth_server_for_runtime(db, server)

    prepared = asyncio.run(prepare_inside_loop())

    assert prepared.headers["Authorization"] == "Bearer fresh-access-token"


def test_oauth_callback_redirect_paths_are_strictly_local():
    """Stored or completed callback paths cannot create an open redirect."""
    from app.llm.router import _safe_mcp_oauth_return_path

    assert (
        _safe_mcp_oauth_return_path("/workspace/connections")
        == "/workspace/connections"
    )
    assert _safe_mcp_oauth_return_path("/admin/mcp-settings") == "/admin/mcp-settings"
    for unsafe in (
        "https://attacker.example/callback",
        "//attacker.example/callback",
        "/workspace/connections?next=https://attacker.example",
        "/other/local/path",
        None,
    ):
        assert _safe_mcp_oauth_return_path(unsafe) == "/workspace/connections"


def test_deleting_server_removes_outstanding_oauth_states(db):
    """Short-lived redirect records must not outlive their owning integration."""
    server = _oauth_server(db)
    state = _save_state(
        db,
        server=server,
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://chat.example.com/callback",
        payload={},
        secrets_payload={"code_verifier": "verifier"},
    )

    delete_mcp_server(db, server.id)

    assert db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first() is None
