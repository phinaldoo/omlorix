from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import logging
import secrets
from urllib.parse import urlparse

import httpx

from app.connections.errors import (
    ConnectionRefreshReauthRequiredError,
    ConnectionRefreshRetryableError,
    refresh_failure_status_code,
)
from app.connections.models import consume_connection_oauth_state, save_connection_oauth_state


NOTION_MCP_ISSUER_URL = "https://mcp.notion.com"
NOTION_MCP_SERVER_URL = "https://mcp.notion.com/mcp"
NOTION_MCP_SSE_URL = "https://mcp.notion.com/sse"
NOTION_OAUTH_AUTHORIZATION_SERVER_METADATA_URL = (
    f"{NOTION_MCP_ISSUER_URL}/.well-known/oauth-authorization-server"
)
NOTION_OAUTH_STATE_TTL_MINUTES = 10
_HTTP_TIMEOUT = 20.0
_NOTION_OAUTH_FALLBACK_METADATA = {
    "issuer": NOTION_MCP_ISSUER_URL,
    "authorization_endpoint": f"{NOTION_MCP_ISSUER_URL}/authorize",
    "token_endpoint": f"{NOTION_MCP_ISSUER_URL}/token",
    "registration_endpoint": f"{NOTION_MCP_ISSUER_URL}/register",
}
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask(value: str | None, *, keep: int = 6) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= keep:
        return text
    return f"{text[:keep]}..."


def _generate_verifier() -> str:
    return secrets.token_urlsafe(48)


def _generate_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _generate_state() -> str:
    return secrets.token_hex(32)


