from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import logging
import secrets

import httpx
from fastapi import HTTPException

from app.connections.errors import (
    ConnectionRefreshReauthRequiredError,
    ConnectionRefreshRetryableError,
    refresh_failure_status_code,
)
from app.connections.models import consume_connection_oauth_state, save_connection_oauth_state
from app.settings.models import get_settings_page_data


SLACK_MCP_SERVER_URL = "https://mcp.slack.com/mcp"
SLACK_OAUTH_AUTHORIZATION_URL = "https://slack.com/oauth/v2_user/authorize"
SLACK_OAUTH_USER_TOKEN_URL = "https://slack.com/api/oauth.v2.user.access"
SLACK_OAUTH_REVOCATION_URL = "https://slack.com/api/auth.revoke"
SLACK_OAUTH_STATE_TTL_MINUTES = 10
SLACK_SCOPE_TIERS = {
    "public_read": [
        "search:read.public",
        "search:read.users",
        "channels:history",
        "users:read",
        "users:read.email",
    ],
    "workspace_read": [
        "search:read.public",
        "search:read.private",
        "search:read.mpim",
        "search:read.im",
        "search:read.files",
        "search:read.users",
        "channels:history",
        "groups:history",
        "mpim:history",
        "im:history",
        "canvases:read",
        "users:read",
        "users:read.email",
    ],
    "workspace_write": [
        "search:read.public",
        "search:read.private",
        "search:read.mpim",
        "search:read.im",
        "search:read.files",
        "search:read.users",
        "chat:write",
        "channels:history",
        "groups:history",
        "mpim:history",
        "im:history",
        "canvases:read",
        "canvases:write",
        "users:read",
        "users:read.email",
    ],
}
_HTTP_TIMEOUT = 20.0
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


def _generate_state() -> str:
    return secrets.token_hex(32)


def _setting_enabled(settings: dict, key: str) -> bool:
    """Return whether a boolean OAuth setting is enabled."""
    value = settings.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _slack_oauth_client_settings(db) -> dict[str, str]:
    settings = get_settings_page_data(db, "login_social")
    if not _setting_enabled(settings, "enable_slack_oauth"):
        raise HTTPException(
            status_code=400,
            detail="Slack OAuth is disabled. Enable Slack OAuth in Admin -> Login -> OAuth.",
        )
    client_id = str(settings.get("slack_client_id") or "").strip()
    client_secret = str(settings.get("slack_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Slack OAuth is not configured. Set slack_client_id and slack_client_secret in Admin -> Login -> OAuth.",
        )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
    }


def slack_oauth_is_configured(db) -> bool:
    try:
        _slack_oauth_client_settings(db)
        return True
    except HTTPException:
        return False


def _normalize_scopes(raw_value: str | list[str] | None) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        values = raw_value.replace(",", " ").split()
    elif isinstance(raw_value, (list, tuple, set)):
        values = [str(item or "").strip() for item in raw_value]
    else:
        return []
    return [value for value in values if value]


def _slack_scope_tier(settings: dict | None) -> str:
    value = str((settings or {}).get("slack_connection_scope_tier") or "").strip().lower()
    return value if value in SLACK_SCOPE_TIERS else "public_read"


def _slack_connection_scopes(db) -> list[str]:
    settings = get_settings_page_data(db, "login_social")
    return list(SLACK_SCOPE_TIERS[_slack_scope_tier(settings)])


def _basic_auth_headers(client_id: str, client_secret: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    return {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}",
    }


def start_slack_oauth(
    db,
    *,
    user_id: str,
    return_path: str,
    redirect_uri: str,
) -> str:
    oauth_client = _slack_oauth_client_settings(db)
    scopes = _slack_connection_scopes(db)
    state = _generate_state()
    expires_at = _utcnow() + timedelta(minutes=SLACK_OAUTH_STATE_TTL_MINUTES)
    save_connection_oauth_state(
        db,
        state=state,
        provider="slack",
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload={
            "scopes": list(scopes),
            "authorization_endpoint": SLACK_OAUTH_AUTHORIZATION_URL,
            "token_endpoint": SLACK_OAUTH_USER_TOKEN_URL,
        },
        secrets={
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "authorization_endpoint": SLACK_OAUTH_AUTHORIZATION_URL,
            "token_endpoint": SLACK_OAUTH_USER_TOKEN_URL,
            "scopes": list(scopes),
        },
        expires_at=expires_at,
    )
    params = httpx.QueryParams(
        {
            "client_id": oauth_client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        }
    )
    logger.info(
        "connections.slack.start user=%s state=%s redirect_uri=%s scopes=%s",
        _mask(user_id),
        _mask(state),
        redirect_uri,
        ",".join(scopes),
    )
    return f"{SLACK_OAUTH_AUTHORIZATION_URL}?{params}"


