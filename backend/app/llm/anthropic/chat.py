"""Anthropic Messages API chat orchestration and streaming response handling."""

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Any

from anthropic import APIStatusError
from fastapi import HTTPException

from app.chats.streaming import interruptible_provider_stream
from app.llm.anthropic.attachments import upload_files
from app.llm.anthropic.errors import _should_retry_without_compaction_anthropic
from app.llm.anthropic.messages import reformat_chat_history
from app.llm.anthropic.models import get_anthropic_client
from app.llm.anthropic.prompt_caching import apply_anthropic_prompt_cache
from app.llm.anthropic.request_settings import _build_anthropic_thinking_params
from app.llm.anthropic.schemas import AnthropicModelSettings
from app.llm.anthropic.settings import remove_deprecated_anthropic_request_settings
from app.llm.anthropic.thinking import (
    ANTHROPIC_PROVIDER_TYPE,
    is_anthropic_base_provider_type,
)
from app.llm.anthropic.usage import (
    _usage_field,
    calculate_anthropic_token_costs,
    normalize_anthropic_usage_metadata,
)
from app.llm.anthropic.model_list import supports_anthropic_native_websearch
from app.llm.helper import (
    build_stream_tool_event_meta,
    build_tool_call_block,
    build_tool_file_block,
    build_widget_block_meta,
    format_meta_timestamp,
    merge_settings,
    normalize_unsupported_file_ids,
    should_persist_files_in_file_block,
    stringify_tool_result_content_for_persistence,
)
from app.llm.metadata import resolve_model_metadata_id
from app.llm.models import Models
from app.llm.system_instruction.chat import (
    append_system_instruction_sections,
    get_default_system_instruction,
)
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION
from app.llmstats.models import (
    create_llm_generation_statistic,
    create_tool_call_statistic,
)
from app.tools.common import (
    is_tool_hidden_from_user,
    should_hide_tool_call_from_user,
    tools_not_yield_arguments,
)
from app.tools.errors import ToolErrorResponse, ToolErrorTracker
from app.users.roles import is_admin_role

logger = logging.getLogger(__name__)


