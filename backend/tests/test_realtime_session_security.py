import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "zstandard" not in sys.modules:
    sys.modules["zstandard"] = ModuleType("zstandard")

from app.realtime.router import _require_runtime
from app.realtime.models import (
    RealtimeSession,
    create_realtime_session,
    list_active_realtime_sessions_for_user,
)
from app.realtime.service import (
    REALTIME_SESSION_IDLE_TTL,
    RealtimeSessionRegistry,
    RealtimeSessionRuntime,
    _sanitize_realtime_runtime_settings,
    exchange_realtime_webrtc_offer,
    persist_realtime_runtime_state,
    restore_realtime_session_runtime,
    serialize_realtime_runtime,
)


def _make_runtime(**overrides):
    now = datetime.now(timezone.utc)
    runtime = RealtimeSessionRuntime(
        id="session-1",
        user_id="user-1",
        group_id=None,
        chat_id="chat-1",
        project_id=None,
        model_id="model-1",
        base_model_id="base-model-1",
        agent_id=None,
        model_settings={},
        skill_id=None,
        skill_content=None,
        agent_instruction=None,
        provider="openai",
        provider_id="provider-1",
        realtime_model="gpt-realtime",
        voice="alloy",
        settings={"temperature": 0.2},
        created_at=now,
        last_activity_at=now,
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def test_sanitize_realtime_runtime_settings_removes_provider_secrets():
    sanitized = _sanitize_realtime_runtime_settings(
        {
            "provider": "openai",
            "provider_id": "provider-1",
            "api_key": "super-secret",
            "base_url": "https://provider.example/v1",
            "organization": "org-1",
            "project": "proj-1",
            "voice": "alloy",
            "temperature": 0.3,
        }
    )

    assert sanitized == {
        "voice": "alloy",
        "temperature": 0.3,
    }


def test_require_runtime_expires_stale_sessions():
    registry = RealtimeSessionRegistry()
    runtime = _make_runtime(
        id="expired-session",
        created_at=datetime.now(timezone.utc) - REALTIME_SESSION_IDLE_TTL - timedelta(seconds=5),
        last_activity_at=datetime.now(timezone.utc) - REALTIME_SESSION_IDLE_TTL - timedelta(seconds=5),
    )
    registry.create(runtime)

    with patch("app.realtime.router.session_registry", registry):
        with pytest.raises(HTTPException) as exc_info:
            _require_runtime(MagicMock(), runtime.id, runtime.user_id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Realtime session expired"
    assert registry.get(runtime.id) is None
    assert runtime.active is False


def test_restore_realtime_session_runtime_round_trips_persisted_state():
    runtime = _make_runtime(
        id="persisted-session",
        session_record_id="session-record-1",
        group_id="group-1",
        project_id="project-1",
        model_settings={"native_websearch": True},
        skill_id="skill-1",
        skill_content="Follow the skill.",
        agent_instruction="Follow the agent.",
        settings={"temperature": 0.2},
        tools=["web_search"],
        tool_schemas=[{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
    )
    runtime.pending_tool_calls["call-1"] = "web_search"
    runtime.consumed_tool_call_ids.add("call-2")
    runtime.completed_tool_calls["call-3"] = {"tool_name": "web_search", "output": "done", "events": [{"type": "tool.done"}]}
    runtime.turn.turn_index = 3
    runtime.turn.user_transcript = "Hello"
    runtime.turn.assistant_transcript = "Hi"
    runtime.turn.file_ids = ["file-1"]
    runtime.turn.tool_calls = 1
    runtime.turn.tool_blocks = [{"type": "tool_call", "content": "web_search"}]

    fake_record = SimpleNamespace(
        id="session-record-1",
        session_id=runtime.id,
        user_id=runtime.user_id,
        chat_id=runtime.chat_id,
        provider=runtime.provider,
        provider_id=runtime.provider_id,
        model_name=runtime.realtime_model,
        status="active",
        started_at=runtime.created_at,
        created_at=runtime.created_at,
        last_updated_at=runtime.last_activity_at,
        runtime_state=serialize_realtime_runtime(runtime),
    )

    with patch("app.realtime.service.get_realtime_session_by_session_id", return_value=fake_record):
        restored = restore_realtime_session_runtime(MagicMock(), session_id=runtime.id)

    assert restored is not None
    assert restored.id == runtime.id
    assert restored.group_id == "group-1"
    assert restored.project_id == "project-1"
    assert restored.pending_tool_calls == {"call-1": "web_search"}
    assert restored.consumed_tool_call_ids == {"call-2"}
    assert restored.completed_tool_calls["call-3"]["output"] == "done"
    assert restored.turn.turn_index == 3
    assert restored.turn.user_transcript == "Hello"
    assert restored.turn.file_ids == ["file-1"]
    assert restored.session_record_id == "session-record-1"


def test_persist_realtime_runtime_state_survives_fresh_database_session():
    engine = create_engine("sqlite:///:memory:")
    RealtimeSession.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    session_record = create_realtime_session(
        db,
        session_id="persisted-session",
        user_id="user-1",
        chat_id="chat-1",
        model_id="model-1",
        model_name="gpt-realtime",
        provider="openai",
        provider_id="provider-1",
    )
    runtime = _make_runtime(id="persisted-session", session_record_id=session_record.id)
    runtime.pending_tool_calls["call-1"] = "web_search"
    persist_realtime_runtime_state(db, runtime)
    db.close()

    fresh_db = SessionLocal()
    try:
        restored = restore_realtime_session_runtime(fresh_db, session_id=runtime.id)
    finally:
        fresh_db.close()

    assert restored is not None
    assert restored.pending_tool_calls == {"call-1": "web_search"}


def test_non_authoritative_runtime_persistence_locks_before_provider_state_merge():
    """A stale request must merge provider control state while holding the row."""
    runtime = _make_runtime(
        session_record_id="session-record-1",
        provider_connection_state="idle",
        provider_session_handle=None,
    )
    record = SimpleNamespace(
        runtime_state={
            "provider_connection_state": "termination_pending",
            "provider_session_handle": "call_new",
        }
    )
    query = MagicMock()
    query.filter.return_value.with_for_update.return_value.first.return_value = record
    db = MagicMock()
    db.query.return_value = query

    with patch(
        "app.realtime.service.update_realtime_session"
    ) as mock_update:
        persist_realtime_runtime_state(db, runtime)

    query.filter.return_value.with_for_update.assert_called_once_with()
    persisted_runtime = mock_update.call_args.kwargs["runtime_state"]
    assert persisted_runtime["provider_connection_state"] == "termination_pending"
    assert persisted_runtime["provider_session_handle"] == "call_new"


@pytest.mark.parametrize(
    ("requested_limit", "expected_limit"),
    [(None, 100), (0, 0), (10_000, 1000)],
)
def test_active_realtime_statistics_normalizes_and_caps_limit(requested_limit, expected_limit):
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = []

    assert list_active_realtime_sessions_for_user(
        db,
        user_id="user-1",
        limit=requested_limit,
    ) == []
    query.limit.assert_called_once_with(expected_limit)


def test_require_runtime_recovers_session_from_persisted_state():
    registry = RealtimeSessionRegistry()
    runtime = _make_runtime(id="persisted-session", session_record_id="session-record-1")

    with patch("app.realtime.router.session_registry", registry), patch(
        "app.realtime.router.restore_realtime_session_runtime",
        return_value=runtime,
    ):
        recovered = _require_runtime(MagicMock(), runtime.id, runtime.user_id)

    assert recovered is runtime
    assert registry.get(runtime.id) is runtime


def test_require_runtime_does_not_expire_fresh_persisted_state_from_stale_cache():
    """Provider liveness in shared storage must override a stale local cache."""

    registry = RealtimeSessionRegistry()
    stale_time = datetime.now(timezone.utc) - REALTIME_SESSION_IDLE_TTL - timedelta(seconds=5)
    cached_runtime = _make_runtime(
        id="shared-session",
        created_at=stale_time,
        last_activity_at=stale_time,
    )
    persisted_runtime = _make_runtime(
        id="shared-session",
        session_record_id="session-record-1",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=45),
        last_activity_at=datetime.now(timezone.utc),
    )
    registry.create(cached_runtime)

    with patch("app.realtime.router.session_registry", registry), patch(
        "app.realtime.router.restore_realtime_session_runtime",
        return_value=persisted_runtime,
    ), patch("app.realtime.router.mark_runtime_inactive") as mark_inactive:
        recovered = _require_runtime(
            MagicMock(),
            persisted_runtime.id,
            persisted_runtime.user_id,
        )

    assert recovered is persisted_runtime
    assert registry.get(persisted_runtime.id) is persisted_runtime
    mark_inactive.assert_not_called()


def test_webrtc_offer_resolves_provider_credentials_on_demand():
    db = MagicMock()
    runtime = _make_runtime()
    response = MagicMock()
    response.content = b"provider-answer"
    response.text = "provider-answer"
    response.headers = {
        "Location": "https://provider.example/v1/realtime/calls/call_123"
    }
    response.is_success = True
    response.json.side_effect = ValueError("not json")

    with patch(
        "app.realtime.service._resolve_openai_client_kwargs",
        return_value={
            "api_key": "fresh-provider-secret",
            "base_url": "https://provider.example/v1",
            "organization": "org-123",
            "project": "proj-123",
        },
    ) as mock_resolve, patch(
        "app.realtime.service.get_jwt_material",
        return_value=("x" * 32, "HS512"),
    ), patch(
        "app.realtime.service.httpx.post",
        return_value=response,
    ) as mock_post, patch(
        "app.realtime.service.persist_realtime_runtime_state",
    ), patch(
        "app.realtime.service.touch_duration_rate_limit_admission",
        return_value=True,
    ):
        answer = exchange_realtime_webrtc_offer(
            db,
            runtime,
            offer_sdp="browser-offer",
        )

    mock_resolve.assert_called_once_with(
        db,
        openai_provider_id="provider-1",
        byok=None,
        openai_provider_type="openai",
    )
    assert answer == "provider-answer"
    assert runtime.provider_session_handle == "call_123"
    assert mock_post.call_args.args[0] == "https://provider.example/v1/realtime/calls"
    headers = mock_post.call_args.kwargs["headers"]
    assert headers == {
        "Authorization": "Bearer fresh-provider-secret",
        "OpenAI-Organization": "org-123",
        "OpenAI-Project": "proj-123",
        "OpenAI-Safety-Identifier": "omlorix_ef2f384848517affa0d198421ae88ecd4ddaa60af66e732ad791e7f0",
    }
    assert mock_post.call_args.kwargs["files"]["sdp"][1] == "browser-offer"
    assert len(headers["OpenAI-Safety-Identifier"]) == 64
    assert "api_key" not in runtime.settings
