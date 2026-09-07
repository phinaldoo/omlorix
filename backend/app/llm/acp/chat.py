"""ACP chat orchestration and streaming response persistence.

The historical ``utils`` module remains the public facade. Synchronizing its
dependencies before each call preserves existing monkeypatch and extension seams.
"""

from __future__ import annotations

# The extracted implementation retains intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "acp_chat": (
        "Chats",
        "HTTPException",
        "Path",
        "SshConnection",
        "UserAcpProfile",
        "_compose_prompt",
        "_effective_acp_system_instruction",
        "_get_saved_control",
        "_get_saved_model",
        "_get_saved_session",
        "_history_attachment_ids",
        "_log_acp_chat_step",
        "_message_role",
        "_plan_markdown",
        "_prepare_acp_attachments",
        "_record_acp_generation_statistic",
        "_resolve_remote_path",
        "_save_session",
        "_sensitive_text_summary",
        "asyncio",
        "build_process_environment",
        "cancel_registry",
        "create_chat_message",
        "datetime",
        "extract_acp_session_controls",
        "get_llm_provider",
        "is_admin_role",
        "json",
        "queue",
        "require_custom_acp_connections",
        "run_acp_turn",
        "serialize_acp_session_controls",
        "threading",
        "timezone",
    )
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate globals before defining the function so annotations keep their
# original evaluation behavior.
for _dependency_name in (
    "Chats",
    "HTTPException",
    "Path",
    "SshConnection",
    "UserAcpProfile",
    "_compose_prompt",
    "_effective_acp_system_instruction",
    "_get_saved_control",
    "_get_saved_model",
    "_get_saved_session",
    "_history_attachment_ids",
    "_log_acp_chat_step",
    "_message_role",
    "_plan_markdown",
    "_prepare_acp_attachments",
    "_record_acp_generation_statistic",
    "_resolve_remote_path",
    "_save_session",
    "_sensitive_text_summary",
    "asyncio",
    "build_process_environment",
    "cancel_registry",
    "create_chat_message",
    "datetime",
    "extract_acp_session_controls",
    "get_llm_provider",
    "is_admin_role",
    "json",
    "queue",
    "require_custom_acp_connections",
    "run_acp_turn",
    "serialize_acp_session_controls",
    "threading",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_acp_chat(
    chat_id: str,
    chat_history: list[dict],
    db,
    db_model=None,
    user_id: str | None = None,
    project_id: str | None = None,
    generation_id: str | None = None,
    temp_request_flag: bool = False,
    byok: dict | None = None,
    settings_override: dict | None = None,
    reference_id: str | None = None,
    system_instruction_sections: list[dict[str, str]] | None = None,
    assistant_metadata: dict | None = None,
    note_ids: list[str] | None = None,
    retry_count: int | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    user_role: str | None = None,
    acp_model_id: str | None = None,
    acp_session_id: str | None = None,
    acp_security_level: str | None = None,
    acp_reasoning_effort: str | None = None,
):
    """Bridge a synchronous Omlorix NDJSON stream to one asynchronous ACP turn."""
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "receive",
        "provider_request_received",
        user_id=str(user_id or ""),
        project_id=str(project_id or ""),
        model_row_id=str(getattr(db_model, "id", "") or ""),
        history_message_count=len(chat_history or []),
        history_roles=[_message_role(message) for message in chat_history or []],
        temp_request=bool(temp_request_flag),
        retry_count=retry_count,
        has_byok=bool(byok),
        requested_session_id=str(acp_session_id or ""),
        requested_model_id=str(acp_model_id or ""),
        requested_security_level=str(acp_security_level or ""),
        requested_reasoning_effort=str(acp_reasoning_effort or ""),
        reference_id=str(reference_id or ""),
    )
    if byok:
        _log_acp_chat_step(generation_id, chat_id, "validate", "request_rejected_byok")
        raise HTTPException(
            status_code=400, detail="ACP runtimes are not available through BYOK"
        )
    if not db_model or not getattr(db_model, "provider_id", None):
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "validate",
            "request_rejected_missing_provider",
        )
        raise HTTPException(status_code=422, detail="ACP provider is not configured")

    provider = get_llm_provider(db, db_model.provider_id)
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    model_settings = db_model.settings if isinstance(db_model.settings, dict) else {}
    effective_system_instruction = _effective_acp_system_instruction(
        model_settings,
        settings_override,
    )
    effective_system_instruction_sections = list(system_instruction_sections or [])
    if effective_system_instruction:
        effective_system_instruction_sections.insert(
            0,
            {
                "title": "System Instruction",
                "content": effective_system_instruction,
            },
        )
    profile_id = str(
        provider_settings.get("acp_profile_id")
        or model_settings.get("acp_profile_id")
        or ""
    ).strip()
    if not profile_id:
        # ACP is exclusively user-managed. Provider rows without a profile ID
        # are legacy administrator configurations and must never launch a local
        # subprocess inside the Omlorix backend.
        raise HTTPException(status_code=404, detail="Your ACP profile is unavailable")

    # A profile may predate a group-policy change. Enforce access at each
    # generation so a disabled profile cannot be invoked through a stale UI.
    require_custom_acp_connections(db, str(user_id))
    profile = (
        db.query(UserAcpProfile)
        .filter(
            UserAcpProfile.id == profile_id,
            UserAcpProfile.user_id == str(user_id),
            UserAcpProfile.enabled.is_(True),
        )
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Your ACP profile is unavailable")
    ssh_connection = (
        db.query(SshConnection)
        .filter(
            SshConnection.id == profile.ssh_connection_id,
            SshConnection.user_id == str(user_id),
            SshConnection.enabled.is_(True),
        )
        .first()
    )
    if not ssh_connection:
        raise HTTPException(
            status_code=404, detail="Your ACP SSH connection is unavailable"
        )
    workspace_root = profile.workspace_root
    session_path = _resolve_remote_path(workspace_root, profile.cwd)
    additional_directories = [
        _resolve_remote_path(workspace_root, path)
        for path in (profile.additional_directories or [])
    ]
    command = profile.executable
    arguments = [str(value) for value in profile.arguments or []]
    mode = str(profile.mode or "").strip() or None
    permission_mode = profile.permission_mode
    permission_timeout_seconds = profile.permission_timeout_seconds
    prompt_timeout_seconds = profile.prompt_timeout_seconds
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "resolve",
        "personal_profile_resolved",
        provider_id=str(getattr(provider, "id", "") or ""),
        profile_id=profile_id,
        ssh_connection_id=str(getattr(ssh_connection, "id", "") or ""),
        transport="ssh",
        command=Path(command).name,
        argument_count=len(arguments),
        session_path=str(session_path),
        additional_directory_count=len(additional_directories),
        permission_mode=permission_mode,
        permission_timeout_seconds=permission_timeout_seconds,
        prompt_timeout_seconds=prompt_timeout_seconds,
    )

    chat = (
        db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
        if chat_id and user_id
        else None
    )
    saved_session_id = _get_saved_session(
        chat, db_model.id, provider.id, str(session_path)
    )
    # Only sessions stored against this durable chat are safe to resume. The
    # frontend also performs short-lived ACP session discovery to populate the
    # model/security/reasoning selectors. That discovery process is closed
    # before a chat turn starts, so its session ID must never be loaded as if it
    # belonged to a newly created chat.
    existing_session_id = saved_session_id
    selected_acp_model_id = str(acp_model_id or "").strip() or _get_saved_model(
        chat, db_model.id, provider.id, str(session_path)
    )
    selected_acp_security_level = str(
        acp_security_level or ""
    ).strip() or _get_saved_control(
        chat,
        db_model.id,
        provider.id,
        str(session_path),
        "selected_security_level",
    )
    selected_acp_reasoning_effort = str(
        acp_reasoning_effort or ""
    ).strip() or _get_saved_control(
        chat,
        db_model.id,
        provider.id,
        str(session_path),
        "selected_reasoning_effort",
    )
    if retry_count:
        existing_session_id = None
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "resolve",
        "session_and_controls_resolved",
        durable_chat_found=chat is not None,
        saved_session_id=str(saved_session_id or ""),
        request_session_id=str(acp_session_id or ""),
        effective_session_id=str(existing_session_id or ""),
        request_session_ignored=bool(acp_session_id and not saved_session_id),
        retry_forced_new_session=bool(retry_count),
        selected_model_id=str(selected_acp_model_id or ""),
        selected_security_level=str(selected_acp_security_level or ""),
        selected_reasoning_effort=str(selected_acp_reasoning_effort or ""),
    )
    new_session_attachments = _prepare_acp_attachments(
        chat_history,
        str(user_id or ""),
        replay=True,
    )
    attachment_by_id = {
        str(attachment.get("file_id") or ""): attachment
        for attachment in new_session_attachments
    }
    # Reuse the already access-checked payloads for a successfully resumed
    # session. This avoids reading and base64-encoding the newest files twice.
    attachments = [
        attachment_by_id[file_id]
        for file_id, _category in _history_attachment_ids(chat_history, replay=False)
        if file_id in attachment_by_id
    ]
    prompt = _compose_prompt(
        chat_history,
        effective_system_instruction_sections,
        replay=not existing_session_id,
        has_attachments=bool(
            attachments if existing_session_id else new_session_attachments
        ),
    )
    # Keep a transcript-bearing fallback ready for agents whose persisted ACP
    # session was deleted between Omlorix turns.
    new_session_prompt = _compose_prompt(
        chat_history,
        effective_system_instruction_sections,
        replay=True,
        has_attachments=bool(new_session_attachments),
    )
    if chat_reference_context:
        prompt += "\n\n[Referenced Chat Context]\n" + str(chat_reference_context)
        new_session_prompt += "\n\n[Referenced Chat Context]\n" + str(
            chat_reference_context
        )
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "prepare",
        "prompt_and_attachments_prepared",
        resume_prompt=_sensitive_text_summary(prompt),
        new_session_prompt=_sensitive_text_summary(new_session_prompt),
        current_attachment_count=len(attachments),
        replay_attachment_count=len(new_session_attachments),
        current_attachments=[
            {
                "file_id": str(attachment.get("file_id") or ""),
                "category": str(attachment.get("category") or ""),
                "mime_type": str(attachment.get("mime_type") or ""),
                "size": attachment.get("size"),
                "has_extracted_text": bool(attachment.get("text")),
                "has_binary_data": bool(attachment.get("data")),
            }
            for attachment in attachments
        ],
        system_instruction_section_count=len(effective_system_instruction_sections),
        has_chat_reference_context=bool(chat_reference_context),
    )

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    thread_error: list[BaseException] = []

    def emit(event: dict[str, Any]) -> None:
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "receive",
            "runtime_event_enqueued",
            event_kind=str(event.get("kind") or ""),
            session_id=str(event.get("session_id") or ""),
        )
        events.put(event)

    def worker() -> None:
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "runtime",
            "worker_thread_started",
            thread_name=threading.current_thread().name,
        )
        try:
            asyncio.run(
                run_acp_turn(
                    command=command,
                    arguments=arguments,
                    process_cwd=str(session_path),
                    environment=build_process_environment([]),
                    session_cwd=str(session_path),
                    additional_directories=additional_directories,
                    existing_session_id=existing_session_id,
                    mode=mode,
                    selected_model_id=selected_acp_model_id,
                    selected_security_level=selected_acp_security_level,
                    selected_reasoning_effort=selected_acp_reasoning_effort,
                    prompt=prompt,
                    new_session_prompt=new_session_prompt,
                    attachments=attachments,
                    new_session_attachments=new_session_attachments,
                    user_id=str(user_id or ""),
                    generation_id=str(generation_id or ""),
                    permission_mode=permission_mode,
                    permission_timeout_seconds=permission_timeout_seconds,
                    prompt_timeout_seconds=prompt_timeout_seconds,
                    emit=emit,
                    is_cancelled=lambda: bool(
                        generation_id and cancel_registry.is_cancelled(generation_id)
                    ),
                    ssh_connection=ssh_connection,
                )
            )
        except BaseException as exc:
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "runtime",
                "worker_thread_failed",
                error_type=type(exc).__name__,
                error=_sensitive_text_summary(str(exc)),
            )
            thread_error.append(exc)
        finally:
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "runtime",
                "worker_thread_finished",
                failed=bool(thread_error),
            )
            events.put({"kind": "worker_done"})

    runtime_thread = threading.Thread(
        target=worker, name=f"acp-turn-{generation_id or chat_id}", daemon=True
    )
    runtime_thread.start()
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "runtime",
        "worker_thread_dispatched",
        thread_name=runtime_thread.name,
    )

    content = ""
    reasoning = ""
    tool_blocks: dict[str, dict[str, Any]] = {}
    tool_block_indices: dict[str, int] = {}
    messages_to_save: list[dict[str, Any]] = []
    completed = False
    cancelled = False
    session_id = existing_session_id
    completion_metadata: dict[str, Any] = {}
    context_usage_metadata: dict[str, int] = {}

    def append_text_block(block_type: str, delta: str) -> None:
        """Append text to the current event segment without crossing tool boundaries."""
        if not delta:
            return
        if messages_to_save and messages_to_save[-1].get("type") == block_type:
            messages_to_save[-1]["content"] = (
                str(messages_to_save[-1].get("content") or "") + delta
            )
            return
        messages_to_save.append({"type": block_type, "content": delta, "meta": {}})

    def tool_message_block(descriptor: dict[str, Any]) -> dict[str, Any]:
        """Build the canonical persisted representation of one ACP tool call."""
        arguments = descriptor.get("args")
        return {
            "type": "tool_call",
            "content": descriptor.get("name") or "ACP tool",
            "meta": {
                "tool_name": descriptor.get("name") or "ACP tool",
                "arguments": (
                    json.dumps(arguments, ensure_ascii=False)
                    if arguments is not None
                    else "{}"
                ),
                "tool_call_id": descriptor.get("id"),
                "status": descriptor.get("status"),
                "kind": descriptor.get("kind"),
                # ACP tool updates may include the final process/search output.
                # Keeping it with the call makes a refreshed transcript capable
                # of rendering the same completed state as the live stream.
                "raw_output": descriptor.get("raw_output"),
            },
        }

    while True:
        event = events.get()
        kind = event.get("kind")
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "receive",
            "runtime_event_dequeued",
            event_kind=str(kind or ""),
            session_id=str(event.get("session_id") or ""),
            queue_depth=events.qsize(),
        )
        if kind == "worker_done":
            break
        if kind == "session":
            session_id = str(event.get("session_id") or "")
            if session_id and not temp_request_flag:
                _save_session(
                    chat,
                    db,
                    db_model.id,
                    provider.id,
                    str(session_path),
                    session_id,
                )
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "persist",
                "session_event_processed",
                session_id=session_id,
                persisted=bool(
                    session_id and not temp_request_flag and chat is not None
                ),
            )
            continue
        if kind == "warning":
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "warning_streamed_to_client",
                i18n_key=str(event.get("i18n_key") or ""),
            )
            yield (
                json.dumps(
                    {
                        "t": "w",
                        "c": event.get("message"),
                        "i18n_key": event.get("i18n_key"),
                    }
                )
                + "\n"
            )
            continue
        if kind == "permission":
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "permission_streamed_to_client",
                permission_id=str(event.get("permission_id") or ""),
                option_count=len(event.get("options") or []),
            )
            yield (
                json.dumps(
                    {
                        "t": "acp_permission",
                        "d": {
                            "permission_id": event.get("permission_id"),
                            "tool_call": event.get("tool_call"),
                            "options": event.get("options"),
                        },
                    }
                )
                + "\n"
            )
            continue
        if kind == "cancelled":
            cancelled = True
            _log_acp_chat_step(
                generation_id, chat_id, "send", "cancelled_streamed_to_client"
            )
            yield json.dumps({"t": "d", "d": "c", "c": {"status": "cancelled"}}) + "\n"
            continue
        if kind == "complete":
            completed = True
            session_id = str(event.get("session_id") or session_id or "")
            if isinstance(event.get("metadata"), dict):
                completion_metadata = dict(event["metadata"])
            if session_id and not temp_request_flag:
                _save_session(
                    chat,
                    db,
                    db_model.id,
                    provider.id,
                    str(session_path),
                    session_id,
                    completion_metadata.get("model_id"),
                    completion_metadata.get("acp_security_level"),
                    completion_metadata.get("acp_reasoning_effort"),
                )
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "receive",
                "completion_event_processed",
                session_id=session_id,
                metadata=completion_metadata,
            )
            continue
        if kind != "session_update":
            continue

        update = event.get("update") if isinstance(event.get("update"), dict) else {}
        update_type = str(
            update.get("sessionUpdate") or update.get("session_update") or ""
        )
        update_content = (
            update.get("content") if isinstance(update.get("content"), dict) else {}
        )
        if (
            update_type == "agent_message_chunk"
            and update_content.get("type") == "text"
        ):
            delta = str(update_content.get("text") or "")
            content += delta
            append_text_block("content", delta)
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "assistant_text_chunk_streamed",
                delta=_sensitive_text_summary(delta),
                accumulated_characters=len(content),
            )
            yield json.dumps({"t": "c", "d": delta}) + "\n"
        elif (
            update_type == "agent_thought_chunk"
            and update_content.get("type") == "text"
        ):
            delta = str(update_content.get("text") or "")
            reasoning += delta
            append_text_block("reasoning", delta)
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "reasoning_chunk_streamed",
                delta=_sensitive_text_summary(delta),
                accumulated_characters=len(reasoning),
            )
            yield json.dumps({"t": "r", "d": delta}) + "\n"
        elif update_type in {"tool_call", "tool_call_update"}:
            tool_id = str(
                update.get("toolCallId") or update.get("tool_call_id") or ""
            ).strip()
            existing = tool_blocks.get(tool_id, {})
            descriptor = {
                **existing,
                "id": tool_id,
                "name": str(
                    update.get("title")
                    or existing.get("name")
                    or update.get("kind")
                    or "ACP tool"
                ),
                "args": update.get("rawInput", existing.get("args")),
                "kind": update.get("kind", existing.get("kind")),
                "status": update.get("status", existing.get("status")),
                "raw_output": update.get("rawOutput", existing.get("raw_output")),
            }
            tool_key = tool_id or f"anonymous:{len(tool_blocks)}"
            tool_blocks[tool_key] = descriptor
            block = tool_message_block(descriptor)
            if tool_key in tool_block_indices:
                messages_to_save[tool_block_indices[tool_key]] = block
            else:
                tool_block_indices[tool_key] = len(messages_to_save)
                messages_to_save.append(block)
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "tool_event_streamed",
                tool_call_id=tool_id,
                tool_name=descriptor["name"],
                tool_kind=str(descriptor.get("kind") or ""),
                tool_status=str(descriptor.get("status") or ""),
                has_arguments=descriptor.get("args") is not None,
                has_raw_output=descriptor.get("raw_output") is not None,
            )
            yield json.dumps({"t": "t_c", "d": descriptor}) + "\n"
        elif update_type in {"plan", "plan_update"}:
            delta = _plan_markdown(update)
            if delta:
                reasoning += delta
                append_text_block("reasoning", delta)
                _log_acp_chat_step(
                    generation_id,
                    chat_id,
                    "send",
                    "plan_update_streamed",
                    delta=_sensitive_text_summary(delta),
                    accumulated_reasoning_characters=len(reasoning),
                )
                yield json.dumps({"t": "r", "d": delta}) + "\n"
        elif update_type == "config_option_update":
            controls = extract_acp_session_controls(
                {"configOptions": update.get("configOptions") or []}
            )
            public_controls = serialize_acp_session_controls(controls)
            # Keep the live UI aligned with agent-side configuration changes.
            # Durable control values are written only after a successful turn.
            if public_controls.get("current_model_id"):
                selected_acp_model_id = str(public_controls["current_model_id"])
            if public_controls.get("current_reasoning_effort"):
                selected_acp_reasoning_effort = str(
                    public_controls["current_reasoning_effort"]
                )
            yield (
                json.dumps(
                    {
                        "t": "acp_config",
                        "d": public_controls,
                    }
                )
                + "\n"
            )
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "configuration_update_streamed",
                controls=public_controls,
            )
        elif update_type == "current_mode_update":
            current_security_level = str(
                update.get("currentModeId") or update.get("current_mode_id") or ""
            )
            if current_security_level:
                selected_acp_security_level = current_security_level
            yield (
                json.dumps(
                    {
                        "t": "acp_config",
                        "d": {"current_security_level": current_security_level},
                    }
                )
                + "\n"
            )
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "send",
                "security_update_streamed",
                current_security_level=current_security_level,
            )
        elif update_type == "usage_update":
            # This is session-context occupancy, distinct from the per-turn
            # token counts returned by session/prompt.
            try:
                context_usage_metadata["context_tokens_used"] = max(
                    int(update.get("used") or 0), 0
                )
                context_usage_metadata["context_window_size"] = max(
                    int(update.get("size") or 0), 0
                )
            except (TypeError, ValueError):
                context_usage_metadata = {}
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "receive",
                "context_usage_processed",
                context_usage=context_usage_metadata,
            )

    runtime_thread.join(timeout=1)
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "runtime",
        "worker_thread_joined",
        thread_alive=runtime_thread.is_alive(),
        completed=completed,
        cancelled=cancelled,
        has_error=bool(thread_error),
    )
    if thread_error:
        error = thread_error[0]
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "runtime",
            "turn_failed",
            error_type=type(error).__name__,
            error=_sensitive_text_summary(str(error)),
        )
        detail = (
            str(error)
            if is_admin_role(user_role)
            else "Your ACP agent failed. Check its SSH connection and remote runtime."
        )
        _record_acp_generation_statistic(
            db,
            db_model=db_model,
            provider=provider,
            model_name=selected_acp_model_id
            or getattr(db_model, "model_name", "ACP agent"),
            metadata=completion_metadata,
            user_id=user_id,
            success=False,
            error=error,
        )
        yield (
            json.dumps(
                {
                    "t": "e",
                    "d": detail,
                    "i18n_key": None
                    if is_admin_role(user_role)
                    else "workspace_acp_runtime_failed",
                }
            )
            + "\n"
        )
        return
    if cancelled or not completed:
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "complete",
            "turn_not_persisted",
            reason="cancelled" if cancelled else "runtime_incomplete",
        )
        return

    effective_model_id = str(
        completion_metadata.get("model_id")
        or selected_acp_model_id
        or getattr(db_model, "model_name", "ACP agent")
    )
    metadata = {
        **(assistant_metadata if isinstance(assistant_metadata, dict) else {}),
        **completion_metadata,
        **context_usage_metadata,
        "model": effective_model_id,
        "model_id": effective_model_id,
        "provider": "acp",
        "acp_session_id": session_id,
        "acp_model_id": effective_model_id,
        "acp_security_level": (
            completion_metadata.get("acp_security_level") or selected_acp_security_level
        ),
        "acp_reasoning_effort": (
            completion_metadata.get("acp_reasoning_effort")
            or selected_acp_reasoning_effort
        ),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }
    _record_acp_generation_statistic(
        db,
        db_model=db_model,
        provider=provider,
        model_name=effective_model_id,
        metadata=metadata,
        user_id=user_id,
        success=True,
    )
    if messages_to_save:
        messages_to_save[-1]["meta"] = {
            **messages_to_save[-1].get("meta", {}),
            **metadata,
        }

    assistant_message = None
    if messages_to_save and not temp_request_flag:
        serialized_message = json.dumps(
            messages_to_save, ensure_ascii=False, default=str
        )
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "persist",
            "assistant_message_insert_begin",
            table="chat_messages",
            columns=[
                "chat_id",
                "model_id",
                "role",
                "reference_id",
                "content",
                "retry_count",
                "created_at",
            ],
            model_id=str(db_model.id),
            role="assistant",
            reference_id=str(reference_id or ""),
            retry_count=retry_count if retry_count is not None else 0,
            content_block_count=len(messages_to_save),
            content_block_types=[
                str(block.get("type") or "") for block in messages_to_save
            ],
            serialized_content=_sensitive_text_summary(serialized_message),
            commit=True,
        )
        try:
            assistant_message = create_chat_message(
                db,
                chat_id,
                db_model.id,
                "assistant",
                reference_id=reference_id,
                content=messages_to_save,
                retry_count=retry_count,
            )
        except Exception as exc:
            _log_acp_chat_step(
                generation_id,
                chat_id,
                "persist",
                "assistant_message_insert_failed",
                table="chat_messages",
                error_type=type(exc).__name__,
                error=_sensitive_text_summary(str(exc)),
            )
            raise
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "persist",
            "assistant_message_insert_committed",
            table="chat_messages",
            message_id=str(getattr(assistant_message, "id", "") or ""),
            persisted_chat_id=str(getattr(assistant_message, "chat_id", "") or ""),
            model_id=str(getattr(assistant_message, "model_id", "") or ""),
            role=str(getattr(assistant_message, "role", "") or ""),
            reference_id=str(getattr(assistant_message, "reference_id", "") or ""),
            retry_count=getattr(assistant_message, "retry_count", None),
            created_at=getattr(assistant_message, "created_at", None),
            persisted_content=_sensitive_text_summary(
                str(getattr(assistant_message, "content", "") or "")
            ),
        )
    else:
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "persist",
            "assistant_message_insert_skipped",
            reason=(
                "temporary_request"
                if temp_request_flag
                else "no_content_reasoning_or_tool_blocks"
            ),
            content_block_count=len(messages_to_save),
        )
    _log_acp_chat_step(
        generation_id,
        chat_id,
        "send",
        "completion_streamed_to_client",
        metadata=metadata,
        assistant_message_id=str(getattr(assistant_message, "id", "") or ""),
    )
    yield json.dumps({"t": "d", "d": "f", "c": metadata}) + "\n"
    if assistant_message:
        _log_acp_chat_step(
            generation_id,
            chat_id,
            "send",
            "assistant_message_id_streamed_to_client",
            assistant_message_id=str(assistant_message.id),
        )
        yield json.dumps({"t": "a_id", "d": assistant_message.id}) + "\n"
