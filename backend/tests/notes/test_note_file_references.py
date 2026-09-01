import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.notes.utils import (  # noqa: E402
    note_content_contains_file_reference,
    parse_note_file_references,
    replace_note_file_references,
)
from app.notes.models import _clone_note_embedded_files  # noqa: E402
from app.notes import router as notes_router  # noqa: E402


def test_parse_note_file_references_detects_omlorix_file_markdown_links():
    references = parse_note_file_references(
        "Chart: ![CPIAUCSL Chart](omlorix-file://0c3e625d-d147-464b-8f02-9360667384be)\n"
        "Source: [CSV](omlorix-file://file-123?download=1)"
    )

    assert [(reference.kind, reference.owner_id, reference.file_id, reference.label) for reference in references] == [
        ("image", "", "0c3e625d-d147-464b-8f02-9360667384be", "CPIAUCSL Chart"),
        ("file", "", "file-123", "CSV"),
    ]


def test_note_content_contains_file_reference_matches_ownerless_omlorix_file_links():
    content = "![Chart](omlorix-file://file-123)"

    assert note_content_contains_file_reference(content, owner_id="owner-1", file_id="file-123") is True
    assert note_content_contains_file_reference(content, owner_id="owner-1", file_id="other-file") is False


def test_replace_note_file_references_rewrites_omlorix_file_urls():
    content = "![Chart](omlorix-file://file-123)\n[Data](omlorix-file://file-123?inline=true)"

    replaced = replace_note_file_references(
        content,
        {("image", "", "file-123"): ("target-user", "file-456")},
    )

    assert replaced == "![Chart](omlorix-file://file-456)\n[Data](omlorix-file://file-456?inline=true)"


def test_clone_note_embedded_files_skips_references_from_non_source_owners():
    content = "{{note:file:other-owner:file-123|Private file}}"

    assert (
        _clone_note_embedded_files(None, content, "target-user", source_owner_id="source-owner")
        == content
    )


@pytest.mark.parametrize(
    ("content_disposition", "expected_audit_count"),
    [("inline; filename=file.png", 0), ("attachment; filename=file.bin", 1)],
)
def test_note_file_route_audits_only_effective_attachments(
    monkeypatch,
    content_disposition,
    expected_audit_count,
):
    note = SimpleNamespace(
        id="note-1",
        user_id="owner-1",
        content="{{note:file:owner-1:file-1|Attachment}}",
    )
    file_record = SimpleNamespace(id="file-1", user_id="owner-1")
    rows = iter([note, file_record])

    class QueryStub:
        def filter(self, *_args):
            return self

        def first(self):
            return next(rows)

    db = SimpleNamespace(query=lambda *_args: QueryStub())
    audit_calls = []
    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *_args: None)
    monkeypatch.setattr(notes_router, "can_user_view_note", lambda *_args: True)
    monkeypatch.setattr(
        notes_router,
        "note_content_contains_file_reference",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        notes_router,
        "_can_user_access_embedded_file",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        notes_router,
        "download_file",
        lambda *_args, **_kwargs: Response(
            content=b"file",
            headers={"Content-Disposition": content_disposition},
        ),
    )
    monkeypatch.setattr(notes_router, "get_audit_request_ip", lambda *_args: None)
    monkeypatch.setattr(
        notes_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = notes_router.download_note_file_route(
        note_id="note-1",
        owner_id="owner-1",
        file_id="file-1",
        request=SimpleNamespace(headers={}),
        inline=True,
        db=db,
        db_log=object(),
        user=SimpleNamespace(id="reader-1"),
    )

    assert response.headers["content-disposition"] == content_disposition
    assert len(audit_calls) == expected_audit_count
    if audit_calls:
        assert audit_calls[0]["action"] == "NOTE_ATTACHMENT_DOWNLOADED"
        assert audit_calls[0]["details"] == {
            "note_id": "note-1",
            "file_id": "file-1",
            "file_owner_user_id": "owner-1",
            "is_collaborator": True,
            "disposition": "attachment",
        }
