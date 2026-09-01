import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

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


from app.files import utils as file_utils
from app.files.storage import build_storage_key
from app.files.storage.paths import resolve_local_storage_path


def test_build_storage_key_rejects_path_traversal_filename():
    with pytest.raises(ValueError, match="file_name"):
        build_storage_key("user-1", "../victim/secret.txt")


@pytest.mark.parametrize("storage_key", ["../victim/secret.txt", "/etc/passwd", r"user-1\\secret.txt"])
def test_resolve_local_storage_path_rejects_invalid_keys(tmp_path, storage_key):
    with pytest.raises(ValueError):
        resolve_local_storage_path(tmp_path, storage_key)


def test_materialize_file_record_rejects_storage_key_outside_user_scope(tmp_path):
    victim_path = tmp_path / "victim-1" / "secret.txt"
    victim_path.parent.mkdir(parents=True, exist_ok=True)
    victim_path.write_text("top secret", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        file_name="safe.txt",
        storage_provider="local",
        storage_key="victim-1/secret.txt",
    )

    with patch.object(file_utils, "BASE_STORAGE_DIR", tmp_path):
        with pytest.raises(HTTPException) as exc:
            file_utils.materialize_file_record(file_record, "attacker-1")

    assert exc.value.status_code == 404
    assert victim_path.exists()


def test_materialize_file_record_rejects_legacy_traversal_file_name(tmp_path):
    victim_path = tmp_path / "victim-1" / "secret.txt"
    victim_path.parent.mkdir(parents=True, exist_ok=True)
    victim_path.write_text("top secret", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-legacy",
        file_name="../victim-1/secret.txt",
        storage_provider="local",
        storage_key="",
    )

    with patch.object(file_utils, "BASE_STORAGE_DIR", tmp_path):
        with pytest.raises(HTTPException) as exc:
            file_utils.materialize_file_record(file_record, "attacker-1")

    assert exc.value.status_code == 404
    assert victim_path.exists()


def test_delete_storage_reference_skips_cross_user_local_key(tmp_path):
    victim_path = tmp_path / "victim-1" / "secret.txt"
    victim_path.parent.mkdir(parents=True, exist_ok=True)
    victim_path.write_text("top secret", encoding="utf-8")

    with patch.object(file_utils, "BASE_STORAGE_DIR", tmp_path):
        file_utils.delete_storage_reference(
            storage_provider="local",
            storage_key="victim-1/secret.txt",
            user_id="attacker-1",
        )

    assert victim_path.exists()


def test_delete_storage_reference_skips_cross_user_remote_key():
    with patch.object(file_utils, "delete_file_from_storage") as mock_delete:
        file_utils.delete_storage_reference(
            storage_provider="s3",
            storage_key="victim-1/secret.txt",
            user_id="attacker-1",
        )

    mock_delete.assert_not_called()
