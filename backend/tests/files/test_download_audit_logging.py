import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


from app.files import router as files_router
from app.files import utils as file_utils


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/files/download",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


def test_download_file_route_audits_owned_file_download():
    request = _request()
    db = MagicMock()
    db_log = MagicMock()
    response = MagicMock()
    file_record = SimpleNamespace(
        id="file-1",
        user_id="owner-1",
        folder_id=None,
        project_id="project-1",
    )

    with patch.object(files_router, "download_file", return_value=response) as mock_download, patch.object(
        files_router,
        "get_accessible_file",
        return_value=file_record,
    ), patch.object(files_router, "_audit_file_event") as mock_audit:
        result = files_router.download_file_route(
            request=request,
            file_id="file-1",
            inline=True,
            user=SimpleNamespace(id="owner-1"),
            db=db,
            db_log=db_log,
        )

    assert result is response
    mock_download.assert_called_once_with("owner-1", "file-1", db, inline=True)
    mock_audit.assert_called_once_with(
        db_log,
        request,
        "owner-1",
        "FILE_DOWNLOADED",
        {
            "actor_user_id": "owner-1",
            "owner_user_id": "owner-1",
            "file_id": "file-1",
            "folder_id": None,
            "folder_owner_user_id": None,
            "project_id": "project-1",
            "access_via_shared_folder": False,
            "inline": True,
        },
    )


def test_download_file_route_audits_shared_folder_download():
    request = _request()
    db = MagicMock()
    db_log = MagicMock()
    response = MagicMock()
    shared_file = SimpleNamespace(
        id="file-2",
        user_id="file-owner-1",
        folder_id="folder-1",
        project_id=None,
    )
    folder_record = SimpleNamespace(id="folder-1", user_id="folder-owner-1")
    folder_query = MagicMock()
    folder_query.filter.return_value.first.return_value = folder_record
    db.query.return_value = folder_query

    with patch.object(files_router, "download_file", return_value=response) as mock_download, patch.object(
        files_router,
        "get_accessible_file",
        return_value=shared_file,
    ), patch.object(
        files_router,
        "_audit_file_event",
    ) as mock_audit:
        result = files_router.download_file_route(
            request=request,
            file_id="file-2",
            inline=False,
            user=SimpleNamespace(id="actor-2"),
            db=db,
            db_log=db_log,
        )

    assert result is response
    mock_download.assert_called_once_with("actor-2", "file-2", db, inline=False)
    mock_audit.assert_called_once_with(
        db_log,
        request,
        "actor-2",
        "FILE_DOWNLOADED",
        {
            "actor_user_id": "actor-2",
            "owner_user_id": "file-owner-1",
            "file_id": "file-2",
            "folder_id": "folder-1",
            "folder_owner_user_id": "folder-owner-1",
            "project_id": None,
            "access_via_shared_folder": True,
            "inline": False,
        },
    )


def test_download_file_does_not_mutate_last_updated_at():
    db = MagicMock()
    response = SimpleNamespace(headers={})
    original_last_updated_at = object()
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type="text/plain",
        file_name="notes.txt",
        meta={},
        last_updated_at=original_last_updated_at,
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=Path("/tmp/notes.txt"),
    ), patch.object(file_utils, "FileResponse", return_value=response):
        result = file_utils.download_file("actor-1", "file-1", db)

    assert result is response
    assert file_record.last_updated_at is original_last_updated_at
    db.commit.assert_not_called()


def test_download_file_disables_caching_for_mutable_file_ids():
    """Reopening an overwritten Canvas file must never reuse its old body."""

    db = MagicMock()
    response = SimpleNamespace(headers={})
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type="text/html",
        file_name="website.html",
        meta={"canvas": True, "canvas_type": "html"},
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=Path("/tmp/website.html"),
    ), patch.object(file_utils, "FileResponse", return_value=response):
        result = file_utils.download_file("actor-1", "file-1", db, inline=True)

    assert result.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert result.headers["Pragma"] == "no-cache"
    assert result.headers["Expires"] == "0"


