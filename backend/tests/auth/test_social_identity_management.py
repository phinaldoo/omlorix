import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse

from app.auth import identities
from app.auth import router as auth_router
from app.auth import account_slots
from app.auth.models import Authentication
from app.users.models import User


class _Query:
    """Small query stand-in for policy tests that do not need a real database."""

    def __init__(self, entity, *, user=None, authentication=None):
        self.entity = entity
        self.user = user
        self.authentication = authentication

    def filter(self, *_args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        if self.entity is User:
            return self.user
        if self.entity is Authentication:
            return self.authentication
        return None

    def yield_per(self, _batch_size):
        if self.entity is User and self.user is not None:
            return [self.user]
        return []

    def delete(self, **_kwargs):
        return 1


class _Db:
    def __init__(self, *, user=None, authentication=None):
        self.user = user
        self.authentication = authentication
        self.commits = 0
        self.added = []

    def query(self, entity):
        return _Query(entity, user=self.user, authentication=self.authentication)

    def commit(self):
        self.commits += 1

    def add(self, value):
        self.added.append(value)


def test_subject_hash_is_scoped_to_provider_and_issuer():
    google = identities.social_identity_hash("google", "https://accounts.google.com", "123")
    github = identities.social_identity_hash("github", "https://github.com", "123")

    assert len(google) == 64
    assert google != github


def test_workspace_and_tenant_subjects_receive_scoped_issuers():
    assert identities.social_identity_issuer(
        "slack",
        {"workspace_id": "t0123"},
    ) == "https://slack.com/workspace/T0123"
    assert identities.social_identity_issuer(
        "microsoft",
        {"tenant_id": "Tenant-A"},
    ) == "https://login.microsoftonline.com/tenant-a/v2.0"


def test_record_social_identity_rejects_a_different_legacy_owner(monkeypatch):
    """A legacy binding cannot be reassigned while it lacks a normalized row."""
    legacy_owner = SimpleNamespace(
        id="legacy-owner",
        settings={
            "social_login": {
                "google_linked": True,
                "google_user_id": "provider-subject",
            }
        },
    )
    db = _Db(user=legacy_owner)
    monkeypatch.setattr(
        identities,
        "update_user_settings_bulk",
        lambda *_args, **_kwargs: pytest.fail("a conflicting claim must not write settings"),
    )

    with pytest.raises(HTTPException) as exc_info:
        identities.record_social_identity(
            "attacker-user",
            "google",
            {"sub": "provider-subject", "email": "attacker@example.com"},
            db,
            commit=False,
        )

    assert exc_info.value.status_code == 409
    assert db.added == []
    assert db.commits == 0


def test_record_social_identity_lazily_normalizes_the_legacy_owner(monkeypatch):
    """The same user's verified login remains a supported migration path."""
    legacy_owner = SimpleNamespace(
        id="legacy-owner",
        settings={
            "social_login": {
                "google_linked": True,
                "google_user_id": "provider-subject",
            }
        },
    )
    db = _Db(user=legacy_owner)
    setting_writes = []
    monkeypatch.setattr(
        identities,
        "update_user_settings_bulk",
        lambda *args, **kwargs: setting_writes.append((args, kwargs)),
    )

    identity = identities.record_social_identity(
        "legacy-owner",
        "google",
        {"sub": "provider-subject", "email": "owner@example.com"},
        db,
        commit=False,
    )

    assert identity.user_id == "legacy-owner"
    assert db.added == [identity]
    assert setting_writes


def test_incomplete_legacy_social_flag_is_not_a_sign_in_method(monkeypatch):
    """A stale linked flag without an immutable subject cannot prevent lockout."""
    monkeypatch.setattr(
        identities,
        "get_user_setting_value",
        lambda _user_id, _section, key, _db, **_kwargs: key == "google_linked",
    )

    assert identities._legacy_social_linked("user-id", "google", object()) is False


def test_sign_in_method_policy_blocks_the_last_method(monkeypatch):
    monkeypatch.setattr(
        identities,
        "_password_is_configured",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(identities, "_active_passkey_count", lambda *_args: 0)
    monkeypatch.setattr(
        identities,
        "_social_method_snapshot",
        lambda *_args, **_kwargs: [
            {
                "provider": "google",
                "label": "Google",
                "linked": True,
                "available": True,
                "account_hint": "us***@example.com",
            },
            {
                "provider": "github",
                "label": "GitHub",
                "linked": False,
                "available": True,
                "account_hint": None,
            },
        ],
    )

    methods = identities.get_sign_in_methods(
        "user-id",
        _Db(user=SimpleNamespace(id="user-id", auth_management_mode="local")),
    )

    assert methods["providers"][0]["can_unlink"] is False
    assert methods["providers"][0]["unlink_blocked_reason"] == "last_sign_in_method"
    assert methods["providers"][1]["can_link"] is True


def test_sign_in_method_policy_allows_unlink_with_password(monkeypatch):
    monkeypatch.setattr(
        identities,
        "_password_is_configured",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(identities, "_active_passkey_count", lambda *_args: 0)
    monkeypatch.setattr(
        identities,
        "_social_method_snapshot",
        lambda *_args, **_kwargs: [
            {
                "provider": "google",
                "label": "Google",
                "linked": True,
                "available": True,
                "account_hint": None,
            }
        ],
    )

    methods = identities.get_sign_in_methods(
        "user-id",
        _Db(user=SimpleNamespace(id="user-id", auth_management_mode="local")),
    )

    assert methods["providers"][0]["can_unlink"] is True


def test_sign_in_methods_hide_local_methods_for_externally_managed_user():
    """The authoritative user row must close every local sign-in method."""

    methods = identities.get_sign_in_methods(
        "managed-user",
        _Db(
            user=SimpleNamespace(
                id="managed-user",
                auth_management_mode="external",
                external_auth_provider="oidc",
            )
        ),
    )

    assert methods == {
        "password_configured": False,
        "passkey_count": 0,
        "providers": [],
        "externally_managed": True,
        "external_auth_provider": "oidc",
    }


def test_sign_in_methods_propagate_user_lookup_attribute_errors():
    """A broken session or model lookup must fail closed, not become local."""

    class _BrokenDb:
        def query(self, _entity):
            raise AttributeError("broken query adapter")

    with pytest.raises(AttributeError, match="broken query adapter"):
        identities.get_sign_in_methods("managed-user", _BrokenDb())


def test_unlink_rechecks_last_method_policy_under_user_lock(monkeypatch):
    db = _Db(user=SimpleNamespace(id="user-id"))
    policy_reads = []

    def get_methods(*_args, commit=True, **_kwargs):
        policy_reads.append(commit)
        return {
            "password_configured": False,
            "passkey_count": 0,
            "providers": [
                {
                    "provider": "google",
                    "linked": True,
                    "can_unlink": False,
                }
            ],
        }

    monkeypatch.setattr(
        identities,
        "get_sign_in_methods",
        get_methods,
    )

    with pytest.raises(HTTPException) as exc_info:
        identities.unlink_social_identity("user-id", "google", db)

    assert exc_info.value.status_code == 409
    assert db.commits == 0
    assert policy_reads == [False]


def test_link_context_requires_a_live_recently_stepped_up_session():
    context = {
        "provider": "google",
        "user_id": "user-id",
        "authentication_id": "auth-id",
    }
    fresh = SimpleNamespace(
        id="auth-id",
        user_id="user-id",
        step_up_authenticated_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    assert identities.validate_social_link_session(
        context,
        "google",
        _Db(authentication=fresh),
    ) == "user-id"

    expired = SimpleNamespace(
        id="auth-id",
        user_id="user-id",
        step_up_authenticated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    with pytest.raises(HTTPException) as exc_info:
        identities.validate_social_link_session(
            context,
            "google",
            _Db(authentication=expired),
        )
    assert exc_info.value.status_code == 403


def test_social_link_cookie_supports_cross_site_oauth_callback(monkeypatch):
    response = Response()
    monkeypatch.setattr(account_slots, "_get_flow_cookie_secret", lambda _db: "x" * 64)
    monkeypatch.setattr(account_slots, "should_secure_auth_cookie", lambda *_args: True)

    account_slots.set_social_link_context_cookie(
        response,
        object(),
        None,
        user_id="user-id",
        authentication_id="auth-id",
        provider="google",
        state_hash="state-hash",
        native_state="native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE",
        native_code_challenge="challenge-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE",
    )

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=none" in cookie
    assert "Secure" in cookie
    parsed = SimpleCookie()
    parsed.load(cookie)
    request = SimpleNamespace(
        cookies={
            account_slots.SOCIAL_LINK_FLOW_COOKIE:
                parsed[account_slots.SOCIAL_LINK_FLOW_COOKIE].value,
        }
    )
    context = account_slots.read_social_link_context_cookie(request, object())
    assert context["native_state"] == "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    assert context["native_code_challenge"] == "challenge-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"


def test_oauth_link_callback_links_current_user_instead_of_signing_in(monkeypatch):
    state = "callback-state"
    linked = []
    audited = []
    provider = SimpleNamespace(TOKEN_URL="https://provider.example/token", is_enabled=lambda: True)
    request = SimpleNamespace(
        method="GET",
        headers={"host": "chat.example"},
        cookies={
            auth_router.SOCIAL_LINK_FLOW_COOKIE: "signed-link-context",
            "social_state": hashlib.sha256(state.encode()).hexdigest(),
            "social_nonce": "nonce-hash",
        },
    )
    context = {
        "provider": "google",
        "user_id": "user-id",
        "authentication_id": "auth-id",
        "state_hash": hashlib.sha256(state.encode()).hexdigest(),
    }

    async def verified_identity(*_args, **_kwargs):
        return {"sub": "provider-subject", "email": "user@example.com"}

    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: context)
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)
    monkeypatch.setattr(auth_router, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "build_auth_redirect_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(auth_router, "verified_social_user_info_from_callback", verified_identity)
    monkeypatch.setattr(identities, "validate_social_link_session", lambda *_args: "user-id")
    monkeypatch.setattr(
        identities,
        "link_social_identity",
        lambda user_id, provider_name, user_info, db: linked.append(
            (user_id, provider_name, user_info["sub"])
        ),
    )
    monkeypatch.setattr(
        auth_router,
        "social_login_callback",
        lambda *_args, **_kwargs: pytest.fail("link callbacks must not enter the sign-in flow"),
    )
    monkeypatch.setattr(
        auth_router,
        "_audit_auth_security_event",
        lambda *_args: audited.append(_args[4]),
    )
    monkeypatch.setattr(auth_router, "clear_social_link_context_cookie", lambda *_args: None)

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code="authorization-code",
            state=state,
            db=object(),
            db_log=object(),
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/index?social_link=success&provider=google"
    assert linked == [("user-id", "google", "provider-subject")]
    assert audited == ["SOCIAL_IDENTITY_LINKED"]


def test_stale_social_link_cookie_falls_back_to_sign_in_and_is_cleared(monkeypatch):
    state = "callback-state"
    sign_in_calls = []
    provider = SimpleNamespace(TOKEN_URL="https://provider.example/token", is_enabled=lambda: True)
    request = SimpleNamespace(
        method="GET",
        headers={"host": "chat.example"},
        cookies={
            auth_router.SOCIAL_LINK_FLOW_COOKIE: "signed-link-context",
            "social_state": hashlib.sha256(state.encode()).hexdigest(),
            "social_nonce": "nonce-hash",
        },
    )
    context = {
        "provider": "google",
        "user_id": "user-id",
        "authentication_id": "auth-id",
        "state_hash": hashlib.sha256(b"different-state").hexdigest(),
    }

    async def sign_in_callback(*_args, **_kwargs):
        sign_in_calls.append(True)
        return RedirectResponse(url="/signed-in", status_code=302)

    def clear_link_cookie(response, *_args):
        response.delete_cookie(auth_router.SOCIAL_LINK_FLOW_COOKIE)

    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: context)
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)
    monkeypatch.setattr(auth_router, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "build_auth_redirect_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(auth_router, "social_login_callback", sign_in_callback)
    monkeypatch.setattr(auth_router, "clear_social_link_context_cookie", clear_link_cookie)

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code="authorization-code",
            state=state,
            db=object(),
            db_log=object(),
        )
    )

    assert result.headers["location"] == "/signed-in"
    assert sign_in_calls == [True]
    assert any(
        auth_router.SOCIAL_LINK_FLOW_COOKIE in cookie
        for cookie in result.headers.getlist("set-cookie")
    )


def test_social_factor_routes_require_step_up_before_mutation():
    source = Path(auth_router.__file__).read_text(encoding="utf-8")

    for marker in (
        'def init_social_identity_link(',
        'def unlink_social_identity_route(',
    ):
        route = source[source.index(marker):]
        route = route[: route.find("\n\n\n", 1)]
        assert "enforce_same_origin" in route
        assert "require_sensitive_action_auth" in route
