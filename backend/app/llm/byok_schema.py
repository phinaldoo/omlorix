"""Sanitize administrator provider schemas for the user-owned BYOK editor.

Administrator provider forms and user BYOK forms intentionally share the
provider-specific field definitions. The BYOK editor already owns provider
name, API key, and base URL controls, however, and must never receive server
operations such as background synchronization or automatic model deletion.
This module defines that boundary in one place so a missing flag in an
individual provider schema cannot expose duplicate or administrative fields.
"""

from __future__ import annotations

from typing import Any

from app.llm.provider_url_suggestions import (
    BASE_URL_FIELD_KEY,
    PROVIDER_URL_SUGGESTIONS_METADATA_KEY,
)


BYOK_SCHEMA_METADATA_KEY = "byok"
BYOK_BASE_URL_SUGGESTIONS_KEY = "base_url_suggestions"

# These controls are rendered directly by the BYOK provider editor. Keeping
# their schema equivalents would produce two inputs with different storage
# paths for the same value.
BYOK_SHARED_PROVIDER_FIELD_KEYS = frozenset(
    {
        "name",
        "api_key",
        BASE_URL_FIELD_KEY,
    }
)

# These fields configure server-owned provider lifecycle behavior. They remain
# available in Admin Settings but have no place in a per-user BYOK connection.
BYOK_ADMIN_ONLY_PROVIDER_FIELD_KEYS = frozenset(
    {
        "settings.disable_background_sync",
        "settings.enable_auto_delete_missing_models",
        "settings.enable_notify_model_changes",
    }
)

# A small number of provider settings are administrative only even though their
# keys are not shared across every provider.
BYOK_PROVIDER_SPECIFIC_ADMIN_FIELD_KEYS: dict[str, frozenset[str]] = {
    "openrouter": frozenset(
        {
            "settings.ranking_url",
            "settings.ranking_title",
        }
    ),
}


def _clean_url_suggestions(field: dict[str, Any]) -> list[dict[str, str]]:
    """Return safe name/URL pairs attached to a provider base-URL field."""

    metadata = field.get("metadata")
    if not isinstance(metadata, dict):
        return []
    raw_suggestions = metadata.get(PROVIDER_URL_SUGGESTIONS_METADATA_KEY)
    if not isinstance(raw_suggestions, list):
        return []

    suggestions: list[dict[str, str]] = []
    for entry in raw_suggestions:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if name and url:
            suggestions.append({"name": name, "url": url})
    return suggestions


def sanitize_byok_provider_schema(
    schema_payload: dict[str, Any],
    provider: str | None = None,
) -> dict[str, Any]:
    """Return a provider schema containing only user-owned BYOK settings.

    URL suggestions are moved to BYOK metadata before the duplicate schema
    base-URL field is removed. Empty sections are omitted so the resulting form
    does not display administrative headings with no controls beneath them.
    """

    payload = dict(schema_payload) if isinstance(schema_payload, dict) else {}
    sections = payload.get("sections")
    if not isinstance(sections, list):
        payload["sections"] = []
        return payload

    provider_key = str(provider or "").strip().lower()
    excluded_keys = (
        BYOK_SHARED_PROVIDER_FIELD_KEYS
        | BYOK_ADMIN_ONLY_PROVIDER_FIELD_KEYS
        | BYOK_PROVIDER_SPECIFIC_ADMIN_FIELD_KEYS.get(provider_key, frozenset())
    )
    filtered_sections: list[dict[str, Any]] = []
    base_url_suggestions: list[dict[str, str]] = []

    for section in sections:
        if not isinstance(section, dict):
            continue
        fields = section.get("fields")
        if not isinstance(fields, list):
            continue

        visible_fields: list[dict[str, Any]] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            if key == BASE_URL_FIELD_KEY and not base_url_suggestions:
                base_url_suggestions = _clean_url_suggestions(field)
            if field.get("hide_on_byok") or key in excluded_keys:
                continue
            visible_fields.append(dict(field))

        if visible_fields:
            section_copy = dict(section)
            section_copy["fields"] = visible_fields
            filtered_sections.append(section_copy)

    payload["sections"] = filtered_sections
    if base_url_suggestions:
        byok_metadata = payload.get(BYOK_SCHEMA_METADATA_KEY)
        byok_metadata = dict(byok_metadata) if isinstance(byok_metadata, dict) else {}
        byok_metadata[BYOK_BASE_URL_SUGGESTIONS_KEY] = base_url_suggestions
        payload[BYOK_SCHEMA_METADATA_KEY] = byok_metadata
    return payload