def test_download_file_allows_same_origin_framing_for_inline_pdf_preview():
    db = MagicMock()
    response = SimpleNamespace(headers={})
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type="application/pdf",
        file_name="report.pdf",
        meta={},
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=Path("/tmp/report.pdf"),
    ), patch.object(file_utils, "FileResponse", return_value=response) as mock_file_response:
        result = file_utils.download_file("actor-1", "file-1", db, inline=True)

    assert result is response
    assert mock_file_response.call_args.kwargs["content_disposition_type"] == "inline"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_download_file_encodes_unicode_filename_for_inline_preview(tmp_path):
    """Unicode filenames must not be written raw into Starlette headers."""
    db = MagicMock()
    file_path = tmp_path / "stored.pdf"
    file_path.write_bytes(b"%PDF-test")
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type="application/pdf",
        file_name="stored.pdf",
        meta={"original_filename": "Invoice € 📄.pdf"},
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=file_path,
    ):
        response = file_utils.download_file("actor-1", "file-1", db, inline=True)

    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("inline; filename*=utf-8''")
    assert "Invoice%20%E2%82%AC%20%F0%9F%93%84.pdf" in disposition

    # Accessing raw_headers forces the same Latin-1 representation used by the
    # ASGI server and guards directly against the production failure.
    assert all(isinstance(value, bytes) for _, value in response.raw_headers)


@pytest.mark.parametrize("html_mime", file_utils.HTML_ATTACHMENT_MIME_TYPES)
def test_download_file_treats_html_mime_alias_as_unsafe_for_inline(html_mime):
    db = MagicMock()
    response = SimpleNamespace(headers={})
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type=f"{html_mime}; charset=utf-8",
        file_name="page.html",
        meta={},
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=Path("/tmp/page.html"),
    ), patch.object(file_utils, "FileResponse", return_value=response) as mock_file_response:
        result = file_utils.download_file("actor-1", "file-1", db, inline=True)

    assert result is response
    assert mock_file_response.call_args.kwargs["content_disposition_type"] == "attachment"
    assert "Content-Security-Policy" not in response.headers
    assert "X-Frame-Options" not in response.headers


def test_download_file_keeps_unicode_active_content_as_attachment(tmp_path):
    """An inline request must not weaken attachment handling for active content."""
    db = MagicMock()
    file_path = tmp_path / "stored.html"
    file_path.write_text("<p>test</p>", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        user_id="actor-1",
        file_type="text/html; charset=utf-8",
        file_name="stored.html",
        meta={"original_filename": "Price €.html"},
    )

    with patch.object(file_utils, "get_accessible_file", return_value=file_record), patch.object(
        file_utils,
        "materialize_file_record",
        return_value=file_path,
    ):
        response = file_utils.download_file("actor-1", "file-1", db, inline=True)

    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment; filename*=utf-8''")
    assert "Price%20%E2%82%AC.html" in disposition


def test_get_file_info_allows_shared_folder_file_metadata():
    db = MagicMock()
    shared_file = SimpleNamespace(
        id="file-2",
        user_id="owner-1",
        file_name="stored.txt",
        file_type="text/plain",
        file_category="document",
        file_size=42,
        meta={"original_filename": "Shared.txt"},
    )

    with patch("app.database.SessionLocal", return_value=db), patch.object(
        file_utils,
        "resolve_accessible_file_record",
        return_value=(shared_file, "owner-1"),
    ) as mock_resolve, patch.object(
        file_utils,
        "materialize_file_record",
        return_value=Path("/tmp/shared.txt"),
    ) as mock_materialize, patch.object(
        file_utils,
        "_resolve_storage_reference",
        return_value=("local", "owner-1/stored.txt"),
    ) as mock_storage:
        result = file_utils.get_file_info("collab-user", "file-2")

    assert result == {
        "file_id": "file-2",
        "owner_user_id": "owner-1",
        "requester_user_id": "collab-user",
        "path": "/tmp/shared.txt",
        "file_name": "stored.txt",
        "storage_provider": "local",
        "storage_key": "owner-1/stored.txt",
        "file_type": "text/plain",
        "file_category": "document",
        "file_size": 42,
        "meta": {"original_filename": "Shared.txt"},
    }
    mock_resolve.assert_called_once_with(db, "collab-user", "file-2")
    mock_materialize.assert_called_once_with(shared_file, "owner-1")
    mock_storage.assert_called_once_with(shared_file, "owner-1")
    db.close.assert_called_once_with()
