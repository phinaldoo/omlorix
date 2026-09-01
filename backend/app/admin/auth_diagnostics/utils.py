"""Read-only OIDC configuration checks executed from the Omlorix backend."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.auth.account_slots import build_auth_redirect_base_url
from app.auth.diagnostics import new_auth_reference
from app.auth.enterprise_sso import EnterpriseOIDCProvider
from app.network.policy import assert_http_url_allowed


def _check(code: str, status: str, **details: Any) -> dict[str, Any]:
    """Build one bounded, translation-friendly check result."""

    return {"code": code, "status": status, "details": details}


def _derived_discovery_url(settings: dict[str, Any]) -> str | None:
    """Map the public issuer path onto the backend-reachable token origin.

    This catches split-horizon Docker setups where the browser uses localhost
    while Omlorix reaches the same IdP through ``host.docker.internal``.
    """

    issuer = urlsplit(str(settings.get("issuer") or ""))
    token = urlsplit(str(settings.get("token_endpoint") or ""))
    if not issuer.path or not token.scheme or not token.netloc:
        return None
    path = f"{issuer.path.rstrip('/')}/.well-known/openid-configuration"
    return urlunsplit((token.scheme, token.netloc, path, "", ""))


async def test_oidc_configuration(db, request) -> dict[str, Any]:
    """Validate saved OIDC settings without performing a user login."""

    reference = new_auth_reference()
    provider = EnterpriseOIDCProvider(db)
    settings = provider.settings
    callback_url = f"{build_auth_redirect_base_url(db, request)}/api/v1/auth/sso/oidc/callback"
    checks: list[dict[str, Any]] = []

    required = {
        "client_id": bool(settings.get("client_id")),
        "client_secret": bool(settings.get("client_secret")),
        "authorization_endpoint": bool(settings.get("authorization_endpoint") or settings.get("discovery_url")),
        "token_endpoint": bool(settings.get("token_endpoint") or settings.get("discovery_url")),
    }
    missing = [key for key, present in required.items() if not present]
    checks.append(_check(
        "oidc_required_settings",
        "failed" if missing else "passed",
        missing=missing,
        enabled=bool(settings.get("enabled")),
    ))

    scopes = [str(scope) for scope in settings.get("scopes", [])]
    missing_scopes = [scope for scope in ("openid", "email", "profile") if scope not in scopes]
    checks.append(_check(
        "oidc_scopes",
        "warning" if missing_scopes else "passed",
        configured=scopes,
        missing=missing_scopes,
    ))

    discovery_candidates: list[str] = []
    for candidate in (settings.get("discovery_url"), _derived_discovery_url(settings)):
        candidate = str(candidate or "").strip()
        if candidate and candidate not in discovery_candidates:
            discovery_candidates.append(candidate)

    metadata: dict[str, Any] = {}
    discovery_url = ""
    discovery_errors: list[str] = []
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        for candidate in discovery_candidates:
            try:
                assert_http_url_allowed(db, url=candidate, feature="OIDC configuration test")
                response = await client.get(candidate)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        metadata = payload
                        discovery_url = candidate
                        break
                discovery_errors.append(f"HTTP {response.status_code}")
            except Exception as exc:
                discovery_errors.append(type(exc).__name__)

        checks.append(_check(
            "oidc_discovery_reachable",
            "passed" if metadata else "failed",
            url=discovery_url or (discovery_candidates[0] if discovery_candidates else ""),
            attempts=len(discovery_candidates),
            errors=discovery_errors[:3],
        ))

        configured_issuer = str(settings.get("issuer") or "")
        metadata_issuer = str(metadata.get("issuer") or "")
        issuer_matches = bool(configured_issuer and metadata_issuer and configured_issuer == metadata_issuer)
        checks.append(_check(
            "oidc_issuer_match",
            "passed" if issuer_matches else "failed",
            configured_issuer=configured_issuer,
            metadata_issuer=metadata_issuer,
        ))

        jwks_uri = str(metadata.get("jwks_uri") or settings.get("jwks_uri") or "")
        jwks_keys = 0
        jwks_status = "failed"
        if jwks_uri:
            try:
                assert_http_url_allowed(db, url=jwks_uri, feature="OIDC configuration test JWKS")
                response = await client.get(jwks_uri)
                payload = response.json() if response.status_code == 200 else {}
                jwks_keys = len(payload.get("keys", [])) if isinstance(payload, dict) else 0
                jwks_status = "passed" if jwks_keys else "failed"
            except Exception:
                jwks_status = "failed"
        checks.append(_check("oidc_jwks_reachable", jwks_status, url=jwks_uri, key_count=jwks_keys))

        claims = metadata.get("claims_supported") if isinstance(metadata.get("claims_supported"), list) else []
        missing_claims = [claim for claim in ("email", "email_verified") if claim not in claims]
        checks.append(_check(
            "oidc_email_claims",
            "warning" if missing_claims else "passed",
            missing=missing_claims,
        ))

    statuses = {item["status"] for item in checks}
    overall = "failed" if "failed" in statuses else "warning" if "warning" in statuses else "passed"
    return {
        "status": overall,
        "reference": reference,
        "callback_url": callback_url,
        "checks": checks,
    }
