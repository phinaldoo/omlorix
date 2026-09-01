from __future__ import annotations

import copy
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user, verified_websocket_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.llm.models import (
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_TARGET_TYPE_DICTATION,
    RATE_LIMIT_TARGET_TYPE_REALTIME,
    admit_user_duration_rate_limit,
    finalize_duration_rate_limit_admission,
)
from app.realtime.proxy import (
    proxy_google_live_session,
    proxy_xai_realtime_session,
    signal_google_proxy_stop,
    signal_openai_realtime_monitor_stop,
    start_openai_realtime_monitor,
)
from app.realtime.schemas import (
    RefreshRealtimeConnectionRequest,
    PersistRealtimeTurnRequest,
    PrepareRealtimeInputRequest,
    RealtimePendingToolCallRequest,
    RealtimeToolCallRequest,
    RealtimeWebRTCOfferRequest,
    RealtimeWebRTCOfferResponse,
    StartRealtimeSessionRequest,
    StartRealtimeSessionResponse,
    StopRealtimeSessionRequest,
)
from app.realtime.service import (
    REALTIME_PROVIDER_CONNECTION_TERMINATED,
    REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING,
    build_realtime_connection_response,
    build_runtime_for_start,
    consume_realtime_pending_tool_call,
    exchange_realtime_webrtc_offer,
    execute_realtime_tool_call,
    generate_realtime_first_turn_title,
    get_realtime_completed_tool_call_response,
    mark_runtime_inactive,
    persist_realtime_runtime_state,
    persist_runtime_turn,
    prepare_runtime_text_input,
    register_realtime_pending_tool_call,
    register_realtime_tool_result,
    reconcile_expired_realtime_sessions,
    restore_realtime_session_runtime,
    serialized_realtime_provider_connection,
    session_registry,
    touch_realtime_runtime,
    validate_realtime_tool_arguments,
)
from app.realtime.transcription import (
    LIVE_TRANSCRIPTION_DEFAULT_MAX_SECONDS,
    load_live_transcription_runtime,
    proxy_live_transcription,
)
from app.settings.utils import get_default_model_id


realtime_router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


def _audit_realtime_event(
    db_log: Session,
    request: Request | None,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        category="realtime",
    )


def _require_runtime(db: Session, session_id: str, user_id: str):
    # Remove an expired local snapshot, but do not let that process-local view
    # decide the lifetime of a provider connection. A proxy or sideband monitor
    # in another worker may have refreshed the authoritative persisted runtime.
    expired_runtime = session_registry.pop_expired(session_id)
    # Persisted state is authoritative because the provider proxy and deadline
    # worker may run in a different process from this HTTP request.
    runtime = restore_realtime_session_runtime(db, session_id=session_id)
    if runtime is None:
        runtime = expired_runtime or session_registry.get(session_id)
    if not runtime or runtime.user_id != user_id:
        raise HTTPException(status_code=404, detail="Realtime session not found")
    if runtime.is_expired():
        terminated = mark_runtime_inactive(
            db,
            runtime,
            reason="expired",
            status="expired",
        )
        if terminated:
            session_registry.remove(session_id)
        else:
            signal_google_proxy_stop(session_id)
        raise HTTPException(status_code=409, detail="Realtime session expired")
    if not runtime.active:
        raise HTTPException(status_code=409, detail="Realtime session is no longer active")
    session_registry.create(runtime)
    return runtime


