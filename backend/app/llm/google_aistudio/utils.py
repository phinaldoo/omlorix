"""Backward-compatible public API for the Google AI Studio integration.

Focused sibling modules own chat, models, usage, attachments, messages, and
generation. Imports remain here as intentional compatibility seams.
"""

from __future__ import annotations

# ruff: noqa: F401, E402

import base64
import inspect
from datetime import datetime, timezone
import json
import logging
import copy
import os
import tempfile
import time
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.groups.init import get_user_group_setting_value
from app.chats.streaming import interruptible_provider_stream
from app.llm.models import (
    Models,
    get_llm_provider,
    update_provider_availability,
    create_model,
)
from app.llm.system_instruction.chat import (
    append_system_instruction_sections,
    get_default_system_instruction,
)
from app.llm.helper import (
    build_tool_call_block,
    extract_tool_call_block,
    format_tool_call_block_label,
    merge_settings,
    should_persist_files_in_file_block,
    build_tool_file_block,
    stringify_tool_result_content_for_persistence,
    build_stream_tool_event_meta,
    build_widget_block_meta,
    build_file_metadata_text,
    normalize_unsupported_file_ids,
    safe_list_project_files,
)
from app.llm.google_aistudio.schemas import (
    GoogleAiStudioModelSettings,
    google_aistudio_supported_languages,
    google_ai_studio_image_mime_types,
    google_ai_studio_document_mime_types,
    google_ai_studio_audio_mime_types,
    google_ai_studio_video_mime_types,
)
from app.llm.google_aistudio.description import normalize_aistudio_model_description
from app.llm.google_aistudio.model_list import (
    AISTUDIO_MODEL_DICT,
    AISTUDIO_MODELS_NOT_SUPPORTED,
)
from app.llm.system_instruction.projects import (
    get_project_context_start,
    get_project_context_end,
)
from app.llm.system_instruction.group import (
    get_group_context_start,
    get_group_context_end,
)
from app.llm.helper import format_meta_timestamp
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.users.init import get_user_setting_value
from app.users.roles import is_admin_role
from app.llm.models import create_llm_provider
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)
from app.llm.token_usage import coerce_token_count
from app.llm.websearch_citations import (
    build_web_search_citations,
    collect_tool_result_citations,
)


logger = logging.getLogger(__name__)

AISTUDIO_FILE_ACTIVE_POLL_INTERVAL_SECONDS = 1.0
AISTUDIO_FILE_ACTIVE_TIMEOUT_SECONDS = 30.0
AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS = 30.0


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value) if value is not None else False


AISTUDIO_SAFETY_DEFAULT_THRESHOLD = "HARM_BLOCK_THRESHOLD_UNSPECIFIED"
AISTUDIO_MEDIA_RESOLUTION_VALUE_MAP = {
    "LOW": "MEDIA_RESOLUTION_LOW",
    "MEDIUM": "MEDIA_RESOLUTION_MEDIUM",
    "HIGH": "MEDIA_RESOLUTION_HIGH",
    "MEDIA_RESOLUTION_LOW": "MEDIA_RESOLUTION_LOW",
    "MEDIA_RESOLUTION_MEDIUM": "MEDIA_RESOLUTION_MEDIUM",
    "MEDIA_RESOLUTION_HIGH": "MEDIA_RESOLUTION_HIGH",
}
AISTUDIO_SAFETY_THRESHOLDS = {
    AISTUDIO_SAFETY_DEFAULT_THRESHOLD,
    "OFF",
    "BLOCK_NONE",
    "BLOCK_ONLY_HIGH",
    "BLOCK_MEDIUM_AND_ABOVE",
    "BLOCK_LOW_AND_ABOVE",
}
AISTUDIO_SAFETY_FIELDS: tuple[tuple[str, str], ...] = (
    ("safety_harassment", "HARM_CATEGORY_HARASSMENT"),
    ("safety_hate_speech", "HARM_CATEGORY_HATE_SPEECH"),
    ("safety_sexually_explicit", "HARM_CATEGORY_SEXUALLY_EXPLICIT"),
    ("safety_dangerous_content", "HARM_CATEGORY_DANGEROUS_CONTENT"),
    ("safety_civic_integrity", "HARM_CATEGORY_CIVIC_INTEGRITY"),
)


