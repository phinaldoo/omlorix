# Docs: https://exa.ai/docs/reference/get-contents
from typing import Any

import requests
from fastapi import HTTPException

from app.tools.websearch.http_errors import raise_provider_http_error

EXA_CONTENTS_URL = "https://api.exa.ai/contents"
REQUEST_TIMEOUT_SECONDS = 20


def exa_scrape_urls(
    api_key: str,
    urls: list[str],
) -> dict[str, Any]:
    """Retrieve extracted text for explicit URLs through Exa's Contents API."""

    try:
        # Unlike Exa's Search endpoint, the Contents endpoint expects text
        # extraction options at the top level of the request body.
        payload = {
            "urls": urls,
            "text": True,
        }
        response = requests.post(
            EXA_CONTENTS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError("Exa scrape response did not include a 'results' list.")
        results_to_return = []
        for result in results:
            results_to_return.append(
                {
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "content": result.get("text", ""),
                }
            )
        cost = data.get("costDollars", {}).get("total")
        if not results_to_return:
            raise Exception("Exa returned no results.")
        return {
            "result": results_to_return,
            "metadata": {
                "provider_scrape": "exa",
                "cost": cost,
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Exa", operation="scrape")
    except HTTPException:
        raise
    except Exception as exc:
        raise Exception(f"Exa scrape failed: {exc}") from exc
