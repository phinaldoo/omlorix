import base64
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa
from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import sessionmaker

from app.auth import account_slots
from app.auth import router as auth_router
from app.auth.models import NativeAuthGrant
from app.auth.native import (
    consume_native_auth_grant,
    create_native_auth_grant,
    get_native_callback_origin,
    native_callback_url,
)
from app.auth.schemas import NativeSocialLinkExchangeRequest


def _pkce_pair() -> tuple[str, str]:
    verifier = "native-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


@pytest.fixture()
def db_session():
    engine = sa.create_engine("sqlite:///:memory:")
    NativeAuthGrant.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def configured_native_callback_origin(monkeypatch):
    monkeypatch.setenv("OMLORIX_NATIVE_CALLBACK_ORIGIN", "https://native.example")


def test_native_grant_requires_matching_state_and_pkce_and_is_one_time(db_session):
    verifier, challenge = _pkce_pair()
    state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    code = create_native_auth_grant(
        db_session,
        purpose="social_exchange",
        provider="google",
        user_id="user-id",
        code_challenge=challenge,
        state=state,
        account_mode="add",
        replace_slot=2,
        twofa_satisfied=True,
    )

    grant = consume_native_auth_grant(
        db_session,
        code,
        expected_purposes={"social_exchange"},
        state=state,
        code_verifier=verifier,
    )

    assert grant.user_id == "user-id"
    assert grant.provider == "google"
    assert grant.account_mode == "add"
    assert grant.replace_slot == 2
    assert grant.twofa_satisfied is True

    with pytest.raises(HTTPException, match="already used"):
        consume_native_auth_grant(
            db_session,
            code,
            expected_purposes={"social_exchange"},
            state=state,
            code_verifier=verifier,
        )


@pytest.mark.parametrize(
    ("state", "verifier", "message"),
    [
        ("different-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE", None, "state mismatch"),
        (None, "wrong-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE", "PKCE verification failed"),
    ],
)
def test_native_grant_rejects_correlation_mismatch(db_session, state, verifier, message):
    valid_verifier, challenge = _pkce_pair()
    valid_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    code = create_native_auth_grant(
        db_session,
        purpose="social_exchange",
        provider="google",
        code_challenge=challenge,
        state=valid_state,
    )

    with pytest.raises(HTTPException, match=message):
        consume_native_auth_grant(
            db_session,
            code,
            expected_purposes={"social_exchange"},
            state=state or valid_state,
            code_verifier=verifier or valid_verifier,
        )


def test_creating_native_grant_prunes_expired_and_old_consumed_rows(db_session):
    _verifier, challenge = _pkce_pair()
    state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    now = datetime.now(timezone.utc)
    expired = NativeAuthGrant(
        token_hash="e" * 64,
        purpose="social_start",
        provider="google",
        code_challenge=challenge,
        state_hash="s" * 64,
        account_mode="primary",
        accepts_terms_of_service=False,
        twofa_satisfied=False,
        created_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=30),
        consumed_at=None,
    )
    old_consumed = NativeAuthGrant(
        token_hash="c" * 64,
        purpose="social_start",
        provider="google",
        code_challenge=challenge,
        state_hash="u" * 64,
        account_mode="primary",
        accepts_terms_of_service=False,
        twofa_satisfied=False,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        consumed_at=now - timedelta(minutes=2),
    )
    live = NativeAuthGrant(
        token_hash="l" * 64,
        purpose="social_start",
        provider="google",
        code_challenge=challenge,
        state_hash="t" * 64,
        account_mode="primary",
        accepts_terms_of_service=False,
        twofa_satisfied=False,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        consumed_at=None,
    )
    expired_hash = expired.token_hash
    old_consumed_hash = old_consumed.token_hash
    live_hash = live.token_hash
    db_session.add_all([expired, old_consumed, live])
    db_session.commit()

    create_native_auth_grant(
        db_session,
        purpose="social_start",
        provider="google",
        code_challenge=challenge,
        state=state,
    )

    hashes = {
        row.token_hash
        for row in db_session.query(NativeAuthGrant).all()
    }
    assert expired_hash not in hashes
    assert old_consumed_hash not in hashes
    assert live_hash in hashes
    assert len(hashes) == 2


