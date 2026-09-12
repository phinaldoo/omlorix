from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user, verified_websocket_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.remote_connections.models import SshConnection, UserAcpProfile
from app.remote_connections.policy import (
    require_custom_acp_connections,
    require_ssh_connections,
)
from app.remote_connections.schemas import (
    AcpModelDiscoveryRequest,
    AcpModelDiscoveryResponse,
    AcpModelSelectionInput,
    AcpProfileInput,
    AcpProfileResponse,
    RemoteConnectionTestResponse,
    SshConnectionInput,
    SshConnectionResponse,
    SshConnectionUpdate,
    SshHostKeyDiscoveryRequest,
)
from app.remote_connections.service import (
    build_ssh_connection_test_candidate,
    delete_acp_profile,
    delete_ssh_connection,
    get_owned_ssh_connection,
    get_owned_acp_profile,
    get_terminal_target_for_model,
    save_acp_model_selection,
    save_acp_profile,
    save_ssh_connection,
    serialize_acp_profile,
)
from app.remote_connections.ssh import (
    classify_ssh_connection_error,
    discover_host_keys,
    serialize_ssh_connection,
    snapshot_ssh_connection,
    test_ssh_connection,
)
from app.remote_connections.terminal import run_ssh_terminal
from app.remote_connections.terminal import resolve_terminal_cwd
from app.utils.origin import enforce_same_origin


remote_connections_router = APIRouter(
    prefix="/api/v1/remote-connections", tags=["remote-connections"]
)


def _audit(db_log, request: Request, user_id: str, action: str, details: dict) -> None:
    """Record connection lifecycle events without logging credentials or commands."""
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details,
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="connections",
    )


@remote_connections_router.websocket("/acp-terminal")
async def open_acp_terminal(
    websocket: WebSocket,
    model_id: str = Query(min_length=1, max_length=255),
    cols: int = Query(default=80, ge=20, le=500),
    rows: int = Query(default=24, ge=5, le=200),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_websocket_user),
):
    """Open an interactive shell for the selected caller-owned ACP model."""
    try:
        require_custom_acp_connections(db, user.id)
        profile, connection = get_terminal_target_for_model(db, user.id, model_id)
    except HTTPException:
        await websocket.close(code=1008, reason="ACP terminal is not available")
        return

    # The terminal can remain open for hours. Copy the required values and
    # release the transaction opened by the ownership queries before entering
    # that long-lived loop, otherwise every terminal retains a pool connection.
    terminal_connection = snapshot_ssh_connection(connection)
    workspace_root = str(profile.workspace_root)
    profile_cwd = str(profile.cwd)
    details = {
        "model_id": str(profile.model_id),
        "profile_id": str(profile.id),
        "connection_id": str(connection.id),
    }
    db.close()

    await websocket.accept()
    _audit(db_log, websocket, user.id, "USER_ACP_TERMINAL_OPENED", details)
    return_code = None
    try:
        return_code = await run_ssh_terminal(
            websocket,
            terminal_connection,
            workspace_root=workspace_root,
            cwd=profile_cwd,
            columns=cols,
            rows=rows,
        )
    except WebSocketDisconnect:
        return_code = None
    except Exception:
        return_code = None
        try:
            await websocket.send_json({"type": "error", "code": "connection_failed"})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        _audit(
            db_log,
            websocket,
            user.id,
            "USER_ACP_TERMINAL_CLOSED",
            {**details, "exit_code": return_code},
        )
        try:
            await websocket.close()
        except RuntimeError:
            pass


@remote_connections_router.get("/ssh", response_model=list[SshConnectionResponse])
def list_ssh_connections(db: Session = Depends(get_db), user=Depends(verified_user)):
    """List the current user's SSH targets without returning secret material."""
    require_ssh_connections(db, user.id)
    rows = (
        db.query(SshConnection)
        .filter(SshConnection.user_id == user.id)
        .order_by(SshConnection.name.asc())
        .all()
    )
    return [serialize_ssh_connection(row) for row in rows]


