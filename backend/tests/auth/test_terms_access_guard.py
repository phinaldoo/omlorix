import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException, WebSocketException
from starlette.requests import Request
from starlette.websockets import WebSocket

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app import dependencies


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "headers": [],
            "client": ("203.0.113.10", 12345),
        }
    )


def _request_with_refresh_cookie(path: str) -> Request:
    """Create a request for the one app route authorized by a refresh token."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "headers": [(b"cookie", b"refresh_token=refresh-token")],
            "client": ("203.0.113.10", 12345),
        }
    )


def _websocket(path: str) -> WebSocket:
    """Create the smallest authenticated WebSocket shape used by the dependency."""
    return WebSocket(
        {
            "type": "websocket",
            "scheme": "ws",
            "server": ("testserver", 80),
            "path": path,
            "headers": [(b"cookie", b"omlorix_access_token=token")],
            "client": ("203.0.113.10", 12345),
        },
        receive=lambda: None,
        send=lambda _message: None,
    )


def _credentials():
    return SimpleNamespace(scheme="Bearer", credentials="token")


def _user():
    return SimpleNamespace(id="user-1", role="user")


def _token_user(user):
    return lambda *args, **kwargs: user


def _disable_2fa_policy(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "ensure_access_token_satisfies_current_2fa_policy",
        lambda *_args, **_kwargs: None,
    )


def _required_terms_policy(_db, user_id):
    assert user_id == "user-1"
    return {
        "revision": 5,
        "accepted_current_revision": False,
        "require_current_revision_for_access": True,
    }


def _accepted_terms_policy(_db, user_id):
    assert user_id == "user-1"
    return {
        "revision": 5,
        "accepted_current_revision": True,
        "require_current_revision_for_access": True,
    }


def test_verified_user_blocks_protected_route_for_required_terms(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", _required_terms_policy)

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_user(_request("/api/v1/chats"), _credentials(), db=object())

    assert exc.value.status_code == 423
    assert exc.value.detail == {
        "type": "terms_of_service_acceptance_required",
        "revision": 5,
    }


def test_verified_user_allows_terms_acceptance_route_without_policy_recursion(monkeypatch):
    user = _user()
    policy_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal policy_called
        policy_called = True
        raise AssertionError("The acceptance route must not be blocked by its own guard")

    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", fail_if_called)

    result = dependencies.verified_user(
        _request("/api/v1/users/terms-of-service/accept"),
        _credentials(),
        db=object(),
    )

    assert result is user
    assert policy_called is False


def test_verified_user_allows_chat_setup_to_load_terms_state(monkeypatch):
    user = _user()
    policy_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal policy_called
        policy_called = True
        raise AssertionError("Chat setup must remain available to render the legal state")

    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", fail_if_called)

    result = dependencies.verified_user(
        _request("/api/v1/settings/chat/setup"),
        _credentials(),
        db=object(),
    )

    assert result is user
    assert policy_called is False


def test_verified_admin_blocks_protected_route_for_required_terms(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_admin_by_token", lambda *args: user)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", _required_terms_policy)

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_admin(_request("/api/v1/admin/users"), _credentials(), db=object())

    assert exc.value.status_code == 423
    assert exc.value.detail["type"] == "terms_of_service_acceptance_required"


def test_verified_user_allows_protected_route_after_current_terms_are_accepted(monkeypatch):
    """Preserve normal authenticated behavior once the legal gate is satisfied."""
    user = _user()
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", _accepted_terms_policy)

    result = dependencies.verified_user(_request("/api/v1/chats"), _credentials(), db=object())

    assert result is user


def test_verified_websocket_user_rejects_stale_terms_acceptance(monkeypatch):
    """Apply the same access policy to non-HTTP authenticated app sessions."""
    user = _user()
    monkeypatch.setattr(dependencies, "enforce_same_origin", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", _required_terms_policy)

    with pytest.raises(WebSocketException) as exc:
        dependencies.verified_websocket_user(
            _websocket("/api/v1/realtime/session/session-1/google-live"),
            db=object(),
        )

    assert exc.value.code == 1008
    assert exc.value.reason == "Access denied"


def test_refresh_authenticated_app_route_rejects_stale_terms_acceptance(monkeypatch):
    """Do not let refresh-token-authorized OAuth setup bypass the central gate."""
    user = _user()
    monkeypatch.setattr(dependencies, "get_active_slot", lambda _request: None)
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    monkeypatch.setattr(dependencies, "get_terms_of_service_policy", _required_terms_policy)

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_user_refresh(
            _request_with_refresh_cookie("/api/v1/connections/providers/example/connect"),
            db=object(),
        )

    assert exc.value.status_code == 423
    assert exc.value.detail["type"] == "terms_of_service_acceptance_required"