def complete_slack_oauth(db, *, state: str, code: str) -> dict:
    logger.info("connections.slack.complete.begin state=%s code_len=%s", _mask(state), len(str(code or "")))
    oauth_state = consume_connection_oauth_state(db, state)
    if not oauth_state:
        raise ValueError("Slack sign-in expired. Start the connection again.")
    if oauth_state.get("provider") != "slack":
        raise ValueError("Slack sign-in state is invalid.")

    secrets_payload = oauth_state.get("secrets") or {}
    client_id = str(secrets_payload.get("client_id") or "").strip()
    client_secret = str(secrets_payload.get("client_secret") or "").strip()
    redirect_uri = str(oauth_state.get("redirect_uri") or "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError("Slack OAuth configuration is incomplete.")

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            SLACK_OAUTH_USER_TOKEN_URL,
            data={
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers=_basic_auth_headers(client_id, client_secret),
        )
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or not payload.get("ok"):
        error_message = payload.get("error") or response.text
        raise ValueError(f"Failed to exchange Slack OAuth code: {error_message}")

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("Slack token exchange did not return a user access token.")

    expires_in = payload.get("expires_in")
    try:
        expires_at = int((_utcnow() + timedelta(seconds=int(expires_in))).timestamp()) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None
    scopes = _normalize_scopes(payload.get("authed_user", {}).get("scope") or payload.get("scope") or secrets_payload.get("scopes"))
    logger.info(
        "connections.slack.complete.success state=%s user=%s team=%s scopes=%s",
        _mask(state),
        _mask(oauth_state.get("user_id")),
        _mask((payload.get("team") or {}).get("id")),
        ",".join(scopes),
    )
    return {
        "user_id": str(oauth_state.get("user_id") or "").strip(),
        "return_path": str(oauth_state.get("return_path") or "/workspace/connections").strip() or "/workspace/connections",
        "display_name": "Slack",
        "secrets": {
            "access_token": access_token,
            "refresh_token": str(payload.get("refresh_token") or "").strip() or None,
            "expires_at": expires_at,
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": SLACK_OAUTH_USER_TOKEN_URL,
            "authorization_endpoint": secrets_payload.get("authorization_endpoint") or SLACK_OAUTH_AUTHORIZATION_URL,
            "issuer": "https://slack.com",
            "scopes": scopes,
        },
        "status": {
            "state": "connected",
            "last_error": "",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": None,
            "connected_at": _utcnow().isoformat(),
            "last_sync_at": _utcnow().isoformat(),
        },
    }


def refresh_slack_tokens(connection_secrets: dict) -> dict:
    refresh_token = str(connection_secrets.get("refresh_token") or "").strip()
    client_id = str(connection_secrets.get("client_id") or "").strip()
    client_secret = str(connection_secrets.get("client_secret") or "").strip()
    if not refresh_token or not client_id or not client_secret:
        raise ConnectionRefreshReauthRequiredError("Slack connection is missing refresh credentials.")
    logger.info(
        "connections.slack.refresh.begin client_id=%s has_refresh=%s",
        _mask(client_id),
        bool(refresh_token),
    )
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.post(
                SLACK_OAUTH_USER_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers=_basic_auth_headers(client_id, client_secret),
            )
    except httpx.HTTPError as exc:
        raise ConnectionRefreshRetryableError("Failed to refresh Slack access token: network error.") from exc
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or not payload.get("ok"):
        error_code = str(payload.get("error") or "").strip().lower()
        error_message = error_code or f"status {response.status_code}"
        if error_code in {"invalid_grant", "invalid_refresh_token", "token_revoked"}:
            logger.warning("connections.slack.refresh.invalid_grant client_id=%s error=%s", _mask(client_id), error_code or "-")
            raise ConnectionRefreshReauthRequiredError("Slack access expired. Reconnect the account.")
        raise ConnectionRefreshRetryableError(
            f"Failed to refresh Slack access token: {error_message}",
            status_code=refresh_failure_status_code(response.status_code),
        )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ConnectionRefreshRetryableError("Slack token refresh did not return an access token.", status_code=502)
    next_refresh_token = str(payload.get("refresh_token") or "").strip() or refresh_token
    expires_in = payload.get("expires_in")
    try:
        expires_at = int((_utcnow() + timedelta(seconds=int(expires_in))).timestamp()) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None
    scopes = _normalize_scopes(payload.get("scope") or connection_secrets.get("scopes"))
    logger.info(
        "connections.slack.refresh.success client_id=%s expires_in=%s scopes=%s",
        _mask(client_id),
        expires_in,
        ",".join(scopes),
    )
    return {
        "access_token": access_token,
        "refresh_token": next_refresh_token,
        "expires_at": expires_at,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_endpoint": SLACK_OAUTH_USER_TOKEN_URL,
        "authorization_endpoint": connection_secrets.get("authorization_endpoint") or SLACK_OAUTH_AUTHORIZATION_URL,
        "issuer": connection_secrets.get("issuer") or "https://slack.com",
        "scopes": scopes,
    }


def revoke_slack_token(token: str) -> None:
    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise ValueError("Slack token revocation requires a token.")

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            SLACK_OAUTH_REVOCATION_URL,
            data={"token": normalized_token},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {normalized_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    revoked = payload.get("revoked")
    if response.status_code == 200 and payload.get("ok") and (revoked is None or bool(revoked)):
        return

    error_message = payload.get("error") or f"status {response.status_code}"
    raise ValueError(f"Slack token revocation failed: {error_message}")
