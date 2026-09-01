from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.utils import router


class _URL:
    scheme = "https"


class _Request:
    def __init__(self, *, secret: str, client_ip: str = "127.0.0.1"):
        self.client = SimpleNamespace(host=client_ip)
        self.url = _URL()
        self.scope = {"scheme": "https"}
        self.headers = {
            "host": "chat.example.test",
            "x-forwarded-for": client_ip,
            "x-omlorix-proxy-verification": secret,
            "x-omlorix-proxy-verification-nonce": "verification_nonce_1234",
        }


def test_proxy_verification_uses_production_resolution_and_preserves_https(monkeypatch):
    secret = "a" * 64
    nonce = "verification_nonce_1234"
    monkeypatch.setenv("OMLORIX_LAUNCHER_PROXY_SECRET", secret)
    monkeypatch.setattr(router, "resolve_request_client_ip", lambda request, default=None: request.client.host)

    result = router.proxy_verification(_Request(secret=secret), nonce)

    assert result.client_ip == "127.0.0.1"
    assert result.scheme == "https"
    assert result.host == "chat.example.test"
    assert result.nonce == nonce
    assert result.trust_chain_accepted is True


def test_proxy_verification_rejects_direct_or_unauthenticated_requests(monkeypatch):
    monkeypatch.setenv("OMLORIX_LAUNCHER_PROXY_SECRET", "a" * 64)

    with pytest.raises(HTTPException) as exc:
        router.proxy_verification(_Request(secret="spoofed"), "verification_nonce_1234")

    assert exc.value.status_code == 404


def test_proxy_verification_rejects_a_nonce_not_authorized_by_the_local_proxy(
    monkeypatch,
):
    """An external visitor cannot turn an arbitrary query nonce into readiness."""
    secret = "a" * 64
    request = _Request(secret=secret)
    request.headers["x-omlorix-proxy-verification-nonce"] = "different_nonce_1234"
    monkeypatch.setenv("OMLORIX_LAUNCHER_PROXY_SECRET", secret)

    with pytest.raises(HTTPException) as exc:
        router.proxy_verification(request, "verification_nonce_1234")

    assert exc.value.status_code == 404
