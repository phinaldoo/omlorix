from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.groups.access_windows_validation import (
    normalize_access_window_settings,
    validate_access_window_settings,
)
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.admin.groups.schemas import (
    FIELD_SCHEMA_BY_KEY,
    _validate_group_default_value,
    _validate_group_field_value,
)
from app.groups.sensitive import ensure_sensitive_settings_encrypted


def _validate_known_setting_value(page_name: str, key_name: str, value: Any) -> Any:
    dotted_key = f"settings.{page_name}.{key_name}"
    field_schema = FIELD_SCHEMA_BY_KEY.get(dotted_key)
    try:
        if field_schema:
            validated_value = _validate_group_field_value(field_schema, value)
        else:
            validated_value = _validate_group_default_value(page_name, key_name, value)

        if page_name == "access_windows":
            validated_page = normalize_access_window_settings(
                {key_name: validated_value},
                field_prefix="settings.access_windows",
            )
            return validated_page[key_name]

        return validated_value
    except ValueError as exc:
        raise ValueError(f"settings.{page_name}.{key_name}: {exc}") from exc


def normalize_group_settings(
    settings: Dict[str, Any] | None,
    *,
    raise_on_invalid: bool = True,
) -> tuple[bool, Dict[str, Any]]:
    """Return one complete, validated settings snapshot for a group.

    Group settings intentionally do not inherit through the organizational
    hierarchy. Missing values are therefore filled from the application
    defaults so every persisted group remains self-contained when it is moved,
    linked to a parent, or unlinked from one.
    """
    if not isinstance(settings, dict):
        if raise_on_invalid:
            raise ValueError("settings must be an object")
        settings = {}

    changed = False
    normalized = deepcopy(settings)

    # Start with a complete independent snapshot. Valid incoming values replace
    # these defaults below; unknown or malformed values cannot leak into the
    # runtime policy.
    result: Dict[str, Any] = deepcopy(DEFAULT_GROUP_SETTINGS)
    for page_name, page_values in normalized.items():
        if page_name not in DEFAULT_GROUP_SETTINGS:
            changed = True
            continue
        if not isinstance(page_values, dict):
            if raise_on_invalid:
                raise ValueError(f"settings.{page_name} must be an object")
            changed = True
            continue

        allowed_keys = DEFAULT_GROUP_SETTINGS[page_name]
        for key_name, value in page_values.items():
            if key_name not in allowed_keys:
                changed = True
                continue
            # Earlier versions stored this toggle as the strings ``"true"``
            # and ``"false"``. Only the repair-oriented read path accepts
            # those legacy values; new writes must provide a real JSON boolean.
            if (
                not raise_on_invalid
                and page_name == "chat"
                and key_name == "show_chat_box_warning"
                and isinstance(value, str)
                and value.lower() in {"true", "false"}
            ):
                value = value.lower() == "true"
                changed = True
            try:
                validated_value = _validate_known_setting_value(page_name, key_name, value)
            except ValueError:
                if raise_on_invalid:
                    raise
                changed = True
                continue
            if validated_value != value:
                changed = True
            result[page_name][key_name] = deepcopy(validated_value)

    if result != normalized:
        changed = True

    try:
        validated = validate_group_settings(result)
    except ValueError:
        if raise_on_invalid:
            raise
        # A malformed legacy combination must not make every read fail. Keep
        # individually valid values, but disable the access-window policy so
        # the repaired complete snapshot is internally consistent.
        result["access_windows"]["enabled"] = False
        changed = True
        validated = validate_group_settings(result)
    return changed, validated


def validate_group_settings(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Validate a complete group settings snapshot before enforcement or storage."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")

    normalized = deepcopy(settings)

    access_windows_settings = normalized.get("access_windows")
    if isinstance(access_windows_settings, dict):
        normalized["access_windows"] = validate_access_window_settings(
            access_windows_settings,
            field_prefix="settings.access_windows",
        )

    return normalized


def sanitize_group_settings_for_storage(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize and encrypt one complete group settings snapshot for storage."""
    _, normalized_settings = normalize_group_settings(settings if settings is not None else {})
    _, encrypted_settings = ensure_sensitive_settings_encrypted(normalized_settings)
    return encrypted_settings
