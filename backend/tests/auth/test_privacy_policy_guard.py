import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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


def _request_with_access_cookie(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "headers": [(b"cookie", b"omlorix_access_token=cookie-token")],
            "client": ("203.0.113.10", 12345),
        }
    )


def _credentials():
    return SimpleNamespace(scheme="Bearer", credentials="token")


def _user():
    return SimpleNamespace(id="user-1", role="user")


def _token_user(user):
    return lambda *args, **kwargs: user


def _disable_2fa_policy(monkeypatch):
    monkeypatch.setattr(dependencies, "ensure_access_token_satisfies_current_2fa_policy", lambda *_args, **_kwargs: None)


def _disable_terms_policy(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "get_terms_of_service_policy",
        lambda *_args, **_kwargs: {
            "revision": 1,
            "accepted_current_revision": True,
            "require_current_revision_for_access": False,
        },
    )


def test_verified_user_does_not_block_modal_privacy_notice(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    _disable_terms_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)

    result = dependencies.verified_user(_request("/api/v1/chats"), _credentials(), db=object())

    assert result is user


def test_verified_user_allows_privacy_notice_route(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_user_by_token", _token_user(user))
    _disable_2fa_policy(monkeypatch)
    _disable_terms_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)

    result = dependencies.verified_user(
        _request("/api/v1/users/privacy-policy/notice"),
        _credentials(),
        db=object(),
    )

    assert result is user


def test_verified_admin_does_not_block_modal_privacy_notice(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_admin_by_token", lambda *args: user)
    _disable_terms_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)

    result = dependencies.verified_admin(_request("/api/v1/admin/users"), _credentials(), db=object())

    assert result is user


def test_verified_user_accepts_http_only_access_cookie_when_authorization_header_is_absent(monkeypatch):
    user = _user()
    seen = {}

    def fake_check_user_by_token(token, *_args, **_kwargs):
        seen["token"] = token
        return user

    monkeypatch.setattr(dependencies, "check_user_by_token", fake_check_user_by_token)
    _disable_2fa_policy(monkeypatch)
    _disable_terms_policy(monkeypatch)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)

    result = dependencies.verified_user(
        _request_with_access_cookie("/api/v1/chats"),
        None,
        db=object(),
    )

    assert result is user
    assert seen["token"] == "cookie-token"


def test_verified_user_enforces_same_origin_for_cookie_authenticated_post(monkeypatch):
    user = _user()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("chat.example", 443),
            "path": "/api/v1/chats/delete",
            "headers": [
                (b"cookie", b"omlorix_access_token=cookie-token"),
                (b"origin", b"https://evil.example"),
            ],
            "client": ("203.0.113.10", 12345),
        }
    )
    monkeypatch.setattr(dependencies, "check_user_by_token", lambda *args, **kwargs: user)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: False)

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_user(request, None, db=object())

    assert exc.value.status_code == 403
