"""Focused tests for safe enterprise SSO diagnostic correlation."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse

from app.admin.auth_diagnostics.utils import _derived_discovery_url
from app.auth import diagnostics
from app.auth import router as auth_router
from app.auth import utils as auth_utils
from app.auth.diagnostics import (
    build_sso_failure_url,
    classify_sso_exception,
    new_auth_reference,
)
from app.auth.enterprise_sso import SSOSecurityData


def test_sso_reference_survives_security_cookie_round_trip():
    reference = new_auth_reference()
    restored = SSOSecurityData.from_json(
        SSOSecurityData(nonce="nonce", correlation_id=reference).to_json()
    )

    assert restored.correlation_id == reference
    assert reference.startswith("AUTH-")


def test_invalid_issuer_has_stable_safe_diagnostic_code():
    code, stage = classify_sso_exception(
        HTTPException(status_code=401, detail="Invalid ID token: Invalid issuer")
    )

    assert (code, stage) == ("oidc_issuer_mismatch", "id_token_validation")
    assert "Invalid issuer" not in build_sso_failure_url("sso_login_failed", "AUTH-123")
    assert build_sso_failure_url("sso_login_failed", "AUTH-123") == (
        "/login?error=sso_login_failed&auth_flow=sso&reference=AUTH-123"
    )


def test_discovery_can_use_backend_reachable_origin_with_public_issuer_path():
    assert _derived_discovery_url(
        {
            "issuer": "http://localhost:9000/application/o/omlorix-oidc/",
            "token_endpoint": "http://host.docker.internal:9000/application/o/omlorix-oidc/token/",
        }
    ) == (
        "http://host.docker.internal:9000/application/o/omlorix-oidc/"
        ".well-known/openid-configuration"
    )


def test_legacy_sso_cookie_receives_correlated_reference(monkeypatch):
    """A pre-diagnostic cookie must not produce a null log or URL reference."""

    provider_state = "provider-state"
    recorded = []
    request = SimpleNamespace(
        method="GET",
        query_params={"state": provider_state},
        headers={"user-agent": "pytest"},
        cookies={
            "sso_state": hashlib.sha256(provider_state.encode("utf-8")).hexdigest(),
            "sso_security": '{"nonce":"provider-nonce","request_id":null}',
        },
        client=SimpleNamespace(host="198.51.100.10"),
    )
    monkeypatch.setattr(
        auth_router,
        "read_flow_context_cookie",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        auth_router,
        "build_auth_redirect_base_url",
        lambda *_args: "https://chat.example",
    )
    monkeypatch.setattr(
        auth_utils,
        "sso_login_callback",
        AsyncMock(
            return_value=RedirectResponse(
                url="/login?error=domain_not_allowed",
                status_code=302,
            )
        ),
    )
    monkeypatch.setattr(diagnostics, "new_auth_reference", lambda: "AUTH-LEGACY1")
    monkeypatch.setattr(
        diagnostics,
        "record_sso_diagnostic",
        lambda _db_log, **kwargs: recorded.append(kwargs),
    )

    result = asyncio.run(
        auth_router.sso_callback_route(
            provider_type="oidc",
            request=request,
            response=Response(),
            db=object(),
            db_log=object(),
        )
    )

    query = parse_qs(urlsplit(result.headers["location"]).query)
    assert query["auth_flow"] == ["sso"]
    assert query["reference"] == ["AUTH-LEGACY1"]
    assert recorded[0]["reference"] == "AUTH-LEGACY1"


def test_early_sso_failure_has_explicit_flow_marker(monkeypatch):
    """The frontend must identify an SSO error without sticky session state."""

    request = SimpleNamespace(
        method="GET",
        query_params={},
        headers={},
        cookies={},
        client=None,
    )
    monkeypatch.setattr(
        auth_router,
        "read_flow_context_cookie",
        lambda *_args, **_kwargs: {},
    )

    result = asyncio.run(
        auth_router.sso_callback_route(
            provider_type="oidc",
            request=request,
            response=Response(),
            db=object(),
            db_log=object(),
        )
    )

    query = parse_qs(urlsplit(result.headers["location"]).query)
    assert query == {"error": ["sso_state_missing"], "auth_flow": ["sso"]}
