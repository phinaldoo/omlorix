from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

from app.database import Base  # noqa: E402
from app.files import router as files_router  # noqa: E402
from app.files.schemas import (  # noqa: E402
    normalize_model_input_formats,
    supported_file_formats_for_model_input_formats,
)
from app.files import utils as file_utils  # noqa: E402
from app.files.models import FileQuotaReservation, Files  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Files.__table__, FileQuotaReservation.__table__],
    )
    return sessionmaker(bind=engine)()


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def _user():
    return SimpleNamespace(id="user-1", group_id="group-1")


def _upload(payload: bytes, filename: str) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(payload))


def _configure_upload(monkeypatch, tmp_path):
    def fake_group_setting(_user_id, _section, key, _db):
        if key == "allow_file_uploads":
            return True
        if key == "max_files_upload_count":
            return -1
        if key == "max_user_files_size_gb":
            return None
        return None

    monkeypatch.setattr(file_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(file_utils, "get_user_group_setting_value", fake_group_setting)
    monkeypatch.setattr(files_router, "_audit_file_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        files_router,
        "resolve_selected_model_for_user",
        lambda _db, *, user_id, model_id: SimpleNamespace(
            selected_model_id=model_id,
            base_model=SimpleNamespace(id=model_id),
        ),
    )
    monkeypatch.setattr(files_router, "ensure_user_access_to_model", lambda *_args, **_kwargs: None)


def test_upload_resolves_custom_agent_and_authorizes_its_backing_model(monkeypatch):
    db = object()
    access_checks = []

    def resolve_selection(received_db, *, user_id, model_id):
        assert received_db is db
        assert user_id == "user-1"
        assert model_id == "agent-1"
        return SimpleNamespace(
            selected_model_id="agent-1",
            base_model=SimpleNamespace(id="base-model-1"),
        )

    async def fake_upload(_file, _project_id, _user_id, _db, *, folder_id=None):
        return {"status": "success", "file_id": "file-1", "file_category": "image"}

    monkeypatch.setattr(files_router, "ensure_user_file_upload_size_limit", lambda *_args: None)
    monkeypatch.setattr(files_router, "resolve_selected_model_for_user", resolve_selection)
    monkeypatch.setattr(
        files_router,
        "ensure_user_access_to_model",
        lambda user_id, model_id, received_db: access_checks.append((user_id, model_id, received_db)),
    )
    monkeypatch.setattr(files_router, "upload_file", fake_upload)
    monkeypatch.setattr(files_router, "_audit_file_event", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        files_router.upload_file_route(
            _request(),
            _upload(b"\x89PNG\r\n\x1a\n", "screenshot.png"),
            project_id=None,
            folder_id=None,
            group_context_id=None,
            model_id="agent-1",
            user=_user(),
            db=db,
            db_log=object(),
        )
    )

    assert result["file_id"] == "file-1"
    assert access_checks == [
        ("user-1", "agent-1", db),
        ("user-1", "base-model-1", db),
    ]


def test_normalize_model_input_formats_uses_mapping_keys():
    assert normalize_model_input_formats({"Image": True, " PDF ": False}) == {"image", "pdf"}


def test_source_text_documents_are_advertised_for_text_only_chat_models():
    """The frontend must allow source formats Omlorix supplies as plain text."""
    categories = supported_file_formats_for_model_input_formats(["text"])

    assert categories == [
        {
            "category": "document",
            "file_formats": [
                "image/svg+xml",
                "text/html",
                "application/html",
                "application/xhtml+xml",
                "application/x-html",
                "text/xhtml",
            ],
        }
    ]


def test_unspecified_model_file_formats_remain_unrestricted():
    """An absent capability declaration must preserve the frontend fallback."""
    assert supported_file_formats_for_model_input_formats(None) == []


def test_model_upload_accepts_unsupported_model_format_type(monkeypatch, tmp_path):
    db = _session()
    upload_calls = []
    _configure_upload(monkeypatch, tmp_path)

    monkeypatch.setattr(file_utils, "_detect_mime_from_content", lambda _path, fallback=None: "application/pdf")
    
    def fake_upload_to_storage(path, user_id, file_name):
        upload_calls.append((Path(path).read_bytes(), user_id, file_name))
        return "local", f"{user_id}/{file_name}", {}
    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload_to_storage)

    result = asyncio.run(
        files_router.upload_file_route(
            _request(),
            _upload(b"%PDF-1.7\n", "image.png"),
            project_id=None,
            folder_id=None,
            group_context_id=None,
            model_id="model-1",
            user=_user(),
            db=db,
            db_log=object(),
        )
    )

    file_record = db.query(Files).one()
    assert result["status"] == "success"
    assert result["file_id"] == file_record.id
    assert file_record.file_type == "application/pdf"
    assert upload_calls


