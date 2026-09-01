import re
from typing import Any
from urllib.parse import urlparse

_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domain(value: Any) -> str:
    """Normalize one hostname-only rule for stable policy comparisons."""

    if not isinstance(value, str):
        # Pydantic field validators intentionally surface this as a validation
        # error; TypeError escapes validation in Pydantic v2.
        raise ValueError("Domain entries must be strings.")  # noqa: TRY004

    normalized = value.strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("Domain entries must not be empty.")
    if (
        "://" in normalized
        or "/" in normalized
        or "?" in normalized
        or "#" in normalized
    ):
        raise ValueError(
            "Domain entries must be hostnames only, without scheme or path."
        )

    labels = normalized.split(".")
    if not labels or any(not label for label in labels):
        raise ValueError("Domain entries must be valid hostnames.")
    for label in labels:
        if not _DOMAIN_LABEL_PATTERN.fullmatch(label):
            raise ValueError("Domain entries must be valid hostnames.")

    return normalized


def normalize_domain_list(value: Any) -> list[str]:
    """Normalize and deduplicate a plain list of hostname rules."""

    if value in (None, ""):
        return []

    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    seen: set[str] = set()

    for item in values:
        domain = normalize_domain(item)
        if domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)

    return normalized


def extract_hostname(url: str) -> str | None:
    """Extract a normalized hostname without treating a path as a domain."""

    try:
        hostname = urlparse(url).hostname
    except (TypeError, ValueError):
        return None
    if not hostname:
        return None
    return hostname.strip().lower().rstrip(".") or None


def hostname_matches_domain(hostname: str | None, domain: str) -> bool:
    """Match an exact hostname or one of its subdomains."""

    if not hostname:
        return False
    normalized_host = hostname.strip().lower().rstrip(".")
    normalized_domain = domain.strip().lower().rstrip(".")
    return normalized_host == normalized_domain or normalized_host.endswith(
        f".{normalized_domain}"
    )


def url_is_allowed_by_domains(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> bool:
    """Return whether *url* satisfies the complete hostname policy.

    When a policy is active, an entry without a parseable hostname fails
    closed. A block rule always wins over an allow rule, including when the
    blocked hostname is a subdomain of an allowed parent.
    """

    resolved_allowed = normalize_domain_list(allowed_domains)
    resolved_blocked = normalize_domain_list(blocked_domains)
    if not resolved_allowed and not resolved_blocked:
        return True

    hostname = extract_hostname(url)
    if not hostname:
        return False

    blocked = any(
        hostname_matches_domain(hostname, domain) for domain in resolved_blocked
    )
    if blocked:
        return False
    if not resolved_allowed:
        return True
    return any(hostname_matches_domain(hostname, domain) for domain in resolved_allowed)


def filter_urls_by_domains(
    urls: list[str],
    *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Filter URL strings while preserving order and duplicate entries."""

    resolved_allowed = normalize_domain_list(allowed_domains)
    resolved_blocked = normalize_domain_list(blocked_domains)

    kept: list[str] = []
    rejected = 0
    for url in urls:
        if not url_is_allowed_by_domains(
            url,
            allowed_domains=resolved_allowed,
            blocked_domains=resolved_blocked,
        ):
            rejected += 1
            continue
        kept.append(url)

    return kept, {
        "input_count": len(urls),
        "output_count": len(kept),
        "filtered_count": rejected,
    }


def resolve_websearch_provider_domain_filters(provider) -> tuple[list[str], list[str]]:
    """Resolve the canonical allow and block lists stored on one provider."""

    if not provider:
        return [], []
    if isinstance(provider, dict):
        settings = provider.get("settings", {})
    else:
        settings = (
            provider.settings
            if isinstance(getattr(provider, "settings", None), dict)
            else {}
        )

    if not isinstance(settings, dict):
        settings = {}

    return (
        normalize_domain_list(settings.get("allowed_domains")),
        normalize_domain_list(settings.get("blocked_domains")),
    )


def websearch_provider_has_domain_filters(provider: Any | None) -> bool:
    """Return whether a provider has an active effective hostname policy."""

    allowed_domains, blocked_domains = resolve_websearch_provider_domain_filters(
        provider
    )
    return bool(allowed_domains or blocked_domains)


def websearch_url_is_allowed(url: str, provider: Any | None) -> bool:
    """Evaluate a URL against the effective policy stored on *provider*."""

    allowed_domains, blocked_domains = resolve_websearch_provider_domain_filters(
        provider
    )
    return url_is_allowed_by_domains(
        url,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )


def filter_websearch_urls_by_domains(
    urls: list[str],
    provider: Any | None,
) -> tuple[list[str], dict[str, int]]:
    """Apply one provider's policy to every candidate target URL."""

    allowed_domains, blocked_domains = resolve_websearch_provider_domain_filters(
        provider
    )
    return filter_urls_by_domains(
        urls,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )


def filter_scraped_webpages_by_domains(
    webpages: list[str],
    provider: Any | None,
) -> tuple[list[str], dict[str, int]]:
    """Backward-compatible alias for callers using the old webpage name."""

    return filter_websearch_urls_by_domains(webpages, provider)


def filter_websearch_result_entries_by_domains(
    entries: list[Any],
    provider: Any | None,
) -> tuple[list[Any], dict[str, int]]:
    """Remove returned content whose source URL violates provider policy.

    Provider-native filters remain useful because they stop work upstream. The
    local check is still required as defense in depth and for remote scrapers
    that report a redirect destination different from the submitted URL.
    Entries without a usable source URL fail closed whenever a policy is active.
    """

    if not websearch_provider_has_domain_filters(provider):
        return list(entries), {
            "input_count": len(entries),
            "output_count": len(entries),
            "filtered_count": 0,
        }

    kept: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_url = entry.get("url") or entry.get("link") or entry.get("source_url")
        if not isinstance(raw_url, str) or not websearch_url_is_allowed(
            raw_url, provider
        ):
            continue
        kept.append(entry)

    return kept, {
        "input_count": len(entries),
        "output_count": len(kept),
        "filtered_count": len(entries) - len(kept),
    }


def filter_websearch_provider_response_by_domains(
    payload: Any,
    provider: Any | None,
) -> Any:
    """Recheck a provider payload without changing its list/dict envelope.

    This helper gives both scrape and combined dispatchers the same fail-closed
    response invariant. A policy-bearing provider cannot return content with a
    missing or disallowed source URL, even if the upstream service ignored a
    native rule or reports a different redirect destination.
    """

    if not websearch_provider_has_domain_filters(provider):
        return payload

    if isinstance(payload, list):
        filtered, _metadata = filter_websearch_result_entries_by_domains(
            payload,
            provider,
        )
        return filtered

    if isinstance(payload, dict):
        filtered_payload = dict(payload)
        for result_key in ("result", "results"):
            if result_key not in filtered_payload:
                continue
            raw_entries = filtered_payload.get(result_key)
            entries = raw_entries if isinstance(raw_entries, list) else []
            filtered_entries, _metadata = filter_websearch_result_entries_by_domains(
                entries,
                provider,
            )
            filtered_payload[result_key] = filtered_entries
            return filtered_payload

        # Keep non-result metadata but make the empty result explicit. Returning
        # an unverified provider mapping would otherwise create an easy bypass
        # for a new or malformed adapter.
        filtered_payload["result"] = []
        return filtered_payload

    return []
