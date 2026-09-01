import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")
    fake_markitdown.MarkItDown = type("MarkItDown", (), {"__init__": lambda self, *args, **kwargs: None})
    sys.modules["markitdown"] = fake_markitdown



from app.chats import models, worker
from app.files import utils as file_utils


@pytest.fixture(autouse=True)
def _stub_deep_research_cleanup(monkeypatch):
    """Keep legacy query-sequence fixtures focused on transcript cleanup."""

    monkeypatch.setattr(
        models,
        "_delete_deep_research_runs_for_chats",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        models,
        "_cleanup_deep_research_artifacts_after_commit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        worker,
        "_delete_deep_research_runs_for_chats",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        worker,
        "_cleanup_deep_research_artifacts_after_commit",
        lambda *_args, **_kwargs: None,
    )


def _message(message_id: str, file_id: str, *, chat_id: str = "chat-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        chat_id=chat_id,
        content=json.dumps(
            [
                {
                    "type": "user",
                    "content": "",
                    "documents": [{"id": file_id, "file_id": file_id}],
                }
            ]
        ),
    )


class _ExpiringMessage:
    def __init__(self, message_id: str, file_id: str, *, chat_id: str = "chat-1"):
        self.id = message_id
        self.chat_id = chat_id
        self._content = _message(message_id, file_id, chat_id=chat_id).content
        self.expired = False

    @property
    def content(self):
        if self.expired:
            raise RuntimeError("message content was accessed after commit")
        return self._content

    def expire(self):
        self.expired = True


class _Query:
    def __init__(self, *, rows=None, first_result=None):
        self.rows = list(rows or [])
        self.first_result = first_result
        self.deleted = False

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.first_result

    def delete(self, synchronize_session=False):
        self.deleted = True
        return len(self.rows)


class _SequencedDb:
    def __init__(self, queries, *, expire_on_commit=None):
        self.queries = list(queries)
        self.expire_on_commit = list(expire_on_commit or [])
        self.deleted_objects = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        expected_model, query = self.queries.pop(0)
        assert model is expected_model
        return query

    def delete(self, obj):
        self.deleted_objects.append(obj)

    def commit(self):
        self.commits += 1
        for item in self.expire_on_commit:
            item.expire()

    def rollback(self):
        self.rollbacks += 1


def test_cleanup_orphaned_meeting_transcript_files_deletes_unreferenced_generated_file(monkeypatch):
    transcript_file = SimpleNamespace(id="file-1", user_id="user-1", meta={"meeting_transcript": True})
    deleted_messages = [_message("msg-1", "file-1")]
    deleted_file_ids = []
    db = _SequencedDb(
        [
            (models.Files, _Query(rows=[transcript_file])),
            (models.ChatMessages, _Query(rows=[])),
            (models.Files, _Query(rows=[transcript_file])),
        ]
    )

    monkeypatch.setattr(
        file_utils,
        "_delete_file_record",
        lambda _db, _user_id, file_row: deleted_file_ids.append(file_row.id),
    )

    models._cleanup_orphaned_meeting_transcript_files(db, "user-1", deleted_messages)

    assert deleted_file_ids == ["file-1"]


def test_cleanup_orphaned_meeting_transcript_files_keeps_still_referenced_file(monkeypatch):
    transcript_file = SimpleNamespace(id="file-1", user_id="user-1", meta={"meeting_transcript": True})
    deleted_messages = [_message("msg-1", "file-1")]
    deleted_file_ids = []
    db = _SequencedDb(
        [
            (models.Files, _Query(rows=[transcript_file])),
            (models.ChatMessages, _Query(rows=[_message("msg-2", "file-1")])),
        ]
    )

    monkeypatch.setattr(
        file_utils,
        "_delete_file_record",
        lambda _db, _user_id, file_row: deleted_file_ids.append(file_row.id),
    )

    models._cleanup_orphaned_meeting_transcript_files(db, "user-1", deleted_messages)

    assert deleted_file_ids == []


def test_delete_chat_passes_deleted_message_content_snapshots_into_transcript_cleanup(monkeypatch):
    chat = SimpleNamespace(id="chat-1", user_id="user-1", meta={})
    deleted_messages = [_ExpiringMessage("msg-1", "file-1")]
    chat_lookup = _Query(first_result=chat)
    chat_messages_lookup = _Query(rows=deleted_messages)
    delete_messages_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, chat_lookup),
            (models.ChatMessages, chat_messages_lookup),
            (models.ChatMessages, delete_messages_query),
        ],
        expire_on_commit=deleted_messages,
    )
    cleanup_calls = []

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(
        models,
        "_cleanup_orphaned_meeting_transcript_files",
        lambda _db, user_id, messages: cleanup_calls.append((user_id, list(messages))),
    )

    assert models.delete_chat("user-1", "group-1", "chat-1", db) is True

    assert db.deleted_objects == [chat]
    assert db.commits == 1
    assert delete_messages_query.deleted is True
    assert cleanup_calls == [("user-1", [_message("msg-1", "file-1").content])]


