import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.auth import token as auth_token
from app.auth import router as auth_router
from app.auth import step_up as step_up_capabilities


JWT_SECRET = "x" * 64
ROUTER_SOURCE = Path(__file__).resolve().parents[2] / "app" / "auth" / "router.py"


@pytest.fixture(autouse=True)
def _signing_material(monkeypatch):
    """Keep authentication tests independent from the process environment."""
    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (JWT_SECRET, "HS512"))


def test_created_access_tokens_include_iat(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())
    payload = jwt.decode(encoded, JWT_SECRET, algorithms=["HS512"])

    assert isinstance(payload["iat"], int)


def test_recent_auth_allows_fresh_session(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )
    auth_entry = SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(seconds=30))
    monkeypatch.setattr(auth_token, "get_authentication", lambda db, user_id, token, token_type: auth_entry)

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())
    payload = auth_token.require_recent_auth_token(encoded, object())

    assert payload["sub"] == "user-id"


def test_recent_auth_rejects_fresh_token_for_stale_session(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )
    auth_entry = SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    monkeypatch.setattr(auth_token, "get_authentication", lambda db, user_id, token, token_type: auth_entry)

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())

    with pytest.raises(HTTPException) as exc_info:
        auth_token.require_recent_auth_token(encoded, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Recent authentication required"


def test_step_up_rejects_fresh_session_without_explicit_step_up(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )
    auth_entry = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        step_up_authenticated_at=None,
    )
    monkeypatch.setattr(auth_token, "get_authentication", lambda db, user_id, token, token_type: auth_entry)

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())

    with pytest.raises(HTTPException) as exc_info:
        auth_token.require_step_up_auth_token(encoded, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Step-up authentication required"


def test_step_up_allows_recent_explicit_step_up(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )
    auth_entry = SimpleNamespace(
        created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        step_up_authenticated_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    monkeypatch.setattr(auth_token, "get_authentication", lambda db, user_id, token, token_type: auth_entry)

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())
    payload = auth_token.require_step_up_auth_token(encoded, object())

    assert payload["sub"] == "user-id"


def test_step_up_rejects_expired_explicit_step_up(monkeypatch):
    monkeypatch.setattr(
        auth_token,
        "get_value_by_page_and_key",
        lambda page, key, db: 30,
    )
    auth_entry = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        step_up_authenticated_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    monkeypatch.setattr(auth_token, "get_authentication", lambda db, user_id, token, token_type: auth_entry)

    encoded = auth_token.create_access_token({"sub": "user-id", "type": "access"}, object())

    with pytest.raises(HTTPException) as exc_info:
        auth_token.require_step_up_auth_token(encoded, object())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Step-up authentication required"


def test_step_up_capabilities_report_only_enrolled_methods(monkeypatch):
    """The modal must not request factors the current user never configured."""

    user = SimpleNamespace(id="user-id", hashed_password="hash")
    monkeypatch.setattr(step_up_capabilities, "_has_usable_password", lambda *_args: True)
    monkeypatch.setattr(step_up_capabilities, "_has_active_passkey", lambda *_args: False)
    monkeypatch.setattr(step_up_capabilities, "resolve_user_2fa_provider", lambda *_args: "totp")
    monkeypatch.setattr(step_up_capabilities, "_is_user_enrolled_for_provider", lambda *_args: False)

    assert step_up_capabilities.get_step_up_methods(user, object()) == {
        "password": True,
        "otp": False,
        "passkey": False,
    }


def test_federated_placeholder_password_is_not_offered_for_step_up(monkeypatch):
    user = SimpleNamespace(id="user-id", hashed_password="internal-placeholder")
    monkeypatch.setattr(
        step_up_capabilities,
        "get_user_setting_value",
        lambda _user_id, page, key, _db: page == "social_login" and key == "needs_password_setup",
    )

    assert step_up_capabilities._has_usable_password(user, object()) is False


def test_factor_management_routes_apply_expected_step_up_policy():
    source = ROUTER_SOURCE.read_text()

    for marker in ('@auth_router.delete("/passkeys/{passkey_id}")',):
        route_block = source[source.index(marker):]
        route_block = route_block[: route_block.find("\n\n\n", 1)]
        assert "require_sensitive_action_auth" in route_block

    deactivate_route = source[source.index('@auth_router.post("/twofa/deactivate")'):]
    deactivate_route = deactivate_route[: deactivate_route.find("\n\n\n", 1)]
    assert "verified_access_token" in deactivate_route
    assert "require_sensitive_action_auth" in deactivate_route

    setup_route = source[source.index('@auth_router.post("/twofa/setup")'):]
    setup_route = setup_route[: setup_route.find("\n\n\n", 1)]
    assert "require_sensitive_action_auth" in setup_route

    setup_material_route = source[source.index('@auth_router.get("/twofa/setup-material")'):]
    setup_material_route = setup_material_route[: setup_material_route.find("\n\n\n", 1)]
    assert "require_sensitive_action_auth" in setup_material_route
    assert "enforce_same_origin" in setup_material_route

    for marker in (
        '@auth_router.post("/passkeys/register/begin")',
        '@auth_router.post("/passkeys/register/finish")',
    ):
        route_block = source[source.index(marker):]
        route_block = route_block[: route_block.find("\n\n\n", 1)]
        assert "require_sensitive_action_auth" in route_block

    otp_begin_route = source[source.index('@auth_router.post("/step-up/otp/begin"'):]
    otp_begin_route = otp_begin_route[: otp_begin_route.find("\n\n\n", 1)]
    assert "verified_user" in otp_begin_route
    assert "enforce_same_origin" in otp_begin_route
    assert "_is_user_enrolled_for_provider" in otp_begin_route
    assert "begin_verify" in otp_begin_route

    methods_route = source[source.index('@auth_router.get("/step-up/methods"'):]
    methods_route = methods_route[: methods_route.find("\n\n\n", 1)]
    assert "response_model=StepUpMethodsResponse" in methods_route
    assert "verified_user" in methods_route
    assert "get_step_up_methods" in methods_route
    assert "require_recent_auth_token" in methods_route
    assert 'response.headers["Cache-Control"] = "private, no-store"' in methods_route

    sign_in_methods_route = source[source.index('@auth_router.get("/sign-in-methods"'):]
    sign_in_methods_route = sign_in_methods_route[: sign_in_methods_route.find("\n\n\n", 1)]
    assert "response_model=SignInMethodsResponse" in sign_in_methods_route
    assert 'response.headers["Cache-Control"] = "private, no-store"' in sign_in_methods_route


def test_twofa_deactivation_route_enforces_step_up_before_mutation(monkeypatch):
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    user = SimpleNamespace(id="user-id")
    calls = []

    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: calls.append("origin"))
    monkeypatch.setattr(
        auth_router,
        "require_sensitive_action_auth",
        lambda *_args: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Step-up authentication required")
        ),
    )
    monkeypatch.setattr(
        auth_router,
        "deactivate_twofa",
        lambda *_args: calls.append("deactivate") or {"status": "success"},
    )
    monkeypatch.setattr(auth_router, "_audit_auth_security_event", lambda *_args: calls.append("audit"))

    with pytest.raises(HTTPException) as exc_info:
        auth_router.deactivate_twofa_route(request, object(), object(), user, "access-token")

    assert exc_info.value.status_code == 403
    assert calls == ["origin"]

    monkeypatch.setattr(
        auth_router,
        "require_sensitive_action_auth",
        lambda *_args: calls.append("step-up"),
    )
    result = auth_router.deactivate_twofa_route(request, object(), object(), user, "access-token")

    assert result == {"status": "success"}
    assert calls == ["origin", "origin", "step-up", "deactivate", "audit"]
