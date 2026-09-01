from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

from app.chats.models import ChatMessages, Chats
from app.database import Base
from app.llm.models import Models
from app.llmstats.models import LLMGenerationStatistic
from app.realtime.schemas import PersistRealtimeTurnRequest
from app.realtime.service import (
    RealtimeSessionRuntime,
    _clamp_realtime_completed_at,
    _generate_and_persist_realtime_first_turn_title,
    persist_runtime_turn,
)
from app.realtime import service as realtime_service
from app.realtime import router as realtime_router


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Chats.__table__,
            ChatMessages.__table__,
            LLMGenerationStatistic.__table__,
            Models.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _runtime() -> RealtimeSessionRuntime:
    runtime = RealtimeSessionRuntime(
        id="session-1",
        user_id="user-1",
        group_id=None,
        chat_id="chat-1",
        project_id=None,
        model_id="model-1",
        base_model_id=None,
        agent_id=None,
        model_settings={},
        skill_id=None,
        skill_content=None,
        agent_instruction=None,
        provider="openai",
        provider_id="provider-1",
        realtime_model="gpt-realtime",
        voice="alloy",
        settings={},
    )
    runtime.turn.reset(next_turn_index=1)
    return runtime


def _insert_chat(db) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        Chats(
            id="chat-1",
            user_id="user-1",
            title="Realtime chat",
            meta={"status": "normal"},
            created_at=now,
            last_updated_at=now,
        )
    )
    db.commit()


def test_persist_runtime_turn_is_idempotent_for_retries():
    db = _db()
    _insert_chat(db)
    runtime = _runtime()

    first = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-1",
        user_transcript="Hello",
        assistant_transcript="Hi there",
        usage={"input_tokens": 4, "output_tokens": 6},
    )

    assert db.query(ChatMessages).count() == 2
    assert db.query(LLMGenerationStatistic).count() == 1

    second = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-1",
        user_transcript="Hello",
        assistant_transcript="Hi there",
        usage={"input_tokens": 100, "output_tokens": 100},
    )

    assert second == first
    assert db.query(ChatMessages).count() == 2
    assert db.query(LLMGenerationStatistic).count() == 1

    stat = db.query(LLMGenerationStatistic).one()
    assert stat.turn_id == "turn-1"
    assert stat.provider_response_id == "turn-1:final"
    assert stat.meta["input_tokens"] == 4
    assert stat.meta["output_tokens"] == 6
    assert stat.usage_verified is False


def test_turn_retry_stays_idempotent_after_analytics_are_deleted():
    """Chat persistence must not depend on optional statistics retention."""
    db = _db()
    _insert_chat(db)
    runtime = _runtime()

    first = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-purged-stats",
        user_transcript="Hello",
        assistant_transcript="Already saved",
    )
    db.query(LLMGenerationStatistic).delete(synchronize_session=False)
    db.commit()

    retried = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-purged-stats",
        user_transcript="Hello",
        assistant_transcript="Already saved",
    )

    assert retried == first
    assert db.query(ChatMessages).count() == 2
    assert db.query(LLMGenerationStatistic).count() == 0


def test_realtime_completion_timestamp_is_clamped_to_server_session_window():
    server_now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    session_started_at = server_now - timedelta(minutes=5)

    assert _clamp_realtime_completed_at(
        server_now - timedelta(days=30),
        session_started_at=session_started_at,
        server_now=server_now,
    ) == session_started_at
    assert _clamp_realtime_completed_at(
        server_now + timedelta(days=30),
        session_started_at=session_started_at,
        server_now=server_now,
    ) == server_now
    assert _clamp_realtime_completed_at(
        None,
        session_started_at=session_started_at,
        server_now=server_now,
    ) == server_now


def test_duplicate_provider_response_does_not_discard_new_turn_messages():
    db = _db()
    _insert_chat(db)
    runtime = _runtime()
    interaction = {
        "response_id": "replayed-response",
        "status": "completed",
        "usage": {"input_tokens": 4, "output_tokens": 6},
        "completed_at": datetime.now(timezone.utc),
    }

    persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-1",
        user_transcript="First user turn",
        assistant_transcript="First answer",
        provider_interactions=[interaction],
    )
    second = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-2",
        user_transcript="Second user turn",
        assistant_transcript="Second answer",
        provider_interactions=[interaction],
    )

    assert second["turn_index"] == 2
    assert db.query(ChatMessages).count() == 4
    assert db.query(LLMGenerationStatistic).count() == 1
    assert db.query(LLMGenerationStatistic).one().turn_id == "turn-1"


