"""Generic OAuth 2.0/OIDC support for remote MCP servers.

This module follows the MCP authorization discovery chain, binds client
credentials to the discovered issuer, validates RFC 9207 ``iss`` callback
parameters, uses PKCE, and refreshes access tokens before runtime requests.
OAuth secrets and short-lived redirect state are stored in encrypted columns.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx2
from mcp.client.auth.oauth2 import check_registration_usable
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    is_valid_client_metadata_url,
    should_use_client_metadata_url,
    validate_authorization_response_iss,
    validate_metadata_issuer,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import ValidationError

from app.mcp.models import (
    AUTH_OAUTH,
    MCPOAuthState,
    MCPServer,
    OWNER_ADMIN,
    OWNER_USER,
)
from app.network.outbound_http import public_async_httpx2_transport
from app.network.policy import assert_url_allowed


_OAUTH_TIMEOUT_SECONDS = 20.0
# The browser callback cookie and the database state must expire together.  A
# public constant keeps the HTTP route and this persistence layer from drifting
# to different validity windows.
MCP_OAUTH_STATE_TTL_SECONDS = 10 * 60
_TOKEN_REFRESH_SKEW_SECONDS = 60


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _pkce_verifier() -> str:
    """Create an RFC 7636 verifier with sufficient entropy."""
    return secrets.token_urlsafe(72)[:128]


def _pkce_challenge(verifier: str) -> str:
    """Create the S256 challenge for a PKCE verifier."""
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _oauth_credentials_need_refresh(oauth: dict[str, Any]) -> bool:
    """Return whether an OAuth credential set needs a token refresh."""
    if not str(oauth.get("access_token") or "").strip():
        return True
    expires_at = oauth.get("expires_at")
    if expires_at is None:
        return False
    try:
        return int(expires_at) <= int(_utcnow().timestamp()) + _TOKEN_REFRESH_SKEW_SECONDS
    except (TypeError, ValueError):
        return True


def _authorization_metadata_from_state(stored: dict[str, Any]) -> OAuthMetadata:
    """Rebuild the issuer metadata recorded for one authorization request."""
    return OAuthMetadata(
        issuer=stored["issuer"],
        authorization_endpoint=stored["authorization_endpoint"],
        token_endpoint=stored["token_endpoint"],
        authorization_response_iss_parameter_supported=stored.get(
            "authorization_response_iss_parameter_supported"
        ),
    )


def _assert_endpoint_allowed(db, endpoint: str, *, feature: str) -> None:
    """Apply Omlorix's outbound policy before contacting discovered endpoints."""
    assert_url_allowed(db, url=endpoint, feature=feature)


async def _json_get(db, client: httpx2.AsyncClient, url: str, *, feature: str) -> dict[str, Any] | None:
    """Fetch one optional OAuth metadata document through the safe transport."""
    _assert_endpoint_allowed(db, url, feature=feature)
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx2.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("MCP OAuth metadata response must be a JSON object.")
    return payload


async def discover_oauth_metadata(
    db,
    server_url: str,
) -> tuple[ProtectedResourceMetadata | None, OAuthMetadata, str | None]:
    """Discover and validate protected-resource and authorization metadata."""
    transport = public_async_httpx2_transport(feature="MCP OAuth discovery")
    async with httpx2.AsyncClient(
        transport=transport,
        timeout=_OAUTH_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        # RFC 9728 gives a protected resource's WWW-Authenticate challenge
        # precedence over well-known fallbacks. Use the side-effect-free
        # server/discover RPC because the 2026 transport removed HTTP GET and a
        # server may expose its resource_metadata pointer only on MCP POSTs.
        # Authentication middleware normally returns 401 before parsing this
        # request; older servers can reject it and fall through to the standard
        # path- and origin-based metadata locations.
        _assert_endpoint_allowed(db, server_url, feature="MCP OAuth challenge discovery")
        try:
            challenge_response = await client.post(
                server_url,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": "omlorix-oauth-discovery",
                    "method": "server/discover",
                    "params": {},
                },
            )
            challenge_metadata_url = extract_resource_metadata_from_www_auth(
                challenge_response
            )
            challenge_scope = extract_scope_from_www_auth(challenge_response)
        except httpx2.HTTPError:
            challenge_metadata_url = None
            challenge_scope = None
        protected_resource: ProtectedResourceMetadata | None = None
        for url in build_protected_resource_metadata_discovery_urls(
            challenge_metadata_url,
            server_url,
        ):
            payload = await _json_get(db, client, url, feature="MCP OAuth protected-resource discovery")
            if payload is not None:
                try:
                    protected_resource = ProtectedResourceMetadata.model_validate(payload)
                    break
                except ValidationError:
                    continue

        auth_server_url = (
            str(protected_resource.authorization_servers[0])
            if protected_resource is not None
            else None
        )
        oauth_metadata: OAuthMetadata | None = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(
            auth_server_url,
            server_url,
        ):
            payload = await _json_get(db, client, url, feature="MCP OAuth authorization-server discovery")
            if payload is not None:
                try:
                    oauth_metadata = OAuthMetadata.model_validate(payload)
                    break
                except ValidationError:
                    continue

    if oauth_metadata is None:
        raise ValueError("MCP server did not expose OAuth authorization-server metadata.")
    expected_issuer = auth_server_url or f"{urlparse(server_url).scheme}://{urlparse(server_url).netloc}"
    validate_metadata_issuer(oauth_metadata, expected_issuer.rstrip("/"))
    if protected_resource is not None:
        expected_resource = resource_url_from_server_url(server_url)
        if not check_resource_allowed(
            requested_resource=expected_resource,
            configured_resource=str(protected_resource.resource),
        ):
            raise ValueError("MCP OAuth protected-resource metadata does not match the server URL.")
    return protected_resource, oauth_metadata, challenge_scope


