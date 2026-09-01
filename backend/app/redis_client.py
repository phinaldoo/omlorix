from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from contextlib import suppress
from typing import Any

from app.utils.async_cleanup import close_async_resource


logger = logging.getLogger(__name__)


_DEFAULT_REDIS_URL = "redis://redis:6379/0"
_DISABLE_VALUES = {"0", "false", "no", "off"}

_sync_client = None
_async_client = None
_async_client_epoch = 0
_client_lock = threading.Lock()
_last_connect_error_at = 0.0
_CONNECT_ERROR_COOLDOWN_SECONDS = 30.0


def redis_enabled() -> bool:
    """Check if Redis is enabled via environment variable."""
    value = (os.getenv("REDIS_ENABLED") or "true").strip().lower()
    return value not in _DISABLE_VALUES


def redis_url() -> str:
    """Get Redis URL from environment or default."""
    return (os.getenv("REDIS_URL") or _DEFAULT_REDIS_URL).strip() or _DEFAULT_REDIS_URL


def get_redis_client():
    """Return a shared synchronous Redis client or ``None`` when unavailable."""

    global _sync_client, _last_connect_error_at
    if not redis_enabled():
        return None

    with _client_lock:
        if _sync_client is not None:
            return _sync_client

        try:
            import redis

            client = redis.Redis.from_url(
                redis_url(),
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            client.ping()
            _sync_client = client
            return _sync_client
        except Exception as exc:  # noqa: BLE001
            now = time.time()
            if now - _last_connect_error_at >= _CONNECT_ERROR_COOLDOWN_SECONDS:
                logger.warning("Redis unavailable (%s). Falling back to local process state.", exc)
                _last_connect_error_at = now
            return None


async def get_async_redis_client():
    """Return a shared asynchronous Redis client or ``None`` when unavailable."""

    global _async_client, _last_connect_error_at
    if not redis_enabled():
        return None

    with _client_lock:
        if _async_client is not None:
            return _async_client
        connect_epoch = _async_client_epoch

    client = None
    try:
        client = _create_async_redis_client()
        await client.ping()
        with _client_lock:
            if _async_client is None and _async_client_epoch == connect_epoch:
                _async_client = client
                selected_client = client
            else:
                selected_client = _async_client

        if selected_client is not client:
            # Concurrent first requests can finish their connection probes at
            # the same time. Keep the winner and deterministically close the
            # unshared pool instead of leaving it for garbage collection.
            try:
                await _close_async_redis_instance(client)
            except Exception:
                logger.warning("Failed to close redundant Redis async client", exc_info=True)
        return selected_client
    except asyncio.CancelledError:
        if client is not None:
            try:
                await _close_async_redis_instance(client)
            except Exception:
                logger.warning("Failed to close cancelled Redis async client", exc_info=True)
        raise
    except Exception as exc:  # noqa: BLE001
        if client is not None:
            try:
                await _close_async_redis_instance(client)
            except Exception:
                logger.warning("Failed to close unusable Redis async client", exc_info=True)
        # Another concurrent probe may have installed a healthy shared client
        # while this probe was failing. Use that winner instead of needlessly
        # degrading this request to process-local behavior.
        with _client_lock:
            selected_client = _async_client
        if selected_client is not None:
            return selected_client
        now = time.time()
        if now - _last_connect_error_at >= _CONNECT_ERROR_COOLDOWN_SECONDS:
            logger.warning("Redis async client unavailable (%s).", exc)
            _last_connect_error_at = now
        return None


def _create_async_redis_client():
    """Create an unshared async client for the race-safe connection probe."""

    from redis.asyncio import Redis

    return Redis.from_url(
        redis_url(),
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
        health_check_interval=30,
        retry_on_timeout=True,
    )


async def _close_async_redis_instance(client) -> None:
    """Close one redis-py async client across supported SDK versions."""

    await close_async_resource(client)


async def close_async_redis_client() -> None:
    """Detach and close the process-wide async Redis pool."""

    global _async_client, _async_client_epoch
    with _client_lock:
        client = _async_client
        _async_client = None
        # Prevent an in-flight first-connection probe from installing itself
        # after shutdown detached the current pool.
        _async_client_epoch += 1
    if client is not None:
        await _close_async_redis_instance(client)


def redis_get_json(client, key: str, default: Any = None) -> Any:
    """Get JSON value from Redis key."""
    if client is None:
        return default
    try:
        raw = client.get(key)
        if raw is None:
            return default
        return json.loads(raw)
    except Exception:
        return default


def redis_set_json(client, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    """Set JSON value to Redis key with optional TTL."""
    if client is None:
        return False
    try:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        if ttl_seconds and ttl_seconds > 0:
            client.set(key, payload, ex=int(ttl_seconds))
        else:
            client.set(key, payload)
        return True
    except Exception:
        return False


def _lock_key(lock_name: str) -> str:
    """Generate Redis lock key."""
    return f"omlorix:lock:{lock_name}"


def try_acquire_lock(lock_name: str, owner: str, ttl_seconds: int) -> bool:
    """Try to acquire a distributed lock."""
    client = get_redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(_lock_key(lock_name), owner, nx=True, ex=max(1, int(ttl_seconds))))
    except Exception:
        return False


def refresh_lock(lock_name: str, owner: str, ttl_seconds: int) -> bool:
    """Extend a distributed lock only while ``owner`` still holds it.

    Long-running workers use this compare-and-expire operation as a lease
    heartbeat. Checking the owner inside Redis prevents a delayed worker from
    extending a lock that expired and was subsequently acquired by a peer.
    """

    client = get_redis_client()
    if client is None:
        # Local/single-process fallback matches try_acquire_lock semantics.
        return True
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] "
        "then return redis.call('expire', KEYS[1], ARGV[2]) "
        "else return 0 end"
    )
    try:
        return bool(
            client.eval(
                script,
                1,
                _lock_key(lock_name),
                owner,
                max(1, int(ttl_seconds)),
            )
        )
    except Exception:
        return False


def release_lock(lock_name: str, owner: str) -> None:
    """Release a distributed lock."""
    client = get_redis_client()
    if client is None:
        return
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] "
        "then return redis.call('del', KEYS[1]) "
        "else return 0 end"
    )
    with suppress(Exception):
        client.eval(script, 1, _lock_key(lock_name), owner)


def new_lock_owner() -> str:
    """Generate a unique lock owner identifier."""
    return str(uuid.uuid4())
