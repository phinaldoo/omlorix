from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from typing import Mapping, Optional, Tuple
import asyncio
import ipaddress
import logging
import time
import threading
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType

import anyio
import httpx

from app.auth.models import check_blocked_ip_address, record_ip_address_security_event
from app.redis_client import get_async_redis_client
from app.settings.utils import get_value_by_page_and_key, coerce_bool
from app.utils.client_ip import (
    extract_client_ip_from_request,
    resolve_configured_trusted_proxy_networks,
)
from app.utils.ip_restrictions import ip_restrictions_disabled_by_environment


logger = logging.getLogger(__name__)
_NO_POLICY_WARNING_EMITTED = False
_warned_untrusted_forwarded_headers = False


_IP_POLICY_SETTING_KEYS = (
    ("security", "enable_ip_restrictions"),
    ("security", "enable_ip_address_restrictions"),
    ("security", "enable_ip_country_restrictions"),
    ("security", "only_allow_specific_ip"),
    ("security", "only_allow_ip_from_specific_countries"),
    ("security", "ip_address_restriction_mode"),
    ("security", "ip_country_restriction_mode"),
    ("security", "allow_specific_ip"),
    ("security", "block_specific_ip"),
    ("security", "allow_country_ip"),
    ("security", "block_country_ip"),
    ("security", "allow_ip_if_no_country_found"),
    ("security", "check_ip_location_provider"),
)


@dataclass(frozen=True)
class _IPPolicySettingsSnapshot:
    values: Mapping[tuple[str, str], object]


@dataclass(frozen=True)
class _PreparedIPRequest:
    client_ip: str | None
    settings: _IPPolicySettingsSnapshot | None = None
    denial: tuple[int, str] | None = None


def _route_category(path: str) -> str:
    """Return a coarse route family without persisting resource identifiers."""

    normalized = str(path or "").lower()
    if normalized.startswith("/api/v1/auth"):
        return "auth"
    if normalized.startswith("/api/v1/admin"):
        return "admin"
    if normalized.startswith("/api/v1/chats"):
        return "chats"
    if normalized.startswith("/api/v1"):
        return "api"
    return "frontend"



# -------------------
# IP Restriction Middleware
# -------------------
IP_RESTRICTION_BYPASS_ENDPOINTS = {
    "/health",
    "/healthz",
    "/ready",
    "/api/v1/client-ip",
}


