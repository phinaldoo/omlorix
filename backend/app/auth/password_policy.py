"""Shared non-negotiable password-policy safety floor."""

from __future__ import annotations

from typing import Any, Mapping


MINIMUM_SECURE_PASSWORD_LENGTH = 10


def effective_minimum_password_length(configured_value: Any) -> int:
    """Return the configured length clamped to Omlorix's security floor.

    The clamp intentionally applies at enforcement/read time as well as in the
    admin schema so an unsafe value already stored by an older deployment can
    never weaken signup, reset, or password-change validation.
    """

    try:
        configured = int(configured_value)
    except (TypeError, ValueError):
        configured = MINIMUM_SECURE_PASSWORD_LENGTH
    return max(MINIMUM_SECURE_PASSWORD_LENGTH, configured)


def normalize_stored_login_general_settings(
    stored_values: Mapping[str, Any],
) -> dict[str, Any]:
    """Clamp a persisted legacy password length without weakening writes.

    This helper is intentionally limited to values read from storage. Admin
    request payloads still pass directly through ``LoginGeneralSettings`` and
    therefore reject attempts to save a value below the security floor.
    """

    normalized = dict(stored_values)
    if "minimum_password_length" in normalized:
        normalized["minimum_password_length"] = effective_minimum_password_length(
            normalized["minimum_password_length"]
        )
    return normalized
