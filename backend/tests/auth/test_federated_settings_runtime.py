from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import zlib
from pathlib import Path
from typing import get_args
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth.enterprise_sso import (
    EnterpriseOIDCProvider,
    EnterpriseSSOProviderFactory,
    SAMLSSOProvider,
    SSOSecurityData,
)
from app.auth import enterprise_sso
from app.auth import social
from app.auth.github_oauth import build_github_oauth_endpoints
from app.auth.schemas import EnterpriseSSOProviderType, SSOAuthInitRequest
from app.auth.social import (
    GitHubAuthProvider,
    GoogleAuthProvider,
    MicrosoftAuthProvider,
)


def test_oidc_authorization_uses_pkce_and_does_not_force_prompt():
    """Bind the callback code to this browser without changing IdP UX by default."""

    provider = EnterpriseOIDCProvider.__new__(EnterpriseOIDCProvider)
    provider.settings = {
        "enabled": True,
        "client_id": "client-id",
        "client_secret": "client-secret",
        "authorization_endpoint": "https://idp.example/authorize?audience=omlorix",
        "token_endpoint": "https://idp.example/token",
        "scopes": ["openid", "email"],
        "prompt": "",
    }

    authorization_url, security_data = provider.get_authorization_url(
        "https://chat.example/callback",
        "state-value",
    )

    params = parse_qs(urlparse(authorization_url).query)
    assert params["audience"] == ["omlorix"]
    assert params["code_challenge_method"] == ["S256"]
    assert "prompt" not in params
    assert security_data.code_verifier
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(security_data.code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    assert params["code_challenge"] == [expected_challenge]
    assert (
        SSOSecurityData.from_json(security_data.to_json()).code_verifier
        == security_data.code_verifier
    )


def test_oidc_logout_discovers_through_backend_and_returns_browser_origin(monkeypatch):
    provider = EnterpriseOIDCProvider.__new__(EnterpriseOIDCProvider)
    provider.db = object()
    provider.settings = {
        "enabled": True,
        "client_id": "client-id",
        "client_secret": "client-secret",
        "issuer": "http://host.docker.internal:9000/application/o/omlorix/",
        "authorization_endpoint": "http://localhost:9000/application/o/authorize/",
        "token_endpoint": "http://host.docker.internal:9000/application/o/token/",
    }
    checked_urls = []

    class MetadataResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "end_session_endpoint": (
                    "http://host.docker.internal:9000/"
                    "application/o/omlorix/end-session/"
                )
            }

    monkeypatch.setattr(
        enterprise_sso,
        "assert_http_url_allowed",
        lambda _db, *, url, feature: checked_urls.append((url, feature)),
    )
    monkeypatch.setattr(
        enterprise_sso.httpx,
        "get",
        lambda *args, **kwargs: MetadataResponse(),
    )
    monkeypatch.setattr(
        enterprise_sso,
        "get_public_url",
        lambda _db: "https://chat.example",
    )

    end_session_url = provider.get_end_session_url()
    parsed_end_session = urlparse(end_session_url)
    assert parsed_end_session._replace(query="").geturl() == (
        "http://localhost:9000/application/o/omlorix/end-session/"
    )
    assert parse_qs(parsed_end_session.query) == {
        "client_id": ["client-id"],
        "post_logout_redirect_uri": ["https://chat.example/login"],
    }
    assert checked_urls[0] == (
        "http://host.docker.internal:9000/application/o/omlorix/"
        ".well-known/openid-configuration",
        "OIDC RP-initiated logout discovery",
    )
    assert checked_urls[1] == (end_session_url, "OIDC RP-initiated logout")


def test_oidc_logout_discovers_from_root_path_issuer(monkeypatch):
    provider = EnterpriseOIDCProvider.__new__(EnterpriseOIDCProvider)
    provider.db = object()
    provider.settings = {
        "enabled": True,
        "client_id": "client-id",
        "client_secret": "client-secret",
        "issuer": "https://idp.example.com",
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
    }
    requested_urls = []

    class MetadataResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"end_session_endpoint": "https://idp.example.com/logout"}

    monkeypatch.setattr(
        enterprise_sso,
        "assert_http_url_allowed",
        lambda _db, *, url, feature: requested_urls.append((url, feature)),
    )
    monkeypatch.setattr(
        enterprise_sso.httpx,
        "get",
        lambda *args, **kwargs: MetadataResponse(),
    )
    monkeypatch.setattr(
        enterprise_sso,
        "get_public_url",
        lambda _db: "https://chat.example",
    )

    assert provider.get_end_session_url()
    assert requested_urls[0] == (
        "https://idp.example.com/.well-known/openid-configuration",
        "OIDC RP-initiated logout discovery",
    )