class IPRestrictionMiddleware:
    """Apply the configured IP access policy to HTTP and WebSocket traffic.

    Starlette's ``BaseHTTPMiddleware`` deliberately forwards non-HTTP ASGI
    scopes without invoking ``dispatch``. Implementing the middleware directly
    against ASGI keeps the policy at the application boundary for both request
    protocols and prevents individual WebSocket routes from having to remember
    a separate authorization dependency.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject disallowed HTTP or WebSocket clients before route dispatch."""

        scope_type = scope["type"]
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        path = connection.url.path

        # Health endpoints are HTTP-only platform probes. Do not turn the
        # bypass list into generic path exemptions for WebSocket handshakes.
        if scope_type == "http" and path in IP_RESTRICTION_BYPASS_ENDPOINTS:
            await self.app(scope, receive, send)
            return

        if ip_restrictions_disabled_by_environment():
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "WEBSOCKET")
        db_factory = connection.app.state.db
        # The default AnyIO worker limiter bounds concurrency. The worker owns
        # the session from creation through close, so a synchronous SQLAlchemy
        # session is never shared with the ASGI task or another thread.
        prepared = await anyio.to_thread.run_sync(
            self._prepare_request_in_worker,
            connection,
            db_factory,
            method,
            path,
        )
        if prepared.denial is not None:
            http_status, http_detail = prepared.denial
            await self._send_denial(
                scope,
                receive,
                send,
                http_status=http_status,
                http_detail=http_detail,
            )
            return

        # The immutable settings snapshot contains no ORM state. Geo-IP Redis
        # and HTTP work therefore stays natively async on the application loop.
        if prepared.client_ip is None or prepared.settings is None:
            raise RuntimeError("IP policy preparation returned incomplete state")
        client_ip = prepared.client_ip
        allowed, denial_reason, country_code = await evaluate_ip_policy(
            client_ip,
            prepared.settings,
        )
        if not allowed:
            await anyio.to_thread.run_sync(
                self._record_policy_denial_in_worker,
                db_factory,
                client_ip,
                denial_reason,
                country_code,
                path,
            )
            logger.warning(
                "Rejected request from disallowed IP %s: %s %s",
                client_ip,
                method,
                path,
            )
            await self._send_denial(
                scope,
                receive,
                send,
                http_status=403,
                http_detail="Access from your IP address is not allowed",
            )
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _prepare_request_in_worker(
        connection: HTTPConnection,
        db_factory,
        method: str,
        path: str,
    ) -> _PreparedIPRequest:
        """Load request policy state in one worker-owned database session."""

        db = db_factory()
        try:
            client_ip = get_client_ip(connection, db)
            if not client_ip:
                logger.warning(
                    "Rejected request because client IP could not be determined: %s %s",
                    method,
                    path,
                )
                return _PreparedIPRequest(
                    client_ip=None,
                    denial=(400, "Could not determine client IP address"),
                )

            # Admin-managed temporary blocks are distinct from the configurable
            # exact-IP/country policy and continue to take precedence.
            if check_blocked_ip_address(client_ip, db):
                try:
                    record_ip_address_security_event(
                        db,
                        client_ip,
                        "request_denied",
                        event_source="ip_restriction_middleware",
                        reason_code="active_ban",
                        route_category=_route_category(path),
                        reason="Blocked IP attempted to access Omlorix",
                        aggregate=True,
                    )
                except Exception:
                    logger.exception(
                        "Failed to record blocked IP middleware security event for %s",
                        client_ip,
                    )
                    db.rollback()
                logger.warning(
                    "Rejected request from blocked IP %s: %s %s",
                    client_ip,
                    method,
                    path,
                )
                return _PreparedIPRequest(
                    client_ip=client_ip,
                    denial=(403, "Access from your IP address has been blocked"),
                )

            return _PreparedIPRequest(
                client_ip=client_ip,
                settings=_load_ip_policy_settings_snapshot(db),
            )
        finally:
            db.close()

    @staticmethod
    def _record_policy_denial_in_worker(
        db_factory,
        client_ip: str,
        denial_reason: str | None,
        country_code: str | None,
        path: str,
    ) -> None:
        """Record a configured-policy denial in a fresh worker-owned session."""

        db = db_factory()
        try:
            try:
                record_ip_address_security_event(
                    db,
                    client_ip,
                    "request_denied",
                    event_source="ip_policy",
                    reason_code=denial_reason,
                    route_category=_route_category(path),
                    country_code=country_code,
                    reason="Request denied by configured IP access policy",
                    aggregate=True,
                )
            except Exception:
                logger.exception("Failed to record IP policy denial for %s", client_ip)
                db.rollback()
        finally:
            db.close()

    @staticmethod
    async def _send_denial(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        http_status: int,
        http_detail: str,
    ) -> None:
        """Return the protocol-appropriate denial without exposing policy data."""

        if scope["type"] == "websocket":
            # Closing before ``websocket.accept`` rejects the handshake. Do not
            # expose allowlist or geolocation details in the close frame.
            await send(
                {
                    "type": "websocket.close",
                    "code": 1008,
                }
            )
            return

        response = JSONResponse(
            status_code=http_status,
            content={"detail": http_detail},
        )
        await response(scope, receive, send)



# -------------------
# Validate IP
# -------------------
def is_valid_ip(ip_address: str) -> bool:
    """Validate if a string is a valid IP address"""
    try:
        ipaddress.ip_address(ip_address)
        return True
    except ValueError:
        return False


def _freeze_policy_setting(value):
    """Copy mutable setting containers into an immutable request snapshot."""

    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_policy_setting(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze_policy_setting(item) for item in value)
    return value


def _load_ip_policy_settings_snapshot(db) -> _IPPolicySettingsSnapshot:
    """Read every policy setting while the worker-owned session is open."""

    values: dict[tuple[str, str], object] = {
        (page, key): _freeze_policy_setting(
            get_value_by_page_and_key(page, key, db)
        )
        for page, key in _IP_POLICY_SETTING_KEYS
    }
    provider = str(
        values.get(("security", "check_ip_location_provider")) or ""
    ).strip()
    if provider in {"ipinfo", "ipstack"}:
        values[("api_keys", provider)] = _freeze_policy_setting(
            get_value_by_page_and_key("api_keys", provider, db)
        )
    return _IPPolicySettingsSnapshot(MappingProxyType(values))