def test_native_callback_contains_only_bounded_handoff_fields():
    state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    callback = native_callback_url(
        path="federated",
        state=state,
        code="one-time-code",
        provider="Google",
        status="social",
    )

    parsed = urlsplit(callback)
    parameters = parse_qs(parsed.fragment)
    assert parsed.scheme == "https"
    assert parsed.netloc == "native.example"
    assert parsed.path == "/auth/federated"
    assert parsed.query == ""
    assert parameters == {
        "state": [state],
        "code": ["one-time-code"],
        "provider": ["google"],
        "status": ["social"],
    }
    assert "access_token" not in callback
    assert "refresh_token" not in callback


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://native.example",
        "https://native.example/auth",
        "https://user@native.example",
        "https://native.example?source=app",
    ],
)
def test_native_callback_origin_must_be_an_explicit_https_origin(monkeypatch, origin):
    monkeypatch.setenv("OMLORIX_NATIVE_CALLBACK_ORIGIN", origin)
    with pytest.raises(HTTPException, match="OMLORIX_NATIVE_CALLBACK_ORIGIN"):
        get_native_callback_origin()


def test_signed_flow_cookie_preserves_native_handoff_context(monkeypatch):
    monkeypatch.setattr(account_slots, "_get_flow_cookie_secret", lambda _db: "x" * 64)
    monkeypatch.setattr(
        account_slots,
        "_get_cookie_security_settings",
        lambda *_args: (True, "none", "https"),
    )
    response = Response()
    state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    _verifier, challenge = _pkce_pair()
    account_slots.set_flow_context_cookie(
        response,
        object(),
        None,
        cookie_name=account_slots.SOCIAL_FLOW_COOKIE,
        account_mode="primary",
        replace_slot=None,
        return_url="",
        native_auth=True,
        native_kind="social",
        native_provider="google",
        native_code_challenge=challenge,
        native_state=state,
    )
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    request = SimpleNamespace(
        cookies={
            account_slots.SOCIAL_FLOW_COOKIE: cookies[account_slots.SOCIAL_FLOW_COOKIE].value,
        }
    )

    context = account_slots.read_flow_context_cookie(
        request,
        object(),
        cookie_name=account_slots.SOCIAL_FLOW_COOKIE,
    )

    assert context["native_auth"] is True
    assert context["native_kind"] == "social"
    assert context["native_provider"] == "google"
    assert context["native_code_challenge"] == challenge
    assert context["native_state"] == state


def test_native_link_init_keeps_same_origin_and_step_up_guards():
    source = Path(auth_router.__file__).read_text(encoding="utf-8")
    route = source[source.index("def init_native_social_identity_link("):]
    route = route[: route.index("\n\n\n", 1)]
    assert "enforce_same_origin" in route
    assert "require_sensitive_action_auth" in route
    assert "get_authentication" in route


def test_native_exchange_is_same_origin_and_pkce_bound():
    source = Path(auth_router.__file__).read_text(encoding="utf-8")
    route = source[source.index("def exchange_native_auth_code("):]
    route = route[: route.index("\n\n\n", 1)]
    assert "enforce_same_origin" in route
    assert "code_verifier=payload.code_verifier" in route
    assert "twofa_satisfied=grant.twofa_satisfied" in route
    assert 'if payload.kind == "social"' in route
    assert "require_locally_managed_account(user)" in route


def test_native_social_link_callback_defers_mutation_until_pkce_exchange(monkeypatch):
    """The browser callback may verify identity, but must not link it yet."""
    oauth_state = "provider-oauth-state"
    state_hash = hashlib.sha256(oauth_state.encode("utf-8")).hexdigest()
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    _verifier, challenge = _pkce_pair()
    request = SimpleNamespace(
        method="GET",
        headers={},
        cookies={
            account_slots.SOCIAL_LINK_FLOW_COOKIE: "signed-link-context",
            "social_state": state_hash,
            "social_nonce": "nonce-hash",
        },
    )
    link_context = {
        "provider": "google",
        "user_id": "user-id",
        "authentication_id": "authentication-id",
        "state_hash": state_hash,
        "native_state": native_state,
        "native_code_challenge": challenge,
    }
    provider = SimpleNamespace(
        TOKEN_URL="https://provider.example/token",
        AUTHORIZATION_URL="https://provider.example/authorize",
        is_enabled=lambda: True,
        validate_domain=lambda _email: True,
        validate_identity=lambda _claims: True,
    )
    verified_user_info = {
        "sub": "provider-subject",
        "email": "person@example.com",
    }
    created_grant: dict[str, object] = {}
    link_calls: list[tuple] = []

    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: link_context)
    monkeypatch.setattr(auth_router, "clear_social_link_context_cookie", lambda *_args: None)
    monkeypatch.setattr(auth_router, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "build_auth_redirect_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(auth_router, "_audit_auth_security_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        auth_router,
        "verified_social_user_info_from_callback",
        AsyncMock(return_value=verified_user_info),
    )

    from app.auth import identities

    monkeypatch.setattr(identities, "validate_social_link_session", lambda *_args: "user-id")
    monkeypatch.setattr(
        identities,
        "link_social_identity",
        lambda *args: link_calls.append(args),
    )

    def capture_grant(_db, **kwargs):
        created_grant.update(kwargs)
        return "pending-native-link-code"

    monkeypatch.setattr(auth_router, "create_native_auth_grant", capture_grant)

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code="provider-code",
            state=oauth_state,
            db=object(),
            db_log=object(),
        )
    )

    callback = urlsplit(result.headers["location"])
    callback_parameters = parse_qs(callback.fragment)
    assert link_calls == []
    assert created_grant["purpose"] == "social_link_exchange"
    assert created_grant["code_challenge"] == challenge
    assert created_grant["identity_claims"] == verified_user_info
    assert callback.scheme == "https"
    assert callback.netloc == "native.example"
    assert callback.path == "/auth/link"
    assert callback.query == ""
    assert callback_parameters["state"] == [native_state]
    assert callback_parameters["code"] == ["pending-native-link-code"]
    assert callback_parameters["status"] == ["pending"]


