from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import hmac
import json
import logging
import math
import re
import threading
import time
from typing import Any
import uuid
from urllib.parse import quote, urlparse, urlunparse

import httpx
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.utils import resolve_selected_model_for_user
from app.auth.jwt_material import get_jwt_material
from app.chats.models import Chats, ChatMessages, create_chat, create_chat_message, get_chat
from app.database import SessionLocal
from app.files.utils import get_file_info, extract_text_from_file_info
from app.llm.google_aistudio.realtime import (
    build_google_aistudio_live_client_setup,
    build_google_aistudio_live_session_config,
    get_google_aistudio_live_models,
    get_google_aistudio_live_default_voice,
)
from app.llm.models import (
    Models,
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    finalize_duration_rate_limit_admission,
    touch_duration_rate_limit_admission,
)
from app.llm.models import get_llm_provider
from app.llm.openai.realtime import get_openai_realtime_models
from app.llm.openai.utils import _resolve_openai_client_kwargs, upload_files
from app.llm.xai.realtime import (
    build_xai_realtime_session_config,
    get_xai_realtime_models,
    normalize_xai_realtime_voice,
)
from app.llm.provider_request import (
    ProviderRequest,
    REQUEST_TYPE_TITLE_GENERATION,
    call_provider_title_generation,
)
from app.llm.schemas import ProviderEnum, provider_api_key_is_optional
from app.llm.system_instruction.title import get_title_generation_prompt
from app.llm.helper import build_file_metadata_text, build_tool_call_block
from app.realtime.models import (
    RealtimeSession,
    create_realtime_session,
    get_realtime_session_by_session_id,
    list_active_realtime_sessions,
    list_active_realtime_sessions_for_user,
    update_realtime_session,
)
from app.llmstats.models import (
    INTERACTION_TYPE_REALTIME_RESPONSE,
    LLMGenerationStatistic,
    ToolCallStatistic,
    create_realtime_response_statistic,
    create_tool_call_statistic,
    tool_statistics_context,
)
from app.skills.models import get_skill_content_for_user, get_skill_file_descriptors_by_category_for_user
from app.settings.models import get_settings_page
from app.tools.helper import resolve_tool_call
from app.tools.utils import resolve_enabled_tools
from app.utils.utils import sanitize_chat_text


logger = logging.getLogger(__name__)

DEFAULT_REALTIME_INSTRUCTIONS = "You are a natural voice assistant. Be helpful and concise."
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
OPENAI_REALTIME_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
}
WEBSOCKET_REALTIME_PROVIDER_TYPES = {
    ProviderEnum.google_aistudio.value,
    ProviderEnum.xai.value,
}
REALTIME_SESSION_IDLE_TTL = timedelta(minutes=30)
REALTIME_SESSION_MAX_LIFETIME = timedelta(hours=4)
OPENAI_REALTIME_SESSION_MAX_LIFETIME = timedelta(minutes=60)
REALTIME_RUNTIME_SECRET_SETTING_KEYS = {
    "api_key",
    "base_url",
    "organization",
    "project",
    "provider",
    "provider_id",
}
REALTIME_RUNTIME_CLEANUP_BATCH_SIZE = 1000
REALTIME_TOOL_ARGUMENTS_MAX_BYTES = 64 * 1024
OPENAI_REALTIME_SAFETY_IDENTIFIER_MAX_LENGTH = 64
OPENAI_REALTIME_SAFETY_IDENTIFIER_PREFIX = "omlorix_"
REALTIME_PROVIDER_CONNECTION_IDLE = "idle"
REALTIME_PROVIDER_CONNECTION_CONNECTING = "connecting"
REALTIME_PROVIDER_CONNECTION_ACTIVE = "active"
REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING = "termination_pending"
REALTIME_PROVIDER_CONNECTION_TERMINATED = "terminated"
REALTIME_PROVIDER_CONNECTION_STATES = {
    REALTIME_PROVIDER_CONNECTION_IDLE,
    REALTIME_PROVIDER_CONNECTION_CONNECTING,
    REALTIME_PROVIDER_CONNECTION_ACTIVE,
    REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING,
    REALTIME_PROVIDER_CONNECTION_TERMINATED,
}
REALTIME_PROVIDER_CONTROL_STATE_KEYS = (
    "provider_connection_state",
    "provider_connection_owner_id",
    "provider_session_handle",
    "provider_connection_started_at",
    "provider_connection_last_seen_at",
    "provider_stop_requested_at",
    "provider_stop_reason",
    "google_session_handle",
)
# Provider-owned transports publish every ten seconds. A one-minute fence
# tolerates transient scheduler/database stalls without allowing a worker to
# declare a healthy proxy dead and admit a second upstream connection.
REALTIME_PROVIDER_ACTIVITY_STALE_AFTER = timedelta(seconds=60)
REALTIME_PROVIDER_CONNECTING_STALE_AFTER = timedelta(seconds=90)
REALTIME_PROVIDER_TERMINATION_RETRY_AFTER = timedelta(seconds=10)
_OPENAI_REALTIME_CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_REALTIME_PROVIDER_CONNECTION_LOCK_PREFIX = "omlorix:realtime:provider:"
# Signaling itself has a 20-second provider timeout. Stops must be able to wait
# through that complete bounded operation so a browser cancellation is not
# dropped merely because the offer is still awaiting its provider response.
_REALTIME_PROVIDER_CONNECTION_LOCK_TIMEOUT = "30s"


FIXED_MODEL_SKILL_OVERRIDE_ERROR = "This model has a fixed skill. Remove selected skills and try again."


