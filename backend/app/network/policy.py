from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.settings.models import get_settings_page


class OutboundAccessMode(str, Enum):
    allow_all = "allow_all"
    private_only = "private_only"
    allowlist_only = "allowlist_only"
    deny_all = "deny_all"


DEFAULT_EXTERNAL_REQUESTS_MODE = OutboundAccessMode.allow_all.value
DEFAULT_EXTERNAL_REQUESTS_ALLOWLIST: list[str] = []
OPENROUTER_EU_PROVIDER_TARGET = "https://eu.openrouter.ai/api/v1"

LOCAL_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".home.arpa",
    ".lan",
    ".test",
)
LOCAL_HOSTS = {"localhost"}
PUBLIC_WEB_SCHEMES = {"http", "https"}
WEBHOOK_URL_MAX_LENGTH = 2048
DEFAULT_PROVIDER_TARGETS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    "openai_chat_completions": "https://api.openai.com/v1",
    "microsoft_azure": "https://management.azure.com",
    "anthropic": "https://api.anthropic.com",
    "anthropic_base": "https://api.anthropic.com",
    "google_aistudio": "https://generativelanguage.googleapis.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "xai": "https://api.x.ai/v1",
    "elevenlabs": "https://api.elevenlabs.io",
}
DEFAULT_WEBSEARCH_PROVIDER_TARGETS: dict[str, str] = {
    "duckduckgo": "https://duckduckgo.com",
    "exa": "https://api.exa.ai",
    "firecrawl": "https://api.firecrawl.dev",
    # Both Ollama web search and web fetch use the hosted service. Keeping the
    # real destination here ensures every global outbound mode evaluates the
    # hostname that the SDK will contact instead of the private-looking label.
    "ollama": "https://ollama.com",
    "serper": "https://google.serper.dev/search",
    "tavily": "https://api.tavily.com",
    # The You search and contents adapters both contact this hosted service.
    # Keep the policy target here so allowlist checks use the same canonical
    # hostname as the adapters.
    "you": "https://ydc-index.io",
    "perplexity": "https://api.perplexity.ai",
}


@dataclass(frozen=True)
class OutboundPolicySnapshot:
    offline_mode: bool
    mode: OutboundAccessMode
    allowlist: tuple[str, ...]


