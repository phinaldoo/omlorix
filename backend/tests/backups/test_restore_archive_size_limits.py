from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.backups import service as backup_service  # noqa: E402


@contextmanager
def _stream_reader(payload: bytes):
    yield BytesIO(payload)


def _tar_bytes(filename: str, payload: bytes) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(filename)
        info.size = len(payload)
        archive.addfile(info, BytesIO(payload))
    return buffer.getvalue()


def test_extract_zstd_archive_rejects_decompressed_output_before_tar_extract(tmp_path, monkeypatch):
    tar_payload = _tar_bytes("manifest.json", b"{}")
    compressed_path = tmp_path / "backup.tar.zst"
    compressed_path.write_bytes(b"small-compressed-input")
    monkeypatch.setattr(
        backup_service.zstandard,
        "ZstdDecompressor",
        lambda: SimpleNamespace(stream_reader=lambda raw: _stream_reader(tar_payload)),
        raising=False,
    )

    with pytest.raises(backup_service.BackupArchiveSizeLimitError) as exc_info:
        backup_service._extract_zstd_archive(
            compressed_path,
            tmp_path / "extract",
            max_decompressed_bytes=len(tar_payload) - 1,
            max_extracted_bytes=len(tar_payload),
        )

    assert exc_info.value.reason == "archive_decompressed_size_exceeded"
    assert not (tmp_path / "extract" / "manifest.json").exists()


def test_extract_zstd_archive_preserves_restore_disk_reserve(tmp_path, monkeypatch):
    tar_payload = _tar_bytes("manifest.json", b"{}")
    compressed_path = tmp_path / "backup.tar.zst"
    compressed_path.write_bytes(b"small-compressed-input")
    monkeypatch.setattr(
        backup_service.zstandard,
        "ZstdDecompressor",
        lambda: SimpleNamespace(stream_reader=lambda raw: _stream_reader(tar_payload)),
        raising=False,
    )
    monkeypatch.setattr(backup_service, "BACKUP_RESTORE_MIN_FREE_BYTES", 15)
    monkeypatch.setattr(backup_service.shutil, "disk_usage", lambda path: SimpleNamespace(free=20))

    with pytest.raises(backup_service.BackupArchiveSizeLimitError) as exc_info:
        backup_service._extract_zstd_archive(
            compressed_path,
            tmp_path / "extract",
            max_decompressed_bytes=len(tar_payload),
            max_extracted_bytes=len(tar_payload),
        )

    assert exc_info.value.reason == "archive_decompressed_size_exceeded"
    assert exc_info.value.limit_bytes == 5
    assert not (tmp_path / "extract" / "manifest.json").exists()


def test_extract_zstd_archive_rejects_extracted_payload_before_writing_member(tmp_path, monkeypatch):
    tar_payload = _tar_bytes("manifest.json", b"0123456789")
    compressed_path = tmp_path / "backup.tar.zst"
    compressed_path.write_bytes(b"small-compressed-input")
    monkeypatch.setattr(
        backup_service.zstandard,
        "ZstdDecompressor",
        lambda: SimpleNamespace(stream_reader=lambda raw: _stream_reader(tar_payload)),
        raising=False,
    )

    with pytest.raises(backup_service.BackupArchiveSizeLimitError) as exc_info:
        backup_service._extract_zstd_archive(
            compressed_path,
            tmp_path / "extract",
            max_decompressed_bytes=len(tar_payload),
            max_extracted_bytes=5,
        )

    assert exc_info.value.reason == "archive_extracted_size_exceeded"
    assert not (tmp_path / "extract" / "manifest.json").exists()


def test_preflight_reports_restore_archive_size_limit_failures(monkeypatch):
    def reject_size_limit(*args, **kwargs):
        raise backup_service.BackupArchiveSizeLimitError(
            reason="archive_decompressed_size_exceeded",
            limit_bytes=100,
            observed_bytes=101,
        )

    monkeypatch.setattr(backup_service, "_extract_zstd_archive", reject_size_limit)

    result = backup_service.preflight_backup_archive(Path("/tmp/backup.tar.zst"), target_mode="empty", db=object())

    assert result == {
        "ok": False,
        "reason": "archive_decompressed_size_exceeded",
        "limit_bytes": 100,
        "observed_bytes": 101,
    }


def test_preflight_rejects_boolean_manifest_version(monkeypatch):
    """Restore manifests reject booleans even though Python treats them as ints."""

    def materialize_archive(_archive_path, destination):
        for relative_path in backup_service.BACKUP_REQUIRED_PATHS.values():
            path = destination / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        (destination / backup_service.BACKUP_REQUIRED_PATHS["manifest"]).write_text(
            json.dumps(
                {
                    "format": backup_service.BACKUP_EXPORT_FORMAT,
                    "export_version": True,
                }
            ),
            encoding="utf-8",
        )
        (destination / backup_service.BACKUP_REQUIRED_PATHS["crypto_probe"]).write_text(
            "encrypted-probe",
            encoding="utf-8",
        )

    monkeypatch.setattr(backup_service, "_extract_zstd_archive", materialize_archive)
    monkeypatch.setattr(
        backup_service,
        "decrypt_value",
        lambda _ciphertext: "omlorix-backup-probe-v1.0",
    )

    result = backup_service.preflight_backup_archive(
        Path("/tmp/backup.tar.zst"),
        target_mode="empty",
        db=object(),
    )

    assert result == {
        "ok": False,
        "reason": "unsupported_export_version",
        "expected_export_version": 1.0,
    }


