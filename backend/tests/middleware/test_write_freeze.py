from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.middleware import write_freeze


def _client_with_write_freeze_middleware() -> TestClient:
    app = Starlette()

    async def ok(_request):
        return PlainTextResponse("ok")

    app.add_route("/{path:path}", ok, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    app.add_middleware(write_freeze.WriteFreezeMiddleware)
    return TestClient(app)


def test_write_freeze_blocks_mutating_api_requests(monkeypatch):
    monkeypatch.setattr(write_freeze, "is_write_freeze_active", lambda: True)
    monkeypatch.setattr(write_freeze, "get_write_freeze_details", lambda: {"reason": "backup"})

    response = _client_with_write_freeze_middleware().post("/api/v1/chats/send", json={"message": "hello"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Writes are temporarily disabled while a backup or restore operation is running.",
        "maintenance": {"reason": "backup"},
    }
    assert response.headers["Retry-After"] == "30"


def test_restore_freeze_blocks_reads_before_they_open_database_sessions(monkeypatch):
    monkeypatch.setattr(write_freeze, "is_write_freeze_active", lambda: True)
    monkeypatch.setattr(write_freeze, "get_write_freeze_details", lambda: {"reason": "restore"})
    client = _client_with_write_freeze_middleware()

    response = client.get("/api/v1/chats/send")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Reads and writes are temporarily disabled while a restore operation is running.",
        "maintenance": {"reason": "restore"},
    }
    assert response.headers["Retry-After"] == "30"
    assert client.get("/api/v1/admin/backups/jobs").status_code == 503
    assert client.get("/ready").status_code == 200


def test_backup_freeze_allows_reads_and_backup_admin_routes(monkeypatch):
    monkeypatch.setattr(write_freeze, "is_write_freeze_active", lambda: True)
    monkeypatch.setattr(write_freeze, "get_write_freeze_details", lambda: {"reason": "backup"})
    client = _client_with_write_freeze_middleware()

    assert client.get("/api/v1/chats/send").status_code == 200
    assert client.post("/api/v1/admin/backups/start").status_code == 200


def test_write_freeze_passes_mutations_when_inactive(monkeypatch):
    monkeypatch.setattr(write_freeze, "is_write_freeze_active", lambda: False)

    response = _client_with_write_freeze_middleware().delete("/api/v1/chats/chat-1")

    assert response.status_code == 200
    assert response.text == "ok"
