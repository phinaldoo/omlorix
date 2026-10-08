from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.files import history
from app.files import utils as files
from app.files.models import (
    CanvasAssetGrant,
    CanvasHistory,
    FileMember,
    FileQuotaReservation,
    Files,
)
from app.tools.canvas_markdown import utils as canvas


@pytest.fixture
def document(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Files.__table__,
            CanvasHistory.__table__,
            FileMember.__table__,
            FileQuotaReservation.__table__,
            CanvasAssetGrant.__table__,
        ],
    )
    monkeypatch.setattr(files, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(files, "MATERIALIZED_TEMP_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        canvas, "serialized_user_file_quota_admission", lambda *_: nullcontext()
    )
    monkeypatch.setattr(canvas, "ensure_user_file_upload_size_limit", lambda *_: None)
    monkeypatch.setattr(
        canvas, "resolve_user_file_upload_limits", lambda *_: (-1, 1000)
    )

    def write(**kwargs):
        key = f"{kwargs['user_id']}/{kwargs['file_name']}"
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(kwargs["file_bytes"])
        return "local", key, {}

    monkeypatch.setattr(canvas, "overwrite_existing_file_bytes", write)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        db.add(
            Files(
                id="document",
                user_id="owner",
                file_name="plan.md",
                file_type="text/markdown",
                file_category="document",
                file_size=7,
                inline_content="# First",
                storage_provider="inline",
                storage_key="owner/plan.md",
                meta={"canvas": True, "canvas_type": "markdown", "canvas_revision": 1},
                created_at=now,
                last_updated_at=now,
            )
        )
        db.add_all(
            [
                FileMember(
                    file_id="document",
                    user_id=user,
                    role=role,
                    granted_at=now - timedelta(days=1),
                )
                for user, role in [("editor", "editor"), ("viewer", "viewer")]
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_canvas_discovery_searches_titles_and_respects_access(document, monkeypatch):
    from app.tools import helper

    monkeypatch.setattr(
        helper, "_admit_tool_invocation_or_payload", lambda *a, **kw: None
    )
    record = document.get(Files, "document")
    record.meta = {**record.meta, "original_filename": "Release checklist.md"}
    document.commit()

    def discover(user_id, **options):
        stream = helper.resolve_tool_call(
            db=document,
            tool_name="canvas",
            tool_arguments={"type": "list", **options},
            user_id=user_id,
            group_id=None,
            project_id=None,
        )
        with pytest.raises(StopIteration) as done:
            next(stream)
        return done.value.value["result"]

    result = discover("viewer", query="checklist", limit=1)
    assert result["items"] == [
        {
            "file_id": "document",
            "filename": "Release checklist.md",
            "canvas_revision": 1,
        }
    ]
    assert result["has_more"] is False
    assert discover("viewer", query="checklist", offset=1)["items"] == []
    assert discover("stranger", query="checklist")["items"] == []
    assert discover("viewer", query="missing")["items"] == []


def test_save_restore_conflict_and_immutable_history(document):
    db = document
    canvas.save_canvas_markdown(
        db,
        user_id="owner",
        file_id="document",
        content="# Second",
        expected_revision=1,
        edited_by="editor",
        edit_source="user",
    )
    version = db.query(CanvasHistory).one()
    assert history.materialize_canvas_history(version).read_text() == "# First"
    restored = history.restore_canvas_history(db, "owner", "document", version.id, 2)
    assert restored["canvas_revision"] == 3
    assert (
        files.materialize_file_record(db.get(Files, "document"), "owner").read_text()
        == "# First"
    )
    versions = db.query(CanvasHistory).order_by(CanvasHistory.revision).all()
    assert [history.materialize_canvas_history(v).read_text() for v in versions] == [
        "# First",
        "# Second",
    ]
    assert history.history_storage_bytes(db, "owner") == 15
    with pytest.raises(canvas.CanvasRevisionConflict):
        history.restore_canvas_history(db, "owner", "document", version.id, 2)
    assert db.get(Files, "document").meta["canvas_revision"] == 3
    assert db.query(CanvasHistory).count() == 2


def test_history_permissions_and_grant_time_are_enforced(document):
    db = document
    canvas.save_canvas_markdown(
        db, user_id="owner", file_id="document", content="# Second", expected_revision=1
    )
    version = db.query(CanvasHistory).one()
    for actor in ["viewer", "stranger"]:
        with pytest.raises(HTTPException) as error:
            history.get_canvas_history(db, actor, "document", version.id)
        assert error.value.status_code == 404
    assert (
        history.get_canvas_history(db, "editor", "document", version.id)[1].id
        == version.id
    )
    db.get(FileMember, ("document", "editor")).granted_at = datetime.now(
        timezone.utc
    ) + timedelta(seconds=1)
    db.commit()
    assert (
        history.list_canvas_history(db, "editor", "document", limit=10, offset=0)[
            "items"
        ]
        == []
    )
    with pytest.raises(HTTPException):
        history.restore_canvas_history(db, "editor", "document", version.id, 2)


def test_generated_pdf_follows_current_source_access_without_edit_permission(document):
    from app.files.access import resolve_file_for_read, resolve_file_for_edit

    db = document
    source = db.get(Files, "document")
    source.meta = {**source.meta, "latex_pdf_file_id": "pdf"}
    now = datetime.now(timezone.utc)
    pdf = Files(
        id="pdf",
        user_id="owner",
        file_name="plan.pdf",
        file_type="application/pdf",
        file_category="document",
        file_size=3,
        storage_provider="inline",
        inline_content="PDF",
        meta={"latex_source_file_id": "document"},
        created_at=now,
        last_updated_at=now,
    )
    db.add(pdf)
    db.commit()
    assert resolve_file_for_read(db, "editor", "pdf")
    assert resolve_file_for_read(db, "viewer", "pdf")
    assert not resolve_file_for_edit(db, "editor", "pdf")
    assert not resolve_file_for_read(db, "stranger", "pdf")
    pdf.user_id = "stranger"
    db.commit()
    assert not resolve_file_for_read(db, "editor", "pdf")
    pdf.user_id = "owner"
    db.delete(db.get(FileMember, ("document", "editor")))
    db.commit()
    assert not resolve_file_for_read(db, "editor", "pdf")


def test_empty_canvas_version_survives_archive_and_restore(document, monkeypatch):
    import io
    import json
    import zipfile
    from app.files import archive
    from app.admin.user_exports.files import models as transfer

    db = document
    monkeypatch.setattr(
        files, "overwrite_existing_file_bytes", canvas.overwrite_existing_file_bytes
    )
    monkeypatch.setattr(files, "resolve_user_file_upload_limits", lambda *_: (-1, 1000))
    canvas.save_canvas_markdown(
        db, user_id="owner", file_id="document", content="", expected_revision=1
    )
    canvas.save_canvas_markdown(
        db, user_id="owner", file_id="document", content="# Third", expected_revision=2
    )
    source = db.get(Files, "document")
    inline_entries = json.loads("".join(archive.stream_history_json(db, source)))
    assert [
        transfer._load_and_validate_inline_file_entry(entry)[0]
        for entry in inline_entries
    ] == [b"# First", b""]
    with zipfile.ZipFile(io.BytesIO(), "w") as bundle:
        entries = archive.export_history_zip(db, source, bundle)
        assert [bundle.read(entry["archive_name"]) for entry in entries] == [
            b"# First",
            b"",
        ]

    now = datetime.now(timezone.utc)
    imported = Files(
        id="imported",
        user_id="owner",
        file_name="copy.md",
        file_type="text/markdown",
        file_category="document",
        file_size=7,
        inline_content="# Third",
        storage_provider="inline",
        meta={"canvas": True, "canvas_type": "markdown", "canvas_revision": 3},
        created_at=now,
        last_updated_at=now,
    )
    db.add(imported)
    db.flush()
    archive.restore_file_history(
        db, imported, inline_entries, transfer._load_and_validate_inline_file_entry, []
    )
    db.commit()
    empty_version = (
        db.query(CanvasHistory).filter_by(file_id="imported", revision=2).one()
    )
    history.restore_canvas_history(db, "owner", "imported", empty_version.id, 3)
    assert files.materialize_file_record(imported, "owner").read_bytes() == b""
    assert db.query(CanvasHistory).filter_by(file_id="imported").count() == 3