def test_delete_chat_removes_deep_research_rows_and_cleans_storage_after_commit(
    monkeypatch,
):
    """Research blobs are deleted only after their owning DB rows commit."""

    chat = SimpleNamespace(id="chat-1", user_id="user-1", meta={})
    delete_messages_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, _Query(first_result=chat)),
            (models.ChatMessages, _Query(rows=[])),
            (models.ChatMessages, delete_messages_query),
        ]
    )
    descriptor = {
        "user_id": "user-1",
        "run_id": "run-1",
        "storage_provider": "local",
        "relative_paths": ["final-report.md"],
    }
    events = []

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(
        models,
        "_delete_deep_research_runs_for_chats",
        lambda _db, user_id, chat_ids: (
            events.append(("rows", user_id, chat_ids, _db.commits)) or [descriptor]
        ),
    )
    monkeypatch.setattr(
        models,
        "_cleanup_deep_research_artifacts_after_commit",
        lambda descriptors: events.append(("storage", descriptors, db.commits)),
    )
    monkeypatch.setattr(
        models,
        "_cleanup_orphaned_meeting_transcript_files_after_commit",
        lambda *_args: None,
    )

    assert models.delete_chat("user-1", "group-1", "chat-1", db) is True
    assert events == [
        ("rows", "user-1", ["chat-1"], 0),
        ("storage", [descriptor], 1),
    ]


def test_delete_chat_does_not_fail_when_post_commit_transcript_cleanup_fails(monkeypatch):
    chat = SimpleNamespace(id="chat-1", user_id="user-1", meta={})
    deleted_messages = [_message("msg-1", "file-1")]
    delete_messages_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, _Query(first_result=chat)),
            (models.ChatMessages, _Query(rows=deleted_messages)),
            (models.ChatMessages, delete_messages_query),
        ]
    )

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(models, "_cleanup_orphaned_meeting_transcript_files", fail_cleanup)

    assert models.delete_chat("user-1", "group-1", "chat-1", db) is True
    assert db.commits == 1
    assert delete_messages_query.deleted is True


def test_delete_chat_cancels_active_generation_before_hard_delete(monkeypatch):
    chat = SimpleNamespace(id="chat-1", user_id="user-1", meta={})
    deleted_messages = [_message("msg-1", "file-1")]
    chat_lookup = _Query(first_result=chat)
    chat_messages_lookup = _Query(rows=deleted_messages)
    delete_messages_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, chat_lookup),
            (models.ChatMessages, chat_messages_lookup),
            (models.ChatMessages, delete_messages_query),
        ]
    )
    cancelled_chat_ids = []

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(
        models,
        "_cancel_active_generation_for_chat",
        lambda chat_id: cancelled_chat_ids.append(chat_id) or True,
    )
    monkeypatch.setattr(models, "_cleanup_orphaned_meeting_transcript_files", lambda *_args, **_kwargs: None)

    assert models.delete_chat("user-1", "group-1", "chat-1", db) is True

    assert cancelled_chat_ids == ["chat-1"]
    assert db.deleted_objects == [chat]
    assert db.commits == 1
    assert delete_messages_query.deleted is True


def test_delete_all_chats_rejects_when_generation_is_still_stopping(monkeypatch):
    chats = [SimpleNamespace(id="chat-1", user_id="user-1")]
    db = _SequencedDb(
        [
            (models.Chats, _Query(rows=chats)),
        ]
    )

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(models, "_cancel_active_generation_for_chat", lambda _chat_id: False)

    with pytest.raises(models.HTTPException) as exc_info:
        models.delete_all_chats("user-1", "group-1", db)

    assert exc_info.value.status_code == 409
    assert db.commits == 0


def test_delete_all_chats_passes_deleted_message_content_snapshots_into_transcript_cleanup(monkeypatch):
    chats = [SimpleNamespace(id="chat-1", user_id="user-1")]
    deleted_messages = [_ExpiringMessage("msg-1", "file-1", chat_id="chat-1")]
    delete_messages_query = _Query()
    delete_chats_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, _Query(rows=chats)),
            (models.ChatMessages, _Query(rows=deleted_messages)),
            (models.ChatMessages, delete_messages_query),
            (models.Chats, delete_chats_query),
        ],
        expire_on_commit=deleted_messages,
    )
    cleanup_calls = []

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(
        models,
        "_cleanup_orphaned_meeting_transcript_files",
        lambda _db, user_id, messages: cleanup_calls.append((user_id, list(messages))),
    )

    assert models.delete_all_chats("user-1", "group-1", db) is True

    assert db.commits == 1
    assert delete_messages_query.deleted is True
    assert delete_chats_query.deleted is True
    assert cleanup_calls == [("user-1", [_message("msg-1", "file-1", chat_id="chat-1").content])]


