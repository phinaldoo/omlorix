from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets

import httpx
from fastapi import HTTPException

from app.connections.models import PROVIDER_GMAIL, PROVIDER_GOOGLE_CALENDAR, PROVIDER_GOOGLE_DRIVE, consume_connection_oauth_state, save_connection_oauth_state
from app.settings.models import get_settings_page_data


GOOGLE_OAUTH_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_REVOCATION_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_OAUTH_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_STATE_TTL_MINUTES = 10
GOOGLE_WORKSPACE_MCP_COMMAND = "/usr/local/bin/google-workspace-worker"
GOOGLE_PROVIDER_SCOPES = {
    PROVIDER_GMAIL: [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.modify",
    ],
    PROVIDER_GOOGLE_CALENDAR: [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/calendar",
    ],
    PROVIDER_GOOGLE_DRIVE: [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/drive.readonly",
    ],
}
GOOGLE_PROVIDER_CAPABILITIES = {
    PROVIDER_GMAIL: ["gmail"],
    PROVIDER_GOOGLE_CALENDAR: ["calendar"],
}
# This metadata key is shared by the bundled worker and Omlorix's discovery
# boundary.  It describes the Google API capability required by a tool; it is
# deliberately separate from MCP's user-editable ``allowed_tools`` list.
GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY = "omlorix/capabilities"
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


def _google_oauth_client_settings(db) -> dict[str, str]:
    settings = get_settings_page_data(db, "login_social")
    if not _setting_enabled(settings, "enable_google_oauth"):
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is disabled. Enable Google OAuth in Admin -> Login -> OAuth.",
        )
    client_id = str(settings.get("google_client_id") or "").strip()
    client_secret = str(settings.get("google_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is not configured. Set google_client_id and google_client_secret in Admin -> Login -> OAuth.",
        )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
    }


def google_oauth_is_configured(db) -> bool:
    try:
        _google_oauth_client_settings(db)
        return True
    except HTTPException:
        return False


def google_picker_client_settings(db) -> dict[str, str]:
    """Return the public Google Picker identifiers configured by an administrator.

    The developer key is intentionally a browser key, not a server secret. Google
    requires it to be visible to Picker clients, so administrators must restrict
    it by HTTP referrer and to the Google Picker API in Google Cloud Console.
    """

    # Picker reuses the already connected user's server-issued access token, but
    # the identifiers must still belong to the configured OAuth project.
    _google_oauth_client_settings(db)
    settings = get_settings_page_data(db, "login_social")
    developer_key = str(settings.get("google_picker_api_key") or "").strip()
    app_id = str(settings.get("google_picker_app_id") or "").strip()
    if not developer_key or not app_id:
        raise HTTPException(
            status_code=503,
            detail="Google Picker is not configured on this server.",
        )
    if not app_id.isdigit():
        raise HTTPException(
            status_code=503,
            detail="Google Picker App ID must be the numeric Google Cloud project number.",
        )
    return {
        "developer_key": developer_key,
        "app_id": app_id,
    }


def _provider_scopes(provider: str) -> list[str]:
    scopes = GOOGLE_PROVIDER_SCOPES.get(str(provider or "").strip().lower())
    if not scopes:
        raise ValueError("Unsupported Google connection provider.")
    return list(scopes)


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


def _fetch_profile(access_token: str) -> dict[str, str]:
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.get(
            GOOGLE_OAUTH_USERINFO_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
        )
    if response.status_code != 200:
        logger.warning("connections.google.profile.failed status=%s body=%s", response.status_code, response.text)
        return {}
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(payload, dict):
        return {}
    return {
        "display_name": str(payload.get("name") or "").strip(),
        "email": str(payload.get("email") or "").strip(),
    }