def test_persist_runtime_turn_rolls_back_all_writes_on_failure(monkeypatch):
    db = _db()
    _insert_chat(db)
    runtime = _runtime()

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(realtime_service, "create_realtime_response_statistic", _raise)

    with pytest.raises(HTTPException) as exc_info:
        persist_runtime_turn(
            db,
            runtime,
            turn_id="turn-rollback",
            user_transcript="Hello",
            assistant_transcript="Hi there",
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist realtime turn"
    assert db.query(ChatMessages).count() == 0
    assert db.query(LLMGenerationStatistic).count() == 0


def test_first_realtime_turn_defers_generation_after_returning_fallback(monkeypatch):
    db = _db()
    _insert_chat(db)
    chat = db.query(Chats).filter(Chats.id == "chat-1").one()
    chat.title = None
    db.add(
        Models(
            id="model-1",
            name="Test model",
            description="Test model",
            model_icon="default",
            provider="google_aistudio",
            provider_id="provider-1",
            model_name="gemini-test",
            settings={
                "title_generation": True,
                "title_generation_model": "current",
                "custom_title_generation_instruction": "Return a short title.",
            },
            capabilities=["completion"],
            access={},
            status="normal",
            is_active=True,
        )
    )
    db.commit()

    runtime = _runtime()
    runtime.created_chat = True
    runtime.base_model_id = "model-1"
    runtime.model_settings = {
        "title_generation": True,
        "title_generation_model": "current",
    }
    captured_requests = []

    def _generate_title(request):
        captured_requests.append(request)
        return "Alpine Weekend Plan"

    monkeypatch.setattr(realtime_service, "call_provider_title_generation", _generate_title)

    saved = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-title",
        user_transcript="Please plan a weekend hiking trip in the Alps.",
        assistant_transcript="Certainly, here is a plan.",
    )

    fallback_title = "Please plan a weekend hiking trip in the Alps."
    assert saved["chat_title"] == fallback_title
    assert saved["chat_title_pending"] is True
    assert db.query(Chats).filter(Chats.id == "chat-1").one().title == fallback_title
    # The turn-save path must not wait for the secondary provider request.
    assert captured_requests == []

    generated_title = _generate_and_persist_realtime_first_turn_title(
        db,
        chat_id="chat-1",
        user_id="user-1",
        project_id=None,
        current_model_id="model-1",
        model_settings=runtime.model_settings,
        first_user_message="Please plan a weekend hiking trip in the Alps.",
        expected_title=fallback_title,
    )

    assert generated_title == "Alpine Weekend Plan"
    assert db.query(Chats).filter(Chats.id == "chat-1").one().title == "Alpine Weekend Plan"
    assert len(captured_requests) == 1
    assert captured_requests[0].prompt == "Please plan a weekend hiking trip in the Alps."
    assert captured_requests[0].provider == "google_aistudio"


def test_turn_route_schedules_title_generation_after_response(monkeypatch):
    runtime = SimpleNamespace(
        chat_id="chat-1",
        user_id="user-1",
        project_id="project-1",
        base_model_id="model-1",
        model_id="agent-1",
        model_settings={
            "title_generation": True,
            "title_generation_model": "current",
        },
    )
    monkeypatch.setattr(realtime_router, "_require_runtime", lambda *_args: runtime)
    monkeypatch.setattr(
        realtime_router,
        "persist_runtime_turn",
        lambda *_args, **_kwargs: {
            "chat_id": "chat-1",
            "chat_title": "First message fallback",
            "chat_title_pending": True,
        },
    )
    monkeypatch.setattr(realtime_router, "persist_realtime_runtime_state", lambda *_args: None)

    background_tasks = BackgroundTasks()
    response = realtime_router.persist_realtime_turn(
        "session-1",
        PersistRealtimeTurnRequest(
            turn_id="turn-1",
            user_transcript="First message fallback",
        ),
        background_tasks,
        db=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert response["chat_title_pending"] is True
    assert len(background_tasks.tasks) == 1
    title_task = background_tasks.tasks[0]
    assert title_task.func is realtime_router.generate_realtime_first_turn_title
    assert title_task.kwargs["expected_title"] == "First message fallback"
    assert title_task.kwargs["current_model_id"] == "model-1"


def test_background_realtime_title_does_not_overwrite_concurrent_rename(monkeypatch):
    db = _db()
    _insert_chat(db)
    chat = db.query(Chats).filter(Chats.id == "chat-1").one()
    chat.title = "First message fallback"
    db.add(
        Models(
            id="model-1",
            name="Test model",
            description="Test model",
            model_icon="default",
            provider="google_aistudio",
            provider_id="provider-1",
            model_name="gemini-test",
            settings={},
            capabilities=["completion"],
            access={},
            status="normal",
            is_active=True,
        )
    )
    db.commit()

    provider_called = False

    def _generate_title(_request):
        nonlocal provider_called
        provider_called = True
        # Commit the rename from another session while the original session is
        # waiting for the provider response. The final title write must compare
        # against the persisted fallback instead of its cached Chat object.
        rename_db = sessionmaker(bind=db.get_bind())()
        try:
            renamed_chat = rename_db.query(Chats).filter(Chats.id == "chat-1").one()
            renamed_chat.title = "My manual title"
            rename_db.commit()
        finally:
            rename_db.close()
        return "Generated title"

    monkeypatch.setattr(realtime_service, "call_provider_title_generation", _generate_title)

    generated_title = _generate_and_persist_realtime_first_turn_title(
        db,
        chat_id="chat-1",
        user_id="user-1",
        project_id=None,
        current_model_id="model-1",
        model_settings={"title_generation_model": "current"},
        first_user_message="First message fallback",
        expected_title="First message fallback",
    )

    assert generated_title is None
    assert provider_called is True
    assert db.query(Chats).filter(Chats.id == "chat-1").one().title == "My manual title"


def test_first_realtime_turn_uses_message_fallback_when_title_generation_is_disabled():
    db = _db()
    _insert_chat(db)
    chat = db.query(Chats).filter(Chats.id == "chat-1").one()
    chat.title = None
    db.commit()

    runtime = _runtime()
    runtime.created_chat = True

    saved = persist_runtime_turn(
        db,
        runtime,
        turn_id="turn-fallback-title",
        user_transcript="Summarize the quarterly planning notes",
        assistant_transcript="Here is the summary.",
    )

    assert saved["chat_title"] == "Summarize the quarterly planning notes"
    assert "chat_title_pending" not in saved
    assert db.query(Chats).filter(Chats.id == "chat-1").one().title == saved["chat_title"]