def test_storage_upload_failure_is_not_masked_by_logging(monkeypatch, tmp_path, caplog):
    """A storage exception must remain the API error instead of crashing logging."""
    db = _session()
    _configure_upload(monkeypatch, tmp_path)
    monkeypatch.setattr(
        file_utils,
        "_detect_mime_from_content",
        lambda _path, fallback=None: "application/pdf",
    )

    def fail_storage_upload(_path, _user_id, _file_name):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(file_utils, "upload_file_to_storage", fail_storage_upload)
    caplog.set_level(logging.ERROR, logger=file_utils.__name__)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            files_router.upload_file_route(
                _request(),
                _upload(b"%PDF-1.7\n", "document.pdf"),
                project_id=None,
                folder_id=None,
                group_context_id=None,
                model_id=None,
                user=_user(),
                db=db,
                db_log=object(),
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Upload failed while storing file"
    storage_log = next(
        record for record in caplog.records
        if record.getMessage() == "[Files] Storage upload error"
    )
    assert storage_log.uploaded_filename == "document.pdf"


def test_model_upload_accepts_detected_type_even_when_extension_looks_unsupported(monkeypatch, tmp_path):
    db = _session()
    upload_calls = []
    _configure_upload(monkeypatch, tmp_path)

    monkeypatch.setattr(file_utils, "_detect_mime_from_content", lambda _path, fallback=None: "image/png")

    def fake_upload_to_storage(path, user_id, file_name):
        upload_calls.append((Path(path).read_bytes(), user_id, file_name))
        return "local", f"{user_id}/{file_name}", {}

    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload_to_storage)

    result = asyncio.run(
        files_router.upload_file_route(
            _request(),
            _upload(b"\x89PNG\r\n\x1a\n", "report.pdf"),
            project_id=None,
            folder_id=None,
            group_context_id=None,
            model_id="model-1",
            user=_user(),
            db=db,
            db_log=object(),
        )
    )

    file_record = db.query(Files).one()
    assert result["status"] == "success"
    assert result["file_id"] == file_record.id
    assert result["file_category"] == "image"
    assert file_record.file_type == "image/png"
    assert upload_calls


def test_model_upload_accepts_extension_fallback_when_content_detection_disagrees(monkeypatch, tmp_path):
    db = _session()
    upload_calls = []
    _configure_upload(monkeypatch, tmp_path)

    monkeypatch.setattr(
        file_utils,
        "_detect_mime_from_content",
        lambda _path, fallback=None: fallback if fallback is not None else "application/pdf",
    )
    
    def fake_upload_to_storage(path, user_id, file_name):
        upload_calls.append((Path(path).read_bytes(), user_id, file_name))
        return "local", f"{user_id}/{file_name}", {}
    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload_to_storage)

    result = asyncio.run(
        files_router.upload_file_route(
            _request(),
            _upload(b"%PDF-1.7\n", "image.png"),
            project_id=None,
            folder_id=None,
            group_context_id=None,
            model_id="model-1",
            user=_user(),
            db=db,
            db_log=object(),
        )
    )

    file_record = db.query(Files).one()
    assert result["status"] == "success"
    assert result["file_id"] == file_record.id
    assert file_record.file_type == "image/png"
    assert upload_calls


def test_upload_rejects_generic_octet_stream_binary(monkeypatch, tmp_path):
    db = _session()
    _configure_upload(monkeypatch, tmp_path)

    monkeypatch.setattr(file_utils, "_detect_mime_from_content", lambda _path, fallback=None: "application/octet-stream")
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda *args, **kwargs: pytest.fail("generic binary upload should be rejected before storage"),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            file_utils.upload_file(
                _upload(b"MZ executable payload", "payload.bin"),
                project_id=None,
                user_id="user-1",
                db=db,
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "File type application/octet-stream is not allowed"
    assert db.query(Files).count() == 0


def test_user_file_upload_accepts_html_as_inert_document(monkeypatch, tmp_path):
    """Conversation uploads may retain HTML source without enabling it globally."""
    db = _session()
    upload_calls = []
    _configure_upload(monkeypatch, tmp_path)
    html = b"<!doctype html><html><body><script>alert('inert')</script></body></html>"

    def fake_upload_to_storage(path, user_id, file_name):
        upload_calls.append((Path(path).read_bytes(), user_id, file_name))
        return "local", f"{user_id}/{file_name}", {}

    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload_to_storage)

    result = asyncio.run(
        file_utils.upload_file(
            _upload(html, "example.html"),
            project_id=None,
            user_id="user-1",
            db=db,
        )
    )

    file_record = db.query(Files).one()
    assert result["status"] == "success"
    assert result["file_category"] == "document"
    assert file_record.file_type == "text/html"
    assert upload_calls[0][0] == html


def test_html_remains_blocked_on_non_user_asset_validation_paths(tmp_path):
    """Agent and skill upload scanners must retain the active-content deny rule."""
    html_path = tmp_path / "asset.html"
    html_path.write_text("<!doctype html><html><body>asset</body></html>", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        file_utils.detect_and_validate_upload_mime(html_path)

    assert exc.value.status_code == 400
    assert exc.value.detail == "File type text/html is not allowed"


def test_user_file_upload_accepts_xhtml_alias_as_inert_document(monkeypatch, tmp_path):
    """XHTML uses the same attachment-only storage boundary as ordinary HTML."""
    db = _session()
    _configure_upload(monkeypatch, tmp_path)
    xhtml = b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body><script>alert('inert')</script></body></html>"
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda path, user_id, file_name: ("local", f"{user_id}/{file_name}", {}),
    )

    result = asyncio.run(
        file_utils.upload_file(
            _upload(xhtml, "example.xhtml"),
            project_id=None,
            user_id="user-1",
            db=db,
        )
    )

    file_record = db.query(Files).one()
    assert result["status"] == "success"
    assert result["file_category"] == "document"
    assert file_record.file_type in {"text/html", "application/xhtml+xml"}


def test_sqlite_upload_wrapper_does_not_retry_internal_type_error(monkeypatch):
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    attempts = []

    async def fail_upload(*_args, **_kwargs):
        attempts.append("inline")
        raise TypeError("upload workflow failed")

    async def fail_retry(*_args, **_kwargs):
        attempts.append("worker")
        pytest.fail("a failed upload must not be retried")

    monkeypatch.setattr(file_utils, "upload_file", fail_upload)
    monkeypatch.setattr(file_utils, "run_blocking_io", fail_retry)

    with pytest.raises(TypeError, match="upload workflow failed"):
        asyncio.run(
            file_utils.upload_file_off_event_loop(
                _upload(b"payload", "example.txt"),
                project_id=None,
                user_id="user-1",
                db=db,
            )
        )

    assert attempts == ["inline"]
