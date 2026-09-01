import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats import utils as chat_utils
from app.chats import models as chat_models
from app.chats.models import (
    ChatReadState,
    Chats,
    mark_chat_read_for_user,
    record_successful_generation_completion,
)
from app.database import Base


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=[Chats.__table__, ChatReadState.__table__])
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _chat(
    chat_id: str,
    *,
    user_id: str = "user-1",
    project_id: str | None = None,
    pinned_position: int | None = None,
    meta: dict | None = None,
    archived: bool = False,
    updated_offset: int = 0,
) -> Chats:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Chats(
        id=chat_id,
        user_id=user_id,
        title=chat_id,
        project_id=project_id,
        share_id=None,
        archived=archived,
        pinned_position=pinned_position,
        meta=meta or {"status": "normal"},
        created_at=created_at,
        last_updated_at=created_at + timedelta(minutes=updated_offset),
    )


def test_paginated_chat_list_pages_in_database_without_get_chats(monkeypatch):
    db = _session()
    db.add_all(
        [
            _chat("pinned-2", pinned_position=2, updated_offset=500),
            _chat("pinned-1", pinned_position=1, updated_offset=400),
            _chat("hidden-temp", meta={"status": "temp"}, updated_offset=1000),
            _chat("hidden-deleted", meta={"shadow_deleted": True}, updated_offset=1001),
            _chat("archived", archived=True, updated_offset=1002),
            _chat("other-user", user_id="user-2", updated_offset=1003),
            *[_chat(f"chat-{i:03d}", updated_offset=i) for i in range(130)],
        ]
    )
    db.commit()

    def fail_get_chats(*args, **kwargs):
        raise AssertionError("list helpers should not load all chats through get_chats")

    monkeypatch.setattr(chat_utils, "get_chats", fail_get_chats)

    first_page = chat_utils.list_chats_paginated("user-1", db, offset=0, limit=20)
    second_page = chat_utils.list_chats_paginated("user-1", db, offset=20, limit=20)

    assert [chat.id for chat in first_page["pinned"]] == ["pinned-1", "pinned-2"]
    assert len(first_page["items"]) == 20
    assert first_page["items"][0].id == "chat-129"
    assert first_page["items"][-1].id == "chat-110"
    assert second_page["items"][0].id == "chat-109"
    assert first_page["total_unpinned"] == 130
    assert first_page["has_more"] is True
    assert first_page["total_pinned"] == 2
    assert first_page["pinned_has_more"] is False

    flattened = chat_utils.list_chats("user-1", db, limit=20)
    assert [chat.id for chat in flattened[:2]] == ["pinned-1", "pinned-2"]
    assert len(flattened) == 22


def test_paginated_chat_list_can_include_archived_chats():
    db = _session()
    db.add_all(
        [
            _chat("visible", updated_offset=1),
            _chat("archived", archived=True, updated_offset=2),
        ]
    )
    db.commit()

    default_page = chat_utils.list_chats_paginated("user-1", db, offset=0, limit=20)
    include_archived_page = chat_utils.list_chats_paginated(
        "user-1",
        db,
        offset=0,
        limit=20,
        include_archived=True,
    )

    assert [chat.id for chat in default_page["items"]] == ["visible"]
    assert [chat.id for chat in include_archived_page["items"]] == ["archived", "visible"]
    assert include_archived_page["total_unpinned"] == 2


