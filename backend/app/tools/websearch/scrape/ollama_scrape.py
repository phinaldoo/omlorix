import logging
from typing import Any
from fastapi import HTTPException
from ollama import Client

from app.tools.websearch.robots import filter_entries_by_robots
from app.tools.websearch.schemas import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)


def ollama_scrape_urls(
    api_key: str,
    urls: list[str],
    *,
    respect_robots_txt: bool = True,
) -> dict[str, Any]:
    try:
        client = Client(
            host="https://ollama.com",
            headers={"Authorization": "Bearer " + api_key},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or "Ollama scrape client initialization failed") from exc

    results: list[dict[str, Any]] = []
    for url in urls:
        try:
            response = client.web_fetch(url)
            results.append({
                "url": url,
                "title": response.title,
                "content": response.content
            })
        except Exception as exc:
            logger.exception("Ollama scrape failed for url '%s': %s", url, exc)
            raise HTTPException(status_code=502, detail=f"Ollama scrape failed for {url}: {exc}") from exc

    if respect_robots_txt and results:
        def _extract_url(entry: dict[str, Any]) -> str | None:
            if isinstance(entry, dict):
                url_value = entry.get("url")
                if isinstance(url_value, str):
                    return url_value
            return None

        allowed, _blocked = filter_entries_by_robots(
            results,
            url_getter=_extract_url,
            user_agent=DEFAULT_USER_AGENT,
        )
        results = allowed

    return {
        "result": results,
        "metadata": {
            "provider_scrape": "ollama",
        },
    }