def anthropic_chat(
    chat_id: str,
    chat_history,
    db,
    db_model: Models | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    generation_id: str | None = None,
    temp_request_flag: bool = False,
    byok: dict | None = None,
    settings_override: dict | None = None,
    reference_id: str | None = None,
    skill_content: str | None = None,
    system_instruction_sections: list[dict[str, str]] | None = None,
    assistant_metadata: dict | None = None,
    note_ids: list[str] | None = None,
    retry_count: int | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    user_role: str | None = None,
):
    from app.chats.models import create_chat_message

    assistant_metadata = (
        assistant_metadata if isinstance(assistant_metadata, dict) else {}
    )

    user_role = (user_role or "").strip().lower()

    model_name: str | None = None
    base_url_display: str | None = None
    request_provider_type = ANTHROPIC_PROVIDER_TYPE

    # -------------------
    # Client
    # -------------------
    if not byok and (not db_model or not db_model.provider_id):
        raise HTTPException(status_code=422, detail="Provider not configured")
    requested_provider_id = getattr(db_model, "provider_id", None) if db_model else None
    selected_provider = None
    if byok:
        if isinstance(byok, dict):
            base_url_display = byok.get("base_url")
            request_provider_type = byok.get("provider") or request_provider_type
        client = get_anthropic_client(
            db, api_key=byok.get("api_key"), base_url=base_url_display
        )
    else:
        from app.llm.provider_groups import resolve_provider_for_request

        selected_provider = resolve_provider_for_request(db, requested_provider_id)
        request_provider_type = getattr(selected_provider, "provider", None) or getattr(
            db_model,
            "provider",
            request_provider_type,
        )
        if db_model:
            db_model_settings = getattr(db_model, "settings", None) or {}
            if isinstance(db_model_settings, dict):
                base_url_display = db_model_settings.get("base_url")
        client = get_anthropic_client(db, selected_provider.id)

    # -------------------
    # Settings / Parameters
    # -------------------
    settings, merged_tools = merge_settings(
        getattr(db_model, "settings", None) if db_model else None,
        settings_override,
        getattr(AnthropicModelSettings, "model_fields", None),
        getattr(db_model, "tools", None) if db_model else None,
    )
    settings = remove_deprecated_anthropic_request_settings(settings)

    # -------------------
    # Chat History
    # -------------------
    uploaded_cleanup: list[str] = []
    input_formats_allowed = settings.get("input_formats", None)
    use_group_context = bool(settings.get("use_group_context", True))
    use_project_context = bool(settings.get("use_project_context", True))
    reformatted_chat_history = reformat_chat_history(
        chat_history,
        user_id,
        db,
        uploaded_cleanup=uploaded_cleanup,
        project_id=project_id,
        max_image_count=settings.get("max_image_count", None),
        max_document_count=settings.get("max_document_count", None),
        input_formats_allowed=input_formats_allowed,
        use_group_context=use_group_context,
        use_project_context=use_project_context,
        note_ids=note_ids,
        reference_parts=reference_parts,
        chat_reference_context=chat_reference_context,
    )
    formatted_history = reformatted_chat_history.get("formatted", [])
    unsupported_file_ids = normalize_unsupported_file_ids(
        reformatted_chat_history.get("unsupported_file_ids")
    )
    if unsupported_file_ids:
        yield json.dumps({"t": "uf", "file_ids": unsupported_file_ids}) + "\n"
    uploaded_cleanup.extend(reformatted_chat_history.get("uploaded_cleanup", []))
    if reformatted_chat_history.get("unsupported"):
        yield (
            json.dumps(
                {
                    "t": "w",
                    "c": "The model does not support all the files in this chat!",
                }
            )
            + "\n"
        )

    # -------------------
    # Tools
    # -------------------
    if byok:
        capabilities = byok.get("capabilities", []) or []
    else:
        capabilities = getattr(db_model, "capabilities", None) or []
    raw_tools = (
        byok.get("tools", [])
        if (byok and isinstance(byok.get("tools"), (list, tuple, set, dict, str)))
        else merged_tools
    )
    tools_flag = "tools" in capabilities or bool(raw_tools)
    tools = []
    tool_list = []
    if tools_flag and raw_tools:
        from app.tools.utils import resolve_enabled_tools

        resolve_enabled_tools_result = resolve_enabled_tools(
            raw_tools,
            db=db,
            model_settings=settings,
            user_id=user_id,
            byok=byok,
            project_id=project_id,
        )
        tool_list = resolve_enabled_tools_result.get("tool_list", []) or []
        temp_tools = resolve_enabled_tools_result.get("tool_schemas", []) or []
        if resolve_enabled_tools_result.get("mcp_requested"):
            try:
                from app.mcp.utils import build_mcp_provider_bundle

                mcp_provider = (
                    byok.get("provider")
                    if isinstance(byok, dict) and isinstance(byok.get("provider"), str)
                    else getattr(db_model, "provider", None) or "anthropic"
                )
                mcp_bundle = build_mcp_provider_bundle(
                    db,
                    provider=mcp_provider,
                    user_id=user_id,
                    model_settings=settings,
                )
                for name in mcp_bundle.get("bridge_tool_names", []) or []:
                    if name not in tool_list:
                        tool_list.append(name)
                temp_tools.extend(mcp_bundle.get("bridge_tool_schemas", []) or [])
            except Exception:
                logger.exception("Failed to build MCP tools for Anthropic provider")
        settings["_runtime_enabled_tools"] = [
            *list(tool_list),
            *(["mcp"] if resolve_enabled_tools_result.get("mcp_requested") else []),
        ]
        settings["_runtime_origin_model_id"] = (
            "" if byok else str(getattr(db_model, "id", "") or "")
        )
        tools = []
        for tool in temp_tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                        "parameters": tool.get("parameters"),
                    },
                }
            )
        # Convert OpenAI tool format to Anthropic tool format
        if tools:
            anthropic_tools = []
            for tool in tools:
                if tool.get("type") == "function" and "function" in tool:
                    function_def = tool["function"]
                    anthropic_tools.append(
                        {
                            "name": function_def.get("name"),
                            "description": function_def.get("description"),
                            "input_schema": function_def.get("parameters"),
                        }
                    )
                else:
                    anthropic_tools.append(tool)
            tools = anthropic_tools

    native_websearch_model = (
        byok.get("model_name")
        if isinstance(byok, dict)
        else getattr(db_model, "model_name", None)
    )
    native_websearch_enabled = (
        bool(settings.get("native_websearch"))
        and not is_anthropic_base_provider_type(request_provider_type)
        and supports_anthropic_native_websearch(native_websearch_model or "")
    )
    if native_websearch_enabled:

        def _is_web_search_tool(entry: dict) -> bool:
            if not isinstance(entry, dict):
                return False
            if entry.get("name") == "web_search" or entry.get("type") == "web_search":
                return True
            func_def = entry.get("function")
            if isinstance(func_def, dict) and func_def.get("name") == "web_search":
                return True
            return False

        tools = [tool for tool in tools if not _is_web_search_tool(tool)]

        tools.append(
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
        )

    # -------------------
    # Variables
    # -------------------
    thinking = ""
    thinking_time_already_started = False
    thinking_time_start = None
    content = ""
    meta_input_tokens = 0
    meta_output_tokens = 0
    meta_reasoning_tokens = 0
    meta_total_tokens = 0
    meta_request_count = 0
    meta_ephemeral_1h_input_tokens = 0
    meta_ephemeral_5m_input_tokens = 0
    meta_cache_creation_input_tokens = 0
    meta_cache_read_input_tokens = 0
    meta_stop_sequence = ""
    meta_stop_reason = ""
    meta_server_tool_use = None
    meta_service_tier = ""
    meta_thinking_signature = ""
    meta_message_id = ""
    # Keep the provider-reported identifier separate from Omlorix's internal
    # message model ID. The configured model is applied when metadata is built.
    meta_target_model_id: str | None = None
    meta_total_thinking_time = 0
    meta_tokens_per_second = None
    meta_last_thinking_time = 0
    meta_time_to_first_token = None
    meta_compaction_enabled = False
    meta_compaction_threshold = None
    meta_compaction_blocks = 0
    meta_compaction_input_tokens = 0
    meta_compaction_output_tokens = 0
    compaction_supported_by_endpoint = True
    compaction_supported_by_client = hasattr(getattr(client, "beta", None), "messages")
    function_call = True
    max_calls = MAX_TOOL_CALLS_PER_GENERATION
    suppress_tools = False
    tool_error_tracker = ToolErrorTracker()
    content_generation_start = None
    content_generation_duration = 0.0
    request_start_time = None

    collecting_tool_arguments = ""
    collecting_tool_name = ""
    collecting_tool_event_sent = False
    tool_call_id = ""
    collecting_compaction_content = ""
    collecting_compaction_active = False
    last_tool_server = False  # Saves if the last tool which the model called is a server side one, so it does not execute the tool call.

    web_search = False
    web_search_sources = []
    web_search_tool_use_id = None
    web_search_events: list[dict] = []
    stream_tool_event_meta_cache: dict[str, dict[str, Any] | None] = {}

    # Track native websearch tool calls for cost calculation
    meta_native_websearch_tool_calls_count = 0

    def get_stream_tool_event_meta(
        tool_name: str | None, *, tool_call_id: str | None = None
    ):
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            return None
        if normalized_tool_name not in stream_tool_event_meta_cache:
            stream_tool_event_meta_cache[normalized_tool_name] = (
                build_stream_tool_event_meta(
                    db,
                    user_id=user_id,
                    tool_name=normalized_tool_name,
                    model_settings=settings,
                )
            )
        meta = copy.deepcopy(stream_tool_event_meta_cache.get(normalized_tool_name))
        if (
            isinstance(meta, dict)
            and isinstance(meta.get("mcp_app"), dict)
            and tool_call_id
        ):
            meta["mcp_app"]["tool_call_id"] = str(tool_call_id)
        return meta

    # New message format: accumulate content blocks
    messages_to_save = []
    last_message_type = "user"
    assistant_message_saved = False
    redacted_thinking_blocks: list[str] = []

    def _resolve_target_model_id():
        return "byok" if byok else getattr(db_model, "id", None)

    def _current_reasoning_meta(additional: dict | None = None) -> dict:
        """Build metadata needed to replay Anthropic thinking unchanged."""
        reasoning_meta: dict[str, Any] = {}
        if meta_last_thinking_time:
            reasoning_meta["reasoning_time"] = meta_last_thinking_time
        if meta_thinking_signature:
            reasoning_meta["anthropic"] = {
                "thinking_signature": meta_thinking_signature,
            }
        if additional:
            reasoning_meta.update(additional)
        return reasoning_meta

    def _finalize_pending_assistant_message(meta_override: dict | None = None):
        nonlocal \
            messages_to_save, \
            thinking, \
            content, \
            last_message_type, \
            assistant_message_saved
        if temp_request_flag or assistant_message_saved:
            return None
        target_id = _resolve_target_model_id()
        if target_id is None:
            return None

        reasoning_meta = _current_reasoning_meta(meta_override)
        if thinking:
            messages_to_save.append(
                {
                    "type": "reasoning",
                    "content": thinking,
                    "meta": reasoning_meta,
                }
            )
            thinking = ""
            last_message_type = "reasoning"

        if content:
            content_meta = dict(meta_override or {})
            messages_to_save.append(
                {
                    "type": "content",
                    "content": content,
                    "meta": content_meta,
                }
            )
            content = ""
            last_message_type = "content"
        elif meta_override and messages_to_save:
            last_meta = messages_to_save[-1].setdefault("meta", {})
            last_meta.update(meta_override)

        if not messages_to_save:
            return None

        assistant_msg = create_chat_message(
            db,
            chat_id,
            target_id,
            "assistant",
            reference_id=reference_id,
            content=messages_to_save,
            retry_count=retry_count,
        )
        assistant_message_saved = True
        return assistant_msg.id if assistant_msg else None

    start_time = datetime.now(timezone.utc)
    meta_generation_success = False
    meta_generation_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""
    if db_model:
        meta_target_provider_id = (
            getattr(selected_provider, "id", None) or db_model.provider_id
        )
    else:
        meta_target_provider_id = byok.get("provider_id") or "byok"

    def _record_generation_stat():
        try:
            meta_payload = {
                "generation_time": round(
                    (datetime.now(timezone.utc) - start_time).total_seconds(), 2
                ),
                "request_count": meta_request_count,
                "input_tokens": meta_input_tokens,
                "input_token_cached": meta_cache_read_input_tokens,
                "cache_write_tokens": meta_cache_creation_input_tokens,
                "ephemeral_1h_input_tokens": meta_ephemeral_1h_input_tokens,
                "ephemeral_5m_input_tokens": meta_ephemeral_5m_input_tokens,
                "output_tokens": meta_output_tokens,
                "reasoning_tokens": meta_reasoning_tokens,
                "total_tokens": meta_total_tokens,
                "thinking_time": meta_last_thinking_time,
                "total_thinking_time": meta_total_thinking_time,
                "tokens_per_second": meta_tokens_per_second,
                "compaction_enabled": meta_compaction_enabled,
                "compaction_threshold": meta_compaction_threshold,
                "compaction_blocks": meta_compaction_blocks,
                "compaction_input_tokens": meta_compaction_input_tokens,
                "compaction_output_tokens": meta_compaction_output_tokens,
            }
            if base_url_display:
                meta_payload["base_url"] = base_url_display
            if not byok:
                from app.llm.provider_groups import build_provider_group_resolution_meta

                meta_payload.update(
                    build_provider_group_resolution_meta(
                        db,
                        requested_provider_id,
                        selected_provider,
                    )
                )
            # Calculate costs
            costs = calculate_anthropic_token_costs(
                model_name=model_name
                or getattr(db_model, "model_name", None)
                or "anthropic",
                input_tokens=meta_input_tokens,
                cached_input_tokens=meta_cache_read_input_tokens,
                cache_write_tokens=meta_cache_creation_input_tokens,
                ephemeral_5m_input_tokens=meta_ephemeral_5m_input_tokens,
                ephemeral_1h_input_tokens=meta_ephemeral_1h_input_tokens,
                output_tokens=meta_output_tokens,
                native_websearch_tool_calls_count=meta_native_websearch_tool_calls_count,
            )
            if costs:
                meta_payload["input_tokens_cost"] = costs.get("input_tokens_cost", 0)
                meta_payload["cached_input_tokens_cost"] = costs.get(
                    "cached_input_tokens_cost",
                    0,
                )
                meta_payload["cache_write_tokens_cost"] = costs.get(
                    "cache_write_tokens_cost",
                    0,
                )
                meta_payload["output_tokens_cost"] = costs.get("output_tokens_cost", 0)
                meta_payload["native_websearch_costs"] = costs.get(
                    "native_websearch_costs", 0
                )
                meta_payload["total_costs"] = costs.get("total_costs", 0)
            create_llm_generation_statistic(
                db,
                model_name=model_name
                or getattr(db_model, "model_name", None)
                or "anthropic",
                model_id=getattr(db_model, "id", None) or "anthropic",
                provider="anthropic",
                provider_id=meta_target_provider_id,
                success=meta_generation_success,
                error=meta_generation_error,
                error_status_code=meta_error_status_code,
                error_message=meta_error_message,
                error_type=meta_error_type,
                category="chat",
                meta={
                    k: v for k, v in meta_payload.items() if v not in (None, "", [], {})
                },
                user_id=user_id,
                is_byok=bool(byok),
            )
        except Exception as e:
            logger.error(f"Failed to record generation stat: {e}")
            pass

    try:
        while function_call and max_calls > 0:
            function_call = False
            meta_time_to_first_token = None
            request_start_time = None
            web_search_tool_use_id = None
            collecting_tool_arguments = ""
            current_request_usage: dict[str, int] = {}

            # -------------------
            # System Instruction
            # -------------------
            custom_system_instruction = settings.get("system_instruction")
            system_instruction = get_default_system_instruction(
                db,
                tools,
                settings.get("knowledge_cutoff", None),
                user_id,
                web_search,
                custom_system_instruction,
            )
            legacy_sections = []
            if skill_content:
                legacy_sections.append(
                    {"title": "Skill Instructions", "content": skill_content}
                )
            system_instruction = append_system_instruction_sections(
                system_instruction,
                [*legacy_sections, *(system_instruction_sections or [])],
            )

            # -------------------
            # Model Name
            # -------------------
            if byok:
                model_name = byok.get("model_name")
            else:
                model_name = getattr(db_model, "model_name", None)

            # -------------------
            # Request Settings
            # -------------------
            max_tokens = settings.get("max_tokens")
            stop_sequences = settings.get("stop_sequences")

            thinking_params = _build_anthropic_thinking_params(
                settings,
                model_name,
                allow_compatible_fallback=is_anthropic_base_provider_type(
                    request_provider_type
                ),
            )

            # -------------------
            # Request
            # -------------------
            request_kwargs = {
                "model": model_name,
                "max_tokens": max_tokens,
                "messages": formatted_history,
                "system": system_instruction,
                "stream": True,
                "tools": [] if suppress_tools else tools,
            }
            apply_anthropic_prompt_cache(request_kwargs, settings)
            if thinking_params is not None:
                request_kwargs["thinking"] = thinking_params

            input_tokens_limit = settings.get("input_tokens_limit")
            if input_tokens_limit in (None, ""):
                input_tokens_limit = settings.get("input_token_limit")
            try:
                input_tokens_limit = (
                    int(input_tokens_limit)
                    if input_tokens_limit not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                input_tokens_limit = None

            use_compaction = False
            if (
                compaction_supported_by_endpoint
                and compaction_supported_by_client
                and input_tokens_limit is not None
                and input_tokens_limit >= 50_000
            ):
                # Anthropic compaction trigger must leave room in the context window.
                compact_threshold = max(50_000, input_tokens_limit - 10_000)
                request_kwargs["context_management"] = {
                    "edits": [
                        {
                            "type": "compact_20260112",
                            "trigger": {
                                "type": "input_tokens",
                                "value": compact_threshold,
                            },
                        }
                    ]
                }
                request_kwargs["betas"] = ["compact-2026-01-12"]
                use_compaction = True
                meta_compaction_enabled = True
                meta_compaction_threshold = compact_threshold
            else:
                meta_compaction_enabled = False
                meta_compaction_threshold = None
            if stop_sequences is not None:
                request_kwargs["stop_sequences"] = stop_sequences

            request_start_time = datetime.now(timezone.utc)
            try:
                create_fn = (
                    client.beta.messages.create
                    if use_compaction
                    else client.messages.create
                )
                response = create_fn(**request_kwargs)
                meta_generation_success = True
            except APIStatusError as api_exc:
                if request_kwargs.get(
                    "context_management"
                ) and _should_retry_without_compaction_anthropic(api_exc):
                    logger.warning(
                        "Retrying Anthropic request without compaction after unsupported-compaction error."
                    )
                    request_kwargs.pop("context_management", None)
                    request_kwargs["betas"] = [
                        beta
                        for beta in (request_kwargs.get("betas") or [])
                        if beta != "compact-2026-01-12"
                    ]
                    if not request_kwargs["betas"]:
                        request_kwargs.pop("betas", None)
                    compaction_supported_by_endpoint = False
                    meta_compaction_enabled = False
                    meta_compaction_threshold = None
                    response = client.messages.create(**request_kwargs)
                    meta_generation_success = True
                else:
                    raise

            for chunk in interruptible_provider_stream(response, generation_id):
                try:
                    if generation_id:
                        from app.chats.streaming import cancel_registry

                        if cancel_registry.is_cancelled(generation_id):
                            # Persist any partial assistant content accumulated so far
                            if (content or thinking or messages_to_save) and (
                                not temp_request_flag
                            ):
                                cancellation_meta = {"status": "cancelled"}
                                additional_meta = {
                                    "model": model_name,
                                    "request_count": meta_request_count,
                                    "input_tokens": meta_input_tokens,
                                    "input_token_cached": meta_cache_read_input_tokens,
                                    "cache_write_tokens": meta_cache_creation_input_tokens,
                                    "ephemeral_5m_input_tokens": meta_ephemeral_5m_input_tokens,
                                    "ephemeral_1h_input_tokens": meta_ephemeral_1h_input_tokens,
                                    "output_tokens": meta_output_tokens,
                                    "reasoning_tokens": meta_reasoning_tokens,
                                    "total_tokens": meta_total_tokens,
                                    "thinking_time": meta_last_thinking_time,
                                    "total_thinking_time": meta_total_thinking_time,
                                    "time_to_first_token": meta_time_to_first_token,
                                }
                                for key, value in additional_meta.items():
                                    if value not in (None, 0, "", [], {}):
                                        cancellation_meta[key] = value
                                cancellation_meta["timestamp"] = format_meta_timestamp()
                                # Flush any pending content/thinking to messages_to_save
                                if thinking and last_message_type != "reasoning":
                                    messages_to_save.append(
                                        {
                                            "type": "reasoning",
                                            "content": thinking,
                                            "meta": _current_reasoning_meta(),
                                        }
                                    )
                                if content and last_message_type != "content":
                                    messages_to_save.append(
                                        {
                                            "type": "content",
                                            "content": content,
                                            "meta": cancellation_meta,
                                        }
                                    )
                                elif messages_to_save:
                                    # Add meta to last block
                                    messages_to_save[-1]["meta"] = cancellation_meta
                                if messages_to_save:
                                    resolved_target_model_id = (
                                        _resolve_target_model_id()
                                    )
                                    if resolved_target_model_id is None:
                                        yield (
                                            json.dumps(
                                                {
                                                    "t": "d",
                                                    "d": "c",
                                                    "c": {"status": "cancelled"},
                                                }
                                            )
                                            + "\n"
                                        )
                                        return
                                    create_chat_message(
                                        db,
                                        chat_id,
                                        resolved_target_model_id,
                                        "assistant",
                                        reference_id=reference_id,
                                        content=messages_to_save,
                                        retry_count=retry_count,
                                    )
                                    assistant_message_saved = True
                            # Inform stream consumer about cancellation and stop
                            yield (
                                json.dumps(
                                    {"t": "d", "d": "c", "c": {"status": "cancelled"}}
                                )
                                + "\n"
                            )
                            return
                except Exception:
                    # Best-effort cancel check; do not break streaming on errors in cancel registry
                    pass
                if chunk.type == "message_start":
                    meta_message_id = chunk.message.id
                    meta_target_model_id = resolve_model_metadata_id(
                        getattr(chunk.message, "model", None),
                        meta_target_model_id,
                    )
                    # Anthropic reports all input/cache counters on message_start.
                    # Hold the request snapshot until message_delta supplies the
                    # final output count, then aggregate the request exactly once.
                    current_request_usage = normalize_anthropic_usage_metadata(
                        chunk.message.usage
                    )
                    meta_service_tier = _usage_field(
                        chunk.message.usage,
                        "service_tier",
                        "",
                    )
                if chunk.type == "content_block_start":
                    if chunk.content_block.type in ("tool_use", "server_tool_use"):
                        collecting_tool_name = chunk.content_block.name
                        collecting_tool_arguments = ""
                        tool_call_id = chunk.content_block.id
                        if (
                            collecting_tool_name
                            and collecting_tool_name in tools_not_yield_arguments
                            and not is_tool_hidden_from_user(collecting_tool_name)
                            and not collecting_tool_event_sent
                        ):
                            yield (
                                json.dumps({"t": "t_c", "d": collecting_tool_name})
                                + "\n"
                            )
                            collecting_tool_event_sent = True
                        if chunk.content_block.type == "server_tool_use":
                            last_tool_server = True
                            if collecting_tool_name == "web_search":
                                meta_native_websearch_tool_calls_count += 1
                    elif chunk.content_block.type == "compaction":
                        collecting_compaction_active = True
                        collecting_compaction_content = ""
                        start_content = _usage_field(chunk.content_block, "content", "")
                        if isinstance(start_content, str) and start_content:
                            collecting_compaction_content += start_content
                    elif chunk.content_block.type == "redacted_thinking":
                        redacted_data = _usage_field(chunk.content_block, "data", "")
                        if isinstance(redacted_data, str) and redacted_data:
                            # Anthropic treats this opaque block as resumable
                            # reasoning state, so save it without rendering it.
                            redacted_thinking_blocks.append(redacted_data)
                            if not temp_request_flag:
                                messages_to_save.append(
                                    {
                                        "type": "reasoning",
                                        "content": "",
                                        "meta": {
                                            "anthropic_redacted_thinking": redacted_data,
                                        },
                                    }
                                )
                    elif chunk.content_block.type == "web_search_tool_result":
                        result_pages = []
                        web_search_content = chunk.content_block.content
                        for page in web_search_content:
                            page_dict = {
                                "encrypted_content": page.encrypted_content,
                                "page_age": page.page_age,
                                "title": page.title,
                                "url": page.url,
                            }
                            web_search_sources.append(page_dict)
                            result_pages.append(page_dict)
                        web_search_tool_use_id = chunk.content_block.tool_use_id
                        matching_event = next(
                            (
                                event
                                for event in web_search_events
                                if event.get("tool_use_id") == web_search_tool_use_id
                            ),
                            None,
                        )
                        if matching_event:
                            matching_event["results"] = result_pages
                        else:
                            web_search_events.append(
                                {
                                    "tool_use_id": web_search_tool_use_id,
                                    "name": "web_search",
                                    "input": {},
                                    "results": result_pages,
                                }
                            )

                if chunk.type == "content_block_delta":
                    if chunk.delta.type == "text_delta":
                        now = datetime.now(timezone.utc)
                        if (
                            meta_time_to_first_token is None
                            and request_start_time is not None
                        ):
                            meta_time_to_first_token = (
                                now - request_start_time
                            ).total_seconds()
                        # Track content generation start time (for tokens_per_second calculation)
                        if content_generation_start is None:
                            content_generation_start = now

                        if thinking_time_already_started:
                            meta_last_thinking_time = (
                                now - thinking_time_start
                            ).total_seconds()
                            meta_total_thinking_time += meta_last_thinking_time
                            yield (
                                json.dumps({"t": "r_f", "d": meta_last_thinking_time})
                                + "\n"
                            )
                            thinking_time_already_started = False
                            thinking_time_start = None
                        yield json.dumps({"t": "c", "d": chunk.delta.text}) + "\n"
                        content += chunk.delta.text

                    if chunk.delta.type == "thinking_delta":
                        now = datetime.now(timezone.utc)
                        if content_generation_start is None:
                            content_generation_start = now
                        if not thinking_time_already_started:
                            thinking_time_start = now
                            thinking_time_already_started = True
                        yield json.dumps({"t": "r", "d": chunk.delta.thinking}) + "\n"
                        thinking += chunk.delta.thinking

                    if chunk.delta.type == "signature_delta":
                        meta_thinking_signature = chunk.delta.signature

                    if chunk.delta.type == "input_json_delta":
                        collecting_tool_arguments += chunk.delta.partial_json
                        if (
                            collecting_tool_name
                            and collecting_tool_name not in tools_not_yield_arguments
                            and not is_tool_hidden_from_user(collecting_tool_name)
                            and isinstance(chunk.delta.partial_json, str)
                            and chunk.delta.partial_json
                        ):
                            tool_delta_payload = {
                                "id": tool_call_id,
                                "name": collecting_tool_name,
                                "delta": chunk.delta.partial_json,
                            }
                            stream_meta = get_stream_tool_event_meta(
                                collecting_tool_name,
                                tool_call_id=tool_call_id,
                            )
                            if stream_meta:
                                tool_delta_payload["meta"] = stream_meta
                            yield (
                                json.dumps(
                                    {
                                        "t": "t_cd",
                                        "d": tool_delta_payload,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    if chunk.delta.type == "compaction_delta":
                        compaction_text = _usage_field(chunk.delta, "content", "")
                        if isinstance(compaction_text, str):
                            collecting_compaction_content += compaction_text

                if chunk.type == "content_block_stop":
                    if collecting_compaction_active:
                        compaction_text = (collecting_compaction_content or "").strip()
                        if compaction_text:
                            meta_compaction_blocks += 1
                            if not temp_request_flag:
                                messages_to_save.append(
                                    {
                                        "type": "compaction",
                                        "content": compaction_text,
                                    }
                                )
                                last_message_type = "compaction"
                        collecting_compaction_active = False
                        collecting_compaction_content = ""

                    if collecting_tool_name:
                        # 1. Parse arguments
                        try:
                            args = json.loads(collecting_tool_arguments)
                        except Exception:
                            args = {}

                        hide_tool_arguments = (
                            collecting_tool_name in tools_not_yield_arguments
                        )
                        hidden_from_user = should_hide_tool_call_from_user(
                            collecting_tool_name, args
                        )
                        if not collecting_tool_event_sent and not hidden_from_user:
                            tool_event_payload = {
                                "t": "t_c",
                                "d": {
                                    "id": tool_call_id,
                                    "name": collecting_tool_name,
                                },
                            }
                            if not hide_tool_arguments:
                                tool_event_payload["d"]["args"] = args
                                tool_event_payload["c"] = args
                            stream_meta = get_stream_tool_event_meta(
                                collecting_tool_name,
                                tool_call_id=tool_call_id,
                            )
                            if stream_meta:
                                tool_event_payload["d"]["meta"] = stream_meta
                            yield json.dumps(tool_event_payload) + "\n"
                        elif not hide_tool_arguments and not hidden_from_user:
                            # We already emitted an early event without arguments; send final payload with arguments now
                            tool_event_payload = {
                                "t": "t_c",
                                "d": {
                                    "id": tool_call_id,
                                    "name": collecting_tool_name,
                                    "args": args,
                                },
                                "c": args,
                            }
                            stream_meta = get_stream_tool_event_meta(
                                collecting_tool_name,
                                tool_call_id=tool_call_id,
                            )
                            if stream_meta:
                                tool_event_payload["d"]["meta"] = stream_meta
                            yield json.dumps(tool_event_payload) + "\n"

                        # 2. Save Assistant Message (Content + Thinking so far)
                        # Calculate thinking time for this turn
                        if thinking_time_already_started:
                            meta_last_thinking_time = (
                                datetime.now(timezone.utc) - thinking_time_start
                            ).total_seconds()
                            meta_total_thinking_time += meta_last_thinking_time
                            yield (
                                json.dumps({"t": "r_f", "d": meta_last_thinking_time})
                                + "\n"
                            )
                            thinking_time_already_started = False
                            thinking_time_start = None

                        meta = {}
                        tool_meta = (
                            {"thinking_time": meta_last_thinking_time}
                            if meta_last_thinking_time
                            else None
                        )
                        if meta_thinking_signature:
                            meta.update(
                                {
                                    "thinking_signature": {
                                        "anthropic": meta_thinking_signature
                                    }
                                }
                            )
                        if tool_meta:
                            meta.update(tool_meta)
                        if meta_time_to_first_token is not None:
                            meta["time_to_first_token"] = meta_time_to_first_token

                        if not temp_request_flag:
                            # Accumulate reasoning and content blocks before tool call
                            if thinking:
                                messages_to_save.append(
                                    {
                                        "type": "reasoning",
                                        "content": thinking,
                                        "meta": _current_reasoning_meta(),
                                    }
                                )
                                last_message_type = "reasoning"
                            if content:
                                messages_to_save.append(
                                    {
                                        "type": "content",
                                        "content": content,
                                        "meta": meta,
                                    }
                                )
                                last_message_type = "content"
                            # Add tool_call block
                            if not hidden_from_user:
                                tool_call_meta = (
                                    {"native_web_search": True}
                                    if last_tool_server
                                    else None
                                )
                                messages_to_save.append(
                                    build_tool_call_block(
                                        collecting_tool_name,
                                        args,
                                        tool_call_id=tool_call_id,
                                        extra_meta=tool_call_meta,
                                    )
                                )
                                last_message_type = "tool_call"
                        if last_tool_server:
                            tool_event_payload = None
                            if collecting_tool_name == "web_search":
                                existing_event = next(
                                    (
                                        event
                                        for event in web_search_events
                                        if event.get("tool_use_id") == tool_call_id
                                    ),
                                    None,
                                )
                                if existing_event:
                                    existing_event["input"] = args or {}
                                    tool_event_payload = existing_event
                                else:
                                    tool_event_payload = {
                                        "tool_use_id": tool_call_id,
                                        "name": collecting_tool_name,
                                        "input": args or {},
                                        "results": [],
                                    }
                                    web_search_events.append(tool_event_payload)
                            if tool_event_payload is None:
                                tool_event_payload = {
                                    "tool_use_id": tool_call_id,
                                    "name": collecting_tool_name,
                                    "input": args or {},
                                    "results": [],
                                }

                            if not temp_request_flag:
                                try:
                                    tool_content = json.dumps(
                                        tool_event_payload,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    )
                                except TypeError:
                                    tool_content = str(tool_event_payload)
                                args_str = (
                                    json.dumps(
                                        args or {},
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    )
                                    if isinstance(args, dict)
                                    else str(args or "{}")
                                )
                                tool_label = (
                                    f"{collecting_tool_name}({args_str})"
                                    if collecting_tool_name
                                    else "tool"
                                )
                                # Add tool_call_result block for native web search
                                messages_to_save.append(
                                    {
                                        "type": "tool_call_result",
                                        "content": tool_content,
                                        "tool_name": tool_label,
                                        "meta": {
                                            "native_web_search": True,
                                            "tool_use_id": tool_event_payload.get(
                                                "tool_use_id"
                                            ),
                                        },
                                    }
                                )
                                last_message_type = "tool_call_result"
                            last_tool_server = False
                            content = ""
                            thinking = ""
                            collecting_tool_name = ""
                            collecting_tool_arguments = ""
                            collecting_tool_event_sent = False
                            tool_call_id = ""
                            content_generation_start = None
                            meta_thinking_signature = None
                            redacted_thinking_blocks.clear()
                            continue

                        # 3. Construct Assistant Message for History
                        assistant_parts = []
                        assistant_parts.extend(
                            {"type": "redacted_thinking", "data": data}
                            for data in redacted_thinking_blocks
                        )
                        if thinking:
                            # Check if thinking signature is available, otherwise just thinking text
                            think_block = {
                                "type": "thinking",
                                "thinking": thinking,
                                "signature": meta_thinking_signature,
                            }
                            # Only add signature if it's not empty? Anthropic requires it if enabled?
                            if not meta_thinking_signature:
                                del think_block["signature"]
                            assistant_parts.append(think_block)
                        if content:
                            assistant_parts.append({"type": "text", "text": content})

                        assistant_parts.append(
                            {
                                "type": "tool_use",
                                "id": tool_call_id,
                                "name": collecting_tool_name,
                                "input": args,
                            }
                        )

                        formatted_history.append(
                            {"role": "assistant", "content": assistant_parts}
                        )

                        # 4. Execute Tool
                        tool_result_content = []

                        if collecting_tool_name in tool_list:
                            helper_payload: dict[str, Any] = {}
                            helper_gen = None
                            tool_error_message: str | None = None
                            tool_error_response: ToolErrorResponse | None = None
                            try:
                                from app.tools.helper import resolve_tool_call

                                helper_gen = resolve_tool_call(
                                    db,
                                    collecting_tool_name,
                                    args,
                                    user_id,
                                    None,
                                    project_id,
                                    model_settings=settings,
                                    byok=byok,
                                    chat_id=chat_id,
                                    chat_history=chat_history,
                                    generation_id=generation_id,
                                    user_role=user_role,
                                    tool_call_id=tool_call_id,
                                )
                            except Exception as tool_exc:
                                tool_error_message = str(tool_exc)
                                tool_error_response = tool_error_tracker.record(
                                    collecting_tool_name, tool_exc
                                )
                                logger.exception(
                                    "Tool %s failed to start: %s",
                                    collecting_tool_name,
                                    tool_exc,
                                )

                            if helper_gen and tool_error_message is None:
                                try:
                                    while True:
                                        helper_item = next(helper_gen)
                                        if helper_item is not None:
                                            yield helper_item
                                except StopIteration as helper_done:
                                    helper_payload = helper_done.value or {}
                                except Exception as tool_exc:
                                    tool_error_message = str(tool_exc)
                                    tool_error_response = tool_error_tracker.record(
                                        collecting_tool_name, tool_exc
                                    )
                                    logger.exception(
                                        "Tool %s raised during execution: %s",
                                        collecting_tool_name,
                                        tool_exc,
                                    )

                            documents: list[str] = []
                            images: list[str] = []
                            videos: list[str] = []
                            audios: list[str] = []
                            youtube: list = []
                            webpages: list = []
                            result_text = ""

                            tool_stat_kwargs = {
                                "db": db,
                                "tool_name": collecting_tool_name or name or "unknown",
                                "model_id": meta_target_model_id
                                or (
                                    getattr(db_model, "id", None) if db_model else None
                                ),
                                "model_name": model_name
                                or (
                                    getattr(db_model, "model_name", None)
                                    if db_model
                                    else None
                                ),
                                "provider": meta_target_provider_id,
                                "user_id": user_id,
                                "is_byok": bool(byok),
                            }
                            tool_stat_logged = False

                            if tool_error_message:
                                if tool_error_response is None:
                                    tool_error_response = tool_error_tracker.record(
                                        collecting_tool_name,
                                        RuntimeError(tool_error_message),
                                    )
                                result_text = tool_error_response.model_output
                                tool_result_content = [
                                    {"type": "text", "text": result_text}
                                ]
                                if tool_error_response.stop_tool_calls:
                                    suppress_tools = True
                                try:
                                    if not tool_stat_logged:
                                        create_tool_call_statistic(
                                            success=False,
                                            error_message=tool_error_message,
                                            meta=tool_error_response.statistic_meta,
                                            **tool_stat_kwargs,
                                        )
                                        tool_stat_logged = True
                                except Exception:
                                    pass
                            else:
                                # Process Results
                                result_text = helper_payload.get("content", "")
                                documents = helper_payload.get("documents") or []
                                images = helper_payload.get("images") or []
                                videos = helper_payload.get("videos") or []
                                audios = helper_payload.get("audios") or []
                                youtube = helper_payload.get("youtube") or []
                                webpages = helper_payload.get("webpages") or []

                                # Convert files to Anthropic blocks
                                file_ids = images + documents
                                tool_result_parts = []
                                if file_ids:
                                    uploaded_files_result = upload_files(
                                        db, file_ids, user_id, input_formats_allowed
                                    )
                                    tool_result_parts = uploaded_files_result.get(
                                        "parts", []
                                    )
                                if tool_result_parts:
                                    if result_text:
                                        tool_result_parts.insert(
                                            0,
                                            {"type": "text", "text": str(result_text)},
                                        )
                                    tool_result_content = tool_result_parts
                                else:
                                    tool_result_content = [
                                        {
                                            "type": "text",
                                            "text": str(result_text) or "success",
                                        }
                                    ]

                                # Save Tool Message to DB using messages_to_save format
                                args_str = json.dumps(
                                    args, ensure_ascii=False, separators=(",", ":")
                                )
                                tool_label = f"{collecting_tool_name}({args_str})"

                                widget_data = helper_payload.get("widget")

                                if not temp_request_flag and not hidden_from_user:
                                    persist_files_in_file_block = (
                                        should_persist_files_in_file_block(
                                            collecting_tool_name
                                        )
                                    )
                                    persisted_result_text = (
                                        stringify_tool_result_content_for_persistence(
                                            collecting_tool_name,
                                            helper_payload.get("result")
                                            if helper_payload.get("result")
                                            not in (None, "")
                                            else result_text,
                                            widget_data,
                                        )
                                    )
                                    persisted_content = (
                                        persisted_result_text
                                        or result_text
                                        or (
                                            json.dumps(webpages, ensure_ascii=False)
                                            if webpages
                                            else "success"
                                        )
                                    )
                                    tool_result_block = {
                                        "type": "tool_call_result",
                                        "content": persisted_content,
                                        "tool_name": tool_label,
                                        # Keep the provider's tool-use ID on both
                                        # sides of the persisted pair.  History
                                        # reconstruction must not rely only on
                                        # block adjacency because file/widget
                                        # blocks may be inserted between them.
                                        "meta": {"tool_call_id": tool_call_id},
                                    }
                                    if documents and not persist_files_in_file_block:
                                        tool_result_block["documents"] = documents
                                    if images and not persist_files_in_file_block:
                                        tool_result_block["images"] = images
                                    if videos and not persist_files_in_file_block:
                                        tool_result_block["videos"] = videos
                                    if audios and not persist_files_in_file_block:
                                        tool_result_block["audios"] = audios
                                    if youtube:
                                        tool_result_block["youtube"] = youtube

                                    # Extract citations from webpages for web_search tool outputs.
                                    if (
                                        collecting_tool_name == "web_search"
                                        and webpages
                                    ):
                                        citations = []
                                        for page in webpages:
                                            if isinstance(page, dict):
                                                citation = {}
                                                if page.get("url"):
                                                    citation["url"] = page["url"]
                                                if page.get("title"):
                                                    citation["title"] = page["title"]
                                                # Check for snippet/content preview
                                                content = page.get("content")
                                                if content and isinstance(content, str):
                                                    # Extract first 200 chars as snippet
                                                    snippet = content[:200].strip()
                                                    if len(content) > 200:
                                                        snippet += "..."
                                                    citation["snippet"] = snippet
                                                if citation.get("url"):
                                                    citations.append(citation)
                                        if citations:
                                            if "meta" not in tool_result_block:
                                                tool_result_block["meta"] = {}
                                            tool_result_block["meta"]["citations"] = (
                                                citations
                                            )
                                    tool_meta = helper_payload.get(
                                        "tool_meta"
                                    ) or helper_payload.get("meta")
                                    if isinstance(tool_meta, dict) and tool_meta:
                                        tool_result_block.setdefault("meta", {}).update(
                                            tool_meta
                                        )

                                    messages_to_save.append(tool_result_block)
                                    if persist_files_in_file_block:
                                        file_block = build_tool_file_block(
                                            tool_name=collecting_tool_name,
                                            tool_label=tool_label,
                                            documents=documents,
                                            images=images,
                                            videos=videos,
                                            audios=audios,
                                        )
                                        if file_block:
                                            messages_to_save.append(file_block)
                                    last_message_type = "tool_call_result"
                                    if widget_data and widget_data.get("html"):
                                        messages_to_save.append(
                                            {
                                                "type": "widget",
                                                "content": widget_data.get("html"),
                                                "meta": build_widget_block_meta(
                                                    widget_data,
                                                    tool_name=collecting_tool_name,
                                                ),
                                            }
                                        )
                                        last_message_type = "widget"

                                try:
                                    if not tool_stat_logged:
                                        create_tool_call_statistic(
                                            success=True,
                                            error_message=None,
                                            meta=helper_payload.get("tool_meta"),
                                            **tool_stat_kwargs,
                                        )
                                        tool_stat_logged = True
                                except Exception:
                                    pass

                        else:
                            tool_result_content = [
                                {
                                    "type": "text",
                                    "text": f"Tool '{collecting_tool_name}' is not allowed or not available",
                                }
                            ]

                        # 5. Append User Message (Tool Result) to History
                        formatted_history.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tool_call_id,
                                        "content": tool_result_content,
                                    }
                                ],
                            }
                        )

                        # 6. Loop Control
                        function_call = True
                        max_calls -= 1

                        # Reset variables
                        content = ""
                        thinking = ""
                        collecting_tool_name = ""
                        collecting_tool_arguments = ""
                        tool_call_id = ""
                        content_generation_start = None
                        meta_thinking_signature = None
                        redacted_thinking_blocks.clear()

                if chunk.type == "message_delta":
                    meta_stop_reason = chunk.delta.stop_reason
                    meta_stop_sequence = chunk.delta.stop_sequence
                    delta_usage = normalize_anthropic_usage_metadata(chunk.usage)
                    if not current_request_usage.get("input_tokens"):
                        current_request_usage = delta_usage

                    request_input_tokens = current_request_usage.get("input_tokens", 0)
                    request_cached_tokens = current_request_usage.get(
                        "input_token_cached",
                        0,
                    )
                    request_cache_write_tokens = current_request_usage.get(
                        "cache_write_tokens",
                        0,
                    )
                    request_output_tokens = delta_usage.get(
                        "output_tokens", 0
                    ) or current_request_usage.get("output_tokens", 0)

                    meta_input_tokens += request_input_tokens
                    meta_cache_read_input_tokens += request_cached_tokens
                    meta_cache_creation_input_tokens += request_cache_write_tokens
                    meta_ephemeral_5m_input_tokens += current_request_usage.get(
                        "ephemeral_5m_input_tokens",
                        0,
                    )
                    meta_ephemeral_1h_input_tokens += current_request_usage.get(
                        "ephemeral_1h_input_tokens",
                        0,
                    )
                    meta_output_tokens += request_output_tokens
                    meta_total_tokens += request_input_tokens + request_output_tokens
                    meta_server_tool_use = _usage_field(
                        chunk.usage,
                        "server_tool_use",
                        None,
                    )
                    usage_iterations = _usage_field(chunk.usage, "iterations", None)
                    if isinstance(usage_iterations, list):
                        compaction_input = 0
                        compaction_output = 0
                        for iteration in usage_iterations:
                            iteration_type = _usage_field(iteration, "type", "")
                            if not isinstance(
                                iteration_type, str
                            ) or not iteration_type.startswith("compact"):
                                continue
                            try:
                                compaction_input += int(
                                    _usage_field(iteration, "input_tokens", 0) or 0
                                )
                            except (TypeError, ValueError):
                                pass
                            try:
                                compaction_output += int(
                                    _usage_field(iteration, "output_tokens", 0) or 0
                                )
                            except (TypeError, ValueError):
                                pass
                        meta_compaction_input_tokens = compaction_input
                        meta_compaction_output_tokens = compaction_output
                if chunk.type == "message_stop":
                    # Calculate tokens per second for content generation only
                    end_time = datetime.now(timezone.utc)
                    if content_generation_start is not None:
                        content_generation_duration = max(
                            (end_time - content_generation_start).total_seconds(),
                            0.0,
                        )
                    else:
                        content_generation_duration = 0.0

                    raw_output_tokens = meta_output_tokens
                    duration = content_generation_duration
                    if duration <= 0 and content_generation_start is not None:
                        duration = max(
                            (
                                datetime.now(timezone.utc) - content_generation_start
                            ).total_seconds(),
                            0.0,
                        )
                    tokens_per_second = (
                        (raw_output_tokens / duration)
                        if duration and duration > 0
                        else None
                    )
                    if tokens_per_second is not None:
                        meta_tokens_per_second = round(tokens_per_second, 2)

                    meta_request_count += 1

                    if not function_call:
                        # Extract citations from messages_to_save
                        all_citations = []
                        for msg in messages_to_save:
                            if (
                                msg.get("type") == "tool_call_result"
                                and msg.get("meta")
                                and msg["meta"].get("citations")
                            ):
                                all_citations.extend(msg["meta"]["citations"])

                        meta_metrics = {
                            "message_id": meta_message_id,
                            "model": resolve_model_metadata_id(
                                meta_target_model_id, model_name
                            ),
                            "ephemeral_1h_input_tokens": meta_ephemeral_1h_input_tokens,
                            "ephemeral_5m_input_tokens": meta_ephemeral_5m_input_tokens,
                            "input_tokens": meta_input_tokens,
                            "input_token_cached": meta_cache_read_input_tokens,
                            "cache_write_tokens": meta_cache_creation_input_tokens,
                            "output_tokens": meta_output_tokens,
                            "total_tokens": meta_total_tokens,
                            "request_count": meta_request_count,
                            "stop_reason": meta_stop_reason,
                            "stop_sequence": meta_stop_sequence,
                            "service_tier": meta_service_tier,
                            "tokens_per_second": meta_tokens_per_second,
                            "thinking_time": meta_last_thinking_time,
                            "total_thinking_time": meta_total_thinking_time,
                            "web_search_sources": web_search_sources,
                            "web_search_tool_use_id": web_search_tool_use_id,
                            "web_search_history": web_search_events,
                            "citations": all_citations if all_citations else None,
                            "compaction_enabled": meta_compaction_enabled,
                            "compaction_threshold": meta_compaction_threshold,
                            "compaction_blocks": meta_compaction_blocks,
                            "compaction_input_tokens": meta_compaction_input_tokens,
                            "compaction_output_tokens": meta_compaction_output_tokens,
                        }
                        meta = {}
                        meta.update({k: v for k, v in meta_metrics.items() if v})
                        if meta_time_to_first_token is not None:
                            meta["time_to_first_token"] = meta_time_to_first_token
                        meta["timestamp"] = format_meta_timestamp()
                        for key, value in assistant_metadata.items():
                            if value not in (None, "", [], {}):
                                meta[key] = value
                        if meta_thinking_signature or meta_server_tool_use:
                            anthropic_meta = meta.setdefault("anthropic", {})
                            if meta_thinking_signature:
                                anthropic_meta["thinking_signature"] = (
                                    meta_thinking_signature
                                )
                            if meta_server_tool_use:
                                server_tool_use = {}
                                if meta_server_tool_use.web_search_requests:
                                    server_tool_use["web_search_requests"] = (
                                        meta_server_tool_use.web_search_requests
                                    )
                                anthropic_meta["server_tool_use"] = server_tool_use
                        yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                        if meta_stop_reason == "refusal":
                            yield (
                                json.dumps(
                                    {
                                        "t": "w",
                                        "d": "The model refused to generate a response.",
                                    }
                                )
                                + "\n"
                            )
                            meta_generation_success = False
                            meta_generation_error = False
                            _record_generation_stat()
                            return True
                        if not temp_request_flag:
                            saved_assistant_id = _finalize_pending_assistant_message(
                                meta
                            )
                            if saved_assistant_id:
                                yield (
                                    json.dumps({"t": "a_id", "d": saved_assistant_id})
                                    + "\n"
                                )
                        _record_generation_stat()
                        return True
    except APIStatusError as e:
        meta_generation_success = False
        meta_generation_error = True
        error_details = e.body.get("error", {})
        meta_error_type = error_details.get("type", "unknown_error")
        meta_error_message = error_details.get("message", str(e))
        meta_error_status_code = e.status_code

        is_admin = is_admin_role(user_role)
        error_message = (
            meta_error_message
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
        _record_generation_stat()

        raise
    except Exception as exc:
        meta_generation_success = False
        meta_generation_error = True
        meta_error_status_code = getattr(exc, "status_code", 0)
        meta_error_message = str(exc)
        meta_error_type = exc.__class__.__name__
        _record_generation_stat()
        is_admin = is_admin_role(user_role)
        error_message = (
            str(exc)
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
        raise
