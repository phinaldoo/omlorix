from __future__ import annotations

from typing import Any

from app.utils.schemas import Sections


PROVIDER_URL_SUGGESTIONS: dict[str, list[dict[str, str]]] = {
    "crawl4ai": [
        {"name": "Local Crawl4AI", "url": "http://localhost:11235"},
        {"name": "Local Crawl4AI (Docker Host)", "url": "http://host.docker.internal:11235"},
    ],
    "searxng": [
        {"name": "Local SearXNG", "url": "http://localhost:8080"},
        {"name": "Local SearXNG (Docker Host)", "url": "http://host.docker.internal:8080"},
    ],
}

PROVIDER_URL_SUGGESTIONS_METADATA_KEY = "provider_url_suggestions"
BASE_URL_FIELD_KEY = "base_url"


def get_provider_url_suggestions(provider: str | None) -> list[dict[str, str]]:
    key = str(provider or "").strip().lower()
    suggestions = PROVIDER_URL_SUGGESTIONS.get(key, [])
    return [dict(item) for item in suggestions if isinstance(item, dict)]


def attach_provider_url_suggestions(schema: Sections | None, provider: str | None) -> Sections | None:
    suggestions = get_provider_url_suggestions(provider)
    if not schema or not getattr(schema, "sections", None) or not suggestions:
        return schema

    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) != BASE_URL_FIELD_KEY:
                continue
            metadata: dict[str, Any] = dict(getattr(field, "metadata", None) or {})
            metadata[PROVIDER_URL_SUGGESTIONS_METADATA_KEY] = suggestions
            field.metadata = metadata
            return schema

    return schema