def _policy_setting_value(page: str, key: str, source):
    if isinstance(source, _IPPolicySettingsSnapshot):
        return source.values.get((page, key))
    return get_value_by_page_and_key(page, key, source)


def _normalize_policy_values(raw_values) -> list[str]:
    """Return non-empty string values from a scalar or list setting."""
    if not isinstance(raw_values, (list, tuple, set)):
        raw_values = [raw_values]
    return [str(value or "").strip() for value in raw_values if str(value or "").strip()]


def _canonical_policy_ip_values(raw_values: list[str]) -> set[str]:
    """Return exact IP policy values in the same form as runtime client IPs."""
    normalized: set[str] = set()
    for value in raw_values:
        candidate = str(value or "").strip()
        if candidate.lower() == "localhost":
            normalized.add("127.0.0.1")
            continue
        if "%" in candidate:
            logger.warning("Ignoring scoped IPv6 policy value '%s'", candidate)
            continue
        try:
            normalized.add(ipaddress.ip_address(candidate).compressed)
        except ValueError:
            logger.warning("Ignoring invalid IP restriction policy value '%s'", candidate)
    return normalized


def _canonical_country_values(raw_values: list[str]) -> set[str]:
    """Return country policy values using provider-style uppercase codes."""
    return {str(value or "").strip().upper() for value in raw_values if str(value or "").strip()}


def _setting_bool(db, key: str, default: bool = False) -> bool:
    """Read a security boolean setting with the project's standard coercion."""
    return coerce_bool(_policy_setting_value("security", key, db), default=default)


def _setting_text(db, key: str, default: str = "") -> str:
    """Read a security text setting as a trimmed lowercase value."""
    return str(_policy_setting_value("security", key, db) or default).strip().lower()


def _exact_ip_rules_enabled(db, allowed_ips: list[str], blocked_ips: list[str]) -> bool:
    """Return whether exact-IP policy should participate in request checks."""
    configured_value = _policy_setting_value("security", "enable_ip_address_restrictions", db)
    if configured_value is not None:
        return coerce_bool(configured_value, default=False)

    # Backward-compatible fallback for older settings rows that predate the
    # explicit exact-IP enable switch.
    return bool(
        coerce_bool(_policy_setting_value("security", "only_allow_specific_ip", db), default=False)
        or allowed_ips
        or blocked_ips
    )


def _country_rules_enabled(db, allowed_countries: list[str], blocked_countries: list[str]) -> bool:
    """Return whether country policy should participate in request checks."""
    configured_value = _policy_setting_value("security", "enable_ip_country_restrictions", db)
    if configured_value is not None:
        return coerce_bool(configured_value, default=False)

    # Backward-compatible fallback for older settings rows that predate the
    # explicit country-rule enable switch.
    return bool(
        coerce_bool(_policy_setting_value("security", "only_allow_ip_from_specific_countries", db), default=False)
        or allowed_countries
        or blocked_countries
    )


def _exact_ip_policy_mode(db, allowed_ips: list[str] | None = None) -> str:
    """Return the exact-IP mode, falling back to the legacy allow-only toggle."""
    mode = _setting_text(db, "ip_address_restriction_mode", "")
    if mode in {"allowlist", "blocklist"}:
        return mode
    return "allowlist" if _setting_bool(db, "only_allow_specific_ip") and bool(allowed_ips) else "blocklist"


def _country_policy_mode(db, allowed_countries: list[str] | None = None) -> str:
    """Return the country mode, falling back to the legacy allow-only toggle."""
    mode = _setting_text(db, "ip_country_restriction_mode", "")
    if mode in {"allowlist", "blocklist"}:
        return mode
    return "allowlist" if _setting_bool(db, "only_allow_ip_from_specific_countries") and bool(allowed_countries) else "blocklist"


