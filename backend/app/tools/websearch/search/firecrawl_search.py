# docs: https://docs.firecrawl.dev/api-reference/endpoint/search
from typing import Any, Dict

import requests

from app.tools.websearch.http_errors import raise_provider_http_error



def firecrawl_search_urls(
    api_key: str,
    query: str,
    country: str,
    fallback_country: str,
    num_results: int = 10,
    enterprise_option: str | None = None,
    base_url: str | None = "https://api.firecrawl.dev",
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    candidate = str(country or fallback_country or "US").strip().upper()
    if not base_url or base_url.strip() == "":
        base_url = "https://api.firecrawl.dev"
    base_url = base_url.rstrip("/")
    resolved_country = candidate if len(candidate) == 2 and candidate.isalpha() else "US"
    data = {
        "query": query,
        "limit": num_results,
        "country": resolved_country,
        "ignoreInvalidURLs": True,
    }
    AVAILABLE_ENTERPRISE_OPTIONS = {"zdr", "anon"}
    if (
        enterprise_option
        and enterprise_option != "none"
        and enterprise_option in AVAILABLE_ENTERPRISE_OPTIONS
    ):
        data["enterprise"] = [enterprise_option]

    try:
        response = requests.post(
            f"{base_url}/v2/search",
            headers=headers,
            json=data,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Firecrawl", operation="search")
    except requests.RequestException as exc:
        raise Exception(f"Firecrawl search failed: {exc}") from exc
    except ValueError as exc:
        raise Exception(f"Invalid Firecrawl JSON response: {exc}") from exc

    if not payload.get("success"):
        raise Exception(f"Firecrawl search failed: {payload.get('error') or 'Unknown error'}")

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    result_to_return = []
    for item in data.get("web", []):
        result_to_return.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "preview": item.get("description"),
        })
    return {
        "result": result_to_return,
        "metadata": {
            "provider_search": "firecrawl",
        },
    }
