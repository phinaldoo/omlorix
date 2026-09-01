"""Protocol-level regression coverage for the global IP restriction policy."""

from __future__ import annotations

import threading

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.middleware import ip_restriction


_LIVE_TRANSCRIPTION_PATH = "/api/v1/realtime/transcription/live"


class _FakeDb:
    """Minimal middleware database session that records proper cleanup."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _websocket_test_app(fake_db: _FakeDb, route_calls: list[str]) -> FastAPI:
    """Build an app whose WebSocket route reveals middleware fallthrough."""

    app = FastAPI()
    app.state.db = lambda: fake_db

    @app.websocket(_LIVE_TRANSCRIPTION_PATH)
    async def websocket_route(websocket: WebSocket) -> None:
        route_calls.append("entered")
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close()

    app.add_middleware(ip_restriction.IPRestrictionMiddleware)
    return app


def test_ip_policy_rejects_websocket_before_route_execution(monkeypatch):
    """A denied IP must not bypass policy through a WebSocket handshake."""

    fake_db = _FakeDb()
    route_calls: list[str] = []
    app = _websocket_test_app(fake_db, route_calls)

    async def deny_request(_ip, _db):
        return False, "ip_blocklist", None

    monkeypatch.setattr(
        ip_restriction,
        "ip_restrictions_disabled_by_environment",
        lambda: False,
    )
    monkeypatch.setattr(
        ip_restriction,
        "get_client_ip",
        lambda _connection, _db: "203.0.113.9",
    )
    monkeypatch.setattr(
        ip_restriction,
        "check_blocked_ip_address",
        lambda _ip, _db: False,
    )
    monkeypatch.setattr(
        ip_restriction,
        "_load_ip_policy_settings_snapshot",
        lambda _db: ip_restriction._IPPolicySettingsSnapshot({}),
    )
    monkeypatch.setattr(ip_restriction, "evaluate_ip_policy", deny_request)
    monkeypatch.setattr(
        ip_restriction,
        "record_ip_address_security_event",
        lambda *_args, **_kwargs: None,
    )

    with TestClient(app) as client:
        try:
            with client.websocket_connect(_LIVE_TRANSCRIPTION_PATH) as websocket:
                websocket.receive_text()
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        else:
            raise AssertionError("denied WebSocket unexpectedly reached the route")

    assert route_calls == []
    assert fake_db.closed is True


def test_ip_policy_allows_websocket_after_successful_decision(monkeypatch):
    """An allowed IP must retain normal WebSocket behavior."""

    fake_db = _FakeDb()
    route_calls: list[str] = []
    app = _websocket_test_app(fake_db, route_calls)

    async def allow_request(_ip, _db):
        return True, None, None

    monkeypatch.setattr(
        ip_restriction,
        "ip_restrictions_disabled_by_environment",
        lambda: False,
    )
    monkeypatch.setattr(
        ip_restriction,
        "get_client_ip",
        lambda _connection, _db: "203.0.113.10",
    )
    monkeypatch.setattr(
        ip_restriction,
        "check_blocked_ip_address",
        lambda _ip, _db: False,
    )
    monkeypatch.setattr(
        ip_restriction,
        "_load_ip_policy_settings_snapshot",
        lambda _db: ip_restriction._IPPolicySettingsSnapshot({}),
    )
    monkeypatch.setattr(ip_restriction, "evaluate_ip_policy", allow_request)

    with TestClient(app) as client:
        with client.websocket_connect(_LIVE_TRANSCRIPTION_PATH) as websocket:
            assert websocket.receive_text() == "ready"

    assert route_calls == ["entered"]
    assert fake_db.closed is True


def test_environment_override_preserves_websocket_access(monkeypatch):
    """The documented emergency override must bypass policy for WebSockets."""

    fake_db = _FakeDb()
    route_calls: list[str] = []
    app = _websocket_test_app(fake_db, route_calls)

    monkeypatch.setattr(
        ip_restriction,
        "ip_restrictions_disabled_by_environment",
        lambda: True,
    )

    async def fail_policy(*_args):
        raise AssertionError("disabled policy must not be evaluated")

    monkeypatch.setattr(
        ip_restriction,
        "evaluate_ip_policy",
        fail_policy,
    )

    with TestClient(app) as client:
        with client.websocket_connect(_LIVE_TRANSCRIPTION_PATH) as websocket:
            assert websocket.receive_text() == "ready"

    assert route_calls == ["entered"]
    assert fake_db.closed is False


def test_ip_policy_database_session_is_created_and_closed_in_worker(monkeypatch):
    """Synchronous middleware persistence must never run on the ASGI thread."""

    thread_ids: dict[str, int] = {}

    class ThreadOwnedDb:
        def __init__(self) -> None:
            self.owner_thread = threading.get_ident()
            self.closed = False

        def close(self) -> None:
            assert threading.get_ident() == self.owner_thread
            self.closed = True

    sessions: list[ThreadOwnedDb] = []
    app = FastAPI()

    def db_factory():
        thread_ids["db"] = threading.get_ident()
        session = ThreadOwnedDb()
        sessions.append(session)
        return session

    app.state.db = db_factory

    @app.get("/api/v1/auth/access-status")
    async def allowed_route():
        thread_ids["route"] = threading.get_ident()
        return {"status": "ok"}

    app.add_middleware(ip_restriction.IPRestrictionMiddleware)

    def get_ip(_connection, db):
        assert threading.get_ident() == db.owner_thread
        return "198.51.100.10"

    def get_setting(_page, _key, db):
        assert threading.get_ident() == db.owner_thread
        return None

    async def allow_request(_ip, settings):
        thread_ids["policy"] = threading.get_ident()
        assert isinstance(settings, ip_restriction._IPPolicySettingsSnapshot)
        return True, None, None

    monkeypatch.setattr(
        ip_restriction,
        "ip_restrictions_disabled_by_environment",
        lambda: False,
    )
    monkeypatch.setattr(ip_restriction, "get_client_ip", get_ip)
    monkeypatch.setattr(
        ip_restriction,
        "check_blocked_ip_address",
        lambda _ip, db: threading.get_ident() != db.owner_thread,
    )
    monkeypatch.setattr(
        ip_restriction,
        "get_value_by_page_and_key",
        get_setting,
    )
    monkeypatch.setattr(ip_restriction, "evaluate_ip_policy", allow_request)

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/access-status")

    assert response.status_code == 200
    assert thread_ids["policy"] == thread_ids["route"]
    assert thread_ids["db"] != thread_ids["route"]
    assert len(sessions) == 1
    assert sessions[0].closed is True
