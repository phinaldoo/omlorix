"""Google AI Studio chat orchestration and streaming.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION

_COMPAT_DEPENDENCIES = {
    "aistudio_chat": (
        "AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS",
        "GoogleAiStudioModelSettings",
        "HTTPException",
        "ToolErrorTracker",
        "_build_aistudio_file_part",
        "_build_aistudio_tools_payload",
        "_coerce_aistudio_native_websearch",
        "_extract_aistudio_query_payload",
        "_extract_google_grounding_citations",
        "_is_google_search_server_tool",
        "_safe_aistudio_dict",
        "append_system_instruction_sections",
        "base64",
        "build_aistudio_generate_content_config",
        "build_aistudio_video_metadata",
        "build_stream_tool_event_meta",
        "build_tool_call_block",
        "build_tool_file_block",
        "build_web_search_citations",
        "build_widget_block_meta",
        "calculate_aistudio_token_costs",
        "collect_tool_result_citations",
        "copy",
        "create_llm_generation_statistic",
        "create_tool_call_statistic",
        "datetime",
        "format_meta_timestamp",
        "genai_errors",
        "get_aistudio_client",
        "get_default_system_instruction",
        "get_user_setting_value",
        "google_aistudio_supported_languages",
        "interruptible_provider_stream",
        "is_admin_role",
        "json",
        "logger",
        "merge_settings",
        "normalize_aistudio_usage_metadata",
        "normalize_unsupported_file_ids",
        "reformat_chat_history",
        "should_hide_tool_call_from_user",
        "should_persist_files_in_file_block",
        "stringify_tool_result_content_for_persistence",
        "time",
        "timezone",
        "tools_not_yield_arguments",
        "types",
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
    "AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS",
    "GoogleAiStudioModelSettings",
    "HTTPException",
    "ToolErrorTracker",
    "_build_aistudio_file_part",
    "_build_aistudio_tools_payload",
    "_coerce_aistudio_native_websearch",
    "_extract_aistudio_query_payload",
    "_extract_google_grounding_citations",
    "_is_google_search_server_tool",
    "_safe_aistudio_dict",
    "append_system_instruction_sections",
    "base64",
    "build_aistudio_generate_content_config",
    "build_aistudio_video_metadata",
    "build_stream_tool_event_meta",
    "build_tool_call_block",
    "build_tool_file_block",
    "build_web_search_citations",
    "build_widget_block_meta",
    "calculate_aistudio_token_costs",
    "collect_tool_result_citations",
    "copy",
    "create_llm_generation_statistic",
    "create_tool_call_statistic",
    "datetime",
    "format_meta_timestamp",
    "genai_errors",
    "get_aistudio_client",
    "get_default_system_instruction",
    "get_user_setting_value",
    "google_aistudio_supported_languages",
    "interruptible_provider_stream",
    "is_admin_role",
    "json",
    "logger",
    "merge_settings",
    "normalize_aistudio_usage_metadata",
    "normalize_unsupported_file_ids",
    "reformat_chat_history",
    "should_hide_tool_call_from_user",
    "should_persist_files_in_file_block",
    "stringify_tool_result_content_for_persistence",
    "time",
    "timezone",
    "tools_not_yield_arguments",
    "types",
    "upload_files",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_aistudio_chat(
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
    user_role_normalized = str(user_role or "").strip().lower()
    model_name: str | None = None

    # -------------------
    # Client
    # -------------------
    if not byok and (not db_model or not db_model.provider_id):
        raise HTTPException(status_code=422, detail="Provider not configured")
    requested_provider_id = getattr(db_model, "provider_id", None) if db_model else None
    selected_provider = None
    if byok:
        client = get_aistudio_client(
            db,
            api_key=byok.get("api_key"),
            api_version=byok.get("api_version"),
        )
    else:
        from app.llm.provider_groups import resolve_provider_for_request

        selected_provider = resolve_provider_for_request(db, requested_provider_id)
        client = get_aistudio_client(db, selected_provider.id)

    # -------------------
    # Settings / Parameters
    # -------------------
    settings, merged_tools = merge_settings(
        db_model.settings,
        settings_override,
        getattr(GoogleAiStudioModelSettings, "model_fields", None),
        db_model.tools,
    )
    video_metadata = build_aistudio_video_metadata(settings)

    # -------------------
    # Language
    # -------------------
    user_language = get_user_setting_value(user_id, "general", "language", db)
    if user_language not in google_aistudio_supported_languages:
        yield (
            json.dumps(
                {
                    "t": "w",
                    "d": "Google AI Studio does not support the language "
                    + user_language,
                }
            )
            + "\n"
        )

    # -------------------
    # Chat History
    # -------------------
    uploaded_cleanup: list[str] = []
    file_active_deadline_monotonic = (
        time.monotonic() + AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS
    )
    input_formats_allowed = settings.get("input_formats", None)
    use_group_context = bool(settings.get("use_group_context", True))
    use_project_context = bool(settings.get("use_project_context", True))

    reformatted_chat_history = reformat_chat_history(
        chat_history,
        user_id,
        db,
        client,
        uploaded_cleanup=uploaded_cleanup,
        project_id=project_id,
        native_youtube_video=settings.get("native_youtube_video", False),
        max_image_count=settings.get("max_image_count", None),
        max_video_count=settings.get("max_video_count", None),
        max_audio_count=settings.get("max_audio_count", None),
        max_document_count=settings.get("max_document_count", None),
        max_youtube_video_count=settings.get("max_youtube_video_count", None),
        input_formats_allowed=input_formats_allowed,
        use_group_context=use_group_context,
        use_project_context=use_project_context,
        note_ids=note_ids,
        reference_parts=reference_parts,
        chat_reference_context=chat_reference_context,
        video_metadata=video_metadata,
        file_active_deadline_monotonic=file_active_deadline_monotonic,
    )
    formatted_history = reformatted_chat_history.get("formatted", [])
    unsupported_file_ids = normalize_unsupported_file_ids(
        reformatted_chat_history.get("unsupported_file_ids")
    )
    if unsupported_file_ids:
        yield json.dumps({"t": "uf", "file_ids": unsupported_file_ids}) + "\n"
    cleanup_items = reformatted_chat_history.get("uploaded_cleanup") or []
    if cleanup_items is not uploaded_cleanup:
        for item in cleanup_items:
            if not item or item in uploaded_cleanup:
                continue
            uploaded_cleanup.append(item)

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
    function_declarations_schema: list[dict] = []
    tool_list: list[str] = []
    native_websearch_requested = _coerce_aistudio_native_websearch(
        settings.get("native_websearch")
    )
    if tools_flag:
        # Use the tools from merge_settings unless BYOK supplies explicit tools
        raw_tools = (
            byok.get("tools")
            if byok and isinstance(byok.get("tools"), (list, tuple, set, dict, str))
            else merged_tools
        )
        from app.tools.utils import resolve_enabled_tools

        resolve_enabled_tools_result = resolve_enabled_tools(
            raw_tools,
            db=db,
            model_settings=settings,
            user_id=user_id,
            byok=byok,
            project_id=project_id,
        )
        function_declarations_schema = (
            resolve_enabled_tools_result.get("tool_schemas", []) or []
        )
        tool_list = resolve_enabled_tools_result.get("tool_list", []) or []
        if resolve_enabled_tools_result.get("mcp_requested"):
            try:
                from app.mcp.utils import build_mcp_provider_bundle

                mcp_provider = (
                    byok.get("provider")
                    if isinstance(byok, dict) and isinstance(byok.get("provider"), str)
                    else getattr(db_model, "provider", None) or "google_aistudio"
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
                function_declarations_schema.extend(
                    mcp_bundle.get("bridge_tool_schemas", []) or []
                )
            except Exception:
                logger.exception("Failed to build MCP tools for Google AI Studio")
    native_websearch_enabled = native_websearch_requested and "web_search" in tool_list
    if native_websearch_enabled:
        tool_list = [name for name in tool_list if name != "web_search"]
    if tools_flag:
        settings["_runtime_enabled_tools"] = [
            *list(tool_list),
            *(["mcp"] if resolve_enabled_tools_result.get("mcp_requested") else []),
        ]
        settings["_runtime_origin_model_id"] = (
            "" if byok else str(getattr(db_model, "id", "") or "")
        )
    tools = _build_aistudio_tools_payload(
        function_declarations_schema,
        native_websearch_enabled=native_websearch_enabled,
    )

    # -------------------
    # Variables
    # -------------------
    thinking = ""
    thinking_time_already_started = False
    thinking_time_start = None
    content = ""
    meta_input_tokens = 0
    meta_input_text_tokens = 0
    meta_input_audio_tokens = 0
    meta_input_video_tokens = 0
    meta_input_image_tokens = 0
    meta_tool_use_prompt_tokens = 0
    meta_cached_input_tokens = 0
    meta_cached_input_text_tokens = 0
    meta_cached_input_audio_tokens = 0
    meta_cached_input_video_tokens = 0
    meta_cached_input_image_tokens = 0
    meta_output_tokens = 0
    meta_reasoning_tokens = 0
    meta_total_tokens = 0
    meta_input_tokens_cost = 0.0
    meta_cached_input_tokens_cost = 0.0
    meta_output_tokens_cost = 0.0
    meta_total_costs = 0.0
    meta_request_count = 0
    meta_total_thinking_time = 0.0
    meta_last_thinking_time = 0.0
    meta_thinking_signature = None
    meta_reasoning_part_signature = None
    meta_content_part_signature = None
    meta_time_to_first_token = None
    function_call = True
    max_calls = MAX_TOOL_CALLS_PER_GENERATION
    suppress_tools = False
    tool_error_tracker = ToolErrorTracker()
    content_generation_start = None
    request_start_time: datetime | None = None
    logged_tool_stat_keys: set[str] = set()
    tool_call_sequence = 0
    start_time = datetime.now(timezone.utc)
    meta_generation_success = False
    meta_generation_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""
    meta_tokens_per_second = None
    meta_native_websearch_tool_calls_count = 0
    meta_citations: list[dict[str, str]] = []
    model_identifier: str | None = None
    target_provider_id = (
        byok.get("provider_id")
        if isinstance(byok, dict) and byok.get("provider_id")
        else getattr(selected_provider, "id", None)
        or getattr(db_model, "provider_id", None)
        or "google_aistudio"
    )
    stream_tool_event_meta_cache: dict[str, dict[str, Any] | None] = {}

    def _accumulated_token_costs() -> dict[str, float]:
        """Return costs already calculated at each request's own price tier."""
        return {
            "input_tokens_cost": meta_input_tokens_cost,
            "cached_input_tokens_cost": meta_cached_input_tokens_cost,
            "output_tokens_cost": meta_output_tokens_cost,
            "total_costs": meta_total_costs,
        }

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

    def _record_generation_stat():
        try:
            resolved_model_name = (
                model_identifier
                or getattr(db_model, "model_name", None)
                or "google_aistudio"
            )
            meta_payload = {
                "generation_time": round(
                    (datetime.now(timezone.utc) - start_time).total_seconds(), 2
                ),
                "request_count": meta_request_count,
                "input_tokens": meta_input_tokens,
                "tool_use_prompt_tokens": meta_tool_use_prompt_tokens,
                "input_token_cached": meta_cached_input_tokens,
                "input_token_text": meta_input_text_tokens,
                "input_token_image": meta_input_image_tokens,
                "input_token_audio": meta_input_audio_tokens,
                "input_token_video": meta_input_video_tokens,
                "input_token_cached_text": meta_cached_input_text_tokens,
                "input_token_cached_image": meta_cached_input_image_tokens,
                "input_token_cached_audio": meta_cached_input_audio_tokens,
                "input_token_cached_video": meta_cached_input_video_tokens,
                "output_tokens": meta_output_tokens,
                "reasoning_tokens": meta_reasoning_tokens,
                "total_tokens": meta_total_tokens,
                "thinking_time": meta_last_thinking_time,
                "total_thinking_time": meta_total_thinking_time,
                "time_to_first_token": meta_time_to_first_token,
                "tokens_per_second": meta_tokens_per_second,
                "native_websearch_tool_calls_count": meta_native_websearch_tool_calls_count,
            }
            if meta_citations:
                meta_payload["citations"] = meta_citations
            if not byok:
                from app.llm.provider_groups import build_provider_group_resolution_meta

                meta_payload.update(
                    build_provider_group_resolution_meta(
                        db,
                        requested_provider_id,
                        selected_provider,
                    )
                )
            token_costs = _accumulated_token_costs()
            if token_costs:
                meta_payload["input_tokens_cost"] = token_costs.get(
                    "input_tokens_cost", 0
                )
                meta_payload["cached_input_tokens_cost"] = token_costs.get(
                    "cached_input_tokens_cost",
                    0,
                )
                meta_payload["output_tokens_cost"] = token_costs.get(
                    "output_tokens_cost", 0
                )
                meta_payload["total_costs"] = token_costs.get("total_costs", 0)
            create_llm_generation_statistic(
                db,
                model_name=resolved_model_name,
                model_id=getattr(db_model, "id", None)
                or model_identifier
                or "google_aistudio",
                provider="google_aistudio",
                provider_id=target_provider_id,
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

    web_search = False

    # New message format: accumulate content blocks
    messages_to_save = []
    last_message_type = "user"
    target_model_id = "byok" if byok else getattr(db_model, "id", None)
    assistant_message_saved = False
    native_tool_call_message_index: dict[str, int] = {}
    native_tool_response_ids: set[str] = set()
    google_model_turn_sequence = 0
    meta_citation_keys: set[tuple[str, str, str]] = set()

    def _signature_meta(signature: str | None) -> dict[str, Any]:
        """Place a Gemini thought signature only on its originating Part."""
        if not signature:
            return {}
        return {
            "thinking_signature": {
                "google_aistudio": signature,
            }
        }

    def _without_global_signature(meta_value: dict | None) -> dict[str, Any]:
        """Remove the legacy message-level signature from generic metrics."""
        cleaned = dict(meta_value or {})
        cleaned.pop("thinking_signature", None)
        return cleaned

    def _finalize_pending_assistant_message(meta_override: dict | None = None):
        nonlocal \
            messages_to_save, \
            content, \
            thinking, \
            last_message_type, \
            assistant_message_saved
        if temp_request_flag or assistant_message_saved:
            return None
        if target_model_id is None:
            return None

        if thinking:
            reasoning_meta = (
                {"reasoning_time": meta_last_thinking_time}
                if meta_last_thinking_time
                else {}
            )
            reasoning_meta.update(_without_global_signature(meta_override))
            reasoning_meta.update(_signature_meta(meta_reasoning_part_signature))
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
            content_meta = _without_global_signature(meta_override)
            content_meta.update(_signature_meta(meta_content_part_signature))
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
            last_meta.update(_without_global_signature(meta_override))

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

    try:
        while function_call and max_calls > 0:
            # Every request iteration is one Gemini model turn. Persist this
            # grouping on calls and results so parallel function calls can be
            # reconstructed without interleaving responses between them.
            google_model_turn_sequence += 1
            google_model_turn_id = f"model-turn:{google_model_turn_sequence}"
            function_call = False
            thinking_already_added = False
            meta_time_to_first_token = None
            request_start_time = None

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
            model_identifier = model_name

            # -------------------
            # Request
            # -------------------
            request_start_time = datetime.now(timezone.utc)
            response = client.models.generate_content_stream(
                model=model_name,
                contents=formatted_history,
                config=build_aistudio_generate_content_config(
                    settings,
                    system_instruction=system_instruction,
                    temperature=settings.get("temperature", None),
                    top_p=settings.get("top_p", None),
                    top_k=settings.get("top_k", None),
                    max_output_tokens=settings.get("max_output_tokens", None),
                    stop_sequences=settings.get("stop_sequences", None),
                    presence_penalty=settings.get("presence_penalty", None),
                    frequency_penalty=settings.get("frequency_penalty", None),
                    seed=settings.get("seed", None),
                    media_resolution=settings.get("media_resolution", None),
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=settings.get("include_thinking", True),
                        thinking_budget=settings.get("thinking_budget", -1),
                    ),
                    tools=[] if suppress_tools else tools,
                    include_server_side_tool_invocations=True
                    if native_websearch_enabled
                    else None,
                ),
            )

            for chunk in interruptible_provider_stream(
                response,
                generation_id,
                close_resource=response,
            ):
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
                                    "request_count": meta_request_count,
                                    "input_tokens": meta_input_tokens,
                                    "tool_use_prompt_tokens": meta_tool_use_prompt_tokens,
                                    "input_token_cached": meta_cached_input_tokens,
                                    "input_token_text": meta_input_text_tokens,
                                    "input_token_image": meta_input_image_tokens,
                                    "input_token_audio": meta_input_audio_tokens,
                                    "input_token_video": meta_input_video_tokens,
                                    "input_token_cached_text": meta_cached_input_text_tokens,
                                    "input_token_cached_image": meta_cached_input_image_tokens,
                                    "input_token_cached_audio": meta_cached_input_audio_tokens,
                                    "input_token_cached_video": meta_cached_input_video_tokens,
                                    "output_tokens": meta_output_tokens,
                                    "reasoning_tokens": meta_reasoning_tokens,
                                    "total_tokens": meta_total_tokens,
                                    "thinking_time": meta_last_thinking_time,
                                    "total_thinking_time": meta_total_thinking_time,
                                }
                                for key, value in additional_meta.items():
                                    if value not in (None, 0, "", [], {}):
                                        cancellation_meta[key] = value
                                cancellation_meta["timestamp"] = format_meta_timestamp()
                                meta_generation_success = True
                                # Flush any pending content/thinking to messages_to_save
                                if thinking and last_message_type != "reasoning":
                                    messages_to_save.append(
                                        {
                                            "type": "reasoning",
                                            "content": thinking,
                                            "meta": {
                                                "reasoning_time": meta_last_thinking_time
                                            }
                                            if meta_last_thinking_time
                                            else {},
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
                                    messages_to_save[-1]["meta"] = cancellation_meta
                                if messages_to_save:
                                    create_chat_message(
                                        db,
                                        chat_id,
                                        target_model_id,
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
                candidate = None
                try:
                    candidate = chunk.candidates[0]
                except (AttributeError, IndexError, TypeError):
                    continue
                content_obj = getattr(candidate, "content", None)
                parts_iterable = getattr(content_obj, "parts", None) or []

                for part in parts_iterable:
                    thought_signature = getattr(part, "thought_signature", None)
                    part_thinking_signature = None
                    if thought_signature:
                        if isinstance(thought_signature, bytes):
                            part_thinking_signature = base64.b64encode(
                                thought_signature
                            ).decode("ascii")
                        else:
                            part_thinking_signature = str(thought_signature)
                        meta_thinking_signature = part_thinking_signature
                    tool_call_part = getattr(part, "tool_call", None)
                    if tool_call_part and _is_google_search_server_tool(tool_call_part):
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
                        content_generation_start = None
                        web_search = True
                        query_payload = _extract_aistudio_query_payload(tool_call_part)
                        tool_call_identifier = (
                            getattr(tool_call_part, "id", None)
                            or getattr(tool_call_part, "call_id", None)
                            or f"google_search:{meta_native_websearch_tool_calls_count + 1}"
                        )
                        if tool_call_identifier not in native_tool_call_message_index:
                            meta_native_websearch_tool_calls_count += 1
                        if (not thinking_already_added and (thinking or content)) and (
                            not temp_request_flag
                        ):
                            if thinking:
                                reasoning_meta = (
                                    {"reasoning_time": meta_last_thinking_time}
                                    if meta_last_thinking_time
                                    else {}
                                )
                                reasoning_meta.update(
                                    _signature_meta(meta_reasoning_part_signature)
                                )
                                messages_to_save.append(
                                    {
                                        "type": "reasoning",
                                        "content": thinking,
                                        "meta": reasoning_meta,
                                    }
                                )
                                last_message_type = "reasoning"
                            if content:
                                messages_to_save.append(
                                    {
                                        "type": "content",
                                        "content": content,
                                        "meta": _signature_meta(
                                            meta_content_part_signature
                                        ),
                                    }
                                )
                                last_message_type = "content"
                            content = ""
                            thinking = ""
                            thinking_already_added = True
                        tool_event_payload = {"t": "t_c", "d": "web_search"}
                        if query_payload not in (None, "", {}):
                            tool_event_payload["c"] = (
                                {"query": query_payload}
                                if isinstance(query_payload, str)
                                else query_payload
                            )
                        yield json.dumps(tool_event_payload) + "\n"
                        if not temp_request_flag:
                            tool_payload = _safe_aistudio_dict(tool_call_part)
                            if not tool_payload:
                                tool_payload = (
                                    {"query": query_payload}
                                    if query_payload
                                    else {"tool_type": "google_search"}
                                )
                            try:
                                tool_content = json.dumps(
                                    tool_payload,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                            except TypeError:
                                tool_content = str(tool_payload)
                            tool_meta = {
                                "native_web_search": True,
                                "google_aistudio_model_turn_id": google_model_turn_id,
                            }
                            if query_payload:
                                tool_meta["query"] = query_payload
                            if part_thinking_signature:
                                tool_meta["thinking_signature"] = {
                                    "google_aistudio": part_thinking_signature,
                                }
                            messages_to_save.append(
                                build_tool_call_block(
                                    "web_search",
                                    tool_content,
                                    tool_call_id=tool_call_identifier,
                                    extra_meta=tool_meta,
                                )
                            )
                            native_tool_call_message_index[tool_call_identifier] = (
                                len(messages_to_save) - 1
                            )
                            last_message_type = "tool_call"
                        continue

                    tool_response_part = getattr(part, "tool_response", None)
                    if tool_response_part and _is_google_search_server_tool(
                        tool_response_part
                    ):
                        web_search = True
                        raw_response_identifier = getattr(
                            tool_response_part, "id", None
                        ) or getattr(tool_response_part, "call_id", None)
                        response_identifier = (
                            str(raw_response_identifier).strip()
                            if raw_response_identifier is not None
                            else ""
                        )
                        if (
                            response_identifier
                            and response_identifier not in native_tool_response_ids
                            and not temp_request_flag
                        ):
                            response_payload = _safe_aistudio_dict(tool_response_part)
                            messages_to_save.append(
                                {
                                    "type": "tool_call_result",
                                    "content": json.dumps(
                                        response_payload,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    ),
                                    "tool_name": "web_search",
                                    "meta": {
                                        "native_web_search": True,
                                        "tool_call_id": response_identifier,
                                        "google_aistudio_model_turn_id": google_model_turn_id,
                                    },
                                }
                            )
                            native_tool_response_ids.add(response_identifier)
                            last_message_type = "tool_call_result"
                        citations = _extract_google_grounding_citations(
                            tool_response_part, candidate, chunk
                        )
                        if citations:
                            for citation in citations:
                                dedupe_key = (
                                    str(citation.get("url", "")),
                                    str(citation.get("title", "")),
                                    str(citation.get("snippet", "")),
                                )
                                if dedupe_key in meta_citation_keys:
                                    continue
                                meta_citation_keys.add(dedupe_key)
                                meta_citations.append(citation)
                            message_index = native_tool_call_message_index.get(
                                response_identifier
                            )
                            if message_index is not None and 0 <= message_index < len(
                                messages_to_save
                            ):
                                block_meta = messages_to_save[message_index].setdefault(
                                    "meta", {}
                                )
                                block_meta["citations"] = citations
                        continue

                    if getattr(part, "function_call", None):
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
                        content_generation_start = None
                        max_calls -= 1
                        meta = {}
                        if meta_thinking_signature:
                            meta.update(
                                {
                                    "thinking_signature": {
                                        "google_aistudio": meta_thinking_signature
                                    }
                                }
                            )
                        tool_meta = (
                            {"thinking_time": meta_last_thinking_time}
                            if meta_last_thinking_time
                            else None
                        )
                        if tool_meta:
                            meta.update(tool_meta)
                        if (not thinking_already_added and (thinking or content)) and (
                            not temp_request_flag
                        ):
                            # Accumulate reasoning and content blocks before tool call
                            if thinking:
                                reasoning_meta = (
                                    {"reasoning_time": meta_last_thinking_time}
                                    if meta_last_thinking_time
                                    else {}
                                )
                                reasoning_meta.update(
                                    _signature_meta(meta_reasoning_part_signature)
                                )
                                messages_to_save.append(
                                    {
                                        "type": "reasoning",
                                        "content": thinking,
                                        "meta": reasoning_meta,
                                    }
                                )
                                last_message_type = "reasoning"
                            if content:
                                content_meta = _without_global_signature(meta)
                                content_meta.update(
                                    _signature_meta(meta_content_part_signature)
                                )
                                messages_to_save.append(
                                    {
                                        "type": "content",
                                        "content": content,
                                        "meta": content_meta,
                                    }
                                )
                                last_message_type = "content"
                            content = ""
                            thinking = ""
                            thinking_already_added = True
                        function_call = True
                        function_call_part = getattr(part, "function_call", None)
                        name = getattr(function_call_part, "name", None)
                        args = getattr(function_call_part, "args", {}) or {}
                        hidden_from_user = should_hide_tool_call_from_user(name, args)
                        tool_call_sequence += 1
                        tool_call_identifier = getattr(
                            function_call_part, "id", None
                        ) or getattr(function_call_part, "call_id", None)
                        if tool_call_identifier is None:
                            tool_call_identifier = (
                                f"{name or 'unknown'}:{tool_call_sequence}"
                            )
                        # Add tool_call block
                        if not temp_request_flag and not hidden_from_user:
                            tool_call_meta = {
                                "google_aistudio_model_turn_id": google_model_turn_id,
                            }
                            if part_thinking_signature:
                                tool_call_meta["thinking_signature"] = {
                                    "google_aistudio": part_thinking_signature,
                                }
                            messages_to_save.append(
                                build_tool_call_block(
                                    name,
                                    args,
                                    tool_call_id=tool_call_identifier,
                                    extra_meta=tool_call_meta,
                                )
                            )
                            last_message_type = "tool_call"

                        hide_tool_arguments = name in tools_not_yield_arguments
                        if not hidden_from_user:
                            tool_event_descriptor = {
                                "id": tool_call_identifier,
                                "name": name,
                            }
                            if not hide_tool_arguments:
                                tool_event_descriptor["args"] = args
                            stream_meta = get_stream_tool_event_meta(
                                name,
                                tool_call_id=tool_call_identifier,
                            )
                            if stream_meta:
                                tool_event_descriptor["meta"] = stream_meta
                            tool_event_payload = {
                                "t": "t_c",
                                "d": tool_event_descriptor,
                            }
                            if not hide_tool_arguments:
                                tool_event_payload["c"] = args
                            yield json.dumps(tool_event_payload) + "\n"
                        try:
                            if content_obj:
                                formatted_history.append(content_obj)
                            if name in tool_list:
                                documents = []
                                images = []
                                videos = []
                                audios = []
                                youtube = []
                                webpages: list = []
                                result = None
                                content = ""

                                helper_payload: dict[str, Any] = {}
                                helper_gen = None
                                tool_error_message: str | None = None
                                tool_error_response: ToolErrorResponse | None = None
                                try:
                                    from app.tools.helper import resolve_tool_call

                                    helper_gen = resolve_tool_call(
                                        db,
                                        name,
                                        args,
                                        user_id,
                                        None,
                                        project_id,
                                        model_settings=settings,
                                        byok=byok,
                                        chat_id=chat_id,
                                        chat_history=chat_history,
                                        generation_id=generation_id,
                                        user_role=user_role_normalized,
                                        tool_call_id=tool_call_identifier,
                                    )
                                except Exception as tool_exc:
                                    tool_error_message = str(tool_exc)
                                    tool_error_response = tool_error_tracker.record(
                                        name, tool_exc
                                    )
                                    logger.exception(
                                        "Tool %s failed to start: %s", name, tool_exc
                                    )

                                tool_stat_kwargs = {
                                    "db": db,
                                    "tool_name": name or "unknown",
                                    "model_id": target_model_id,
                                    "model_name": model_name,
                                    "provider": target_provider_id,
                                    "user_id": user_id,
                                    "is_byok": bool(byok),
                                }

                                def _log_tool_stat_once(
                                    success: bool,
                                    error_message: str | None,
                                    meta: dict | None = None,
                                ):
                                    identifier = (
                                        tool_call_identifier
                                        or f"{(name or 'unknown')}:{tool_call_sequence}"
                                    )
                                    if identifier in logged_tool_stat_keys:
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
                                    finally:
                                        logged_tool_stat_keys.add(identifier)

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
                                            name, tool_exc
                                        )
                                        logger.exception(
                                            "Tool %s raised during execution: %s",
                                            name,
                                            tool_exc,
                                        )

                                if tool_error_message:
                                    if tool_error_response is None:
                                        tool_error_response = tool_error_tracker.record(
                                            name,
                                            RuntimeError(tool_error_message),
                                        )
                                    result = tool_error_response.result_payload
                                    content = tool_error_response.model_output
                                    helper_payload = {}
                                    if tool_error_response.stop_tool_calls:
                                        suppress_tools = True
                                    documents = []
                                    images = []
                                    videos = []
                                    audios = []
                                    youtube = []
                                    webpages = []
                                    try:
                                        _log_tool_stat_once(
                                            False,
                                            tool_error_message,
                                            meta=tool_error_response.statistic_meta,
                                        )
                                    except Exception:
                                        pass
                                else:
                                    content = helper_payload.get("content", "")
                                    documents = helper_payload.get("documents") or []
                                    images = helper_payload.get("images") or []
                                    videos = helper_payload.get("videos") or []
                                    audios = helper_payload.get("audios") or []
                                    youtube = helper_payload.get("youtube") or []
                                    webpages = helper_payload.get("webpages") or []
                                    result = helper_payload.get("result")

                                file_ids = images + videos + audios + documents
                                if tool_error_message is None and file_ids:
                                    uploaded_files = upload_files(
                                        db,
                                        client,
                                        file_ids,
                                        user_id,
                                        uploaded_cleanup,
                                        input_formats_allowed=input_formats_allowed,
                                        video_metadata=video_metadata,
                                        file_active_deadline_monotonic=file_active_deadline_monotonic,
                                    )
                                    parts = uploaded_files["parts"]
                                    if parts:
                                        formatted_history.append(
                                            types.Content(role="user", parts=parts)
                                        )
                                    uploaded_cleanup.extend(
                                        uploaded_files["uploaded_cleanup"]
                                    )

                                youtube_urls = []
                                for yt_entry in youtube:
                                    url = (
                                        yt_entry.get("url")
                                        if isinstance(yt_entry, dict)
                                        else None
                                    )
                                    if url:
                                        youtube_urls.append(url)
                                if (
                                    settings.get("native_youtube_video", False)
                                    and youtube_urls
                                ):
                                    for yt_url in youtube_urls:
                                        formatted_history.append(
                                            types.Content(
                                                role="model",
                                                parts=[
                                                    _build_aistudio_file_part(
                                                        file_uri=yt_url,
                                                        video_metadata=video_metadata,
                                                    )
                                                ],
                                            )
                                        )

                                # Store tool name together with arguments for clarity, e.g. name({json-args})
                                args_str = (
                                    json.dumps(
                                        args, ensure_ascii=False, separators=(",", ":")
                                    )
                                    if isinstance(args, dict)
                                    else str(args)
                                )
                                tool_label = (
                                    f"{name}({args_str})" if name else "unknown"
                                )

                                widget_data = helper_payload.get("widget")
                                persisted_tool_content = (
                                    stringify_tool_result_content_for_persistence(
                                        name,
                                        helper_payload.get("result")
                                        if helper_payload.get("result")
                                        not in (None, "")
                                        else content,
                                        widget_data,
                                    )
                                )

                                content_str = persisted_tool_content or (
                                    webpages if webpages else None
                                )

                                if not temp_request_flag and not hidden_from_user:
                                    persist_files_in_file_block = (
                                        should_persist_files_in_file_block(name)
                                    )
                                    tool_result_block = {
                                        "type": "tool_call_result",
                                        "content": content_str,
                                        "tool_name": tool_label,
                                        "meta": {
                                            "tool_call_id": tool_call_identifier,
                                            "google_aistudio_model_turn_id": google_model_turn_id,
                                        },
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
                                    if name == "web_search" and webpages:
                                        citations = build_web_search_citations(webpages)
                                        if citations:
                                            tool_result_block.setdefault("meta", {})[
                                                "citations"
                                            ] = citations
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
                                            tool_name=name,
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
                                                    tool_name=name,
                                                ),
                                            }
                                        )
                                        last_message_type = "widget"

                                else:
                                    pass
                                if tool_error_message is None:
                                    try:
                                        _log_tool_stat_once(
                                            True,
                                            None,
                                            meta=helper_payload.get("tool_meta"),
                                        )
                                    except Exception:
                                        pass
                            else:
                                result = {
                                    "error": f"Tool '{name}' is not allowed or not available"
                                }

                            result_to_append = (
                                result
                                if result is not None
                                else (webpages + youtube if youtube else webpages)
                            )
                            if not result_to_append:
                                result_to_append = "success"
                            function_response_part = types.Part(
                                function_response=types.FunctionResponse(
                                    id=str(tool_call_identifier),
                                    name=name,
                                    response={"result": result_to_append},
                                )
                            )
                            formatted_history.append(
                                types.Content(
                                    role="user", parts=[function_response_part]
                                )
                            )

                            content = ""
                            meta_thinking_signature = None
                            meta_reasoning_part_signature = None
                            meta_content_part_signature = None
                        except Exception as e:
                            is_admin = is_admin_role(user_role_normalized)
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
                        continue

                    if part.thought:
                        if part_thinking_signature:
                            meta_reasoning_part_signature = part_thinking_signature
                        # Track generation start time on first streamed output event (thoughts or text).
                        if content_generation_start is None:
                            content_generation_start = datetime.now(timezone.utc)
                        if not thinking_time_already_started:
                            thinking_time_start = datetime.now(timezone.utc)
                            thinking_time_already_started = True
                        yield json.dumps({"t": "r", "d": part.text}) + "\n"
                        thinking += part.text
                    elif not part.text:
                        continue
                    else:
                        if part_thinking_signature:
                            meta_content_part_signature = part_thinking_signature
                        # Track content generation start time (for tokens_per_second calculation)
                        if content_generation_start is None:
                            content_generation_start = datetime.now(timezone.utc)

                        if (
                            meta_time_to_first_token is None
                            and request_start_time is not None
                        ):
                            meta_time_to_first_token = (
                                datetime.now(timezone.utc) - request_start_time
                            ).total_seconds()

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
                        yield json.dumps({"t": "c", "d": part.text}) + "\n"
                        content += part.text

                if getattr(candidate, "finish_reason", None):
                    final_citations = _extract_google_grounding_citations(
                        candidate, chunk
                    )
                    if final_citations:
                        for citation in final_citations:
                            dedupe_key = (
                                str(citation.get("url", "")),
                                str(citation.get("title", "")),
                                str(citation.get("snippet", "")),
                            )
                            if dedupe_key in meta_citation_keys:
                                continue
                            meta_citation_keys.add(dedupe_key)
                            meta_citations.append(citation)
                    # Capture usage metadata first
                    um = getattr(chunk, "usage_metadata", None)
                    # Calculate tokens per second for content generation only
                    end_time = datetime.now(timezone.utc)
                    if content_generation_start is not None:
                        content_generation_time = (
                            end_time - content_generation_start
                        ).total_seconds()
                        out_tokens = int(getattr(um, "candidates_token_count", 0) or 0)
                        tokens_per_second = (
                            (out_tokens / content_generation_time)
                            if content_generation_time > 0
                            else 0
                        )
                    else:
                        tokens_per_second = 0

                    if um:
                        usage_meta = normalize_aistudio_usage_metadata(um)
                        meta_input_tokens += usage_meta["input_tokens"]
                        meta_tool_use_prompt_tokens += usage_meta[
                            "tool_use_prompt_tokens"
                        ]
                        meta_cached_input_tokens += usage_meta["input_token_cached"]
                        meta_input_text_tokens += usage_meta["input_token_text"]
                        meta_input_image_tokens += usage_meta["input_token_image"]
                        meta_input_audio_tokens += usage_meta["input_token_audio"]
                        meta_input_video_tokens += usage_meta["input_token_video"]
                        meta_cached_input_text_tokens += usage_meta[
                            "input_token_cached_text"
                        ]
                        meta_cached_input_image_tokens += usage_meta[
                            "input_token_cached_image"
                        ]
                        meta_cached_input_audio_tokens += usage_meta[
                            "input_token_cached_audio"
                        ]
                        meta_cached_input_video_tokens += usage_meta[
                            "input_token_cached_video"
                        ]
                        meta_output_tokens += usage_meta["output_tokens"]
                        meta_reasoning_tokens += usage_meta["reasoning_tokens"]
                        meta_total_tokens += usage_meta["total_tokens"]
                        # Price each API request independently. The Gemini 200k
                        # tier is per prompt, not based on the sum of a tool loop.
                        request_costs = calculate_aistudio_token_costs(
                            model_name,
                            input_tokens_total=usage_meta["input_tokens"],
                            input_text_tokens=usage_meta["input_token_text"],
                            input_image_tokens=usage_meta["input_token_image"],
                            input_audio_tokens=usage_meta["input_token_audio"],
                            input_video_tokens=usage_meta["input_token_video"],
                            cached_input_tokens=usage_meta["input_token_cached"],
                            cached_input_text_tokens=usage_meta[
                                "input_token_cached_text"
                            ],
                            cached_input_image_tokens=usage_meta[
                                "input_token_cached_image"
                            ],
                            cached_input_audio_tokens=usage_meta[
                                "input_token_cached_audio"
                            ],
                            cached_input_video_tokens=usage_meta[
                                "input_token_cached_video"
                            ],
                            output_tokens=usage_meta["output_tokens"],
                            reasoning_tokens=usage_meta["reasoning_tokens"],
                        )
                        if request_costs:
                            meta_input_tokens_cost += request_costs.get(
                                "input_tokens_cost",
                                0,
                            )
                            meta_cached_input_tokens_cost += request_costs.get(
                                "cached_input_tokens_cost",
                                0,
                            )
                            meta_output_tokens_cost += request_costs.get(
                                "output_tokens_cost",
                                0,
                            )
                            meta_total_costs += request_costs.get("total_costs", 0)
                    meta_request_count += 1
                    if not function_call:
                        meta_tokens_per_second = (
                            round(tokens_per_second, 2) if tokens_per_second else None
                        )
                        meta_metrics = {
                            "input_tokens": meta_input_tokens,
                            "tool_use_prompt_tokens": meta_tool_use_prompt_tokens,
                            "input_token_cached": meta_cached_input_tokens,
                            "input_token_text": meta_input_text_tokens,
                            "input_token_image": meta_input_image_tokens,
                            "input_token_audio": meta_input_audio_tokens,
                            "input_token_video": meta_input_video_tokens,
                            "input_token_cached_text": meta_cached_input_text_tokens,
                            "input_token_cached_image": meta_cached_input_image_tokens,
                            "input_token_cached_audio": meta_cached_input_audio_tokens,
                            "input_token_cached_video": meta_cached_input_video_tokens,
                            "output_tokens": meta_output_tokens,
                            "reasoning_tokens": meta_reasoning_tokens,
                            "total_tokens": meta_total_tokens,
                            "request_count": meta_request_count,
                            "thinking_time": meta_last_thinking_time,
                            "total_thinking_time": meta_total_thinking_time,
                            "tokens_per_second": meta_tokens_per_second,
                            "time_to_first_token": meta_time_to_first_token,
                            "native_websearch_tool_calls_count": meta_native_websearch_tool_calls_count,
                        }
                        meta = {"model": model_name}
                        token_costs = _accumulated_token_costs()
                        if token_costs:
                            meta_metrics["input_tokens_cost"] = token_costs.get(
                                "input_tokens_cost", 0
                            )
                            meta_metrics["cached_input_tokens_cost"] = token_costs.get(
                                "cached_input_tokens_cost",
                                0,
                            )
                            meta_metrics["output_tokens_cost"] = token_costs.get(
                                "output_tokens_cost", 0
                            )
                            meta_metrics["total_costs"] = token_costs.get(
                                "total_costs", 0
                            )
                        meta.update({k: v for k, v in meta_metrics.items() if v})
                        if meta_thinking_signature:
                            meta.update(
                                {
                                    "thinking_signature": {
                                        "google_aistudio": meta_thinking_signature
                                    }
                                }
                            )
                        all_citations = list(meta_citations) if meta_citations else []
                        tool_citations = collect_tool_result_citations(messages_to_save)
                        if tool_citations:
                            all_citations.extend(tool_citations)
                        if all_citations:
                            meta["citations"] = all_citations
                        for key, value in assistant_metadata.items():
                            if value not in (None, "", [], {}):
                                meta[key] = value
                        meta["timestamp"] = format_meta_timestamp()
                        yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                        if not temp_request_flag:
                            saved_assistant_id = _finalize_pending_assistant_message(
                                meta
                            )
                            if saved_assistant_id:
                                yield (
                                    json.dumps({"t": "a_id", "d": saved_assistant_id})
                                    + "\n"
                                )
                        meta_generation_success = True
                        return
    except genai_errors.ClientError as exc:
        meta_error = True
        meta_generation_error = True
        meta_error_status_code = getattr(exc, "code", 0)
        meta_error_message = getattr(exc, "message", str(exc))
        meta_error_type = getattr(exc, "status", str(exc)) or exc.__class__.__name__
        is_admin = is_admin_role(user_role_normalized)
        error_message = (
            meta_error_message
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"

    except Exception as e:
        # Emit final error line if the stream itself fails
        meta_generation_error = True
        meta_error_message = str(e)
        meta_error_type = e.__class__.__name__
        is_admin = is_admin_role(user_role_normalized)
        error_message = (
            str(e)
            if is_admin
            else "An error occurred during generation. Please try again."
        )
        yield json.dumps({"t": "e", "d": error_message}) + "\n"
        yield json.dumps({"t": "d", "d": "c", "c": {"status": "error"}}) + "\n"
    finally:
        if (
            not assistant_message_saved
            and not temp_request_flag
            and (content or thinking or messages_to_save)
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
        # Best-effort cleanup of any Files API uploads we created for this request
        if uploaded_cleanup:
            seen_cleanup_names = set()
            for _name in uploaded_cleanup:
                if not _name or _name in seen_cleanup_names:
                    continue
                seen_cleanup_names.add(_name)
                try:
                    client.files.delete(name=_name)
                except Exception:
                    pass