def _ip_restriction_policy_state(db) -> tuple[bool, bool, bool, list[str], list[str], list[str], list[str]]:
    """Return active policy state and normalized policy lists."""
    allowed_ips = _normalize_policy_values(_policy_setting_value("security", "allow_specific_ip", db) or [])
    blocked_ips = _normalize_policy_values(_policy_setting_value("security", "block_specific_ip", db) or [])
    allowed_countries = _normalize_policy_values(_policy_setting_value("security", "allow_country_ip", db) or [])
    blocked_countries = _normalize_policy_values(_policy_setting_value("security", "block_country_ip", db) or [])

    exact_rules_enabled = _exact_ip_rules_enabled(db, allowed_ips, blocked_ips)
    country_rules_enabled = _country_rules_enabled(db, allowed_countries, blocked_countries)
    exact_mode = _exact_ip_policy_mode(db, allowed_ips)
    country_mode = _country_policy_mode(db, allowed_countries)
    specific_allow_enabled = exact_rules_enabled and exact_mode == "allowlist" and bool(allowed_ips)
    specific_block_enabled = exact_rules_enabled and exact_mode == "blocklist" and bool(blocked_ips)
    country_allow_enabled = country_rules_enabled and country_mode == "allowlist" and bool(allowed_countries)
    country_block_enabled = country_rules_enabled and country_mode == "blocklist" and bool(blocked_countries)
    has_active_policy = bool(specific_allow_enabled or specific_block_enabled or country_allow_enabled or country_block_enabled)
    return has_active_policy, specific_allow_enabled, country_allow_enabled, allowed_ips, blocked_ips, allowed_countries, blocked_countries


def _warn_no_ip_restriction_policy_once() -> None:
    global _NO_POLICY_WARNING_EMITTED
    if _NO_POLICY_WARNING_EMITTED:
        return
    _NO_POLICY_WARNING_EMITTED = True
    logger.warning(
        "security.enable_ip_restrictions is true, but no IP or country allow/block policy is configured; "
        "IP restriction enforcement is skipped until at least one policy list is populated."
    )



# -------------------
# Cache Geo-IP results for 1 h (3600 s) in process memory
# -------------------
_COUNTRY_CACHE: dict[Tuple[str, str, str], Tuple[str, float]] = {}
_CACHE_LOCK = threading.Lock()
_COUNTRY_CACHE_TTL_SECONDS = 3600
_COUNTRY_LOCK_TTL_SECONDS = 15
_COUNTRY_LOCK_WAIT_ATTEMPTS = 8
_COUNTRY_LOCK_WAIT_SECONDS = 0.15


def _parse_trusted_proxy_entries(raw_entries) -> tuple[ipaddress._BaseNetwork, ...]:
    if not isinstance(raw_entries, (list, tuple, set)):
        raw_entries = [raw_entries]

    parsed: list[ipaddress._BaseNetwork] = []
    for entry in raw_entries:
        value = str(entry or "").strip()
        if not value:
            continue
        try:
            if "/" in value:
                parsed.append(ipaddress.ip_network(value, strict=False))
            else:
                ip_obj = ipaddress.ip_address(value)
                prefix = 32 if ip_obj.version == 4 else 128
                parsed.append(ipaddress.ip_network(f"{ip_obj}/{prefix}", strict=False))
        except ValueError:
            logger.warning("Ignoring invalid trusted proxy entry '%s'", value)
    return tuple(parsed)


def _is_trusted_proxy(client_host: str, trusted_entries: tuple[ipaddress._BaseNetwork, ...]) -> bool:
    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(client_ip in network for network in trusted_entries)


def _validate_forwarded_chain(
    header_ips: list[str],
    trusted_entries: tuple[ipaddress._BaseNetwork, ...],
    client_host: str,
) -> str | None:
    sanitized_chain: list[ipaddress._BaseAddress] = []
    for raw_ip in header_ips:
        candidate = str(raw_ip or "").strip()
        if not candidate:
            continue
        try:
            sanitized_chain.append(ipaddress.ip_address(candidate))
        except ValueError:
            logger.warning("Rejecting malformed X-Forwarded-For entry '%s'", candidate)
            return None

    try:
        direct_proxy_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return None

    full_chain = sanitized_chain + [direct_proxy_ip]
    if len(full_chain) < 2:
        return None

    for proxy_ip in full_chain[1:]:
        if not any(proxy_ip in network for network in trusted_entries):
            logger.warning("Rejecting forwarded chain because proxy '%s' is not trusted", proxy_ip)
            return None

    return str(full_chain[0])


def _country_cache_key(ip: str, provider: str, token: Optional[str]) -> str:
    token_fingerprint = hashlib.sha1((token or "").encode("utf-8")).hexdigest()[:12]
    return f"omlorix:geoip:{provider or 'unknown'}:{token_fingerprint}:{ip}"


def _country_lock_key(cache_key: str) -> str:
    return f"{cache_key}:lock"



