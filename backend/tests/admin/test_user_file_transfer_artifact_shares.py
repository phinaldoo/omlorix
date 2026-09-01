import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


from app.admin.user_exports import utils as user_archive_transfer
from app.admin.user_exports.files import models as user_file_transfer
from app.files.models import FileArtifactShare, Files
from app.users.models import User


class _FakeQuery:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ExportDb:
    def __init__(self, *, user, files, shares):
        self.user = user
        self.files = files
        self.shares = shares

    def query(self, model):
        if model is User:
            return _FakeQuery([self.user] if self.user else [])
        if model is Files:
            return _FakeQuery(self.files)
        if model is FileArtifactShare:
            return _FakeQuery(self.shares)
        raise AssertionError(f"Unexpected model queried: {model!r}")


class _ImportDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.refreshes = []

    def add(self, instance):
        self.added.append(instance)

    def commit(self):
        self.commits += 1

    def refresh(self, instance):
        self.refreshes.append(instance)




def test_import_skips_password_protected_artifact_shares(monkeypatch):
    imported_file = Files(
        id="new-file-1",
        user_id="target-user",
        file_name="artifact.md",
        storage_provider="local",
        storage_key="target-user/artifact.md",
        file_category="document",
        file_type="text/markdown",
        file_size=9,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    target_user = User(id="target-user", email="target@example.com")
    db = _ImportDb()

    monkeypatch.setattr(user_file_transfer, "_build_existing_file_indexes", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(user_file_transfer, "_load_existing_artifact_share_ids", lambda *args, **kwargs: set())
    def _persist_generated_file_bytes(*args, **kwargs):
        imported_file.meta = kwargs.get("meta")
        return imported_file

    monkeypatch.setattr(user_file_transfer, "persist_generated_file_bytes", _persist_generated_file_bytes)

    result = user_file_transfer._import_file_entries_for_target_user(
        db,
        target_user=target_user,
        package_email="source@example.com",
        user_action="created",
        files=[
            {
                "id": "source-file-1",
                "file_name": "artifact.md",
                "file_type": "text/markdown",
                "folder_id": "source-folder-1",
                "artifact_shares": [
                    {
                        "id": "share-1",
                        "created_at": "2026-01-03T00:00:00+00:00",
                        "expires_at": "2026-01-04T00:00:00+00:00",
                        "last_accessed_at": "2026-01-05T00:00:00+00:00",
                        "access_count": 3,
                        "has_password": True,
                    }
                ],
            }
        ],
        loader=lambda entry: (b"# Shared\n", "artifact.md", "text/markdown", "sha256"),
        folder_id_map={"source-folder-1": "mapped-folder-1"},
    )

    restored_shares = [row for row in db.added if isinstance(row, FileArtifactShare)]
    assert restored_shares == []
    assert imported_file.folder_id == "mapped-folder-1"
    assert imported_file.meta["import_source_folder_id"] == "source-folder-1"
    assert result["created_files_count"] == 1
    assert result["warnings"] == [
        {
            "index": 0,
            "source_file_id": "source-file-1",
            "original_filename": "artifact.md",
            "warning": "Skipped password-protected artifact shares because exported passwords are redacted. Recreate them after import.",
            "skipped_password_protected_artifact_share_count": 1,
        }
    ]


def test_import_resets_unprotected_artifact_share_expiry_when_missing(monkeypatch):
    imported_file = Files(
        id="new-file-1",
        user_id="target-user",
        file_name="artifact.md",
        storage_provider="local",
        storage_key="target-user/artifact.md",
        file_category="document",
        file_type="text/markdown",
        file_size=9,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    target_user = User(id="target-user", email="target@example.com")
    db = _ImportDb()

    monkeypatch.setattr(user_file_transfer, "_build_existing_file_indexes", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(user_file_transfer, "_load_existing_artifact_share_ids", lambda *args, **kwargs: set())

    def _persist_generated_file_bytes(*args, **kwargs):
        imported_file.meta = kwargs.get("meta")
        return imported_file

    monkeypatch.setattr(user_file_transfer, "persist_generated_file_bytes", _persist_generated_file_bytes)

    before_import = datetime.now(timezone.utc)
    result = user_file_transfer._import_file_entries_for_target_user(
        db,
        target_user=target_user,
        package_email="source@example.com",
        user_action="created",
        files=[
            {
                "id": "source-file-1",
                "file_name": "artifact.md",
                "file_type": "text/markdown",
                "artifact_shares": [
                    {
                        "id": "share-1",
                        "created_at": "2026-01-03T00:00:00+00:00",
                        "expires_at": None,
                        "last_accessed_at": "2026-01-05T00:00:00+00:00",
                        "access_count": 3,
                        "has_password": False,
                    }
                ],
            }
        ],
        loader=lambda entry: (b"# Shared\n", "artifact.md", "text/markdown", "sha256"),
        folder_id_map={},
    )
    after_import = datetime.now(timezone.utc)

    restored_shares = [row for row in db.added if isinstance(row, FileArtifactShare)]
    assert len(restored_shares) == 1
    restored_share = restored_shares[0]
    assert restored_share.id == "share-1"
    assert restored_share.password_hash is None
    assert restored_share.expires_at is not None
    assert restored_share.expires_at >= before_import + timedelta(hours=23, minutes=59)
    assert restored_share.expires_at <= after_import + timedelta(hours=24, minutes=1)
    assert result["warnings"] == [
        {
            "index": 0,
            "source_file_id": "source-file-1",
            "original_filename": "artifact.md",
            "warning": "Reset one or more imported artifact share expirations to a safe bounded value.",
            "reset_artifact_share_expiry_count": 1,
        }
    ]


def test_export_admin_user_files_bundle_manifest_includes_folders_and_shared_folder_subscriptions(monkeypatch):
    monkeypatch.setattr(user_file_transfer, "_copy_export_file_to_zip_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.users.utils._export_user_file_folders",
        lambda user_id, db: {
            "owned": [
                {
                    "id": "folder-1",
                    "user_id": user_id,
                    "name": "Research",
                    "live_share_id": "live-share-1",
                }
            ],
            "subscriptions": [
                {
                    "id": "sub-1",
                    "folder_id": "shared-folder-1",
                    "subscriber_id": user_id,
                    "share_type": "live",
                    "target_share_id": "target-live-share-1",
                }
            ],
        },
    )

    user = User(id="user-1", email="person@example.com")
    file_record = Files(
        id="file-1",
        user_id="user-1",
        file_name="artifact.md",
        storage_provider="local",
        storage_key="user-1/artifact.md",
        file_category="document",
        file_type="text/markdown",
        file_size=12,
        meta={"original_filename": "artifact.md"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    zip_buffer, _, manifest = user_file_transfer.export_admin_user_files_bundle(
        _ExportDb(user=user, files=[file_record], shares=[]),
        "user-1",
    )

    assert manifest["folder_count"] == 1
    assert manifest["shared_file_folder_subscription_count"] == 1

    with zip_buffer, zipfile.ZipFile(zip_buffer, "r") as archive:
        manifest_payload = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest_payload["folders"] == [
        {
            "id": "folder-1",
            "user_id": "user-1",
            "name": "Research",
            "live_share_id": "live-share-1",
        }
    ]
    assert manifest_payload["shared_file_folder_subscriptions"] == [
        {
            "id": "sub-1",
            "folder_id": "shared-folder-1",
            "subscriber_id": "user-1",
            "share_type": "live",
            "target_share_id": "target-live-share-1",
        }
    ]


def test_export_admin_user_files_bundle_accepts_temporary_account_email_references(monkeypatch):
    monkeypatch.setattr(
        "app.users.utils._export_user_file_folders",
        lambda user_id, db: {"owned": [], "subscriptions": []},
    )

    user = User(
        id="temporary-user-1",
        email="Class8A01.abcdef12@temporary.local",
        account_type="temporary",
    )

    zip_buffer, filename, manifest = user_file_transfer.export_admin_user_files_bundle(
        _ExportDb(user=user, files=[], shares=[]),
        "temporary-user-1",
    )

    assert manifest["user"]["email"] == "class8a01.abcdef12@temporary.local"
    assert manifest["file_count"] == 0
    assert filename.startswith("class8a01.abcdef12@temporary.local-files-")

    with zip_buffer, zipfile.ZipFile(zip_buffer, "r") as archive:
        manifest_payload = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest_payload["user"]["email"] == "class8a01.abcdef12@temporary.local"
    assert manifest_payload["files"] == []


def test_export_admin_user_files_bundle_skips_missing_file_content_with_warning(monkeypatch):
    monkeypatch.setattr(
        "app.users.utils._export_user_file_folders",
        lambda user_id, db: {"owned": [], "subscriptions": []},
    )

    def fail_copy(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="Failed to prepare file 'document.pdf' for export")

    monkeypatch.setattr(user_file_transfer, "_copy_export_file_to_zip_entry", fail_copy)

    user = User(id="user-1", email="person@example.com")
    file_record = Files(
        id="file-1",
        user_id="user-1",
        file_name="document.pdf",
        storage_provider="local",
        storage_key="user-1/document.pdf",
        file_category="document",
        file_type="application/pdf",
        file_size=12,
        meta={"original_filename": "document.pdf"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    zip_buffer, _, manifest = user_file_transfer.export_admin_user_files_bundle(
        _ExportDb(user=user, files=[file_record], shares=[]),
        "user-1",
    )

    assert manifest["file_count"] == 0
    assert manifest["warnings"] == [
        {
            "file_id": "file-1",
            "original_filename": "document.pdf",
            "warning": "Failed to prepare file 'document.pdf' for export",
        }
    ]

    with zip_buffer, zipfile.ZipFile(zip_buffer, "r") as archive:
        manifest_payload = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest_payload["files"] == []
    assert manifest_payload["warnings"] == manifest["warnings"]


def test_admin_users_archive_accepts_temporary_accounts_without_files(monkeypatch):
    monkeypatch.setattr(
        "app.users.utils._export_user_file_folders",
        lambda user_id, db: {"owned": [], "subscriptions": []},
    )

    user = User(
        id="temporary-user-1",
        email="Class8A01.abcdef12@temporary.local",
        account_type="temporary",
    )

    monkeypatch.setattr(user_archive_transfer, "iter_admin_export_users", lambda db: iter([user]))
    monkeypatch.setattr(
        user_archive_transfer,
        "iter_user_data_export_json",
        lambda *args, **kwargs: iter(['{"user_id":"temporary-user-1"}']),
    )

    zip_buffer, _, manifest = user_archive_transfer.export_admin_users_archive(
        _ExportDb(user=user, files=[], shares=[]),
        db_log=None,
    )

    assert manifest["user_count"] == 1
    assert manifest["user_files_count"] == 0

    with zip_buffer, zipfile.ZipFile(zip_buffer, "r") as archive:
        index_name = manifest["entries"]["user_index"]
        user_index = json.loads(archive.read(index_name).decode("utf-8"))
        user_path = user_index["users"][0]["payload"]["path"]
        assert sorted(archive.namelist()) == sorted(
            ["manifest.json", index_name, user_path]
        )
        user_payload = json.loads(archive.read(user_path).decode("utf-8"))

    assert user_index["users"][0]["user_id"] == "temporary-user-1"
    assert user_index["users"][0]["email"] == "class8a01.abcdef12@temporary.local"
    assert user_payload == {"user_id": "temporary-user-1"}


def test_admin_users_archive_includes_hidden_chats_by_default(monkeypatch):
    """Both selected-user and all-user archives request every retained chat."""
    user = User(id="user-1", email="person@example.com")
    export_calls = []

    monkeypatch.setattr(
        user_archive_transfer,
        "iter_admin_export_users",
        lambda db, user_ids=None: iter([user]),
    )

    def fake_user_export(*args, **kwargs):
        export_calls.append(kwargs)
        return iter(['{"user_id":"user-1"}'])

    monkeypatch.setattr(
        user_archive_transfer,
        "iter_user_data_export_json",
        fake_user_export,
    )
    monkeypatch.setattr(
        user_archive_transfer,
        "export_admin_user_files_bundle",
        lambda *_args, **_kwargs: (
            io.BytesIO(),
            "person-files.zip",
            {"file_count": 0, "warnings": []},
        ),
    )

    for user_ids in (None, ["user-1"]):
        zip_buffer, _, _ = user_archive_transfer.export_admin_users_archive(
            _ExportDb(user=user, files=[], shares=[]),
            db_log=None,
            user_ids=user_ids,
        )
        zip_buffer.close()

    assert [call["include_deleted_or_temp_chats"] for call in export_calls] == [
        True,
        True,
    ]


def test_admin_users_archive_includes_user_file_export_warnings(monkeypatch):
    user = User(id="user-1", email="person@example.com")

    monkeypatch.setattr(user_archive_transfer, "iter_admin_export_users", lambda db: iter([user]))
    monkeypatch.setattr(
        user_archive_transfer,
        "iter_user_data_export_json",
        lambda *args, **kwargs: iter(['{"user_id":"user-1"}']),
    )

    def fake_file_bundle(*_args, **_kwargs):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", "{}")
        buffer.seek(0)
        return (
            buffer,
            "person-files.zip",
            {
                "file_count": 0,
                "warnings": [
                    {
                        "file_id": "file-1",
                        "original_filename": "document.pdf",
                        "warning": "Failed to prepare file 'document.pdf' for export",
                    }
                ],
            },
        )

    monkeypatch.setattr(user_archive_transfer, "export_admin_user_files_bundle", fake_file_bundle)

    zip_buffer, _, manifest = user_archive_transfer.export_admin_users_archive(
        _ExportDb(user=user, files=[], shares=[]),
        db_log=None,
    )
    with zipfile.ZipFile(zip_buffer, "r") as archive:
        user_index = json.loads(
            archive.read(manifest["entries"]["user_index"]).decode("utf-8")
        )
    zip_buffer.close()

    assert manifest["user_files_count"] == 0
    assert manifest["user_file_warning_count"] == 1
    assert user_index["users"][0]["file_warnings"] == [
        {
            "file_id": "file-1",
            "original_filename": "document.pdf",
            "warning": "Failed to prepare file 'document.pdf' for export",
        }
    ]


def test_import_admin_user_files_archive_restores_folder_state_before_files(monkeypatch):
    target_user = User(id="target-user", email="target@example.com")
    db = _ImportDb()

    captured = {}

    monkeypatch.setattr(user_file_transfer, "_resolve_existing_user_by_email", lambda db, email: (target_user, "updated"))
    monkeypatch.setattr(
        "app.users.utils._bulk_insert_file_folders",
        lambda db, user_id, folders: ({"source-folder-1": "mapped-folder-1"}, [{"section": "file_folders", "warning": "folder restored"}]),
    )
    monkeypatch.setattr(
        "app.users.utils._bulk_insert_shared_file_folder_subscriptions",
        lambda db, user_id, subscriptions, folder_id_map=None: [{"section": "shared_file_folder_subscriptions", "warning": folder_id_map["source-folder-1"]}],
    )

    def fake_import(db, **kwargs):
        captured.update(kwargs)
        return {
            "target_user_id": target_user.id,
            "target_user_email": target_user.email,
            "user_action": "updated",
            "created_files": [],
            "created_files_count": 0,
            "skipped_files": [],
            "skipped_files_count": 0,
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(user_file_transfer, "_import_file_entries_for_target_user", fake_import)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "export_type": user_file_transfer.ADMIN_USER_FILES_EXPORT_TYPE,
                    "export_version": user_file_transfer.ADMIN_USER_FILES_EXPORT_VERSION,
                    "user": {"email": "target@example.com"},
                    "files": [],
                    "folders": [{"id": "source-folder-1", "name": "Research"}],
                    "shared_file_folder_subscriptions": [{"id": "sub-1", "folder_id": "external-folder-1", "share_type": "live"}],
                }
            ),
        )

    buffer.seek(0)
    with zipfile.ZipFile(buffer, "r") as archive:
        result = user_file_transfer.import_admin_user_files_archive(db, archive)

    assert captured["folder_id_map"] == {"source-folder-1": "mapped-folder-1"}
    assert result["restored_folder_count"] == 1
    assert result["warnings"] == [
        {"section": "file_folders", "warning": "folder restored"},
        {"section": "shared_file_folder_subscriptions", "warning": "mapped-folder-1"},
    ]


def test_import_admin_user_files_archive_accepts_temporary_account_email_references(monkeypatch):
    target_user = User(
        id="temporary-user-1",
        email="class8a01.abcdef12@temporary.local",
        account_type="temporary",
    )
    db = _ImportDb()
    captured = {}

    monkeypatch.setattr(
        user_file_transfer,
        "_resolve_existing_user_by_email",
        lambda db, email: (target_user, "updated"),
    )

    def fake_import(db, **kwargs):
        captured.update(kwargs)
        return {
            "target_user_id": target_user.id,
            "target_user_email": target_user.email,
            "user_action": "updated",
            "created_files": [],
            "created_files_count": 0,
            "skipped_files": [],
            "skipped_files_count": 0,
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(user_file_transfer, "_import_file_entries_for_target_user", fake_import)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "export_type": user_file_transfer.ADMIN_USER_FILES_EXPORT_TYPE,
                    "export_version": user_file_transfer.ADMIN_USER_FILES_EXPORT_VERSION,
                    "user": {"email": "Class8A01.abcdef12@temporary.local"},
                    "files": [],
                }
            ),
        )

    buffer.seek(0)
    with zipfile.ZipFile(buffer, "r") as archive:
        result = user_file_transfer.import_admin_user_files_archive(
            db,
            archive,
            expected_email=" class8a01.abcdef12@temporary.local ",
        )

    assert captured["package_email"] == "class8a01.abcdef12@temporary.local"
    assert result["target_user_id"] == "temporary-user-1"


def test_import_admin_user_inline_files_accepts_temporary_account_email_references(monkeypatch):
    target_user = User(
        id="temporary-user-1",
        email="class8a01.abcdef12@temporary.local",
        account_type="temporary",
    )
    captured = {}

    def fake_import(db, **kwargs):
        captured.update(kwargs)
        return {
            "target_user_id": target_user.id,
            "target_user_email": target_user.email,
            "user_action": "updated",
            "created_files": [],
            "created_files_count": 0,
            "skipped_files": [],
            "skipped_files_count": 0,
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(user_file_transfer, "_import_file_entries_for_target_user", fake_import)

    result = user_file_transfer.import_admin_user_inline_files_for_user(
        _ImportDb(),
        target_user=target_user,
        source_email="Class8A01.abcdef12@temporary.local",
        files=[{"email": " class8a01.abcdef12@temporary.local "}],
    )

    assert captured["package_email"] == "class8a01.abcdef12@temporary.local"
    assert result["target_user_id"] == "temporary-user-1"


def test_import_admin_user_files_archive_rejects_unexpected_manifest_email():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "export_type": user_file_transfer.ADMIN_USER_FILES_EXPORT_TYPE,
                    "export_version": user_file_transfer.ADMIN_USER_FILES_EXPORT_VERSION,
                    "user": {"email": "victim@example.com"},
                    "files": [],
                }
            ),
        )

    buffer.seek(0)
    with zipfile.ZipFile(buffer, "r") as archive, pytest.raises(HTTPException) as exc_info:
        user_file_transfer.import_admin_user_files_archive(
            _ImportDb(),
            archive,
            expected_email="harmless@example.com",
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "Invalid file package. Manifest user email does not match the expected user email."
    )


def test_read_archive_entry_reports_missing_entry_name():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", "{}")

    buffer.seek(0)
    with zipfile.ZipFile(buffer, "r") as archive, pytest.raises(HTTPException) as exc_info:
        user_file_transfer._read_archive_entry_with_size_limit(
            archive,
            "files/missing.md",
            original_filename="missing.md",
            max_bytes=10,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid file package. Missing archived file 'files/missing.md'."


def test_read_archive_entry_uses_original_filename_in_size_errors():
    payload = b"0123456789"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("files/artifact.md", payload)

    buffer.seek(0)
    with zipfile.ZipFile(buffer, "r") as archive, pytest.raises(HTTPException) as exc_info:
        user_file_transfer._read_archive_entry_with_size_limit(
            archive,
            "files/artifact.md",
            original_filename="artifact.md",
            max_bytes=5,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Archived file 'artifact.md' exceeds the maximum file size."


def test_user_file_archive_restore_accepts_html_attachment(monkeypatch, tmp_path):
    """Backups can restore the same inert HTML accepted by user uploads."""
    html = b"<!doctype html><html><body><script>alert('inert')</script></body></html>"
    monkeypatch.setattr(user_file_transfer, "TEMP_DIR", tmp_path)
    file_type, digest = user_file_transfer._validate_imported_file_bytes(
        file_bytes=html,
        original_filename="attachment.html",
        fallback_type="text/html",
    )

    assert file_type == "text/html"
    assert len(digest) == 64
