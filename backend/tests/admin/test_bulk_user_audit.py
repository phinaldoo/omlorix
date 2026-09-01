from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from openpyxl import Workbook

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

from app.admin.users import router as admin_router
from app.admin.users import utils as bulk_user


def test_bulk_user_template_uses_modal_password_fields():
    assert bulk_user.BULK_USER_TEMPLATE_HEADERS == (
        "email",
        "first_name",
        "last_name",
    )
    assert bulk_user.REQUIRED_HEADERS == {"email", "first_name", "last_name"}
    assert bulk_user.SUPPORTED_HEADERS == bulk_user.REQUIRED_HEADERS


@pytest.mark.parametrize("header", ["password", "has_to_change_password"])
def test_csv_import_rejects_password_related_headers(header):
    contents = (
        f"email,first_name,last_name,{header}\n"
        "user@example.com,First,User,ignored\n"
    ).encode()

    with pytest.raises(
        bulk_user.BulkUserTemplateError,
        match=rf"unsupported headers: {header}",
    ):
        list(bulk_user.iter_csv_rows(contents))


def test_csv_import_rejects_duplicate_canonical_headers():
    contents = (
        "email, Email ,first_name,last_name\n"
        "first@example.com,second@example.com,First,User\n"
    ).encode()

    with pytest.raises(bulk_user.BulkUserTemplateError, match="duplicate headers: email"):
        list(bulk_user.iter_csv_rows(contents))


def test_xlsx_import_rejects_duplicate_canonical_headers():
    workbook = Workbook()
    workbook.active.append(["email", "Email ", "first_name", "last_name"])
    buffer = BytesIO()
    workbook.save(buffer)

    with pytest.raises(bulk_user.BulkUserTemplateError, match="duplicate headers: email"):
        list(bulk_user.iter_xlsx_rows(buffer.getvalue()))


def test_bulk_import_rejects_removed_2fa_secret_header():
    contents = (
        "email,first_name,last_name,2fa_secret\n"
        "user@example.com,First,User,JBSWY3DPEHPK3PXP\n"
    ).encode()

    with pytest.raises(
        bulk_user.BulkUserTemplateError,
        match="unsupported headers: 2fa_secret",
    ):
        list(bulk_user.iter_csv_rows(contents))


def test_csv_import_decodes_cp1252_before_latin1():
    contents = (
        "email,first_name,last_name\nuser@example.com,Pat,O’Connor\n"
    ).encode("cp1252")

    assert list(bulk_user.iter_csv_rows(contents)) == [
        (
            2,
            {
                "email": "user@example.com",
                "first_name": "Pat",
                "last_name": "O’Connor",
            },
        )
    ]


@pytest.mark.parametrize(
    "contents, invalid_column",
    [
        (
            "email,first_name,last_name\nuser@example.com,First,User,unexpected\n",
            4,
        ),
        (
            "email,first_name,last_name,\nuser@example.com,First,User,unexpected\n",
            4,
        ),
    ],
)
def test_csv_import_rejects_data_without_a_supported_header(contents, invalid_column):
    with pytest.raises(
        bulk_user.BulkUserTemplateError,
        match=rf"CSV row 2 .* columns: {invalid_column}",
    ):
        list(bulk_user.iter_csv_rows(contents.encode()))


def test_xlsx_import_rejects_data_under_a_blank_header():
    workbook = Workbook()
    workbook.active.append(["email", "first_name", "last_name", None])
    workbook.active.append(["user@example.com", "First", "User", "unexpected"])
    buffer = BytesIO()
    workbook.save(buffer)

    with pytest.raises(
        bulk_user.BulkUserTemplateError,
        match=r"XLSX row 2 .* columns: 4",
    ):
        list(bulk_user.iter_xlsx_rows(buffer.getvalue()))


def test_bulk_import_stops_after_maximum_row_count(monkeypatch):
    consumed_rows: list[int] = []

    def parser(_contents):
        for row_number in range(2, 12):
            consumed_rows.append(row_number)
            yield row_number, {
                "email": f"user-{row_number}@example.com",
                "first_name": "First",
                "last_name": "User",
            }

    monkeypatch.setattr(bulk_user, "MAX_BULK_USER_IMPORT_ROWS", 2)
    monkeypatch.setattr(bulk_user, "iter_csv_rows", parser)

    result = bulk_user.create_users_from_csv(
        b"ignored",
        object(),
        default_password="TemporaryPass123!",
    )

    assert consumed_rows == [2, 3, 4]
    assert result == {
        "status": "error",
        "created_users": [],
        "errors": ["Import file exceeds the 2 user limit"],
        "total_created": 0,
        "total_errors": 1,
    }


@pytest.mark.parametrize("accept_language", ["*", "*;q=0.5"])
def test_accept_language_wildcard_falls_back_to_english(accept_language):
    assert bulk_user._parse_accept_language(accept_language) == "en_US"


