from __future__ import annotations

import json
from copy import deepcopy
from datetime import time as dt_time
from typing import Any, Dict, List
from zoneinfo import ZoneInfo


ACCESS_WINDOW_MODES = {"allowlist", "blocklist"}
ACCESS_WINDOW_SETTING_KEYS = {
    "enabled",
    "timezone",
    "mode",
    "rules",
    "show_next_available",
    "blocked_message",
}


def _format_time(value: dt_time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_access_window_time(value: Any) -> dt_time | None:
    """Parse an access-window HH:MM value into a time object."""
    if not isinstance(value, str):
        return None

    parts = value.strip().split(":")
    if len(parts) < 2:
        return None

    try:
        parsed = dt_time(int(parts[0]), int(parts[1]))
    except (TypeError, ValueError):
        return None

    return parsed


def normalize_access_window_timezone(value: Any, *, field_path: str) -> str:
    """Validate and normalize a timezone string."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string")

    normalized = value.strip()
    if not normalized:
        return ""

    try:
        ZoneInfo(normalized)
    except Exception as exc:
        raise ValueError(f"{field_path} must be a valid IANA timezone") from exc

    return normalized


def normalize_access_window_time(value: Any, *, field_path: str) -> str:
    """Validate and normalize an HH:MM time string."""
    parsed = parse_access_window_time(value)
    if parsed is None:
        raise ValueError(f"{field_path} must use HH:MM format")
    return _format_time(parsed)


def normalize_access_window_days(value: Any, *, field_path: str) -> List[int]:
    """Validate and normalize a weekday list (0=Mon ... 6=Sun)."""
    if value in (None, ""):
        return []

    days_source = value if isinstance(value, list) else [value]
    normalized: List[int] = []

    for index, entry in enumerate(days_source):
        if isinstance(entry, bool):
            raise ValueError(f"{field_path}[{index}] must be an integer between 0 and 6")

        try:
            day_int = int(entry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_path}[{index}] must be an integer between 0 and 6") from exc

        if not 0 <= day_int <= 6:
            raise ValueError(f"{field_path}[{index}] must be between 0 and 6")
        if day_int not in normalized:
            normalized.append(day_int)

    normalized.sort()
    return normalized


def _decode_rule_json(value: str, *, field_path: str) -> Any:
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ValueError(f"{field_path} must contain valid JSON rule objects") from exc


def normalize_access_window_rule(
    rule: Any,
    *,
    field_path: str,
    allow_json_strings: bool,
) -> Dict[str, Any]:
    """Validate and normalize a single access-window rule."""
    current = rule
    if allow_json_strings and isinstance(current, str):
        current = _decode_rule_json(current, field_path=field_path)

    if not isinstance(current, dict):
        raise ValueError(f"{field_path} must be an object")

    normalized = deepcopy(current)
    normalized["start"] = normalize_access_window_time(
        normalized.get("start"),
        field_path=f"{field_path}.start",
    )
    normalized["end"] = normalize_access_window_time(
        normalized.get("end"),
        field_path=f"{field_path}.end",
    )
    normalized["days"] = normalize_access_window_days(
        normalized.get("days", []),
        field_path=f"{field_path}.days",
    )

    label = normalized.get("label")
    if label is not None and not isinstance(label, str):
        raise ValueError(f"{field_path}.label must be a string")

    return normalized


def normalize_access_window_rules(
    value: Any,
    *,
    field_path: str,
    allow_json_strings: bool = False,
) -> List[Dict[str, Any]]:
    """Validate and normalize an access-window rules collection."""
    if value in (None, ""):
        return []

    rules_source = value
    if allow_json_strings and isinstance(rules_source, str):
        rules_source = _decode_rule_json(rules_source, field_path=field_path)

    if allow_json_strings and isinstance(rules_source, dict):
        rules_source = [rules_source]

    if not isinstance(rules_source, list):
        raise ValueError(f"{field_path} must be a list of rule objects")

    normalized: List[Dict[str, Any]] = []
    for index, rule in enumerate(rules_source):
        normalized.append(
            normalize_access_window_rule(
                rule,
                field_path=f"{field_path}[{index}]",
                allow_json_strings=allow_json_strings,
            )
        )

    return normalized


def normalize_access_window_settings(
    settings: Dict[str, Any],
    *,
    field_prefix: str,
) -> Dict[str, Any]:
    """Validate the access-window keys present in a settings payload."""
    normalized = {
        key: deepcopy(value)
        for key, value in settings.items()
        if key in ACCESS_WINDOW_SETTING_KEYS
    }

    if "timezone" in normalized:
        normalized["timezone"] = normalize_access_window_timezone(
            normalized.get("timezone"),
            field_path=f"{field_prefix}.timezone",
        )
    if "rules" in normalized:
        normalized["rules"] = normalize_access_window_rules(
            normalized.get("rules"),
            field_path=f"{field_prefix}.rules",
        )
    return normalized


def validate_access_window_settings(
    settings: Dict[str, Any],
    *,
    field_prefix: str,
) -> Dict[str, Any]:
    """Validate a complete access-window settings page for runtime or writes."""
    if not isinstance(settings, dict):
        raise ValueError(f"{field_prefix} must be an object")

    normalized = normalize_access_window_settings(
        settings,
        field_prefix=field_prefix,
    )

    mode = normalized.get("mode", "allowlist")
    if mode not in ACCESS_WINDOW_MODES:
        raise ValueError(f"{field_prefix}.mode must be 'allowlist' or 'blocklist'")
    normalized["mode"] = mode

    enabled = bool(normalized.get("enabled", False))
    normalized["enabled"] = enabled

    rules = normalized.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError(f"{field_prefix}.rules must be a list of rule objects")

    if not enabled:
        return normalized

    timezone = normalize_access_window_timezone(
        normalized.get("timezone", "UTC"),
        field_path=f"{field_prefix}.timezone",
    )
    normalized["timezone"] = timezone or "UTC"

    if mode == "allowlist" and not rules:
        raise ValueError(
            f"{field_prefix}.rules: at least one valid rule is required when access windows are enabled in allowlist mode"
        )

    if mode == "blocklist" and not rules:
        raise ValueError(
            f"{field_prefix}.rules: at least one valid rule is required when access windows are enabled in blocklist mode"
        )

    return normalized