@realtime_router.post("/session/start", response_model=StartRealtimeSessionResponse)
def start_realtime_session(
    payload: StartRealtimeSessionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    # Recover this user's persisted sessions before checking open duration
    # reservations. Otherwise a short call that expired during a process
    # restart could keep its entire minute budget reserved.
    reconcile_expired_realtime_sessions(db, user_id=user.id)
    duration_admission = admit_user_duration_rate_limit(
        db,
        user_id=user.id,
        group_id=getattr(user, "group_id", None),
        target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
    )
    if isinstance(duration_admission, dict):
        _audit_realtime_event(
            db_log,
            request,
            user.id,
            "REALTIME_SESSION_RATE_LIMITED",
            {
                "rate_limit_id": duration_admission.get("rate_limit_id"),
                "period": duration_admission.get("period"),
                "remaining_usage_seconds": duration_admission.get("remaining_usage_seconds"),
            },
        )
        raise HTTPException(status_code=429, detail=duration_admission)

    requested_model_id = payload.model_id or get_default_model_id(db)
    try:
        runtime = build_runtime_for_start(
            db,
            user_id=user.id,
            group_id=getattr(user, "group_id", None),
            chat_id=payload.chat_id,
            project_id=payload.project_id,
            model_id=requested_model_id,
            skill_id=payload.skill_id,
        )
        if duration_admission is not None:
            runtime.rate_limit_admission_id = duration_admission.admission_id
            runtime.max_duration_seconds = duration_admission.reserved_seconds
            persist_realtime_runtime_state(db, runtime)
        connection_payload = build_realtime_connection_response(db, runtime)
    except Exception:
        if "runtime" in locals():
            mark_runtime_inactive(db, runtime, reason="start_failed", status="error")
            session_registry.remove(runtime.id)
        elif duration_admission is not None:
            finalize_duration_rate_limit_admission(
                db,
                duration_admission.admission_id,
                consumed_seconds=0,
                final_status=RATE_LIMIT_ADMISSION_FAILED,
            )
        raise

    _audit_realtime_event(
        db_log,
        request,
        user.id,
        "START_REALTIME_SESSION",
        {
            "runtime_id": runtime.id,
            "chat_id": runtime.chat_id,
            "project_id": runtime.project_id,
            "provider": runtime.provider,
            "model_id": runtime.model_id,
            "base_model_id": runtime.base_model_id,
            "skill_id": runtime.skill_id,
            "tool_count": len(runtime.tools),
        },
    )

    return StartRealtimeSessionResponse(
        session_id=runtime.id,
        chat_id=runtime.chat_id,
        created_chat=bool(getattr(runtime, "created_chat", False)),
        provider=runtime.provider,
        transport=connection_payload["transport"],
        protocol_version=connection_payload["protocol_version"],
        realtime_call_ready=True,
        signaling_url=connection_payload.get("signaling_url"),
        websocket_url=connection_payload.get("websocket_url"),
        session=connection_payload["session"],
        max_session_seconds=connection_payload.get("max_session_seconds", 3600),
        session_expires_at=connection_payload["session_expires_at"],
        session_limit_source=("rate_limit" if getattr(runtime, "rate_limit_admission_id", None) else "provider"),
    )


@realtime_router.post("/session/{session_id}/prepare-input")
def prepare_realtime_input(
    session_id: str,
    payload: PrepareRealtimeInputRequest,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    runtime = _require_runtime(db, session_id, user.id)
    response = prepare_runtime_text_input(
        db,
        runtime,
        text=payload.text or "",
        file_ids=payload.file_ids or [],
    )
    persist_realtime_runtime_state(db, runtime)
    return response


@realtime_router.post("/session/{session_id}/heartbeat")
def heartbeat_realtime_session(
    session_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Refresh browser/UI liveness without extending the billable quota lease.

    Duration reservations are renewed only by server-observed provider
    transports: the OpenAI enforcement worker or the Gemini proxy. A modified
    browser therefore cannot keep or release quota by forging heartbeats.
    """
    runtime = _require_runtime(db, session_id, user.id)
    touch_realtime_runtime(runtime)
    persist_realtime_runtime_state(db, runtime)
    return {"status": "ok", "expires_at": runtime.expires_at().isoformat()}


@realtime_router.post(
    "/session/{session_id}/webrtc-offer",
    response_model=RealtimeWebRTCOfferResponse,
)
def exchange_realtime_session_webrtc_offer(
    session_id: str,
    payload: RealtimeWebRTCOfferRequest,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Exchange SDP through Omlorix so provider credentials remain server-side."""
    runtime = _require_runtime(db, session_id, user.id)
    # One provider call must map to exactly one persisted termination handle.
    # Serializing offers also prevents two racing browser requests from
    # creating a second provider session before state is persisted.
    with session_registry.connection_lock(session_id):
        with serialized_realtime_provider_connection(db, session_id):
            runtime = _require_runtime(db, session_id, user.id)
            answer_sdp = exchange_realtime_webrtc_offer(
                db,
                runtime,
                offer_sdp=payload.sdp,
            )
    # OpenAI documents the call-id sideband WebSocket as the server control
    # channel for WebRTC. Do not return the SDP answer until Omlorix owns that
    # channel; it is the authoritative provider-liveness signal used for quota.
    if not start_openai_realtime_monitor(session_id, user_id=user.id):
        current_runtime = (
            restore_realtime_session_runtime(db, session_id=session_id)
            or runtime
        )
        mark_runtime_inactive(
            db,
            current_runtime,
            reason="monitor_start_failed",
            status="error",
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to monitor realtime provider connection",
        )
    return RealtimeWebRTCOfferResponse(sdp=answer_sdp)


@realtime_router.websocket("/session/{session_id}/google-live")
async def open_google_live_proxy(
    websocket: WebSocket,
    session_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_websocket_user),
):
    """Proxy one authenticated Gemini Live connection through Omlorix."""
    runtime = restore_realtime_session_runtime(db, session_id=session_id)
    if runtime is None:
        runtime = session_registry.get(session_id)
    if (
        runtime is None
        or runtime.user_id != user.id
        or not runtime.active
        or runtime.is_expired()
        or runtime.provider != "google_aistudio"
        or runtime.provider_connection_state
        in {
            REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING,
            REALTIME_PROVIDER_CONNECTION_TERMINATED,
        }
    ):
        await websocket.close(code=1008, reason="Realtime session is unavailable")
        return

    session_registry.create(runtime)
    # The proxy creates short-lived sessions for maintenance work. Release the
    # request-scoped database connection before entering the long-lived socket.
    db.close()
    await proxy_google_live_session(websocket, runtime=runtime)


@realtime_router.websocket("/session/{session_id}/xai-live")
async def open_xai_live_proxy(
    websocket: WebSocket,
    session_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_websocket_user),
):
    """Proxy one authenticated xAI Speech-to-Speech connection through Omlorix."""
    runtime = restore_realtime_session_runtime(db, session_id=session_id)
    if runtime is None:
        runtime = session_registry.get(session_id)
    if (
        runtime is None
        or runtime.user_id != user.id
        or not runtime.active
        or runtime.is_expired()
        or runtime.provider != "xai"
        or runtime.provider_connection_state
        in {
            REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING,
            REALTIME_PROVIDER_CONNECTION_TERMINATED,
        }
    ):
        await websocket.close(code=1008, reason="Realtime session is unavailable")
        return

    session_registry.create(runtime)
    db.close()
    await proxy_xai_realtime_session(websocket, runtime=runtime)


@realtime_router.websocket("/transcription/live")
async def open_live_transcription_proxy(
    websocket: WebSocket,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_websocket_user),
):
    """Open one authenticated, rate-limited live dictation stream."""
    try:
        runtime = load_live_transcription_runtime(db)
    except HTTPException:
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "code": "configuration_unavailable"}
        )
        await websocket.close(code=1008, reason="Live transcription unavailable")
        return

    duration_admission = admit_user_duration_rate_limit(
        db,
        user_id=user.id,
        group_id=getattr(user, "group_id", None),
        target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
    )
    if isinstance(duration_admission, dict):
        _audit_realtime_event(
            db_log,
            websocket,
            user.id,
            "LIVE_TRANSCRIPTION_RATE_LIMITED",
            {
                "rate_limit_id": duration_admission.get("rate_limit_id"),
                "period": duration_admission.get("period"),
                "remaining_usage_seconds": duration_admission.get(
                    "remaining_usage_seconds"
                ),
            },
        )
        db_log.close()
        await websocket.accept()
        browser_error_code = (
            "user_dictation_in_progress"
            if duration_admission.get("reason") == "active_reservation"
            else duration_admission.get(
                "code",
                "user_dictation_rate_limited",
            )
        )
        await websocket.send_json(
            {
                "type": "error",
                # Preserve the admission layer's precise reason. In
                # particular, an active or recently abandoned reservation is
                # not the same thing as consumed minute quota.
                "code": browser_error_code,
                "detail": duration_admission,
            }
        )
        await websocket.close(code=1008, reason="Dictation limit reached")
        return

    admission_id = (
        duration_admission.admission_id
        if duration_admission is not None
        else None
    )
    max_duration_seconds = (
        duration_admission.reserved_seconds
        if duration_admission is not None
        else LIVE_TRANSCRIPTION_DEFAULT_MAX_SECONDS
    )
    _audit_realtime_event(
        db_log,
        websocket,
        user.id,
        "START_LIVE_TRANSCRIPTION",
        {
            "provider_id": runtime.provider_id,
            "provider": runtime.provider_type,
            "model": runtime.model,
            "delay": runtime.delay,
        },
    )

    # The proxy renews/finalizes its reservation through independent short
    # sessions and has already persisted its audit event. Neither request-
    # scoped connection should remain checked out for the long-lived socket.
    db.close()
    db_log.close()
    await proxy_live_transcription(
        websocket,
        runtime=runtime,
        admission_id=admission_id,
        max_duration_seconds=max_duration_seconds,
    )


