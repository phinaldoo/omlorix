import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

import anyio
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal
from app.settings.utils import get_value_by_page_and_key
from app.settings.public_urls import normalize_public_urls


logger = logging.getLogger(__name__)
_CORS_CACHE_GENERATION = 0
_CORS_CACHE_GENERATION_LOCK = threading.Lock()
_CORS_CACHE_SECONDS = 5.0


def invalidate_cors_allowed_origins() -> None:
    """Invalidate every dynamic CORS snapshot in this process.

    Settings writers can call this after committing ``general.public_url``.
    The next request carrying an Origin header refreshes its middleware
    instance off the event loop. A short TTL also catches changes made by
    another process or instance.
    """

    global _CORS_CACHE_GENERATION
    with _CORS_CACHE_GENERATION_LOCK:
        _CORS_CACHE_GENERATION += 1


def _cors_cache_generation() -> int:
    with _CORS_CACHE_GENERATION_LOCK:
        return _CORS_CACHE_GENERATION


def _normalize_origin(value: str | None) -> str | None:
    """Normalize CORS origin URL."""
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").strip().lower()
        port = parsed.port
    except ValueError:
        return None
    if not scheme or not host:
        return None
    if (scheme == "https" and (port is None or port == 443)) or (
        scheme == "http" and (port is None or port == 80)
    ):
        return f"{scheme}://{host}"
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def _load_cors_allowed_origins() -> list[str]:
    """Load CORS allowed origins from environment and settings."""

    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def _to_port(raw: str | None, default: int) -> int:
        """Parse port number from string."""
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        return value if 0 < value <= 65535 else default

    def _bootstrap_local_origins() -> list[str]:
        """Generate localhost origins for bootstrap."""
        http_port = _to_port(os.getenv("FRONTEND_HTTP_HOST_PORT"), 80)

        candidates = [
            f"http://localhost:{http_port}",
            f"http://127.0.0.1:{http_port}",
            "http://localhost",
            "http://127.0.0.1",
        ]

        normalized = [_normalize_origin(origin) for origin in candidates]
        return _dedupe([origin for origin in normalized if origin])

    env_value = os.getenv("CORS_ALLOW_ORIGINS")
    if env_value:
        origins = [origin.strip() for origin in env_value.split(",") if origin.strip()]
        normalized = [_normalize_origin(origin) for origin in origins]
        normalized = _dedupe([origin for origin in normalized if origin])
        if normalized:
            return normalized

    configured_candidates = [os.getenv("PUBLIC_URL")]

    db = None
    try:
        db = SessionLocal()
        db_origins = normalize_public_urls(
            get_value_by_page_and_key("general", "public_url", db),
            allow_empty=True,
        )
        configured_candidates.extend(db_origins)
    except HTTPException as exc:
        if exc.status_code != 404:
            logger.warning("Unable to load public_url from settings for CORS configuration", exc_info=True)
    except Exception:
        logger.warning("Unable to load public_url from settings for CORS configuration", exc_info=True)
    finally:
        if db:
            db.close()

    configured_origins = []
    for candidate in configured_candidates:
        origin = _normalize_origin(candidate)
        if origin:
            configured_origins.append(origin)

    configured_origins = _dedupe(configured_origins)
    if configured_origins:
        return configured_origins

    mode = os.getenv("MODE", "production").strip().lower()
    if mode not in {"dev", "development", "local", "test"}:
        logger.warning(
            "No CORS_ALLOW_ORIGINS or general.public_url configured; CORS is disabled for production mode."
        )
        return []

    bootstrap_origins = _bootstrap_local_origins()
    logger.warning(
        "No CORS_ALLOW_ORIGINS or general.public_url configured yet; "
        "using bootstrap localhost origins in non-production mode."
    )
    return bootstrap_origins


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS middleware backed by an asynchronously refreshed snapshot."""

    def __init__(
        self,
        app,
        *,
        allow_origin_resolver: Callable[[], Sequence[str]],
        origin_cache_seconds: float | None = None,
        **kwargs,
    ):
        self.allow_origin_resolver = allow_origin_resolver
        self.origin_cache_seconds = max(
            1.0,
            float(origin_cache_seconds if origin_cache_seconds is not None else _CORS_CACHE_SECONDS),
        )
        self._allowed_origin_snapshot: frozenset[str] = frozenset()
        self._snapshot_expires_at = 0.0
        self._snapshot_generation = -1
        self._snapshot_lock = threading.Lock()
        self._refresh_lock = anyio.Lock()
        super().__init__(app, allow_origins=[], **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and any(
            key.lower() == b"origin" for key, _value in scope.get("headers", ())
        ):
            await self._ensure_allowed_origins_fresh()
        await super().__call__(scope, receive, send)

    def invalidate_allowed_origins(self) -> None:
        """Mark this middleware instance for refresh on the next CORS request."""

        with self._snapshot_lock:
            self._snapshot_expires_at = 0.0

    def refresh_allowed_origins(self) -> None:
        """Synchronously refresh the immutable snapshot.

        This method performs the resolver's I/O and is therefore called in a
        worker thread by ``__call__``. It is public for startup hooks and tests
        that already execute outside the event loop.
        """

        target_generation = _cors_cache_generation()
        try:
            resolved = self.allow_origin_resolver()
            normalized = {
                origin
                for origin in (_normalize_origin(value) for value in resolved)
                if origin
            }
        except Exception:
            logger.warning("Unable to refresh dynamic CORS origins", exc_info=True)
            # Fail closed because an origin may have been revoked, then retry
            # soon in case the settings store was only temporarily unavailable.
            with self._snapshot_lock:
                self._allowed_origin_snapshot = frozenset()
                self._snapshot_expires_at = time.monotonic() + min(
                    self.origin_cache_seconds,
                    5.0,
                )
                self._snapshot_generation = target_generation
            return

        with self._snapshot_lock:
            self._allowed_origin_snapshot = frozenset(normalized)
            self._snapshot_expires_at = time.monotonic() + self.origin_cache_seconds
            self._snapshot_generation = target_generation

    async def _ensure_allowed_origins_fresh(self) -> None:
        if self._snapshot_is_fresh():
            return

        async with self._refresh_lock:
            if self._snapshot_is_fresh():
                return
            # The resolver can query the synchronous SQLAlchemy settings store.
            # Keep that work off the ASGI event loop.
            await anyio.to_thread.run_sync(self.refresh_allowed_origins)

    def _snapshot_is_fresh(self) -> bool:
        generation = _cors_cache_generation()
        with self._snapshot_lock:
            return (
                self._snapshot_generation == generation
                and time.monotonic() < self._snapshot_expires_at
            )

    def is_allowed_origin(self, origin: str) -> bool:
        normalized_origin = _normalize_origin(origin)
        if not normalized_origin:
            return False
        with self._snapshot_lock:
            return normalized_origin in self._allowed_origin_snapshot
