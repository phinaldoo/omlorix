import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))



from app.chats import io as chat_io
from app.chats import compliance as chat_compliance
from app.chats import download as chat_download
from app.chats.models import ChatMessages, ChatReadState, Chats
from app.database import Base
from app.tools.deep_research.models import DeepResearchRun


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Chats.__table__,
            ChatMessages.__table__,
            ChatReadState.__table__,
            DeepResearchRun.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _foreign_key_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    Base.metadata.create_all(
        bind=engine,
        tables=[Chats.__table__, ChatMessages.__table__],
    )
    db = sessionmaker(bind=engine)()
    db.execute(text("INSERT INTO users (id) VALUES ('target-user')"))
    db.commit()
    return db


def test_import_flushes_chat_parent_before_message_children():
    db = _foreign_key_session()
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = {
        "chat": {
            "id": "source-chat",
            "title": "Imported chat",
            "created_at": created_at.isoformat(),
            "last_updated_at": created_at.isoformat(),
        },
        "messages": [
            {
                "id": "source-message",
                "model_id": "model-1",
                "role": "user",
                "content": "Restored message",
                "created_at": created_at.isoformat(),
            }
        ],
    }
    try:
        chat_io._import_single_chat("target-user", entry, db)
        db.rollback()
        assert db.query(Chats).count() == 0
        assert db.query(ChatMessages).count() == 0

        result = chat_io._import_single_chat("target-user", entry, db)
        db.commit()

        imported_chat = db.query(Chats).filter(Chats.id == result["chat_id"]).one()
        imported_message = (
            db.query(ChatMessages)
            .filter(ChatMessages.chat_id == imported_chat.id)
            .one()
        )
        assert imported_chat.user_id == "target-user"
        assert imported_chat.title == "Imported chat"
        assert imported_message.content == "Restored message"
        assert result["message_count"] == 1
    finally:
        db.close()


def test_import_strips_openai_continuation_capabilities_but_preserves_display_metadata():
    """Imported messages must not retain access to provider-side response state."""
    serialized = chat_io._build_imported_message_content(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "content",
                    "content": "Answer",
                    "meta": {
                        "response_id": "resp_foreign",
                        "continuation_fingerprint": "attacker-computable-hash",
                        "continuation_signature": "copied-signature",
                        "model": "gpt-5.6-sol",
                        "input_tokens": 42,
                    },
                }
            ],
        }
    )

    metadata = json.loads(serialized)[0]["meta"]
    assert "response_id" not in metadata
    assert "continuation_fingerprint" not in metadata
    assert "continuation_signature" not in metadata
    assert metadata["model"] == "gpt-5.6-sol"
    assert metadata["input_tokens"] == 42

    legacy_serialized = chat_io._build_imported_message_content(
        {
            "role": "assistant",
            "content": "Legacy answer",
            "meta": {
                "response_id": "resp_foreign",
                "continuation_signature": "copied-signature",
                "model": "gpt-5.6-sol",
            },
        }
    )
    legacy_metadata = json.loads(legacy_serialized)[0]["meta"]
    assert "response_id" not in legacy_metadata
    assert "continuation_signature" not in legacy_metadata
    assert legacy_metadata["model"] == "gpt-5.6-sol"


