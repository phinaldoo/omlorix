"""Contract tests for duplicating and branching saved chats."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Keep this focused unit test independent from optional compression and metrics
# packages that the wider application initializes in production.
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


from app.chats import models as chat_models
from app.chats import utils as chat_utils
from app.chats.models import ChatMessages, Chats


BASE_TIME = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


def _message(
    message_id: str,
    role: str,
    offset: int,
    *,
    reference_id: str | None = None,
    content: str | None = None,
) -> ChatMessages:
    """Build a fully populated stored row for copy-contract assertions."""
    return ChatMessages(
        id=message_id,
        chat_id="source-chat",
        model_id=f"model-{message_id}",
        role=role,
        content=content or json.dumps([{"type": role, "content": message_id}]),
        reference_id=reference_id,
        realtime_session_id="realtime-session",
        realtime_turn_id=f"turn-{message_id}",
        generation={"generation_number": offset + 1, "provider": "test"},
        thinking=f"thinking-{message_id}",
        retry_count=offset,
        bookmarked=True,
        created_at=BASE_TIME + timedelta(seconds=offset),
    )


class _Query:
    """Small SQLAlchemy-query stand-in for the two copy helpers."""

    def __init__(self, *, result=None, rows=None):
        self._result = result
        self._rows = list(rows or [])

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return list(self._rows)


class _CopyDb:
    """Capture newly constructed chat/message rows without a database server."""

    def __init__(self, source_chat: Chats, messages: list[ChatMessages], branch_message: ChatMessages):
        self.source_chat = source_chat
        self.messages = list(messages)
        self.branch_message = branch_message
        self.new_chat = None
        self.new_messages: list[ChatMessages] = []
        self.commits = 0

    def query(self, model):
        if model is Chats:
            return _Query(result=self.source_chat)
        if model is ChatMessages:
            return _Query(result=self.branch_message, rows=self.messages)
        return _Query()

    def add(self, value):
        if isinstance(value, Chats):
            self.new_chat = value
            # SQLAlchemy normally materializes this default during flush.
            value.id = "new-chat"
            if value.archived is None:
                value.archived = False
            return
        self.new_messages.append(value)

    def add_all(self, values):
        self.new_messages.extend(values)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1


def _source_chat() -> Chats:
    """Return a source with state that copied chats must deliberately reset."""
    return Chats(
        id="source-chat",
        user_id="user-1",
        title="Planning",
        project_id="project-1",
        share={"access_mode": "invited"},
        share_id="share-1",
        archived=True,
        pinned_position=3,
        meta={"status": "normal", "agent_id": "agent-1", "base_model_id": "model-1"},
        created_at=BASE_TIME,
        last_updated_at=BASE_TIME,
        response_version=7,
        last_completed_generation_id="generation-7",
    )


def test_branch_boundary_keeps_trailing_non_user_rows_until_next_user_turn():
    """Tool/system rows in the selected response turn belong to the branch."""
    messages = [
        _message("user-1", "user", 0),
        _message("assistant-1", "assistant", 1, reference_id="user-1"),
        _message("tool-1", "tool", 2, reference_id="assistant-1"),
        _message("system-1", "system", 3),
        _message("user-2", "user", 4),
        _message("assistant-2", "assistant", 5, reference_id="user-2"),
    ]

    selected = chat_utils._select_branch_message_rows(messages, "assistant-1")

    assert [message.id for message in selected] == [
        "user-1",
        "assistant-1",
        "tool-1",
        "system-1",
    ]


def test_cloned_message_preserves_transcript_metadata_but_resets_identity_state():
    """Copy only durable transcript data, never bookmark or realtime identity."""
    content = json.dumps(
        [
            {
                "type": "tool_call_result",
                "content": "done",
                "documents": [{"id": "file-1", "meta": {"original_filename": "brief.pdf"}}],
                "meta": {"tool_name": "search", "citations": ["https://example.test"]},
            }
        ]
    )
    source = _message("assistant-1", "assistant", 2, reference_id="user-1", content=content)
    id_map = {"user-1": "new-user-1"}

    cloned = chat_models.clone_chat_message_for_new_chat(source, "new-chat", id_map)

    assert cloned.id != source.id
    assert cloned.chat_id == "new-chat"
    assert cloned.reference_id == "new-user-1"
    assert cloned.model_id == source.model_id
    assert cloned.role == source.role
    assert cloned.content == source.content
    assert cloned.generation == source.generation
    assert cloned.generation is not source.generation
    assert cloned.thinking == source.thinking
    assert cloned.retry_count == source.retry_count
    assert cloned.created_at == source.created_at
    assert cloned.bookmarked is False
    assert cloned.realtime_session_id is None
    assert cloned.realtime_turn_id is None
    assert id_map[source.id] == cloned.id


def test_branch_copies_only_the_selected_turn_and_resets_chat_level_state():
    """A branch gets a fresh chat shell while leaving the source untouched."""
    source_chat = _source_chat()
    messages = [
        _message("user-1", "user", 0),
        _message("assistant-1", "assistant", 1, reference_id="user-1"),
        _message("tool-1", "tool", 2, reference_id="assistant-1"),
        _message("user-2", "user", 3),
    ]
    db = _CopyDb(source_chat, messages, messages[1])
    source_snapshot = dict(source_chat.__dict__)

    result = chat_utils.branch_chat("user-1", "assistant-1", db)

    assert result == {"status": "success", "new_chat_id": "new-chat"}
    assert db.commits == 1
    assert db.new_chat.title == "Planning (Branch)"
    assert db.new_chat.project_id == "project-1"
    assert db.new_chat.share is None
    assert db.new_chat.share_id is None
    assert db.new_chat.archived is False
    assert db.new_chat.pinned_position is None
    assert db.new_chat.meta is None
    assert db.new_chat.response_version == 0
    assert db.new_chat.last_completed_generation_id is None
    assert [message.role for message in db.new_messages] == ["user", "assistant", "tool"]
    assert db.new_messages[1].reference_id == db.new_messages[0].id
    assert db.new_messages[2].reference_id == db.new_messages[1].id
    assert source_chat.__dict__ == source_snapshot
    assert [message.chat_id for message in messages] == ["source-chat"] * 4


def test_duplicate_copies_every_message_but_starts_with_fresh_chat_state():
    """Duplicate and Branch share metadata rules but not transcript extent."""
    source_chat = _source_chat()
    messages = [
        _message("user-1", "user", 0),
        _message("assistant-1", "assistant", 1, reference_id="user-1"),
        _message("user-2", "user", 2),
        _message("assistant-2", "assistant", 3, reference_id="user-2"),
    ]
    messages[1].generation = {}
    db = _CopyDb(source_chat, messages, messages[1])

    result = chat_models.duplicate_chat("user-1", "source-chat", db)

    assert result == {"status": "success"}
    assert db.commits == 1
    assert db.new_chat.title == "Planning (Copy)"
    assert db.new_chat.project_id == "project-1"
    assert db.new_chat.share is None
    assert db.new_chat.share_id is None
    assert db.new_chat.archived is False
    assert db.new_chat.pinned_position is None
    assert db.new_chat.meta is None
    assert db.new_chat.response_version == 0
    assert db.new_chat.last_completed_generation_id is None
    assert len(db.new_messages) == len(messages)
    assert [message.content for message in db.new_messages] == [message.content for message in messages]
    assert db.new_messages[1].generation == {}
    assert db.new_messages[1].generation is not messages[1].generation
    assert all(message.bookmarked is False for message in db.new_messages)
    assert db.new_messages[1].reference_id == db.new_messages[0].id
    assert db.new_messages[3].reference_id == db.new_messages[2].id