def _read_json(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Notion OAuth response.")
    return payload


def _normalize_notion_oauth_metadata(metadata: dict | None) -> dict:
    payload = dict(metadata or {})
    for key, value in _NOTION_OAUTH_FALLBACK_METADATA.items():
        payload.setdefault(key, value)
    if not payload.get("authorization_endpoint") or not payload.get("token_endpoint"):
        raise ValueError("Notion MCP OAuth metadata is missing required endpoints.")
    return payload


def _discover_notion_oauth_metadata_fallback(client: httpx.Client) -> dict:
    try:
        logger.info("connections.notion.discovery.fallback metadata_url=%s", NOTION_OAUTH_AUTHORIZATION_SERVER_METADATA_URL)
        metadata = _read_json(
            client.get(
                NOTION_OAUTH_AUTHORIZATION_SERVER_METADATA_URL,
                headers={"Accept": "application/json"},
            )
        )
        return _normalize_notion_oauth_metadata(metadata)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("connections.notion.discovery.fallback.failed error=%s using_static_fallback=true", exc)
        return _normalize_notion_oauth_metadata(_NOTION_OAUTH_FALLBACK_METADATA)


def discover_notion_oauth_metadata() -> dict:
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        try:
            logger.info(
                "connections.notion.discovery.protected_resource url=%s/.well-known/oauth-protected-resource",
                NOTION_MCP_SERVER_URL,
            )
            protected_resource = _read_json(
                client.get(
                    f"{NOTION_MCP_SERVER_URL}/.well-known/oauth-protected-resource",
                    headers={"Accept": "application/json"},
                )
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response is None or response.status_code not in {401, 403, 404}:
                logger.exception("connections.notion.discovery.protected_resource.unhandled error=%s", exc)
                raise
            logger.warning(
                "connections.notion.discovery.protected_resource.status status=%s falling_back=true",
                response.status_code,
            )
            return _discover_notion_oauth_metadata_fallback(client)
        authorization_servers = protected_resource.get("authorization_servers")
        if not isinstance(authorization_servers, list) or not authorization_servers:
            logger.warning("connections.notion.discovery.missing_authorization_servers falling_back=true")
            return _discover_notion_oauth_metadata_fallback(client)
        auth_server = str(authorization_servers[0] or "").strip()
        if not auth_server:
            logger.warning("connections.notion.discovery.empty_authorization_server falling_back=true")
            return _discover_notion_oauth_metadata_fallback(client)
        try:
            logger.info("connections.notion.discovery.authorization_server url=%s", auth_server)
            metadata = _read_json(
                client.get(
                    f"{auth_server.rstrip('/')}/.well-known/oauth-authorization-server",
                    headers={"Accept": "application/json"},
                )
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("connections.notion.discovery.authorization_server.failed error=%s falling_back=true", exc)
            return _discover_notion_oauth_metadata_fallback(client)
    logger.info(
        "connections.notion.discovery.success issuer=%s authorization_endpoint=%s token_endpoint=%s registration_endpoint=%s",
        metadata.get("issuer"),
        metadata.get("authorization_endpoint"),
        metadata.get("token_endpoint"),
        metadata.get("registration_endpoint"),
    )
    return _normalize_notion_oauth_metadata(metadata)


def register_notion_oauth_client(metadata: dict, *, redirect_uri: str, origin: str) -> dict:
    registration_endpoint = str(metadata.get("registration_endpoint") or "").strip()
    if not registration_endpoint:
        raise ValueError("Notion MCP does not expose dynamic client registration.")
    logger.info(
        "connections.notion.register.begin registration_endpoint=%s redirect_uri=%s origin=%s",
        registration_endpoint,
        redirect_uri,
        origin,
    )
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            registration_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "client_name": "Omlorix Notion Connection",
                "client_uri": origin,
                # MCP 2026 requires OAuth dynamic registrations to identify
                # the client application type so redirect-URI validation is
                # unambiguous for OpenID Connect authorization servers.
                "application_type": "web",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
    payload = _read_json(response)
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("Notion MCP client registration did not return a client_id.")
    logger.info(
        "connections.notion.register.success client_id=%s has_client_secret=%s",
        _mask(client_id),
        bool(payload.get("client_secret")),
    )
    return payload


def start_notion_oauth(
    db,
    *,
    user_id: str,
    return_path: str,
    redirect_uri: str,
    origin: str,
) -> str:
    logger.info(
        "connections.notion.start.begin user=%s return_path=%s redirect_uri=%s origin=%s",
        _mask(user_id),
        return_path,
        redirect_uri,
        origin,
    )
    metadata = discover_notion_oauth_metadata()
    client = register_notion_oauth_client(metadata, redirect_uri=redirect_uri, origin=origin)
    verifier = _generate_verifier()
    challenge = _generate_challenge(verifier)
    state = _generate_state()
    expires_at = _utcnow() + timedelta(minutes=NOTION_OAUTH_STATE_TTL_MINUTES)
    save_connection_oauth_state(
        db,
        state=state,
        provider="notion",
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload={
            "authorization_endpoint": metadata.get("authorization_endpoint"),
        },
        secrets={
            "code_verifier": verifier,
            "client_id": client.get("client_id"),
            "client_secret": client.get("client_secret"),
            "token_endpoint": metadata.get("token_endpoint"),
            "authorization_endpoint": metadata.get("authorization_endpoint"),
            "registration_endpoint": metadata.get("registration_endpoint"),
            "issuer": metadata.get("issuer"),
        },
        expires_at=expires_at,
    )
    logger.info(
        "connections.notion.start.state_saved user=%s state=%s client_id=%s expires_at=%s",
        _mask(user_id),
        _mask(state),
        _mask(client.get("client_id")),
        expires_at.isoformat(),
    )
    params = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": client.get("client_id"),
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "consent",
        }
    )
    logger.info(
        "connections.notion.start.redirect authorization_endpoint=%s state=%s",
        metadata.get("authorization_endpoint"),
        _mask(state),
    )
    return f"{str(metadata.get('authorization_endpoint')).rstrip('/')}?{params}"


def complete_notion_oauth(
    db,
    *,
    state: str,
    code: str,
    authorization_issuer: str | None = None,
) -> dict:
    """Complete Notion OAuth and validate an RFC 9207 issuer when supplied."""
    logger.info("connections.notion.complete.begin state=%s code_len=%s", _mask(state), len(str(code or "")))
    oauth_state = consume_connection_oauth_state(db, state)
    if not oauth_state:
        logger.warning("connections.notion.complete.state_missing state=%s", _mask(state))
        raise ValueError("Notion sign-in expired. Start the connection again.")

    secrets_payload = oauth_state.get("secrets") or {}
    expected_issuer = str(secrets_payload.get("issuer") or "").strip()
    returned_issuer = str(authorization_issuer or "").strip()
    if returned_issuer and (
        not expected_issuer
        or not secrets.compare_digest(returned_issuer, expected_issuer)
    ):
        logger.warning(
            "connections.notion.complete.issuer_mismatch state=%s expected=%s returned=%s",
            _mask(state),
            expected_issuer,
            returned_issuer,
        )
        raise ValueError("Notion authorization issuer did not match the requested issuer.")
    verifier = str(secrets_payload.get("code_verifier") or "").strip()
    client_id = str(secrets_payload.get("client_id") or "").strip()
    token_endpoint = str(secrets_payload.get("token_endpoint") or "").strip()
    logger.info(
        "connections.notion.complete.state_loaded state=%s user=%s client_id=%s token_endpoint=%s has_verifier=%s",
        _mask(state),
        _mask(oauth_state.get("user_id")),
        _mask(client_id),
        token_endpoint,
        bool(verifier),
    )
    if not verifier or not client_id or not token_endpoint:
        raise ValueError("Stored Notion OAuth state is incomplete.")

    params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": oauth_state.get("redirect_uri"),
        "code_verifier": verifier,
    }
    client_secret = str(secrets_payload.get("client_secret") or "").strip()
    if client_secret:
        params["client_secret"] = client_secret

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        logger.info(
            "connections.notion.complete.token_exchange.begin state=%s client_id=%s redirect_uri=%s",
            _mask(state),
            _mask(client_id),
            oauth_state.get("redirect_uri"),
        )
        response = client.post(
            token_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Omlorix-Notion-MCP/1.0",
            },
            data=params,
        )
    token_payload = _read_json(response)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        logger.warning("connections.notion.complete.token_exchange.missing_access_token state=%s", _mask(state))
        raise ValueError("Notion token exchange did not return an access token.")
    logger.info(
        "connections.notion.complete.token_exchange.success state=%s has_refresh=%s expires_in=%s scopes=%s",
        _mask(state),
        bool(token_payload.get("refresh_token")),
        token_payload.get("expires_in"),
        token_payload.get("scope"),
    )

    scopes = token_payload.get("scope")
    expires_in = token_payload.get("expires_in")
    try:
        expires_at_value = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at_value = None

    return {
        "user_id": oauth_state.get("user_id"),
        "return_path": oauth_state.get("return_path") or "/workspace/connections",
        "display_name": "Notion",
        "secrets": {
            "access_token": access_token,
            "refresh_token": token_payload.get("refresh_token"),
            "expires_at": int((_utcnow() + timedelta(seconds=expires_at_value)).timestamp()) if expires_at_value else None,
            "client_id": client_id,
            "client_secret": client_secret or None,
            "token_endpoint": token_endpoint,
            "authorization_endpoint": secrets_payload.get("authorization_endpoint"),
            "registration_endpoint": secrets_payload.get("registration_endpoint"),
            "issuer": secrets_payload.get("issuer"),
            "scopes": str(scopes or "").split() if scopes else [],
        },
        "status": {
            "state": "connected",
            "last_error": "",
            "connected_at": _utcnow().isoformat(),
        },
    }


