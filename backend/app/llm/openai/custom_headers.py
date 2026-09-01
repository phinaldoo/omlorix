from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

_HEADER_NAME_ALLOWED_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)

CUSTOM_HEADER_SECRET_PLACEHOLDER = "<redacted>"

_DENIED_CUSTOM_HEADER_NAMES = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "forwarded",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "via",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-prefix",
        "x-forwarded-proto",
        "x-forwarded-protocol",
        "x-real-ip",
    }
)
_DENIED_CUSTOM_HEADER_PREFIXES = ("proxy-", "x-forwarded-")


def _is_valid_header_name(value: str) -> bool:
    """Check if header name is valid."""
    return bool(value) and all(char in _HEADER_NAME_ALLOWED_CHARS for char in value)


def _coerce_custom_header_entries(value: Any) -> list[str]:
    """Coerce custom header entries to strings."""
    if value is None:
        return []

    if isinstance(value, str):
        return [line.strip() for line in value.splitlines()]
    if isinstance(value, Mapping):
        return [f"{key}: {item}" for key, item in value.items()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if item is not None]
    return [str(value).strip()]


def is_denied_custom_header_name(value: str) -> bool:
    """Return whether a custom header name is unsafe to configure."""
    normalized = value.strip().lower()
    return normalized in _DENIED_CUSTOM_HEADER_NAMES or any(
        normalized.startswith(prefix) for prefix in _DENIED_CUSTOM_HEADER_PREFIXES
    )


def normalize_custom_header_entries(value: Any) -> list[str]:
    """Normalize custom header entries."""
    entries = _coerce_custom_header_entries(value)

    normalized: list[str] = []
    for entry in entries:
        text = str(entry or "").strip()
        if not text:
            continue
        if "\r" in text or "\n" in text:
            raise ValueError("Custom header entries must be a single line formatted as 'Header-Name: value'.")
        if ":" not in text:
            raise ValueError("Custom header entries must be formatted as 'Header-Name: value'.")

        name, raw_header_value = text.split(":", 1)
        header_name = name.strip()
        header_value = raw_header_value.strip()

        if not header_name or not _is_valid_header_name(header_name):
            raise ValueError(f"Invalid custom header name '{header_name or text}'.")
        if is_denied_custom_header_name(header_name):
            raise ValueError(f"Custom header '{header_name}' is not allowed.")
        if not header_value:
            raise ValueError(f"Custom header '{header_name}' is missing a value.")

        normalized.append(f"{header_name}: {header_value}")

    return normalized


def custom_headers_to_dict(
    value: Any,
    *,
    max_headers: int | None = None,
) -> dict[str, str]:
    """Convert custom headers to dict."""
    entries = normalize_custom_header_entries(value)
    if max_headers is not None and len(entries) > max_headers:
        raise ValueError(f"At most {max_headers} custom headers are supported.")

    headers: dict[str, str] = {}
    for entry in entries:
        header_name, header_value = entry.split(":", 1)
        headers[header_name.strip()] = header_value.strip()
    return headers


def redact_custom_header_entries(value: Any) -> list[str]:
    """Return custom header names with all values redacted for export or audit payloads."""
    redacted: list[str] = []
    for entry in _coerce_custom_header_entries(value):
        text = str(entry or "").strip()
        if not text:
            continue
        if "\r" in text:
            text = text.split("\r", 1)[0].strip()
        if "\n" in text:
            text = text.split("\n", 1)[0].strip()
        if ":" not in text:
            redacted.append(CUSTOM_HEADER_SECRET_PLACEHOLDER)
            continue

        name, _ = text.split(":", 1)
        header_name = name.strip()
        if not header_name or not _is_valid_header_name(header_name):
            redacted.append(CUSTOM_HEADER_SECRET_PLACEHOLDER)
            continue
        redacted.append(f"{header_name}: {CUSTOM_HEADER_SECRET_PLACEHOLDER}")
    return redacted


def redact_custom_headers_in_settings(settings: Any) -> dict[str, Any]:
    """Redact custom header secrets from provider settings."""
    if not isinstance(settings, Mapping):
        return {}

    sanitized = deepcopy(dict(settings))
    if "custom_headers" not in sanitized:
        return sanitized

    redacted_headers = redact_custom_header_entries(sanitized.pop("custom_headers"))
    if redacted_headers:
        sanitized["custom_headers_redacted"] = redacted_headers
    else:
        sanitized["custom_headers"] = []
    return sanitized


def redact_custom_headers_for_display_settings(settings: Any) -> dict[str, Any]:
    """Redact custom header values while keeping the editable settings key."""
    if not isinstance(settings, Mapping):
        return {}

    sanitized = deepcopy(dict(settings))
    if "custom_headers" in sanitized:
        sanitized["custom_headers"] = redact_custom_header_entries(sanitized.get("custom_headers"))
    return sanitized


def preserve_redacted_custom_headers_in_settings(existing_settings: Any, provided_settings: Any) -> dict[str, Any]:
    """Replace redacted custom header placeholders in an update payload with stored values."""
    if not isinstance(provided_settings, Mapping):
        return {}

    sanitized = deepcopy(dict(provided_settings))
    try:
        existing_headers = custom_headers_to_dict(
            existing_settings.get("custom_headers") if isinstance(existing_settings, Mapping) else []
        )
    except ValueError:
        existing_headers = {}
    if "custom_headers" not in sanitized:
        if existing_headers:
            sanitized["custom_headers"] = [f"{name}: {value}" for name, value in existing_headers.items()]
        return sanitized

    merged_headers: list[str] = []
    existing_by_name = {name.lower(): (name, value) for name, value in existing_headers.items()}
    for entry in _coerce_custom_header_entries(sanitized.get("custom_headers")):
        text = str(entry or "").strip()
        if not text:
            continue
        if ":" not in text:
            merged_headers.append(text)
            continue

        name, raw_value = text.split(":", 1)
        header_name = name.strip()
        header_value = raw_value.strip()
        if header_value == CUSTOM_HEADER_SECRET_PLACEHOLDER:
            existing = existing_by_name.get(header_name.lower())
            if existing is None:
                continue
            existing_name, existing_value = existing
            merged_headers.append(f"{existing_name}: {existing_value}")
            continue
        merged_headers.append(text)

    sanitized["custom_headers"] = merged_headers
    return sanitized
