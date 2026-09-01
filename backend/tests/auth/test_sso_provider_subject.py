import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


if "onelogin.saml2.auth" not in sys.modules:
    fake_onelogin = ModuleType("onelogin")
    fake_saml2 = ModuleType("onelogin.saml2")
    fake_saml2_auth = ModuleType("onelogin.saml2.auth")
    fake_saml2_auth.OneLogin_Saml2_Auth = object
    sys.modules["onelogin"] = fake_onelogin
    sys.modules["onelogin.saml2"] = fake_saml2
    sys.modules["onelogin.saml2.auth"] = fake_saml2_auth

from app.auth import utils as auth_utils
from app.auth import enterprise_sso
from app.auth import identities as auth_identities
from app.users import models as user_models


class _ProviderFactory:
    provider = None

    @classmethod
    def get_provider(cls, provider_type, db):
        return cls.provider


class _SSOProvider:
    async def handle_callback(self, request_data, redirect_uri, security_data=None, request=None):
        return {
            "email": "user@example.com",
            "email_verified": True,
            "sub": "incoming-subject",
            "provider_id": "default",
        }

    def validate_domain(self, email):
        return True

    def link_existing_users_by_email(self):
        return True


def test_linked_sso_subject_must_match_before_signin(monkeypatch):
    db = object()
    db_log = object()
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        is_active=True,
        deleted_at=None,
        role="user",
        group_id="default",
    )
    logs = []

    _ProviderFactory.provider = _SSOProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(user_models, "get_user", lambda db, email: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("sso_login", "oidc_linked"): True,
            ("sso_login", "oidc_user_id"): "stored-subject",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_sync_existing_user_from_sso",
        lambda *_args, **_kwargs: pytest.fail("mismatched SSO subject must not sync or continue"),
    )
    monkeypatch.setattr(
        auth_utils,
        "_complete_sso_login",
        lambda *_args, **_kwargs: pytest.fail("mismatched SSO subject must not complete login"),
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            db,
            db_log,
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=sso_login_failed"
    assert logs == [
        (
            "sso_login",
            "warning",
            "SSO signin blocked by provider identity mismatch for oidc: user@example.com",
            "user-id",
        )
    ]


def test_linked_sso_provider_id_must_match_before_signin(monkeypatch):
    db = object()
    db_log = object()
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        is_active=True,
        deleted_at=None,
        role="user",
        group_id="default",
    )
    logs = []

    _ProviderFactory.provider = _SSOProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(user_models, "get_user", lambda db, email: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("sso_login", "oidc_linked"): True,
            ("sso_login", "oidc_user_id"): "incoming-subject",
            ("sso_login", "provider_id"): "stored-provider",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_sync_existing_user_from_sso",
        lambda *_args, **_kwargs: pytest.fail("mismatched SSO provider id must not sync or continue"),
    )
    monkeypatch.setattr(
        auth_utils,
        "_complete_sso_login",
        lambda *_args, **_kwargs: pytest.fail("mismatched SSO provider id must not complete login"),
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            db,
            db_log,
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=sso_login_failed"
    assert logs == [
        (
            "sso_login",
            "warning",
            "SSO signin blocked by provider identity mismatch for oidc: user@example.com",
            "user-id",
        )
    ]


def test_sso_subject_lookup_does_not_enter_social_provider_normalization(monkeypatch):
    """A real query-capable session must still use the enterprise SSO schema."""
    linked_user = SimpleNamespace(id="sso-user")
    db = SimpleNamespace(query=lambda *_args: None)

    monkeypatch.setattr(
        auth_identities,
        "find_user_by_social_identity",
        lambda *_args, **_kwargs: pytest.fail(
            "enterprise SSO providers must not enter social identity lookup"
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_settings_value",
        lambda query_db, path, values: linked_user
        if query_db is db
        and path == ("sso_login", "oidc_user_id")
        and values == ["sso-subject"]
        else None,
    )

    resolved = auth_utils._find_user_by_linked_provider_subject(
        db,
        section="sso_login",
        provider="oidc",
        user_info={"sub": "sso-subject"},
    )

    assert resolved is linked_user


@pytest.mark.parametrize("linked_role", ["user", "admin", "owner"])
def test_sso_subject_lookup_is_used_before_email_matching(monkeypatch, linked_role):
    """A previously bound immutable subject remains a valid login identity."""

    db = object()
    db_log = object()
    user = SimpleNamespace(
        id="subject-user",
        email="old@example.com",
        is_active=True,
        deleted_at=None,
        role=linked_role,
        group_id="default",
    )

    class _SubjectLookupProvider(_SSOProvider):
        async def handle_callback(self, request_data, redirect_uri, security_data=None, request=None):
            return {
                "email": "renamed@example.com",
                "email_verified": True,
                "sub": "incoming-subject",
                "provider_id": "default",
            }

    async def _complete_sso_login(user, *_args, **_kwargs):
        return SimpleNamespace(user_id=user.id)

    _ProviderFactory.provider = _SubjectLookupProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.14")
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_settings_value",
        lambda db, path, values, use_constant_time=False: user if path == ("sso_login", "oidc_user_id") else None,
    )
    monkeypatch.setattr(
        auth_utils,
        "user_exists_by_email",
        lambda db, email: pytest.fail("subject-linked SSO accounts should be resolved before email matching"),
    )
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("sso_login", "oidc_linked"): True,
            ("sso_login", "oidc_user_id"): "incoming-subject",
            ("sso_login", "provider_id"): "default",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: {"is_locked": False})
    monkeypatch.setattr(auth_utils, "is_group_accessible_now", lambda group_id, db, is_admin=False: {"accessible": True})
    monkeypatch.setattr(auth_utils, "ensure_provider_alignment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "evaluate_login_2fa", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_sync_existing_user_from_sso", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "mark_user_externally_managed",
        lambda *_args, **_kwargs: linked_role == "user",
    )
    monkeypatch.setattr(auth_utils, "_complete_sso_login", _complete_sso_login)

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            db,
            db_log,
        )
    )

    assert result.user_id == "subject-user"


