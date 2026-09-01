from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.chat_imports import models as openwebui_import_module  # noqa: E402
from app.admin.chat_imports.models import (  # noqa: E402
    _build_message_branches,
    _linearise_messages,
    _parse_users_csv,
    import_openwebui_chats,
)
from app.chats.models import ChatMessages, Chats  # noqa: E402
from app.database import Base  # noqa: E402


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(
        bind=engine, tables=[Chats.__table__, ChatMessages.__table__]
    )
    return sessionmaker(bind=engine)()


def _message_text(message: ChatMessages) -> str:
    return json.loads(message.content)[0]["content"]


def _branched_history() -> dict:
    return {
        "u1": {
            "role": "user",
            "content": "Question",
            "timestamp": 1,
            "parentId": None,
            "childrenIds": ["a-old", "a-current"],
        },
        "a-old": {
            "role": "assistant",
            "content": "Old answer",
            "timestamp": 2,
            "parentId": "u1",
            "childrenIds": [],
        },
        "a-current": {
            "role": "assistant",
            "content": "Current answer",
            "timestamp": 3,
            "parentId": "u1",
            "childrenIds": [],
        },
    }


def test_linearise_messages_prefers_openwebui_current_id_branch():
    ordered = _linearise_messages(_branched_history(), "a-current")

    assert [message["content"] for message in ordered] == ["Question", "Current answer"]


def test_build_message_branches_supports_histories_beyond_recursion_depth():
    history = {}
    message_count = 1_200
    for index in range(message_count):
        message_id = f"m-{index}"
        next_id = f"m-{index + 1}" if index + 1 < message_count else None
        history[message_id] = {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": str(index),
            "parentId": f"m-{index - 1}" if index else None,
            "childrenIds": [next_id] if next_id else [],
            "timestamp": index,
        }

    branches, skipped = _build_message_branches(history, "m-1199")

    assert skipped == 0
    assert len(branches) == 1
    assert len(branches[0]["messages"]) == message_count


def test_import_openwebui_chats_imports_alternate_branches_as_chats():
    db = _session()
    entry = {
        "id": "owui-chat-1",
        "title": "Branched",
        "created_at": 1,
        "updated_at": 3,
        "chat": {
            "history": {
                "currentId": "a-current",
                "messages": _branched_history(),
            },
        },
    }

    result = import_openwebui_chats(db, "user-1", [entry])

    assert result == {
        "imported_chats": 2,
        "imported_messages": 4,
        "imported_branches": 1,
        "skipped_chats": 0,
        "skipped_branches": 0,
        "skipped_messages": 0,
    }

    chats = db.query(Chats).order_by(Chats.title.asc()).all()
    assert [chat.title for chat in chats] == ["Branched", "Branched (Branch 2)"]
    assert chats[0].meta["openwebui_branch"] == {
        "index": 1,
        "count": 2,
        "leaf_id": "a-current",
        "current": True,
    }
    assert chats[1].meta["openwebui_branch"] == {
        "index": 2,
        "count": 2,
        "leaf_id": "a-old",
        "current": False,
    }

    messages_by_title = {}
    for chat in chats:
        messages = (
            db.query(ChatMessages)
            .filter(ChatMessages.chat_id == chat.id)
            .order_by(ChatMessages.created_at.asc())
            .all()
        )
        messages_by_title[chat.title] = [_message_text(message) for message in messages]
        assert messages[1].reference_id == messages[0].id

    assert messages_by_title == {
        "Branched": ["Question", "Current answer"],
        "Branched (Branch 2)": ["Question", "Old answer"],
    }


def test_import_openwebui_chats_ignores_non_boolean_archived_flags():
    db = _session()
    entry = {
        "id": "owui-chat-2",
        "title": "Archive flag",
        "archived": "false",
        "created_at": 1,
        "chat": {
            "messages": [
                {
                    "role": "user",
                    "content": "Question",
                    "timestamp": 1,
                }
            ]
        },
    }

    result = import_openwebui_chats(db, "user-1", [entry])

    assert result["imported_chats"] == 1
    imported_chat = db.query(Chats).first()
    assert imported_chat.archived is False


def test_import_openwebui_chats_preserves_pin_on_only_the_primary_branch():
    db = _session()
    db.add(
        Chats(
            id="existing",
            user_id="user-1",
            title="Existing pin",
            pinned_position=4,
            meta={"status": "normal"},
            created_at=openwebui_import_module._unix_ts_to_utc(1),
            last_updated_at=openwebui_import_module._unix_ts_to_utc(1),
        )
    )
    db.commit()
    entry = {
        "id": "owui-pinned",
        "title": "Pinned branch",
        "pinned": True,
        "chat": {
            "history": {
                "currentId": "a-current",
                "messages": _branched_history(),
            }
        },
    }

    import_openwebui_chats(db, "user-1", [entry])

    imported = (
        db.query(Chats).filter(Chats.id != "existing").order_by(Chats.title.asc()).all()
    )
    assert [chat.pinned_position for chat in imported] == [5, None]