def test_delete_all_chats_does_not_roll_back_committed_delete_when_cleanup_fails(monkeypatch):
    chats = [SimpleNamespace(id="chat-1", user_id="user-1")]
    deleted_messages = [_message("msg-1", "file-1", chat_id="chat-1")]
    delete_messages_query = _Query()
    delete_chats_query = _Query()
    db = _SequencedDb(
        [
            (models.Chats, _Query(rows=chats)),
            (models.ChatMessages, _Query(rows=deleted_messages)),
            (models.ChatMessages, delete_messages_query),
            (models.Chats, delete_chats_query),
        ]
    )

    def fake_group_setting(_group_id, _section, key, _db):
        return False if key == "shadow_chat_deletion" else True

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(models, "get_group_setting_value", fake_group_setting)
    monkeypatch.setattr(models, "_cleanup_orphaned_meeting_transcript_files", fail_cleanup)

    assert models.delete_all_chats("user-1", "group-1", db) is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert delete_messages_query.deleted is True
    assert delete_chats_query.deleted is True


def test_auto_delete_bulk_cleanup_groups_deleted_message_content_snapshots_by_owner(monkeypatch):
    chats = [
        SimpleNamespace(id="chat-1", user_id="user-1"),
        SimpleNamespace(id="chat-2", user_id="user-2"),
    ]
    deleted_messages = [
        _ExpiringMessage("msg-1", "file-1", chat_id="chat-1"),
        _ExpiringMessage("msg-2", "file-2", chat_id="chat-2"),
    ]
    delete_messages_query = _Query()
    delete_chats_query = _Query()
    db = _SequencedDb(
        [
            (worker.Chats, _Query(rows=chats)),
            (worker.ChatMessages, _Query(rows=deleted_messages)),
            (worker.ChatMessages, delete_messages_query),
            (worker.Chats, delete_chats_query),
        ],
        expire_on_commit=deleted_messages,
    )
    cleanup_calls = []

    monkeypatch.setattr(
        worker,
        "_cleanup_orphaned_meeting_transcript_files_after_commit",
        lambda _db, user_id, messages: cleanup_calls.append((user_id, list(messages))),
    )

    assert worker._delete_chat_ids(db, ["chat-1", "chat-2"]) == 2

    assert db.commits == 1
    assert delete_messages_query.deleted is True
    assert delete_chats_query.deleted is True
    assert cleanup_calls == [
        ("user-1", [_message("msg-1", "file-1", chat_id="chat-1").content]),
        ("user-2", [_message("msg-2", "file-2", chat_id="chat-2").content]),
    ]


def test_auto_delete_bulk_cleanup_skips_chat_with_generation_still_stopping(monkeypatch):
    chats = [
        SimpleNamespace(id="chat-1", user_id="user-1"),
        SimpleNamespace(id="chat-2", user_id="user-2"),
    ]
    deleted_messages = [
        _message("msg-1", "file-1", chat_id="chat-1"),
    ]
    delete_messages_query = _Query()
    delete_chats_query = _Query()
    db = _SequencedDb(
        [
            (worker.Chats, _Query(rows=chats)),
            (worker.ChatMessages, _Query(rows=deleted_messages)),
            (worker.ChatMessages, delete_messages_query),
            (worker.Chats, delete_chats_query),
        ]
    )
    cleanup_calls = []

    monkeypatch.setattr(worker, "_cancel_active_generation_for_chat", lambda chat_id: chat_id == "chat-1")
    monkeypatch.setattr(
        worker,
        "_cleanup_orphaned_meeting_transcript_files_after_commit",
        lambda _db, user_id, messages: cleanup_calls.append((user_id, list(messages))),
    )

    assert worker._delete_chat_ids(db, ["chat-1", "chat-2"]) == 1

    assert db.commits == 1
    assert delete_messages_query.deleted is True
    assert delete_chats_query.deleted is True
    assert cleanup_calls == [("user-1", [_message("msg-1", "file-1", chat_id="chat-1").content])]


def test_create_chat_message_rejects_missing_chat():
    db = _SequencedDb(
        [
            (models.Chats, _Query(first_result=None)),
        ]
    )

    with pytest.raises(models.HTTPException) as exc_info:
        models.create_chat_message(
            db,
            "missing-chat",
            "model-1",
            "assistant",
            content=[{"type": "content", "content": "hi"}],
        )

    assert exc_info.value.status_code == 404
    assert db.commits == 0
