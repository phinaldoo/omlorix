"""User archive validation, import, and restore helpers.

Restore operations are kept separate from export generation because they have
different trust boundaries, transaction behavior, and failure handling.
"""

from datetime import datetime, timezone
from fastapi import HTTPException, status
from pathlib import Path, PurePosixPath
from sqlalchemy.inspection import inspect as sqla_inspect
import logging
import json
import uuid
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, Iterable, List
from pydantic import BaseModel, ValidationError, constr

from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.timezones import normalize_timezone_identifier
from sqlalchemy.orm.attributes import flag_modified
from app.users.models import (
    User,
    build_user_email_match,
    create_user,
    get_user,
    user_exists_by_email,
)
from app.users.roles import is_admin_role, is_owner_role
from app.auth.models import (
    Authentication,
    delete_authentication_all,
    delete_user_transient_auth_state,
)
from app.auth.session_store import revoke_user_sessions
from app.settings.utils import (
    get_value_by_page_and_key,
)

from app.chats.models import ChatMessages
from app.files.models import Files
from app.memories.schemas import MemoryExportPayload
from app.memories.service import MemoryScope, import_memory_export
from app.notes.models import import_user_notes
from app.projects.models import Project
from app.automations.models import Automation
from app.automations.schedule import compute_next_schedule_state
from app.todos.models import DEFAULT_TODO_SORT_ORDER, TodoLists, Todos
from app.auth.utils import hash_password
from app.utils.icon_security import sanitize_icon_input

from app.users.data_export import (
    ADMIN_USER_EXPORT_VERSION,
    USER_DATA_EXPORT_TYPE,
    USER_DATA_EXPORT_VERSION,
    USER_DATA_INLINE_CONTENT_KEY,
    _decode_export_content,
    _get_skipped_export_only_sections,
    _normalize_export_relative_path,
    _normalize_import_email,
    _normalize_uuid,
    _require_imported_user_auth_reset,
    _resolve_preferred_user_id,
    _safe_child_path,
    _sanitize_user_archive_settings,
    _sanitize_user_profile_for_archive,
    _share_id_for_folder_subscription,
)

logger = logging.getLogger(__name__)


def _call_legacy_user_utility(name: str, *args, **kwargs):
    """Resolve a shared account helper lazily without creating an import cycle."""
    from app.users import utils as user_utils

    return getattr(user_utils, name)(*args, **kwargs)


def _assert_password_policy(*args, **kwargs):
    return _call_legacy_user_utility("_assert_password_policy", *args, **kwargs)


def _merge_and_sync_user_settings(*args, **kwargs):
    return _call_legacy_user_utility("_merge_and_sync_user_settings", *args, **kwargs)


def _normalized_timestamp(*args, **kwargs):
    return _call_legacy_user_utility("_normalized_timestamp", *args, **kwargs)


def _safe_datetime(*args, **kwargs):
    return _call_legacy_user_utility("_safe_datetime", *args, **kwargs)


def _set_password_state_flags(*args, **kwargs):
    return _call_legacy_user_utility("_set_password_state_flags", *args, **kwargs)


class ImportUserProfile(BaseModel):
    email: constr(strip_whitespace=True, to_lower=True, min_length=3)
    first_name: constr(strip_whitespace=True, min_length=1)
    last_name: constr(strip_whitespace=True, min_length=1)
    group_id: str | None = None
    role: str | None = None
    settings: Dict[str, Any] | None = None
    custom_profile_picture: bool | None = None
    is_active: bool | None = None


class ImportUserPayload(BaseModel):
    export_type: str
    export_version: float | int | str
    user: Dict[str, Any]
    settings: Dict[str, Any] | None = None
    group: Dict[str, Any] | None = None
    auth: Dict[str, Any] | None = None
    activity_logs: Dict[str, Any] | None = None
    chats: List[Dict[str, Any]] | None = None
    notes: Dict[str, Any] | None = None
    todos: List[Dict[str, Any]] | None = None
    memories: Dict[str, Any] | None = None
    files: List[Dict[str, Any]] | None = None
    file_folders: List[Dict[str, Any]] | None = None
    shared_file_folder_subscriptions: List[Dict[str, Any]] | None = None
    projects: List[Dict[str, Any]] | None = None
    automations: List[Dict[str, Any]] | None = None
    skills: List[Dict[str, Any]] | None = None
    skill_files: List[Dict[str, Any]] | None = None
    shared_skill_subscriptions: List[Dict[str, Any]] | None = None
    agents: List[Dict[str, Any]] | None = None
    agent_assets: List[Dict[str, Any]] | None = None
    shared_agent_subscriptions: List[Dict[str, Any]] | None = None
    prompts: List[Dict[str, Any]] | None = None
    shared_prompt_subscriptions: List[Dict[str, Any]] | None = None
    user_connections: List[Dict[str, Any]] | None = None
    connection_oauth_states: List[Dict[str, Any]] | None = None
    mcp_servers: List[Dict[str, Any]] | None = None
    model_setting_presets: List[Dict[str, Any]] | None = None
    slide_presentations: List[Dict[str, Any]] | None = None


class AdminUserImportOptions(BaseModel):
    default_password: str
    force_password_change: bool = True


PASSWORD_STATE_IMPORT_SETTINGS_DENYLIST = (
    ("security", "has_to_change_password"),
    ("social_login", "needs_password_setup"),
    ("social_login", "pending_auth_code"),
    ("social_login", "pending_auth_code_expires"),
    ("sso_login", "needs_password_setup"),
    ("sso_login", "pending_auth_code"),
    ("sso_login", "pending_auth_code_expires"),
)

SELF_IMPORT_PORTABLE_SETTING_PAGES = {
    "general",
    "appearance",
    "chat",
}

SELF_IMPORT_PORTABLE_SECURITY_SETTING_KEYS = {
    "profile_visibility",
    "allow_llm_to_access_personal_information_preset",
    "allow_llm_to_access_personal_information",
}

SELF_IMPORT_PORTABLE_STATE_SETTING_KEYS = {
    "welcome_card_dismissed",
}


def _sanitize_existing_user_import_settings(
    imported_settings: Dict[str, Any] | None,
) -> Dict[str, Any]:
    sanitized_settings = _sanitize_user_archive_settings(imported_settings)
    for section_name, setting_name in PASSWORD_STATE_IMPORT_SETTINGS_DENYLIST:
        if section_name not in sanitized_settings:
            continue
        section_settings = sanitized_settings.get(section_name)
        if isinstance(section_settings, dict):
            section_settings.pop(setting_name, None)
        else:
            sanitized_settings.pop(section_name, None)
    return sanitized_settings


def _sanitize_self_user_import_settings(
    imported_settings: Dict[str, Any] | None,
) -> Dict[str, Any]:
    sanitized_settings = _sanitize_existing_user_import_settings(imported_settings)
    portable_settings: Dict[str, Any] = {}

    for page_name in SELF_IMPORT_PORTABLE_SETTING_PAGES:
        page_settings = sanitized_settings.get(page_name)
        if isinstance(page_settings, dict):
            portable_settings[page_name] = deepcopy(page_settings)

    security_settings = sanitized_settings.get("security")
    if isinstance(security_settings, dict):
        portable_security_settings = {}
        for key_name in SELF_IMPORT_PORTABLE_SECURITY_SETTING_KEYS:
            if key_name in security_settings:
                portable_security_settings[key_name] = deepcopy(
                    security_settings[key_name]
                )
        if portable_security_settings:
            portable_settings["security"] = portable_security_settings

    state_settings = sanitized_settings.get("states")
    if isinstance(state_settings, dict):
        portable_state_settings = {}
        for key_name in SELF_IMPORT_PORTABLE_STATE_SETTING_KEYS:
            if key_name in state_settings:
                portable_state_settings[key_name] = deepcopy(state_settings[key_name])
        if portable_state_settings:
            portable_settings["states"] = portable_state_settings

    return portable_settings


def _hydrate_user_settings(
    imported_settings: Dict[str, Any] | None, *, require_auth_reset: bool = False
) -> Dict[str, Any]:
    sanitized_settings = _sanitize_user_archive_settings(imported_settings)
    if not sanitized_settings:
        hydrated = deepcopy(DEFAULT_USER_SETTINGS)
    else:
        hydrated = _merge_and_sync_user_settings(
            DEFAULT_USER_SETTINGS, sanitized_settings
        )

    if require_auth_reset:
        hydrated = _require_imported_user_auth_reset(hydrated)
    return hydrated


def _create_user_record(
    db,
    profile_data: Dict[str, Any],
    group_id: str,
    hashed_password: str,
    force_password_change: bool = True,
    preferred_user_id: str | None = None,
) -> User:
    email = profile_data.get("email")
    if user_exists_by_email(db, email):
        raise HTTPException(status_code=409, detail="Email already exists")

    first_name = profile_data.get("first_name") or "Imported"
    last_name = profile_data.get("last_name") or "User"
    role = "user"
    explicit_user_id = _normalize_uuid(preferred_user_id)
    if explicit_user_id and db.query(User).filter(User.id == explicit_user_id).first():
        explicit_user_id = None

    created_user = create_user(
        db,
        email,
        hashed_password,
        first_name,
        last_name,
        role,
        group_id,
        user_id=explicit_user_id,
    )

    imported_settings = _sanitize_existing_user_import_settings(
        profile_data.get("settings")
    )
    merged_settings = _hydrate_user_settings(imported_settings)
    security_settings = merged_settings.setdefault("security", {})
    if isinstance(security_settings, dict):
        security_settings["has_to_change_password"] = bool(force_password_change)
    created_user.settings = merged_settings
    flag_modified(created_user, "settings")

    created_user.custom_profile_picture = False
    created_user.is_active = True
    created_user.last_model = profile_data.get("last_model") or created_user.last_model
    db.commit()
    db.refresh(created_user)
    return created_user


