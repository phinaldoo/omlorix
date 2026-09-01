import asyncio
from contextlib import nullcontext
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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

from app.llm.schemas import ProviderEnum
from app.realtime import proxy as realtime_proxy
from app.realtime import service as realtime_service


def _runtime(**overrides):
    payload = {
        "provider": ProviderEnum.openai.value,
        "provider_id": "provider-1",
        "realtime_model": "gpt-realtime",
        "voice": "alloy",
        "settings": {},
        "model_settings": {},
        "tools": [],
        "tool_schemas": [],
        "agent_instruction": "admin agent policy",
        "skill_content": "admin skill policy",
        "id": "session-1",
        "user_id": "user-1",
        "active": True,
        "session_record_id": None,
        "rate_limit_admission_id": None,
        "provider_connection_state": realtime_service.REALTIME_PROVIDER_CONNECTION_IDLE,
        "provider_connection_owner_id": None,
        "provider_session_handle": None,
        "provider_connection_started_at": None,
        "provider_connection_last_seen_at": None,
        "provider_stop_requested_at": None,
        "provider_stop_reason": None,
        "google_session_handle": None,
        "created_at": datetime.now(timezone.utc),
        "last_activity_at": datetime.now(timezone.utc),
        "absolute_expires_at": lambda: datetime.now(timezone.utc) + timedelta(hours=1),
        "is_expired": lambda now=None: False,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_openai_client_session_config_redacts_instructions():
    runtime = _runtime()

    provider_config = realtime_service.build_realtime_session_config(runtime)
    client_config = realtime_service.build_realtime_client_session_config(runtime)

    assert "admin skill policy" in provider_config["instructions"]
    assert "admin agent policy" in provider_config["instructions"]
    assert "instructions" not in client_config


def test_connection_response_uses_redacted_openai_client_session():
    runtime = _runtime(provider_id="provider-1")

    response = realtime_service.build_realtime_connection_response(MagicMock(), runtime)

    assert "client_secret" not in response
    assert "call_url" not in response
    assert response["websocket_url"] is None
    assert response["signaling_url"] == (
        "/api/v1/realtime/session/session-1/webrtc-offer"
    )
    assert response["protocol_version"] == "webrtc-server-signaled-v1"
    assert "instructions" not in response["session"]
    assert response["max_session_seconds"] <= 3600
    assert response["session_expires_at"] > datetime.now(timezone.utc)


def test_google_connection_keeps_privileged_config_in_constrained_token():
    """Google's browser setup must not disclose agent or skill instructions."""
    runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        provider_id="google-provider",
        realtime_model="gemini-3.1-flash-live-preview",
        voice="Kore",
    )

    response = realtime_service.build_realtime_connection_response(
        MagicMock(),
        runtime,
    )

    with patch.object(
        realtime_proxy,
        "mint_google_aistudio_live_ephemeral_token",
        return_value="auth-token",
    ) as mock_mint:
        upstream_url, setup_envelope = realtime_proxy._google_provider_setup(
            MagicMock(),
            runtime,
        )

    provider_config = mock_mint.call_args.kwargs["session_config"]
    serialized_provider_config = str(
        provider_config.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
    )
    assert "admin agent policy" in serialized_provider_config
    assert "admin skill policy" in serialized_provider_config
    assert response["session"] == {
        "model": "models/gemini-3.1-flash-live-preview",
    }
    serialized_client_setup = str(response["session"])
    assert "admin agent policy" not in serialized_client_setup
    assert "admin skill policy" not in serialized_client_setup
    assert set(response["session"]) == {"model"}
    assert "client_secret" not in response
    assert "call_url" not in response
    assert response["websocket_url"] == (
        "/api/v1/realtime/session/session-1/google-live"
    )
    assert response["protocol_version"] == "google-live-proxy-v1"
    assert upstream_url.endswith("?access_token=auth-token")
    assert setup_envelope == {
        "setup": {"model": "models/gemini-3.1-flash-live-preview"}
    }


