"""OpenRouter chat orchestration and streaming.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import utils as _compat_source
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION

_COMPAT_DEPENDENCIES = {
    "openrouter_chat": (
        "Any",
        "HTTPError",
        "HTTPException",
        "OpenRouterFunctionCallAccumulator",
        "OpenrouterModelSettings",
        "ToolErrorTracker",
        "_append_openrouter_response_reasoning_items",
        "_apply_openrouter_simple_settings",
        "_openrouter_convert_history_to_responses_input",
        "add_cached_input_token_meta",
        "append_system_instruction_sections",
        "build_openrouter_headers",
        "build_stream_tool_event_meta",
        "build_tool_call_block",
        "build_tool_file_block",
        "build_web_search_citations",
        "build_widget_block_meta",
        "collect_tool_result_citations",
        "copy",
        "create_llm_generation_statistic",
        "create_tool_call_statistic",
        "datetime",
        "extract_openrouter_incomplete_reason",
        "extract_openrouter_response_error",
        "extract_openrouter_response_usage",
        "format_meta_timestamp",
        "get_default_system_instruction",
        "get_openrouter_api_base_url",
        "get_openrouter_provider_information",
        "interruptible_provider_stream",
        "is_admin_role",
        "is_tool_hidden_from_user",
        "json",
        "logger",
        "merge_settings",
        "normalize_openrouter_usage",
        "normalize_unsupported_file_ids",
        "openrouter_response_error_http_status",
        "reformat_chat_history",
        "requests",
        "resolve_openrouter_attribution",
        "resolve_parallel_subagent_tool_calls",
        "resolve_tool_call",
        "should_hide_tool_call_from_user",
        "should_persist_files_in_file_block",
        "stringify_tool_result_content_for_persistence",
        "time",
        "timezone",
        "tools_not_yield_arguments",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "Any",
    "HTTPError",
    "HTTPException",
    "OpenRouterFunctionCallAccumulator",
    "OpenrouterModelSettings",
    "ToolErrorTracker",
    "_append_openrouter_response_reasoning_items",
    "_apply_openrouter_simple_settings",
    "_openrouter_convert_history_to_responses_input",
    "add_cached_input_token_meta",
    "append_system_instruction_sections",
    "build_openrouter_headers",
    "build_stream_tool_event_meta",
    "build_tool_call_block",
    "build_tool_file_block",
    "build_web_search_citations",
    "build_widget_block_meta",
    "collect_tool_result_citations",
    "copy",
    "create_llm_generation_statistic",
    "create_tool_call_statistic",
    "datetime",
    "extract_openrouter_incomplete_reason",
    "extract_openrouter_response_error",
    "extract_openrouter_response_usage",
    "format_meta_timestamp",
    "get_default_system_instruction",
    "get_openrouter_api_base_url",
    "get_openrouter_provider_information",
    "interruptible_provider_stream",
    "is_admin_role",
    "is_tool_hidden_from_user",
    "json",
    "logger",
    "merge_settings",
    "normalize_openrouter_usage",
    "normalize_unsupported_file_ids",
    "openrouter_response_error_http_status",
    "reformat_chat_history",
    "requests",
    "resolve_openrouter_attribution",
    "resolve_parallel_subagent_tool_calls",
    "resolve_tool_call",
    "should_hide_tool_call_from_user",
    "should_persist_files_in_file_block",
    "stringify_tool_result_content_for_persistence",
    "time",
    "timezone",
    "tools_not_yield_arguments",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_openrouter_chat(
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
    assistant_metadata = (
        assistant_metadata if isinstance(assistant_metadata, dict) else {}
    )
    try:
        from app.chats.models import create_chat_message

        # -------------------
        # API Key
        # -------------------
        if byok:
            api_key_raw = byok.get("api_key") if isinstance(byok, dict) else None
            if not isinstance(api_key_raw, str) or not api_key_raw.strip():
                raise HTTPException(status_code=422, detail="BYOK api_key not provided")
            api_key = api_key_raw.strip()
            provider = None
            settings_candidate = (
                byok.get("settings") if isinstance(byok, dict) else None
            )
            provider_settings = (
                settings_candidate if isinstance(settings_candidate, dict) else None
            )
            ranking_url, ranking_title = resolve_openrouter_attribution(
                provider_settings
            )
            api_base_url = get_openrouter_api_base_url(provider_settings)
            requested_provider_id = (
                byok.get("provider_id") if isinstance(byok, dict) else None
            )
            selected_provider = None
        else:
            from app.llm.provider_groups import resolve_provider_for_request

            requested_provider_id = getattr(db_model, "provider_id", None)
            selected_provider = resolve_provider_for_request(db, requested_provider_id)
            provider_info = get_openrouter_provider_information(
                db, selected_provider.id
            )
            provider = provider_info["provider"]
            api_key = provider_info["api_key"]
            provider_settings = provider_info["settings"]
            ranking_url = provider_info["ranking_url"]
            ranking_title = provider_info["ranking_title"]
            api_base_url = provider_info["api_base_url"]

        # -------------------
        # Settings
        # -------------------
        base_settings = getattr(db_model, "settings", None) if db_model else None
        settings, merged_tools = merge_settings(
            base_settings,
            settings_override,
            getattr(OpenrouterModelSettings, "model_fields", None),
            getattr(db_model, "tools", None) if db_model else None,
        )
        # OpenRouter's Responses endpoint does not accept the Chat Completions
        # ``reasoning.exclude`` request field. Enforce the setting at Omlorix's
        # persistence boundary instead: provider-issued reasoning may be held
        # transiently for a same-turn tool continuation, but it must never enter
        # a saved chat message when the user requested hidden reasoning.
        exclude_reasoning_from_persistence = bool(settings.get("reasoning_exclude"))

        if byok and isinstance(byok.get("supported_parameters"), (list, tuple, set)):
            settings.setdefault(
                "supported_parameters", list(byok["supported_parameters"])
            )

        # -------------------
        # Tools
        # -------------------
        if byok:
            capabilities = byok.get("capabilities", [])
        else:
            capabilities = db_model.capabilities
        tools_flag = False
        if "tools" in capabilities:
            tools_flag = True
        tool_list: list[str] = []
        tool_specs: list[dict] = []
        if tools_flag:
            if byok and isinstance(byok.get("tools"), (list, tuple, set, dict, str)):
                raw_tools = byok.get("tools")
            else:
                raw_tools = merged_tools
            # If OpenRouter native websearch is enabled, then remove the web_search tool from the raw tools
            if settings.get("native_websearch"):
                if isinstance(raw_tools, list):
                    raw_tools = [
                        tool for tool in raw_tools if tool.get("name") != "web_search"
                    ]
            from app.tools.utils import resolve_enabled_tools

            resolve_enabled_tools_result = resolve_enabled_tools(
                raw_tools,
                db=db,
                model_settings=settings,
                user_id=user_id,
                byok=byok,
                project_id=project_id,
            )
            if isinstance(resolve_enabled_tools_result, dict):
                tool_schemas = resolve_enabled_tools_result.get("tool_schemas") or []
                tool_list = resolve_enabled_tools_result.get("tool_list") or []
                settings["_runtime_enabled_tools"] = [
                    *list(tool_list),
                    *(
                        ["mcp"]
                        if resolve_enabled_tools_result.get("mcp_requested")
                        else []
                    ),
                ]
                settings["_runtime_origin_model_id"] = (
                    "" if byok else str(getattr(db_model, "id", "") or "")
                )
                if resolve_enabled_tools_result.get("mcp_requested"):
                    try:
                        from app.mcp.utils import build_mcp_provider_bundle

                        mcp_provider = (
                            byok.get("provider")
                            if isinstance(byok, dict)
                            and isinstance(byok.get("provider"), str)
                            else getattr(db_model, "provider", None) or "openrouter"
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
                        tool_schemas = list(tool_schemas) + list(
                            mcp_bundle.get("bridge_tool_schemas", []) or []
                        )
                    except Exception:
                        logger.exception("Failed to build MCP tools for OpenRouter")
                for tool in tool_schemas:
                    tool_specs.append(
                        {
                            "type": "function",
                            "name": tool.get("name"),
                            "description": tool.get("description"),
                            "parameters": tool.get("parameters"),
                        }
                    )

        # -------------------
        # Chat History
        # -------------------
        # Format the chat history, append the system instruction and the user message
        if byok:
            capabilities = byok.get("capabilities", []) or []
        elif db_model and isinstance(getattr(db_model, "capabilities", None), list):
            capabilities = db_model.capabilities
        else:
            capabilities = []
        video_enabled = "video" in capabilities
        input_formats_allowed = settings.get("input_formats", None)

        def _coerce_bool_setting(value, default: bool = True) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
                return default
            if isinstance(value, (int, float)):
                return value != 0
            return default

        use_group_context = _coerce_bool_setting(
            settings.get("use_group_context"), True
        )
        use_project_context = _coerce_bool_setting(
            settings.get("use_project_context"), True
        )

        reformat_result = reformat_chat_history(
            chat_history,
            user_id,
            db,
            project_id=project_id,
            max_image_count=settings.get("max_image_count", None),
            max_document_count=settings.get("max_document_count", None),
            max_audio_count=settings.get("max_audio_count", None),
            max_video_count=settings.get("max_video_count", None),
            video_enabled=video_enabled,
            native_youtube_video=settings.get("native_youtube_video", False),
            input_formats_allowed=input_formats_allowed,
            use_group_context=use_group_context,
            use_project_context=use_project_context,
            note_ids=note_ids,
            reference_parts=reference_parts,
            chat_reference_context=chat_reference_context,
        )
        formatted_history = (
            reformat_result.get("formatted", [])
            if isinstance(reformat_result, dict)
            else reformat_result
        )
        if isinstance(reformat_result, dict):
            unsupported_file_ids = normalize_unsupported_file_ids(
                reformat_result.get("unsupported_file_ids")
            )
            if unsupported_file_ids:
                yield json.dumps({"t": "uf", "file_ids": unsupported_file_ids}) + "\n"
        if isinstance(reformat_result, dict) and reformat_result.get("unsupported"):
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
        # Variables for the while loop
        # -------------------
        thinking = ""
        openrouter_reasoning_details: list[dict[str, Any]] = []
        openrouter_responses_reasoning_items: list[dict[str, Any]] = []
        thinking_time_already_started = False
        content = ""

        function_call = True
        max_calls = MAX_TOOL_CALLS_PER_GENERATION
        suppress_tools = False
        tool_error_tracker = ToolErrorTracker()
        content_generation_start = None
        thinking_time_start = None

        # -------------------
        # Meta data variables
        # -------------------
        meta_total_thinking_time = 0.0
        meta_last_thinking_time = 0.0
        meta_request_count = 0
        input_tokens = 0
        input_tokens_cached = 0
        cache_write_tokens = 0
        input_tokens_image = 0
        input_tokens_audio = 0
        input_tokens_video = 0
        output_tokens = 0
        output_tokens_image = 0
        output_tokens_audio = 0
        output_tokens_video = 0
        reasoning_tokens = 0
        total_tokens = 0
        meta_provider = ""
        meta_is_byok = None  # Now omlorix byok, but openrouter byok
        total_costs = 0
        upstream_inference_cost = 0
        upstream_inference_prompt_cost = 0
        upstream_inference_completions_cost = 0
        meta_time_to_first_token: float | None = None
        start_time = datetime.now(timezone.utc)
        meta_generation_success = False
        meta_generation_error = False
        meta_error_status_code = 0
        meta_error_message = ""
        meta_error_type = ""
        meta_tokens_per_second = None
        model_identifier: str | None = None
        provider_identifier = (
            byok.get("provider_id")
            if isinstance(byok, dict) and byok.get("provider_id")
            else getattr(selected_provider, "id", None)
            or getattr(db_model, "provider_id", None)
            or "openrouter"
        )

        def _record_generation_stat():
            try:
                meta_payload = {
                    "generation_time": round(
                        (datetime.now(timezone.utc) - start_time).total_seconds(), 2
                    ),
                    "request_count": meta_request_count,
                    "input_tokens": input_tokens,
                    "input_token_cached": input_tokens_cached,
                    "input_tokens_cached": input_tokens_cached,
                    "cache_write_tokens": cache_write_tokens,
                    "input_token_image": input_tokens_image,
                    "input_token_audio": input_tokens_audio,
                    "input_token_video": input_tokens_video,
                    "input_tokens_audio": input_tokens_audio,
                    "input_tokens_video": input_tokens_video,
                    "output_tokens": output_tokens,
                    "output_image_tokens": output_tokens_image,
                    "output_tokens_image": output_tokens_image,
                    "output_audio_tokens": output_tokens_audio,
                    "output_video_tokens": output_tokens_video,
                    "reasoning_tokens": reasoning_tokens,
                    "total_tokens": total_tokens,
                    "thinking_time": meta_last_thinking_time,
                    "total_thinking_time": meta_total_thinking_time,
                    "time_to_first_token": meta_time_to_first_token,
                    "tokens_per_second": meta_tokens_per_second,
                    "meta_is_byok": meta_is_byok,
                    "total_costs": total_costs,
                    "upstream_inference_cost": upstream_inference_cost,
                    "input_tokens_cost": upstream_inference_prompt_cost,
                    "output_tokens_cost": upstream_inference_completions_cost,
                }
                if not byok:
                    from app.llm.provider_groups import (
                        build_provider_group_resolution_meta,
                    )

                    meta_payload.update(
                        build_provider_group_resolution_meta(
                            db,
                            requested_provider_id,
                            selected_provider,
                        )
                    )
                create_llm_generation_statistic(
                    db,
                    model_name=model_identifier
                    or getattr(db_model, "model_name", None)
                    or "openrouter",
                    model_id=getattr(db_model, "id", None)
                    or model_identifier
                    or "openrouter",
                    provider="openrouter",
                    provider_id=provider_identifier,
                    success=meta_generation_success,
                    error=meta_generation_error,
                    error_status_code=meta_error_status_code,
                    error_message=meta_error_message,
                    error_type=meta_error_type,
                    category="chat",
                    meta={
                        k: v
                        for k, v in meta_payload.items()
                        if v not in (None, 0, "", [], {})
                    },
                    user_id=user_id,
                    is_byok=bool(byok),
                )
            except Exception:
                pass

        # For system instruction for websearch citations instruction
        web_search = False

        # New message format: accumulate content blocks
        messages_to_save = []
        last_message_type = "user"
        assistant_message_saved = False

        def _merge_reasoning_details(raw_details: Any) -> None:
            """Combine streamed OpenRouter reasoning-detail deltas in order."""
            if not isinstance(raw_details, list):
                return
            for fallback_index, raw_detail in enumerate(raw_details):
                if not isinstance(raw_detail, dict):
                    continue
                detail = copy.deepcopy(raw_detail)
                detail_index = detail.get("index", fallback_index)
                existing = next(
                    (
                        item
                        for stored_index, item in enumerate(
                            openrouter_reasoning_details
                        )
                        if item.get("index", stored_index) == detail_index
                    ),
                    None,
                )
                if existing is None:
                    openrouter_reasoning_details.append(detail)
                    continue
                for key, value in detail.items():
                    old_value = existing.get(key)
                    if isinstance(value, str) and isinstance(old_value, str):
                        if value == old_value:
                            continue
                        if value.startswith(old_value):
                            existing[key] = value
                        elif key in {"text", "summary", "data", "signature"}:
                            existing[key] = old_value + value
                        else:
                            existing[key] = value
                    else:
                        existing[key] = copy.deepcopy(value)

        def _reasoning_meta() -> dict[str, Any]:
            """Return resumable OpenRouter state for one reasoning block."""
            meta: dict[str, Any] = {}
            if meta_last_thinking_time:
                meta["reasoning_time"] = meta_last_thinking_time
            if openrouter_reasoning_details:
                meta["openrouter_reasoning_details"] = copy.deepcopy(
                    openrouter_reasoning_details
                )
            if openrouter_responses_reasoning_items:
                meta["openrouter_responses_reasoning_items"] = copy.deepcopy(
                    openrouter_responses_reasoning_items
                )
            return meta

        def _clear_pending_reasoning() -> None:
            """Remove all plaintext and structured reasoning held in memory."""
            nonlocal thinking
            thinking = ""
            openrouter_reasoning_details.clear()
            openrouter_responses_reasoning_items.clear()

        def _persist_pending_reasoning() -> bool:
            """Save and consume the current OpenRouter reasoning sequence."""
            if not (
                thinking
                or openrouter_reasoning_details
                or openrouter_responses_reasoning_items
            ):
                return False

            # The response stream may contain reasoning even though the visible
            # delta was suppressed. Discard every representation together so an
            # empty reasoning block cannot retain plaintext summaries, encrypted
            # state, or compatibility ``reasoning_details`` in its metadata.
            if exclude_reasoning_from_persistence:
                _clear_pending_reasoning()
                return False

            messages_to_save.append(
                {
                    "type": "reasoning",
                    "content": thinking,
                    "meta": _reasoning_meta(),
                }
            )
            _clear_pending_reasoning()
            return True

        def _consume_pending_reasoning_for_tool_continuation() -> bool:
            """Replay, persist, and clear reasoning from the current tool turn.

            The OpenRouter Responses request is stateless.  A completed
            reasoning output item therefore has to be inserted into
            ``formatted_history`` before the function call and output that
            continue the same turn.  Saving it only to the database is too late
            for the immediate provider follow-up.
            """
            nonlocal last_message_type
            has_reasoning = bool(
                thinking
                or openrouter_reasoning_details
                or openrouter_responses_reasoning_items
            )
            if not has_reasoning:
                return False

            _append_openrouter_response_reasoning_items(
                formatted_history,
                openrouter_responses_reasoning_items,
            )

            # Stateless Responses tool calls require the exact provider-issued
            # reasoning item in the immediate follow-up request. The item above
            # stays only in this generator's formatted_history when exclusion is
            # enabled; it is deliberately omitted from database-bound content.
            if not temp_request_flag and not exclude_reasoning_from_persistence:
                messages_to_save.append(
                    {
                        "type": "reasoning",
                        "content": thinking,
                        "meta": _reasoning_meta(),
                    }
                )
                last_message_type = "reasoning"

            _clear_pending_reasoning()
            return True

        def _finalize_pending_assistant_message(meta_override: dict | None = None):
            nonlocal \
                messages_to_save, \
                thinking, \
                content, \
                last_message_type, \
                assistant_message_saved
            if temp_request_flag or assistant_message_saved:
                return None
            target_id = "byok" if byok else getattr(db_model, "id", None)
            if target_id is None:
                return None

            if _persist_pending_reasoning():
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
                messages_to_save[-1].setdefault("meta", {}).update(meta_override)

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

        def _build_completion_meta(
            model_name: str | None,
            meta_provider: str | None,
            input_tokens: int,
            input_tokens_audio: int,
            input_tokens_video: int,
            input_tokens_cached: int,
            output_tokens: int,
            output_tokens_image: int,
            reasoning_tokens: int,
            total_tokens: int,
            meta_request_count: int,
            meta_last_thinking_time: float | None,
            meta_total_thinking_time: float | None,
            tokens_per_second: float | None,
            meta_time_to_first_token: float | None,
            meta_is_byok: bool | None,
            total_costs: float | int | None,
            upstream_inference_cost: float | int | None,
            upstream_inference_prompt_cost: float | int | None,
            upstream_inference_completions_cost: float | int | None,
            assistant_metadata: dict,
            timeout: bool = False,
            timeout_reason: str | None = None,
            timeout_message: str | None = None,
        ) -> dict:
            meta_values = {
                "model": model_name,
                "provider": meta_provider,
                "input_tokens": input_tokens,
                "input_token_audio": input_tokens_audio,
                "input_token_video": input_tokens_video,
                "input_tokens_audio": input_tokens_audio,
                "input_tokens_video": input_tokens_video,
                "input_tokens_cached": input_tokens_cached,
                "output_tokens": output_tokens,
                "output_image_tokens": output_tokens_image,
                "output_tokens_image": output_tokens_image,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "request_count": meta_request_count,
                "thinking_time": meta_last_thinking_time,
                "total_thinking_time": meta_total_thinking_time,
                "tokens_per_second": tokens_per_second,
                "time_to_first_token": meta_time_to_first_token,
                "meta_is_byok": meta_is_byok,
                "total_costs": total_costs,
                "upstream_inference_cost": upstream_inference_cost,
                "upstream_inference_prompt_cost": upstream_inference_prompt_cost,
                "upstream_inference_completions_cost": upstream_inference_completions_cost,
                "citations": collect_tool_result_citations(messages_to_save) or None,
            }
            if timeout:
                meta_values["timeout"] = True
            if timeout_reason:
                meta_values["timeout_reason"] = timeout_reason
            if timeout_message:
                meta_values["timeout_message"] = timeout_message
            add_cached_input_token_meta(
                meta_values,
                input_tokens_cached,
                aliases=("input_tokens_cached",),
            )

            meta = {}
            for key, value in meta_values.items():
                if value not in (None, 0, "", [], {}):
                    meta[key] = value
            for key, value in assistant_metadata.items():
                if value not in (None, "", [], {}):
                    meta[key] = value
            if meta:
                meta["timestamp"] = format_meta_timestamp()
            if timeout and "timeout" not in meta:
                meta["timeout"] = True
            return meta

        def _coerce_timeout(value, default):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return default
            return default if numeric <= 0 else numeric

        inactivity_timeout_sec = _coerce_timeout(
            settings.get("inactivity_timeout_sec"), 90.0
        )
        connect_timeout_sec = _coerce_timeout(settings.get("connect_timeout_sec"), 10.0)
        last_activity = time.monotonic()
        request_start_time: datetime | None = None

        def emit_timeout_event(
            reason: str | None = None, timeout_message: str | None = None
        ):
            nonlocal \
                content, \
                thinking, \
                meta_generation_error, \
                meta_error_type, \
                meta_error_message, \
                meta_tokens_per_second

            end_time = datetime.now(timezone.utc)
            tokens_per_second = None
            if content_generation_start is not None and output_tokens:
                elapsed = max(
                    (end_time - content_generation_start).total_seconds(), 0.0
                )
                if elapsed > 0:
                    tokens_per_second = output_tokens / elapsed
            if tokens_per_second is not None:
                meta_tokens_per_second = round(tokens_per_second, 2)
            meta = _build_completion_meta(
                model_name=model_name,
                meta_provider=meta_provider,
                input_tokens=input_tokens,
                input_tokens_audio=input_tokens_audio,
                input_tokens_video=input_tokens_video,
                input_tokens_cached=input_tokens_cached,
                output_tokens=output_tokens,
                output_tokens_image=output_tokens_image,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                meta_request_count=meta_request_count,
                meta_last_thinking_time=meta_last_thinking_time,
                meta_total_thinking_time=meta_total_thinking_time,
                tokens_per_second=round(tokens_per_second, 2)
                if tokens_per_second is not None
                else None,
                meta_time_to_first_token=meta_time_to_first_token,
                meta_is_byok=meta_is_byok,
                total_costs=total_costs,
                upstream_inference_cost=upstream_inference_cost,
                upstream_inference_prompt_cost=upstream_inference_prompt_cost,
                upstream_inference_completions_cost=upstream_inference_completions_cost,
                assistant_metadata=assistant_metadata,
                timeout=True,
                timeout_reason=reason,
                timeout_message=timeout_message,
            )
            meta_generation_error = True
            meta_error_type = (reason or "Timeout") or "Timeout"
            meta_error_message = timeout_message or reason or "generation timeout"

            yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"

            if not temp_request_flag:
                _finalize_pending_assistant_message(meta)

        while function_call and max_calls > 0:
            tool_call_accumulators: dict[int, dict] = {}
            tool_call_event_sent: dict[int, bool] = {}
            responses_function_calls = OpenRouterFunctionCallAccumulator()
            stream_tool_event_meta_cache: dict[str, dict[str, Any] | None] = {}

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
                meta = copy.deepcopy(
                    stream_tool_event_meta_cache.get(normalized_tool_name)
                )
                if (
                    isinstance(meta, dict)
                    and isinstance(meta.get("mcp_app"), dict)
                    and tool_call_id
                ):
                    meta["mcp_app"]["tool_call_id"] = str(tool_call_id)
                return meta

            function_call = False
            last_activity = time.monotonic()
            meta_time_to_first_token = None
            request_start_time = None

            # -------------------
            # System Instruction
            # -------------------
            custom_system_instruction = settings.get("system_instruction")
            system_instruction = get_default_system_instruction(
                db,
                tool_list,
                settings.get("knowledge_cutoff", "-"),
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
            response_input = _openrouter_convert_history_to_responses_input(
                formatted_history
            )

            # -------------------
            # OpenRouter Request
            # -------------------
            url = f"{api_base_url}/responses"

            # Model name
            if (
                byok
                and isinstance(byok.get("model_name"), str)
                and byok.get("model_name").strip()
            ):
                model_name = byok.get("model_name").strip()
            else:
                model_name = getattr(db_model, "model_name", None)
            if not isinstance(model_name, str) or not model_name.strip():
                raise HTTPException(status_code=422, detail="Model not configured")
            target_model_id = "byok" if byok else getattr(db_model, "id", None)
            model_identifier = model_name

            payload = {
                "model": model_name,
                "input": response_input,
                "instructions": system_instruction,
                "stream": True,
            }

            # Model Provider settings
            provider_mode = settings.get("provider_mode", "specific")

            if provider_mode == "specific":
                # Specific provider mode: use only_provider with optional fallbacks
                only_provider = settings.get("only_provider")
                if only_provider:
                    allow_fallbacks = settings.get("allow_fallbacks", False)
                    payload["provider"] = {
                        "only": [only_provider],
                        "allow_fallbacks": allow_fallbacks,
                    }
            elif provider_mode == "sort":
                # Sort mode: use provider.sort with the specified criteria
                provider_sort = settings.get("provider_sort")
                if provider_sort and provider_sort in [
                    "price",
                    "throughput",
                    "latency",
                ]:
                    payload["provider"] = {
                        "sort": provider_sort,
                    }
            # If provider_mode == "auto", don't include provider field at all (automatic selection)

            # Add PDF processing plugins if we have document attachments
            has_documents = any(
                isinstance(msg.get("content"), list)
                and any(
                    part.get("type") in {"file", "input_file"}
                    for part in msg["content"]
                    if isinstance(part, dict)
                )
                for msg in response_input
                if isinstance(msg.get("content"), list)
            )

            plugins = []
            if has_documents:
                pdf_engine = settings.get("pdf_processing_engine")
                plugins.append({"id": "file-parser", "pdf": {"engine": pdf_engine}})

            if plugins:
                payload["plugins"] = plugins

            # Add tools if we have any
            if tool_specs and not suppress_tools:
                payload["tools"] = tool_specs
            _apply_openrouter_simple_settings(payload, settings)
            headers = build_openrouter_headers(
                api_key,
                provider_settings,
                ranking_url=ranking_url,
                ranking_title=ranking_title,
            )

            request_start_time = datetime.now(timezone.utc)
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=(connect_timeout_sec, inactivity_timeout_sec),
                )
            except HTTPError as e:
                meta_generation_error = True
                resp = e.response
                if resp is not None:
                    meta_error_status_code = resp.status_code
                    meta_error_type = "HTTPError"
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            err = data.get("error")
                            if isinstance(err, dict) and "message" in err:
                                meta_error_message = err["message"]
                            else:
                                meta_error_message = data.get("message", str(data))
                        else:
                            meta_error_message = str(data)
                    except ValueError:
                        meta_error_message = resp.text
                else:
                    meta_error_message = str(e)
                    meta_error_type = "HTTPError"
                logger.error("[OpenRouter] Failed to reach key endpoint: %s", e)
                raise HTTPException(
                    status_code=424, detail="Failed to request OpenRouter"
                ) from e

            except requests.RequestException as exc:
                meta_generation_error = True
                meta_error_type = exc.__class__.__name__
                meta_error_message = str(exc)
                meta_error_status_code = getattr(exc, "status_code", 0)
                is_admin = is_admin_role(user_role)
                error_message = (
                    str(exc)
                    if is_admin
                    else "An error occurred during generation. Please try again."
                )
                yield json.dumps({"t": "e", "d": error_message}) + "\n"
                return

            if response.status_code != 200:
                error_message = None
                try:
                    error_data = response.json()
                except ValueError:
                    error_data = None
                metadata_info = None
                if isinstance(error_data, dict):
                    error_block = error_data.get("error") or error_data.get("message")
                    if isinstance(error_block, dict):
                        metadata_info = error_block.get("metadata")
                        error_message = error_block.get("message") or error_block.get(
                            "code"
                        )
                    elif isinstance(error_block, str):
                        error_message = error_block
                if not error_message:
                    error_message = response.text or "Unexpected OpenRouter error"
                if metadata_info:
                    try:
                        metadata_serialized = json.dumps(
                            metadata_info, ensure_ascii=False
                        )
                    except Exception:
                        metadata_serialized = str(metadata_info)
                    error_message = f"{error_message} | metadata: {metadata_serialized}"

                meta_generation_error = True
                meta_error_type = "HTTPError"
                meta_error_message = error_message
                meta_error_status_code = response.status_code

                is_admin = is_admin_role(user_role)
                display_error = (
                    error_message
                    if is_admin
                    else "An error occurred during generation. Please try again."
                )
                yield json.dumps({"t": "e", "d": display_error}) + "\n"
                return

            # Process stream and handle mid-stream errors
            try:
                response_completed_seen = False
                for line in interruptible_provider_stream(
                    response.iter_lines(),
                    generation_id,
                    close_resource=response,
                ):
                    last_activity = time.monotonic()
                    # Check for cancellation for this generation and exit gracefully if set
                    try:
                        if generation_id:
                            from app.chats.streaming import cancel_registry

                            if cancel_registry.is_cancelled(generation_id):
                                # Persist any partial assistant content accumulated so far
                                if (content or thinking) and (not temp_request_flag):
                                    cancellation_meta = {"status": "cancelled"}
                                    additional_meta = {
                                        "model": model_name,
                                        "provider": meta_provider,
                                        "request_count": meta_request_count,
                                        "input_tokens": input_tokens,
                                        "input_tokens_cached": input_tokens_cached,
                                        "input_tokens_audio": input_tokens_audio,
                                        "input_tokens_video": input_tokens_video,
                                        "output_tokens": output_tokens,
                                        "output_tokens_image": output_tokens_image,
                                        "reasoning_tokens": reasoning_tokens,
                                        "total_tokens": total_tokens,
                                        "thinking_time": meta_last_thinking_time,
                                        "total_thinking_time": meta_total_thinking_time,
                                        "time_to_first_token": meta_time_to_first_token,
                                        "meta_is_byok": meta_is_byok,
                                        "total_costs": total_costs,
                                        "upstream_inference_cost": upstream_inference_cost,
                                        "upstream_inference_prompt_cost": upstream_inference_prompt_cost,
                                        "upstream_inference_completions_cost": upstream_inference_completions_cost,
                                    }
                                    for key, value in additional_meta.items():
                                        if value not in (None, 0, "", [], {}):
                                            cancellation_meta[key] = value
                                    cancellation_meta["timestamp"] = (
                                        format_meta_timestamp()
                                    )
                                    meta_generation_success = True
                                    saved_assistant_id = (
                                        _finalize_pending_assistant_message(
                                            cancellation_meta
                                        )
                                    )
                                    if saved_assistant_id:
                                        yield (
                                            json.dumps(
                                                {"t": "a_id", "d": saved_assistant_id}
                                            )
                                            + "\n"
                                        )
                                # Inform stream consumer about cancellation and stop
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
                    except Exception:
                        # Best-effort cancel check; do not break streaming on errors in cancel registry
                        pass
                    if line:
                        line_text = line.decode("utf-8")
                        if not line_text.startswith("data: "):
                            continue
                        data = line_text[6:]
                        if data == "[DONE]":
                            if not response_completed_seen:
                                meta_request_count += 1
                            if not function_call:
                                end_time = datetime.now(timezone.utc)
                                if content_generation_start is not None:
                                    content_generation_time = (
                                        end_time - content_generation_start
                                    ).total_seconds()
                                    tokens_per_second = (
                                        (output_tokens / content_generation_time)
                                        if content_generation_time > 0
                                        else 0
                                    )
                                else:
                                    tokens_per_second = 0
                                meta_tokens_per_second = round(tokens_per_second, 2)
                                meta_values = {
                                    "model": model_name,
                                    "provider": meta_provider,
                                    "input_tokens": input_tokens,
                                    "input_tokens_audio": input_tokens_audio,
                                    "input_tokens_video": input_tokens_video,
                                    "input_tokens_cached": input_tokens_cached,
                                    "output_tokens": output_tokens,
                                    "output_tokens_image": output_tokens_image,
                                    "reasoning_tokens": reasoning_tokens,
                                    "total_tokens": total_tokens,
                                    "request_count": meta_request_count,
                                    "thinking_time": meta_last_thinking_time,
                                    "total_thinking_time": meta_total_thinking_time,
                                    "tokens_per_second": tokens_per_second,
                                    "time_to_first_token": meta_time_to_first_token,
                                    "meta_is_byok": meta_is_byok,
                                    "total_costs": total_costs,
                                    "upstream_inference_cost": upstream_inference_cost,
                                    "upstream_inference_prompt_cost": upstream_inference_prompt_cost,
                                    "upstream_inference_completions_cost": upstream_inference_completions_cost,
                                }
                                add_cached_input_token_meta(
                                    meta_values,
                                    input_tokens_cached,
                                    aliases=("input_tokens_cached",),
                                )
                                meta = {}
                                for key, value in meta_values.items():
                                    # Check if the value of the variable is not 0 or None before adding it to the meta dictionary
                                    if value not in (None, 0):
                                        meta[key] = value
                                for key, value in assistant_metadata.items():
                                    if value not in (None, "", [], {}):
                                        meta[key] = value
                                if meta:
                                    meta["timestamp"] = format_meta_timestamp()
                                yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                                if not temp_request_flag:
                                    saved_assistant_id = (
                                        _finalize_pending_assistant_message(meta)
                                    )
                                    if saved_assistant_id:
                                        yield (
                                            json.dumps(
                                                {"t": "a_id", "d": saved_assistant_id}
                                            )
                                            + "\n"
                                        )
                                meta_generation_success = True
                                break
                        try:
                            parsed = json.loads(data)

                            if not isinstance(parsed, dict):
                                continue

                            _merge_reasoning_details(parsed.get("reasoning_details"))

                            event_type = parsed.get("type")
                            normalized_usage = extract_openrouter_response_usage(parsed)
                            raw_content = None
                            reasoning = None
                            finish_reason = None
                            tool_calls_delta = None
                            terminal_completed = event_type == "response.completed"
                            terminal_incomplete_reason = (
                                extract_openrouter_incomplete_reason(parsed)
                            )
                            terminal_error = extract_openrouter_response_error(parsed)

                            # Any canonical terminal event accounts for one API
                            # request and supersedes the optional [DONE] sentinel.
                            # Counting only response.completed under-reported
                            # failed and token-limited requests.
                            if event_type in {
                                "response.completed",
                                "response.incomplete",
                                "response.failed",
                            }:
                                response_completed_seen = True
                                if event_type != "response.completed":
                                    meta_request_count += 1

                            if event_type == "response.created":
                                response_payload = parsed.get("response") or {}
                                if isinstance(response_payload, dict):
                                    meta_provider = (
                                        response_payload.get("provider")
                                        or meta_provider
                                    )

                            if event_type == "response.output_item.added":
                                function_state = (
                                    responses_function_calls.register_output_event(
                                        parsed
                                    )
                                )
                                public_state = responses_function_calls.public_state(
                                    function_state
                                )
                                if public_state:
                                    output_index = public_state.get("output_index") or 0
                                    function_name = str(
                                        public_state.get("name") or ""
                                    ).strip()
                                    function_call_id = public_state.get(
                                        "call_id"
                                    ) or public_state.get("item_id")
                                    if (
                                        function_name in tools_not_yield_arguments
                                        and not is_tool_hidden_from_user(function_name)
                                        and not tool_call_event_sent.get(output_index)
                                    ):
                                        yield (
                                            json.dumps({"t": "t_c", "d": function_name})
                                            + "\n"
                                        )
                                        tool_call_event_sent[output_index] = True

                            if event_type == "response.output_item.done":
                                output_item = parsed.get("item")
                                if (
                                    isinstance(output_item, dict)
                                    and output_item.get("type") == "reasoning"
                                ):
                                    openrouter_responses_reasoning_items.append(
                                        copy.deepcopy(output_item)
                                    )
                                responses_function_calls.register_output_event(
                                    parsed,
                                    finalized=True,
                                )

                            if event_type == "response.function_call_arguments.delta":
                                function_state = responses_function_calls.append_delta(
                                    parsed
                                )
                                public_state = responses_function_calls.public_state(
                                    function_state
                                )
                                function_name = str(
                                    (public_state or {}).get("name") or ""
                                ).strip()
                                function_call_id = (public_state or {}).get(
                                    "call_id"
                                ) or (public_state or {}).get("item_id")
                                delta_text = parsed.get("delta")
                                if (
                                    function_name
                                    and function_name not in tools_not_yield_arguments
                                    and not is_tool_hidden_from_user(function_name)
                                    and isinstance(delta_text, str)
                                    and delta_text
                                ):
                                    tool_delta_payload = {
                                        "id": function_call_id,
                                        "name": function_name,
                                        "delta": delta_text,
                                    }
                                    stream_meta = get_stream_tool_event_meta(
                                        function_name,
                                        tool_call_id=function_call_id,
                                    )
                                    if stream_meta:
                                        tool_delta_payload["meta"] = stream_meta
                                    yield (
                                        json.dumps(
                                            {"t": "t_cd", "d": tool_delta_payload},
                                            ensure_ascii=False,
                                        )
                                        + "\n"
                                    )

                            if event_type == "response.function_call_arguments.done":
                                responses_function_calls.finalize_arguments(parsed)

                            if event_type == "response.completed":
                                completed_response = parsed.get("response") or {}
                                if isinstance(completed_response, dict):
                                    meta_provider = (
                                        completed_response.get("provider")
                                        or parsed.get("provider")
                                        or meta_provider
                                    )
                                    responses_function_calls.register_completed_response(
                                        completed_response
                                    )
                                completed_calls = (
                                    responses_function_calls.finalized_calls()
                                )
                                for fallback_index, completed_call in enumerate(
                                    completed_calls
                                ):
                                    output_index = completed_call.get("output_index")
                                    if not isinstance(output_index, int):
                                        output_index = fallback_index
                                    tool_call_accumulators[output_index] = {
                                        "argument_segments": [
                                            completed_call.get("arguments") or ""
                                        ],
                                        "id": completed_call.get("call_id")
                                        or completed_call.get("item_id"),
                                        "response_item_id": completed_call.get(
                                            "item_id"
                                        ),
                                        "name": completed_call.get("name"),
                                        "completed": False,
                                    }
                                if completed_calls:
                                    finish_reason = "tool_calls"
                                meta_request_count += 1

                            if event_type == "response.output_text.delta":
                                delta_text = parsed.get("delta")
                                if isinstance(delta_text, str):
                                    raw_content = delta_text

                            if event_type in {
                                "response.reasoning.delta",
                                "response.reasoning_text.delta",
                                "response.reasoning_summary_text.delta",
                            }:
                                reasoning_delta = parsed.get("delta")
                                if isinstance(reasoning_delta, str):
                                    reasoning = reasoning_delta

                            # fallback for legacy chunks
                            if isinstance(parsed.get("choices"), list) and parsed.get(
                                "choices"
                            ):
                                delta = parsed["choices"][0].get("delta", {})
                                if isinstance(delta, dict):
                                    if isinstance(delta.get("content"), str):
                                        raw_content = delta.get("content")
                                    if isinstance(delta.get("reasoning"), str):
                                        reasoning = delta.get("reasoning")
                                    _merge_reasoning_details(
                                        delta.get("reasoning_details")
                                    )
                                    if isinstance(delta.get("tool_calls"), list):
                                        tool_calls_delta = delta.get("tool_calls")
                                finish_reason = (
                                    parsed["choices"][0].get("finish_reason")
                                    or finish_reason
                                )

                            meta_provider = parsed.get("provider") or meta_provider

                            # -------------------
                            # Token Usage
                            # -------------------
                            usage = normalized_usage
                            if isinstance(usage, dict):
                                request_usage = normalize_openrouter_usage(usage)
                                input_tokens += int(
                                    request_usage.get("input_tokens", 0)
                                )
                                input_tokens_cached += int(
                                    request_usage.get("input_token_cached", 0)
                                )
                                cache_write_tokens += int(
                                    request_usage.get("cache_write_tokens", 0)
                                )
                                input_tokens_image += int(
                                    request_usage.get("input_token_image", 0)
                                )
                                input_tokens_audio += int(
                                    request_usage.get("input_token_audio", 0)
                                )
                                input_tokens_video += int(
                                    request_usage.get("input_token_video", 0)
                                )
                                output_tokens += int(
                                    request_usage.get("output_tokens", 0)
                                )
                                output_tokens_image += int(
                                    request_usage.get("output_image_tokens", 0)
                                )
                                output_tokens_audio += int(
                                    request_usage.get("output_audio_tokens", 0)
                                )
                                output_tokens_video += int(
                                    request_usage.get("output_video_tokens", 0)
                                )
                                reasoning_tokens += int(
                                    request_usage.get("reasoning_tokens", 0)
                                )
                                total_tokens += int(
                                    request_usage.get("total_tokens", 0)
                                )

                                # Each tool round is a separately billed
                                # OpenRouter request; costs must be accumulated.
                                total_costs += float(
                                    request_usage.get("total_costs", 0)
                                )
                                upstream_inference_cost += float(
                                    request_usage.get("upstream_inference_cost", 0)
                                )
                                upstream_inference_prompt_cost += float(
                                    request_usage.get("input_tokens_cost", 0)
                                )
                                upstream_inference_completions_cost += float(
                                    request_usage.get("output_tokens_cost", 0)
                                )
                                if "meta_is_byok" in request_usage:
                                    meta_is_byok = bool(request_usage["meta_is_byok"])

                            if terminal_error:
                                meta_generation_error = True
                                meta_error_type = terminal_error["error_type"]
                                meta_error_message = terminal_error["message"]
                                meta_error_status_code = (
                                    openrouter_response_error_http_status(
                                        terminal_error
                                    )
                                )
                                display_error = (
                                    terminal_error["message"]
                                    if is_admin_role(user_role)
                                    else "An error occurred during generation. Please try again."
                                )
                                yield json.dumps({"t": "e", "d": display_error}) + "\n"
                                return

                            # -------------------
                            # Content
                            # -------------------
                            if raw_content:
                                # Track content generation start time (for tokens_per_second calculation)
                                if content_generation_start is None:
                                    content_generation_start = datetime.now(
                                        timezone.utc
                                    )

                                if thinking_time_already_started:
                                    meta_last_thinking_time = (
                                        datetime.now(timezone.utc) - thinking_time_start
                                    ).total_seconds()
                                    yield (
                                        json.dumps(
                                            {"t": "r_f", "d": meta_last_thinking_time}
                                        )
                                        + "\n"
                                    )
                                    meta_total_thinking_time += meta_last_thinking_time
                                    thinking_time_already_started = False
                                    thinking_time_start = None
                                content += raw_content
                                if (
                                    meta_time_to_first_token is None
                                    and request_start_time is not None
                                ):
                                    meta_time_to_first_token = (
                                        datetime.now(timezone.utc) - request_start_time
                                    ).total_seconds()
                                yield json.dumps({"t": "c", "d": raw_content}) + "\n"

                            # -------------------
                            # Reasoning
                            # -------------------
                            if reasoning and not settings.get("reasoning_exclude"):
                                # Track generation start time on first streamed output event (reasoning or text).
                                if content_generation_start is None:
                                    content_generation_start = datetime.now(
                                        timezone.utc
                                    )
                                if not thinking_time_already_started:
                                    thinking_time_start = datetime.now(timezone.utc)
                                    thinking_time_already_started = True
                                thinking += reasoning
                                yield json.dumps({"t": "r", "d": reasoning}) + "\n"

                            # -------------------
                            # Tool calls
                            # -------------------
                            if isinstance(tool_calls_delta, list) and tool_calls_delta:
                                for tool_call_delta in tool_calls_delta:
                                    if not isinstance(tool_call_delta, dict):
                                        continue

                                    tool_call_index = tool_call_delta.get("index", 0)
                                    accumulator = tool_call_accumulators.setdefault(
                                        tool_call_index,
                                        {
                                            "argument_segments": [],
                                            "id": None,
                                            "name": None,
                                            "completed": False,
                                        },
                                    )

                                    tool_call_id_value = tool_call_delta.get("id")
                                    if (
                                        isinstance(tool_call_id_value, str)
                                        and tool_call_id_value
                                    ):
                                        accumulator["id"] = tool_call_id_value

                                    function_delta = tool_call_delta.get("function")
                                    delta_name = accumulator.get("name") or ""
                                    if isinstance(function_delta, dict):
                                        new_name = function_delta.get("name")
                                        if (
                                            isinstance(new_name, str)
                                            and new_name.strip()
                                        ):
                                            delta_name = new_name.strip()
                                            accumulator["name"] = delta_name

                                        arguments_segment = function_delta.get(
                                            "arguments"
                                        )
                                        if (
                                            isinstance(arguments_segment, str)
                                            and arguments_segment
                                        ):
                                            segments = accumulator.setdefault(
                                                "argument_segments", []
                                            )
                                            segments.append(arguments_segment)
                                            if (
                                                delta_name
                                                and delta_name
                                                not in tools_not_yield_arguments
                                                and not is_tool_hidden_from_user(
                                                    delta_name
                                                )
                                            ):
                                                tool_delta_payload = {
                                                    "id": accumulator.get("id")
                                                    or f"idx:{tool_call_index}",
                                                    "name": delta_name,
                                                    "delta": arguments_segment,
                                                }
                                                stream_meta = (
                                                    get_stream_tool_event_meta(
                                                        delta_name,
                                                        tool_call_id=accumulator.get(
                                                            "id"
                                                        )
                                                        or f"idx:{tool_call_index}",
                                                    )
                                                )
                                                if stream_meta:
                                                    tool_delta_payload["meta"] = (
                                                        stream_meta
                                                    )
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
                                    if (
                                        delta_name in tools_not_yield_arguments
                                        and not is_tool_hidden_from_user(delta_name)
                                        and not tool_call_event_sent.get(
                                            tool_call_index
                                        )
                                    ):
                                        yield (
                                            json.dumps({"t": "t_c", "d": delta_name})
                                            + "\n"
                                        )
                                        tool_call_event_sent[tool_call_index] = True

                            # Responses emits one arguments-done event per call.
                            # Execute only after the terminal response has supplied
                            # every parallel call; executing on a parseable delta
                            # can otherwise run the first call too early.
                            if finish_reason == "tool_calls":
                                parallel_subagent_calls: list[dict[str, Any]] = []
                                if (
                                    finish_reason == "tool_calls"
                                    and "subagent" in tool_list
                                    and max_calls >= 2
                                ):
                                    pending_accumulators = [
                                        (tool_call_index, accumulator)
                                        for tool_call_index, accumulator in list(
                                            tool_call_accumulators.items()
                                        )
                                        if not accumulator.get("completed")
                                    ]
                                    if len(pending_accumulators) > 1:
                                        for (
                                            tool_call_index,
                                            accumulator,
                                        ) in pending_accumulators:
                                            name = accumulator.get("name")
                                            tool_call_id = (
                                                accumulator.get("id")
                                                or f"idx:{tool_call_index}"
                                            )
                                            arguments_segments = (
                                                accumulator.get("argument_segments")
                                                or []
                                            )
                                            arguments_str = "".join(
                                                arguments_segments
                                            ).strip()
                                            if arguments_str:
                                                try:
                                                    arguments = json.loads(
                                                        arguments_str
                                                    )
                                                except json.JSONDecodeError:
                                                    parallel_subagent_calls = []
                                                    break
                                            else:
                                                arguments = {}
                                            if name != "subagent" or not isinstance(
                                                arguments, dict
                                            ):
                                                parallel_subagent_calls = []
                                                break
                                            parallel_subagent_calls.append(
                                                {
                                                    "tool_call_index": tool_call_index,
                                                    "accumulator": accumulator,
                                                    "name": name,
                                                    "tool_call_id": tool_call_id,
                                                    "response_item_id": accumulator.get(
                                                        "response_item_id"
                                                    )
                                                    or tool_call_id,
                                                    "arguments": arguments,
                                                    "arguments_for_message": arguments_str
                                                    if arguments_str
                                                    else "{}",
                                                    "serialized_arguments": json.dumps(
                                                        arguments,
                                                        ensure_ascii=False,
                                                        separators=(",", ":"),
                                                    ),
                                                }
                                            )

                                if parallel_subagent_calls and max_calls >= len(
                                    parallel_subagent_calls
                                ):
                                    if thinking_time_already_started:
                                        meta_last_thinking_time = (
                                            datetime.now(timezone.utc)
                                            - thinking_time_start
                                        ).total_seconds()
                                        yield (
                                            json.dumps(
                                                {
                                                    "t": "r_f",
                                                    "d": meta_last_thinking_time,
                                                }
                                            )
                                            + "\n"
                                        )
                                        meta_total_thinking_time += (
                                            meta_last_thinking_time
                                        )
                                        thinking_time_already_started = False
                                        thinking_time_start = None

                                    tool_meta = (
                                        {"thinking_time": meta_last_thinking_time}
                                        if meta_last_thinking_time
                                        else None
                                    )
                                    meta = {}
                                    if tool_meta:
                                        meta.update(tool_meta)
                                    _consume_pending_reasoning_for_tool_continuation()
                                    if content:
                                        if not temp_request_flag:
                                            messages_to_save.append(
                                                {
                                                    "type": "content",
                                                    "content": content,
                                                    "meta": meta,
                                                }
                                            )
                                            last_message_type = "content"
                                        content = ""
                                    content_generation_start = None
                                    function_call = True
                                    max_calls -= len(parallel_subagent_calls)

                                    for call in parallel_subagent_calls:
                                        call["accumulator"]["completed"] = True
                                        name = call["name"]
                                        tool_call_id = call["tool_call_id"]
                                        arguments = call["arguments"]
                                        formatted_history.append(
                                            {
                                                "type": "function_call",
                                                "id": call["response_item_id"],
                                                "call_id": tool_call_id,
                                                "name": name,
                                                "arguments": call[
                                                    "arguments_for_message"
                                                ],
                                            }
                                        )
                                        hidden_from_user = (
                                            should_hide_tool_call_from_user(
                                                name, arguments
                                            )
                                        )
                                        hide_tool_arguments = (
                                            name in tools_not_yield_arguments
                                        )
                                        if not hidden_from_user:
                                            tool_event_descriptor = {
                                                "id": tool_call_id,
                                                "name": name,
                                            }
                                            if not hide_tool_arguments:
                                                tool_event_descriptor["args"] = (
                                                    arguments
                                                )
                                            stream_meta = get_stream_tool_event_meta(
                                                name,
                                                tool_call_id=tool_call_id,
                                            )
                                            if stream_meta:
                                                tool_event_descriptor["meta"] = (
                                                    stream_meta
                                                )
                                            tool_event_payload = {
                                                "t": "t_c",
                                                "d": tool_event_descriptor,
                                            }
                                            if not hide_tool_arguments:
                                                tool_event_payload["c"] = arguments
                                            yield json.dumps(tool_event_payload) + "\n"

                                        if (
                                            not temp_request_flag
                                            and not hidden_from_user
                                        ):
                                            messages_to_save.append(
                                                build_tool_call_block(
                                                    name,
                                                    call["serialized_arguments"],
                                                    tool_call_id=tool_call_id,
                                                    extra_meta={
                                                        "openrouter_item_id": call[
                                                            "response_item_id"
                                                        ]
                                                    },
                                                )
                                            )
                                            last_message_type = "tool_call"

                                    parallel_gen = resolve_parallel_subagent_tool_calls(
                                        [
                                            {"arguments": call["arguments"]}
                                            for call in parallel_subagent_calls
                                        ],
                                        user_id=user_id,
                                        group_id=None,
                                        project_id=project_id,
                                        model_settings=settings,
                                        byok=byok,
                                        chat_id=chat_id,
                                        chat_history=chat_history,
                                        generation_id=generation_id,
                                        user_role=str(user_role or "").strip().lower(),
                                    )
                                    try:
                                        while True:
                                            parallel_item = next(parallel_gen)
                                            if parallel_item is not None:
                                                yield parallel_item
                                    except StopIteration as parallel_done:
                                        parallel_results = parallel_done.value or []

                                    for call, parallel_result in zip(
                                        parallel_subagent_calls, parallel_results
                                    ):
                                        name = call["name"]
                                        tool_call_id = call["tool_call_id"]
                                        serialized_arguments = call[
                                            "serialized_arguments"
                                        ]
                                        hidden_from_user = (
                                            should_hide_tool_call_from_user(
                                                name, call.get("arguments")
                                            )
                                        )
                                        helper_payload = (
                                            parallel_result.get("helper_payload") or {}
                                        )
                                        tool_error_message = parallel_result.get(
                                            "tool_error_message"
                                        )
                                        provider_identifier = (
                                            meta_provider
                                            or (
                                                byok.get("provider_id")
                                                if isinstance(byok, dict)
                                                else getattr(
                                                    db_model, "provider_id", None
                                                )
                                            )
                                            or "openrouter"
                                        )
                                        tool_stat_kwargs = {
                                            "db": db,
                                            "tool_name": name or "unknown",
                                            "model_id": target_model_id,
                                            "model_name": model_name,
                                            "provider": provider_identifier,
                                            "user_id": user_id,
                                            "is_byok": bool(byok),
                                        }

                                        if tool_error_message:
                                            user_friendly_tool_error = "An error occurred during tool execution."
                                            result = {"error": user_friendly_tool_error}
                                            tool_content = user_friendly_tool_error
                                            tool_documents = []
                                            tool_images = []
                                            tool_videos = []
                                            tool_audios = []
                                            tool_youtube = []
                                            tool_webpages = []
                                            formatted_history.append(
                                                {
                                                    "type": "function_call_output",
                                                    "call_id": tool_call_id,
                                                    "output": str(
                                                        user_friendly_tool_error
                                                        or tool_error_message
                                                    ),
                                                }
                                            )
                                            try:
                                                create_tool_call_statistic(
                                                    success=False,
                                                    error_message=tool_error_message,
                                                    **tool_stat_kwargs,
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            result = helper_payload.get("result")
                                            tool_content = (
                                                helper_payload.get("content")
                                                or result
                                                or "success"
                                            )
                                            tool_documents = (
                                                helper_payload.get("documents") or []
                                            )
                                            tool_images = (
                                                helper_payload.get("images") or []
                                            )
                                            tool_videos = (
                                                helper_payload.get("videos") or []
                                            )
                                            tool_audios = (
                                                helper_payload.get("audios") or []
                                            )
                                            tool_youtube = (
                                                helper_payload.get("youtube") or []
                                            )
                                            tool_webpages = (
                                                helper_payload.get("webpages") or []
                                            )
                                            formatted_history.append(
                                                {
                                                    "type": "function_call_output",
                                                    "call_id": tool_call_id,
                                                    "output": str(result),
                                                }
                                            )
                                            try:
                                                create_tool_call_statistic(
                                                    success=True,
                                                    error_message=None,
                                                    meta=helper_payload.get(
                                                        "tool_meta"
                                                    ),
                                                    **tool_stat_kwargs,
                                                )
                                            except Exception:
                                                pass

                                        widget_data = helper_payload.get("widget")
                                        if (
                                            not temp_request_flag
                                            and not hidden_from_user
                                        ):
                                            tool_label = (
                                                f"{name}({serialized_arguments})"
                                                if name
                                                else "tool"
                                            )
                                            content_str = stringify_tool_result_content_for_persistence(
                                                name,
                                                result
                                                if result not in (None, "")
                                                else tool_content,
                                                widget_data,
                                            )
                                            persist_files_in_file_block = (
                                                should_persist_files_in_file_block(name)
                                            )
                                            tool_result_block = {
                                                "type": "tool_call_result",
                                                "content": content_str or "success",
                                                "tool_name": tool_label,
                                                "meta": {"tool_call_id": tool_call_id},
                                            }
                                            if (
                                                tool_documents
                                                and not persist_files_in_file_block
                                            ):
                                                tool_result_block["documents"] = list(
                                                    dict.fromkeys(tool_documents)
                                                )
                                            if (
                                                tool_images
                                                and not persist_files_in_file_block
                                            ):
                                                tool_result_block["images"] = list(
                                                    dict.fromkeys(tool_images)
                                                )
                                            if (
                                                tool_videos
                                                and not persist_files_in_file_block
                                            ):
                                                tool_result_block["videos"] = list(
                                                    dict.fromkeys(tool_videos)
                                                )
                                            if (
                                                tool_audios
                                                and not persist_files_in_file_block
                                            ):
                                                tool_result_block["audios"] = list(
                                                    dict.fromkeys(tool_audios)
                                                )
                                            if tool_youtube:
                                                tool_result_block["youtube"] = (
                                                    tool_youtube
                                                )
                                            tool_meta = helper_payload.get(
                                                "tool_meta"
                                            ) or helper_payload.get("meta")
                                            if (
                                                isinstance(tool_meta, dict)
                                                and tool_meta
                                            ):
                                                tool_result_block.setdefault(
                                                    "meta", {}
                                                ).update(tool_meta)
                                            messages_to_save.append(tool_result_block)
                                            if persist_files_in_file_block:
                                                file_block = build_tool_file_block(
                                                    tool_name=name,
                                                    tool_label=tool_label,
                                                    documents=tool_documents,
                                                    images=tool_images,
                                                    videos=tool_videos,
                                                    audios=tool_audios,
                                                )
                                                if file_block:
                                                    messages_to_save.append(file_block)
                                            last_message_type = "tool_call_result"
                                            if widget_data and widget_data.get("html"):
                                                messages_to_save.append(
                                                    {
                                                        "type": "widget",
                                                        "content": widget_data.get(
                                                            "html"
                                                        ),
                                                        "meta": build_widget_block_meta(
                                                            widget_data,
                                                            tool_name=name,
                                                            tool_call_id=tool_call_id,
                                                        ),
                                                    }
                                                )
                                                last_message_type = "widget"
                                    continue

                                for tool_call_index, accumulator in list(
                                    tool_call_accumulators.items()
                                ):
                                    if accumulator.get("completed"):
                                        continue

                                    name = accumulator.get("name")
                                    tool_call_id = (
                                        accumulator.get("id")
                                        or f"idx:{tool_call_index}"
                                    )
                                    arguments_segments = (
                                        accumulator.get("argument_segments") or []
                                    )
                                    arguments_str = "".join(arguments_segments).strip()

                                    arguments_ready = False
                                    arguments = None
                                    if arguments_str:
                                        try:
                                            arguments = json.loads(arguments_str)
                                            arguments_ready = True
                                        except json.JSONDecodeError:
                                            arguments = None
                                    else:
                                        # No arguments provided by the model
                                        arguments = {}
                                        arguments_ready = finish_reason == "tool_calls"

                                    if not (name and arguments_ready):
                                        continue
                                    if thinking_time_already_started:
                                        meta_last_thinking_time = (
                                            datetime.now(timezone.utc)
                                            - thinking_time_start
                                        ).total_seconds()
                                        yield (
                                            json.dumps(
                                                {
                                                    "t": "r_f",
                                                    "d": meta_last_thinking_time,
                                                }
                                            )
                                            + "\n"
                                        )
                                        meta_total_thinking_time += (
                                            meta_last_thinking_time
                                        )
                                        thinking_time_already_started = False
                                        thinking_time_start = None

                                    accumulator["completed"] = True

                                    # Ensure assistant history uses a JSON string for arguments
                                    arguments_for_message = (
                                        arguments_str if arguments_str else "{}"
                                    )

                                    # Reset content generation start time for accurate tokens_per_second after tool calls
                                    completion_content_generation_start = (
                                        content_generation_start
                                    )
                                    content_generation_start = None
                                    max_calls -= 1
                                    tool_meta = (
                                        {"thinking_time": meta_last_thinking_time}
                                        if meta_last_thinking_time
                                        else None
                                    )
                                    meta = {}
                                    if tool_meta:
                                        meta.update(tool_meta)
                                    _consume_pending_reasoning_for_tool_continuation()
                                    if content:
                                        if not temp_request_flag:
                                            messages_to_save.append(
                                                {
                                                    "type": "content",
                                                    "content": content,
                                                    "meta": meta,
                                                }
                                            )
                                            last_message_type = "content"
                                        content = ""
                                    # Append assistant tool call turn so the next request includes it
                                    formatted_history.append(
                                        {
                                            "type": "function_call",
                                            "id": accumulator.get("response_item_id")
                                            or tool_call_id,
                                            "call_id": tool_call_id,
                                            "name": name,
                                            "arguments": arguments_for_message,
                                        }
                                    )
                                    content = ""

                                    function_call = True
                                    serialized_arguments = (
                                        json.dumps(
                                            arguments,
                                            ensure_ascii=False,
                                            separators=(",", ":"),
                                        )
                                        if isinstance(arguments, dict)
                                        else str(arguments)
                                    )
                                    # Here is a valid tool call
                                    hide_tool_arguments = (
                                        name in tools_not_yield_arguments
                                    )
                                    hidden_from_user = should_hide_tool_call_from_user(
                                        name, arguments
                                    )
                                    if not hidden_from_user:
                                        tool_event_descriptor = {
                                            "id": tool_call_id,
                                            "name": name,
                                        }
                                        if not hide_tool_arguments:
                                            tool_event_descriptor["args"] = arguments
                                        stream_meta = get_stream_tool_event_meta(
                                            name,
                                            tool_call_id=tool_call_id,
                                        )
                                        if stream_meta:
                                            tool_event_descriptor["meta"] = stream_meta
                                        tool_event_payload = {
                                            "t": "t_c",
                                            "d": tool_event_descriptor,
                                        }
                                        if not hide_tool_arguments:
                                            tool_event_payload["c"] = arguments
                                        yield json.dumps(tool_event_payload) + "\n"

                                    # Add tool_call block to messages_to_save
                                    if not temp_request_flag and not hidden_from_user:
                                        messages_to_save.append(
                                            build_tool_call_block(
                                                name,
                                                serialized_arguments,
                                                tool_call_id=tool_call_id,
                                                extra_meta={
                                                    "openrouter_item_id": accumulator.get(
                                                        "response_item_id"
                                                    )
                                                    or tool_call_id,
                                                },
                                            )
                                        )
                                        last_message_type = "tool_call"

                                    result = None
                                    tool_content = None
                                    tool_documents: list | None = None
                                    tool_images: list | None = None
                                    tool_videos: list | None = None
                                    tool_audios: list | None = None
                                    tool_youtube: list | None = None

                                    provider_identifier = (
                                        meta_provider
                                        or (
                                            byok.get("provider_id")
                                            if isinstance(byok, dict)
                                            else getattr(db_model, "provider_id", None)
                                        )
                                        or "openrouter"
                                    )
                                    tool_stat_kwargs = {
                                        "db": db,
                                        "tool_name": name or "unknown",
                                        "model_id": target_model_id,
                                        "model_name": model_name,
                                        "provider": provider_identifier,
                                        "user_id": user_id,
                                        "is_byok": bool(byok),
                                    }

                                    if name in tool_list:
                                        helper_payload: dict[str, Any] = {}
                                        helper_gen = None
                                        tool_error_message: str | None = None
                                        tool_error_response: (
                                            ToolErrorResponse | None
                                        ) = None
                                        try:
                                            helper_gen = resolve_tool_call(
                                                db,
                                                name,
                                                arguments,
                                                user_id,
                                                None,
                                                project_id,
                                                model_settings=settings,
                                                byok=byok,
                                                chat_id=chat_id,
                                                chat_history=chat_history,
                                                generation_id=generation_id,
                                                user_role=str(user_role or "")
                                                .strip()
                                                .lower(),
                                                tool_call_id=tool_call_id,
                                            )
                                        except Exception as tool_exc:
                                            tool_error_message = str(tool_exc)
                                            tool_error_response = (
                                                tool_error_tracker.record(
                                                    name, tool_exc
                                                )
                                            )
                                            logger.exception(
                                                "Tool %s failed to start: %s",
                                                name,
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
                                                tool_error_response = (
                                                    tool_error_tracker.record(
                                                        name, tool_exc
                                                    )
                                                )
                                                logger.exception(
                                                    "Tool %s raised during execution: %s",
                                                    name,
                                                    tool_exc,
                                                )

                                        # A completed long-running tool is
                                        # activity. Reset the stream timeout so
                                        # the assistant follow-up is requested.
                                        last_activity = time.monotonic()

                                        if tool_error_message:
                                            if tool_error_response is None:
                                                tool_error_response = (
                                                    tool_error_tracker.record(
                                                        name,
                                                        RuntimeError(
                                                            tool_error_message
                                                        ),
                                                    )
                                                )
                                            result = tool_error_response.result_payload
                                            tool_content = (
                                                tool_error_response.model_output
                                            )
                                            if tool_error_response.stop_tool_calls:
                                                suppress_tools = True
                                            tool_documents = []
                                            tool_images = []
                                            tool_videos = []
                                            tool_audios = []
                                            tool_youtube = []
                                            tool_webpages = []
                                            formatted_history.append(
                                                {
                                                    "type": "function_call_output",
                                                    "call_id": tool_call_id,
                                                    "output": tool_content,
                                                }
                                            )
                                            try:
                                                create_tool_call_statistic(
                                                    success=False,
                                                    error_message=tool_error_message,
                                                    meta=tool_error_response.statistic_meta,
                                                    **tool_stat_kwargs,
                                                )
                                            except Exception:
                                                pass
                                        else:
                                            if name == "web_search":
                                                web_search = True

                                            result = helper_payload.get("result")
                                            tool_content = (
                                                helper_payload.get("content")
                                                or result
                                                or "success"
                                            )
                                            tool_documents = (
                                                helper_payload.get("documents") or []
                                            )
                                            tool_images = (
                                                helper_payload.get("images") or []
                                            )
                                            tool_videos = (
                                                helper_payload.get("videos") or []
                                            )
                                            tool_audios = (
                                                helper_payload.get("audios") or []
                                            )
                                            tool_youtube = (
                                                helper_payload.get("youtube") or []
                                            )
                                            tool_webpages = (
                                                helper_payload.get("webpages") or []
                                            )

                                            formatted_history.append(
                                                {
                                                    "type": "function_call_output",
                                                    "call_id": tool_call_id,
                                                    "output": str(result),
                                                }
                                            )

                                            try:
                                                create_tool_call_statistic(
                                                    success=True,
                                                    error_message=None,
                                                    meta=helper_payload.get(
                                                        "tool_meta"
                                                    ),
                                                    **tool_stat_kwargs,
                                                )
                                            except Exception:
                                                pass
                                    else:
                                        is_admin = is_admin_role(user_role)
                                        error_message = (
                                            "Tool not allowed"
                                            if is_admin
                                            else "An error occurred during generation. Please try again."
                                        )
                                        yield (
                                            json.dumps({"t": "e", "d": error_message})
                                            + "\n"
                                        )
                                        try:
                                            create_tool_call_statistic(
                                                success=False,
                                                error_message="Tool not allowed",
                                                **tool_stat_kwargs,
                                            )
                                        except Exception:
                                            pass
                                        continue

                                    widget_data = helper_payload.get("widget")

                                    if not temp_request_flag and not hidden_from_user:
                                        tool_label = (
                                            f"{name}({serialized_arguments})"
                                            if name
                                            else "tool"
                                        )
                                        content_str = stringify_tool_result_content_for_persistence(
                                            name,
                                            result
                                            if result not in (None, "")
                                            else tool_content,
                                            widget_data,
                                        )
                                        persist_files_in_file_block = (
                                            should_persist_files_in_file_block(name)
                                        )
                                        # Add tool_call_result block to messages_to_save
                                        tool_result_block = {
                                            "type": "tool_call_result",
                                            "content": content_str or "success",
                                            "tool_name": tool_label,
                                            "meta": {"tool_call_id": tool_call_id},
                                        }
                                        if (
                                            tool_documents
                                            and not persist_files_in_file_block
                                        ):
                                            tool_result_block["documents"] = list(
                                                dict.fromkeys(tool_documents)
                                            )
                                        if (
                                            tool_images
                                            and not persist_files_in_file_block
                                        ):
                                            tool_result_block["images"] = list(
                                                dict.fromkeys(tool_images)
                                            )
                                        if (
                                            tool_videos
                                            and not persist_files_in_file_block
                                        ):
                                            tool_result_block["videos"] = list(
                                                dict.fromkeys(tool_videos)
                                            )
                                        if (
                                            tool_audios
                                            and not persist_files_in_file_block
                                        ):
                                            tool_result_block["audios"] = list(
                                                dict.fromkeys(tool_audios)
                                            )
                                        if tool_youtube:
                                            tool_result_block["youtube"] = tool_youtube
                                        if name == "web_search" and tool_webpages:
                                            citations = build_web_search_citations(
                                                tool_webpages
                                            )
                                            if citations:
                                                tool_result_block.setdefault(
                                                    "meta", {}
                                                )["citations"] = citations
                                        tool_meta = helper_payload.get(
                                            "tool_meta"
                                        ) or helper_payload.get("meta")
                                        if isinstance(tool_meta, dict) and tool_meta:
                                            tool_result_block.setdefault(
                                                "meta", {}
                                            ).update(tool_meta)
                                        messages_to_save.append(tool_result_block)
                                        if persist_files_in_file_block:
                                            file_block = build_tool_file_block(
                                                tool_name=name,
                                                tool_label=tool_label,
                                                documents=tool_documents,
                                                images=tool_images,
                                                videos=tool_videos,
                                                audios=tool_audios,
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
                                                        tool_name=name,
                                                        tool_call_id=tool_call_id,
                                                    ),
                                                }
                                            )
                                            last_message_type = "widget"
                            if (
                                terminal_completed or terminal_incomplete_reason
                            ) and not function_call:
                                if (
                                    thinking_time_already_started
                                    and thinking_time_start is not None
                                ):
                                    meta_last_thinking_time = (
                                        datetime.now(timezone.utc) - thinking_time_start
                                    ).total_seconds()
                                    yield (
                                        json.dumps(
                                            {"t": "r_f", "d": meta_last_thinking_time}
                                        )
                                        + "\n"
                                    )
                                    meta_total_thinking_time += meta_last_thinking_time
                                    thinking_time_already_started = False
                                    thinking_time_start = None

                                end_time = datetime.now(timezone.utc)
                                tokens_per_second = None
                                if (
                                    content_generation_start is not None
                                    and output_tokens
                                ):
                                    elapsed = max(
                                        (
                                            end_time - content_generation_start
                                        ).total_seconds(),
                                        0.0,
                                    )
                                    if elapsed > 0:
                                        tokens_per_second = output_tokens / elapsed
                                meta_tokens_per_second = (
                                    round(tokens_per_second, 2)
                                    if tokens_per_second is not None
                                    else None
                                )
                                meta = _build_completion_meta(
                                    model_name=model_name,
                                    meta_provider=meta_provider,
                                    input_tokens=input_tokens,
                                    input_tokens_audio=input_tokens_audio,
                                    input_tokens_video=input_tokens_video,
                                    input_tokens_cached=input_tokens_cached,
                                    output_tokens=output_tokens,
                                    output_tokens_image=output_tokens_image,
                                    reasoning_tokens=reasoning_tokens,
                                    total_tokens=total_tokens,
                                    meta_request_count=meta_request_count,
                                    meta_last_thinking_time=meta_last_thinking_time,
                                    meta_total_thinking_time=meta_total_thinking_time,
                                    tokens_per_second=meta_tokens_per_second,
                                    meta_time_to_first_token=meta_time_to_first_token,
                                    meta_is_byok=meta_is_byok,
                                    total_costs=total_costs,
                                    upstream_inference_cost=upstream_inference_cost,
                                    upstream_inference_prompt_cost=upstream_inference_prompt_cost,
                                    upstream_inference_completions_cost=upstream_inference_completions_cost,
                                    assistant_metadata=assistant_metadata,
                                )
                                if terminal_incomplete_reason:
                                    meta["status"] = "incomplete"
                                    meta["incomplete_reason"] = (
                                        terminal_incomplete_reason
                                    )
                                yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                                if not temp_request_flag:
                                    saved_assistant_id = (
                                        _finalize_pending_assistant_message(meta)
                                    )
                                    if saved_assistant_id:
                                        yield (
                                            json.dumps(
                                                {"t": "a_id", "d": saved_assistant_id}
                                            )
                                            + "\n"
                                        )
                                meta_generation_success = True
                                break
                        except json.JSONDecodeError:
                            pass
                    try:
                        if time.monotonic() - last_activity > inactivity_timeout_sec:
                            yield from emit_timeout_event("inactivity_no_chunks")
                            return
                    except Exception:
                        pass
            except requests.exceptions.ReadTimeout as exc:
                yield from emit_timeout_event("read_timeout", str(exc))
                return
            except requests.RequestException as exc:
                meta_generation_error = True
                meta_error_type = exc.__class__.__name__
                meta_error_message = str(exc)
                yield (
                    json.dumps(
                        {
                            "t": "e",
                            "d": "An error occurred during the generation. Please try again later or choose a different model.",
                        }
                    )
                    + "\n"
                )
                return
            except Exception as exc:
                meta_generation_error = True
                meta_error_type = exc.__class__.__name__
                meta_error_message = str(exc)
                yield (
                    json.dumps(
                        {
                            "t": "e",
                            "d": "An error occurred during the generation. Please try again later or choose a different model.",
                        }
                    )
                    + "\n"
                )
                return
    except Exception as e:
        meta_generation_error = True
        meta_error_type = e.__class__.__name__
        meta_error_message = str(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if (
            not assistant_message_saved
            and not temp_request_flag
            and (messages_to_save or thinking or content)
        ):
            pending_meta = {
                "status": "error" if meta_generation_error else "incomplete",
                "timestamp": format_meta_timestamp(),
            }
            if meta_error_type:
                pending_meta["error_type"] = meta_error_type
            if meta_error_message:
                pending_meta["error_message"] = meta_error_message
            _finalize_pending_assistant_message(pending_meta)

        _record_generation_stat()
