"""ACP connection lifecycle and turn execution.

The historical ``runtime`` module remains the public facade. Dependencies are
synchronized before calls to preserve its established patching surface.
"""

from __future__ import annotations

# Extracted implementations retain intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import runtime as _compat_source

_COMPAT_DEPENDENCIES = {
    "run_acp_turn": (
        "OmlorixAcpClient",
        "Path",
        "_log_acp_step",
        "_run_connected_acp_turn",
        "_text_fingerprint",
        "contextlib",
        "spawn_agent_process",
    ),
    "_run_connected_acp_turn": (
        "Implementation",
        "PROTOCOL_VERSION",
        "_block_summary",
        "_log_acp_step",
        "_protocol_payload",
        "_split_model_variant",
        "_text_fingerprint",
        "_validate_protocol_version",
        "asyncio",
        "attachment_has_usable_acp_representation",
        "build_acp_prompt_blocks",
        "contextlib",
        "extract_acp_session_controls",
        "normalize_acp_completion_metadata",
        "omlorix_client_capabilities",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


for _dependency_name in (
    "Implementation",
    "OmlorixAcpClient",
    "PROTOCOL_VERSION",
    "Path",
    "_block_summary",
    "_log_acp_step",
    "_protocol_payload",
    "_run_connected_acp_turn",
    "_split_model_variant",
    "_text_fingerprint",
    "_validate_protocol_version",
    "asyncio",
    "attachment_has_usable_acp_representation",
    "build_acp_prompt_blocks",
    "contextlib",
    "extract_acp_session_controls",
    "normalize_acp_completion_metadata",
    "omlorix_client_capabilities",
    "spawn_agent_process",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


async def _impl_run_acp_turn(
    *,
    command: str,
    arguments: list[str],
    process_cwd: str,
    environment: dict[str, str],
    session_cwd: str,
    additional_directories: list[str],
    existing_session_id: str | None,
    mode: str | None,
    selected_model_id: str | None,
    selected_security_level: str | None,
    selected_reasoning_effort: str | None,
    prompt: str,
    new_session_prompt: str | None,
    attachments: list[dict[str, Any]] | None,
    new_session_attachments: list[dict[str, Any]] | None,
    user_id: str,
    generation_id: str,
    permission_mode: str,
    permission_timeout_seconds: int,
    prompt_timeout_seconds: int,
    emit: EventCallback,
    is_cancelled: CancelledCallback,
    ssh_connection: Any | None = None,
) -> None:
    """Run one ACP prompt over a local or strictly pinned SSH stdio process."""
    _log_acp_step(
        generation_id,
        "transport",
        "runtime_start",
        transport="ssh" if ssh_connection is not None else "local_stdio",
        command=Path(command).name,
        argument_count=len(arguments),
        process_cwd=process_cwd,
        session_cwd=session_cwd,
        additional_directory_count=len(additional_directories),
        environment_variable_names=sorted(environment),
        has_existing_session=bool(existing_session_id),
        prompt=_text_fingerprint(prompt),
        fallback_prompt=(
            _text_fingerprint(new_session_prompt) if new_session_prompt else None
        ),
        attachment_count=len(attachments or []),
        fallback_attachment_count=len(new_session_attachments or []),
        prompt_timeout_seconds=prompt_timeout_seconds,
    )
    client = OmlorixAcpClient(
        emit=emit,
        user_id=user_id,
        generation_id=generation_id,
        permission_mode=permission_mode,
        permission_timeout_seconds=permission_timeout_seconds,
    )
    if ssh_connection is None:
        _log_acp_step(
            generation_id,
            "transport",
            "launch_local_process",
            executable=Path(command).name,
            argument_count=len(arguments),
        )
        process_context = spawn_agent_process(
            client, command, *arguments, env=environment, cwd=process_cwd
        )
        credential_context = contextlib.nullcontext()
    else:
        # SSH is itself the local subprocess. ACP's newline-delimited JSON flows
        # unchanged over the authenticated SSH channel to the remote agent.
        from app.remote_connections.ssh import (
            build_acp_remote_command,
            build_ssh_arguments,
            materialize_ssh_credentials,
            validate_ssh_destination,
        )

        destination = validate_ssh_destination(
            ssh_connection.host, int(ssh_connection.port or 22)
        )[0]
        _log_acp_step(
            generation_id,
            "transport",
            "prepare_ssh_process",
            connection_id=str(getattr(ssh_connection, "id", "") or ""),
            port=int(ssh_connection.port or 22),
            executable=Path(command).name,
            argument_count=len(arguments),
        )
        credential_context = materialize_ssh_credentials(ssh_connection)

    with credential_context as credentials:
        if ssh_connection is not None:
            identity_file, known_hosts_file = credentials
            ssh_arguments = build_ssh_arguments(
                ssh_connection,
                identity_file,
                known_hosts_file,
                destination_host=destination,
            )
            remote_command = build_acp_remote_command([command, *arguments])
            process_context = spawn_agent_process(
                client, "ssh", *ssh_arguments, remote_command
            )

        async with process_context as (agent, process):
            _log_acp_step(
                generation_id,
                "transport",
                "process_started",
                process_type=type(process).__name__,
                agent_type=type(agent).__name__,
            )
            await _run_connected_acp_turn(
                agent=agent,
                client=client,
                session_cwd=session_cwd,
                additional_directories=additional_directories,
                existing_session_id=existing_session_id,
                mode=mode,
                selected_model_id=selected_model_id,
                selected_security_level=selected_security_level,
                selected_reasoning_effort=selected_reasoning_effort,
                prompt=prompt,
                new_session_prompt=new_session_prompt,
                attachments=attachments,
                new_session_attachments=new_session_attachments,
                prompt_timeout_seconds=prompt_timeout_seconds,
                emit=emit,
                is_cancelled=is_cancelled,
            )
    _log_acp_step(generation_id, "transport", "runtime_finished")


async def _impl__run_connected_acp_turn(
    *,
    agent: Any,
    client: OmlorixAcpClient,
    session_cwd: str,
    additional_directories: list[str],
    existing_session_id: str | None,
    mode: str | None,
    selected_model_id: str | None,
    selected_security_level: str | None,
    selected_reasoning_effort: str | None,
    prompt: str,
    new_session_prompt: str | None,
    attachments: list[dict[str, Any]] | None,
    new_session_attachments: list[dict[str, Any]] | None,
    prompt_timeout_seconds: int,
    emit: EventCallback,
    is_cancelled: CancelledCallback,
) -> None:
    """Negotiate and run ACP independently of the selected stdio transport."""
    generation_id = client.generation_id
    _log_acp_step(
        generation_id,
        "send",
        "initialize_request",
        protocol_version=int(PROTOCOL_VERSION),
    )
    initialize_response = await agent.initialize(
        PROTOCOL_VERSION,
        omlorix_client_capabilities(),
        Implementation(name="omlorix", title="Omlorix", version="1.0"),
    )
    _log_acp_step(
        generation_id,
        "receive",
        "initialize_response",
        protocol_version=int(initialize_response.protocol_version),
        agent_capabilities=_protocol_payload(initialize_response.agent_capabilities),
    )
    _validate_protocol_version(initialize_response)
    capabilities = initialize_response.agent_capabilities
    session_id: str
    loaded = False
    session_response: Any | None = None
    requested_controls = any(
        str(value or "").strip()
        for value in (
            selected_model_id,
            selected_security_level,
            selected_reasoning_effort,
            mode,
        )
    )
    if existing_session_id and bool(getattr(capabilities, "load_session", False)):
        try:
            # ACP session/load intentionally replays history through
            # session/update. Omlorix already has that transcript, so hide
            # replayed events and resume streaming only for the new turn.
            client.suppress_session_updates = True
            _log_acp_step(
                generation_id,
                "send",
                "load_session_request",
                session_id=existing_session_id,
                cwd=session_cwd,
                additional_directory_count=len(additional_directories),
            )
            session_response = await agent.load_session(
                cwd=session_cwd,
                session_id=existing_session_id,
                mcp_servers=[],
                additional_directories=additional_directories,
            )
            session_id = existing_session_id
            # ACP permits a successful session/load response with no payload.
            # The Python SDK currently normalizes JSON `null` into an empty
            # LoadSessionResponse, so inspect the advertised controls instead
            # of relying on a `None` check. Such a session can be resumed as-is,
            # but it provides no option IDs with which to validate or apply
            # requested model/security controls. Recreate only when controls
            # must be applied; the replay prompt below preserves the Omlorix
            # conversation in that fallback session.
            load_controls_available = any(
                extract_acp_session_controls(session_response).values()
            )
            loaded = load_controls_available or not requested_controls
            _log_acp_step(
                generation_id,
                "receive",
                "load_session_response",
                session_id=session_id,
                response_available=load_controls_available,
                recreated_for_requested_controls=not loaded,
            )
            if not loaded:
                emit(
                    {
                        "kind": "warning",
                        "i18n_key": "acp_session_recreated",
                        "message": (
                            "The saved ACP session did not return configurable "
                            "controls; a new session was created."
                        ),
                    }
                )
        except Exception as exc:
            _log_acp_step(
                generation_id,
                "receive",
                "load_session_failed",
                session_id=existing_session_id,
                error_type=type(exc).__name__,
                error=_text_fingerprint(str(exc)),
            )
            emit(
                {
                    "kind": "warning",
                    "i18n_key": "acp_session_recreated",
                    "message": "The saved ACP session could not be loaded; a new session was created.",
                }
            )

    if not loaded:
        _log_acp_step(
            generation_id,
            "send",
            "new_session_request",
            cwd=session_cwd,
            additional_directory_count=len(additional_directories),
        )
        session_response = await agent.new_session(
            cwd=session_cwd,
            mcp_servers=[],
            additional_directories=additional_directories,
        )
        session_id = session_response.session_id
        _log_acp_step(
            generation_id,
            "receive",
            "new_session_response",
            session_id=session_id,
        )

    # Emit the resolved session for both new and resumed conversations. This
    # lets the chat layer persist a discovery-created session before prompting.
    emit({"kind": "session", "session_id": session_id})

    controls = extract_acp_session_controls(session_response)
    model_control = controls.get("model")
    security_control = controls.get("security")
    reasoning_control = controls.get("reasoning")

    requested_model = str(selected_model_id or "").strip()
    requested_security = str(selected_security_level or "").strip()
    requested_reasoning = str(selected_reasoning_effort or "").strip()
    _log_acp_step(
        generation_id,
        "controls",
        "controls_discovered",
        session_id=session_id,
        requested_model=requested_model,
        requested_security=requested_security,
        requested_reasoning=requested_reasoning,
        available_model_count=len((model_control or {}).get("options") or []),
        available_security_count=len((security_control or {}).get("options") or []),
        available_reasoning_count=len((reasoning_control or {}).get("options") or []),
    )

    # Older Codex ACP builds expose `model[effort]` as one model selector.
    # Accept previously persisted combined values while presenting independent
    # model and reasoning selectors to new Omlorix clients.
    parsed_requested_model = _split_model_variant(requested_model)
    if (
        parsed_requested_model
        and model_control
        and model_control.get("combined_variants")
    ):
        requested_model = parsed_requested_model[0]
        requested_reasoning = requested_reasoning or parsed_requested_model[1]

    effective_model_id = ""
    effective_reasoning = ""
    if model_control and model_control.get("combined_variants"):
        model_value = requested_model or str(model_control["current_value"])
        reasoning_value = requested_reasoning or (
            str(reasoning_control["current_value"]) if reasoning_control else ""
        )
        available_models = {
            str(option["id"]) for option in model_control.get("options") or []
        }
        available_efforts = {
            str(option["id"])
            for option in (reasoning_control or {}).get("options") or []
        }
        if model_value not in available_models:
            raise ValueError("The selected ACP model is not available for this session")
        if reasoning_value not in available_efforts:
            raise ValueError(
                "The selected ACP reasoning effort is not available for this session"
            )
        combined_value = model_control["combined_variants"].get(
            f"{model_value}\0{reasoning_value}"
        )
        if not combined_value:
            raise ValueError(
                "The selected ACP model does not support this reasoning effort"
            )
        if requested_model or requested_reasoning:
            _log_acp_step(
                generation_id,
                "send",
                "set_combined_model_reasoning",
                session_id=session_id,
                config_id=str(model_control["config_id"]),
                value=combined_value,
            )
            await agent.set_config_option(
                session_id=session_id,
                config_id=str(model_control["config_id"]),
                value=combined_value,
            )
        effective_model_id = combined_value
        effective_reasoning = reasoning_value
    else:
        if requested_model:
            if not model_control:
                raise ValueError(
                    "The ACP agent does not expose model selection for this session"
                )
            available_ids = {
                str(option["id"]) for option in model_control.get("options") or []
            }
            if requested_model not in available_ids:
                raise ValueError(
                    "The selected ACP model is not available for this session"
                )
            _log_acp_step(
                generation_id,
                "send",
                "set_model",
                session_id=session_id,
                config_id=str(model_control["config_id"]),
                value=requested_model,
            )
            await agent.set_config_option(
                session_id=session_id,
                config_id=str(model_control["config_id"]),
                value=requested_model,
            )
        effective_model_id = requested_model or (
            str(model_control.get("current_value") or "") if model_control else ""
        )

        if requested_reasoning:
            if not reasoning_control:
                raise ValueError(
                    "The ACP agent does not expose reasoning effort selection for this session"
                )
            available_efforts = {
                str(option["id"]) for option in reasoning_control.get("options") or []
            }
            if requested_reasoning not in available_efforts:
                raise ValueError(
                    "The selected ACP reasoning effort is not available for this session"
                )
            _log_acp_step(
                generation_id,
                "send",
                "set_reasoning",
                session_id=session_id,
                config_id=str(reasoning_control["config_id"]),
                value=requested_reasoning,
            )
            await agent.set_config_option(
                session_id=session_id,
                config_id=str(reasoning_control["config_id"]),
                value=requested_reasoning,
            )
        effective_reasoning = requested_reasoning or (
            str(reasoning_control.get("current_value") or "")
            if reasoning_control
            else ""
        )

    profile_security = str(mode or "").strip()
    requested_or_profile_security = requested_security or profile_security
    effective_security = requested_or_profile_security or (
        str(security_control.get("current_value") or "") if security_control else ""
    )
    if requested_or_profile_security:
        if not security_control:
            raise ValueError(
                "The ACP agent does not expose security level selection for this session"
            )
        available_security = {
            str(option["id"]) for option in security_control.get("options") or []
        }
        if requested_or_profile_security not in available_security:
            raise ValueError(
                "The selected ACP security level is not available for this session"
            )
    # Do not send a redundant mode/config update when the user and profile both
    # leave security at the agent-provided default. Some agents announce every
    # setter call, which would otherwise create noisy live updates.
    if requested_or_profile_security:
        if security_control.get("transport") == "session_mode":
            _log_acp_step(
                generation_id,
                "send",
                "set_session_mode",
                session_id=session_id,
                value=effective_security,
            )
            await agent.set_session_mode(
                session_id=session_id,
                mode_id=effective_security,
            )
        else:
            _log_acp_step(
                generation_id,
                "send",
                "set_security_config",
                session_id=session_id,
                config_id=str(security_control["config_id"]),
                value=effective_security,
            )
            await agent.set_config_option(
                session_id=session_id,
                config_id=str(security_control["config_id"]),
                value=effective_security,
            )

    # JSON-RPC notifications sent immediately before the load response can be
    # dispatched on the following event-loop tick. Keep suppression in place
    # until the new prompt is ready so replay cannot race into the visible UI.
    await asyncio.sleep(0)
    client.suppress_session_updates = False
    # A saved session can disappear when an agent prunes local state. In that
    # case the fallback prompt contains the full Omlorix conversation.
    prompt_text = prompt if loaded or not new_session_prompt else new_session_prompt
    prompt_attachments = (
        attachments if loaded or not new_session_prompt else new_session_attachments
    )
    prompt_blocks = build_acp_prompt_blocks(
        prompt_text,
        prompt_attachments,
        capabilities,
    )
    _log_acp_step(
        generation_id,
        "send",
        "prompt_prepared",
        session_id=session_id,
        resumed_session=loaded,
        prompt=_text_fingerprint(prompt_text),
        attachment_count=len(prompt_attachments or []),
        blocks=_block_summary(prompt_blocks),
    )
    if any(
        not attachment_has_usable_acp_representation(attachment, capabilities)
        for attachment in prompt_attachments or []
    ):
        emit(
            {
                "kind": "warning",
                "i18n_key": "acp_attachment_content_unavailable",
                "message": (
                    "One or more attachments could not be sent to this ACP agent "
                    "because their media type is unsupported or the file is unavailable."
                ),
            }
        )
    started_at = asyncio.get_running_loop().time()
    client.prompt_started_at = started_at
    prompt_task = asyncio.create_task(
        agent.prompt(
            session_id=session_id,
            prompt=prompt_blocks,
        )
    )
    _log_acp_step(
        generation_id,
        "send",
        "prompt_request_dispatched",
        session_id=session_id,
        block_count=len(prompt_blocks),
    )
    while not prompt_task.done():
        if is_cancelled():
            _log_acp_step(
                generation_id,
                "send",
                "cancel_request",
                session_id=session_id,
                reason="user_cancelled",
            )
            try:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        agent.cancel(session_id=session_id),
                        timeout=2,
                    )
            finally:
                prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await prompt_task
            emit({"kind": "cancelled"})
            return
        if asyncio.get_running_loop().time() - started_at > prompt_timeout_seconds:
            _log_acp_step(
                generation_id,
                "send",
                "cancel_request",
                session_id=session_id,
                reason="prompt_timeout",
            )
            try:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        agent.cancel(session_id=session_id),
                        timeout=2,
                    )
            finally:
                prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await prompt_task
            raise TimeoutError("ACP agent turn timed out")
        await asyncio.sleep(0.2)

    response = await prompt_task
    completed_at = asyncio.get_running_loop().time()
    first_token_duration = (
        client.first_output_at - started_at
        if client.first_output_at is not None
        else None
    )
    completion_metadata = normalize_acp_completion_metadata(
        response,
        selected_model_id=effective_model_id,
        generation_time=completed_at - started_at,
        time_to_first_token=first_token_duration,
    )
    _log_acp_step(
        generation_id,
        "receive",
        "prompt_response",
        session_id=session_id,
        duration_seconds=completed_at - started_at,
        time_to_first_token=first_token_duration,
        metadata=completion_metadata,
    )
    emit(
        {
            "kind": "complete",
            "session_id": session_id,
            "metadata": {
                **completion_metadata,
                **(
                    {"acp_reasoning_effort": effective_reasoning}
                    if effective_reasoning
                    else {}
                ),
                **(
                    {"acp_security_level": effective_security}
                    if effective_security
                    else {}
                ),
            },
        }
    )
