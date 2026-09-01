from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.models import create_group, get_group, group_exists, list_groups
from app.groups.sensitive import decrypt_sensitive_settings, ensure_sensitive_settings_encrypted
from app.groups.settings_validation import normalize_group_settings, validate_group_settings
from app.users.models import get_user


logger = logging.getLogger(__name__)


def _parse_settings(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return decrypt_sensitive_settings(deepcopy(raw))
    if isinstance(raw, str):
        if raw in {"-", "null", "None"}:
            return {}
        try:
            parsed: Any = json.loads(raw)
            return decrypt_sensitive_settings(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _sanitize_settings(current: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    """Repair one stored group snapshot without consulting its hierarchy."""

    return normalize_group_settings(current, raise_on_invalid=False)


def _get_group(group_id: str, db: Session):
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


def get_group_settings(group_id: str, db: Session, *, commit: bool = True) -> Dict[str, Any]:
    """Return the complete settings snapshot owned by one group.

    Parent relationships organize groups and management scope only. They do
    not participate in settings resolution.
    """

    group = _get_group(group_id, db)
    current_settings = _parse_settings(group.settings)
    changed, sanitized = _sanitize_settings(current_settings)
    if changed:
        _, encrypted = ensure_sensitive_settings_encrypted(sanitized)
        group.settings = encrypted
        flag_modified(group, "settings")
        if commit:
            db.commit()
            db.refresh(group)
        else:
            db.flush()
    return sanitized


def persist_group_settings(
    group_id: str,
    settings: Dict[str, Any],
    db: Session,
    *,
    commit: bool = True,
) -> Dict[str, Any]:
    """Validate and persist a complete independent settings snapshot."""

    previous_settings = get_group_settings(group_id, db, commit=commit)
    group = _get_group(group_id, db)
    try:
        _, normalized_settings = normalize_group_settings(settings)
        validated_settings = validate_group_settings(normalized_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _, encrypted = ensure_sensitive_settings_encrypted(validated_settings)
    group.settings = encrypted
    flag_modified(group, "settings")
    if commit:
        db.commit()
        db.refresh(group)
    else:
        db.flush()
    next_settings = get_group_settings(group_id, db, commit=commit)
    invalidate_leaderboard_cache_after_settings_change(previous_settings, next_settings)
    return next_settings


def invalidate_leaderboard_cache_after_settings_change(
    previous_settings: Dict[str, Any],
    next_settings: Dict[str, Any],
) -> None:
    previous_leaderboard = previous_settings.get("leaderboard") if isinstance(previous_settings, dict) else {}
    next_leaderboard = next_settings.get("leaderboard") if isinstance(next_settings, dict) else {}
    if previous_leaderboard == next_leaderboard:
        return

    from app.llm.leaderboard import clear_llm_model_leaderboard_cache

    keys_to_clear = {
        settings.get("artificial_analysis_api_key")
        for settings in (previous_leaderboard, next_leaderboard)
        if isinstance(settings, dict)
    }
    cleared_specific_key = False
    for api_key in keys_to_clear:
        if isinstance(api_key, str) and api_key.strip():
            clear_llm_model_leaderboard_cache(api_key)
            cleared_specific_key = True
    if not cleared_specific_key:
        clear_llm_model_leaderboard_cache()


def update_group_settings(group_id: str, page_name: str, key_name: str, value: Any, db: Session) -> Dict[str, Any]:
    if page_name not in DEFAULT_GROUP_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    if key_name not in DEFAULT_GROUP_SETTINGS[page_name]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    current = get_group_settings(group_id, db)
    if current[page_name][key_name] == value:
        return current
    current[page_name][key_name] = value
    return persist_group_settings(group_id, current, db)


def update_all_groups_setting(page_name: str, key_name: str, value: Any, db: Session) -> Dict[str, Any]:
    if page_name not in DEFAULT_GROUP_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    if key_name not in DEFAULT_GROUP_SETTINGS[page_name]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    updated_count = 0
    try:
        groups = list_groups(db)
        for group in groups:
            settings = get_group_settings(group.id, db, commit=False)
            if settings.get(page_name, {}).get(key_name) == value:
                continue
            settings.setdefault(page_name, {})[key_name] = value
            persist_group_settings(group.id, settings, db, commit=False)
            updated_count += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "updated_groups": updated_count,
        page_name: {key_name: value},
    }


def delete_group_settings(group_id: str, page_name: str, key_name: str | None = None, db: Session | None = None) -> Dict[str, Any]:
    if db is None:
        raise ValueError("db Session must be provided")
    if page_name not in DEFAULT_GROUP_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    settings = get_group_settings(group_id, db)
    if key_name is None:
        settings[page_name] = deepcopy(DEFAULT_GROUP_SETTINGS[page_name])
    else:
        if key_name not in DEFAULT_GROUP_SETTINGS[page_name]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
        settings[page_name][key_name] = deepcopy(DEFAULT_GROUP_SETTINGS[page_name][key_name])
    return persist_group_settings(group_id, settings, db)


def reset_all_group_settings(group_id: str, db: Session) -> Dict[str, Any]:
    return persist_group_settings(group_id, deepcopy(DEFAULT_GROUP_SETTINGS), db)


def get_group_setting_value(
    group_id: str,
    page_name: str,
    key_name: str,
    db: Session,
    *,
    commit: bool = True,
) -> Any:
    settings_dict = get_group_settings(group_id, db, commit=commit)
    if page_name not in DEFAULT_GROUP_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    if key_name not in DEFAULT_GROUP_SETTINGS[page_name]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    return settings_dict[page_name][key_name]


def ensure_data_control_permission(
    user_id: str,
    key_name: str,
    db: Session,
    detail: str | None = None,
) -> None:
    allowed = get_user_group_setting_value(user_id, "data_controls", key_name, db)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail or "This data control is disabled for your group.",
        )


def get_group_page_settings(group_id: str, page_name: str, db: Session) -> Dict[str, Any]:
    if page_name not in DEFAULT_GROUP_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    settings_dict = get_group_settings(group_id, db)
    return settings_dict[page_name]


def get_user_group_setting_value(
    user_id: str,
    page_name: str,
    key_name: str,
    db: Session,
    *,
    commit: bool = True,
) -> Any:
    user = get_user(db, user_id, None)
    return get_group_setting_value(
        user.group_id,
        page_name,
        key_name,
        db,
        commit=commit,
    )


def initialize_groups(db: Session) -> None:
    if group_exists(db, "default"):
        return

    _, initial_settings = ensure_sensitive_settings_encrypted(deepcopy(DEFAULT_GROUP_SETTINGS))
    create_group(
        db,
        "default",
        "Everybody",
        initial_settings,
        parent_id=None,
    )
