import logging
from fastapi import HTTPException

from app.network.outbound_http import outbound_policy_web_request
from app.network.policy import OutboundRequestBlockedError

logger = logging.getLogger(__name__)


def searxng_search_images(
    base_url: str,
    query: str,
    *,
    db,
    num_results: int = 5,
):
    """Search SearXNG through the outbound-policy-aware HTTP transport."""

    empty_response = {
        "result": [],
        "metadata": {
            "provider_images": "searxng",
        },
    }

    if not base_url:
        return empty_response

    searxng_url = f"{str(base_url).rstrip('/')}/search"
    params = {
        "q": query,
        "categories": "images",
        "format": "json",
        "engines": "bing_images,google_images",
    }
    headers = {"Accept": "application/json"}

    try:
        response = outbound_policy_web_request(
            db,
            "GET",
            searxng_url,
            feature="SearXNG image search",
            params=params,
            headers=headers,
            timeout=10,
        )
        try:
            response.raise_for_status()
            data = response.json()
        finally:
            response.close()
        if not isinstance(data, dict):
            raise ValueError("SearXNG image search response must be a JSON object.")
    except OutboundRequestBlockedError as exc:
        # Preserve the policy's controlled 403 response for a redirect or peer
        # that crosses the configured outbound boundary.
        raise exc.to_http_exception() from exc
    except Exception as exc:
        logger.warning("SearXNG image search failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="SearXNG image search failed.") from exc

    results = data.get("results", [])
    if not isinstance(results, list):
        return empty_response
    try:
        resolved_num_results = int(num_results or 5)
    except (TypeError, ValueError):
        resolved_num_results = 5
    return {
        "result": results[: max(1, min(resolved_num_results, 10))],
        "metadata": {
            "provider_images": "searxng",
        },
    }
