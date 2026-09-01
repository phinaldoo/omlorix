import asyncio
from pathlib import Path
import re

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app.middleware.request_body_limit import (
    AUTH_REQUEST_BODY_LIMIT_BYTES,
    DEFAULT_REQUEST_BODY_LIMIT_BYTES,
    LARGE_REQUEST_BODY_LIMIT_BYTES,
    RequestBodyLimitMiddleware,
    is_explicit_large_body_route,
    resolve_request_body_limit_bytes,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_TEMPLATE = REPO_ROOT / "nginx/default.http.conf.template/default.conf"

LARGE_BODY_ROUTE_CASES = (
    ("POST", "/api/v1/files/upload"),
    ("POST", "/api/v1/files/canvas/save"),
    ("POST", "/api/v1/files/canvas/spreadsheet/save"),
    ("POST", "/api/v1/files/canvas/markdown/pdf"),
    ("POST", "/api/v1/files/canvas/latex/render"),
    ("POST", "/api/v1/chats/import/chatgpt"),
    ("POST", "/api/v1/chats/meetings/transcribe"),
    ("POST", "/api/v1/llm/transcribe"),
    ("POST", "/api/v1/users/import/self"),
    ("POST", "/api/v1/admin/users/import"),
    ("POST", "/api/v1/admin/ip-address/statistics/import"),
    ("POST", "/api/v1/admin/import/openwebui/chats"),
    ("POST", "/api/v1/admin/import/openwebui/chats/bulk"),
    ("POST", "/api/v1/agents/agent-1/assets"),
    ("POST", "/api/v1/skills/import-markdown-files"),
    ("POST", "/api/v1/skills/skill-1/files/assets"),
    ("POST", "/api/v1/skills/admin/import-files"),
    ("POST", "/api/v1/skills/admin/skill-1/files/references"),
    ("PUT", "/api/v1/presentations/presentation-1/editor"),
)


def _test_client() -> TestClient:
    app = Starlette()

    async def consume_body(request: Request):
        body = await request.body()
        return JSONResponse({"received": len(body)})

    app.add_route("/{path:path}", consume_body, methods=["POST", "PUT"])
    app.add_middleware(
        RequestBodyLimitMiddleware,
        auth_limit_bytes=8,
        default_limit_bytes=16,
        large_limit_bytes=32,
    )
    return TestClient(app)


def test_declared_oversized_auth_body_is_rejected_before_route_parsing():
    response = _test_client().post("/api/v1/auth/signin", content=b"x" * 9)

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body exceeds the allowed size."}


def test_ordinary_body_at_limit_remains_available_to_route():
    response = _test_client().post("/api/v1/chats/send", content=b"x" * 16)

    assert response.status_code == 200
    assert response.json() == {"received": 16}


def test_explicit_upload_route_retains_larger_allowance():
    response = _test_client().post("/api/v1/files/upload/", content=b"x" * 24)

    assert response.status_code == 200
    assert response.json() == {"received": 24}


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("POST", "/api/v1/files/upload/not-a-route"),
        ("PUT", "/api/v1/files/upload"),
        ("POST", "/api/v1/agents/agent-1/assets/from-files"),
        ("POST", "/api/v1/skills/admin/files/assets"),
        ("POST", "/api/v1/presentations/presentation-1/editor"),
    ),
)
def test_similar_or_wrong_method_routes_do_not_inherit_large_allowance(method, path):
    response = _test_client().request(method, path, content=b"x" * 17)

    assert response.status_code == 413


def _run_streamed_request(
    chunks: list[bytes],
    *,
    declared_length: bytes | None = None,
    with_inner_base_middleware: bool = False,
):
    received_by_app: list[bytes] = []
    sent: list[dict] = []
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def consumer(_scope, limited_receive, limited_send):
        while True:
            message = await limited_receive()
            if message["type"] != "http.request":
                break
            received_by_app.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        response = JSONResponse({"received": sum(map(len, received_by_app))})
        await response(_scope, limited_receive, limited_send)

    headers = []
    if declared_length is not None:
        headers.append((b"content-length", declared_length))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/chats/send",
        "raw_path": b"/api/v1/chats/send",
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.10", 12345),
        "server": ("testserver", 80),
    }
    inner_app = consumer
    if with_inner_base_middleware:
        async def pass_through(request, call_next):
            return await call_next(request)

        inner_app = BaseHTTPMiddleware(inner_app, dispatch=pass_through)

    middleware = RequestBodyLimitMiddleware(
        inner_app,
        auth_limit_bytes=4,
        default_limit_bytes=10,
        large_limit_bytes=20,
    )
    asyncio.run(middleware(scope, receive, send))
    return received_by_app, sent


