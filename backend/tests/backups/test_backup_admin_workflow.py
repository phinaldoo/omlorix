from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from pydantic import ValidationError
from requests.exceptions import SSLError
from webdav3.exceptions import NoConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.backups import models as backup_models
from app.backups import router as backup_router
from app.backups import service as backup_service
from app.backups.errors import classify_backup_destination_test_error
from app.backups.schemas import BackupDestinationCreate
from app.backups.storage.webdav import WebDAVBackupStorageAdapter


class FakeScheduleSession:
    def __init__(self):
        self.committed = False
        self.refreshed = None

    def commit(self):
        self.committed = True

    def refresh(self, row):
        self.refreshed = row


def test_encrypted_backup_header_uses_export_version():
    """Encrypted backup metadata identifies the current export contract."""
    metadata = backup_service._build_archive_encryption_metadata("long-test-passphrase")

    assert metadata["format"] == backup_service.BACKUP_ENCRYPTED_ARCHIVE_FORMAT
    assert metadata["export_version"] == 1.0


def test_encrypted_backup_round_trip_uses_current_format(tmp_path):
    """Current Omlorix encrypted archives remain detectable and decryptable."""
    plaintext = tmp_path / "backup.tar.zst"
    encrypted = tmp_path / "backup.tar.zst.enc"
    restored = tmp_path / "restored-backup.tar.zst"
    plaintext.write_bytes(b"omlorix-backup-payload")

    backup_service._encrypt_archive_file(
        plaintext,
        encrypted,
        passphrase="long-test-passphrase",
    )

    assert backup_service._archive_looks_encrypted(encrypted) is True
    backup_service._decrypt_archive_file(
        encrypted,
        restored,
        passphrase="long-test-passphrase",
    )
    assert restored.read_bytes() == plaintext.read_bytes()


def test_encrypted_backup_restore_rejects_boolean_version(tmp_path):
    """A boolean header cannot masquerade as the numeric 1.0 format."""
    source_path = tmp_path / "backup.tar.zst.enc"
    target_path = tmp_path / "backup.tar.zst"
    metadata = {
        "format": backup_service.BACKUP_ENCRYPTED_ARCHIVE_FORMAT,
        "export_version": True,
    }
    source_path.write_bytes(
        backup_service.ENCRYPTED_ARCHIVE_MAGIC
        + json.dumps(metadata).encode("utf-8")
        + b"\n"
    )

    with pytest.raises(RuntimeError, match="Unsupported encrypted backup export version"):
        backup_service._decrypt_archive_file(
            source_path,
            target_path,
            passphrase="long-test-passphrase",
        )

    assert not target_path.exists()


def test_schedule_update_can_clear_nullable_retention_and_destination(monkeypatch):
    schedule = SimpleNamespace(
        id="schedule-1",
        name="Daily",
        enabled=True,
        timezone="UTC",
        frequency="daily",
        minute=0,
        hour=2,
        days_of_week=[0],
        retention_count=7,
        retention_days=30,
        destination_id="destination-1",
        updated_at=None,
    )
    db = FakeScheduleSession()

    monkeypatch.setattr(backup_models, "get_backup_schedule", lambda db_arg, schedule_id: schedule)

    result = backup_models.update_backup_schedule(
        db,
        schedule_id=schedule.id,
        retention_count=None,
        retention_days=None,
        destination_id=None,
    )

    assert result is schedule
    assert schedule.retention_count is None
    assert schedule.retention_days is None
    assert schedule.destination_id is None
    assert db.committed is True
    assert db.refreshed is schedule


def test_schedule_update_omitted_nullable_fields_preserve_existing_values(monkeypatch):
    schedule = SimpleNamespace(
        id="schedule-1",
        name="Daily",
        enabled=True,
        timezone="UTC",
        frequency="daily",
        minute=0,
        hour=2,
        days_of_week=[0],
        retention_count=7,
        retention_days=30,
        destination_id="destination-1",
        updated_at=None,
    )
    db = FakeScheduleSession()

    monkeypatch.setattr(backup_models, "get_backup_schedule", lambda db_arg, schedule_id: schedule)

    backup_models.update_backup_schedule(db, schedule_id=schedule.id, name="Daily backups")

    assert schedule.name == "Daily backups"
    assert schedule.retention_count == 7
    assert schedule.retention_days == 30
    assert schedule.destination_id == "destination-1"


