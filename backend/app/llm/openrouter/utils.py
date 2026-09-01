"""Backward-compatible public API for the OpenRouter integration.

Focused sibling modules own chat, models, usage, messages, and generation.
Imports remain here as intentional compatibility and monkeypatch seams.
"""

# ruff: noqa: F401, E402

from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Set, Any
import threading
import requests
import logging
import base64
import json
import time
import copy
from requests.exceptions import HTTPError
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
from app.llm.helper import (
    build_tool_call_block,
    build_file_metadata_text,
    build_stream_tool_event_meta,
    format_tool_call_block_label,
    extract_tool_call_block,
    merge_settings,
    normalize_unsupported_file_ids,
    safe_list_project_files,
)
from app.llm.models import LLMProvider, Models, create_llm_provider, get_llm_provider
from app.llm.schemas import ProviderEnum
from app.llm.capabilities import determine_model_capabilities
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
from app.llm.openrouter.schemas import (
    OpenrouterModelSettings,
    openrouter_image_mime_types,
    openrouter_audio_mime_types,
    openrouter_document_mime_types,
    openrouter_video_mime_types,
    OPENROUTER_NAME_EXTENSIONS,
)
from app.llm.pdf_utils import (
    render_pdf_pages_to_png_bytes,
    should_convert_pdf_to_images,
)
from app.tools.helper import resolve_parallel_subagent_tool_calls, resolve_tool_call
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.llm.helper import (
    format_meta_timestamp,
    should_persist_files_in_file_block,
    build_tool_file_block,
    stringify_tool_result_content_for_persistence,
    build_widget_block_meta,
)
from app.llm.websearch_citations import (
    build_web_search_citations,
    collect_tool_result_citations,
)
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)
from app.llm.openrouter.common import (
    build_openrouter_api_url,
    build_openrouter_headers,
    get_openrouter_api_base_url,
    get_openrouter_base_url,
    get_openrouter_attribution_headers,
    resolve_openrouter_attribution,
)
from app.llm.openrouter.responses import (
    OpenRouterFunctionCallAccumulator,
    apply_openrouter_responses_settings,
    extract_openrouter_incomplete_reason,
    extract_openrouter_response_error,
    extract_openrouter_response_usage,
    openrouter_response_error_http_status,
)
from app.llm.token_usage import add_cached_input_token_meta


logger = logging.getLogger(__name__)


def _merge_openrouter_simple_settings(
    model_settings: dict | None,
    settings_override: dict | None,
) -> dict:
    settings, _ = merge_settings(
        model_settings,
        settings_override,
        getattr(OpenrouterModelSettings, "model_fields", None),
    )
    return settings


def _apply_openrouter_simple_settings(payload: dict, settings: dict | None) -> None:
    """Apply OpenRouter settings using the Responses API wire contract."""
    apply_openrouter_responses_settings(payload, settings)


# -------------------
# Create OpenRouter Provider
# -------------------
def create_open_router_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_open_router_provider", globals())
    return _implementation._impl_create_open_router_provider(*args, **kwargs)


def _format_model_payload(item: dict) -> dict:
    identifier = item.get("id") or ""
    provider, model = (
        (identifier.split("/", 1) + [None])[:2]
        if "/" in identifier
        else (None, identifier or None)
    )
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "created": item.get("created"),
        "description": item.get("description"),
        "architecture": item.get("architecture", {}),
        "top_provider": item.get("top_provider", {}),
        "pricing": item.get("pricing", {}),
        "canonical_slug": item.get("canonical_slug"),
        "context_length": item.get("context_length"),
        "hugging_face_id": item.get("hugging_face_id"),
        "per_request_limits": item.get("per_request_limits", {}),
        "supported_parameters": item.get("supported_parameters", []),
        "default_parameters": item.get("default_parameters", {}),
        "knowledge_cutoff": item.get("knowledge_cutoff"),
        "expiration_date": item.get("expiration_date"),
        "provider": provider or item.get("provider"),
        "model": model,
        "endpoints": item.get("endpoints", []),
    }


