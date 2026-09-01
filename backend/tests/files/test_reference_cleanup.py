from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")
    fake_markitdown.MarkItDown = type("MarkItDown", (), {"__init__": lambda self, *args, **kwargs: None})
    sys.modules["markitdown"] = fake_markitdown


from app.chats.download import export_chat_full  # noqa: E402
from app.chats.models import ChatMessages, ChatReadState, Chats  # noqa: E402
from app.database import Base  # noqa: E402
from app.files.reference_cleanup import cleanup_file_references  # noqa: E402
from app.files import reference_cleanup  # noqa: E402
from app.groups.models import Group, GroupManager, export_groups  # noqa: E402
from app.notes.models import NoteHistory, Notes  # noqa: E402
from app.projects.models import Project  # noqa: E402
from app.tools.deep_research.models import DeepResearchRun  # noqa: E402
from app.tools.slide_presentation.models import SlidePresentations  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            ChatMessages.__table__,
            # Chat exports now look up the per-user read state. This focused
            # in-memory schema therefore needs the table even though this test
            # does not create a read-state row itself.
            ChatReadState.__table__,
            Chats.__table__,
            Group.__table__,
            # Group exports include delegated manager assignments, so the
            # focused schema must provide the queried table even when empty.
            GroupManager.__table__,
            Project.__table__,
            SlidePresentations.__table__,
            Notes.__table__,
            NoteHistory.__table__,
            DeepResearchRun.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_cleanup_file_references_scrubs_exports_and_related_file_links():
    db = _session()
    now = datetime.now(timezone.utc)
    deleted_file_id = "file-delete"
    kept_file_id = "file-keep"

    db.add(
        Chats(
            id="chat-1",
            user_id="user-1",
            title="Chat",
            meta={"status": "normal"},
            created_at=now,
            last_updated_at=now,
        )
    )
    db.add(
        ChatMessages(
            id="message-1",
            chat_id="chat-1",
            model_id="model-1",
            role="user",
            content=json.dumps(
                [
                    {
                        "type": "user",
                        "content": "files",
                        "documents": [
                            {
                                "id": deleted_file_id,
                                "file_id": deleted_file_id,
                                "file_name": "deleted.pdf",
                                "original_filename": "deleted.pdf",
                            },
                            {"id": kept_file_id, "file_id": kept_file_id, "file_name": "keep.pdf"},
                        ],
                        "meta": {"file_id": deleted_file_id, "file_name": "deleted.pdf"},
                    }
                ]
            ),
            created_at=now,
        )
    )
    db.add(
        Group(
            id="group-1",
            name="Group",
            settings={"context": {"group_context_file_ids": [deleted_file_id, kept_file_id]}},
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        Project(
            id="project-1",
            user_id="user-1",
            title="Project",
            documents=json.dumps([deleted_file_id, kept_file_id]),
            created_at=now,
            last_updated_at=now,
        )
    )
    db.add(
        SlidePresentations(
            id="presentation-1",
            user_id="user-1",
            title="Deck",
            slide_count=1,
            storage_provider="local",
            storage_prefix="presentations/presentation-1",
            file_id=deleted_file_id,
            created_at=now,
            last_updated_at=now,
        )
    )
    db.add(
        DeepResearchRun(
            id="research-1",
            user_id="user-1",
            chat_id="chat-1",
            query="Analyze the attached file",
            artifacts=[
                {
                    "stable_id": "chart-1",
                    "file_id": deleted_file_id,
                    "source_phase": "analysis",
                    "original_filename": "chart.png",
                    "relative_path": "artifacts/chart.png",
                    "validation_status": "validated",
                },
                "legacy-artifact-entry",
            ],
        )
    )
    db.add(
        Notes(
            id="note-1",
            user_id="user-1",
            content=f"Before {{{{note:file:user-1:{deleted_file_id}|deleted.pdf}}}} after",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        NoteHistory(
            id="history-1",
            note_id="note-1",
            user_id="user-1",
            content=f"Before {{{{note:file:user-1:{deleted_file_id}|deleted.pdf}}}} after",
            previous_content=f"Old {{{{note:file:user-1:{deleted_file_id}|deleted.pdf}}}}",
            version_number="1",
            created_at=now,
        )
    )
    db.commit()

    cleanup_file_references(db, "user-1", deleted_file_id)
    db.commit()

    chat_export = export_chat_full("user-1", "chat-1", db)
    group_export = export_groups(db)
    project = db.query(Project).filter(Project.id == "project-1").first()
    presentation = db.query(SlidePresentations).filter(SlidePresentations.id == "presentation-1").first()
    note = db.query(Notes).filter(Notes.id == "note-1").first()
    history = db.query(NoteHistory).filter(NoteHistory.id == "history-1").first()
    research_run = (
        db.query(DeepResearchRun).filter(DeepResearchRun.id == "research-1").first()
    )

    serialized_chat = json.dumps(chat_export)
    serialized_groups = json.dumps(group_export)
    assert deleted_file_id not in serialized_chat
    assert "deleted.pdf" not in serialized_chat
    assert kept_file_id in serialized_chat
    assert deleted_file_id not in serialized_groups
    assert kept_file_id in serialized_groups
    assert json.loads(project.documents) == [kept_file_id]
    assert presentation.file_id is None
    assert research_run.artifacts[0]["file_id"] is None
    assert research_run.artifacts[1] == "legacy-artifact-entry"
    assert deleted_file_id not in note.content
    assert "deleted.pdf" not in note.content
    assert deleted_file_id not in history.content
    assert deleted_file_id not in history.previous_content


def test_presentation_source_cleanup_removes_indexed_revision_and_local_tree(
    monkeypatch,
):
    presentation = type(
        "Presentation",
        (),
        {
            "id": "deck-1",
            "file_id": "pptx-1",
            "storage_provider": "s3",
            "storage_prefix": "user-1/presentations/deck-1/revisions/latest",
            "slide_count": 2,
        },
    )()
    derivative = type(
        "Derivative",
        (),
        {
            "id": "pptx-1",
            "storage_provider": "local",
            "storage_key": "user-1/pptx-1.pptx",
            "file_name": "deck.pptx",
        },
    )()

    class FakeQuery:
        def __init__(self, result):
            self.result = result

        def filter(self, *args):
            return self

        def all(self):
            return list(self.result)

        def first(self):
            return self.result[0] if self.result else None

    class FakeDB:
        def __init__(self):
            self.query_results = [[], [presentation], [derivative]]
            self.deleted = []

        def query(self, model):
            return FakeQuery(self.query_results.pop(0))

        def connection(self):
            return object()

        def delete(self, value):
            self.deleted.append(value)

    class FakeInspector:
        def has_table(self, *args, **kwargs):
            return True

    captured = []
    monkeypatch.setattr(reference_cleanup, "inspect", lambda connection: FakeInspector())
    for scrubber in (
        "_scrub_chat_message_content",
        "_scrub_group_context_settings",
        "_scrub_project_attachment_fields",
        "_scrub_deep_research_artifact_file_ids",
        "_scrub_note_file_references",
    ):
        monkeypatch.setattr(reference_cleanup, scrubber, lambda *args: 0)

    from app.files import utils as file_utils
    from app.tools.slide_presentation import storage as presentation_storage

    monkeypatch.setattr(
        presentation_storage,
        "delete_slide_presentation_artifacts",
        lambda **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        file_utils,
        "delete_storage_reference",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
    )

    db = FakeDB()
    assert reference_cleanup._scrub_slide_presentation_file_ids(
        db, "user-1", "deck-1"
    ) == 1
    assert [item["storage_prefix"] for item in captured] == [
        "user-1/presentations/deck-1/revisions/latest",
        "user-1/presentations/deck-1",
    ]
    assert all(item["storage_provider"] == "s3" for item in captured)
    assert db.deleted == [derivative, presentation]
