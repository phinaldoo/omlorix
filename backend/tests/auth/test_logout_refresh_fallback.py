import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = fastapi.HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.auth import router as auth_router
from app.auth import utils as auth_utils


def test_logout_falls_back_to_refresh_session_when_access_token_is_expired(monkeypatch):
    request = SimpleNamespace(
        cookies={},
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    response = SimpleNamespace()
    calls = {}

    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda request, db: None)
    monkeypatch.setattr(
        auth_router,
        "resolve_access_token",
        lambda request: (_ for _ in ()).throw(HTTPException(status_code=401, detail="access token has expired")),
    )
    monkeypatch.setattr(
        auth_router,
        "get_active_refresh_token",
        lambda request, response, db: ("refresh-token", 1),
    )

    def fake_check_user_by_token(token, ip_address, token_type, db, *, enforce_2fa_policy=True):
        calls["checked_token"] = token
        calls["checked_type"] = token_type
        calls["enforce_2fa_policy"] = enforce_2fa_policy
        return SimpleNamespace(id="user-1", external_auth_provider="oidc")

    def fake_logout(
        db,
        db_log,
        request,
        user_id,
        token,
        response,
        *,
        token_type="access",
        external_auth_provider=None,
    ):
        calls["logout"] = {
            "user_id": user_id,
            "token": token,
            "token_type": token_type,
            "external_auth_provider": external_auth_provider,
        }
        return {"status": "success"}

    monkeypatch.setattr(auth_router, "check_user_by_token", fake_check_user_by_token)
    monkeypatch.setattr(auth_router, "logout", fake_logout)

    assert auth_router.logout_route(request, response, db=object(), db_log=object()) == {"status": "success"}
    assert calls == {
        "checked_token": "refresh-token",
        "checked_type": "refresh",
        "enforce_2fa_policy": False,
        "logout": {
            "user_id": "user-1",
            "token": "refresh-token",
            "token_type": "refresh",
            "external_auth_provider": "oidc",
        },
    }


def test_logout_preserves_access_token_error_when_no_refresh_session(monkeypatch):
    request = SimpleNamespace(cookies={}, headers={}, client=None)
    response = SimpleNamespace()

    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda request, db: None)
    monkeypatch.setattr(
        auth_router,
        "resolve_access_token",
        lambda request: (_ for _ in ()).throw(HTTPException(status_code=401, detail="access token has expired")),
    )
    monkeypatch.setattr(auth_router, "get_active_refresh_token", lambda request, response, db: (None, None))

    with pytest.raises(HTTPException) as exc_info:
        auth_router.logout_route(request, response, db=object(), db_log=object())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "access token has expired"


def test_logout_snapshots_refresh_token_before_deleting_session(monkeypatch):
    class AuthenticationEntry:
        deleted = False

        @property
        def refresh_token(self):
            if self.deleted:
                raise AssertionError("deleted authentication row was accessed")
            return "refresh-token"

    auth_entry = AuthenticationEntry()
    request = SimpleNamespace(
        cookies={
            "omlorix_active_slot": "1",
            "omlorix_refresh_slot_1": "refresh-token",
        },
        headers={"User-Agent": "test-browser"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    response = SimpleNamespace()
    cleared_slots = []

    monkeypatch.setattr(auth_utils, "get_authentication", lambda *args: auth_entry)

    def delete_authentication(*args, **kwargs):
        auth_entry.deleted = True
        return True

    monkeypatch.setattr(auth_utils, "delete_authentication", delete_authentication)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "clear_refresh_slot_cookie",
        lambda _response, slot, *_args: cleared_slots.append(slot),
    )
    monkeypatch.setattr(auth_utils, "clear_legacy_refresh_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "clear_access_token_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "list_browser_accounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(auth_utils, "clear_active_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "record_auth_logout_metric", lambda: None)

    assert auth_utils.logout(
        object(),
        object(),
        request,
        "user-1",
        "access-token",
        response,
    ) == {"status": "success"}
    assert cleared_slots == [1]


def test_logout_returns_oidc_end_session_url_for_the_final_browser_account(monkeypatch):
    request = SimpleNamespace(
        cookies={},
        headers={"User-Agent": "test-browser"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    response = SimpleNamespace()

    monkeypatch.setattr(
        auth_utils,
        "get_authentication",
        lambda *args: SimpleNamespace(refresh_token="refresh-token"),
    )
    monkeypatch.setattr(auth_utils, "delete_authentication", lambda *args, **kwargs: True)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "clear_refresh_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "clear_legacy_refresh_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "clear_access_token_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "list_browser_accounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(auth_utils, "clear_active_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_utils, "record_auth_logout_metric", lambda: None)
    monkeypatch.setattr(
        auth_utils,
        "_resolve_oidc_rp_logout_url",
        lambda _db, provider: (
            "https://idp.example/application/o/omlorix/end-session/"
            if provider == "oidc"
            else None
        ),
    )

    assert auth_utils.logout(
        object(),
        object(),
        request,
        "user-1",
        "access-token",
        response,
        external_auth_provider="oidc",
    ) == {
        "status": "success",
        "federated_logout_url": "https://idp.example/application/o/omlorix/end-session/",
    }
