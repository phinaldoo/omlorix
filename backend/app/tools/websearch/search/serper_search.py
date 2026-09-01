from typing import Dict, Any

import requests

from app.tools.websearch.http_errors import raise_provider_http_error

def serper_search_urls(
    api_key: str,
    query: str,
    language: str | None = None,
    country: str | None = None,
    fallback_language: str | None = "en",
    fallback_country: str | None = "us",
    num_results: int | None = None,
) -> Dict[str, Any]:
    if not api_key:
        raise ValueError("Missing Serper API key.")

    normalized_language = (language.strip().lower() if isinstance(language, str) and language.strip()
                          else (fallback_language.strip().lower() if isinstance(fallback_language, str) and fallback_language.strip() else "en"))
    normalized_country = (country.strip().lower() if isinstance(country, str) and country.strip()
                          else (fallback_country.strip().lower() if isinstance(fallback_country, str) and fallback_country.strip() else "us"))

    payload: Dict[str, Any] = {
        "q": query,
        "hl": normalized_language,
        "gl": normalized_country,
    }

    if num_results:
        try:
            parsed = int(num_results)
            if parsed > 0:
                payload["num"] = min(parsed, 20)
        except (TypeError, ValueError):
            pass

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post("https://google.serper.dev/search", json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Serper", operation="search")
    except requests.RequestException as exc:
        raise RuntimeError(f"Serper search request failed: {exc}") from exc

    organic_results = data.get("organic", [])
    if not isinstance(organic_results, list):
        return {"result": [], "metadata": {"provider_search": "serper"}}
    results_to_return = []
    for item in organic_results: 
        url = item.get("link", "")
        if not url:
            continue
        results_to_return.append({
            "url": url,
            "title": item.get("title", ""),
            "preview": item.get("snippet", ""),
        })
    return {
        "result": results_to_return,
        "metadata": {
            "provider_search": "serper",
        },
    }