def test_google_proxy_claim_persists_one_cross_process_connection_owner():
    """A Gemini quota reservation exposes only one provider connection slot."""
    runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        session_record_id="session-record-1",
    )
    record = SimpleNamespace()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.with_for_update.return_value.first.return_value = record
    db = MagicMock()
    db.query.return_value = query

    with patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ) as mock_persist:
        claimed = realtime_service.claim_google_proxy_connection(
            db,
            session_id="session-1",
            connection_id="connection-1",
        )

    assert claimed is runtime
    assert (
        runtime.provider_connection_state
        == realtime_service.REALTIME_PROVIDER_CONNECTION_CONNECTING
    )
    assert runtime.provider_connection_owner_id == "connection-1"
    assert mock_persist.call_args.kwargs["provider_state_authoritative"] is True


@pytest.mark.parametrize(
    ("coroutine", "sync_helper"),
    [
        (
            realtime_proxy._heartbeat_google_proxy,
            realtime_proxy._heartbeat_google_proxy_once,
        ),
        (
            realtime_proxy._renew_google_proxy_admission,
            realtime_proxy._renew_google_proxy_admission_once,
        ),
    ],
)
def test_google_proxy_maintenance_offloads_database_work(
    monkeypatch,
    coroutine,
    sync_helper,
):
    """Proxy heartbeat and quota work each run outside the event loop."""
    calls = []

    async def fake_sleep(_seconds):
        return None

    async def fake_to_thread(function, *args):
        calls.append((function, args))
        return False

    monkeypatch.setattr(realtime_proxy.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(realtime_proxy.asyncio, "to_thread", fake_to_thread)

    asyncio.run(coroutine("session-1", "connection-1"))

    assert calls == [
        (
            sync_helper,
            ("session-1", "connection-1"),
        )
    ]


def test_google_admission_renewal_cannot_overwrite_a_concurrent_stop():
    """State is restored after slow renewal before requesting termination."""
    active_runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        rate_limit_admission_id="admission-1",
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_connection_owner_id="connection-1",
    )
    stopped_runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        provider_connection_state=(
            realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
        ),
        provider_connection_owner_id="connection-1",
    )
    db = MagicMock()

    with patch.object(
        realtime_proxy,
        "SessionLocal",
        return_value=db,
    ), patch.object(
        realtime_service.session_registry,
        "connection_lock",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "serialized_realtime_provider_connection",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        side_effect=[active_runtime, stopped_runtime],
    ), patch.object(
        realtime_proxy,
        "touch_duration_rate_limit_admission",
        return_value=False,
    ), patch.object(
        realtime_service,
        "request_provider_session_termination",
    ) as mock_terminate, patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ) as mock_persist:
        should_continue = realtime_proxy._renew_google_proxy_admission_once(
            "session-1",
            "connection-1",
        )

    assert should_continue is False
    mock_terminate.assert_not_called()
    mock_persist.assert_not_called()


def test_google_proxy_stale_fence_allows_several_missed_heartbeats():
    """Normal scheduler jitter cannot immediately release live ownership."""
    assert (
        realtime_service.REALTIME_PROVIDER_ACTIVITY_STALE_AFTER.total_seconds()
        >= realtime_proxy.GOOGLE_LIVE_PROXY_HEARTBEAT_SECONDS * 4
    )


def test_google_proxy_accept_failure_releases_candidate(monkeypatch):
    """A failed ASGI handshake must not block the next reconnect forever."""
    registry = realtime_proxy._GoogleProxyRegistry()
    client = MagicMock()
    client.accept = AsyncMock(side_effect=RuntimeError("handshake failed"))
    client.close = AsyncMock()
    runtime = _runtime(provider=ProviderEnum.google_aistudio.value)
    monkeypatch.setattr(realtime_proxy, "_google_proxy_registry", registry)

    asyncio.run(realtime_proxy.proxy_google_live_session(client, runtime=runtime))

    replacement = realtime_proxy._GoogleProxyController(
        connection_id="connection-2",
        loop=MagicMock(),
        close_event=MagicMock(),
    )
    assert registry.reserve_candidate("session-1", replacement) is True


