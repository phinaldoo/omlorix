import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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

from app import dependencies
from app.auth.account_slots import ACCESS_COOKIE


def _request(*, method: str = "GET", headers=None, cookies=None) -> Request:
    raw_headers = []
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    if cookies:
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        raw_headers.append((b"cookie", cookie_header.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "server": ("chat.example", 443),
            "path": "/api/v1/probe",
            "headers": raw_headers,
        }
    )


def test_resolve_access_token_prefers_explicit_bearer_credentials():
    request = _request(headers={"Authorization": "Bearer header-token"}, cookies={ACCESS_COOKIE: "cookie-token"})
    credentials = SimpleNamespace(scheme="Bearer", credentials="credential-token")

    assert dependencies.resolve_access_token(request, credentials) == "credential-token"
    assert request.state.omlorix_access_token_source == "bearer"


def test_resolve_access_token_uses_authorization_header_before_cookie():
    request = _request(headers={"Authorization": "Bearer header-token"}, cookies={ACCESS_COOKIE: "cookie-token"})

    assert dependencies.resolve_access_token(request) == "header-token"
    assert request.state.omlorix_access_token_source == "bearer"


def test_resolve_access_token_uses_cookie_when_no_bearer_exists():
    request = _request(cookies={ACCESS_COOKIE: "cookie-token"})

    assert dependencies.resolve_access_token(request) == "cookie-token"
    assert request.state.omlorix_access_token_source == "cookie"


def test_resolve_access_token_rejects_missing_token():
    with pytest.raises(HTTPException) as exc:
        dependencies.resolve_access_token(_request())

    assert exc.value.status_code == 401


def test_cookie_auth_same_origin_guard_runs_only_for_unsafe_cookie_requests(monkeypatch):
    calls = []
    monkeypatch.setattr(dependencies, "enforce_same_origin", lambda request, db: calls.append((request.method, db)))

    safe_request = _request(method="GET")
    safe_request.state.omlorix_access_token_source = "cookie"
    dependencies._enforce_same_origin_for_cookie_auth(safe_request, db="db-safe")

    bearer_request = _request(method="POST")
    bearer_request.state.omlorix_access_token_source = "bearer"
    dependencies._enforce_same_origin_for_cookie_auth(bearer_request, db="db-bearer")

    unsafe_request = _request(method="POST")
    unsafe_request.state.omlorix_access_token_source = "cookie"
    dependencies._enforce_same_origin_for_cookie_auth(unsafe_request, db="db-unsafe")

    assert calls == [("POST", "db-unsafe")]