def test_shadow_delete_persists_marker_and_hides_chat_from_paginated_list(monkeypatch):
    """A shadow-deleted chat remains durable but is excluded from listings."""
    db = _session()
    db.add(_chat("shadow-delete-target"))
    db.commit()

    def fake_group_setting(_group_id, _section, key, _db):
        return key == "shadow_chat_deletion" or key == "allow_chat_deletion"

    monkeypatch.setattr(chat_models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(chat_models, "_cancel_active_generation_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(chat_models, "record_chat_deleted_metric", lambda **_kwargs: None)

    assert chat_models.delete_chat("user-1", "group-1", "shadow-delete-target", db) is True

    # Force a database reload so this assertion cannot pass from the mutated
    # in-memory dictionary left on the ORM object.
    db.expire_all()
    persisted_chat = db.get(Chats, "shadow-delete-target")
    assert persisted_chat is not None
    assert persisted_chat.meta["shadow_deleted"] is True
    assert "shadow_deleted_at" in persisted_chat.meta

    page = chat_utils.list_chats_paginated("user-1", db, offset=0, limit=20)
    assert [chat.id for chat in page["items"]] == []


def test_list_chats_can_include_archived_chats():
    db = _session()
    db.add_all(
        [
            _chat("visible", updated_offset=1),
            _chat("archived", archived=True, updated_offset=2),
        ]
    )
    db.commit()

    assert [chat.id for chat in chat_utils.list_chats("user-1", db)] == ["visible"]
    assert [
        chat.id
        for chat in chat_utils.list_chats("user-1", db, include_archived=True)
    ] == ["archived", "visible"]


def test_paginated_chat_list_caps_pinned_chats():
    db = _session()
    db.add_all(
        [
            _chat(f"pinned-{i:03d}", pinned_position=i + 1, updated_offset=i)
            for i in range(chat_utils.MAX_PINNED_CHAT_LIST_LIMIT + 5)
        ]
    )
    db.commit()

    page = chat_utils.list_chats_paginated("user-1", db, offset=0, limit=20)

    assert len(page["pinned"]) == chat_utils.MAX_PINNED_CHAT_LIST_LIMIT
    assert page["total_pinned"] == chat_utils.MAX_PINNED_CHAT_LIST_LIMIT + 5
    assert page["pinned_has_more"] is True
    assert page["items"] == []


def test_response_completion_and_read_receipt_are_durable_and_idempotent():
    db = _session()
    chat = _chat("attention-chat")
    db.add(chat)
    db.commit()

    first_version = record_successful_generation_completion(db, chat.id, "generation-1")
    replayed_version = record_successful_generation_completion(db, chat.id, "generation-1")

    assert first_version == 1
    assert replayed_version == 1
    page = chat_utils.list_chats_paginated("user-1", db)
    assert page["items"][0].has_unread_response is True

    mark_chat_read_for_user(db, "user-1", page["items"][0])
    refreshed_page = chat_utils.list_chats_paginated("user-1", db)
    assert refreshed_page["items"][0].has_unread_response is False

    record_successful_generation_completion(db, chat.id, "generation-2")
    newest_page = chat_utils.list_chats_paginated("user-1", db)
    assert newest_page["items"][0].has_unread_response is True


def test_only_successful_terminal_events_advance_attention_state():
    assert chat_utils._is_successful_generation_done_line('{"t":"d","d":"f"}\n') is True
    assert chat_utils._is_successful_generation_done_line('{"t":"d","c":{"status":"cancelled"}}\n') is False
    assert chat_utils._is_successful_generation_done_line('{"t":"d","d":"c","c":{"status":"error"}}\n') is False
    assert chat_utils._is_successful_generation_done_line('{"t":"e","d":"failed"}\n') is False


def test_shared_provider_stream_requires_an_explicit_terminal_event():
    partial_line = '{"t":"c","d":"partial"}\n'

    with pytest.raises(chat_utils._IncompleteProviderStreamError):
        list(chat_utils._require_provider_stream_terminal([], None))

    with pytest.raises(chat_utils._IncompleteProviderStreamError):
        list(chat_utils._require_provider_stream_terminal([partial_line], None))

    assert chat_utils._is_generation_terminal_line('{"t":"d","d":"unknown"}\n') is False

    assert list(
        chat_utils._require_provider_stream_terminal(
            [partial_line, '{"t":"d","d":"f"}\n'],
            None,
        )
    ) == [partial_line, '{"t":"d","d":"f"}\n']

    assert list(
        chat_utils._require_provider_stream_terminal(
            [partial_line, '{"t":"e","d":"failed"}\n'],
            None,
        )
    ) == [partial_line, '{"t":"e","d":"failed"}\n']

    error_payload = json.loads(
        chat_utils._build_generation_error_line(
            chat_utils._IncompleteProviderStreamError("internal protocol detail"),
            user_role="user",
            byok=None,
        )
    )
    assert error_payload == {
        "t": "e",
        "d": "Connection interrupted. Please try again.",
        "admin_detail": None,
        "i18n_key": "chat_connection_interrupted_retry",
    }
