"""Stable user utility API composed from focused feature modules.

New implementation code should be added to the relevant focused module rather
than growing this compatibility façade back into a monolith.
"""

# This module deliberately re-exports legacy names after defining the local
# settings/account functions they depend on. Keep those compatibility imports
# even when a linter cannot see an in-module use for every exported symbol.
# ruff: noqa: E402, F401

from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
import logging
import string
import tempfile
from copy import deepcopy
from typing import Any, Dict, Iterator, List

from sqlalchemy.exc import IntegrityError
from app.users.schemas import (
    UserPersonalDetails,
    LLMAccessPresetEnum,
    PersonalityPresetEnum,
)
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.timezones import normalize_timezone_identifier, normalize_user_timezone
from sqlalchemy.orm.attributes import flag_modified
from app.users.init import (
    _parse_settings,
    _sync_with_defaults,
    get_user_setting_value,
    update_user_settings,
    update_user_settings_bulk,
    get_user_settings,
)
from app.groups.init import get_user_group_setting_value
from app.users.models import (
    User,
    build_user_email_match,
    canonicalize_user_email,
    create_user,
    get_user,
    normalize_utc_datetime,
    update_user_profile_picture_boolean,
    update_user_first_name,
    update_user_last_name,
    user_exists_by_email,
    update_user_email,
    change_user_last_model,
    soft_delete_user,
    hard_delete_user,
    restore_user as orm_restore_user,
    list_all_users,
)
from app.users.roles import is_admin_role, is_owner_role, normalize_external_role
from app.users.external_management import (
    is_externally_managed,
    require_locally_managed_account,
)
from app.auth.models import (
    Authentication,
    consume_password_reset_token,
    delete_authentication_all,
    delete_user_transient_auth_state,
    invalidate_user_password_reset_tokens,
)
from app.auth.session_store import revoke_user_sessions
from app.auth.password_policy import effective_minimum_password_length
from app.llm.utils import ensure_user_access_to_model
from app.settings.utils import (
    coerce_bool,
    get_effective_pinned_model_ids_for_user,
    get_login_passkey_policy,
    get_value_by_page_and_key,
    sanitize_pinned_model_ids,
)
from app.users.deletion_policy import (
    get_audit_log_user_deletion_retention_policy,
    get_auth_log_user_deletion_retention_policy,
)
from app.logging.models import (
    audit_log_erasure_guard,
    delete_authentication_logs_for_user,
    schedule_auth_log_deletion,
    cancel_auth_log_deletions_for_user,
    delete_audit_logs_for_user,
    schedule_audit_log_deletion,
    cancel_audit_log_deletions_for_user,
    delete_admin_notifications_for_user,
)

from app.logging.models import create_authentication_log
from app.groups.models import get_group, list_all_groups

logger = logging.getLogger(__name__)

LLM_ACCESS_FIELDS = (
    "first_name",
    "language",
    "country",
    "timezone",
    "location",
)

PASSWORD_POLICY_SPECIAL_CHARACTERS = string.punctuation


def _normalize_llm_permissions(value: Any) -> Dict[str, bool]:
    """Normalize LLM permissions to a dict with all fields."""
    normalized = {field: False for field in LLM_ACCESS_FIELDS}
    if isinstance(value, dict):
        for field in normalized:
            normalized[field] = bool(value.get(field, False))
    elif isinstance(value, bool):
        for field in normalized:
            normalized[field] = value
    return normalized


def _infer_llm_preset(permissions: Dict[str, bool]) -> str:
    """Infer the LLM access preset from permissions."""
    values = list(permissions.values())
    if values and all(values):
        return LLMAccessPresetEnum.all.value
    if not any(values):
        return LLMAccessPresetEnum.none.value
    return LLMAccessPresetEnum.custom.value


def normalize_personality_preset(value: PersonalityPresetEnum | str | None) -> str:
    """Normalize a personality preset value or raise an HTTP 400 error."""
    raw_value = value.value if hasattr(value, "value") else str(value or "")
    normalized = raw_value.strip().lower()
    if normalized not in PersonalityPresetEnum._value2member_map_:
        raise HTTPException(
            status_code=400, detail=f"Unsupported personality preset '{raw_value}'."
        )
    return normalized


def update_llm_access_settings(
    db,
    user_id: str,
    preset: LLMAccessPresetEnum | str | None,
    permissions_payload: Any,
    commit: bool = True,
) -> Dict[str, Any]:
    """Update LLM access settings for a user."""
    current_settings = get_user_settings(user_id, db, commit=commit)
    security_settings = current_settings.get("security") or {}
    existing_permissions = _normalize_llm_permissions(
        security_settings.get("allow_llm_to_access_personal_information")
    )

    normalized_permissions = None
    if permissions_payload is not None:
        normalized_permissions = _normalize_llm_permissions(permissions_payload)

    preset_str = None
    if preset is not None:
        preset_str = preset.value if hasattr(preset, "value") else str(preset)
        preset_str = (preset_str or "").lower()

    if preset_str in {LLMAccessPresetEnum.all.value, LLMAccessPresetEnum.none.value}:
        target_value = preset_str == LLMAccessPresetEnum.all.value
        normalized_permissions = {field: target_value for field in LLM_ACCESS_FIELDS}
    elif (
        preset_str == LLMAccessPresetEnum.custom.value
        and normalized_permissions is None
    ):
        normalized_permissions = existing_permissions

    if normalized_permissions is None:
        normalized_permissions = existing_permissions

    inferred_preset = _infer_llm_preset(normalized_permissions)
    if preset_str in {LLMAccessPresetEnum.all.value, LLMAccessPresetEnum.none.value}:
        final_preset = preset_str
    elif preset_str == LLMAccessPresetEnum.custom.value:
        final_preset = (
            LLMAccessPresetEnum.custom.value
            if inferred_preset == LLMAccessPresetEnum.custom.value
            else inferred_preset
        )
    else:
        final_preset = inferred_preset

    update_user_settings_bulk(
        user_id,
        {
            "security": {
                "allow_llm_to_access_personal_information": normalized_permissions,
                "allow_llm_to_access_personal_information_preset": final_preset,
            }
        },
        db,
        commit=commit,
    )

    return {"permissions": normalized_permissions, "preset": final_preset}


def update_user_personality_settings(
    db,
    user_id: str,
    *,
    preset: PersonalityPresetEnum | str | None = None,
    custom_instruction: str | None = None,
) -> Dict[str, Any]:
    """Update chat personality settings for a user."""
    if preset is None and custom_instruction is None:
        raise HTTPException(
            status_code=400, detail="At least one personality setting must be provided."
        )

    updates: Dict[str, Dict[str, Any]] = {"chat": {}}

    if preset is not None:
        updates["chat"]["personality_preset"] = normalize_personality_preset(preset)

    if custom_instruction is not None:
        normalized_instruction = str(custom_instruction).strip()
        if len(normalized_instruction) > 1000:
            raise HTTPException(
                status_code=400,
                detail="Custom personality instructions must be 1000 characters or fewer.",
            )
        updates["chat"]["personality_custom_instruction"] = normalized_instruction

    update_user_settings_bulk(user_id, updates, db)
    current_settings = get_user_settings(user_id, db)
    chat_settings = current_settings.get("chat") or {}

    return {
        "status": "success",
        "updated": {
            "chat": {
                "personality_preset": str(
                    chat_settings.get("personality_preset")
                    or PersonalityPresetEnum.none.value
                ),
                "personality_custom_instruction": str(
                    chat_settings.get("personality_custom_instruction") or ""
                ),
            }
        },
    }