def test_postgres_plain_sql_estimate_uses_larger_sequential_dump(tmp_path, monkeypatch):
    """Preflight reserves expanded SQL space without summing sequential restores."""
    main_dump = tmp_path / backup_service.BACKUP_REQUIRED_PATHS["main_dump"]
    audit_dump = tmp_path / backup_service.BACKUP_REQUIRED_PATHS["audit_dump"]
    main_dump.parent.mkdir(parents=True)
    main_dump.write_bytes(b"m" * 10)
    audit_dump.write_bytes(b"a" * 4)
    monkeypatch.setattr(backup_service, "DATABASE_CONFIG", {"driver": "postgresql"})
    monkeypatch.setattr(backup_service, "AUDIT_DATABASE_CONFIG", {"driver": "postgresql"})
    monkeypatch.setattr(backup_service, "BACKUP_POSTGRES_PLAIN_SQL_EXPANSION_FACTOR", 5)

    assert backup_service._estimate_postgres_plain_sql_restore_bytes(tmp_path) == 50


def test_restore_capacity_checks_each_mounted_filesystem_independently(tmp_path, monkeypatch):
    """Free overlay space must not hide an undersized application-data mount."""
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    backup_dir = tmp_path / "backups"
    for directory in (workspace, data_dir, log_dir, backup_dir):
        directory.mkdir()

    filesystem_ids = {
        workspace: 1,
        data_dir: 2,
        log_dir: 3,
        backup_dir: 4,
    }
    free_bytes = {1: 10_000, 2: 100, 3: 10_000, 4: 10_000}

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "LOG_DIR", log_dir)
    monkeypatch.setattr(backup_service, "BACKUP_LOCAL_DIR", backup_dir)
    monkeypatch.setattr(backup_service, "BACKUP_RESTORE_MIN_FREE_BYTES", 10)
    monkeypatch.setattr(backup_service, "_configured_local_file_storage_path", lambda: None)
    monkeypatch.setattr(
        backup_service,
        "_filesystem_capacity_identity",
        lambda path: filesystem_ids[Path(path)],
    )
    monkeypatch.setattr(
        backup_service,
        "_filesystem_free_bytes",
        lambda path: free_bytes[filesystem_ids[Path(path)]],
    )

    checks = backup_service._build_restore_filesystem_checks(
        restore_workspace=workspace,
        postgres_plain_sql_restore_bytes=50,
        app_data_restore_bytes=100,
        app_logs_restore_bytes=20,
        external_file_storage_restore_bytes=0,
        in_place_overhead_bytes=40,
    )

    data_check = next(
        check
        for check in checks
        if "application_data" in check["components"]
    )
    assert data_check["filesystem_id"] == 2
    assert data_check["required_bytes"] == 125
    assert data_check["free_bytes"] == 100
    assert data_check["ok"] is False
    assert any(check["filesystem_id"] == 1 and check["ok"] for check in checks)


def test_restore_capacity_sums_targets_that_share_a_mount(tmp_path, monkeypatch):
    """Data and logs coexist after replacement and must share one byte budget."""
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    backup_dir = tmp_path / "backups"
    for directory in (workspace, data_dir, log_dir, backup_dir):
        directory.mkdir()

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "LOG_DIR", log_dir)
    monkeypatch.setattr(backup_service, "BACKUP_LOCAL_DIR", backup_dir)
    monkeypatch.setattr(backup_service, "BACKUP_RESTORE_MIN_FREE_BYTES", 10)
    monkeypatch.setattr(backup_service, "_configured_local_file_storage_path", lambda: None)
    monkeypatch.setattr(backup_service, "_filesystem_capacity_identity", lambda path: 1)
    monkeypatch.setattr(backup_service, "_filesystem_free_bytes", lambda path: 1_000)

    checks = backup_service._build_restore_filesystem_checks(
        restore_workspace=workspace,
        postgres_plain_sql_restore_bytes=0,
        app_data_restore_bytes=100,
        app_logs_restore_bytes=50,
        external_file_storage_restore_bytes=0,
        in_place_overhead_bytes=0,
    )

    assert checks == [
        {
            "phase": "filesystem",
            "filesystem_id": 1,
            "components": {
                "application_data": 100,
                "application_logs": 50,
            },
            "payload_bytes": 150,
            "safety_margin_bytes": 37,
            "required_bytes": 187,
            "free_bytes": 1_000,
            "ok": True,
        }
    ]