def test_native_social_link_exchange_requires_pkce_before_mutation(db_session, monkeypatch):
    verifier, challenge = _pkce_pair()
    state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    identity_claims = {
        "sub": "provider-subject",
        "email": "person@example.com",
    }
    linked: list[dict[str, str]] = []
    expected_methods = {
        "password_configured": True,
        "passkey_count": 0,
        "providers": [],
    }
    provider = SimpleNamespace(is_enabled=lambda: True)

    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(auth_router, "_audit_auth_security_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)

    from app.auth import identities

    monkeypatch.setattr(identities, "normalize_social_provider", lambda value: value.lower())
    monkeypatch.setattr(identities, "validate_social_link_session", lambda *_args: "user-id")
    monkeypatch.setattr(
        identities,
        "link_social_identity",
        lambda _user_id, _provider, claims, _db: linked.append(claims),
    )
    monkeypatch.setattr(identities, "get_sign_in_methods", lambda *_args: expected_methods)

    wrong_code = create_native_auth_grant(
        db_session,
        purpose="social_link_exchange",
        provider="google",
        user_id="user-id",
        authentication_id="authentication-id",
        code_challenge=challenge,
        state=state,
        identity_claims=identity_claims,
    )
    with pytest.raises(HTTPException, match="PKCE verification failed"):
        auth_router.exchange_native_social_identity_link(
            "google",
            NativeSocialLinkExchangeRequest(
                code=wrong_code,
                code_verifier="wrong-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE",
                state=state,
            ),
            SimpleNamespace(),
            db_session,
            object(),
            SimpleNamespace(id="user-id"),
            "access-token",
        )
    assert linked == []

    code = create_native_auth_grant(
        db_session,
        purpose="social_link_exchange",
        provider="google",
        user_id="user-id",
        authentication_id="authentication-id",
        code_challenge=challenge,
        state=state,
        identity_claims=identity_claims,
    )
    result = auth_router.exchange_native_social_identity_link(
        "google",
        NativeSocialLinkExchangeRequest(
            code=code,
            code_verifier=verifier,
            state=state,
        ),
        SimpleNamespace(),
        db_session,
        object(),
        SimpleNamespace(id="user-id"),
        "access-token",
    )

    assert result == expected_methods
    assert linked == [identity_claims]


def _assert_native_failure_callback(
    response: Response,
    *,
    state: str,
    provider: str,
    kind: str,
    reason: str,
) -> None:
    callback = urlsplit(response.headers["location"])
    parameters = parse_qs(callback.fragment)
    assert callback.scheme == "https"
    assert callback.netloc == "native.example"
    assert callback.path == "/auth/federated"
    assert callback.query == ""
    assert parameters == {
        "state": [state],
        "provider": [provider],
        "status": [kind],
        "reason": [reason],
    }


def test_native_social_cancellation_returns_control_to_app(monkeypatch):
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    flow_context = {
        "native_auth": True,
        "native_kind": "social",
        "native_provider": "google",
        "native_state": native_state,
    }
    request = SimpleNamespace(method="GET", headers={}, cookies={})
    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: None)
    monkeypatch.setattr(auth_router, "read_flow_context_cookie", lambda *_args, **_kwargs: flow_context)
    monkeypatch.setattr(auth_router, "clear_flow_context_cookie", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code=None,
            state=None,
            db=object(),
            db_log=object(),
            error="access_denied",
        )
    )

    _assert_native_failure_callback(
        result,
        state=native_state,
        provider="google",
        kind="social",
        reason="cancelled",
    )


