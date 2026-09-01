from datetime import datetime, timezone
from typing import Any, Optional


def datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert a datetime to ISO 8601 string format.
    
    If the datetime is naive (no timezone info), it will be treated as UTC.
    Returns None if the input is None.
    """
    if dt is None:
        return None
    normalized = dt
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse a datetime from various formats.
    
    Accepts datetime objects or ISO formatted strings.
    If the datetime is naive (no timezone info), it will be treated as UTC.
    Returns None if the input is None or an empty string.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        dt = datetime.fromisoformat(normalized)
    else:
        raise ValueError("Datetime values must be ISO formatted strings or datetime.datetime objects")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt





def _mask_api_key_preview(value: Any, visible_chars: int = 6) -> str | None:
    """Mask an API key for preview display."""
    if not isinstance(value, str) or not value:
        return None
    # Clamp visible_chars to ensure at least one character is masked
    visible_chars = min(visible_chars, max(0, len(value) - 1))
    preview = value[:visible_chars]
    return f"{preview}..."


def _mask_secret_preview(value: Any, visible_chars: int = 3, min_length_for_prefix: int = 7) -> str | None:
    """Mask a saved secret without exposing short values."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < min_length_for_prefix:
        return None
    visible_chars = max(0, min(visible_chars, 3, len(text) - 1))
    if visible_chars == 0:
        return None
    preview = text[:visible_chars]
    return f"{preview}..."


def _is_masked_api_key(value: str | None, actual_value: str | None) -> bool:
    """Check if the provided value is a masked version of the actual API key."""
    if not isinstance(value, str) or not isinstance(actual_value, str):
        return False
    if not value.endswith("..."):
        return False
    # Ensure the prefix (part before "...") is non-empty to reject "..." as a valid mask
    if len(value[:-3]) == 0:
        return False
    return actual_value.startswith(value[:-3])



def _set_schema_field_placeholder(schema, field_key: str, placeholder: str | None) -> bool:
    if not schema or not getattr(schema, "sections", None):
        return False
    for section in getattr(schema, "sections", []) or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.placeholder = placeholder
                return True
    return False


def _set_schema_field_required(schema, field_key: str, required: bool | None) -> bool:
    if not schema or not getattr(schema, "sections", None):
        return False
    for section in getattr(schema, "sections", []) or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.required = required
                return True
    return False
