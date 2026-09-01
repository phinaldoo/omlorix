import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils import origin
from app.utils import trusted_hosts
from app.middleware.trusted_host import LocalOrPrivateTrustedHostMiddleware


def _trusted_host_client(
    *,
    allow_local_or_private_hosts: bool,
    allowed_hosts: list[str] | None = None,
) -> TestClient:
    """Build a minimal app for exercising Host-header validation."""

    app = FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, str]:
        """Return a small response after the Host header passes validation."""

        return {"status": "ok"}

    @app.get("/health")
    def health() -> dict[str, str]:
        """Mirror the production liveness route used by container probes."""

        return {"status": "ok"}

    app.add_middleware(
        LocalOrPrivateTrustedHostMiddleware,
        allowed_hosts=(
            allowed_hosts
            if allowed_hosts is not None
            else ["chat.example.com", "*.tenant.example.com"]
        ),
        allow_local_or_private_hosts=allow_local_or_private_hosts,
        www_redirect=False,
    )
    return TestClient(app)


def test_enforce_same_origin_allows_any_origin_without_public_url(monkeypatch):
    """Origin enforcement is disabled until a public URL is configured."""

    def fake_setting(page, key, db):
        values = {
            ("general", "allow_local_or_private_origins"): False,
            ("general", "public_url"): "",
        }
        return values.get((page, key))

    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.setattr(origin, "get_value_by_page_and_key", fake_setting)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("attacker.example", 443),
            "path": "/api/v1/auth/refresh",
            "headers": [
                (b"host", b"attacker.example"),
                (b"origin", b"https://attacker.example"),
            ],
        }
    )

    origin.enforce_same_origin(request, db=object())


def test_enforce_same_origin_accepts_private_ip_origin_without_public_url(monkeypatch):
    """The unconfigured state also permits non-loopback IP browser origins."""
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv(origin.ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV, raising=False)
    monkeypatch.setattr(
        origin,
        "get_value_by_page_and_key",
        lambda page, key, db: [],
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("192.168.1.50", 8000),
            "path": "/api/v1/auth/signin",
            "headers": [
                (b"host", b"192.168.1.50:8000"),
                (b"origin", b"http://192.168.1.50:8000"),
            ],
        }
    )

    origin.enforce_same_origin(request, db=object())


def test_enforce_same_origin_bootstrap_fails_closed_when_settings_cannot_be_read(
    monkeypatch,
):
    """A settings failure must not be mistaken for an unconfigured fresh install."""
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv(origin.ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV, raising=False)

    def fail_to_read_setting(page, key, db):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(origin, "get_value_by_page_and_key", fail_to_read_setting)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("localhost", 80),
            "path": "/api/v1/auth/signin",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"http://localhost"),
            ],
        }
    )

    with pytest.raises(HTTPException) as exc:
        origin.enforce_same_origin(request, db=object())

    assert exc.value.status_code == 403


def test_enforce_same_origin_rejects_unconfigured_origin_when_public_url_exists(
    monkeypatch,
):
    """Configuring a public URL immediately restores strict enforcement."""
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv(origin.ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV, raising=False)
    monkeypatch.setattr(
        origin,
        "get_value_by_page_and_key",
        lambda page, key, db: ["https://chat.example.com"],
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "path": "/api/v1/auth/signin",
            "headers": [
                (b"host", b"chat.example.com"),
                (b"origin", b"https://attacker.example"),
            ],
        }
    )

    with pytest.raises(HTTPException) as exc:
        origin.enforce_same_origin(request, db=object())

    assert exc.value.status_code == 403


def test_enforce_same_origin_accepts_public_url_from_env(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("general", "allow_local_or_private_origins"): False,
            ("general", "public_url"): "",
        }
        return values.get((page, key))

    monkeypatch.setenv("PUBLIC_URL", "https://chat.example.com")
    monkeypatch.setattr(origin, "get_value_by_page_and_key", fake_setting)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "path": "/api/v1/auth/refresh",
            "headers": [(b"origin", b"https://chat.example.com")],
        }
    )

    origin.enforce_same_origin(request, db=object())


def test_enforce_same_origin_accepts_each_configured_public_url(monkeypatch):
    """Secondary configured URLs are first-class trusted browser origins."""
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        origin,
        "get_value_by_page_and_key",
        lambda page, key, db: ["https://primary.example", "https://secondary.example"],
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("secondary.example", 443),
            "path": "/api/v1/auth/refresh",
            "headers": [(b"origin", b"https://secondary.example")],
        }
    )

    origin.enforce_same_origin(request, db=object())