@remote_connections_router.post("/ssh/discover-host-keys")
def discover_ssh_host_keys(
    payload: SshHostKeyDiscoveryRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Fetch untrusted keys for the user to compare with the remote device."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    try:
        return {"keys": discover_host_keys(payload.host, payload.port)}
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc


@remote_connections_router.post("/ssh", response_model=SshConnectionResponse)
def create_ssh_connection(
    payload: SshConnectionInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Create an encrypted SSH target owned exclusively by the caller."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    connection = save_ssh_connection(db, user.id, payload)
    _audit(
        db_log,
        request,
        user.id,
        "SSH_CONNECTION_CREATED",
        {
            "connection_id": connection.id,
            "host": connection.host,
            "port": connection.port,
        },
    )
    return serialize_ssh_connection(connection)


@remote_connections_router.post(
    "/ssh/test", response_model=RemoteConnectionTestResponse
)
def test_new_ssh_connection(
    payload: SshConnectionInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Test unsaved SSH form values without persisting their private key."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    connection = build_ssh_connection_test_candidate(user.id, payload)
    try:
        status = test_ssh_connection(connection)
    except Exception as exc:
        _audit(
            db_log,
            request,
            user.id,
            "SSH_CONNECTION_DRAFT_TEST_FAILED",
            {"host": payload.host, "port": payload.port},
        )
        return {"state": "error", "error_code": classify_ssh_connection_error(exc)}
    _audit(
        db_log,
        request,
        user.id,
        "SSH_CONNECTION_DRAFT_TEST_SUCCEEDED",
        {"host": payload.host, "port": payload.port},
    )
    return {"state": "connected", "status": status}


@remote_connections_router.put(
    "/ssh/{connection_id}", response_model=SshConnectionResponse
)
def update_ssh_connection(
    connection_id: str,
    payload: SshConnectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Update a caller-owned SSH target and optionally rotate its private key."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    connection = save_ssh_connection(db, user.id, payload, connection_id)
    _audit(
        db_log,
        request,
        user.id,
        "SSH_CONNECTION_UPDATED",
        {
            "connection_id": connection.id,
            "private_key_rotated": bool(payload.private_key),
        },
    )
    return serialize_ssh_connection(connection)


@remote_connections_router.post(
    "/ssh/{connection_id}/test", response_model=RemoteConnectionTestResponse
)
def test_saved_ssh_connection(
    connection_id: str,
    payload: SshConnectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Verify host pinning, authentication, and a side-effect-free command."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    saved_connection = get_owned_ssh_connection(db, user.id, connection_id)
    connection = build_ssh_connection_test_candidate(
        user.id,
        payload,
        saved_connection,
    )
    try:
        connection_status = test_ssh_connection(connection)
    except Exception as exc:
        _audit(
            db_log,
            request,
            user.id,
            "SSH_CONNECTION_TEST_FAILED",
            {"connection_id": saved_connection.id},
        )
        return {"state": "error", "error_code": classify_ssh_connection_error(exc)}
    _audit(
        db_log,
        request,
        user.id,
        "SSH_CONNECTION_TEST_SUCCEEDED",
        {"connection_id": saved_connection.id},
    )
    return {"state": "connected", "status": connection_status}


@remote_connections_router.delete("/ssh/{connection_id}")
def remove_ssh_connection(
    connection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Remove a caller-owned SSH target after dependency checks."""
    require_ssh_connections(db, user.id)
    enforce_same_origin(request, db)
    delete_ssh_connection(db, user.id, connection_id)
    _audit(
        db_log,
        request,
        user.id,
        "SSH_CONNECTION_DELETED",
        {"connection_id": connection_id},
    )
    return {"status": "success"}


@remote_connections_router.get("/acp", response_model=list[AcpProfileResponse])
def list_acp_profiles(db: Session = Depends(get_db), user=Depends(verified_user)):
    """List the caller's ACP profiles and model-picker IDs."""
    require_custom_acp_connections(db, user.id)
    rows = (
        db.query(UserAcpProfile)
        .filter(UserAcpProfile.user_id == user.id)
        .order_by(UserAcpProfile.name.asc())
        .all()
    )
    return [serialize_acp_profile(row) for row in rows]


@remote_connections_router.post("/acp/models", response_model=AcpModelDiscoveryResponse)
async def discover_models_for_acp_profile(
    payload: AcpModelDiscoveryRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Return session controls advertised by a caller-owned ACP connection."""
    from app.llm.acp.runtime import discover_acp_models

    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    profile, connection = get_terminal_target_for_model(db, user.id, payload.model_id)
    try:
        result = await discover_acp_models(
            command=profile.executable,
            arguments=[str(value) for value in (profile.arguments or [])],
            session_cwd=resolve_terminal_cwd(profile.workspace_root, profile.cwd),
            additional_directories=[
                resolve_terminal_cwd(profile.workspace_root, path)
                for path in (profile.additional_directories or [])
            ],
            existing_session_id=payload.session_id,
            ssh_connection=connection,
        )
    except Exception as exc:
        _audit(
            db_log,
            request,
            user.id,
            "USER_ACP_MODELS_DISCOVERY_FAILED",
            {"profile_id": profile.id, "model_id": profile.model_id},
        )
        raise HTTPException(
            status_code=502, detail="Unable to load models from the ACP agent."
        ) from exc
    _audit(
        db_log,
        request,
        user.id,
        "USER_ACP_MODELS_DISCOVERED",
        {
            "profile_id": profile.id,
            "model_id": profile.model_id,
            "available_model_count": len(result.get("models") or []),
            "available_security_level_count": len(result.get("security_levels") or []),
            "available_reasoning_effort_count": len(
                result.get("reasoning_efforts") or []
            ),
        },
    )
    return result


@remote_connections_router.post("/acp/model-selection")
def persist_acp_model_selection(
    payload: AcpModelSelectionInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Save selected ACP session controls for an owned Omlorix conversation."""
    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    save_acp_model_selection(
        db,
        user.id,
        chat_id=payload.chat_id,
        model_id=payload.model_id,
        session_id=payload.session_id,
        acp_model_id=payload.acp_model_id,
        acp_security_level=payload.acp_security_level,
        acp_reasoning_effort=payload.acp_reasoning_effort,
    )
    _audit(
        db_log,
        request,
        user.id,
        "USER_ACP_MODEL_SELECTED",
        {
            "chat_id": payload.chat_id,
            "model_id": payload.model_id,
            "acp_model_id": payload.acp_model_id,
            "acp_security_level": payload.acp_security_level,
            "acp_reasoning_effort": payload.acp_reasoning_effort,
        },
    )
    return {"status": "success"}


@remote_connections_router.post("/acp", response_model=AcpProfileResponse)
def create_acp_profile(
    payload: AcpProfileInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Create a private ACP model backed by one caller-owned SSH target."""
    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    profile = save_acp_profile(db, user.id, payload)
    _audit(
        db_log,
        request,
        user.id,
        "USER_ACP_PROFILE_CREATED",
        {
            "profile_id": profile.id,
            "connection_id": profile.ssh_connection_id,
            "model_id": profile.model_id,
        },
    )
    return serialize_acp_profile(profile)


@remote_connections_router.put("/acp/{profile_id}", response_model=AcpProfileResponse)
def update_acp_profile(
    profile_id: str,
    payload: AcpProfileInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Update a private ACP model without exposing its SSH credentials."""
    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    profile = save_acp_profile(db, user.id, payload, profile_id)
    _audit(
        db_log,
        request,
        user.id,
        "USER_ACP_PROFILE_UPDATED",
        {"profile_id": profile.id, "connection_id": profile.ssh_connection_id},
    )
    return serialize_acp_profile(profile)


@remote_connections_router.post(
    "/acp/{profile_id}/test", response_model=RemoteConnectionTestResponse
)
async def test_saved_acp_profile(
    profile_id: str,
    payload: AcpProfileInput,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Verify the SSH transport and ACP protocol handshake for a profile."""
    from app.llm.acp.runtime import test_acp_connection

    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    profile = get_owned_acp_profile(db, user.id, profile_id)
    connection = get_owned_ssh_connection(db, user.id, payload.ssh_connection_id)
    try:
        result = await test_acp_connection(
            {
                "command": payload.executable,
                "arguments": list(payload.arguments or []),
                "workspace_root": payload.workspace_root,
            },
            ssh_connection=connection,
        )
    except Exception as exc:
        _audit(
            db_log,
            request,
            user.id,
            "USER_ACP_PROFILE_TEST_FAILED",
            {"profile_id": profile.id},
        )
        return {"state": "error", "detail": str(exc)[:1000]}
    _audit(
        db_log,
        request,
        user.id,
        "USER_ACP_PROFILE_TEST_SUCCEEDED",
        {"profile_id": profile.id},
    )
    return {"state": "connected", **result}


@remote_connections_router.delete("/acp/{profile_id}")
def remove_acp_profile(
    profile_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Remove a caller-owned ACP profile and its private model entry."""
    require_custom_acp_connections(db, user.id)
    enforce_same_origin(request, db)
    delete_acp_profile(db, user.id, profile_id)
    _audit(
        db_log, request, user.id, "USER_ACP_PROFILE_DELETED", {"profile_id": profile_id}
    )
    return {"status": "success"}
