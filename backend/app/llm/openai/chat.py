"""OpenAI Responses API chat orchestration and streaming.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source
from app.llm.provider_request import release_db_session_before_provider_io

_COMPAT_DEPENDENCIES = {
    "openai_chat": (
        "APIConnectionError",
        "AuthenticationError",
        "BadRequestError",
        "LLMProvider",
        "OpenAI",
        "OpenAIModelSettings",
        "ToolErrorTracker",
        "XAI_PROVIDER_TYPE",
        "_OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES",
        "_OPENAI_REASONING_SUMMARY_STREAM_EVENT_TYPES",
        "_OPENAI_STREAM_DEBUG_FLAG",
        "_OpenAIFunctionCallAccumulator",
        "_OpenAIReasoningTimer",
        "_OpenAIToolCallBudget",
        "_apply_openai_prompt_cache_settings",
        "_apply_openai_store_setting",
        "_build_native_websearch_user_context",
        "_build_openai_reasoning_payload",
        "_extract_openai_reasoning_summary_text",
        "_find_openai_previous_response",
        "_get_openai_model_caps",
        "_is_openai_tool_search_enabled",
        "_merge_openai_request_options",
        "_openai_chat_history_fingerprint",
        "_openai_continuation_signature",
        "_parse_openai_exception",
        "_prepare_openai_tool_schemas_for_tool_search",
        "_provider_reported_cost_from_usage",
        "_record_openai_generation_stat",
        "_requests_openai_encrypted_reasoning",
        "_resolve_openai_client_context",
        "_resolve_openai_store_setting",
        "_sanitize_openai_reasoning_item",
        "_serialize_openai_tool_search_arguments",
        "_serialize_openai_tool_search_tools",
        "_should_persist_openai_encrypted_reasoning",
        "_should_retry_without_compaction",
        "add_cached_input_token_meta",
        "append_system_instruction_sections",
        "build_stream_tool_event_meta",
        "build_tool_call_block",
        "build_tool_file_block",
        "build_widget_block_meta",
        "calculate_openai_token_costs",
        "copy",
        "create_tool_call_statistic",
        "datetime",
        "exception_metadata",
        "format_meta_timestamp",
        "get_default_system_instruction",
        "get_jwt_material",
        "interruptible_provider_stream",
        "is_admin_role",
        "is_lmstudio_provider_type",
        "is_openai_responses_provider_type",
        "is_tool_hidden_from_user",
        "json",
        "jsonable_encoder",
        "logger",
        "merge_openai_cost_breakdown",
        "merge_settings",
        "normalize_openai_provider_type",
        "normalize_unsupported_file_ids",
        "object_event_metadata",
        "redacted_debug_logging_enabled",
        "reformat_chat_history",
        "resolve_model_metadata_id",
        "should_hide_tool_call_from_user",
        "should_persist_files_in_file_block",
        "stringify_tool_result_content_for_model",
        "stringify_tool_result_content_for_persistence",
        "time",
        "timezone",
        "tools_not_yield_arguments",
        "upload_files",
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
    "AuthenticationError",
    "BadRequestError",
    "LLMProvider",
    "OpenAI",
    "OpenAIModelSettings",
    "ToolErrorTracker",
    "XAI_PROVIDER_TYPE",
    "_OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES",
    "_OPENAI_REASONING_SUMMARY_STREAM_EVENT_TYPES",
    "_OPENAI_STREAM_DEBUG_FLAG",
    "_OpenAIFunctionCallAccumulator",
    "_OpenAIReasoningTimer",
    "_OpenAIToolCallBudget",
    "_apply_openai_prompt_cache_settings",
    "_apply_openai_store_setting",
    "_build_native_websearch_user_context",
    "_build_openai_reasoning_payload",
    "_extract_openai_reasoning_summary_text",
    "_find_openai_previous_response",
    "_get_openai_model_caps",
    "_is_openai_tool_search_enabled",
    "_merge_openai_request_options",
    "_openai_chat_history_fingerprint",
    "_openai_continuation_signature",
    "_parse_openai_exception",
    "_prepare_openai_tool_schemas_for_tool_search",
    "_provider_reported_cost_from_usage",
    "_record_openai_generation_stat",
    "_requests_openai_encrypted_reasoning",
    "_resolve_openai_client_context",
    "_resolve_openai_store_setting",
    "_sanitize_openai_reasoning_item",
    "_serialize_openai_tool_search_arguments",
    "_serialize_openai_tool_search_tools",
    "_should_persist_openai_encrypted_reasoning",
    "_should_retry_without_compaction",
    "add_cached_input_token_meta",
    "append_system_instruction_sections",
    "build_stream_tool_event_meta",
    "build_tool_call_block",
    "build_tool_file_block",
    "build_widget_block_meta",
    "calculate_openai_token_costs",
    "copy",
    "create_tool_call_statistic",
    "datetime",
    "exception_metadata",
    "format_meta_timestamp",
    "get_default_system_instruction",
    "get_jwt_material",
    "interruptible_provider_stream",
    "is_admin_role",
    "is_lmstudio_provider_type",
    "is_openai_responses_provider_type",
    "is_tool_hidden_from_user",
    "json",
    "jsonable_encoder",
    "logger",
    "merge_openai_cost_breakdown",
    "merge_settings",
    "normalize_openai_provider_type",
    "normalize_unsupported_file_ids",
    "object_event_metadata",
    "redacted_debug_logging_enabled",
    "reformat_chat_history",
    "resolve_model_metadata_id",
    "should_hide_tool_call_from_user",
    "should_persist_files_in_file_block",
    "stringify_tool_result_content_for_model",
    "stringify_tool_result_content_for_persistence",
    "time",
    "timezone",
    "tools_not_yield_arguments",
    "upload_files",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


class _OpenAIResponsesStreamProtocolError(RuntimeError):
    """Raised when a Responses stream cannot prove successful completion."""


def _validated_openai_responses_stream(iterable):
    """Yield Responses events while requiring one canonical completion event."""
    completed = False
    for event in iterable:
        event_type = str(getattr(event, "type", "") or "")
        if event_type in {"response.failed", "response.incomplete"}:
            raise _OpenAIResponsesStreamProtocolError(
                f"OpenAI Responses stream reported {event_type}."
            )
        if event_type == "response.completed":
            completed = True
        yield event
    if not completed:
        raise _OpenAIResponsesStreamProtocolError(
            "OpenAI Responses stream ended without response.completed."
        )


def _interruptible_openai_response_stream(response, generation_id, db):
    """Read OpenAI events without retaining clean DB transactions while idle."""

    return interruptible_provider_stream(
        response,
        generation_id,
        before_wait=lambda: release_db_session_before_provider_io(db),
    )


def _impl_openai_chat(
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
    openai_provider_type: str | None = "openai",
    skill_content: str | None = None,
    system_instruction_sections: list[dict[str, str]] | None = None,
    assistant_metadata: dict | None = None,
    note_ids: list[str] | None = None,
    retry_count: int | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    user_role: str | None = None,
) -> Generator[str, None, bool]:
    """OpenAI chat function."""
    model_name: str | None = None
    messages_to_save: list[dict] = []
    last_message_type = "user"
    reasoning = ""
    content = ""
    target_model_id: str | None = None
    assistant_message_saved = False
    assistant_metadata = (
        assistant_metadata if isinstance(assistant_metadata, dict) else {}
    )
    normalized_user_role = str(user_role or "").strip().lower()
    try:
        from app.chats.models import create_chat_message

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
        requested_provider_identifier = client_context.get("requested_provider_id")
        selected_provider_identifier = (
            client_context.get("selected_provider_id") or requested_provider_identifier
        )
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

        requested_model_name = (
            byok.get("model_name").strip()
            if isinstance(byok, dict)
            and isinstance(byok.get("model_name"), str)
            and byok.get("model_name").strip()
            else getattr(db_model, "model_name", None)
        )

        # Stored-response continuation is both cheaper and more faithful than
        # replaying a visible transcript. Use it only for a verified, unchanged
        # branch produced by the same provider/model and only when all-turn
        # reasoning was explicitly requested.
        previous_response_id: str | None = None
        previous_response_message_index: int | None = None
        continuation_signing_secret: str | None = None
        history_for_request = list(chat_history or [])
        requested_store = _resolve_openai_store_setting(settings)
        if (
            settings.get("reasoning_context") == "all_turns"
            # Omitting ``store`` uses the Responses API's stored-response
            # default. Only an explicit false value disables ID continuation.
            and requested_store is not False
            and is_openai_responses_provider_type(openai_provider_type)
        ):
            try:
                continuation_signing_secret, _algorithm = get_jwt_material()
            except Exception:
                # Continuation is only an optimization. If signing material is
                # unavailable, fail closed and replay the visible transcript.
                logger.warning(
                    "OpenAI stored-response continuation disabled because signing material is unavailable",
                    exc_info=True,
                )
            if continuation_signing_secret:
                previous_response_id, previous_response_message_index = (
                    _find_openai_previous_response(
                        history_for_request,
                        model_name=requested_model_name,
                        provider_id=selected_provider_identifier,
                        user_id=user_id,
                        chat_id=chat_id,
                        signing_secret=continuation_signing_secret,
                        provider_type=openai_provider_type,
                    )
                )
            if previous_response_id and previous_response_message_index is not None:
                history_for_request = history_for_request[
                    previous_response_message_index + 1 :
                ]

        # -------------------
        # Chat History
        # -------------------
        input_formats_allowed = settings.get("input_formats", None)
        use_group_context = settings.get("use_group_context")
        use_project_context = settings.get("use_project_context")
        reformatted_chat_history = reformat_chat_history(
            history_for_request,
            user_id,
            db,
            include_tool_content=True,
            project_id=project_id,
            max_image_count=settings.get("max_image_count"),
            max_document_count=settings.get("max_document_count"),
            max_audio_count=settings.get("max_audio_count"),
            input_formats_allowed=input_formats_allowed,
            use_group_context=False if previous_response_id else use_group_context,
            use_project_context=False if previous_response_id else use_project_context,
            note_ids=None if previous_response_id else note_ids,
            reference_parts=None if previous_response_id else reference_parts,
            chat_reference_context=None
            if previous_response_id
            else chat_reference_context,
            image_detail=settings.get("image_detail"),
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
            tool_schemas = copy.deepcopy(resolved_tools.get("tool_schemas", []) or [])
            if resolved_tools.get("mcp_requested"):
                try:
                    from app.mcp.utils import build_mcp_provider_bundle

                    mcp_provider = (
                        openai_provider_type
                        or (
                            byok.get("provider")
                            if isinstance(byok, dict)
                            and isinstance(byok.get("provider"), str)
                            else None
                        )
                        or getattr(db_model, "provider", None)
                        or "openai"
                    )
                    if (
                        isinstance(byok, dict)
                        and mcp_provider == "openai"
                        and str(byok.get("base_url") or "").strip()
                    ):
                        mcp_provider = "openai_responses"
                    mcp_bundle = build_mcp_provider_bundle(
                        db,
                        provider=mcp_provider,
                        user_id=user_id,
                        model_settings=settings,
                    )
                    for name in mcp_bundle.get("bridge_tool_names", []) or []:
                        if name not in tool_list:
                            tool_list.append(name)
                    tool_schemas.extend(mcp_bundle.get("bridge_tool_schemas", []) or [])
                except Exception:
                    logger.exception("Failed to build MCP tools for OpenAI provider")
            normalized_tool_schemas: list[dict] = []
            for schema in tool_schemas or []:
                if not isinstance(schema, dict):
                    normalized_tool_schemas.append(schema)
                    continue
                if schema.get("type"):
                    normalized_tool_schemas.append(schema)
                    continue
                if schema.get("name") and isinstance(schema.get("parameters"), dict):
                    normalized_tool_schemas.append(
                        {
                            "type": "function",
                            "name": schema.get("name"),
                            "description": schema.get("description"),
                            "parameters": schema.get("parameters"),
                        }
                    )
                    continue
                if isinstance(schema.get("function"), dict):
                    function_schema = schema.get("function") or {}
                    normalized_tool_schemas.append(
                        {
                            "type": "function",
                            "name": function_schema.get("name"),
                            "description": function_schema.get("description"),
                            "parameters": function_schema.get("parameters"),
                        }
                    )
                    continue
                normalized_tool_schemas.append(schema)
            tool_schemas = normalized_tool_schemas

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

            tool_search_enabled = _is_openai_tool_search_enabled(
                settings,
                model_name=(
                    byok.get("model_name").strip()
                    if isinstance(byok, dict)
                    and isinstance(byok.get("model_name"), str)
                    and byok.get("model_name").strip()
                    else getattr(db_model, "model_name", None)
                ),
                provider_type=openai_provider_type,
            )
            if tool_search_enabled:
                tool_schemas, _ = _prepare_openai_tool_schemas_for_tool_search(
                    tool_schemas
                )
            tools = list(tool_list)

        # -------------------
        # Variables for the while loop
        # -------------------
        reasoning = ""
        content = ""

        function_call = True
        tool_call_budget = _OpenAIToolCallBudget()
        tool_error_tracker = ToolErrorTracker()
        final_response_without_tools = False
        content_generation_start = None
        request_start_time = None
        content_generation_duration = 0.0
        reasoning_timer = _OpenAIReasoningTimer()

        # -------------------
        # Meta data variables
        # -------------------
        meta_input_tokens = 0
        meta_cached_input_tokens = 0
        meta_cache_write_tokens = 0
        meta_output_tokens = 0
        meta_reasoning_tokens = 0
        meta_total_tokens = 0
        meta_request_count = 0
        # Keep the configured identifier as a durable fallback. OpenAI-compatible
        # proxies can legally complete a response while returning ``model=null``.
        meta_model_id = resolve_model_metadata_id(requested_model_name)
        meta_response_id = ""
        meta_reasoning_effort = ""
        meta_reasoning_mode = ""
        meta_reasoning_context = ""
        meta_prompt_cache_retention = None
        meta_store = None
        meta_service_tier = ""
        meta_citations = []
        meta_time_to_first_token = None

        start_time = datetime.now(timezone.utc)
        meta_generation_success = False
        meta_generation_error = False
        meta_error_status_code = 0
        meta_error_message = ""
        meta_error_type = ""
        meta_tokens_per_second = None
        meta_compaction_enabled = False
        meta_compaction_threshold = None
        meta_compaction_events = 0
        compaction_supported_by_endpoint = True
        model_identifier: str | None = None
        base_url_display: str | None = None
        provider_identifier = (
            byok.get("provider_id")
            if isinstance(byok, dict) and byok.get("provider_id")
            else selected_provider_identifier
            or getattr(db_model, "provider_id", None)
            or "openai"
        )

        # Track native websearch tool calls for cost calculation
        meta_native_websearch_tool_calls_count = 0
        meta_request_costs: dict[str, float] = {}
        meta_provider_reported_cost = 0.0
        meta_provider_cost_ticks = 0
        meta_has_provider_reported_cost = False
        opaque_reasoning_items: list[dict[str, Any]] = []

        provider_name = (
            openai_provider_type
            or (
                byok.get("provider")
                if isinstance(byok, dict) and isinstance(byok.get("provider"), str)
                else None
            )
            or getattr(db_model, "provider", None)
            or "openai"
        )

        target_model_id = "byok" if byok else getattr(db_model, "id", None)

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
                elapsed = reasoning_timer.finish()
                reasoning_meta = {"reasoning_time": elapsed} if elapsed else {}
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

        def add_last_part_to_messages(
            current_type: str, meta: dict | None = None
        ) -> list[str]:
            nonlocal content_generation_start, last_message_type, content, reasoning
            events: list[str] = []
            if content_generation_start is None:
                content_generation_start = datetime.now(timezone.utc)
            previous_message_type = last_message_type
            if temp_request_flag or previous_message_type == "user":
                last_message_type = current_type
                return events

            meta_payload = dict(meta) if meta else {}
            if previous_message_type == "content" and content:
                messages_to_save.append(
                    {
                        "type": "content",
                        "content": content,
                        "meta": dict(meta_payload),
                    }
                )
                content = ""
            elif previous_message_type == "reasoning":
                meta_payload, elapsed = reasoning_timer.finish_metadata(meta_payload)
                if elapsed > 0:
                    events.append(json.dumps({"t": "r_f", "d": elapsed}) + "\n")
                messages_to_save.append(
                    {
                        "type": "reasoning",
                        "content": reasoning if reasoning else "",
                        "meta": meta_payload,
                    }
                )
                reasoning = ""
            # Every boundary, including tools and hosted calls, must transition
            # the state so a later reasoning item starts a fresh segment timer.
            last_message_type = current_type
            return events

        def finalize_response(
            *,
            response_obj,
            tokens_per_second,
            meta_citations,
            messages_to_save,
            temp_request_flag,
            add_last_part_to_messages,
            _finalize_pending_assistant_message,
        ):
            nonlocal meta_tokens_per_second, meta_generation_success
            reasoning_line = (
                getattr(response_obj, "reasoning", None) if response_obj else None
            )
            text_cfg = getattr(response_obj, "text", None) if response_obj else None
            verbosity = getattr(text_cfg, "verbosity", None) if text_cfg else None
            reasoning_effort = (
                getattr(reasoning_line, "effort", None) if reasoning_line else None
            )
            reasoning_mode = (
                getattr(reasoning_line, "mode", None) if reasoning_line else None
            )
            reasoning_context = (
                getattr(reasoning_line, "context", None) if reasoning_line else None
            )

            # Finish a reasoning-only response before constructing final usage
            # metadata or attaching opaque continuation state to its block.
            if not temp_request_flag and last_message_type == "reasoning":
                for flush_event in add_last_part_to_messages("finish"):
                    yield flush_event

            if opaque_reasoning_items and _should_persist_openai_encrypted_reasoning(
                settings,
                meta_store,
            ):
                reasoning_block = next(
                    (
                        block
                        for block in messages_to_save
                        if block.get("type") == "reasoning"
                    ),
                    None,
                )
                if reasoning_block is None:
                    reasoning_block = {"type": "reasoning", "content": "", "meta": {}}
                    messages_to_save.insert(0, reasoning_block)
                reasoning_block.setdefault("meta", {})["openai_reasoning_items"] = (
                    copy.deepcopy(opaque_reasoning_items)
                )

            pending_blocks = copy.deepcopy(messages_to_save)
            if reasoning:
                pending_blocks.append({"type": "reasoning", "content": reasoning})
            if content:
                pending_blocks.append({"type": "content", "content": content})
            continuation_fingerprint = _openai_chat_history_fingerprint(
                [
                    *list(chat_history or []),
                    {"role": "assistant", "content": pending_blocks},
                ]
            )

            all_citations = list(meta_citations) if meta_citations else []
            for msg in messages_to_save:
                if (
                    msg.get("type") == "tool_call_result"
                    and msg.get("meta")
                    and msg["meta"].get("citations")
                ):
                    all_citations.extend(msg["meta"]["citations"])

            meta_tokens_per_second = (
                round(tokens_per_second, 2)
                if tokens_per_second
                else meta_tokens_per_second
            )
            effective_model_id = resolve_model_metadata_id(
                meta_model_id,
                model_identifier,
                requested_model_name,
            )
            meta_values = {
                "model": effective_model_id,
                "input_tokens": meta_input_tokens,
                "output_tokens": meta_output_tokens,
                "reasoning_tokens": meta_reasoning_tokens,
                "total_tokens": meta_total_tokens,
                "request_count": meta_request_count,
                "time_to_first_token": meta_time_to_first_token,
                "total_reasoning_time": reasoning_timer.total_duration,
                "reasoning_time": reasoning_timer.last_duration,
                "tokens_per_second": round(tokens_per_second, 2)
                if tokens_per_second
                else None,
                "reasoning_effort": reasoning_effort or meta_reasoning_effort,
                "reasoning_mode": reasoning_mode or meta_reasoning_mode,
                "reasoning_context": reasoning_context or meta_reasoning_context,
                "verbosity": verbosity,
                "response_id": meta_response_id,
                "prompt_cache_retention": meta_prompt_cache_retention,
                "store": meta_store,
                "citations": all_citations if all_citations else None,
                "service_tier": meta_service_tier,
                "selected_provider_id": selected_provider_identifier,
                "continuation_fingerprint": continuation_fingerprint,
                "compaction_enabled": meta_compaction_enabled,
                "compaction_threshold": meta_compaction_threshold,
                "compaction_events": meta_compaction_events,
                "total_costs": (
                    meta_provider_reported_cost
                    if meta_has_provider_reported_cost
                    else meta_request_costs.get("total_costs")
                ),
                "cost_in_usd_ticks": (
                    meta_provider_cost_ticks
                    if meta_has_provider_reported_cost
                    else None
                ),
                "pricing_source": (
                    "provider_usage" if meta_has_provider_reported_cost else None
                ),
            }
            if meta_response_id and meta_store is True and continuation_signing_secret:
                continuation_signature = _openai_continuation_signature(
                    signing_secret=continuation_signing_secret,
                    user_id=user_id,
                    chat_id=chat_id,
                    response_id=meta_response_id,
                    provider_id=selected_provider_identifier,
                    model_name=effective_model_id,
                    fingerprint=continuation_fingerprint,
                )
                if continuation_signature:
                    meta_values["continuation_signature"] = continuation_signature
            add_cached_input_token_meta(meta_values, meta_cached_input_tokens)
            if meta_cache_write_tokens:
                meta_values["cache_write_tokens"] = meta_cache_write_tokens
            meta_values["timestamp"] = format_meta_timestamp()
            meta = {}
            for key, value in meta_values.items():
                if value not in (None, 0):
                    meta[key] = value
            for key, value in assistant_metadata.items():
                if value not in (None, "", [], {}):
                    meta[key] = value
            yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
            if not temp_request_flag:
                for flush_event in add_last_part_to_messages("finish", meta):
                    yield flush_event
                saved_assistant_id = _finalize_pending_assistant_message(meta)
                if saved_assistant_id:
                    yield json.dumps({"t": "a_id", "d": saved_assistant_id}) + "\n"
            meta_generation_success = True

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
                    "reasoning_time": reasoning_timer.last_duration,
                    "total_reasoning_time": reasoning_timer.total_duration,
                    "time_to_first_token": meta_time_to_first_token,
                    "tokens_per_second": meta_tokens_per_second,
                    "service_tier": meta_service_tier or "standard",
                    "compaction_enabled": meta_compaction_enabled,
                    "compaction_threshold": meta_compaction_threshold,
                    "compaction_events": meta_compaction_events,
                }
                if base_url_display:
                    meta_payload["base_url"] = base_url_display
                if not byok:
                    from app.llm.provider_groups import (
                        build_provider_group_resolution_meta,
                    )

                    selected_provider = None
                    if selected_provider_identifier:
                        selected_provider = (
                            db.query(LLMProvider)
                            .filter(LLMProvider.id == selected_provider_identifier)
                            .first()
                        )
                    meta_payload.update(
                        build_provider_group_resolution_meta(
                            db,
                            requested_provider_identifier,
                            selected_provider,
                        )
                    )
                # Token rates, including the long-context threshold, have
                # already been applied to each request. Hosted search charges
                # are independent per tool call and can be added once here.
                final_costs = dict(meta_request_costs)
                websearch_costs = calculate_openai_token_costs(
                    model_name=model_identifier
                    or getattr(db_model, "model_name", None)
                    or "openai",
                    provider_type=openai_provider_type,
                    service_tier=meta_service_tier or "standard",
                    input_tokens=0,
                    cached_input_tokens=0,
                    cache_write_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    native_websearch_tool_calls_count=meta_native_websearch_tool_calls_count,
                )
                merge_openai_cost_breakdown(final_costs, websearch_costs)
                if meta_has_provider_reported_cost:
                    # xAI's exact billed total includes prompt-cache discounts,
                    # priority processing, and every server-side tool call.  It
                    # therefore supersedes the catalog estimate for the total.
                    final_costs["total_costs"] = meta_provider_reported_cost
                    meta_payload["cost_in_usd_ticks"] = meta_provider_cost_ticks
                    meta_payload["pricing_source"] = "provider_usage"
                meta_payload.update(final_costs)
                _record_openai_generation_stat(
                    db,
                    model_name=model_identifier
                    or getattr(db_model, "model_name", None)
                    or "openai",
                    model_id=getattr(db_model, "id", None)
                    or model_identifier
                    or "openai",
                    provider=provider_name,
                    provider_id=selected_provider_identifier or provider_identifier,
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

        inactivity_timeout_sec = 100
        last_activity = time.monotonic()

        while function_call and (
            not tool_call_budget.exhausted or final_response_without_tools
        ):
            # Once the 200-call budget is exhausted, permit exactly one final
            # provider round without tool schemas so the model can summarize
            # the admitted results and finish the assistant response cleanly.
            allow_tools_for_request = not tool_call_budget.exhausted
            final_response_without_tools = False
            function_call = False
            function_calls = []
            function_call_accumulator = _OpenAIFunctionCallAccumulator()
            function_call_event_sent_map: dict[str, bool] = {}
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

            reasoning_already_added = False
            last_activity = time.monotonic()
            meta_time_to_first_token = None
            request_start_time = None

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
            provider_name = (
                openai_provider_type
                or (
                    byok.get("provider")
                    if isinstance(byok, dict) and isinstance(byok.get("provider"), str)
                    else None
                )
                or getattr(db_model, "provider", None)
                or "openai"
            )
            if base_url_display is None:
                base_url_display = client_kwargs.get("base_url")
                if not base_url_display and isinstance(byok, dict):
                    base_url_display = byok.get("base_url")

            provider_is_lmstudio = is_lmstudio_provider_type(openai_provider_type)

            # -------------------
            # OpenAI Request
            # -------------------
            response_kwargs = {
                "model": model_name,
                "input": formatted_history,
                "instructions": system_instruction,
                "stream": True,
            }
            if not provider_is_lmstudio:
                _apply_openai_store_setting(
                    response_kwargs,
                    settings,
                    provider_type=openai_provider_type,
                )
                if _requests_openai_encrypted_reasoning(settings):
                    # ZDR can force the effective response to store=false even
                    # when Omlorix requested storage, so every all-turn request
                    # asks for the client-side continuation state.
                    response_kwargs["include"] = ["reasoning.encrypted_content"]
            # LM Studio's Responses endpoint supports stateful continuation even
            # though it does not expose all OpenAI-hosted storage controls.
            if previous_response_id:
                response_kwargs["previous_response_id"] = previous_response_id

            def _coerce_float(value):
                try:
                    if value in (None, ""):
                        return None
                    return float(value)
                except (TypeError, ValueError):
                    return None

            def _coerce_int(value):
                try:
                    if value in (None, ""):
                        return None
                    return int(value)
                except (TypeError, ValueError):
                    return None

            sampling_params = {
                "temperature": _coerce_float(settings.get("temperature")),
                "top_p": _coerce_float(settings.get("top_p")),
                "frequency_penalty": _coerce_float(settings.get("frequency_penalty")),
                "presence_penalty": _coerce_float(settings.get("presence_penalty")),
            }
            model_caps = _get_openai_model_caps(
                model_name,
                provider_type=openai_provider_type,
            )
            for param_key, param_value in sampling_params.items():
                capability = model_caps.get(param_key) if model_caps else None
                if isinstance(capability, dict) and not capability.get(param_key):
                    continue
                if param_value is not None:
                    response_kwargs[param_key] = param_value

            max_output_tokens = _coerce_int(settings.get("max_output_tokens"))
            if max_output_tokens is not None and max_output_tokens > 0:
                response_kwargs["max_output_tokens"] = max_output_tokens

            input_tokens_limit = _coerce_int(settings.get("input_tokens_limit"))
            if input_tokens_limit is None:
                input_tokens_limit = _coerce_int(settings.get("input_token_limit"))
            if (
                not provider_is_lmstudio
                and compaction_supported_by_endpoint
                and input_tokens_limit is not None
                and input_tokens_limit > 1000
            ):  # 1000 is minimum supported threshold
                response_kwargs["context_management"] = [
                    {
                        "type": "compaction",
                        "compact_threshold": input_tokens_limit,
                    }
                ]
                meta_compaction_enabled = True
                meta_compaction_threshold = input_tokens_limit
            else:
                meta_compaction_enabled = False
                meta_compaction_threshold = None

            verbosity_value = settings.get("verbosity")
            if isinstance(verbosity_value, str):
                verbosity_value = verbosity_value.strip()
            if not provider_is_lmstudio and verbosity_value not in (None, ""):
                response_kwargs["text"] = {"verbosity": verbosity_value}

            reasoning_payload = _build_openai_reasoning_payload(
                settings,
                model_name=model_name,
                provider_type=openai_provider_type,
            )
            if reasoning_payload is not None:
                response_kwargs["reasoning"] = reasoning_payload
            priority_tier = settings.get("priority_processing") or "standard"
            if not provider_is_lmstudio and priority_tier in {"flex", "priority"}:
                response_kwargs["service_tier"] = priority_tier
            if (
                not provider_is_lmstudio
                and normalize_openai_provider_type(openai_provider_type)
                != XAI_PROVIDER_TYPE
                and settings.get("send_user_identifier")
                and user_id
            ):
                response_kwargs["safety_identifier"] = str(user_id)
            if allow_tools_for_request and tools_flag and tool_schemas:
                response_kwargs["tools"] = tool_schemas
            if not provider_is_lmstudio:
                _apply_openai_prompt_cache_settings(
                    response_kwargs,
                    settings,
                    model_name=model_name,
                    provider_id=selected_provider_identifier,
                    user_id=user_id,
                    provider_type=openai_provider_type,
                )
            request_start_time = datetime.now(timezone.utc)
            # Setup above may perform many synchronous lookups. Return its clean
            # transaction to the pool before the potentially multi-minute
            # upstream request; this Session is reset and remains reusable for
            # tool calls, assistant persistence, and statistics afterward.
            release_db_session_before_provider_io(db)
            try:
                response = client.responses.create(
                    **_merge_openai_request_options(response_kwargs, request_options)
                )
            except BadRequestError as exc:
                if response_kwargs.get(
                    "context_management"
                ) and _should_retry_without_compaction(exc):
                    logger.warning(
                        "Retrying OpenAI response without context_management after unsupported-compaction error."
                    )
                    response_kwargs.pop("context_management", None)
                    compaction_supported_by_endpoint = False
                    meta_compaction_enabled = False
                    meta_compaction_threshold = None
                    response = client.responses.create(
                        **_merge_openai_request_options(
                            response_kwargs, request_options
                        )
                    )
                else:
                    raise
            reasoning_summary_text = ""
            reasoning_summary_emitted_from_item_done = False
            for chunk in _validated_openai_responses_stream(
                _interruptible_openai_response_stream(response, generation_id, db)
            ):
                if redacted_debug_logging_enabled(_OPENAI_STREAM_DEBUG_FLAG):
                    logger.debug(
                        "OpenAI stream event metadata=%s", object_event_metadata(chunk)
                    )
                last_activity = time.monotonic()
                # Check for cancellation for this generation and exit gracefully if set
                try:
                    if generation_id:
                        from app.chats.streaming import cancel_registry

                        if cancel_registry.is_cancelled(generation_id):
                            if (
                                not temp_request_flag
                                and last_message_type == "reasoning"
                            ):
                                for flush_event in add_last_part_to_messages(
                                    "cancelled"
                                ):
                                    yield flush_event
                            cancellation_meta = {"status": "cancelled"}
                            # Persist any partial assistant content accumulated so far
                            additional_meta = {
                                "model": model_name,
                                "request_count": meta_request_count,
                                "input_tokens": meta_input_tokens,
                                "output_tokens": meta_output_tokens,
                                "reasoning_tokens": meta_reasoning_tokens,
                                "total_tokens": meta_total_tokens,
                                "reasoning_time": reasoning_timer.last_duration,
                                "total_reasoning_time": reasoning_timer.total_duration,
                            }
                            for key, value in additional_meta.items():
                                if value not in (None, 0, "", [], {}):
                                    cancellation_meta[key] = value
                            cancellation_meta["timestamp"] = format_meta_timestamp()
                            meta_generation_success = True
                            if not temp_request_flag:
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

                if chunk.type == "response.created":
                    # Do not let a compatibility proxy's null model erase the
                    # configured request identifier retained above.
                    meta_model_id = resolve_model_metadata_id(
                        getattr(chunk.response, "model", None),
                        meta_model_id,
                        model_name,
                    )
                    meta_response_id = chunk.response.id
                    reasoning_info = getattr(chunk.response, "reasoning", None)
                    meta_reasoning_effort = getattr(reasoning_info, "effort", None)
                    meta_reasoning_mode = getattr(reasoning_info, "mode", None)
                    meta_reasoning_context = getattr(reasoning_info, "context", None)
                    meta_prompt_cache_retention = getattr(
                        chunk.response, "prompt_cache_retention", None
                    )
                    meta_store = getattr(chunk.response, "store", None)
                    if provider_is_lmstudio and meta_store is None:
                        # LM Studio documents response-id continuation but may
                        # omit OpenAI's hosted-only store flag from events.
                        meta_store = True
                    meta_service_tier = getattr(chunk.response, "service_tier", None)

                elif chunk.type == "response.reasoning_text.delta":
                    if content_generation_start is None:
                        content_generation_start = datetime.now(timezone.utc)
                    reasoning += chunk.delta
                    if last_message_type != "reasoning":
                        reasoning_timer.start()
                        for flush_event in add_last_part_to_messages("reasoning"):
                            yield flush_event
                    yield json.dumps({"t": "r", "d": chunk.delta}) + "\n"

                elif chunk.type in _OPENAI_REASONING_SUMMARY_STREAM_EVENT_TYPES:
                    # Some OpenAI models do not emit reasoning_text deltas; they emit reasoning summary events.
                    if content_generation_start is None:
                        content_generation_start = datetime.now(timezone.utc)

                    delta_text = _extract_openai_reasoning_summary_text(chunk)
                    if not isinstance(delta_text, str) or not delta_text:
                        continue
                    if (
                        chunk.type in _OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES
                        and reasoning_summary_text.endswith(delta_text)
                    ):
                        continue

                    reasoning_summary_text += delta_text
                    reasoning += delta_text
                    if last_message_type != "reasoning":
                        reasoning_timer.start()
                        for flush_event in add_last_part_to_messages("reasoning"):
                            yield flush_event
                    yield json.dumps({"t": "r", "d": delta_text}) + "\n"

                elif chunk.type == "response.output_item.added":
                    if chunk.item.type == "reasoning":
                        if content_generation_start is None:
                            content_generation_start = datetime.now(timezone.utc)
                        if last_message_type != "reasoning":
                            reasoning_timer.start()
                            for flush_event in add_last_part_to_messages("reasoning"):
                                yield flush_event
                        reasoning += ""
                        yield json.dumps({"t": "r", "d": ""}) + "\n"
                    elif chunk.item.type == "function_call":
                        for flush_event in add_last_part_to_messages("tool_call"):
                            yield flush_event
                        function_state = (
                            function_call_accumulator.register_output_event(chunk)
                        )
                        function_call_name = (
                            function_state.get("name") if function_state else None
                        )
                        function_id = (
                            function_state.get("id") if function_state else None
                        )
                        if (
                            function_call_name
                            and function_call_name in tools_not_yield_arguments
                            and not is_tool_hidden_from_user(function_call_name)
                            and function_id
                            and not function_call_event_sent_map.get(function_id)
                        ):
                            yield (
                                json.dumps({"t": "t_c", "d": function_call_name}) + "\n"
                            )
                            function_call_event_sent_map[function_id] = True
                    elif chunk.item.type == "tool_search_call":
                        for flush_event in add_last_part_to_messages(
                            "tool_search_call"
                        ):
                            yield flush_event
                    elif chunk.item.type == "web_search_call":
                        for flush_event in add_last_part_to_messages("web_search_call"):
                            yield flush_event

                elif chunk.type == "response.output_item.done":
                    if chunk.item.type == "reasoning":
                        # If the provider only returns summaries and we did not receive summary part deltas,
                        # emit the summary once so the UI can display reasoning.
                        if not reasoning_summary_text:
                            summary_list = getattr(chunk.item, "summary", None)
                            if (
                                isinstance(summary_list, list)
                                and summary_list
                                and not reasoning_summary_emitted_from_item_done
                            ):
                                try:
                                    summary_text = getattr(
                                        summary_list[0], "text", None
                                    )
                                except Exception:
                                    summary_text = None
                                if isinstance(summary_text, str) and summary_text:
                                    if content_generation_start is None:
                                        content_generation_start = datetime.now(
                                            timezone.utc
                                        )
                                    reasoning_summary_text = summary_text
                                    reasoning += summary_text
                                    if last_message_type != "reasoning":
                                        reasoning_timer.start()
                                        for flush_event in add_last_part_to_messages(
                                            "reasoning"
                                        ):
                                            yield flush_event
                                    yield (
                                        json.dumps({"t": "r", "d": summary_text}) + "\n"
                                    )
                                    reasoning_summary_emitted_from_item_done = True

                        formatted_history.append(
                            {
                                "id": chunk.item.id,
                                "summary": chunk.item.summary,
                                "type": "reasoning",
                                "content": chunk.item.content
                                if chunk.item.content
                                else None,
                                "encrypted_content": chunk.item.encrypted_content
                                if chunk.item.encrypted_content
                                else None,
                            }
                        )
                        if _requests_openai_encrypted_reasoning(settings):
                            try:
                                serialized_reasoning_item = jsonable_encoder(chunk.item)
                            except Exception:
                                serialized_reasoning_item = None
                            sanitized_reasoning_item = _sanitize_openai_reasoning_item(
                                serialized_reasoning_item
                            )
                            if sanitized_reasoning_item:
                                # Summary text is safe for the existing reasoning
                                # UI; encrypted_content remains opaque provider
                                # state and is never rendered as ordinary text.
                                opaque_reasoning_items.append(sanitized_reasoning_item)
                    elif chunk.item.type == "function_call":
                        # Some compatible endpoints only populate arguments on
                        # output_item.done. Keep this as a finalized fallback;
                        # arguments.done remains authoritative when present.
                        function_call_accumulator.register_output_event(
                            chunk,
                            finalized=True,
                        )
                    elif chunk.item.type == "web_search_call":
                        meta_native_websearch_tool_calls_count += 1
                        action = getattr(chunk.item, "action", None)
                        query = getattr(action, "query", None)
                        web_search = True
                        if query:
                            yield (
                                json.dumps(
                                    {
                                        "t": "t_c",
                                        "d": "web_search",
                                        "c": {"query": query},
                                    }
                                )
                                + "\n"
                            )
                        for flush_event in add_last_part_to_messages("web_search_call"):
                            yield flush_event

                        if not temp_request_flag and target_model_id is not None:
                            try:
                                action_payload = (
                                    jsonable_encoder(action)
                                    if action is not None
                                    else {}
                                )
                            except Exception:
                                action_payload = {}
                            if not action_payload:
                                if query:
                                    action_payload = {"query": query}
                                else:
                                    action_payload = {"status": "completed"}
                            try:
                                tool_content = json.dumps(
                                    action_payload,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                            except TypeError:
                                tool_content = str(action_payload)
                            messages_to_save.append(
                                build_tool_call_block(
                                    "web_search",
                                    tool_content,
                                    extra_meta={"native_web_search": True},
                                )
                            )
                    elif chunk.item.type == "tool_search_call":
                        for flush_event in add_last_part_to_messages(
                            "tool_search_call"
                        ):
                            yield flush_event
                        search_arguments, search_arguments_text = (
                            _serialize_openai_tool_search_arguments(chunk.item)
                        )
                        if search_arguments:
                            yield (
                                json.dumps(
                                    {
                                        "t": "t_c",
                                        "d": "tool_search",
                                        "c": search_arguments,
                                    }
                                )
                                + "\n"
                            )
                        formatted_history.append(
                            {
                                "type": "tool_search_call",
                                "execution": getattr(chunk.item, "execution", None)
                                or "server",
                                "call_id": getattr(chunk.item, "call_id", None),
                                "status": getattr(chunk.item, "status", None)
                                or "completed",
                                "arguments": search_arguments,
                            }
                        )
                        if not temp_request_flag and target_model_id is not None:
                            messages_to_save.append(
                                build_tool_call_block(
                                    "tool_search",
                                    search_arguments_text,
                                    tool_call_id=getattr(chunk.item, "call_id", None),
                                    extra_meta={
                                        "tool_search_call": True,
                                        "tool_search_execution": getattr(
                                            chunk.item, "execution", None
                                        )
                                        or "server",
                                        "tool_search_status": getattr(
                                            chunk.item, "status", None
                                        )
                                        or "completed",
                                    },
                                )
                            )
                    elif chunk.item.type == "tool_search_output":
                        tools_payload, tools_text = _serialize_openai_tool_search_tools(
                            chunk.item
                        )
                        formatted_history.append(
                            {
                                "type": "tool_search_output",
                                "execution": getattr(chunk.item, "execution", None)
                                or "server",
                                "call_id": getattr(chunk.item, "call_id", None),
                                "status": getattr(chunk.item, "status", None)
                                or "completed",
                                "tools": tools_payload,
                            }
                        )
                        if not temp_request_flag and target_model_id is not None:
                            messages_to_save.append(
                                {
                                    "type": "tool_call_result",
                                    "content": tools_text,
                                    "meta": {
                                        "tool_name": "tool_search",
                                        "tool_search_output": True,
                                        "tool_search_call_id": getattr(
                                            chunk.item, "call_id", None
                                        ),
                                        "tool_search_execution": getattr(
                                            chunk.item, "execution", None
                                        )
                                        or "server",
                                        "tool_search_status": getattr(
                                            chunk.item, "status", None
                                        )
                                        or "completed",
                                        "tool_search_tools": tools_payload,
                                    },
                                }
                            )
                    elif chunk.item.type == "message":
                        for block in chunk.item.content:
                            text = annotations = getattr(block, "text", None)
                            if text:
                                formatted_history.append(
                                    {
                                        "role": "assistant",
                                        "id": chunk.item.id,
                                        "type": "message",
                                        "content": text,
                                    }
                                )
                            annotations = getattr(block, "annotations", None)
                            if not annotations:
                                continue
                            for annotation in annotations:
                                meta_citations.append(
                                    {
                                        "end_index": annotation.end_index,
                                        "start_index": annotation.start_index,
                                        "title": annotation.title,
                                        "type": annotation.type,
                                        "url": annotation.url,
                                    }
                                )
                    elif chunk.item.type == "compaction":
                        meta_compaction_events += 1

                elif chunk.type == "response.output_text.delta":
                    if (
                        meta_time_to_first_token is None
                        and request_start_time is not None
                    ):
                        meta_time_to_first_token = (
                            datetime.now(timezone.utc) - request_start_time
                        ).total_seconds()
                    if last_message_type != "content":
                        for flush_event in add_last_part_to_messages("content"):
                            yield flush_event
                        last_message_type = "content"
                    content += chunk.delta
                    yield json.dumps({"t": "c", "d": chunk.delta}) + "\n"

                elif chunk.type == "response.function_call_arguments.delta":
                    function_state = function_call_accumulator.append_delta(chunk)
                    function_call_name = (
                        function_state.get("name") if function_state else None
                    )
                    function_call_id = (
                        function_state.get("call_id") if function_state else None
                    )
                    function_id = function_state.get("id") if function_state else None
                    if (
                        function_call_name
                        and function_call_name not in tools_not_yield_arguments
                        and not is_tool_hidden_from_user(function_call_name)
                        and isinstance(chunk.delta, str)
                        and chunk.delta
                    ):
                        tool_delta_payload = {
                            "id": function_call_id or function_id,
                            "name": function_call_name,
                            "delta": chunk.delta,
                        }
                        stream_meta = get_stream_tool_event_meta(
                            function_call_name,
                            tool_call_id=function_call_id or function_id,
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

                elif chunk.type == "response.function_call_arguments.done":
                    function_call_accumulator.finalize_arguments(chunk)

                if chunk.type == "response.completed":
                    response_obj = getattr(chunk, "response", None)
                    # Some proxies report the canonical model only on the final
                    # event, while others omit it from every response event.
                    meta_model_id = resolve_model_metadata_id(
                        getattr(response_obj, "model", None),
                        meta_model_id,
                        model_name,
                    )
                    completed_service_tier = getattr(
                        response_obj,
                        "service_tier",
                        None,
                    )
                    if completed_service_tier:
                        meta_service_tier = completed_service_tier
                    completed_store = getattr(response_obj, "store", None)
                    if completed_store is not None:
                        # The completed response carries the provider's effective
                        # value, including a server-enforced ZDR override.
                        meta_store = completed_store

                    # The completed response is the last compatibility fallback
                    # for proxies that omit argument delta/done events entirely.
                    for output_index, output_item in enumerate(
                        getattr(response_obj, "output", None) or []
                    ):
                        function_call_accumulator.register_item(
                            output_item,
                            output_index=output_index,
                            finalized=True,
                        )
                    for completed_call in function_call_accumulator.drain_finalized():
                        completed_call["event_sent"] = function_call_event_sent_map.get(
                            completed_call.get("id"),
                            False,
                        )
                        function_calls.append(completed_call)
                    if function_calls:
                        function_call = True

                    usage = getattr(response_obj, "usage", None)
                    input_tokens = output_tokens = reasoning_tokens = total_tokens = (
                        cached_tokens
                    ) = cache_write_tokens = 0
                    input_details = None
                    output_details = None
                    if usage:
                        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                        input_details = getattr(usage, "input_tokens_details", None)
                        cached_tokens = int(
                            getattr(input_details, "cached_tokens", 0) or 0
                        )
                        cache_write_tokens = int(
                            getattr(input_details, "cache_write_tokens", 0) or 0
                        )
                        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                        output_details = getattr(usage, "output_tokens_details", None)
                        reasoning_tokens = int(
                            getattr(output_details, "reasoning_tokens", 0) or 0
                        )
                        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                        reported_cost, reported_details = (
                            _provider_reported_cost_from_usage(
                                usage,
                                provider_type=openai_provider_type,
                            )
                        )
                        if reported_details:
                            meta_provider_reported_cost += reported_cost
                            meta_provider_cost_ticks += int(
                                reported_details.get("cost_in_usd_ticks", 0) or 0
                            )
                            meta_has_provider_reported_cost = True
                    else:
                        usage = None

                    meta_input_tokens += input_tokens
                    meta_cached_input_tokens += cached_tokens
                    meta_cache_write_tokens += cache_write_tokens
                    meta_output_tokens += output_tokens
                    meta_reasoning_tokens += reasoning_tokens
                    meta_total_tokens += total_tokens

                    request_costs = calculate_openai_token_costs(
                        model_name=model_identifier
                        or getattr(db_model, "model_name", None)
                        or "openai",
                        provider_type=openai_provider_type,
                        service_tier=meta_service_tier or "standard",
                        input_tokens=input_tokens,
                        cached_input_tokens=cached_tokens,
                        cache_write_tokens=cache_write_tokens,
                        output_tokens=output_tokens,
                        reasoning_tokens=reasoning_tokens,
                        native_websearch_tool_calls_count=0,
                    )
                    merge_openai_cost_breakdown(
                        meta_request_costs,
                        request_costs,
                    )

                    out_tokens = output_tokens
                    now = datetime.now(timezone.utc)
                    duration = 0.0
                    if content_generation_start is not None:
                        duration = max(
                            (now - content_generation_start).total_seconds(), 0.0
                        )
                    content_generation_duration = duration
                    tokens_per_second = (out_tokens / duration) if duration > 0 else 0

                    meta_request_count += 1

                    if not function_call:
                        for finish_event in finalize_response(
                            response_obj=response_obj,
                            tokens_per_second=tokens_per_second,
                            meta_citations=meta_citations,
                            messages_to_save=messages_to_save,
                            temp_request_flag=temp_request_flag,
                            add_last_part_to_messages=add_last_part_to_messages,
                            _finalize_pending_assistant_message=_finalize_pending_assistant_message,
                        ):
                            yield finish_event
                        return
                    else:
                        try:
                            calls_to_execute, rejected_calls = tool_call_budget.admit(
                                function_calls
                            )
                            if rejected_calls:
                                logger.warning(
                                    "OpenAI tool call limit reached admitted=%s rejected=%s limit=%s",
                                    len(calls_to_execute),
                                    len(rejected_calls),
                                    tool_call_budget.limit,
                                )

                            for call in calls_to_execute:
                                name = call["name"]
                                resolved_tool_call_id = call["call_id"] or call["id"]
                                arguments_raw = call["arguments"] or "{}"
                                try:
                                    parsed_arguments = json.loads(arguments_raw)
                                except json.JSONDecodeError:
                                    logger.warning(
                                        "Unable to parse tool arguments for %s argument_length=%s",
                                        name,
                                        len(arguments_raw),
                                    )
                                    parsed_arguments = {}

                                for flush_event in add_last_part_to_messages(
                                    "tool_call"
                                ):
                                    yield flush_event
                                content_generation_start = None
                                # Persist the completed call once in canonical structured metadata.
                                arguments_label = (
                                    arguments_raw if arguments_raw else "{}"
                                )
                                hide_tool_call_from_user = (
                                    should_hide_tool_call_from_user(
                                        name, parsed_arguments
                                    )
                                )
                                if not hide_tool_call_from_user:
                                    messages_to_save.append(
                                        build_tool_call_block(
                                            name,
                                            arguments_label,
                                            tool_call_id=resolved_tool_call_id,
                                            tool_namespace=call.get("namespace"),
                                        )
                                    )
                                event_sent = bool(call.get("event_sent"))
                                if not event_sent and not hide_tool_call_from_user:
                                    hide_tool_arguments = (
                                        name in tools_not_yield_arguments
                                    )
                                    tool_event_descriptor = {
                                        "id": resolved_tool_call_id,
                                        "name": name,
                                    }
                                    if not hide_tool_arguments:
                                        tool_event_descriptor["args"] = parsed_arguments
                                    stream_meta = get_stream_tool_event_meta(
                                        name,
                                        tool_call_id=resolved_tool_call_id,
                                    )
                                    if stream_meta:
                                        tool_event_descriptor["meta"] = stream_meta
                                    tool_event_payload = {
                                        "t": "t_c",
                                        "d": tool_event_descriptor,
                                    }
                                    if not hide_tool_arguments:
                                        tool_event_payload["c"] = parsed_arguments
                                    yield json.dumps(tool_event_payload) + "\n"
                                    event_sent = True
                                content_str = ""
                                model_tool_output = ""
                                if name in tool_list:
                                    documents = []
                                    images = []
                                    videos = []
                                    audios = []
                                    youtube = []
                                    webpages = []
                                    result = None

                                    helper_payload: dict[str, Any] = {}
                                    helper_gen = None
                                    tool_error_message: str | None = None
                                    tool_error_response: ToolErrorResponse | None = None
                                    tool_stat_logged = False
                                    try:
                                        from app.tools.helper import resolve_tool_call

                                        helper_gen = resolve_tool_call(
                                            db,
                                            name,
                                            parsed_arguments,
                                            user_id,
                                            None,
                                            project_id,
                                            model_settings=settings,
                                            byok=byok,
                                            chat_id=chat_id,
                                            chat_history=chat_history,
                                            generation_id=generation_id,
                                            user_role=normalized_user_role,
                                            tool_call_id=resolved_tool_call_id,
                                        )
                                    except Exception as tool_exc:
                                        tool_error_message = str(tool_exc)
                                        tool_error_response = tool_error_tracker.record(
                                            name, tool_exc
                                        )
                                        logger.error(
                                            "Tool %s failed to start meta=%s",
                                            name,
                                            exception_metadata(tool_exc),
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
                                            logger.error(
                                                "Tool %s raised during streaming meta=%s",
                                                name,
                                                exception_metadata(tool_exc),
                                            )

                                    # Long-running tools are active work, not a
                                    # stalled provider stream. Refresh the
                                    # timeout clock so the loop can issue the
                                    # follow-up request that produces the main
                                    # assistant's final answer.
                                    last_activity = time.monotonic()

                                    if tool_error_message:
                                        tool_meta = None
                                        if tool_error_response is None:
                                            tool_error_response = (
                                                tool_error_tracker.record(
                                                    name,
                                                    RuntimeError(tool_error_message),
                                                )
                                            )
                                        result = tool_error_response.result_payload
                                        content = tool_error_response.model_output
                                        helper_payload = {}
                                        if tool_error_response.stop_tool_calls:
                                            # Permit one final model round without tools so it can
                                            # explain the repeated validation failure to the user.
                                            tool_call_budget.remaining = 0
                                            final_response_without_tools = True
                                        try:
                                            if not tool_stat_logged:
                                                create_tool_call_statistic(
                                                    db=db,
                                                    tool_name=name,
                                                    success=False,
                                                    error_message=tool_error_message,
                                                    model_id=target_model_id,
                                                    model_name=model_name,
                                                    provider=provider_name,
                                                    user_id=user_id,
                                                    meta=tool_error_response.statistic_meta,
                                                    is_byok=bool(byok),
                                                )
                                                tool_stat_logged = True
                                        except Exception:
                                            pass  # Don't fail the request if stats logging fails
                                    else:
                                        content = helper_payload.get("content", "")
                                        documents = (
                                            helper_payload.get("documents") or []
                                        )
                                        images = helper_payload.get("images") or []
                                        videos = helper_payload.get("videos") or []
                                        audios = helper_payload.get("audios") or []
                                        youtube = helper_payload.get("youtube") or []
                                        webpages = helper_payload.get("webpages") or []
                                        result = helper_payload.get("result")
                                        tool_meta = helper_payload.get(
                                            "tool_meta"
                                        ) or helper_payload.get("meta")
                                        try:
                                            if not tool_stat_logged:
                                                create_tool_call_statistic(
                                                    db=db,
                                                    tool_name=name,
                                                    success=True,
                                                    error_message=None,
                                                    model_id=target_model_id,
                                                    model_name=model_name,
                                                    provider=provider_name,
                                                    user_id=user_id,
                                                    meta=tool_meta,
                                                    is_byok=bool(byok),
                                                )
                                                tool_stat_logged = True
                                        except Exception:
                                            pass  # Don't fail the request if stats logging fails

                                    file_ids = images + videos + audios + documents

                                    tool_call_file_parts = []
                                    function_call_parts_result = None
                                    if not tool_error_message and file_ids:
                                        uploaded_files = upload_files(
                                            db,
                                            file_ids,
                                            user_id,
                                            input_formats_allowed=input_formats_allowed,
                                            image_detail=settings.get("image_detail"),
                                        )
                                        tool_call_file_parts = (
                                            uploaded_files.get("parts") or []
                                        )
                                    # Ensure content is a string before persisting to DB, preferring explicit tool result payloads
                                    widget_data = helper_payload.get("widget")
                                    content_str = (
                                        stringify_tool_result_content_for_persistence(
                                            name,
                                            result
                                            if result not in (None, "")
                                            else content,
                                            widget_data,
                                        )
                                    )
                                    if not content_str and webpages:
                                        try:
                                            content_str = json.dumps(
                                                webpages, ensure_ascii=False
                                            )
                                        except (TypeError, ValueError):
                                            content_str = str(webpages)
                                    # The database keeps bounded widget context,
                                    # but the active Responses call needs the
                                    # actual tool output to finish the assistant
                                    # turn (notably the Deep Research report).
                                    model_tool_output = (
                                        stringify_tool_result_content_for_model(
                                            content,
                                            content_str,
                                        )
                                    )
                                    tool_label = (
                                        f"{name}({str(arguments_raw)})"
                                        if name
                                        else "unknown"
                                    )
                                    if (
                                        not temp_request_flag
                                        and not hide_tool_call_from_user
                                    ):
                                        persist_files_in_file_block = (
                                            should_persist_files_in_file_block(name)
                                        )
                                        tool_result_block = {
                                            "type": "tool_call_result",
                                            "content": content_str,
                                            "youtube": youtube,
                                            "tool_name": tool_label,
                                        }
                                        if not persist_files_in_file_block:
                                            tool_result_block["documents"] = documents
                                            tool_result_block["images"] = images
                                            tool_result_block["videos"] = videos
                                            tool_result_block["audios"] = audios

                                        # Extract citations from webpages for web_search tool outputs.
                                        if name == "web_search" and webpages:
                                            citations = []
                                            for page in webpages:
                                                if isinstance(page, dict):
                                                    citation = {}
                                                    if page.get("url"):
                                                        citation["url"] = page["url"]
                                                    if page.get("title"):
                                                        citation["title"] = page[
                                                            "title"
                                                        ]
                                                    # Check for snippet/content preview
                                                    content_text = page.get("content")
                                                    if content_text and isinstance(
                                                        content_text, str
                                                    ):
                                                        # Extract first 200 chars as snippet
                                                        snippet = content_text[
                                                            :200
                                                        ].strip()
                                                        if len(content_text) > 200:
                                                            snippet += "..."
                                                        citation["snippet"] = snippet
                                                    if citation.get("url"):
                                                        citations.append(citation)
                                            if citations:
                                                if "meta" not in tool_result_block:
                                                    tool_result_block["meta"] = {}
                                                tool_result_block["meta"][
                                                    "citations"
                                                ] = citations
                                        tool_result_block.setdefault("meta", {})
                                        if isinstance(tool_meta, dict) and tool_meta:
                                            tool_result_block["meta"].update(tool_meta)
                                        tool_result_block["meta"]["tool_name"] = name
                                        tool_result_block["meta"]["tool_call_id"] = (
                                            resolved_tool_call_id
                                        )

                                        messages_to_save.append(tool_result_block)
                                        if persist_files_in_file_block:
                                            file_block = build_tool_file_block(
                                                tool_name=name,
                                                tool_label=tool_label,
                                                documents=documents,
                                                images=images,
                                                videos=videos,
                                                audios=audios,
                                            )
                                            if file_block:
                                                messages_to_save.append(file_block)
                                        # Save widget if returned from tool
                                        if widget_data and widget_data.get("html"):
                                            messages_to_save.append(
                                                {
                                                    "type": "widget",
                                                    "content": widget_data.get("html"),
                                                    "meta": build_widget_block_meta(
                                                        widget_data,
                                                        tool_name=name,
                                                        tool_call_id=resolved_tool_call_id,
                                                        tool_namespace=call.get(
                                                            "namespace"
                                                        ),
                                                    ),
                                                }
                                            )
                                else:
                                    result = {
                                        "error": f"Tool '{name}' is not allowed or not available"
                                    }
                                    content_str = json.dumps(result, ensure_ascii=False)
                                    model_tool_output = content_str

                                formatted_history.append(
                                    {
                                        "id": call["id"],
                                        "call_id": resolved_tool_call_id,
                                        "type": "function_call",
                                        "name": name,
                                        "namespace": call.get("namespace"),
                                        "arguments": arguments_raw,
                                        "status": "completed",
                                    }
                                )
                                function_call_output_text = model_tool_output
                                if not function_call_output_text:
                                    logger.warning(
                                        "Tool %s produced an empty function_call_output payload result_present=%s content_present=%s webpages=%s files=%s",
                                        name,
                                        result not in (None, ""),
                                        content not in (None, ""),
                                        len(webpages),
                                        len(tool_call_file_parts),
                                    )
                                function_call_output_parts = [
                                    {
                                        "type": "input_text",
                                        "text": function_call_output_text,
                                    }
                                ]
                                if tool_call_file_parts:
                                    function_call_output_parts.extend(
                                        tool_call_file_parts
                                    )
                                formatted_history.append(
                                    {
                                        "type": "function_call_output",
                                        "call_id": resolved_tool_call_id,
                                        "output": function_call_output_parts,
                                    }
                                )
                                content = ""
                                # The tool result is persisted separately from
                                # assistant content. Still advance the stream
                                # boundary so the next response can time a new
                                # reasoning segment independently.
                                last_message_type = "tool_call_result"

                            if rejected_calls:
                                # The provider requested more calls than this
                                # generation may execute. Stop explicitly after
                                # the admitted prefix; a follow-up request would
                                # be invalid because the rejected calls have no
                                # real tool outputs.
                                limit_error = (
                                    "Maximum OpenAI tool call limit reached for "
                                    f"this response ({tool_call_budget.limit})."
                                )
                                meta_generation_error = True
                                meta_error_type = "ToolCallLimitExceeded"
                                meta_error_message = limit_error
                                yield (
                                    json.dumps(
                                        {
                                            "t": "e",
                                            "d": "An error occurred during generation. Please try again.",
                                        }
                                    )
                                    + "\n"
                                )
                                yield (
                                    json.dumps(
                                        {"t": "d", "d": "c", "c": {"status": "error"}}
                                    )
                                    + "\n"
                                )
                                return

                            if tool_call_budget.exhausted:
                                # All 200 admitted calls have real outputs. Give
                                # the model one tool-free round to turn those
                                # outputs into a normal final assistant message.
                                function_call = True
                                final_response_without_tools = True
                        except Exception as e:
                            logger.error(
                                "Tool execution loop failed name=%s meta=%s",
                                name,
                                exception_metadata(e),
                            )
                            is_admin = is_admin_role(normalized_user_role)
                            error_message = (
                                str(e)
                                if is_admin
                                else "An error occurred during generation. Please try again."
                            )
                            yield json.dumps({"t": "e", "d": error_message}) + "\n"
                            function_call = False
                            content = ""
                            images = []
                            videos = []
                            audios = []
                            documents = []
                            youtube = []

                # Checking if timeout occured
                try:
                    if time.monotonic() - last_activity > inactivity_timeout_sec:
                        if not temp_request_flag and last_message_type == "reasoning":
                            for flush_event in add_last_part_to_messages("timeout"):
                                yield flush_event
                        now = datetime.now(timezone.utc)
                        duration = 0.0
                        if content_generation_start is not None:
                            duration = max(
                                (now - content_generation_start).total_seconds(), 0.0
                            )
                        tokens_per_second = (
                            (meta_output_tokens / duration)
                            if duration and duration > 0
                            else None
                        )

                        meta_tokens_per_second = (
                            round(tokens_per_second, 2)
                            if tokens_per_second is not None
                            else meta_tokens_per_second
                        )
                        meta_values = {
                            "model": model_name,
                            "input_tokens": meta_input_tokens,
                            "output_tokens": meta_output_tokens,
                            "reasoning_tokens": meta_reasoning_tokens,
                            "total_tokens": meta_total_tokens,
                            "request_count": meta_request_count,
                            "total_reasoning_time": reasoning_timer.total_duration,
                            "reasoning_time": reasoning_timer.last_duration,
                            "timeout": True,
                        }
                        add_cached_input_token_meta(
                            meta_values, meta_cached_input_tokens
                        )
                        if meta_cache_write_tokens:
                            meta_values["cache_write_tokens"] = meta_cache_write_tokens
                        if tokens_per_second is not None:
                            meta_values["tokens_per_second"] = round(
                                tokens_per_second, 2
                            )
                        meta_values["timestamp"] = format_meta_timestamp()

                        meta = {}
                        for key, value in meta_values.items():
                            if value not in (None, 0, "", [], {}):
                                meta[key] = value
                        if "timeout" not in meta:
                            meta["timeout"] = True
                        yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                        for flush_event in add_last_part_to_messages("timeout", meta):
                            yield flush_event
                        meta_generation_error = True
                        meta_error_type = "Timeout"
                        meta_error_message = (
                            meta.get("timeout_message") or "generation timeout"
                        )
                        return
                except Exception:
                    pass

    except (AuthenticationError, BadRequestError, APIConnectionError) as exc:
        status, message, error_type, _ = _parse_openai_exception(exc)
        meta_generation_error = True
        meta_error_status_code = status or 400
        meta_error_type = error_type or exc.__class__.__name__
        meta_error_message = message
        is_admin = is_admin_role(normalized_user_role)
        error_message = (
            message
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
    except Exception as exc:
        logger.error("Failed to generate response: exc_type=%s", type(exc).__name__)
        meta_generation_error = True
        meta_error_message = str(exc)
        meta_error_type = exc.__class__.__name__
        is_admin = is_admin_role(normalized_user_role)
        protocol_error = isinstance(exc, _OpenAIResponsesStreamProtocolError)
        error_message = (
            "Connection interrupted. Please try again."
            if protocol_error
            else (
                str(exc)
                if is_admin
                else "An error occurred during generation. Please try again."
            )
        )
        error_payload = {"t": "e", "d": error_message}
        if protocol_error:
            error_payload["i18n_key"] = "chat_connection_interrupted_retry"
        yield json.dumps(error_payload) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
    finally:
        if (
            not assistant_message_saved
            and not temp_request_flag
            and (messages_to_save or content or reasoning)
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