def test_import_openwebui_chats_supports_current_structured_output_and_content_parts():
    """Cover the content and output shapes exported by Open WebUI v0.11."""
    db = _session()
    entry = {
        "id": "owui-chat-current",
        "title": "Current Open WebUI",
        "created_at": 10,
        "updated_at": 11,
        "chat": {
            "history": {
                "currentId": "a1",
                "messages": {
                    "u1": {
                        "id": "u1",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Hello "},
                            {"type": "text", "text": "world"},
                        ],
                        "parentId": None,
                        "childrenIds": ["a1"],
                        "timestamp": 10,
                    },
                    "a1": {
                        "id": "a1",
                        "role": "assistant",
                        "content": "The answer is 42.",
                        "output": [
                            {
                                "type": "reasoning",
                                "status": "completed",
                                "duration": 1.25,
                                "summary": [
                                    {"type": "summary_text", "text": "Count carefully."}
                                ],
                            },
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "calculator",
                                "arguments": {"expression": "6*7"},
                            },
                            {
                                "type": "function_call_output",
                                "call_id": "call-1",
                                "output": [{"type": "output_text", "text": "42"}],
                            },
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "The answer is 42."}
                                ],
                            },
                        ],
                        "parentId": "u1",
                        "childrenIds": [],
                        "timestamp": 11,
                        "model": "qwen3:0.6b",
                    },
                },
            }
        },
    }

    result = import_openwebui_chats(db, "user-1", [entry])

    assert result["imported_chats"] == 1
    assert result["imported_messages"] == 2
    messages = db.query(ChatMessages).order_by(ChatMessages.created_at.asc()).all()
    assert _message_text(messages[0]) == "Hello world"
    assert messages[1].thinking == "Count carefully."
    assert json.loads(messages[1].content) == [
        {
            "type": "reasoning",
            "content": "Count carefully.",
            "meta": {"reasoning_time": 1.25},
        },
        {
            "type": "tool_call",
            "content": "",
            "meta": {
                "tool_name": "calculator",
                "arguments": '{"expression":"6*7"}',
                "tool_call_id": "call-1",
            },
        },
        {
            "type": "tool_call_result",
            "content": "42",
            "tool_name": "calculator",
            "meta": {"tool_call_id": "call-1"},
        },
        {"type": "content", "content": "The answer is 42."},
    ]


def test_import_openwebui_chats_renders_legacy_reasoning_alongside_content():
    """Legacy details reasoning must remain visible in structured transcripts."""
    db = _session()
    entry = {
        "title": "Legacy reasoning",
        "chat": {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        '<details type="reasoning"><summary>Thinking</summary>'
                        "Check the premise.</details>Final answer."
                    ),
                    "timestamp": 1,
                }
            ]
        },
    }

    import_openwebui_chats(db, "user-1", [entry])

    message = db.query(ChatMessages).one()
    assert message.thinking == "Check the premise."
    assert json.loads(message.content) == [
        {"type": "reasoning", "content": "Check the premise."},
        {"type": "content", "content": "Final answer."},
    ]


def test_import_openwebui_chats_preserves_order_for_equal_timestamps():
    db = _session()
    entry = {
        "title": "Same second",
        "created_at": 100,
        "chat": {
            "messages": [
                {"role": "user", "content": "first", "timestamp": 100},
                {"role": "assistant", "content": "second", "timestamp": 100},
                {"role": "user", "content": "third", "timestamp": 100},
            ]
        },
    }

    import_openwebui_chats(db, "user-1", [entry])

    messages = db.query(ChatMessages).order_by(ChatMessages.created_at.asc()).all()
    assert [_message_text(message) for message in messages] == [
        "first",
        "second",
        "third",
    ]
    assert messages[0].created_at < messages[1].created_at < messages[2].created_at


def test_import_openwebui_chats_never_persists_a_partial_failed_chat(monkeypatch):
    db = _session()
    real_builder = openwebui_import_module._build_content_blocks

    def failing_builder(role, text, *, message=None):
        if text == "explode":
            raise ValueError("synthetic conversion failure")
        return real_builder(role, text, message=message)

    monkeypatch.setattr(
        openwebui_import_module, "_build_content_blocks", failing_builder
    )
    entries = [
        {
            "title": "Must roll back",
            "chat": {
                "messages": [
                    {"role": "user", "content": "partial"},
                    {"role": "assistant", "content": "explode"},
                ]
            },
        },
        {
            "title": "Still imports",
            "chat": {"messages": [{"role": "user", "content": "complete"}]},
        },
    ]

    result = import_openwebui_chats(db, "user-1", entries)

    assert result["imported_chats"] == 1
    assert result["imported_messages"] == 1
    assert result["skipped_chats"] == 1
    assert [chat.title for chat in db.query(Chats).all()] == ["Still imports"]
    assert [_message_text(message) for message in db.query(ChatMessages).all()] == [
        "complete"
    ]


def test_import_openwebui_chats_skips_unsupported_roles_with_an_explicit_count():
    db = _session()
    entry = {
        "title": "Tool role",
        "chat": {
            "messages": [
                {"role": "user", "content": "question", "timestamp": 1},
                {"role": "tool", "content": "raw tool result", "timestamp": 2},
                {"role": "assistant", "content": "answer", "timestamp": 3},
            ]
        },
    }

    result = import_openwebui_chats(db, "user-1", [entry])

    assert result["imported_chats"] == 1
    assert result["imported_messages"] == 2
    assert result["skipped_messages"] == 1
    assert [
        message.role
        for message in db.query(ChatMessages).order_by(ChatMessages.created_at).all()
    ] == [
        "user",
        "assistant",
    ]


def test_parse_users_csv_accepts_utf8_bom_from_spreadsheet_exports():
    users = _parse_users_csv(
        '\ufeffid,name,email,role\n"owui-user","Example","Person@Example.com","user"\n'
    )

    assert users == {"owui-user": "person@example.com"}
