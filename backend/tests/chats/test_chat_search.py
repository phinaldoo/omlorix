import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats import utils as chat_utils
from app.chats.models import ChatMessages, ChatReadState, Chats
from app.database import Base


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=[Chats.__table__, ChatMessages.__table__, ChatReadState.__table__])
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _chat(chat_id: str, *, title: str, updated_offset: int, user_id: str = "user-1", meta: dict | None = None):
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Chats(
        id=chat_id,
        user_id=user_id,
        title=title,
        project_id=None,
        share_id=None,
        archived=False,
        pinned_position=None,
        meta=meta or {"status": "normal"},
        created_at=created_at,
        last_updated_at=created_at + timedelta(minutes=updated_offset),
    )


def _message(
    message_id: str,
    *,
    chat_id: str,
    role: str,
    content: str,
    created_offset: int,
):
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=created_offset)
    return ChatMessages(
        id=message_id,
        chat_id=chat_id,
        model_id="model-1",
        content=content,
        role=role,
        reference_id=None,
        generation={"generation_number": 1},
        retry_count=0,
        bookmarked=False,
        created_at=created_at,
    )


def test_search_chats_uses_database_filtering_without_n_plus_one(monkeypatch):
    db = _session()
    db.add_all(
        [
            _chat("chat-message", title="General", updated_offset=30),
            _chat("chat-title", title="Vector roadmap", updated_offset=20),
            _chat("chat-hidden", title="Vector temp", updated_offset=40, meta={"status": "temp"}),
            _chat("chat-other-user", title="Vector external", updated_offset=50, user_id="user-2"),
        ]
    )
    db.add_all(
        [
            _message("m1", chat_id="chat-message", role="user", content="Kickoff notes", created_offset=1),
            _message(
                "m2",
                chat_id="chat-message",
                role="assistant",
                content="We should add a vector search index soon for better recall.",
                created_offset=2,
            ),
            _message("m3", chat_id="chat-title", role="user", content="Roadmap kickoff preview", created_offset=3),
            _message("m4", chat_id="chat-hidden", role="user", content="vector should stay hidden", created_offset=4),
            _message("m5", chat_id="chat-other-user", role="user", content="vector should stay private", created_offset=5),
        ]
    )
    db.commit()

    def fail_get_chats(*args, **kwargs):
        raise AssertionError("search should not load all chats through get_chats")

    def fail_get_chat_messages(*args, **kwargs):
        raise AssertionError("search should not fetch messages one chat at a time")

    monkeypatch.setattr(chat_utils, "get_chats", fail_get_chats)
    monkeypatch.setattr(chat_utils, "db_get_chat_messages", fail_get_chat_messages)

    result = chat_utils.search_chats("user-1", "vector", db, offset=0, limit=10)

    assert result["total_count"] == 2
    assert result["has_more"] is False
    assert [item["chat_id"] for item in result["items"]] == ["chat-message", "chat-title"]
    assert "vector search index soon" in result["items"][0]["snippet"].lower()
    assert result["items"][1]["snippet"] == "Roadmap kickoff preview"
    assert result["items"][0]["last_updated_at"].endswith("+00:00")


def test_search_chats_paginates_sorted_results():
    db = _session()
    db.add_all(
        [
            _chat("chat-1", title="Alpha vector", updated_offset=10),
            _chat("chat-2", title="Beta vector", updated_offset=30),
            _chat("chat-3", title="Gamma vector", updated_offset=20),
        ]
    )
    db.add_all(
        [
            _message("m1", chat_id="chat-1", role="user", content="alpha preview", created_offset=1),
            _message("m2", chat_id="chat-2", role="user", content="beta preview", created_offset=2),
            _message("m3", chat_id="chat-3", role="user", content="gamma preview", created_offset=3),
        ]
    )
    db.commit()

    result = chat_utils.search_chats("user-1", "vector", db, offset=1, limit=1)

    assert result["total_count"] == 3
    assert result["has_more"] is True
    assert [item["chat_id"] for item in result["items"]] == ["chat-3"]
    assert result["items"][0]["snippet"] == "gamma preview"


def test_search_chats_extracts_plain_text_from_serialized_message_blocks():
    db = _session()
    serialized_content = json.dumps(
        [
            {"type": "reasoning", "content": "Internal reasoning"},
            {"type": "tool_call_result", "content": '{"status":"ok"}'},
            {
                "type": "user",
                "content": "E2E rich Markdown test: **bold text** and a table.",
            }
        ]
    )
    db.add(_chat("chat-copy", title="Rich Markdown (Copy)", updated_offset=10))
    db.add(
        _message(
            "m1",
            chat_id="chat-copy",
            role="user",
            content=serialized_content,
            created_offset=1,
        )
    )
    db.commit()

    title_result = chat_utils.search_chats("user-1", "Copy", db, offset=0, limit=10)
    message_result = chat_utils.search_chats("user-1", "Markdown", db, offset=0, limit=10)

    expected = "E2E rich Markdown test: **bold text** and a table."
    assert title_result["items"][0]["snippet"] == expected
    assert message_result["items"][0]["snippet"] == expected
    assert not title_result["items"][0]["snippet"].startswith("[{")
    assert "Internal reasoning" not in title_result["items"][0]["snippet"]


def test_chat_reference_candidates_support_offset_pagination():
    """The embedded picker can request every page without repeating chats."""

    db = _session()
    db.add_all(
        [
            _chat("chat-1", title="First", updated_offset=10),
            _chat("chat-2", title="Second", updated_offset=30),
            _chat("chat-3", title="Third", updated_offset=20),
        ]
    )
    db.commit()

    first_page = chat_utils.list_chat_reference_candidates("user-1", db, offset=0, limit=2)
    second_page = chat_utils.list_chat_reference_candidates("user-1", db, offset=2, limit=2)

    assert [item["chat_id"] for item in first_page["items"]] == ["chat-2", "chat-3"]
    assert first_page["total_count"] == 3
    assert first_page["has_more"] is True
    assert first_page["offset"] == 0
    assert first_page["limit"] == 2
    assert [item["chat_id"] for item in second_page["items"]] == ["chat-1"]
    assert second_page["has_more"] is False
    assert second_page["offset"] == 2


def test_search_chats_treats_like_wildcards_as_literal_text():
    db = _session()
    db.add(_chat("chat-1", title="Budget 100% ready", updated_offset=10))
    db.add(_message("m1", chat_id="chat-1", role="user", content="Coverage is at 100% now", created_offset=1))
    db.commit()

    result = chat_utils.search_chats("user-1", "100%", db, offset=0, limit=10)

    assert result["total_count"] == 1
    assert result["items"][0]["chat_id"] == "chat-1"
