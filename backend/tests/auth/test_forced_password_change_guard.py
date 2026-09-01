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


def _credentials():
    return SimpleNamespace(scheme="Bearer", credentials="token")


def _user():
    return SimpleNamespace(id="user-1", role="admin")


def _allow_privacy_and_terms(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "get_terms_of_service_policy",
        lambda *_args, **_kwargs: {
            "revision": 1,
            "accepted_current_revision": True,
            "require_current_revision_for_access": False,
        },
    )


def test_verified_admin_blocks_protected_route_until_password_is_changed(monkeypatch):
    user = _user()
    monkeypatch.setattr(dependencies, "check_admin_by_token", lambda *args, **kwargs: user)
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: True)
    _allow_privacy_and_terms(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_admin(_request("/api/v1/admin/users"), _credentials(), db=object())

    assert exc.value.status_code == 423
    assert exc.value.detail == "Password change required before accessing other resources"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users/password/change",
        "/api/v1/users/password/set",
        "/api/v1/users/password/requirements",
        "/api/v1/auth/logout",
        "/api/v1/auth/logins",
        "/api/v1/auth/login",
    ],
)
def test_verified_user_allows_password_change_recovery_routes(monkeypatch, path):
    user = _user()
    monkeypatch.setattr(dependencies, "check_user_by_token", lambda *args, **kwargs: user)
    monkeypatch.setattr(
        dependencies,
        "ensure_access_token_satisfies_current_2fa_policy",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(dependencies, "get_user_setting_value", lambda *args: True)
    _allow_privacy_and_terms(monkeypatch)

    result = dependencies.verified_user(_request(path), _credentials(), db=object())

    assert result is user