def test_load_trusted_hosts_uses_public_url_and_explicit_hosts(monkeypatch):
    """Configured production hosts must remain an exact operator allowlist."""
    monkeypatch.setenv("TRUSTED_HOSTS", "chat.internal.example, *.tenant.example.com")
    monkeypatch.setenv("PUBLIC_URL", "https://chat.example.com:443")

    assert trusted_hosts.load_trusted_hosts(
        public_url_candidates=["https://admin.example.com"], mode="production"
    ) == [
        "chat.internal.example",
        "*.tenant.example.com",
        "chat.example.com",
        "admin.example.com",
    ]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "testserver"])
def test_configured_production_allowlist_rejects_bootstrap_hosts(
    monkeypatch,
    host: str,
):
    """Exercise the original bypass through list construction and middleware."""
    monkeypatch.setenv("TRUSTED_HOSTS", "chat.example.com")
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    allowed_hosts = trusted_hosts.load_trusted_hosts(
        public_url_candidates=[],
        mode="production",
    )

    response = _trusted_host_client(
        allow_local_or_private_hosts=False,
        allowed_hosts=allowed_hosts,
    ).get(
        "/probe",
        headers={"Host": host},
    )

    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_load_trusted_hosts_allows_every_host_in_production_without_config(
    monkeypatch,
):
    monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    assert trusted_hosts.load_trusted_hosts(
        public_url_candidates=[], mode="production"
    ) == ["*"]


def test_load_trusted_hosts_allows_every_host_in_non_production_without_config(
    monkeypatch,
):
    monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    assert trusted_hosts.load_trusted_hosts(
        public_url_candidates=[], mode="test"
    ) == ["*"]


def test_load_trusted_hosts_rejects_every_host_when_settings_cannot_be_read(monkeypatch):
    """A storage failure must not be treated as a confirmed fresh install."""
    monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    assert trusted_hosts.load_trusted_hosts(
        public_url_candidates=[],
        mode="production",
        allow_any_if_unconfigured=False,
    ) == []


@pytest.mark.parametrize(
    "host",
    [
        "192.168.187.176",
        "10.20.30.40:8443",
        "127.0.0.1",
        "[fd00::1234]:443",
        "[::1]:443",
        "localhost:8443",
    ],
)
def test_trusted_host_middleware_allows_literal_private_hosts_when_enabled(host: str):
    """The private-origin opt-in also permits matching private Host headers."""

    response = _trusted_host_client(allow_local_or_private_hosts=True).get(
        "/probe",
        headers={"Host": host},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "192.168.187.176",
        "[fd00::1234]",
        "127.0.0.1",
        "localhost",
    ],
)
def test_trusted_host_middleware_rejects_private_hosts_when_disabled(host: str):
    """Private Host headers remain opt-in even if their addresses are local."""

    response = _trusted_host_client(allow_local_or_private_hosts=False).get(
        "/probe",
        headers={"Host": host},
    )

    assert response.status_code == 400
    assert response.text == "Invalid host header"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.20.30.40"])
def test_trusted_host_middleware_allows_internal_health_probe_hosts(host: str):
    """Orchestrator probes remain available without opening application routes."""

    response = _trusted_host_client(allow_local_or_private_hosts=False).get(
        "/health",
        headers={"Host": host},
    )

    assert response.status_code == 200


def test_trusted_host_middleware_rejects_untrusted_health_probe_hostname():
    """The health exception must not become an arbitrary Host bypass."""

    response = _trusted_host_client(allow_local_or_private_hosts=False).get(
        "/health",
        headers={"Host": "attacker.example"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "8.8.8.8",
        "private-looking.example",
        "192.168.1.2.attacker.example",
        "testserver",
    ],
)
def test_trusted_host_middleware_still_rejects_untrusted_public_hosts(host: str):
    """The opt-in must not become a wildcard for DNS names or public IPs."""

    response = _trusted_host_client(allow_local_or_private_hosts=True).get(
        "/probe",
        headers={"Host": host},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("host", ["chat.example.com", "alpha.tenant.example.com"])
def test_trusted_host_middleware_preserves_configured_host_matching(host: str):
    """Exact and wildcard configured hosts continue to work unchanged."""

    response = _trusted_host_client(allow_local_or_private_hosts=False).get(
        "/probe",
        headers={"Host": host},
    )

    assert response.status_code == 200


def test_trusted_host_middleware_rejects_an_overlong_numeric_port_without_raising():
    """An attacker-controlled port must never reach an unsafe integer conversion."""

    response = _trusted_host_client(allow_local_or_private_hosts=False).get(
        "/probe",
        headers={"Host": f"chat.example.com:{'9' * 10_000}"},
    )

    assert response.status_code == 400
    assert response.text == "Invalid host header"
