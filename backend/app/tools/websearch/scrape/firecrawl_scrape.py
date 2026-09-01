# Docs: https://docs.firecrawl.dev/api-reference/endpoint/batch-scrape
import time
from typing import Any

import requests
from fastapi import HTTPException

from app.tools.websearch.firecrawl_proxy import (
    FIRECRAWL_HOSTED_BASE_URL,
    normalize_firecrawl_base_url,
    normalize_firecrawl_proxy_mode,
)
from app.tools.websearch.http_errors import raise_provider_http_error
from app.tools.websearch.schemas import ISO_COUNTRIES


def get_location_config(country: object, fallback_country: object) -> dict[str, str]:
    """Build Firecrawl's location payload from a supported country code."""

    candidate = str(country or fallback_country or "US").strip().upper()
    country_code = candidate if len(candidate) == 2 and candidate.isalpha() else "US"
    if country_code not in ISO_COUNTRIES:
        country_code = "US"
    return {"country": country_code}


def firecrawl_scrape_urls(
    api_key: str,
    urls: list[str],
    country: str = "us",
    fallback_country: str = "us",
    proxy: str = "auto",
    view_raw: bool = False,
    base_url: str | None = FIRECRAWL_HOSTED_BASE_URL,
    enterprise_option: str | None = None,
) -> dict[str, Any]:
    """Scrape URLs through Firecrawl's asynchronous v2 batch endpoint.

    The runtime normalization is intentionally repeated here, even though the
    settings model performs the same conversion. It protects requests made
    from persisted rows or direct internal callers that bypass schema
    validation.
    """

    try:
        base_url = normalize_firecrawl_base_url(base_url)
        proxy = normalize_firecrawl_proxy_mode(proxy)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "urls": urls,
            "location": get_location_config(country, fallback_country),
            "proxy": proxy,
        }
        if view_raw:
            payload["formats"] = ["rawHtml"]
        else:
            payload["formats"] = ["markdown"]

        if enterprise_option and enterprise_option != "none":
            payload["zeroDataRetention"] = True

        # Create batch scrape job
        r = requests.post(
            f"{base_url}/v2/batch/scrape",
            headers=headers,
            json=payload,
            timeout=20,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            raise_provider_http_error(exc, provider_name="Firecrawl", operation="scrape")
        job = r.json()
        job_id = job["id"]

        all_data = []
        meta = {}

        # Poll for results
        start_time = time.time()
        max_polling_time = 180
        while True:
            if time.time() - start_time > max_polling_time:
                raise RuntimeError(f"Batch scrape timed out after {max_polling_time} seconds")

            r = requests.get(
                f"{base_url}/v2/batch/scrape/{job_id}",
                headers=headers,
                timeout=20,
            )
            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                raise_provider_http_error(exc, provider_name="Firecrawl", operation="scrape")
            result = r.json()

            if result.get("status") == "completed":
                all_data.extend(result.get("data", []))
                meta["creditsUsed"] = result.get("creditsUsed", 0)
                meta["id"] = job_id
                break
            if result.get("status") == "failed":
                raise RuntimeError(f"Batch scrape failed: {result}")

            time.sleep(1)
        results = []
        for item in all_data:
            entry = {
                "url": item.get("metadata", {}).get("url") or "",
                "title": item.get("metadata", {}).get("title") or "",
            }
            if view_raw:
                entry["content"] = item.get("rawHtml") or ""
            else:
                entry["markdown"] = item.get("markdown") or ""
            results.append(entry)
        if not results:
            raise Exception("Firecrawl returned no results.")
        return {
            "result": results,
            "metadata": {
                "provider_scrape": "firecrawl",
                "creditsUsed": meta.get("creditsUsed", 0),
                "id": meta.get("id"),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise Exception(f"Firecrawl scrape failed {exc}") from exc