class OutboundRequestBlockedError(RuntimeError):
    def __init__(
        self,
        *,
        target: str | None = None,
        feature: str | None = None,
        policy_mode: OutboundAccessMode,
        reason: str,
    ) -> None:
        self.target = target
        self.feature = feature or "outbound request"
        self.policy_mode = policy_mode
        self.reason = reason
        detail = f"{self.feature} blocked by external requests policy: {reason}"
        if target:
            detail = f"{detail} (target: {target})"
        super().__init__(detail)

    def to_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(self),
        )


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _normalize_hostname(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    host = value.strip()
    if not host:
        return None
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.rstrip(".").lower() or None


def _extract_hostname(target: str | None) -> str | None:
    if not isinstance(target, str):
        return None
    text = target.strip()
    if not text:
        return None
    parsed = urlparse(text if "://" in text else f"//{text}")
    return _normalize_hostname(parsed.hostname or text)


def _normalize_allowlist_entries(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    normalized: list[str] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized


def get_outbound_policy_snapshot(db) -> OutboundPolicySnapshot:
    settings_page = get_settings_page(db, "general")
    settings_data = settings_page.data if settings_page and isinstance(settings_page.data, dict) else {}

    offline_mode = _coerce_bool(settings_data.get("offline_mode"), default=False)
    configured_mode = str(settings_data.get("external_requests_mode") or DEFAULT_EXTERNAL_REQUESTS_MODE).strip().lower()
    mode = configured_mode if configured_mode in {item.value for item in OutboundAccessMode} else DEFAULT_EXTERNAL_REQUESTS_MODE
    effective_mode = OutboundAccessMode.private_only if offline_mode else OutboundAccessMode(mode)
    allowlist = tuple(_normalize_allowlist_entries(settings_data.get("external_requests_allowlist")))
    return OutboundPolicySnapshot(
        offline_mode=offline_mode,
        mode=effective_mode,
        allowlist=allowlist,
    )


def _is_private_ip_address(hostname: str) -> bool:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


@lru_cache(maxsize=512)
def _resolve_host_ips(hostname: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return ()
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = sockaddr[0]
        if ip and ip not in ips:
            ips.append(ip)
    return tuple(ips)


def _is_public_ip_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
    )


def _is_public_network_target(target: str | None) -> bool:
    hostname = _extract_hostname(target)
    if not hostname:
        return False
    if hostname in LOCAL_HOSTS:
        return False
    if any(hostname.endswith(suffix) for suffix in LOCAL_HOST_SUFFIXES):
        return False
    if "." not in hostname and ":" not in hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return _is_public_ip_address(hostname)

    resolved_ips = _resolve_host_ips(hostname)
    if not resolved_ips:
        return False
    return all(_is_public_ip_address(ip) for ip in resolved_ips)


def is_private_network_target(target: str | None) -> bool:
    hostname = _extract_hostname(target)
    if not hostname:
        return False
    if hostname in LOCAL_HOSTS:
        return True
    if any(hostname.endswith(suffix) for suffix in LOCAL_HOST_SUFFIXES):
        return True
    if "." not in hostname:
        return True
    if _is_private_ip_address(hostname):
        return True

    resolved_ips = _resolve_host_ips(hostname)
    if not resolved_ips:
        return False
    return all(_is_private_ip_address(ip) for ip in resolved_ips)


def _target_matches_allowlist_entry(target: str | None, entry: str) -> bool:
    hostname = _extract_hostname(target)
    if not hostname:
        return False

    candidate = entry.strip()
    if not candidate:
        return False

    try:
        network = ipaddress.ip_network(candidate, strict=False)
    except ValueError:
        network = None

    if network is not None:
        if _is_private_ip_address(hostname):
            try:
                return ipaddress.ip_address(hostname) in network
            except ValueError:
                pass
        for resolved_ip in _resolve_host_ips(hostname):
            try:
                if ipaddress.ip_address(resolved_ip) in network:
                    return True
            except ValueError:
                continue
        return False

    return _hostname_matches_allowlist_entry(hostname, candidate)


def _hostname_matches_allowlist_entry(hostname: str, entry: str) -> bool:
    """Match a hostname against only hostname-based allowlist entries.

    Connection-time checks use this narrower helper so a hostname allowlist can
    authorize the exact DNS name being connected while IP and CIDR entries are
    evaluated against the pinned peer address separately.
    """

    try:
        ipaddress.ip_network(entry, strict=False)
    except ValueError:
        pass
    else:
        return False

    parsed = urlparse(entry if "://" in entry else f"//{entry}")
    allow_host = _normalize_hostname(parsed.hostname or entry)
    if not allow_host:
        return False

    if allow_host.startswith("*."):
        suffix = allow_host[1:]
        return hostname.endswith(suffix)
    if allow_host.startswith("."):
        return hostname.endswith(allow_host)
    return hostname == allow_host


def _ip_matches_allowlist_entry(ip_address: str, entry: str) -> bool:
    """Return whether a pinned peer IP is covered by an IP/CIDR entry."""

    try:
        peer_ip = ipaddress.ip_address(ip_address)
        network = ipaddress.ip_network(entry.strip(), strict=False)
    except ValueError:
        return False
    return peer_ip in network


def target_is_allowlisted(target: str | None, allowlist: list[str] | tuple[str, ...]) -> bool:
    for entry in allowlist:
        if _target_matches_allowlist_entry(target, entry):
            return True
    return False


def assert_outbound_target_allowed(
    db,
    *,
    target: str | None,
    feature: str,
    require_private_allowlist: bool = False,
) -> None:
    snapshot = get_outbound_policy_snapshot(db)

    if snapshot.mode == OutboundAccessMode.allow_all:
        if (
            require_private_allowlist
            and is_private_network_target(target)
            and not target_is_allowlisted(target, snapshot.allowlist)
        ):
            raise OutboundRequestBlockedError(
                target=target,
                feature=feature,
                policy_mode=snapshot.mode,
                reason="local and private network destinations must be explicitly allowlisted",
            )
        return

    if snapshot.mode == OutboundAccessMode.deny_all:
        raise OutboundRequestBlockedError(
            target=target,
            feature=feature,
            policy_mode=snapshot.mode,
            reason="all outbound network access is disabled",
        )

    if snapshot.mode == OutboundAccessMode.private_only:
        if is_private_network_target(target):
            if require_private_allowlist and not target_is_allowlisted(target, snapshot.allowlist):
                raise OutboundRequestBlockedError(
                    target=target,
                    feature=feature,
                    policy_mode=snapshot.mode,
                    reason="local and private network destinations must be explicitly allowlisted",
                )
            return
        raise OutboundRequestBlockedError(
            target=target,
            feature=feature,
            policy_mode=snapshot.mode,
            reason="only local and private network destinations are allowed",
        )

    if snapshot.mode == OutboundAccessMode.allowlist_only:
        if target_is_allowlisted(target, snapshot.allowlist):
            return
        raise OutboundRequestBlockedError(
            target=target,
            feature=feature,
            policy_mode=snapshot.mode,
            reason="the destination is not in the configured allowlist",
        )


def assert_outbound_peer_ip_allowed(
    db,
    *,
    host: str,
    ip_address: str,
    port: int | None = None,
    feature: str,
) -> None:
    """Re-apply the active outbound policy to a pinned connection peer.

    URL checks protect the configured hostname, while this check closes the
    DNS time-of-check/time-of-use gap. Hostname allowlist entries continue to
    authorize that exact host; IP and CIDR entries must contain the actual peer
    selected for the connection.
    """

    snapshot = get_outbound_policy_snapshot(db)
    target = f"{host}:{port} ({ip_address})" if port is not None else f"{host} ({ip_address})"

    if snapshot.mode == OutboundAccessMode.allow_all:
        return
    if snapshot.mode == OutboundAccessMode.deny_all:
        raise OutboundRequestBlockedError(
            target=target,
            feature=feature,
            policy_mode=snapshot.mode,
            reason="all outbound network access is disabled",
        )
    if snapshot.mode == OutboundAccessMode.private_only:
        if is_private_network_target(ip_address):
            return
        raise OutboundRequestBlockedError(
            target=target,
            feature=feature,
            policy_mode=snapshot.mode,
            reason="the connected peer is not a local or private network destination",
        )

    normalized_host = _normalize_hostname(host)
    hostname_allowed = bool(normalized_host) and any(
        _hostname_matches_allowlist_entry(normalized_host, entry)
        for entry in snapshot.allowlist
    )
    peer_ip_allowed = any(
        _ip_matches_allowlist_entry(ip_address, entry)
        for entry in snapshot.allowlist
    )
    # A hostname allowlist entry authorizes its public peers. Private, local,
    # link-local, and otherwise non-public peers require an explicit IP/CIDR
    # entry; otherwise an allowlisted attacker-controlled hostname could rebind
    # to an internal service after the hostname check.
    if peer_ip_allowed or (hostname_allowed and _is_public_ip_address(ip_address)):
        return
    raise OutboundRequestBlockedError(
        target=target,
        feature=feature,
        policy_mode=snapshot.mode,
        reason="the connected peer is not in the configured allowlist",
    )


def _public_web_url_block_reason(url: str | None) -> str | None:
    if not isinstance(url, str):
        return "URL is missing"
    candidate = url.strip()
    if not candidate:
        return "URL is missing"
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in PUBLIC_WEB_SCHEMES:
        return "only http and https URLs are allowed"
    if not _normalize_hostname(parsed.hostname):
        return "URL host is missing"
    if not _is_public_network_target(candidate):
        return "private, local, link-local, and metadata-network destinations are not allowed"
    return None


def _public_https_webhook_url_block_reason(url: str | None) -> str | None:
    if not isinstance(url, str):
        return "Webhook URL is required"
    candidate = url.strip()
    if not candidate:
        return None
    if len(candidate) > WEBHOOK_URL_MAX_LENGTH:
        return "Webhook URL is too long"
    try:
        parsed = urlparse(candidate)
        parsed.port
    except ValueError:
        return "Webhook URL must use a valid host and port"
    if parsed.scheme.lower() != "https" or not _normalize_hostname(parsed.hostname):
        return "Webhook URL must be a valid https URL"
    if parsed.username or parsed.password:
        return "Webhook URL must not include credentials"
    if not _is_public_network_target(candidate):
        return "Webhook URL must target a publicly routable host"
    return None


def validate_and_normalize_public_webhook_url(url: str | None) -> str:
    normalized = "" if url is None else str(url).strip()
    reason = _public_https_webhook_url_block_reason(normalized)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    return normalized


def assert_public_webhook_url_allowed(db, *, url: str | None, feature: str) -> None:
    if db is not None:
        assert_outbound_target_allowed(db, target=url, feature=feature)
    reason = _public_https_webhook_url_block_reason(url)
    if reason is not None:
        snapshot_mode = (
            get_outbound_policy_snapshot(db).mode
            if db is not None
            else OutboundAccessMode.allow_all
        )
        raise OutboundRequestBlockedError(
            target=url,
            feature=feature,
            policy_mode=snapshot_mode,
            reason=reason,
        )


def is_public_web_url(url: str | None) -> bool:
    return _public_web_url_block_reason(url) is None


def assert_public_url_allowed(db, *, url: str | None, feature: str) -> None:
    if db is not None:
        assert_outbound_target_allowed(db, target=url, feature=feature)
    reason = _public_web_url_block_reason(url)
    if reason is not None:
        snapshot_mode = (
            get_outbound_policy_snapshot(db).mode
            if db is not None
            else OutboundAccessMode.allow_all
        )
        raise OutboundRequestBlockedError(
            target=url,
            feature=feature,
            policy_mode=snapshot_mode,
            reason=reason,
        )


def assert_public_resolved_ip_allowed(db, *, ip_address: str | None, feature: str) -> None:
    text = str(ip_address or "").strip()
    if not text or not _is_public_ip_address(text):
        snapshot_mode = (
            get_outbound_policy_snapshot(db).mode
            if db is not None
            else OutboundAccessMode.allow_all
        )
        raise OutboundRequestBlockedError(
            target=text or None,
            feature=feature,
            policy_mode=snapshot_mode,
            reason="resolved peer address is private, local, link-local, reserved, or otherwise non-public",
        )


def assert_url_allowed(db, *, url: str | None, feature: str) -> None:
    assert_outbound_target_allowed(db, target=url, feature=feature)


def assert_http_url_allowed(db, *, url: str | None, feature: str) -> None:
    """Require an HTTP(S) URL and apply the configured outbound policy."""

    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OutboundRequestBlockedError(
            target=text or None,
            feature=feature,
            policy_mode=get_outbound_policy_snapshot(db).mode,
            reason="URL must use http or https and include a hostname",
        )
    assert_url_allowed(db, url=text, feature=feature)


def assert_public_http_url_allowed(db, *, url: str | None, feature: str) -> None:
    assert_http_url_allowed(db, url=url, feature=feature)
    text = str(url or "").strip()
    if not _is_public_network_target(text):
        raise OutboundRequestBlockedError(
            target=text,
            feature=feature,
            policy_mode=get_outbound_policy_snapshot(db).mode,
            reason="personal MCP servers must use publicly routable destinations",
        )


def get_llm_provider_target(provider_type: str, settings: dict[str, Any] | None = None) -> str | None:
    provider_key = str(provider_type or "").strip().lower()
    settings = settings if isinstance(settings, dict) else {}

    if provider_key in {
        "openai",
        "openai_responses",
        "openai_chat_completions",
        "xai",
    }:
        return str(settings.get("base_url") or DEFAULT_PROVIDER_TARGETS[provider_key]).strip()
    if provider_key == "microsoft_azure":
        return str(settings.get("azure_endpoint") or DEFAULT_PROVIDER_TARGETS[provider_key]).strip()
    if provider_key in {"anthropic", "anthropic_base"}:
        return str(settings.get("base_url") or DEFAULT_PROVIDER_TARGETS[provider_key]).strip()
    if provider_key == "openrouter":
        if _coerce_bool(settings.get("eu_routing"), default=False):
            return OPENROUTER_EU_PROVIDER_TARGET
        return DEFAULT_PROVIDER_TARGETS[provider_key]
    if provider_key in {"google_aistudio", "elevenlabs"}:
        return DEFAULT_PROVIDER_TARGETS[provider_key]
    if provider_key in {"ollama", "lmstudio"}:
        return str(settings.get("base_url") or "").strip() or None
    return None


def assert_llm_provider_allowed(db, provider, *, feature: str) -> None:
    provider_settings = provider.settings if isinstance(getattr(provider, "settings", None), dict) else {}
    target = get_llm_provider_target(getattr(provider, "provider", None), provider_settings)
    assert_outbound_target_allowed(
        db,
        target=target or getattr(provider, "provider", None),
        feature=feature,
    )


def assert_llm_config_allowed(
    db,
    *,
    provider_type: str,
    settings: dict[str, Any] | None = None,
    feature: str,
    require_private_allowlist: bool = False,
) -> None:
    target = get_llm_provider_target(provider_type, settings)
    assert_outbound_target_allowed(
        db,
        target=target or provider_type,
        feature=feature,
        require_private_allowlist=require_private_allowlist,
    )


def get_websearch_provider_target(provider_type: str, settings: dict[str, Any] | None = None) -> str | None:
    provider_key = str(provider_type or "").strip().lower()
    settings = settings if isinstance(settings, dict) else {}

    if provider_key in {"searxng", "crawl4ai", "custom"}:
        return str(settings.get("base_url") or "").strip() or None
    if provider_key == "firecrawl":
        return str(settings.get("base_url") or DEFAULT_WEBSEARCH_PROVIDER_TARGETS[provider_key]).strip()
    if provider_key in {"duckduckgo", "exa", "ollama", "serper", "tavily", "you", "perplexity"}:
        return DEFAULT_WEBSEARCH_PROVIDER_TARGETS.get(provider_key)
    if provider_key == "aiohttp":
        return "direct-web-fetch"
    return None


def get_websearch_scrape_provider_target(provider_type: str, settings: dict[str, Any] | None = None) -> str | None:
    provider_key = str(provider_type or "").strip().lower()
    settings = settings if isinstance(settings, dict) else {}

    if provider_key == "custom":
        return str(settings.get("scrape_base_url") or settings.get("base_url") or "").strip() or None
    return get_websearch_provider_target(provider_key, settings)


def get_websearch_provider_targets(
    provider_type: str,
    settings: dict[str, Any] | None = None,
    *,
    include_scrape_target: bool = False,
) -> list[str]:
    provider_key = str(provider_type or "").strip().lower()
    settings = settings if isinstance(settings, dict) else {}
    primary_target = get_websearch_provider_target(provider_key, settings)
    targets = [primary_target] if primary_target else []
    if include_scrape_target and provider_key == "custom":
        scrape_target = get_websearch_scrape_provider_target(provider_key, settings)
        if scrape_target and scrape_target not in targets:
            targets.append(scrape_target)
    return targets


def assert_websearch_provider_allowed(
    db,
    provider,
    *,
    feature: str,
    use_scrape_target: bool = False,
    include_all_targets: bool = False,
) -> None:
    settings = provider.settings if isinstance(getattr(provider, "settings", None), dict) else {}
    if include_all_targets:
        targets = get_websearch_provider_targets(
            getattr(provider, "provider", None),
            settings,
            include_scrape_target=True,
        )
    else:
        target = (
            get_websearch_scrape_provider_target(getattr(provider, "provider", None), settings)
            if use_scrape_target
            else get_websearch_provider_target(getattr(provider, "provider", None), settings)
        )
        targets = [target] if target else []
    for target in targets or [getattr(provider, "provider", None)]:
        assert_outbound_target_allowed(
            db,
            target=target,
            feature=feature,
        )