def iter_user_data_export_json(
    user_id: str,
    db,
    db_log,
    *,
    include_file_contents: bool = False,
    include_files_section: bool = True,
    include_deleted_or_temp_chats: bool = False,
) -> Iterator[str]:
    """Generate one complete user archive without materializing the payload.

    Feature enablement controls runtime access, not ownership or portability.
    Dormant records therefore remain in the archive so exports are complete and
    can round-trip through the single self-service restore endpoint.
    """
    from app.admin.user_exports.files.models import (
        stream_admin_user_file_entries_json_array,
    )

    user, profile, settings, group_payload = _build_user_data_export_core(user_id, db)
    prompt_sections = {
        "prompts": _stream_user_prompts_json_array(user_id, db),
        "shared_prompt_subscriptions": _stream_shared_prompt_subscriptions_json_array(
            user_id, db
        ),
    }
    activity_logs_stream = (
        _stream_user_activity_logs_json(user_id, db_log)
        if _user_has_activity_logs(user_id, db_log)
        else None
    )

    fields: list[tuple[str, Any, bool]] = [
        ("export_type", USER_DATA_EXPORT_TYPE, False),
        ("export_version", USER_DATA_EXPORT_VERSION, False),
        ("email", user.email, False),
        ("user_id", user.id, False),
        ("user", profile, False),
        ("settings", settings, False),
        ("group", group_payload, False),
        (
            "auth",
            _stream_json_object_fields(
                [
                    (
                        "active_tokens",
                        _stream_authentication_records_json_array(
                            _query_user_authentication_records(user_id, db)
                        ),
                        True,
                    )
                ]
            ),
            True,
        ),
        ("activity_logs", activity_logs_stream, True),
        (
            "chats",
            _stream_user_chats_json_array(
                user_id,
                db,
                include_deleted_or_temp=include_deleted_or_temp_chats,
            ),
            True,
        ),
        ("notes", _stream_user_notes_json(user_id, db), True),
        (
            "memories",
            iter([_json_dumps(_strip_nulls(_export_user_memories(user_id, db)))]),
            True,
        ),
        ("todos", _stream_user_todos_json_array(user_id, db), True),
        (
            "skills",
            _stream_model_query_json_array(_query_user_skills(user_id, db)),
            True,
        ),
        ("skill_files", _stream_user_skill_files_json_array(user_id, db), True),
        (
            "shared_skill_subscriptions",
            _stream_model_query_json_array(
                _query_user_skill_subscriptions(user_id, db)
            ),
            True,
        ),
        (
            "file_folders",
            _stream_model_query_json_array(_query_user_file_folders(user_id, db)),
            True,
        ),
        (
            "shared_file_folder_subscriptions",
            _stream_user_file_folder_subscriptions_json_array(user_id, db),
            True,
        ),
        (
            "agents",
            _stream_model_query_json_array(_query_user_agents(user_id, db)),
            True,
        ),
        ("agent_assets", _stream_user_agent_assets_json_array(user_id, db), True),
        (
            "shared_agent_subscriptions",
            _stream_model_query_json_array(
                _query_user_agent_subscriptions(user_id, db)
            ),
            True,
        ),
        ("prompts", prompt_sections["prompts"], True),
        (
            "shared_prompt_subscriptions",
            prompt_sections["shared_prompt_subscriptions"],
            True,
        ),
        ("user_connections", _stream_user_connections_json_array(user_id, db), True),
        ("mcp_servers", _export_user_mcp_servers(user_id, db), False),
        (
            "model_setting_presets",
            _stream_user_model_setting_presets_json_array(user_id, db),
            True,
        ),
        ("usage_stats", _stream_user_usage_stats_json(user_id, db), True),
        (
            "slide_presentations",
            _stream_user_slide_presentations_json_array(user_id, db),
            True,
        ),
        (
            "files",
            stream_admin_user_file_entries_json_array(
                db,
                user.id,
                include_content=include_file_contents,
            ),
            True,
        ),
        (
            "projects",
            _stream_model_query_json_array(_query_user_projects(user_id, db)),
            True,
        ),
        (
            "automations",
            _stream_model_query_json_array(_query_user_automations(user_id, db)),
            True,
        ),
        (
            "feedback",
            _stream_model_query_json_array(_query_user_feedback(user_id, db)),
            True,
        ),
        (
            "export_coverage",
            _build_user_data_export_coverage(),
            False,
        ),
    ]
    if not include_files_section:
        # Canonical admin ZIPs carry files in per-user nested bundles. Avoid a
        # second metadata-only copy in the account shard and its parse cost.
        fields = [field for field in fields if field[0] != "files"]
    yield from _stream_json_object_fields(fields)


def build_user_data_export_json_file(
    user_id: str,
    db,
    db_log,
    *,
    include_file_contents: bool = False,
    include_deleted_or_temp_chats: bool = False,
):
    """Write a user data export JSON document to a spooled file.

    Callers choose whether retained hidden chats belong in their export. This
    keeps the lower-level serializer reusable while making the policy explicit
    at authenticated export boundaries.
    """
    export_file = tempfile.SpooledTemporaryFile(
        max_size=USER_DATA_EXPORT_SPOOL_THRESHOLD_BYTES, mode="w+b"
    )
    try:
        for chunk in iter_user_data_export_json(
            user_id,
            db,
            db_log,
            include_file_contents=include_file_contents,
            include_deleted_or_temp_chats=include_deleted_or_temp_chats,
        ):
            export_file.write(chunk.encode("utf-8"))
        export_file.seek(0)
        return export_file
    except Exception:
        export_file.close()
        raise


def build_user_data_export_audit_details(
    user_id: str, db_log, db=None
) -> Dict[str, Any]:
    has_activity_logs = _user_has_activity_logs(user_id, db_log)
    sections = [
        "agent_assets",
        "agents",
        "auth",
        "automations",
        "chats",
        "email",
        "export_coverage",
        "feedback",
        "files",
        "file_folders",
        "group",
        "mcp_servers",
        "model_setting_presets",
        "notes",
        "projects",
        "prompts",
        "settings",
        "shared_agent_subscriptions",
        "shared_file_folder_subscriptions",
        "shared_prompt_subscriptions",
        "shared_skill_subscriptions",
        "skill_files",
        "skills",
        "slide_presentations",
        "todos",
        "usage_stats",
        "user",
        "user_connections",
        "user_id",
    ]
    sections.append("memories")
    if has_activity_logs:
        sections.append("activity_logs")
    return {
        "sections": sorted(sections),
        "has_activity_logs": has_activity_logs,
    }


def iter_admin_export_users(db) -> Iterator[User]:
    """Iterate users in the stable order used by canonical admin archives."""
    yield from _iter_query_rows(db.query(User).order_by(User.last_active_at.desc()))


