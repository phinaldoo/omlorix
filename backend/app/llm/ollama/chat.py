"""Ollama chat orchestration and streaming.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

from app.llm.generation.engine import chat_adapter, ProviderCall, stream_tool_call

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import utils as _compat_source
from app.llm.helper import sanitize_tool_call_arguments_object_for_history
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION

_COMPAT_DEPENDENCIES = {
    "ollama_chat": (
        "HTTPException",
        "OllamaModelSettings",
        "RemoteProtocolError",
        "ResponseError",
        "ToolErrorTracker",
        "_resolve_ollama_input_formats",
        "_resolve_ollama_think_value",
        "_resolve_ollama_tool_call_id",
        "append_system_instruction_sections",
        "build_stream_tool_event_meta",
        "build_tool_call_block",
        "build_tool_file_block",
        "build_web_search_citations",
        "build_widget_block_meta",
        "check_ollama_version",
        "collect_tool_result_citations",
        "create_llm_generation_statistic",
        "create_tool_call_statistic",
        "datetime",
        "format_meta_timestamp",
        "format_tool_call_block_label",
        "get_default_system_instruction",
        "get_model_capabilities",
        "get_ollama_client",
        "httpx",
        "interruptible_provider_stream",
        "is_admin_role",
        "json",
        "jsonable_encoder",
        "logger",
        "normalize_unsupported_file_ids",
        "reformat_chat_history",
        "requests",
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
    "HTTPException",
    "OllamaModelSettings",
    "RemoteProtocolError",
    "ResponseError",
    "ToolErrorTracker",
    "_resolve_ollama_input_formats",
    "_resolve_ollama_think_value",
    "_resolve_ollama_tool_call_id",
    "append_system_instruction_sections",
    "build_stream_tool_event_meta",
    "build_tool_call_block",
    "build_tool_file_block",
    "build_web_search_citations",
    "build_widget_block_meta",
    "check_ollama_version",
    "collect_tool_result_citations",
    "create_llm_generation_statistic",
    "create_tool_call_statistic",
    "datetime",
    "format_meta_timestamp",
    "format_tool_call_block_label",
    "get_default_system_instruction",
    "get_model_capabilities",
    "get_ollama_client",
    "httpx",
    "interruptible_provider_stream",
    "is_admin_role",
    "json",
    "jsonable_encoder",
    "logger",
    "normalize_unsupported_file_ids",
    "reformat_chat_history",
    "requests",
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


@chat_adapter
def _impl_ollama_chat(
    chat_id: str,
    chat_history: list[dict],
    db,
    db_model: Models | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    generation_id: str | None = None,
    temp_request_flag: bool = False,
    byok: dict | None = None,
    settings_override: dict | None = None,
    vision_gating: bool = False,
    reference_id: str | None = None,
    skill_content: str | None = None,
    system_instruction_sections: list[dict[str, str]] | None = None,
    assistant_metadata: dict | None = None,
    note_ids: list[str] | None = None,
    retry_count: int | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    user_role: str | None = None,
    engine=None,
):
    assistant_metadata = (
        assistant_metadata if isinstance(assistant_metadata, dict) else {}
    )
    try:
        create_chat_message = engine.persist_message

        # -------------------
        # Client (deferred until streaming timeout computed)
        # -------------------
        if not byok and (not db_model or not db_model.provider_id):
            raise HTTPException(status_code=422, detail="Provider not configured")
        resolved_provider = None
        requested_provider_id = (
            getattr(db_model, "provider_id", None) if db_model else None
        )

        # -------------------
        # Settings
        # -------------------
        settings: dict = {}
        override = settings_override if isinstance(settings_override, dict) else {}
        if isinstance(override.get("settings"), dict):
            flattened_override = dict(override)
            nested = flattened_override.pop("settings")
            flattened_override.update(nested)
            override = flattened_override
        db_settings: dict = {}
        byok_settings: dict = {}
        if db_model:
            try:
                db_settings = db_model.settings or {}
                if not isinstance(db_settings, dict):
                    db_settings = {}
            except Exception:
                db_settings = {}
        if byok:
            try:
                byok_settings = byok.get("settings") or {}
                if not isinstance(byok_settings, dict):
                    byok_settings = {}
            except Exception:
                byok_settings = {}
        try:
            schema_keys = set(getattr(OllamaModelSettings, "model_fields", {}).keys())
            key_set = (
                set(schema_keys)
                | set(override.keys())
                | set(db_settings.keys())
                | set(byok_settings.keys())
            )
            merged: dict = {}
            for key in key_set:
                if key in override and override.get(key) is not None:
                    merged[key] = override.get(key)
                elif key in db_settings and db_settings.get(key) is not None:
                    merged[key] = db_settings.get(key)
                elif key in byok_settings and byok_settings.get(key) is not None:
                    merged[key] = byok_settings.get(key)
                else:
                    merged[key] = None
            settings = merged
        except Exception:
            settings = override or db_settings or byok_settings or {}

        # -------------------
        # Tools
        # -------------------
        if db_model:
            capabilities = db_model.capabilities
        elif byok:
            capabilities = get_model_capabilities(
                db,
                byok.get("model_name"),
                byok_base_url=byok.get("base_url"),
                byok_api_key=byok.get("api_key"),
            )
        has_tools = (isinstance(capabilities, list) and ("tools" in capabilities)) or (
            isinstance(capabilities, dict) and bool(capabilities.get("tools"))
        )
        tool_list: list[str] = []
        tool_specs: list[dict] = []
        tool_schemas = None
        allowed_tools_set = set()
        if has_tools and db_model:  # TODO, Tools for BYOK
            raw_tools = getattr(db_model, "tools", None)
            from app.tools.utils import resolve_enabled_tools

            resolution = resolve_enabled_tools(
                raw_tools,
                db=db,
                model_settings=getattr(db_model, "settings", None),
                user_id=user_id,
                project_id=project_id,
            )
            tool_list = (
                resolution.get("tool_list", []) if isinstance(resolution, dict) else []
            )
            if isinstance(settings, dict):
                settings["_runtime_enabled_tools"] = [
                    *list(tool_list),
                    *(
                        ["mcp"]
                        if isinstance(resolution, dict)
                        and resolution.get("mcp_requested")
                        else []
                    ),
                ]
                settings["_runtime_origin_model_id"] = (
                    "" if byok else str(getattr(db_model, "id", "") or "")
                )
            tool_specs = (
                resolution.get("tool_schemas", [])
                if isinstance(resolution, dict)
                else []
            )
            if isinstance(resolution, dict) and resolution.get("mcp_requested"):
                try:
                    from app.mcp.utils import build_mcp_provider_bundle

                    mcp_bundle = build_mcp_provider_bundle(
                        db,
                        provider=getattr(db_model, "provider", None) or "ollama",
                        user_id=user_id,
                        # Use the merged request settings so conversation-level
                        # enabled_mcp_servers selections match every provider.
                        model_settings=settings,
                    )
                    for name in mcp_bundle.get("bridge_tool_names", []) or []:
                        if name not in tool_list:
                            tool_list.append(name)
                    tool_specs = list(tool_specs) + list(
                        mcp_bundle.get("bridge_tool_schemas", []) or []
                    )
                except Exception:
                    logger.exception("Failed to build MCP tools for Ollama")
            tool_schemas = []
            for tool in tool_specs:
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
            allowed_tools_set = set(tool_list)
        request_model_name = (
            byok.get("model_name") if byok else getattr(db_model, "model_name", None)
        )
        think_flag = _resolve_ollama_think_value(
            request_model_name, capabilities, settings
        )

        # -------------------
        # Chat History
        # -------------------
        capabilities = []
        if db_model and isinstance(getattr(db_model, "capabilities", None), list):
            capabilities = db_model.capabilities
        input_formats_allowed = _resolve_ollama_input_formats(
            settings.get("input_formats", None), capabilities
        )

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
            include_tool_content=True,
            project_id=project_id,
            input_formats_allowed=input_formats_allowed,
            use_group_context=use_group_context,
            use_project_context=use_project_context,
            max_image_count=settings.get("max_image_count"),
            max_document_count=settings.get("max_document_count"),
            note_ids=note_ids,
            reference_parts=reference_parts,
            chat_reference_context=chat_reference_context,
        )
        engine.context.prefix_count = (
            reformat_result.get("context_prefix_count", 0)
            if isinstance(reformat_result, dict)
            else 0
        )
        engine.context.prefix_sections = (
            reformat_result.get("context_sections", [])
            if isinstance(reformat_result, dict)
            else []
        )
        formatted_history = (
            reformat_result.get("formatted", reformat_result)
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

        def _stringify_tool_payload(value):
            if value is None:
                return ""
            try:
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)
            except TypeError:
                try:
                    encoded = jsonable_encoder(value)
                    return json.dumps(encoded, ensure_ascii=False)
                except Exception:
                    pass
            return str(value)

        def _extract_tool_assets(payload):
            documents: set[str] = set()
            images: set[str] = set()
            videos: set[str] = set()
            audios: set[str] = set()
            youtube: list = []

            def _collect_from_content(content):
                if not isinstance(content, dict):
                    return
                for key, target in (
                    ("documents", documents),
                    ("images", images),
                    ("videos", videos),
                    ("audios", audios),
                ):
                    values = content.get(key)
                    if isinstance(values, list):
                        for item in values:
                            if item is not None:
                                target.add(str(item))
                yt_values = content.get("youtube")
                if isinstance(yt_values, list):
                    for entry in yt_values:
                        youtube.append(entry)

            def _walk(node):
                if isinstance(node, dict):
                    _collect_from_content(node)
                    for value in node.values():
                        _walk(value)
                elif isinstance(node, list):
                    for item in node:
                        _walk(item)

            _walk(payload)

            return {
                "documents": list(documents) if documents else None,
                "images": list(images) if images else None,
                "videos": list(videos) if videos else None,
                "audios": list(audios) if audios else None,
                "youtube": youtube if youtube else None,
            }

        options = {}
        for key in [
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
            "numa",
            "num_ctx",
            "num_batch",
            "num_gpu",
            "main_gpu",
            "use_mmap",
            "num_thread",
        ]:
            val = settings.get(key)
            if val is not None:
                options[key] = val

        keep_alive = settings.get("keep_alive", 300)
        if keep_alive is None:
            keep_alive = 300

        # -------------------
        # Variables for the while loop
        # -------------------
        thinking = ""
        thinking_time_already_started = False
        content = ""
        content_generation_start_time: datetime | None = None
        content_generation_end_time: datetime | None = None
        function_call = True
        max_calls = MAX_TOOL_CALLS_PER_GENERATION
        suppress_tools = False
        tool_error_tracker = ToolErrorTracker()
        thinking_time_start = None

        # -------------------
        # Meta data variables
        # -------------------
        meta_total_duration = 0.0
        meta_load_duration = 0.0
        meta_prompt_eval_count = 0
        meta_prompt_eval_duration = 0.0
        meta_eval_count = 0
        meta_eval_duration = 0.0
        meta_request_count = 0
        meta_total_thinking_time = 0.0
        meta_last_thinking_time = 0.0

        # Generation statistics tracking
        start_time = datetime.now(timezone.utc)
        meta_generation_success = False
        meta_generation_error = False
        meta_error_status_code = 0
        meta_error_message = ""
        meta_error_type = ""
        resolved_provider_id = None
        if byok:
            if isinstance(byok, dict):
                resolved_provider_id = byok.get("provider_id") or byok.get("provider")
        else:
            resolved_provider_id = getattr(resolved_provider, "id", None) or getattr(
                db_model, "provider_id", None
            )
        if not resolved_provider_id:
            resolved_provider_id = "unknown"

        meta_target_provider_id = resolved_provider_id
        model_name = (
            byok.get("model_name") if byok else getattr(db_model, "model_name", None)
        )
        target_model_id = "byok" if byok else getattr(db_model, "id", None)

        def _resolve_tokens_per_second():
            if not meta_eval_count:
                return None
            if meta_eval_duration:
                try:
                    return round(meta_eval_count / meta_eval_duration, 2)
                except ZeroDivisionError:
                    return None
            if content_generation_start_time is not None:
                effective_end = content_generation_end_time or datetime.now(
                    timezone.utc
                )
                duration_seconds = max(
                    (effective_end - content_generation_start_time).total_seconds(), 0.0
                )
                if duration_seconds > 0:
                    return round(meta_eval_count / duration_seconds, 2)
            return None

        def _record_generation_stat():
            try:
                tokens_per_second = _resolve_tokens_per_second()
                meta_payload = {
                    "generation_time": round(
                        (datetime.now(timezone.utc) - start_time).total_seconds(), 2
                    ),
                    "request_count": meta_request_count,
                    "input_tokens": meta_prompt_eval_count,
                    "output_tokens": meta_eval_count,
                    "total_duration": meta_total_duration,
                    "load_duration": meta_load_duration,
                    "thinking_time": meta_last_thinking_time,
                    "total_thinking_time": meta_total_thinking_time,
                }
                if tokens_per_second is not None:
                    meta_payload["tokens_per_second"] = tokens_per_second
                if not byok:
                    from app.llm.provider_groups import (
                        build_provider_group_resolution_meta,
                    )

                    meta_payload.update(
                        build_provider_group_resolution_meta(
                            db,
                            requested_provider_id,
                            resolved_provider,
                        )
                    )
                create_llm_generation_statistic(
                    db,
                    model_name=model_name or "ollama",
                    model_id=getattr(db_model, "id", None) or "ollama",
                    provider="ollama",
                    provider_id=meta_target_provider_id,
                    success=meta_generation_success,
                    error=meta_generation_error,
                    error_status_code=meta_error_status_code,
                    error_message=meta_error_message,
                    error_type=meta_error_type,
                    category="chat_request",
                    meta={
                        k: v
                        for k, v in meta_payload.items()
                        if v not in (None, "", [], {})
                    },
                    user_id=user_id,
                    is_byok=bool(byok),
                )
            except Exception:
                pass

        # For system instruction for websearch citations instruction
        web_search = False  # TODO

        # New message format: accumulate content blocks
        messages_to_save = []
        last_message_type = "user"

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
        read_timeout_sec = _coerce_timeout(
            settings.get("read_timeout_sec"), inactivity_timeout_sec
        )
        write_timeout_sec = _coerce_timeout(
            settings.get("write_timeout_sec"), inactivity_timeout_sec
        )
        pool_timeout_sec = _coerce_timeout(settings.get("pool_timeout_sec"), 5.0)

        try:
            httpx_timeout = httpx.Timeout(
                connect=connect_timeout_sec,
                read=read_timeout_sec,
                write=write_timeout_sec,
                pool=pool_timeout_sec,
            )
        except Exception:
            httpx_timeout = httpx.Timeout(
                connect=10.0,
                read=inactivity_timeout_sec,
                write=inactivity_timeout_sec,
                pool=5.0,
            )

        try:
            if byok:
                client = get_ollama_client(
                    db,
                    byok_base_url=byok.get("base_url"),
                    byok_api_key=byok.get("api_key"),
                    timeout=httpx_timeout,
                )
            else:
                client, resolved_provider = get_ollama_client(
                    db,
                    db_model.provider_id,
                    timeout=httpx_timeout,
                    return_provider=True,
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to initialize Ollama client: {exc}"
            )

        last_activity = time.monotonic()
        last_chunk_model = getattr(db_model, "model_name", None)
        if byok and isinstance(byok.get("model_name"), str):
            last_chunk_model = byok.get("model_name") or last_chunk_model

        def _build_meta(
            timeout_flag: bool = False,
            reason: str | None = None,
            timeout_message: str | None = None,
        ):
            tokens_per_second = _resolve_tokens_per_second()
            all_citations = collect_tool_result_citations(messages_to_save)
            meta_values = {
                "model": last_chunk_model,
                "total_duration": meta_total_duration,
                "load_duration": meta_load_duration,
                "input_tokens": meta_prompt_eval_count,
                "input_duration": meta_prompt_eval_duration,
                "output_tokens": meta_eval_count,
                "output_duration": meta_eval_duration,
                "tokens_per_second": tokens_per_second,
                "request_count": meta_request_count,
                "thinking_time": meta_last_thinking_time,
                "total_thinking_time": meta_total_thinking_time,
                "citations": all_citations if all_citations else None,
            }
            meta = {
                k: v for k, v in meta_values.items() if v not in (None, 0, "", [], {})
            }
            if timeout_flag:
                meta["timeout"] = True
            if reason:
                meta["timeout_reason"] = reason
            if timeout_message:
                meta["timeout_message"] = timeout_message
            for key, value in assistant_metadata.items():
                if value not in (None, "", [], {}):
                    meta[key] = value
            if meta:
                meta["timestamp"] = format_meta_timestamp()
            return meta

        def _persist_assistant_meta(meta: dict):
            nonlocal messages_to_save, last_message_type
            if temp_request_flag or not meta:
                return None
            target_id = "byok" if byok else getattr(db_model, "id", None)
            if target_id is None:
                return None
            # Flush any pending reasoning/content to messages_to_save
            if thinking:
                messages_to_save.append(
                    {
                        "type": "reasoning",
                        "content": thinking,
                        "meta": {"reasoning_time": meta_last_thinking_time}
                        if meta_last_thinking_time
                        else {},
                    }
                )
            if content:
                messages_to_save.append(
                    {"type": "content", "content": content, "meta": meta}
                )
            elif messages_to_save:
                messages_to_save[-1]["meta"] = meta
            if messages_to_save:
                assistant_msg = create_chat_message(
                    db,
                    chat_id,
                    target_id,
                    "assistant",
                    reference_id=reference_id,
                    content=messages_to_save,
                    retry_count=retry_count,
                )
                return assistant_msg.id if assistant_msg else None
            return None

        def emit_timeout_event(
            reason: str | None = None, timeout_message: str | None = None
        ):
            meta = _build_meta(
                timeout_flag=True, reason=reason, timeout_message=timeout_message
            )
            if not meta:
                meta = {"timeout": True, "timestamp": format_meta_timestamp()}
            yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
            _persist_assistant_meta(meta)

        while function_call and max_calls > 0:
            function_call = False
            thinking_already_added = False
            tool_call_accumulators: dict[int, dict] = {}
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
                meta = jsonable_encoder(
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
                tool_list,
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
            messages = [
                {"role": "system", "content": system_instruction}
            ] + formatted_history

            # -------------------
            # Preflight
            # -------------------
            try:
                if db_model:
                    _ = check_ollama_version(
                        db,
                        getattr(resolved_provider, "id", None) or db_model.provider_id,
                    )
                elif byok:
                    _ = check_ollama_version(
                        db,
                        byok_base_url=byok.get("base_url"),
                        byok_api_key=byok.get("api_key"),
                    )
            except HTTPException as he:
                is_admin = is_admin_role(user_role)
                error_message = (
                    str(he.detail) if is_admin else "Provider is not reachable"
                )
                yield json.dumps({"t": "e", "d": error_message}) + "\n"
                return
            except Exception as e:
                is_admin = is_admin_role(user_role)
                error_message = (
                    str(e)
                    if is_admin
                    else "An error occurred during generation. Please try again."
                )
                yield json.dumps({"t": "e", "d": error_message}) + "\n"
                return
            try:
                if db_model:
                    model = db_model.model_name
                elif byok:
                    model = byok.get("model_name")
                chat_kwargs = {
                    "model": model,
                    "messages": messages,
                    "tools": None if suppress_tools else tool_schemas,
                    "stream": True,
                    "options": options,
                    "keep_alive": keep_alive,
                }
                if think_flag is not None:
                    chat_kwargs["think"] = think_flag
                response = yield ProviderCall(
                    client.chat, {**chat_kwargs}, settings, "ollama", args=()
                )
            except (requests.RequestException, ConnectionError, httpx.HTTPError) as e:
                is_admin = is_admin_role(user_role)
                error_message = (
                    f"Ollama chat failed to start: {e}"
                    if is_admin
                    else "An error occurred during generation. Please try again."
                )
                yield json.dumps({"t": "e", "d": error_message}) + "\n"
                return
            except Exception as e:
                is_admin = is_admin_role(user_role)
                error_message = (
                    str(e)
                    if is_admin
                    else "An error occurred during generation. Please try again."
                )
                yield json.dumps({"t": "e", "d": error_message}) + "\n"
                return
            try:
                for chunk in engine.events(
                    response,
                    generation_id,
                    stream_factory=interruptible_provider_stream,
                    close_resource=client,
                    close_resource_on_finish=False,
                ):
                    last_activity = time.monotonic()
                    last_chunk_model = getattr(chunk, "model", last_chunk_model)
                    # Check for cancellation for this generation and exit gracefully if set
                    try:
                        if generation_id:
                            from app.chats.streaming import cancel_registry

                            if cancel_registry.is_cancelled(generation_id):
                                if (content or thinking or messages_to_save) and (
                                    not temp_request_flag
                                ):
                                    cancellation_meta = {"status": "cancelled"}
                                    cancellation_meta["tokens_streamed"] = (
                                        meta_eval_count
                                    )
                                    additional_meta = {
                                        "model": (
                                            byok.get("model_name")
                                            if byok
                                            else getattr(db_model, "model_name", None)
                                        ),
                                        "request_count": meta_request_count,
                                        "total_duration": meta_total_duration,
                                        "load_duration": meta_load_duration,
                                        "prompt_eval_count": meta_prompt_eval_count,
                                        "prompt_eval_duration": meta_prompt_eval_duration,
                                        "eval_count": meta_eval_count,
                                        "eval_duration": meta_eval_duration,
                                        "thinking_time": meta_last_thinking_time,
                                        "total_thinking_time": meta_total_thinking_time,
                                    }
                                    for key, value in additional_meta.items():
                                        if value not in (None, 0, "", [], {}):
                                            cancellation_meta[key] = value
                                    cancellation_meta["timestamp"] = (
                                        format_meta_timestamp()
                                    )
                                    target_id = (
                                        "byok"
                                        if byok
                                        else getattr(db_model, "id", None)
                                    )
                                    if target_id is not None:
                                        if (
                                            thinking
                                            and last_message_type != "reasoning"
                                        ):
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
                                            messages_to_save[-1]["meta"] = (
                                                cancellation_meta
                                            )
                                        if messages_to_save:
                                            create_chat_message(
                                                db,
                                                chat_id,
                                                target_id,
                                                "assistant",
                                                reference_id=reference_id,
                                                content=messages_to_save,
                                                retry_count=retry_count,
                                            )
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
                        pass

                    msg = getattr(chunk, "message", None)

                    if msg and getattr(msg, "tool_calls", None):
                        if thinking_time_already_started:
                            meta_last_thinking_time = (
                                datetime.now(timezone.utc) - thinking_time_start
                            ).total_seconds()
                            yield (
                                json.dumps({"t": "r_f", "d": meta_last_thinking_time})
                                + "\n"
                            )
                            meta_total_thinking_time += meta_last_thinking_time
                            thinking_time_already_started = False
                            thinking_time_start = None
                        max_calls -= 1
                        tool_meta = (
                            {"thinking_time": meta_last_thinking_time}
                            if meta_last_thinking_time
                            else None
                        )
                        meta = {}
                        if tool_meta:
                            meta.update(tool_meta)
                        if meta:
                            meta.setdefault("timestamp", format_meta_timestamp())
                        if (not thinking_already_added and thinking) and (
                            not temp_request_flag
                        ):
                            messages_to_save.append(
                                {
                                    "type": "reasoning",
                                    "content": thinking,
                                    "meta": {"reasoning_time": meta_last_thinking_time}
                                    if meta_last_thinking_time
                                    else {},
                                }
                            )
                            last_message_type = "reasoning"
                            messages.append(
                                {
                                    "role": "assistant",
                                    "thinking": thinking,
                                }
                            )
                            thinking = ""
                            thinking_already_added = True
                        tool_calls = getattr(msg, "tool_calls", None)
                        processed_tool = False
                        if tool_calls:
                            for tool in tool_calls:
                                if max_calls <= 0:
                                    break
                                try:
                                    function_block = getattr(tool, "function", None)
                                    tool_call_id = getattr(tool, "id", None)
                                    tool_name = getattr(function_block, "name", None)
                                    arguments_raw = getattr(
                                        function_block, "arguments", None
                                    )
                                except Exception:
                                    tool_name = None
                                    arguments_raw = None
                                    tool_call_id = None
                                if not isinstance(tool_name, str):
                                    continue
                                tool_call_id = _resolve_ollama_tool_call_id(
                                    tool_call_id
                                )

                                tool_stat_kwargs = {
                                    "db": db,
                                    "tool_name": tool_name or "unknown",
                                    "model_id": target_model_id,
                                    "model_name": model_name,
                                    "provider": meta_target_provider_id,
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

                                if tool_name not in allowed_tools_set:
                                    logger.warning(
                                        "Tool %s not allowed for this model", tool_name
                                    )
                                    _log_tool_stat(False, "Tool not allowed")
                                    continue

                                if isinstance(arguments_raw, str):
                                    try:
                                        arguments = json.loads(arguments_raw)
                                    except Exception:
                                        arguments = {}
                                elif isinstance(arguments_raw, dict):
                                    arguments = arguments_raw
                                elif arguments_raw is None:
                                    arguments = {}
                                else:
                                    arguments = jsonable_encoder(arguments_raw)

                                if (
                                    not thinking_already_added and (thinking or content)
                                ) and (not temp_request_flag):
                                    if thinking:
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
                                    assistant_snapshot = {"role": "assistant"}
                                    if content:
                                        assistant_snapshot["content"] = content
                                    if thinking:
                                        assistant_snapshot["thinking"] = thinking
                                    if tool_call_id:
                                        assistant_snapshot["tool_call_id"] = (
                                            tool_call_id
                                        )
                                    formatted_history.append(assistant_snapshot)
                                    content = ""
                                    thinking = ""
                                    thinking_already_added = True

                                function_call = True
                                max_calls -= 1
                                processed_tool = True

                                arguments_json = sanitize_tool_call_arguments_object_for_history(
                                    tool_name,
                                    arguments,
                                )
                                assistant_tool_message = {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": tool_call_id,
                                            "type": "function",
                                            "function": {
                                                "name": tool_name,
                                                "arguments": arguments_json or {},
                                            },
                                        }
                                    ],
                                }
                                formatted_history.append(assistant_tool_message)

                                hide_tool_arguments = (
                                    tool_name in tools_not_yield_arguments
                                )
                                hidden_from_user = should_hide_tool_call_from_user(
                                    tool_name, arguments
                                )
                                if not hidden_from_user:
                                    tool_event_payload = {
                                        "t": "t_c",
                                        "d": {
                                            "id": tool_call_id,
                                            "name": tool_name,
                                        },
                                    }
                                    if not hide_tool_arguments:
                                        tool_event_payload["d"]["args"] = arguments
                                        tool_event_payload["c"] = arguments
                                    stream_meta = get_stream_tool_event_meta(
                                        tool_name,
                                        tool_call_id=tool_call_id,
                                    )
                                    if stream_meta:
                                        tool_event_payload["d"]["meta"] = stream_meta
                                    yield json.dumps(tool_event_payload) + "\n"

                                helper_payload: dict[str, Any] = {}
                                helper_gen = None
                                tool_error_message: str | None = None
                                tool_error_response: ToolErrorResponse | None = None
                                try:
                                    helper_gen = stream_tool_call(resolve_tool_call,
                                        db,
                                        tool_name,
                                        arguments,
                                        user_id,
                                        None,
                                        project_id,
                                        model_settings=settings,
                                        byok=byok,
                                        chat_id=chat_id,
                                        chat_history=chat_history,
                                        generation_id=generation_id,
                                        user_role=str(user_role or "").strip().lower(),
                                        tool_call_id=tool_call_id,
                                    )
                                except Exception as tool_exc:
                                    tool_error_message = str(tool_exc)
                                    tool_error_response = tool_error_tracker.record(
                                        tool_name, tool_exc
                                    )
                                    logger.exception(
                                        "Tool %s failed to start: %s",
                                        tool_name,
                                        tool_exc,
                                    )

                                if helper_gen and tool_error_message is None:
                                    try:
                                        helper_payload = yield from helper_gen
                                    except Exception as tool_exc:
                                        tool_error_message = str(tool_exc)
                                        tool_error_response = tool_error_tracker.record(
                                            tool_name, tool_exc
                                        )
                                        logger.exception(
                                            "Tool %s raised during execution: %s",
                                            tool_name,
                                            tool_exc,
                                        )

                                # Tool execution can legitimately outlive the
                                # provider inactivity window. It completed, so
                                # allow the next model turn to consume its result.
                                last_activity = time.monotonic()

                                if tool_error_message:
                                    if tool_error_response is None:
                                        tool_error_response = tool_error_tracker.record(
                                            tool_name,
                                            RuntimeError(tool_error_message),
                                        )
                                    tool_documents = []
                                    tool_images = []
                                    tool_videos = []
                                    tool_audios = []
                                    tool_youtube = []
                                    tool_webpages = []
                                    tool_content = tool_error_response.model_output
                                    if tool_error_response.stop_tool_calls:
                                        suppress_tools = True
                                    _log_tool_stat(
                                        False,
                                        tool_error_message,
                                        meta=tool_error_response.statistic_meta,
                                    )
                                else:
                                    tool_result_payload = helper_payload.get("result")
                                    tool_documents = (
                                        helper_payload.get("documents") or []
                                    )
                                    tool_images = helper_payload.get("images") or []
                                    tool_videos = helper_payload.get("videos") or []
                                    tool_audios = helper_payload.get("audios") or []
                                    tool_youtube = helper_payload.get("youtube") or []
                                    tool_webpages = helper_payload.get("webpages") or []
                                    tool_content = _stringify_tool_payload(
                                        helper_payload.get("content")
                                        or tool_result_payload
                                    )
                                    _log_tool_stat(
                                        True, None, meta=helper_payload.get("tool_meta")
                                    )

                                tool_history_entry = {
                                    "role": "tool",
                                    "content": tool_content,
                                    "name": tool_name,
                                    "tool_call_id": tool_call_id,
                                }
                                if tool_youtube:
                                    tool_history_entry["youtube"] = tool_youtube
                                if tool_documents:
                                    tool_history_entry["documents"] = tool_documents
                                if tool_images:
                                    tool_history_entry["images"] = tool_images
                                if tool_videos:
                                    tool_history_entry["videos"] = tool_videos
                                if tool_audios:
                                    tool_history_entry["audios"] = tool_audios

                                formatted_history.append(tool_history_entry)

                                if not temp_request_flag and not hidden_from_user:
                                    tool_call_block = build_tool_call_block(
                                        tool_name,
                                        arguments,
                                        tool_call_id=tool_call_id,
                                    )
                                    tool_call_label = (
                                        f"{tool_name}()" if tool_name else "tool"
                                    )
                                    messages_to_save.append(tool_call_block)
                                    last_message_type = "tool_call"
                                    persist_files_in_file_block = (
                                        should_persist_files_in_file_block(tool_name)
                                    )
                                    tool_result_content = (
                                        stringify_tool_result_content_for_persistence(
                                            tool_name,
                                            helper_payload.get("result")
                                            if helper_payload.get("result")
                                            not in (None, "")
                                            else (tool_content or "success"),
                                            helper_payload.get("widget"),
                                        )
                                    )
                                    tool_result_block = {
                                        "type": "tool_call_result",
                                        "content": tool_result_content or "success",
                                        "tool_name": tool_call_label,
                                    }
                                    if tool_name == "web_search" and tool_webpages:
                                        citations = build_web_search_citations(
                                            tool_webpages
                                        )
                                        if citations:
                                            tool_result_block["meta"] = {
                                                "citations": citations
                                            }
                                    tool_meta = helper_payload.get(
                                        "tool_meta"
                                    ) or helper_payload.get("meta")
                                    if isinstance(tool_meta, dict) and tool_meta:
                                        tool_result_block.setdefault("meta", {}).update(
                                            tool_meta
                                        )
                                    tool_result_block.setdefault("meta", {})[
                                        "tool_call_id"
                                    ] = tool_call_id
                                    if (
                                        tool_documents
                                        and not persist_files_in_file_block
                                    ):
                                        tool_result_block["documents"] = tool_documents
                                    if tool_images and not persist_files_in_file_block:
                                        tool_result_block["images"] = tool_images
                                    if tool_videos and not persist_files_in_file_block:
                                        tool_result_block["videos"] = tool_videos
                                    if tool_audios and not persist_files_in_file_block:
                                        tool_result_block["audios"] = tool_audios
                                    if tool_youtube:
                                        tool_result_block["youtube"] = tool_youtube
                                    messages_to_save.append(tool_result_block)
                                    if persist_files_in_file_block:
                                        file_block = build_tool_file_block(
                                            tool_name=tool_name,
                                            tool_label=tool_call_label,
                                            documents=tool_documents,
                                            images=tool_images,
                                            videos=tool_videos,
                                            audios=tool_audios,
                                        )
                                        if file_block:
                                            messages_to_save.append(file_block)
                                    last_message_type = "tool_call_result"
                                    widget_data = helper_payload.get("widget")
                                    if widget_data and widget_data.get("html"):
                                        messages_to_save.append(
                                            {
                                                "type": "widget",
                                                "content": widget_data.get("html"),
                                                "meta": build_widget_block_meta(
                                                    widget_data,
                                                    tool_name=tool_name,
                                                    tool_call_id=tool_call_id,
                                                ),
                                            }
                                        )
                                        last_message_type = "widget"

                        if processed_tool:
                            continue

                    if msg and getattr(msg, "thinking", None):
                        if not thinking_time_already_started:
                            thinking_time_start = datetime.now(timezone.utc)
                            thinking_time_already_started = True
                        yield json.dumps({"t": "r", "d": str(msg.thinking)}) + "\n"
                        thinking += msg.thinking

                    if msg and getattr(msg, "content", None):
                        now_ts = datetime.now(timezone.utc)
                        if content_generation_start_time is None:
                            content_generation_start_time = now_ts
                        content_generation_end_time = now_ts
                        if thinking_time_already_started:
                            meta_last_thinking_time = (
                                datetime.now(timezone.utc) - thinking_time_start
                            ).total_seconds()
                            yield (
                                json.dumps({"t": "r_f", "d": meta_last_thinking_time})
                                + "\n"
                            )
                            meta_total_thinking_time += meta_last_thinking_time
                            thinking_time_already_started = False
                            thinking_time_start = None
                        content += msg.content
                        yield json.dumps({"t": "c", "d": str(msg.content)}) + "\n"

                    if getattr(chunk, "done", False):
                        if (
                            content_generation_start_time is not None
                            and content_generation_end_time is None
                        ):
                            content_generation_end_time = datetime.now(timezone.utc)
                        td = getattr(chunk, "total_duration", 0) or 0
                        ld = getattr(chunk, "load_duration", 0) or 0
                        ped = getattr(chunk, "prompt_eval_duration", 0) or 0
                        ed = getattr(chunk, "eval_duration", 0) or 0
                        pec = getattr(chunk, "prompt_eval_count", 0) or 0
                        ec = getattr(chunk, "eval_count", 0) or 0

                        meta_total_duration += td / 1_000_000_000
                        meta_load_duration += ld / 1_000_000_000
                        meta_prompt_eval_duration += ped / 1_000_000_000
                        meta_eval_duration += ed / 1_000_000_000
                        meta_prompt_eval_count += pec
                        meta_eval_count += ec
                        meta_request_count += 1
                        if not function_call:
                            meta_generation_success = True
                            meta = _build_meta()
                            yield json.dumps({"t": "d", "d": "f", "c": meta}) + "\n"
                            saved_assistant_id = _persist_assistant_meta(meta)
                            if saved_assistant_id:
                                yield (
                                    json.dumps({"t": "a_id", "d": saved_assistant_id})
                                    + "\n"
                                )
                            _record_generation_stat()
                            return
            except Exception as e:
                yield json.dumps({"t": "e", "d": f"chunk error: {e}"}) + "\n"

                try:
                    if time.monotonic() - last_activity > inactivity_timeout_sec:
                        yield from emit_timeout_event("inactivity_no_chunks")
                        return
                except Exception:
                    pass
            except httpx.ReadTimeout as exc:
                yield from emit_timeout_event("read_timeout", str(exc))
                return
            except httpx.TimeoutException as exc:
                yield from emit_timeout_event("timeout", str(exc))
                return
            except httpx.HTTPError as exc:
                yield json.dumps({"t": "e", "d": f"Ollama stream error: {exc}"}) + "\n"
                return

    except ConnectionError as e:
        meta_generation_error = True
        meta_error_message = str(e)
        meta_error_type = "ConnectionError"
        _record_generation_stat()
        yield json.dumps({"t": "e", "d": e}) + "\n"
        yield json.dumps({"t": "d", "d": e}) + "\n"
        return
    except ResponseError as e:
        meta_generation_error = True
        meta_error_status_code = getattr(e, "status_code", None) or 0
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        _record_generation_stat()
        yield json.dumps({"t": "e", "d": e}) + "\n"
        yield json.dumps({"t": "d", "d": e}) + "\n"
        return
    except RemoteProtocolError as e:
        meta_generation_error = True
        meta_error_status_code = 0
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        _record_generation_stat()
        yield json.dumps({"t": "e", "d": e}) + "\n"
        yield json.dumps({"t": "d", "d": e}) + "\n"
        return
