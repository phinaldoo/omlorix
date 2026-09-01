"""Streaming user-data export and archive sanitization helpers.

The export path is intentionally isolated from restore logic so its security
filtering, bounded-query behavior, and streaming format can evolve together.
"""

from datetime import datetime, timezone, date
from pathlib import Path, PurePosixPath
from decimal import Decimal
from sqlalchemy.inspection import inspect as sqla_inspect
import base64
import logging
import os
import json
import uuid
import tempfile
from copy import deepcopy
from typing import Any, Dict, Iterable, Iterator, List

from sqlalchemy.orm import load_only
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.init import (
    get_user_settings,
)
from app.users.models import (
    User,
    get_user,
)
from app.auth.models import (
    Authentication,
)

from app.logging.models import Logs, AuthenticationLogs
from app.chats.export_security import (
    is_chat_excluded_from_default_export,
    sanitize_chat_share_for_export,
)
from app.chats.models import ChatReadState, Chats, ChatMessages
from app.memories.service import MemoryScope, export_memories
from app.notes.models import export_user_notes
from app.projects.models import Project
from app.automations.models import Automation
from app.feedback.models import ModelFeedback
from app.todos.models import TodoLists, Todos
from app.groups.models import get_group
from app.utils.email import normalize_email

logger = logging.getLogger(__name__)

ADMIN_USER_EXPORT_VERSION = 1.0
USER_DATA_EXPORT_TYPE = "user_data"
USER_DATA_EXPORT_VERSION = 1.0


def _safe_parse_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid integer value for %s: %r; using default %s",
            name,
            raw_value,
            default,
        )
        return default


USER_DATA_EXPORT_QUERY_BATCH_SIZE = max(
    1, _safe_parse_env_int("USER_DATA_EXPORT_QUERY_BATCH_SIZE", 500)
)
USER_DATA_EXPORT_SPOOL_THRESHOLD_BYTES = max(
    1024 * 1024,
    _safe_parse_env_int("USER_DATA_EXPORT_SPOOL_THRESHOLD_BYTES", 8 * 1024 * 1024),
)


def _json_dumps(value: Any) -> str:
    """Serialize compact JSON for generated export streams."""
    return json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))


def _iter_query_rows(query, batch_size: int = USER_DATA_EXPORT_QUERY_BATCH_SIZE):
    """Iterate query results in batches instead of materializing a list."""
    if hasattr(query, "execution_options"):
        query = query.execution_options(stream_results=True)
    if hasattr(query, "yield_per"):
        query = query.yield_per(batch_size)
    try:
        yield from query
    except TypeError:
        yield from query.all()


