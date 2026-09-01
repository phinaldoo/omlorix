"""Backward-compatible API for OpenAI-compatible Chat Completions.

Focused sibling modules own chat, generation, attachments, and message
formatting. Imports remain here as intentional compatibility seams.
"""

# ruff: noqa: F401, E402

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
)
import base64
import copy
import json
import mimetypes
import os
import uuid
from app.files.utils import (
    get_file_info,
    extract_text_file,
    extract_text_from_file_info,
)
from app.groups.init import get_user_group_setting_value
from app.chats.streaming import interruptible_provider_stream
from app.users.roles import is_admin_role
from app.llm.openai.utils import (
    _merge_openai_request_options,
    _parse_openai_exception,
    _record_openai_stat_with_costs,
    _resolve_openai_client_context,
    _record_openai_generation_stat,
    _build_native_websearch_user_context,
    _resolve_openai_reasoning_effort,
    _normalize_openai_image_detail,
    _apply_openai_prompt_cache_settings,
    calculate_openai_token_costs,
    merge_openai_cost_breakdown,
)
from app.llm.token_usage import add_cached_input_token_meta
from app.llm.helper import (
    build_tool_call_block,
    extract_tool_call_block,
    format_tool_call_block_label,
    build_file_metadata_text,
    normalize_unsupported_file_ids,
    merge_unsupported_file_ids,
    safe_list_project_files,
)
from app.llm.pdf_utils import (
    render_pdf_pages_to_png_bytes,
    should_convert_pdf_to_images,
)
from app.llm.system_instruction.projects import (
    get_project_context_start,
    get_project_context_end,
)
from app.llm.system_instruction.group import (
    get_group_context_start,
    get_group_context_end,
)
from app.llm.helper import (
    merge_settings,
    format_meta_timestamp,
    should_persist_files_in_file_block,
    build_tool_file_block,
    stringify_tool_result_content_for_persistence,
    build_stream_tool_event_meta,
    build_widget_block_meta,
)
from app.llm.websearch_citations import (
    build_web_search_citations,
    collect_tool_result_citations,
)
from app.llm.system_instruction.chat import (
    append_system_instruction_sections,
    get_default_system_instruction,
)
from app.llm.models import Models
from app.llm.metadata import resolve_model_metadata_id
from app.tools.helper import resolve_parallel_subagent_tool_calls, resolve_tool_call
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)
from app.llm.openai.schemas import (
    OpenAIModelSettings,
    openai_image_mime_types,
    openai_document_mime_types,
    openai_audio_mime_types,
)
import time


def _apply_openai_chat_completion_simple_settings(
    request_kwargs: dict[str, Any],
    settings: dict | None,
    *,
    model_name: str | None = None,
    provider_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Apply saved model settings using the Chat Completions wire contract."""
    if not isinstance(settings, dict):
        return

    optional_params = (
        "frequency_penalty",
        "logit_bias",
        "presence_penalty",
        "temperature",
        "top_p",
        "verbosity",
    )
    for param in optional_params:
        value = settings.get(param)
        if value is not None:
            request_kwargs[param] = value
    # Omlorix uses ``max_output_tokens`` as its provider-neutral saved and
    # per-request key. Chat Completions calls the corresponding wire field
    # ``max_completion_tokens``. Retain a direct max_completion_tokens fallback
    # for older manually-created settings payloads.
    max_completion_tokens = settings.get("max_completion_tokens")
    if max_completion_tokens is None:
        max_completion_tokens = settings.get("max_output_tokens")
    if max_completion_tokens is not None:
        request_kwargs["max_completion_tokens"] = max_completion_tokens
    _apply_openai_chat_completions_reasoning_effort(request_kwargs, settings)
    _apply_openai_prompt_cache_settings(
        request_kwargs,
        settings,
        model_name=model_name or str(request_kwargs.get("model") or "") or None,
        provider_id=provider_id,
        user_id=user_id,
        provider_type="openai_chat_completions",
    )


import logging

logger = logging.getLogger(__name__)


def _apply_openai_chat_completions_reasoning_effort(
    request_kwargs: dict[str, Any],
    settings: dict | None,
) -> None:
    """Apply reasoning_effort to OpenAI Chat Completions requests."""
    reasoning_effort = _resolve_openai_reasoning_effort(settings)
    if reasoning_effort:
        request_kwargs["reasoning_effort"] = reasoning_effort


def _build_openai_chat_image_part(
    image_url: str, image_detail: Any = None
) -> dict[str, Any]:
    """Build a Chat Completions image part with the optional vision detail level."""
    image_url_payload: dict[str, Any] = {"url": image_url}
    normalized_detail = _normalize_openai_image_detail(image_detail)
    if normalized_detail:
        image_url_payload["detail"] = normalized_detail
    return {
        "type": "image_url",
        "image_url": image_url_payload,
    }


def openai_chat_completions_chat(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("openai_chat_completions_chat", globals())
    return _implementation._impl_openai_chat_completions_chat(*args, **kwargs)


def openai_chat_completions_title_generation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation as _implementation

    _implementation._sync_compat_dependencies(
        "openai_chat_completions_title_generation", globals()
    )
    return _implementation._impl_openai_chat_completions_title_generation(
        *args, **kwargs
    )


def upload_files(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import attachments as _implementation

    _implementation._sync_compat_dependencies("upload_files", globals())
    return _implementation._impl_upload_files(*args, **kwargs)


def reformat_chat_history(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import messages as _implementation

    _implementation._sync_compat_dependencies("reformat_chat_history", globals())
    return _implementation._impl_reformat_chat_history(*args, **kwargs)
