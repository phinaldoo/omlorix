from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from typing import Any, Sequence


_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        return str(raw).strip().lower() in _TRUE_VALUES
    return default


def _dedupe_networks(networks: Sequence[_Network]) -> list[_Network]:
    result: list[_Network] = []
    seen: set[str] = set()
    for network in networks:
        key = str(network)
        if key in seen:
            continue
        seen.add(key)
        result.append(network)
    return result


def parse_trusted_proxy_networks(raw: str | None) -> list[_Network]:
    networks: list[_Network] = []
    for item in str(raw or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except Exception:
            continue
        networks.append(network)
    return networks


def _trusted_proxy_setting_to_raw(value: Any) -> str:
    """Convert DB settings list/scalar values to the comma format parsed below."""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "")


def _resolve_db_trusted_proxy_networks(db) -> list[_Network]:
    """Resolve admin-configured trusted proxy networks from security settings."""
    if db is None:
        return []

    try:
        from app.settings.utils import coerce_bool, get_value_by_page_and_key

        trust_proxy_headers = coerce_bool(
            get_value_by_page_and_key("security", "trust_proxy_headers", db),
            default=False,
        )
        if not trust_proxy_headers:
            return []

        raw_trusted_proxies = get_value_by_page_and_key("security", "trusted_proxies", db)
    except Exception:
        return []

    return parse_trusted_proxy_networks(_trusted_proxy_setting_to_raw(raw_trusted_proxies))


def resolve_trusted_proxy_networks(*env_names: str) -> list[_Network]:
    env_snapshot = tuple(str(os.getenv(name) or "") for name in env_names)
    return list(_resolve_trusted_proxy_networks_cached(env_names, env_snapshot))


def resolve_configured_trusted_proxy_networks(db=None, *env_names: str) -> list[_Network]:
    """Resolve trusted proxy networks from environment variables and DB settings.

    Environment variables are available for bootstrapping Docker deployments
    before an administrator can reach the settings UI. Database settings allow
    admins to manage the same trust boundary from the security page later.
    """
    configured_env_names = (
        "TRUSTED_PROXIES",
        "OMLORIX_TRUSTED_PROXIES",
        *env_names,
    )
    networks: list[_Network] = []
    has_env_proxy_config = False
    for name in configured_env_names:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        parsed = parse_trusted_proxy_networks(raw)
        if parsed:
            has_env_proxy_config = True
            networks.extend(parsed)

    trust_env_headers = _env_bool("TRUST_PROXY_HEADERS", "OMLORIX_TRUST_PROXY_HEADERS")
    db_networks = _resolve_db_trusted_proxy_networks(db)
    networks.extend(db_networks)
    if not has_env_proxy_config and not trust_env_headers and not db_networks and not networks:
        return []
    return _dedupe_networks(networks)


@lru_cache(maxsize=32)
def _resolve_trusted_proxy_networks_cached(
    env_names: tuple[str, ...],
    env_snapshot: tuple[str, ...],
) -> tuple[_Network, ...]:
    networks: list[_Network] = []
    seen: set[str] = set()
    for _env_name, raw_value in zip(env_names, env_snapshot):
        for network in parse_trusted_proxy_networks(raw_value):
            key = str(network)
            if key in seen:
                continue
            seen.add(key)
            networks.append(network)

    if not networks:
        for fallback in ("127.0.0.1/32", "::1/128"):
            network = ipaddress.ip_network(fallback, strict=False)
            key = str(network)
            if key in seen:
                continue
            seen.add(key)
            networks.append(network)
    return tuple(networks)


def _parse_ip_token(raw: str | None) -> str | None:
    value = str(raw or "").strip().strip('"')
    if not value:
        return None

    if value.lower().startswith("for="):
        value = value[4:].strip().strip('"')

    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif value.count(":") == 1:
        host, port = value.split(":", 1)
        if port.isdigit():
            value = host.strip()

    try:
        return ipaddress.ip_address(value).compressed
    except Exception:
        return None


def _ip_in_trusted_networks(ip: str, trusted_proxy_networks: Sequence[_Network]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except Exception:
        return False
    return any(address in network for network in trusted_proxy_networks)


def _parse_x_forwarded_for(value: str | None, *, reject_invalid: bool = False) -> list[str] | None:
    ips: list[str] = []
    for token in str(value or "").split(","):
        parsed = _parse_ip_token(token)
        if parsed:
            ips.append(parsed)
        elif reject_invalid and str(token or "").strip():
            return None
    return ips


def extract_client_ip_from_request(
    request,
    *,
    trusted_proxy_networks: Sequence[_Network],
    default: str | None = None,
    reject_invalid_forwarded: bool = False,
    validate_forwarded_chain: bool = False,
) -> str | None:
    direct_ip = _parse_ip_token(getattr(getattr(request, "client", None), "host", None))
    if not direct_ip:
        return default

    if not trusted_proxy_networks or not _ip_in_trusted_networks(direct_ip, trusted_proxy_networks):
        return direct_ip

    forwarded_for = getattr(request, "headers", {}).get("x-forwarded-for", "")
    if forwarded_for:
        forwarded_chain = _parse_x_forwarded_for(forwarded_for, reject_invalid=reject_invalid_forwarded)
        if forwarded_chain is None or (validate_forwarded_chain and not forwarded_chain):
            return default
        if forwarded_chain:
            chain = [*forwarded_chain, direct_ip]
            if validate_forwarded_chain:
                for proxy_ip in chain[1:]:
                    if not _ip_in_trusted_networks(proxy_ip, trusted_proxy_networks):
                        return default
                return forwarded_chain[0]

            for candidate in reversed(chain):
                if not _ip_in_trusted_networks(candidate, trusted_proxy_networks):
                    return candidate
            return forwarded_chain[0]

    real_ip_header = getattr(request, "headers", {}).get("x-real-ip", "")
    real_ip = _parse_ip_token(real_ip_header)
    if real_ip:
        return real_ip
    if reject_invalid_forwarded and str(real_ip_header or "").strip():
        return default

    return direct_ip


def resolve_request_client_ip(request, *, default: str | None = None) -> str | None:
    """Resolve a request client IP using the backend's shared trusted-proxy rules."""
    trusted_networks = resolve_trusted_proxy_networks(
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
        "TRUSTED_PROXIES",
    )
    return extract_client_ip_from_request(request, trusted_proxy_networks=trusted_networks, default=default)


def resolve_audit_request_client_ip(request, db=None, *, default: str | None = None) -> str | None:
    """Resolve an audit IP using explicit env or DB trusted-proxy configuration."""
    trusted_networks = resolve_configured_trusted_proxy_networks(
        db,
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
    )
    return extract_client_ip_from_request(
        request,
        trusted_proxy_networks=trusted_networks,
        default=default,
    )