# -------------------
# Cache country
# -------------------
async def _cache_country(ip: str, provider: str, token: Optional[str]) -> str:
    """Cached Geo-IP lookup using async HTTP client."""

    redis_client = await get_async_redis_client()
    redis_cache_key = _country_cache_key(ip, provider or "", token)
    if redis_client is not None:
        try:
            cached = await redis_client.get(redis_cache_key)
            if isinstance(cached, str) and cached:
                return cached
        except Exception:
            pass

        lock_key = _country_lock_key(redis_cache_key)
        lock_owner = f"{time.time():.6f}:{ip}"
        acquired_lock = False
        try:
            acquired_lock = bool(
                await redis_client.set(
                    lock_key,
                    lock_owner,
                    nx=True,
                    ex=_COUNTRY_LOCK_TTL_SECONDS,
                )
            )
        except Exception:
            acquired_lock = False

        if acquired_lock:
            try:
                country = await _fetch_country(ip, provider, token)
                try:
                    await redis_client.set(redis_cache_key, country, ex=_COUNTRY_CACHE_TTL_SECONDS)
                except Exception:
                    pass
                return country
            finally:
                script = (
                    "if redis.call('get', KEYS[1]) == ARGV[1] "
                    "then return redis.call('del', KEYS[1]) "
                    "else return 0 end"
                )
                try:
                    await redis_client.eval(script, 1, lock_key, lock_owner)
                except Exception:
                    pass

        for _ in range(_COUNTRY_LOCK_WAIT_ATTEMPTS):
            await asyncio.sleep(_COUNTRY_LOCK_WAIT_SECONDS)
            try:
                cached = await redis_client.get(redis_cache_key)
                if isinstance(cached, str) and cached:
                    return cached
            except Exception:
                break

        country = await _fetch_country(ip, provider, token)
        with suppress(Exception):
            await redis_client.set(redis_cache_key, country, ex=_COUNTRY_CACHE_TTL_SECONDS)
        return country

    cache_key = (ip, provider or "", token or "")
    now = time.time()
    with _CACHE_LOCK:
        entry = _COUNTRY_CACHE.get(cache_key)
        if entry and now - entry[1] < _COUNTRY_CACHE_TTL_SECONDS:
            return entry[0]

    # Perform async lookup outside the lock to avoid blocking other requests.
    country = await _fetch_country(ip, provider, token)

    with _CACHE_LOCK:
        _COUNTRY_CACHE[cache_key] = (country, time.time())

    return country



# -------------------
# Check if IP is allowed
# -------------------
async def evaluate_ip_policy(ip_address: str, db) -> tuple[bool, str | None, str | None]:
    """
    Evaluate an IP policy and return ``(allowed, reason_code, country_code)``.
    
    Args:
        ip_address: The IP address to check (IPv4 or IPv6)
        db: Database session for retrieving settings
        
    Returns:
        The reason code is populated only for denials and is intentionally
        stable so analytics can group policy decisions without parsing text.
    """
    if ip_address == "localhost":
        ip_address = "127.0.0.1"

    if not is_valid_ip(ip_address):
        return False, "invalid_ip", None

    ip = ipaddress.ip_address(ip_address)

    # Get enable_ip_restrictions setting
    if not coerce_bool(_policy_setting_value("security", "enable_ip_restrictions", db), default=False):
        return True, None, None

    (
        has_active_policy,
        specific_allow_enabled,
        country_allow_enabled,
        allowed_ips,
        blocked_ips,
        allowed_countries,
        blocked_countries,
    ) = _ip_restriction_policy_state(db)
    if not has_active_policy:
        _warn_no_ip_restriction_policy_once()
        return True, None, None

    if specific_allow_enabled:
        normalized_allowed_ips = _canonical_policy_ip_values(allowed_ips)
        exact_ip_allowed = str(ip) in normalized_allowed_ips or (
            ip.is_loopback and "localhost" in normalized_allowed_ips
        )
        if not exact_ip_allowed:
            return False, "not_in_ip_allowlist", None

    if _exact_ip_rules_enabled(db, allowed_ips, blocked_ips) and _exact_ip_policy_mode(db, allowed_ips) == "blocklist":
        normalized_blocked_ips = _canonical_policy_ip_values(blocked_ips)
        if str(ip) in normalized_blocked_ips:
            return False, "ip_blocklist", None

    country_block_enabled = (
        _country_rules_enabled(db, allowed_countries, blocked_countries)
        and _country_policy_mode(db, allowed_countries) == "blocklist"
        and bool(blocked_countries)
    )
    if not country_allow_enabled and not country_block_enabled:
        return True, None, None

    country = await get_country_by_ip(ip_address, db)
    if country == "Unknown":
        if coerce_bool(_policy_setting_value("security", "allow_ip_if_no_country_found", db), default=False):
            return True, None, None
        else:
            return False, "unknown_country", None

    if country_allow_enabled:
        if country in _canonical_country_values(allowed_countries):
            return True, None, country
        return False, "country_not_allowlisted", country
            
    if country_block_enabled and country in _canonical_country_values(blocked_countries):
        return False, "country_blocklist", country
        
    return True, None, country