def _merge_user_record(
    db,
    existing_user: User,
    profile_data: Dict[str, Any],
    warnings: List[Dict[str, Any]] | None = None,
    new_password_hash: str | None = None,
    force_password_change: bool | None = None,
) -> User:
    first_name = profile_data.get("first_name")
    if isinstance(first_name, str) and first_name.strip():
        existing_user.first_name = first_name.strip()

    last_name = profile_data.get("last_name")
    if isinstance(last_name, str) and last_name.strip():
        existing_user.last_name = last_name.strip()

    imported_settings = _sanitize_existing_user_import_settings(
        profile_data.get("settings")
    )
    if isinstance(imported_settings, dict):
        base_settings = (
            existing_user.settings
            if isinstance(existing_user.settings, dict)
            else deepcopy(DEFAULT_USER_SETTINGS)
        )
        existing_user.settings = _merge_and_sync_user_settings(
            base_settings, imported_settings
        )
        flag_modified(existing_user, "settings")

    last_model = profile_data.get("last_model")
    if last_model is not None:
        if isinstance(last_model, str):
            existing_user.last_model = last_model.strip() or None
        else:
            if isinstance(last_model, (dict, list, tuple, set)):
                try:
                    normalized_last_model = json.dumps(
                        last_model, ensure_ascii=True, default=str
                    )
                except Exception:
                    normalized_last_model = str(last_model)
            else:
                normalized_last_model = str(last_model)
            normalized_last_model = normalized_last_model.strip()
            existing_user.last_model = normalized_last_model or None

    if isinstance(new_password_hash, str) and new_password_hash:
        existing_user.hashed_password = new_password_hash
        _set_password_state_flags(
            existing_user,
            db,
            has_to_change_password=bool(force_password_change),
        )
        delete_authentication_all(
            db, user_id=existing_user.id, commit=False, revoke_cached=False
        )
        delete_user_transient_auth_state(db, existing_user.id, commit=False)
    db.commit()
    db.refresh(existing_user)
    if isinstance(new_password_hash, str) and new_password_hash:
        revoke_user_sessions(existing_user.id)
    return existing_user


