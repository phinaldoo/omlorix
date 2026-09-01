"""Focused tests for the canonical administrative user archive contract."""

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.admin.user_exports import utils as archive_utils
from app.admin.users import router as users_router
from app.dependencies import get_db, get_db_log, verified_admin


def _user_payload(user_id: str, email: str, **sections) -> dict:
    return {
        "export_type": "user_data",
        "export_version": archive_utils.ADMIN_USER_EXPORT_VERSION,
        "email": email,
        "user_id": user_id,
        "user": {"email": email, "user_id": user_id},
        **sections,
    }


def _archive_bytes(
    user_payloads: list[dict],
    *,
    index_checksum_bytes: bytes | None = None,
) -> io.BytesIO:
    """Build the smallest canonical sharded archive for parser/import tests."""
    index_users = []
    encoded_payloads = []
    for index, payload in enumerate(user_payloads):
        payload_bytes = json.dumps(payload).encode("utf-8")
        payload_path = f"users/{index:06d}.json"
        encoded_payloads.append((payload_path, payload_bytes))
        index_users.append(
            {
                "index": index,
                "user_id": payload["user_id"],
                "email": payload["email"],
                "payload": {
                    "path": payload_path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                },
            }
        )

    index_payload = {
        "export_type": "admin_user_index",
        "export_version": archive_utils.ADMIN_USERS_ARCHIVE_EXPORT_VERSION,
        "users": index_users,
    }
    index_bytes = json.dumps(index_payload).encode("utf-8")
    checksum_source = (
        index_bytes if index_checksum_bytes is None else index_checksum_bytes
    )
    manifest = {
        "export_type": archive_utils.ADMIN_USERS_ARCHIVE_EXPORT_TYPE,
        "export_version": archive_utils.ADMIN_USERS_ARCHIVE_EXPORT_VERSION,
        "entries": {"user_index": archive_utils.ADMIN_USERS_ARCHIVE_INDEX_NAME},
        "checksums": {
            archive_utils.ADMIN_USERS_ARCHIVE_INDEX_NAME: hashlib.sha256(
                checksum_source
            ).hexdigest()
        },
        "user_count": len(index_users),
        "user_files_count": 0,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(archive_utils.ADMIN_USERS_ARCHIVE_INDEX_NAME, index_bytes)
        for payload_path, payload_bytes in encoded_payloads:
            archive.writestr(payload_path, payload_bytes)
    buffer.seek(0)
    return buffer


def _representative_payloads() -> list[dict]:
    return [
        _user_payload(
            "source-user-1",
            "first@example.com",
            notes={"data": {"notes": [{"id": "note-1"}]}},
            memories={"data": {"memories": [{"id": "memory-1"}]}},
            files=[{"id": "metadata-only-file"}],
        ),
        _user_payload("source-user-2", "second@example.com"),
    ]


def test_canonical_archive_rejects_tampered_user_index():
    buffer = _archive_bytes(
        _representative_payloads(),
        index_checksum_bytes=b"different index",
    )

    with zipfile.ZipFile(buffer) as archive, pytest.raises(HTTPException) as exc_info:
        archive_utils._read_admin_users_archive(archive)

    assert exc_info.value.status_code == 400
    assert "integrity check failed" in str(exc_info.value.detail)


def test_canonical_archive_rejects_duplicate_index_user_id():
    payloads = _representative_payloads()
    payloads[1]["user_id"] = payloads[0]["user_id"]
    payloads[1]["user"]["user_id"] = payloads[0]["user_id"]
    buffer = _archive_bytes(payloads)

    with zipfile.ZipFile(buffer) as archive, pytest.raises(HTTPException) as exc_info:
        archive_utils._read_admin_users_archive(archive)

    assert exc_info.value.status_code == 400
    assert "index entry is malformed" in str(exc_info.value.detail)


def test_canonical_archive_bounds_fully_parsed_user_index(monkeypatch):
    buffer = _archive_bytes(_representative_payloads())
    monkeypatch.setattr(archive_utils, "ARCHIVE_USER_INDEX_MAX_BYTES", 32)

    with zipfile.ZipFile(buffer) as archive, pytest.raises(HTTPException) as exc_info:
        archive_utils._read_admin_users_archive(archive)

    assert exc_info.value.status_code == 400
    assert "users-index.json" in str(exc_info.value.detail)
    assert "exceeds" in str(exc_info.value.detail)


def test_canonical_selected_import_keeps_notes_and_memories_but_defers_files(
    monkeypatch,
):
    captured = {}

    def fake_import_users(payload, _db, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return {"created": [], "updated": [], "warnings": [], "errors": []}

    monkeypatch.setattr(archive_utils, "import_users_admin", fake_import_users)
    buffer = _archive_bytes(_representative_payloads())

    with zipfile.ZipFile(buffer) as archive:
        archive_utils.import_admin_users_archive(
            object(),
            archive,
            selected_indices=[0],
            allow_administrative_targets=True,
        )

    imported_users = captured["payload"]["data"]["users"]
    assert len(imported_users) == 1
    assert imported_users[0]["notes"]["data"]["notes"][0]["id"] == "note-1"
    assert imported_users[0]["memories"]["data"]["memories"][0]["id"] == "memory-1"
    assert "files" not in imported_users[0]
    assert captured["allow_administrative_targets"] is True
    assert captured["include_internal_restore_maps"] is True


def test_sharded_archive_import_reads_only_selected_user_payload(monkeypatch):
    selected_payload = _user_payload("source-user-1", "first@example.com")
    unselected_payload = _user_payload(
        "source-user-2",
        "second@example.com",
        padding="x" * 4096,
    )
    buffer = _archive_bytes([selected_payload, unselected_payload])
    selected_size = len(json.dumps(selected_payload).encode("utf-8"))
    monkeypatch.setattr(
        archive_utils,
        "ARCHIVE_PARSED_JSON_ENTRY_MAX_BYTES",
        selected_size + 32,
    )
    captured = {}

    def fake_import_users(payload, _db, **_kwargs):
        captured["payload"] = payload
        return {"created": [], "updated": [], "warnings": [], "errors": []}

    monkeypatch.setattr(archive_utils, "import_users_admin", fake_import_users)

    with zipfile.ZipFile(buffer) as archive:
        result = archive_utils.import_admin_users_archive(
            object(),
            archive,
            selected_indices=[0],
        )

    assert result["created"] == []
    assert captured["payload"]["data"]["users"] == [selected_payload]


def test_admin_import_accepts_real_multipart_canonical_archive(monkeypatch):
    """Stage a real multipart archive and preserve its worker import contract."""

    captured = {}
    staged_name = "a" * 32 + ".zip"

    def fake_import_users(payload, _db, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return {"created": [], "updated": [], "warnings": [], "errors": []}

    def fake_stage_import(stream, *, extension, principal_id, import_kind):
        captured["staged_archive"] = stream.read()
        captured["extension"] = extension
        captured["principal_id"] = principal_id
        captured["import_kind"] = import_kind
        return staged_name

    async def fake_enqueue_import(**kwargs):
        captured["queued"] = kwargs
        return SimpleNamespace(id="worker-job-1")

    async def fake_wait_for_operations_result(_job):
        return {"created": [], "updated": [], "warnings": [], "errors": []}

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(archive_utils, "import_users_admin", fake_import_users)
    monkeypatch.setattr(users_router, "stage_import_stream", fake_stage_import)
    monkeypatch.setattr(users_router, "enqueue_import_job_async", fake_enqueue_import)
    monkeypatch.setattr(
        users_router,
        "wait_for_operations_result_async",
        fake_wait_for_operations_result,
    )
    monkeypatch.setattr(users_router, "SessionLocal", _Session)
    monkeypatch.setattr(users_router, "AuditSessionLocal", _Session)
    monkeypatch.setattr(
        users_router,
        "get_audit_request_ip",
        lambda _request, _db: "127.0.0.1",
    )
    monkeypatch.setattr(users_router, "create_audit_log", lambda **_kwargs: None)

    app = FastAPI()
    app.include_router(users_router.admin_router)
    app.dependency_overrides[get_db] = _Session
    app.dependency_overrides[get_db_log] = _Session
    app.dependency_overrides[verified_admin] = lambda: SimpleNamespace(
        id="owner-1", role="owner"
    )

    archive = _archive_bytes(_representative_payloads())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/admin/users/import",
            files={
                "file": (
                    "admin-users.zip",
                    archive.getvalue(),
                    "application/zip",
                )
            },
            data={
                "selected_indices": "[0]",
                "default_password": "TempPass123!",
                "force_password_change": "false",
            },
            headers={"user-agent": "pytest"},
        )

    assert response.status_code == 200
    assert captured["extension"] == "zip"
    assert captured["principal_id"] == "owner-1"
    assert captured["import_kind"] == "import_admin_users"
    queued = captured["queued"]
    assert queued["kind"] == "import_admin_users"
    assert queued["staged_name"] == staged_name
    assert queued["user_id"] == "owner-1"

    with zipfile.ZipFile(io.BytesIO(captured["staged_archive"])) as staged_archive:
        archive_utils.import_admin_users_archive(
            object(),
            staged_archive,
            selected_indices=queued["options"]["selected_indices"],
            import_options=queued["options"]["import_options"],
            allow_administrative_targets=queued["options"][
                "allow_administrative_targets"
            ],
        )

    imported_users = captured["payload"]["data"]["users"]
    assert [user["user_id"] for user in imported_users] == ["source-user-1"]
    assert "files" not in imported_users[0]
    assert captured["payload"]["import_options"] == {
        "default_password": "TempPass123!",
        "force_password_change": False,
    }
    assert captured["allow_administrative_targets"] is True
