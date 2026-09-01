import sys
import threading
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.middleware.cors import DynamicCORSMiddleware, invalidate_cors_allowed_origins


def test_cors_origin_allowlist_refreshes_after_explicit_invalidation():
    allowed_origins = ["https://old.example.test"]
    resolver_calls = 0

    def resolve_origins():
        nonlocal resolver_calls
        resolver_calls += 1
        return allowed_origins

    app = Starlette()

    async def probe(_request):
        return PlainTextResponse("ok")

    app.add_route("/probe", probe)
    app.add_middleware(
        DynamicCORSMiddleware,
        allow_origin_resolver=resolve_origins,
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization"],
    )
    client = TestClient(app)

    old_response = client.get("/probe", headers={"Origin": "https://old.example.test"})
    assert old_response.headers["access-control-allow-origin"] == "https://old.example.test"

    allowed_origins[:] = ["https://new.example.test"]
    invalidate_cors_allowed_origins()

    stale_response = client.get("/probe", headers={"Origin": "https://old.example.test"})
    assert "access-control-allow-origin" not in stale_response.headers

    new_response = client.get("/probe", headers={"Origin": "https://new.example.test"})
    assert new_response.headers["access-control-allow-origin"] == "https://new.example.test"

    preflight_response = client.options(
        "/probe",
        headers={
            "Origin": "https://new.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight_response.headers["access-control-allow-origin"] == "https://new.example.test"
    assert resolver_calls == 2


def test_cors_resolver_runs_off_the_request_event_loop():
    resolver_threads: list[int] = []
    handler_threads: list[int] = []

    def resolve_origins():
        resolver_threads.append(threading.get_ident())
        return ["https://allowed.example.test"]

    app = Starlette()

    async def probe(_request):
        handler_threads.append(threading.get_ident())
        return PlainTextResponse("ok")

    app.add_route("/probe", probe)
    app.add_middleware(
        DynamicCORSMiddleware,
        allow_origin_resolver=resolve_origins,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )

    with TestClient(app) as client:
        response = client.get(
            "/probe",
            headers={"Origin": "https://allowed.example.test"},
        )

    assert response.headers["access-control-allow-origin"] == "https://allowed.example.test"
    assert resolver_threads
    assert handler_threads
    assert resolver_threads[0] != handler_threads[0]


def test_cors_malformed_origin_is_rejected_without_error():
    app = Starlette()

    async def probe(_request):
        return PlainTextResponse("ok")

    app.add_route("/probe", probe)
    app.add_middleware(
        DynamicCORSMiddleware,
        allow_origin_resolver=lambda: ["https://allowed.example.test"],
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization"],
    )
    client = TestClient(app)

    for origin in ("http://[::1", "http://example.com:bad", "http://example.com:99999"):
        response = client.get("/probe", headers={"Origin": origin})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

        preflight_response = client.options(
            "/probe",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight_response.status_code == 400
        assert "access-control-allow-origin" not in preflight_response.headers
