import logging
from typing import Any

import requests

from app.tools.websearch.http_errors import raise_provider_http_error

logger = logging.getLogger(__name__)

def tavily_scrape_urls(api_key: str, urls: list[str], view_raw: bool = False) -> dict[str, Any]:
    try:
        response = requests.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "urls": urls,
                "format": "text" if view_raw else "markdown",
            },
            timeout=20,
        )
        response.raise_for_status()
        response_json = response.json()
        result = []
        for result_item in response_json.get("results", []):
            if "url" not in result_item or "raw_content" not in result_item:
                logger.warning("Tavily scrape result missing expected keys: %s", result_item)
            result.append({
                "url": result_item.get("url"),
                "content": result_item.get("raw_content"),
            })
        return {
            "result": result,
            "metadata": {
                "provider_scrape": "tavily",
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Tavily", operation="scrape")
    except ValueError as exc:
        raise Exception(f"Invalid Tavily JSON response: {exc}") from exc
    except Exception as exc:
        raise Exception(str(exc)) from exc
