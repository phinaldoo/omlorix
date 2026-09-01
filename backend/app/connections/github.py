from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import logging
import secrets

import httpx
from fastapi import HTTPException

from app.auth.github_oauth import (
    DEFAULT_GITHUB_BASE_URL,
    GitHubOAuthEndpoints,
    build_github_oauth_endpoints,
)
from app.connections.models import consume_connection_oauth_state, save_connection_oauth_state
from app.network.policy import assert_http_url_allowed
from app.settings.models import get_settings_page_data


GITHUB_MCP_SERVER_URL = "https://api.githubcopilot.com/mcp"
GITHUB_OAUTH_REVOCATION_API_VERSION = "2022-11-28"
GITHUB_OAUTH_STATE_TTL_MINUTES = 10
GITHUB_SCOPE_TIERS = {
    "profile_only": [
        "read:user",
        "user:email",
    ],
    "repository_access": [
        "repo",
        "read:org",
        "read:user",
        "user:email",
    ],
    "extended_access": [
        "repo",
        "read:org",
        "read:user",
        "user:email",
        "notifications",
        "gist",
        "project",
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


def _github_oauth_client_settings(db) -> dict[str, str | GitHubOAuthEndpoints]:
    """Load GitHub.com credentials for the hosted workspace connector.

    Social sign-in supports one self-hosted GitHub Enterprise Server, but the
    managed workspace integration sends its token to GitHub's public hosted MCP
    service.  Restricting this flow to GitHub.com prevents an enterprise-server
    token from being disclosed to an incompatible public service.
    """

    settings = get_settings_page_data(db, "login_social")
    if not _setting_enabled(settings, "enable_github_oauth"):
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth is disabled. Enable GitHub OAuth in Admin -> Login -> OAuth.",
        )
    client_id = str(settings.get("github_client_id") or "").strip()
    client_secret = str(settings.get("github_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth is not configured. Set github_client_id and github_client_secret in Admin -> Login -> OAuth.",
        )
    endpoints = build_github_oauth_endpoints(
        settings.get("github_base_url", DEFAULT_GITHUB_BASE_URL)
    )
    if not endpoints.is_github_dot_com:
        raise HTTPException(
            status_code=400,
            detail="Managed GitHub workspace connections are available only when the GitHub Base URL is https://github.com.",
        )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "endpoints": endpoints,
    }


def github_oauth_is_configured(db) -> bool:
    try:
        _github_oauth_client_settings(db)
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


def _github_scope_tier(settings: dict | None) -> str:
    value = str((settings or {}).get("github_connection_scope_tier") or "").strip().lower()
    return value if value in GITHUB_SCOPE_TIERS else "repository_access"


def _github_connection_scopes(db) -> list[str]:
    settings = get_settings_page_data(db, "login_social")
    return list(GITHUB_SCOPE_TIERS[_github_scope_tier(settings)])


def start_github_oauth(
    db,
    *,
    user_id: str,
    return_path: str,
    redirect_uri: str,
) -> str:
    oauth_client = _github_oauth_client_settings(db)
    endpoints = oauth_client["endpoints"]
    assert isinstance(endpoints, GitHubOAuthEndpoints)
    scopes = _github_connection_scopes(db)
    state = _generate_state()
    expires_at = _utcnow() + timedelta(minutes=GITHUB_OAUTH_STATE_TTL_MINUTES)
    save_connection_oauth_state(
        db,
        state=state,
        provider="github",
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload={
            "scopes": list(scopes),
            "authorization_endpoint": endpoints.authorization_url,
            "token_endpoint": endpoints.token_url,
        },
        secrets={
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "authorization_endpoint": endpoints.authorization_url,
            "token_endpoint": endpoints.token_url,
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
            "allow_signup": "true",
        }
    )
    logger.info(
        "connections.github.start user=%s state=%s redirect_uri=%s scopes=%s",
        _mask(user_id),
        _mask(state),
        redirect_uri,
        ",".join(scopes),
    )
    return f"{endpoints.authorization_url}?{params}"


def complete_github_oauth(db, *, state: str, code: str) -> dict:
    logger.info("connections.github.complete.begin state=%s code_len=%s", _mask(state), len(str(code or "")))
    oauth_state = consume_connection_oauth_state(db, state)
    if not oauth_state:
        raise ValueError("GitHub sign-in expired. Start the connection again.")
    if oauth_state.get("provider") != "github":
        raise ValueError("GitHub sign-in state is invalid.")

    secrets_payload = oauth_state.get("secrets") or {}
    client_id = str(secrets_payload.get("client_id") or "").strip()
    client_secret = str(secrets_payload.get("client_secret") or "").strip()
    token_endpoint = str(secrets_payload.get("token_endpoint") or "").strip()
    redirect_uri = str(oauth_state.get("redirect_uri") or "").strip()
    if not client_id or not client_secret or not token_endpoint or not redirect_uri:
        raise ValueError("GitHub OAuth configuration is incomplete.")

    assert_http_url_allowed(
        db,
        url=token_endpoint,
        feature="GitHub workspace connection OAuth completion",
    )

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            token_endpoint,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    access_token = str(payload.get("access_token") or "").strip()
    if response.status_code != 200 or not access_token:
        error_message = payload.get("error_description") or payload.get("error") or response.text
        raise ValueError(f"Failed to exchange GitHub OAuth code: {error_message}")

    scopes = _normalize_scopes(payload.get("scope") or secrets_payload.get("scopes"))
    logger.info(
        "connections.github.complete.success state=%s user=%s scopes=%s",
        _mask(state),
        _mask(oauth_state.get("user_id")),
        ",".join(scopes),
    )
    return {
        "user_id": str(oauth_state.get("user_id") or "").strip(),
        "return_path": str(oauth_state.get("return_path") or "/workspace/connections").strip() or "/workspace/connections",
        "secrets": {
            "access_token": access_token,
            "refresh_token": None,
            "expires_at": None,
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


def build_github_oauth_revocation_url(db) -> str:
    oauth_client = _github_oauth_client_settings(db)
    endpoints = oauth_client["endpoints"]
    assert isinstance(endpoints, GitHubOAuthEndpoints)
    client_id = str(oauth_client["client_id"])
    return f"{endpoints.application_grants_url}/{client_id}/grant"


def revoke_github_oauth_grant(db, *, access_token: str) -> None:
    normalized_token = str(access_token or "").strip()
    if not normalized_token:
        raise ValueError("GitHub OAuth revocation requires an access token.")

    oauth_client = _github_oauth_client_settings(db)
    credentials = base64.b64encode(
        f"{oauth_client['client_id']}:{oauth_client['client_secret']}".encode("utf-8")
    ).decode("ascii")
    url = build_github_oauth_revocation_url(db)
    assert_http_url_allowed(
        db,
        url=url,
        feature="GitHub workspace connection OAuth revocation",
    )

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.delete(
            url,
            json={"access_token": normalized_token},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": GITHUB_OAUTH_REVOCATION_API_VERSION,
            },
        )

    if response.status_code == 204:
        return

    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    error_message = payload.get("message") or f"status {response.status_code}"
    raise ValueError(f"GitHub OAuth revocation failed: {error_message}")