def test_webdav_is_valid_backup_destination_provider():
    payload = BackupDestinationCreate(
        name="NAS",
        provider="webdav",
        config={"url": "https://nas.example.com/webdav", "username": "user", "password": "secret"},
    )

    assert payload.provider == "webdav"


def test_webdav_tls_verification_setting_reaches_each_request():
    """Ensure the adapter's TLS setting is not overridden by webdavclient3."""
    adapter = WebDAVBackupStorageAdapter(
        {
            "url": "https://nas.example.test/webdav",
            "verify_ssl": False,
        }
    )
    request_options = {}

    def fake_request(**kwargs):
        """Capture the effective Requests options without making a network call."""
        request_options.update(kwargs)
        return SimpleNamespace(status_code=200)

    adapter.client.session.request = fake_request
    adapter.client.execute_request(action="check", path="/")

    assert request_options["verify"] is False


def test_backup_destination_error_classification_retains_tls_root_cause():
    """Unwrap webdavclient3's generic connection error for actionable UI guidance."""
    try:
        try:
            raise SSLError("certificate verify failed: IP address mismatch")
        except SSLError:
            raise NoConnection("https://nas.example.test")
    except NoConnection as exc:
        error_code = classify_backup_destination_test_error(exc)

    assert error_code == "backup_destination_tls_certificate_invalid"


