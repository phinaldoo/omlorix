# Docs: https://exa.ai/docs/reference/search
from typing import Any

import requests
from fastapi import HTTPException

from app.tools.websearch.http_errors import raise_provider_http_error

AVAILABLE_TYPES = frozenset({"auto", "fast", "instant"})
MAX_RETURNED_TEXT_CHARACTERS = 10_000
REQUEST_TIMEOUT_SECONDS = 20


def _truncate_returned_text(value: Any) -> str:
    """Bound Exa response text locally without sending unsupported API options."""

    if not isinstance(value, str):
        return ""
    return value[:MAX_RETURNED_TEXT_CHARACTERS]


def _normalize_user_location(value: Any) -> str | None:
    """Normalize Exa's optional userLocation value to a two-letter country code."""

    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if len(candidate) != 2 or not candidate.isalpha():
        return None
    return candidate


def exa_web_search_combined(
    api_key: str,
    query: str,
    max_results: int,
    search_type: str = "auto",
    user_location: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search Exa and return bounded webpage text with trusted cost metadata."""

    try:
        payload = {
            "query": query,
            "numResults": max_results,
            "type": search_type if search_type in AVAILABLE_TYPES else "auto",
            "contents": {"text": True},
        }
        normalized_user_location = _normalize_user_location(user_location)
        if normalized_user_location:
            payload["userLocation"] = normalized_user_location
        if include_domains:
            payload["includeDomains"] = include_domains
        if exclude_domains:
            payload["excludeDomains"] = exclude_domains
        response = requests.post(
            "https://api.exa.ai/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if not isinstance(results, list):
            results = []
        result_list = []
        for result in results:
            result_list.append(
                {
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "text": _truncate_returned_text(result.get("text")),
                }
            )

        cost = data.get("costDollars", {}).get("total")

        return {
            "result": result_list,
            "metadata": {
                "provider_combined": "exa",
                "cost": cost,
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Exa", operation="search")
    except HTTPException:
        raise
    except Exception as exc:
        raise Exception(f"Exa search failed: {exc}") from exc