def build_client_metadata(*, public_url: str, redirect_uri: str) -> OAuthClientMetadata:
    """Build Omlorix's web-client metadata for DCR or metadata documents."""
    return OAuthClientMetadata(
        client_name="Omlorix MCP Client",
        client_uri=public_url,
        redirect_uris=[redirect_uri],
        response_types=["code"],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="none",
        application_type="web",
    )


async def _register_client(
    db,
    metadata: OAuthMetadata,
    client_metadata: OAuthClientMetadata,
) -> OAuthClientInformationFull:
    """Dynamically register Omlorix when metadata-document IDs are unavailable."""
    endpoint = str(metadata.registration_endpoint or "").strip()
    if not endpoint:
        raise ValueError("MCP authorization server does not support client registration.")
    _assert_endpoint_allowed(db, endpoint, feature="MCP OAuth client registration")
    transport = public_async_httpx2_transport(feature="MCP OAuth client registration")
    async with httpx2.AsyncClient(
        transport=transport,
        timeout=_OAUTH_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.post(
            endpoint,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=client_metadata.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
    response.raise_for_status()
    client_info = OAuthClientInformationFull.model_validate(response.json())
    check_registration_usable(client_info)
    return client_info


def _save_state(
    db,
    *,
    server: MCPServer,
    user_id: str,
    return_path: str,
    redirect_uri: str,
    payload: dict[str, Any],
    secrets_payload: dict[str, Any],
) -> str:
    """Persist one encrypted, single-use OAuth redirect state."""
    state = secrets.token_urlsafe(48)
    row = MCPOAuthState(
        state=state,
        server_id=server.id,
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload=payload,
        secrets=secrets_payload,
        created_at=_utcnow(),
        expires_at=_utcnow() + timedelta(seconds=MCP_OAUTH_STATE_TTL_SECONDS),
    )
    db.add(row)
    db.commit()
    return state


async def start_mcp_oauth(
    db,
    *,
    server: MCPServer,
    user_id: str,
    public_url: str,
    return_path: str,
) -> str:
    """Start a generic MCP OAuth authorization-code flow."""
    if server.auth_mode != AUTH_OAUTH or not server.url:
        raise ValueError("MCP server is not configured for OAuth.")
    redirect_uri = f"{public_url.rstrip('/')}/api/v1/llm/mcp/oauth/callback"
    client_metadata_url = f"{public_url.rstrip('/')}/api/v1/llm/mcp/oauth/client-metadata.json"
    protected_resource, metadata, challenge_scope = await discover_oauth_metadata(
        db,
        server.url,
    )
    client_metadata = build_client_metadata(public_url=public_url, redirect_uri=redirect_uri)

    if is_valid_client_metadata_url(client_metadata_url) and should_use_client_metadata_url(
        metadata,
        client_metadata_url,
    ):
        client_info = OAuthClientInformationFull(
            client_id=client_metadata_url,
            redirect_uris=client_metadata.redirect_uris,
            token_endpoint_auth_method="none",
            grant_types=client_metadata.grant_types,
            response_types=client_metadata.response_types,
            application_type="web",
        )
    else:
        existing = server.oauth if isinstance(server.oauth, dict) else {}
        if (
            existing.get("issuer") == str(metadata.issuer)
            and existing.get("client_id")
            and isinstance(existing.get("client_info"), dict)
        ):
            client_info = OAuthClientInformationFull.model_validate(existing["client_info"])
        else:
            client_info = await _register_client(db, metadata, client_metadata)

    verifier = _pkce_verifier()
    selected_scope = get_client_metadata_scopes(
        challenge_scope,
        protected_resource,
        metadata,
        client_metadata.grant_types,
    )
    existing_oauth = server.oauth if isinstance(server.oauth, dict) else {}
    scopes = list(
        dict.fromkeys(
            str(scope or "").strip()
            for scope in [
                *str(existing_oauth.get("scope") or "").split(),
                *(
                    existing_oauth.get("pending_scopes")
                    if isinstance(existing_oauth.get("pending_scopes"), list)
                    else []
                ),
                *(selected_scope.split() if selected_scope else []),
            ]
            if str(scope or "").strip()
        )
    )
    state = _save_state(
        db,
        server=server,
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload={"server_name": server.name},
        secrets_payload={
            "code_verifier": verifier,
            "issuer": str(metadata.issuer),
            "authorization_endpoint": str(metadata.authorization_endpoint),
            "token_endpoint": str(metadata.token_endpoint),
            "authorization_response_iss_parameter_supported": (
                metadata.authorization_response_iss_parameter_supported
            ),
            "client_info": client_info.model_dump(by_alias=True, mode="json", exclude_none=True),
            "resource": str(protected_resource.resource) if protected_resource else server.url,
            "scopes": scopes,
        },
    )
    params = {
        "response_type": "code",
        "client_id": client_info.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "resource": str(protected_resource.resource) if protected_resource else server.url,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{str(metadata.authorization_endpoint)}?{urlencode(params)}"


def _consume_state(
    db,
    state: str,
    *,
    expected_user_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume one non-expired state belonging to the callback's user.

    The user comparison deliberately happens before deletion.  A callback from
    another authenticated browser must neither redeem nor cancel the flow that
    belongs to the initiating user.
    """
    row = db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first()
    if row is None:
        raise ValueError("MCP authorization state is invalid or already used.")
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    secrets_payload = deepcopy(row.secrets if isinstance(row.secrets, dict) else {})
    public_payload = {
        "server_id": row.server_id,
        "user_id": row.user_id,
        "return_path": row.return_path,
        "redirect_uri": row.redirect_uri,
    }
    if expires_at <= _utcnow():
        db.delete(row)
        db.commit()
        raise ValueError("MCP authorization state expired.")
    if row.user_id != expected_user_id:
        raise ValueError("MCP authorization state belongs to another user.")
    db.delete(row)
    db.commit()
    return public_payload, secrets_payload


def _validate_callback_server_access(
    server: MCPServer | None,
    *,
    expected_user_id: str,
    expected_user_is_admin: bool,
) -> MCPServer:
    """Reauthorize the state target immediately before using OAuth secrets.

    Start-route authorization is not sufficient because server ownership or an
    administrator's role can change during the redirect round trip.  This
    helper therefore rechecks the current server record for both success and
    denied callbacks.
    """
    if server is None or server.auth_mode != AUTH_OAUTH:
        raise ValueError("MCP OAuth server no longer exists or changed authentication mode.")
    if server.owner_type == OWNER_USER:
        if server.owner_user_id != expected_user_id or server.managed_connection_id:
            raise ValueError("MCP OAuth server no longer belongs to the initiating user.")
        return server
    if server.owner_type == OWNER_ADMIN:
        if not expected_user_is_admin:
            raise ValueError("Administrative permission is required to complete MCP OAuth.")
        return server
    raise ValueError("MCP OAuth server has an unsupported owner type.")


def abort_mcp_oauth(
    db,
    state: str,
    *,
    authorization_issuer: str | None = None,
    expected_user_id: str,
    expected_user_is_admin: bool,
) -> tuple[str, str] | None:
    """Validate and consume a denied response for the authenticated user."""
    row = db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first()
    if row is None:
        return None
    public_state, stored = _consume_state(
        db,
        state,
        expected_user_id=expected_user_id,
    )
    server = db.query(MCPServer).filter(MCPServer.id == public_state["server_id"]).first()
    _validate_callback_server_access(
        server,
        expected_user_id=expected_user_id,
        expected_user_is_admin=expected_user_is_admin,
    )
    metadata = _authorization_metadata_from_state(stored)
    validate_authorization_response_iss(authorization_issuer, metadata)
    return public_state["user_id"], public_state["return_path"]


async def _exchange_token(
    db,
    *,
    token_endpoint: str,
    form: dict[str, str],
    client_info: OAuthClientInformationFull,
) -> OAuthToken:
    """Exchange or refresh tokens using the registered client auth method."""
    _assert_endpoint_allowed(db, token_endpoint, feature="MCP OAuth token exchange")
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    if client_info.token_endpoint_auth_method == "client_secret_basic" and client_info.client_secret:
        encoded = base64.b64encode(
            f"{client_info.client_id}:{client_info.client_secret}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    else:
        form["client_id"] = client_info.client_id
        if client_info.token_endpoint_auth_method == "client_secret_post" and client_info.client_secret:
            form["client_secret"] = client_info.client_secret
    transport = public_async_httpx2_transport(feature="MCP OAuth token exchange")
    async with httpx2.AsyncClient(
        transport=transport,
        timeout=_OAUTH_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.post(token_endpoint, headers=headers, data=form)
    response.raise_for_status()
    return OAuthToken.model_validate(response.json())


async def complete_mcp_oauth(
    db,
    *,
    state: str,
    code: str,
    authorization_issuer: str | None,
    expected_user_id: str,
    expected_user_is_admin: bool,
) -> tuple[MCPServer, str, str]:
    """Validate a user-bound callback, exchange its code, and persist tokens."""
    row, stored = _consume_state(
        db,
        state,
        expected_user_id=expected_user_id,
    )
    server = db.query(MCPServer).filter(MCPServer.id == row["server_id"]).first()
    server = _validate_callback_server_access(
        server,
        expected_user_id=expected_user_id,
        expected_user_is_admin=expected_user_is_admin,
    )
    metadata = _authorization_metadata_from_state(stored)
    validate_authorization_response_iss(authorization_issuer, metadata)
    client_info = OAuthClientInformationFull.model_validate(stored["client_info"])
    token = await _exchange_token(
        db,
        token_endpoint=stored["token_endpoint"],
        client_info=client_info,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": row["redirect_uri"],
            "code_verifier": stored["code_verifier"],
            "resource": stored["resource"],
        },
    )
    expires_at = (
        int(_utcnow().timestamp()) + int(token.expires_in)
        if token.expires_in is not None
        else None
    )
    server.oauth = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "token_type": token.token_type,
        "scope": token.scope,
        "expires_at": expires_at,
        "issuer": stored["issuer"],
        "token_endpoint": stored["token_endpoint"],
        "resource": stored["resource"],
        "client_id": client_info.client_id,
        "client_info": client_info.model_dump(by_alias=True, mode="json", exclude_none=True),
    }
    db.add(server)
    db.commit()
    db.refresh(server)
    return server, row["user_id"], row["return_path"]


async def _refresh_server_oauth(db, server: MCPServer, oauth: dict[str, Any]) -> dict[str, Any]:
    """Refresh one server token while serializing concurrent workers.

    PostgreSQL holds the row lock through the token exchange and commit. After
    acquiring it, the worker reloads encrypted OAuth state so a refresh already
    completed by another worker is reused instead of rotating the same refresh
    token twice.
    """
    locked_server = (
        db.query(MCPServer)
        .filter(MCPServer.id == server.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if locked_server is None:
        raise ValueError("MCP OAuth server no longer exists.")
    current = deepcopy(
        locked_server.oauth if isinstance(locked_server.oauth, dict) else oauth
    )
    if not _oauth_credentials_need_refresh(current):
        return current

    refresh_token = str(current.get("refresh_token") or "").strip()
    if not refresh_token:
        raise ValueError("MCP OAuth access token expired and no refresh token is available.")
    client_info = OAuthClientInformationFull.model_validate(current["client_info"])
    try:
        token = await _exchange_token(
            db,
            token_endpoint=str(current["token_endpoint"]),
            client_info=client_info,
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "resource": str(current.get("resource") or locked_server.url or ""),
                **({"scope": str(current["scope"])} if current.get("scope") else {}),
            },
        )
    except Exception:
        db.rollback()
        raise
    refreshed = dict(current)
    refreshed.update(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token or refresh_token,
            "token_type": token.token_type,
            "scope": token.scope or current.get("scope"),
            "expires_at": (
                int(_utcnow().timestamp()) + int(token.expires_in)
                if token.expires_in is not None
                else None
            ),
        }
    )
    refreshed.pop("pending_scopes", None)
    locked_server.oauth = refreshed
    db.add(locked_server)
    db.commit()
    db.refresh(locked_server)
    return refreshed


def prepare_oauth_server_for_runtime(db, server: MCPServer) -> MCPServer | SimpleNamespace:
    """Return a runtime copy carrying a fresh bearer token, never a persisted header."""
    if str(getattr(server, "auth_mode", "headers") or "headers") != AUTH_OAUTH:
        return server
    oauth = deepcopy(server.oauth if isinstance(server.oauth, dict) else {})
    if _oauth_credentials_need_refresh(oauth):
        # Import lazily to avoid the module-level oauth -> utils -> oauth cycle.
        # The helper uses a worker loop when this sync boundary is invoked from
        # code that already owns an asyncio event loop.
        from app.mcp.utils import _run_async

        oauth = _run_async(_refresh_server_oauth(db, server, oauth))
    access_token = str(oauth.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("MCP server requires OAuth authorization.")

    values = {
        column.name: getattr(server, column.name)
        for column in server.__table__.columns
    }
    values["headers"] = {
        **deepcopy(server.headers if isinstance(server.headers, dict) else {}),
        "Authorization": f"Bearer {access_token}",
    }
    return SimpleNamespace(**values)