def start_google_oauth(
    db,
    *,
    provider: str,
    user_id: str,
    return_path: str,
    redirect_uri: str,
) -> str:
    normalized_provider = str(provider or "").strip().lower()
    oauth_client = _google_oauth_client_settings(db)
    scopes = _provider_scopes(normalized_provider)
    state = _generate_state()
    expires_at = _utcnow() + timedelta(minutes=GOOGLE_OAUTH_STATE_TTL_MINUTES)
    save_connection_oauth_state(
        db,
        state=state,
        provider=normalized_provider,
        user_id=user_id,
        return_path=return_path,
        redirect_uri=redirect_uri,
        payload={
            "scopes": scopes,
            "authorization_endpoint": GOOGLE_OAUTH_AUTHORIZATION_URL,
            "token_endpoint": GOOGLE_OAUTH_TOKEN_URL,
        },
        secrets={
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "authorization_endpoint": GOOGLE_OAUTH_AUTHORIZATION_URL,
            "token_endpoint": GOOGLE_OAUTH_TOKEN_URL,
            "scopes": scopes,
        },
        expires_at=expires_at,
    )
    params = httpx.QueryParams(
        {
            "client_id": oauth_client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
    )
    logger.info(
        "connections.google.start provider=%s user=%s state=%s redirect_uri=%s scopes=%s",
        normalized_provider,
        _mask(user_id),
        _mask(state),
        redirect_uri,
        ",".join(scopes),
    )
    return f"{GOOGLE_OAUTH_AUTHORIZATION_URL}?{params}"


def complete_google_oauth(db, *, provider: str, state: str, code: str) -> dict:
    normalized_provider = str(provider or "").strip().lower()
    logger.info(
        "connections.google.complete.begin provider=%s state=%s code_len=%s",
        normalized_provider,
        _mask(state),
        len(str(code or "")),
    )
    oauth_state = consume_connection_oauth_state(db, state)
    if not oauth_state:
        raise ValueError("Google sign-in expired. Start the connection again.")
    if oauth_state.get("provider") != normalized_provider:
        raise ValueError("Google sign-in state is invalid.")

    secrets_payload = oauth_state.get("secrets") or {}
    client_id = str(secrets_payload.get("client_id") or "").strip()
    client_secret = str(secrets_payload.get("client_secret") or "").strip()
    redirect_uri = str(oauth_state.get("redirect_uri") or "").strip()
    token_endpoint = str(secrets_payload.get("token_endpoint") or GOOGLE_OAUTH_TOKEN_URL).strip()
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError("Google OAuth configuration is incomplete.")

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            token_endpoint,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
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
        raise ValueError(f"Failed to exchange Google OAuth code: {error_message}")

    refresh_token = str(payload.get("refresh_token") or "").strip() or None
    if not refresh_token:
        raise ValueError("Google OAuth did not return a refresh token. Reconnect and ensure consent is granted.")
    expires_in = payload.get("expires_in")
    try:
        expires_at = int((_utcnow() + timedelta(seconds=int(expires_in))).timestamp()) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None
    scopes = _normalize_scopes(payload.get("scope") or secrets_payload.get("scopes"))
    profile = _fetch_profile(access_token)
    logger.info(
        "connections.google.complete.success provider=%s state=%s user=%s email=%s scopes=%s",
        normalized_provider,
        _mask(state),
        _mask(oauth_state.get("user_id")),
        _mask(profile.get("email")),
        ",".join(scopes),
    )
    if normalized_provider == PROVIDER_GMAIL:
        display_name = "Gmail"
    elif normalized_provider == PROVIDER_GOOGLE_CALENDAR:
        display_name = "Google Calendar"
    else:
        display_name = "Google Drive"
    return {
        "user_id": str(oauth_state.get("user_id") or "").strip(),
        "return_path": str(oauth_state.get("return_path") or "/workspace/connections").strip() or "/workspace/connections",
        "display_name": display_name,
        "secrets": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": token_endpoint,
            "authorization_endpoint": secrets_payload.get("authorization_endpoint") or GOOGLE_OAUTH_AUTHORIZATION_URL,
            "issuer": "https://accounts.google.com",
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


def refresh_google_tokens(secrets: dict | None) -> dict:
    payload = secrets if isinstance(secrets, dict) else {}
    client_id = str(payload.get("client_id") or "").strip()
    client_secret = str(payload.get("client_secret") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    token_endpoint = str(payload.get("token_endpoint") or GOOGLE_OAUTH_TOKEN_URL).strip() or GOOGLE_OAUTH_TOKEN_URL
    if not client_id or not client_secret or not refresh_token:
        raise ValueError("Google connection is missing refresh credentials. Reconnect the account.")

    logger.info(
        "connections.google.refresh.begin client_id=%s has_refresh=%s",
        _mask(client_id),
        bool(refresh_token),
    )
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            token_endpoint,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    access_token = str(body.get("access_token") or "").strip()
    if response.status_code != 200 or not access_token:
        error_code = str(body.get("error") or "").strip().lower()
        error_description = body.get("error_description") or body.get("error") or response.text
        if error_code == "invalid_grant":
            logger.warning("connections.google.refresh.invalid_grant client_id=%s", _mask(client_id))
            raise ValueError("Google access expired. Reconnect the account.")
        raise ValueError(f"Failed to refresh Google access token: {error_description}")

    expires_in = body.get("expires_in")
    try:
        expires_at = int((_utcnow() + timedelta(seconds=int(expires_in))).timestamp()) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None

    refreshed_scopes = _normalize_scopes(body.get("scope") or payload.get("scopes"))
    logger.info(
        "connections.google.refresh.success client_id=%s expires_in=%s scopes=%s",
        _mask(client_id),
        expires_in,
        ",".join(refreshed_scopes),
    )
    return {
        **payload,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "token_endpoint": token_endpoint,
        "authorization_endpoint": payload.get("authorization_endpoint") or GOOGLE_OAUTH_AUTHORIZATION_URL,
        "issuer": payload.get("issuer") or "https://accounts.google.com",
        "scopes": refreshed_scopes,
    }


def revoke_google_token(token: str) -> None:
    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise ValueError("Google token revocation requires a token.")

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(
            GOOGLE_OAUTH_REVOCATION_URL,
            data={"token": normalized_token},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    if response.status_code == 200:
        return

    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    error_message = payload.get("error_description") or payload.get("error") or f"status {response.status_code}"
    raise ValueError(f"Google token revocation failed: {error_message}")