def _get_catalog_model_parts(
    model_entry: dict, requested_model: str
) -> tuple[str, str]:
    """Return the canonical OpenRouter author and slug for a catalog entry.

    Older Omlorix model rows can contain only the short ``model`` value exposed by
    ``_format_model_payload``.  The OpenRouter metadata endpoint, however, always
    needs the full ``author/slug`` path.  Prefer the catalog's canonical ID so
    those rows remain editable without guessing the author from the short name.
    """
    catalog_id = model_entry.get("id")
    if isinstance(catalog_id, str) and "/" in catalog_id:
        author, slug = catalog_id.split("/", 1)
        if author and slug:
            return author, slug

    # Keep a defensive fallback for compatible OpenRouter-style APIs that expose
    # provider/model fields but omit the canonical ID from their model listing.
    provider = model_entry.get("provider")
    catalog_model = model_entry.get("model")
    if (
        isinstance(provider, str)
        and provider
        and isinstance(catalog_model, str)
        and catalog_model
    ):
        return provider, catalog_model

    if "/" in requested_model:
        author, slug = requested_model.split("/", 1)
        if author and slug:
            return author, slug

    raise ValueError(
        f"Invalid model_name format: '{requested_model}'. Expected a catalog model with an 'author/slug' ID"
    )


def _find_catalog_model_entry(catalog: list[dict], requested_model: str) -> dict:
    """Resolve one catalog row without guessing between duplicate legacy slugs."""
    canonical_matches = [item for item in catalog if item.get("id") == requested_model]
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        raise ValueError(
            f"OpenRouter catalog contains multiple rows for canonical model ID '{requested_model}'"
        )

    slug_matches = [item for item in catalog if item.get("model") == requested_model]
    if len(slug_matches) == 1:
        return slug_matches[0]
    if not slug_matches:
        raise ValueError(
            f"OpenRouter model '{requested_model}' was not found in the catalog. "
            "Expected a canonical 'author/slug' ID or a unique legacy slug"
        )

    canonical_ids = sorted(
        {
            str(item.get("id") or "").strip()
            for item in slug_matches
            if str(item.get("id") or "").strip()
        }
    )
    match_description = ", ".join(canonical_ids) or f"{len(slug_matches)} catalog rows"
    raise ValueError(
        f"Ambiguous legacy OpenRouter model slug '{requested_model}'; matched {match_description}. "
        "Use a canonical 'author/slug' ID"
    )