def _normalize_realtime_skill_ids(skill_id: str | None = None, skill_ids: list[str] | None = None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    def _push(raw_value: Any) -> None:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        normalized.append(value)

    if skill_id is not None:
        _push(skill_id)
    if isinstance(skill_ids, list):
        for item in skill_ids:
            _push(item)
    return normalized


def _extract_realtime_model_skill_ids(model_settings: dict[str, Any] | None) -> list[str]:
    settings = model_settings if isinstance(model_settings, dict) else {}
    model_skill_ids = settings.get("skill_ids")
    if isinstance(model_skill_ids, list):
        return _normalize_realtime_skill_ids(skill_ids=model_skill_ids)
    return _normalize_realtime_skill_ids(skill_id=settings.get("skill_id"))


def _serialize_runtime_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _deserialize_runtime_datetime(value: Any, *, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if default is not None:
        return default if default.tzinfo else default.replace(tzinfo=timezone.utc)
    return _utc_now()


def _deserialize_optional_runtime_datetime(value: Any) -> datetime | None:
    """Deserialize an optional runtime timestamp without inventing activity."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _deserialize_runtime_datetime(value)


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_realtime_tool_schemas(raw_tool_schemas: list[Any] | None) -> list[dict[str, Any]]:
    normalized_tool_schemas: list[dict[str, Any]] = []
    for index, schema in enumerate(raw_tool_schemas or []):
        if not isinstance(schema, dict):
            logger.warning("Skipping realtime tool schema at index %s because it is not an object", index)
            continue

        schema_type = str(schema.get("type") or "").strip()
        if schema_type:
            if schema_type == "function" and isinstance(schema.get("function"), dict):
                function_schema = schema.get("function") or {}
                function_name = str(function_schema.get("name") or "").strip()
                function_parameters = function_schema.get("parameters")
                if function_name and isinstance(function_parameters, dict):
                    normalized_tool_schemas.append(
                        {
                            "type": "function",
                            "name": function_name,
                            "description": function_schema.get("description"),
                            "parameters": function_parameters,
                        }
                    )
                    continue
                logger.warning(
                    "Skipping realtime function tool schema at index %s because function name/parameters are invalid",
                    index,
                )
                continue
            normalized_tool_schemas.append(schema)
            continue

        tool_name = str(schema.get("name") or "").strip()
        tool_parameters = schema.get("parameters")
        if tool_name and isinstance(tool_parameters, dict):
            normalized_tool_schemas.append(
                {
                    "type": "function",
                    "name": tool_name,
                    "description": schema.get("description"),
                    "parameters": tool_parameters,
                }
            )
            continue

        function_schema = schema.get("function")
        if isinstance(function_schema, dict):
            function_name = str(function_schema.get("name") or "").strip()
            function_parameters = function_schema.get("parameters")
            if function_name and isinstance(function_parameters, dict):
                normalized_tool_schemas.append(
                    {
                        "type": "function",
                        "name": function_name,
                        "description": function_schema.get("description"),
                        "parameters": function_parameters,
                    }
                )
                continue

        logger.warning("Skipping realtime tool schema at index %s because it is missing required type/name shape", index)
    return normalized_tool_schemas


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_realtime_completed_at(
    value: Any,
    *,
    session_started_at: datetime,
    server_now: datetime | None = None,
) -> datetime:
    """Keep browser-reported completion time inside the server session window.

    The timestamp helps order provider responses but arrives through an
    untrusted browser. Clamping rather than rejecting preserves the transcript
    and usage fact while preventing arbitrary past or future analytics dates.
    """
    now = server_now or _utc_now()
    normalized_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    normalized_start = (
        session_started_at
        if session_started_at.tzinfo
        else session_started_at.replace(tzinfo=timezone.utc)
    )
    lower_bound = min(normalized_start.astimezone(timezone.utc), normalized_now)
    if not isinstance(value, datetime):
        return normalized_now
    normalized_value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    normalized_value = normalized_value.astimezone(timezone.utc)
    return min(max(normalized_value, lower_bound), normalized_now)


def _billable_realtime_elapsed_seconds(
    runtime: "RealtimeSessionRuntime",
    ended_at: datetime,
) -> int:
    """Round an admitted call's wall-clock duration up to one second."""
    elapsed = max((ended_at - runtime.created_at).total_seconds(), 0.0)
    return max(1, int(math.ceil(elapsed)))


def touch_realtime_runtime(runtime: "RealtimeSessionRuntime") -> None:
    """Refresh the idle deadline for an active direct-provider session."""
    runtime.last_activity_at = _utc_now()


def _normalize_realtime_tool_name(value: str | None) -> str:
    return str(value or "").strip()


def _tool_schema_name(schema: dict[str, Any]) -> str | None:
    if not isinstance(schema, dict):
        return None
    direct_name = _normalize_realtime_tool_name(schema.get("name"))
    if direct_name:
        return direct_name
    function_schema = schema.get("function")
    if isinstance(function_schema, dict):
        function_name = _normalize_realtime_tool_name(function_schema.get("name"))
        if function_name:
            return function_name
    return None


def validate_realtime_tool_arguments(
    runtime: "RealtimeSessionRuntime",
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Validate an untrusted browser tool request against its session schema.

    Realtime provider events arrive in the browser, so the backend must treat
    both the call metadata and arguments as user-controlled input. The normal
    tool resolver remains responsible for authorization; this validation adds
    a strict size bound and rejects arguments that the provider's advertised
    JSON schema would not have been allowed to produce.
    """
    normalized_tool_name = _assert_realtime_tool_allowed(runtime, tool_name)
    try:
        encoded_arguments = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Realtime tool arguments must be valid JSON.") from exc
    if len(encoded_arguments) > REALTIME_TOOL_ARGUMENTS_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Realtime tool arguments are too large.")

    matching_schema = next(
        (
            schema
            for schema in runtime.tool_schemas or []
            if _tool_schema_name(schema) == normalized_tool_name
        ),
        None,
    )
    if not isinstance(matching_schema, dict):
        # Some built-in tools are represented only by the resolved tool list.
        # Their resolver performs its own validation and authorization.
        return
    parameters = matching_schema.get("parameters")
    if not isinstance(parameters, dict):
        function_schema = matching_schema.get("function")
        parameters = function_schema.get("parameters") if isinstance(function_schema, dict) else None
    if not isinstance(parameters, dict):
        return
    try:
        validator = Draft202012Validator(parameters)
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.absolute_path))
    except SchemaError:
        logger.exception("Invalid realtime tool schema for %s", normalized_tool_name)
        raise HTTPException(status_code=500, detail="Realtime tool schema is invalid.")
    if errors:
        raise HTTPException(status_code=422, detail=f"Realtime tool arguments are invalid: {errors[0].message}")


def realtime_allowed_tool_names(runtime: "RealtimeSessionRuntime") -> set[str]:
    allowed = {
        name
        for name in (_normalize_realtime_tool_name(item) for item in (runtime.tools or []))
        if name
    }
    for schema in runtime.tool_schemas or []:
        schema_name = _tool_schema_name(schema)
        if schema_name:
            allowed.add(schema_name)
    return allowed


def _assert_realtime_tool_allowed(runtime: "RealtimeSessionRuntime", tool_name: str) -> str:
    normalized_tool_name = _normalize_realtime_tool_name(tool_name)
    if not normalized_tool_name:
        raise HTTPException(status_code=400, detail="Tool name is required.")
    if normalized_tool_name not in realtime_allowed_tool_names(runtime):
        raise HTTPException(status_code=403, detail="Tool is not allowed for this realtime session.")
    return normalized_tool_name


def register_realtime_pending_tool_call(
    runtime: "RealtimeSessionRuntime",
    *,
    call_id: str,
    tool_name: str,
) -> dict[str, str]:
    normalized_call_id = str(call_id or "").strip()
    if not normalized_call_id:
        raise HTTPException(status_code=400, detail="Tool call id is required.")
    normalized_tool_name = _assert_realtime_tool_allowed(runtime, tool_name)
    completed_tool_call = runtime.completed_tool_calls.get(normalized_call_id)
    if isinstance(completed_tool_call, dict):
        completed_tool_name = _normalize_realtime_tool_name(completed_tool_call.get("tool_name"))
        if completed_tool_name != normalized_tool_name:
            raise HTTPException(status_code=409, detail="Realtime tool call id is already bound to a different tool.")
        runtime.last_activity_at = _utc_now()
        return {"status": "completed"}
    if normalized_call_id in runtime.consumed_tool_call_ids:
        raise HTTPException(status_code=409, detail="Realtime tool call id was already consumed.")
    existing_tool_name = runtime.pending_tool_calls.get(normalized_call_id)
    if existing_tool_name and existing_tool_name != normalized_tool_name:
        raise HTTPException(status_code=409, detail="Realtime tool call id is already bound to a different tool.")
    runtime.pending_tool_calls[normalized_call_id] = normalized_tool_name
    runtime.last_activity_at = _utc_now()
    return {"status": "registered"}


def consume_realtime_pending_tool_call(
    runtime: "RealtimeSessionRuntime",
    *,
    call_id: str,
    tool_name: str,
) -> None:
    normalized_call_id = str(call_id or "").strip()
    if not normalized_call_id:
        raise HTTPException(status_code=400, detail="Tool call id is required.")
    normalized_tool_name = _assert_realtime_tool_allowed(runtime, tool_name)
    if normalized_call_id in runtime.consumed_tool_call_ids:
        raise HTTPException(status_code=409, detail="Realtime tool call id was already consumed.")
    pending_tool_name = runtime.pending_tool_calls.get(normalized_call_id)
    if pending_tool_name != normalized_tool_name:
        raise HTTPException(status_code=403, detail="Realtime tool call id is not pending for this tool.")
    runtime.pending_tool_calls.pop(normalized_call_id, None)
    runtime.consumed_tool_call_ids.add(normalized_call_id)
    runtime.last_activity_at = _utc_now()


def get_realtime_completed_tool_call_response(
    runtime: "RealtimeSessionRuntime",
    *,
    call_id: str,
    tool_name: str,
) -> dict[str, Any] | None:
    normalized_call_id = str(call_id or "").strip()
    if not normalized_call_id:
        raise HTTPException(status_code=400, detail="Tool call id is required.")
    normalized_tool_name = _assert_realtime_tool_allowed(runtime, tool_name)
    completed_tool_call = runtime.completed_tool_calls.get(normalized_call_id)
    if not isinstance(completed_tool_call, dict):
        return None
    completed_tool_name = _normalize_realtime_tool_name(completed_tool_call.get("tool_name"))
    if completed_tool_name != normalized_tool_name:
        raise HTTPException(status_code=409, detail="Realtime tool call id is already bound to a different tool.")
    runtime.last_activity_at = _utc_now()
    return {
        "output": str(completed_tool_call.get("output") or ""),
        "events": list(completed_tool_call.get("events") or []),
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _coerce_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_settings_record(record_data: dict[str, Any] | None) -> dict[str, Any]:
    data = record_data if isinstance(record_data, dict) else {}
    raw_realtime_tools = data.get("realtime_tools")
    if not isinstance(raw_realtime_tools, list):
        raw_realtime_tools = []
    realtime_tools: list[str] = []
    seen_realtime_tools: set[str] = set()
    for raw_tool_name in raw_realtime_tools:
        tool_name = str(raw_tool_name or "").strip()
        if not tool_name or tool_name in seen_realtime_tools:
            continue
        seen_realtime_tools.add(tool_name)
        realtime_tools.append(tool_name)
    return {
        "enabled": _coerce_bool(data.get("realtime_enabled"), False),
        "provider_id": str(data.get("realtime_provider_id") or "").strip() or None,
        "model": str(data.get("realtime_model") or "").strip() or None,
        "voice": str(data.get("realtime_voice") or "alloy").strip() or "alloy",
        "tools": realtime_tools,
        "temperature": _coerce_float(data.get("realtime_temperature")),
        "max_output_tokens": _coerce_int(data.get("realtime_max_output_tokens")),
        "input_transcription_enabled": _coerce_bool(data.get("realtime_input_transcription_enabled"), True),
        "output_transcription_enabled": _coerce_bool(data.get("realtime_output_transcription_enabled"), True),
        "language_code": str(data.get("realtime_language_code") or "").strip() or None,
        "enable_session_resumption": _coerce_bool(data.get("realtime_enable_session_resumption"), True),
        "enable_context_window_compression": _coerce_bool(data.get("realtime_enable_context_window_compression"), True),
        "compression_trigger_tokens": _coerce_int(data.get("realtime_compression_trigger_tokens")),
        "compression_target_tokens": _coerce_int(data.get("realtime_compression_target_tokens")),
        "enable_affective_dialog": _coerce_bool(data.get("realtime_enable_affective_dialog"), False),
        "enable_proactive_audio": _coerce_bool(data.get("realtime_enable_proactive_audio"), False),
        "activity_handling": str(data.get("realtime_activity_handling") or "START_OF_ACTIVITY_INTERRUPTS").strip().upper(),
        "turn_coverage": str(data.get("realtime_turn_coverage") or "TURN_INCLUDES_ONLY_ACTIVITY").strip().upper(),
        "start_sensitivity": str(data.get("realtime_start_sensitivity") or "").strip().upper() or None,
        "end_sensitivity": str(data.get("realtime_end_sensitivity") or "").strip().upper() or None,
        "prefix_padding_ms": _coerce_int(data.get("realtime_prefix_padding_ms")),
        "silence_duration_ms": _coerce_int(data.get("realtime_silence_duration_ms")),
    }


def _resolve_realtime_http_base_url(base_url: str | None) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        return "https://api.openai.com/v1"
    parsed = urlparse(base_url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        scheme = "https"
    path = parsed.path or ""
    if not path.endswith("/v1"):
        path = path.rstrip("/")
        if not path:
            path = "/v1"
        elif path.endswith("/"):
            path = f"{path}v1"
        else:
            path = f"{path}/v1"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def build_realtime_call_url(base_url: str | None) -> str:
    return f"{_resolve_realtime_http_base_url(base_url)}/realtime/calls"


def _build_realtime_headers(settings: dict[str, Any], *, safety_identifier: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = str(settings.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    organization = str(settings.get("organization") or "").strip()
    project = str(settings.get("project") or "").strip()
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    if safety_identifier:
        headers["OpenAI-Safety-Identifier"] = safety_identifier
    return headers


def _build_realtime_safety_identifier(db: Session, user_id: str) -> str:
    """Build OpenAI's stable privacy-preserving end-user identifier."""
    secret, _algorithm = get_jwt_material()
    digest = hmac.new(
        secret.encode("utf-8"),
        f"openai-realtime:{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    # OpenAI limits safety identifiers to 64 characters. The retained 57 hex
    # characters still provide 228 bits from the HMAC while preserving Omlorix's
    # recognizable namespace prefix.
    available_digest_length = OPENAI_REALTIME_SAFETY_IDENTIFIER_MAX_LENGTH - len(
        OPENAI_REALTIME_SAFETY_IDENTIFIER_PREFIX
    )
    return f"{OPENAI_REALTIME_SAFETY_IDENTIFIER_PREFIX}{digest[:available_digest_length]}"


def _sanitize_realtime_runtime_settings(settings: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return {}
    return {
        key: value
        for key, value in settings.items()
        if key not in REALTIME_RUNTIME_SECRET_SETTING_KEYS
    }


def _load_openai_realtime_request_settings(
    db: Session,
    *,
    provider_id: str,
    provider_type: str,
) -> dict[str, Any]:
    client_kwargs = _resolve_openai_client_kwargs(
        db,
        openai_provider_id=provider_id,
        byok=None,
        openai_provider_type=provider_type,
    )
    return {
        "api_key": client_kwargs.get("api_key"),
        "base_url": client_kwargs.get("base_url"),
        "organization": client_kwargs.get("organization"),
        "project": client_kwargs.get("project"),
    }


def load_realtime_runtime_settings(db: Session) -> dict[str, Any]:
    record = get_settings_page(db, "realtime")
    data = record.data if record and isinstance(record.data, dict) else {}
    settings = _normalize_settings_record(data)
    if not settings["enabled"]:
        raise HTTPException(status_code=400, detail="Realtime conversations are disabled")
    if not settings["provider_id"]:
        raise HTTPException(status_code=400, detail="Realtime provider is not configured")
    if not settings["model"]:
        raise HTTPException(status_code=400, detail="Realtime model is not configured")

    provider = get_llm_provider(db, settings["provider_id"])
    if provider is None:
        raise HTTPException(status_code=400, detail="Realtime provider is unavailable")
    if provider.provider not in OPENAI_REALTIME_PROVIDER_TYPES | WEBSOCKET_REALTIME_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail="Realtime provider type is not supported")
    if not provider.api_key and not provider_api_key_is_optional(provider.provider):
        raise HTTPException(status_code=400, detail="Realtime provider API key is missing")

    if provider.provider == ProviderEnum.google_aistudio.value:
        allowed_models = set(
            get_google_aistudio_live_models(
                db=db,
                google_provider_id=provider.id,
            )
        )
    elif provider.provider == ProviderEnum.xai.value:
        allowed_models = set(
            get_xai_realtime_models(
                db=db,
                provider_id=provider.id,
            )
        )
    else:
        allowed_models = set(
            get_openai_realtime_models(
                db=db,
                openai_provider_id=provider.id,
                openai_provider_type=provider.provider,
            )
        )
    if settings["model"] not in allowed_models:
        raise HTTPException(status_code=400, detail="Realtime model is not supported by selected provider")

    if provider.provider in OPENAI_REALTIME_PROVIDER_TYPES:
        client_kwargs = _resolve_openai_client_kwargs(
            db,
            openai_provider_id=provider.id,
            byok=None,
            openai_provider_type=provider.provider,
        )

        settings.update(
            {
                "provider": provider.provider,
                "provider_id": provider.id,
                "api_key": client_kwargs.get("api_key"),
                "base_url": client_kwargs.get("base_url"),
                "organization": client_kwargs.get("organization"),
                "project": client_kwargs.get("project"),
            }
        )
    elif provider.provider == ProviderEnum.google_aistudio.value:
        settings.update(
            {
                "provider": provider.provider,
                "provider_id": provider.id,
                "api_key": provider.api_key,
                "base_url": None,
                "organization": None,
                "project": None,
                "voice": get_google_aistudio_live_default_voice(settings.get("voice")),
            }
        )
    else:
        provider_settings = (
            provider.settings if isinstance(provider.settings, dict) else {}
        )
        settings.update(
            {
                "provider": provider.provider,
                "provider_id": provider.id,
                "api_key": provider.api_key,
                "base_url": provider_settings.get("base_url"),
                "organization": None,
                "project": None,
                "voice": normalize_xai_realtime_voice(settings.get("voice")),
            }
        )
    return settings


def build_file_context_text(db: Session, *, user_id: str, file_ids: list[str]) -> str:
    if not file_ids:
        return ""
    snippets: list[str] = []
    for file_id in file_ids:
        try:
            info = get_file_info(user_id, file_id)
        except Exception:
            info = None
        if not info:
            continue
        file_type = str(info.get("file_type") or "").lower()
        file_text = extract_text_from_file_info(info) or ""
        if file_text:
            snippets.append(build_file_metadata_text(
                file_id,
                info,
                native_context_included=False,
                model_context_representation="text_extract",
                text_content_included=True,
            ))
        else:
            snippets.append(build_file_metadata_text(
                file_id,
                info,
                native_context_included=False,
                model_context_representation="metadata_only",
                text_content_included=False,
            ))
        if file_text:
            trimmed = file_text.strip()
            if len(trimmed) > 6000:
                trimmed = trimmed[:6000]
            snippets.append(trimmed)
        else:
            snippets.append(f"[File attached ({file_type or 'unknown'})]")
    return "\n\n".join(snippets).strip()


def realtime_model_supports_native_multimodal_inputs(model_name: str | None) -> bool:
    normalized = str(model_name or "").strip().lower()
    return normalized.startswith("gpt-realtime")


def build_realtime_attachment_fields(*, user_id: str, file_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    attachment_fields: dict[str, list[dict[str, Any]]] = {
        "images": [],
        "videos": [],
        "audios": [],
        "documents": [],
    }
    category_map = {
        "image": "images",
        "video": "videos",
        "audio": "audios",
        "document": "documents",
    }
    seen_ids: set[str] = set()

    for raw_file_id in file_ids:
        file_id = str(raw_file_id or "").strip()
        if not file_id or file_id in seen_ids:
            continue
        seen_ids.add(file_id)

        try:
            info = get_file_info(user_id, file_id)
        except Exception:
            info = None
        if not info:
            continue

        target_field = category_map.get(str(info.get("file_category") or "").strip().lower())
        if not target_field:
            continue

        meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}
        original_name = str(meta.get("original_filename") or info.get("file_name") or file_id).strip() or file_id
        mime_type = str(info.get("file_type") or "").strip() or None
        file_size = info.get("file_size")

        attachment_fields[target_field].append(
            {
                "id": file_id,
                "file_id": file_id,
                "file_name": info.get("file_name"),
                "original_name": original_name,
                "original_filename": original_name,
                "file_type": mime_type,
                "mime_type": mime_type,
                "file_size": file_size,
            }
        )

    return attachment_fields


def build_realtime_input_parts(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    text: str,
    file_ids: list[str],
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    normalized_text = str(text or "").strip()
    if normalized_text:
        parts.append({"type": "input_text", "text": normalized_text})

    if not file_ids:
        return parts

    if realtime_model_supports_native_multimodal_inputs(runtime.realtime_model):
        # Realtime conversation items support native image input, while the
        # shared Responses helper can also emit input_file parts that are not
        # part of the Realtime conversation contract. Keep documents on the
        # safe extracted-text path and only pass genuine images natively.
        image_file_ids: list[str] = []
        contextual_file_ids: list[str] = []
        for file_id in file_ids:
            try:
                file_info = get_file_info(runtime.user_id, str(file_id))
            except Exception:
                file_info = None
            if isinstance(file_info, dict) and str(file_info.get("file_category") or "").lower() == "image":
                image_file_ids.append(file_id)
            else:
                contextual_file_ids.append(file_id)
        upload_result = upload_files(
            db,
            image_file_ids,
            runtime.user_id,
            input_formats_allowed=["image"],
        )
        uploaded_parts = [
            part
            for part in (upload_result.get("parts") or [])
            if isinstance(part, dict) and str(part.get("type") or "").strip()
        ]
        if uploaded_parts:
            parts.extend(uploaded_parts)
        file_ids = contextual_file_ids
        if not file_ids:
            return parts

    file_context = build_file_context_text(db, user_id=runtime.user_id, file_ids=file_ids)
    if file_context:
        if normalized_text:
            parts.append({"type": "input_text", "text": f"[Attached file context]\n{file_context}"})
        else:
            parts.append({"type": "input_text", "text": file_context})
    return parts


def _collect_skill_file_ids(db: Session, user_id: str, skill_ids: list[str] | None) -> list[str]:
    if not skill_ids:
        return []

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for skill_id in skill_ids:
        by_category = get_skill_file_descriptors_by_category_for_user(db, user_id, str(skill_id or ""))
        if not isinstance(by_category, dict):
            continue
        for key in ("image", "video", "audio", "document"):
            for raw_file_id in by_category.get(key) or []:
                file_id = str(raw_file_id or "").strip()
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)
                ordered_ids.append(file_id)
    return ordered_ids


def execute_realtime_tool_call(
    db: Session,
    *,
    session_id: str,
    tool_call_id: str,
    turn_id: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
    user_id: str,
    group_id: str | None,
    project_id: str | None,
    model_id: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
    model_settings: dict[str, Any] | None = None,
    chat_id: str | None = None,
    user_role: str | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()

    def ensure_tool_statistic(*, success: bool, error_message: str | None = None) -> None:
        """Record one shared tool fact when the tool did not record itself."""
        existing = (
            db.query(ToolCallStatistic.id)
            .filter(
                ToolCallStatistic.interaction_type
                == INTERACTION_TYPE_REALTIME_RESPONSE,
                ToolCallStatistic.session_id == session_id,
                ToolCallStatistic.turn_id == turn_id,
                ToolCallStatistic.tool_call_id == tool_call_id,
            )
            .first()
        )
        if existing:
            return
        create_tool_call_statistic(
            db=db,
            tool_name=tool_name,
            success=success,
            error_message=error_message,
            execution_time=max(time.perf_counter() - started_at, 0.0),
            model_id=model_id,
            model_name=model_name,
            provider=provider,
            user_id=user_id,
            interaction_type=INTERACTION_TYPE_REALTIME_RESPONSE,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        )

    with tool_statistics_context(
        interaction_type=INTERACTION_TYPE_REALTIME_RESPONSE,
        session_id=session_id,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
    ):
        helper_gen = resolve_tool_call(
            db=db,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            user_id=user_id,
            group_id=group_id,
            project_id=project_id,
            model_settings=model_settings,
            byok=None,
            chat_id=chat_id,
            chat_history=None,
            user_role=str(user_role or "").strip().lower(),
            # Realtime calls use the same Canvas event protocol as text-model
            # providers, so preserve the provider call ID for exact terminal-event
            # correlation in the sidebar.
            tool_call_id=tool_call_id,
        )
        streamed_events: list[dict[str, Any]] = []
        result_payload: dict[str, Any] = {}
        while True:
            try:
                event_line = next(helper_gen)
            except StopIteration as stop:
                result_payload = stop.value if isinstance(stop.value, dict) else {"result": stop.value}
                break
            except Exception as exc:
                # Roll back any incomplete tool transaction before committing
                # a standalone failure statistic.
                db.rollback()
                ensure_tool_statistic(success=False, error_message=str(exc))
                raise RuntimeError(f"Tool execution failed: {exc}") from exc
            if not event_line:
                continue
            try:
                event_data = json.loads(event_line)
            except Exception:
                continue
            if isinstance(event_data, dict):
                streamed_events.append(event_data)
        ensure_tool_statistic(success=True)
    return {
        "payload": result_payload or {},
        "events": streamed_events,
    }


def resolve_configured_realtime_tools(
    db: Session,
    *,
    configured_tools: list[str],
    model_settings: dict[str, Any] | None,
    user_id: str,
    project_id: str | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve the admin realtime allow-list into executable tool schemas.

    Tool-specific configuration still comes from the selected chat model, but
    the names originate exclusively from the realtime settings allow-list.
    This keeps the schemas sent to the provider and the backend execution
    allow-list derived from one canonical selection.
    """
    resolved = resolve_enabled_tools(
        configured_tools,
        db=db,
        model_settings=model_settings,
        user_id=user_id,
        project_id=project_id,
    )
    return (
        list(resolved.get("tool_list") or []),
        list(resolved.get("tool_schemas") or []),
    )


@dataclass
class RealtimeTurnBuffer:
    turn_index: int = 0
    started_at: datetime = field(default_factory=_utc_now)
    interrupted: bool = False
    user_transcript: str = ""
    assistant_transcript: str = ""
    file_ids: list[str] = field(default_factory=list)
    tool_blocks: list[dict[str, Any]] = field(default_factory=list)

    def reset(self, *, next_turn_index: int) -> None:
        self.turn_index = next_turn_index
        self.started_at = _utc_now()
        self.interrupted = False
        self.user_transcript = ""
        self.assistant_transcript = ""
        self.file_ids = []
        self.tool_blocks.clear()


@dataclass
class RealtimeSessionRuntime:
    id: str
    user_id: str
    group_id: str | None
    chat_id: str
    project_id: str | None
    model_id: str | None
    base_model_id: str | None
    agent_id: str | None
    model_settings: dict[str, Any]
    skill_id: str | None
    skill_content: str | None
    agent_instruction: str | None
    provider: str
    provider_id: str
    realtime_model: str
    voice: str
    settings: dict[str, Any]
    # This remains true for the lifetime of a session created from the empty
    # composer. It lets reconnect responses and first-turn persistence retain
    # the same semantics after a process restart.
    created_chat: bool = False
    skill_file_ids: list[str] = field(default_factory=list)
    agent_file_ids: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: dict[str, str] = field(default_factory=dict)
    consumed_tool_call_ids: set[str] = field(default_factory=set)
    completed_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    last_activity_at: datetime = field(default_factory=_utc_now)
    active: bool = True
    # Database identifier of the short-lived operational session row. It is
    # intentionally distinct from shared LLM interaction statistic IDs.
    session_record_id: str | None = None
    rate_limit_admission_id: str | None = None
    max_duration_seconds: int | None = None
    # Provider connection state is persisted so a worker can enforce the hard
    # deadline after process restarts. ``provider_session_handle`` is an opaque
    # provider identifier (for example OpenAI's WebRTC call ID), never a secret.
    provider_connection_state: str = REALTIME_PROVIDER_CONNECTION_IDLE
    provider_connection_owner_id: str | None = None
    provider_session_handle: str | None = None
    provider_connection_started_at: datetime | None = None
    provider_connection_last_seen_at: datetime | None = None
    provider_stop_requested_at: datetime | None = None
    provider_stop_reason: str | None = None
    google_session_handle: str | None = None
    turn: RealtimeTurnBuffer = field(default_factory=RealtimeTurnBuffer)

    def absolute_expires_at(self) -> datetime:
        """Return the immutable provider/quota deadline for this session."""
        max_lifetime = (
            OPENAI_REALTIME_SESSION_MAX_LIFETIME
            if self.provider in OPENAI_REALTIME_PROVIDER_TYPES
            else timedelta(minutes=120)
            if self.provider == ProviderEnum.xai.value
            else REALTIME_SESSION_MAX_LIFETIME
        )
        absolute_deadline = self.created_at + max_lifetime
        if self.max_duration_seconds is not None:
            absolute_deadline = min(
                absolute_deadline,
                self.created_at + timedelta(seconds=max(1, int(self.max_duration_seconds))),
            )
        return absolute_deadline

    def expires_at(self) -> datetime:
        """Return the earlier of the renewable idle and absolute deadlines."""
        idle_deadline = self.last_activity_at + REALTIME_SESSION_IDLE_TTL
        absolute_deadline = self.absolute_expires_at()
        return min(idle_deadline, absolute_deadline)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current_time = now or _utc_now()
        return current_time >= self.expires_at()


def _serialize_realtime_turn_buffer(turn: RealtimeTurnBuffer) -> dict[str, Any]:
    return {
        "turn_index": int(turn.turn_index or 0),
        "started_at": _serialize_runtime_datetime(turn.started_at),
        "interrupted": bool(turn.interrupted),
        "user_transcript": str(turn.user_transcript or ""),
        "assistant_transcript": str(turn.assistant_transcript or ""),
        "file_ids": [str(file_id).strip() for file_id in (turn.file_ids or []) if str(file_id).strip()],
        "tool_blocks": list(turn.tool_blocks or []),
    }


def _deserialize_realtime_turn_buffer(payload: dict[str, Any] | None) -> RealtimeTurnBuffer:
    data = payload if isinstance(payload, dict) else {}
    return RealtimeTurnBuffer(
        turn_index=max(0, int(data.get("turn_index") or 0)),
        started_at=_deserialize_runtime_datetime(data.get("started_at")),
        interrupted=bool(data.get("interrupted")),
        user_transcript=str(data.get("user_transcript") or ""),
        assistant_transcript=str(data.get("assistant_transcript") or ""),
        file_ids=[str(file_id).strip() for file_id in (data.get("file_ids") or []) if str(file_id).strip()],
        tool_blocks=list(data.get("tool_blocks") or []),
    )


def serialize_realtime_runtime(runtime: RealtimeSessionRuntime) -> dict[str, Any]:
    return {
        "id": runtime.id,
        "user_id": runtime.user_id,
        "group_id": runtime.group_id,
        "chat_id": runtime.chat_id,
        "project_id": runtime.project_id,
        "model_id": runtime.model_id,
        "base_model_id": runtime.base_model_id,
        "agent_id": runtime.agent_id,
        "model_settings": dict(runtime.model_settings or {}),
        "skill_id": runtime.skill_id,
        "skill_content": runtime.skill_content,
        "agent_instruction": runtime.agent_instruction,
        "provider": runtime.provider,
        "provider_id": runtime.provider_id,
        "realtime_model": runtime.realtime_model,
        "voice": runtime.voice,
        "settings": dict(runtime.settings or {}),
        "created_chat": bool(runtime.created_chat),
        "skill_file_ids": [str(file_id).strip() for file_id in (runtime.skill_file_ids or []) if str(file_id).strip()],
        "agent_file_ids": [str(file_id).strip() for file_id in (runtime.agent_file_ids or []) if str(file_id).strip()],
        "tools": [str(tool).strip() for tool in (runtime.tools or []) if str(tool).strip()],
        "tool_schemas": list(runtime.tool_schemas or []),
        "pending_tool_calls": dict(runtime.pending_tool_calls or {}),
        "consumed_tool_call_ids": sorted(str(call_id).strip() for call_id in (runtime.consumed_tool_call_ids or set()) if str(call_id).strip()),
        "completed_tool_calls": dict(runtime.completed_tool_calls or {}),
        "created_at": _serialize_runtime_datetime(runtime.created_at),
        "last_activity_at": _serialize_runtime_datetime(runtime.last_activity_at),
        "active": bool(runtime.active),
        "session_record_id": runtime.session_record_id,
        "rate_limit_admission_id": runtime.rate_limit_admission_id,
        "max_duration_seconds": runtime.max_duration_seconds,
        "provider_connection_state": runtime.provider_connection_state,
        "provider_connection_owner_id": runtime.provider_connection_owner_id,
        "provider_session_handle": runtime.provider_session_handle,
        "provider_connection_started_at": _serialize_runtime_datetime(runtime.provider_connection_started_at),
        "provider_connection_last_seen_at": _serialize_runtime_datetime(runtime.provider_connection_last_seen_at),
        "provider_stop_requested_at": _serialize_runtime_datetime(runtime.provider_stop_requested_at),
        "provider_stop_reason": runtime.provider_stop_reason,
        "google_session_handle": runtime.google_session_handle,
        "turn": _serialize_realtime_turn_buffer(runtime.turn),
    }


def persist_realtime_runtime_state(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    provider_state_authoritative: bool = False,
) -> None:
    """Persist runtime state without letting ordinary requests regress control.

    Browser heartbeat, tool, and turn requests may race with the provider proxy
    or deadline worker in another process. Unless the caller explicitly owns
    provider state, retain the latest persisted termination handle and
    connection state instead of overwriting them with a stale registry copy.
    """
    if not runtime.session_record_id:
        return
    serialized_runtime = serialize_realtime_runtime(runtime)
    if not provider_state_authoritative:
        # Hold the operational row through the merge and update. An authoritative
        # writer that updates the same row must then finish before this read, or
        # wait until this transaction commits, so stale request state cannot
        # replace a newer provider handle or termination state.
        current_record = (
            db.query(RealtimeSession)
            .filter(
                RealtimeSession.id == runtime.session_record_id
            )
            .with_for_update()
            .first()
        )
        current_runtime = (
            current_record.runtime_state
            if current_record is not None
            and isinstance(current_record.runtime_state, dict)
            else {}
        )
        if isinstance(current_runtime, dict):
            for key in REALTIME_PROVIDER_CONTROL_STATE_KEYS:
                if key in current_runtime:
                    serialized_runtime[key] = current_runtime[key]
    update_realtime_session(
        db,
        runtime.session_record_id,
        runtime_state=serialized_runtime,
    )


def restore_realtime_session_runtime(
    db: Session,
    *,
    session_id: str,
) -> RealtimeSessionRuntime | None:
    record = get_realtime_session_by_session_id(db, session_id=session_id)
    if not record:
        return None

    payload = record.runtime_state if isinstance(record.runtime_state, dict) else {}
    if not payload:
        return None

    created_at = _deserialize_runtime_datetime(payload.get("created_at"), default=record.started_at or record.created_at)
    last_activity_at = _deserialize_runtime_datetime(payload.get("last_activity_at"), default=record.last_updated_at or created_at)
    runtime = RealtimeSessionRuntime(
        id=str(payload.get("id") or record.session_id or session_id),
        user_id=str(payload.get("user_id") or record.user_id or ""),
        group_id=_normalize_optional_string(payload.get("group_id")),
        chat_id=str(payload.get("chat_id") or record.chat_id or ""),
        project_id=_normalize_optional_string(payload.get("project_id")),
        model_id=_normalize_optional_string(payload.get("model_id")),
        base_model_id=_normalize_optional_string(payload.get("base_model_id")),
        agent_id=_normalize_optional_string(payload.get("agent_id")),
        model_settings=payload.get("model_settings") if isinstance(payload.get("model_settings"), dict) else {},
        skill_id=_normalize_optional_string(payload.get("skill_id")),
        skill_content=str(payload.get("skill_content")) if payload.get("skill_content") is not None else None,
        agent_instruction=str(payload.get("agent_instruction")) if payload.get("agent_instruction") is not None else None,
        provider=str(payload.get("provider") or record.provider or ""),
        provider_id=str(payload.get("provider_id") or record.provider_id or ""),
        realtime_model=str(payload.get("realtime_model") or record.model_name or ""),
        voice=str(payload.get("voice") or "alloy"),
        settings=payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
        created_chat=bool(payload.get("created_chat")),
        skill_file_ids=[str(file_id).strip() for file_id in (payload.get("skill_file_ids") or []) if str(file_id).strip()],
        agent_file_ids=[str(file_id).strip() for file_id in (payload.get("agent_file_ids") or []) if str(file_id).strip()],
        tools=[str(tool).strip() for tool in (payload.get("tools") or []) if str(tool).strip()],
        tool_schemas=list(payload.get("tool_schemas") or []) if isinstance(payload.get("tool_schemas"), list) else [],
        pending_tool_calls={
            str(call_id).strip(): str(tool_name).strip()
            for call_id, tool_name in (
                payload.get("pending_tool_calls").items()
                if isinstance(payload.get("pending_tool_calls"), dict)
                else []
            )
            if str(call_id).strip() and str(tool_name).strip()
        },
        consumed_tool_call_ids={
            str(call_id).strip()
            for call_id in (payload.get("consumed_tool_call_ids") or [])
            if str(call_id).strip()
        },
        completed_tool_calls=payload.get("completed_tool_calls") if isinstance(payload.get("completed_tool_calls"), dict) else {},
        created_at=created_at,
        last_activity_at=last_activity_at,
        active=bool(payload.get("active")) and str(record.status or "").strip().lower() == "active",
        session_record_id=record.id,
        rate_limit_admission_id=_normalize_optional_string(payload.get("rate_limit_admission_id")),
        max_duration_seconds=_coerce_int(payload.get("max_duration_seconds")),
        provider_connection_state=(
            str(payload.get("provider_connection_state") or REALTIME_PROVIDER_CONNECTION_IDLE)
            if str(payload.get("provider_connection_state") or REALTIME_PROVIDER_CONNECTION_IDLE)
            in REALTIME_PROVIDER_CONNECTION_STATES
            else REALTIME_PROVIDER_CONNECTION_IDLE
        ),
        provider_connection_owner_id=_normalize_optional_string(
            payload.get("provider_connection_owner_id")
        ),
        provider_session_handle=_normalize_optional_string(payload.get("provider_session_handle")),
        provider_connection_started_at=_deserialize_optional_runtime_datetime(
            payload.get("provider_connection_started_at")
        ),
        provider_connection_last_seen_at=_deserialize_optional_runtime_datetime(
            payload.get("provider_connection_last_seen_at")
        ),
        provider_stop_requested_at=_deserialize_optional_runtime_datetime(
            payload.get("provider_stop_requested_at")
        ),
        provider_stop_reason=_normalize_optional_string(payload.get("provider_stop_reason")),
        google_session_handle=_normalize_optional_string(payload.get("google_session_handle")),
        turn=_deserialize_realtime_turn_buffer(payload.get("turn")),
    )
    return runtime


def claim_websocket_proxy_connection(
    db: Session,
    *,
    session_id: str,
    connection_id: str,
) -> RealtimeSessionRuntime | None:
    """Atomically claim a server-owned realtime upstream across app processes.

    PostgreSQL row locking prevents two load-balanced WebSocket handshakes from
    minting separate provider sessions for one quota reservation. SQLite is a
    single-process development fallback and is additionally protected by the
    in-process proxy registry.
    """
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return None
    record = (
        db.query(RealtimeSession)
        .filter(
            RealtimeSession.session_id == session_id,
            RealtimeSession.status == "active",
        )
        .with_for_update()
        .first()
    )
    if record is None:
        db.rollback()
        return None
    runtime = restore_realtime_session_runtime(db, session_id=session_id)
    if (
        runtime is None
        or not runtime.active
        or runtime.provider not in WEBSOCKET_REALTIME_PROVIDER_TYPES
        or runtime.is_expired()
        or runtime.provider_connection_state
        not in {
            REALTIME_PROVIDER_CONNECTION_IDLE,
        }
    ):
        db.rollback()
        return None

    now = _utc_now()
    runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_CONNECTING
    runtime.provider_connection_owner_id = normalized_connection_id
    runtime.provider_connection_last_seen_at = now
    runtime.last_activity_at = now
    persist_realtime_runtime_state(
        db,
        runtime,
        provider_state_authoritative=True,
    )
    return runtime


def claim_google_proxy_connection(
    db: Session,
    *,
    session_id: str,
    connection_id: str,
) -> RealtimeSessionRuntime | None:
    """Backward-compatible alias for the shared WebSocket proxy claim."""
    return claim_websocket_proxy_connection(
        db,
        session_id=session_id,
        connection_id=connection_id,
    )


def _reconcile_realtime_runtime_locked(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    now: datetime,
) -> bool:
    """Reconcile one freshly restored runtime under its transition lock.

    Returns whether the runtime was fully terminated. Provider activity is
    never manufactured here: only the process that owns a live proxy or
    sideband WebSocket may publish liveness and renew quota.
    """
    last_provider_activity = runtime.provider_connection_last_seen_at
    provider_activity_is_stale = (
        last_provider_activity is None
        or last_provider_activity <= now - REALTIME_PROVIDER_ACTIVITY_STALE_AFTER
    )

    # A server-owned WebSocket proxy is process-owned. If its activity marker stopped after a
    # crash, the upstream socket died with that process and can safely be
    # treated as terminated.
    websocket_proxy_is_stale = (
        runtime.provider_connection_state
        == REALTIME_PROVIDER_CONNECTION_ACTIVE
        and provider_activity_is_stale
    ) or (
        runtime.provider_connection_state
        == REALTIME_PROVIDER_CONNECTION_CONNECTING
        and (
            last_provider_activity is None
            or last_provider_activity
            <= now - REALTIME_PROVIDER_CONNECTING_STALE_AFTER
        )
    )
    if (
        runtime.provider in WEBSOCKET_REALTIME_PROVIDER_TYPES
        and websocket_proxy_is_stale
    ):
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_IDLE
        runtime.provider_connection_owner_id = None
        persist_realtime_runtime_state(
            db,
            runtime,
            provider_state_authoritative=True,
        )

    if runtime.provider_connection_state == REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING:
        if runtime.provider in OPENAI_REALTIME_PROVIDER_TYPES:
            if (
                runtime.provider_stop_requested_at is not None
                and runtime.provider_stop_requested_at
                > now - REALTIME_PROVIDER_TERMINATION_RETRY_AFTER
            ):
                return False
            return _mark_runtime_inactive_locked(
                db,
                runtime,
                reason=runtime.provider_stop_reason or "termination_retry",
                status="expired" if runtime.is_expired(now=now) else "stopped",
            )
        if provider_activity_is_stale:
            return _mark_runtime_inactive_locked(
                db,
                runtime,
                reason=runtime.provider_stop_reason or "proxy_terminated",
                status="expired" if runtime.is_expired(now=now) else "stopped",
                provider_already_terminated=True,
            )
        return False

    if runtime.is_expired(now=now):
        provider_already_terminated = (
            runtime.provider in WEBSOCKET_REALTIME_PROVIDER_TYPES
            and runtime.provider_connection_state
            in {
                REALTIME_PROVIDER_CONNECTION_IDLE,
                REALTIME_PROVIDER_CONNECTION_TERMINATED,
            }
        )
        return _mark_runtime_inactive_locked(
            db,
            runtime,
            reason="expired",
            status="expired",
            provider_already_terminated=provider_already_terminated,
        )

    if (
        runtime.provider in OPENAI_REALTIME_PROVIDER_TYPES
        and runtime.provider_connection_state
        == REALTIME_PROVIDER_CONNECTION_ACTIVE
        and provider_activity_is_stale
    ):
        # Losing the authenticated sideband monitor means Omlorix can no longer
        # prove or enforce the direct media call. Fail closed by hanging it up;
        # a worker must never renew a reservation from persisted ACTIVE alone.
        return _mark_runtime_inactive_locked(
            db,
            runtime,
            reason="provider_monitor_lost",
            status="stopped",
        )

    return False


def reconcile_expired_realtime_sessions(
    db: Session,
    *,
    user_id: str | None = None,
) -> None:
    """Enforce provider deadlines and keep active reservations authoritative."""
    if user_id:
        active_records: Any = list_active_realtime_sessions_for_user(
            db,
            user_id=user_id,
            limit=REALTIME_RUNTIME_CLEANUP_BATCH_SIZE,
        )
    else:
        def _all_active_records():
            after_created_at = None
            after_id = None
            while True:
                page = list_active_realtime_sessions(
                    db,
                    limit=REALTIME_RUNTIME_CLEANUP_BATCH_SIZE,
                    after_created_at=after_created_at,
                    after_id=after_id,
                )
                if not page:
                    return
                yield from page
                if len(page) < REALTIME_RUNTIME_CLEANUP_BATCH_SIZE:
                    return
                after_created_at = page[-1].created_at
                after_id = page[-1].id

        active_records = _all_active_records()

    now = _utc_now()
    for record in active_records:
        session_id = str(record.session_id)
        terminated = False
        with session_registry.connection_lock(session_id):
            with serialized_realtime_provider_connection(db, session_id):
                runtime = restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if runtime is None or not runtime.active:
                    continue
                terminated = _reconcile_realtime_runtime_locked(
                    db,
                    runtime,
                    now=now,
                )
        if terminated:
            session_registry.remove(session_id)


class RealtimeSessionRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, RealtimeSessionRuntime] = {}
        self._connection_locks: dict[str, threading.RLock] = {}

    def _expire_locked(
        self,
        *,
        now: datetime,
        session_id: str | None = None,
    ) -> list[RealtimeSessionRuntime]:
        expired: list[RealtimeSessionRuntime] = []
        session_ids = [session_id] if session_id else list(self._sessions.keys())
        for existing_session_id in session_ids:
            runtime = self._sessions.get(existing_session_id)
            if not runtime or not runtime.is_expired(now=now):
                continue
            runtime.active = False
            expired.append(self._sessions.pop(existing_session_id))
        return expired

    def create(self, runtime: RealtimeSessionRuntime) -> None:
        with self._lock:
            self._expire_locked(now=_utc_now())
            self._sessions[runtime.id] = runtime
            self._connection_locks.setdefault(runtime.id, threading.RLock())

    def connection_lock(self, session_id: str) -> threading.RLock:
        """Return the per-runtime lock that serializes provider connections."""
        with self._lock:
            return self._connection_locks.setdefault(session_id, threading.RLock())

    def get(self, session_id: str) -> RealtimeSessionRuntime | None:
        with self._lock:
            self._expire_locked(now=_utc_now())
            return self._sessions.get(session_id)

    def pop_expired(self, session_id: str) -> RealtimeSessionRuntime | None:
        with self._lock:
            expired = self._expire_locked(now=_utc_now(), session_id=session_id)
            return expired[0] if expired else None

    def cleanup_expired(self) -> list[RealtimeSessionRuntime]:
        with self._lock:
            return self._expire_locked(now=_utc_now())

    def remove(self, session_id: str) -> RealtimeSessionRuntime | None:
        with self._lock:
            self._expire_locked(now=_utc_now())
            runtime = self._sessions.pop(session_id, None)
            self._connection_locks.pop(session_id, None)
            return runtime


session_registry = RealtimeSessionRegistry()


def _realtime_provider_connection_lock_key(session_id: str) -> int:
    """Return a stable signed PostgreSQL advisory-lock key for one runtime."""
    digest = hashlib.sha256(
        f"{_REALTIME_PROVIDER_CONNECTION_LOCK_PREFIX}{session_id}".encode("utf-8")
    ).digest()
    unsigned_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return unsigned_value - 2**64 if unsigned_value >= 2**63 else unsigned_value


@contextmanager
def serialized_realtime_provider_connection(db: Session, session_id: str):
    """Serialize provider creation across all backend worker processes.

    PostgreSQL session-level advisory locks belong to one physical database
    connection. Keep a dedicated connection checked out for the complete
    guarded operation so commits and rollbacks performed by ``db`` cannot
    return the lock owner to SQLAlchemy's pool before it is unlocked. The
    router also holds its process-local lock, which covers SQLite development
    and avoids needless duplicate local work.
    """
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        yield
        return

    try:
        session_bind = db.get_bind()
        dialect_name = str(
            getattr(getattr(session_bind, "dialect", None), "name", "") or ""
        ).lower()
    except Exception:
        session_bind = None
        dialect_name = ""
    if dialect_name != "postgresql":
        yield
        return

    lock_key = _realtime_provider_connection_lock_key(normalized_session_id)
    # ``Session.get_bind()`` normally returns an Engine. If the Session was
    # explicitly bound to a Connection, use its Engine so this guard still
    # obtains an independent, pinned physical connection.
    lock_engine = getattr(session_bind, "engine", session_bind)
    lock_connection = lock_engine.connect()
    lock_acquired = False
    discard_lock_connection = False
    try:
        # PostgreSQL applies lock_timeout while waiting for advisory locks too.
        # A bounded wait prevents an unhealthy holder from wedging request
        # workers. SET LOCAL is scoped to this dedicated connection's implicit
        # transaction and never changes the application's pooled defaults.
        lock_connection.execute(
            text(
                "SET LOCAL lock_timeout = "
                f"'{_REALTIME_PROVIDER_CONNECTION_LOCK_TIMEOUT}'"
            )
        )
        lock_connection.execute(
            text("SELECT pg_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        lock_acquired = True
        yield
    finally:
        if not lock_acquired:
            # A transport error can make lock acquisition outcome uncertain.
            # Closing the physical PostgreSQL session is the only reliable way
            # to guarantee that an unacknowledged session lock is released.
            discard_lock_connection = True
        if lock_acquired:
            try:
                unlock_result = lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar()
                if unlock_result is not True:
                    # A false result means this physical connection no longer
                    # owns the lock. Never return an uncertain connection to
                    # the pool because it may still retain another lock level.
                    discard_lock_connection = True
                    logger.error(
                        "Realtime provider advisory lock was not owned during cleanup for %s",
                        normalized_session_id,
                    )
            except Exception:
                # Cleanup must not replace an exception from the guarded
                # operation. Invalidating the connection closes the underlying
                # PostgreSQL session, which releases any remaining advisory
                # lock even when an explicit unlock could not be confirmed.
                discard_lock_connection = True
                logger.exception(
                    "Failed to release realtime provider advisory lock for %s",
                    normalized_session_id,
                )

        if discard_lock_connection:
            try:
                lock_connection.invalidate()
            except Exception:
                logger.exception(
                    "Failed to invalidate realtime provider lock connection for %s",
                    normalized_session_id,
                )
        try:
            lock_connection.rollback()
        except Exception:
            logger.debug(
                "Failed to roll back realtime provider lock connection for %s",
                normalized_session_id,
                exc_info=True,
            )
        finally:
            try:
                lock_connection.close()
            except Exception:
                # Closing is the final cleanup fallback and must never mask a
                # provider/signaling exception raised inside the guard.
                logger.exception(
                    "Failed to close realtime provider lock connection for %s",
                    normalized_session_id,
                )


def build_runtime_for_start(
    db: Session,
    *,
    user_id: str,
    group_id: str | None,
    chat_id: str | None,
    project_id: str | None,
    model_id: str | None,
    skill_id: str | None = None,
) -> RealtimeSessionRuntime:
    reconcile_expired_realtime_sessions(db)
    settings = load_realtime_runtime_settings(db)

    if chat_id:
        try:
            chat = get_chat(db, chat_id, user_id)
            resolved_chat_id = chat.id
        except HTTPException:
            raise HTTPException(status_code=404, detail="Chat not found")
    else:
        chat = create_chat(user_id=user_id, db=db, project_id=project_id, meta={"status": "normal"})
        resolved_chat_id = chat.id

    selected_model_id = str(model_id or "").strip() or None
    selected_base_model_id: str | None = None
    selected_agent_id: str | None = None
    selected_agent_file_ids: list[str] = []
    selected_skill_file_ids: list[str] = []
    selected_model_settings: dict[str, Any] = {}
    effective_agent_instruction: str | None = None
    requested_skill_id = str(skill_id or "").strip() or None
    effective_skill_ids: list[str] = []
    if requested_skill_id:
        effective_skill_ids.append(requested_skill_id)
    effective_skill_content: str | None = None
    selected_tools: list[str] = []
    tool_schemas: list[dict[str, Any]] = []
    if selected_model_id:
        try:
            resolved_selection = resolve_selected_model_for_user(db, user_id=user_id, model_id=selected_model_id)
            db_model = resolved_selection.base_model
            selected_base_model_id = db_model.id
            if resolved_selection.model_kind == "agent" and resolved_selection.agent is not None:
                selected_agent_id = resolved_selection.agent.id
                effective_agent_instruction = resolved_selection.agent_instruction
                selected_agent_file_ids = [
                    file_id
                    for field_file_ids in (resolved_selection.asset_descriptors_by_category or {}).values()
                    for file_id in field_file_ids
                    if str(file_id or "").strip()
                ]
                if resolved_selection.agent_skill_ids:
                    effective_skill_ids = [*resolved_selection.agent_skill_ids, *effective_skill_ids]
            selected_model_settings = db_model.settings if isinstance(db_model.settings, dict) else {}
            model_skill_ids = _extract_realtime_model_skill_ids(selected_model_settings)
            if model_skill_ids:
                if requested_skill_id:
                    raise HTTPException(status_code=400, detail=FIXED_MODEL_SKILL_OVERRIDE_ERROR)
                effective_skill_ids = [*effective_skill_ids, *model_skill_ids]
        except HTTPException:
            raise
        except Exception:
            selected_model_id = None
            selected_base_model_id = None
            selected_agent_id = None
            selected_agent_file_ids = []
            selected_model_settings = {}
            effective_agent_instruction = None
            selected_tools = []
            tool_schemas = []

    # Realtime calls have their own explicit tool allow-list. The selected chat
    # model still supplies per-tool configuration, but its normal tool list must
    # not silently grant tools that an administrator omitted here.
    selected_tools, tool_schemas = resolve_configured_realtime_tools(
        db=db,
        configured_tools=settings.get("tools") or [],
        model_settings=selected_model_settings,
        user_id=user_id,
        project_id=project_id,
    )

    if effective_skill_ids:
        seen_skill_ids: set[str] = set()
        ordered_skill_ids: list[str] = []
        for raw_skill_id in effective_skill_ids:
            normalized_skill_id = str(raw_skill_id or "").strip()
            if not normalized_skill_id or normalized_skill_id in seen_skill_ids:
                continue
            seen_skill_ids.add(normalized_skill_id)
            ordered_skill_ids.append(normalized_skill_id)

        skill_chunks: list[str] = []
        for resolved_skill_id in ordered_skill_ids:
            # Skill files are tracked in ``selected_skill_file_ids`` below and
            # formatted according to the realtime provider's capabilities.
            # Keep only the authored skill instructions in the system prompt.
            content = get_skill_content_for_user(db, user_id, resolved_skill_id)
            if content:
                skill_chunks.append(f"[Skill {resolved_skill_id}]\n{content}")

        effective_skill_content = "\n\n".join(skill_chunks) if skill_chunks else None
        selected_skill_file_ids = _collect_skill_file_ids(db, user_id, ordered_skill_ids)
        if requested_skill_id and not effective_skill_content:
            raise HTTPException(status_code=404, detail="Skill not found")

        effective_skill_id = ordered_skill_ids[0] if ordered_skill_ids else None
    else:
        effective_skill_id = None

    session_id = str(uuid.uuid4())
    normalized_tool_schemas = _normalize_realtime_tool_schemas(tool_schemas)
    runtime = RealtimeSessionRuntime(
        id=session_id,
        user_id=user_id,
        group_id=group_id,
        chat_id=resolved_chat_id,
        project_id=project_id,
        model_id=selected_model_id,
        base_model_id=selected_base_model_id,
        agent_id=selected_agent_id,
        model_settings=selected_model_settings,
        skill_id=effective_skill_id,
        skill_content=effective_skill_content,
        agent_instruction=effective_agent_instruction,
        provider=settings["provider"],
        provider_id=settings["provider_id"],
        realtime_model=settings["model"],
        voice=settings["voice"],
        settings=_sanitize_realtime_runtime_settings(settings),
        created_chat=not bool(chat_id),
        skill_file_ids=selected_skill_file_ids,
        agent_file_ids=selected_agent_file_ids,
        tools=selected_tools,
        tool_schemas=normalized_tool_schemas,
    )

    chat_meta = chat.meta if isinstance(chat.meta, dict) else {}
    if selected_agent_id:
        chat_meta["agent_id"] = selected_agent_id
    else:
        chat_meta.pop("agent_id", None)
    if selected_base_model_id:
        chat_meta["base_model_id"] = selected_base_model_id
    else:
        chat_meta.pop("base_model_id", None)
    chat.meta = chat_meta
    db.commit()
    db.refresh(chat)

    session_record = create_realtime_session(
        db,
        session_id=session_id,
        user_id=user_id,
        chat_id=resolved_chat_id,
        model_id=selected_base_model_id or selected_model_id,
        model_name=settings["model"],
        provider=settings["provider"],
        provider_id=settings["provider_id"],
    )
    runtime.session_record_id = session_record.id
    runtime.turn.reset(next_turn_index=1)
    persist_realtime_runtime_state(db, runtime)
    session_registry.create(runtime)
    return runtime


def build_realtime_instructions(runtime: RealtimeSessionRuntime) -> str:
    sections = [DEFAULT_REALTIME_INSTRUCTIONS]
    if runtime.agent_instruction:
        sections.append(
            "Follow the agent instructions below for this realtime conversation.\n\n"
            f"## Agent Instructions\n\n{runtime.agent_instruction}"
        )
    if runtime.skill_content:
        sections.append(
            "Follow the skill instructions below for this realtime conversation.\n\n"
            f"## Skill Instructions\n\n{runtime.skill_content}"
        )
    return "\n\n".join(section for section in sections if section)


def build_realtime_session_config(runtime: RealtimeSessionRuntime) -> dict[str, Any]:
    if runtime.provider == ProviderEnum.google_aistudio.value:
        native_google_search_enabled = bool(
            runtime.model_settings.get("native_websearch")
            and "web_search" in runtime.tools
        )
        return build_google_aistudio_live_session_config(
            instructions=build_realtime_instructions(runtime),
            model_name=runtime.realtime_model,
            voice=runtime.voice,
            settings=runtime.settings,
            tool_schemas=runtime.tool_schemas,
            native_google_search_enabled=native_google_search_enabled,
        )

    if runtime.provider == ProviderEnum.xai.value:
        return build_xai_realtime_session_config(
            instructions=build_realtime_instructions(runtime),
            voice=runtime.voice,
            settings=runtime.settings,
            tool_schemas=runtime.tool_schemas,
        )

    input_audio: dict[str, Any] = {
        "turn_detection": {
            "type": "server_vad",
            "create_response": True,
            "interrupt_response": True,
        },
    }
    if bool(runtime.settings.get("input_transcription_enabled", True)):
        input_audio["transcription"] = {"model": DEFAULT_TRANSCRIPTION_MODEL}

    session: dict[str, Any] = {
        "type": "realtime",
        "model": runtime.realtime_model,
        "instructions": build_realtime_instructions(runtime),
        "audio": {
            "input": input_audio,
            "output": {
                "voice": runtime.voice,
            },
        },
    }
    max_output_tokens = runtime.settings.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and 1 <= max_output_tokens <= 4096:
        session["max_output_tokens"] = max_output_tokens
    if runtime.tool_schemas:
        session["tools"] = runtime.tool_schemas
        session["tool_choice"] = "auto"
    return session


def build_realtime_client_session_config(runtime: RealtimeSessionRuntime) -> dict[str, Any]:
    """Build the client-visible realtime session configuration.

    OpenAI-compatible sessions receive their full instructions through the
    backend-owned unified WebRTC request. Gemini receives its complete
    configuration through a backend-minted constrained token. The browser only
    needs non-sensitive protocol metadata, so privileged instructions are
    removed from both response paths.
    """
    if runtime.provider == ProviderEnum.google_aistudio.value:
        return build_google_aistudio_live_client_setup(
            model_name=runtime.realtime_model,
        )
    if runtime.provider == ProviderEnum.xai.value:
        return {
            "model": runtime.realtime_model,
            "sample_rate": 24_000,
            "audio_format": "pcm16",
        }

    session_config = build_realtime_session_config(runtime)
    client_config = copy.deepcopy(session_config)
    if isinstance(client_config, dict):
        client_config.pop("instructions", None)
    return client_config


def exchange_realtime_webrtc_offer(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    offer_sdp: str,
) -> str:
    """Exchange a browser SDP offer without exposing provider credentials.

    The trusted backend owns signaling, captures the provider's authoritative
    call ID from ``Location``, and can therefore terminate the direct media
    session when Omlorix's quota deadline is reached.
    """
    if runtime.provider not in OPENAI_REALTIME_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail="WebRTC signaling is not supported for this realtime provider")

    # Browser-generated SDP ends with CRLF. Preserve the offer exactly as the
    # browser produced it: OpenAI's WebRTC parser consumes line-oriented SDP,
    # and trimming the final line ending can make an otherwise valid Safari
    # offer fail with ``failed to unmarshal SDP: EOF``.
    provider_offer_sdp = str(offer_sdp or "")
    if not provider_offer_sdp.strip():
        raise HTTPException(status_code=422, detail="Realtime SDP offer is required")
    if runtime.provider_connection_state in {
        REALTIME_PROVIDER_CONNECTION_CONNECTING,
        REALTIME_PROVIDER_CONNECTION_ACTIVE,
        REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING,
    }:
        raise HTTPException(status_code=409, detail="Realtime provider connection already exists")

    request_settings = _load_openai_realtime_request_settings(
        db,
        provider_id=runtime.provider_id,
        provider_type=runtime.provider,
    )
    headers = _build_realtime_headers(
        request_settings,
        safety_identifier=_build_realtime_safety_identifier(db, runtime.user_id),
    )
    # httpx must generate the multipart boundary. OpenAI's server-side WebRTC
    # interface accepts both the SDP offer and the complete privileged session
    # configuration in this request, replacing the old browser-held secret.
    headers.pop("Content-Type", None)
    # Match the official multipart schema: both fields have no filename, while
    # their per-part media types describe the SDP and JSON payloads. httpx owns
    # the multipart boundary and top-level Content-Type header.
    multipart_body = {
        "sdp": (None, provider_offer_sdp, "application/sdp"),
        "session": (
            None,
            json.dumps(build_realtime_session_config(runtime), separators=(",", ":")),
            "application/json",
        ),
    }

    runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_CONNECTING
    runtime.provider_session_handle = None
    runtime.provider_stop_requested_at = None
    runtime.provider_stop_reason = None
    persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)

    try:
        response = httpx.post(
            build_realtime_call_url(request_settings.get("base_url")),
            headers=headers,
            files=multipart_body,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_IDLE
        persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)
        raise HTTPException(status_code=502, detail="Failed to establish realtime provider connection") from exc

    try:
        data = response.json() if response.content else {}
    except Exception:
        data = {"error": {"message": response.text}} if response.text else {}
    if not response.is_success:
        detail = data
        if isinstance(data, dict):
            error_obj = data.get("error")
            if isinstance(error_obj, dict):
                detail = error_obj.get("message") or error_obj.get("code") or data
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_IDLE
        persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)
        raise HTTPException(status_code=response.status_code, detail=detail or "Failed to establish realtime provider connection")

    location = str(response.headers.get("Location") or response.headers.get("location") or "").strip()
    call_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
    if not _OPENAI_REALTIME_CALL_ID_PATTERN.fullmatch(call_id):
        # Never hand an answer to the browser when Omlorix cannot retain an
        # authoritative termination handle for the resulting provider call.
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_IDLE
        persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)
        raise HTTPException(
            status_code=502,
            detail="Realtime provider did not return a controllable call identifier",
        )

    # Return OpenAI's SDP answer byte-for-byte (after HTTP text decoding). The
    # browser's SDP parser should receive the same terminating line ending that
    # the provider emitted.
    answer_sdp = str(response.text or "")
    if not answer_sdp.strip():
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_ACTIVE
        runtime.provider_session_handle = call_id
        persist_realtime_runtime_state(
            db,
            runtime,
            provider_state_authoritative=True,
        )
        mark_runtime_inactive(
            db,
            runtime,
            reason="empty_provider_answer",
            status="error",
        )
        raise HTTPException(status_code=502, detail="Realtime provider returned an empty SDP answer")

    now = _utc_now()
    runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_ACTIVE
    runtime.provider_session_handle = call_id
    runtime.provider_connection_started_at = now
    runtime.provider_connection_last_seen_at = now
    runtime.last_activity_at = now
    persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)
    reservation_is_valid = (
        not runtime.rate_limit_admission_id
        or touch_duration_rate_limit_admission(
            db,
            runtime.rate_limit_admission_id,
        )
    )
    if not reservation_is_valid:
        mark_runtime_inactive(
            db,
            runtime,
            reason="reservation_expired",
            status="expired",
        )
        raise HTTPException(
            status_code=409,
            detail="Realtime session reservation expired",
        )
    return answer_sdp


def terminate_openai_realtime_call(
    db: Session,
    runtime: RealtimeSessionRuntime,
) -> bool:
    """Terminate one OpenAI-compatible WebRTC call by its trusted call ID."""
    call_id = str(runtime.provider_session_handle or "").strip()
    if not call_id:
        return True
    if not _OPENAI_REALTIME_CALL_ID_PATTERN.fullmatch(call_id):
        logger.error("Refusing to use invalid realtime provider call ID for session %s", runtime.id)
        return False

    request_settings = _load_openai_realtime_request_settings(
        db,
        provider_id=runtime.provider_id,
        provider_type=runtime.provider,
    )
    hangup_url = (
        f"{_resolve_realtime_http_base_url(request_settings.get('base_url'))}"
        f"/realtime/calls/{quote(call_id, safe='')}/hangup"
    )
    try:
        response = httpx.post(
            hangup_url,
            headers=_build_realtime_headers(request_settings),
            timeout=20.0,
        )
    except httpx.HTTPError:
        logger.exception("Failed to terminate realtime provider call for session %s", runtime.id)
        return False

    # A missing/already-ended provider call is an idempotent success.
    if response.is_success or response.status_code in {404, 409, 410}:
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_TERMINATED
        runtime.provider_connection_last_seen_at = _utc_now()
        return True

    logger.warning(
        "Realtime provider hangup failed for session %s with status %s",
        runtime.id,
        response.status_code,
    )
    return False


def build_realtime_connection_response(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    session_handle: str | None = None,
) -> dict[str, Any]:
    # Return an absolute deadline as well as a convenience duration. The
    # runtime clock starts before provider credential minting and browser
    # negotiation, so returning the original allowance would grant that setup
    # time again when the browser eventually starts its timer.
    session_expires_at = runtime.absolute_expires_at()
    remaining_session_seconds = int(
        math.ceil((session_expires_at - _utc_now()).total_seconds())
    )
    if remaining_session_seconds <= 0:
        raise HTTPException(status_code=409, detail="Realtime session expired")

    if runtime.provider == ProviderEnum.google_aistudio.value:
        if session_handle is not None:
            runtime.google_session_handle = _normalize_optional_string(session_handle)
            persist_realtime_runtime_state(
                db,
                runtime,
                provider_state_authoritative=True,
            )
        # Gemini credentials remain server-side. The browser connects to a
        # same-origin Omlorix proxy that owns and can close the upstream socket.
        session_payload = build_google_aistudio_live_client_setup(
            model_name=runtime.realtime_model,
        )
        return {
            "transport": "websocket",
            "protocol_version": "google-live-proxy-v1",
            "websocket_url": f"/api/v1/realtime/session/{quote(runtime.id, safe='')}/google-live",
            "signaling_url": None,
            "session": session_payload,
            "max_session_seconds": remaining_session_seconds,
            "session_expires_at": session_expires_at,
        }

    if runtime.provider == ProviderEnum.xai.value:
        return {
            "transport": "websocket",
            "protocol_version": "xai-realtime-proxy-v1",
            "websocket_url": (
                f"/api/v1/realtime/session/{quote(runtime.id, safe='')}/xai-live"
            ),
            "signaling_url": None,
            "session": build_realtime_client_session_config(runtime),
            "max_session_seconds": remaining_session_seconds,
            "session_expires_at": session_expires_at,
        }

    return {
        "transport": "webrtc",
        "protocol_version": "webrtc-server-signaled-v1",
        "websocket_url": None,
        "signaling_url": f"/api/v1/realtime/session/{quote(runtime.id, safe='')}/webrtc-offer",
        "session": build_realtime_client_session_config(runtime),
        "max_session_seconds": remaining_session_seconds,
        "session_expires_at": session_expires_at,
    }


def request_provider_session_termination(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    reason: str | None,
) -> bool:
    """Request authoritative provider termination and return completion state.

    OpenAI-compatible calls can be terminated synchronously with their call ID.
    Gemini is transported through Omlorix's WebSocket proxy, so a persisted
    ``termination_pending`` state tells the owning proxy to close upstream.
    """
    if runtime.provider_connection_state in {
        REALTIME_PROVIDER_CONNECTION_IDLE,
        REALTIME_PROVIDER_CONNECTION_TERMINATED,
    }:
        runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_TERMINATED
        runtime.provider_connection_owner_id = None
        return True

    runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
    # This timestamp doubles as retry backoff state after a provider outage.
    runtime.provider_stop_requested_at = _utc_now()
    runtime.provider_stop_reason = str(reason or "server_stop").strip()[:256]
    persist_realtime_runtime_state(db, runtime, provider_state_authoritative=True)

    if runtime.provider in OPENAI_REALTIME_PROVIDER_TYPES:
        terminated = terminate_openai_realtime_call(db, runtime)
        persist_realtime_runtime_state(
            db,
            runtime,
            provider_state_authoritative=True,
        )
        return terminated

    # Gemini's credential and upstream WebSocket are held only by Omlorix. The
    # proxy observes this persisted state and closes the provider connection.
    return False


def _mark_runtime_inactive_locked(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    reason: str | None = None,
    status: str = "stopped",
    provider_already_terminated: bool = False,
) -> bool:
    """Terminate resources while the caller owns the provider transition lock.

    Returns ``False`` while a provider-owned resource is still awaiting
    authoritative termination. Callers must not release the duration admission
    or represent the runtime as stopped until this function returns ``True``.
    """
    if not provider_already_terminated and not request_provider_session_termination(
        db,
        runtime,
        reason=reason,
    ):
        return False

    runtime.provider_connection_state = REALTIME_PROVIDER_CONNECTION_TERMINATED
    runtime.provider_connection_owner_id = None
    runtime.active = False
    runtime.last_activity_at = _utc_now()
    finalize_duration_rate_limit_admission(
        db,
        getattr(runtime, "rate_limit_admission_id", None),
        consumed_seconds=(
            0
            if status == "error" and reason == "start_failed"
            else _billable_realtime_elapsed_seconds(runtime, runtime.last_activity_at)
        ),
        final_status=(
            RATE_LIMIT_ADMISSION_FAILED
            if status == "error" and reason == "start_failed"
            else RATE_LIMIT_ADMISSION_COMPLETED
        ),
    )
    if runtime.session_record_id:
        update_realtime_session(
            db,
            runtime.session_record_id,
            status=status,
            ended_at=_utc_now(),
            # A stopped provider cannot reconnect. Remove instructions,
            # transcripts, tool outputs, schemas, and connection handles from
            # durable storage while retaining the small lifecycle summary.
            runtime_state={},
            stop_reason=reason,
        )
    return True


def mark_runtime_inactive(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    reason: str | None = None,
    status: str = "stopped",
    provider_already_terminated: bool = False,
) -> bool:
    """Serialize provider termination against connection establishment.

    OpenAI signaling performs an outbound provider request after the browser
    already knows the Omlorix session ID. A browser stop or deadline worker can
    therefore arrive while that request is still pending. Both paths must use
    the same process-local and PostgreSQL advisory locks as signaling so a stop
    cannot observe an empty handle and then be overwritten by the late provider
    response.

    The runtime is restored after acquiring the locks because the object passed
    by an HTTP request or background worker may predate a provider transition
    committed by another process.
    """

    session_id = str(getattr(runtime, "id", "") or "").strip()
    if not session_id:
        return _mark_runtime_inactive_locked(
            db,
            runtime,
            reason=reason,
            status=status,
            provider_already_terminated=provider_already_terminated,
        )

    original_runtime = runtime
    with session_registry.connection_lock(session_id):
        with serialized_realtime_provider_connection(db, session_id):
            persisted_runtime = restore_realtime_session_runtime(
                db,
                session_id=session_id,
            )
            if persisted_runtime is not None:
                runtime = persisted_runtime
            terminated = _mark_runtime_inactive_locked(
                db,
                runtime,
                reason=reason,
                status=status,
                provider_already_terminated=provider_already_terminated,
            )

            # Keep an in-process registry/request object coherent with the
            # authoritative persisted runtime. This is best-effort convenience;
            # future requests still restore database state before acting.
            if runtime is not original_runtime:
                for key in REALTIME_PROVIDER_CONTROL_STATE_KEYS:
                    setattr(original_runtime, key, getattr(runtime, key))
                original_runtime.active = runtime.active
                original_runtime.last_activity_at = runtime.last_activity_at

    # Workers also terminate runtimes restored from the database that were
    # never registered in this process. Retiring the lock entry here prevents
    # those one-off session IDs from accumulating for the lifetime of a worker.
    if terminated:
        session_registry.remove(session_id)
    return terminated


def prepare_runtime_text_input(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    text: str,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_file_ids: list[str] = []
    seen_file_ids: set[str] = set()
    for raw_file_id in [*(runtime.skill_file_ids or []), *(runtime.agent_file_ids or []), *(file_ids or [])]:
        if raw_file_id is None:
            continue
        normalized_file_id = str(raw_file_id).strip()
        if not normalized_file_id or normalized_file_id in seen_file_ids:
            continue
        seen_file_ids.add(normalized_file_id)
        normalized_file_ids.append(normalized_file_id)
    display_text = str(text or "").strip()
    if not display_text and normalized_file_ids:
        display_text = f"Attached {len(normalized_file_ids)} file{'s' if len(normalized_file_ids) != 1 else ''}."
    runtime.turn.file_ids = normalized_file_ids
    runtime.last_activity_at = _utc_now()
    if runtime.provider == ProviderEnum.google_aistudio.value:
        file_context = build_file_context_text(db, user_id=runtime.user_id, file_ids=normalized_file_ids)
        prompt_chunks = [
            chunk
            for chunk in [
                str(text or "").strip(),
                file_context.strip() if file_context else "",
            ]
            if chunk
        ]
        if not prompt_chunks:
            return {
                "mode": "realtime_input",
                "realtime_input": {},
                "display_text": display_text,
                "file_ids": normalized_file_ids,
            }
        return {
            "mode": "realtime_input",
            "realtime_input": {
                "text": "\n\n".join(prompt_chunks),
            },
            "display_text": display_text,
            "file_ids": normalized_file_ids,
        }
    content_parts = build_realtime_input_parts(
        db,
        runtime,
        text=str(text or ""),
        file_ids=normalized_file_ids,
    )
    return {
        "mode": "conversation_item",
        "content_parts": content_parts,
        "display_text": display_text,
        "file_ids": normalized_file_ids,
    }


def register_realtime_tool_result(
    runtime: RealtimeSessionRuntime,
    *,
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    output_string: str,
    events: list[dict[str, Any]] | None = None,
) -> None:
    runtime.completed_tool_calls[str(call_id or "").strip()] = {
        "tool_name": tool_name,
        "output": output_string,
        "events": list(events or []),
    }
    runtime.turn.tool_blocks.append(
        build_tool_call_block(
            tool_name,
            arguments,
            tool_call_id=call_id,
            extra_meta={
                "realtime": {"session_id": runtime.id, "turn_index": runtime.turn.turn_index},
            },
        )
    )
    runtime.turn.tool_blocks.append(
        {
            "type": "tool_call_result",
            "content": output_string[:8000],
            "meta": {
                "tool_name": tool_name,
                "call_id": call_id,
                "realtime": {"session_id": runtime.id, "turn_index": runtime.turn.turn_index},
            },
        }
    )
    runtime.last_activity_at = _utc_now()


def _get_persisted_realtime_turn(
    db: Session,
    *,
    chat_id: str,
    session_id: str,
    turn_id: str,
) -> dict[str, Any] | None:
    # Chat messages, not optional analytics, are the durable idempotency
    # boundary. An administrator may delete statistics while a call is still
    # active; a browser retry must still return the already-created messages.
    existing_messages = (
        db.query(ChatMessages)
        .filter(
            ChatMessages.chat_id == chat_id,
            ChatMessages.realtime_session_id == session_id,
            ChatMessages.realtime_turn_id == turn_id,
        )
        .all()
    )
    if not existing_messages:
        return None

    user_message = next((row for row in existing_messages if row.role == "user"), None)
    assistant_message = next((row for row in existing_messages if row.role == "assistant"), None)
    if not user_message and not assistant_message:
        return None

    existing_stat = (
        db.query(LLMGenerationStatistic)
        .filter(
            LLMGenerationStatistic.interaction_type
            == INTERACTION_TYPE_REALTIME_RESPONSE,
            LLMGenerationStatistic.session_id == session_id,
            LLMGenerationStatistic.turn_id == turn_id,
        )
        .order_by(LLMGenerationStatistic.created_at.asc())
        .first()
    )
    turn_index = getattr(existing_stat, "turn_index", None)
    if turn_index is None:
        # Message metadata keeps the presentation index available after an
        # analytics purge without making analytics authoritative for retries.
        for message in existing_messages:
            try:
                blocks = json.loads(message.content)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                realtime_meta = (
                    block.get("meta", {}).get("realtime", {})
                    if isinstance(block, dict)
                    else {}
                )
                if not isinstance(realtime_meta, dict):
                    continue
                raw_turn_index = realtime_meta.get("turn_index")
                if raw_turn_index is None:
                    continue
                try:
                    turn_index = max(0, int(raw_turn_index))
                except (TypeError, ValueError):
                    turn_index = None
                break
            if turn_index is not None:
                break

    chat = db.query(Chats).filter(Chats.id == chat_id).first()
    payload = {
        "turn_index": turn_index,
        "user_message_id": getattr(user_message, "id", None),
        "assistant_message_id": getattr(assistant_message, "id", None),
        "chat_id": chat_id,
    }
    chat_title = str(getattr(chat, "title", "") or "").strip()
    if chat_title:
        payload["chat_title"] = chat_title
    return payload


def _fallback_realtime_chat_title(first_user_message: str) -> str:
    """Build the same safe, bounded fallback title used by normal chats."""
    raw_title = str(first_user_message or "").strip()[:60]
    return str(sanitize_chat_text(raw_title) or "").strip()


def _prepare_realtime_first_turn_title(
    db: Session,
    runtime: RealtimeSessionRuntime,
    first_user_message: str,
) -> tuple[str | None, bool]:
    """Store a fallback title and report whether generation should run later.

    Realtime audio uses a separate persistence endpoint from normal streaming
    chat. The first-turn endpoint must remain fast because the browser serializes
    provider events behind that request, so the potentially slow model call is
    delegated to a FastAPI background task. The bounded fallback gives the new
    sidebar row a useful title immediately.
    """
    if not runtime.created_chat or not str(first_user_message or "").strip():
        return None, False

    fallback_title = _fallback_realtime_chat_title(first_user_message)
    if not fallback_title:
        return None, False

    # Use one conditional UPDATE instead of a read followed by a write. Besides
    # avoiding a race, this bypasses a potentially stale SQLAlchemy identity-map
    # value when another request renames the chat concurrently.
    try:
        updated = (
            db.query(Chats)
            .filter(
                Chats.id == runtime.chat_id,
                Chats.user_id == runtime.user_id,
                or_(Chats.title.is_(None), func.trim(Chats.title) == ""),
            )
            .update({Chats.title: fallback_title}, synchronize_session=False)
        )
        db.commit()
    except Exception:
        logger.exception("Failed to persist fallback realtime title for chat %s", runtime.chat_id)
        db.rollback()
        return None, False

    if not updated:
        # The chat may already have a title because the user renamed it while
        # the first turn was being saved. Return that value without scheduling
        # generation that could replace it.
        chat = (
            db.query(Chats)
            .filter(Chats.id == runtime.chat_id, Chats.user_id == runtime.user_id)
            .first()
        )
        return str(getattr(chat, "title", "") or "").strip() or None, False

    model_settings = runtime.model_settings if isinstance(runtime.model_settings, dict) else {}
    current_model_id = str(runtime.base_model_id or runtime.model_id or "").strip()
    title_model_mode = model_settings.get("title_generation_model")
    specific_model_id = str(model_settings.get("title_generation_model_id") or "").strip()
    has_title_model = bool(
        current_model_id
        or (title_model_mode == "specific" and specific_model_id)
    )
    should_generate = bool(
        model_settings.get("title_generation", False)
        and has_title_model
        and title_model_mode in {"current", "specific"}
    )
    return fallback_title, should_generate


def _generate_and_persist_realtime_first_turn_title(
    db: Session,
    *,
    chat_id: str,
    user_id: str,
    project_id: str | None,
    current_model_id: str,
    model_settings: dict[str, Any],
    first_user_message: str,
    expected_title: str,
) -> str | None:
    """Generate a title and atomically replace only the untouched fallback."""
    try:
        # Skip the provider request when the fallback has already been renamed.
        fallback_is_current = (
            db.query(Chats.id)
            .filter(
                Chats.id == chat_id,
                Chats.user_id == user_id,
                Chats.title == expected_title,
            )
            .first()
        )
        if not fallback_is_current:
            return None

        current_model = db.query(Models).filter(Models.id == current_model_id).first()
        use_model = current_model
        if model_settings.get("title_generation_model") == "specific":
            title_model_id = str(model_settings.get("title_generation_model_id") or "").strip()
            if title_model_id:
                specific_model = db.query(Models).filter(Models.id == title_model_id).first()
                if specific_model:
                    use_model = specific_model
        if not use_model:
            return None

        use_model_settings = use_model.settings if isinstance(use_model.settings, dict) else {}
        custom_instruction = str(
            use_model_settings.get("custom_title_generation_instruction") or ""
        ).strip()
        system_instruction = custom_instruction or get_title_generation_prompt(user_id, db)
        generated_title = call_provider_title_generation(
            ProviderRequest(
                request_type=REQUEST_TYPE_TITLE_GENERATION,
                db=db,
                provider=use_model.provider,
                model=use_model,
                prompt=first_user_message,
                system_instruction=system_instruction,
                user_id=user_id,
                project_id=project_id,
                extra={"provider_id": use_model.provider_id},
            )
        )
        sanitized_title = str(
            sanitize_chat_text(str(generated_title or "")[:60]) or ""
        ).strip()
        if not sanitized_title:
            return None

        # Compare-and-set protects a rename committed while the provider request
        # was running. It also avoids relying on the session identity map.
        updated = (
            db.query(Chats)
            .filter(
                Chats.id == chat_id,
                Chats.user_id == user_id,
                Chats.title == expected_title,
            )
            .update({Chats.title: sanitized_title}, synchronize_session=False)
        )
        db.commit()
        return sanitized_title if updated else None
    except Exception:
        db.rollback()
        logger.exception("Realtime title generation failed for chat %s", chat_id)
        return None


def generate_realtime_first_turn_title(**kwargs: Any) -> None:
    """Run realtime title generation with a session owned by the background task."""
    db = SessionLocal()
    try:
        _generate_and_persist_realtime_first_turn_title(db, **kwargs)
    finally:
        db.close()


def persist_runtime_turn(
    db: Session,
    runtime: RealtimeSessionRuntime,
    *,
    turn_id: str,
    user_transcript: str = "",
    assistant_transcript: str = "",
    file_ids: list[str] | None = None,
    interrupted: bool = False,
    error_message: str | None = None,
    usage: dict[str, Any] | None = None,
    provider_interactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_turn_id = str(turn_id or "").strip()
    if not normalized_turn_id:
        raise HTTPException(status_code=400, detail="Realtime turn id is required")

    existing_turn = _get_persisted_realtime_turn(
        db,
        chat_id=runtime.chat_id,
        session_id=runtime.id,
        turn_id=normalized_turn_id,
    )
    if existing_turn:
        runtime.last_activity_at = _utc_now()
        return existing_turn

    turn = runtime.turn
    turn.user_transcript = str(user_transcript or "").strip()
    turn.assistant_transcript = str(assistant_transcript or "").strip()
    turn.file_ids = [
        str(file_id).strip()
        for file_id in (file_ids if file_ids is not None else turn.file_ids)
        if str(file_id).strip()
    ]
    turn.interrupted = bool(interrupted)
    runtime.last_activity_at = _utc_now()

    if not (turn.user_transcript or turn.assistant_transcript or turn.tool_blocks or error_message):
        return {}

    realtime_meta = {
        "session_id": runtime.id,
        "turn_id": normalized_turn_id,
        "turn_index": turn.turn_index,
        "interrupted": bool(turn.interrupted),
        "file_ids": turn.file_ids,
    }
    if runtime.agent_id:
        realtime_meta["agent_id"] = runtime.agent_id
    if runtime.base_model_id:
        realtime_meta["base_model_id"] = runtime.base_model_id
    attachment_fields = build_realtime_attachment_fields(
        user_id=runtime.user_id,
        file_ids=turn.file_ids,
    )
    user_blocks = [
        {
            "type": "user",
            "content": turn.user_transcript,
            "meta": {"realtime": realtime_meta},
            **{field: value for field, value in attachment_fields.items() if value},
        }
    ]
    assistant_blocks: list[dict[str, Any]] = list(turn.tool_blocks)
    assistant_blocks.append(
        {
            "type": "content",
            "content": turn.assistant_transcript,
            "meta": {"realtime": realtime_meta},
        }
    )
    try:
        user_msg = create_chat_message(
            db,
            chat_id=runtime.chat_id,
            model_id=runtime.model_id or runtime.realtime_model,
            role="user",
            content=user_blocks,
            reference_id=None,
            realtime_session_id=runtime.id,
            realtime_turn_id=normalized_turn_id,
            commit=False,
        )
        assistant_msg = create_chat_message(
            db,
            chat_id=runtime.chat_id,
            model_id=runtime.model_id or runtime.realtime_model,
            role="assistant",
            content=assistant_blocks,
            reference_id=user_msg.id,
            realtime_session_id=runtime.id,
            realtime_turn_id=normalized_turn_id,
            commit=False,
        )

        interactions: list[dict[str, Any]] = []
        seen_response_ids: set[str] = set()
        for item in provider_interactions or []:
            if not isinstance(item, dict):
                continue
            response_id = str(item.get("response_id") or "").strip()
            if not response_id or response_id in seen_response_ids:
                continue
            seen_response_ids.add(response_id)
            interactions.append(item)
        if not interactions:
            # Gemini Live currently emits one usage snapshot around turn
            # completion without a provider response ID. A deterministic
            # synthetic ID retains response-grain idempotency without storing
            # the browser's entire turn payload in analytics metadata.
            interactions = [
                {
                    "response_id": f"{normalized_turn_id}:final",
                    "status": "failed" if error_message else "completed",
                    "error_message": error_message,
                    "usage": usage or {},
                    "completed_at": runtime.last_activity_at,
                }
            ]

        for interaction_index, interaction in enumerate(interactions):
            is_final_interaction = interaction_index == len(interactions) - 1
            completed_at = _clamp_realtime_completed_at(
                interaction.get("completed_at"),
                session_started_at=runtime.created_at,
            )
            try:
                # Analytics is subordinate to the chat transcript. A nested
                # transaction lets one malicious or replayed response ID roll
                # back only that fact rather than both persisted messages.
                with db.begin_nested():
                    create_realtime_response_statistic(
                        db,
                        model_name=runtime.realtime_model,
                        model_id=str(runtime.base_model_id or runtime.model_id or runtime.realtime_model),
                        provider=runtime.provider,
                        provider_id=runtime.provider_id,
                        session_id=runtime.id,
                        turn_id=normalized_turn_id,
                        provider_response_id=str(interaction.get("response_id") or "").strip(),
                        turn_index=turn.turn_index,
                        usage=interaction.get("usage") if isinstance(interaction.get("usage"), dict) else {},
                        provider_status=str(interaction.get("status") or ""),
                        interrupted=turn.interrupted if is_final_interaction else False,
                        error_message=(
                            str(interaction.get("error_message") or error_message or "")
                            if is_final_interaction or interaction.get("error_message")
                            else None
                        ),
                        started_at=turn.started_at if interaction_index == 0 else None,
                        completed_at=completed_at,
                        user_id=runtime.user_id,
                        commit=False,
                    )
            except IntegrityError:
                logger.warning(
                    "Skipping duplicate realtime response statistic for session %s and response %s",
                    runtime.id,
                    str(interaction.get("response_id") or "").strip(),
                )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_turn = _get_persisted_realtime_turn(
            db,
            chat_id=runtime.chat_id,
            session_id=runtime.id,
            turn_id=normalized_turn_id,
        )
        if existing_turn:
            return existing_turn
        raise HTTPException(status_code=500, detail="Failed to persist realtime turn")
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to persist realtime turn") from exc
    saved_payload = {
        "turn_index": turn.turn_index,
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id,
        "chat_id": runtime.chat_id,
    }
    chat_title = None
    chat_title_pending = False
    if turn.turn_index == 1 and turn.user_transcript:
        chat_title, chat_title_pending = _prepare_realtime_first_turn_title(
            db,
            runtime,
            turn.user_transcript,
        )
    if not chat_title:
        chat = db.query(Chats).filter(Chats.id == runtime.chat_id).first()
        chat_title = str(getattr(chat, "title", "") or "").strip()
    if chat_title:
        saved_payload["chat_title"] = chat_title
    if chat_title_pending:
        saved_payload["chat_title_pending"] = True
    turn.reset(next_turn_index=turn.turn_index + 1)
    return saved_payload
