"""Backward-compatible public API for the OpenAI integration.

Focused sibling modules own chat, model, usage, attachment, message, and
generation behavior. Imports remain here as intentional compatibility seams.
"""

# ruff: noqa: F401, E402

from openai import OpenAI, AuthenticationError, BadRequestError, APIConnectionError
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from copy import deepcopy
from typing import Any, Optional, Generator
from urllib.parse import urlparse
import mimetypes
import logging
import base64
import hashlib
import hmac
import time
import copy
import json
import os


from app.files.utils import (
    get_file_info,
    extract_text_file,
    extract_text_from_file_info,
)
from app.groups.init import get_user_group_setting_value
from app.users.init import get_user_setting_value
from app.users.roles import is_admin_role
from app.llm.capabilities import determine_model_capabilities
from app.llm.base_settings import LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS
from app.llm.helper import (
    build_tool_call_block,
    extract_tool_call_block,
    merge_settings,
    should_persist_files_in_file_block,
    build_tool_file_block,
    stringify_tool_result_content_for_model,
    stringify_tool_result_content_for_persistence,
    build_stream_tool_event_meta,
    build_widget_block_meta,
    build_file_metadata_text,
    normalize_unsupported_file_ids,
    merge_unsupported_file_ids,
    safe_list_project_files,
)
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.pdf_utils import (
    render_pdf_pages_to_png_bytes,
    should_convert_pdf_to_images,
)
from app.llm.openai.catalog import (
    get_responses_model_capabilities,
    get_responses_unsupported_models,
)
from app.llm.openai.provider_types import (
    XAI_PROVIDER_TYPE,
    allows_manual_openai_model_entry,
    is_azure_openai_provider_type,
    is_lmstudio_provider_type,
    is_openai_chat_completions_provider_type,
    is_openai_custom_base_url_provider_type,
    is_openai_responses_provider_type,
    normalize_openai_provider_type,
)
from app.llm.lmstudio.utils import (
    get_lmstudio_openai_base_url,
    normalize_lmstudio_responses_reasoning_effort,
)
from app.llm.metadata import resolve_model_metadata_id
from app.llm.openai.schemas import (
    OpenAIModelSettings,
    openai_image_mime_types,
    openai_document_mime_types,
    openai_audio_mime_types,
)
from app.llm.models import LLMProvider, Models, create_llm_provider, get_llm_provider
from app.llm.schemas import ProviderEnum, provider_api_key_is_optional
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
from app.logging.privacy import (
    exception_metadata,
    object_event_metadata,
    redacted_debug_logging_enabled,
)
from app.auth.jwt_material import get_jwt_material
from app.chats.streaming import interruptible_provider_stream
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.llm.helper import format_meta_timestamp
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)
from app.llm.token_usage import (
    add_cached_input_token_meta,
    coerce_token_count,
    read_cached_input_tokens,
    read_cache_write_tokens,
)
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION

logger = logging.getLogger(__name__)

_OPENAI_STREAM_DEBUG_FLAG = "OMLORIX_LOG_REDACTED_OPENAI_STREAMS"
_OPENAI_REASONING_SUMMARY_STREAM_EVENT_TYPES = {
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done",
    "response.reasoning_summary_part.delta",
    "response.reasoning_summary_part.done",
}
_OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES = {
    "response.reasoning_summary_text.done",
    "response.reasoning_summary_part.done",
}
_OPENAI_IMAGE_DETAIL_LEVELS = {"auto", "low", "high", "original"}
_OPENAI_CONTINUATION_SIGNATURE_VERSION = 1