def test_sso_role_sync_to_pending_blocks_login_before_2fa_or_auth_code(monkeypatch):
    db = object()
    db_log = object()
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        is_active=True,
        deleted_at=None,
        role="user",
        group_id="default",
        account_type="regular",
        temporary_expires_at=None,
    )

    _ProviderFactory.provider = _SSOProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(user_models, "get_user", lambda db, email: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("sso_login", "oidc_linked"): True,
            ("sso_login", "oidc_user_id"): "incoming-subject",
            ("sso_login", "provider_id"): "default",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: {"is_locked": False})
    monkeypatch.setattr(auth_utils, "is_group_accessible_now", lambda *args, **kwargs: {"accessible": True})
    monkeypatch.setattr(
        auth_utils,
        "_sync_existing_user_from_sso",
        lambda *_args, **_kwargs: setattr(user, "role", "pending"),
    )
    monkeypatch.setattr(
        auth_utils,
        "mark_user_externally_managed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        auth_utils,
        "evaluate_login_2fa",
        lambda *_args, **_kwargs: pytest.fail("post-sync pending users must be blocked before 2FA"),
    )
    monkeypatch.setattr(
        auth_utils,
        "_complete_sso_login",
        lambda *_args, **_kwargs: pytest.fail("post-sync pending users must not reach auth code issuance"),
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            db,
            db_log,
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == (
        "/login?error=account_pending&auth_flow=sso"
    )


def test_sso_email_match_without_subject_link_is_blocked_without_explicit_opt_in(monkeypatch):
    db = object()
    db_log = object()
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        is_active=True,
        deleted_at=None,
        role="user",
        group_id="default",
    )

    class _NoEmailLinkProvider(_SSOProvider):
        def link_existing_users_by_email(self):
            return False

    _ProviderFactory.provider = _NoEmailLinkProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.15")
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(user_models, "get_user", lambda db, email: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("sso_login", "oidc_linked"): False,
            ("sso_login", "oidc_user_id"): "",
            ("sso_login", "provider_id"): "",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "_sync_existing_user_from_sso",
        lambda *_args, **_kwargs: pytest.fail("unlinked SSO email matches must not auto-link without explicit opt-in"),
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            db,
            db_log,
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=signup_not_allowed"


def _configure_existing_user_email_link_callback(
    monkeypatch,
    *,
    role: str,
) -> tuple[SimpleNamespace, list[tuple], list[tuple], list[tuple]]:
    """Configure a complete callback harness for an unlinked email match.

    The harness deliberately exercises the real callback branch that stores a
    new provider identity. Tests can therefore prove both that protected roles
    stop before the mutation and that ordinary-user email linking still works.
    """

    user = SimpleNamespace(
        id=f"{role}-id",
        email="user@example.com",
        is_active=True,
        deleted_at=None,
        role=role,
        group_id="default",
    )
    audit_logs: list[tuple] = []
    identity_writes: list[tuple] = []
    setting_writes: list[tuple] = []

    _ProviderFactory.provider = _SSOProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.enterprise_sso",
        SimpleNamespace(EnterpriseSSOProviderFactory=_ProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.16")
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(user_models, "get_user", lambda db, email: user)
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: audit_logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_validate_or_store_sso_provider_identity",
        lambda *args, **kwargs: identity_writes.append((args, kwargs)) or True,
    )

    # ``update_user_settings`` is imported locally inside the callback, so the
    # defining module must be patched rather than the auth-utils namespace.
    from app.users import init as users_init

    monkeypatch.setattr(
        users_init,
        "update_user_settings",
        lambda *args, **kwargs: setting_writes.append((args, kwargs)),
    )
    monkeypatch.setattr(auth_utils, "_sync_existing_user_from_sso", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "mark_user_externally_managed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(auth_utils, "_sso_login_eligibility_redirect_response", lambda **_kwargs: None)
    monkeypatch.setattr(auth_utils, "ensure_provider_alignment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "evaluate_login_2fa", lambda *_args, **_kwargs: None)

    async def _complete_sso_login(user, *_args, **_kwargs):
        return SimpleNamespace(user_id=user.id)

    monkeypatch.setattr(auth_utils, "_complete_sso_login", _complete_sso_login)
    return user, audit_logs, identity_writes, setting_writes


@pytest.mark.parametrize("protected_role", ["owner", "admin"])
def test_sso_email_link_cannot_claim_unlinked_administrative_account(
    monkeypatch,
    protected_role,
):
    """An email assertion alone must never establish an administrative link."""

    user, audit_logs, identity_writes, setting_writes = _configure_existing_user_email_link_callback(
        monkeypatch,
        role=protected_role,
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            object(),
            object(),
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=sso_login_failed"
    assert user.role == protected_role
    assert identity_writes == []
    assert setting_writes == []
    assert audit_logs == [
        (
            "sso_login",
            "warning",
            f"SSO email linking blocked for protected {protected_role} account: user@example.com",
            user.id,
        )
    ]


def test_sso_email_link_still_links_regular_user_when_explicitly_enabled(monkeypatch):
    """Preserve the supported opt-in email-link flow for ordinary users."""

    user, audit_logs, identity_writes, setting_writes = _configure_existing_user_email_link_callback(
        monkeypatch,
        role="user",
    )

    result = asyncio.run(
        auth_utils.sso_login_callback(
            "oidc",
            {},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}, client=None),
            object(),
            object(),
            object(),
        )
    )

    assert result.user_id == user.id
    assert len(identity_writes) == 1
    assert len(setting_writes) == 1
    assert audit_logs == []


class _OIDCResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _OIDCClient:
    def __init__(self, userinfo):
        self.userinfo = userinfo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, data, headers, auth=None):
        return _OIDCResponse({"access_token": "access-token", "id_token": "id-token"})

    async def get(self, url, headers):
        return _OIDCResponse(self.userinfo)


def _oidc_provider(monkeypatch, userinfo, id_token_claims=None):
    provider = enterprise_sso.EnterpriseOIDCProvider.__new__(enterprise_sso.EnterpriseOIDCProvider)
    provider.settings = {
        "token_endpoint": "https://idp.example/token",
        "userinfo_endpoint": "https://idp.example/userinfo",
    }
    provider.db = object()

    claims = id_token_claims or {
        "sub": "id-token-subject",
        "email": "user@example.com",
        "email_verified": True,
    }

    async def decode_id_token(id_token, expected_nonce=None):
        return claims

    provider._decode_id_token_verified = decode_id_token
    monkeypatch.setattr(enterprise_sso, "assert_http_url_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(enterprise_sso.httpx, "AsyncClient", lambda: _OIDCClient(userinfo))
    return provider


def test_oidc_userinfo_subject_must_match_verified_id_token(monkeypatch):
    provider = _oidc_provider(
        monkeypatch,
        {
            "sub": "userinfo-subject",
            "email": "user@example.com",
            "email_verified": True,
        },
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            provider.handle_callback(
                {"code": "auth-code"},
                "https://omlorix.example/api/v1/auth/sso/oidc/callback",
                enterprise_sso.SSOSecurityData(
                    nonce="nonce", code_verifier="pkce-verifier"
                ),
            )
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "OIDC UserInfo subject does not match ID token subject"


def test_oidc_userinfo_cannot_override_verified_id_token_subject(monkeypatch):
    provider = _oidc_provider(
        monkeypatch,
        {
            "sub": "id-token-subject",
            "email": "updated@example.com",
            "email_verified": True,
            "name": "Updated User",
        },
    )

    user_info = asyncio.run(
        provider.handle_callback(
            {"code": "auth-code"},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            enterprise_sso.SSOSecurityData(
                nonce="nonce", code_verifier="pkce-verifier"
            ),
        )
    )

    assert user_info["sub"] == "id-token-subject"
    assert user_info["email"] == "updated@example.com"
    assert user_info["name"] == "Updated User"


def test_oidc_uses_verified_id_token_subject_when_userinfo_omits_subject(monkeypatch):
    provider = _oidc_provider(
        monkeypatch,
        {
            "email": "updated@example.com",
            "email_verified": True,
            "id": "userinfo-id",
            "provider_user_id": "userinfo-provider-user-id",
        },
    )

    user_info = asyncio.run(
        provider.handle_callback(
            {"code": "auth-code"},
            "https://omlorix.example/api/v1/auth/sso/oidc/callback",
            enterprise_sso.SSOSecurityData(
                nonce="nonce", code_verifier="pkce-verifier"
            ),
        )
    )

    assert user_info["sub"] == "id-token-subject"
    assert auth_utils._provider_subject_id(user_info) == "id-token-subject"


def test_oidc_id_token_subject_is_required(monkeypatch):
    provider = _oidc_provider(
        monkeypatch,
        {
            "sub": "userinfo-subject",
            "email": "user@example.com",
            "email_verified": True,
        },
        id_token_claims={
            "email": "user@example.com",
            "email_verified": True,
        },
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            provider.handle_callback(
                {"code": "auth-code"},
                "https://omlorix.example/api/v1/auth/sso/oidc/callback",
                enterprise_sso.SSOSecurityData(
                    nonce="nonce", code_verifier="pkce-verifier"
                ),
            )
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "OIDC ID token missing subject"


class _FakeSAMLAuth:
    def __init__(self, req, settings):
        self.req = req
        self.settings = settings

    def process_response(self, request_id=None):
        self.request_id = request_id

    def get_errors(self):
        return []

    def get_last_error_reason(self):
        return ""

    def is_authenticated(self):
        return True

    def get_nameid(self):
        return "user@example.com"

    def get_nameid_format(self):
        return "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"

    def get_attributes(self):
        return {
            "email": ["user@example.com"],
            "immutableId": ["stable-subject-123"],
        }


def test_saml_requires_immutable_subject_identifier(monkeypatch):
    provider = enterprise_sso.SAMLSSOProvider.__new__(enterprise_sso.SAMLSSOProvider)
    provider.settings = {
        "entity_id": "https://omlorix.example/saml",
        "sso_url": "https://idp.example/sso",
        "x509_cert": "cert",
        "attribute_mapping": {},
    }

    monkeypatch.setattr(
        sys.modules["onelogin.saml2.auth"],
        "OneLogin_Saml2_Auth",
        _FakeSAMLAuth,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            provider.handle_callback(
                {"SAMLResponse": "encoded-response"},
                "https://omlorix.example/api/v1/auth/sso/saml/callback",
                enterprise_sso.SSOSecurityData(request_id="request-id"),
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "SAML response missing an immutable subject identifier. "
        "Configure a subject attribute mapping or a persistent NameID."
    )


def test_saml_uses_configured_subject_attribute(monkeypatch):
    provider = enterprise_sso.SAMLSSOProvider.__new__(enterprise_sso.SAMLSSOProvider)
    provider.settings = {
        "entity_id": "https://omlorix.example/saml",
        "sso_url": "https://idp.example/sso",
        "x509_cert": "cert",
        "attribute_mapping": {
            "subject": "immutableId",
            "display_name": "displayName",
            "first_name": "firstName",
            "last_name": "lastName",
        },
    }

    monkeypatch.setattr(
        sys.modules["onelogin.saml2.auth"],
        "OneLogin_Saml2_Auth",
        _FakeSAMLAuth,
    )

    user_info = asyncio.run(
        provider.handle_callback(
            {"SAMLResponse": "encoded-response"},
            "https://omlorix.example/api/v1/auth/sso/saml/callback",
            enterprise_sso.SSOSecurityData(request_id="request-id"),
        )
    )

    assert user_info["sub"] == "stable-subject-123"
    assert user_info["email"] == "user@example.com"
    assert user_info["email_verified"] is True
    assert user_info["provider_id"] == "default"
