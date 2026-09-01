from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections import notion


class _JsonResponse:
    """Small response double accepted by Notion's strict JSON reader."""

    status_code = 200

    def json(self):
        return {"client_id": "registered-client"}

    def raise_for_status(self):
        return None


class _RegistrationClient:
    """Capture the dynamic registration payload without making a request."""

    def __init__(self, captured, *args, **kwargs):
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, **kwargs):
        self.captured.update({"url": url, **kwargs})
        return _JsonResponse()


def test_dynamic_client_registration_declares_web_application_type(monkeypatch):
    """MCP 2026 OAuth registrations explicitly identify browser clients."""
    captured = {}
    monkeypatch.setattr(
        notion.httpx,
        "Client",
        lambda *args, **kwargs: _RegistrationClient(captured, *args, **kwargs),
    )

    registration = notion.register_notion_oauth_client(
        {"registration_endpoint": "https://auth.example.com/register"},
        redirect_uri="https://chat.example.com/oauth/callback",
        origin="https://chat.example.com",
    )

    assert registration["client_id"] == "registered-client"
    assert captured["json"]["application_type"] == "web"
    assert captured["json"]["redirect_uris"] == [
        "https://chat.example.com/oauth/callback"
    ]


def test_authorization_response_issuer_mismatch_is_rejected(monkeypatch):
    """An RFC 9207 issuer mismatch fails before any token is exchanged."""
    monkeypatch.setattr(
        notion,
        "consume_connection_oauth_state",
        lambda _db, _state: {
            "user_id": "user-1",
            "secrets": {
                "issuer": "https://auth.example.com",
                "code_verifier": "verifier",
                "client_id": "client-id",
                "token_endpoint": "https://auth.example.com/token",
            },
        },
    )

    def unexpected_http_client(*_args, **_kwargs):
        raise AssertionError("issuer validation must happen before token exchange")

    monkeypatch.setattr(notion.httpx, "Client", unexpected_http_client)

    with pytest.raises(ValueError, match="issuer did not match"):
        notion.complete_notion_oauth(
            object(),
            state="state",
            code="code",
            authorization_issuer="https://attacker.example.com",
        )
