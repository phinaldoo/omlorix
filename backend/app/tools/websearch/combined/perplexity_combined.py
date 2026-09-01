# Docs: https://docs.perplexity.ai/api-reference/search-post
from typing import Any
import requests

from app.tools.websearch.domain_filters import normalize_domain_list
from app.tools.websearch.http_errors import raise_provider_http_error


PERPLEXITY_MAX_DOMAIN_FILTER_RULES = 20


class PerplexityDomainFilterError(ValueError):
    """Describe a canonical domain policy Perplexity cannot represent."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        max_rules: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.max_rules = max_rules


def build_perplexity_search_domain_filter(
    allowed_domains: list[str] | None,
    blocked_domains: list[str] | None,
) -> list[str]:
    """Translate Omlorix's canonical policy into Perplexity's signed format.

    Perplexity accepts one list containing either plain allow rules or
    ``-``-prefixed block rules. It cannot apply both modes in one request, so
    rejecting an unrepresentable policy here prevents a partially forwarded
    rule set from weakening the pre-fetch boundary.
    """

    normalized_allowed = normalize_domain_list(allowed_domains)
    normalized_blocked = normalize_domain_list(blocked_domains)

    if normalized_allowed and normalized_blocked:
        raise PerplexityDomainFilterError(
            "websearch_domain_policy_mixed_modes",
            "Perplexity domain filters must use either allowed_domains or "
            "blocked_domains, not both.",
        )

    rules = normalized_allowed or [f"-{domain}" for domain in normalized_blocked]
    if len(rules) > PERPLEXITY_MAX_DOMAIN_FILTER_RULES:
        raise PerplexityDomainFilterError(
            "websearch_domain_policy_too_many_rules",
            "Perplexity accepts at most "
            f"{PERPLEXITY_MAX_DOMAIN_FILTER_RULES} domain filter rules.",
            max_rules=PERPLEXITY_MAX_DOMAIN_FILTER_RULES,
        )

    return rules


def perplexity_combined_search(
    api_key: str,
    query: str,
    country: str | None = None,
    max_results: int | None = None,
    max_tokens_per_page: int | None = None,
    max_tokens: int | None = None,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    search_language_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Run a Perplexity search using Omlorix's canonical domain policy."""

    search_domain_filter = build_perplexity_search_domain_filter(
        allowed_domains,
        blocked_domains,
    )
    payload = {
        "query": query,
        "max_results": max_results if max_results is not None else 5,
        "max_tokens": max_tokens if max_tokens is not None else 1000000,
    }
    if country:
        payload["country"] = country
    if max_tokens_per_page:
        payload["max_tokens_per_page"] = max_tokens_per_page
    if search_domain_filter:
        # The provider-specific representation exists only at this outbound
        # boundary. Omlorix stores and evaluates separate canonical lists.
        payload["search_domain_filter"] = search_domain_filter
    if search_language_filter:
        payload["search_language_filter"] = search_language_filter[:10]

    try:
        response = requests.post(
            "https://api.perplexity.ai/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        response_data = response.json()
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Perplexity", operation="search")
    except (requests.RequestException, ValueError) as exc:
        raise Exception(f"Perplexity search failed: {exc}") from exc

    results = response_data.get("results", [])
    results_to_return = []
    for result in results:
        results_to_return.append({
            "title": result.get("title"),
            "url": result.get("url"),
            "content": result.get("snippet"),
        })
    return {
        "result": results_to_return,
        "metadata": {
            "provider_combined": "perplexity",
        },
    }
