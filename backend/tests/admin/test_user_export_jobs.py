import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.user_exports.jobs import utils as user_export_jobs
from app.admin.export_jobs.schemas import AdminUserExportJobCreateRequest
from app.admin.export_jobs.models import AdminUserExportJob, create_admin_user_export_job, get_admin_user_export_job
from app.database import Base
from app.workers.models import DurableWorkerJob


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[AdminUserExportJob.__table__, DurableWorkerJob.__table__],
    )
    return sessionmaker(bind=engine)()


def test_admin_export_catalog_and_queue_commit_atomically(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(user_export_jobs, "ADMIN_USER_EXPORT_DIR", tmp_path)

    job = user_export_jobs.create_and_enqueue_admin_user_export_job(
        db,
        requested_by_user_id="admin-1",
        reason="Account migration",
        user_ids=["user-2"],
    )

    assert db.query(AdminUserExportJob).filter_by(id=job.id).one().status == "queued"
    queue_job = db.query(DurableWorkerJob).one()
    assert queue_job.kind == "admin_user_export"
    assert queue_job.payload == {"export_job_id": job.id}


def test_admin_export_enqueue_failure_rolls_back_catalog(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(user_export_jobs, "ADMIN_USER_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(
        user_export_jobs,
        "_enqueue_admin_user_export_job_in_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("queue unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        user_export_jobs.create_and_enqueue_admin_user_export_job(
            db,
            requested_by_user_id="admin-1",
            reason="Account migration",
        )

    assert db.query(AdminUserExportJob).count() == 0
    assert db.query(DurableWorkerJob).count() == 0


def test_admin_user_export_job_lifecycle(tmp_path, monkeypatch):
    db = _session()
    monkeypatch.setattr(user_export_jobs, "ADMIN_USER_EXPORT_DIR", tmp_path)
    audit_calls = []
    monkeypatch.setattr(user_export_jobs, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    def fake_export(_db, _db_log, target_path, *, user_ids=None):
        assert user_ids == ["user-2"]
        with zipfile.ZipFile(target_path, "w") as archive:
            archive.writestr("manifest.json", '{"ok":true}')
        return "admin-users-test.zip", {"user_count": 2, "user_files_count": 1}

    monkeypatch.setattr(user_export_jobs, "export_admin_users_archive_to_path", fake_export)

    job = create_admin_user_export_job(
        db,
        requested_by_user_id="admin-1",
        options_json={
            "scope": "selected",
            "user_ids": ["user-2"],
            "reason": "Account migration",
        },
    )
    completed = user_export_jobs._run_admin_user_export_job_with_sessions(db, object(), job.id)

    assert completed.status == "success"
    assert completed.filename == "admin-users-test.zip"
    assert completed.size_bytes and completed.size_bytes > 0
    assert completed.manifest_json["user_count"] == 2
    assert completed.options_json["user_ids"] == ["user-2"]
    assert completed.expires_at is not None
    assert audit_calls[0]["action"] == "EXPORT_USERS_ADMIN_JOB_COMPLETED"
    assert audit_calls[0]["details"]["user_count"] == 2

    path, filename = user_export_jobs.materialize_admin_user_export_job(db, job.id)
    assert path.exists()
    assert filename == "admin-users-test.zip"

    result = user_export_jobs.delete_admin_user_export_job_artifact(db, job.id)
    deleted = get_admin_user_export_job(db, job.id)
    assert result == {"status": "success", "job_id": job.id}
    assert deleted.status == "deleted"
    assert not path.exists()


def test_admin_user_export_request_rejects_blank_user_ids():
    """Blank selections cannot create scope and audit-count disagreements."""
    with pytest.raises(ValidationError):
        AdminUserExportJobCreateRequest(
            reason="Account migration",
            user_ids=["   "],
        )


def test_admin_user_export_worker_normalizes_persisted_selected_ids(
    tmp_path, monkeypatch
):
    """Persisted options cannot produce blank or duplicate selections."""
    db = _session()
    monkeypatch.setattr(user_export_jobs, "ADMIN_USER_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(user_export_jobs, "create_audit_log", lambda **kwargs: None)

    def fake_export(_db, _db_log, target_path, *, user_ids=None):
        assert user_ids == ["user-2", "user-3"]
        with zipfile.ZipFile(target_path, "w") as archive:
            archive.writestr("manifest.json", '{"ok":true}')
        return "admin-users-test.zip", {"user_count": 2, "user_files_count": 0}

    monkeypatch.setattr(
        user_export_jobs,
        "export_admin_users_archive_to_path",
        fake_export,
    )
    job = create_admin_user_export_job(
        db,
        requested_by_user_id="admin-1",
        options_json={
            "scope": "selected",
            "user_ids": ["", " user-2 ", "user-2", None, "user-3"],
            "reason": "Account migration",
        },
    )

    completed = user_export_jobs._run_admin_user_export_job_with_sessions(
        db, object(), job.id
    )

    assert completed.status == "success"


def test_admin_user_export_worker_fails_closed_for_blank_selected_scope(
    tmp_path, monkeypatch
):
    """A malformed selected job must never broaden itself to all users."""
    db = _session()
    monkeypatch.setattr(user_export_jobs, "ADMIN_USER_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(user_export_jobs, "create_audit_log", lambda **kwargs: None)
    monkeypatch.setattr(
        user_export_jobs,
        "export_admin_users_archive_to_path",
        lambda *_args, **_kwargs: pytest.fail("malformed selection must not export"),
    )
    job = create_admin_user_export_job(
        db,
        requested_by_user_id="admin-1",
        options_json={
            "scope": "selected",
            "user_ids": ["", "   ", None],
            "reason": "Account migration",
        },
    )

    failed = user_export_jobs._run_admin_user_export_job_with_sessions(
        db, object(), job.id
    )

    assert failed.status == "failed"
    assert failed.error == "Selected user export contains no valid user IDs"