def test_bulk_user_upload_audit_details_include_bounded_created_targets(monkeypatch):
    monkeypatch.setattr(admin_router, "_BULK_USER_AUDIT_CREATED_USERS_LIMIT", 2)

    details = admin_router._build_bulk_user_upload_audit_details(
        filename="users.csv",
        file_type="csv",
        result={
            "status": "success",
            "total_created": 3,
            "total_errors": 0,
            "created_users": [
                {"id": "user-1", "email": "one@example.com", "first_name": "One"},
                {"id": "user-2", "email": "two@example.com", "first_name": "Two"},
                {"id": "user-3", "email": "three@example.com", "first_name": "Three"},
            ],
        },
    )

    assert details == {
        "filename": "users.csv",
        "status": "success",
        "total_created": 3,
        "total_errors": 0,
        "created_users": [
            {"user_id": "user-1"},
            {"user_id": "user-2"},
        ],
        "created_users_logged": 2,
        "created_users_omitted": 1,
        "file_type": "csv",
    }


def test_upload_users_bulk_route_audits_created_user_targets(monkeypatch):
    audit_calls: list[dict] = []
    queued: dict = {}
    upload_result = {
        "status": "success",
        "total_created": 1,
        "total_errors": 0,
        "created_users": [
            {
                "id": "user-1",
                "email": "new.user@example.com",
                "first_name": "New",
                "last_name": "User",
                "role": "user",
            }
        ],
        "errors": [],
    }

    monkeypatch.setattr(
        admin_router,
        "stage_import_stream",
        lambda _stream, *, extension, **identity: queued.update(identity)
        or queued.setdefault(
            "staged_name", f"staged.{extension}"
        ),
    )

    def fake_enqueue(_db, **kwargs):
        queued.update(kwargs)
        return SimpleNamespace(id="worker-job-1")

    monkeypatch.setattr(admin_router, "enqueue_import_job", fake_enqueue)
    monkeypatch.setattr(
        admin_router,
        "wait_for_operations_result",
        lambda _job: dict(upload_result, file_type="csv"),
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = object()
    db_log = object()
    admin_user = SimpleNamespace(id="admin-1")
    file = SimpleNamespace(
        filename="users.csv",
        file=BytesIO(b"email,first_name,last_name\nnew.user@example.com,New,User\n"),
    )

    result = admin_router.upload_users_bulk_route(
        request,
        file,
        db,
        db_log,
        admin_user,
        default_password="temporary-password",
        force_password_change=True,
    )

    assert result == dict(upload_result, force_password_change=True)
    assert queued["kind"] == "import_bulk_users"
    assert queued["principal_id"] == "admin-1"
    assert queued["import_kind"] == "import_bulk_users"
    assert queued["options"] == {
        "default_password": "temporary-password",
        "force_password_change": True,
    }
    assert audit_calls == [
        {
            "db_log": db_log,
            "user_id": "admin-1",
            "action": "UPLOAD_USERS_BULK",
            "details": {
                "filename": "users.csv",
                "status": "success",
                "total_created": 1,
                "total_errors": 0,
                "created_users": [{"user_id": "user-1"}],
                "created_users_logged": 1,
                "created_users_omitted": 0,
                "file_type": "csv",
                "force_password_change": True,
            },
            "ip_address": "203.0.113.10",
            "user_agent": "pytest",
            "category": "admin",
        }
    ]


def test_admin_zip_import_resolves_audit_ip_with_worker_thread_session(monkeypatch):
    """The async route must not run trusted-proxy database reads on its event loop."""
    from fastapi import UploadFile

    class _Session:
        def __init__(self, name):
            self.name = name
            self.closed = False

        def close(self):
            self.closed = True

    archive_bytes = BytesIO()
    import zipfile

    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("manifest.json", "{}")
    archive_bytes.seek(0)
    upload = UploadFile(filename="admin-users.zip", file=archive_bytes)

    sessions = iter([_Session("audit-ip")])
    audit_session = _Session("audit-log")
    audit_calls = []
    ip_lookup_sessions = []

    class _Request:
        headers = {
            "content-type": "multipart/form-data; boundary=pytest",
            "user-agent": "pytest",
        }

        async def form(self):
            return {
                "file": upload,
                "default_password": "TempPass123!",
                "force_password_change": "true",
            }

    monkeypatch.setattr(admin_router, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(admin_router, "AuditSessionLocal", lambda: audit_session)
    monkeypatch.setattr(
        admin_router,
        "stage_import_stream",
        lambda _stream, *, extension, **_identity: f"staged.{extension}",
    )
    async def enqueue_job(**_kwargs):
        return SimpleNamespace(id="worker-job-1")

    monkeypatch.setattr(admin_router, "enqueue_import_job_async", enqueue_job)
    async def wait_for_result(_job):
        return {"created": [], "updated": []}

    monkeypatch.setattr(
        admin_router,
        "wait_for_operations_result_async",
        wait_for_result,
    )
    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, db: ip_lookup_sessions.append(db) or "203.0.113.10",
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    request_db = _Session("request")
    result = __import__("asyncio").run(
        admin_router.admin_import_users_route(
            request=_Request(),
            db=request_db,
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1", role="admin"),
        )
    )

    assert result == {"created": [], "updated": []}
    assert [session.name for session in ip_lookup_sessions] == ["audit-ip"]
    assert request_db not in ip_lookup_sessions
    assert audit_calls[0]["action"] == "IMPORT_USERS_ADMIN"
    assert audit_session.closed is True
