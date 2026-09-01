from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging

from fastapi import HTTPException
import jwt
from jwt import InvalidTokenError as JWTError

from app.auth.token import _get_jwt_material


CHAT_SHARE_ACCESS_TOKEN_TYPE = "chat_share_access"
DEFAULT_CHAT_SHARE_TOKEN_TTL_SECONDS = 15 * 60
logger = logging.getLogger(__name__)


def _share_password_fingerprint(password_hash: str | None) -> str:
    """Return a SHA-256 fingerprint of the password hash, or "nopw" if empty."""
    cleaned = str(password_hash or "").strip()
    if not cleaned:
        return "nopw"
    return hashlib.sha256(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()


def create_chat_share_access_token(
    db,
    share_id: str,
    share_password_hash: str | None = None,
    expires_in_seconds: int = DEFAULT_CHAT_SHARE_TOKEN_TTL_SECONDS,
) -> tuple[str, datetime]:
    """Create a signed JWT access token for a shared chat. Returns (token, expires_at)."""
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        raise HTTPException(status_code=400, detail="share_id is required")

    ttl_seconds = max(60, int(expires_in_seconds or DEFAULT_CHAT_SHARE_TOKEN_TTL_SECONDS))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload = {
        "type": CHAT_SHARE_ACCESS_TOKEN_TYPE,
        "share_id": cleaned_share_id,
        "pwd_fp": _share_password_fingerprint(share_password_hash),
        "exp": expires_at,
    }
    secret, algorithm = _get_jwt_material()
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token, expires_at


def verify_chat_share_access_token(db, token: str) -> tuple[str, str]:
    """Verify and decode a chat share access JWT. Returns (share_id, pwd_fingerprint)."""
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise HTTPException(status_code=401, detail="Missing share access token")

    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(cleaned_token, secret, algorithms=[algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired share access token")

    if payload.get("type") != CHAT_SHARE_ACCESS_TOKEN_TYPE:
        raise HTTPException(status_code=401, detail="Invalid share access token")

    share_id = str(payload.get("share_id") or "").strip()
    if not share_id:
        raise HTTPException(status_code=401, detail="Invalid share access token")

    pwd_fp = str(payload.get("pwd_fp") or "").strip()
    if not pwd_fp:
        pwd_fp = "nopw"
        logger.debug("Accepted legacy chat share token without pwd_fp for share_id=%s", share_id)

    return share_id, pwd_fp
