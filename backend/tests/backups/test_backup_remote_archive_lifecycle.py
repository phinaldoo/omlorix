from __future__ import annotations

import shutil
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backups import service as backup_service
from app.backups.models import BackupArtifact, BackupDestination, BackupJob
from app.database import Base


def _backup_session(*, job_id: str, provider: str):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BackupDestination.__table__,
            BackupJob.__table__,
            BackupArtifact.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    destination_id = f"{provider}-destination"
    db.add(
        BackupDestination(
            id=destination_id,
            name=f"{provider} destination",
            provider=provider,
            config_encrypted={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        BackupJob(
            id=job_id,
            trigger_type="manual",
            status="queued",
            options={"encryption_enabled": True},
            destination_id=destination_id,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return db, engine


def _configure_backup_runtime(
    monkeypatch,
    tmp_path: Path,
    *,
    provider: str,
    adapter,
) -> tuple[Path, Path]:
    staging_root = tmp_path / "staging"
    archive_root = tmp_path / "archives"
    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", staging_root)
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archive_root)
    monkeypatch.setenv(
        backup_service.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV,
        "long-test-passphrase",
    )
    monkeypatch.setattr(
        backup_service,
        "_resolve_destination",
        lambda _db, _destination_id: (SimpleNamespace(provider=provider), {}),
    )
    monkeypatch.setattr(
        backup_service,
        "_resolve_adapter_for_destination",
        lambda _destination, _config: adapter,
    )
    monkeypatch.setattr(
        backup_service,
        "distributed_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(backup_service, "activate_write_freeze", lambda **_kwargs: None)
    monkeypatch.setattr(backup_service, "deactivate_write_freeze", lambda: None)

    def create_components(staging_dir: Path) -> dict[str, Path]:
        dump_path = staging_dir / "db" / "main.dump"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_bytes(b"database snapshot")
        return {"main_dump": dump_path}

    def create_archive(
        _staging_dir: Path,
        target_path: Path,
        *,
        passphrase: str,
    ) -> None:
        assert passphrase == "long-test-passphrase"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"encrypted backup archive")

    monkeypatch.setattr(backup_service, "_create_backup_components", create_components)
    monkeypatch.setattr(
        backup_service,
        "_create_encrypted_zstd_archive",
        create_archive,
    )
    return staging_root, archive_root


@pytest.mark.parametrize(
    ("provider", "storage_uri"),
    [
        ("s3", "s3://backups/archive.tar.zst.enc"),
        ("gcs", "gs://backups/archive.tar.zst.enc"),
        ("azure", "azure://backups/archive.tar.zst.enc"),
        ("webdav", "webdav://backups/archive.tar.zst.enc"),
    ],
)
def test_remote_backup_removes_local_archive_after_success(
    monkeypatch,
    tmp_path,
    provider,
    storage_uri,
):
    job_id = f"remote-success-{provider}"
    db, engine = _backup_session(job_id=job_id, provider=provider)
    uploaded_paths: list[Path] = []

    def upload_file(local_path: Path, _remote_path: str) -> str:
        assert local_path.is_file()
        uploaded_paths.append(local_path)
        return storage_uri

    adapter = SimpleNamespace(upload_file=upload_file)
    staging_root, archive_root = _configure_backup_runtime(
        monkeypatch,
        tmp_path,
        provider=provider,
        adapter=adapter,
    )

    try:
        result = backup_service._run_backup_job_with_session(db, job_id)

        assert result.status == "success"
        assert len(uploaded_paths) == 1
        assert uploaded_paths[0].parent == staging_root / job_id
        assert not uploaded_paths[0].exists()
        assert not (staging_root / job_id).exists()
        assert not (archive_root / f"{job_id}.tar.zst.enc").exists()
        artifact = db.query(BackupArtifact).one()
        assert artifact.storage_uri == storage_uri
        assert artifact.verified_at is not None
    finally:
        db.close()
        engine.dispose()


def test_remote_backup_removes_local_archive_after_upload_failure(monkeypatch, tmp_path):
    job_id = "remote-upload-failure"
    db, engine = _backup_session(job_id=job_id, provider="s3")
    uploaded_paths: list[Path] = []

    def upload_file(local_path: Path, _remote_path: str) -> str:
        assert local_path.is_file()
        uploaded_paths.append(local_path)
        raise RuntimeError("simulated remote upload failure")

    staging_root, archive_root = _configure_backup_runtime(
        monkeypatch,
        tmp_path,
        provider="s3",
        adapter=SimpleNamespace(upload_file=upload_file),
    )

    try:
        result = backup_service._run_backup_job_with_session(db, job_id)

        assert result.status == "failed"
        assert len(uploaded_paths) == 1
        assert not uploaded_paths[0].exists()
        assert not (staging_root / job_id).exists()
        assert not (archive_root / f"{job_id}.tar.zst.enc").exists()
        assert db.query(BackupArtifact).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_backup_fails_when_stale_work_tree_cannot_be_removed(monkeypatch, tmp_path):
    job_id = "stale-work-cleanup-failure"
    db, engine = _backup_session(job_id=job_id, provider="s3")
    uploaded_paths: list[Path] = []

    def upload_file(local_path: Path, _remote_path: str) -> str:
        uploaded_paths.append(local_path)
        return "s3://backups/archive.tar.zst.enc"

    staging_root, _archive_root = _configure_backup_runtime(
        monkeypatch,
        tmp_path,
        provider="s3",
        adapter=SimpleNamespace(upload_file=upload_file),
    )
    work_dir = staging_root / job_id
    stale_file = work_dir / "contents" / "stale.dump"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_bytes(b"stale backup data")
    real_rmtree = backup_service.shutil.rmtree

    def fail_work_tree_removal(path, *args, **kwargs):
        if Path(path) == work_dir:
            if kwargs.get("ignore_errors", False):
                return None
            raise OSError("simulated stale work-tree cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_service.shutil, "rmtree", fail_work_tree_removal)

    try:
        result = backup_service._run_backup_job_with_session(db, job_id)

        assert result.status == "failed"
        assert uploaded_paths == []
        assert stale_file.read_bytes() == b"stale backup data"
        assert db.query(BackupArtifact).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_local_backup_keeps_durable_archive(monkeypatch, tmp_path):
    job_id = "local-success"
    db, engine = _backup_session(job_id=job_id, provider="local")
    uploaded_paths: list[Path] = []

    def upload_file(local_path: Path, remote_path: str) -> str:
        uploaded_paths.append(local_path)
        destination_path = archive_root / remote_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination_path)
        return f"local://{remote_path}"

    staging_root, archive_root = _configure_backup_runtime(
        monkeypatch,
        tmp_path,
        provider="local",
        adapter=SimpleNamespace(upload_file=upload_file),
    )

    try:
        result = backup_service._run_backup_job_with_session(db, job_id)

        assert result.status == "success"
        assert uploaded_paths == [archive_root / f"{job_id}.tar.zst.enc"]
        assert uploaded_paths[0].is_file()
        assert not (staging_root / job_id).exists()
        artifact = db.query(BackupArtifact).one()
        stored_path = archive_root / artifact.storage_uri.removeprefix("local://")
        assert stored_path.is_file()
    finally:
        db.close()
        engine.dispose()