def refresh_notion_tokens(connection_secrets: dict) -> dict:
    refresh_token = str(connection_secrets.get("refresh_token") or "").strip()
    client_id = str(connection_secrets.get("client_id") or "").strip()
    token_endpoint = str(connection_secrets.get("token_endpoint") or "").strip()
    if not refresh_token or not client_id or not token_endpoint:
        raise ConnectionRefreshReauthRequiredError("Notion connection is missing refresh credentials.")
    logger.info(
        "connections.notion.refresh.begin client_id=%s token_endpoint=%s has_refresh=%s",
        _mask(client_id),
        token_endpoint,
        bool(refresh_token),
    )

    params = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    client_secret = str(connection_secrets.get("client_secret") or "").strip()
    if client_secret:
        params["client_secret"] = client_secret

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.post(
                token_endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Omlorix-Notion-MCP/1.0",
                },
                data=params,
            )
    except httpx.HTTPError as exc:
        raise ConnectionRefreshRetryableError("Failed to refresh Notion access token: network error.") from exc

    if response.is_error:
        body = response.text
        if "invalid_grant" in body:
            logger.warning("connections.notion.refresh.invalid_grant client_id=%s", _mask(client_id))
            raise ConnectionRefreshReauthRequiredError("Notion authorization expired. Reconnect your workspace.")
        reason = str(response.reason_phrase or "").strip() or f"status {response.status_code}"
        raise ConnectionRefreshRetryableError(
            f"Failed to refresh Notion access token: {reason}",
            status_code=refresh_failure_status_code(response.status_code),
        )

    token_payload = response.json()
    if not isinstance(token_payload, dict):
        raise ConnectionRefreshRetryableError("Unexpected Notion token refresh response.", status_code=502)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise ConnectionRefreshRetryableError("Notion token refresh did not return an access token.", status_code=502)
    logger.info(
        "connections.notion.refresh.success client_id=%s has_refresh=%s expires_in=%s scopes=%s",
        _mask(client_id),
        bool(token_payload.get("refresh_token") or refresh_token),
        token_payload.get("expires_in"),
        token_payload.get("scope"),
    )
    expires_in = token_payload.get("expires_in")
    try:
        expires_at_value = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at_value = None
    scopes = token_payload.get("scope")
    refreshed = dict(connection_secrets)
    refreshed.update(
        {
            "access_token": access_token,
            "refresh_token": str(token_payload.get("refresh_token") or "").strip() or refresh_token,
            "expires_at": int((_utcnow() + timedelta(seconds=expires_at_value)).timestamp()) if expires_at_value else None,
            "scopes": str(scopes or "").split() if scopes else connection_secrets.get("scopes") or [],
        }
    )
    return refreshed