def test_provider_connection_lock_cleanup_preserves_guarded_error():
    """The dedicated lock connection must preserve the guarded exception."""
    db = MagicMock()
    session_bind = MagicMock()
    session_bind.dialect.name = "postgresql"
    lock_connection = MagicMock()
    session_bind.engine.connect.return_value = lock_connection
    db.get_bind.return_value = session_bind
    lock_connection.execute.side_effect = [
        None,
        None,
        RuntimeError("unlock failed"),
    ]
    lock_connection.rollback.side_effect = RuntimeError("rollback failed")

    with pytest.raises(ValueError, match="guarded failure"):
        with realtime_service.serialized_realtime_provider_connection(
            db,
            "session-1",
        ):
            raise ValueError("guarded failure")

    executed_sql = [
        str(call.args[0]) for call in lock_connection.execute.call_args_list
    ]
    assert "SET LOCAL lock_timeout" in executed_sql[0]
    assert "pg_advisory_lock" in executed_sql[1]
    assert "pg_advisory_unlock" in executed_sql[2]
    db.execute.assert_not_called()
    db.rollback.assert_not_called()
    lock_connection.invalidate.assert_called_once_with()
    lock_connection.rollback.assert_called_once_with()
    lock_connection.close.assert_called_once_with()


def test_provider_connection_lock_uses_one_physical_connection_until_unlock():
    """Session transaction changes cannot move advisory-lock cleanup."""
    db = MagicMock()
    session_bind = MagicMock()
    session_bind.dialect.name = "postgresql"
    lock_connection = MagicMock()
    unlock_result = MagicMock()
    unlock_result.scalar.return_value = True
    lock_connection.execute.side_effect = [None, None, unlock_result]
    session_bind.engine.connect.return_value = lock_connection
    db.get_bind.return_value = session_bind

    with realtime_service.serialized_realtime_provider_connection(
        db,
        "session-1",
    ):
        # Realtime state persistence commits through this request Session. The
        # dedicated connection must remain untouched until the guard exits.
        db.commit()
        db.rollback()

    executed_sql = [
        str(call.args[0]) for call in lock_connection.execute.call_args_list
    ]
    assert "pg_advisory_lock" in executed_sql[1]
    assert "pg_advisory_unlock" in executed_sql[2]
    session_bind.engine.connect.assert_called_once_with()
    lock_connection.invalidate.assert_not_called()
    lock_connection.rollback.assert_called_once_with()
    lock_connection.close.assert_called_once_with()


def test_google_text_turn_uses_realtime_input_for_current_live_models():
    """In-conversation text for Gemini 3.1 must use realtimeInput.text."""
    runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        user_id="user-1",
        skill_file_ids=[],
        agent_file_ids=[],
        turn=SimpleNamespace(file_ids=[]),
        last_activity_at=datetime.now(timezone.utc),
    )

    prepared = realtime_service.prepare_runtime_text_input(
        MagicMock(),
        runtime,
        text="Explain the latest result",
        file_ids=[],
    )

    assert prepared["mode"] == "realtime_input"
    assert prepared["realtime_input"] == {
        "text": "Explain the latest result",
    }
    assert "client_content" not in prepared


def test_connection_response_returns_only_remaining_absolute_lifetime():
    """Provider setup time must not be added back to a quota reservation."""
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    deadline = now + timedelta(seconds=17, milliseconds=100)
    runtime = _runtime(
        provider_id="provider-1",
        absolute_expires_at=lambda: deadline,
    )

    with patch.object(realtime_service, "_utc_now", return_value=now):
        response = realtime_service.build_realtime_connection_response(MagicMock(), runtime)

    assert response["max_session_seconds"] == 18
    assert response["session_expires_at"] == deadline