def test_chunked_body_is_stopped_before_over_limit_chunk_reaches_parser():
    received_by_app, sent = _run_streamed_request([b"a" * 6, b"b" * 5])

    assert received_by_app == [b"a" * 6]
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 413


def test_underreported_content_length_cannot_bypass_streamed_byte_count():
    received_by_app, sent = _run_streamed_request(
        [b"a" * 6, b"b" * 5],
        declared_length=b"1",
    )

    assert received_by_app == [b"a" * 6]
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 413


def test_chunked_limit_survives_inner_base_http_middleware():
    received_by_app, sent = _run_streamed_request(
        [b"a" * 6, b"b" * 5],
        with_inner_base_middleware=True,
    )

    assert received_by_app == [b"a" * 6]
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 413


def test_chunked_body_at_limit_reaches_parser_unchanged():
    received_by_app, sent = _run_streamed_request([b"a" * 6, b"b" * 4])

    assert received_by_app == [b"a" * 6, b"b" * 4]
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 200


@pytest.mark.parametrize(("method", "path"), LARGE_BODY_ROUTE_CASES)
def test_only_named_workflows_receive_application_large_body_limit(method, path):
    assert is_explicit_large_body_route(method, path)
    assert resolve_request_body_limit_bytes(method, path) == LARGE_REQUEST_BODY_LIMIT_BYTES


def test_application_limit_tiers_are_hard_bounded():
    assert AUTH_REQUEST_BODY_LIMIT_BYTES == 1 * 1024 * 1024
    assert DEFAULT_REQUEST_BODY_LIMIT_BYTES == 16 * 1024 * 1024
    assert LARGE_REQUEST_BODY_LIMIT_BYTES == 512 * 1024 * 1024
    assert resolve_request_body_limit_bytes("POST", "/api/v1/auth/signin/") == (
        AUTH_REQUEST_BODY_LIMIT_BYTES
    )
    assert resolve_request_body_limit_bytes("POST", "/api/v1/chats/send") == (
        DEFAULT_REQUEST_BODY_LIMIT_BYTES
    )


def test_nginx_applies_small_auth_and_ordinary_limits_and_buffers_requests():
    source = NGINX_TEMPLATE.read_text(encoding="utf-8")
    signup = source.split("location = /api/v1/auth/signup {", 1)[1].split(
        "location ^~ /api/v1/auth/ {", 1
    )[0]
    auth = source.split("location ^~ /api/v1/auth/ {", 1)[1].split(
        "location = /api/v1/chats/shared/access {", 1
    )[0]
    ordinary = source.split("location /api/ {", 1)[1].split(
        "# FastAPI docs and schema", 1
    )[0]

    for auth_location in (signup, auth):
        assert "client_max_body_size 1M;" in auth_location
        assert "client_body_timeout 15s;" in auth_location
        assert "proxy_request_buffering on;" in auth_location
    assert "client_max_body_size 16M;" in ordinary
    assert "client_body_timeout 60s;" in ordinary
    assert "proxy_request_buffering on;" in ordinary
    assert "client_max_body_size 512M;" not in ordinary


def test_nginx_large_route_allowlist_matches_application_workflows():
    source = NGINX_TEMPLATE.read_text(encoding="utf-8")
    large_location = source.split(
        "# Only named upload, import, transcription, and large editor-save routes", 1
    )[1].split("# Proxy all other API requests", 1)[0]
    match = re.search(r'location ~ "([^"]+)"', large_location)

    assert match is not None
    nginx_large_path = re.compile(match.group(1))
    for _method, path in LARGE_BODY_ROUTE_CASES:
        assert nginx_large_path.fullmatch(path)
    assert "client_max_body_size 512M;" in large_location
    assert "client_body_timeout 300s;" in large_location
    assert "proxy_request_buffering off;" in large_location
    assert nginx_large_path.fullmatch("/api/v1/files/upload/not-a-route") is None
    assert nginx_large_path.fullmatch("/api/v1/agents/agent-1/assets/from-files") is None
    assert nginx_large_path.fullmatch("/api/v1/skills/admin/files/assets") is None


def test_main_registers_request_limit_before_route_parsing():
    source = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    assert "from app.middleware.request_body_limit import RequestBodyLimitMiddleware" in source
    registration = source.index("app.add_middleware(RequestBodyLimitMiddleware)")
    assert registration < source.index("app.include_router(admin_router)")
