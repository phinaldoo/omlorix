from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

if not getattr(sys.modules.get("zstandard"), "__file__", None):
    sys.modules.pop("zstandard", None)
_REAL_ZSTANDARD = importlib.import_module("zstandard")

from app.backups import service as backup_service  # noqa: E402


def test_encrypted_archive_stream_never_materializes_plaintext_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(backup_service, "zstandard", _REAL_ZSTANDARD)
    source = tmp_path / "source"
    output = tmp_path / "output"
    extracted = tmp_path / "extracted"
    source.mkdir()
    output.mkdir()
    (source / "private.txt").write_text("private backup payload", encoding="utf-8")
    encrypted = output / "backup.tar.zst.enc"

    backup_service._create_encrypted_zstd_archive(
        source,
        encrypted,
        passphrase="long-test-passphrase",
    )

    assert encrypted.stat().st_mode & 0o777 == 0o600
    assert list(output.iterdir()) == [encrypted]
    assert b"private backup payload" not in encrypted.read_bytes()

    compressed = tmp_path / "restored.tar.zst"
    backup_service._decrypt_archive_file(
        encrypted,
        compressed,
        passphrase="long-test-passphrase",
    )
    backup_service._extract_zstd_archive(compressed, extracted)
    assert (extracted / "private.txt").read_text(encoding="utf-8") == (
        "private backup payload"
    )


def test_encrypted_archive_failure_removes_partial_ciphertext(monkeypatch, tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "private.txt").write_text("private backup payload", encoding="utf-8")
    encrypted = output / "backup.tar.zst.enc"

    def fail_after_write(_source: Path, writer) -> None:
        writer.write(b"private backup payload")
        raise RuntimeError("simulated compressor failure")

    monkeypatch.setattr(backup_service, "_write_zstd_archive", fail_after_write)

    with pytest.raises(RuntimeError, match="compressor failure"):
        backup_service._create_encrypted_zstd_archive(
            source,
            encrypted,
            passphrase="long-test-passphrase",
        )

    assert list(output.iterdir()) == []


def test_maintenance_removes_abandoned_private_backup_work(monkeypatch, tmp_path):
    staging = tmp_path / "staging"
    cache = tmp_path / "cache"
    archives = tmp_path / "archives"
    for path in (staging, cache, archives):
        path.mkdir()
    abandoned_stage = staging / "job-1"
    abandoned_stage.mkdir()
    (abandoned_stage / "main.dump").write_text("private", encoding="utf-8")
    abandoned_decryption = cache / ".decrypted-backup.tmp"
    abandoned_decryption.write_text("private", encoding="utf-8")
    abandoned_job_download = cache / "f38e1819-882c-4db0-ae44-e5408f9b5402.tar.zst"
    abandoned_job_download.write_text("private", encoding="utf-8")
    abandoned_verification = cache / "verify-20260830150100.tar.zst"
    abandoned_verification.write_text("private", encoding="utf-8")
    abandoned_ciphertext = archives / ".backup.tar.zst.enc.token.tmp"
    abandoned_ciphertext.write_text("ciphertext", encoding="utf-8")
    recent = cache / "decrypted-recent.tar.zst"
    recent.write_text("recent", encoding="utf-8")
    old_timestamp = time.time() - (25 * 60 * 60)
    for path in (
        abandoned_stage,
        abandoned_decryption,
        abandoned_job_download,
        abandoned_verification,
        abandoned_ciphertext,
    ):
        os.utime(path, (old_timestamp, old_timestamp))

    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", staging)
    monkeypatch.setattr(backup_service, "BACKUP_DOWNLOAD_CACHE_DIR", cache)
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archives)

    assert backup_service.cleanup_stale_backup_work_files(
        retention_hours=24,
    ) == 5
    assert not abandoned_stage.exists()
    assert not abandoned_decryption.exists()
    assert not abandoned_job_download.exists()
    assert not abandoned_verification.exists()
    assert not abandoned_ciphertext.exists()
    assert recent.exists()


def test_remote_artifact_verification_removes_materialized_cache(monkeypatch, tmp_path):
    payload = b"remote-backup"
    cached = tmp_path / "artifact-1.tar.zst"
    artifact = SimpleNamespace(
        id="artifact-1",
        storage_uri="s3://private/backup.tar.zst",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
    )
    verified = []

    monkeypatch.setattr(backup_service, "BACKUP_DOWNLOAD_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        backup_service,
        "get_backup_artifact",
        lambda _db, artifact_id: artifact if artifact_id == artifact.id else None,
    )

    def materialize(_db, _source_uri, _job_id):
        cached.write_bytes(payload)
        return cached

    monkeypatch.setattr(backup_service, "_materialize_source_artifact", materialize)
    monkeypatch.setattr(
        backup_service,
        "mark_backup_artifact_verified",
        lambda _db, artifact_id: verified.append(artifact_id),
    )

    result = backup_service.verify_backup_artifact(object(), artifact.id)

    assert result["ok"] is True
    assert verified == [artifact.id]
    assert not cached.exists()


def test_concurrent_remote_verifications_use_independent_cache_paths(
    monkeypatch,
    tmp_path,
):
    materialized_paths = []
    monkeypatch.setattr(backup_service, "BACKUP_DOWNLOAD_CACHE_DIR", tmp_path)

    def download(_db, _source_uri, target_path):
        target_path.write_bytes(b"remote-backup")
        materialized_paths.append(target_path)
        return target_path

    monkeypatch.setattr(backup_service, "_download_remote_artifact", download)

    with backup_service._verification_source_artifact(
        object(),
        "s3://private/backup.tar.zst",
        "verify-fixed",
    ) as first_path:
        with backup_service._verification_source_artifact(
            object(),
            "s3://private/backup.tar.zst",
            "verify-fixed",
        ) as second_path:
            assert first_path != second_path
            assert first_path.exists()
            assert second_path.exists()

        assert first_path.exists()
        assert not second_path.exists()

    assert len(materialized_paths) == 2
    assert not first_path.exists()
