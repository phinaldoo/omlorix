import requests
from typing import Dict, Any

from app.tools.websearch.http_errors import raise_provider_http_error

def custom_search_urls(
    base_url: str,
    query: str,
    *,
    country: str | None = None,
    fallback_country: str = "us",
    num_results: int = 10
) -> Dict[str, Any]:
    if not country:
        country = fallback_country

    payload = {
        "query": query,
        "country": country,
        "num_results": num_results
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            base_url,
            json=payload,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        items = data if isinstance(data, list) else data.get("results", [])
        result_to_return = []
        for item in items:
            result_to_return.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "preview": item.get("content", ""),
            })
        return {
            "result": result_to_return,
            "metadata": {
                "provider_search": "custom",
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Custom search provider", operation="search")
    except requests.exceptions.Timeout:
        raise Exception("Request timeout: custom search provider did not respond in time") from None
    except requests.exceptions.RequestException as exc:
        raise Exception(f"Request failed: {exc}") from exc
    except ValueError as exc:
        raise Exception(f"Invalid JSON response: {exc}") from exc