class _OpenAIToolCallBudget:
    """Admit at most the configured number of tool calls for one generation.

    The budget is shared by every Responses API round in ``openai_chat``. A
    provider may return many parallel calls in one completed response, so the
    admission decision must happen for the whole batch before any call reaches
    the execution loop.
    """

    def __init__(self, limit: int = MAX_TOOL_CALLS_PER_GENERATION) -> None:
        if not isinstance(limit, int) or limit < 0:
            raise ValueError("Tool call limit must be a non-negative integer.")
        self.limit = limit
        self.remaining = limit

    @property
    def exhausted(self) -> bool:
        """Return whether no additional tool call may be executed."""
        return self.remaining <= 0

    def admit(
        self,
        calls: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split a provider batch into admitted and rejected calls.

        Rejected calls are returned to the caller so it can terminate the
        generation explicitly instead of silently dropping provider-requested
        work or executing past the configured security boundary.
        """
        normalized_calls = list(calls or [])
        admitted_count = min(len(normalized_calls), max(self.remaining, 0))
        admitted = normalized_calls[:admitted_count]
        rejected = normalized_calls[admitted_count:]
        self.remaining -= admitted_count
        return admitted, rejected


class _OpenAIReasoningTimer:
    """Track wall-clock time for each streamed reasoning segment.

    A response can contain several reasoning segments separated by local tool
    calls. Keeping the timer in a small state object makes every segment start
    and finish independently instead of relying on the previous persisted
    message type to retain timing state across Responses API requests.
    """

    def __init__(self) -> None:
        self._started_at: datetime | None = None
        self.last_duration = 0.0
        self.total_duration = 0.0

    @property
    def is_running(self) -> bool:
        """Return whether a reasoning segment currently has an active timer."""
        return self._started_at is not None

    def start(self, started_at: datetime | None = None) -> None:
        """Start a reasoning segment unless it is already being timed."""
        if self._started_at is None:
            self._started_at = started_at or datetime.now(timezone.utc)

    def finish(self, finished_at: datetime | None = None) -> float:
        """Finish the current segment and add its duration to the total."""
        if self._started_at is None:
            return 0.0

        end_time = finished_at or datetime.now(timezone.utc)
        elapsed = max((end_time - self._started_at).total_seconds(), 0.0)
        self._started_at = None
        self.last_duration = elapsed
        self.total_duration += elapsed
        return elapsed

    def finish_metadata(
        self,
        meta: dict[str, Any] | None = None,
        *,
        finished_at: datetime | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Return block metadata containing this segment's elapsed time."""
        meta_payload = dict(meta or {})
        elapsed = self.finish(finished_at)
        if elapsed > 0:
            meta_payload["reasoning_time"] = elapsed
        return meta_payload, elapsed


class _OpenAIFunctionCallAccumulator:
    """Collect Responses API function calls without mixing parallel calls.

    Streaming argument deltas are keyed by item ID (with output index as a
    fallback). The finalized event or completed response output is authoritative
    because OpenAI-compatible providers are allowed to return the complete JSON
    there even when they omit incremental argument events.
    """

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, Any]] = {}
        self._key_by_output_index: dict[int, str] = {}

    def _resolve_key(
        self,
        *,
        item_id: Any = None,
        output_index: Any = None,
        create: bool,
    ) -> str | None:
        """Resolve one stable accumulator key from an event's identifiers."""
        normalized_item_id = str(item_id).strip() if item_id not in (None, "") else ""
        normalized_output_index = (
            output_index if isinstance(output_index, int) else None
        )

        if normalized_item_id and normalized_item_id in self._calls:
            return normalized_item_id
        if (
            normalized_output_index is not None
            and normalized_output_index in self._key_by_output_index
        ):
            return self._key_by_output_index[normalized_output_index]
        if not create:
            return None

        if normalized_item_id:
            key = normalized_item_id
        elif normalized_output_index is not None:
            key = f"output:{normalized_output_index}"
        else:
            key = f"unkeyed:{len(self._calls)}"

        self._calls.setdefault(
            key,
            {
                "id": normalized_item_id or None,
                "call_id": None,
                "name": None,
                "namespace": None,
                "arguments": "",
                "output_index": normalized_output_index,
                "finalized": False,
                "emitted": False,
            },
        )
        if normalized_output_index is not None:
            self._key_by_output_index[normalized_output_index] = key
        return key

    def register_item(
        self,
        item: Any,
        *,
        output_index: Any = None,
        finalized: bool = False,
    ) -> dict[str, Any] | None:
        """Register metadata from an added, done, or completed output item."""
        if getattr(item, "type", None) != "function_call":
            return None

        item_id = getattr(item, "id", None)
        key = self._resolve_key(item_id=item_id, output_index=output_index, create=True)
        if key is None:
            return None
        state = self._calls[key]

        if item_id not in (None, ""):
            state["id"] = str(item_id)
        for state_key, attribute_name in (
            ("call_id", "call_id"),
            ("name", "name"),
            ("namespace", "namespace"),
        ):
            value = getattr(item, attribute_name, None)
            if value not in (None, ""):
                state[state_key] = value

        # Added items normally carry an empty string. A done/completed item can
        # carry the only complete argument payload supplied by a compatible API.
        item_arguments = getattr(item, "arguments", None)
        if isinstance(item_arguments, str) and (
            item_arguments or not state["arguments"]
        ):
            state["arguments"] = item_arguments
        if finalized:
            state["finalized"] = True
        return state

    def register_output_event(
        self, event: Any, *, finalized: bool = False
    ) -> dict[str, Any] | None:
        """Register the function-call item contained in an output-item event."""
        return self.register_item(
            getattr(event, "item", None),
            output_index=getattr(event, "output_index", None),
            finalized=finalized,
        )

    def append_delta(self, event: Any) -> dict[str, Any] | None:
        """Append one argument delta to the call identified by the event."""
        item_id = getattr(event, "item_id", None)
        output_index = getattr(event, "output_index", None)
        key = self._resolve_key(item_id=item_id, output_index=output_index, create=True)
        if key is None:
            return None
        state = self._calls[key]
        if item_id not in (None, "") and not state.get("id"):
            state["id"] = str(item_id)
        delta = getattr(event, "delta", None)
        if isinstance(delta, str):
            state["arguments"] += delta
        return state

    def finalize_arguments(self, event: Any) -> dict[str, Any] | None:
        """Finalize a call from the authoritative arguments-done event."""
        event_item = getattr(event, "item", None)
        if getattr(event_item, "type", None) == "function_call":
            state = self.register_item(
                event_item,
                output_index=getattr(event, "output_index", None),
            )
        else:
            item_id = getattr(event, "item_id", None)
            output_index = getattr(event, "output_index", None)
            key = self._resolve_key(
                item_id=item_id, output_index=output_index, create=True
            )
            state = self._calls[key] if key is not None else None

        if state is None:
            return None

        item_id = getattr(event, "item_id", None)
        if item_id not in (None, ""):
            state["id"] = str(item_id)
        event_name = getattr(event, "name", None)
        if event_name not in (None, ""):
            state["name"] = event_name

        final_arguments = getattr(event, "arguments", None)
        if final_arguments is None and event_item is not None:
            final_arguments = getattr(event_item, "arguments", None)
        if isinstance(final_arguments, str) and (
            final_arguments or not state["arguments"]
        ):
            state["arguments"] = final_arguments

        state["finalized"] = True
        # Tool execution waits for response.completed, so keep this state open
        # for a later output_item.done/completed response to fill compatibility
        # gaps before drain_finalized emits the call exactly once.
        return self._public_call(state)

    def drain_finalized(self) -> list[dict[str, Any]]:
        """Return finalized calls not already emitted by an arguments-done event."""
        calls: list[dict[str, Any]] = []
        for state in self._calls.values():
            if not state["finalized"]:
                continue
            call = self._emit_once(state)
            if call is not None:
                calls.append(call)
        return calls

    @staticmethod
    def _public_call(state: dict[str, Any]) -> dict[str, Any]:
        """Build the canonical call dictionary consumed by tool execution."""
        return {
            "id": state.get("id"),
            "call_id": state.get("call_id"),
            "name": state.get("name"),
            "namespace": state.get("namespace"),
            "arguments": state.get("arguments") or "",
        }

    def _emit_once(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Mark a completed state emitted so fallback sources cannot duplicate it."""
        if state["emitted"]:
            return None
        state["emitted"] = True
        return self._public_call(state)


def _normalize_openai_image_detail(value: Any) -> str | None:
    """Return a valid OpenAI image detail level, or None to omit the parameter."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _OPENAI_IMAGE_DETAIL_LEVELS else None


def _build_openai_responses_image_part(
    image_url: str, image_detail: Any = None
) -> dict[str, Any]:
    """Build an OpenAI Responses image part with the optional vision detail level."""
    part: dict[str, Any] = {
        "type": "input_image",
        "image_url": image_url,
    }
    normalized_detail = _normalize_openai_image_detail(image_detail)
    if normalized_detail:
        part["detail"] = normalized_detail
    return part


def _extract_openai_reasoning_summary_text(event: Any) -> str | None:
    event_type = getattr(event, "type", None)
    if event_type not in _OPENAI_REASONING_SUMMARY_STREAM_EVENT_TYPES:
        return None

    delta = getattr(event, "delta", None)
    if isinstance(delta, str) and delta:
        return delta

    text = getattr(event, "text", None)
    if isinstance(text, str) and text:
        return text

    part = getattr(event, "part", None)
    part_text = getattr(part, "text", None) if part is not None else None
    if isinstance(part_text, str) and part_text:
        return part_text

    return None


def _get_openai_model_caps(
    model_name: str | None,
    *,
    provider_type: str | None = None,
) -> dict[str, Any] | None:
    """Get model capabilities from the effective provider's catalog."""

    return get_responses_model_capabilities(model_name, provider_type)


def _openai_models_share_catalog_entry(
    first: str | None,
    second: str | None,
    *,
    provider_type: str | None = None,
) -> bool:
    """Return whether two model identifiers resolve to the same catalog entry."""
    if not first or not second:
        return False
    if first == second:
        return True
    first_caps = _get_openai_model_caps(first, provider_type=provider_type)
    second_caps = _get_openai_model_caps(second, provider_type=provider_type)
    return bool(first_caps is not None and first_caps is second_caps)


def _semantic_chat_history_payload(chat_history: Any) -> list[dict[str, Any]]:
    """Build a stable, non-secret-bearing payload for continuation validation.

    Response IDs are safe to reuse only while the visible conversation branch is
    unchanged. The fingerprint intentionally ignores volatile usage/timing meta,
    but retains roles, content blocks, attachments, and tool identifiers.
    """
    semantic_messages: list[dict[str, Any]] = []
    for raw_message in list(chat_history or []):
        if isinstance(raw_message, dict):
            message = raw_message
        elif hasattr(raw_message, "model_dump"):
            try:
                message = raw_message.model_dump()
            except Exception:
                message = {}
        else:
            message = {
                key: getattr(raw_message, key, None)
                for key in (
                    "role",
                    "content",
                    "images",
                    "videos",
                    "audios",
                    "documents",
                    "youtube",
                )
            }

        semantic_message: dict[str, Any] = {
            "role": str(message.get("role") or ""),
            "content": _strip_openai_continuation_meta(message.get("content")),
        }
        for key in ("images", "videos", "audios", "documents", "youtube"):
            if message.get(key):
                semantic_message[key] = _strip_openai_continuation_meta(
                    message.get(key)
                )
        semantic_messages.append(semantic_message)
    return semantic_messages


def _strip_openai_continuation_meta(value: Any) -> Any:
    """Remove volatile metadata before hashing a persisted chat branch."""
    if isinstance(value, list):
        return [_strip_openai_continuation_meta(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key == "meta":
            # Only tool identity affects the semantic transcript. Usage,
            # timestamps, response IDs, and encrypted provider state do not.
            if isinstance(item, dict):
                stable_meta = {
                    meta_key: item.get(meta_key)
                    for meta_key in (
                        "tool_name",
                        "tool_call_id",
                        "tool_namespace",
                        "arguments",
                        "native_web_search",
                        "tool_search_call",
                        "tool_search_output",
                    )
                    if item.get(meta_key) not in (None, "", [], {})
                }
                if stable_meta:
                    cleaned[key] = stable_meta
            continue
        cleaned[key] = _strip_openai_continuation_meta(item)
    return cleaned


def _openai_chat_history_fingerprint(chat_history: Any) -> str:
    """Return a deterministic fingerprint for a visible chat branch."""
    payload = _semantic_chat_history_payload(chat_history)
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _openai_continuation_signature(
    *,
    signing_secret: str,
    user_id: str | None,
    chat_id: str | None,
    response_id: str | None,
    provider_id: str | None,
    model_name: str | None,
    fingerprint: str | None,
) -> str:
    """Sign one stored-response capability for its owning user and chat.

    The signature is persisted with the visible assistant metadata, so it must
    authenticate every field that controls continuation. Binding the capability
    to both the user and chat prevents copied or imported metadata from granting
    access to provider-side response state owned by another conversation.
    """

    secret = str(signing_secret or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_chat_id = str(chat_id or "").strip()
    normalized_response_id = str(response_id or "").strip()
    normalized_provider_id = str(provider_id or "").strip()
    normalized_model_name = str(model_name or "").strip()
    normalized_fingerprint = str(fingerprint or "").strip()
    if not all(
        (
            secret,
            normalized_user_id,
            normalized_chat_id,
            normalized_response_id,
            normalized_model_name,
            normalized_fingerprint,
        )
    ):
        return ""

    payload = json.dumps(
        {
            "v": _OPENAI_CONTINUATION_SIGNATURE_VERSION,
            "user_id": normalized_user_id,
            "chat_id": normalized_chat_id,
            "response_id": normalized_response_id,
            "provider_id": normalized_provider_id,
            "model": normalized_model_name,
            "fingerprint": normalized_fingerprint,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _message_dict_for_openai_continuation(raw_message: Any) -> dict[str, Any]:
    """Normalize a chat-history entry for continuation inspection."""
    if isinstance(raw_message, dict):
        return raw_message
    if hasattr(raw_message, "model_dump"):
        try:
            return raw_message.model_dump()
        except Exception:
            return {}
    return {
        "role": getattr(raw_message, "role", None),
        "content": getattr(raw_message, "content", None),
    }


def _decode_openai_message_blocks(raw_content: Any) -> list[dict[str, Any]]:
    """Decode persisted Omlorix blocks without mutating their stored form."""
    if isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except Exception:
            return []
    if isinstance(raw_content, dict):
        raw_content = [raw_content]
    if not isinstance(raw_content, list):
        return []
    return [block for block in raw_content if isinstance(block, dict)]


def _sanitize_openai_reasoning_item(value: Any) -> dict[str, Any] | None:
    """Return a minimal replay-safe encrypted OpenAI reasoning item."""
    if not isinstance(value, dict) or value.get("type") != "reasoning":
        return None
    encrypted_content = value.get("encrypted_content")
    if not isinstance(encrypted_content, str) or not encrypted_content:
        return None
    sanitized: dict[str, Any] = {
        "type": "reasoning",
        "encrypted_content": encrypted_content,
    }
    if isinstance(value.get("id"), str) and value.get("id"):
        sanitized["id"] = value["id"]
    for key in ("summary", "content"):
        if isinstance(value.get(key), list):
            sanitized[key] = copy.deepcopy(value[key])
    return sanitized


def _find_openai_previous_response(
    chat_history: Any,
    *,
    model_name: str | None,
    provider_id: str | None,
    user_id: str | None,
    chat_id: str | None,
    signing_secret: str | None,
    provider_type: str | None = None,
) -> tuple[str | None, int | None]:
    """Find an owned, branch-safe stored response for GPT-5.6 continuation."""
    if not str(signing_secret or "").strip():
        # Missing signing material must disable the optimization rather than
        # trusting client-controlled continuation metadata.
        return None, None

    messages = list(chat_history or [])
    for index in range(len(messages) - 1, -1, -1):
        message = _message_dict_for_openai_continuation(messages[index])
        if str(message.get("role") or "").lower() != "assistant":
            continue

        for block in reversed(_decode_openai_message_blocks(message.get("content"))):
            meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
            response_id = str(meta.get("response_id") or "").strip()
            if not response_id or meta.get("store") is not True:
                continue
            if str(meta.get("selected_provider_id") or "") != str(provider_id or ""):
                continue
            if not _openai_models_share_catalog_entry(
                str(meta.get("model") or ""),
                model_name,
                provider_type=provider_type,
            ):
                continue

            expected_fingerprint = str(meta.get("continuation_fingerprint") or "")
            if not expected_fingerprint:
                continue
            if (
                _openai_chat_history_fingerprint(messages[: index + 1])
                != expected_fingerprint
            ):
                continue

            expected_signature = _openai_continuation_signature(
                signing_secret=str(signing_secret),
                user_id=user_id,
                chat_id=chat_id,
                response_id=response_id,
                provider_id=meta.get("selected_provider_id"),
                model_name=meta.get("model"),
                fingerprint=expected_fingerprint,
            )
            supplied_signature = str(meta.get("continuation_signature") or "")
            try:
                supplied_signature_bytes = supplied_signature.encode("ascii")
                expected_signature_bytes = expected_signature.encode("ascii")
            except UnicodeEncodeError:
                # Hex-encoded HMAC signatures are ASCII-only. Treat malformed
                # metadata as untrusted instead of passing non-ASCII strings to
                # compare_digest, which raises TypeError for those values.
                continue
            if not expected_signature or not hmac.compare_digest(
                supplied_signature_bytes,
                expected_signature_bytes,
            ):
                continue
            return response_id, index
    return None, None


def _supports_openai_tool_search(
    model_name: str | None,
    *,
    provider_type: str | None,
) -> bool:
    """Check if the model supports OpenAI tool search."""
    if is_openai_chat_completions_provider_type(provider_type):
        return False
    if not is_openai_responses_provider_type(provider_type):
        return False
    caps = _get_openai_model_caps(model_name, provider_type=provider_type)
    return bool(caps and caps.get("supports_tool_search"))


def _is_openai_tool_search_enabled(
    settings: dict[str, Any] | None,
    *,
    model_name: str | None,
    provider_type: str | None,
) -> bool:
    """Check if OpenAI tool search is enabled."""
    if not _supports_openai_tool_search(model_name, provider_type=provider_type):
        return False
    if not isinstance(settings, dict):
        return False
    raw_value = settings.get("tool_search")
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"true", "1", "yes", "on"}
    return raw_value is True


def _apply_openai_tool_search_to_schema(schema: Any) -> tuple[Any, bool]:
    """Apply OpenAI tool search to schema."""
    if not isinstance(schema, dict):
        return schema, False

    schema_type = str(schema.get("type") or "").strip().lower()
    if schema_type == "function":
        updated = copy.deepcopy(schema)
        updated["defer_loading"] = True
        return updated, True

    if schema_type == "mcp":
        updated = copy.deepcopy(schema)
        updated["defer_loading"] = True
        return updated, True

    if schema_type == "namespace":
        namespace_tools = schema.get("tools")
        if not isinstance(namespace_tools, list):
            return schema, False
        updated = copy.deepcopy(schema)
        has_deferred_tool = False
        updated_tools: list[Any] = []
        for tool in namespace_tools:
            updated_tool, tool_is_deferred = _apply_openai_tool_search_to_schema(tool)
            updated_tools.append(updated_tool)
            has_deferred_tool = has_deferred_tool or tool_is_deferred
        updated["tools"] = updated_tools
        return updated, has_deferred_tool

    return schema, False


def _prepare_openai_tool_schemas_for_tool_search(
    tool_schemas: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Prepare OpenAI tool schemas for tool search."""
    prepared: list[dict[str, Any]] = []
    has_deferred_tool = False

    for schema in tool_schemas or []:
        updated_schema, schema_is_deferred = _apply_openai_tool_search_to_schema(schema)
        prepared.append(updated_schema)
        has_deferred_tool = has_deferred_tool or schema_is_deferred

    has_tool_search = any(
        isinstance(schema, dict)
        and str(schema.get("type") or "").strip().lower() == "tool_search"
        for schema in prepared
    )
    if has_deferred_tool and not has_tool_search:
        prepared.append({"type": "tool_search"})

    return prepared, has_deferred_tool


def _serialize_openai_tool_search_arguments(item: Any) -> tuple[dict[str, Any], str]:
    """Serialize OpenAI tool search arguments."""
    try:
        arguments_payload = jsonable_encoder(getattr(item, "arguments", None) or {})
    except Exception:
        arguments_payload = {}
    if not isinstance(arguments_payload, dict):
        arguments_payload = {}
    try:
        arguments_text = json.dumps(
            arguments_payload, ensure_ascii=False, separators=(",", ":")
        )
    except TypeError:
        arguments_text = str(arguments_payload or {})
    return arguments_payload, arguments_text or "{}"


def _serialize_openai_tool_search_tools(item: Any) -> tuple[list[Any], str]:
    """Serialize OpenAI tool search tools."""
    try:
        tools_payload = jsonable_encoder(getattr(item, "tools", None) or [])
    except Exception:
        tools_payload = []
    if not isinstance(tools_payload, list):
        tools_payload = []
    try:
        tools_text = json.dumps(tools_payload, ensure_ascii=False)
    except TypeError:
        tools_text = str(tools_payload)
    return tools_payload, tools_text or "[]"


def _normalize_openai_text_setting(value: Any) -> str | None:
    """Normalize OpenAI text setting."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _resolve_openai_store_setting(settings: dict | None) -> bool | None:
    """Return a valid optional Responses API ``store`` setting.

    ``None`` means that Omlorix should omit the field and let the endpoint apply
    its own default. This distinction matters because the OpenAI SDK serializes
    an explicitly supplied Python ``None`` as JSON ``null``, while the API
    defines ``store`` as a boolean.

    Persisted model settings normally contain real booleans. The small amount
    of coercion below keeps older imports and request-level overrides safe
    without forwarding strings or numbers to the provider.
    """
    if not isinstance(settings, dict):
        return None

    value = settings.get("store")
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return None


def _apply_openai_store_setting(
    request_kwargs: dict[str, Any],
    settings: dict | None,
    *,
    provider_type: str | None,
) -> bool | None:
    """Apply a configured boolean ``store`` value to a Responses request.

    Unset and invalid values are intentionally omitted instead of becoming
    JSON ``null``. The setting is scoped to Responses-based providers; Chat
    Completions uses a different storage contract. LM Studio is also excluded
    because its compatible endpoint does not expose OpenAI's hosted control.

    The returned value is the normalized requested setting, not the effective
    response value. OpenAI may still force storage off for Zero Data Retention
    organizations, so callers must continue to trust the response metadata.
    """
    store_setting = _resolve_openai_store_setting(settings)
    supports_openai_store = is_openai_responses_provider_type(
        provider_type
    ) and not is_lmstudio_provider_type(provider_type)
    if supports_openai_store and store_setting is not None:
        request_kwargs["store"] = store_setting
    return store_setting


def _normalize_openai_reasoning_effort(value: Any) -> str | None:
    """Normalize OpenAI reasoning effort."""
    return _normalize_openai_text_setting(value)


def _resolve_openai_reasoning_effort(settings: dict | None) -> str | None:
    """Resolve reasoning effort from merged OpenAI settings."""
    if not isinstance(settings, dict):
        return None
    return _normalize_openai_reasoning_effort(settings.get("reasoning_effort"))


def _normalize_openai_reasoning_summary(value: Any) -> str | None:
    """Normalize OpenAI reasoning summary."""
    return _normalize_openai_text_setting(value)


def _requests_openai_encrypted_reasoning(settings: dict | None) -> bool:
    """Return whether a request must round-trip encrypted reasoning state.

    OpenAI can force ``store=false`` for Zero Data Retention organizations even
    when Omlorix requested stored responses. Requesting the encrypted item for
    every all-turn request keeps that server-side policy transparent to the
    conversation and is harmless when the effective response remains stored.
    """
    return bool(
        isinstance(settings, dict) and settings.get("reasoning_context") == "all_turns"
    )


def _should_persist_openai_encrypted_reasoning(
    settings: dict | None,
    effective_store: Any,
) -> bool:
    """Return whether encrypted reasoning must be retained client-side.

    The response's effective ``store`` value is authoritative because ZDR can
    override the requested value. If the provider omits that value, retaining
    the already-encrypted item is the safe fallback for all-turn continuity.
    """
    return (
        _requests_openai_encrypted_reasoning(settings) and effective_store is not True
    )


def _build_openai_reasoning_payload(
    settings: dict | None,
    *,
    model_name: str | None = None,
    provider_type: str | None = None,
) -> dict[str, Any] | None:
    """Build an endpoint- and model-aware OpenAI reasoning payload."""
    if not isinstance(settings, dict):
        return None

    reasoning_enabled = settings.get("reasoning")
    reasoning_effort = _resolve_openai_reasoning_effort(settings)
    if is_lmstudio_provider_type(provider_type):
        # LM Studio's native model list can advertise "on"/"off", but its
        # OpenAI-compatible Responses endpoint accepts only OpenAI effort enum
        # values. Normalize legacy saved settings before serializing the body.
        reasoning_effort = normalize_lmstudio_responses_reasoning_effort(
            reasoning_effort
        )
        if reasoning_enabled is False and reasoning_effort is not None:
            # A stale effort selection must not override the explicit toggle.
            reasoning_effort = "none"
    reasoning_summary = _normalize_openai_reasoning_summary(
        settings.get("reasoning_summary")
    )

    reasoning_payload: dict[str, Any] = {}
    if reasoning_effort:
        reasoning_payload["effort"] = reasoning_effort
    if reasoning_summary and str(reasoning_effort or "").lower() != "none":
        reasoning_payload["summary"] = reasoning_summary

    caps = _get_openai_model_caps(model_name, provider_type=provider_type)
    is_responses_api = is_openai_responses_provider_type(provider_type)
    if caps and is_responses_api and caps.get("supports_reasoning_mode"):
        reasoning_mode = (
            _normalize_openai_text_setting(settings.get("reasoning_mode")) or "standard"
        )
        if reasoning_mode in {"standard", "pro"}:
            reasoning_payload["mode"] = reasoning_mode
    if caps and is_responses_api and caps.get("reasoning_context"):
        reasoning_context = (
            _normalize_openai_text_setting(settings.get("reasoning_context")) or "auto"
        )
        if reasoning_context in {"auto", "current_turn", "all_turns"}:
            reasoning_payload["context"] = reasoning_context

    if reasoning_enabled is False and not reasoning_effort:
        has_nondefault_gpt56_control = reasoning_payload.get(
            "mode"
        ) == "pro" or reasoning_payload.get("context") not in (None, "auto")
        if not has_nondefault_gpt56_control:
            return None

    if reasoning_payload or reasoning_enabled is True:
        return reasoning_payload

    return None


def _merge_openai_extra_body(
    request_kwargs: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Merge new API fields into extra_body for older typed SDK releases."""
    existing = request_kwargs.get("extra_body")
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    request_kwargs["extra_body"] = merged


def _generated_openai_prompt_cache_key(
    *,
    provider_id: str | None,
    model_name: str | None,
    user_id: str | None,
) -> str:
    """Generate a stable routing key without exposing Omlorix identifiers."""
    raw_scope = "\x1f".join(
        (
            str(provider_id or "openai"),
            str(model_name or "unknown"),
            str(user_id or "anonymous"),
        )
    )
    digest = hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:32]
    return f"omlorix:{digest}"


def _apply_openai_prompt_cache_settings(
    request_kwargs: dict[str, Any],
    settings: dict | None,
    *,
    model_name: str | None,
    provider_id: str | None,
    user_id: str | None,
    provider_type: str | None = None,
) -> None:
    """Apply provider-supported prompt-cache routing and retention controls."""
    if not isinstance(settings, dict):
        return

    # Native providers retain the pre-toggle behavior for saved models. Generic
    # compatible endpoints must opt in because they may reject OpenAI-specific
    # prompt-cache extensions.
    cache_override = settings.get("prompt_cache_override")
    if cache_override is False or (
        cache_override is None
        and is_openai_custom_base_url_provider_type(provider_type)
    ):
        return

    caps = _get_openai_model_caps(model_name, provider_type=provider_type)
    prompt_cache_caps = caps.get("prompt_caching") if caps else None
    if not isinstance(prompt_cache_caps, dict):
        return

    configured_key = _normalize_openai_text_setting(settings.get("prompt_cache_key"))
    request_kwargs["prompt_cache_key"] = (
        configured_key
        or _generated_openai_prompt_cache_key(
            provider_id=provider_id,
            model_name=model_name,
            user_id=user_id,
        )
    )

    supported_ttls = set(prompt_cache_caps.get("ttl") or [])
    if supported_ttls:
        ttl = _normalize_openai_text_setting(settings.get("prompt_cache_ttl")) or "30m"
        if ttl not in supported_ttls:
            ttl = "30m"

        # The project's pinned SDK predates prompt_cache_options. extra_body
        # keeps the request wire-compatible without sending this OpenAI-only
        # control to providers such as xAI that support routing keys but no TTL.
        _merge_openai_extra_body(
            request_kwargs,
            {"prompt_cache_options": {"mode": "implicit", "ttl": ttl}},
        )


def _normalize_azure_openai_base_url(endpoint: str | None) -> str | None:
    """Normalize Azure OpenAI base URL."""
    if not isinstance(endpoint, str):
        return None
    normalized = endpoint.strip().rstrip("/")
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None
    if normalized.endswith("/openai/v1"):
        return f"{normalized}/"
    if normalized.endswith("/openai"):
        return f"{normalized}/v1/"
    return f"{normalized}/openai/v1/"


def _resolve_openai_default_headers(
    value: Any,
    *,
    provider_type: str | None = None,
) -> dict[str, str]:
    """Resolve OpenAI default headers."""
    normalized_provider_type = normalize_openai_provider_type(provider_type)
    max_headers = (
        10 if is_azure_openai_provider_type(normalized_provider_type) else None
    )
    try:
        return custom_headers_to_dict(value, max_headers=max_headers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _merge_openai_request_options(
    request_kwargs: dict | None = None, request_options: dict | None = None
) -> dict:
    """Merge OpenAI request options."""
    merged = dict(request_kwargs or {})
    for key, value in (request_options or {}).items():
        if not value:
            continue
        if key == "extra_query" and isinstance(value, dict):
            existing = merged.get("extra_query")
            if isinstance(existing, dict):
                merged["extra_query"] = {**value, **existing}
            else:
                merged["extra_query"] = dict(value)
            continue
        merged.setdefault(key, value)
    return merged


def _coerce_optional_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_openai_simple_generation_settings(
    request_kwargs: dict[str, Any],
    settings: dict | None,
    *,
    user_id: str | None = None,
    openai_provider_type: str | None = None,
) -> None:
    """Apply saved model settings to non-chat Responses API requests."""
    if not isinstance(settings, dict):
        return

    provider_is_lmstudio = is_lmstudio_provider_type(openai_provider_type)
    model_name = str(request_kwargs.get("model") or "") or None
    model_caps = _get_openai_model_caps(
        model_name,
        provider_type=openai_provider_type,
    )
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        capability = model_caps.get(key) if model_caps else None
        if isinstance(capability, dict) and not capability.get(key):
            continue
        value = _coerce_optional_float(settings.get(key))
        if value is not None:
            request_kwargs[key] = value

    max_output_tokens = _coerce_optional_int(settings.get("max_output_tokens"))
    if max_output_tokens is not None and max_output_tokens > 0:
        request_kwargs["max_output_tokens"] = max_output_tokens

    # Title generation uses the Responses API too. Honor an explicit storage
    # preference on those calls,
    # while leaving an unset value out of the serialized request entirely.
    _apply_openai_store_setting(
        request_kwargs,
        settings,
        provider_type=openai_provider_type,
    )

    reasoning_payload = _build_openai_reasoning_payload(
        settings,
        model_name=model_name,
        provider_type=openai_provider_type,
    )
    if reasoning_payload is not None:
        request_kwargs["reasoning"] = reasoning_payload

    verbosity_value = settings.get("verbosity")
    if isinstance(verbosity_value, str):
        verbosity_value = verbosity_value.strip()
    if not provider_is_lmstudio and verbosity_value not in (None, ""):
        request_kwargs["text"] = {"verbosity": verbosity_value}

    priority_tier = settings.get("priority_processing") or "standard"
    if not provider_is_lmstudio and priority_tier in {"flex", "priority"}:
        request_kwargs["service_tier"] = priority_tier
    if (
        not provider_is_lmstudio
        and normalize_openai_provider_type(openai_provider_type) != XAI_PROVIDER_TYPE
        and settings.get("send_user_identifier")
        and user_id
    ):
        request_kwargs["safety_identifier"] = str(user_id)
    if not provider_is_lmstudio:
        _apply_openai_prompt_cache_settings(
            request_kwargs,
            settings,
            model_name=model_name,
            provider_id=None,
            user_id=user_id,
            provider_type=openai_provider_type,
        )


def _parse_openai_exception(
    exc: Exception,
) -> tuple[Optional[int], str, Optional[str], Optional[str]]:
    """Normalize errors from OpenAI and OpenAI-compatible providers.

    The official OpenAI API nests error details under an ``error`` mapping,
    while some compatible providers return the message directly in a string
    ``error`` field and keep metadata at the top level.  Handle both formats so
    parsing an upstream failure never replaces it with a secondary exception.
    """
    resp = getattr(exc, "response", None)
    payload: dict[str, Any] = {}
    if resp and hasattr(resp, "json"):
        try:
            decoded_payload = resp.json()
            # Error responses should be JSON objects, but do not let an
            # unexpected JSON primitive break the error-reporting path.
            payload = decoded_payload if isinstance(decoded_payload, dict) else {}
        except Exception:
            payload = {}

    status = (
        payload.get("status")
        or getattr(resp, "status_code", None)
        or getattr(exc, "status_code", None)
    )
    raw_error = payload.get("error")

    if isinstance(raw_error, dict):
        # Standard OpenAI error payload: {"error": {"message": ..., ...}}.
        message = raw_error.get("message") or payload.get("message") or str(exc)
        error_type = raw_error.get("type") or payload.get("type")
        error_code = raw_error.get("code") or payload.get("code")
    else:
        # Compatible providers such as xAI may return
        # {"code": "invalid-argument", "error": "Readable message"}.
        message = raw_error or payload.get("message") or str(exc)
        error_type = payload.get("type")
        error_code = payload.get("code")

    # Keep the return contract stable even if a provider uses non-string JSON
    # primitives for its error fields.
    message = message if isinstance(message, str) else str(message)
    error_type = (
        error_type
        if isinstance(error_type, str) or error_type is None
        else str(error_type)
    )
    error_code = (
        error_code
        if isinstance(error_code, str) or error_code is None
        else str(error_code)
    )
    return status, message, error_type, error_code


def _should_retry_without_compaction(exc: Exception) -> bool:
    """Return True when the error suggests context_management/compaction is unsupported."""
    status, message, error_type, error_code = _parse_openai_exception(exc)
    if status not in (None, 400, 404, 422):
        return False
    candidates = [
        str(message or ""),
        str(error_type or ""),
        str(error_code or ""),
    ]
    text = " ".join(candidates).lower()
    return any(
        token in text
        for token in ("context_management", "compaction", "compact_threshold")
    )


def _build_native_websearch_user_context(db, user_id: str | None) -> dict:
    """Return user_location and language metadata when permitted by user settings."""

    if not db or not user_id:
        return {}

    try:
        llm_permissions = get_user_setting_value(
            user_id,
            "security",
            "allow_llm_to_access_personal_information",
            db,
        )
    except Exception:
        llm_permissions = None

    def _allows(field: str) -> bool:
        if isinstance(llm_permissions, dict):
            return bool(llm_permissions.get(field))
        return bool(llm_permissions)

    def _get_general_setting(key: str) -> str | None:
        try:
            value = get_user_setting_value(user_id, "general", key, db)
        except Exception:
            return None
        if isinstance(value, str):
            value = value.strip()
        return value or None

    context: dict[str, Any] = {}
    location_payload: dict[str, Any] = {"type": "approximate"}

    if _allows("country"):
        country_code = _get_general_setting("country")
        if country_code:
            location_payload["country"] = country_code.upper()

    if _allows("location"):
        city_value = _get_general_setting("location")
        if city_value:
            location_payload["city"] = city_value

    if _allows("timezone"):
        timezone_value = _get_general_setting("timezone")
        if timezone_value:
            location_payload["timezone"] = timezone_value

    if _allows("region"):
        region_value = _get_general_setting("region")
        if region_value:
            location_payload["region"] = region_value

    return context


def _resolve_effective_provider_id(
    db: Session | None,
    provider_id: str | None,
) -> tuple[str | None, str | None]:
    """Resolve provider groups to a concrete provider while preserving the original identifier."""

    if not provider_id or db is None:
        return provider_id, provider_id

    from app.llm.provider_groups import resolve_provider_for_request

    provider = resolve_provider_for_request(db, provider_id)
    return provider.id, provider_id


def _record_openai_generation_stat(
    db: Session | None,
    *,
    model_name: str,
    model_id: str | None,
    provider: str,
    provider_id: str | None,
    category: str,
    meta: dict,
    success: bool,
    error: bool,
    error_status_code: int | None = None,
    error_message: str | None = None,
    error_type: str | None = None,
    cost_kwargs: dict | None = None,
    user_id: str | None = None,
    is_byok: bool = False,
) -> None:
    """Shared helper to enrich meta with costs and persist LLM generation statistics."""
    if not db:
        return

    meta_payload = dict(meta)
    if cost_kwargs:
        costs = calculate_openai_token_costs(**cost_kwargs)
        if costs:
            meta_payload.setdefault(
                "input_tokens_cost", costs.get("input_tokens_cost", 0)
            )
            meta_payload.setdefault(
                "cached_input_tokens_cost",
                costs.get("cached_input_tokens_cost", 0),
            )
            meta_payload.setdefault(
                "cache_write_tokens_cost", costs.get("cache_write_tokens_cost", 0)
            )
            meta_payload.setdefault(
                "output_tokens_cost", costs.get("output_tokens_cost", 0)
            )
            meta_payload.setdefault(
                "native_websearch_costs", costs.get("native_websearch_costs", 0)
            )
            meta_payload.setdefault("total_costs", costs.get("total_costs", 0))

    provider_identifier = provider_id or "openai"
    model_identifier = model_id or model_name
    create_llm_generation_statistic(
        db,
        model_name=model_name,
        model_id=model_identifier,
        provider=provider,
        provider_id=provider_identifier,
        success=success,
        error=error,
        error_status_code=error_status_code,
        error_message=error_message,
        error_type=error_type,
        category=category,
        meta={k: v for k, v in meta_payload.items() if v not in (None, "", [], {})},
        user_id=user_id,
        is_byok=is_byok,
    )


def _provider_reported_cost_from_usage(
    usage: Any,
    *,
    provider_type: str | None,
) -> tuple[float, dict[str, Any]]:
    """Return an authoritative provider cost when the API supplies one.

    xAI reports the exact amount billed for every inference request.  Importing
    its converter lazily keeps the shared Responses adapter independent for
    OpenAI and other compatible provider types.
    """

    if normalize_openai_provider_type(provider_type) != XAI_PROVIDER_TYPE:
        return 0.0, {}
    from app.llm.xai.common import xai_cost_from_usage_object

    return xai_cost_from_usage_object(usage)


def _apply_provider_reported_cost_meta(
    meta: dict[str, Any],
    usage: Any,
    *,
    provider_type: str | None,
) -> bool:
    """Attach exact billed cost metadata and report whether it was present."""

    cost, details = _provider_reported_cost_from_usage(
        usage,
        provider_type=provider_type,
    )
    if not details:
        return False
    meta["total_costs"] = cost
    meta.update(details)
    return True


def _record_openai_stat_with_costs(
    db: Session | None,
    *,
    category: str,
    model_name: str | None,
    provider: str,
    provider_id: str | None,
    meta: dict,
    success: bool,
    error: bool,
    error_status_code: int,
    error_message: str | None = None,
    error_type: str | None = None,
    service_tier: str | None = "standard",
    native_websearch_tool_calls_count: int = 0,
    user_id: str | None = None,
    is_byok: bool = False,
    **cost_kwargs,
) -> None:
    """Helper to hydrate cost kwargs and persist stats for simple generations."""
    resolved_model = model_name or "openai"
    resolved_provider = provider_id or "openai"
    cached_tokens = read_cached_input_tokens(meta)
    cache_write_tokens = read_cache_write_tokens(meta)
    cost_kwargs = {
        "model_name": resolved_model,
        "provider_type": provider,
        "service_tier": service_tier or "standard",
        "input_tokens": meta.get("input_tokens", 0),
        "cached_input_tokens": cached_tokens or 0,
        "cache_write_tokens": cache_write_tokens or 0,
        "output_tokens": meta.get("output_tokens", 0),
        "reasoning_tokens": meta.get("reasoning_tokens", 0),
        "native_websearch_tool_calls_count": native_websearch_tool_calls_count,
    }
    _record_openai_generation_stat(
        db,
        model_name=resolved_model,
        model_id=resolved_model,
        provider=provider,
        provider_id=resolved_provider,
        category=category,
        meta=meta,
        success=success,
        error=error,
        error_status_code=error_status_code,
        error_message=error_message,
        error_type=error_type,
        cost_kwargs=cost_kwargs,
        user_id=user_id,
        is_byok=is_byok,
    )


# -------------------
# Calculate OpenAI Tokens Costs
# -------------------
def calculate_openai_token_costs(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies("calculate_openai_token_costs", globals())
    return _implementation._impl_calculate_openai_token_costs(*args, **kwargs)


_OPENAI_COST_KEYS = (
    "input_tokens_cost",
    "cached_input_tokens_cost",
    "cache_write_tokens_cost",
    "output_tokens_cost",
    "native_websearch_costs",
    "total_costs",
)


def merge_openai_cost_breakdown(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies("merge_openai_cost_breakdown", globals())
    return _implementation._impl_merge_openai_cost_breakdown(*args, **kwargs)


# -------------------
# Create OpenAI Provider
# -------------------
def create_openai_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_openai_provider", globals())
    return _implementation._impl_create_openai_provider(*args, **kwargs)


def _resolve_openai_sdk_api_key(value: Any, *, provider_type: str | None) -> str:
    """Return a non-empty SDK api_key for providers that allow omitted credentials."""
    token = str(value or "").strip()
    if token or not is_lmstudio_provider_type(provider_type):
        return token
    return "lmstudio"


# -------------------
# Resolve OpenAI Client Kwargs
# -------------------
def _resolve_openai_client_context(
    db: Session | None,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str = "openai",
) -> dict[str, Any]:
    """Resolve OpenAI client context."""
    provider_type = normalize_openai_provider_type(openai_provider_type)
    effective_provider_id = openai_provider_id
    if openai_provider_id and db is not None:
        effective_provider_id, _ = _resolve_effective_provider_id(
            db, openai_provider_id
        )

    def _build_context(creds: dict) -> dict[str, Any]:
        kwargs = {
            "api_key": _resolve_openai_sdk_api_key(
                creds.get("api_key"), provider_type=provider_type
            ),
            # This application-level safety boundary is deliberately not
            # configurable by administrator or BYOK provider settings.
            "timeout": LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS,
        }
        request_options: dict[str, Any] = {}
        default_headers = _resolve_openai_default_headers(
            creds.get("custom_headers"),
            provider_type=provider_type,
        )
        if default_headers:
            kwargs["default_headers"] = default_headers

        if is_azure_openai_provider_type(provider_type):
            azure_base_url = _normalize_azure_openai_base_url(
                creds.get("azure_endpoint") or creds.get("base_url")
            )
            if not azure_base_url:
                raise HTTPException(
                    status_code=422, detail="Azure endpoint is required"
                )
            kwargs["base_url"] = azure_base_url
            api_version = str(creds.get("api_version") or "").strip()
            if api_version:
                request_options["extra_query"] = {"api-version": api_version}
            return {"client_kwargs": kwargs, "request_options": request_options}

        if is_lmstudio_provider_type(provider_type):
            kwargs["base_url"] = get_lmstudio_openai_base_url(creds.get("base_url"))
            return {"client_kwargs": kwargs, "request_options": request_options}

        for key in ("base_url", "organization", "project"):
            value = creds.get(key)
            if value:
                kwargs[key] = value
        return {"client_kwargs": kwargs, "request_options": request_options}

    if effective_provider_id:
        if db is None:
            raise HTTPException(
                status_code=500,
                detail="Database session required to resolve provider credentials",
            )
        provider = (
            db.query(LLMProvider)
            .filter(
                LLMProvider.id == effective_provider_id,
                LLMProvider.provider == provider_type,
            )
            .first()
        )
        if not provider:
            raise HTTPException(status_code=404, detail="LLM provider not found")
        if not provider.api_key and not provider_api_key_is_optional(provider.provider):
            raise HTTPException(
                status_code=422, detail="Provider api_key not configured"
            )
        settings = dict(provider.settings)
        credentials = {
            "api_key": provider.api_key or "",
            "base_url": settings.get("base_url"),
            "azure_endpoint": settings.get("azure_endpoint"),
            "api_version": settings.get("api_version"),
            "organization": settings.get("organization"),
            "project": settings.get("project"),
            "custom_headers": settings.get("custom_headers"),
        }
        context = _build_context(credentials)
        context["requested_provider_id"] = openai_provider_id
        context["selected_provider_id"] = effective_provider_id
        context["selected_provider_name"] = (
            provider.name or provider.provider or effective_provider_id
        )
        return context

    if byok:
        raw_api_key = byok.get("api_key")
        api_key = str(raw_api_key or "").strip() if isinstance(raw_api_key, str) else ""
        if not api_key and not provider_api_key_is_optional(provider_type):
            raise HTTPException(status_code=422, detail="BYOK API Key is required")
        credentials = {
            "api_key": api_key,
            "base_url": byok.get("base_url"),
            "azure_endpoint": byok.get("azure_endpoint"),
            "api_version": byok.get("api_version"),
            "organization": byok.get("organization"),
            "project": byok.get("project"),
            "custom_headers": byok.get("custom_headers"),
        }
        context = _build_context(credentials)
        context["requested_provider_id"] = byok.get("provider_id")
        context["selected_provider_id"] = byok.get("provider_id")
        context["selected_provider_name"] = byok.get("provider_name") or byok.get(
            "provider_label"
        )
        return context

    raise HTTPException(
        status_code=422,
        detail="Either openai_provider_id or BYOK credentials must be supplied",
    )


def _resolve_openai_client_kwargs(
    db: Session | None,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str = "openai",
) -> dict:
    """Resolve OpenAI client kwargs."""
    return _resolve_openai_client_context(
        db, openai_provider_id, byok, openai_provider_type
    )["client_kwargs"]


def _resolve_openai_request_options(
    db: Session | None,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str = "openai",
) -> dict:
    """Resolve OpenAI request options."""
    return _resolve_openai_client_context(
        db, openai_provider_id, byok, openai_provider_type
    )["request_options"]


# -------------------
# List Models Completion
# -------------------
def list_models_openai(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_openai", globals())
    return _implementation._impl_list_models_openai(*args, **kwargs)


# -------------------
# Create OpenAI Model
# -------------------
def openai_create_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("openai_create_model", globals())
    return _implementation._impl_openai_create_model(*args, **kwargs)


# -------------------
# OpenAI Chat
# -------------------
def openai_chat(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("openai_chat", globals())
    return _implementation._impl_openai_chat(*args, **kwargs)


# -------------------
# Title Generation
# -------------------
def openai_title_generation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation as _implementation

    _implementation._sync_compat_dependencies("openai_title_generation", globals())
    return _implementation._impl_openai_title_generation(*args, **kwargs)


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