def test_selected_but_incomplete_enterprise_providers_remain_unavailable():
    """Saved activation intent must not bypass provider runtime prerequisites."""

    saml = SAMLSSOProvider.__new__(SAMLSSOProvider)
    saml.settings = {"enabled": True}
    oidc = EnterpriseOIDCProvider.__new__(EnterpriseOIDCProvider)
    oidc.settings = {"enabled": True}
    assert saml.is_enabled() is False
    assert oidc.is_enabled() is False


def test_trusted_headers_authentication_is_absent_from_sso_contract():
    """The retired method must be rejected by both request and runtime contracts."""

    assert get_args(EnterpriseSSOProviderType) == ("saml", "oidc")
    with pytest.raises(ValidationError):
        SSOAuthInitRequest(provider_type="trusted_headers")

    with pytest.raises(HTTPException) as exc_info:
        EnterpriseSSOProviderFactory.get_provider("trusted_headers", object())

    assert exc_info.value.status_code == 400

    from app.auth import router as auth_router_module

    app = FastAPI()
    app.include_router(auth_router_module.auth_router)
    app.dependency_overrides[auth_router_module.get_db] = lambda: object()
    response = TestClient(app).post(
        "/api/v1/auth/sso/trusted_headers/init",
        json={"provider_type": "saml"},
    )

    assert response.status_code == 422


def test_saml_request_signing_requires_runtime_key_material():
    """A selected signing mode stays unavailable until both SP credentials exist."""

    provider = SAMLSSOProvider.__new__(SAMLSSOProvider)
    provider.settings = {
        "enabled": True,
        "entity_id": "urn:omlorix:sp",
        "idp_entity_id": "urn:example:idp",
        "sso_url": "https://idp.example/sso",
        "x509_cert": "idp-cert",
        "sign_authn_requests": True,
    }

    assert provider.is_enabled() is False

    provider.settings.update({"sp_x509_cert": "sp-cert", "sp_private_key": "sp-key"})
    assert provider.is_enabled() is True


def test_unsigned_saml_authorization_builds_redirect_authn_request():
    provider = SAMLSSOProvider.__new__(SAMLSSOProvider)
    provider.settings = {
        "enabled": True,
        "entity_id": "urn:omlorix:sp",
        "idp_entity_id": "urn:example:idp",
        "sso_url": "https://idp.example/sso",
        "x509_cert": "idp-cert",
        "sign_authn_requests": False,
    }

    authorization_url, security_data = provider.get_authorization_url(
        "https://chat.example/api/v1/auth/sso/saml/callback",
        "state-value",
    )

    params = parse_qs(urlparse(authorization_url).query)
    request_xml = zlib.decompress(
        base64.b64decode(params["SAMLRequest"][0]),
        -zlib.MAX_WBITS,
    )
    request = ElementTree.fromstring(request_xml)

    assert request.tag == "{urn:oasis:names:tc:SAML:2.0:protocol}AuthnRequest"
    assert request.attrib["ID"] == security_data.request_id
    assert request.attrib["Destination"] == "https://idp.example/sso"
    assert (
        request.find("{urn:oasis:names:tc:SAML:2.0:assertion}Issuer").text
        == "urn:omlorix:sp"
    )
    assert params["RelayState"] == ["state-value"]


def test_saml_runtime_loader_preserves_legacy_entity_id_fallback(monkeypatch):
    """An unsaved pre-split SAML document must remain usable after upgrade."""

    monkeypatch.setattr(
        enterprise_sso,
        "get_settings_page_data",
        lambda _db, _page: {
            "enable_saml": True,
            "saml_entity_id": "urn:legacy:shared-entity",
            "saml_sso_url": "https://idp.example/sso",
            "saml_x509_cert": "idp-cert",
            "saml_advanced_settings": {},
        },
    )

    provider = SAMLSSOProvider(object())

    assert provider.settings["entity_id"] == "urn:legacy:shared-entity"
    assert provider.settings["idp_entity_id"] == "urn:legacy:shared-entity"
    assert provider.is_enabled() is True


