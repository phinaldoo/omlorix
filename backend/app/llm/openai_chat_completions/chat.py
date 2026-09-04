"""OpenAI-compatible Chat Completions orchestration and streaming.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

from app.llm.generation.engine import chat_adapter, ProviderCall, stream_tool_call

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai_chat_completions import utils as _compat_source
from app.llm.helper import sanitize_tool_call_arguments_for_persistence
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION

_COMPAT_DEPENDENCIES = {
    "openai_chat_completions_chat": (
        "APIConnectionError",
        "Any",
        "AuthenticationError",
        "BadRequestError",
        "OpenAI",
        "OpenAIModelSettings",
        "ToolErrorTracker",
        "_apply_openai_chat_completion_simple_settings",
        "_build_native_websearch_user_context",
        "_merge_openai_request_options",
        "_parse_openai_exception",
        "_record_openai_generation_stat",
        "_resolve_openai_client_context",
        "add_cached_input_token_meta",
        "append_system_instruction_sections",
        "build_stream_tool_event_meta",
        "build_tool_call_block",
        "build_tool_file_block",
        "build_web_search_citations",
        "build_widget_block_meta",
        "calculate_openai_token_costs",
        "collect_tool_result_citations",
        "copy",
        "create_tool_call_statistic",
        "datetime",
        "format_meta_timestamp",
        "get_default_system_instruction",
        "interruptible_provider_stream",
        "is_admin_role",
        "is_tool_hidden_from_user",
        "json",
        "logger",
        "merge_openai_cost_breakdown",
        "merge_settings",
        "normalize_unsupported_file_ids",
        "reformat_chat_history",
        "resolve_model_metadata_id",
        "resolve_parallel_subagent_tool_calls",
        "resolve_tool_call",
        "should_hide_tool_call_from_user",
        "should_persist_files_in_file_block",
        "stringify_tool_result_content_for_persistence",
        "time",
        "timezone",
        "tools_not_yield_arguments",
        "uuid",
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
    "APIConnectionError",
    "Any",
    "AuthenticationError",
    "BadRequestError",
    "OpenAI",
    "OpenAIModelSettings",
    "ToolErrorTracker",
    "_apply_openai_chat_completion_simple_settings",
    "_build_native_websearch_user_context",
    "_merge_openai_request_options",
    "_parse_openai_exception",
    "_record_openai_generation_stat",
    "_resolve_openai_client_context",
    "add_cached_input_token_meta",
    "append_system_instruction_sections",
    "build_stream_tool_event_meta",
    "build_tool_call_block",
    "build_tool_file_block",
    "build_web_search_citations",
    "build_widget_block_meta",
    "calculate_openai_token_costs",
    "collect_tool_result_citations",
    "copy",
    "create_tool_call_statistic",
    "datetime",
    "format_meta_timestamp",
    "get_default_system_instruction",
    "interruptible_provider_stream",
    "is_admin_role",
    "is_tool_hidden_from_user",
    "json",
    "logger",
    "merge_openai_cost_breakdown",
    "merge_settings",
    "normalize_unsupported_file_ids",
    "reformat_chat_history",
    "resolve_model_metadata_id",
    "resolve_parallel_subagent_tool_calls",
    "resolve_tool_call",
    "should_hide_tool_call_from_user",
    "should_persist_files_in_file_block",
    "stringify_tool_result_content_for_persistence",
    "time",
    "timezone",
    "tools_not_yield_arguments",
    "uuid",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


@chat_adapter
def _impl_openai_chat_completions_chat(
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
    openai_provider_type: str = "openai_chat_completions",
    engine=None,
):
    assistant_metadata = (
        assistant_metadata if isinstance(assistant_metadata, dict) else {}
    )
    try:
        create_chat_message = engine.persist_message

        # -------------------
        # Client
        # -------------------
        client_context = _resolve_openai_client_context(
            db,
            getattr(db_model, "provider_id", None),
            byok,
            openai_provider_type=openai_provider_type,
        )
        client_kwargs = client_context["client_kwargs"]
        request_options = client_context["request_options"]
        selected_provider_identifier = client_context.get("selected_provider_id")
        client = OpenAI(**client_kwargs)

        # -------------------
        # Settings
        # -------------------
        settings, merged_tools = merge_settings(
            db_model.settings if db_model else None,
            settings_override,
            getattr(OpenAIModelSettings, "model_fields", None),
            getattr(db_model, "tools", None) if db_model else None,
        )

        # -------------------
        # Chat History
        # -------------------
        input_formats_allowed = settings.get("input_formats", None)
        use_group_context = settings.get("use_group_context")
        use_project_context = settings.get("use_project_context")
        reformatted_chat_history = reformat_chat_history(
            chat_history,
            user_id,
            db,
            include_tool_content=True,
            project_id=project_id,
            max_image_count=settings.get("max_image_count"),
            max_document_count=settings.get("max_document_count"),
            max_audio_count=settings.get("max_audio_count"),
            input_formats_allowed=input_formats_allowed,
            use_group_context=use_group_context,
            use_project_context=use_project_context,
            is_chat_completions_api=True,
            note_ids=note_ids,
            reference_parts=reference_parts,
            chat_reference_context=chat_reference_context,
            image_detail=settings.get("image_detail"),
        )
        engine.context.prefix_count = reformatted_chat_history.get(
            "context_prefix_count", 0
        )
        engine.context.prefix_sections = (
            reformatted_chat_history.get("context_sections", [])
            if isinstance(reformatted_chat_history, dict)
            else []
        )
        formatted_history = reformatted_chat_history.get("formatted", [])
        unsupported_file_ids = normalize_unsupported_file_ids(
            reformatted_chat_history.get("unsupported_file_ids")
        )
        if unsupported_file_ids:
            yield json.dumps({"t": "uf", "file_ids": unsupported_file_ids}) + "\n"
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
            capabilities = byok.get("capabilities", [])
        else:
            capabilities = db_model.capabilities
        tools_flag = False
        if "tools" in capabilities:
            tools_flag = True
        tool_list: list[str] = []
        tool_schemas: list[dict] = []
        tools: list[str] = []
        if tools_flag:
            if byok and isinstance(byok.get("tools"), (list, tuple, set, dict, str)):
                raw_tools = byok.get("tools")
            else:
                raw_tools = merged_tools
            from app.tools.utils import resolve_enabled_tools

            resolved_tools = resolve_enabled_tools(
                raw_tools,
                db=db,
                model_settings=settings,
                user_id=user_id,
                byok=byok,
                project_id=project_id,
            )
            tool_list = resolved_tools.get("tool_list", []) or []
            settings["_runtime_enabled_tools"] = [
                *list(tool_list),
                *(["mcp"] if resolved_tools.get("mcp_requested") else []),
            ]
            settings["_runtime_origin_model_id"] = (
                "" if byok else str(getattr(db_model, "id", "") or "")
            )
            temp_tool_schemas = resolved_tools.get("tool_schemas", []) or []
            if resolved_tools.get("mcp_requested"):
                try:
                    from app.mcp.utils import build_mcp_provider_bundle

                    mcp_provider = (
                        byok.get("provider")
                        if isinstance(byok, dict)
                        and isinstance(byok.get("provider"), str)
                        else getattr(db_model, "provider", None)
                        or "openai_chat_completions"
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
                    temp_tool_schemas.extend(
                        mcp_bundle.get("bridge_tool_schemas", []) or []
                    )
                except Exception:
                    logger.exception(
                        "Failed to build MCP tools for OpenAI Chat Completions"
                    )
            tool_schemas = []
            for tool in temp_tool_schemas:
                tool_schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name"),
                            "description": tool.get("description"),
                            "parameters": tool.get("parameters"),
                        },
                    }
                )
            native_websearch_enabled = settings.get("native_websearch")
            if isinstance(native_websearch_enabled, str):
                native_websearch_enabled = native_websearch_enabled.strip().lower() in {
                    "true",
                    "1",
                    "yes",
                    "on",
                }
            else:
                native_websearch_enabled = native_websearch_enabled is True

            # Check if native web search is enabled
            if native_websearch_enabled:
                disabled_tools = {"web_search"}
                sanitized_schemas: list[dict] = []
                for schema in tool_schemas or []:
                    if not isinstance(schema, dict):
                        sanitized_schemas.append(schema)
                        continue
                    schema_name = schema.get("name") or schema.get("function", {}).get(
                        "name"
                    )
                    if schema_name in disabled_tools:
                        continue
                    sanitized_schemas.append(schema)
                tool_schemas = sanitized_schemas
                has_native_websearch = any(
                    isinstance(schema, dict) and schema.get("type") == "web_search"
                    for schema in tool_schemas
                )
                if not has_native_websearch:
                    tool_schemas.append({"type": "web_search"})

                websearch_context = _build_native_websearch_user_context(db, user_id)
                if websearch_context:
                    for schema in tool_schemas:
                        if (
                            isinstance(schema, dict)
                            and schema.get("type") == "web_search"
                        ):
                            for key, value in websearch_context.items():
                                schema[key] = copy.deepcopy(value)
            tools = list(tool_list)

        def _target_model_id():
            return "byok" if byok else getattr(db_model, "id", None)

        def _serialize_tool_output(payload):
            if payload in (None, ""):
                return "success"
            if isinstance(payload, (dict, list)):
                try:
                    return json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    )
                except TypeError:
                    return str(payload)
            return str(payload)

        def _unique_or_none(items):
            if not items:
                return None
            try:
                return list(dict.fromkeys(items))
            except TypeError:
                return items

        def _persist_assistant_content_if_needed(
            text: str, system_instruction_value: str, meta: dict | None = None
        ):
            nonlocal messages_to_save, last_message_type
            if temp_request_flag or not text:
                return
            target_id = _target_model_id()
            if target_id is None:
                return
            # Accumulate content block
            messages_to_save.append(
                {"type": "content", "content": text, "meta": meta or {}}
            )
            last_message_type = "content"

        def _persist_tool_message(
            content_value: str,
            tool_name_value: str | None,
            tool_label: str,
            system_instruction_value: str,
            widget_data: dict | None = None,
            result_value: Any = None,
            *,
            tool_call_id: str | None = None,
            documents=None,
            images=None,
            videos=None,
            audios=None,
            youtube=None,
            webpages=None,
            tool_meta=None,
        ):
            nonlocal messages_to_save, last_message_type
            if temp_request_flag:
                return
            if is_tool_hidden_from_user(tool_name_value):
                return
            target_id = _target_model_id()
            if target_id is None:
                return
            persist_files_in_file_block = should_persist_files_in_file_block(
                tool_name_value
            )
            persisted_content_value = stringify_tool_result_content_for_persistence(
                tool_name_value,
                result_value
                if result_value not in (None, "")
                else (content_value or "success"),
                widget_data,
            )
            # Add tool_call_result block
            tool_result_block = {
                "type": "tool_call_result",
                "content": persisted_content_value or "success",
                "tool_name": tool_label,
            }
            if tool_call_id:
                tool_result_block["meta"] = {"tool_call_id": str(tool_call_id)}
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
            if tool_name_value == "web_search" and webpages:
                citations = build_web_search_citations(webpages)
                if citations:
                    tool_result_block["meta"] = {"citations": citations}
            if isinstance(tool_meta, dict) and tool_meta:
                tool_result_block.setdefault("meta", {}).update(tool_meta)
            messages_to_save.append(tool_result_block)
            if persist_files_in_file_block:
                file_block = build_tool_file_block(
                    tool_name=tool_name_value,
                    tool_label=tool_label,
                    documents=documents,
                    images=images,
                    videos=videos,
                    audios=audios,
                )
                if file_block:
                    messages_to_save.append(file_block)
            last_message_type = "tool_call_result"

        # -------------------
        # Variables for the while loop
        # -------------------
        reasoning = ""
        content = ""

        function_call = True
        max_calls = MAX_TOOL_CALLS_PER_GENERATION
        suppress_tools = False
        tool_error_tracker = ToolErrorTracker()
        content_generation_start = None
        request_start_time = None
        content_generation_duration = 0.0
        reasoning_time_start = None

        # -------------------
        # Meta data variables
        # -------------------
        meta_input_tokens = 0
        meta_cached_input_tokens = 0
        meta_cache_write_tokens = 0
        meta_output_tokens = 0
        meta_reasoning_tokens = 0
        meta_total_tokens = 0
        meta_input_audio_tokens = 0
        meta_output_accepted_prediction_tokens = 0
        meta_output_rejected_prediction_tokens = 0
        meta_output_audio_tokens = 0
        meta_last_reasoning_time = 0
        meta_total_reasoning_time = 0.0

        meta_request_count = 0
        meta_tokens_per_second = None

        # Chat Completions-compatible providers may omit ``chunk.model`` on
        # every streamed chunk, so start with the requested model identifier.
        meta_model_id = resolve_model_metadata_id(
            byok.get("model_name") if isinstance(byok, dict) else None,
            getattr(db_model, "model_name", None),
        )
        meta_service_tier = ""
        meta_finish_reason = ""
        meta_refusal = ""
        meta_time_to_first_token = None

        meta_function_call_name = ""
        meta_function_call_arguments = ""
        start_time = datetime.now(timezone.utc)
        meta_generation_success = False
        meta_generation_error = False
        meta_error_status_code = 0
        meta_error_message = ""
        meta_error_type = ""
        model_identifier: str | None = None
        provider_identifier = (
            byok.get("provider_id")
            if isinstance(byok, dict) and byok.get("provider_id")
            else getattr(db_model, "provider_id", None) or "openai"
        )

        # Track native websearch tool calls for cost calculation
        meta_native_websearch_tool_calls_count = 0
        meta_request_costs: dict[str, float] = {}

        def _record_generation_stat():
            try:
                meta_payload = {
                    "generation_time": round(
                        (datetime.now(timezone.utc) - start_time).total_seconds(), 2
                    ),
                    "request_count": meta_request_count,
                    "input_tokens": meta_input_tokens,
                    "input_token_cached": meta_cached_input_tokens,
                    "cache_write_tokens": meta_cache_write_tokens,
                    "output_tokens": meta_output_tokens,
                    "reasoning_tokens": meta_reasoning_tokens,
                    "total_tokens": meta_total_tokens,
                    "time_to_first_token": meta_time_to_first_token,
                    "tokens_per_second": meta_tokens_per_second,
                    "service_tier": meta_service_tier or "standard",
                }
                final_costs = dict(meta_request_costs)
                websearch_costs = calculate_openai_token_costs(
                    model_name=model_identifier
                    or getattr(db_model, "model_name", None)
                    or "openai",
                    service_tier=meta_service_tier or "standard",
                    input_tokens=0,
                    cached_input_tokens=0,
                    cache_write_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    native_websearch_tool_calls_count=meta_native_websearch_tool_calls_count,
                )
                merge_openai_cost_breakdown(final_costs, websearch_costs)
                meta_payload.update(final_costs)
                _record_openai_generation_stat(
                    db,
                    model_name=model_identifier
                    or getattr(db_model, "model_name", None)
                    or "openai",
                    model_id=getattr(db_model, "id", None)
                    or model_identifier
                    or "openai",
                    provider="openai",
                    provider_id=provider_identifier,
                    category="chat",
                    meta=meta_payload,
                    success=meta_generation_success,
                    error=meta_generation_error,
                    error_status_code=meta_error_status_code,
                    error_message=meta_error_message,
                    error_type=meta_error_type,
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
        target_model_id = "byok" if byok else db_model.id
        assistant_message_saved = False

        def _finalize_pending_assistant_message(meta_override: dict | None = None):
            nonlocal \
                messages_to_save, \
                last_message_type, \
                content, \
                reasoning, \
                assistant_message_saved
            if temp_request_flag or assistant_message_saved:
                return None
            if target_model_id is None:
                return None

            if reasoning:
                reasoning_meta = (
                    {"reasoning_time": meta_last_reasoning_time}
                    if meta_last_reasoning_time
                    else {}
                )
                if meta_override:
                    reasoning_meta.update(meta_override)
                messages_to_save.append(
                    {
                        "type": "reasoning",
                        "content": reasoning,
                        "meta": reasoning_meta,
                    }
                )
                reasoning = ""
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
                target_model_id,
                "assistant",
                reference_id=reference_id,
                content=messages_to_save,
                retry_count=retry_count,
            )
            assistant_message_saved = True
            return assistant_msg.id if assistant_msg else None

        inactivity_timeout_sec = 100
        last_activity = time.monotonic()

        while function_call and max_calls > 0:
            function_call = False
            last_activity = time.monotonic()
            meta_time_to_first_token = None
            request_start_time = None
            tool_call_accumulator: dict[int, dict[str, str]] = {}
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

            # -------------------
            # System Instruction
            # -------------------
            custom_system_instruction = settings.get("system_instruction")
            system_instruction = get_default_system_instruction(
                db,
                tools,
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

            if (
                byok
                and isinstance(byok.get("model_name"), str)
                and byok.get("model_name").strip()
            ):
                model_name = byok.get("model_name").strip()
            else:
                model_name = getattr(db_model, "model_name", None)
            model_identifier = model_name

            messages = [
                {"role": "system", "content": system_instruction}
            ] + formatted_history

            # -------------------
            # OpenAI Request
            # -------------------
            request_kwargs = {
                "model": model_name,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            _apply_openai_chat_completion_simple_settings(
                request_kwargs,
                settings,
                model_name=model_name,
                provider_id=selected_provider_identifier,
                user_id=user_id,
            )
            if tools_flag and tool_schemas and not suppress_tools:
                request_kwargs["tools"] = tool_schemas
            try:
                request_start_time = datetime.now(timezone.utc)
                response = yield ProviderCall(
                    client.chat.completions.create,
                    {**_merge_openai_request_options(request_kwargs, request_options)},
                    settings,
                    "openai_chat_completions",
                    args=(),
                )
            except Exception as exc:
                meta_generation_error = True
                meta_error_message = str(exc)
                meta_error_type = exc.__class__.__name__
                raise

            for chunk in engine.events(response, generation_id, stream_factory=interruptible_provider_stream):
                last_activity = time.monotonic()
                # Check for cancellation for this generation and exit gracefully if set
                try:
                    if generation_id:
                        from app.chats.streaming import cancel_registry

                        if cancel_registry.is_cancelled(generation_id):
                            # Persist any partial assistant content accumulated so far
                            if (content or reasoning or messages_to_save) and (
                                not temp_request_flag
                            ):
                                cancellation_meta = {"status": "cancelled"}
                                additional_meta = {
                                    "model": model_name,
                                    "request_count": meta_request_count,
                                    "input_tokens": meta_input_tokens,
                                    "input_token_cached": meta_cached_input_tokens,
                                    "output_tokens": meta_output_tokens,
                                    "reasoning_tokens": meta_reasoning_tokens,
                                    "total_tokens": meta_total_tokens,
                                }
                                for key, value in additional_meta.items():
                                    if value not in (None, 0, "", [], {}):
                                        cancellation_meta[key] = value
                                cancellation_meta["timestamp"] = format_meta_timestamp()
                                meta_generation_success = True
                                if content:
                                    last_message_type = "content"
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
                                    {"t": "d", "d": "c", "c": {"status": "cancelled"}}
                                )
                                + "\n"
                            )
                            return
                except Exception:
                    # Best-effort cancel check; do not break streaming on errors in cancel registry
                    pass

                choice = chunk.choices[0] if chunk.choices else None
                delta = choice.delta if choice and choice.delta else None

                if delta and getattr(delta, "reasoning_content", None):
                    if content_generation_start is None:
                        content_generation_start = datetime.now(timezone.utc)
                    if last_message_type != "reasoning":
                        reasoning_time_start = datetime.now(timezone.utc)
                        last_message_type = "reasoning"
                    reasoning += delta.reasoning_content
                    yield json.dumps({"t": "r", "d": delta.reasoning_content}) + "\n"

                if delta and delta.content:
                    if content_generation_start is None:
                        content_generation_start = datetime.now(timezone.utc)
                    if (
                        meta_time_to_first_token is None
                        and request_start_time is not None
                    ):
                        meta_time_to_first_token = (
                            datetime.now(timezone.utc) - request_start_time
                        ).total_seconds()
                    if last_message_type != "content":
                        if reasoning_time_start is not None:
                            elapsed = max(
                                (
                                    datetime.now(timezone.utc) - reasoning_time_start
                                ).total_seconds(),
                                0.0,
                            )
                            if elapsed > 0:
                                meta_last_reasoning_time = elapsed
                                meta_total_reasoning_time += elapsed
                                yield json.dumps({"t": "r_f", "d": elapsed}) + "\n"
                            reasoning_time_start = None
                        last_message_type = "content"
                    content += delta.content
                    yield json.dumps({"t": "c", "d": delta.content}) + "\n"
                if choice and choice.finish_reason:
                    meta_finish_reason = choice.finish_reason
                    if choice.finish_reason == "tool_calls":
                        function_call = True
                if delta and delta.refusal:
                    meta_refusal = delta.refusal
                if delta and getattr(delta, "tool_calls", None):
                    for tool_delta in delta.tool_calls:
                        if isinstance(tool_delta, dict):
                            idx = tool_delta.get("index", 0)
                            tool_id = tool_delta.get("id")
                            function_delta = tool_delta.get("function", {}) or {}
                            tool_name = function_delta.get("name")
                            arguments_chunk = function_delta.get("arguments")
                        else:
                            idx = getattr(tool_delta, "index", 0)
                            tool_id = getattr(tool_delta, "id", None)
                            function_delta = getattr(tool_delta, "function", None)
                            tool_name = (
                                getattr(function_delta, "name", None)
                                if function_delta
                                else None
                            )
                            arguments_chunk = (
                                getattr(function_delta, "arguments", None)
                                if function_delta
                                else None
                            )
                        idx = idx or 0
                        entry = tool_call_accumulator.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tool_id:
                            entry["id"] = tool_id
                        if tool_name:
                            entry["name"] = tool_name
                        if arguments_chunk:
                            entry["arguments"] = (
                                entry.get("arguments", "") + arguments_chunk
                            )
                            delta_tool_name = entry.get("name") or tool_name
                            if (
                                delta_tool_name
                                and delta_tool_name not in tools_not_yield_arguments
                                and not is_tool_hidden_from_user(delta_tool_name)
                            ):
                                tool_delta_payload = {
                                    "id": entry.get("id") or f"idx:{idx}",
                                    "name": delta_tool_name,
                                    "delta": arguments_chunk,
                                }
                                stream_meta = get_stream_tool_event_meta(
                                    delta_tool_name,
                                    tool_call_id=entry.get("id") or f"idx:{idx}",
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
                if chunk.service_tier:
                    meta_service_tier = chunk.service_tier
                if chunk.usage:
                    request_output_tokens = int(chunk.usage.completion_tokens or 0)
                    request_input_tokens = int(chunk.usage.prompt_tokens or 0)
                    request_total_tokens = int(chunk.usage.total_tokens or 0)
                    request_reasoning_tokens = 0
                    request_cached_tokens = 0
                    request_cache_write_tokens = 0

                    meta_output_tokens += request_output_tokens
                    meta_input_tokens += request_input_tokens
                    meta_total_tokens += request_total_tokens
                    if chunk.usage.completion_tokens_details:
                        if chunk.usage.completion_tokens_details.accepted_prediction_tokens:
                            meta_output_accepted_prediction_tokens += chunk.usage.completion_tokens_details.accepted_prediction_tokens
                        if chunk.usage.completion_tokens_details.rejected_prediction_tokens:
                            meta_output_rejected_prediction_tokens += chunk.usage.completion_tokens_details.rejected_prediction_tokens
                        if chunk.usage.completion_tokens_details.audio_tokens:
                            meta_output_audio_tokens += (
                                chunk.usage.completion_tokens_details.audio_tokens
                            )
                        if chunk.usage.completion_tokens_details.reasoning_tokens:
                            request_reasoning_tokens = int(
                                chunk.usage.completion_tokens_details.reasoning_tokens
                                or 0
                            )
                            meta_reasoning_tokens += request_reasoning_tokens
                    if chunk.usage.prompt_tokens_details:
                        if chunk.usage.prompt_tokens_details.audio_tokens:
                            meta_input_audio_tokens += (
                                chunk.usage.prompt_tokens_details.audio_tokens
                            )
                        if chunk.usage.prompt_tokens_details.cached_tokens:
                            request_cached_tokens = int(
                                chunk.usage.prompt_tokens_details.cached_tokens or 0
                            )
                            meta_cached_input_tokens += request_cached_tokens
                        request_cache_write_tokens = int(
                            getattr(
                                chunk.usage.prompt_tokens_details,
                                "cache_write_tokens",
                                0,
                            )
                            or 0
                        )
                        meta_cache_write_tokens += request_cache_write_tokens

                    request_costs = calculate_openai_token_costs(
                        model_name=model_identifier
                        or getattr(db_model, "model_name", None)
                        or "openai",
                        service_tier=meta_service_tier or "standard",
                        input_tokens=request_input_tokens,
                        cached_input_tokens=request_cached_tokens,
                        cache_write_tokens=request_cache_write_tokens,
                        output_tokens=request_output_tokens,
                        reasoning_tokens=request_reasoning_tokens,
                        native_websearch_tool_calls_count=0,
                    )
                    merge_openai_cost_breakdown(
                        meta_request_costs,
                        request_costs,
                    )
                meta_model_id = resolve_model_metadata_id(
                    getattr(chunk, "model", None),
                    meta_model_id,
                    model_name,
                )

            if function_call:
                if not tool_call_accumulator:
                    logger.warning(
                        "[OpenAI Chat Completions] Function call requested but no tool data returned."
                    )
                    function_call = False
                    continue

                assistant_tool_calls: list[dict] = []
                tool_call_entries: list[dict[str, str]] = []
                for idx in sorted(tool_call_accumulator.keys()):
                    accumulator_entry = tool_call_accumulator[idx]
                    function_name = accumulator_entry.get("name")
                    if not function_name:
                        continue
                    call_id = accumulator_entry.get("id") or f"call_{uuid.uuid4().hex}"
                    arguments_str = accumulator_entry.get("arguments") or "{}"
                    assistant_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": function_name,
                                "arguments": sanitize_tool_call_arguments_for_persistence(
                                    function_name,
                                    arguments_str,
                                ),
                            },
                        }
                    )
                    tool_call_entries.append(
                        {
                            "id": call_id,
                            "name": function_name,
                            "arguments": arguments_str,
                        }
                    )

                if not assistant_tool_calls:
                    logger.warning(
                        "[OpenAI Chat Completions] No valid tool calls extracted."
                    )
                    function_call = False
                    continue

                formatted_history.append(
                    {
                        "role": "assistant",
                        "tool_calls": assistant_tool_calls,
                    }
                )

                if reasoning and not temp_request_flag:
                    reasoning_meta = (
                        {"reasoning_time": meta_last_reasoning_time}
                        if meta_last_reasoning_time
                        else {}
                    )
                    messages_to_save.append(
                        {
                            "type": "reasoning",
                            "content": reasoning,
                            "meta": reasoning_meta,
                        }
                    )
                    reasoning = ""
                    last_message_type = "reasoning"

                _persist_assistant_content_if_needed(content, system_instruction)
                content = ""

                parsed_parallel_subagent_entries: list[dict[str, Any]] = []
                if (
                    len(tool_call_entries) > 1
                    and "subagent" in tool_list
                    and all(
                        entry.get("name") == "subagent" for entry in tool_call_entries
                    )
                ):
                    for entry in tool_call_entries:
                        arguments = entry["arguments"] or "{}"
                        parsed_args = {}
                        if isinstance(arguments, str) and arguments.strip():
                            try:
                                parsed_args = json.loads(arguments)
                            except Exception:
                                parsed_args = {"_raw": arguments}
                        if not isinstance(parsed_args, dict):
                            parsed_args = {"_raw": arguments}
                        parsed_parallel_subagent_entries.append(
                            {
                                "call_id": entry["id"],
                                "function_name": entry["name"],
                                "arguments": arguments,
                                "parsed_args": parsed_args,
                                "history_arguments": arguments
                                if arguments.strip()
                                else json.dumps(parsed_args, ensure_ascii=False),
                            }
                        )

                if parsed_parallel_subagent_entries:
                    entries_to_run = parsed_parallel_subagent_entries[
                        : max(0, max_calls)
                    ]
                    excess_entries = parsed_parallel_subagent_entries[
                        len(entries_to_run) :
                    ]
                    max_calls -= len(entries_to_run)

                    for parsed_entry in entries_to_run:
                        call_id = parsed_entry["call_id"]
                        function_name = parsed_entry["function_name"]
                        arguments = parsed_entry["arguments"]
                        history_arguments = parsed_entry["history_arguments"]
                        hide_tool_arguments = function_name in tools_not_yield_arguments
                        hide_tool_call_from_user = should_hide_tool_call_from_user(
                            function_name, arguments
                        )
                        if not hide_tool_call_from_user:
                            tool_event_payload = {
                                "t": "t_c",
                                "d": {"id": call_id, "name": function_name},
                            }
                            if not hide_tool_arguments:
                                tool_event_payload["d"]["args"] = arguments
                            stream_meta = get_stream_tool_event_meta(
                                function_name,
                                tool_call_id=call_id,
                            )
                            if stream_meta:
                                tool_event_payload["d"]["meta"] = stream_meta
                            yield json.dumps(tool_event_payload) + "\n"

                        if not temp_request_flag and not hide_tool_call_from_user:
                            messages_to_save.append(
                                build_tool_call_block(
                                    function_name,
                                    history_arguments,
                                    tool_call_id=call_id,
                                )
                            )
                            last_message_type = "tool_call"

                    def _parallel_error_result(error_message: str) -> dict[str, Any]:
                        """Return the normal parallel result envelope with an error payload."""
                        return {
                            "helper_payload": {},
                            "tool_error_message": error_message
                            or "Subagent tool call failed.",
                        }

                    parallel_results: list[dict[str, Any]] = []
                    if entries_to_run:
                        try:
                            parallel_gen = resolve_parallel_subagent_tool_calls(
                                [
                                    {"arguments": entry["parsed_args"]}
                                    for entry in entries_to_run
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
                            while True:
                                parallel_item = next(parallel_gen)
                                if parallel_item is not None:
                                    yield parallel_item
                        except StopIteration as parallel_done:
                            parallel_results = parallel_done.value or []
                        except Exception as parallel_exc:
                            logger.exception(
                                "Parallel subagent batch failed: %s", parallel_exc
                            )
                            parallel_results = [
                                _parallel_error_result(str(parallel_exc))
                                for _entry in entries_to_run
                            ]

                        # A completed subagent batch is active work. Reset the
                        # provider inactivity clock before the follow-up turn.
                        last_activity = time.monotonic()

                    if len(parallel_results) < len(entries_to_run):
                        missing_count = len(entries_to_run) - len(parallel_results)
                        parallel_results.extend(
                            _parallel_error_result(
                                "Subagent tool call did not return a result."
                            )
                            for _index in range(missing_count)
                        )

                    excess_results = [
                        _parallel_error_result(
                            "Maximum tool call limit reached for this response."
                        )
                        for _entry in excess_entries
                    ]

                    for parsed_entry, parallel_result in zip(
                        entries_to_run + excess_entries,
                        parallel_results + excess_results,
                    ):
                        call_id = parsed_entry["call_id"]
                        function_name = parsed_entry["function_name"]
                        history_arguments = parsed_entry["history_arguments"]
                        helper_payload = parallel_result.get("helper_payload") or {}
                        tool_error_message = parallel_result.get("tool_error_message")
                        tool_stat_kwargs = {
                            "db": db,
                            "tool_name": function_name or "unknown",
                            "model_id": _target_model_id(),
                            "model_name": model_identifier,
                            "provider": provider_identifier,
                            "user_id": user_id,
                            "is_byok": bool(byok),
                        }
                        try:
                            create_tool_call_statistic(
                                success=not bool(tool_error_message),
                                error_message=tool_error_message,
                                meta=helper_payload.get("tool_meta")
                                if not tool_error_message
                                else None,
                                **tool_stat_kwargs,
                            )
                        except Exception:
                            pass

                        if tool_error_message:
                            result_payload = {"error": tool_error_message}
                            tool_documents = []
                            tool_images = []
                            tool_videos = []
                            tool_audios = []
                            tool_youtube = []
                        else:
                            result_payload = helper_payload.get("content")
                            if not result_payload:
                                result_payload = helper_payload.get("result")
                            if not result_payload:
                                webpages_payload = helper_payload.get("webpages") or []
                                result_payload = webpages_payload or "success"
                            tool_documents = helper_payload.get("documents") or []
                            tool_images = helper_payload.get("images") or []
                            tool_videos = helper_payload.get("videos") or []
                            tool_audios = helper_payload.get("audios") or []
                            tool_youtube = helper_payload.get("youtube") or []

                        tool_content = _serialize_tool_output(result_payload)
                        formatted_history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": tool_content,
                            }
                        )

                        tool_label = f"{function_name}()" if function_name else "tool"
                        _persist_tool_message(
                            tool_content,
                            function_name,
                            tool_label,
                            system_instruction,
                            helper_payload.get("widget"),
                            tool_call_id=call_id,
                            result_value=helper_payload.get("result"),
                            documents=_unique_or_none(tool_documents),
                            images=_unique_or_none(tool_images),
                            videos=_unique_or_none(tool_videos),
                            audios=_unique_or_none(tool_audios),
                            youtube=tool_youtube or None,
                            webpages=helper_payload.get("webpages") or None,
                            tool_meta=helper_payload.get("tool_meta")
                            or helper_payload.get("meta"),
                        )
                        widget_data = helper_payload.get("widget")
                        if widget_data and widget_data.get("html"):
                            if not temp_request_flag:
                                messages_to_save.append(
                                    {
                                        "type": "widget",
                                        "content": widget_data.get("html"),
                                        "meta": build_widget_block_meta(
                                            widget_data,
                                            tool_name=function_name,
                                            tool_call_id=call_id,
                                        ),
                                    }
                                )
                                last_message_type = "widget"
                    continue

                for entry in tool_call_entries:
                    if max_calls <= 0:
                        function_call = False
                        break
                    max_calls -= 1

                    call_id = entry["id"]
                    function_name = entry["name"]
                    arguments = entry["arguments"] or "{}"
                    parsed_args = {}
                    if isinstance(arguments, str) and arguments.strip():
                        try:
                            parsed_args = json.loads(arguments)
                        except Exception:
                            parsed_args = {"_raw": arguments}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {"_raw": arguments}

                    history_arguments = (
                        arguments
                        if arguments.strip()
                        else json.dumps(parsed_args, ensure_ascii=False)
                    )

                    hide_tool_arguments = function_name in tools_not_yield_arguments
                    hide_tool_call_from_user = should_hide_tool_call_from_user(
                        function_name, parsed_args
                    )
                    if not hide_tool_call_from_user:
                        tool_event_payload = {
                            "t": "t_c",
                            "d": {"id": call_id, "name": function_name},
                        }
                        if not hide_tool_arguments:
                            tool_event_payload["d"]["args"] = arguments
                        stream_meta = get_stream_tool_event_meta(
                            function_name,
                            tool_call_id=call_id,
                        )
                        if stream_meta:
                            tool_event_payload["d"]["meta"] = stream_meta
                        yield json.dumps(tool_event_payload) + "\n"

                    # Add tool_call block to messages_to_save
                    if not temp_request_flag and not hide_tool_call_from_user:
                        messages_to_save.append(
                            build_tool_call_block(
                                function_name,
                                history_arguments,
                                tool_call_id=call_id,
                            )
                        )
                        last_message_type = "tool_call"

                    tool_stat_kwargs = {
                        "db": db,
                        "tool_name": function_name or "unknown",
                        "model_id": _target_model_id(),
                        "model_name": model_identifier,
                        "provider": provider_identifier,
                        "user_id": user_id,
                        "is_byok": bool(byok),
                    }
                    tool_stat_logged = False

                    def _log_tool_stat(
                        success: bool,
                        error_message: str | None,
                        meta: dict | None = None,
                    ):
                        nonlocal tool_stat_logged
                        if tool_stat_logged:
                            return
                        try:
                            create_tool_call_statistic(
                                success=success,
                                error_message=error_message,
                                meta=meta,
                                **tool_stat_kwargs,
                            )
                        except Exception:
                            pass
                        else:
                            tool_stat_logged = True

                    if function_name not in tool_list:
                        error_output = json.dumps(
                            {
                                "error": f"Tool '{function_name}' is not allowed or not available"
                            },
                            ensure_ascii=False,
                        )
                        formatted_history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": error_output,
                            }
                        )
                        _persist_tool_message(
                            error_output,
                            function_name,
                            f"{function_name}()" if function_name else "tool",
                            system_instruction,
                            tool_call_id=call_id,
                        )
                        _log_tool_stat(False, "Tool not allowed")
                        continue

                    if function_name == "web_search":
                        web_search = True

                    helper_payload: dict[str, Any] = {}
                    helper_gen = None
                    tool_error_message: str | None = None
                    tool_error_response: ToolErrorResponse | None = None
                    try:
                        helper_gen = stream_tool_call(resolve_tool_call,
                            db,
                            function_name,
                            parsed_args,
                            user_id,
                            None,
                            project_id,
                            model_settings=settings,
                            byok=byok,
                            chat_id=chat_id,
                            chat_history=chat_history,
                            generation_id=generation_id,
                            user_role=str(user_role or "").strip().lower(),
                            tool_call_id=call_id,
                        )
                    except Exception as tool_exc:
                        tool_error_message = str(tool_exc)
                        tool_error_response = tool_error_tracker.record(
                            function_name, tool_exc
                        )
                        logger.exception(
                            "Tool %s failed to start: %s", function_name, tool_exc
                        )

                    if helper_gen and tool_error_message is None:
                        try:
                            helper_payload = yield from helper_gen
                        except Exception as tool_exc:
                            tool_error_message = str(tool_exc)
                            tool_error_response = tool_error_tracker.record(
                                function_name, tool_exc
                            )
                            logger.exception(
                                "Tool %s raised during execution: %s",
                                function_name,
                                tool_exc,
                            )

                    # Tool execution can legitimately outlive the provider
                    # inactivity window. Refresh it before the next model turn.
                    last_activity = time.monotonic()

                    if tool_error_message:
                        if tool_error_response is None:
                            tool_error_response = tool_error_tracker.record(
                                function_name,
                                RuntimeError(tool_error_message),
                            )
                        result_payload = tool_error_response.result_payload
                        if tool_error_response.stop_tool_calls:
                            suppress_tools = True
                        tool_documents = []
                        tool_images = []
                        tool_videos = []
                        tool_audios = []
                        tool_youtube = []
                    else:
                        result_payload = helper_payload.get("content")
                        if not result_payload:
                            result_payload = helper_payload.get("result")
                        if not result_payload:
                            webpages_payload = helper_payload.get("webpages") or []
                            result_payload = webpages_payload or "success"

                        tool_documents = helper_payload.get("documents") or []
                        tool_images = helper_payload.get("images") or []
                        tool_videos = helper_payload.get("videos") or []
                        tool_audios = helper_payload.get("audios") or []
                        tool_youtube = helper_payload.get("youtube") or []

                    tool_content = _serialize_tool_output(result_payload)
                    _log_tool_stat(
                        not tool_error_message,
                        tool_error_message,
                        meta=(
                            helper_payload.get("tool_meta")
                            if not tool_error_message
                            else tool_error_response.statistic_meta
                        ),
                    )

                    formatted_history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": tool_content,
                        }
                    )

                    tool_documents_unique = _unique_or_none(tool_documents)
                    tool_images_unique = _unique_or_none(tool_images)
                    tool_videos_unique = _unique_or_none(tool_videos)
                    tool_audios_unique = _unique_or_none(tool_audios)
                    tool_youtube_payload = tool_youtube or None
                    tool_webpages = helper_payload.get("webpages") or None
                    tool_label = f"{function_name}()" if function_name else "tool"

                    _persist_tool_message(
                        tool_content,
                        function_name,
                        tool_label,
                        system_instruction,
                        helper_payload.get("widget"),
                        tool_call_id=call_id,
                        result_value=helper_payload.get("result"),
                        documents=tool_documents_unique,
                        images=tool_images_unique,
                        videos=tool_videos_unique,
                        audios=tool_audios_unique,
                        youtube=tool_youtube_payload,
                        webpages=tool_webpages,
                        tool_meta=helper_payload.get("tool_meta")
                        or helper_payload.get("meta"),
                    )
                    widget_data = helper_payload.get("widget")
                    if widget_data and widget_data.get("html"):
                        if not temp_request_flag:
                            messages_to_save.append(
                                {
                                    "type": "widget",
                                    "content": widget_data.get("html"),
                                    "meta": build_widget_block_meta(
                                        widget_data,
                                        tool_name=function_name,
                                        tool_call_id=call_id,
                                    ),
                                }
                            )
                            last_message_type = "widget"

                if max_calls <= 0:
                    function_call = False

                continue

            meta_values = {
                "model": resolve_model_metadata_id(meta_model_id, model_name),
                "input_tokens": meta_input_tokens,
                "output_tokens": meta_output_tokens,
                "reasoning_tokens": meta_reasoning_tokens,
                "total_tokens": meta_total_tokens,
                "request_count": meta_request_count,
                "time_to_first_token": meta_time_to_first_token,
                "reasoning_time": meta_last_reasoning_time,
                "total_reasoning_time": meta_total_reasoning_time,
                "service_tier": meta_service_tier,
            }
            add_cached_input_token_meta(meta_values, meta_cached_input_tokens)
            if meta_cache_write_tokens:
                meta_values["cache_write_tokens"] = meta_cache_write_tokens
            tokens_per_second = None
            if content_generation_start is not None:
                content_generation_duration = max(
                    (
                        datetime.now(timezone.utc) - content_generation_start
                    ).total_seconds(),
                    0.0,
                )
                if content_generation_duration > 0 and meta_output_tokens > 0:
                    tokens_per_second = round(
                        meta_output_tokens / content_generation_duration, 2
                    )
            if tokens_per_second is not None:
                meta_tokens_per_second = tokens_per_second
                meta_values["tokens_per_second"] = tokens_per_second
            all_citations = collect_tool_result_citations(messages_to_save)
            if all_citations:
                meta_values["citations"] = all_citations
            meta_values["timestamp"] = format_meta_timestamp()
            meta = {}
            for key, value in meta_values.items():
                # Check if the value of the variable is not 0 or None before adding it to the meta dictionary
                if value not in (None, 0):
                    meta[key] = value
            for key, value in assistant_metadata.items():
                if value not in (None, "", [], {}):
                    meta[key] = value
            yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
            if not temp_request_flag:
                saved_assistant_id = _finalize_pending_assistant_message(meta)
                if saved_assistant_id:
                    yield json.dumps({"t": "a_id", "d": saved_assistant_id}) + "\n"
            meta_generation_success = True
            return

    except (AuthenticationError, BadRequestError, APIConnectionError) as exc:
        status, message, error_type, _ = _parse_openai_exception(exc)
        meta_generation_error = True
        meta_error_status_code = status or 400
        meta_error_type = error_type or exc.__class__.__name__
        meta_error_message = message
        is_admin = is_admin_role(user_role)
        error_message = (
            message
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
    except Exception as exc:
        logger.error("Failed to generate response: %s", exc)
        meta_generation_error = True
        meta_error_message = str(exc)
        meta_error_type = exc.__class__.__name__
        is_admin = is_admin_role(user_role)
        error_message = (
            str(exc)
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
    finally:
        if (
            not assistant_message_saved
            and not temp_request_flag
            and (messages_to_save or content)
        ):
            pending_meta = {
                "status": "error" if meta_generation_error else "incomplete",
                "timestamp": format_meta_timestamp(),
            }
            add_cached_input_token_meta(pending_meta, meta_cached_input_tokens)
            if meta_cache_write_tokens:
                pending_meta["cache_write_tokens"] = meta_cache_write_tokens
            if meta_error_type:
                pending_meta["error_type"] = meta_error_type
            if meta_error_message:
                pending_meta["error_message"] = meta_error_message
            _finalize_pending_assistant_message(pending_meta)

        _record_generation_stat()

    return True
