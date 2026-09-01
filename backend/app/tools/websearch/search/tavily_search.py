import json
from pathlib import Path
from typing import Any, Dict

import requests

from app.tools.websearch.http_errors import raise_provider_http_error
_ISO_COUNTRIES = json.loads((Path(__file__).resolve().parent.parent / "iso_3166_1_countries.json").read_text())
ISO_TO_COUNTRY = {code.lower(): name.lower() for code, name in _ISO_COUNTRIES.items()}


def tavily_search_urls(api_key: str, query: str, country: str = "us", fallback_country: str = "us") -> Dict[str, Any]:
    payload = {
        "query": query,
        "max_results": 10,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    resolved_country = ISO_TO_COUNTRY.get(str(country or "").lower()) or ISO_TO_COUNTRY.get(str(fallback_country or "").lower())
    if resolved_country:
        payload["country"] = resolved_country

    try:
        response = requests.post("https://api.tavily.com/search", json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Tavily", operation="search")
    except requests.RequestException as exc:
        raise Exception(str(exc)) from exc
    except ValueError as exc:
        raise Exception(f"Invalid Tavily JSON response: {exc}") from exc

    if "results" not in data:
        raise Exception("No results in Tavily response")

    results_to_return = []
    for item in data["results"]:
        results_to_return.append({
            "url": item.get("url"),
            "title": item.get("title"),
            "preview": item.get("content"),
        })
    return {
        "result": results_to_return,
        "metadata": {
            "provider_search": "tavily",
        },
    }
