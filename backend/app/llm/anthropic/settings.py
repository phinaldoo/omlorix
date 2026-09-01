"""Anthropic-specific model-setting cleanup."""

from typing import Any


ANTHROPIC_DEPRECATED_REQUEST_SETTINGS = frozenset(
    {"output_format", "temperature", "top_k", "top_p"}
)


def remove_deprecated_anthropic_request_settings(settings: Any) -> Any:
    """Return Anthropic settings without deprecated request parameters.

    Model records store provider settings directly, while user presets wrap
    them in a ``settings`` object. Cleaning both shapes keeps persistence,
    imports, exports, and request dispatch aligned with the current API.
    """
    if not isinstance(settings, dict):
        return settings

    cleaned = {
        key: value
        for key, value in settings.items()
        if key not in ANTHROPIC_DEPRECATED_REQUEST_SETTINGS
    }
    nested = cleaned.get("settings")
    if isinstance(nested, dict):
        cleaned["settings"] = {
            key: value
            for key, value in nested.items()
            if key not in ANTHROPIC_DEPRECATED_REQUEST_SETTINGS
        }
    return cleaned
