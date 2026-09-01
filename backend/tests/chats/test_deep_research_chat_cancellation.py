from types import SimpleNamespace

from app.chats import router as chats_router
from app.tools.deep_research import models as deep_research_models


def test_chat_cancel_persists_matching_deep_research_cancellation(monkeypatch):
    """The shared chat endpoint exposes cancellation to the inline research run."""

    run = SimpleNamespace(id="run-1", chat_id="chat-1")
    cancelled = []
    monkeypatch.setattr(
        deep_research_models,
        "get_user_deep_research_run_by_generation",
        lambda _db, generation_id, user_id: (
            run if (generation_id, user_id) == ("generation-1", "user-1") else None
        ),
    )
    monkeypatch.setattr(
        deep_research_models,
        "request_deep_research_cancellation",
        lambda _db, selected_run: cancelled.append(selected_run),
    )
    monkeypatch.setattr(
        chats_router.stream_hub,
        "get_chat_for_generation",
        lambda _generation_id: None,
    )
    monkeypatch.setattr(
        chats_router.cancel_registry,
        "cancel",
        lambda generation_id: cancelled.append(generation_id),
    )
    monkeypatch.setattr(chats_router, "_log_chat_event", lambda *_args, **_kwargs: None)

    response = chats_router.cancel_generation(
        "generation-1",
        request=object(),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert response == {"status": "success"}
    assert cancelled == ["generation-1", run]


def test_chat_cancel_accepts_user_owned_generation_before_stream_start(monkeypatch):
    """An immediate Stop is authorized from the pre-worker ID reservation."""

    cancelled = []
    monkeypatch.setattr(
        deep_research_models,
        "get_user_deep_research_run_by_generation",
        lambda *_args: None,
    )
    monkeypatch.setattr(chats_router.stream_hub, "get_chat_for_generation", lambda _id: None)
    monkeypatch.setattr(
        chats_router.cancel_registry,
        "is_owned_by",
        lambda generation_id, user_id: (generation_id, user_id) == ("generation-early", "user-1"),
    )
    monkeypatch.setattr(chats_router.cancel_registry, "cancel", cancelled.append)
    monkeypatch.setattr(chats_router, "_log_chat_event", lambda *_args, **_kwargs: None)

    response = chats_router.cancel_generation(
        "generation-early",
        request=object(),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert response == {"status": "success"}
    assert cancelled == ["generation-early"]
