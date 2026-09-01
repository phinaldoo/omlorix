from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile

from app.chats import router as chats_router_module
from app.dependencies import verified_user


def _request():
    return SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.10"),
        headers={"user-agent": "pytest"},
    )


def _expanded_routes(container):
    for route in container.routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            yield from _expanded_routes(included_router)
        else:
            yield route


def test_chatgpt_import_route_is_authenticated_and_typed():
    api = FastAPI()
    api.include_router(chats_router_module.chats_router)
    route = next(
        route
        for route in _expanded_routes(api)
        if route.path == "/api/v1/chats/import/chatgpt"
    )

    assert route.methods == {"POST"}
    assert route.response_model is chats_router_module.ChatGPTArchiveImportResult
    assert verified_user in {dependency.call for dependency in route.dependant.dependencies}


def test_chatgpt_import_route_checks_policy_invokes_parser_and_audits(monkeypatch):
    gate_calls = []
    operation_calls = []
    audit_calls = []
    result = {
        "imported_chats": 2,
        "imported_messages": 8,
        "imported_files": 1,
        "skipped_chats": 1,
        "skipped_duplicates": 1,
        "shared_index_entries": 0,
    }
    upload = UploadFile(filename="../private/chatgpt-export.zip", file=io.BytesIO(b"archive"))

    monkeypatch.setattr(
        chats_router_module,
        "ensure_data_control_permission",
        lambda user_id, key_name, db, detail=None: gate_calls.append(
            {"user_id": user_id, "key_name": key_name, "detail": detail}
        ),
    )

    def fake_stage(archive_file, *, extension, principal_id, import_kind):
        operation_calls.append(
            {
                "archive_file": archive_file,
                "extension": extension,
                "principal_id": principal_id,
                "import_kind": import_kind,
            }
        )
        assert archive_file.closed is False
        return "staged.zip"

    monkeypatch.setattr(chats_router_module, "stage_import_stream", fake_stage)
    monkeypatch.setattr(
        chats_router_module,
        "enqueue_import_job",
        lambda db, **kwargs: operation_calls.append({"db": db, **kwargs})
        or SimpleNamespace(id="job-1"),
    )
    monkeypatch.setattr(
        chats_router_module,
        "wait_for_operations_result",
        lambda job: operation_calls.append({"job_id": job.id}) or result,
    )
    monkeypatch.setattr(chats_router_module, "get_audit_request_ip", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(
        chats_router_module,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    db = object()
    db_log = object()

    response = chats_router_module.import_chatgpt_archive_route(
        request=_request(),
        archive=upload,
        db=db,
        db_log=db_log,
        user=SimpleNamespace(id="user-1"),
    )

    assert response == result
    assert upload.file.closed is True
    assert gate_calls == [
        {
            "user_id": "user-1",
            "key_name": "allow_user_data",
            "detail": "ChatGPT archive import is disabled for your group's data controls.",
        }
    ]
    assert operation_calls == [
        {
            "archive_file": upload.file,
            "extension": "zip",
            "principal_id": "user-1",
            "import_kind": "import_chatgpt",
        },
        {
            "db": db,
            "kind": "import_chatgpt",
            "staged_name": "staged.zip",
            "user_id": "user-1",
            "options": {"archive_name": "chatgpt-export.zip"},
        },
        {"job_id": "job-1"},
    ]
    assert audit_calls[0]["action"] == "IMPORT_CHATGPT_ARCHIVE"
    assert audit_calls[0]["details"] == result
    assert audit_calls[0]["category"] == "user"


def test_chatgpt_import_route_closes_upload_after_parser_failure(monkeypatch):
    upload = UploadFile(filename="broken.zip", file=io.BytesIO(b"not-a-zip"))
    audit_calls = []

    monkeypatch.setattr(chats_router_module, "ensure_data_control_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chats_router_module, "stage_import_stream", lambda *_args, **_kwargs: "staged.zip")
    monkeypatch.setattr(
        chats_router_module,
        "enqueue_import_job",
        lambda *_args, **_kwargs: SimpleNamespace(id="job-1"),
    )
    monkeypatch.setattr(
        chats_router_module,
        "wait_for_operations_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=400, detail="bad archive")
        ),
    )
    monkeypatch.setattr(
        chats_router_module,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc_info:
        chats_router_module.import_chatgpt_archive_route(
            request=_request(),
            archive=upload,
            db=object(),
            db_log=object(),
            user=SimpleNamespace(id="user-1"),
        )

    assert exc_info.value.status_code == 400
    assert upload.file.closed is True
    assert audit_calls == []
