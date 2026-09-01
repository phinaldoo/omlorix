from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backups import router as backup_router
from app.backups import service as backup_service
from app.backups.models import BackupJob
from app.database import Base
from app.dependencies import get_db, get_db_log, verified_admin


def _backup_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(backup_router.backups_router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_db_log] = lambda: object()
    app.dependency_overrides[verified_admin] = lambda: SimpleNamespace(id="admin-1")
    return TestClient(app)


def test_plaintext_policy_rejects_api_request_before_job_creation(
    monkeypatch,
):
    """The HTTP boundary must not persist or enqueue a known-impossible job."""
    monkeypatch.setenv(
        backup_service.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV,
        "configured-test-passphrase",
    )
    monkeypatch.setattr(backup_service, "BACKUP_ALLOW_PLAINTEXT_ARCHIVES", False)
    created_jobs = []
    enqueued_jobs = []
    monkeypatch.setattr(
        backup_router,
        "create_backup_job",
        lambda *args, **kwargs: created_jobs.append((args, kwargs)),
    )
    monkeypatch.setattr(
        backup_router,
        "enqueue_backup_job",
        lambda job_id: enqueued_jobs.append(job_id),
    )

    with _backup_test_client() as client:
        response = client.post(
            "/api/v1/admin/backups/create",
            json={"destination_id": None, "encryption_enabled": False},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "backup_plaintext_archives_disabled"}
    }
    assert created_jobs == []
    assert enqueued_jobs == []


def test_encrypted_api_request_still_creates_and_enqueues_backup(
    monkeypatch,
):
    """Requiring encryption must preserve the configured encrypted path."""
    monkeypatch.setenv(
        backup_service.BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV,
        "configured-test-passphrase",
    )
    monkeypatch.setattr(backup_service, "BACKUP_ALLOW_PLAINTEXT_ARCHIVES", False)
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(id="encrypted-job")
    captured = {"created": [], "enqueued": []}

    def create_job(*args, **kwargs):
        captured["created"].append((args, kwargs))
        return job

    monkeypatch.setattr(backup_router, "create_backup_job", create_job)
    monkeypatch.setattr(
        backup_router,
        "enqueue_backup_job",
        lambda job_id: captured["enqueued"].append(job_id),
    )
    monkeypatch.setattr(backup_router, "_audit", lambda **kwargs: None)
    monkeypatch.setattr(
        backup_router,
        "build_backup_job_response",
        lambda db, row: {
            "id": row.id,
            "trigger_type": "manual",
            "status": "queued",
            "error": None,
            "manifest_json": None,
            "options": {"encryption_enabled": True},
            "size_bytes": None,
            "requested_by_user_id": "admin-1",
            "destination_id": None,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
            "artifacts": [],
        },
    )

    with _backup_test_client() as client:
        response = client.post(
            "/api/v1/admin/backups/create",
            json={"destination_id": None, "encryption_enabled": True},
        )

    assert response.status_code == 200
    assert response.json()["id"] == "encrypted-job"
    assert captured["created"][0][1]["options"] == {"encryption_enabled": True}
    assert captured["enqueued"] == ["encrypted-job"]


def test_plaintext_policy_rejection_leaves_no_nonterminal_backup_job(
    tmp_path,
    monkeypatch,
):
    """A policy error after reservation must turn the queued job into failed."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[BackupJob.__table__])
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    db.add(
        BackupJob(
            id="plaintext-policy-job",
            trigger_type="manual",
            status="queued",
            options={"encryption_enabled": False},
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    monkeypatch.setattr(backup_service, "BACKUP_ALLOW_PLAINTEXT_ARCHIVES", False)
    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", tmp_path / "staging")

    result = backup_service._run_backup_job_with_session(db, "plaintext-policy-job")

    assert result.status == "failed"
    assert result.finished_at is not None
    assert "Plaintext backup archives are disabled" in result.error
    assert db.query(BackupJob).filter(BackupJob.status.in_(["queued", "running"])).count() == 0
