import asyncio
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.realtime import router as realtime_router
from app.realtime.schemas import (
    RealtimeToolCallRequest,
    RealtimeWebRTCOfferRequest,
    StartRealtimeSessionRequest,
    StopRealtimeSessionRequest,
)


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


def _runtime(**overrides):
    payload = {
        "id": "session-1",
        "user_id": "user-1",
        "group_id": "group-1",
        "chat_id": "chat-1",
        "project_id": "project-1",
        "provider": "openai",
        "model_id": "model-1",
        "base_model_id": "base-model-1",
        "skill_id": "skill-1",
        "tools": ["weather"],
        "tool_schemas": [],
        "pending_tool_calls": {"call-1": "weather"},
        "consumed_tool_call_ids": set(),
        "completed_tool_calls": {},
        "model_settings": {},
        "session_record_id": None,
        "active": True,
        "is_expired": lambda: False,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_live_transcription_closes_request_sessions_before_proxy():
    """The long-lived dictation socket must release both DB connections."""
    runtime = SimpleNamespace(
        provider_id="provider-1",
        provider_type="openai",
        model="gpt-live-transcribe",
        delay="low",
    )
    admission = SimpleNamespace(
        admission_id="admission-1",
        reserved_seconds=120,
    )
    db = MagicMock()
    db_log = MagicMock()
    audited = {}

    async def fake_proxy(_websocket, **kwargs):
        db.close.assert_called_once_with()
        db_log.close.assert_called_once_with()
        assert kwargs["admission_id"] == "admission-1"
        assert kwargs["max_duration_seconds"] == 120

    with patch.object(
        realtime_router,
        "load_live_transcription_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_router,
        "admit_user_duration_rate_limit",
        return_value=admission,
    ), patch.object(
        realtime_router,
        "_audit_realtime_event",
        side_effect=lambda _db_log, _request, _user_id, action, details: (
            audited.update({"action": action, "details": details})
        ),
    ), patch.object(
        realtime_router,
        "proxy_live_transcription",
        side_effect=fake_proxy,
    ):
        asyncio.run(
            realtime_router.open_live_transcription_proxy(
                websocket=SimpleNamespace(),
                db=db,
                db_log=db_log,
                user=SimpleNamespace(id="user-1", group_id="group-1"),
            )
        )

    assert audited == {
        "action": "START_LIVE_TRANSCRIPTION",
        "details": {
            "provider_id": "provider-1",
            "provider": "openai",
            "model": "gpt-live-transcribe",
            "delay": "low",
        },
    }


def test_start_realtime_session_audits_session_start():
    runtime = _runtime()

    with patch.object(
        realtime_router,
        "build_runtime_for_start",
        return_value=runtime,
    ), patch.object(
        realtime_router,
        "build_realtime_connection_response",
        return_value={
            "transport": "webrtc",
            "protocol_version": "webrtc-v1",
            "session": {},
            "max_session_seconds": 3600,
            "session_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
    ), patch.object(
        realtime_router,
        "create_audit_log",
    ) as mock_audit:
        response = realtime_router.start_realtime_session(
            payload=StartRealtimeSessionRequest(
                chat_id="chat-1",
                project_id="project-1",
                model_id="model-1",
                skill_id="skill-1",
            ),
            request=_request("/api/v1/realtime/session/start"),
            db=MagicMock(),
            db_log=MagicMock(),
            user=SimpleNamespace(id="user-1", group_id="group-1"),
        )

    assert response.session_id == "session-1"
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "START_REALTIME_SESSION"
    assert mock_audit.call_args.kwargs["category"] == "realtime"
    assert mock_audit.call_args.kwargs["details"] == {
        "runtime_id": "session-1",
        "chat_id": "chat-1",
        "project_id": "project-1",
        "provider": "openai",
        "model_id": "model-1",
        "base_model_id": "base-model-1",
        "skill_id": "skill-1",
        "tool_count": 1,
    }


def test_realtime_tool_call_audits_tool_execution():
    runtime = _runtime()

    with patch.object(
        realtime_router,
        "_require_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_router,
        "execute_realtime_tool_call",
        return_value={"payload": {"result": {"ok": True}}, "events": [{"type": "tool.result"}]},
    ) as mock_execute_tool, patch.object(
        realtime_router,
        "register_realtime_tool_result",
    ), patch.object(
        realtime_router,
        "create_audit_log",
    ) as mock_audit:
        response = realtime_router.realtime_tool_call(
            session_id="session-1",
            payload=RealtimeToolCallRequest(
                call_id="call-1",
                turn_id="turn-1",
                tool_name="weather",
                arguments={"city": "Berlin"},
            ),
            request=_request("/api/v1/realtime/session/session-1/tool-call"),
            db=MagicMock(),
            db_log=MagicMock(),
            user=SimpleNamespace(id="user-1", role="member"),
        )

    assert response["events"] == [{"type": "tool.result"}]
    assert mock_execute_tool.call_args.kwargs["tool_call_id"] == "call-1"
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "CALL_REALTIME_TOOL"
    assert mock_audit.call_args.kwargs["details"] == {
        "runtime_id": "session-1",
        "chat_id": "chat-1",
        "project_id": "project-1",
        "tool_name": "weather",
        "call_id": "call-1",
        "event_count": 1,
    }


def test_webrtc_offer_is_exchanged_only_through_backend_signaling():
    runtime = _runtime()
    fake_registry = SimpleNamespace(
        connection_lock=lambda _session_id: nullcontext(),
    )

    with patch.object(
        realtime_router,
        "session_registry",
        fake_registry,
    ), patch.object(
        realtime_router,
        "_require_runtime",
        return_value=runtime,
    ) as mock_require, patch.object(
        realtime_router,
        "exchange_realtime_webrtc_offer",
        return_value="provider-answer",
    ) as mock_exchange, patch.object(
        realtime_router,
        "start_openai_realtime_monitor",
        return_value=True,
    ) as mock_monitor:
        response = realtime_router.exchange_realtime_session_webrtc_offer(
            session_id="session-1",
            payload=RealtimeWebRTCOfferRequest(sdp="browser-offer"),
            db=MagicMock(),
            user=SimpleNamespace(id="user-1"),
        )

    assert response.sdp == "provider-answer"
    assert mock_require.call_count == 2
    assert mock_exchange.call_args.kwargs["offer_sdp"] == "browser-offer"
    mock_monitor.assert_called_once_with("session-1", user_id="user-1")


def test_stop_realtime_session_audits_stop():
    runtime = _runtime()
    fake_registry = SimpleNamespace(
        pop_expired=lambda _session_id: None,
        get=lambda _session_id: runtime,
        remove=MagicMock(),
    )

    with patch.object(realtime_router, "session_registry", fake_registry), patch.object(
        realtime_router,
        "mark_runtime_inactive",
    ), patch.object(
        realtime_router,
        "create_audit_log",
    ) as mock_audit:
        response = realtime_router.stop_realtime_session(
            session_id="session-1",
            payload=StopRealtimeSessionRequest(reason="user_left"),
            request=_request("/api/v1/realtime/session/session-1/stop"),
            db=MagicMock(),
            db_log=MagicMock(),
            user=SimpleNamespace(id="user-1"),
        )

    assert response == {"status": "success"}
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "STOP_REALTIME_SESSION"
    assert mock_audit.call_args.kwargs["details"] == {
        "runtime_id": "session-1",
        "chat_id": "chat-1",
        "project_id": "project-1",
        "status": "success",
        "reason": "user_left",
    }


def test_stop_realtime_session_recovers_persisted_runtime_before_stopping():
    runtime = _runtime()
    fake_registry = SimpleNamespace(
        pop_expired=lambda _session_id: None,
        get=lambda _session_id: None,
        remove=MagicMock(),
    )

    with patch.object(realtime_router, "session_registry", fake_registry), patch.object(
        realtime_router,
        "restore_realtime_session_runtime",
        return_value=runtime,
    ) as mock_restore, patch.object(
        realtime_router,
        "mark_runtime_inactive",
    ), patch.object(
        realtime_router,
        "create_audit_log",
    ) as mock_audit:
        response = realtime_router.stop_realtime_session(
            session_id="session-1",
            payload=StopRealtimeSessionRequest(reason="user_left"),
            request=_request("/api/v1/realtime/session/session-1/stop"),
            db=MagicMock(),
            db_log=MagicMock(),
            user=SimpleNamespace(id="user-1"),
        )

    assert response == {"status": "success"}
    mock_restore.assert_called_once()
    fake_registry.remove.assert_called_once_with("session-1")
    assert mock_audit.call_args.kwargs["details"]["status"] == "success"