def test_openai_webrtc_offer_is_server_signaled_and_keeps_termination_handle():
    """The browser receives SDP, but never the credential used by Omlorix."""
    browser_offer_sdp = "v=0\r\no=browser-offer\r\n"
    provider_answer_sdp = "v=0\r\no=provider-answer\r\n"
    runtime = _runtime(
        session_record_id="session-record-1",
        rate_limit_admission_id="admission-1",
    )
    response = MagicMock(
        is_success=True,
        status_code=201,
        headers={"Location": "https://api.openai.com/v1/realtime/calls/call_123"},
        text=provider_answer_sdp,
        content=provider_answer_sdp.encode("utf-8"),
    )

    with patch.object(
        realtime_service,
        "_load_openai_realtime_request_settings",
        return_value={"api_key": "server-api-key", "base_url": "https://api.openai.com/v1"},
    ), patch.object(
        realtime_service,
        "_build_realtime_safety_identifier",
        return_value="omlorix_user",
    ), patch.object(
        realtime_service.httpx,
        "post",
        return_value=response,
    ) as mock_post, patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ) as mock_persist, patch.object(
        realtime_service,
        "touch_duration_rate_limit_admission",
        return_value=True,
    ):
        answer = realtime_service.exchange_realtime_webrtc_offer(
            MagicMock(),
            runtime,
            offer_sdp=browser_offer_sdp,
        )

    assert answer == provider_answer_sdp
    assert runtime.provider_session_handle == "call_123"
    assert (
        runtime.provider_connection_state
        == realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
    )
    request_kwargs = mock_post.call_args.kwargs
    assert request_kwargs["headers"]["Authorization"] == "Bearer server-api-key"
    assert "Content-Type" not in request_kwargs["headers"]
    # The official multipart encoding retains the media types, omits filenames,
    # and must preserve the browser offer's final CRLF byte-for-byte.
    assert request_kwargs["files"]["sdp"] == (
        None,
        browser_offer_sdp,
        "application/sdp",
    )
    assert request_kwargs["files"]["session"][2] == "application/json"
    assert "admin agent policy" in request_kwargs["files"]["session"][1]
    assert mock_persist.call_args.kwargs["provider_state_authoritative"] is True


def test_openai_sideband_claim_uses_call_id_and_server_credentials():
    """The server monitor attaches to the exact provider call it created."""
    runtime = _runtime(
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_session_handle="call_123",
    )
    db = MagicMock()

    with patch.object(
        realtime_proxy,
        "SessionLocal",
        return_value=db,
    ), patch.object(
        realtime_service.session_registry,
        "connection_lock",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "serialized_realtime_provider_connection",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_service,
        "_load_openai_realtime_request_settings",
        return_value={
            "api_key": "server-api-key",
            "base_url": "https://api.openai.com/v1",
        },
    ), patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ) as mock_persist:
        monitor_url, headers, remaining_seconds = (
            realtime_proxy._claim_openai_realtime_monitor(
                "session-1",
                "monitor-1",
            )
        )

    assert monitor_url == "wss://api.openai.com/v1/realtime?call_id=call_123"
    assert headers == {"Authorization": "Bearer server-api-key"}
    assert remaining_seconds > 0
    assert runtime.provider_connection_owner_id == "monitor-1"
    assert mock_persist.call_args.kwargs["provider_state_authoritative"] is True


def test_openai_monitor_start_waits_only_for_thread_acknowledgement(monkeypatch):
    """Provider connection timeout must not become synchronous request latency."""
    controller = realtime_proxy._OpenAIRealtimeMonitorController(
        connection_id="monitor-1",
        close_event=MagicMock(),
        startup_event=MagicMock(),
        started=True,
    )
    controller.startup_event.wait.return_value = True
    monkeypatch.setattr(
        realtime_proxy,
        "_launch_openai_realtime_monitor",
        lambda _session_id: controller,
    )

    assert realtime_proxy.start_openai_realtime_monitor("session-1") is True
    controller.startup_event.wait.assert_called_once_with(
        timeout=realtime_proxy.OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS,
    )
    assert (
        realtime_proxy.OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS
        < realtime_proxy.OPENAI_REALTIME_MONITOR_SETUP_TIMEOUT_SECONDS
    )


