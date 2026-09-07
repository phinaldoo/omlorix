"""Integration coverage for the authenticated ACP terminal WebSocket route."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_db, get_db_log, verified_websocket_user
from app.dependencies import verified_user
from app.llm.acp import runtime as acp_runtime_module
from app.remote_connections import router as terminal_router_module
from app.remote_connections.router import remote_connections_router


def test_terminal_websocket_resolves_model_and_streams_binary_io(monkeypatch):
    """Exercise the route handshake, input flow, output flow, and clean exit."""
    app = FastAPI()
    app.include_router(remote_connections_router)
    app.dependency_overrides[verified_websocket_user] = lambda: SimpleNamespace(
        id="user-one"
    )
    class Db:
        """Track that the route releases its main session before terminal I/O."""

        closed = False

        def close(self):
            self.closed = True

    db = Db()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_db_log] = lambda: object()

    profile = SimpleNamespace(
        id="profile-one",
        model_id="model-one",
        workspace_root="/srv/project",
        cwd=".",
    )
    connection = SimpleNamespace(
        id="connection-one",
        name="Development Mac",
        host="mac.example.test",
        port=22,
        username="omlorix",
        host_key="ssh-ed25519 AAAATest",
        config={"connect_timeout_seconds": 15},
        secrets={"private_key": "private"},
    )
    resolved = []
    audits = []

    def fake_resolve(_db, user_id, model_id):
        resolved.append((user_id, model_id))
        return profile, connection

    async def fake_terminal(websocket, target, **options):
        assert db.closed is True
        assert target.id == connection.id
        assert target.name == connection.name
        assert options["workspace_root"] == "/srv/project"
        await websocket.send_json({"type": "ready", "title": target.name})
        message = await websocket.receive_json()
        await websocket.send_bytes(message["data"].encode("utf-8"))
        return 0

    monkeypatch.setattr(
        terminal_router_module, "get_terminal_target_for_model", fake_resolve
    )
    monkeypatch.setattr(
        terminal_router_module, "require_custom_acp_connections", lambda *_args: None
    )
    monkeypatch.setattr(terminal_router_module, "run_ssh_terminal", fake_terminal)
    monkeypatch.setattr(
        terminal_router_module,
        "_audit",
        lambda _db, _request, _user_id, action, details: audits.append(
            (action, details)
        ),
    )

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/remote-connections/acp-terminal?model_id=model-one&cols=100&rows=30"
        ) as websocket:
            assert websocket.receive_json() == {
                "type": "ready",
                "title": "Development Mac",
            }
            websocket.send_json({"type": "input", "data": "pwd\r"})
            assert websocket.receive_bytes() == b"pwd\r"

    assert resolved == [("user-one", "model-one")]
    assert [entry[0] for entry in audits] == [
        "USER_ACP_TERMINAL_OPENED",
        "USER_ACP_TERMINAL_CLOSED",
    ]


def test_acp_model_discovery_resolves_owned_connection(monkeypatch):
    """The HTTP model picker reuses the authenticated user's ACP session."""
    app = FastAPI()
    app.include_router(remote_connections_router)
    app.dependency_overrides[verified_user] = lambda: SimpleNamespace(id="user-one")
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_db_log] = lambda: object()

    profile = SimpleNamespace(
        id="profile-one",
        model_id="model-one",
        executable="codex-acp",
        arguments=[],
        workspace_root="/srv/project",
        cwd=".",
        additional_directories=["docs"],
    )
    connection = SimpleNamespace(id="connection-one")
    calls = []

    monkeypatch.setattr(
        terminal_router_module,
        "get_terminal_target_for_model",
        lambda _db, user_id, model_id: (
            calls.append(("resolve", user_id, model_id)) or (profile, connection)
        ),
    )
    monkeypatch.setattr(
        terminal_router_module, "require_custom_acp_connections", lambda *_args: None
    )
    monkeypatch.setattr(
        terminal_router_module, "enforce_same_origin", lambda _request, _db: None
    )
    monkeypatch.setattr(
        terminal_router_module, "_audit", lambda *_args, **_kwargs: None
    )

    async def fake_discovery(**kwargs):
        calls.append(("discover", kwargs))
        return {
            "session_id": "session-one",
            "current_model_id": "gpt-5.4",
            "models": [{"id": "gpt-5.4", "name": "GPT-5.4", "description": None}],
            "current_security_level": "agent",
            "security_levels": [
                {"id": "read-only", "name": "Read-only", "description": None},
                {"id": "agent", "name": "Agent", "description": None},
            ],
            "current_reasoning_effort": "high",
            "reasoning_efforts": [
                {"id": "low", "name": "Low", "description": None},
                {"id": "high", "name": "High", "description": None},
            ],
            "reasoning_efforts_by_model": {"gpt-5.4": ["low", "high"]},
        }

    monkeypatch.setattr(acp_runtime_module, "discover_acp_models", fake_discovery)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/remote-connections/acp/models",
            json={"model_id": "model-one", "session_id": "session-existing"},
        )

    assert response.status_code == 200
    assert response.json()["models"][0]["id"] == "gpt-5.4"
    assert response.json()["current_security_level"] == "agent"
    assert response.json()["current_reasoning_effort"] == "high"
    assert response.json()["reasoning_efforts_by_model"] == {"gpt-5.4": ["low", "high"]}
    assert calls[0] == ("resolve", "user-one", "model-one")
    assert calls[1][1]["existing_session_id"] == "session-existing"
    assert calls[1][1]["session_cwd"] == "/srv/project"
    assert calls[1][1]["additional_directories"] == ["/srv/project/docs"]