def build_aistudio_safety_settings(
    settings: dict | None = None,
) -> list[types.SafetySetting]:
    raw_settings = settings if isinstance(settings, dict) else {}
    safety_settings: list[types.SafetySetting] = []
    for field_name, category in AISTUDIO_SAFETY_FIELDS:
        threshold = (
            str(raw_settings.get(field_name) or AISTUDIO_SAFETY_DEFAULT_THRESHOLD)
            .strip()
            .upper()
        )
        if threshold not in AISTUDIO_SAFETY_THRESHOLDS:
            threshold = AISTUDIO_SAFETY_DEFAULT_THRESHOLD
        safety_settings.append(
            types.SafetySetting(category=category, threshold=threshold)
        )
    return safety_settings


def _normalize_aistudio_media_resolution(value: Any):
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if not normalized:
        return None
    mapped = AISTUDIO_MEDIA_RESOLUTION_VALUE_MAP.get(normalized)
    if not mapped:
        return None
    media_resolution_enum = getattr(types, "MediaResolution", None)
    return (
        getattr(media_resolution_enum, mapped, mapped)
        if media_resolution_enum is not None
        else mapped
    )


def _coerce_aistudio_video_fps(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return None
    if fps <= 0:
        return None
    return fps


def build_aistudio_video_metadata(settings: dict | None = None):
    fps = _coerce_aistudio_video_fps((settings or {}).get("video_fps"))
    if fps is None:
        return None
    video_metadata_cls = getattr(types, "VideoMetadata", None)
    if video_metadata_cls is None:
        return None
    return video_metadata_cls(fps=fps)


def _build_aistudio_file_part(
    *,
    file_uri: str,
    mime_type: str | None = None,
    video_metadata=None,
):
    part_cls = getattr(types, "Part", None)
    if part_cls is None:
        raise AttributeError("google.genai.types does not expose Part")

    file_data_cls = getattr(types, "FileData", None)
    if file_data_cls is not None:
        file_data_kwargs = {"file_uri": file_uri}
        if mime_type:
            file_data_kwargs["mime_type"] = mime_type
        part_kwargs = {"file_data": file_data_cls(**file_data_kwargs)}
        if video_metadata is not None:
            part_kwargs["video_metadata"] = video_metadata
        return part_cls(**part_kwargs)

    if video_metadata is None and hasattr(part_cls, "from_uri"):
        return part_cls.from_uri(file_uri=file_uri, mime_type=mime_type)

    raise AttributeError(
        "google.genai.types does not expose FileData required for video metadata parts"
    )


def _aistudio_file_state_name(uploaded_file: Any) -> str | None:
    state = getattr(uploaded_file, "state", None)
    if state is None:
        return None
    state_name = getattr(state, "name", None)
    if isinstance(state_name, str) and state_name.strip():
        return state_name.strip().upper()
    state_value = str(state).strip()
    if not state_value:
        return None
    if "." in state_value:
        state_value = state_value.rsplit(".", 1)[-1]
    return state_value.upper()


def wait_for_aistudio_file_active(
    client,
    uploaded_file: Any,
    *,
    poll_interval_seconds: float = AISTUDIO_FILE_ACTIVE_POLL_INTERVAL_SECONDS,
    timeout_seconds: float | None = AISTUDIO_FILE_ACTIVE_TIMEOUT_SECONDS,
    deadline_monotonic: float | None = None,
):
    file_name = str(getattr(uploaded_file, "name", "") or "").strip()
    state_name = _aistudio_file_state_name(uploaded_file)
    if not file_name or not state_name or state_name == "ACTIVE":
        return uploaded_file

    files_api = getattr(client, "files", None)
    get_file = getattr(files_api, "get", None)
    if not callable(get_file):
        raise RuntimeError(
            f"Uploaded Google AI Studio file {file_name} is {state_name} and cannot be refreshed."
        )

    now = time.monotonic()
    deadlines: list[float] = []
    if timeout_seconds is not None:
        deadlines.append(now + max(float(timeout_seconds), 0.1))
    if deadline_monotonic is not None:
        deadlines.append(float(deadline_monotonic))
    deadline = min(deadlines) if deadlines else now
    sleep_seconds = max(float(poll_interval_seconds), 0.1)
    current = uploaded_file

    while state_name == "PROCESSING":
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError(
                f"Timed out waiting for Google AI Studio file {file_name} to become ACTIVE."
            )
        time.sleep(min(sleep_seconds, remaining_seconds))
        current = get_file(name=file_name)
        state_name = _aistudio_file_state_name(current)
        if not state_name:
            return current

    if state_name == "FAILED":
        raise RuntimeError(f"Google AI Studio file {file_name} failed processing.")
    if state_name != "ACTIVE":
        raise RuntimeError(
            f"Google AI Studio file {file_name} is in unexpected state {state_name}."
        )
    return current


def _extract_aistudio_config_supported_fields() -> set[str] | None:
    model_fields = getattr(types.GenerateContentConfig, "model_fields", None)
    if isinstance(model_fields, dict):
        return set(model_fields.keys())

    annotations = getattr(types.GenerateContentConfig, "__annotations__", None)
    if isinstance(annotations, dict) and annotations:
        return set(annotations.keys())

    try:
        signature = inspect.signature(types.GenerateContentConfig)
    except (TypeError, ValueError):
        return None

    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return None

    return {
        name
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }


def _extract_aistudio_extra_forbidden_fields(exc: Exception) -> set[str]:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return set()

    unsupported_fields: set[str] = set()
    try:
        for error in errors():
            if error.get("type") != "extra_forbidden":
                continue
            location = error.get("loc") or ()
            if location:
                unsupported_fields.add(str(location[0]))
    except Exception:
        return set()
    return unsupported_fields


def build_aistudio_generate_content_config(
    settings: dict | None = None,
    **config_kwargs,
) -> types.GenerateContentConfig:
    payload = {key: value for key, value in config_kwargs.items() if value is not None}
    if "media_resolution" in payload:
        payload["media_resolution"] = _normalize_aistudio_media_resolution(
            payload.get("media_resolution")
        )
        if payload["media_resolution"] is None:
            payload.pop("media_resolution", None)
    payload.setdefault("safety_settings", build_aistudio_safety_settings(settings))

    supported_fields = _extract_aistudio_config_supported_fields()
    if supported_fields is not None:
        removed_fields = sorted(
            key for key in payload.keys() if key not in supported_fields
        )
        if removed_fields:
            logger.info(
                "Dropping unsupported Google AI Studio config fields: %s",
                ", ".join(removed_fields),
            )
            payload = {
                key: value for key, value in payload.items() if key in supported_fields
            }

    try:
        return types.GenerateContentConfig(**payload)
    except Exception as exc:
        unsupported_fields = _extract_aistudio_extra_forbidden_fields(exc)
        if not unsupported_fields:
            raise

        retry_payload = {
            key: value
            for key, value in payload.items()
            if key not in unsupported_fields
        }
        if retry_payload == payload:
            raise

        logger.info(
            "Retrying Google AI Studio config without unsupported fields: %s",
            ", ".join(sorted(unsupported_fields)),
        )
        try:
            return types.GenerateContentConfig(**retry_payload)
        except Exception:
            logger.exception(
                "Google AI Studio config retry also failed after dropping fields: %s",
                ", ".join(sorted(unsupported_fields)),
            )
            raise


def _coerce_aistudio_native_websearch(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return value is True


def _safe_aistudio_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            dumped = value.dict(exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            key: item for key, item in vars(value).items() if not key.startswith("_")
        }
    return {}


def _safe_aistudio_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _sanitize_aistudio_tool_schema(value: Any):
    if isinstance(value, list):
        return [_sanitize_aistudio_tool_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized = {}
    for key, item in value.items():
        if key in {"additionalProperties", "additional_properties"}:
            continue
        sanitized[key] = _sanitize_aistudio_tool_schema(item)
    return sanitized


def _build_aistudio_google_search_tool():
    search_tool_cls = getattr(types, "ToolGoogleSearch", None)
    if search_tool_cls is None:
        search_tool_cls = getattr(types, "GoogleSearch", None)
    if search_tool_cls is None:
        raise AttributeError(
            "google.genai.types does not expose ToolGoogleSearch or GoogleSearch"
        )
    return search_tool_cls()


def _build_aistudio_tools_payload(
    function_declarations_schema: list[dict] | None,
    *,
    native_websearch_enabled: bool = False,
) -> list[types.Tool]:
    typed_declarations: list[types.FunctionDeclaration] = []
    for schema in function_declarations_schema or []:
        if not isinstance(schema, dict):
            continue
        if native_websearch_enabled and schema.get("name") == "web_search":
            continue
        typed_declarations.append(
            types.FunctionDeclaration(
                name=schema.get("name"),
                description=schema.get("description"),
                parameters=_sanitize_aistudio_tool_schema(schema.get("parameters")),
            )
        )

    tool_kwargs: dict[str, Any] = {}
    if native_websearch_enabled:
        tool_kwargs["google_search"] = _build_aistudio_google_search_tool()
    if typed_declarations:
        tool_kwargs["function_declarations"] = typed_declarations
    if not tool_kwargs:
        return []
    return [types.Tool(**tool_kwargs)]


def _extract_aistudio_query_payload(value: Any) -> Any:
    payload = _safe_aistudio_dict(value)
    if payload:
        for key in ("query", "search_query", "searchQuery"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    for attr in ("query", "search_query", "searchQuery"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _extract_urls_from_payload(value: Any) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url") or node.get("uri")
            title = node.get("title") or node.get("name") or node.get("domain")
            snippet = node.get("snippet") or node.get("text") or node.get("content")
            if isinstance(url, str) and url.strip():
                citation = {
                    "url": url.strip(),
                    "title": str(title).strip() if title not in (None, "") else "",
                    "snippet": str(snippet).strip()
                    if snippet not in (None, "")
                    else "",
                }
                dedupe_key = (citation["url"], citation["title"], citation["snippet"])
                if dedupe_key not in seen:
                    citations.append(
                        {key: item for key, item in citation.items() if item}
                    )
                    seen.add(dedupe_key)
            for item in node.values():
                _walk(item)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return citations


def _extract_google_grounding_citations(*values: Any) -> list[dict[str, str]]:
    for value in values:
        if value is None:
            continue
        payload = _safe_aistudio_dict(value)
        citations = _extract_urls_from_payload(payload) if payload else []
        if citations:
            return citations
        grounding_metadata = _safe_aistudio_dict(
            getattr(value, "grounding_metadata", None)
        )
        if grounding_metadata:
            citations = _extract_urls_from_payload(grounding_metadata)
            if citations:
                return citations
    return []


def _is_google_search_server_tool(tool_payload: Any) -> bool:
    tool_type = getattr(tool_payload, "tool_type", None)
    if tool_type is None and isinstance(tool_payload, dict):
        tool_type = tool_payload.get("tool_type") or tool_payload.get("type")
    normalized = str(tool_type or "").strip().lower()
    return normalized in {"google_search", "search", "google-search"}


def _aistudio_usage_field(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from Google SDK models or dictionary test fixtures."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _aistudio_modality_token_counts(details: Any) -> dict[str, int]:
    """Sum Google token-detail rows into the supported pricing modalities."""
    counts = {"text": 0, "image": 0, "audio": 0, "video": 0}
    for detail in details or []:
        modality = _aistudio_usage_field(detail, "modality")
        modality_value = _aistudio_usage_field(modality, "value")
        if modality_value is None and modality is not None:
            modality_value = str(modality).split(".")[-1]
        modality_key = str(modality_value or "").strip().lower()
        if modality_key in counts:
            counts[modality_key] += coerce_token_count(
                _aistudio_usage_field(detail, "token_count", 0)
            )
    return counts


def _fit_modality_counts(total: int, counts: dict[str, int]) -> dict[str, int]:
    """Fit modality details into a provider total without inventing tokens.

    Google normally provides complete modality details. If an SDK/version omits
    a detail row, the unclassified remainder is treated as text because text is
    the safest default pricing bucket and matches the previous implementation.
    """
    total = coerce_token_count(total)
    fitted = {"text": 0, "image": 0, "audio": 0, "video": 0}
    remaining = total
    for modality in ("text", "image", "audio", "video"):
        fitted[modality] = min(
            coerce_token_count(counts.get(modality, 0)),
            remaining,
        )
        remaining -= fitted[modality]
    fitted["text"] += remaining
    return fitted


def normalize_aistudio_usage_metadata(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies(
        "normalize_aistudio_usage_metadata", globals()
    )
    return _implementation._impl_normalize_aistudio_usage_metadata(*args, **kwargs)


def _get_aistudio_pricing_for_model(model_name: str | None):
    if not model_name:
        return None
    for group_name, schema in AISTUDIO_MODEL_DICT.items():
        identifiers = schema.get("ids") or []
        if model_name == group_name or model_name in identifiers:
            pricing = schema.get("pricing") or {}
            if pricing:
                return pricing
            break
    return None


def _calculate_priced_tokens(
    pricing: dict,
    price_key: str,
    tokens: int,
    *,
    use_high_context_price: bool,
    fallback_price_key: str | None = None,
    fallback_multiplier: float = 1.0,
) -> float:
    """Price one token bucket using the tier selected by total prompt size."""
    tokens = coerce_token_count(tokens)
    if tokens <= 0:
        return 0.0
    suffix = "_200k" if use_high_context_price else ""
    price_per_million = pricing.get(f"{price_key}{suffix}")
    if price_per_million is None:
        price_per_million = pricing.get(price_key)
    if price_per_million is None and fallback_price_key:
        price_per_million = pricing.get(f"{fallback_price_key}{suffix}")
        if price_per_million is None:
            price_per_million = pricing.get(fallback_price_key)
        if price_per_million is not None:
            price_per_million = float(price_per_million) * fallback_multiplier
    if price_per_million is None:
        return 0.0
    return (tokens / 1_000_000) * float(price_per_million)


def calculate_aistudio_token_costs(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies(
        "calculate_aistudio_token_costs", globals()
    )
    return _implementation._impl_calculate_aistudio_token_costs(*args, **kwargs)


def is_aistudio_thinking_enforced(model_name: str) -> bool:
    """Return True when the model enforces thinking (cannot be disabled)."""
    for group_name, schema in AISTUDIO_MODEL_DICT.items():
        identifiers = schema.get("ids") or []
        if model_name == group_name or model_name in identifiers:
            thinking = schema.get("thinking") or {}
            return bool(
                thinking.get("thinking")
                and not thinking.get("thinking_disabled_allowed", True)
            )
    return False


def create_aistudio_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_aistudio_provider", globals())
    return _implementation._impl_create_aistudio_provider(*args, **kwargs)


# -------------------
# Client
# -------------------
def get_aistudio_client(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_aistudio_client", globals())
    return _implementation._impl_get_aistudio_client(*args, **kwargs)


# -------------------
# List completion models
# -------------------
def list_models_google_aistudio(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_google_aistudio", globals())
    return _implementation._impl_list_models_google_aistudio(*args, **kwargs)


# -------------------
# Get Model
# -------------------
def get_aistudio_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_aistudio_model", globals())
    return _implementation._impl_get_aistudio_model(*args, **kwargs)


# -------------------
# Create model
# -------------------
def aistudio_create_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("aistudio_create_model", globals())
    return _implementation._impl_aistudio_create_model(*args, **kwargs)


# -------------------
# Chat
# -------------------
def aistudio_chat(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("aistudio_chat", globals())
    return _implementation._impl_aistudio_chat(*args, **kwargs)


# -------------------
# Title Generation
# -------------------
def google_aistudio_title_generation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation as _implementation

    _implementation._sync_compat_dependencies(
        "google_aistudio_title_generation", globals()
    )
    return _implementation._impl_google_aistudio_title_generation(*args, **kwargs)


# -------------------
# Upload files
# -------------------
def upload_files(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import attachments as _implementation

    _implementation._sync_compat_dependencies("upload_files", globals())
    return _implementation._impl_upload_files(*args, **kwargs)


# -------------------
# Reformat Chat History
# -------------------
def reformat_chat_history(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import messages as _implementation

    _implementation._sync_compat_dependencies("reformat_chat_history", globals())
    return _implementation._impl_reformat_chat_history(*args, **kwargs)
