"""ACP connection validation and model discovery.

The historical ``runtime`` module remains the public facade. Dependencies are
synchronized before calls to preserve its established patching surface.
"""

from __future__ import annotations

# Extracted implementations retain intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import runtime as _compat_source

_COMPAT_DEPENDENCIES = {
    "test_acp_connection": (
        "Implementation",
        "OmlorixAcpClient",
        "PROTOCOL_VERSION",
        "_validate_protocol_version",
        "asyncio",
        "build_process_environment",
        "contextlib",
        "omlorix_client_capabilities",
        "resolve_workspace_path",
        "spawn_agent_process",
    ),
    "discover_acp_models": (
        "Implementation",
        "OmlorixAcpClient",
        "PROTOCOL_VERSION",
        "_validate_protocol_version",
        "asyncio",
        "contextlib",
        "extract_acp_session_controls",
        "omlorix_client_capabilities",
        "serialize_acp_session_controls",
        "spawn_agent_process",
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
    "_validate_protocol_version",
    "asyncio",
    "build_process_environment",
    "contextlib",
    "extract_acp_session_controls",
    "omlorix_client_capabilities",
    "resolve_workspace_path",
    "serialize_acp_session_controls",
    "spawn_agent_process",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


async def _impl_test_acp_connection(
    settings: dict[str, Any],
    *,
    ssh_connection: Any | None = None,
) -> dict[str, Any]:
    """Perform a real ACP initialize handshake locally or through SSH."""
    workspace = str(settings.get("workspace_root") or "")
    if ssh_connection is None:
        workspace = str(resolve_workspace_path(workspace, "."))
    events: list[dict[str, Any]] = []
    client = OmlorixAcpClient(
        emit=events.append,
        user_id="connection-test",
        generation_id="connection-test",
        permission_mode="deny",
        permission_timeout_seconds=15,
    )
    credential_context: Any = contextlib.nullcontext()
    if ssh_connection is None:
        process_context = spawn_agent_process(
            client,
            str(settings.get("command") or ""),
            *list(settings.get("arguments") or []),
            env=build_process_environment(settings.get("environment_allowlist")),
            cwd=workspace,
        )
    else:
        from app.remote_connections.ssh import (
            build_acp_remote_command,
            build_ssh_arguments,
            materialize_ssh_credentials,
            validate_ssh_destination,
        )

        destination = validate_ssh_destination(
            ssh_connection.host, int(ssh_connection.port or 22)
        )[0]
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
            remote_command = build_acp_remote_command(
                [
                    str(settings.get("command") or ""),
                    *list(settings.get("arguments") or []),
                ]
            )
            process_context = spawn_agent_process(
                client, "ssh", *ssh_arguments, remote_command
            )
        async with process_context as (agent, _process):
            response = await asyncio.wait_for(
                agent.initialize(
                    PROTOCOL_VERSION,
                    omlorix_client_capabilities(),
                    Implementation(name="omlorix", title="Omlorix", version="1.0"),
                ),
                timeout=15,
            )
            _validate_protocol_version(response)
            return {
                "protocol_version": response.protocol_version,
                "agent_info": response.agent_info.model_dump(by_alias=True)
                if response.agent_info
                else None,
            }


async def _impl_discover_acp_models(
    *,
    command: str,
    arguments: list[str],
    session_cwd: str,
    additional_directories: list[str],
    existing_session_id: str | None = None,
    ssh_connection: Any | None = None,
) -> dict[str, Any]:
    """Open or resume one ACP session and return its advertised model selector.

    The returned session ID is sent with the next prompt so agents that support
    durable session loading can reuse discovery. Agents may still reject a
    later load; the normal turn path then creates a fresh session and reapplies
    the selected model before prompting.
    """
    client = OmlorixAcpClient(
        emit=lambda _event: None,
        user_id="model-discovery",
        generation_id="model-discovery",
        permission_mode="deny",
        permission_timeout_seconds=15,
    )
    credential_context: Any = contextlib.nullcontext()
    if ssh_connection is None:
        process_context = spawn_agent_process(
            client, command, *arguments, cwd=session_cwd
        )
    else:
        from app.remote_connections.ssh import (
            build_acp_remote_command,
            build_ssh_arguments,
            materialize_ssh_credentials,
            validate_ssh_destination,
        )

        destination = validate_ssh_destination(
            ssh_connection.host, int(ssh_connection.port or 22)
        )[0]
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

        async with process_context as (agent, _process):
            initialize_response = await asyncio.wait_for(
                agent.initialize(
                    PROTOCOL_VERSION,
                    omlorix_client_capabilities(),
                    Implementation(name="omlorix", title="Omlorix", version="1.0"),
                ),
                timeout=15,
            )
            _validate_protocol_version(initialize_response)
            capabilities = initialize_response.agent_capabilities
            response: Any | None = None
            session_id = str(existing_session_id or "").strip()
            if session_id and bool(getattr(capabilities, "load_session", False)):
                try:
                    client.suppress_session_updates = True
                    response = await asyncio.wait_for(
                        agent.load_session(
                            cwd=session_cwd,
                            session_id=session_id,
                            mcp_servers=[],
                            additional_directories=additional_directories,
                        ),
                        timeout=30,
                    )
                    # The SDK normalizes a response-less load into an empty
                    # response object. Discovery still needs advertised option
                    # metadata, so create a disposable new session when none
                    # was returned.
                    if not any(extract_acp_session_controls(response).values()):
                        session_id = ""
                except Exception:
                    session_id = ""
                finally:
                    client.suppress_session_updates = False

            if not session_id:
                response = await asyncio.wait_for(
                    agent.new_session(
                        cwd=session_cwd,
                        mcp_servers=[],
                        additional_directories=additional_directories,
                    ),
                    timeout=30,
                )
                session_id = str(response.session_id)

            controls = extract_acp_session_controls(response)
            if not any(controls.values()):
                raise ValueError(
                    "This ACP agent does not advertise selectable session controls"
                )
            return {
                "session_id": session_id,
                **serialize_acp_session_controls(controls),
            }