def _normalized_timestamp(value: Any) -> datetime | None:
    """Normalize a value to a UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


def _safe_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    """Safely convert a value to datetime, using fallback if conversion fails."""
    normalized = _normalized_timestamp(value)
    if normalized is not None:
        return normalized
    return fallback or datetime.now(timezone.utc)


def _merge_settings(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge settings dicts."""
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_and_sync_user_settings(
    base: Dict[str, Any], updates: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge imported settings and prune keys that are no longer in defaults."""
    _changed, merged = _sync_with_defaults(_merge_settings(deepcopy(base), updates))
    return merged


ALLOWED_FONT_FAMILIES = {
    "inter",
    "system",
    "verdana",
    "georgia",
    "times",
    "courier",
    "roboto",
}


def initialize_user_locale_defaults(db, user_id: str, **detected_values):
    """Fill blank locale preferences from browser detection without overwriting choices.

    This function is intentionally idempotent. The chat client may call it
    whenever one of the core locale fields is blank, while explicit settings
    selected later by the user remain authoritative.
    """

    current = get_user_settings(user_id, db)
    general = current.get("general", {}) if isinstance(current, dict) else {}
    updates: dict[str, str] = {}

    for field in ("language", "country", "timezone"):
        current_value = str(general.get(field) or "").strip()
        detected_value = detected_values.get(field)
        if hasattr(detected_value, "value"):
            detected_value = detected_value.value
        normalized_value = str(detected_value or "").strip()
        if not current_value and normalized_value:
            updates[field] = normalized_value

    if not updates:
        return {"status": "success", "updated": {}}

    changed = update_user_settings_bulk(user_id, {"general": updates}, db)
    return {"status": "success", "updated": changed}


def dismiss_user_welcome_card(db, user_id: str):
    """Persist that the user dismissed the non-blocking first-run welcome card."""

    update_user_settings_bulk(
        user_id,
        {"states": {"welcome_card_dismissed": True}},
        db,
    )
    return {"status": "success"}


# -------------------
# Update User Toggle Setting
# -------------------
def update_user_toggle_setting(db, user_id: str, **toggle_values):
    """Update a single toggle setting supplied in ``toggle_values``.

    Exactly one toggle is allowed per request to keep the operation explicit
    for the caller (frontend expects single-toggle updates).
    """

    toggle_map = {
        "allow_llm_to_access_personal_information": (
            "security",
            "allow_llm_to_access_personal_information",
        ),
        "allow_llm_to_access_personal_information_preset": (
            "security",
            "allow_llm_to_access_personal_information_preset",
        ),
        "render_user_messages_markdown": ("chat", "render_user_messages_markdown"),
        "render_assistant_messages_markdown": (
            "chat",
            "render_assistant_messages_markdown",
        ),
        "ctrl_enter_to_send": ("chat", "ctrl_enter_to_send"),
        "always_use_temporary_chat": ("chat", "always_use_temporary_chat"),
        "chat_full_width": ("chat", "chat_full_width"),
        "byok_statistics_enabled": ("chat", "byok_statistics_enabled"),
        "byok_statistics_retention_days": ("chat", "byok_statistics_retention_days"),
        "show_message_nav": ("chat", "show_message_nav"),
        "show_model_settings": ("chat", "show_model_settings"),
        "show_assistant_message_metadata": ("chat", "show_assistant_message_metadata"),
        "speech_playback_speed": ("chat", "speech_playback_speed"),
    }

    provided = {
        field: value for field, value in toggle_values.items() if value is not None
    }

    if not provided:
        raise HTTPException(status_code=400, detail="No toggle value provided.")

    if len(provided) != 1:
        raise HTTPException(
            status_code=400, detail="Exactly one toggle can be updated per request."
        )

    field, value = next(iter(provided.items()))

    if field not in toggle_map:
        raise HTTPException(status_code=400, detail=f"Unsupported toggle '{field}'.")

    page, key = toggle_map[field]

    if field == "always_use_temporary_chat":
        temp_chat_allowed = bool(
            get_user_group_setting_value(user_id, "chat", "allow_temporary_chat", db)
        )
        if not temp_chat_allowed:
            raise HTTPException(
                status_code=403,
                detail="Temporary chats are disabled for your group.",
            )

    # Handle allow_llm_to_access_personal_information specially - it can be dict or bool
    if field == "allow_llm_to_access_personal_information":
        preset_hint = None
        permissions_payload = value
        if not isinstance(value, dict):
            preset_hint = "all" if value else "none"
        llm_result = update_llm_access_settings(
            db, user_id, preset_hint, permissions_payload
        )
        return {
            "status": "success",
            "updated": {
                "security": {
                    "allow_llm_to_access_personal_information": llm_result[
                        "permissions"
                    ],
                    "allow_llm_to_access_personal_information_preset": llm_result[
                        "preset"
                    ],
                }
            },
        }
    if field == "allow_llm_to_access_personal_information_preset":
        llm_result = update_llm_access_settings(db, user_id, value, None)
        return {
            "status": "success",
            "updated": {
                "security": {
                    "allow_llm_to_access_personal_information": llm_result[
                        "permissions"
                    ],
                    "allow_llm_to_access_personal_information_preset": llm_result[
                        "preset"
                    ],
                }
            },
        }

    if field == "speech_playback_speed":
        try:
            speed = float(value)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="speech_playback_speed must be a number."
            )
        speed = max(0.5, min(2.0, speed))
        updated_part = update_user_settings(user_id, page, key, speed, db)
        return {"status": "success", "updated": updated_part.get(page, {key: speed})}

    if field == "byok_statistics_retention_days":
        try:
            from app.llmstats.models import coerce_byok_stats_retention_days

            retention_days = coerce_byok_stats_retention_days(value)
        except Exception:
            retention_days = 90
        updated_part = update_user_settings(user_id, page, key, retention_days, db)
        return {
            "status": "success",
            "updated": updated_part.get(page, {key: retention_days}),
        }

    updated_part = update_user_settings(user_id, page, key, bool(value), db)
    if field == "byok_statistics_enabled":
        try:
            from app.llmstats.models import invalidate_user_statistics_cache

            invalidate_user_statistics_cache()
        except Exception:
            pass

    return {"status": "success", "updated": updated_part.get(page, {key: bool(value)})}


# -------------------
# Update User Select Setting
# -------------------
def update_user_select_setting(db, user_id: str, **select_values):
    """Update a single select-style user setting."""

    select_map = {
        "profile_visibility": ("security", "profile_visibility"),
        "language": ("general", "language"),
        "country": ("general", "country"),
        "timezone": ("general", "timezone"),
        "font": ("appearance", "font"),
    }

    provided = {
        field: value for field, value in select_values.items() if value is not None
    }

    if not provided:
        raise HTTPException(status_code=400, detail="No select value provided.")

    if len(provided) != 1:
        raise HTTPException(
            status_code=400, detail="Exactly one select can be updated per request."
        )

    field, value = next(iter(provided.items()))

    if field not in select_map:
        raise HTTPException(status_code=400, detail=f"Unsupported select '{field}'.")

    normalized_value = _normalize_select_value(field, value)

    page, key = select_map[field]

    updated_part = update_user_settings(user_id, page, key, normalized_value, db)

    return {
        "status": "success",
        "updated": updated_part.get(page, {key: normalized_value}),
    }


def _normalize_visibility_value(value, *, default: str = "private") -> str:
    if value is None:
        return default

    if hasattr(value, "value"):
        value = value.value

    normalized = str(value).strip().lower()

    if normalized not in {"public", "private"}:
        raise HTTPException(status_code=400, detail="Unsupported visibility selection.")

    return normalized


def _normalize_select_value(field: str, value):
    if value is None:
        return ""

    if hasattr(value, "value"):
        value = value.value

    if isinstance(value, str):
        normalized = value.strip()
    else:
        normalized = str(value)

    if field in {"language", "country"}:
        normalized = normalized.lower()
    elif field == "profile_visibility":
        return _normalize_visibility_value(normalized)
    elif field == "timezone":
        try:
            normalized = normalize_user_timezone(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif field == "font":
        normalized = normalized.lower()
        if not normalized:
            normalized = "inter"
        if normalized not in ALLOWED_FONT_FAMILIES:
            raise HTTPException(status_code=400, detail="Unsupported font selection.")

    return normalized


# -------------------
# Update User Color Theme
# -------------------
def update_user_color_theme(user_id, db, theme=None, color_theme=None):
    """Update display mode and/or the user's message-bubble color.

    At least **one** of *theme* or *color_theme* has to be provided. If both are
    supplied, both will be updated. ``color_theme`` is retained as the stored
    field name, but now affects only the user's chat-message background. Values are validated against the allowed
    lists before persisting via :pyfunc:`app.users.init.update_user_settings`.
    """

    # Ensure the user exists early.
    get_user(db, user_id, None)
    # Require at least one argument
    if theme is None and color_theme is None:
        raise HTTPException(
            status_code=400, detail="Either 'theme' or 'color_theme' must be provided."
        )
    updates = {}
    if theme is not None:
        # Extract raw value from Enum
        raw_theme = theme.value if hasattr(theme, "value") else theme
        update_user_settings(user_id, "appearance", "theme", raw_theme, db)
        updates["theme"] = raw_theme
    if color_theme is not None:
        raw_color = color_theme.value if hasattr(color_theme, "value") else color_theme
        update_user_settings(user_id, "appearance", "color_theme", raw_color, db)
        updates["color_theme"] = raw_color
    # Return the keys that were actually modified
    updates["status"] = "success"
    return updates


# -------------------
# Update User Personal Details
# -------------------
def update_user_personal_details(
    user_id, db, user_personal_details: UserPersonalDetails
):
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_locally_managed_account(user)
    if getattr(user, "account_type", "regular") == "temporary":
        raise HTTPException(
            status_code=403, detail="Temporary accounts cannot change personal details."
        )

    first_name = (
        user_personal_details.first_name.strip()
        if isinstance(user_personal_details.first_name, str)
        else None
    )
    last_name = (
        user_personal_details.last_name.strip()
        if isinstance(user_personal_details.last_name, str)
        else None
    )
    email = (
        canonicalize_user_email(str(user_personal_details.email))
        if user_personal_details.email is not None
        else None
    )

    if first_name and first_name != user.first_name:
        if not get_user_group_setting_value(
            user_id,
            "users",
            "allow_change_name",
            db,
            commit=False,
        ):
            raise HTTPException(status_code=409, detail="Name change is not allowed.")
        user.first_name = first_name
        logger.info("Updated first name for user %s", user_id)

    if last_name and last_name != user.last_name:
        if not get_user_group_setting_value(
            user_id,
            "users",
            "allow_change_name",
            db,
            commit=False,
        ):
            raise HTTPException(status_code=409, detail="Name change is not allowed.")
        user.last_name = last_name
        logger.info("Updated last name for user %s", user_id)

    if email and email != canonicalize_user_email(getattr(user, "email", "")):
        if not get_user_group_setting_value(
            user_id,
            "users",
            "allow_change_email",
            db,
            commit=False,
        ):
            raise HTTPException(status_code=409, detail="Email change is not allowed.")
        from app.email.change import request_email_change

        try:
            request_email_change(db, user, email)
        except Exception:
            db.rollback()
            raise
        email_change_pending = True
    else:
        email_change_pending = False

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    return {
        "status": "success",
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "email_change_pending": email_change_pending,
    }


def get_password_policy_requirements(db) -> dict[str, object]:
    return {
        "min_len": effective_minimum_password_length(
            get_value_by_page_and_key("login_general", "minimum_password_length", db)
        ),
        "min_special": int(
            get_value_by_page_and_key("login_general", "minimum_special_characters", db)
            or 0
        ),
        "min_upper": int(
            get_value_by_page_and_key(
                "login_general", "minimum_uppercase_characters", db
            )
            or 0
        ),
        "min_lower": int(
            get_value_by_page_and_key(
                "login_general", "minimum_lowercase_characters", db
            )
            or 0
        ),
        "min_num": int(
            get_value_by_page_and_key("login_general", "minimum_number_characters", db)
            or 0
        ),
        "special_characters": PASSWORD_POLICY_SPECIAL_CHARACTERS,
        "character_class_mode": "unicode_letter_digit_with_ascii_special",
    }


def count_password_character_classes(
    password: str,
    *,
    special_characters: str = PASSWORD_POLICY_SPECIAL_CHARACTERS,
) -> dict[str, int]:
    special_set = set(str(special_characters or ""))
    return {
        "len": len(password),
        "special": sum(1 for char in password if char in special_set),
        "upper": sum(1 for char in password if char.isupper()),
        "lower": sum(1 for char in password if char.islower()),
        "num": sum(1 for char in password if char.isdigit()),
    }


def _mark_user_settings_modified(user) -> None:
    try:
        flag_modified(user, "settings")
    except Exception:
        pass


def _set_password_state_flags(
    user,
    db,
    *,
    has_to_change_password: bool | None = None,
    social_needs_password_setup: bool | None = None,
    sso_needs_password_setup: bool | None = None,
) -> None:
    current_settings = _parse_settings(getattr(user, "settings", None))
    _changed, settings = _sync_with_defaults(current_settings)
    changed = False

    if (
        has_to_change_password is not None
        and settings["security"].get("has_to_change_password") != has_to_change_password
    ):
        settings["security"]["has_to_change_password"] = has_to_change_password
        changed = True
    if (
        social_needs_password_setup is not None
        and settings["social_login"].get("needs_password_setup")
        != social_needs_password_setup
    ):
        settings["social_login"]["needs_password_setup"] = social_needs_password_setup
        changed = True
    if (
        sso_needs_password_setup is not None
        and settings["sso_login"].get("needs_password_setup")
        != sso_needs_password_setup
    ):
        settings["sso_login"]["needs_password_setup"] = sso_needs_password_setup
        changed = True

    if changed:
        user.settings = settings
        _mark_user_settings_modified(user)


def _commit_password_change_transaction(
    db,
    *,
    user,
    new_password_hash: str,
    has_to_change_password: bool | None = None,
    social_needs_password_setup: bool | None = None,
    sso_needs_password_setup: bool | None = None,
    reset_token=None,
    security_event_type: str = "password_changed",
    security_context: dict[str, str] | None = None,
    verified_current_password: str | None = None,
    new_password_plaintext_for_reuse_check: str | None = None,
) -> dict[str, object]:
    locked_user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user.id)
        .with_for_update()
        .first()
    )
    if locked_user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user = locked_user
    previous_password_hash = getattr(user, "hashed_password", "")
    previous_settings = deepcopy(getattr(user, "settings", None))
    previous_consumed_at = (
        getattr(reset_token, "consumed_at", None) if reset_token is not None else None
    )
    previous_requested_ip = (
        getattr(reset_token, "requested_ip", None) if reset_token is not None else None
    )
    previous_requested_user_agent = (
        getattr(reset_token, "requested_user_agent", None)
        if reset_token is not None
        else None
    )

    try:
        if verified_current_password is not None:
            from app.auth.utils import verify_password

            if not verify_password(
                verified_current_password,
                str(getattr(user, "hashed_password", "") or ""),
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Old password is incorrect.",
                )
        if new_password_plaintext_for_reuse_check is not None:
            from app.auth.utils import verify_password

            if verify_password(
                new_password_plaintext_for_reuse_check,
                str(getattr(user, "hashed_password", "") or ""),
            ):
                raise HTTPException(
                    status_code=400,
                    detail="New password must be different from the current password.",
                )
        if reset_token is not None and not consume_password_reset_token(
            db, getattr(reset_token, "id", None)
        ):
            raise HTTPException(
                status_code=400, detail="Invalid or expired password reset token."
            )
        # A password mutation invalidates every sibling recovery capability in
        # the same user-first transaction. The winning reset token was already
        # consumed above and is therefore excluded by this update.
        from app.email.change import cancel_pending_email_changes

        cancel_pending_email_changes(db, user.id)
        invalidate_user_password_reset_tokens(db, user.id, commit=False)
        delete_user_transient_auth_state(db, user.id, commit=False)

        user.hashed_password = new_password_hash
        _set_password_state_flags(
            user,
            db,
            has_to_change_password=has_to_change_password,
            social_needs_password_setup=social_needs_password_setup,
            sso_needs_password_setup=sso_needs_password_setup,
        )
        delete_authentication_all(
            db, user_id=user.id, commit=False, revoke_cached=False
        )
        from app.email.service import enqueue_security_event

        context = security_context or {}
        enqueue_security_event(
            db,
            user=user,
            event_type=security_event_type,
            source_id=(
                f"reset-token:{getattr(reset_token, 'id', '')}"
                if reset_token is not None
                else None
            ),
            device=context.get("device"),
            network=context.get("network"),
        )
        db.commit()
    except Exception:
        db.rollback()
        user.hashed_password = previous_password_hash
        if hasattr(user, "settings"):
            user.settings = previous_settings
        if reset_token is not None:
            reset_token.consumed_at = previous_consumed_at
            reset_token.requested_ip = previous_requested_ip
            reset_token.requested_user_agent = previous_requested_user_agent
        raise

    # Revoke cached sessions immediately after the authoritative database
    # commit. A later refresh failure must never leave a stale Redis session
    # usable after the password has changed.
    revoke_user_sessions(user.id)
    try:
        db.refresh(user)
    except Exception as exc:
        raise RuntimeError(
            "Password change committed but failed to refresh user state"
        ) from exc
    return {"status": "success", "reauth_required": True}


def _assert_password_policy(new_password: str, db):
    """Ensure *new_password* satisfies globally configured password policy."""
    requirements = get_password_policy_requirements(db)
    counts = count_password_character_classes(
        new_password,
        special_characters=str(requirements["special_characters"]),
    )

    min_len = int(requirements["min_len"])
    min_special = int(requirements["min_special"])
    min_upper = int(requirements["min_upper"])
    min_lower = int(requirements["min_lower"])
    min_num = int(requirements["min_num"])

    violations: list[str] = []
    if counts["len"] < min_len:
        violations.append(f"Password must be at least {min_len} characters long")
    if counts["special"] < min_special:
        violations.append(
            f"Password must contain at least {min_special} special character(s)"
        )
    if counts["upper"] < min_upper:
        violations.append(
            f"Password must contain at least {min_upper} uppercase letter(s)"
        )
    if counts["lower"] < min_lower:
        violations.append(
            f"Password must contain at least {min_lower} lowercase letter(s)"
        )
    if counts["num"] < min_num:
        violations.append(f"Password must contain at least {min_num} number(s)")

    if violations:
        raise HTTPException(status_code=400, detail="; ".join(violations))


def _ensure_new_password_differs_from_current(user, new_password: str) -> None:
    from app.auth.utils import verify_password

    current_hash = str(getattr(user, "hashed_password", "") or "")
    if current_hash and verify_password(new_password, current_hash):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from the current password.",
        )


# -------------------
# Change Password
# -------------------
def change_password(
    user_id: str,
    old_password: str,
    new_password: str,
    db,
    *,
    security_context: dict[str, str] | None = None,
):
    from app.auth.utils import verify_password, hash_password

    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    require_locally_managed_account(user)
    if getattr(user, "account_type", "regular") == "temporary":
        raise HTTPException(
            status_code=403, detail="Temporary accounts cannot change passwords."
        )

    must_change = bool(
        get_user_setting_value(
            user_id,
            "security",
            "has_to_change_password",
            db,
            commit=False,
        )
        or False
    )
    if not must_change:
        if not get_user_group_setting_value(
            user_id,
            "users",
            "allow_change_password",
            db,
            commit=False,
        ):
            raise HTTPException(
                status_code=409, detail="Password change is not enabled."
            )

    if not verify_password(old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Old password is incorrect.")

    _ensure_new_password_differs_from_current(user, new_password)
    _assert_password_policy(new_password, db)
    return _commit_password_change_transaction(
        db,
        user=user,
        new_password_hash=hash_password(new_password),
        has_to_change_password=False if must_change else None,
        security_event_type="password_changed",
        security_context=security_context,
        verified_current_password=old_password,
        new_password_plaintext_for_reuse_check=new_password,
    )


# -------------------
# Admin update user profile
# -------------------
def admin_update_user_profile(
    payload,
    db,
    *,
    security_context: dict[str, str] | None = None,
):
    """Update user profile fields from admin panel.

    Handles: email, first_name, last_name, group_id, password,
    wrong_sign_in_attempts, and lock fields.
    """
    from app.auth.utils import hash_password

    user = (
        db.query(User)
        .filter(User.id == payload.user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    externally_managed_fields = (
        "email",
        "first_name",
        "last_name",
        "wrong_sign_in_attempts",
    )
    attempted_external_fields = [
        field
        for field in externally_managed_fields
        if getattr(payload, field, None) is not None
    ]
    if getattr(payload, "password", None) is not None and str(payload.password).strip():
        attempted_external_fields.append("password")
    if is_externally_managed(user) and attempted_external_fields:
        raise HTTPException(
            status_code=409,
            detail=(
                "Identity and local authentication fields for externally managed "
                "accounts must be changed in the organization's identity provider."
            ),
        )
    updated_fields = []
    changes = []
    password_changed = False
    identity_changed = False
    original_email = user.email

    def record_change(field: str, old_value: Any, new_value: Any) -> None:
        updated_fields.append(field)
        changes.append(
            {
                "field": field,
                "old": old_value,
                "new": new_value,
            }
        )

    # Update email
    if payload.email is not None:
        email = canonicalize_user_email(payload.email)
        if email and email != user.email:
            old_email = user.email
            email_match = build_user_email_match(email)
            existing = (
                db.query(User).filter(email_match, User.id != user.id).first()
                if email_match is not None
                else None
            )
            if existing:
                raise HTTPException(
                    status_code=409, detail="Email already in use by another user."
                )
            user.email = email
            record_change("email", old_email, email)
            identity_changed = True

    # Update first_name
    if payload.first_name is not None:
        first_name = payload.first_name.strip()
        if first_name and first_name != user.first_name:
            old_first_name = user.first_name
            user.first_name = first_name
            record_change("first_name", old_first_name, first_name)

    # Update last_name
    if payload.last_name is not None:
        last_name = payload.last_name.strip()
        if last_name and last_name != user.last_name:
            old_last_name = user.last_name
            user.last_name = last_name
            record_change("last_name", old_last_name, last_name)

    # Update group_id
    if payload.group_id is not None:
        group_id = payload.group_id.strip()
        if group_id and group_id != user.group_id:
            old_group_id = user.group_id
            # Verify group exists
            try:
                group = get_group(db, group_id)
                if not group:
                    raise HTTPException(status_code=404, detail="Group not found.")
            except Exception:
                raise HTTPException(status_code=404, detail="Group not found.")
            user.group_id = group_id
            record_change("group_id", old_group_id, group_id)

    # Update password
    if payload.password is not None and payload.password.strip():
        _ensure_new_password_differs_from_current(user, payload.password)
        _assert_password_policy(payload.password, db)
        user.hashed_password = hash_password(payload.password)
        # Password replacement is an account-recovery boundary. Remove every
        # database-backed login in the same transaction so a stolen access or
        # refresh token cannot survive a successful administrative reset.
        delete_authentication_all(
            db,
            user_id=user.id,
            commit=False,
            revoke_cached=False,
        )
        password_changed = True
        updated_fields.append("password")
        changes.append({"field": "password", "changed": True, "sessions_revoked": True})

    # Update wrong_sign_in_attempts
    if payload.wrong_sign_in_attempts is not None:
        attempts = max(0, int(payload.wrong_sign_in_attempts))
        settings = user.settings or {}
        if not isinstance(settings, dict):
            settings = {}
        secret_settings = settings.get("secret", {})
        if not isinstance(secret_settings, dict):
            secret_settings = {}
        old_attempts = secret_settings.get("wrong_sign_in_attempts", 0)
        if old_attempts == attempts:
            pass
        else:
            secret_settings["wrong_sign_in_attempts"] = attempts
            settings["secret"] = secret_settings
            user.settings = settings
            flag_modified(user, "settings")
            record_change("wrong_sign_in_attempts", old_attempts, attempts)

    # Update lock fields
    if payload.lock is not None:
        old_lock = deepcopy(
            user.lock
            or {"is_locked": False, "lock_until": None, "type": "", "reason": ""}
        )
        lock_data = {
            "is_locked": bool(payload.lock.is_locked),
            "lock_until": payload.lock.lock_until if payload.lock.lock_until else None,
            "type": payload.lock.type if payload.lock.type else "",
            "reason": payload.lock.reason if payload.lock.reason else "",
        }
        if old_lock != lock_data:
            user.lock = lock_data
            record_change("lock", old_lock, lock_data)

    if updated_fields:
        context = security_context or {}
        if password_changed or identity_changed:
            from app.email.change import cancel_pending_email_changes

            cancel_pending_email_changes(db, user.id)
            invalidate_user_password_reset_tokens(db, user.id, commit=False)
            delete_user_transient_auth_state(db, user.id, commit=False)
        if identity_changed:
            from app.email.models import (
                cancel_user_email,
            )

            delete_authentication_all(
                db,
                user_id=user.id,
                commit=False,
                revoke_cached=False,
            )
            cancel_user_email(
                db,
                user.id,
                preserve_template_types=("security_event",),
                commit=False,
            )

            from app.email.service import enqueue_security_event, user_email_language

            try:
                language_code = user_email_language(user, db)
                event_source = f"admin-email:{user.id}:{datetime.now(timezone.utc).isoformat()}"
                for audience, recipient in (("old", original_email), ("new", user.email)):
                    enqueue_security_event(
                        db,
                        recipient=recipient,
                        user_id=user.id,
                        language_code=language_code,
                        event_type="admin_email_changed",
                        source_id=f"{event_source}:{audience}",
                        device=context.get("device"),
                        network=context.get("network"),
                        priority=5,
                    )
            except Exception:
                db.rollback()
                raise
        if password_changed:
            from app.email.service import enqueue_security_event

            try:
                enqueue_security_event(
                    db,
                    user=user,
                    event_type="admin_password_reset",
                    source_id=f"admin-password:{user.id}:{datetime.now(timezone.utc).isoformat()}",
                    device=context.get("device"),
                    network=context.get("network"),
                    priority=5,
                )
            except Exception:
                db.rollback()
                raise
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Email already in use by another user.",
            ) from exc
        except Exception:
            db.rollback()
            raise
        if password_changed or identity_changed:
            # Database deletion is authoritative. Clear Redis after the commit
            # so the cache is never revoked for a transaction that rolled back.
            revoke_user_sessions(user.id)
        db.refresh(user)

    return {
        "status": "success",
        "updated_fields": updated_fields,
        "changes": changes,
        "sessions_revoked": password_changed or identity_changed,
    }


# -------------------
# Get admin user profile data
# -------------------
def get_admin_user_profile(
    user_id: str,
    db,
    *,
    include_sensitive_profile: bool = False,
    include_security: bool = False,
    include_activity: bool = False,
):
    """Get user profile data for admin editing with explicit category opt-ins."""
    user = get_user(db, user_id)

    # Get group info
    group_name = None
    try:
        group = get_group(db, user.group_id)
        if group:
            group_name = group.name
    except Exception:
        pass

    profile = {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "group_id": user.group_id,
        "group_name": group_name,
        "role": user.role,
        "is_active": user.is_active,
        "externally_managed": is_externally_managed(user),
        "external_auth_provider": getattr(user, "external_auth_provider", None),
    }

    if include_security:
        wrong_sign_in_attempts = 0
        settings = user.settings or {}
        if isinstance(settings, dict):
            secret = settings.get("secret", {})
            if isinstance(secret, dict):
                wrong_sign_in_attempts = secret.get("wrong_sign_in_attempts", 0)
        lock_data = user.lock or {
            "is_locked": False,
            "lock_until": None,
            "type": "",
            "reason": "",
        }
        profile.update(
            {
                "wrong_sign_in_attempts": wrong_sign_in_attempts,
                "lock": lock_data,
            }
        )

    if include_activity:
        profile.update(
            {
                "created_at": _serialize_datetime(user.created_at),
                "last_active_at": _serialize_datetime(user.last_active_at),
            }
        )

    return profile


# -------------------
# Change Password Init
# -------------------
def change_password_init(db):
    return get_password_policy_requirements(db)


# -------------------
# Set Password (for social login users)
# -------------------
def set_password_for_social_user(
    user_id: str,
    new_password: str,
    db,
    *,
    security_context: dict[str, str] | None = None,
):
    """
    Set a password for social login users who don't have one.
    This allows them to login with email/password in addition to social login.
    """
    from app.auth.utils import hash_password

    user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    require_locally_managed_account(user)

    needs_social_password_setup = bool(
        get_user_setting_value(
            user_id,
            "social_login",
            "needs_password_setup",
            db,
            commit=False,
        )
    )
    needs_sso_password_setup = bool(
        get_user_setting_value(
            user_id,
            "sso_login",
            "needs_password_setup",
            db,
            commit=False,
        )
    )
    must_change = bool(
        get_user_setting_value(
            user_id,
            "security",
            "has_to_change_password",
            db,
            commit=False,
        )
        or False
    )

    if not (needs_social_password_setup or needs_sso_password_setup):
        raise HTTPException(
            status_code=400, detail="Password is already set for this account."
        )

    _ensure_new_password_differs_from_current(user, new_password)
    _assert_password_policy(new_password, db)
    return _commit_password_change_transaction(
        db,
        user=user,
        new_password_hash=hash_password(new_password),
        has_to_change_password=False if must_change else None,
        social_needs_password_setup=False if needs_social_password_setup else None,
        sso_needs_password_setup=False if needs_sso_password_setup else None,
        security_event_type="password_set",
        security_context=security_context,
        new_password_plaintext_for_reuse_check=new_password,
    )


def _normalize_user_deletion_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"delete_instantly", "delete_after_days", "retain"}:
        return mode
    return "delete_after_days"


def _get_configured_user_deletion_mode(db) -> str:
    mode = get_value_by_page_and_key("users", "user_deletion_mode", db)
    if mode in (None, ""):
        mode = get_value_by_page_and_key("security", "user_deletion_mode", db)
    if mode in (None, ""):
        mode = "delete_after_days"
    return _normalize_user_deletion_mode(mode)


def _get_user_deletion_retention_days(db) -> int:
    days_value = get_value_by_page_and_key("users", "user_deletion_retention_days", db)
    if days_value in (None, ""):
        days_value = get_value_by_page_and_key(
            "security", "user_deletion_retention_days", db
        )
    try:
        return int(days_value) if days_value is not None else 30
    except (ValueError, TypeError):
        return 30


def get_user_deletion_policy(db, *, now: datetime | None = None) -> dict[str, Any]:
    mode = _get_configured_user_deletion_mode(db)
    retention_days = (
        _get_user_deletion_retention_days(db) if mode == "delete_after_days" else None
    )
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if mode == "delete_instantly" or (
        mode == "delete_after_days" and (retention_days or 0) <= 0
    ):
        return {
            "mode": mode,
            "effect": "erasure",
            "restorable": False,
            "retention_days": 0 if mode == "delete_after_days" else retention_days,
            "purge_scheduled_at": None,
        }

    if mode == "delete_after_days":
        purge_scheduled_at = now + timedelta(days=retention_days or 30)
        return {
            "mode": mode,
            "effect": "scheduled_deletion",
            "restorable": True,
            "retention_days": retention_days,
            "purge_scheduled_at": purge_scheduled_at,
        }

    return {
        "mode": mode,
        "effect": "deactivation",
        "restorable": True,
        "retention_days": None,
        "purge_scheduled_at": None,
    }


def _apply_auth_log_retention(db_log, user_id: str, policy: dict[str, Any]) -> None:
    """Apply the resolved authentication-log policy."""

    if policy["mode"] == "retain":
        cancel_auth_log_deletions_for_user(db_log, user_id)
    elif policy["delete_immediately"]:
        cancel_auth_log_deletions_for_user(db_log, user_id)
        delete_authentication_logs_for_user(db_log, user_id)
    else:
        schedule_auth_log_deletion(db_log, user_id, policy["retention_days"])


def _apply_audit_log_retention(
    db_log,
    user_id: str,
    policy: dict[str, Any],
    *,
    main_db,
) -> None:
    """Apply the coupled audit-log and user-scoped-notification policy."""

    if policy["mode"] == "retain":
        cancel_audit_log_deletions_for_user(db_log, user_id)
    elif policy["delete_immediately"]:
        cancel_audit_log_deletions_for_user(db_log, user_id)
        with audit_log_erasure_guard(user_id, bind=main_db.get_bind()) as guard_db:
            # Soft deletion commits before cross-database audit retention runs.
            # Refresh the authoritative row only after taking the guard shared
            # with restoration. A missing row is a completed hard deletion.
            user = (
                main_db.query(User)
                .filter(User.id == user_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if user is not None and user.deleted_at is None:
                main_db.rollback()
                return

            delete_audit_logs_for_user(
                db_log,
                user_id,
                main_db=main_db,
                erasure_guard_db=guard_db,
            )
            delete_admin_notifications_for_user(db_log, user_id)
    else:
        schedule_audit_log_deletion(db_log, user_id, policy["retention_days"])


# -------------------
# Delete User
# -------------------
def delete_user(
    db,
    db_log,
    user_id,
    *,
    check_self_deletion: bool = True,
    force_hard_delete: bool = False,
    allow_administrative_target: bool = False,
):
    """
    Delete a user based on the configured retention policy.

    Args:
        db: Main database session
        db_log: Audit database session
        user_id: The user to delete
        check_self_deletion: If True, verify self-deletion is allowed (for user-initiated deletion)
        force_hard_delete: If True, bypass the configured user deletion mode and erase immediately
        allow_administrative_target: Whether the caller has already verified
            that the instance owner authorized deletion of an admin account.
    """
    user = get_user(db, user_id)
    if check_self_deletion and is_externally_managed(user):
        raise HTTPException(
            status_code=409,
            detail="Externally managed accounts must be removed by the organization.",
        )
    if check_self_deletion and getattr(user, "account_type", "regular") == "temporary":
        raise HTTPException(
            status_code=409,
            detail="Temporary accounts are removed by their account manager or expiry policy.",
        )
    if check_self_deletion and not get_user_group_setting_value(
        user_id, "users", "allow_self_deletion", db
    ):
        raise HTTPException(status_code=409, detail="Self-deletion is not allowed.")

    deletion_policy = (
        {
            "mode": "delete_instantly",
            "effect": "erasure",
            "restorable": False,
            "retention_days": None,
            "purge_scheduled_at": None,
        }
        if force_hard_delete
        else get_user_deletion_policy(db)
    )

    user_role = getattr(user, "role", None)
    if is_owner_role(user_role):
        # Reject before backup or authentication cleanup so a forbidden owner
        # deletion cannot cause partial, destructive side effects.
        raise HTTPException(status_code=409, detail="Cannot delete the owner account.")
    if is_admin_role(user_role) and not allow_administrative_target:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete an administrator account.",
        )
    # Resolve log policy before changing account state. Immediate audit
    # retention needs its main-database privacy fence in the same transaction
    # as a soft deletion; otherwise a crash after the account commit could
    # leave a queued event deliverable with no durable follow-up.
    auth_retention_policy = get_auth_log_user_deletion_retention_policy(db)
    audit_retention_policy = get_audit_log_user_deletion_retention_policy(db)
    # Handle user deletion based on mode
    if deletion_policy["effect"] == "erasure":
        # Hard delete immediately
        hard_delete_user(
            db,
            user_id,
            allow_administrative_target=allow_administrative_target,
        )
    else:
        # Soft delete with optional scheduled permanent deletion
        scheduled_for = deletion_policy["purge_scheduled_at"]
        deleted_user = soft_delete_user(
            db,
            user_id,
            scheduled_for=scheduled_for,
            allow_administrative_target=allow_administrative_target,
            commit=False,
        )
        deletion_policy["purge_scheduled_at"] = deleted_user.deletion_scheduled_for
        # A soft delete preserves the user row, so remove its authentication
        # rows explicitly and revoke any cached sessions. The shared helper
        # performs a bulk DELETE without loading encrypted token columns; this
        # is essential when stale ciphertext cannot be decrypted with the
        # current key.
        delete_authentication_all(
            db,
            user_id=user_id,
            commit=False,
            revoke_cached=False,
        )
        invalidate_user_password_reset_tokens(db, user_id, commit=False)
        delete_user_transient_auth_state(db, user_id, commit=False)
        from app.email.models import (
            PendingEmailChange,
            TrustedDeviceNotification,
            cancel_user_email,
        )
        from app.email.service import enqueue_security_event

        cancel_user_email(db, user_id, commit=False)
        db.query(PendingEmailChange).filter(
            PendingEmailChange.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(TrustedDeviceNotification).filter(
            TrustedDeviceNotification.user_id == user_id
        ).delete(synchronize_session=False)
        if audit_retention_policy.get("mode") == "delete_instantly" or bool(
            audit_retention_policy.get("delete_immediately")
        ):
            from app.workers.models import erase_user_audit_event_state

            erase_user_audit_event_state(
                db,
                user_id=user_id,
                commit=False,
            )
            from app.workers.events import enqueue_audit_erasure

            enqueue_audit_erasure(
                db,
                user_id=user_id,
                boundary_id=deleted_user.deleted_at.isoformat(),
                commit=False,
            )
        try:
            enqueue_security_event(
                db,
                user=user,
                event_type=(
                    "account_deletion_scheduled"
                    if deletion_policy["effect"] == "scheduled_deletion"
                    else "account_deactivated"
                ),
                source_id=(
                    f"soft-delete:{deleted_user.id}:"
                    f"{deleted_user.deleted_at.isoformat()}"
                ),
                purge_at=(
                    deletion_policy["purge_scheduled_at"].isoformat()
                    if deletion_policy.get("purge_scheduled_at")
                    else None
                ),
                priority=0,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        revoke_user_sessions(user_id)

    _apply_auth_log_retention(db_log, user_id, auth_retention_policy)
    _apply_audit_log_retention(
        db_log,
        user_id,
        audit_retention_policy,
        main_db=db,
    )

    return {"status": "success", "account_deletion": deletion_policy}


def restore_deleted_user(db, db_log, user_id: str):
    """
    Restore a soft-deleted user, allowing them to log in again.
    Also cancels any pending auth log deletions.
    """
    orm_restore_user(db, user_id)
    cancel_auth_log_deletions_for_user(db_log, user_id)
    cancel_audit_log_deletions_for_user(db_log, user_id)
    return {"status": "success"}


# -------------------
# Get User Settings Screen
# -------------------
def get_effective_two_factor_forced(_user_id, db):
    """Return whether the global 2FA policy forces enrollment."""
    enabled = coerce_bool(
        get_value_by_page_and_key("login_general", "enable_2fa", db),
        default=True,
    )
    forced = coerce_bool(
        get_value_by_page_and_key("login_general", "force_2fa", db),
        default=False,
    )
    return enabled and forced


def get_effective_two_factor_enabled(_user_id, db):
    """Return whether the global 2FA policy makes 2FA available."""
    return coerce_bool(
        get_value_by_page_and_key("login_general", "enable_2fa", db),
        default=True,
    )


# -------------------
# Set User Last Model
# -------------------
def set_user_last_model(user_id: str, db, model_id: str):
    """Update the user's last used model after validating access.

    The provided model_id must refer to an active model that the user is
    allowed to access. Admins are allowed to use any active model.
    """
    # Validate access before persisting
    ensure_user_access_to_model(user_id, model_id, db)

    change_user_last_model(user_id, model_id, db)
    return {"status": "success"}


def update_user_pinned_models(user_id: str, db, pinned_models: list[str] | None):
    """Persist the user's pinned model preferences."""

    if pinned_models is None:
        update_user_settings_bulk(
            user_id,
            {"chat": {"pinned_models": [], "pinned_models_customized": False}},
            db,
        )
        return {
            "status": "success",
            "pinned_models": get_effective_pinned_model_ids_for_user(user_id, db),
            "pinned_models_customized": False,
        }

    requested = sanitize_pinned_model_ids(pinned_models)

    try:
        from app.llm.utils import list_user_models

        visible_model_ids = {
            str(item.get("model_id")).strip()
            for item in list_user_models(db, user_id)
            if isinstance(item, dict) and str(item.get("model_id") or "").strip()
        }
    except Exception:
        logger.exception("Failed to validate pinned models for user %s", user_id)
        visible_model_ids = set(requested)

    sanitized = [model_id for model_id in requested if model_id in visible_model_ids]

    update_user_settings_bulk(
        user_id,
        {"chat": {"pinned_models": sanitized, "pinned_models_customized": True}},
        db,
    )
    return {
        "status": "success",
        "pinned_models": sanitized,
        "pinned_models_customized": True,
    }


def update_sidebar_button_visibility(user_id: str, db, button_visibility: dict):
    """Update the user's sidebar button visibility preferences."""

    # Validate button_visibility is a dict
    if not isinstance(button_visibility, dict):
        button_visibility = {}

    # Get current settings to merge with
    current_settings = get_user_settings(user_id, db)
    current_visibility = current_settings.get("chat", {}).get(
        "sidebar_button_visibility", {}
    )

    # Validate the button visibility dict
    valid_buttons = {
        "create_chat",
        "search_chats",
        "workspace",
        "automations",
        "projects",
    }
    filtered_visibility = {}

    # Merge current visibility with new values
    for key in valid_buttons:
        if key in button_visibility:
            filtered_visibility[key] = bool(button_visibility[key])
        elif key in current_visibility:
            filtered_visibility[key] = bool(current_visibility[key])
        else:
            # Default to true if not specified
            filtered_visibility[key] = True

    update_user_settings_bulk(
        user_id,
        {"chat": {"sidebar_button_visibility": filtered_visibility}},
        db,
    )

    return {
        "status": "success",
        "sidebar_button_visibility": filtered_visibility,
    }


# -------------------
# User Settings Init
# -------------------
def user_settings_init(user_id, db):
    # Return information needed for user settings screen init

    user = get_user(db, user_id, None)
    externally_managed = is_externally_managed(user)
    settings = get_user_settings(user_id, db) or {}

    def _page(name: str) -> dict:
        page = settings.get(name) or {}
        return page if isinstance(page, dict) else {}

    security = _page("security")
    general = _page("general")
    appearance = _page("appearance")
    chat = _page("chat")

    allow_change_name = get_user_group_setting_value(
        user_id, "users", "allow_change_name", db
    )
    allow_change_email = get_user_group_setting_value(
        user_id, "users", "allow_change_email", db
    )
    allow_self_deletion = get_user_group_setting_value(
        user_id, "users", "allow_self_deletion", db
    )
    allow_change_password = get_user_group_setting_value(
        user_id, "users", "allow_change_password", db
    )
    if getattr(user, "account_type", "regular") == "temporary":
        allow_change_name = False
        allow_change_email = False
        allow_self_deletion = False
        allow_change_password = False
    if externally_managed:
        # Profile identity and every local authentication factor belong to the
        # upstream organization for this account.
        allow_change_name = False
        allow_change_email = False
        allow_change_password = False
        allow_self_deletion = False
    allow_byok = get_user_group_setting_value(user_id, "chat", "allow_byok", db)
    allow_mcp = get_user_group_setting_value(user_id, "tools_mcp", "enable_mcp", db)
    enable_projects = get_user_group_setting_value(
        user_id, "projects", "enable_projects", db
    )
    enable_automations = get_user_group_setting_value(
        user_id, "automations", "enabled_automations", db
    )
    byok_default_scrape_provider = get_user_group_setting_value(
        user_id, "chat", "byok_default_scrape_provider", db
    )
    byok_default_search_provider = get_user_group_setting_value(
        user_id, "chat", "byok_default_search_provider", db
    )
    byok_title_generation_model_id = get_user_group_setting_value(
        user_id,
        "chat",
        "byok_title_generation_model_id",
        db,
    )
    data_control_allow_user_data = get_user_group_setting_value(
        user_id, "data_controls", "allow_user_data", db
    )
    profile_visibility = _normalize_visibility_value(
        security.get("profile_visibility"), default="private"
    )
    allow_llm_to_access_personal_information = security.get(
        "allow_llm_to_access_personal_information"
    )
    allow_llm_to_access_personal_information_preset = security.get(
        "allow_llm_to_access_personal_information_preset"
    )
    # Use the effective login policy here, not just the per-user settings blob.
    # The security section should be visible whenever 2FA is available at all,
    # while the action buttons need separate user-state flags so an enrolled
    # account does not keep showing the first-time setup button.
    two_factor_authentication_forced = get_effective_two_factor_forced(user_id, db)
    two_factor_authentication_enabled = get_effective_two_factor_enabled(user_id, db)
    two_factor_authentication_setup = coerce_bool(
        get_user_setting_value(user_id, "login_2fa", "enable_2fa", db),
        default=False,
    ) or bool(get_user_setting_value(user_id, "secret", "2fa_secret", db))
    from app.auth.twofa_provider import resolve_user_2fa_provider

    twofa_provider = resolve_user_2fa_provider(user, db)
    byok_statistics_enabled = chat.get("byok_statistics_enabled")
    try:
        from app.llmstats.models import coerce_byok_stats_retention_days

        byok_statistics_retention_days = coerce_byok_stats_retention_days(
            chat.get("byok_statistics_retention_days")
        )
    except Exception:
        byok_statistics_retention_days = 90
    render_assistant_messages_markdown = chat.get("render_assistant_messages_markdown")
    ctrl_enter_to_send = chat.get("ctrl_enter_to_send")
    show_message_nav = chat.get("show_message_nav")
    show_model_settings = chat.get("show_model_settings")
    show_assistant_message_metadata = chat.get("show_assistant_message_metadata")
    personality_preset = str(
        chat.get("personality_preset") or PersonalityPresetEnum.none.value
    )
    personality_custom_instruction = str(
        chat.get("personality_custom_instruction") or ""
    )
    pinned_models = get_effective_pinned_model_ids_for_user(user_id, db)

    language = general.get("language")
    country = general.get("country")
    user_timezone = general.get("timezone")
    location = general.get("location")
    font = appearance.get("font")

    speech_playback_speed = chat.get("speech_playback_speed")
    render_user_messages_markdown = chat.get("render_user_messages_markdown")
    temporary_chat_allowed = bool(
        get_user_group_setting_value(user_id, "chat", "allow_temporary_chat", db)
    )
    always_use_temporary_chat = (
        False if not temporary_chat_allowed else chat.get("always_use_temporary_chat")
    )
    chat_full_width = chat.get("chat_full_width")
    enable_passkeys = coerce_bool(
        get_login_passkey_policy(db).get("enable_passkeys", None),
        default=True,
    )
    if externally_managed:
        two_factor_authentication_forced = False
        two_factor_authentication_enabled = False
        two_factor_authentication_setup = False
        enable_passkeys = False

    # Get social login status
    social_login = _page("social_login")
    needs_password_setup = (
        False if externally_managed else social_login.get("needs_password_setup", False)
    )
    from app.groups.management import managed_groups_for_user

    managed_groups = managed_groups_for_user(db, user)

    return {
        "externally_managed": externally_managed,
        "external_auth_provider": getattr(user, "external_auth_provider", None),
        "account_type": getattr(user, "account_type", "regular"),
        "temporary_expires_at": normalize_utc_datetime(
            getattr(user, "temporary_expires_at", None)
        ),
        "allow_change_name": allow_change_name,
        "allow_change_email": allow_change_email,
        "allow_self_deletion": allow_self_deletion,
        "user_deletion_policy": get_user_deletion_policy(db),
        "allow_change_password": allow_change_password,
        "needs_password_setup": needs_password_setup,
        "profile_visibility": profile_visibility,
        "allow_llm_to_access_personal_information": allow_llm_to_access_personal_information,
        "allow_llm_to_access_personal_information_preset": allow_llm_to_access_personal_information_preset,
        "two_factor_authentication_enabled": two_factor_authentication_enabled,
        "two_factor_authentication_setup": two_factor_authentication_setup,
        "two_factor_authentication_forced": two_factor_authentication_forced,
        "twofa_provider": twofa_provider,
        "language": language,
        "country": country,
        "timezone": user_timezone,
        "location": location,
        "font": font,
        "speech_playback_speed": speech_playback_speed,
        "render_user_messages_markdown": render_user_messages_markdown,
        "ctrl_enter_to_send": ctrl_enter_to_send,
        "always_use_temporary_chat": always_use_temporary_chat,
        "temporary_chat_allowed": temporary_chat_allowed,
        "chat_full_width": chat_full_width,
        "enable_passkeys": enable_passkeys,
        "byok_statistics_enabled": bool(byok_statistics_enabled),
        "byok_statistics_retention_days": byok_statistics_retention_days,
        "render_assistant_messages_markdown": render_assistant_messages_markdown,
        "allow_byok": bool(allow_byok),
        "allow_mcp": bool(allow_mcp),
        "enable_projects": coerce_bool(enable_projects, default=False),
        "enable_automations": coerce_bool(enable_automations, default=False),
        "byok_title_generation_model_id": byok_title_generation_model_id or "",
        "byok_default_scrape_provider": byok_default_scrape_provider or "",
        "byok_default_search_provider": byok_default_search_provider or "",
        "show_message_nav": show_message_nav,
        "show_model_settings": show_model_settings,
        "show_assistant_message_metadata": show_assistant_message_metadata,
        "personality_preset": personality_preset,
        "personality_custom_instruction": personality_custom_instruction,
        "pinned_models": pinned_models if isinstance(pinned_models, list) else [],
        "data_controls": {
            "allow_user_data": data_control_allow_user_data,
        },
        "managed_groups_available": bool(managed_groups),
        "managed_group_count": len(managed_groups),
        "chat": chat,
    }


# Compatibility façade -----------------------------------------------------
# Routers, extensions, and older integrations may continue importing from
# ``app.users.utils``. Implementations live in focused modules, while these
# explicit re-exports keep that stable import surface discoverable.
from app.users.data_export import (
    ADMIN_USER_EXPORT_VERSION,
    AUTHENTICATION_EXPORT_FIELDS,
    AUTHENTICATION_EXPORT_SECRET_FIELDS,
    EXPORT_ONLY_USER_DATA_SECTIONS,
    SKIPPED_SECTION_SCAN_NODE_LIMIT,
    USER_ARCHIVE_AUTH_SETTING_PAGES,
    USER_ARCHIVE_PENDING_AUTH_SETTING_PAGES,
    USER_ARCHIVE_PROFILE_DENYLIST,
    USER_ARCHIVE_SOCIAL_IDENTITY_SETTING_KEYS,
    USER_CONNECTION_EXPORT_SECRET_FIELDS,
    USER_DATA_EXPORT_QUERY_BATCH_SIZE,
    USER_DATA_EXPORT_SPOOL_THRESHOLD_BYTES,
    USER_DATA_EXPORT_TYPE,
    USER_DATA_EXPORT_VERSION,
    USER_DATA_INLINE_CONTENT_CHUNK_SIZE,
    USER_DATA_INLINE_CONTENT_KEY,
    _agent_asset_materialized_path,
    _build_user_data_export_core,
    _build_user_data_export_coverage,
    _chat_export_payload,
    _decode_export_content,
    _export_agent_asset_entry,
    _export_skill_files,
    _export_slide_presentation_artifacts,
    _export_user_activity_logs,
    _export_user_agents,
    _export_user_chats,
    _export_user_connections,
    _export_user_file_folders,
    _export_user_mcp_servers,
    _export_user_memories,
    _export_user_model_setting_presets,
    _export_user_notes,
    _export_user_prompts,
    _export_user_skills,
    _export_user_slide_presentations,
    _export_user_todos,
    _export_user_usage_stats,
    _file_content_base64,
    _get_skipped_export_only_sections,
    _iter_file_content_base64_chunks,
    _iter_query_rows,
    _json_dumps,
    _materialize_presentation_artifact_path,
    _model_as_dict,
    _normalize_export_relative_path,
    _normalize_import_email,
    _normalize_uuid,
    _presentation_artifact_relative_paths,
    _query_user_agent_assets,
    _query_user_agent_subscriptions,
    _query_user_agents,
    _query_user_authentication_records,
    _query_user_automations,
    _query_user_feedback,
    _query_user_file_folder_subscriptions,
    _query_user_file_folders,
    _query_user_projects,
    _query_user_skill_subscriptions,
    _query_user_skills,
    _query_user_slide_presentations,
    _read_presentation_artifact_bytes,
    _require_imported_user_auth_reset,
    _resolve_preferred_user_id,
    _safe_child_path,
    _safe_parse_env_int,
    _sanitize_user_archive_settings,
    _sanitize_user_profile_export,
    _sanitize_user_profile_for_archive,
    _section_has_import_data,
    _serialize_authentication_record,
    _serialize_authentication_records,
    _serialize_date,
    _serialize_datetime,
    _serialize_decimal,
    _serialize_models,
    _serialize_query_models,
    _serialize_shared_file_folder_subscription_for_export,
    _serialize_user_connection_record,
    _share_id_for_folder_subscription,
    _stream_agent_asset_entry_json,
    _stream_authentication_records_json_array,
    _stream_chat_export_json,
    _stream_chat_messages_json_array,
    _stream_json_array_items,
    _stream_json_object_fields,
    _stream_json_object_with_base64_field,
    _stream_llm_generation_stats_export_json,
    _stream_model_query_json_array,
    _stream_model_rows_json_array,
    _stream_shared_prompt_subscriptions_json_array,
    _stream_skill_file_entry_json,
    _stream_slide_presentation_artifacts_json_array,
    _stream_slide_presentation_json,
    _stream_todo_list_export_json,
    _stream_tool_call_stats_export_json,
    _stream_user_activity_logs_json,
    _stream_user_agent_assets_json_array,
    _stream_user_chats_json_array,
    _stream_user_connections_json_array,
    _stream_user_file_folder_subscriptions_json_array,
    _stream_user_model_setting_presets_json_array,
    _stream_user_notes_json,
    _stream_user_prompts_json_array,
    _stream_user_skill_files_json_array,
    _stream_user_slide_presentations_json_array,
    _stream_user_todos_json_array,
    _stream_user_usage_stats_json,
    _strip_nulls,
    _user_has_activity_logs,
)
from app.users.profile_pictures import (
    CUSTOM_PROFILE_PICTURE_DIR,
    CUSTOM_PROFILE_PICTURE_MAX_BYTES,
    CUSTOM_PROFILE_PICTURE_MAX_SIZE_MB,
    DEFAULT_ALLOWED_IMAGE_EXTENSIONS,
    OAUTH_PROFILE_PICTURE_DIR,
    OAUTH_PROFILE_PICTURE_MAX_BYTES,
    PROFILE_PICTURE_FORMAT_TO_EXTENSION,
    PROFILE_PICTURE_MAX_BYTES,
    PROFILE_PICTURE_MAX_DIMENSION,
    PROFILE_PICTURE_NO_STORE_HEADERS,
    SECONDARY_EXTENSION_DENYLIST,
    _list_profile_picture_files,
    _profile_picture_dirs,
    _remove_profile_picture_files,
    _resolve_profile_picture_file,
    _store_profile_picture_bytes,
    _validate_and_prepare_profile_picture_bytes,
    clear_oauth_profile_picture,
    delete_profile_picture,
    get_profile_picture,
    get_profile_picture_status,
    save_oauth_profile_picture,
    upload_profile_picture,
)
from app.users.data_import import (
    AdminUserImportOptions,
    ImportUserPayload,
    ImportUserProfile,
    PASSWORD_STATE_IMPORT_SETTINGS_DENYLIST,
    SELF_IMPORT_PORTABLE_SECURITY_SETTING_KEYS,
    SELF_IMPORT_PORTABLE_SETTING_PAGES,
    SELF_IMPORT_PORTABLE_STATE_SETTING_KEYS,
    SELF_USER_ARCHIVE_RESTORE_POLICY,
    UserArchiveRestorePolicy,
    UserArchiveRestoreResult,
    _bulk_insert_agent_assets,
    _bulk_insert_agents,
    _bulk_insert_authentication,
    _bulk_insert_automations,
    _bulk_insert_chat_data,
    _bulk_insert_connection_oauth_states,
    _bulk_insert_file_folders,
    _bulk_insert_files,
    _bulk_insert_model_setting_presets,
    _bulk_insert_projects,
    _bulk_insert_prompts,
    _bulk_insert_shared_agent_subscriptions,
    _bulk_insert_shared_file_folder_subscriptions,
    _bulk_insert_shared_prompt_subscriptions,
    _bulk_insert_shared_skill_subscriptions,
    _bulk_insert_skills,
    _bulk_insert_slide_presentations,
    _bulk_insert_todos,
    _bulk_insert_user_connections,
    _bulk_insert_user_mcp_servers,
    _bulk_merge_serialized_models,
    _coerce_serialized_model_value,
    _create_user_record,
    _delete_slide_presentation_artifact_paths,
    _delete_uploaded_file_references,
    _file_import_id_map,
    _hydrate_user_settings,
    _import_user_archive_inline_files,
    _import_user_memories_archive,
    _import_user_notes_archive,
    _merge_user_record,
    _normalize_todo_sort_order,
    _parse_admin_user_import_options,
    _prepare_new_serialized_model_payload,
    _prepare_serialized_model_payload,
    _primary_key_column_keys,
    _rebuild_automation_payload,
    _rebuild_todo_list_payload,
    _rebuild_todo_payload,
    _remap_imported_project_file_references,
    _resolve_shared_file_folder_subscription_target,
    _restore_user_archive_sections,
    _sanitize_existing_user_import_settings,
    _sanitize_self_user_import_settings,
    _upload_inline_file_bytes,
    _validate_user_archive_payload,
    _write_slide_presentation_artifacts,
    import_user_data_for_existing_user,
    import_user_from_export,
    import_users_admin,
    reconnect_imported_user_archive_file_references,
)
from app.users.admin_management import (
    _admin_user_matches_search,
    _admin_user_summary,
    create_user_via_admin,
    get_user_list,
    get_user_list_page,
    update_user_location,
)
