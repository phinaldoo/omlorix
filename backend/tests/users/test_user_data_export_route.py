import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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

from app.users import router as users_router


async def _read_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks)


def _request():
    return SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.10"),
        headers={"user-agent": "pytest"},
    )


def test_user_data_export_route_checks_group_policy(monkeypatch, tmp_path):
    payload = {"user_id": "user-1"}
    gate_calls = []
    export_calls = []
    db = object()
    result_path = tmp_path / "export.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        users_router,
        "ensure_data_control_permission",
        lambda user_id, key_name, db, detail=None: gate_calls.append(
            {"user_id": user_id, "key_name": key_name, "detail": detail}
        ),
    )
    monkeypatch.setattr(
        users_router,
        "enqueue_user_data_export",
        lambda db, *, user_id: export_calls.append({"db": db, "user_id": user_id})
        or SimpleNamespace(id="export-job-1"),
    )
    monkeypatch.setattr(
        users_router,
        "wait_for_operations_result",
        lambda job: {"result_name": "export.json"},
    )
    monkeypatch.setattr(
        users_router,
        "resolve_operations_result_path",
        lambda name: result_path,
    )
    monkeypatch.setattr(
        users_router,
        "build_user_data_export_audit_details",
        lambda user_id, db_log, db=None: {"sections": ["user_id"], "has_activity_logs": False},
    )
    monkeypatch.setattr(users_router, "create_audit_log", lambda **kwargs: None)

    response = users_router.export_user_data_route(
        request=_request(),
        db=db,
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert json.loads(asyncio.run(_read_streaming_response(response))) == payload
    assert gate_calls == [
        {
            "user_id": "user-1",
            "key_name": "allow_user_data",
            "detail": "User data export is disabled for your group's data controls.",
        }
    ]
    assert export_calls == [{"db": db, "user_id": "user-1"}]


def test_user_data_self_import_route_uses_the_same_complete_archive_policy(monkeypatch):
    """The unified policy is authoritative for every section in a self restore."""
    gate_calls = []
    import_calls = []
    payload = {
        "export_type": "user_data",
        "export_version": 1.0,
        "notes": {"data": {"notes": [{"id": "note-1"}]}},
        "memories": {"data": {"memories": [{"content": "Remember this"}]}},
    }

    monkeypatch.setattr(
        users_router,
        "ensure_data_control_permission",
        lambda user_id, key_name, db, detail=None: gate_calls.append(
            {"user_id": user_id, "key_name": key_name, "detail": detail}
        ),
    )
    monkeypatch.setattr(
        users_router,
        "stage_import_json",
        lambda archive, **_identity: "staged.json",
    )
    monkeypatch.setattr(
        users_router,
        "enqueue_import_job",
        lambda _db, **kwargs: import_calls.append(kwargs)
        or SimpleNamespace(id="import-job-1"),
    )
    monkeypatch.setattr(
        users_router,
        "wait_for_operations_result",
        lambda job: {"imported": ["notes", "memories"], "skipped_sections": [], "errors": []},
    )
    monkeypatch.setattr(users_router, "create_audit_log", lambda **_kwargs: None)

    response = users_router.import_user_data_self_route(
        payload=payload,
        request=_request(),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert json.loads(response.body) == {
        "imported": ["notes", "memories"],
        "skipped_sections": [],
        "errors": [],
    }
    assert gate_calls == [
        {
            "user_id": "user-1",
            "key_name": "allow_user_data",
            "detail": "User data import is disabled for your group's data controls.",
        }
    ]
    assert import_calls == [
        {
            "kind": "import_user_self",
            "staged_name": "staged.json",
            "user_id": "user-1",
        }
    ]