def test_native_social_eligibility_failure_returns_control_to_app(monkeypatch):
    oauth_state = "provider-oauth-state"
    state_hash = hashlib.sha256(oauth_state.encode("utf-8")).hexdigest()
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    flow_context = {
        "native_auth": True,
        "native_kind": "social",
        "native_provider": "google",
        "native_state": native_state,
    }
    request = SimpleNamespace(
        method="GET",
        headers={},
        cookies={"social_state": state_hash, "social_nonce": "nonce-hash"},
    )
    provider = SimpleNamespace(
        TOKEN_URL="https://provider.example/token",
        AUTHORIZATION_URL="https://provider.example/authorize",
        is_enabled=lambda: True,
    )
    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: None)
    monkeypatch.setattr(auth_router, "read_flow_context_cookie", lambda *_args, **_kwargs: flow_context)
    monkeypatch.setattr(auth_router, "clear_flow_context_cookie", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "build_auth_redirect_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        auth_router,
        "social_login_callback",
        AsyncMock(return_value=RedirectResponse("/login?error=account_inactive", status_code=302)),
    )

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code="provider-code",
            state=oauth_state,
            db=object(),
            db_log=object(),
        )
    )

    _assert_native_failure_callback(
        result,
        state=native_state,
        provider="google",
        kind="social",
        reason="not_eligible",
    )


def test_native_social_disabled_provider_returns_control_to_app(monkeypatch):
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    flow_context = {
        "native_auth": True,
        "native_kind": "social",
        "native_provider": "google",
        "native_state": native_state,
    }
    request = SimpleNamespace(method="GET", headers={}, cookies={})
    provider = SimpleNamespace(
        TOKEN_URL="https://provider.example/token",
        AUTHORIZATION_URL="https://provider.example/authorize",
        is_enabled=lambda: False,
    )
    monkeypatch.setattr(auth_router, "read_social_link_context_cookie", lambda *_args: None)
    monkeypatch.setattr(auth_router, "read_flow_context_cookie", lambda *_args, **_kwargs: flow_context)
    monkeypatch.setattr(auth_router, "clear_flow_context_cookie", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router.SocialAuthProviderFactory, "get_provider", lambda *_args: provider)

    result = asyncio.run(
        auth_router._handle_social_login_callback(
            provider="google",
            request=request,
            response=Response(),
            code="provider-code",
            state="provider-state",
            db=object(),
            db_log=object(),
        )
    )

    _assert_native_failure_callback(
        result,
        state=native_state,
        provider="google",
        kind="social",
        reason="unavailable",
    )


def test_native_sso_invalid_state_returns_control_to_app(monkeypatch):
    expected_state = "provider-state"
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    flow_context = {
        "native_auth": True,
        "native_kind": "sso",
        "native_provider": "oidc",
        "native_state": native_state,
    }
    request = SimpleNamespace(
        method="GET",
        query_params={"state": "wrong-provider-state"},
        headers={},
        cookies={"sso_state": hashlib.sha256(expected_state.encode("utf-8")).hexdigest()},
    )
    monkeypatch.setattr(auth_router, "read_flow_context_cookie", lambda *_args, **_kwargs: flow_context)
    monkeypatch.setattr(auth_router, "clear_flow_context_cookie", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        auth_router.sso_callback_route(
            provider_type="oidc",
            request=request,
            response=Response(),
            db=object(),
            db_log=object(),
        )
    )

    _assert_native_failure_callback(
        result,
        state=native_state,
        provider="oidc",
        kind="sso",
        reason="invalid_flow",
    )


def test_native_sso_json_failure_returns_control_without_detail(monkeypatch):
    from app.auth import utils as auth_utils

    provider_state = "provider-state"
    native_state = "native-state-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDE"
    flow_context = {
        "native_auth": True,
        "native_kind": "sso",
        "native_provider": "oidc",
        "native_state": native_state,
    }
    request = SimpleNamespace(
        method="GET",
        query_params={"state": provider_state},
        headers={},
        cookies={
            "sso_state": hashlib.sha256(provider_state.encode("utf-8")).hexdigest(),
            "sso_security": '{"nonce":"provider-nonce","request_id":null}',
        },
    )
    monkeypatch.setattr(auth_router, "read_flow_context_cookie", lambda *_args, **_kwargs: flow_context)
    monkeypatch.setattr(auth_router, "clear_flow_context_cookie", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "build_auth_redirect_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(
        auth_utils,
        "sso_login_callback",
        AsyncMock(return_value={"status": "error", "detail": "private provider detail"}),
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

    _assert_native_failure_callback(
        result,
        state=native_state,
        provider="oidc",
        kind="sso",
        reason="failed",
    )
    assert "private" not in result.headers["location"]