def test_backup_destination_route_returns_structured_failure(monkeypatch):
    """Return a stable error code so the frontend can localize test failures."""
    audit_details = []

    def fail_destination_test(db, destination_id):
        """Model the generic exception currently raised by webdavclient3."""
        raise NoConnection("https://nas.example.test")

    monkeypatch.setattr(backup_router, "test_backup_destination", fail_destination_test)
    monkeypatch.setattr(
        backup_router,
        "_audit",
        lambda **kwargs: audit_details.append(kwargs["details"]),
    )

    result = backup_router.test_destination_route(
        destination_id="destination-1",
        request=SimpleNamespace(),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result.status == "error"
    assert result.details == {"error_code": "backup_destination_unreachable"}
    assert audit_details == [
        {
            "destination_id": "destination-1",
            "status": "error",
            "error_code": "backup_destination_unreachable",
        }
    ]


def test_backup_destination_route_does_not_expose_success_probe_details(monkeypatch):
    """Keep provider URLs and temporary probe data out of API responses."""
    audit_details = []
    monkeypatch.setattr(
        backup_router,
        "test_backup_destination",
        lambda db, destination_id: {
            "status": "ok",
            "provider": "webdav",
            "url": "https://nas.internal.example.test:5556",
            "probe_content": "omlorix-backup-probe",
            "destination_id": destination_id,
        },
    )
    monkeypatch.setattr(
        backup_router,
        "_audit",
        lambda **kwargs: audit_details.append(kwargs["details"]),
    )

    result = backup_router.test_destination_route(
        destination_id="destination-1",
        request=SimpleNamespace(),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result.status == "success"
    assert result.details is None
    assert audit_details == [
        {
            "destination_id": "destination-1",
            "status": "ok",
        }
    ]


def test_unknown_backup_destination_provider_is_rejected():
    try:
        BackupDestinationCreate(name="Nope", provider="ftp", config={})
    except ValidationError as exc:
        assert "provider" in str(exc)
    else:
        raise AssertionError("Unsupported backup provider should fail validation")


def test_destination_update_preserves_redacted_and_omitted_secrets(monkeypatch):
    """Editing non-secret fields must never replace or remove saved credentials."""
    destination = SimpleNamespace(
        id="destination-1",
        name="NAS",
        provider="webdav",
        config_encrypted={"enc_v1": "encrypted"},
        enabled=True,
        updated_at=None,
    )
    db = FakeScheduleSession()
    encrypted_payload = {}

    monkeypatch.setattr(
        backup_models,
        "get_backup_destination",
        lambda db_arg, destination_id: destination,
    )
    monkeypatch.setattr(
        backup_models,
        "decrypt_destination_config",
        lambda encrypted: {
            "url": "https://nas.example.com/webdav",
            "username": "admin",
            "password": "actual-secret",
            "session_token": "temporary-secret",
            "nested": {"api_token": "nested-secret", "label": "old"},
        },
    )
    monkeypatch.setattr(
        backup_models,
        "encrypt_destination_config",
        lambda config: encrypted_payload.setdefault("config", config) or {"enc_v1": "updated"},
    )

    backup_models.update_backup_destination(
        db,
        destination_id=destination.id,
        config={
            "url": "https://nas.example.com/webdav",
            "username": "new-admin",
            "password": backup_models.REDACTED_CONFIG_VALUE,
            "nested": {
                "api_token": backup_models.REDACTED_CONFIG_VALUE,
                "label": "new",
            },
        },
    )

    assert encrypted_payload["config"]["password"] == "actual-secret"
    assert encrypted_payload["config"]["session_token"] == "temporary-secret"
    assert encrypted_payload["config"]["nested"]["api_token"] == "nested-secret"
    assert encrypted_payload["config"]["nested"]["label"] == "new"


def test_destination_update_can_explicitly_clear_saved_secret(monkeypatch):
    """Null is an explicit clear operation, unlike a blank omitted edit field."""
    destination = SimpleNamespace(
        id="destination-1",
        name="NAS",
        provider="webdav",
        config_encrypted={"enc_v1": "encrypted"},
        enabled=True,
        updated_at=None,
    )
    db = FakeScheduleSession()
    encrypted_payload = {}

    monkeypatch.setattr(
        backup_models,
        "get_backup_destination",
        lambda db_arg, destination_id: destination,
    )
    monkeypatch.setattr(
        backup_models,
        "decrypt_destination_config",
        lambda encrypted: {
            "url": "https://nas.example.com/webdav",
            "password": "actual-secret",
        },
    )
    monkeypatch.setattr(
        backup_models,
        "encrypt_destination_config",
        lambda config: encrypted_payload.setdefault("config", config) or {"enc_v1": "updated"},
    )

    backup_models.update_backup_destination(
        db,
        destination_id=destination.id,
        config={"url": "https://nas.example.com/webdav", "password": None},
    )

    assert encrypted_payload["config"]["password"] is None


def test_destination_provider_change_without_config_does_not_reuse_secrets(monkeypatch):
    """Changing provider without config must never carry old credentials forward."""
    destination = SimpleNamespace(
        id="destination-1",
        name="NAS",
        provider="webdav",
        config_encrypted={"enc_v1": "encrypted"},
        enabled=True,
        updated_at=None,
    )
    db = FakeScheduleSession()
    encrypted_payload = {}

    monkeypatch.setattr(
        backup_models,
        "get_backup_destination",
        lambda db_arg, destination_id: destination,
    )
    monkeypatch.setattr(
        backup_models,
        "decrypt_destination_config",
        lambda encrypted: {
            "url": "https://nas.example.com/webdav",
            "password": "actual-secret",
        },
    )
    monkeypatch.setattr(
        backup_models,
        "encrypt_destination_config",
        lambda config: encrypted_payload.setdefault("config", config) or {"enc_v1": "updated"},
    )

    backup_models.update_backup_destination(
        db,
        destination_id=destination.id,
        provider="local",
    )

    assert encrypted_payload["config"] == {}


def test_redaction_marker_is_never_treated_as_a_new_secret():
    merged = backup_models.merge_redacted_destination_config(
        {},
        {
            "url": "https://nas.example.com/webdav",
            "password": backup_models.REDACTED_CONFIG_VALUE,
        },
    )

    assert "password" not in merged


def test_service_account_json_is_redacted_and_preserved_on_merge():
    credentials = {"client_email": "backup@example.com", "private_key": "secret"}
    redacted = backup_models.redact_destination_config({"credentials_json": credentials})

    assert redacted["credentials_json"] == backup_models.REDACTED_CONFIG_VALUE
    assert backup_models.merge_redacted_destination_config(
        {"credentials_json": credentials},
        {"credentials_json": backup_models.REDACTED_CONFIG_VALUE},
    )["credentials_json"] == credentials


def test_destination_config_rejects_missing_provider_required_fields():
    for provider, config, expected_field in (
        ("s3", {}, "bucket"),
        ("gcs", {"bucket": "  "}, "bucket"),
        ("azure", {"container": "backups"}, "account_url"),
        ("webdav", {"url": ""}, "url"),
    ):
        try:
            backup_models.validate_backup_destination_config(provider, config)
        except Exception as exc:
            assert exc.status_code == 422
            assert exc.detail["field"] == expected_field
        else:
            raise AssertionError(f"{provider} config should reject missing {expected_field}")


def test_backup_download_filename_reflects_archive_encryption(tmp_path):
    encrypted = tmp_path / "backup.tar.zst.enc"
    plaintext = tmp_path / "backup.tar.zst"
    encrypted.write_bytes(backup_service.ENCRYPTED_ARCHIVE_MAGIC + b"header\npayload")
    plaintext.write_bytes(b"\x28\xb5\x2f\xfdpayload")

    assert backup_service.backup_archive_download_filename("job-1", encrypted) == "omlorix-backup-job-1.tar.zst.enc"
    assert backup_service.backup_archive_download_filename("job-2", plaintext) == "omlorix-backup-job-2.tar.zst"


def test_backup_download_response_exposes_size_for_native_progress(tmp_path, monkeypatch):
    """Native browser downloads need Content-Length before streaming progress."""
    archive = tmp_path / "backup.tar.zst"
    archive.write_bytes(b"backup-data")

    monkeypatch.setattr(
        backup_router,
        "materialize_backup_job_artifact",
        lambda db, job_id: (archive, "artifact-1"),
    )
    monkeypatch.setattr(backup_router, "_audit", lambda **kwargs: None)

    response = backup_router.download_backup_job_route(
        "job-1",
        SimpleNamespace(),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response.headers["content-length"] == str(archive.stat().st_size)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-disposition"].endswith('filename="omlorix-backup-job-1.tar.zst"')
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_backup_download_preflight_materializes_and_exposes_file_headers(tmp_path, monkeypatch):
    """HEAD preflight must fail early or expose the headers used by native GET."""
    archive = tmp_path / "backup.tar.zst"
    archive.write_bytes(b"backup-data")

    monkeypatch.setattr(
        backup_router,
        "materialize_backup_job_artifact",
        lambda db, job_id: (archive, "artifact-1"),
    )

    response = backup_router.prepare_backup_job_download_route(
        "job-1",
        db=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response.body == b""
    assert response.headers["content-length"] == str(archive.stat().st_size)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-disposition"].endswith('filename="omlorix-backup-job-1.tar.zst"')


def test_backup_download_reuses_complete_remote_materialization(tmp_path, monkeypatch):
    """The HEAD and GET pair must not download a remote artifact twice."""
    cached_archive = tmp_path / "job-1.tar.zst"
    cached_archive.write_bytes(b"backup-data")
    artifact = SimpleNamespace(
        id="artifact-1",
        storage_uri="s3://bucket/job-1.tar.zst",
        bytes=cached_archive.stat().st_size,
        checksum_sha256=hashlib.sha256(cached_archive.read_bytes()).hexdigest(),
    )

    monkeypatch.setattr(backup_service, "BACKUP_DOWNLOAD_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        backup_service,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(id=job_id, status="success"),
    )
    monkeypatch.setattr(backup_service, "list_backup_artifacts", lambda db, job_id: [artifact])
    monkeypatch.setattr(
        backup_service,
        "_materialize_source_artifact",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote artifact downloaded twice")),
    )

    path, artifact_id = backup_service.materialize_backup_job_artifact(object(), "job-1")

    assert path == cached_archive
    assert artifact_id == artifact.id


def test_backup_download_rejects_incomplete_jobs_before_artifact_access(monkeypatch):
    monkeypatch.setattr(
        backup_service,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(id=job_id, status="running"),
    )
    monkeypatch.setattr(
        backup_service,
        "list_backup_artifacts",
        lambda *args: (_ for _ in ()).throw(AssertionError("artifacts must not be read")),
    )

    with pytest.raises(RuntimeError, match="not complete"):
        backup_service.materialize_backup_job_artifact(object(), "job-running")


def test_backup_download_rejects_deleted_jobs_before_artifact_access(monkeypatch):
    monkeypatch.setattr(backup_service, "get_backup_job", lambda db, job_id: None)
    monkeypatch.setattr(
        backup_service,
        "list_backup_artifacts",
        lambda *args: (_ for _ in ()).throw(AssertionError("artifacts must not be read")),
    )

    with pytest.raises(RuntimeError, match="not found"):
        backup_service.materialize_backup_job_artifact(object(), "job-deleted")


def test_backup_download_rejects_corrupt_local_artifact(tmp_path, monkeypatch):
    archive = tmp_path / "job-1.tar.zst"
    archive.write_bytes(b"tampered")
    artifact = SimpleNamespace(
        id="artifact-1",
        storage_uri="local://job-1.tar.zst",
        bytes=archive.stat().st_size,
        checksum_sha256=hashlib.sha256(b"expected").hexdigest(),
    )
    monkeypatch.setattr(
        backup_service,
        "get_backup_job",
        lambda db, job_id: SimpleNamespace(id=job_id, status="success"),
    )
    monkeypatch.setattr(backup_service, "list_backup_artifacts", lambda db, job_id: [artifact])
    monkeypatch.setattr(backup_service, "_resolve_local_artifact_path", lambda uri: archive)

    with pytest.raises(RuntimeError, match="catalog checksum"):
        backup_service.materialize_backup_job_artifact(object(), "job-1")

    assert archive.read_bytes() == b"tampered"


class FakeBackupQuery:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def filter(self, criterion):
        column_name = getattr(getattr(criterion, "left", None), "key", None)
        value = getattr(getattr(criterion, "right", None), "value", None)
        if column_name == "destination_id":
            self.jobs = [job for job in self.jobs if job.destination_id == value]
        elif column_name == "status":
            self.jobs = [job for job in self.jobs if job.status == value]
        return self

    def order_by(self, *args):
        self.jobs.sort(key=lambda job: job.created_at, reverse=True)
        return self

    def all(self):
        return self.jobs


class FakeBackupSession:
    def __init__(self, jobs):
        self.jobs = jobs

    def query(self, model):
        assert model is backup_service.BackupJob
        return FakeBackupQuery(self.jobs)


def test_schedule_retention_prunes_only_successful_backups_from_same_schedule(monkeypatch):
    now = datetime.now(timezone.utc)
    schedule = SimpleNamespace(
        id="schedule-1",
        destination_id="destination-1",
        retention_count=2,
        retention_days=5,
    )
    jobs = [
        SimpleNamespace(id="newest", destination_id="destination-1", status="success", options={"schedule_id": "schedule-1"}, created_at=now),
        SimpleNamespace(id="second", destination_id="destination-1", status="success", options={"schedule_id": "schedule-1"}, created_at=now - timedelta(days=1)),
        SimpleNamespace(id="old-same-schedule", destination_id="destination-1", status="success", options={"schedule_id": "schedule-1"}, created_at=now - timedelta(days=8)),
        SimpleNamespace(id="manual", destination_id="destination-1", status="success", options={}, created_at=now - timedelta(days=9)),
        SimpleNamespace(id="other-schedule", destination_id="destination-1", status="success", options={"schedule_id": "schedule-2"}, created_at=now - timedelta(days=10)),
        SimpleNamespace(id="failed", destination_id="destination-1", status="failed", options={"schedule_id": "schedule-1"}, created_at=now - timedelta(days=11)),
    ]
    deleted_job_ids = []

    monkeypatch.setattr(
        backup_service,
        "delete_backup_job_and_artifacts",
        lambda db, job_id, *, delete_remote: deleted_job_ids.append((job_id, delete_remote)),
    )

    backup_service.apply_backup_schedule_retention(FakeBackupSession(jobs), schedule)

    assert deleted_job_ids == [("old-same-schedule", True)]