def test_openai_monitor_registry_bounds_total_and_per_user_ownership():
    """One account cannot allocate an unbounded set of monitor threads."""

    registry = realtime_proxy._OpenAIRealtimeMonitorRegistry(
        max_total=2,
        max_per_user=1,
    )

    def controller(connection_id: str, user_id: str):
        return realtime_proxy._OpenAIRealtimeMonitorController(
            connection_id=connection_id,
            user_id=user_id,
            close_event=MagicMock(),
            startup_event=MagicMock(),
        )

    assert registry.reserve("session-1", controller("monitor-1", "user-1")) is True
    assert registry.reserve("session-2", controller("monitor-2", "user-1")) is False
    assert registry.reserve("session-3", controller("monitor-3", "user-2")) is True
    assert registry.reserve("session-4", controller("monitor-4", "user-3")) is False

    registry.release("session-1", "monitor-1")
    assert registry.reserve("session-2", controller("monitor-2", "user-1")) is True


def test_async_openai_monitor_start_offloads_thread_event_wait(monkeypatch):
    """Coroutine callers must not block their event loop on threading.Event."""
    controller = realtime_proxy._OpenAIRealtimeMonitorController(
        connection_id="monitor-1",
        close_event=MagicMock(),
        startup_event=MagicMock(),
        started=True,
    )
    to_thread_calls = []

    async def fake_to_thread(function, *args, **kwargs):
        to_thread_calls.append((function, args, kwargs))
        return True

    monkeypatch.setattr(
        realtime_proxy,
        "_launch_openai_realtime_monitor",
        lambda _session_id: controller,
    )
    monkeypatch.setattr(realtime_proxy.asyncio, "to_thread", fake_to_thread)

    started = asyncio.run(
        realtime_proxy.start_openai_realtime_monitor_async("session-1")
    )

    assert started is True
    assert to_thread_calls == [
        (
            controller.startup_event.wait,
            (),
            {
                "timeout": (
                    realtime_proxy.OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS
                )
            },
        )
    ]


def test_openai_monitor_start_timeout_requests_background_cleanup(monkeypatch):
    """A daemon that never acknowledges startup must still be asked to stop."""
    controller = realtime_proxy._OpenAIRealtimeMonitorController(
        connection_id="monitor-1",
        close_event=MagicMock(),
        startup_event=MagicMock(),
    )
    controller.startup_event.wait.return_value = False
    monkeypatch.setattr(
        realtime_proxy,
        "_launch_openai_realtime_monitor",
        lambda _session_id: controller,
    )

    assert realtime_proxy.start_openai_realtime_monitor("session-1") is False
    controller.close_event.set.assert_called_once_with()


def test_worker_terminates_stale_openai_call_instead_of_renewing_it():
    """Persisted ACTIVE state alone is not evidence of provider liveness."""
    now = datetime.now(timezone.utc)
    runtime = _runtime(
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_session_handle="call_123",
        provider_connection_last_seen_at=(
            now
            - realtime_service.REALTIME_PROVIDER_ACTIVITY_STALE_AFTER
            - timedelta(seconds=1)
        ),
    )
    db = MagicMock()

    with patch.object(
        realtime_service,
        "_mark_runtime_inactive_locked",
        return_value=True,
    ) as mock_inactive, patch.object(
        realtime_service,
        "touch_duration_rate_limit_admission",
    ) as mock_touch:
        terminated = realtime_service._reconcile_realtime_runtime_locked(
            db,
            runtime,
            now=now,
        )

    assert terminated is True
    assert mock_inactive.call_args.kwargs["reason"] == "provider_monitor_lost"
    mock_touch.assert_not_called()


def test_openai_call_is_not_released_when_provider_hangup_fails():
    """Quota remains reserved until Omlorix confirms provider termination."""
    runtime = _runtime(
        session_record_id="session-record-1",
        rate_limit_admission_id="admission-1",
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_session_handle="call_123",
    )

    with patch.object(
        realtime_service,
        "terminate_openai_realtime_call",
        return_value=False,
    ), patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ), patch.object(
        realtime_service,
        "finalize_duration_rate_limit_admission",
    ) as mock_finalize:
        terminated = realtime_service.mark_runtime_inactive(
            MagicMock(),
            runtime,
            reason="quota_deadline",
            status="expired",
        )

    assert terminated is False
    assert runtime.active is True
    assert (
        runtime.provider_connection_state
        == realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
    )
    mock_finalize.assert_not_called()


