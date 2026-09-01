from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.backups import router as backup_router  # noqa: E402
from app.backups import service as backup_service  # noqa: E402
from app.backups.models import (  # noqa: E402
    BackupArtifact,
    BackupJob,
    paginate_backup_jobs,
)
from app.database import Base  # noqa: E402


def _session():
    """Create the smallest database needed by backup-history paging tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[BackupJob.__table__, BackupArtifact.__table__],
    )
    return sessionmaker(bind=engine)()


def _backup_job(offset: int, *, status: str | None = None) -> BackupJob:
    """Build a deterministic newest-first backup-history row."""
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=offset)
    return BackupJob(
        id=f"job-{offset:02d}",
        trigger_type="manual",
        status=status or ("success" if offset % 2 == 0 else "failed"),
        created_at=created_at,
        updated_at=created_at,
    )


def _populate_backup_history(db, count: int = 13) -> None:
    """Insert jobs and one artifact per job for page and hydration assertions."""
    jobs = [_backup_job(offset) for offset in range(count)]
    db.add_all(jobs)
    db.flush()
    db.add_all(
        [
            BackupArtifact(
                id=f"artifact-{offset:02d}",
                backup_job_id=f"job-{offset:02d}",
                storage_uri=f"local://backup-{offset:02d}.tar.zst",
                checksum_sha256=f"{offset:064x}",
                bytes=1000 + offset,
                created_at=job.created_at,
            )
            for offset, job in enumerate(jobs)
        ]
    )
    db.commit()


def test_paginate_backup_jobs_filters_before_paging_and_clamps_stale_pages():
    """Filtering, ordering, totals, and stale final pages remain deterministic."""
    db = _session()
    _populate_backup_history(db)

    filtered, total, total_pages, resolved_page = paginate_backup_jobs(
        db,
        page=2,
        page_size=3,
        status="success",
    )
    assert [job.id for job in filtered] == ["job-06", "job-04", "job-02"]
    assert (total, total_pages, resolved_page) == (7, 3, 2)

    clamped, total, total_pages, resolved_page = paginate_backup_jobs(
        db,
        page=99,
        page_size=5,
    )
    assert [job.id for job in clamped] == ["job-02", "job-01", "job-00"]
    assert (total, total_pages, resolved_page) == (13, 3, 3)


def test_backup_artifacts_use_id_as_deterministic_newest_tie_breaker():
    """Restore callers receive one stable newest-first artifact ordering."""
    from app.backups.models import list_backup_artifacts

    db = _session()
    job = _backup_job(1, status="success")
    db.add(job)
    db.flush()
    db.add_all(
        [
            BackupArtifact(
                id=artifact_id,
                backup_job_id=job.id,
                storage_uri=f"local://{artifact_id}.tar.zst",
                checksum_sha256="0" * 64,
                bytes=100,
                created_at=job.created_at,
            )
            for artifact_id in ("artifact-a", "artifact-b")
        ]
    )
    db.commit()

    artifacts = list_backup_artifacts(db, job.id)

    assert [artifact.id for artifact in artifacts] == ["artifact-b", "artifact-a"]


def test_backup_jobs_route_returns_only_one_page_with_batched_artifacts(monkeypatch):
    """The route hydrates only the requested page and avoids artifact N+1 queries."""
    db = _session()
    _populate_backup_history(db)

    def fail_if_artifacts_are_loaded_per_job(*_args, **_kwargs):
        raise AssertionError("backup history loaded artifacts one job at a time")

    monkeypatch.setattr(
        backup_service,
        "list_backup_artifacts",
        fail_if_artifacts_are_loaded_per_job,
    )

    response = backup_router.list_backup_jobs_route(
        page=2,
        page_size=5,
        status=None,
        db=db,
        admin_user=SimpleNamespace(id="admin-user"),
    )

    assert response.page == 2
    assert response.page_size == 5
    assert response.total == 13
    assert response.total_pages == 3
    assert [job.id for job in response.items] == [
        "job-07",
        "job-06",
        "job-05",
        "job-04",
        "job-03",
    ]
    assert [job.artifacts[0].id for job in response.items] == [
        "artifact-07",
        "artifact-06",
        "artifact-05",
        "artifact-04",
        "artifact-03",
    ]
