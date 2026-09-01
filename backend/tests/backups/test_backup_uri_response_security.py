from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

from psycopg2.extensions import parse_dsn
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())
from app.backups import service as backup_service  # noqa: E402


def test_backup_uri_metadata_redaction_omits_paths_and_object_keys():
    uri = "s3://private-bucket/backups/full-instance.tar.zst"

    result = backup_service.redact_backup_uri_metadata(uri)

    assert result["scheme"] == "s3"
    assert result["location"] == "remote"
    assert len(result["fingerprint"]) == 12
    assert "private-bucket" not in str(result)
    assert "full-instance" not in str(result)


def test_sanitize_backup_response_metadata_redacts_nested_uri_fields():
    payload = {
        "source_uri": "file:///var/lib/omlorix/backups/upload.tar.zst",
        "nested": {
            "pre_restore_uri": "local://archives/pre-restore.tar.zst",
            "safe": "value",
        },
    }

    result = backup_service.sanitize_backup_response_metadata(payload)

    assert result["source_uri"]["scheme"] == "file"
    assert result["nested"]["pre_restore_uri"]["scheme"] == "local"
    assert result["nested"]["safe"] == "value"
    assert "/var/lib/omlorix" not in str(result)
    assert "pre-restore.tar.zst" not in str(result)


def test_sanitize_backup_response_metadata_redacts_uri_text():
    payload = {
        "error": "Unsupported backup source URI 'file:///var/lib/omlorix/backups/upload.tar.zst'",
    }

    result = backup_service.sanitize_backup_response_metadata(payload)

    assert "file://[redacted:" in result["error"]
    assert "/var/lib/omlorix" not in result["error"]


def test_sanitize_backup_response_metadata_redacts_backup_local_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_LOCAL_DIR", tmp_path)
    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", tmp_path / "archives")
    monkeypatch.setattr(
        backup_service,
        "BACKUP_DOWNLOAD_CACHE_DIR",
        tmp_path / "download-cache",
    )
    missing_path = tmp_path / "uploads" / "missing.tar.zst"
    payload = {
        "error": f"Backup artifact does not exist at {missing_path}",
    }

    result = backup_service.sanitize_backup_response_metadata(payload)

    assert result["error"] == "Backup artifact does not exist at [backup-path-redacted]"
    assert str(tmp_path) not in result["error"]


def test_safe_backup_error_message_never_persists_subprocess_argv():
    error = subprocess.CalledProcessError(
        1,
        [
            "pg_dump",
            "--format=custom",
            "--file",
            "/app/backups/staging/job-1/db/main.dump",
            "postgresql://postgres:password@postgres:5432/omlorix-db",
        ],
    )

    result = backup_service.safe_backup_error_message(error, operation="Backup job")

    assert result == "Backup job failed because command 'pg_dump' exited with status 1. Check server logs for details."
    assert "postgresql://" not in result
    assert "password" not in result
    assert "/app/backups" not in result
    assert "--file" not in result


def test_postgres_dump_preserves_url_policy_without_password_in_argv(
    tmp_path,
    monkeypatch,
):
    """pg_dump must share the application policy without exposing credentials."""
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    output_path = tmp_path / "database.dump"
    config = {
        "driver": "postgresql",
        "url": (
            "postgresql://omlorix:database-secret@database.example/omlorix"
            "?sslmode=verify-full&sslrootcert=%2Fcerts%2Froot.crt"
            "&channel_binding=require"
        ),
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "database-secret",
        "database_host": "database.example",
    }

    backup_service._dump_database(config, output_path, schemas=["app"])

    command, kwargs = calls[0]
    connection_parameters = parse_dsn(command[-1])
    assert command[0] == "pg_dump"
    assert connection_parameters["sslmode"] == "verify-full"
    assert connection_parameters["sslrootcert"] == "/certs/root.crt"
    assert connection_parameters["channel_binding"] == "require"
    assert connection_parameters["application_name"] == "omlorix-backup"
    assert kwargs["env"]["PGPASSWORD"] == "database-secret"
    assert kwargs["timeout"] == backup_service.BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS
    assert "database-secret" not in " ".join(command)


def test_postgres_dump_timeout_uses_safe_backup_error_message(tmp_path, monkeypatch):
    """A bounded pg_dump timeout must propagate into the existing safe handler."""
    observed_timeout = None

    def run(command, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(command, observed_timeout)

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    config = {
        "driver": "postgresql",
        "url": "postgresql://omlorix:database-secret@database.example/omlorix",
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "database-secret",
        "database_host": "database.example",
    }

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        backup_service._dump_database(config, tmp_path / "database.dump")

    result = backup_service.safe_backup_error_message(
        raised.value,
        operation="Backup job",
    )
    assert observed_timeout == backup_service.BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS
    assert result == (
        "Backup job timed out while running command 'pg_dump' "
        f"after {backup_service.BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS} seconds. "
        "Check server logs for details."
    )
    assert "database-secret" not in result


def test_redact_backup_uri_text_redacts_database_urls():
    raw = "connection failed for postgresql://postgres:password@postgres:5432/omlorix-db"

    result = backup_service.redact_backup_uri_text(raw)

    assert result is not None
    assert "postgresql://[redacted:" in result
    assert "password" not in result
    assert "omlorix-db" not in result


def test_backup_job_response_uses_artifact_id_and_redacted_storage(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    artifact = SimpleNamespace(
        id="artifact-1",
        backup_job_id="backup-1",
        storage_uri="s3://private-bucket/backups/backup-1.tar.zst",
        checksum_sha256="0" * 64,
        bytes=123,
        verified_at=None,
        expires_at=None,
        created_at=now,
    )
    job = SimpleNamespace(
        id="backup-1",
        trigger_type="manual",
        status="success",
        error=None,
        manifest_json={"storage_uri": artifact.storage_uri},
        options={},
        size_bytes=123,
        requested_by_user_id="admin-1",
        destination_id="destination-1",
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        backup_service,
        "list_backup_artifacts",
        lambda db, job_id: [artifact],
    )

    result = backup_service.build_backup_job_response(object(), job)

    assert result["artifacts"][0]["id"] == "artifact-1"
    assert "storage_uri" not in result["artifacts"][0]
    assert result["artifacts"][0]["storage"]["scheme"] == "s3"
    assert "private-bucket" not in str(result)


def test_restore_job_response_redacts_backup_local_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_LOCAL_DIR", tmp_path)
    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", tmp_path / "archives")
    monkeypatch.setattr(
        backup_service,
        "BACKUP_DOWNLOAD_CACHE_DIR",
        tmp_path / "download-cache",
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    source_path = tmp_path / "uploads" / "opaque-id.tar.zst"
    job = SimpleNamespace(
        id="restore-1",
        source_uri=f"file://{source_path}",
        target_mode="empty",
        status="failed",
        error=f"Backup artifact does not exist at {source_path}",
        preflight_json=None,
        options={},
        requested_by_user_id="admin-1",
        confirmed_by_user_id=None,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )

    result = backup_service.build_restore_job_response(job)

    assert result["source"]["scheme"] == "file"
    assert result["error"] == "Backup artifact does not exist at [backup-path-redacted]"
    assert str(tmp_path) not in result["error"]
