from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backups import service as backup_service
from app.backups.models import BackupArtifact, BackupJob
from app.database import Base


def _session():
    """Create the minimal catalog database used by restore reconciliation."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[BackupJob.__table__, BackupArtifact.__table__],
    )
    return sessionmaker(bind=engine)()


def _replace_with_in_archive_running_state(db, job_id: str) -> None:
    """Simulate the catalog state contained in a backup's database dump."""
    for artifact in db.query(BackupArtifact).filter(BackupArtifact.backup_job_id == job_id).all():
        db.delete(artifact)
    job = db.query(BackupJob).filter(BackupJob.id == job_id).one()
    job.status = "running"
    job.error = None
    job.manifest_json = None
    job.size_bytes = None
    job.finished_at = None
    db.commit()


@pytest.mark.parametrize(
    "unsafe_job_id",
    [
        "../../outside-archive",
        "/absolute/outside-archive",
        "windows\\outside-archive",
    ],
)
def test_recovery_artifact_rejects_unsafe_manifest_job_id(
    tmp_path,
    monkeypatch,
    unsafe_job_id: str,
):
    """Manifest-controlled job IDs cannot choose a recovery copy destination."""
    archive_root = tmp_path / "archives"
    source = tmp_path / "copied-source.tar.zst"
    source.write_bytes(b"valid-backup-archive")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archive_root)

    checksum = backup_service._sha256_file(source)
    with pytest.raises(RuntimeError, match="safe path component"):
        backup_service._durable_recovery_artifact_uri(
            source,
            backup_job_id=unsafe_job_id,
            checksum_sha256=checksum,
        )

    assert not (tmp_path / "outside-archive").exists()


def test_recovery_artifact_rejects_recovery_directory_symlink_escape(
    tmp_path,
    monkeypatch,
):
    """The resolved copy target must remain below the configured archive root."""
    archive_root = tmp_path / "archives"
    outside_root = tmp_path / "outside"
    source = tmp_path / "copied-source.tar.zst"
    archive_root.mkdir()
    outside_root.mkdir()
    source.write_bytes(b"valid-backup-archive")
    (archive_root / "recovery").symlink_to(outside_root, target_is_directory=True)
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archive_root)

    with pytest.raises(RuntimeError, match="escapes the backup archive directory"):
        backup_service._durable_recovery_artifact_uri(
            source,
            backup_job_id="source-job",
            checksum_sha256=backup_service._sha256_file(source),
        )

    assert list(outside_root.iterdir()) == []


def test_source_backup_remains_verifiable_by_job_id_after_repeated_restores(
    tmp_path,
    monkeypatch,
):
    """A source job is terminal and usable after every restore of its own dump."""
    db = _session()
    archive_root = tmp_path / "archives"
    copied_source = tmp_path / "copied-source.tar.zst"
    copied_source.write_bytes(b"valid-backup-archive")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archive_root)

    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    manifest = {
        "format": "omlorix-backup-v1",
        "generated_at": created_at.isoformat(),
        "backup_job_id": "source-job",
        "trigger_type": "manual",
    }
    db.add(
        BackupJob(
            id="source-job",
            trigger_type="manual",
            status="success",
            manifest_json=manifest,
            options={"encryption_enabled": True},
            size_bytes=copied_source.stat().st_size,
            started_at=created_at,
            finished_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.commit()

    first_context = backup_service._snapshot_backup_catalog_for_recovery(
        db,
        source_uri="file:///restore/input",
        source_path=copied_source,
        manifest=manifest,
    )
    _replace_with_in_archive_running_state(db, "source-job")
    # Even if an older archive contains stale metadata for the same job, it
    # must not sort ahead of the recovery copy and break job-ID verification.
    db.add(
        BackupArtifact(
            id="stale-artifact",
            backup_job_id="source-job",
            storage_uri="local://missing-archive.tar.zst",
            checksum_sha256="0" * 64,
            bytes=1,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    backup_service._reconcile_backup_catalog_after_restore(db, [first_context])

    first_verification = backup_service.verify_backup_job(db, "source-job")
    first_artifact = db.query(BackupArtifact).filter_by(backup_job_id="source-job").one()
    assert first_verification["ok"] is True
    assert first_artifact.storage_uri.startswith("local://recovery/")
    assert db.query(BackupJob).filter_by(id="source-job").one().status == "success"

    # A second job-ID restore resolves the durable recovery artifact, then the
    # archived database again replaces the row with its old running/no-artifact
    # state.  Reconciliation must remain idempotent.
    second_source = backup_service._materialize_source_artifact(
        db,
        first_artifact.storage_uri,
        "second-restore",
    )
    second_context = backup_service._snapshot_backup_catalog_for_recovery(
        db,
        source_uri=first_artifact.storage_uri,
        source_path=second_source,
        manifest=manifest,
    )
    _replace_with_in_archive_running_state(db, "source-job")
    backup_service._reconcile_backup_catalog_after_restore(db, [second_context])

    assert backup_service.verify_backup_job(db, "source-job")["ok"] is True
    assert db.query(BackupJob).filter_by(id="source-job").one().status == "success"
    assert db.query(BackupArtifact).filter_by(backup_job_id="source-job").count() == 1


def test_pre_restore_safety_backup_is_reinserted_with_verified_artifact(
    tmp_path,
    monkeypatch,
):
    """The safety archive remains cataloged after the source schema replaces it."""
    db = _session()
    archive_root = tmp_path / "archives"
    safety_archive = archive_root / "safety.tar.zst"
    safety_archive.parent.mkdir(parents=True)
    safety_archive.write_bytes(b"pre-restore-safety-backup")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", archive_root)

    created_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
    manifest = {
        "format": "omlorix-backup-v1",
        "generated_at": created_at.isoformat(),
        "backup_job_id": "pre-restore-job",
        "trigger_type": "pre_restore",
    }
    db.add(
        BackupJob(
            id="pre-restore-job",
            trigger_type="pre_restore",
            status="success",
            manifest_json=manifest,
            options={"pre_restore": True, "encryption_enabled": True},
            size_bytes=safety_archive.stat().st_size,
            started_at=created_at,
            finished_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.commit()

    context = backup_service._snapshot_backup_catalog_for_recovery(
        db,
        source_uri="local://safety.tar.zst",
        source_path=safety_archive,
        manifest=manifest,
    )

    # The restored source database may not contain the safety job at all.
    db.query(BackupJob).filter_by(id="pre-restore-job").delete()
    db.commit()
    backup_service._reconcile_backup_catalog_after_restore(db, [context])

    safety_job = db.query(BackupJob).filter_by(id="pre-restore-job").one()
    assert safety_job.status == "success"
    assert safety_job.trigger_type == "pre_restore"
    assert backup_service.verify_backup_job(db, safety_job.id)["ok"] is True
