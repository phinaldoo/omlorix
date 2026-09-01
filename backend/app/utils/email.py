import hashlib
from typing import Any


def normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def build_email_reference_token(value: Any) -> str | None:
    normalized = normalize_email(value)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
