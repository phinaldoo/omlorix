import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.chats import compliance as chat_compliance
from app.chats import download as chat_download
from app.chats.models import ChatMessages, Chats
from app.users.models import User
from app.utils.email import build_email_reference_token


class _GuardedQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def execution_options(self, **kwargs):
        return self

    def yield_per(self, batch_size):
        return self

    def all(self):
        raise AssertionError("streamed export should not materialize query rows with .all()")

    def __iter__(self):
        return iter(self._rows)


class _FakeDb:
    def __init__(self, chat_rows, message_batches):
        self._chat_rows = list(chat_rows)
        self._message_batches = list(message_batches)

    def query(self, *models):
        if len(models) == 2 and models[0] is Chats:
            return _GuardedQuery(self._chat_rows)
        if len(models) == 1 and models[0] is Chats:
            return _GuardedQuery(self._chat_rows)
        if len(models) == 1 and models[0] is ChatMessages:
            if not self._message_batches:
                raise AssertionError("missing message batch for streamed export")
            return _GuardedQuery(self._message_batches.pop(0))
        raise AssertionError(f"unexpected query models: {models!r}")


@pytest.fixture(autouse=True)
def disable_compliance_watermark_lookup(monkeypatch):
    """Avoid unrelated user/group queries in the streaming fixture."""

    monkeypatch.setattr(chat_compliance, "get_compliance_watermark", lambda *args, **kwargs: "")


def test_iter_all_chats_export_json_streams_rows_without_materializing(monkeypatch):
    monkeypatch.setattr(
        chat_download,
        "_export_deep_research_runs_for_chat",
        lambda *args, **kwargs: [],
    )

    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Visible chat",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    skipped_chat = Chats(
        id="chat-2",
        user_id="user-2",
        title="Skipped temp chat",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={"status": "temp"},
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )
    message = ChatMessages(
        id="msg-1",
        chat_id="chat-1",
        model_id="model-1",
        role="assistant",
        content=json.dumps([{"type": "assistant", "content": "Hello"}]),
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    counters = {}
    payload = json.loads(
        "".join(
            chat_download.iter_all_chats_export_json(
                _FakeDb(
                    chat_rows=[(chat, "visible@example.com"), (skipped_chat, "skipped@example.com")],
                    message_batches=[[message]],
                ),
                counters=counters,
            )
        )
    )

    assert payload["export_type"] == "chats"
    assert payload["data"]["count"] == 1
    assert len(payload["data"]["chats"]) == 1
    assert payload["data"]["chats"][0]["chat"]["id"] == "chat-1"
    assert payload["data"]["chats"][0]["messages"][0]["id"] == "msg-1"
    assert payload["data"]["user_reference_map"] == {
        "user-1": build_email_reference_token("visible@example.com"),
    }
    assert counters == {"count": 1, "user_reference_count": 1}


def test_iter_all_chats_export_json_applies_each_user_compliance_watermark(monkeypatch):
    monkeypatch.setattr(chat_download, "_export_deep_research_runs_for_chat", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        chat_compliance,
        "get_compliance_watermark",
        lambda user_id, db: f"Admin marker for {user_id}",
    )

    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Admin export",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    message = ChatMessages(
        id="msg-1",
        chat_id="chat-1",
        model_id="model-1",
        role="assistant",
        content=json.dumps([{"type": "content", "content": "Admin answer"}]),
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    payload = json.loads(
        "".join(
            chat_download.iter_all_chats_export_json(
                _FakeDb(
                    chat_rows=[(chat, "admin@example.com")],
                    message_batches=[[message]],
                )
            )
        )
    )
    exported_blocks = json.loads(payload["data"]["chats"][0]["messages"][0]["content"])

    assert exported_blocks[-1] == {"type": "content", "content": "Admin marker for user-1"}


def test_get_all_chats_export_audit_details_counts_before_streaming():
    visible_chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Visible chat",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    skipped_chat = Chats(
        id="chat-2",
        user_id="user-2",
        title="Skipped temp chat",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={"status": "temp"},
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )

    audit_details = chat_download.get_all_chats_export_audit_details(
        _FakeDb(
            chat_rows=[(visible_chat, "visible@example.com"), (skipped_chat, "skipped@example.com")],
            message_batches=[],
        )
    )

    assert audit_details == {
        "count": 1,
        "user_reference_count": 1,
        "include_deleted_or_temp": False,
    }


def test_iter_user_chats_export_json_streams_selected_user_rows(monkeypatch):
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Selected user chat",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        chat_download,
        "_build_chat_export_payload",
        lambda row, db, include_attention=True: {
            "chat": {"id": row.id, "user_id": row.user_id},
            "messages": [],
            "deep_research_runs": [],
        },
    )

    counters = {}
    payload = json.loads(
        "".join(
            chat_download.iter_user_chats_export_json(
                "user-1",
                _FakeDb(chat_rows=[chat], message_batches=[]),
                counters=counters,
            )
        )
    )

    assert payload["data"]["count"] == 1
    assert payload["data"]["chats"][0]["chat"]["id"] == "chat-1"
    assert "user_reference_map" not in payload["data"]
    assert counters == {"count": 1}
