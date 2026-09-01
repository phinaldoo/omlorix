from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import PlainTextResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.middleware import rate_limiter


REPO_ROOT = Path(__file__).resolve().parents[3]


def _rate_limit_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("203.0.113.10", 12345),
            "path": "/api/v1/auth/login",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


def test_rate_limiter_uses_atomic_async_redis_attempt(monkeypatch):
    calls: list[tuple[str, int, str, int]] = []

    class FakeAsyncRedis:
        async def eval(self, script, key_count, key, ttl):
            calls.append((script, key_count, key, ttl))
            return [1, ttl]

    async def get_client():
        return FakeAsyncRedis()

    async def call_next(_request):
        return PlainTextResponse("ok")

    monkeypatch.setattr(rate_limiter, "get_async_redis_client", get_client)
    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)
    response = asyncio.run(limiter.dispatch(_rate_limit_request(), call_next))

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == str(limiter._auth_rule.limit - 1)
    assert len(calls) == 1
    script, key_count, key, ttl = calls[0]
    assert "INCR" in script
    assert "EXPIRE" in script
    assert "TTL" in script
    assert key_count == 1
    assert key.startswith("omlorix:ratelimit:auth:ip:203.0.113.10:")
    assert 1 <= ttl <= limiter._auth_rule.window_seconds + 1


def test_rate_limiter_falls_back_locally_when_async_redis_fails(monkeypatch):
    class BrokenAsyncRedis:
        async def eval(self, *_args):
            raise ConnectionError("redis unavailable")

    async def get_client():
        return BrokenAsyncRedis()

    async def call_next(_request):
        return PlainTextResponse("ok")

    monkeypatch.setenv("RATE_LIMIT_AUTH_RPM", "1")
    monkeypatch.setattr(rate_limiter, "get_async_redis_client", get_client)
    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)

    first = asyncio.run(limiter.dispatch(_rate_limit_request(), call_next))
    second = asyncio.run(limiter.dispatch(_rate_limit_request(), call_next))

    assert first.status_code == 200
    assert second.status_code == 429


def test_rate_limiter_refreshes_database_proxy_settings_off_loop(monkeypatch):
    thread_ids: dict[str, int] = {}

    class ThreadOwnedDb:
        def __init__(self):
            self.owner_thread = threading.get_ident()
            self.closed = False

        def close(self):
            assert threading.get_ident() == self.owner_thread
            self.closed = True

    session: ThreadOwnedDb | None = None

    def db_factory():
        nonlocal session
        thread_ids["db"] = threading.get_ident()
        session = ThreadOwnedDb()
        return session

    def resolve_proxies(db, *_env_names):
        if db is None:
            return []
        thread_ids["settings"] = threading.get_ident()
        assert threading.get_ident() == db.owner_thread
        return []

    async def get_client():
        return None

    async def call_next(_request):
        thread_ids["handler"] = threading.get_ident()
        return PlainTextResponse("ok")

    for name in ("TRUSTED_PROXIES", "OMLORIX_TRUSTED_PROXIES", "RATE_LIMIT_TRUSTED_PROXIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        rate_limiter,
        "resolve_configured_trusted_proxy_networks",
        resolve_proxies,
    )
    monkeypatch.setattr(rate_limiter, "get_async_redis_client", get_client)

    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)
    request = _rate_limit_request()
    request.scope["app"] = SimpleNamespace(state=SimpleNamespace(db=db_factory))
    response = asyncio.run(limiter.dispatch(request, call_next))

    assert response.status_code == 200
    assert thread_ids["db"] == thread_ids["settings"]
    assert thread_ids["db"] != thread_ids["handler"]
    assert session is not None and session.closed is True


def test_bundled_compose_uses_dynamic_networking_and_fails_closed_by_default():
    compose_files = (
        "docker-compose.server.yml",
        "docker-compose.managed-cloud.yml",
    )

    for compose_file in compose_files:
        source = (REPO_ROOT / compose_file).read_text(encoding="utf-8")

        assert "TRUSTED_PROXIES=${TRUSTED_PROXIES:-}" in source or (
            "TRUSTED_PROXIES: ${TRUSTED_PROXIES:-}" in source
        )
        assert "ipv4_address:" not in source
        assert "subnet: 172.31.250.0/24" not in source

    port_overlay = (REPO_ROOT / "docker-compose.frontend-port.yml").read_text(encoding="utf-8")
    assert "${FRONTEND_HTTP_HOST_BIND:-127.0.0.1}" in port_overlay


def test_frontend_host_bind_defaults_and_exposure_guidance_stay_aligned():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    env_metadata = json.loads(
        (REPO_ROOT / "electron" / "env-metadata.json").read_text(encoding="utf-8")
    )
    metadata_description = env_metadata["fields"]["FRONTEND_HTTP_HOST_BIND"]["description"]

    assert "FRONTEND_HTTP_HOST_BIND=127.0.0.1" in env_example
    assert "FRONTEND_HTTP_HOST_BIND=0.0.0.0" not in env_example
    assert "Loopback is the safe default" in env_example
    assert "FRONTEND_HTTP_HOST_BIND=0.0.0.0" in readme
    assert "FRONTEND_HTTP_HOST_BIND=127.0.0.1" in readme
    assert "The default is 127.0.0.1" in metadata_description

    for guidance in (env_example, readme, metadata_description):
        assert "firewall" in guidance.lower()
        assert "trusted-host policy" in guidance.lower()
        assert "reverse-proxy/tls design" in guidance.lower()


def test_rate_limiter_warns_when_forwarded_headers_have_no_trusted_proxies(monkeypatch, caplog):
    monkeypatch.delenv("RATE_LIMIT_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("172.18.0.4", 12345),
            "path": "/api/v1/auth/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.10")],
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )

    with caplog.at_level(logging.WARNING, logger=rate_limiter.logger.name):
        assert limiter._resolve_subject(request) == "ip:172.18.0.4"

    assert "Forwarded client IP headers are present but no trusted proxy CIDRs are configured" in caplog.text


def test_rate_limiter_separates_clients_behind_bundled_frontend_proxy(monkeypatch, caplog):
    monkeypatch.delenv("RATE_LIMIT_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("TRUSTED_PROXIES", "172.31.250.10/32")

    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)

    def request_for(forwarded_for: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "server": ("127.0.0.1", 8000),
                "client": ("172.31.250.10", 12345),
                "path": "/api/v1/auth/login",
                "headers": [(b"x-forwarded-for", forwarded_for.encode("ascii"))],
                "app": SimpleNamespace(state=SimpleNamespace()),
            }
        )

    with caplog.at_level(logging.WARNING, logger=rate_limiter.logger.name):
        assert limiter._resolve_subject(request_for("198.51.100.50")) == "ip:198.51.100.50"
        assert limiter._resolve_subject(request_for("203.0.113.10")) == "ip:203.0.113.10"

    assert "Forwarded client IP headers are present but no trusted proxy CIDRs are configured" not in caplog.text


def test_rate_limiter_uses_global_trusted_proxies_env_without_db(monkeypatch, caplog):
    monkeypatch.delenv("RATE_LIMIT_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")

    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("172.18.0.4", 12345),
            "path": "/api/v1/auth/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 172.18.0.2")],
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )

    with caplog.at_level(logging.WARNING, logger=rate_limiter.logger.name):
        assert limiter._resolve_subject(request) == "ip:203.0.113.10"

    assert "Forwarded client IP headers are present but no trusted proxy CIDRs are configured" not in caplog.text
