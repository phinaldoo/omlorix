from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.backups import service as backup_service  # noqa: E402


@contextmanager
def _noop_context(*args, **kwargs):
    yield


@contextmanager
def _prepared_archive(path):
    yield path


def test_in_place_restore_aborts_without_pre_restore_artifact(monkeypatch):
    restore_job = SimpleNamespace(
        id="restore-job-id",
        source_uri="local://restore.tar.zst",
        target_mode="in_place",
        requested_by_user_id="user-id",
        confirmed_by_user_id="user-id",
        options={},
        created_at=datetime.now(timezone.utc),
        started_at=None,
        status="queued",
        error=None,
        preflight_json=None,
    )
    pre_restore_job = SimpleNamespace(id="pre-restore-job-id", status="success")
    restore_called = False
    recorded_catalog_contexts = None

    def get_restore_job(db, restore_job_id):
        return restore_job

    def update_restore_job_status(db, *, restore_job_id, status, error=None, preflight_json=None):
        restore_job.status = status
        restore_job.error = error
        restore_job.preflight_json = preflight_json
        return restore_job

    def restore_from_archive(*args, **kwargs):
        nonlocal restore_called
        restore_called = True
        raise AssertionError("live restore should not run without a verified pre-restore artifact")

    monkeypatch.setattr(backup_service, "get_restore_job", get_restore_job)
    monkeypatch.setattr(backup_service, "update_restore_job_status", update_restore_job_status)
    monkeypatch.setattr(backup_service, "distributed_lock", _noop_context)
    monkeypatch.setattr(backup_service, "_materialize_source_artifact", lambda db, uri, job_id: Path("/tmp/restore.tar.zst"))
    monkeypatch.setattr(backup_service, "_prepare_archive_for_restore", _prepared_archive)
    monkeypatch.setattr(
        backup_service,
        "preflight_backup_archive",
        lambda path, target_mode, db: {
            "ok": True,
            "manifest": {"backup_job_id": "source-backup-id"},
        },
    )
    monkeypatch.setattr(
        backup_service,
        "_snapshot_backup_catalog_for_recovery",
        lambda *args, **kwargs: SimpleNamespace(id="source-backup-id"),
    )
    monkeypatch.setattr(backup_service, "_create_pre_restore_backup", lambda db, requested_by_user_id: pre_restore_job)
    monkeypatch.setattr(backup_service, "list_backup_artifacts", lambda db, backup_job_id: [])
    monkeypatch.setattr(backup_service, "_restore_from_archive", restore_from_archive)
    monkeypatch.setattr(backup_service, "_release_restore_session", lambda db: None)
    def record_restore_terminal_status(
        context,
        *,
        status,
        error,
        preflight_json,
        backup_catalog_contexts=None,
    ):
        nonlocal recorded_catalog_contexts
        recorded_catalog_contexts = backup_catalog_contexts
        return SimpleNamespace(
            status=status,
            error=error,
            preflight_json=preflight_json,
        )

    monkeypatch.setattr(
        backup_service,
        "_record_restore_terminal_status",
        record_restore_terminal_status,
    )

    result = backup_service._run_restore_job_with_session(object(), restore_job.id)

    assert result.status == "failed"
    assert result.error == "In-place restore requires a pre-restore backup artifact before mutating live state."
    assert result.preflight_json["recovery"] == {
        "state": "not_started",
        "safe_to_restart": True,
    }
    assert [context.id for context in recorded_catalog_contexts] == ["source-backup-id"]
    assert restore_called is False
