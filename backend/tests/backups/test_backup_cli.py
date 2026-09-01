import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys


class FakeCliSession:
    """Fake enough SQLAlchemy session behavior for the backup CLI status refresh."""

    def __init__(self):
        self.expired = False
        self.closed = False

    def expire_all(self):
        self.expired = True

    def close(self):
        self.closed = True


def test_create_expires_session_before_reading_final_status(monkeypatch, capsys):
    """The launcher should see success after the worker updates the job in another session."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    job = SimpleNamespace(id="backup-1")
    artifact = SimpleNamespace(
        storage_uri="local://omlorix-backups/backup-1.tar.zst.enc",
        bytes=2048,
        verified_at="2026-08-01T10:00:00Z",
    )

    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(backup_cli, "create_backup_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(backup_cli, "run_backup_job_sync", lambda job_id: None)
    monkeypatch.setattr(
        backup_cli, "list_backup_artifacts", lambda db, job_id: [artifact]
    )

    def fake_get_backup_job(db, job_id):
        return SimpleNamespace(
            status="success" if db.expired else "queued",
            error=None,
            destination_id="destination-1",
            options={"encryption_enabled": True},
            size_bytes=2048,
        )

    monkeypatch.setattr(backup_cli, "get_backup_job", fake_get_backup_job)

    exit_code = backup_cli._cmd_create(
        SimpleNamespace(destination=None, no_encrypted=False, safe_output=True)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "success"
    assert output["destination_id"] == "destination-1"
    assert output["encryption_enabled"] is True
    assert output["size_bytes"] == 2048
    assert "artifacts" not in output
    assert fake_db.closed is True


def test_managed_cli_backup_runs_in_operations_worker(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    job = SimpleNamespace(id="backup-worker-1")
    queued = []
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(backup_cli, "create_backup_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(backup_cli, "external_operations_enabled", lambda: True)
    monkeypatch.setattr(
        backup_cli,
        "enqueue_backup_job",
        lambda job_id: queued.append(job_id),
    )
    monkeypatch.setattr(
        backup_cli,
        "_wait_for_backup_terminal",
        lambda db, job_id: setattr(db, "expired", True),
    )
    monkeypatch.setattr(
        backup_cli,
        "run_backup_job_sync",
        lambda job_id: (_ for _ in ()).throw(AssertionError("must run in worker")),
    )
    monkeypatch.setattr(backup_cli, "list_backup_artifacts", lambda db, job_id: [])
    monkeypatch.setattr(
        backup_cli,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(
            status="success",
            error=None,
            destination_id=None,
            options={"encryption_enabled": True},
            size_bytes=1,
        ),
    )

    assert backup_cli._cmd_create(
        SimpleNamespace(destination=None, no_encrypted=False, safe_output=True)
    ) == 0
    assert queued == ["backup-worker-1"]
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_options_returns_only_enabled_safe_destination_fields(monkeypatch, capsys):
    """Launcher discovery must never serialize encrypted destination config."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        backup_cli,
        "list_backup_destinations",
        lambda db: [
            SimpleNamespace(
                id="destination-enabled",
                name="Primary S3",
                provider="s3",
                enabled=True,
                config_encrypted={"secret_access_key": "must-not-leak"},
            ),
            SimpleNamespace(
                id="destination-disabled",
                name="Old WebDAV",
                provider="webdav",
                enabled=False,
                config_encrypted={"password": "must-not-leak"},
            ),
        ],
    )
    monkeypatch.setattr(
        backup_cli,
        "get_backup_runtime_capabilities",
        lambda: {
            "archive_encryption_available": True,
            "plaintext_archives_allowed": False,
        },
    )

    exit_code = backup_cli._cmd_options(SimpleNamespace())

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"destination-enabled"' in output
    assert '"destination-disabled"' not in output
    assert '"Primary S3"' in output
    assert "must-not-leak" not in output
    assert '"archive_encryption_available": true' in output
    assert fake_db.closed is True