def _bulk_insert_chat_data(
    db,
    user_id: str,
    chats: List[Dict[str, Any]],
    *,
    project_id_map: Dict[str, str] | None = None,
    file_id_map: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Restore canonical chats through the complete chat import primitive.

    The shared primitive restores messages and Deep Research artifacts and
    safely rewrites their embedded identifiers. Returning the chat map lets
    later user-owned sections retain their parent link.
    """
    from app.chats.io import _import_single_chat

    chat_id_map: Dict[str, str] = {}
    for chat_export in chats:
        if not isinstance(chat_export, dict):
            continue
        source_chat_id = str(chat_export.get("id") or "").strip()
        entry = {
            "chat": {
                key: value
                for key, value in chat_export.items()
                if key not in {"messages", "deep_research_runs"}
            },
            "messages": chat_export.get("messages") or [],
            "deep_research_runs": chat_export.get("deep_research_runs") or [],
        }
        created = _import_single_chat(
            user_id,
            entry,
            db,
            project_id_map=project_id_map,
            file_id_map=file_id_map,
        )
        if source_chat_id and created.get("chat_id"):
            chat_id_map[source_chat_id] = str(created["chat_id"])
    db.commit()
    return chat_id_map


def _bulk_insert_files(db, user_id: str, files: List[Dict[str, Any]]):
    for file_export in files:
        if not isinstance(file_export, dict):
            continue
        file_payload = _prepare_new_serialized_model_payload(
            Files,
            file_export,
            overrides={
                "user_id": user_id,
                "project_id": None,
            },
        )
        file_payload.update(
            {
                "user_id": user_id,
                "project_id": None,
            }
        )
        file_name = str(file_payload.get("file_name") or "").strip()
        storage_provider = (
            str(file_payload.get("storage_provider") or "").strip().lower() or "local"
        )
        storage_key = str(file_payload.get("storage_key") or "").strip()
        if not storage_key and file_name:
            storage_key = f"{user_id}/{file_name}"
        file_payload["storage_provider"] = storage_provider
        file_payload["storage_key"] = storage_key or f"{user_id}/unknown-file"
        db.add(Files(**file_payload))
    db.commit()


def _bulk_insert_projects(
    db,
    user_id: str,
    projects: List[Dict[str, Any]],
    *,
    file_id_map: Dict[str, str] | None = None,
):
    """Restore projects and rewrite references to imported user files."""
    from app.chats.io import _remap_portable_archive_references

    id_map: Dict[str, str] = {}
    for project_export in projects:
        if not isinstance(project_export, dict):
            continue
        original_id = project_export.get("id")
        project_export = _remap_portable_archive_references(
            project_export,
            file_id_map or {},
        )
        normalized_id = _normalize_uuid(original_id)
        preferred_id = (
            normalized_id
            if normalized_id
            and not db.query(Project).filter(Project.id == normalized_id).first()
            else None
        )
        project_payload = _prepare_new_serialized_model_payload(
            Project,
            project_export,
            overrides={
                "user_id": user_id,
                # Project share links are bearer credentials and belong to the
                # source instance. Every archive restore creates a private copy.
                "link_share_id": None,
                "link_share_password_hash": None,
                "link_share_expires_at": None,
                "link_share_created_at": None,
            },
            primary_key_value=preferred_id,
        )
        project = Project(**project_payload)
        db.add(project)
        db.flush()
        if original_id:
            id_map[str(original_id)] = project.id
    db.commit()
    return id_map


def _remap_imported_project_file_references(
    db,
    project_id_map: Dict[str, str],
    file_id_map: Dict[str, str],
) -> None:
    """Reconnect project attachment arrays after inline files receive new IDs."""
    if not project_id_map or not file_id_map:
        return
    from app.chats.io import _remap_portable_archive_references

    for project_id in project_id_map.values():
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            continue
        for field_name in ("images", "videos", "audios", "documents"):
            current_value = getattr(project, field_name, None)
            setattr(
                project,
                field_name,
                _remap_portable_archive_references(current_value, file_id_map),
            )
    db.commit()


def reconnect_imported_user_archive_file_references(
    db,
    *,
    project_id_map: Dict[str, str],
    chat_id_map: Dict[str, str],
    file_id_map: Dict[str, str],
) -> None:
    """Reconnect delayed nested-file references in an admin ZIP restore."""
    if not file_id_map:
        return
    from app.chats.io import _remap_portable_archive_references

    _remap_imported_project_file_references(db, project_id_map, file_id_map)
    imported_chat_ids = [chat_id for chat_id in chat_id_map.values() if chat_id]
    if not imported_chat_ids:
        return
    chunk_size = 500
    for index in range(0, len(imported_chat_ids), chunk_size):
        chat_id_chunk = imported_chat_ids[index : index + chunk_size]
        messages = (
            db.query(ChatMessages)
            .filter(ChatMessages.chat_id.in_(chat_id_chunk))
            .yield_per(chunk_size)
        )
        for message in messages:
            for field_name in ("content", "thinking", "generation"):
                current_value = getattr(message, field_name, None)
                remapped_value = _remap_portable_archive_references(
                    current_value, file_id_map
                )
                # Avoid marking unchanged rows dirty. Large restores commonly
                # contain messages without file references, and updating them
                # would add needless write load and audit noise.
                if remapped_value != current_value:
                    setattr(message, field_name, remapped_value)
        db.commit()


def _normalize_todo_sort_order(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return deepcopy(DEFAULT_TODO_SORT_ORDER)

    allowed_keys = {
        entry["key"]
        for entry in DEFAULT_TODO_SORT_ORDER
        if isinstance(entry, dict) and entry.get("key")
    }
    normalized: List[Dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        direction = str(entry.get("direction") or "").strip().lower()
        if key not in allowed_keys or direction not in {"asc", "desc"}:
            continue
        normalized.append({"key": key, "direction": direction})

    return normalized or deepcopy(DEFAULT_TODO_SORT_ORDER)


def _rebuild_todo_list_payload(
    todo_list_export: Dict[str, Any], *, user_id: str
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    order_value = todo_list_export.get("order")
    if not isinstance(order_value, int):
        order_value = 0

    title = str(todo_list_export.get("title") or "").strip()[:255]
    if not title:
        title = "Imported list"

    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "order": order_value,
        "title": title,
        "description": str(todo_list_export.get("description") or "").strip(),
        "icon": sanitize_icon_input(todo_list_export.get("icon"), fallback="checklist"),
        "clone_share_id": None,
        "live_share_id": None,
        "collaborate_share_id": None,
        "sort_order": _normalize_todo_sort_order(todo_list_export.get("sort_order")),
        "created_at": _safe_datetime(todo_list_export.get("created_at"), now),
        "updated_at": _safe_datetime(todo_list_export.get("updated_at"), now),
    }


def _rebuild_todo_payload(
    todo_export: Dict[str, Any], *, todo_list_id: str
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    order_value = todo_export.get("order")
    if not isinstance(order_value, int):
        order_value = 0

    priority_value = todo_export.get("priority")
    if not isinstance(priority_value, int):
        priority_value = 0

    is_done = bool(todo_export.get("is_done", False))
    completed_at = (
        _normalized_timestamp(todo_export.get("completed_at")) if is_done else None
    )

    content = str(todo_export.get("content") or "").strip()
    if not content:
        content = "Imported todo"

    notes_value = todo_export.get("notes")
    notes = None
    if isinstance(notes_value, str):
        notes = notes_value.strip() or None

    return {
        "id": str(uuid.uuid4()),
        "todo_list": todo_list_id,
        "order": order_value,
        "content": content,
        "notes": notes,
        "priority": priority_value,
        "due_at": _normalized_timestamp(todo_export.get("due_at")),
        "is_done": is_done,
        "is_marked": bool(todo_export.get("is_marked", False)),
        "completed_at": completed_at,
        "created_at": _safe_datetime(todo_export.get("created_at"), now),
        "updated_at": _safe_datetime(todo_export.get("updated_at"), now),
    }


def _bulk_insert_todos(db, user_id: str, todo_lists: List[Dict[str, Any]]) -> None:
    for todo_list_export in todo_lists:
        if not isinstance(todo_list_export, dict):
            continue

        todo_list_payload = _rebuild_todo_list_payload(
            todo_list_export, user_id=user_id
        )
        todo_list = TodoLists(**todo_list_payload)
        db.add(todo_list)

        for todo_export in todo_list_export.get("todos") or []:
            if not isinstance(todo_export, dict):
                continue
            todo_payload = _rebuild_todo_payload(todo_export, todo_list_id=todo_list.id)
            db.add(Todos(**todo_payload))
    db.commit()


def _normalize_automation_mcp_import_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for server_id in value:
        candidate = str(server_id or "").strip()
        if candidate and candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _rebuild_automation_payload(
    automation_export: Dict[str, Any],
    *,
    mcp_server_id_map: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    automation_id = _normalize_uuid(automation_export.get("id")) or str(uuid.uuid4())

    title = str(automation_export.get("title") or "").strip()[:255]
    if not title:
        title = "Imported automation"

    prompt = str(automation_export.get("prompt") or "").strip()
    if not prompt:
        prompt = "Imported automation prompt was empty."

    model_id = str(automation_export.get("model_id") or "").strip()

    schedule_rules = automation_export.get("schedule_rules")
    if not isinstance(schedule_rules, list):
        schedule_rules = []
    raw_schedule_timezone = automation_export.get("schedule_timezone")
    schedule_timezone = None
    if isinstance(raw_schedule_timezone, str) and raw_schedule_timezone.strip():
        try:
            schedule_timezone = normalize_timezone_identifier(raw_schedule_timezone)
        except ValueError:
            schedule_timezone = None

    note_ids = automation_export.get("note_ids")
    if not isinstance(note_ids, list):
        note_ids = []

    file_ids = automation_export.get("file_ids")
    if not isinstance(file_ids, list):
        file_ids = []

    source_mcp_server_ids = _normalize_automation_mcp_import_ids(
        automation_export.get("mcp_server_ids")
    )
    resolved_mcp_server_ids = [
        (mcp_server_id_map or {}).get(server_id, server_id)
        for server_id in source_mcp_server_ids
    ]

    is_active = bool(automation_export.get("is_active", True))
    schedule_state = (
        compute_next_schedule_state(
            schedule_rules,
            reference_time=now,
            schedule_timezone=schedule_timezone,
        )
        if is_active
        else None
    )

    return {
        "id": automation_id,
        "title": title,
        "icon": str(automation_export.get("icon") or "0")[:10],
        "icon_color": str(automation_export.get("icon_color") or "#FF6B6B")[:20],
        "prompt": prompt,
        "model_id": model_id,
        "schedule_rules": schedule_rules,
        "schedule_timezone": schedule_timezone,
        "skill_id": automation_export.get("skill_id") or None,
        "note_ids": [str(note_id) for note_id in note_ids if note_id],
        "file_ids": [str(file_id) for file_id in file_ids if file_id],
        "mcp_server_ids": resolved_mcp_server_ids,
        "is_active": is_active,
        "last_triggered_at": _normalized_timestamp(
            automation_export.get("last_triggered_at")
        ),
        "next_run_at": schedule_state.run_at if schedule_state else None,
        "next_run_slot": schedule_state.slot if schedule_state else None,
        "scheduler_claimed_at": None,
        "created_at": _safe_datetime(automation_export.get("created_at"), now),
        "last_updated_at": _safe_datetime(
            automation_export.get("last_updated_at"), now
        ),
    }


def _bulk_insert_automations(
    db,
    user_id: str,
    automations: List[Dict[str, Any]],
    *,
    mcp_server_id_map: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    from app.automations.models import _normalize_automation_mcp_server_ids

    warnings: List[Dict[str, Any]] = []
    for automation_export in automations:
        source_mcp_server_ids = _normalize_automation_mcp_import_ids(
            automation_export.get("mcp_server_ids")
        )
        automation_payload = _rebuild_automation_payload(
            automation_export,
            mcp_server_id_map=mcp_server_id_map,
        )
        candidate_mcp_server_ids = automation_payload["mcp_server_ids"]
        try:
            restored_mcp_server_ids = _normalize_automation_mcp_server_ids(
                db,
                user_id,
                automation_payload["model_id"],
                candidate_mcp_server_ids,
                reject_inaccessible=False,
            )
        except HTTPException:
            # Canonical account restore intentionally keeps dormant automation
            # records even when their source model is absent on the target. Its
            # MCP context still needs an explicit, machine-readable warning.
            restored_mcp_server_ids = []

        restored_id_set = set(restored_mcp_server_ids)
        inaccessible_source_ids = [
            source_id
            for source_id, candidate_id in zip(
                source_mcp_server_ids, candidate_mcp_server_ids
            )
            if candidate_id not in restored_id_set
        ]
        if inaccessible_source_ids:
            warnings.append(
                {
                    "section": "automations",
                    "code": "automation_mcp_servers_unavailable",
                    "warning": (
                        "Some selected MCP servers could not be restored for "
                        "this automation."
                    ),
                    "automation_id": automation_payload["id"],
                    "automation_title": automation_payload["title"],
                    "inaccessible_mcp_server_ids": inaccessible_source_ids,
                }
            )
        automation_payload["mcp_server_ids"] = restored_mcp_server_ids
        automation_payload["user_id"] = user_id
        db.add(Automation(**automation_payload))
    db.commit()
    return warnings


def _bulk_insert_authentication(db, user_id: str, auth_records: List[Dict[str, Any]]):
    for auth_export in auth_records:
        if not isinstance(auth_export, dict):
            continue
        payload = _prepare_new_serialized_model_payload(
            Authentication,
            auth_export,
            overrides={"user_id": user_id},
        )
        db.add(Authentication(**payload))
    db.commit()


def _coerce_serialized_model_value(column, value: Any) -> Any:
    if value is None:
        return None

    column_type_name = column.type.__class__.__name__.lower()
    if "datetime" in column_type_name:
        return _normalized_timestamp(value)

    return value


def _prepare_serialized_model_payload(
    model_cls,
    payload: Dict[str, Any],
    *,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    mapper = sqla_inspect(model_cls)
    prepared: Dict[str, Any] = {}
    for column in mapper.columns:
        key = column.key
        if key not in payload:
            continue
        prepared[key] = _coerce_serialized_model_value(column, payload.get(key))

    if overrides:
        prepared.update(overrides)

    return prepared


def _primary_key_column_keys(model_cls) -> list[str]:
    return [column.key for column in sqla_inspect(model_cls).primary_key]


def _prepare_new_serialized_model_payload(
    model_cls,
    payload: Dict[str, Any],
    *,
    overrides: Dict[str, Any] | None = None,
    primary_key_value: str | None = None,
) -> Dict[str, Any]:
    prepared = _prepare_serialized_model_payload(model_cls, payload)
    primary_keys = _primary_key_column_keys(model_cls)
    for key in primary_keys:
        prepared.pop(key, None)

    if len(primary_keys) == 1:
        prepared[primary_keys[0]] = primary_key_value or str(uuid.uuid4())

    if overrides:
        prepared.update(overrides)

    return prepared


def _bulk_merge_serialized_models(
    db,
    model_cls,
    rows: List[Dict[str, Any]],
    *,
    overrides: Dict[str, Any] | None = None,
) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = _prepare_new_serialized_model_payload(
            model_cls, row, overrides=overrides
        )
        db.add(model_cls(**payload))
    db.commit()


def _bulk_insert_prompts(db, user_id: str, prompts: List[Dict[str, Any]]) -> None:
    """Import prompts while remapping owner attribution to the target user."""
    from app.prompts.models import Prompts

    for row in prompts:
        if not isinstance(row, dict):
            continue
        source_owner_id = str(row.get("user_id") or "").strip()
        prompt_id = str(uuid.uuid4())
        prompt_payload = _prepare_new_serialized_model_payload(
            Prompts,
            row,
            overrides={
                "user_id": user_id,
                # Share IDs are bearer credentials for the source prompt.
                # Archive restores always create a private prompt copy.
                "clone_share_id": None,
                "live_share_id": None,
                "collaborate_share_id": None,
            },
            primary_key_value=prompt_id,
        )
        if prompt_payload.get("last_edited_by_user_id") == source_owner_id:
            prompt_payload["last_edited_by_user_id"] = user_id
        prompt_payload.setdefault("revision", 1)
        prompt_payload.setdefault("last_edited_by_user_id", user_id)
        db.add(Prompts(**prompt_payload))
    db.commit()


def _bulk_insert_shared_prompt_subscriptions(
    db, user_id: str, subscriptions: List[Dict[str, Any]]
) -> None:
    from app.prompts.models import SharedPromptSubscription

    _bulk_merge_serialized_models(
        db,
        SharedPromptSubscription,
        subscriptions,
        overrides={"subscriber_id": user_id},
    )


def _bulk_insert_user_connections(
    db, user_id: str, connections: List[Dict[str, Any]]
) -> None:
    from app.connections.models import VALID_CONNECTION_PROVIDERS, UserConnection

    _bulk_merge_serialized_models(
        db,
        UserConnection,
        [
            row
            for row in connections
            if isinstance(row, dict)
            and row.get("provider") in VALID_CONNECTION_PROVIDERS
        ],
        overrides={"user_id": user_id},
    )


def _bulk_insert_connection_oauth_states(
    db, user_id: str, oauth_states: List[Dict[str, Any]]
) -> None:
    from app.connections.models import ConnectionOAuthState, VALID_CONNECTION_PROVIDERS

    _bulk_merge_serialized_models(
        db,
        ConnectionOAuthState,
        [
            row
            for row in oauth_states
            if isinstance(row, dict)
            and row.get("provider") in VALID_CONNECTION_PROVIDERS
        ],
        overrides={"user_id": user_id},
    )


def _bulk_insert_user_mcp_servers(
    db, user_id: str, servers: List[Dict[str, Any]]
) -> Dict[str, str]:
    """Import personal MCP definitions and map source IDs to restored IDs."""
    from app.mcp.models import OWNER_USER, create_mcp_server
    from app.mcp.schemas import CreateMCPServerRequest

    validated_servers: list[tuple[str | None, Any]] = []
    for row in servers:
        if not isinstance(row, dict):
            raise ValueError("Personal MCP server import rows must be objects.")
        source_id = str(row.get("id") or "").strip() or None
        validated_servers.append(
            (
                source_id,
                CreateMCPServerRequest.model_validate(
                    {
                        **{key: value for key, value in row.items() if key != "id"},
                        "owner_type": OWNER_USER,
                        # Secrets are intentionally non-portable. Ignore any injected
                        # headers even if a hand-edited import provides them. Retired
                        # local-process fields are rejected by the request schema.
                        "headers": {},
                    }
                ),
            )
        )

    # Validate the complete section before creating anything. This preserves
    # the importer's section-level all-or-nothing behavior for malformed rows.
    server_id_map: Dict[str, str] = {}
    for source_id, validated in validated_servers:
        restored = create_mcp_server(
            db,
            owner_type=OWNER_USER,
            owner_user_id=user_id,
            managed_connection_id=None,
            command=None,
            args=[],
            env={},
            **validated.model_dump(exclude={"owner_type"}),
        )
        restored_id = str(getattr(restored, "id", "") or "").strip()
        if source_id and restored_id:
            server_id_map[source_id] = restored_id
    return server_id_map


def _bulk_insert_model_setting_presets(
    db, user_id: str, presets: List[Dict[str, Any]]
) -> None:
    from app.llm.models import ModelSettingPresets

    _bulk_merge_serialized_models(
        db,
        ModelSettingPresets,
        presets,
        overrides={"user_id": user_id},
    )


def _bulk_insert_skills(
    db,
    user_id: str,
    skills: List[Dict[str, Any]],
    skill_files: List[Dict[str, Any]] | None = None,
) -> Dict[str, str]:
    from app.skills.models import Skills, _delete_skill_directory, _skill_directory

    skill_id_map: dict[str, str] = {}
    for row in skills:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("id") or "").strip()
        skill_id = str(uuid.uuid4())
        payload = _prepare_new_serialized_model_payload(
            Skills,
            row,
            overrides={"id": skill_id, "user_id": user_id},
        )
        if source_id:
            skill_id_map[source_id] = skill_id
        db.add(Skills(**payload))
    db.commit()

    files_by_skill_id: dict[str, list[dict[str, Any]]] = {}
    for entry in skill_files or []:
        if not isinstance(entry, dict):
            continue
        source_skill_id = str(entry.get("skill_id") or "").strip()
        skill_id = skill_id_map.get(source_skill_id)
        if skill_id:
            files_by_skill_id.setdefault(skill_id, []).append(entry)

    for skill_id, entries in files_by_skill_id.items():
        _delete_skill_directory(user_id, skill_id)
        try:
            skill_dir = _skill_directory(user_id, skill_id)
        except ValueError:
            continue
        skill_dir.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            content = _decode_export_content(entry.get(USER_DATA_INLINE_CONTENT_KEY))
            if content is None:
                continue
            try:
                relative_path = _normalize_export_relative_path(
                    entry.get("relative_path")
                )
                target = _safe_child_path(skill_dir, relative_path)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return skill_id_map


def _bulk_insert_shared_skill_subscriptions(
    db, user_id: str, subscriptions: List[Dict[str, Any]]
) -> None:
    from app.skills.models import SharedSkillSubscription

    _bulk_merge_serialized_models(
        db,
        SharedSkillSubscription,
        subscriptions,
        overrides={"subscriber_id": user_id},
    )


def _bulk_insert_file_folders(
    db,
    user_id: str,
    folders: List[Dict[str, Any]],
) -> tuple[Dict[str, str], List[Dict[str, Any]]]:
    from app.file_folders.models import FILE_FOLDER_SYSTEM_KINDS, FileFolders
    from sqlalchemy import and_, or_

    folder_id_map: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    target_user_id = str(user_id)
    source_ids = {
        str(row.get("id") or "").strip()
        for row in folders
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    requested_share_ids = {
        str(row.get(field) or "").strip()
        for row in folders
        if isinstance(row, dict)
        for field in ("clone_share_id", "live_share_id", "collaborate_share_id")
        if str(row.get(field) or "").strip()
    }

    # One indexed, archive-bounded query replaces the previous whole-table
    # scans and one-query-per-folder lookup. Target-user system folders are the
    # only rows loaded independently of IDs present in the archive.
    collision_filters = [
        and_(
            FileFolders.user_id == user_id,
            FileFolders.system_kind.isnot(None),
        )
    ]
    if source_ids:
        collision_filters.append(FileFolders.id.in_(source_ids))
    if requested_share_ids:
        collision_filters.extend(
            (
                FileFolders.clone_share_id.in_(requested_share_ids),
                FileFolders.live_share_id.in_(requested_share_ids),
                FileFolders.collaborate_share_id.in_(requested_share_ids),
            )
        )

    collision_rows = (
        db.query(
            FileFolders.id,
            FileFolders.user_id,
            FileFolders.system_kind,
            FileFolders.clone_share_id,
            FileFolders.live_share_id,
            FileFolders.collaborate_share_id,
        )
        .filter(or_(*collision_filters))
        .all()
    )

    existing_folder_ids: set[str] = set()
    existing_owned_folders: dict[str, str] = {}
    existing_share_ids: set[str] = set()
    existing_system_folders: dict[str, str] = {}
    for (
        folder_id,
        folder_user_id,
        system_kind,
        clone_share_id,
        live_share_id,
        collaborate_share_id,
    ) in collision_rows:
        normalized_folder_id = str(folder_id or "").strip()
        normalized_user_id = str(folder_user_id or "").strip()
        normalized_system_kind = str(system_kind or "").strip()

        if normalized_folder_id in source_ids:
            existing_folder_ids.add(normalized_folder_id)
            if normalized_user_id == target_user_id:
                existing_owned_folders[normalized_folder_id] = normalized_folder_id

        if (
            normalized_user_id == target_user_id
            and normalized_system_kind in FILE_FOLDER_SYSTEM_KINDS
        ):
            existing_system_folders[normalized_system_kind] = normalized_folder_id

        for share_id in (clone_share_id, live_share_id, collaborate_share_id):
            normalized_share_id = str(share_id or "").strip()
            if normalized_share_id in requested_share_ids:
                existing_share_ids.add(normalized_share_id)

    # Imported system-folder identities remain portable, but only one folder of
    # each recognized kind may exist per user.  Share capabilities are never
    # restored onto a system folder because those folders are private automatic
    # storage containers.
    for row in folders:
        if not isinstance(row, dict):
            continue

        source_id = str(row.get("id") or "").strip()
        existing_folder_id = existing_owned_folders.get(source_id)
        if existing_folder_id:
            folder_id_map[source_id] = existing_folder_id
            continue
        if source_id and source_id in folder_id_map:
            continue

        requested_system_kind = str(row.get("system_kind") or "").strip().lower()
        system_kind = (
            requested_system_kind
            if requested_system_kind in FILE_FOLDER_SYSTEM_KINDS
            else None
        )
        if system_kind and system_kind in existing_system_folders:
            if source_id:
                folder_id_map[source_id] = existing_system_folders[system_kind]
            continue

        folder_id = (
            source_id
            if source_id and source_id not in existing_folder_ids
            else str(uuid.uuid4())
        )
        payload = _prepare_new_serialized_model_payload(
            FileFolders,
            row,
            overrides={"id": folder_id, "user_id": user_id},
        )
        payload["system_kind"] = system_kind

        if system_kind:
            # Share tokens are capabilities and cannot be restored on private
            # system folders, even from a hand-edited archive.
            payload["clone_share_id"] = None
            payload["live_share_id"] = None
            payload["collaborate_share_id"] = None

        regenerated_share_types: list[str] = []
        for share_field, share_label in (
            ("clone_share_id", "clone"),
            ("live_share_id", "live"),
            ("collaborate_share_id", "collaborate"),
        ):
            share_id = str(payload.get(share_field) or "").strip()
            if not share_id:
                continue
            if share_id in existing_share_ids:
                payload[share_field] = str(uuid.uuid4())
                regenerated_share_types.append(share_label)
            existing_share_ids.add(str(payload.get(share_field) or "").strip())

        if regenerated_share_types:
            warnings.append(
                {
                    "section": "file_folders",
                    "warning": "One or more imported folder share IDs conflicted with existing folders and were regenerated.",
                    "source_folder_id": source_id or None,
                    "folder_id": folder_id,
                    "regenerated_share_types": regenerated_share_types,
                }
            )

        existing_folder_ids.add(folder_id)
        if system_kind:
            existing_system_folders[system_kind] = folder_id
        if source_id:
            folder_id_map[source_id] = folder_id
        db.add(FileFolders(**payload))

    db.commit()
    return folder_id_map, warnings


def _resolve_shared_file_folder_subscription_target(
    db, row: Dict[str, Any], folder_id_map: Dict[str, str]
) -> str | None:
    from app.file_folders.models import (
        FileFolders,
        ShareType,
        get_shared_folder_by_share_id,
    )

    source_folder_id = str(row.get("folder_id") or "").strip()
    if source_folder_id and source_folder_id in folder_id_map:
        return folder_id_map[source_folder_id]

    normalized_share_type = str(row.get("share_type") or "").strip().lower()
    if normalized_share_type == ShareType.CLONE.value:
        return None

    target_share_id = str(row.get("target_share_id") or "").strip()
    if target_share_id:
        share_type = (
            ShareType(normalized_share_type)
            if normalized_share_type in {item.value for item in ShareType}
            else None
        )
        folder = get_shared_folder_by_share_id(db, target_share_id, share_type)
        if (
            folder
            and _share_id_for_folder_subscription(folder, normalized_share_type)
            == target_share_id
        ):
            return str(folder.id)

    if source_folder_id:
        folder = (
            db.query(FileFolders).filter(FileFolders.id == source_folder_id).first()
        )
        if folder and _share_id_for_folder_subscription(folder, normalized_share_type):
            return str(folder.id)

    return None


def _bulk_insert_shared_file_folder_subscriptions(
    db,
    user_id: str,
    subscriptions: List[Dict[str, Any]],
    *,
    folder_id_map: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    from app.file_folders.models import SharedFileFolderSubscription

    resolved_folder_id_map = folder_id_map or {}
    warnings: list[dict[str, Any]] = []

    for row in subscriptions:
        if not isinstance(row, dict):
            continue

        resolved_folder_id = _resolve_shared_file_folder_subscription_target(
            db, row, resolved_folder_id_map
        )
        normalized_share_type = (
            str(row.get("share_type") or "").strip().lower() or "live"
        )
        if resolved_folder_id is None:
            warnings.append(
                {
                    "section": "shared_file_folder_subscriptions",
                    "warning": "Skipped shared file folder subscription because the referenced shared folder could not be resolved.",
                    "source_folder_id": str(row.get("folder_id") or "").strip() or None,
                    "target_share_id": str(row.get("target_share_id") or "").strip()
                    or None,
                    "share_type": normalized_share_type,
                }
            )
            continue

        existing = (
            db.query(SharedFileFolderSubscription)
            .filter(
                SharedFileFolderSubscription.folder_id == resolved_folder_id,
                SharedFileFolderSubscription.subscriber_id == user_id,
            )
            .first()
        )
        subscribed_at = _prepare_serialized_model_payload(
            SharedFileFolderSubscription, row
        ).get("subscribed_at")

        if existing:
            existing.share_type = normalized_share_type
            if subscribed_at is not None:
                existing.subscribed_at = subscribed_at
            continue

        payload = _prepare_new_serialized_model_payload(
            SharedFileFolderSubscription,
            row,
            overrides={
                "folder_id": resolved_folder_id,
                "subscriber_id": user_id,
                "share_type": normalized_share_type,
            },
        )
        db.add(SharedFileFolderSubscription(**payload))

    db.commit()
    return warnings


def _bulk_insert_agents(
    db,
    user_id: str,
    agents: List[Dict[str, Any]],
    *,
    skill_id_map: Dict[str, str] | None = None,
) -> Dict[str, str]:
    from app.agents.models import UserAgent

    resolved_skill_id_map = skill_id_map or {}
    agent_id_map: dict[str, str] = {}
    for row in agents:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("id") or "").strip()
        agent_id = str(uuid.uuid4())
        payload = _prepare_new_serialized_model_payload(
            UserAgent,
            row,
            overrides={
                "id": agent_id,
                "user_id": user_id,
                "clone_share_id": None,
                "live_share_id": None,
                "collaborate_share_id": None,
            },
        )
        source_skill_id = str(row.get("skill_id") or "").strip()
        if source_skill_id:
            mapped_skill_id = resolved_skill_id_map.get(source_skill_id)
            if mapped_skill_id is None:
                logger.warning(
                    "Skipping unmapped skill_id while importing agent %s",
                    source_id or agent_id,
                    extra={
                        "event": "user_data_import_unmapped_agent_skill",
                        "agent_id": source_id or agent_id,
                        "source_skill_id": source_skill_id,
                    },
                )
            payload["skill_id"] = mapped_skill_id
        if source_id:
            agent_id_map[source_id] = agent_id
        db.add(UserAgent(**payload))
    db.commit()
    return agent_id_map


def _upload_inline_file_bytes(
    user_id: str, file_name: str, file_bytes: bytes
) -> tuple[str, str, dict[str, Any]]:
    from app.files.storage import upload_file_to_storage

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(file_bytes)
    try:
        return upload_file_to_storage(temp_path, user_id, file_name)
    finally:
        temp_path.unlink(missing_ok=True)


def _delete_uploaded_file_references(
    user_id: str,
    references: Iterable[tuple[str, str, str | None]],
) -> None:
    from app.files.utils import delete_storage_reference

    for storage_provider, storage_key, file_name in references:
        try:
            delete_storage_reference(
                storage_provider=storage_provider,
                storage_key=storage_key,
                user_id=user_id,
                file_name=file_name,
            )
        except Exception:
            logger.debug(
                "Failed to clean up imported upload %s",
                storage_key,
                exc_info=True,
            )


def _bulk_insert_agent_assets(
    db,
    user_id: str,
    assets: List[Dict[str, Any]],
    *,
    agent_id_map: Dict[str, str] | None = None,
) -> None:
    from app.agents.models import UserAgentAsset

    resolved_agent_id_map = agent_id_map or {}
    uploaded_references: list[tuple[str, str, str | None]] = []
    try:
        for row in assets:
            if not isinstance(row, dict):
                continue
            file_bytes = _decode_export_content(row.get(USER_DATA_INLINE_CONTENT_KEY))
            if not file_bytes:
                continue
            source_agent_id = str(row.get("agent_id") or "").strip()
            mapped_agent_id = resolved_agent_id_map.get(source_agent_id)
            if mapped_agent_id is None:
                logger.warning(
                    "Skipping agent asset with unmapped agent_id %s",
                    source_agent_id or "(missing)",
                    extra={
                        "event": "user_data_import_unmapped_agent_asset",
                        "source_agent_id": source_agent_id or None,
                    },
                )
                continue
            payload = _prepare_new_serialized_model_payload(
                UserAgentAsset,
                row,
                overrides={"owner_user_id": user_id},
            )
            asset_id = str(payload["id"])
            file_name = Path(
                str(payload.get("file_name") or f"agent-asset-{asset_id}")
            ).name
            if not file_name:
                file_name = f"agent-asset-{asset_id}"
            provider, storage_key, storage_meta = _upload_inline_file_bytes(
                user_id, file_name, file_bytes
            )
            uploaded_references.append((provider, storage_key, file_name))
            payload.update(
                {
                    "agent_id": mapped_agent_id,
                    "file_name": file_name,
                    "storage_provider": provider,
                    "storage_key": storage_key,
                    "storage_meta": storage_meta,
                    "file_size": len(file_bytes),
                }
            )
            db.add(UserAgentAsset(**payload))
        db.commit()
    except Exception:
        db.rollback()
        _delete_uploaded_file_references(user_id, uploaded_references)
        raise


def _bulk_insert_shared_agent_subscriptions(
    db, user_id: str, subscriptions: List[Dict[str, Any]]
) -> None:
    from app.agents.models import SharedUserAgentSubscription

    _bulk_merge_serialized_models(
        db,
        SharedUserAgentSubscription,
        subscriptions,
        overrides={"subscriber_id": user_id},
    )


def _write_slide_presentation_artifacts(
    *,
    user_id: str,
    presentation_id: str,
    artifacts: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    from app.tools.slide_presentation.storage import (
        _upload_single_artifact,
        build_presentation_storage_prefix,
        get_presentation_storage_provider,
    )

    provider = get_presentation_storage_provider()
    storage_prefix = build_presentation_storage_prefix(user_id, presentation_id)
    uploaded_relative_paths: list[str] = []
    try:
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            file_bytes = _decode_export_content(
                artifact.get(USER_DATA_INLINE_CONTENT_KEY)
            )
            if file_bytes is None:
                continue
            try:
                relative_path = _normalize_export_relative_path(
                    artifact.get("relative_path")
                ).as_posix()
            except ValueError:
                continue
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(file_bytes)
            try:
                _upload_single_artifact(
                    temp_path,
                    user_id=user_id,
                    presentation_id=presentation_id,
                    relative_path=relative_path,
                )
                uploaded_relative_paths.append(relative_path)
            finally:
                temp_path.unlink(missing_ok=True)
    except Exception:
        _delete_slide_presentation_artifact_paths(
            provider, storage_prefix, uploaded_relative_paths
        )
        raise
    return provider, storage_prefix, uploaded_relative_paths


def _delete_slide_presentation_artifact_paths(
    storage_provider: str,
    storage_prefix: str,
    relative_paths: Iterable[str],
) -> None:
    from app.files.storage import delete_file_from_storage

    provider = str(storage_provider or "local").strip().lower() or "local"
    prefix = str(storage_prefix or "").strip().strip("/\\")
    if not prefix:
        return
    for relative_path in relative_paths:
        try:
            normalized_relative_path = _normalize_export_relative_path(
                relative_path
            ).as_posix()
            storage_key = f"{prefix}/{normalized_relative_path}"
            if provider == "local":
                from app.files.utils import BASE_STORAGE_DIR

                _safe_child_path(BASE_STORAGE_DIR, PurePosixPath(storage_key)).unlink(
                    missing_ok=True
                )
            else:
                delete_file_from_storage(provider, storage_key)
        except Exception:
            logger.debug(
                "Failed to clean up imported slide artifact %s/%s",
                prefix,
                relative_path,
                exc_info=True,
            )


def _bulk_insert_slide_presentations(
    db,
    user_id: str,
    rows: List[Dict[str, Any]],
    *,
    file_id_map: Dict[str, str] | None = None,
) -> None:
    from app.tools.slide_presentation.models import SlidePresentations

    resolved_file_id_map = file_id_map or {}
    uploaded_presentations: list[tuple[str, str, list[str]]] = []
    try:
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_presentation_id = str(row.get("id") or "").strip()
            # New presentations use their canonical Canvas HTML file ID as
            # identity. Preserve that relationship when user-data import has
            # already remapped the exported file record.
            presentation_id = resolved_file_id_map.get(source_presentation_id) or str(
                uuid.uuid4()
            )
            artifacts = (
                row.get("artifacts") if isinstance(row.get("artifacts"), list) else []
            )
            provider, storage_prefix, uploaded_relative_paths = (
                _write_slide_presentation_artifacts(
                    user_id=user_id,
                    presentation_id=presentation_id,
                    artifacts=artifacts,
                )
            )
            uploaded_presentations.append(
                (provider, storage_prefix, uploaded_relative_paths)
            )
            source_file_id = str(row.get("file_id") or "").strip()
            mapped_file_id = (
                resolved_file_id_map.get(source_file_id) if source_file_id else None
            )
            if source_file_id and mapped_file_id is None:
                logger.warning(
                    "Imported slide presentation %s references unmapped file_id %s",
                    presentation_id,
                    source_file_id,
                    extra={
                        "event": "user_data_import_unmapped_slide_file",
                        "presentation_id": presentation_id,
                        "source_file_id": source_file_id,
                    },
                )
            source_storage_meta = row.get("storage_meta")
            if not isinstance(source_storage_meta, dict):
                source_storage_meta = {}
            imported_storage_meta = {
                "uploaded_files": uploaded_relative_paths,
                "brief_file_id": resolved_file_id_map.get(
                    str(source_storage_meta.get("brief_file_id") or "").strip()
                ),
                # Imported derivatives have not been rendered by this
                # instance, so the source must begin stale.
                "render_revision": None,
            }
            if source_presentation_id in resolved_file_id_map:
                imported_storage_meta["html_file_id"] = resolved_file_id_map[
                    source_presentation_id
                ]
            payload = _prepare_new_serialized_model_payload(
                SlidePresentations,
                row,
                overrides={
                    "id": presentation_id,
                    "user_id": user_id,
                    "storage_provider": provider,
                    "storage_prefix": storage_prefix,
                    # Imported artifacts are newly written to this instance's
                    # configured provider. Do not retain migration provenance
                    # or provider manifests from the exported source instance.
                    "storage_meta": imported_storage_meta,
                    "file_id": mapped_file_id,
                },
            )
            db.add(SlidePresentations(**payload))
        db.commit()
    except Exception:
        db.rollback()
        for provider, storage_prefix, uploaded_relative_paths in uploaded_presentations:
            _delete_slide_presentation_artifact_paths(
                provider, storage_prefix, uploaded_relative_paths
            )
        raise


def _file_import_id_map(file_import_result: Dict[str, Any]) -> Dict[str, str]:
    mapped: dict[str, str] = {}
    for entry in list(file_import_result.get("created_files") or []) + list(
        file_import_result.get("skipped_files") or []
    ):
        if not isinstance(entry, dict):
            continue
        source_file_id = str(entry.get("source_file_id") or "").strip()
        file_id = str(entry.get("file_id") or "").strip()
        if source_file_id and file_id:
            mapped[source_file_id] = file_id
    return mapped


def _import_user_notes_archive(
    db,
    user_id: str,
    raw_notes: Any,
    *,
    restore_sharing_metadata: bool,
    skip_existing_owned: bool = False,
) -> dict[str, Any]:
    """Import account notes with history using the feature-owned importer."""
    return import_user_notes(
        db,
        user_id,
        raw_notes,
        restore_sharing_metadata=restore_sharing_metadata,
        skip_existing_owned=skip_existing_owned,
    )


def _import_user_memories_archive(
    db, user_id: str, raw_memories: Any
) -> dict[str, Any]:
    """Validate and import the user's bounded memory collection."""
    validated_payload = MemoryExportPayload.model_validate(raw_memories)
    return import_memory_export(db, MemoryScope.personal(user_id), validated_payload)


@dataclass(frozen=True)
class UserArchiveRestorePolicy:
    """Security-sensitive differences between account archive consumers.

    The shared section engine never decides whether a caller is an
    administrator or the account owner. Its caller must resolve the target
    account first and supply this narrow policy explicitly.
    """

    restore_note_sharing_metadata: bool
    skip_existing_owned_notes: bool


@dataclass
class UserArchiveRestoreResult:
    """Internal normalized result returned by the shared section engine."""

    imported_sections: List[str] = dataclass_field(default_factory=list)
    warnings: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    errors: List[str] = dataclass_field(default_factory=list)
    project_id_map: Dict[str, str] = dataclass_field(default_factory=dict)
    chat_id_map: Dict[str, str] = dataclass_field(default_factory=dict)
    file_import_result: Dict[str, Any] = dataclass_field(
        default_factory=lambda: {
            "created_files_count": 0,
            "skipped_files_count": 0,
            "errors": [],
            "warnings": [],
        }
    )
    notes_import_result: Dict[str, Any] = dataclass_field(
        default_factory=lambda: {
            "created": [],
            "skipped": [],
            "warnings": [],
            "errors": [],
        }
    )
    memories_import_result: Dict[str, Any] = dataclass_field(
        default_factory=lambda: {"created_count": 0, "deduped_count": 0}
    )


SELF_USER_ARCHIVE_RESTORE_POLICY = UserArchiveRestorePolicy(
    # Self-service restore must never reactivate exported bearer links or note
    # subscriptions. Administrator migration may opt in at its authenticated
    # boundary, but this constant is deliberately immutable and non-configurable.
    restore_note_sharing_metadata=False,
    skip_existing_owned_notes=True,
)


def _validate_user_archive_payload(
    payload: Dict[str, Any],
) -> tuple[ImportUserPayload, List[Dict[str, Any]]]:
    """Validate the one user-data contract shared by admin and self restore."""
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Invalid import payload. Expected an object."
        )
    if payload.get("export_type") != USER_DATA_EXPORT_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_type '{payload.get('export_type')}'.",
        )
    if payload.get("export_version") != USER_DATA_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported export_version '{payload.get('export_version')}'. "
                f"Expected '{USER_DATA_EXPORT_VERSION}'."
            ),
        )
    if "messaging" in payload:
        raise HTTPException(
            status_code=400,
            detail="Unsupported user-data section 'messaging'.",
        )
    try:
        validated = ImportUserPayload.model_validate(payload)
    except ValidationError as exc:
        logger.info("Rejected invalid user archive payload", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid import payload.") from exc
    return validated, _get_skipped_export_only_sections(payload)


def _import_user_archive_inline_files(
    db,
    *,
    target_user,
    source_email: str,
    files: List[Dict[str, Any]],
    user_action: str,
    folder_id_map: Dict[str, str],
    project_id_map: Dict[str, str],
) -> Dict[str, Any]:
    """Call the shared inline file importer without creating an import cycle."""
    from app.admin.user_exports.files.models import (
        import_admin_user_inline_files_for_user,
    )

    return import_admin_user_inline_files_for_user(
        db,
        target_user=target_user,
        source_email=source_email,
        files=files,
        user_action=user_action,
        folder_id_map=folder_id_map,
        project_id_map=project_id_map,
    )


def _restore_user_archive_sections(
    validated: ImportUserPayload,
    db,
    *,
    target_user,
    source_email: str,
    user_action: str,
    policy: UserArchiveRestorePolicy,
) -> UserArchiveRestoreResult:
    """Restore every portable owned-data section through one ordered engine.

    Identity, role, group, password, and self-service settings are intentionally
    outside this function. Keeping those operations in the authenticated
    wrappers prevents a reusable content importer from gaining account-level
    authority. Each section has an independent failure boundary so one corrupt
    feature cannot silently suppress unrelated portable data.
    """
    result = UserArchiveRestoreResult()
    user_id = str(target_user.id)
    folder_id_map: Dict[str, str] = {}
    file_id_map: Dict[str, str] = {}
    skill_id_map: Dict[str, str] = {}
    agent_id_map: Dict[str, str] = {}
    project_id_map: Dict[str, str] = {}
    chat_id_map: Dict[str, str] = {}
    mcp_server_id_map: Dict[str, str] = {}

    def restore_section(
        section_name: str,
        importer,
        *importer_args,
        mark_imported: bool = True,
        **importer_kwargs,
    ):
        """Run one section transaction and normalize its fatal error."""
        try:
            section_result = importer(*importer_args, **importer_kwargs)
            if mark_imported:
                result.imported_sections.append(section_name)
            return section_result
        except Exception:
            logger.exception("Failed to import %s for user %s", section_name, user_id)
            result.errors.append(
                f"{section_name}: import failed. See server logs for details."
            )
            db.rollback()
            return None

    if validated.notes:
        notes_result = restore_section(
            "notes",
            _import_user_notes_archive,
            db,
            user_id,
            validated.notes,
            restore_sharing_metadata=policy.restore_note_sharing_metadata,
            skip_existing_owned=policy.skip_existing_owned_notes,
        )
        if isinstance(notes_result, dict):
            result.notes_import_result = notes_result
            result.warnings.extend(
                {**entry, "section": "notes"}
                for entry in notes_result.get("warnings", [])
                if isinstance(entry, dict)
            )
            for entry in notes_result.get("errors", []):
                detail = entry.get("error", entry) if isinstance(entry, dict) else entry
                result.errors.append(f"notes: {detail}")

    if validated.memories:
        memories_result = restore_section(
            "memories",
            _import_user_memories_archive,
            db,
            user_id,
            validated.memories,
        )
        if isinstance(memories_result, dict):
            result.memories_import_result = memories_result

    if validated.skills:
        skill_id_map = (
            restore_section(
                "skills",
                _bulk_insert_skills,
                db,
                user_id,
                validated.skills,
                validated.skill_files or [],
            )
            or {}
        )
    if validated.shared_skill_subscriptions:
        restore_section(
            "shared_skill_subscriptions",
            _bulk_insert_shared_skill_subscriptions,
            db,
            user_id,
            validated.shared_skill_subscriptions,
        )
    if validated.file_folders:
        folder_result = restore_section(
            "file_folders",
            _bulk_insert_file_folders,
            db,
            user_id,
            validated.file_folders,
        )
        if isinstance(folder_result, tuple) and folder_result:
            folder_id_map = folder_result[0] or {}
            result.warnings.extend(
                warning
                for warning in (folder_result[1] if len(folder_result) > 1 else [])
                if isinstance(warning, dict)
            )
    if validated.shared_file_folder_subscriptions:
        subscription_warnings = restore_section(
            "shared_file_folder_subscriptions",
            _bulk_insert_shared_file_folder_subscriptions,
            db,
            user_id,
            validated.shared_file_folder_subscriptions,
            folder_id_map=folder_id_map,
        )
        if isinstance(subscription_warnings, list):
            result.warnings.extend(
                warning
                for warning in subscription_warnings
                if isinstance(warning, dict)
            )
    if validated.todos:
        restore_section("todos", _bulk_insert_todos, db, user_id, validated.todos)
    if validated.projects:
        project_id_map = (
            restore_section(
                "projects",
                _bulk_insert_projects,
                db,
                user_id,
                validated.projects,
            )
            or {}
        )
    if validated.files:
        file_result = restore_section(
            "files",
            _import_user_archive_inline_files,
            db,
            target_user=target_user,
            source_email=source_email,
            files=validated.files,
            user_action=user_action,
            folder_id_map=folder_id_map,
            project_id_map=project_id_map,
            mark_imported=False,
        )
        if isinstance(file_result, dict):
            result.file_import_result = file_result
            file_id_map = _file_import_id_map(file_result)
            if file_result.get("created_files_count") or file_result.get(
                "skipped_files_count"
            ):
                result.imported_sections.append("files")
            result.warnings.extend(
                warning
                for warning in file_result.get("warnings", [])
                if isinstance(warning, dict)
            )
            if file_id_map and project_id_map:
                restore_section(
                    "project_file_references",
                    _remap_imported_project_file_references,
                    db,
                    project_id_map,
                    file_id_map,
                    mark_imported=False,
                )
    if validated.chats:
        chat_id_map = (
            restore_section(
                "chats",
                _bulk_insert_chat_data,
                db,
                user_id,
                validated.chats,
                project_id_map=project_id_map,
                file_id_map=file_id_map,
            )
            or {}
        )
    if validated.agents:
        agent_id_map = (
            restore_section(
                "agents",
                _bulk_insert_agents,
                db,
                user_id,
                validated.agents,
                skill_id_map=skill_id_map,
            )
            or {}
        )
    if validated.agent_assets:
        restore_section(
            "agent_assets",
            _bulk_insert_agent_assets,
            db,
            user_id,
            validated.agent_assets,
            agent_id_map=agent_id_map,
        )
    if validated.slide_presentations:
        restore_section(
            "slide_presentations",
            _bulk_insert_slide_presentations,
            db,
            user_id,
            validated.slide_presentations,
            file_id_map=file_id_map,
        )
    for section_name, rows, importer in (
        ("prompts", validated.prompts, _bulk_insert_prompts),
        (
            "shared_prompt_subscriptions",
            validated.shared_prompt_subscriptions,
            _bulk_insert_shared_prompt_subscriptions,
        ),
        (
            "user_connections",
            validated.user_connections,
            _bulk_insert_user_connections,
        ),
    ):
        if rows:
            restore_section(section_name, importer, db, user_id, rows)

    # Automation selections may reference both restored connection-backed
    # servers and portable personal MCP definitions. Restore those dependencies
    # first, then pass the personal-server ID map into the automation importer.
    if validated.mcp_servers:
        mcp_server_id_map = (
            restore_section(
                "mcp_servers",
                _bulk_insert_user_mcp_servers,
                db,
                user_id,
                validated.mcp_servers,
            )
            or {}
        )
    for section_name, rows, importer in (
        (
            "connection_oauth_states",
            validated.connection_oauth_states,
            _bulk_insert_connection_oauth_states,
        ),
        (
            "model_setting_presets",
            validated.model_setting_presets,
            _bulk_insert_model_setting_presets,
        ),
    ):
        if rows:
            restore_section(section_name, importer, db, user_id, rows)

    if validated.automations:
        automation_import_kwargs = (
            {"mcp_server_id_map": mcp_server_id_map} if mcp_server_id_map else {}
        )
        automation_warnings = restore_section(
            "automations",
            _bulk_insert_automations,
            db,
            user_id,
            validated.automations,
            **automation_import_kwargs,
        )
        if isinstance(automation_warnings, list):
            result.warnings.extend(
                warning for warning in automation_warnings if isinstance(warning, dict)
            )
    result.project_id_map = project_id_map
    result.chat_id_map = chat_id_map
    return result


def import_user_from_export(
    payload: Dict[str, Any],
    db,
    db_log=None,
    *,
    default_password: str | None = None,
    force_password_change: bool = True,
    allow_administrative_target: bool = False,
    restore_sharing_metadata: bool = False,
) -> Dict[str, Any]:
    """Import one administrator-selected account, then restore shared sections.

    Only a caller that has already authenticated the instance owner may set
    ``allow_administrative_target``. Keeping the check at this mutation sink
    also protects ZIP imports and other background-thread callers.
    """
    validated, skipped_sections = _validate_user_archive_payload(payload)
    profile_data = _sanitize_user_profile_for_archive(validated.user or {})
    email = _normalize_import_email(profile_data.get("email") or payload.get("email"))
    if not email:
        raise HTTPException(status_code=400, detail="Missing email in import payload")
    profile_data["email"] = email
    if isinstance(validated.settings, dict) and not isinstance(
        profile_data.get("settings"), dict
    ):
        profile_data["settings"] = _sanitize_user_archive_settings(validated.settings)

    group_id = get_value_by_page_and_key("login_general", "default_user_group", db)
    email_match = build_user_email_match(email)
    existing_user = (
        db.query(User).filter(email_match).first() if email_match is not None else None
    )
    profile_warnings: List[Dict[str, Any]] = []
    if existing_user:
        existing_role = getattr(existing_user, "role", None)
        if is_admin_role(existing_role) and not allow_administrative_target:
            detail = (
                "The owner account cannot be modified by another administrator."
                if is_owner_role(existing_role)
                else "Only the owner can modify administrator accounts."
            )
            raise HTTPException(status_code=403, detail=detail)
        imported_password_hash = None
        if isinstance(default_password, str) and default_password:
            _assert_password_policy(default_password, db)
            imported_password_hash = hash_password(default_password)
        user = _merge_user_record(
            db,
            existing_user,
            profile_data,
            warnings=profile_warnings,
            new_password_hash=imported_password_hash,
            force_password_change=(
                force_password_change if imported_password_hash else None
            ),
        )
        action = "updated"
    else:
        preferred_user_id = _resolve_preferred_user_id(profile_data, payload, db)
        if not isinstance(default_password, str) or not default_password:
            raise HTTPException(
                status_code=400,
                detail="Default password is required to create imported users.",
            )
        _assert_password_policy(default_password, db)
        user = _create_user_record(
            db,
            profile_data,
            group_id,
            hashed_password=hash_password(default_password),
            force_password_change=force_password_change,
            preferred_user_id=preferred_user_id,
        )
        action = "created"

    restore_result = _restore_user_archive_sections(
        validated,
        db,
        target_user=user,
        source_email=email,
        user_action=action,
        policy=UserArchiveRestorePolicy(
            restore_note_sharing_metadata=restore_sharing_metadata,
            skip_existing_owned_notes=action == "updated",
        ),
    )
    profile_warnings.extend(restore_result.warnings)
    file_result = restore_result.file_import_result
    if file_result.get("errors"):
        profile_warnings.append(
            {
                "section": "files",
                "warning": "Some user files could not be imported",
                "created_files_count": int(file_result.get("created_files_count", 0)),
                "skipped_files_count": int(file_result.get("skipped_files_count", 0)),
                "error_count": len(file_result.get("errors", [])),
                "details": file_result.get("errors", []),
            }
        )

    return {
        "user_id": user.id,
        "email": user.email,
        "action": action,
        "skipped_sections": skipped_sections,
        "warnings": profile_warnings,
        "errors": restore_result.errors,
        "created_files_count": int(file_result.get("created_files_count", 0)),
        "skipped_files_count": int(file_result.get("skipped_files_count", 0)),
        "file_errors": file_result.get("errors", []),
        "created_notes_count": len(
            restore_result.notes_import_result.get("created") or []
        ),
        "skipped_notes_count": len(
            restore_result.notes_import_result.get("skipped") or []
        ),
        "created_memories_count": int(
            restore_result.memories_import_result.get("created_count", 0)
        ),
        "deduped_memories_count": int(
            restore_result.memories_import_result.get("deduped_count", 0)
        ),
        # These maps are consumed only by the outer canonical ZIP importer,
        # which restores nested file bytes after the account JSON phase.
        "_project_id_map": restore_result.project_id_map,
        "_chat_id_map": restore_result.chat_id_map,
    }


def import_user_data_for_existing_user(
    user_id: str,
    payload: Dict[str, Any],
    db,
    db_log,
) -> Dict[str, Any]:
    """Merge portable content into the signed-in account without identity changes."""
    validated, skipped_sections = _validate_user_archive_payload(payload)
    user = get_user(db, user_id)
    imported_sections: List[str] = []
    errors: List[str] = []
    sanitized_settings = (
        _sanitize_self_user_import_settings(validated.settings)
        if validated.settings
        else {}
    )
    if sanitized_settings:
        base_settings = (
            user.settings
            if isinstance(user.settings, dict)
            else deepcopy(DEFAULT_USER_SETTINGS)
        )
        user.settings = _merge_and_sync_user_settings(
            base_settings or DEFAULT_USER_SETTINGS, sanitized_settings
        )
        flag_modified(user, "settings")
        try:
            db.commit()
            db.refresh(user)
            imported_sections.append("settings")
        except Exception:
            db.rollback()
            logger.exception("Failed to merge settings for user %s", user_id)
            errors.append("settings: import failed. See server logs for details.")

    # The canonical export always records its source email at the top level.
    # This reference validates inline file provenance; it never selects or
    # mutates the authenticated self-service target account.
    source_email = _normalize_import_email(payload.get("email"))
    if not source_email:
        raise HTTPException(
            status_code=400,
            detail="User archive is missing its source email.",
        )
    restore_result = _restore_user_archive_sections(
        validated,
        db,
        target_user=user,
        source_email=source_email,
        user_action="updated",
        policy=SELF_USER_ARCHIVE_RESTORE_POLICY,
    )
    imported_sections.extend(restore_result.imported_sections)
    errors.extend(restore_result.errors)
    file_errors = restore_result.file_import_result.get("errors", [])
    if file_errors:
        errors.append(f"files: {len(file_errors)} file(s) failed to import")

    return {
        "user_id": user.id,
        "imported": imported_sections,
        "skipped_sections": skipped_sections,
        "warnings": restore_result.warnings,
        "errors": errors,
    }


def _parse_admin_user_import_options(raw_options: Any, db) -> AdminUserImportOptions:
    if not isinstance(raw_options, dict):
        raise HTTPException(status_code=400, detail="User import options are required.")
    try:
        options = AdminUserImportOptions.model_validate(raw_options)
    except ValidationError as exc:
        logger.info("Rejected invalid admin user import options", exc_info=True)
        raise HTTPException(
            status_code=400, detail="Invalid user import options."
        ) from exc
    if not options.default_password:
        raise HTTPException(status_code=400, detail="Default password is required.")
    _assert_password_policy(options.default_password, db)
    return options


def import_users_admin(
    payload: Dict[str, Any],
    db,
    *,
    allow_administrative_targets: bool = False,
    include_internal_restore_maps: bool = False,
) -> Dict[str, Any]:
    """Import administrator-selected accounts under the owner/admin hierarchy."""

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "admin_user":
        raise HTTPException(
            status_code=400, detail=f"Unsupported export_type '{export_type}'."
        )

    if export_version != ADMIN_USER_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_version '{export_version}'. Expected '{ADMIN_USER_EXPORT_VERSION}'.",
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(
            status_code=400, detail="Invalid export payload. Missing 'data' object."
        )

    raw_users = data_block.get("users")
    if not isinstance(raw_users, list):
        raise HTTPException(
            status_code=400, detail="Invalid export payload. 'users' must be a list."
        )

    raw_import_options = payload.get("import_options")
    import_options = (
        _parse_admin_user_import_options(raw_import_options, db)
        if raw_import_options is not None
        else None
    )

    raw_reference_map = data_block.get("user_reference_map")
    user_reference_map: Dict[str, str] = {}
    if isinstance(raw_reference_map, dict):
        for source_user_id, source_email in raw_reference_map.items():
            normalized_id = str(source_user_id or "").strip()
            normalized_email = _normalize_import_email(source_email)
            if normalized_id and normalized_email:
                user_reference_map[normalized_id] = normalized_email

    created: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for index, user_entry in enumerate(raw_users):
        if not isinstance(user_entry, dict):
            errors.append({"index": index, "error": "User entry must be an object."})
            continue

        profile_data = (
            user_entry.get("user") if isinstance(user_entry.get("user"), dict) else {}
        )
        email = _normalize_import_email(
            profile_data.get("email") or user_entry.get("email")
        )
        if not email:
            source_user_id = str(
                profile_data.get("user_id") or user_entry.get("user_id") or ""
            ).strip()
            if source_user_id:
                email = _normalize_import_email(user_reference_map.get(source_user_id))
        if not email:
            errors.append(
                {"index": index, "error": "Missing email reference for user entry"}
            )
            continue

        profile_data["email"] = email
        user_entry["user"] = profile_data

        try:
            summary = import_user_from_export(
                user_entry,
                db,
                default_password=import_options.default_password
                if import_options
                else None,
                force_password_change=import_options.force_password_change
                if import_options
                else True,
                allow_administrative_target=allow_administrative_targets,
                # Administrative migration archives may restore the sharing
                # metadata they exported. Self-service imports keep this off.
                restore_sharing_metadata=True,
            )
        except HTTPException as exc:
            # Authorization failures must fail the whole request instead of
            # being buried in a per-row warning after other mutations occur.
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                raise
            errors.append(
                {
                    "index": index,
                    "email": user_entry.get("user", {}).get("email"),
                    "error": exc.detail,
                }
            )
            continue
        except Exception:
            logger.exception(
                "Failed to import admin user archive entry %s for %s",
                index,
                email,
            )
            errors.append(
                {
                    "index": index,
                    "email": user_entry.get("user", {}).get("email"),
                    "error": "User import failed. See server logs for details.",
                }
            )
            continue

        destination = updated if summary.get("action") == "updated" else created
        destination_entry = {
            "index": index,
            "user_id": summary.get("user_id"),
            "email": summary.get("email"),
            "errors": summary.get("errors") or [],
            "skipped_sections": summary.get("skipped_sections") or [],
            "created_files_count": int(summary.get("created_files_count", 0)),
            "skipped_files_count": int(summary.get("skipped_files_count", 0)),
            "file_errors": summary.get("file_errors") or [],
            "created_notes_count": int(summary.get("created_notes_count", 0)),
            "skipped_notes_count": int(summary.get("skipped_notes_count", 0)),
            "created_memories_count": int(summary.get("created_memories_count", 0)),
            "deduped_memories_count": int(summary.get("deduped_memories_count", 0)),
        }
        if include_internal_restore_maps:
            destination_entry["_project_id_map"] = summary.get("_project_id_map") or {}
            destination_entry["_chat_id_map"] = summary.get("_chat_id_map") or {}
        destination.append(destination_entry)

        for item in summary.get("warnings") or []:
            if not isinstance(item, dict):
                continue
            warnings.append(
                {
                    **item,
                    "index": index,
                    "email": summary.get("email"),
                }
            )

        if summary.get("errors"):
            warnings.append(
                {
                    "index": index,
                    "email": summary.get("email"),
                    "warning": "Related data import completed with warnings",
                    "details": summary.get("errors") or [],
                }
            )

    imported_rows = [*created, *updated]
    return {
        "created": created,
        "updated": updated,
        "warnings": warnings,
        "errors": errors,
        "force_password_change": import_options.force_password_change
        if import_options
        else None,
        "created_notes_count": sum(
            int(entry.get("created_notes_count", 0)) for entry in imported_rows
        ),
        "skipped_notes_count": sum(
            int(entry.get("skipped_notes_count", 0)) for entry in imported_rows
        ),
        "created_memories_count": sum(
            int(entry.get("created_memories_count", 0)) for entry in imported_rows
        ),
        "deduped_memories_count": sum(
            int(entry.get("deduped_memories_count", 0)) for entry in imported_rows
        ),
    }


# -------------------
# User Settings Init
# -------------------
