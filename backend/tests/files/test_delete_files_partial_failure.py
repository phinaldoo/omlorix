import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
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
from app.files import utils as files_utils
from app.files.schemas import FileDeleteTimeOption


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/api/v1/files/all",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


class DeleteFilesPartialFailureTests:
    def test_delete_all_files_returns_partial_failure_with_deleted_count_and_errors(self, monkeypatch):
        files = [
            SimpleNamespace(id="file-1", file_name="deleted.txt"),
            SimpleNamespace(id="file-2", file_name="still-there.txt"),
        ]
        db = MagicMock()

        monkeypatch.setattr(files_utils, "list_files", lambda db, user_id: files)

        def delete_file_record(db, user_id, file_info):
            if file_info.id == "file-2":
                raise HTTPException(status_code=500, detail="storage delete failed")

        monkeypatch.setattr(files_utils, "_delete_file_record", delete_file_record)

        result = files_utils.delete_all_files("user-1", db)

        assert result["status"] == "partial_failure"
        assert result["deleted_count"] == 1
        assert len(result["errors"]) == 1
        assert "still-there.txt" in result["errors"][0]
        db.rollback.assert_called_once()

    def test_delete_file_bulk_route_returns_partial_failure_with_deleted_count_and_errors(self, monkeypatch):
        files = [
            SimpleNamespace(id="file-1", file_name="deleted.txt"),
            SimpleNamespace(id="file-2", file_name="still-there.txt"),
        ]
        db = MagicMock()
        query = db.query.return_value
        query.filter.return_value = query
        query.all.return_value = files

        def delete_file_record(db, user_id, file_info):
            if file_info.id == "file-2":
                raise HTTPException(status_code=500, detail="storage delete failed")

        monkeypatch.setattr(files_utils, "_delete_file_record", delete_file_record)

        result = files_utils.delete_file("user-1", None, db, FileDeleteTimeOption.ALL)

        assert result["status"] == "partial_failure"
        assert result["deleted_count"] == 1
        assert len(result["errors"]) == 1
        assert "still-there.txt" in result["errors"][0]
        db.rollback.assert_called_once()

    def test_delete_all_files_route_returns_non_2xx_and_audits_errors(self):
        result = {
            "status": "partial_failure",
            "message": "Deleted 1 of 2 files. 1 files could not be deleted.",
            "deleted_count": 1,
            "errors": ["Failed to delete still-there.txt: storage delete failed"],
        }

        with patch.object(files_router, "delete_all_files", return_value=result), patch.object(
            files_router,
            "_audit_file_event",
        ) as audit_file_event:
            response = files_router.delete_all_files_route(
                request=_request(),
                delete_all=True,
                user=SimpleNamespace(id="user-1"),
                db=MagicMock(),
                db_log=MagicMock(),
            )

        assert response.status_code == 500
        assert json.loads(response.body)["deleted_count"] == 1
        audit_file_event.assert_called_once()
        assert audit_file_event.call_args.args[3] == "ALL_FILES_DELETE_FAILED"
        assert audit_file_event.call_args.args[4]["deleted_count"] == 1
        assert audit_file_event.call_args.args[4]["error_count"] == 1

    def test_delete_file_route_returns_non_2xx_and_audits_errors(self):
        result = {
            "status": "partial_failure",
            "message": "Deleted 1 of 2 files. 1 files could not be deleted.",
            "deleted_count": 1,
            "errors": ["Failed to delete still-there.txt: storage delete failed"],
        }

        with patch.object(files_router, "delete_file", return_value=result), patch.object(
            files_router,
            "_audit_file_event",
        ) as audit_file_event:
            response = files_router.delete_file_route(
                request=_request(),
                file_id=None,
                time=FileDeleteTimeOption.ALL,
                user=SimpleNamespace(id="user-1"),
                db=MagicMock(),
                db_log=MagicMock(),
            )

        assert response.status_code == 500
        assert json.loads(response.body)["deleted_count"] == 1
        audit_file_event.assert_called_once()
        assert audit_file_event.call_args.args[3] == "FILE_DELETE_FAILED"
        assert audit_file_event.call_args.args[4]["deleted_count"] == 1
        assert audit_file_event.call_args.args[4]["error_count"] == 1

    def test_materialize_local_file_rejects_storage_key_path_traversal(self, tmp_path, monkeypatch):
        storage_base = tmp_path / "files"
        storage_base.mkdir()
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("outside", encoding="utf-8")
        monkeypatch.setattr(files_utils, "BASE_STORAGE_DIR", storage_base)

        file_record = SimpleNamespace(
            id="file-1",
            file_name="missing.txt",
            storage_provider="local",
            storage_key="../outside.txt",
        )

        with pytest.raises(HTTPException) as denied:
            files_utils.materialize_file_record(file_record, "user-1")

        assert denied.value.status_code == 404

    def test_delete_storage_reference_rejects_storage_key_path_traversal(self, tmp_path, monkeypatch):
        storage_base = tmp_path / "files"
        storage_base.mkdir()
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("outside", encoding="utf-8")
        monkeypatch.setattr(files_utils, "BASE_STORAGE_DIR", storage_base)

        files_utils.delete_storage_reference(
            storage_provider="local",
            storage_key="../outside.txt",
            user_id="user-1",
            file_name="missing.txt",
        )

        assert outside_file.exists()
