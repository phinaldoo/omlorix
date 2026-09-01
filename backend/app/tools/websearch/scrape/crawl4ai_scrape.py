import json
import logging
import time
from typing import Any, List
import requests
from fastapi import HTTPException
from requests.exceptions import RequestException, Timeout, ConnectionError

from app.tools.websearch.http_errors import raise_provider_http_error



logger = logging.getLogger(__name__)

def crawl4ai_scrape(
    base_url: str,
    urls: List[str],
    retry_count: int = 2,
    view_raw: bool = False,
    api_token: str | None = None,
) -> dict[str, Any]:
    """Scrape URLs through Crawl4AI, optionally authenticating with a token.

    Crawl4AI 0.9 protects its network API with bearer authentication. Keeping
    the token optional preserves deployments where a restricted sidecar adds
    authentication as well as older development servers that do not require it.
    """

    if not urls:
        return {
            "result": [],
            "metadata": {
                "provider_scrape": "crawl4ai",
            },
        }

    if not base_url:
        raise HTTPException(status_code=400, detail="Crawl4AI base URL is not configured.")

    endpoint = f"{base_url.rstrip('/')}/crawl"
    payload = {
        "urls": urls,
        "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {"stream": False, "cache_mode": "bypass"},
        },
    }
    headers = {"Content-Type": "application/json"}
    normalized_api_token = str(api_token or "").strip()
    if normalized_api_token:
        # The static CRAWL4AI_API_TOKEN and server-issued JWTs both use the
        # standard bearer scheme. Never place the credential in the URL or body.
        headers["Authorization"] = f"Bearer {normalized_api_token}"

    last_exc: Exception | None = None

    for attempt in range(retry_count + 1):
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            raise_provider_http_error(exc, provider_name="Crawl4AI", operation="scrape")
        except Timeout as exc:
            last_exc = exc
            logger.exception("Crawl4AI scrape attempt failed")
        except ConnectionError as exc:
            last_exc = exc
            logger.exception("Crawl4AI scrape attempt failed")
        except RequestException as exc:
            last_exc = exc
            logger.exception("Crawl4AI scrape attempt failed")
        except json.JSONDecodeError as exc:
            last_exc = exc
            logger.exception("Crawl4AI scrape attempt failed")
        else:
            if not data.get("success", False):
                error_message = data.get("error") or "Crawl4AI request failed."
                raise HTTPException(status_code=502, detail=error_message)

            api_results = data.get("results") or []
            processed: List[dict[str, Any]] = []
            extras: List[dict[str, Any]] = []

            for item in api_results:
                if not isinstance(item, dict):
                    continue

                item_url = item.get("url") or ""
                item_success = item.get("success", False)

                if not item_success:
                    error_message = item.get("error_message") or "Failed to scrape this website."
                    raise HTTPException(status_code=502, detail=f"Crawl4AI failed for {item_url or 'URL'}: {error_message}")

                if view_raw:
                    content_value = item.get("html") or item.get("cleaned_html") or ""
                    entry = {"url": item_url, "html": content_value}
                else:
                    markdown_payload = item.get("markdown")
                    markdown_value = ""
                    if isinstance(markdown_payload, dict):
                        markdown_value = (
                            markdown_payload.get("raw_markdown")
                            or markdown_payload.get("markdown_with_citations")
                            or ""
                        )
                    elif isinstance(markdown_payload, str):
                        markdown_value = markdown_payload
                    entry = {"url": item_url, "markdown": markdown_value}

                if item_url:
                    processed.append(entry)
                else:
                    extras.append(entry)

            bucketed: dict[str, list[dict[str, Any]]] = {}
            for entry in processed:
                key = entry.get("url") or ""
                bucketed.setdefault(key, []).append(entry)

            ordered_results: List[dict[str, Any]] = []
            for url in urls:
                bucket = bucketed.get(url)
                if bucket:
                    ordered_results.append(bucket.pop(0))
                else:
                    raise HTTPException(status_code=502, detail=f"Crawl4AI returned no result for {url}.")

            ordered_results.extend(extras)

            return {
                "result": ordered_results,
                "metadata": {
                    "provider_scrape": "crawl4ai",
                },
            }

        if attempt < retry_count:
            wait_time = 2 ** attempt
            time.sleep(wait_time)

    suffix = f" ({last_exc})" if last_exc else ""
    raise HTTPException(status_code=502, detail=f"Failed to scrape this website.{suffix}")