def test_stop_restores_provider_handle_after_waiting_for_signaling():
    """A stop must terminate the provider call committed by pending signaling."""

    stale_runtime = _runtime(
        session_record_id="session-record-1",
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_CONNECTING,
        provider_session_handle=None,
    )
    persisted_runtime = _runtime(
        # The persistence write is mocked below; omitting the statistic ID
        # keeps this focused test on lock-time restoration of provider state.
        session_record_id=None,
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_session_handle="call_123",
    )
    db = MagicMock()

    with patch.object(
        realtime_service.session_registry,
        "connection_lock",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "serialized_realtime_provider_connection",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        return_value=persisted_runtime,
    ), patch.object(
        realtime_service,
        "terminate_openai_realtime_call",
        return_value=True,
    ) as mock_terminate, patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ), patch.object(
        realtime_service,
        "finalize_duration_rate_limit_admission",
    ), patch.object(
        realtime_service,
        "update_realtime_session",
    ):
        terminated = realtime_service.mark_runtime_inactive(
            db,
            stale_runtime,
            reason="client_stop",
        )

    assert terminated is True
    mock_terminate.assert_called_once_with(db, persisted_runtime)
    assert stale_runtime.provider_session_handle == "call_123"
    assert stale_runtime.provider_connection_state == (
        realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATED
    )
    assert stale_runtime.active is False


def test_google_proxy_activation_preserves_pending_stop():
    """A Gemini setup completion cannot overwrite a stop that won the race."""

    runtime = _runtime(
        provider=ProviderEnum.google_aistudio.value,
        provider_connection_state=(
            realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
        ),
        provider_connection_owner_id="connection-1",
        provider_stop_reason="client_stop",
    )
    db = MagicMock()

    with patch.object(
        realtime_proxy,
        "SessionLocal",
        return_value=db,
    ), patch.object(
        realtime_service.session_registry,
        "connection_lock",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "serialized_realtime_provider_connection",
        return_value=nullcontext(),
    ), patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_service,
        "persist_realtime_runtime_state",
    ) as mock_persist:
        with pytest.raises(RuntimeError, match="stopped during Gemini setup"):
            realtime_proxy._activate_google_proxy_connection(
                "session-1",
                "connection-1",
            )

    assert runtime.provider_connection_state == (
        realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
    )
    assert runtime.provider_stop_reason == "client_stop"
    mock_persist.assert_not_called()
    db.close.assert_called_once_with()


def test_google_proxy_stop_reaches_setup_candidate():
    """A local stop wakes a proxy before it has been promoted to active."""

    loop = MagicMock()
    close_event = MagicMock()
    controller = realtime_proxy._GoogleProxyController(
        connection_id="connection-1",
        loop=loop,
        close_event=close_event,
    )
    registry = realtime_proxy._GoogleProxyRegistry()

    assert registry.reserve_candidate("session-1", controller) is True
    assert registry.request_close("session-1") is True

    loop.call_soon_threadsafe.assert_called_once_with(close_event.set)


def test_openai_hangup_uses_persisted_provider_call_id():
    runtime = _runtime(
        provider_connection_state=realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE,
        provider_session_handle="call_123",
    )
    response = MagicMock(is_success=True, status_code=200)

    with patch.object(
        realtime_service,
        "_load_openai_realtime_request_settings",
        return_value={"api_key": "server-api-key", "base_url": "https://api.openai.com/v1"},
    ), patch.object(
        realtime_service.httpx,
        "post",
        return_value=response,
    ) as mock_post:
        terminated = realtime_service.terminate_openai_realtime_call(
            MagicMock(),
            runtime,
        )

    assert terminated is True
    assert mock_post.call_args.args[0].endswith(
        "/realtime/calls/call_123/hangup"
    )
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer server-api-key"
    )


def test_openai_session_config_applies_supported_shared_controls():
    runtime = _runtime(
        settings={
            "input_transcription_enabled": False,
            "max_output_tokens": 1024,
        }
    )

    provider_config = realtime_service.build_realtime_session_config(runtime)

    assert "transcription" not in provider_config["audio"]["input"]
    assert provider_config["max_output_tokens"] == 1024


