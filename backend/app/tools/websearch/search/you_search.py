from typing import Any, Dict

import requests

from app.network.policy import DEFAULT_WEBSEARCH_PROVIDER_TARGETS
from app.tools.websearch.http_errors import raise_provider_http_error


def you_search_urls(
    api_key: str,
    query: str,
    country: str | None = None,
    fallback_country: str | None = "US",
    count: int | None = 10,
) -> Dict[str, Any]:

    candidate = str(country or fallback_country or "US").strip().upper()
    normalized_country = candidate if len(candidate) == 2 and candidate.isalpha() else "US"

    params: dict[str, Any] = {
        "query": query,
        "country": normalized_country,
    }
    params["count"] = max(1, min(int(count) if count is not None else 10, 100))

    headers = {"X-API-Key": api_key}

    try:
        response = requests.get(
            f"{DEFAULT_WEBSEARCH_PROVIDER_TARGETS['you']}/v1/search",
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="You.com", operation="search")
    except (requests.RequestException, ValueError) as exc:
        raise Exception(str(exc)) from exc

    results = payload.get("results", {}).get("web", []) if isinstance(payload.get("results"), dict) else []
    results_to_save = []
    for result in (results or []):
        if isinstance(result, dict) and result.get("url"):
            results_to_save.append({
                "title": result.get("title", ""),
                "url": result.get("url"),
                "preview": result.get("description", ""),
            })
 
    return {
        "result": results_to_save,
        "metadata": {
            "provider_search": "you",
        },
    }