def test_chat_export_import_roundtrip_preserves_reference_ids_and_bookmarks(
    monkeypatch,
    tmp_path,
):
    db = _session()
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # The embedded transcript deliberately exceeds the legacy 200 kB generic
    # metadata limit. Complete token events must remain importable with the chat.
    subagent_token = "x" * 210000
    chat = Chats(
        id="source-chat",
        user_id="user-1",
        title="Imported thread",
        project_id=None,
        share=None,
        share_id=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=created_at,
        last_updated_at=created_at + timedelta(minutes=3),
        response_version=1,
    )
    messages = [
        ChatMessages(
            id="source-user-message",
            chat_id="source-chat",
            model_id="model-1",
            role="user",
            content="Question",
            reference_id=None,
            generation={"generation_number": 1},
            retry_count=0,
            bookmarked=True,
            created_at=created_at + timedelta(minutes=1),
        ),
        ChatMessages(
            id="source-assistant-message-1",
            chat_id="source-chat",
            model_id="model-1",
            role="assistant",
            content="Answer v1",
            reference_id="source-user-message",
            generation={"generation_number": 1},
            retry_count=0,
            bookmarked=False,
            created_at=created_at + timedelta(minutes=2),
        ),
        ChatMessages(
            id="source-assistant-message-2",
            chat_id="source-chat",
            model_id="model-1",
            role="assistant",
            content=json.dumps(
                [
                    {
                        "type": "tool_call_result",
                        "content": "tool output is stored normally but never replayed",
                        "meta": {
                            "deep_research": True,
                            "run_id": "research-run-1",
                            "deep_research_activity": {
                                "schema_version": 1,
                                "events": [
                                    {
                                        "event": "tool_call",
                                        "tool": "web_search",
                                        "arguments": {"query": "primary source"},
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "type": "widget",
                        "content": (
                            "<section class=\"deep-research-widget\" "
                            "data-widget-id=\"research-run-1\" "
                            "data-run-id=\"research-run-1\" "
                            "data-session-id=\"research-run-1\">"
                            "Ordinary research-run-1 prose remains unchanged. "
                            '<a href="/api/v1/deep-research/runs/'
                            'research-run-1/files/final.md">Report</a></section>'
                        ),
                        "meta": {
                            "widget_type": "deep_research",
                            "tool_result": {"run_id": "research-run-1"},
                        },
                    },
                    {
                        "type": "tool_call_result",
                        "content": "Subagent completed",
                        "meta": {
                            "subagent": {
                                "id": "subagent-run-1",
                                "status": "completed",
                                "result": "Subagent completed",
                                "events": [
                                    {
                                        "type": "message_delta",
                                        "raw": {"d": subagent_token},
                                    }
                                ],
                            }
                        },
                    },
                ]
            ),
            reference_id="source-user-message",
            generation={"generation_number": 1},
            retry_count=1,
            bookmarked=True,
            created_at=created_at + timedelta(minutes=3),
        ),
    ]
    db.add(chat)
    db.add(
        DeepResearchRun(
            id="research-run-1",
            user_id="user-1",
            chat_id="source-chat",
            query="Primary-source research",
            execution_mode="custom",
            output_format="markdown",
            status="completed",
            phase="completed",
            model_id="model-1",
            model_name="Model 1",
            config_snapshot={},
            usage={},
            quality_gate={},
            result_meta={},
            evidence=[],
            artifacts=[],
        )
    )
    db.add_all(messages)
    db.commit()

    monkeypatch.setattr(
        "app.tools.deep_research.storage.get_deep_research_workspace_dir",
        lambda _user_id, run_id: tmp_path / run_id,
    )
    monkeypatch.setattr(
        "app.tools.deep_research.storage.upload_deep_research_artifacts",
        lambda **_kwargs: {},
    )

    monkeypatch.setattr(chat_compliance, "get_compliance_watermark", lambda *args, **kwargs: "")
    payload = chat_io.export_user_chats_payload("user-1", db)
    exported_messages = payload["data"]["chats"][0]["messages"]
    assert payload["data"]["chats"][0]["chat"]["has_unread_response"] is True

    assert [message["bookmarked"] for message in exported_messages] == [True, False, True]
    assert [message["reference_id"] for message in exported_messages] == [
        None,
        "source-user-message",
        "source-user-message",
    ]

    result = chat_io.import_user_chats_payload("user-2", payload, db)

    assert result["created_count"] == 1
    assert result["created_message_count"] == 3

    imported_chat_id = result["created"][0]["chat_id"]
    imported_chat = db.query(Chats).filter(Chats.id == imported_chat_id).one()
    assert imported_chat.response_version == 1
    imported_messages = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == imported_chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )

    imported_user_message = imported_messages[0]
    imported_assistant_messages = imported_messages[1:]

    assert imported_user_message.id != "source-user-message"
    assert [message.id for message in imported_assistant_messages] != [
        "source-assistant-message-1",
        "source-assistant-message-2",
    ]
    assert {message.reference_id for message in imported_assistant_messages} == {imported_user_message.id}
    assert [message.bookmarked for message in imported_messages] == [True, False, True]
    imported_activity = json.loads(imported_assistant_messages[1].content)[0]["meta"][
        "deep_research_activity"
    ]
    assert imported_activity["events"][0]["arguments"] == {
        "query": "primary source"
    }
    imported_run = (
        db.query(DeepResearchRun)
        .filter(
            DeepResearchRun.user_id == "user-2",
            DeepResearchRun.chat_id == imported_chat_id,
        )
        .one()
    )
    imported_blocks = json.loads(imported_assistant_messages[1].content)
    assert imported_run.id != "research-run-1"
    assert imported_blocks[0]["meta"]["run_id"] == imported_run.id
    assert imported_blocks[1]["meta"]["tool_result"]["run_id"] == imported_run.id
    imported_widget_html = imported_blocks[1]["content"]
    assert f'data-widget-id="{imported_run.id}"' in imported_widget_html
    assert f'data-run-id="{imported_run.id}"' in imported_widget_html
    assert f'data-session-id="{imported_run.id}"' in imported_widget_html
    assert f"/deep-research/runs/{imported_run.id}/files/final.md" in imported_widget_html
    assert "Ordinary research-run-1 prose remains unchanged." in imported_widget_html
    imported_subagent = imported_blocks[2]["meta"]["subagent"]
    assert imported_subagent["id"] == "subagent-run-1"
    assert imported_subagent["events"][0]["raw"]["d"] == subagent_token


def test_deep_research_export_shares_binary_budget_across_runs(monkeypatch):
    """Artifact bytes from separate runs consume one chat-level export budget."""

    artifact_size = 1024 * 1024
    monkeypatch.setattr(
        chat_download,
        "CHAT_IMPORT_MAX_DEEP_RESEARCH_BYTES_PER_CHAT",
        8 * 1024 * 1024,
    )
    runs = []
    for index in range(7):
        runs.append(
            types.SimpleNamespace(
                id=f"run-{index}",
                user_id="user-1",
                generation_id=None,
                query="Research",
                execution_mode="custom",
                output_format="markdown",
                status="completed",
                phase="completed",
                provider_id=None,
                model_id="model-1",
                model_name="Model",
                prompt_version="v2",
                revision_round=0,
                max_revision_rounds=2,
                cancel_requested=False,
                started_at=None,
                completed_at=None,
                created_at=None,
                updated_at=None,
                final_report_path=None,
                final_html_path=None,
                manifest_path=None,
                error_code=None,
                error_message_key=None,
                config_snapshot={},
                usage={},
                quality_gate={},
                result_meta={},
                evidence=[],
                artifacts=[
                    {
                        "stable_id": f"artifact-{index}",
                        "source_phase": "deep-research",
                        "original_filename": f"chart-{index}.png",
                        "relative_path": f"artifacts/chart-{index}.png",
                        "media_type": "image/png",
                        "validation_status": "validated",
                    }
                ],
            )
        )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return runs

    class FakeDB:
        def query(self, _model):
            return FakeQuery()

    class FakeArtifactPath:
        name = "chart.png"

        def stat(self):
            return types.SimpleNamespace(st_size=artifact_size)

        def read_bytes(self):
            return b"x" * artifact_size

    def fake_materialize(_user_id, _run_id, relative_path, **_kwargs):
        if str(relative_path).startswith("artifacts/"):
            return FakeArtifactPath()
        raise FileNotFoundError(relative_path)

    monkeypatch.setattr(
        "app.tools.deep_research.storage.materialize_deep_research_artifact",
        fake_materialize,
    )

    exported = chat_download._export_deep_research_runs_for_chat(
        "user-1",
        "chat-1",
        FakeDB(),
    )

    # Five 1 MB files fit after base64 expansion; the sixth would exceed the
    # exact serialized 8 MB import ceiling.
    assert sum(len(run["artifact_contents"]) for run in exported) == 5
    assert all(run["artifact_contents"] == {} for run in exported[5:])
