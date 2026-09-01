from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
import time

import anyio
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import jwt
from jwt import InvalidTokenError as JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from app.redis_client import get_async_redis_client
from app.utils.client_ip import (
    extract_client_ip_from_request,
    resolve_configured_trusted_proxy_networks,
)


_DISABLE_VALUES = {"0", "false", "no", "off"}
_RATE_LIMIT_ANALYTICS_MAX_PENDING_KEYS = 4096
_RATE_LIMIT_ANALYTICS_FLUSH_SECONDS = 1.0
logger = logging.getLogger(__name__)


_RATE_LIMIT_ATTEMPT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = redis.call('TTL', KEYS[1])
end
return {current, ttl}
"""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return default


@dataclass(frozen=True)
class _RateLimitRule:
    name: str
    limit: int
    window_seconds: int


def _client_ip_from_request(request: Request, *, trusted_proxies) -> str | None:
    return extract_client_ip_from_request(request, trusted_proxy_networks=trusted_proxies, default=None)


def _has_forwarded_client_headers(request: Request) -> bool:
    headers = request.headers
    return bool(headers.get("x-forwarded-for") or headers.get("x-real-ip") or headers.get("forwarded"))


def _joined_env_values(*names: str) -> str:
    values: list[str] = []
    for name in names:
        raw = str(os.getenv(name) or "").strip()
        if raw:
            values.append(raw)
    return ",".join(values)


class RedisRateLimiterMiddleware(BaseHTTPMiddleware):
    """Fixed-window Redis rate limiter for high-cost API routes."""

    def __init__(self, app):
        super().__init__(app)
        enabled_raw = (os.getenv("RATE_LIMIT_ENABLED") or "true").strip().lower()
        self._enabled = enabled_raw not in _DISABLE_VALUES

        self._trusted_proxies_env_raw = _joined_env_values(
            "TRUSTED_PROXIES",
            "OMLORIX_TRUSTED_PROXIES",
            "RATE_LIMIT_TRUSTED_PROXIES",
        )
        self._trusted_proxies = resolve_configured_trusted_proxy_networks(None, "RATE_LIMIT_TRUSTED_PROXIES")
        self._settings_proxy_cache: tuple[float, list] | None = None
        self._settings_proxy_cache_ttl_seconds = max(10, _env_int("RATE_LIMIT_PROXY_SETTINGS_CACHE_SECONDS", 60))
        self._settings_proxy_cache_lock = anyio.Lock()

        self._default_rule = _RateLimitRule(
            name="default",
            limit=_env_int("RATE_LIMIT_DEFAULT_RPM", 180),
            window_seconds=60,
        )
        self._chat_rule = _RateLimitRule(
            name="chat_generation",
            limit=_env_int("RATE_LIMIT_CHAT_RPM", 30),
            window_seconds=60,
        )
        self._heavy_rule = _RateLimitRule(
            name="heavy_tools",
            limit=_env_int("RATE_LIMIT_HEAVY_RPM", 10),
            window_seconds=60,
        )
        self._widget_frame_rule = _RateLimitRule(
            name="widget_frames",
            # Transcript hydration creates one short-lived frame per persisted
            # interactive widget. Keep abuse bounded without making an ordinary
            # multi-widget chat exhaust the budget on first render.
            limit=_env_int("RATE_LIMIT_WIDGET_FRAME_RPM", 120),
            window_seconds=60,
        )
        self._auth_rule = _RateLimitRule(
            name="auth",
            limit=_env_int("RATE_LIMIT_AUTH_RPM", 40),
            window_seconds=60,
        )
        self._local_lock = threading.Lock()
        self._local_counts: dict[str, tuple[int, int]] = {}
        self._local_last_prune_at = 0.0
        self._warned_untrusted_forwarded_headers = False
        # Rate-limit rejection traffic is attacker-controlled. Keep analytics
        # off the response path and coalesce repeated events in a bounded map
        # before a single daemon worker writes them to the database.
        self._analytics_lock = threading.Lock()
        self._analytics_pending: dict[tuple[str, str], int] = {}
        self._analytics_db_factory = None
        self._analytics_worker: threading.Thread | None = None
        self._analytics_dropped_warning_emitted = False

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if path in {"/health", "/healthz", "/ready", "/metrics"}:
            return await call_next(request)

        rule = self._resolve_rule(path)
        if rule is None:
            return await call_next(request)

        # Trusted-proxy settings use the synchronous ORM. Refresh their small
        # process-local snapshot in the bounded worker pool before reading it
        # on the event loop.
        await self._ensure_settings_proxy_cache(request)
        subject = self._resolve_subject(request)
        window_start = int(time.time() // rule.window_seconds) * rule.window_seconds
        key = f"omlorix:ratelimit:{rule.name}:{subject}:{window_start}"
        redis_expiry_seconds = max(
            1,
            window_start + rule.window_seconds - int(time.time()),
        ) + 1
        redis_client = await get_async_redis_client()

        if redis_client is not None:
            try:
                current_count, ttl_seconds = await self._record_redis_attempt(
                    redis_client,
                    key,
                    redis_expiry_seconds,
                )
                if ttl_seconds <= 0:
                    ttl_seconds = rule.window_seconds
            except Exception:
                current_count, ttl_seconds = self._record_local_attempt(key, rule, window_start)
        else:
            current_count, ttl_seconds = self._record_local_attempt(key, rule, window_start)

        remaining = max(0, rule.limit - current_count)
        reset_at = int(time.time()) + ttl_seconds
        headers = {
            "X-RateLimit-Limit": str(rule.limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset_at),
        }

        if current_count > rule.limit:
            headers["Retry-After"] = str(ttl_seconds)
            self._record_rate_limit_event(request, rule)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please retry later."},
                headers=headers,
            )

        response = await call_next(request)
        for key_name, key_value in headers.items():
            response.headers[key_name] = key_value
        return response

    @staticmethod
    async def _record_redis_attempt(redis_client, key: str, expiry_seconds: int) -> tuple[int, int]:
        """Atomically increment a fixed-window counter and return its TTL."""

        result = await redis_client.eval(
            _RATE_LIMIT_ATTEMPT_SCRIPT,
            1,
            key,
            max(1, int(expiry_seconds)),
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RuntimeError("Redis returned an invalid rate-limit result")
        return int(result[0]), int(result[1])

    def _record_rate_limit_event(self, request: Request, rule: _RateLimitRule) -> None:
        """Queue an optional aggregate without database work on the request path."""

        db_factory = getattr(request.app.state, "db", None)
        if not callable(db_factory):
            return
        trusted_proxies = self._resolve_trusted_proxies(request)
        ip_address = _client_ip_from_request(request, trusted_proxies=trusted_proxies)
        if not ip_address:
            return
        pending_key = (ip_address, rule.name)
        start_worker = False
        with self._analytics_lock:
            if (
                pending_key not in self._analytics_pending
                and len(self._analytics_pending) >= _RATE_LIMIT_ANALYTICS_MAX_PENDING_KEYS
            ):
                # Analytics must never allow arbitrary IP cardinality to consume
                # unbounded process memory. Enforcement remains unaffected.
                if not self._analytics_dropped_warning_emitted:
                    logger.warning(
                        "Rate-limit analytics buffer is full; dropping optional analytics events"
                    )
                    self._analytics_dropped_warning_emitted = True
                return
            self._analytics_pending[pending_key] = (
                self._analytics_pending.get(pending_key, 0) + 1
            )
            self._analytics_db_factory = db_factory
            if self._analytics_worker is None:
                self._analytics_worker = threading.Thread(
                    target=self._rate_limit_analytics_worker,
                    name="rate-limit-analytics",
                    daemon=True,
                )
                start_worker = True
        if start_worker:
            self._analytics_worker.start()

    def _drain_rate_limit_analytics(self):
        """Atomically detach the current coalesced analytics batch."""

        with self._analytics_lock:
            pending = self._analytics_pending
            self._analytics_pending = {}
            return pending, self._analytics_db_factory

    def _flush_rate_limit_analytics(self, pending, db_factory) -> None:
        """Persist one bounded batch with one settings check and batched counts."""

        if not pending or not callable(db_factory):
            return
        db = db_factory()
        try:
            # Local imports avoid making middleware initialization part of the
            # authentication model import graph.
            from app.auth.models import (
                is_ip_address_statistics_enabled,
                record_ip_address_security_event,
            )

            if not is_ip_address_statistics_enabled(db):
                return
            for (ip_address, rule_name), request_count in pending.items():
                try:
                    # A savepoint isolates an unexpected per-key failure while
                    # allowing the whole drained batch to use one final commit.
                    with db.begin_nested():
                        record_ip_address_security_event(
                            db,
                            ip_address,
                            "rate_limited",
                            event_source="redis_rate_limiter",
                            reason_code=f"rate_limit_{rule_name}",
                            route_category=rule_name,
                            reason="Request rejected by application rate limit",
                            request_count=request_count,
                            aggregate=True,
                            commit=False,
                            statistics_enabled=True,
                        )
                except Exception:
                    # A malformed or conflicting event must not discard other
                    # independent IP/rule aggregates in the same drained batch.
                    logger.exception(
                        "Failed to flush one rate-limit analytics aggregate"
                    )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to initialize the rate-limit analytics flush")
        finally:
            db.close()

    def _rate_limit_analytics_worker(self) -> None:
        """Periodically flush coalesced rejection counts until the buffer is idle."""

        while True:
            time.sleep(_RATE_LIMIT_ANALYTICS_FLUSH_SECONDS)
            pending, db_factory = self._drain_rate_limit_analytics()
            self._flush_rate_limit_analytics(pending, db_factory)
            with self._analytics_lock:
                if self._analytics_pending:
                    continue
                self._analytics_worker = None
                self._analytics_dropped_warning_emitted = False
                return

    def _record_local_attempt(self, key: str, rule: _RateLimitRule, window_start: int) -> tuple[int, int]:
        now = time.time()
        window_end = window_start + rule.window_seconds
        with self._local_lock:
            if now - self._local_last_prune_at >= 60:
                self._local_counts = {
                    stored_key: value
                    for stored_key, value in self._local_counts.items()
                    if value[1] > now
                }
                self._local_last_prune_at = now

            current_count, expires_at = self._local_counts.get(key, (0, window_end))
            if expires_at <= now:
                current_count = 0
                expires_at = window_end
            current_count += 1
            self._local_counts[key] = (current_count, expires_at)
            ttl_seconds = max(1, int(expires_at - now))
            return current_count, ttl_seconds

    def _resolve_rule(self, path: str) -> _RateLimitRule | None:
        if not path.startswith("/api/v1"):
            return None

        if path.startswith("/api/v1/chats/send") or path.startswith("/api/v1/chats/regenerate"):
            return self._chat_rule

        if path.startswith("/api/v1/tools/slide_presentation"):
            return self._heavy_rule

        # Only frame creation is expensive. The opaque GET URL must remain
        # under the normal rule so loading several widgets does not consume the
        # creation budget.
        if path == "/api/v1/llm/widgets/frame":
            return self._widget_frame_rule

        if path.startswith("/api/v1/auth/"):
            return self._auth_rule

        return self._default_rule

    def _resolve_subject(self, request: Request) -> str:
        authorization = request.headers.get("Authorization") or ""
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
            if token:
                subject = self._subject_from_token(token)
                if subject:
                    return f"user:{subject}"

        trusted_proxies = self._resolve_trusted_proxies(request)
        self._warn_if_forwarded_headers_untrusted(request, trusted_proxies)
        host = _client_ip_from_request(request, trusted_proxies=trusted_proxies)
        if not host:
            raise HTTPException(status_code=400, detail="Unable to determine client IP for rate limiting")
        return f"ip:{host}"

    def _warn_if_forwarded_headers_untrusted(self, request: Request, trusted_proxies) -> None:
        if trusted_proxies or self._warned_untrusted_forwarded_headers or not _has_forwarded_client_headers(request):
            return

        self._warned_untrusted_forwarded_headers = True
        client_host = getattr(getattr(request, "client", None), "host", None) or "unknown"
        logger.warning(
            "Forwarded client IP headers are present but no trusted proxy CIDRs are configured; "
            "rate limiting will use the direct client IP (client=%s path=%s).",
            client_host,
            request.url.path,
        )

    def _resolve_trusted_proxies(self, request: Request):
        if self._trusted_proxies_env_raw:
            return self._trusted_proxies

        if self._settings_proxy_cache:
            return self._settings_proxy_cache[1]

        return []

    async def _ensure_settings_proxy_cache(self, request: Request) -> None:
        """Refresh database-backed trusted proxies without blocking ASGI."""

        if self._trusted_proxies_env_raw:
            return

        now = time.monotonic()
        if self._settings_proxy_cache and (
            now - self._settings_proxy_cache[0] <= self._settings_proxy_cache_ttl_seconds
        ):
            return

        async with self._settings_proxy_cache_lock:
            now = time.monotonic()
            if self._settings_proxy_cache and (
                now - self._settings_proxy_cache[0] <= self._settings_proxy_cache_ttl_seconds
            ):
                return

            db_factory = getattr(request.app.state, "db", None)
            if not callable(db_factory):
                self._settings_proxy_cache = (now, [])
                return

            parsed = await anyio.to_thread.run_sync(
                self._load_settings_trusted_proxies,
                db_factory,
            )
            self._settings_proxy_cache = (time.monotonic(), parsed)

    @staticmethod
    def _load_settings_trusted_proxies(db_factory) -> list:
        """Load trusted proxies in a worker-owned database session."""

        db = None
        try:
            db = db_factory()
            return resolve_configured_trusted_proxy_networks(db, "RATE_LIMIT_TRUSTED_PROXIES")
        except Exception:
            return []
        finally:
            if db is not None:
                db.close()

    def _subject_from_token(self, token: str) -> str | None:
        try:
            # The canonical helper is already process-cached and environment
            # only, so rate-limit identity resolution needs no database session.
            from app.auth.jwt_material import get_jwt_material

            secret, algorithm = get_jwt_material()
            claims = jwt.decode(token, secret, algorithms=[algorithm])
            sub = claims.get("sub")
            if isinstance(sub, str):
                cleaned = sub.strip()
                if cleaned:
                    return f"user:{cleaned}"
        except (JWTError, RuntimeError):
            return None
        return None