def test_realtime_documents_use_extracted_text_instead_of_input_file_parts():
    runtime = _runtime(user_id="user-1")
    db = MagicMock()
    file_info = {
        "file_category": "document",
        "file_type": "application/pdf",
    }

    with patch.object(realtime_service, "get_file_info", return_value=file_info), patch.object(
        realtime_service,
        "build_file_context_text",
        return_value="Extracted document text",
    ), patch.object(realtime_service, "upload_files") as mock_upload:
        parts = realtime_service.build_realtime_input_parts(
            db,
            runtime,
            text="Review this",
            file_ids=["file-1"],
        )

    mock_upload.assert_called_once_with(
        db,
        [],
        "user-1",
        input_formats_allowed=["image"],
    )
    assert all(part.get("type") != "input_file" for part in parts)
    assert parts[-1] == {"type": "input_text", "text": "[Attached file context]\nExtracted document text"}


def test_realtime_tool_setting_resolves_into_provider_and_execution_allowlists():
    """One admin selection should drive schemas and backend authorization."""
    normalized = realtime_service._normalize_settings_record(
        {
            "realtime_tools": ["weather", "weather", ""],
        }
    )
    tools, tool_schemas = realtime_service.resolve_configured_realtime_tools(
        None,
        configured_tools=normalized["tools"],
        model_settings={},
        user_id="user-1",
        project_id=None,
    )
    runtime = _runtime(tools=tools, tool_schemas=tool_schemas)

    provider_config = realtime_service.build_realtime_session_config(runtime)

    assert tools == ["weather"]
    assert {schema["name"] for schema in tool_schemas} == {"weather"}
    assert {schema["name"] for schema in provider_config["tools"]} == {"weather"}
    assert realtime_service.realtime_allowed_tool_names(runtime) == {"weather"}


def test_empty_realtime_tool_setting_disables_provider_tools():
    """An empty multi-select is an explicit no-tools policy."""
    tools, tool_schemas = realtime_service.resolve_configured_realtime_tools(
        None,
        configured_tools=[],
        model_settings={},
        user_id="user-1",
        project_id=None,
    )
    runtime = _runtime(tools=tools, tool_schemas=tool_schemas)

    provider_config = realtime_service.build_realtime_session_config(runtime)

    assert tools == []
    assert tool_schemas == []
    assert "tools" not in provider_config
    assert realtime_service.realtime_allowed_tool_names(runtime) == set()


def test_user_scoped_reconciliation_checks_short_rate_limited_sessions_immediately():
    """A short persisted call should not wait for the generic idle sweep."""
    now = datetime.now(timezone.utc)
    record = SimpleNamespace(id="stat-1", session_id="session-1")
    runtime = _runtime(
        id="session-1",
        active=True,
        rate_limit_admission_id="admission-1",
        created_at=now - timedelta(minutes=6),
        last_activity_at=now - timedelta(minutes=6),
        session_record_id="session-record-1",
        is_expired=lambda now=None: True,
    )
    db = MagicMock()

    with patch.object(
        realtime_service,
        "list_active_realtime_sessions_for_user",
        return_value=[record],
    ) as mock_list, patch.object(
        realtime_service,
        "restore_realtime_session_runtime",
        return_value=runtime,
    ), patch.object(
        realtime_service,
        "finalize_duration_rate_limit_admission",
    ) as mock_finalize, patch.object(
        realtime_service,
        "serialize_realtime_runtime",
        return_value={},
    ), patch.object(
        realtime_service,
        "update_realtime_session",
    ) as mock_update:
        realtime_service.reconcile_expired_realtime_sessions(db, user_id="user-1")

    mock_list.assert_called_once_with(
        db,
        user_id="user-1",
        limit=realtime_service.REALTIME_RUNTIME_CLEANUP_BATCH_SIZE,
    )
    mock_finalize.assert_called_once()
    assert mock_finalize.call_args.kwargs["consumed_seconds"] >= 360
    assert mock_update.call_args.kwargs["status"] == "expired"