@realtime_router.post("/session/{session_id}/tool-call")
def realtime_tool_call(
    session_id: str,
    payload: RealtimeToolCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    runtime = _require_runtime(db, session_id, user.id)
    completed_response = get_realtime_completed_tool_call_response(
        runtime,
        call_id=payload.call_id,
        tool_name=payload.tool_name,
    )
    if completed_response is not None:
        persist_realtime_runtime_state(db, runtime)
        return completed_response
    validate_realtime_tool_arguments(
        runtime,
        tool_name=payload.tool_name,
        arguments=payload.arguments or {},
    )
    consume_realtime_pending_tool_call(
        runtime,
        call_id=payload.call_id,
        tool_name=payload.tool_name,
    )
    persist_realtime_runtime_state(db, runtime)
    result = execute_realtime_tool_call(
        db,
        session_id=runtime.id,
        tool_call_id=payload.call_id,
        turn_id=payload.turn_id,
        tool_name=payload.tool_name,
        tool_arguments=payload.arguments or {},
        user_id=runtime.user_id,
        group_id=runtime.group_id,
        project_id=runtime.project_id,
        model_id=runtime.model_id,
        model_name=getattr(runtime, "realtime_model", None) or runtime.model_id,
        provider=runtime.provider,
        model_settings=runtime.model_settings,
        chat_id=runtime.chat_id,
        user_role=user.role,
    )
    tool_payload = result.get("payload") or {}
    output_payload = tool_payload.get("result") if isinstance(tool_payload, dict) and "result" in tool_payload else tool_payload
    if isinstance(output_payload, (dict, list)):
        output_string = json.dumps(output_payload, ensure_ascii=False)
    else:
        output_string = str(output_payload if output_payload is not None else "")

    register_realtime_tool_result(
        runtime,
        call_id=payload.call_id,
        tool_name=payload.tool_name,
        arguments=payload.arguments or {},
        output_string=output_string,
        events=result.get("events") or [],
    )
    persist_realtime_runtime_state(db, runtime)
    _audit_realtime_event(
        db_log,
        request,
        user.id,
        "CALL_REALTIME_TOOL",
        {
            "runtime_id": runtime.id,
            "chat_id": runtime.chat_id,
            "project_id": runtime.project_id,
            "tool_name": payload.tool_name,
            "call_id": payload.call_id,
            "event_count": len(result.get("events") or []),
        },
    )
    return {
        "output": output_string,
        "events": result.get("events") or [],
    }


@realtime_router.post("/session/{session_id}/tool-call/pending")
def realtime_pending_tool_call(
    session_id: str,
    payload: RealtimePendingToolCallRequest,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    runtime = _require_runtime(db, session_id, user.id)
    response = register_realtime_pending_tool_call(
        runtime,
        call_id=payload.call_id,
        tool_name=payload.tool_name,
    )
    persist_realtime_runtime_state(db, runtime)
    return response


@realtime_router.post("/session/{session_id}/turn")
def persist_realtime_turn(
    session_id: str,
    payload: PersistRealtimeTurnRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    runtime = _require_runtime(db, session_id, user.id)
    response = persist_runtime_turn(
        db,
        runtime,
        turn_id=payload.turn_id,
        user_transcript=payload.user_transcript or "",
        assistant_transcript=payload.assistant_transcript or "",
        file_ids=payload.file_ids or [],
        interrupted=payload.interrupted,
        error_message=payload.error_message,
        usage=payload.usage.model_dump(exclude_none=True) if payload.usage else None,
        provider_interactions=[
            interaction.model_dump(exclude_none=True)
            for interaction in payload.provider_interactions
        ],
    )
    persist_realtime_runtime_state(db, runtime)
    if response.get("chat_title_pending") and response.get("chat_title"):
        # FastAPI starts this work only after the turn response has been sent.
        # Capture immutable values rather than sharing the request-scoped DB
        # session or reading mutable turn state from the runtime later.
        background_tasks.add_task(
            generate_realtime_first_turn_title,
            chat_id=runtime.chat_id,
            user_id=runtime.user_id,
            project_id=runtime.project_id,
            current_model_id=str(runtime.base_model_id or runtime.model_id or ""),
            model_settings=copy.deepcopy(runtime.model_settings),
            first_user_message=str(payload.user_transcript or "").strip(),
            expected_title=str(response["chat_title"]),
        )
    return response


@realtime_router.post("/session/{session_id}/stop")
def stop_realtime_session(
    session_id: str,
    request: Request,
    payload: StopRealtimeSessionRequest | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    expired_runtime = session_registry.pop_expired(session_id)
    if expired_runtime:
        if expired_runtime.user_id != user.id:
            raise HTTPException(status_code=404, detail="Realtime session not found")
        expired_runtime = (
            restore_realtime_session_runtime(db, session_id=session_id)
            or expired_runtime
        )
        terminated = mark_runtime_inactive(
            db,
            expired_runtime,
            reason="expired",
            status="expired",
        )
        if not terminated:
            signal_google_proxy_stop(session_id)
        _audit_realtime_event(
            db_log,
            request,
            user.id,
            "STOP_REALTIME_SESSION",
            {
                "runtime_id": session_id,
                "chat_id": expired_runtime.chat_id,
                "project_id": expired_runtime.project_id,
                "status": "expired" if terminated else "termination_pending",
                "reason": payload.reason if payload else None,
            },
        )
        return {"status": "already_stopped" if terminated else "stopping"}
    runtime = restore_realtime_session_runtime(db, session_id=session_id)
    if runtime is None:
        runtime = session_registry.get(session_id)
    if not runtime:
        _audit_realtime_event(
            db_log,
            request,
            user.id,
            "STOP_REALTIME_SESSION",
            {
                "runtime_id": session_id,
                "status": "already_stopped",
                "reason": payload.reason if payload else None,
            },
        )
        return {"status": "already_stopped"}
    if runtime.user_id != user.id:
        raise HTTPException(status_code=404, detail="Realtime session not found")
    if runtime.is_expired():
        terminated = mark_runtime_inactive(
            db,
            runtime,
            reason="expired",
            status="expired",
        )
        if terminated:
            session_registry.remove(session_id)
        else:
            signal_google_proxy_stop(session_id)
        _audit_realtime_event(
            db_log,
            request,
            user.id,
            "STOP_REALTIME_SESSION",
            {
                "runtime_id": session_id,
                "chat_id": runtime.chat_id,
                "project_id": runtime.project_id,
                "status": "expired" if terminated else "termination_pending",
                "reason": payload.reason if payload else None,
            },
        )
        return {"status": "already_stopped" if terminated else "stopping"}
    if not runtime.active:
        _audit_realtime_event(
            db_log,
            request,
            user.id,
            "STOP_REALTIME_SESSION",
            {
                "runtime_id": session_id,
                "chat_id": runtime.chat_id,
                "project_id": runtime.project_id,
                "status": "already_stopped",
                "reason": payload.reason if payload else None,
            },
        )
        return {"status": "already_stopped"}

    terminated = mark_runtime_inactive(
        db,
        runtime,
        reason=(payload.reason if payload else None),
        status="stopped",
    )
    if terminated:
        session_registry.remove(session_id)
        signal_openai_realtime_monitor_stop(session_id)
    else:
        # Gemini proxy ownership may live in this process. Wake it immediately;
        # another process will observe the persisted termination request during
        # its ten-second maintenance pass.
        signal_google_proxy_stop(session_id)
    _audit_realtime_event(
        db_log,
        request,
        user.id,
        "STOP_REALTIME_SESSION",
        {
            "runtime_id": session_id,
            "chat_id": runtime.chat_id,
            "project_id": runtime.project_id,
            "status": "success" if terminated else "termination_pending",
            "reason": payload.reason if payload else None,
        },
    )
    return {"status": "success" if terminated else "stopping"}


@realtime_router.post("/session/{session_id}/connection", response_model=StartRealtimeSessionResponse)
def refresh_realtime_session_connection(
    session_id: str,
    payload: RefreshRealtimeConnectionRequest | None = None,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    runtime = _require_runtime(db, session_id, user.id)
    # A Gemini resumption handle is provider-control state. Serialize it with
    # stop/proxy transitions and restore after locking so stale request state
    # cannot revive a provider session that another process stopped.
    with session_registry.connection_lock(session_id):
        with serialized_realtime_provider_connection(db, session_id):
            runtime = _require_runtime(db, session_id, user.id)
            connection_payload = build_realtime_connection_response(
                db,
                runtime,
                session_handle=(payload.session_handle if payload else None),
            )
    return StartRealtimeSessionResponse(
        session_id=runtime.id,
        chat_id=runtime.chat_id,
        created_chat=bool(getattr(runtime, "created_chat", False)),
        provider=runtime.provider,
        transport=connection_payload["transport"],
        protocol_version=connection_payload["protocol_version"],
        realtime_call_ready=True,
        signaling_url=connection_payload.get("signaling_url"),
        websocket_url=connection_payload.get("websocket_url"),
        session=connection_payload["session"],
        max_session_seconds=connection_payload.get("max_session_seconds", 3600),
        session_expires_at=connection_payload["session_expires_at"],
        session_limit_source=("rate_limit" if getattr(runtime, "rate_limit_admission_id", None) else "provider"),
    )