def _as_float(value: int | float | str | None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _as_int(value: int | float | str | None) -> int:
    return int(_as_float(value))


def normalize_openrouter_usage(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies("normalize_openrouter_usage", globals())
    return _implementation._impl_normalize_openrouter_usage(*args, **kwargs)


_OPENROUTER_ADDITIVE_USAGE_FIELDS = {
    "input_tokens",
    "input_token_cached",
    "cache_write_tokens",
    "input_token_image",
    "input_token_audio",
    "input_token_video",
    "output_tokens",
    "output_image_tokens",
    "output_audio_tokens",
    "output_video_tokens",
    "reasoning_tokens",
    "total_tokens",
    "total_costs",
    "upstream_inference_cost",
    "input_tokens_cost",
    "output_tokens_cost",
}


def merge_openrouter_usage(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import usage as _implementation

    _implementation._sync_compat_dependencies("merge_openrouter_usage", globals())
    return _implementation._impl_merge_openrouter_usage(*args, **kwargs)


def _price_per_million_tokens(value: int | float | str | None) -> float | None:
    if value is None:
        return None
    return _as_float(value) * 1_000_000


def _sum_pricing_values(pricing: dict | int | float | str | None) -> float:
    if isinstance(pricing, dict):
        total = 0.0
        for value in pricing.values():
            total += _sum_pricing_values(value)
        return total
    return _as_float(pricing)


def _is_zero_priced_model(model: dict) -> bool:
    pricing = model.get("pricing") if isinstance(model, dict) else None
    total = _sum_pricing_values(pricing or {})
    return abs(total) < 1e-12


def _openrouter_extract_model_slug(entry: dict | None) -> str | None:
    if not isinstance(entry, dict):
        return None
    model_value = entry.get("model")
    if isinstance(model_value, str) and model_value.strip():
        return model_value.strip()
    identifier = entry.get("id")
    if isinstance(identifier, str) and identifier.strip():
        raw = identifier.strip()
        if "/" in raw:
            return raw.split("/", 1)[1]
        return raw
    return None


def _has_configured_tools(tools: Any) -> bool:
    if isinstance(tools, dict):
        return any(bool(value) for value in tools.values())
    if isinstance(tools, (list, tuple, set)):
        for item in tools:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict) and any(bool(v) for v in item.values()):
                return True
            if item:
                return True
        return False
    return bool(tools)


def _openrouter_transform_content_part_for_responses(part: Any) -> Any:
    if not isinstance(part, dict):
        return part

    part_type = part.get("type")
    if part_type == "text":
        return {"type": "input_text", "text": part.get("text", "")}

    if part_type == "image_url":
        image_url = part.get("image_url")
        if isinstance(image_url, dict):
            return {
                "type": "input_image",
                "image_url": image_url.get("url"),
                "detail": part.get("detail") or image_url.get("detail") or "auto",
            }
        if isinstance(image_url, str):
            return {
                "type": "input_image",
                "image_url": image_url,
                "detail": part.get("detail") or "auto",
            }

    if part_type == "file":
        file_payload = part.get("file")
        if isinstance(file_payload, dict):
            transformed = {"type": "input_file"}
            if file_payload.get("filename"):
                transformed["filename"] = file_payload.get("filename")
            if file_payload.get("file_data"):
                transformed["file_data"] = file_payload.get("file_data")
            if file_payload.get("file_id"):
                transformed["file_id"] = file_payload.get("file_id")
            return transformed

    # Chat Completions represents video_url as an object. OpenRouter's
    # Responses schema expects the URL itself while retaining input_video.
    if part_type == "input_video":
        video_url = part.get("video_url")
        if isinstance(video_url, dict):
            return {"type": "input_video", "video_url": video_url.get("url")}
        if isinstance(video_url, str):
            return {"type": "input_video", "video_url": video_url}

    # OpenRouter uses the same input_audio shape on both APIs.
    if part_type == "input_audio":
        return copy.deepcopy(part)

    return part


def _openrouter_transform_assistant_content_for_responses(part: Any) -> Any:
    """Convert persisted assistant text to the Responses output-message shape.

    Responses history is asymmetric: user messages contain ``input_*`` parts,
    while replayed assistant messages must contain ``output_text`` parts.  The
    persisted Omlorix history uses the Chat Completions ``text`` shape, so it
    needs a dedicated conversion instead of the user-input helper above.
    """
    if not isinstance(part, dict):
        return part

    part_type = str(part.get("type") or "").strip()
    if part_type in {"text", "input_text", "output_text"}:
        return {
            "type": "output_text",
            "text": str(part.get("text") or part.get("content") or ""),
        }

    # Preserve non-text parts using the existing canonical input conversion.
    # This retains multimodal history for models/routes that support it while
    # ensuring the common persisted text shape is always Responses-valid.
    return _openrouter_transform_content_part_for_responses(part)


def _append_openrouter_response_reasoning_items(
    target: list[dict[str, Any]],
    reasoning_items: Any,
) -> bool:
    """Append exact provider-issued Responses reasoning items to ``target``.

    Chat Completions ``reasoning_details`` and Responses ``reasoning`` output
    items are different wire formats. Only the latter can be replayed in a
    stateless Responses request, and its opaque fields must remain unchanged.
    """
    if not isinstance(reasoning_items, list):
        return False

    appended = False
    for reasoning_item in reasoning_items:
        if (
            isinstance(reasoning_item, dict)
            and reasoning_item.get("type") == "reasoning"
        ):
            target.append(copy.deepcopy(reasoning_item))
            appended = True
    return appended


def _openrouter_convert_history_to_responses_input(
    history: list[dict] | None,
) -> list[dict]:
    if not isinstance(history, list):
        return []

    converted: list[dict] = []
    assistant_message_index = 0

    def build_message_payload(
        item: dict,
        role: str,
        content_value: Any,
    ) -> dict:
        """Build a valid stateless Responses message from Omlorix history."""
        nonlocal assistant_message_index

        message_payload: dict[str, Any] = {"type": "message", "role": role}
        if role == "assistant":
            # OpenRouter requires every replayed assistant message to have an
            # ID and completed status. Omlorix does not persist provider output
            # message IDs for ordinary text turns, so generate a deterministic
            # request-local ID when the source history has none.
            source_id = item.get("id") or item.get("message_id")
            message_payload["id"] = str(
                source_id or f"omlorix_assistant_{assistant_message_index}"
            )
            message_payload["status"] = "completed"
            assistant_message_index += 1
            if isinstance(content_value, list):
                message_payload["content"] = [
                    _openrouter_transform_assistant_content_for_responses(part)
                    for part in content_value
                ]
            else:
                message_payload["content"] = [
                    {"type": "output_text", "text": str(content_value or "")}
                ]
            return message_payload

        if isinstance(content_value, list):
            message_payload["content"] = [
                _openrouter_transform_content_part_for_responses(part)
                for part in content_value
            ]
        else:
            message_payload["content"] = (
                content_value if content_value is not None else ""
            )
        return message_payload

    for item in history:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type in {"function_call", "function_call_output", "reasoning"}:
            converted_item = copy.deepcopy(item)
            if item_type == "function_call" and not converted_item.get("id"):
                # OpenRouter requires an item ID for function-call input. Older
                # Omlorix rows only retained call_id, which is a stable fallback.
                converted_item["id"] = converted_item.get("call_id")
            converted.append(converted_item)
            continue

        if item_type == "message":
            role = str(item.get("role") or "user").strip().lower()
            converted.append(build_message_payload(item, role, item.get("content")))
            continue

        role = str(item.get("role") or "").strip().lower()
        if not role:
            continue

        if role == "assistant":
            _append_openrouter_response_reasoning_items(
                converted,
                item.get("_openrouter_responses_reasoning_items"),
            )

        if role == "tool":
            call_id = item.get("tool_call_id")
            if call_id:
                converted.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": item.get("content", ""),
                    }
                )
            continue

        assistant_has_tool_calls = role == "assistant" and isinstance(
            item.get("tool_calls"),
            list,
        )
        if assistant_has_tool_calls:
            for tool_call in item.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function_payload = tool_call.get("function") or {}
                name = function_payload.get("name")
                arguments = function_payload.get("arguments")
                call_id = tool_call.get("id")
                if name and call_id:
                    converted.append(
                        {
                            "type": "function_call",
                            "id": tool_call.get("_openrouter_item_id") or call_id,
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments or {}),
                        }
                    )

        content = item.get("content")
        if assistant_has_tool_calls and content in (None, "", []):
            # Responses represents calls as standalone output items; an empty
            # assistant message between the call and output is not part of the
            # original provider sequence.
            continue
        converted.append(build_message_payload(item, role, content))

    return converted