def test_list_reports_the_clamped_page_size(monkeypatch, capsys):
    """Pagination metadata must describe the bounded query that actually ran."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    captured = {}
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)

    def paginate(db, *, page, page_size):
        captured.update(db=db, page=page, page_size=page_size)
        return [], 0, 0, 1

    monkeypatch.setattr(backup_cli, "paginate_backup_jobs", paginate)

    assert backup_cli._cmd_list(SimpleNamespace(page=1, page_size=1000)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert captured["page_size"] == 100
    assert payload["page_size"] == 100
    assert fake_db.closed is True


def test_download_metadata_preserves_encrypted_filename(monkeypatch, tmp_path, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")
    backup_service = importlib.import_module("app.backups.service")

    archive = tmp_path / "cached.tar.zst"
    archive.write_bytes(backup_service.ENCRYPTED_ARCHIVE_MAGIC + b"archive")
    fake_db = FakeCliSession()
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        backup_cli,
        "materialize_backup_job_artifact",
        lambda db, job_id: (archive, "artifact-1"),
    )

    assert backup_cli._cmd_download(
        SimpleNamespace(job_id="job-1", metadata=True)
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "job_id": "job-1",
        "filename": "omlorix-backup-job-1.tar.zst.enc",
        "bytes": archive.stat().st_size,
    }
    assert fake_db.closed is True


def test_download_streams_exact_archive_bytes(monkeypatch, tmp_path, capsysbinary):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    archive = tmp_path / "backup.tar.zst"
    archive_bytes = b"\x28\xb5\x2f\xfd\x00binary backup\x00\xff"
    archive.write_bytes(archive_bytes)
    fake_db = FakeCliSession()
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        backup_cli,
        "materialize_backup_job_artifact",
        lambda db, job_id: (archive, "artifact-1"),
    )

    assert backup_cli._cmd_download(
        SimpleNamespace(job_id="job-1", metadata=False)
    ) == 0
    captured = capsysbinary.readouterr()
    assert captured.out == archive_bytes
    assert captured.err == b""
    assert fake_db.closed is True


def test_restore_refuses_to_run_without_explicit_offline_mode(monkeypatch, capsys):
    """A live application worker must never be able to start a full restore."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")
    monkeypatch.setattr(
        backup_cli,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    exit_code = backup_cli._cmd_restore(
        SimpleNamespace(
            source="file:///restore/input",
            target="in_place",
            confirm="RESTORE-IN-PLACE",
            offline=False,
        )
    )

    assert exit_code == 2
    assert "requires --offline" in capsys.readouterr().err


def test_offline_restore_reopens_session_before_reading_restored_job(
    monkeypatch, capsys
):
    """The CLI reports terminal state through a post-restore connection."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    creation_db = FakeCliSession()
    result_db = FakeCliSession()
    sessions = iter((creation_db, result_db))
    job = SimpleNamespace(id="restore-1")
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(backup_cli, "create_restore_job", lambda *args, **kwargs: job)

    def run_restore(job_id):
        assert job_id == "restore-1"
        assert creation_db.closed is True

    monkeypatch.setattr(backup_cli, "run_restore_job_sync", run_restore)

    def fake_get_restore_job(db, job_id):
        assert db is result_db
        assert creation_db.closed is True
        return SimpleNamespace(
            status="success",
            error=None,
            preflight_json={
                "ok": True,
                "recovery": {
                    "state": "restored",
                    "safe_to_restart": True,
                },
            },
        )

    monkeypatch.setattr(backup_cli, "get_restore_job", fake_get_restore_job)

    exit_code = backup_cli._cmd_restore(
        SimpleNamespace(
            source="file:///restore/input",
            target="in_place",
            confirm="RESTORE-IN-PLACE",
            offline=True,
        )
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"status": "success"' in output
    assert '"state": "restored"' in output
    assert '"safe_to_restart": true' in output
    assert creation_db.closed is True
    assert result_db.closed is True


def test_restore_accepts_successful_backup_job_without_exposing_artifact_uri(
    monkeypatch, capsys
):
    """A backup job ID should be sufficient for a complete CLI-only restore round trip."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    lookup_db = FakeCliSession()
    creation_db = FakeCliSession()
    result_db = FakeCliSession()
    sessions = iter((lookup_db, creation_db, result_db))
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(
        backup_cli,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(status="success"),
    )
    monkeypatch.setattr(
        backup_cli,
        "list_backup_artifacts",
        lambda db, job_id: [SimpleNamespace(storage_uri="local://private/archive.enc")],
    )
    captured = {}

    def create_restore(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="restore-from-job")

    monkeypatch.setattr(backup_cli, "create_restore_job", create_restore)
    monkeypatch.setattr(backup_cli, "run_restore_job_sync", lambda job_id: None)
    monkeypatch.setattr(
        backup_cli,
        "get_restore_job",
        lambda db, job_id: SimpleNamespace(
            status="success", error=None, preflight_json=None
        ),
    )

    result = backup_cli._cmd_restore(
        SimpleNamespace(
            source=None,
            job_id="backup-1",
            target="empty",
            confirm=None,
            offline=True,
        )
    )

    assert result == 0
    assert captured["source_uri"] == "local://private/archive.enc"
    assert "local://private" not in capsys.readouterr().out


def test_restore_preflight_checks_selected_target_before_shutdown(monkeypatch, capsys):
    """The host coordinator must detect a non-empty target before stopping Omlorix."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    captured = {}
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)

    def verify_source(db, source, *, target_mode):
        captured.update(db=db, source=source, target_mode=target_mode)
        return {
            "ok": False,
            "preflight": {"ok": False, "reason": "target_not_empty"},
        }

    monkeypatch.setattr(backup_cli, "verify_backup_source", verify_source)

    exit_code = backup_cli._cmd_restore_preflight(
        SimpleNamespace(
            source="file:///app/backups/backup.tar.zst",
            job_id=None,
            target="empty",
        )
    )

    assert exit_code == 1
    assert captured == {
        "db": fake_db,
        "source": "file:///app/backups/backup.tar.zst",
        "target_mode": "empty",
    }
    assert (
        json.loads(capsys.readouterr().out)["preflight"]["reason"] == "target_not_empty"
    )
    assert fake_db.closed is True


def test_restore_preflight_job_id_does_not_expose_artifact_uri(monkeypatch, capsys):
    """Job-ID preflight should validate its artifact without printing provider details."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        backup_cli,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(status="success"),
    )
    monkeypatch.setattr(
        backup_cli,
        "list_backup_artifacts",
        lambda db, job_id: [
            SimpleNamespace(storage_uri="s3://private-bucket/archive.enc")
        ],
    )
    monkeypatch.setattr(
        backup_cli,
        "verify_backup_source",
        lambda db, source, *, target_mode: {
            "source_uri": source,
            "ok": True,
            "preflight": {"ok": True, "target_mode": target_mode},
        },
    )

    exit_code = backup_cli._cmd_restore_preflight(
        SimpleNamespace(source=None, job_id="backup-1", target="in_place")
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"backup_job_id": "backup-1"' in output
    assert "private-bucket" not in output
    assert fake_db.closed is True


def test_restore_preflight_reports_unrestorable_job_as_structured_failure(
    monkeypatch, capsys
):
    """An invalid job selection must not escape as an operator-facing traceback."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")

    fake_db = FakeCliSession()
    monkeypatch.setattr(backup_cli, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(backup_cli, "get_backup_job", lambda db, job_id: None)
    monkeypatch.setattr(backup_cli, "list_backup_artifacts", lambda db, job_id: [])

    exit_code = backup_cli._cmd_restore_preflight(
        SimpleNamespace(source=None, job_id="missing-job", target="empty")
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "backup_job_id": "missing-job",
        "preflight": {
            "ok": False,
            "reason": "backup_job_not_restorable",
            "detail": "Backup job is not a successful restorable backup",
        },
    }
    assert fake_db.closed is True


def test_main_suppresses_traceback_for_expected_backup_failure(monkeypatch, capsys):
    """Normal policy failures should produce one sanitized error, not call frames."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setitem(sys.modules, "zstandard", SimpleNamespace())
    backup_cli = importlib.import_module("app.backups.cli")
    monkeypatch.delenv("OMLORIX_BACKUP_CLI_DEBUG", raising=False)

    parser = SimpleNamespace(
        parse_args=lambda argv: SimpleNamespace(
            command="create",
            func=lambda args: (_ for _ in ()).throw(
                RuntimeError("Plaintext backups are disabled")
            ),
        )
    )
    monkeypatch.setattr(backup_cli, "build_parser", lambda: parser)

    assert backup_cli.main([]) == 1
    error = capsys.readouterr().err
    assert "Plaintext backups are disabled" in error
    assert "Traceback" not in error