def _serialize_datetime(value: datetime | None) -> str | None:
    """Serialize datetime to UTC ISO format string."""
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _serialize_date(value: datetime | date | None) -> str | None:
    """Serialize date to ISO format string."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _serialize_decimal(value: Any) -> float | None:
    """Serialize Decimal to float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _strip_nulls(data: Any) -> Any:
    """Recursively strip None values from dict or list."""
    if isinstance(data, dict):
        return {k: _strip_nulls(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_strip_nulls(v) for v in data if v is not None]
    return data


def _model_as_dict(instance) -> Dict[str, Any]:
    """Convert SQLAlchemy model instance to dict."""
    if instance is None:
        return {}
    try:
        mapper = sqla_inspect(instance.__class__)
    except Exception:
        return {
            key: _serialize_datetime(value)
            if isinstance(value, datetime)
            else _serialize_date(value)
            if isinstance(value, date)
            else _serialize_decimal(value)
            if isinstance(value, Decimal)
            else value
            for key, value in vars(instance).items()
            if not key.startswith("_")
        }
    data: Dict[str, Any] = {}
    for column in mapper.columns:
        key = column.key
        value = getattr(instance, key)
        if isinstance(value, datetime):
            data[key] = _serialize_datetime(value)
        elif isinstance(value, date):
            data[key] = _serialize_date(value)
        elif isinstance(value, Decimal):
            data[key] = _serialize_decimal(value)
        else:
            data[key] = value
    return data


def _normalize_uuid(value: Any) -> str | None:
    """Normalize a value to a UUID string."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _normalize_import_email(value: Any) -> str | None:
    """Normalize email for import."""
    return normalize_email(value)


def _resolve_preferred_user_id(
    profile_data: Dict[str, Any], payload: Dict[str, Any], db
) -> str | None:
    """Resolve a preferred user ID without reusing an erased identity."""

    from app.workers.models import (
        AuditEventSubjectState,
        audit_event_subject_fingerprint,
    )

    candidates = [
        profile_data.get("user_id"),
        profile_data.get("id"),
        payload.get("user_id"),
    ]
    for candidate in candidates:
        normalized = _normalize_uuid(candidate)
        if not normalized:
            continue
        if db.query(User).filter(User.id == normalized).first():
            continue
        erased_subject = (
            db.query(AuditEventSubjectState)
            .filter(
                AuditEventSubjectState.subject_fingerprint
                == audit_event_subject_fingerprint(normalized),
                AuditEventSubjectState.erased_at.is_not(None),
            )
            .first()
        )
        if erased_subject is not None:
            # A user archive may restore content, but it must not resurrect the
            # internal identity protected by a completed permanent erasure.
            # Creating a fresh ID also keeps the one-way audit fence intact.
            continue
        return normalized
    return None


AUTHENTICATION_EXPORT_SECRET_FIELDS = {
    "access_token",
    "refresh_token",
    "access_token_hash",
    "refresh_token_hash",
}

AUTHENTICATION_EXPORT_FIELDS = (
    "id",
    "user_id",
    "device_info",
    "ip_address",
    "created_at",
    "last_active_at",
    "step_up_authenticated_at",
    "step_up_method",
)

USER_CONNECTION_EXPORT_SECRET_FIELDS = {
    "secrets",
}

USER_ARCHIVE_PROFILE_DENYLIST = {
    "hashed_password",
    "lock",
    "username",
    "gender",
    "date_of_birth",
    "bio",
    "group_id",
    "role",
    "is_active",
    "created_at",
    "last_active_at",
    "deleted_at",
    "deletion_scheduled_for",
}

USER_ARCHIVE_AUTH_SETTING_PAGES = {
    "secret",
    "login_2fa",
}

USER_ARCHIVE_PENDING_AUTH_SETTING_PAGES = {
    "social_login",
    "sso_login",
}

# These denormalized fields are live authentication bindings. Archives omit
# normalized identity rows, so retaining these settings would allow an import
# to restore a provider credential without proving it again.
USER_ARCHIVE_SOCIAL_IDENTITY_SETTING_KEYS = {
    "google_linked",
    "google_user_id",
    "github_linked",
    "github_user_id",
    "slack_linked",
    "slack_user_id",
    "microsoft_linked",
    "microsoft_user_id",
    "apple_linked",
    "apple_user_id",
}


def _serialize_models(rows: List[Any]) -> List[Dict[str, Any]]:
    """Serialize a list of SQLAlchemy model instances to dicts."""
    return [_model_as_dict(row) for row in rows]


def _serialize_query_models(query) -> List[Dict[str, Any]]:
    """Serialize query rows while fetching them in bounded batches."""
    return [_model_as_dict(row) for row in _iter_query_rows(query)]


def _stream_json_array_items(items: Iterable[Any]) -> Iterator[str]:
    yield "["
    first = True
    for item in items:
        if not first:
            yield ","
        first = False
        yield _json_dumps(_strip_nulls(item))
    yield "]"


def _stream_model_query_json_array(query, transform=None) -> Iterator[str]:
    yield "["
    first = True
    for row in _iter_query_rows(query):
        payload = transform(row) if transform else _model_as_dict(row)
        if not first:
            yield ","
        first = False
        yield _json_dumps(_strip_nulls(payload))
    yield "]"


def _stream_model_rows_json_array(rows: Iterable[Any], transform=None) -> Iterator[str]:
    yield "["
    first = True
    for row in rows:
        payload = transform(row) if transform else _model_as_dict(row)
        if not first:
            yield ","
        first = False
        yield _json_dumps(_strip_nulls(payload))
    yield "]"


def _stream_json_object_fields(
    fields: Iterable[tuple[str, Any, bool]],
) -> Iterator[str]:
    yield "{"
    first = True
    for key, value, is_stream in fields:
        if value is None:
            continue
        if not first:
            yield ","
        first = False
        yield _json_dumps(key)
        yield ":"
        if is_stream:
            yield from value
        else:
            yield _json_dumps(_strip_nulls(value))
    yield "}"


def _sanitize_user_archive_settings(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Remove reusable auth material and retired fields from settings archives."""
    if not isinstance(settings, dict):
        return {}
    sanitized = deepcopy(settings)
    for page_name in USER_ARCHIVE_AUTH_SETTING_PAGES:
        sanitized.pop(page_name, None)
    for page_name in USER_ARCHIVE_PENDING_AUTH_SETTING_PAGES:
        page = sanitized.get(page_name)
        if isinstance(page, dict):
            for key in list(page.keys()):
                if key.startswith("pending_"):
                    page.pop(key, None)
    social_settings = sanitized.get("social_login")
    if isinstance(social_settings, dict):
        for key in USER_ARCHIVE_SOCIAL_IDENTITY_SETTING_KEYS:
            social_settings.pop(key, None)

    # Older development archives can contain retired locale preferences both
    # as settings and as AI personal-context permissions. Strip both copies
    # before an archive is exported, validated, or restored.
    retired_locale_fields = {"currency", "date_format", "time_format", "week_start"}
    general_settings = sanitized.get("general")
    if isinstance(general_settings, dict):
        for field_name in retired_locale_fields:
            general_settings.pop(field_name, None)
    security_settings = sanitized.get("security")
    if isinstance(security_settings, dict):
        permissions = security_settings.get("allow_llm_to_access_personal_information")
        if isinstance(permissions, dict):
            for field_name in retired_locale_fields:
                permissions.pop(field_name, None)
    return sanitized


def _sanitize_user_profile_for_archive(
    profile: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Remove auth and lock state from exported/imported user profile data."""
    if not isinstance(profile, dict):
        return {}
    sanitized = deepcopy(profile)
    for field in USER_ARCHIVE_PROFILE_DENYLIST:
        sanitized.pop(field, None)
    if "settings" in sanitized:
        sanitized["settings"] = _sanitize_user_archive_settings(
            sanitized.get("settings")
        )
    return sanitized


def _require_imported_user_auth_reset(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Force password reset and MFA re-enrollment for archive-created users."""
    reset_settings = deepcopy(settings)
    security_settings = reset_settings.setdefault("security", {})
    if isinstance(security_settings, dict):
        security_settings["has_to_change_password"] = True
    else:
        reset_settings["security"] = {"has_to_change_password": True}

    login_2fa_settings = reset_settings.setdefault(
        "login_2fa", deepcopy(DEFAULT_USER_SETTINGS["login_2fa"])
    )
    if isinstance(login_2fa_settings, dict):
        login_2fa_settings["enable_2fa"] = False
        login_2fa_settings["provider"] = ""
    else:
        reset_settings["login_2fa"] = deepcopy(DEFAULT_USER_SETTINGS["login_2fa"])
    return reset_settings


def _serialize_authentication_record(record: Authentication) -> Dict[str, Any]:
    """Serialize non-secret authentication session metadata for data exports."""
    data: Dict[str, Any] = {}
    for field in AUTHENTICATION_EXPORT_FIELDS:
        value = getattr(record, field, None)
        if isinstance(value, datetime):
            data[field] = _serialize_datetime(value)
        elif isinstance(value, date):
            data[field] = _serialize_date(value)
        elif isinstance(value, Decimal):
            data[field] = _serialize_decimal(value)
        else:
            data[field] = value
    return data


def _serialize_authentication_records(
    rows: List[Authentication],
) -> List[Dict[str, Any]]:
    """Serialize authentication records without live credentials or token hashes."""
    return [_serialize_authentication_record(row) for row in rows]


def _serialize_user_connection_record(record) -> Dict[str, Any]:
    """Serialize connection metadata without reusable OAuth credentials."""
    data = _model_as_dict(record)
    for field in USER_CONNECTION_EXPORT_SECRET_FIELDS:
        data.pop(field, None)
    return data


EXPORT_ONLY_USER_DATA_SECTIONS = (
    "group",
    "auth",
    "activity_logs",
    "feedback",
    "usage_stats",
    "shared_agent_subscriptions",
)

SKIPPED_SECTION_SCAN_NODE_LIMIT = 10_000


def _section_has_import_data(value: Any) -> bool:
    """Return whether an exported section contains data worth reporting."""
    stack: List[Any] = [value]
    seen_containers: set[int] = set()
    visited_nodes = 0

    while stack:
        current = stack.pop()
        if current is None:
            continue

        visited_nodes += 1
        if visited_nodes > SKIPPED_SECTION_SCAN_NODE_LIMIT:
            # Treat oversized export-only sections as containing skipped data
            # rather than failing the entire import request.
            return True

        if isinstance(current, dict):
            container_id = id(current)
            if container_id in seen_containers:
                continue
            seen_containers.add(container_id)
            stack.extend(current.values())
            continue

        if isinstance(current, list):
            container_id = id(current)
            if container_id in seen_containers:
                continue
            seen_containers.add(container_id)
            stack.extend(current)
            continue

        if isinstance(current, str):
            if current.strip():
                return True
            continue

        return True

    return False


def _get_skipped_export_only_sections(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Report exported sections this importer intentionally leaves untouched."""
    skipped_sections: List[Dict[str, str]] = []
    for section in EXPORT_ONLY_USER_DATA_SECTIONS:
        if _section_has_import_data(payload.get(section)):
            skipped_sections.append(
                {
                    "section": section,
                    "reason": "export_only",
                }
            )
    return skipped_sections


def _export_user_chats(user_id: str, db) -> List[Dict[str, Any]]:
    """Export all chats and their feature-owned Deep Research data for a user."""
    from app.chats.download import _export_deep_research_runs_for_chat

    chats = list(
        _iter_query_rows(
            db.query(Chats)
            .filter(Chats.user_id == user_id)
            .order_by(Chats.created_at.asc(), Chats.id.asc())
        )
    )
    if not chats:
        return []

    chats = [chat for chat in chats if not is_chat_excluded_from_default_export(chat)]
    chat_ids = [chat.id for chat in chats if getattr(chat, "id", None)]
    messages_by_chat_id: Dict[str, List[Dict[str, Any]]] = {}

    if chat_ids:
        chunk_size = 500
        for index in range(0, len(chat_ids), chunk_size):
            chat_id_chunk = chat_ids[index : index + chunk_size]
            if not chat_id_chunk:
                continue
            messages = (
                db.query(ChatMessages)
                .filter(ChatMessages.chat_id.in_(chat_id_chunk))
                .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
            )
            for message in _iter_query_rows(messages):
                chat_id = str(getattr(message, "chat_id", "") or "")
                if not chat_id:
                    continue
                messages_by_chat_id.setdefault(chat_id, []).append(
                    _model_as_dict(message)
                )

    exported_chats: List[Dict[str, Any]] = []
    receipt_rows = (
        db.query(ChatReadState)
        .filter(ChatReadState.user_id == user_id, ChatReadState.chat_id.in_(chat_ids))
        .all()
        if chat_ids
        else []
    )
    read_versions = {
        str(row.chat_id): int(row.read_response_version or 0) for row in receipt_rows
    }
    for chat in chats:
        chat_payload = _model_as_dict(chat)
        chat_payload.pop("share_id", None)
        chat_payload.pop("response_version", None)
        chat_payload.pop("last_completed_generation_id", None)
        chat_payload["share"] = sanitize_chat_share_for_export(
            chat_payload.get("share")
        )
        chat_payload["has_unread_response"] = int(
            getattr(chat, "response_version", 0) or 0
        ) > read_versions.get(str(chat.id), 0)
        chat_payload["messages"] = messages_by_chat_id.get(str(chat.id), [])
        # Deep Research reports and artifacts live outside the chat/message
        # tables. Keep the materialized export path equivalent to the
        # streaming canonical archive so export_coverage remains truthful.
        chat_payload["deep_research_runs"] = _export_deep_research_runs_for_chat(
            user_id, chat.id, db
        )
        exported_chats.append(chat_payload)

    return exported_chats


def _chat_export_payload(
    chat: Chats, *, has_unread_response: bool | None = None
) -> Dict[str, Any]:
    """Serialize portable chat fields and optionally its normalized unread state."""
    chat_payload = _model_as_dict(chat)
    chat_payload.pop("share_id", None)
    chat_payload.pop("response_version", None)
    chat_payload.pop("last_completed_generation_id", None)
    chat_payload["share"] = sanitize_chat_share_for_export(chat_payload.get("share"))
    if has_unread_response is not None:
        chat_payload["has_unread_response"] = bool(has_unread_response)
    return chat_payload


def _stream_chat_messages_json_array(messages: Iterable[ChatMessages]) -> Iterator[str]:
    yield from _stream_model_rows_json_array(messages)


def _stream_chat_export_json(
    chat: Chats,
    messages: Iterable[ChatMessages],
    db,
    *,
    has_unread_response: bool | None = None,
) -> Iterator[str]:
    """Stream one canonical chat, including its feature-owned artifacts.

    Deep Research stores reports and generated artifacts outside the ordinary
    chat/message tables.  Keeping those rows nested with their owning chat
    makes the user bundle the complete migration boundary instead of requiring
    the retired chat-only archive as a second pass.
    """
    from app.chats.download import _export_deep_research_runs_for_chat

    chat_payload = _chat_export_payload(chat, has_unread_response=has_unread_response)
    fields: list[tuple[str, Any, bool]] = [
        (key, value, False) for key, value in chat_payload.items()
    ]
    fields.append(("messages", _stream_chat_messages_json_array(messages), True))
    fields.append(
        (
            "deep_research_runs",
            _export_deep_research_runs_for_chat(chat.user_id, chat.id, db),
            False,
        )
    )
    yield from _stream_json_object_fields(fields)


def _stream_user_chats_json_array(
    user_id: str,
    db,
    *,
    include_deleted_or_temp: bool = False,
) -> Iterator[str]:
    """Stream the user's chats, optionally including retained hidden records."""
    chats = list(
        _iter_query_rows(
            db.query(Chats)
            .filter(Chats.user_id == user_id)
            .order_by(Chats.created_at.asc(), Chats.id.asc())
        )
    )
    if not include_deleted_or_temp:
        chats = [
            chat for chat in chats if not is_chat_excluded_from_default_export(chat)
        ]
    chat_ids = [str(chat.id) for chat in chats if getattr(chat, "id", None)]
    receipt_rows = (
        db.query(ChatReadState)
        .filter(ChatReadState.user_id == user_id, ChatReadState.chat_id.in_(chat_ids))
        .all()
        if chat_ids
        else []
    )
    read_versions = {
        str(row.chat_id): int(row.read_response_version or 0) for row in receipt_rows
    }
    messages_by_chat_id: Dict[str, List[ChatMessages]] = {}
    if chat_ids:
        messages_query = (
            db.query(ChatMessages)
            .filter(ChatMessages.chat_id.in_(chat_ids))
            .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        )
        for message in _iter_query_rows(messages_query):
            chat_id = str(getattr(message, "chat_id", "") or "")
            if not chat_id:
                continue
            messages_by_chat_id.setdefault(chat_id, []).append(message)

    yield "["
    first = True
    for chat in chats:
        if not first:
            yield ","
        first = False
        has_unread_response = int(
            getattr(chat, "response_version", 0) or 0
        ) > read_versions.get(str(chat.id), 0)
        yield from _stream_chat_export_json(
            chat,
            messages_by_chat_id.get(str(chat.id), []),
            db,
            has_unread_response=has_unread_response,
        )
    yield "]"


def _export_user_activity_logs(user_id: str, db_log) -> Dict[str, Any]:
    """Export activity logs for a user."""
    if db_log is None:
        return {}

    try:
        audit_logs = (
            db_log.query(Logs)
            .filter(Logs.user_id == user_id)
            .order_by(Logs.timestamp.asc(), Logs.id.asc())
        )
        authentication_logs = (
            db_log.query(AuthenticationLogs)
            .filter(AuthenticationLogs.user_id == user_id)
            .order_by(AuthenticationLogs.timestamp.asc(), AuthenticationLogs.id.asc())
        )
        audit_log_payload = _serialize_query_models(audit_logs)
        authentication_log_payload = _serialize_query_models(authentication_logs)
    except Exception:
        logger.exception("Failed to export activity logs for user %s", user_id)
        return {}

    activity_payload: Dict[str, Any] = {}
    if audit_log_payload:
        activity_payload["audit_logs"] = audit_log_payload
    if authentication_log_payload:
        activity_payload["authentication_logs"] = authentication_log_payload
    return activity_payload


def _user_has_activity_logs(user_id: str, db_log) -> bool:
    if db_log is None:
        return False
    try:
        return bool(
            db_log.query(Logs.id).filter(Logs.user_id == user_id).first()
            or db_log.query(AuthenticationLogs.id)
            .filter(AuthenticationLogs.user_id == user_id)
            .first()
        )
    except Exception:
        logger.exception("Failed to inspect activity logs for user %s", user_id)
        return False


def _stream_user_activity_logs_json(user_id: str, db_log) -> Iterator[str]:
    if db_log is None:
        yield "{}"
        return
    fields: list[tuple[str, Any, bool]] = []
    try:
        if db_log.query(Logs.id).filter(Logs.user_id == user_id).first():
            fields.append(
                (
                    "audit_logs",
                    _stream_model_query_json_array(
                        db_log.query(Logs)
                        .filter(Logs.user_id == user_id)
                        .order_by(Logs.timestamp.asc(), Logs.id.asc())
                    ),
                    True,
                )
            )
        if (
            db_log.query(AuthenticationLogs.id)
            .filter(AuthenticationLogs.user_id == user_id)
            .first()
        ):
            fields.append(
                (
                    "authentication_logs",
                    _stream_model_query_json_array(
                        db_log.query(AuthenticationLogs)
                        .filter(AuthenticationLogs.user_id == user_id)
                        .order_by(
                            AuthenticationLogs.timestamp.asc(),
                            AuthenticationLogs.id.asc(),
                        )
                    ),
                    True,
                )
            )
    except Exception:
        logger.exception("Failed to stream activity logs for user %s", user_id)
        fields = []
    yield from _stream_json_object_fields(fields)


def _export_user_notes(user_id: str, db) -> Dict[str, Any]:
    """Export notes through the feature-owned, round-trippable contract.

    Keeping the note payload in its native format preserves version history
    and explicitly supported sharing metadata.
    """
    return export_user_notes(db, user_id)


def _stream_user_notes_json(user_id: str, db) -> Iterator[str]:
    """Stream the canonical notes payload as one JSON value.

    The outer archive remains streamed per user. Notes are materialized only
    for the current user so their feature exporter can correlate revisions and
    subscriptions without creating an all-users in-memory payload.
    """
    yield _json_dumps(_strip_nulls(_export_user_notes(user_id, db)))


def _export_user_todos(user_id: str, db) -> list[Dict[str, Any]]:
    """Export todo lists and todos for a user."""
    todo_lists = list(
        _iter_query_rows(
            db.query(TodoLists)
            .filter(TodoLists.user_id == user_id)
            .order_by(TodoLists.created_at.asc(), TodoLists.id.asc())
        )
    )
    if not todo_lists:
        return []

    exported: list[Dict[str, Any]] = []
    for todo_list in todo_lists:
        todos = (
            db.query(Todos)
            .filter(Todos.todo_list == todo_list.id)
            .order_by(Todos.order.asc(), Todos.created_at.asc(), Todos.id.asc())
        )
        payload = _model_as_dict(todo_list)
        payload["todos"] = _serialize_query_models(todos)
        exported.append(payload)
    return exported


def _stream_todo_list_export_json(todo_list: TodoLists, db) -> Iterator[str]:
    payload = _model_as_dict(todo_list)
    todos_query = (
        db.query(Todos)
        .filter(Todos.todo_list == todo_list.id)
        .order_by(Todos.order.asc(), Todos.created_at.asc(), Todos.id.asc())
    )
    fields: list[tuple[str, Any, bool]] = [
        (key, value, False) for key, value in payload.items()
    ]
    fields.append(("todos", _stream_model_query_json_array(todos_query), True))
    yield from _stream_json_object_fields(fields)


def _stream_user_todos_json_array(user_id: str, db) -> Iterator[str]:
    query = (
        db.query(TodoLists)
        .filter(TodoLists.user_id == user_id)
        .order_by(TodoLists.created_at.asc(), TodoLists.id.asc())
    )
    yield "["
    first = True
    for todo_list in _iter_query_rows(query):
        if not first:
            yield ","
        first = False
        yield from _stream_todo_list_export_json(todo_list, db)
    yield "]"


def _export_user_memories(user_id: str, db) -> Dict[str, Any]:
    """Export memories through the feature-owned portable contract."""
    return export_memories(db, MemoryScope.personal(user_id))


def _export_user_prompts(user_id: str, db) -> Dict[str, Any]:
    """Export prompt records owned by the user plus accepted shared prompt subscriptions."""
    from app.prompts.models import Prompts, SharedPromptSubscription

    prompts = (
        db.query(Prompts)
        .filter(Prompts.user_id == user_id)
        .order_by(Prompts.updated_at.asc(), Prompts.created_at.asc(), Prompts.id.asc())
    )
    subscriptions = (
        db.query(SharedPromptSubscription)
        .filter(SharedPromptSubscription.subscriber_id == user_id)
        .order_by(
            SharedPromptSubscription.subscribed_at.asc(),
            SharedPromptSubscription.id.asc(),
        )
    )
    return {
        "owned": _serialize_query_models(prompts),
        "subscriptions": _serialize_query_models(subscriptions),
    }


def _stream_user_prompts_json_array(user_id: str, db) -> Iterator[str]:
    from app.prompts.models import Prompts

    query = (
        db.query(Prompts)
        .filter(Prompts.user_id == user_id)
        .order_by(Prompts.updated_at.asc(), Prompts.created_at.asc(), Prompts.id.asc())
    )
    yield from _stream_model_query_json_array(query)


def _stream_shared_prompt_subscriptions_json_array(user_id: str, db) -> Iterator[str]:
    from app.prompts.models import SharedPromptSubscription

    query = (
        db.query(SharedPromptSubscription)
        .filter(SharedPromptSubscription.subscriber_id == user_id)
        .order_by(
            SharedPromptSubscription.subscribed_at.asc(),
            SharedPromptSubscription.id.asc(),
        )
    )
    yield from _stream_model_query_json_array(query)


def _export_user_connections(user_id: str, db) -> Dict[str, Any]:
    """Export user-managed connection metadata without OAuth credentials."""
    from app.connections.models import VALID_CONNECTION_PROVIDERS, UserConnection

    connections = (
        db.query(UserConnection)
        .filter(UserConnection.user_id == user_id)
        .order_by(
            UserConnection.provider.asc(),
            UserConnection.created_at.asc(),
            UserConnection.id.asc(),
        )
    )
    return {
        "connections": [
            _serialize_user_connection_record(row)
            for row in _iter_query_rows(connections)
            if getattr(row, "provider", None) in VALID_CONNECTION_PROVIDERS
        ],
    }


def _stream_user_connections_json_array(user_id: str, db) -> Iterator[str]:
    from app.connections.models import VALID_CONNECTION_PROVIDERS, UserConnection

    query = (
        db.query(UserConnection)
        .filter(UserConnection.user_id == user_id)
        .order_by(
            UserConnection.provider.asc(),
            UserConnection.created_at.asc(),
            UserConnection.id.asc(),
        )
    )
    yield from _stream_json_array_items(
        _serialize_user_connection_record(row)
        for row in _iter_query_rows(query)
        if getattr(row, "provider", None) in VALID_CONNECTION_PROVIDERS
    )


def _export_user_mcp_servers(user_id: str, db) -> List[Dict[str, Any]]:
    """Export remote personal MCP definitions without plaintext header secrets."""
    from app.mcp.models import (
        OWNER_USER,
        list_mcp_servers,
        serialize_mcp_server_export,
    )

    servers = list_mcp_servers(
        db,
        owner_type=OWNER_USER,
        owner_user_id=user_id,
        include_managed=False,
    )
    return [serialize_mcp_server_export(server) for server in servers]


def _export_user_model_setting_presets(user_id: str, db) -> List[Dict[str, Any]]:
    """Export saved per-user model presets."""
    from app.llm.models import ModelSettingPresets

    rows = (
        db.query(ModelSettingPresets)
        .filter(ModelSettingPresets.user_id == user_id)
        .order_by(
            ModelSettingPresets.model_id.asc(),
            ModelSettingPresets.name.asc(),
            ModelSettingPresets.id.asc(),
        )
    )
    return _serialize_query_models(rows)


def _stream_user_model_setting_presets_json_array(user_id: str, db) -> Iterator[str]:
    from app.llm.models import ModelSettingPresets

    query = (
        db.query(ModelSettingPresets)
        .filter(ModelSettingPresets.user_id == user_id)
        .order_by(
            ModelSettingPresets.model_id.asc(),
            ModelSettingPresets.name.asc(),
            ModelSettingPresets.id.asc(),
        )
    )
    yield from _stream_model_query_json_array(query)


USER_DATA_INLINE_CONTENT_KEY = "content_base64"
USER_DATA_INLINE_CONTENT_CHUNK_SIZE = 768 * 1024


def _normalize_export_relative_path(value: Any) -> PurePosixPath:
    """Normalize a stored relative path and reject traversal."""
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized:
        raise ValueError("relative_path is required")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("relative_path must stay inside the export root")
    if path.as_posix() in {"", "."}:
        raise ValueError("relative_path is required")
    return path


def _safe_child_path(root: Path, relative_path: PurePosixPath | str) -> Path:
    """Resolve a child path without allowing traversal outside root."""
    if not isinstance(relative_path, PurePosixPath):
        relative_path = _normalize_export_relative_path(relative_path)
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    target = (root_resolved / Path(*relative_path.parts)).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError("relative_path resolves outside export root")
    return target


def _file_content_base64(path: Path) -> str:
    """Read a file as base64 text for JSON export."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _iter_file_content_base64_chunks(path: Path) -> Iterator[str]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(USER_DATA_INLINE_CONTENT_CHUNK_SIZE)
            if not chunk:
                break
            yield base64.b64encode(chunk).decode("ascii")


def _stream_json_object_with_base64_field(
    fields: Iterable[tuple[str, Any]],
    content_path: Path,
    *,
    content_key: str = USER_DATA_INLINE_CONTENT_KEY,
    cleanup_path: Path | None = None,
) -> Iterator[str]:
    try:
        yield "{"
        first = True
        for key, value in fields:
            if value is None:
                continue
            if not first:
                yield ","
            first = False
            yield _json_dumps(key)
            yield ":"
            yield _json_dumps(_strip_nulls(value))
        if not first:
            yield ","
        yield _json_dumps(content_key)
        yield ':"'
        yield from _iter_file_content_base64_chunks(content_path)
        yield '"}'
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def _decode_export_content(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        return None


def _export_skill_files(user_id: str, skill_ids: list[str]) -> List[Dict[str, Any]]:
    """Export files stored in user skill directories."""
    if not skill_ids:
        return []
    from app.skills.models import _skill_directory

    entries: list[dict[str, Any]] = []
    for skill_id in skill_ids:
        try:
            skill_dir = _skill_directory(user_id, skill_id)
        except ValueError:
            continue
        if not skill_dir.exists() or not skill_dir.is_dir():
            continue
        for path in sorted(
            (candidate for candidate in skill_dir.rglob("*") if candidate.is_file()),
            key=lambda candidate: str(candidate.relative_to(skill_dir)).lower(),
        ):
            if path.is_symlink():
                continue
            relative_path = str(path.relative_to(skill_dir)).replace("\\", "/")
            try:
                _normalize_export_relative_path(relative_path)
                content = _file_content_base64(path)
            except Exception:
                logger.exception(
                    "Failed to export skill file %s for skill %s",
                    relative_path,
                    skill_id,
                )
                continue
            entries.append(
                {
                    "skill_id": skill_id,
                    "relative_path": relative_path,
                    "file_size": int(path.stat().st_size),
                    USER_DATA_INLINE_CONTENT_KEY: content,
                }
            )
    return entries


def _export_user_skills(user_id: str, db) -> Dict[str, Any]:
    """Export owned skills, accepted skill subscriptions, and skill files."""
    from app.skills.models import Skills, SharedSkillSubscription

    skills = (
        db.query(Skills)
        .filter(Skills.user_id == user_id)
        .order_by(Skills.created_at.asc(), Skills.id.asc())
    )
    subscriptions = (
        db.query(SharedSkillSubscription)
        .filter(SharedSkillSubscription.subscriber_id == user_id)
        .order_by(
            SharedSkillSubscription.subscribed_at.asc(),
            SharedSkillSubscription.id.asc(),
        )
    )
    skills_payload = _serialize_query_models(skills)
    skill_ids = [str(skill.get("id")) for skill in skills_payload if skill.get("id")]
    return {
        "owned": skills_payload,
        "subscriptions": _serialize_query_models(subscriptions),
        "files": _export_skill_files(user_id, skill_ids),
    }


def _query_user_skills(user_id: str, db):
    from app.skills.models import Skills

    return (
        db.query(Skills)
        .filter(Skills.user_id == user_id)
        .order_by(Skills.created_at.asc(), Skills.id.asc())
    )


def _query_user_skill_subscriptions(user_id: str, db):
    from app.skills.models import SharedSkillSubscription

    return (
        db.query(SharedSkillSubscription)
        .filter(SharedSkillSubscription.subscriber_id == user_id)
        .order_by(
            SharedSkillSubscription.subscribed_at.asc(),
            SharedSkillSubscription.id.asc(),
        )
    )


def _stream_skill_file_entry_json(
    skill_id: str, path: Path, relative_path: str, file_size: int
) -> Iterator[str]:
    yield from _stream_json_object_with_base64_field(
        [
            ("skill_id", skill_id),
            ("relative_path", relative_path),
            ("file_size", file_size),
        ],
        path,
    )


def _stream_user_skill_files_json_array(user_id: str, db) -> Iterator[str]:
    from app.skills.models import _skill_directory

    yield "["
    first = True
    for skill in _iter_query_rows(_query_user_skills(user_id, db)):
        skill_id = str(getattr(skill, "id", "") or "")
        if not skill_id:
            continue
        try:
            skill_dir = _skill_directory(user_id, skill_id)
        except ValueError:
            continue
        if not skill_dir.exists() or not skill_dir.is_dir():
            continue
        for path in sorted(
            (candidate for candidate in skill_dir.rglob("*") if candidate.is_file()),
            key=lambda candidate: str(candidate.relative_to(skill_dir)).lower(),
        ):
            if path.is_symlink():
                continue
            relative_path = str(path.relative_to(skill_dir)).replace("\\", "/")
            try:
                _normalize_export_relative_path(relative_path)
                file_size = int(path.stat().st_size)
            except Exception:
                logger.exception(
                    "Failed to export skill file %s for skill %s",
                    relative_path,
                    skill_id,
                )
                continue
            if not first:
                yield ","
            first = False
            yield from _stream_skill_file_entry_json(
                skill_id, path, relative_path, file_size
            )
    yield "]"


def _export_user_file_folders(user_id: str, db) -> Dict[str, Any]:
    """Export file folders and accepted shared-folder subscriptions."""
    from app.file_folders.models import FileFolders, SharedFileFolderSubscription

    folders = (
        db.query(FileFolders)
        .filter(FileFolders.user_id == user_id)
        .order_by(
            FileFolders.order.asc(), FileFolders.created_at.asc(), FileFolders.id.asc()
        )
    )
    subscription_rows = (
        db.query(SharedFileFolderSubscription)
        .filter(SharedFileFolderSubscription.subscriber_id == user_id)
        .order_by(
            SharedFileFolderSubscription.subscribed_at.asc(),
            SharedFileFolderSubscription.id.asc(),
        )
    )
    serialized_folders = _serialize_query_models(folders)
    subscriptions = list(_iter_query_rows(subscription_rows))
    folder_ids = {
        str(getattr(subscription, "folder_id", "") or "").strip()
        for subscription in subscriptions
        if str(getattr(subscription, "folder_id", "") or "").strip()
    }
    folders_by_id: Dict[str, Any] = {}
    if folder_ids:
        folders_by_id = {
            str(folder.id): folder
            for folder in db.query(FileFolders)
            .filter(FileFolders.id.in_(sorted(folder_ids)))
            .all()
        }
    return {
        "owned": serialized_folders,
        "subscriptions": [
            _serialize_shared_file_folder_subscription_for_export(
                subscription, folders_by_id.get(str(subscription.folder_id))
            )
            for subscription in subscriptions
        ],
    }


def _query_user_file_folders(user_id: str, db):
    from app.file_folders.models import FileFolders

    return (
        db.query(FileFolders)
        .filter(FileFolders.user_id == user_id)
        .order_by(
            FileFolders.order.asc(), FileFolders.created_at.asc(), FileFolders.id.asc()
        )
    )


def _query_user_file_folder_subscriptions(user_id: str, db):
    from app.file_folders.models import SharedFileFolderSubscription

    return (
        db.query(SharedFileFolderSubscription)
        .filter(SharedFileFolderSubscription.subscriber_id == user_id)
        .order_by(
            SharedFileFolderSubscription.subscribed_at.asc(),
            SharedFileFolderSubscription.id.asc(),
        )
    )


def _share_id_for_folder_subscription(folder, share_type: Any) -> str | None:
    normalized_share_type = str(share_type or "").strip().lower()
    if folder is None:
        return None
    if normalized_share_type == "live":
        return str(getattr(folder, "live_share_id", "") or "").strip() or None
    if normalized_share_type == "collaborate":
        return str(getattr(folder, "collaborate_share_id", "") or "").strip() or None
    if normalized_share_type == "clone":
        return str(getattr(folder, "clone_share_id", "") or "").strip() or None
    return None


def _serialize_shared_file_folder_subscription_for_export(
    subscription, folder
) -> Dict[str, Any]:
    entry = _model_as_dict(subscription)
    target_share_id = _share_id_for_folder_subscription(folder, entry.get("share_type"))
    if target_share_id:
        entry["target_share_id"] = target_share_id
    if folder is not None:
        entry["target_folder_name"] = getattr(folder, "name", None)
        entry["target_folder_owner_user_id"] = getattr(folder, "user_id", None)
    return _strip_nulls(entry)


def _stream_user_file_folder_subscriptions_json_array(
    user_id: str, db
) -> Iterator[str]:
    exported = _export_user_file_folders(user_id, db).get("subscriptions", [])
    yield "["
    first = True
    for entry in exported:
        if not first:
            yield ","
        first = False
        yield _json_dumps(entry)
    yield "]"


def _export_agent_asset_entry(asset) -> Dict[str, Any]:
    """Export an agent asset record with inline content when available."""
    from app.files.utils import materialize_file_record

    entry = _model_as_dict(asset)
    source_stub = type(
        "AgentAssetExportStub",
        (),
        {
            "id": asset.id,
            "file_name": asset.file_name,
            "storage_provider": asset.storage_provider,
            "storage_key": asset.storage_key,
        },
    )()
    try:
        materialized = materialize_file_record(source_stub, asset.owner_user_id)
        entry[USER_DATA_INLINE_CONTENT_KEY] = _file_content_base64(materialized)
    except Exception:
        logger.exception(
            "Failed to export agent asset content for asset %s",
            getattr(asset, "id", None),
        )
        entry["content_unavailable"] = True
    return entry


def _agent_asset_materialized_path(asset) -> Path | None:
    from app.files.utils import materialize_file_record

    source_stub = type(
        "AgentAssetExportStub",
        (),
        {
            "id": asset.id,
            "file_name": asset.file_name,
            "storage_provider": asset.storage_provider,
            "storage_key": asset.storage_key,
        },
    )()
    try:
        return materialize_file_record(source_stub, asset.owner_user_id)
    except Exception:
        logger.exception(
            "Failed to export agent asset content for asset %s",
            getattr(asset, "id", None),
        )
        return None


def _export_user_agents(user_id: str, db) -> Dict[str, Any]:
    """Export user-owned agents and assets attached to those agents."""
    from app.agents.models import SharedUserAgentSubscription, UserAgent, UserAgentAsset

    agents = (
        db.query(UserAgent)
        .filter(UserAgent.user_id == user_id)
        .order_by(UserAgent.created_at.asc(), UserAgent.id.asc())
    )
    agents_payload = _serialize_query_models(agents)
    agent_ids = [str(agent.get("id")) for agent in agents_payload if agent.get("id")]
    subscriptions = (
        db.query(SharedUserAgentSubscription)
        .filter(SharedUserAgentSubscription.subscriber_id == user_id)
        .order_by(
            SharedUserAgentSubscription.subscribed_at.asc(),
            SharedUserAgentSubscription.id.asc(),
        )
    )

    if agent_ids:
        assets = (
            db.query(UserAgentAsset)
            .filter(UserAgentAsset.agent_id.in_(agent_ids))
            .order_by(UserAgentAsset.created_at.asc(), UserAgentAsset.id.asc())
        )
    else:
        assets = ()

    return {
        "owned": agents_payload,
        "subscriptions": _serialize_query_models(subscriptions),
        "assets": [
            _export_agent_asset_entry(asset) for asset in _iter_query_rows(assets)
        ],
    }


def _query_user_agents(user_id: str, db):
    from app.agents.models import UserAgent

    return (
        db.query(UserAgent)
        .filter(UserAgent.user_id == user_id)
        .order_by(UserAgent.created_at.asc(), UserAgent.id.asc())
    )


def _query_user_agent_subscriptions(user_id: str, db):
    from app.agents.models import SharedUserAgentSubscription

    return (
        db.query(SharedUserAgentSubscription)
        .filter(SharedUserAgentSubscription.subscriber_id == user_id)
        .order_by(
            SharedUserAgentSubscription.subscribed_at.asc(),
            SharedUserAgentSubscription.id.asc(),
        )
    )


def _query_user_agent_assets(user_id: str, db):
    from sqlalchemy import or_
    from app.agents.models import UserAgent, UserAgentAsset

    agent_ids = db.query(UserAgent.id).filter(UserAgent.user_id == user_id)
    return (
        db.query(UserAgentAsset)
        .filter(
            or_(
                UserAgentAsset.owner_user_id == user_id,
                UserAgentAsset.agent_id.in_(agent_ids),
            )
        )
        .order_by(UserAgentAsset.created_at.asc(), UserAgentAsset.id.asc())
    )


def _stream_agent_asset_entry_json(asset) -> Iterator[str]:
    entry = _strip_nulls(_model_as_dict(asset))
    content_path = _agent_asset_materialized_path(asset)
    if content_path is None:
        entry["content_unavailable"] = True
        yield _json_dumps(entry)
        return
    yield from _stream_json_object_with_base64_field(entry.items(), content_path)


def _stream_user_agent_assets_json_array(user_id: str, db) -> Iterator[str]:
    yield "["
    first = True
    for asset in _iter_query_rows(_query_user_agent_assets(user_id, db)):
        if not first:
            yield ","
        first = False
        yield from _stream_agent_asset_entry_json(asset)
    yield "]"


def _export_user_usage_stats(user_id: str, db) -> Dict[str, Any]:
    """Export LLM and tool usage statistics tied to the user."""
    from app.llmstats.models import export_llm_generation_stats, export_tool_call_stats

    return {
        "llm_generation_stats": export_llm_generation_stats(db, user_id=user_id),
        "tool_call_stats": export_tool_call_stats(db, user_id=user_id),
    }


def _stream_llm_generation_stats_export_json(user_id: str, db) -> Iterator[str]:
    from app.llmstats.models import (
        LLMGenerationStatistic,
        _serialize_datetime_value,
        current_llm_generation_stats_export_version,
        sanitize_provider_error_message,
    )

    base_query = db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.user_id == user_id
    )
    total_count = base_query.count()
    stats_query = base_query.order_by(LLMGenerationStatistic.created_at.desc())

    def serialize_stat(stat):
        status_payload = (
            dict(stat.status or {}) if isinstance(stat.status, dict) else {}
        )
        if "error_message" in status_payload:
            status_payload["error_message"] = sanitize_provider_error_message(
                status_payload.get("error_message")
            )
        return {
            "id": stat.id,
            "model_name": stat.model_name,
            "model_id": stat.model_id,
            "provider": stat.provider,
            "provider_id": stat.provider_id,
            "category": stat.category,
            "status": status_payload,
            "meta": stat.meta or {},
            "user_id": stat.user_id,
            "is_byok": bool(stat.is_byok),
            "created_at": _serialize_datetime_value(stat.created_at),
        }

    yield from _stream_json_object_fields(
        [
            ("export_type", "llm_generation_stats", False),
            ("export_version", current_llm_generation_stats_export_version, False),
            (
                "exported_at",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                False,
            ),
            (
                "data",
                _stream_json_object_fields(
                    [
                        (
                            "statistics",
                            _stream_model_query_json_array(
                                stats_query, transform=serialize_stat
                            ),
                            True,
                        ),
                        ("total_count", total_count, False),
                    ]
                ),
                True,
            ),
        ]
    )


def _stream_tool_call_stats_export_json(user_id: str, db) -> Iterator[str]:
    from app.llmstats.models import (
        ToolCallStatistic,
        _serialize_datetime_value,
        current_tool_call_stats_export_version,
        sanitize_provider_error_message,
    )

    base_query = db.query(ToolCallStatistic).filter(
        ToolCallStatistic.user_id == user_id
    )
    total_count = base_query.count()
    stats_query = base_query.order_by(ToolCallStatistic.created_at.desc())

    def serialize_stat(stat):
        return {
            "id": stat.id,
            "tool_name": stat.tool_name,
            "success": stat.success,
            "error_message": sanitize_provider_error_message(stat.error_message)
            or None,
            "execution_time": stat.execution_time,
            "model_id": stat.model_id,
            "model_name": stat.model_name,
            "provider": stat.provider,
            "user_id": stat.user_id,
            "is_byok": bool(stat.is_byok),
            "meta": stat.meta or {},
            "created_at": _serialize_datetime_value(stat.created_at),
        }

    yield from _stream_json_object_fields(
        [
            ("export_type", "tool_call_stats", False),
            ("export_version", current_tool_call_stats_export_version, False),
            (
                "exported_at",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                False,
            ),
            (
                "data",
                _stream_json_object_fields(
                    [
                        (
                            "statistics",
                            _stream_model_query_json_array(
                                stats_query, transform=serialize_stat
                            ),
                            True,
                        ),
                        ("total_count", total_count, False),
                    ]
                ),
                True,
            ),
        ]
    )


def _stream_user_usage_stats_json(user_id: str, db) -> Iterator[str]:
    yield from _stream_json_object_fields(
        [
            (
                "llm_generation_stats",
                _stream_llm_generation_stats_export_json(user_id, db),
                True,
            ),
            ("tool_call_stats", _stream_tool_call_stats_export_json(user_id, db), True),
        ]
    )


def _presentation_artifact_relative_paths(slide_count: int) -> list[str]:
    relative_paths = ["metadata.json", "title.txt", "presentation.html"]
    for index in range(1, max(0, int(slide_count or 0)) + 1):
        relative_paths.append(f"images/slide_{index}.png")
    return relative_paths


def _read_presentation_artifact_bytes(record, relative_path: str) -> bytes | None:
    storage_provider = (
        str(getattr(record, "storage_provider", "") or "local").strip().lower()
        or "local"
    )
    storage_prefix = (
        str(getattr(record, "storage_prefix", "") or "").strip().strip("/\\")
    )
    if not storage_prefix:
        return None
    storage_key = f"{storage_prefix}/{relative_path}"
    try:
        if storage_provider == "local":
            from app.files.utils import BASE_STORAGE_DIR

            path = _safe_child_path(BASE_STORAGE_DIR, PurePosixPath(storage_key))
            if not path.exists() or not path.is_file():
                return None
            return path.read_bytes()

        from app.files.storage import download_file_from_storage

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            download_file_from_storage(storage_provider, storage_key, temp_path)
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                return None
            return temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception:
        logger.exception(
            "Failed to export slide presentation artifact %s for presentation %s",
            relative_path,
            getattr(record, "id", None),
        )
        return None


def _materialize_presentation_artifact_path(
    record, relative_path: str
) -> tuple[Path, Path | None] | tuple[None, None]:
    storage_provider = (
        str(getattr(record, "storage_provider", "") or "local").strip().lower()
        or "local"
    )
    storage_prefix = (
        str(getattr(record, "storage_prefix", "") or "").strip().strip("/\\")
    )
    if not storage_prefix:
        return None, None
    storage_key = f"{storage_prefix}/{relative_path}"
    try:
        if storage_provider == "local":
            from app.files.utils import BASE_STORAGE_DIR

            path = _safe_child_path(BASE_STORAGE_DIR, PurePosixPath(storage_key))
            if not path.exists() or not path.is_file():
                return None, None
            return path, None

        from app.files.storage import download_file_from_storage

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            download_file_from_storage(storage_provider, storage_key, temp_path)
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                temp_path.unlink(missing_ok=True)
                return None, None
            return temp_path, temp_path
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    except Exception:
        logger.exception(
            "Failed to export slide presentation artifact %s for presentation %s",
            relative_path,
            getattr(record, "id", None),
        )
        return None, None


def _export_slide_presentation_artifacts(record) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for relative_path in _presentation_artifact_relative_paths(
        getattr(record, "slide_count", 0)
    ):
        content = _read_presentation_artifact_bytes(record, relative_path)
        if content is None:
            continue
        artifacts.append(
            {
                "relative_path": relative_path,
                "file_size": len(content),
                USER_DATA_INLINE_CONTENT_KEY: base64.b64encode(content).decode("ascii"),
            }
        )
    return artifacts


def _export_user_slide_presentations(user_id: str, db) -> List[Dict[str, Any]]:
    """Export slide presentation index records and available presentation artifacts."""
    from app.tools.slide_presentation.models import SlidePresentations

    rows = (
        db.query(SlidePresentations)
        .filter(SlidePresentations.user_id == user_id)
        .order_by(SlidePresentations.created_at.asc(), SlidePresentations.id.asc())
    )
    exported: list[dict[str, Any]] = []
    for row in _iter_query_rows(rows):
        payload = _model_as_dict(row)
        payload["artifacts"] = _export_slide_presentation_artifacts(row)
        exported.append(payload)
    return exported


def _query_user_slide_presentations(user_id: str, db):
    from app.tools.slide_presentation.models import SlidePresentations

    return (
        db.query(SlidePresentations)
        .filter(SlidePresentations.user_id == user_id)
        .order_by(SlidePresentations.created_at.asc(), SlidePresentations.id.asc())
    )


def _stream_slide_presentation_artifacts_json_array(record) -> Iterator[str]:
    yield "["
    first = True
    for relative_path in _presentation_artifact_relative_paths(
        getattr(record, "slide_count", 0)
    ):
        content_path, cleanup_path = _materialize_presentation_artifact_path(
            record, relative_path
        )
        if content_path is None:
            continue
        try:
            file_size = int(content_path.stat().st_size)
        except OSError:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)
            continue
        if not first:
            yield ","
        first = False
        yield from _stream_json_object_with_base64_field(
            [
                ("relative_path", relative_path),
                ("file_size", file_size),
            ],
            content_path,
            cleanup_path=cleanup_path,
        )
    yield "]"


def _stream_slide_presentation_json(record) -> Iterator[str]:
    payload = _model_as_dict(record)
    fields: list[tuple[str, Any, bool]] = [
        (key, value, False) for key, value in payload.items()
    ]
    fields.append(
        ("artifacts", _stream_slide_presentation_artifacts_json_array(record), True)
    )
    yield from _stream_json_object_fields(fields)


def _stream_user_slide_presentations_json_array(user_id: str, db) -> Iterator[str]:
    yield "["
    first = True
    for record in _iter_query_rows(_query_user_slide_presentations(user_id, db)):
        if not first:
            yield ","
        first = False
        yield from _stream_slide_presentation_json(record)
    yield "]"


def _build_user_data_export_coverage() -> Dict[str, Any]:
    """Describe which account surfaces are exported versus intentionally excluded."""
    included_sections = [
        "user",
        "settings",
        "group",
        "auth",
        "activity_logs",
        "chats",
        "notes",
        "todos",
        "files",
        "file_folders",
        "shared_file_folder_subscriptions",
        "projects",
        "automations",
        "feedback",
        "skills",
        "skill_files",
        "shared_skill_subscriptions",
        "agents",
        "agent_assets",
        "shared_agent_subscriptions",
        "prompts",
        "shared_prompt_subscriptions",
        "user_connections",
        "mcp_servers",
        "model_setting_presets",
        "usage_stats",
        "slide_presentations",
        "deep_research_runs",
    ]
    included_sections.insert(8, "memories")
    excluded_sections = [
        {
            "section": "social_auth_identities",
            "reason": "Social sign-in bindings are credentials and must be proven again with the provider rather than restored from an archive.",
        },
        {
            "section": "connection_oauth_states",
            "reason": "OAuth handshake state is temporary security material and must be recreated by reconnecting the provider after import.",
        },
        {
            "section": "email_delivery_outbox",
            "reason": "Queued system messages contain transient encrypted delivery material and must never be replayed by a user-data import.",
        },
        {
            "section": "pending_email_changes",
            "reason": "Pending email-change proofs are short-lived authentication material and must be requested again after import.",
        },
        {
            "section": "pending_auth_actions",
            "reason": "One-time browser authentication actions are instance-bound credentials and must be recreated after import.",
        },
        {
            "section": "trusted_device_notifications",
            "reason": "Opaque new-device notification markers are instance security state and are safely re-established by future sign-ins.",
        },
        {
            "section": "scim",
            "reason": "SCIM links and memberships are admin-managed provisioning state rather than self-service portable content.",
        },
        {
            "section": "user_notifications",
            "reason": "User notifications can be shared or broadcast records and are instance-managed rather than part of user archives.",
        },
    ]
    return {
        "included_sections": included_sections,
        "excluded_sections": excluded_sections,
    }


def _build_user_data_export_core(
    user_id: str, db
) -> tuple[User, Dict[str, Any], Dict[str, Any], Dict[str, Any] | None]:
    user = get_user(db, user_id, None)
    profile = _sanitize_user_profile_for_archive(_model_as_dict(user))
    if isinstance(profile, dict):
        profile = _sanitize_user_profile_export(profile)
        profile["email"] = user.email
        profile["user_id"] = user.id
    settings = _sanitize_user_archive_settings(get_user_settings(user_id, db))

    group_payload: Dict[str, Any] | None = None
    if getattr(user, "group_id", None):
        group_payload = {"id": user.group_id}
        try:
            group = get_group(db, user.group_id)
            if group:
                group_payload["name"] = getattr(group, "name", None)
        except Exception:
            logger.exception("Failed to export group metadata for user %s", user_id)

    return user, profile, settings, group_payload


def _query_user_projects(user_id: str, db):
    return (
        db.query(Project)
        .filter(Project.user_id == user_id)
        .order_by(Project.created_at.asc(), Project.id.asc())
    )


def _query_user_automations(user_id: str, db):
    return (
        db.query(Automation)
        .filter(Automation.user_id == user_id)
        .order_by(Automation.created_at.asc(), Automation.id.asc())
    )


def _query_user_feedback(user_id: str, db):
    return (
        db.query(ModelFeedback)
        .filter(ModelFeedback.user_id == user_id)
        .order_by(ModelFeedback.created_at.asc(), ModelFeedback.id.asc())
    )


def _query_user_authentication_records(user_id: str, db):
    query = (
        db.query(Authentication)
        .filter(Authentication.user_id == user_id)
        .order_by(Authentication.created_at.asc(), Authentication.id.asc())
    )
    # Authentication access/refresh tokens are never exported. Avoid loading
    # those encrypted columns at all so stale or corrupted token ciphertext
    # cannot break a user data export.
    if hasattr(query, "options"):
        query = query.options(
            load_only(
                *(
                    getattr(Authentication, field)
                    for field in AUTHENTICATION_EXPORT_FIELDS
                )
            )
        )
    return query


def _stream_authentication_records_json_array(query) -> Iterator[str]:
    yield from _stream_model_query_json_array(
        query, transform=_serialize_authentication_record
    )


def _sanitize_user_profile_export(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Remove non-portable account fields from an exported user profile."""
    return _sanitize_user_profile_for_archive(profile)