def test_saml_runtime_loader_prefers_canonical_idp_entity_id(monkeypatch):
    """New settings must keep the separately configured IdP trust identity."""

    monkeypatch.setattr(
        enterprise_sso,
        "get_settings_page_data",
        lambda _db, _page: {
            "enable_saml": True,
            "saml_entity_id": "urn:omlorix:sp",
            "saml_sso_url": "https://idp.example/sso",
            "saml_x509_cert": "idp-cert",
            "saml_advanced_settings": {"idp_entity_id": "urn:example:idp"},
        },
    )

    provider = SAMLSSOProvider(object())

    assert provider.settings["entity_id"] == "urn:omlorix:sp"
    assert provider.settings["idp_entity_id"] == "urn:example:idp"


def test_saml_settings_keep_sp_and_idp_identity_separate_and_support_rotation():
    """Never substitute the SP entity ID for a missing IdP trust identifier."""

    provider = SAMLSSOProvider.__new__(SAMLSSOProvider)
    provider.settings = {
        "entity_id": "urn:omlorix:sp",
        "idp_entity_id": "urn:example:idp",
        "sso_url": "https://idp.example/sso",
        "x509_cert": "current-cert",
        "additional_x509_certs": ["next-cert"],
        "nameid_format": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        "sign_authn_requests": True,
        "sp_x509_cert": "sp-cert",
        "sp_private_key": "sp-key",
    }

    settings = provider._build_saml_settings("https://chat.example/saml/callback")

    assert settings["sp"]["entityId"] == "urn:omlorix:sp"
    assert settings["idp"]["entityId"] == "urn:example:idp"
    assert settings["idp"]["x509certMulti"]["signing"] == ["current-cert", "next-cert"]
    assert settings["security"]["authnRequestsSigned"] is True
    assert settings["sp"]["privateKey"] == "sp-key"


def test_social_identity_allowlists_use_verified_provider_context():
    """Email suffixes alone must not satisfy tenant, hosted-domain, or org policy."""

    google = GoogleAuthProvider.__new__(GoogleAuthProvider)
    google.settings = {"google_allowed_domains": ["example.com"]}
    assert (
        google.validate_identity({"email": "user@example.com", "hd": "example.com"})
        is True
    )
    assert google.validate_identity({"email": "user@example.com"}) is False

    microsoft = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    microsoft.settings = {
        "microsoft_allowed_tenant_ids": ["11111111-1111-1111-1111-111111111111"]
    }
    assert (
        microsoft.validate_identity(
            {"tenant_id": "11111111-1111-1111-1111-111111111111"}
        )
        is True
    )
    assert (
        microsoft.validate_identity(
            {"tenant_id": "22222222-2222-2222-2222-222222222222"}
        )
        is False
    )

    github = GitHubAuthProvider.__new__(GitHubAuthProvider)
    github.settings = {"github_allowed_organizations": ["example-org"]}
    assert github.validate_identity({"organizations": ["Example-Org"]}) is True
    assert github.validate_identity({"organizations": []}) is False


class _MembershipResponse:
    """Minimal HTTP response used by the organization-membership regression."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _MembershipClient:
    """Record direct membership requests without contacting GitHub."""

    def __init__(self):
        self.urls = []

    async def get(self, url, headers):
        self.urls.append(url)
        if url.endswith("/target-org"):
            return _MembershipResponse(200, {"state": "active"})
        return _MembershipResponse(404, {})


def test_github_allowlist_checks_each_membership_directly(monkeypatch):
    """Do not deny membership merely because it was beyond `/user/orgs` page one."""

    provider = GitHubAuthProvider.__new__(GitHubAuthProvider)
    provider.db = object()
    provider.settings = {
        "github_allowed_organizations": ["first-org", "target-org"]
    }
    provider.endpoints = build_github_oauth_endpoints("https://ghe.example")
    client = _MembershipClient()
    monkeypatch.setattr(
        social, "assert_http_url_allowed", lambda *args, **kwargs: None
    )

    memberships = asyncio.run(
        provider._get_allowed_organization_memberships(client, "access-token")
    )

    assert memberships == ["target-org"]
    assert client.urls == [
        "https://ghe.example/api/v3/user/memberships/orgs/first-org",
        "https://ghe.example/api/v3/user/memberships/orgs/target-org",
    ]
