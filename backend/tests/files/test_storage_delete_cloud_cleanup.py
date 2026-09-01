import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


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


from app.files import utils as files_utils
from app.files import storage as files_storage


class StorageDeleteCloudCleanupTests:
    def test_delete_file_from_storage_surfaces_adapter_setup_failures(self, monkeypatch):
        monkeypatch.setattr(
            files_storage,
            "get_user_file_storage_adapter_for_provider",
            lambda _provider: (_ for _ in ()).throw(RuntimeError("missing FILE_STORAGE_S3_BUCKET")),
        )

        with pytest.raises(RuntimeError, match="missing FILE_STORAGE_S3_BUCKET"):
            files_storage.delete_file_from_storage("s3", "user-1/cloud.txt")

    def test_delete_file_record_keeps_db_row_when_storage_cleanup_fails(self, monkeypatch):
        db = MagicMock()
        file_info = SimpleNamespace(
            id="file-1",
            file_name="cloud.txt",
            storage_provider="s3",
            storage_key="user-1/cloud.txt",
        )

        monkeypatch.setattr(
            files_utils,
            "delete_file_from_storage",
            lambda _provider, _storage_key: (_ for _ in ()).throw(RuntimeError("missing FILE_STORAGE_S3_BUCKET")),
        )

        with pytest.raises(HTTPException) as exc_info:
            files_utils._delete_file_record(db, "user-1", file_info)

        assert exc_info.value.status_code == 500
        assert "missing FILE_STORAGE_S3_BUCKET" in exc_info.value.detail
        db.delete.assert_not_called()
        db.commit.assert_not_called()
