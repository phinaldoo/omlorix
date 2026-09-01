# Docs: https://you.com/docs/api-reference/contents
from typing import Any

import requests

from app.network.policy import DEFAULT_WEBSEARCH_PROVIDER_TARGETS
from app.tools.websearch.http_errors import raise_provider_http_error


def you_scrape_urls(
    api_key: str,
    urls: list[str],
    *,
    view_raw: bool = False,
) -> dict[str, Any]:
    payload = {
        "urls": urls,
        "formats": ["html"] if view_raw else ["markdown"],
    }
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{DEFAULT_WEBSEARCH_PROVIDER_TARGETS['you']}/v1/contents",
            json=payload,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        result_to_return = []
        for item in data:
            result_to_return.append({
                "url": item.get("url"),
                "title": item.get("title"),
                "content": item.get("html" if view_raw else "markdown"),
            })
        return {
            "result": result_to_return,
            "metadata": {
                "provider_scrape": "you",
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="You.com", operation="scrape")
    except requests.RequestException as exc:
        raise Exception(str(exc)) from exc
    except ValueError as exc:
        raise Exception(f"Invalid You.com JSON response: {exc}") from exc
