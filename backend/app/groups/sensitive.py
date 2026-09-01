from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Dict, Tuple

from fastapi import HTTPException, status

from app.utils.encryption import encrypt_value, decrypt_value
from app.utils.helpers import _mask_secret_preview

_SENSITIVE_PREFIX = "enc:v1:"
_SENSITIVE_KEYS: set[tuple[str, str]] = {
    ("leaderboard", "artificial_analysis_api_key"),
}
SENSITIVE_RESPONSE_MASK = "********"


def _is_sensitive_key(page_name: str, key_name: str) -> bool:
    """Check whether a setting key is marked as sensitive."""
    return (page_name, key_name) in _SENSITIVE_KEYS


def resolve_sensitive_setting_update(
    page_name: str,
    key_name: str,
    incoming_value: Any,
    current_value: Any,
) -> Any:
    """Preserve a secret only when its safe UI marker is submitted unchanged.

    Redacted schema fields use either a three-character preview or a fixed
    eight-asterisk mask as their existing-value marker. An empty string is not
    a marker: it remains available as an explicit request to clear the secret.
    """
    if not _is_sensitive_key(page_name, key_name):
        return incoming_value
    if not isinstance(incoming_value, str) or not isinstance(current_value, str):
        return incoming_value
    if not current_value:
        return incoming_value
    if incoming_value == SENSITIVE_RESPONSE_MASK:
        return current_value

    current_preview = _mask_secret_preview(current_value)
    if current_preview and incoming_value == current_preview:
        return current_value
    return incoming_value


def encrypt_sensitive_value(
    page_name: str,
    key_name: str,
    value: Any,
    *,
    treat_value_as_plaintext: bool = True,
) -> Any:
    """Return storage-safe value for potentially sensitive fields."""
    if not _is_sensitive_key(page_name, key_name):
        return value

    if value is None:
        return ""

    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Value for '{page_name}.{key_name}' must be a string.",
        )

    normalized = value.strip()
    if not normalized:
        return ""

    if (
        not treat_value_as_plaintext
        and normalized.startswith(_SENSITIVE_PREFIX)
    ):
        ciphertext = normalized[len(_SENSITIVE_PREFIX) :]
        decrypted = decrypt_value(ciphertext)
        if decrypted is not None:
            return normalized
        # Fall through and re-encrypt if the ciphertext is invalid/corrupted.

    encrypted = encrypt_value(normalized)
    return f"{_SENSITIVE_PREFIX}{encrypted}"


def decrypt_sensitive_value(page_name: str, key_name: str, value: Any) -> Any:
    """Decrypt a stored sensitive value back to plaintext."""
    if not _is_sensitive_key(page_name, key_name):
        return value

    if value is None:
        return ""

    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored value for '{page_name}.{key_name}' is invalid.",
        )

    normalized = value.strip()
    if not normalized:
        return ""

    if not normalized.startswith(_SENSITIVE_PREFIX):
        return normalized

    ciphertext = normalized[len(_SENSITIVE_PREFIX) :]
    decrypted = decrypt_value(ciphertext)
    if decrypted is None:
        raise RuntimeError(
            "Unable to decrypt sensitive group setting. "
            "Ensure ENCRYPTION_KEY matches the key used for encryption."
        )
    return decrypted


def ensure_sensitive_settings_encrypted(
    settings: Dict[str, Any] | None
) -> Tuple[bool, Dict[str, Any]]:
    """Return a new settings dict with all sensitive fields encrypted."""
    if not isinstance(settings, dict):
        return False, {}

    sanitized = deepcopy(settings)
    changed = False

    for page_name, page_values in sanitized.items():
        if not isinstance(page_values, dict):
            continue

        for key_name, raw_value in list(page_values.items()):
            encrypted_value = encrypt_sensitive_value(
                page_name,
                key_name,
                raw_value,
                treat_value_as_plaintext=False,
            )
            if encrypted_value != raw_value:
                page_values[key_name] = encrypted_value
                changed = True

    return changed, sanitized


def decrypt_sensitive_page(
    page_name: str,
    page_values: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Decrypt all sensitive values within a single settings page."""
    if not isinstance(page_values, dict):
        return {}

    decrypted = deepcopy(page_values)
    for key_name, value in decrypted.items():
        decrypted[key_name] = decrypt_sensitive_value(page_name, key_name, value)
    return decrypted


def decrypt_sensitive_settings(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Decrypt all sensitive values across all settings pages."""
    if not isinstance(settings, dict):
        return {}

    decrypted = deepcopy(settings)
    for page_name, page_values in decrypted.items():
        if not isinstance(page_values, dict):
            continue
        decrypted[page_name] = decrypt_sensitive_page(page_name, page_values)
    return decrypted


def mask_sensitive_settings_for_response(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return settings with sensitive values replaced by a non-secret preview."""
    if not isinstance(settings, dict):
        return {}

    masked = deepcopy(settings)
    for page_name, page_values in masked.items():
        if not isinstance(page_values, dict):
            continue
        for key_name, value in list(page_values.items()):
            if not _is_sensitive_key(page_name, key_name):
                continue
            page_values[key_name] = _mask_secret_preview(value) or (
                SENSITIVE_RESPONSE_MASK if value else ""
            )
    return masked


def filter_settings_for_response(
    settings: Dict[str, Any] | None,
    allowed_setting_paths: Iterable[str],
) -> Dict[str, Any]:
    """Return only explicitly allowed group setting paths, with secrets masked."""
    if not isinstance(settings, dict):
        return {}

    filtered: Dict[str, Any] = {}
    for setting_path in allowed_setting_paths:
        if not isinstance(setting_path, str):
            continue
        page_name, separator, key_name = setting_path.partition(".")
        if not separator or not page_name or not key_name:
            continue
        page_values = settings.get(page_name)
        if not isinstance(page_values, dict) or key_name not in page_values:
            continue
        filtered.setdefault(page_name, {})[key_name] = page_values[key_name]

    return mask_sensitive_settings_for_response(filtered)
