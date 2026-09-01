"""Backward-compatible public API for the Ollama integration.

Focused sibling modules own chat, model lifecycle, messages, and generation.
Imports remain here as intentional compatibility and monkeypatch seams.
"""

# ruff: noqa: F401, E402

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException
from ollama import Client, ProcessResponse
from ollama._types import ResponseError
from httpx import RemoteProtocolError
from datetime import datetime, timezone
from typing import Set, Any
import threading
import requests
import logging
import base64
import json
import time
import httpx
import uuid

from app.database import SessionLocal, AuditSessionLocal
from app.files.schemas import TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
from app.files.utils import (
    extract_text_file,
    extract_text_from_file_info,
    get_file_info,
    normalize_file_mime_type,
)
from app.groups.init import get_user_group_setting_value
from app.chats.streaming import interruptible_provider_stream
from app.users.roles import is_admin_role
from app.llm.ollama.schemas import OllamaModelSettings
from app.llm.ollama.model_list import (
    OLLAMA_REASONING_EFFORT_VALUES,
    ollama_model_supports_reasoning_effort,
)
from app.llm.models import create_llm_provider, LLMProvider, Models
from app.llm.system_instruction.chat import (
    append_system_instruction_sections,
    get_default_system_instruction,
)
from app.llm.system_instruction.projects import (
    get_project_context_start,
    get_project_context_end,
)
from app.llm.system_instruction.group import (
    get_group_context_start,
    get_group_context_end,
)
from app.tools.helper import resolve_tool_call
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.llm.helper import (
    build_tool_call_block,
    extract_tool_call_block,
    format_tool_call_block_label,
    format_meta_timestamp,
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
from app.llm.websearch_citations import (
    build_web_search_citations,
    collect_tool_result_citations,
)
from app.llm.pdf_utils import (
    render_pdf_pages_to_png_bytes,
    should_convert_pdf_to_images,
)
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)


logger = logging.getLogger(__name__)

_OLLAMA_EFFECTIVE_INPUT_FORMATS = frozenset(
    {"text", "image", "pdf", "text_document"}
)


def _resolve_ollama_tool_call_id(provider_tool_call_id: Any) -> str:
    """Preserve an Ollama call ID or create one fallback for this tool call."""
    return provider_tool_call_id or f"call_{uuid.uuid4().hex}"


def _normalize_ollama_input_formats(input_formats_allowed: Any) -> list[str] | None:
    """Normalize an Ollama input format setting while preserving an unspecified value."""
    if input_formats_allowed is None:
        return None
    if isinstance(input_formats_allowed, dict):
        iterable = input_formats_allowed.values()
    elif isinstance(input_formats_allowed, (list, tuple, set)):
        iterable = input_formats_allowed
    else:
        iterable = [input_formats_allowed]

    formats: list[str] = []
    for item in iterable:
        value = getattr(item, "value", item)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if (
            normalized in _OLLAMA_EFFECTIVE_INPUT_FORMATS
            and normalized not in formats
        ):
            formats.append(normalized)
    return formats


def _ollama_capabilities_include_vision(capabilities: Any) -> bool:
    """Return whether Ollama model capabilities advertise vision support."""
    if isinstance(capabilities, dict):
        return bool(capabilities.get("vision"))
    if isinstance(capabilities, (list, tuple, set)):
        return "vision" in {str(item).strip().lower() for item in capabilities}
    return False


def _resolve_ollama_input_formats(
    input_formats_allowed: Any, capabilities: Any
) -> list[str]:
    """Use configured input formats, or safely infer them from Ollama capabilities."""
    normalized = _normalize_ollama_input_formats(input_formats_allowed)
    if normalized is not None:
        return normalized

    inferred = ["text"]
    if _ollama_capabilities_include_vision(capabilities):
        inferred.append("image")
    inferred.extend(["pdf", "text_document"])
    return inferred


def _ollama_images_allowed(input_formats_allowed: Any) -> bool:
    """Return whether image payloads may be sent to Ollama for this request."""
    normalized = _normalize_ollama_input_formats(input_formats_allowed)
    return normalized is None or "image" in normalized


_OLLAMA_SIMPLE_OPTION_KEYS = {
    "num_keep",
    "seed",
    "num_predict",
    "top_k",
    "top_p",
    "min_p",
    "typical_p",
    "repeat_last_n",
    "temperature",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "penalize_newline",
    "stop",
}


def _merge_ollama_simple_settings(
    model_settings: dict | None, settings_override: dict | None
) -> dict:
    settings, _ = merge_settings(
        model_settings,
        settings_override,
        getattr(OllamaModelSettings, "model_fields", None),
    )
    return settings


def _ollama_options_from_settings(settings: dict | None) -> dict:
    if not isinstance(settings, dict):
        return {}
    return {
        key: value
        for key, value in settings.items()
        if key in _OLLAMA_SIMPLE_OPTION_KEYS and value is not None
    }


def _capabilities_include_thinking(capabilities: Any) -> bool:
    """Check if capabilities include thinking."""
    return (isinstance(capabilities, list) and ("thinking" in capabilities)) or (
        isinstance(capabilities, dict) and bool(capabilities.get("thinking"))
    )


