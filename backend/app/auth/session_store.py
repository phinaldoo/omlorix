from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging

import jwt

from app.redis_client import get_redis_client


logger = logging.getLogger(__name__)


_ACCESS_KEY_PREFIX = "omlorix:auth:access"
_REFRESH_KEY_PREFIX = "omlorix:auth:refresh"
_USER_ACCESS_SET_PREFIX = "omlorix:auth:user_access"
_USER_REFRESH_SET_PREFIX = "omlorix:auth:user_refresh"

_DEFAULT_ACCESS_TTL_SECONDS = 60 * 60
_DEFAULT_REFRESH_TTL_SECONDS = 7 * 24 * 60 * 60


def _token_digest(token: str) -> str:
    """Generate SHA256 digest of token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_key(prefix: str, token: str) -> str:
    """Generate Redis key for token."""
    return f"{prefix}:{_token_digest(token)}"


def _user_token_set(prefix: str, user_id: str) -> str:
    """Generate Redis set key for user tokens."""
    return f"{prefix}:{user_id}"


def _token_ttl_seconds(token: str, default_ttl: int) -> int:
    """Extract a token TTL without trusting or verifying its JWT claims.

    Tokens reach this helper immediately after Omlorix creates or validates them.
    Signature verification is intentionally disabled here because the claims are
    used only to choose a Redis cache lifetime, never to authorize a request.
    """
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
        exp = claims.get("exp")
        if exp is None:
            return default_ttl
        exp_float = float(exp)
        now = datetime.now(timezone.utc).timestamp()
        return max(30, int(exp_float - now))
    except Exception:
        return default_ttl


def cache_session(user_id: str, access_token: str, refresh_token: str) -> bool:
    """Cache session tokens in Redis."""
    client = get_redis_client()
    if client is None:
        return False

    access_ttl = _token_ttl_seconds(access_token, _DEFAULT_ACCESS_TTL_SECONDS)
    refresh_ttl = _token_ttl_seconds(refresh_token, _DEFAULT_REFRESH_TTL_SECONDS)

    access_key = _token_key(_ACCESS_KEY_PREFIX, access_token)
    refresh_key = _token_key(_REFRESH_KEY_PREFIX, refresh_token)
    access_set = _user_token_set(_USER_ACCESS_SET_PREFIX, user_id)
    refresh_set = _user_token_set(_USER_REFRESH_SET_PREFIX, user_id)

    try:
        pipe = client.pipeline()
        pipe.set(access_key, user_id, ex=access_ttl)
        pipe.set(refresh_key, user_id, ex=refresh_ttl)
        pipe.sadd(access_set, _token_digest(access_token))
        pipe.sadd(refresh_set, _token_digest(refresh_token))
        pipe.expire(access_set, refresh_ttl)
        pipe.expire(refresh_set, refresh_ttl)
        pipe.execute()
        return True
    except Exception:
        logger.debug("Failed to cache auth session in Redis", exc_info=True)
        return False


def cache_access_token(user_id: str, access_token: str) -> bool:
    """Cache access token in Redis."""
    client = get_redis_client()
    if client is None:
        return False

    access_ttl = _token_ttl_seconds(access_token, _DEFAULT_ACCESS_TTL_SECONDS)
    access_key = _token_key(_ACCESS_KEY_PREFIX, access_token)
    access_set = _user_token_set(_USER_ACCESS_SET_PREFIX, user_id)

    try:
        pipe = client.pipeline()
        pipe.set(access_key, user_id, ex=access_ttl)
        pipe.sadd(access_set, _token_digest(access_token))
        pipe.expire(access_set, max(access_ttl, 60))
        pipe.execute()
        return True
    except Exception:
        logger.debug("Failed to cache access token in Redis", exc_info=True)
        return False


def cache_refresh_token(user_id: str, refresh_token: str) -> bool:
    """Cache refresh token in Redis."""
    client = get_redis_client()
    if client is None:
        return False

    refresh_ttl = _token_ttl_seconds(refresh_token, _DEFAULT_REFRESH_TTL_SECONDS)
    refresh_key = _token_key(_REFRESH_KEY_PREFIX, refresh_token)
    refresh_set = _user_token_set(_USER_REFRESH_SET_PREFIX, user_id)

    try:
        pipe = client.pipeline()
        pipe.set(refresh_key, user_id, ex=refresh_ttl)
        pipe.sadd(refresh_set, _token_digest(refresh_token))
        pipe.expire(refresh_set, refresh_ttl)
        pipe.execute()
        return True
    except Exception:
        logger.debug("Failed to cache refresh token in Redis", exc_info=True)
        return False


def token_exists(user_id: str, token: str, token_kind: str) -> bool:
    """Check if token exists in cache."""
    client = get_redis_client()
    if client is None:
        return False

    prefix = _ACCESS_KEY_PREFIX if token_kind == "access" else _REFRESH_KEY_PREFIX
    key = _token_key(prefix, token)
    try:
        stored_user_id = client.get(key)
        return isinstance(stored_user_id, str) and stored_user_id == user_id
    except Exception:
        return False


def rotate_access_token(user_id: str, old_access_token: str, new_access_token: str) -> None:
    """Rotate cached access token."""
    client = get_redis_client()
    if client is None:
        return

    old_key = _token_key(_ACCESS_KEY_PREFIX, old_access_token)
    new_key = _token_key(_ACCESS_KEY_PREFIX, new_access_token)
    access_set = _user_token_set(_USER_ACCESS_SET_PREFIX, user_id)

    new_ttl = _token_ttl_seconds(new_access_token, _DEFAULT_ACCESS_TTL_SECONDS)
    try:
        pipe = client.pipeline()
        pipe.delete(old_key)
        pipe.srem(access_set, _token_digest(old_access_token))
        pipe.set(new_key, user_id, ex=new_ttl)
        pipe.sadd(access_set, _token_digest(new_access_token))
        pipe.expire(access_set, max(new_ttl, 60))
        pipe.execute()
    except Exception:
        logger.debug("Failed to rotate cached access token", exc_info=True)


def rotate_session_tokens(
    user_id: str,
    old_access_token: str,
    old_refresh_token: str,
    new_access_token: str,
    new_refresh_token: str,
) -> None:
    """Rotate cached access and refresh tokens."""
    client = get_redis_client()
    if client is None:
        return

    access_set = _user_token_set(_USER_ACCESS_SET_PREFIX, user_id)
    refresh_set = _user_token_set(_USER_REFRESH_SET_PREFIX, user_id)
    new_access_ttl = _token_ttl_seconds(new_access_token, _DEFAULT_ACCESS_TTL_SECONDS)
    new_refresh_ttl = _token_ttl_seconds(new_refresh_token, _DEFAULT_REFRESH_TTL_SECONDS)

    try:
        pipe = client.pipeline()
        pipe.delete(_token_key(_ACCESS_KEY_PREFIX, old_access_token))
        pipe.delete(_token_key(_REFRESH_KEY_PREFIX, old_refresh_token))
        pipe.srem(access_set, _token_digest(old_access_token))
        pipe.srem(refresh_set, _token_digest(old_refresh_token))
        pipe.set(_token_key(_ACCESS_KEY_PREFIX, new_access_token), user_id, ex=new_access_ttl)
        pipe.set(_token_key(_REFRESH_KEY_PREFIX, new_refresh_token), user_id, ex=new_refresh_ttl)
        pipe.sadd(access_set, _token_digest(new_access_token))
        pipe.sadd(refresh_set, _token_digest(new_refresh_token))
        pipe.expire(access_set, max(new_access_ttl, 60))
        pipe.expire(refresh_set, max(new_refresh_ttl, 60))
        pipe.execute()
    except Exception:
        logger.debug("Failed to rotate cached session tokens", exc_info=True)


def revoke_tokens(
    *,
    user_id: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> None:
    """Revoke specific tokens from cache."""
    client = get_redis_client()
    if client is None:
        return

    try:
        pipe = client.pipeline()
        if access_token:
            pipe.delete(_token_key(_ACCESS_KEY_PREFIX, access_token))
            if user_id:
                pipe.srem(_user_token_set(_USER_ACCESS_SET_PREFIX, user_id), _token_digest(access_token))
        if refresh_token:
            pipe.delete(_token_key(_REFRESH_KEY_PREFIX, refresh_token))
            if user_id:
                pipe.srem(_user_token_set(_USER_REFRESH_SET_PREFIX, user_id), _token_digest(refresh_token))
        pipe.execute()
    except Exception:
        logger.debug("Failed to revoke cached session tokens", exc_info=True)


def revoke_token_digests(
    *,
    user_id: str | None = None,
    access_token_hash: str | None = None,
    refresh_token_hash: str | None = None,
) -> None:
    """Revoke specific cached tokens when only their stored SHA256 digests are available."""
    client = get_redis_client()
    if client is None:
        return

    try:
        pipe = client.pipeline()
        if access_token_hash:
            pipe.delete(f"{_ACCESS_KEY_PREFIX}:{access_token_hash}")
            if user_id:
                pipe.srem(_user_token_set(_USER_ACCESS_SET_PREFIX, user_id), access_token_hash)
        if refresh_token_hash:
            pipe.delete(f"{_REFRESH_KEY_PREFIX}:{refresh_token_hash}")
            if user_id:
                pipe.srem(_user_token_set(_USER_REFRESH_SET_PREFIX, user_id), refresh_token_hash)
        pipe.execute()
    except Exception:
        logger.debug("Failed to revoke cached session token digests", exc_info=True)


def revoke_user_sessions(user_id: str) -> None:
    """Revoke all sessions for a user."""
    client = get_redis_client()
    if client is None:
        return

    access_set_key = _user_token_set(_USER_ACCESS_SET_PREFIX, user_id)
    refresh_set_key = _user_token_set(_USER_REFRESH_SET_PREFIX, user_id)

    try:
        access_digests = client.smembers(access_set_key) or set()
        refresh_digests = client.smembers(refresh_set_key) or set()

        pipe = client.pipeline()
        for digest in access_digests:
            pipe.delete(f"{_ACCESS_KEY_PREFIX}:{digest}")
        for digest in refresh_digests:
            pipe.delete(f"{_REFRESH_KEY_PREFIX}:{digest}")
        pipe.delete(access_set_key)
        pipe.delete(refresh_set_key)
        pipe.execute()
    except Exception:
        logger.debug("Failed to revoke cached user sessions", exc_info=True)


def revoke_all_sessions() -> None:
    """Revoke all cached sessions."""
    client = get_redis_client()
    if client is None:
        return

    try:
        patterns = (
            f"{_ACCESS_KEY_PREFIX}:*",
            f"{_REFRESH_KEY_PREFIX}:*",
            f"{_USER_ACCESS_SET_PREFIX}:*",
            f"{_USER_REFRESH_SET_PREFIX}:*",
        )
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=500)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        logger.debug("Failed to revoke all cached sessions", exc_info=True)