def _openrouter_extract_response_text(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output_items = payload.get("output")
    if not isinstance(output_items, list):
        return ""

    chunks: list[str] = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)

    return "".join(chunks).strip()


def list_models_openrouter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_openrouter", globals())
    return _implementation._impl_list_models_openrouter(*args, **kwargs)


def get_model_providers(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_model_providers", globals())
    return _implementation._impl_get_model_providers(*args, **kwargs)


def get_model_information(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_model_information", globals())
    return _implementation._impl_get_model_information(*args, **kwargs)


def get_model_information_endpoint(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies(
        "get_model_information_endpoint", globals()
    )
    return _implementation._impl_get_model_information_endpoint(*args, **kwargs)


def get_api_key_info(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_api_key_info", globals())
    return _implementation._impl_get_api_key_info(*args, **kwargs)


def create_open_router_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_open_router_model", globals())
    return _implementation._impl_create_open_router_model(*args, **kwargs)


def get_openrouter_provider_information(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies(
        "get_openrouter_provider_information", globals()
    )
    return _implementation._impl_get_openrouter_provider_information(*args, **kwargs)


def openrouter_chat(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("openrouter_chat", globals())
    return _implementation._impl_openrouter_chat(*args, **kwargs)


def openrouter_title_generation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation as _implementation

    _implementation._sync_compat_dependencies("openrouter_title_generation", globals())
    return _implementation._impl_openrouter_title_generation(*args, **kwargs)


def reformat_chat_history(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import messages as _implementation

    _implementation._sync_compat_dependencies("reformat_chat_history", globals())
    return _implementation._impl_reformat_chat_history(*args, **kwargs)