def _resolve_ollama_reasoning_enabled(settings: dict | None) -> bool | None:
    """Resolve Ollama reasoning enabled setting."""
    if not isinstance(settings, dict):
        return None
    reasoning = settings.get("reasoning")
    if reasoning is None:
        reasoning = settings.get("thinking_enabled")
    if reasoning is None:
        return None
    return bool(reasoning)


def _resolve_ollama_reasoning_effort(settings: dict | None) -> str | None:
    """Resolve Ollama reasoning effort setting."""
    if not isinstance(settings, dict):
        return None
    effort = settings.get("reasoning_effort")
    if effort is None:
        return None
    normalized = str(effort).strip().lower()
    if not normalized:
        return None
    return normalized if normalized in OLLAMA_REASONING_EFFORT_VALUES else None


def _resolve_ollama_think_value(
    model_name: str | None, capabilities: Any, settings: dict | None
):
    """Resolve Ollama think value."""
    supports_reasoning_effort = ollama_model_supports_reasoning_effort(model_name)
    if not supports_reasoning_effort and not _capabilities_include_thinking(
        capabilities
    ):
        return None

    reasoning_enabled = _resolve_ollama_reasoning_enabled(settings)
    if supports_reasoning_effort:
        if reasoning_enabled is False:
            return None
        reasoning_effort = _resolve_ollama_reasoning_effort(settings)
        if reasoning_effort:
            return reasoning_effort
        return "medium"

    if reasoning_enabled is None:
        return True
    return reasoning_enabled


def _resolve_ollama_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_resolve_ollama_provider", globals())
    return _implementation._impl__resolve_ollama_provider(*args, **kwargs)


def _extract_provider_base_url(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_extract_provider_base_url", globals())
    return _implementation._impl__extract_provider_base_url(*args, **kwargs)


# -------------------
# Create ollama provider
# -------------------
def create_ollama_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_ollama_provider", globals())
    return _implementation._impl_create_ollama_provider(*args, **kwargs)


# -------------------
# Get Base Url
# -------------------
def get_ollama_provider_url(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_ollama_provider_url", globals())
    return _implementation._impl_get_ollama_provider_url(*args, **kwargs)


# -------------------
# Get Client
# -------------------
def get_ollama_client(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_ollama_client", globals())
    return _implementation._impl_get_ollama_client(*args, **kwargs)


# -------------------
# Get Model Capabilities
# -------------------
def get_model_capabilities(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_model_capabilities", globals())
    return _implementation._impl_get_model_capabilities(*args, **kwargs)


# -------------------
# Chat
# -------------------
def ollama_chat(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("ollama_chat", globals())
    return _implementation._impl_ollama_chat(*args, **kwargs)


# -------------------
# List models Completion
# -------------------
def list_models_ollama(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_ollama", globals())
    return _implementation._impl_list_models_ollama(*args, **kwargs)


# -------------------
# List models All
# -------------------
def list_models_all(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_all", globals())
    return _implementation._impl_list_models_all(*args, **kwargs)


# -------------------
# List running models
# -------------------
def list_models_loaded(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_loaded", globals())
    return _implementation._impl_list_models_loaded(*args, **kwargs)


# -------------------
# Create model
# -------------------
def ollama_create_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("ollama_create_model", globals())
    return _implementation._impl_ollama_create_model(*args, **kwargs)


# -------------------
# Get model info
# -------------------
def get_model_info(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_model_info", globals())
    return _implementation._impl_get_model_info(*args, **kwargs)


# -------------------
# Progress To Jsonable
# -------------------
def _progress_to_jsonable(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_progress_to_jsonable", globals())
    return _implementation._impl__progress_to_jsonable(*args, **kwargs)


# -------------------
# Download model
# -------------------
def download_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("download_model", globals())
    return _implementation._impl_download_model(*args, **kwargs)


# -------------------
# Delete model
# -------------------
def delete_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("delete_model", globals())
    return _implementation._impl_delete_model(*args, **kwargs)


# -------------------
# Load model
# -------------------
def load_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("load_model", globals())
    return _implementation._impl_load_model(*args, **kwargs)


# -------------------
# Unload model
# -------------------
def unload_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("unload_model", globals())
    return _implementation._impl_unload_model(*args, **kwargs)


# -------------------
# Check Ollama version
# -------------------
def check_ollama_version(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("check_ollama_version", globals())
    return _implementation._impl_check_ollama_version(*args, **kwargs)


# -------------------
# Title Generation
# -------------------
def ollama_title_generation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation as _implementation

    _implementation._sync_compat_dependencies("ollama_title_generation", globals())
    return _implementation._impl_ollama_title_generation(*args, **kwargs)


# -------------------
# Reformat Chat History
# -------------------
def reformat_chat_history(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import messages as _implementation

    _implementation._sync_compat_dependencies("reformat_chat_history", globals())
    return _implementation._impl_reformat_chat_history(*args, **kwargs)