def _is_trusted_notion_oauth_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    trusted = urlparse(NOTION_MCP_ISSUER_URL)
    return (
        parsed.scheme == "https"
        and parsed.hostname == trusted.hostname
        and parsed.port is None
        and not parsed.username
        and not parsed.password
    )


def build_notion_revocation_endpoint(connection_secrets: dict | None) -> str:
    payload = connection_secrets if isinstance(connection_secrets, dict) else {}
    explicit_endpoint = str(payload.get("revocation_endpoint") or "").strip()
    if explicit_endpoint and _is_trusted_notion_oauth_url(explicit_endpoint):
        return explicit_endpoint

    token_endpoint = str(payload.get("token_endpoint") or "").strip()
    if _is_trusted_notion_oauth_url(token_endpoint):
        if token_endpoint.endswith("/token"):
            return f"{token_endpoint[:-len('/token')]}/revoke"
        if token_endpoint.endswith("/oauth/token"):
            return f"{token_endpoint[:-len('/token')]}/revoke"

    issuer = str(payload.get("issuer") or NOTION_MCP_ISSUER_URL).strip().rstrip("/")
    if not _is_trusted_notion_oauth_url(issuer):
        issuer = NOTION_MCP_ISSUER_URL
    return f"{issuer}/revoke"


def revoke_notion_token(connection_secrets: dict | None, *, token: str) -> None:
    payload = connection_secrets if isinstance(connection_secrets, dict) else {}
    normalized_token = str(token or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    if not normalized_token or not client_id:
        raise ValueError("Notion token revocation requires a client_id and token.")

    revocation_endpoint = build_notion_revocation_endpoint(payload)
    request_body = {
        "client_id": client_id,
        "token": normalized_token,
    }
    client_secret = str(payload.get("client_secret") or "").strip()
    if client_secret:
        request_body["client_secret"] = client_secret

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            revocation_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Omlorix-Notion-MCP/1.0",
            },
            data=request_body,
        )

    if response.status_code == 200:
        return

    raise ValueError(f"Notion token revocation failed with status {response.status_code}.")