async def is_ip_allowed(ip_address: str, db) -> bool:
    """Compatibility wrapper returning only the allow/deny decision."""

    allowed, _reason_code, _country_code = await evaluate_ip_policy(ip_address, db)
    return allowed
        






# -------------------
# Get client IP
# -------------------
def get_client_ip(request: HTTPConnection, db) -> Optional[str]:
    """Extract an HTTP or WebSocket client IP with trusted-proxy safeguards."""
    global _warned_untrusted_forwarded_headers

    client_scope = request.scope.get("client")
    if not client_scope:
        logger.warning("Request missing client scope; cannot determine remote IP.")
        return None

    client_host = client_scope[0]
    if not client_host:
        logger.warning("Empty client host provided by ASGI scope; rejecting headers.")
        return None

    if not is_valid_ip(client_host):
        logger.warning("Invalid client host '%s' reported by ASGI scope; rejecting request headers.", client_host)
        return None

    trusted_entries = resolve_configured_trusted_proxy_networks(
        db,
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
    )
    if trusted_entries:
        return extract_client_ip_from_request(
            request,
            trusted_proxy_networks=trusted_entries,
            default=None,
            reject_invalid_forwarded=True,
            validate_forwarded_chain=True,
        )

    if (
        not _warned_untrusted_forwarded_headers
        and (
            request.headers.get("X-Forwarded-For")
            or request.headers.get("X-Real-IP")
            or request.headers.get("Forwarded")
        )
    ):
        _warned_untrusted_forwarded_headers = True
        logger.warning(
            "Forwarded client IP headers are present but no trusted proxies are configured; "
            "using direct client IP (client=%s).",
            client_host,
        )
    return client_host



# -------------------
# Get country by IP
# -------------------
async def get_country_by_ip(
    ip: str,
    db,
    *,
    provider_override: str | None = None,
    token_override: Optional[str] = None,
) -> str:
    """Return an ISO country code for an IP using the configured or supplied provider.

    The optional overrides let pre-save safety checks validate a newly selected
    provider or API key before those values are persisted. Normal request-time
    lookups continue to use the database-backed configuration.
    """
    provider = provider_override or _policy_setting_value("security", "check_ip_location_provider", db)
    token: Optional[str] = None
    if provider in {"ipinfo", "ipstack"}:
        token = (
            token_override
            if provider_override is not None
            else _policy_setting_value("api_keys", provider, db)
        )

    try:
        return await _cache_country(ip, provider, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Geo-IP lookup failed for %s via %s: %s", ip, provider, exc)
        return "Unknown"



# -------------------
# Fetch country
# -------------------
async def _fetch_country(ip: str, provider: str | None, token: Optional[str]) -> str:
    """Perform async HTTP request to the configured Geo-IP provider."""

    if not provider:
        return "Unknown"

    timeout = httpx.Timeout(3.0, read=3.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if provider == "ipinfo":
                if not token:
                    return "Unknown"
                resp = await client.get(f"https://api.ipinfo.io/lite/{ip}?token={token}")
                resp.raise_for_status()
                return resp.json().get("country_code", "Unknown")
            if provider == "ipstack":
                if not token:
                    return "Unknown"
                resp = await client.get(f"https://api.ipstack.com/{ip}?access_key={token}")
                if resp.is_success:
                    return resp.json().get("country_code", "Unknown").upper()
                return "Unknown"
            if provider == "db-ip-free":
                resp = await client.get(f"https://api.db-ip.com/v2/free/{ip}")
                if resp.is_success:
                    return resp.json().get("countryCode", "Unknown").upper()
                return "Unknown"
        except httpx.HTTPError as exc:
            logger.debug("Geo-IP provider request failed (%s): %s", provider, exc)
            return "Unknown"

    logger.warning("Unsupported IP provider '%s'", provider)
    return "Unknown"
