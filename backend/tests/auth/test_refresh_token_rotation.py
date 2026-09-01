from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import jwt
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda *writer_args, **writer_kwargs: SimpleNamespace()
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda *reader_args, **reader_kwargs: SimpleNamespace()
    )
    sys.modules["zstandard"] = fake_zstandard


from app.auth import account_slots, token as auth_token


SECRET = "s" * 64


@pytest.fixture(autouse=True)
def _stub_terms_policy(monkeypatch):
    """Keep refresh-token tests focused on token rotation rather than legal settings DB reads."""
    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(
        auth_token,
        "get_terms_of_service_policy",
        lambda *_args, **_kwargs: {
            "revision": 1,
            "accepted_current_revision": True,
            "require_current_revision_for_access": False,
        },
    )


def _refresh_jwt(user_id: str = "user-1", *, expires_delta: timedelta | None = None) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=5)),
        },
        SECRET,
        algorithm="HS512",
    )


def _settings(page, key, db):
    values = {
        ("security", "access_token_expire_minutes"): 5,
        ("security", "refresh_token_expire_minutes"): 60,
        ("general", "application_name"): "Omlorix",
        ("states", "server_setup"): True,
    }
    return values.get((page, key))


def _request_with_cookies(cookie_header: str = "") -> Request:
    headers = []
    if cookie_header:
        headers.append((b"cookie", cookie_header.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/auth/refresh",
            "headers": headers,
        }
    )


def _set_cookie_headers(response) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in getattr(response, "raw_headers", [])
        if key.lower() == b"set-cookie"
    ]


def _current_resolution(session_id: str = "session-1") -> SimpleNamespace:
    """Return the resolver shape for an active refresh-token family."""

    return SimpleNamespace(
        state="current",
        authentication=SimpleNamespace(id=session_id, access_token="old-access"),
        rotated_at=None,
    )


def test_create_refresh_token_generates_unique_tokens(monkeypatch):
    monkeypatch.setattr(auth_token, "get_value_by_page_and_key", _settings)

    first = auth_token.create_refresh_token({"sub": "user-1", "type": "refresh"}, db=object())
    second = auth_token.create_refresh_token({"sub": "user-1", "type": "refresh"}, db=object())

    assert first != second


def test_refresh_rotates_refresh_token_and_cookie(monkeypatch):
    old_refresh = _refresh_jwt()
    rotated = {}
    cookies = {}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (old_refresh, 2))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: _current_resolution(),
    )
    monkeypatch.setattr(
        auth_token,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            is_active=True,
            lock={},
            account_type="regular",
            temporary_expires_at=None,
        ),
    )
    monkeypatch.setattr(auth_token, "create_access_token", lambda data, db: "new-access")
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda data, db: "new-refresh")

    def fake_rotate(db, user_id, refresh_token, new_access_token, new_refresh_token, **kwargs):
        rotated.update(
            {
                "user_id": user_id,
                "old_refresh": refresh_token,
                "new_access": new_access_token,
                "new_refresh": new_refresh_token,
                "session_id": kwargs["session_id"],
            }
        )

    def fake_set_refresh_cookie(response, slot, refresh_token, db, request):
        cookies["slot"] = slot
        cookies["refresh_token"] = refresh_token

    monkeypatch.setattr(auth_token, "rotate_authentication_tokens", fake_rotate)
    monkeypatch.setattr(auth_token, "update_last_active_user", lambda db, user_id: None)
    monkeypatch.setattr(auth_token, "create_authentication_log", lambda *args: None)
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "ensure_session_satisfies_current_2fa_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "build_login_2fa_session_claims", lambda *args, **kwargs: {})
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)
    def fake_user_setting_value(_user_id, page, key, _db):
        if (page, key) == ("general", "language"):
            return "de"
        return False

    monkeypatch.setattr(auth_token, "get_user_setting_value", fake_user_setting_value)
    monkeypatch.setattr(auth_token, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(auth_token, "set_refresh_slot_cookie", fake_set_refresh_cookie)
    monkeypatch.setattr(
        auth_token,
        "set_access_token_cookie",
        lambda response, access_token, db, request: cookies.update({"access_token": access_token}),
    )
    monkeypatch.setattr(auth_token, "set_active_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_token, "clear_legacy_refresh_cookie", lambda *args: None)

    payload = auth_token.get_access_token_by_refresh_token(
        SimpleNamespace(cookies={}),
        SimpleNamespace(),
        db=object(),
        db_log=object(),
    )

    assert payload["session_authenticated"] is True
    assert payload["language"] == "de"
    assert payload["terms_of_service_policy"] == {
        "revision": 1,
        "accepted_current_revision": True,
        "require_current_revision_for_access": False,
    }
    assert rotated == {
        "user_id": "user-1",
        "old_refresh": old_refresh,
        "new_access": "new-access",
        "new_refresh": "new-refresh",
        "session_id": "session-1",
    }
    assert cookies == {"slot": 2, "refresh_token": "new-refresh", "access_token": "new-access"}


def test_refresh_reads_terms_policy_before_rotating_tokens(monkeypatch):
    old_refresh = _refresh_jwt()
    rotated = {"called": False}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (old_refresh, 2))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: _current_resolution(),
    )
    monkeypatch.setattr(
        auth_token,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            is_active=True,
            lock={},
            account_type="regular",
            temporary_expires_at=None,
        ),
    )
    monkeypatch.setattr(auth_token, "create_access_token", lambda data, db: "new-access")
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda data, db: "new-refresh")
    monkeypatch.setattr(auth_token, "update_last_active_user", lambda db, user_id: None)
    monkeypatch.setattr(auth_token, "create_authentication_log", lambda *args: None)
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "ensure_session_satisfies_current_2fa_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "build_login_2fa_session_claims", lambda *args, **kwargs: {})
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)

    def fail_terms_policy(*_args, **_kwargs):
        raise RuntimeError("terms policy lookup failed")

    def fake_rotate(*_args, **_kwargs):
        rotated["called"] = True

    monkeypatch.setattr(auth_token, "get_terms_of_service_policy", fail_terms_policy)
    monkeypatch.setattr(auth_token, "rotate_authentication_tokens", fake_rotate)

    with pytest.raises(RuntimeError, match="terms policy lookup failed"):
        auth_token.get_access_token_by_refresh_token(
            SimpleNamespace(cookies={}),
            SimpleNamespace(),
            db=object(),
            db_log=object(),
        )

    assert rotated["called"] is False


def test_refresh_does_not_reconcile_slots_against_stale_request_cookies(monkeypatch):
    old_refresh = _refresh_jwt()
    cookies = {}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (old_refresh, 2))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: _current_resolution(),
    )
    monkeypatch.setattr(
        auth_token,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            is_active=True,
            lock={},
            account_type="regular",
            temporary_expires_at=None,
        ),
    )
    monkeypatch.setattr(auth_token, "create_access_token", lambda data, db: "new-access")
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda data, db: "new-refresh")
    monkeypatch.setattr(auth_token, "rotate_authentication_tokens", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "update_last_active_user", lambda db, user_id: None)
    monkeypatch.setattr(auth_token, "create_authentication_log", lambda *args: None)
    monkeypatch.setattr(auth_token, "ensure_user_runtime_auth_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "ensure_session_satisfies_current_2fa_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_token, "build_login_2fa_session_claims", lambda *args, **kwargs: {})
    monkeypatch.setattr(auth_token, "get_client_ip", lambda request, db: None)
    monkeypatch.setattr(auth_token, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(auth_token, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(
        auth_token,
        "set_refresh_slot_cookie",
        lambda response, slot, refresh_token, db, request: cookies.update(
            {"slot": slot, "refresh_token": refresh_token}
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "set_access_token_cookie",
        lambda response, access_token, db, request: cookies.update({"access_token": access_token}),
    )
    monkeypatch.setattr(auth_token, "set_active_slot_cookie", lambda *args: None)
    monkeypatch.setattr(auth_token, "clear_legacy_refresh_cookie", lambda *args: None)
    monkeypatch.setattr(
        auth_token,
        "ensure_active_slot_cookie",
        lambda *args: (_ for _ in ()).throw(AssertionError("stale slot reconciliation should not run after rotation")),
        raising=False,
    )

    payload = auth_token.get_access_token_by_refresh_token(
        SimpleNamespace(cookies={}),
        SimpleNamespace(),
        db=object(),
        db_log=object(),
    )

    assert payload["active_account_slot"] == 2
    assert cookies == {"slot": 2, "refresh_token": "new-refresh", "access_token": "new-access"}


def test_get_active_refresh_token_uses_reconciled_slot(monkeypatch):
    monkeypatch.setattr(account_slots, "ensure_active_slot_cookie", lambda request, response, db: 1)

    refresh_token, active_slot = account_slots.get_active_refresh_token(
        SimpleNamespace(
            cookies={
                account_slots.ACTIVE_SLOT_COOKIE: "2",
                account_slots.get_refresh_slot_cookie_name(1): "valid-slot-1",
                account_slots.get_refresh_slot_cookie_name(2): "stale-slot-2",
            }
        ),
        SimpleNamespace(),
        db=object(),
    )

    assert (refresh_token, active_slot) == ("valid-slot-1", 1)


def test_get_active_refresh_token_prefers_valid_active_slot_cookie_before_fallback(monkeypatch):
    stale_refresh = _refresh_jwt("user-1")
    valid_refresh = _refresh_jwt("user-2")
    deleted_refresh_rows = []
    auth_rows = {
        valid_refresh: SimpleNamespace(user_id="user-2", last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    }

    def fake_user(_db, user_id):
        return SimpleNamespace(
            id=user_id,
            email=f"{user_id}@example.com",
            first_name=user_id,
            last_name="",
            custom_profile_picture=False,
            deleted_at=None,
            is_active=True,
        )

    monkeypatch.setattr(account_slots, "get_jwt_material", lambda db=None: (SECRET, "HS512"))
    monkeypatch.setattr(
        account_slots,
        "get_authentication_by_token",
        lambda db, token, token_type: auth_rows.get(token),
    )
    monkeypatch.setattr(account_slots, "get_user", fake_user)
    monkeypatch.setattr(account_slots, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(
        account_slots,
        "delete_authentication",
        lambda db, refresh_token=None, **kwargs: deleted_refresh_rows.append(refresh_token),
    )

    request = _request_with_cookies(
        "omlorix_active_slot=1; "
        f"omlorix_refresh_slot_1={stale_refresh}; "
        f"omlorix_refresh_slot_2={valid_refresh}"
    )
    response = Response()

    refresh_token, active_slot = account_slots.get_active_refresh_token(request, response, db=object())

    assert (refresh_token, active_slot) == (stale_refresh, 1)
    assert deleted_refresh_rows == []
    assert _set_cookie_headers(response) == []


def test_get_active_refresh_token_skips_expired_active_slot_and_falls_back(monkeypatch):
    expired_refresh = _refresh_jwt("user-1", expires_delta=timedelta(minutes=-5))
    valid_refresh = _refresh_jwt("user-2")
    deleted_refresh_rows = []
    auth_rows = {
        expired_refresh: SimpleNamespace(user_id="user-1", last_active_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        valid_refresh: SimpleNamespace(user_id="user-2", last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    }

    def fake_user(_db, user_id):
        return SimpleNamespace(
            id=user_id,
            email=f"{user_id}@example.com",
            first_name=user_id,
            last_name="",
            custom_profile_picture=False,
            deleted_at=None,
            is_active=True,
        )

    monkeypatch.setattr(account_slots, "get_jwt_material", lambda db=None: (SECRET, "HS512"))
    monkeypatch.setattr(
        account_slots,
        "get_authentication_by_token",
        lambda db, token, token_type: auth_rows.get(token),
    )
    monkeypatch.setattr(account_slots, "get_user", fake_user)
    monkeypatch.setattr(account_slots, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(
        account_slots,
        "delete_authentication",
        lambda db, refresh_token=None, **kwargs: deleted_refresh_rows.append(refresh_token),
    )

    request = _request_with_cookies(
        "omlorix_active_slot=1; "
        f"omlorix_refresh_slot_1={expired_refresh}; "
        f"omlorix_refresh_slot_2={valid_refresh}"
    )
    response = Response()

    refresh_token, active_slot = account_slots.get_active_refresh_token(request, response, db=object())

    assert (refresh_token, active_slot) == (valid_refresh, 2)
    assert deleted_refresh_rows == [expired_refresh]
    set_cookie_headers = _set_cookie_headers(response)
    assert any(header.startswith('omlorix_refresh_slot_1=""') for header in set_cookie_headers)
    assert any(header.startswith("omlorix_active_slot=2") for header in set_cookie_headers)


def test_refresh_expired_selected_token_deletes_auth_row_and_clears_slot(monkeypatch):
    expired_refresh = _refresh_jwt(expires_delta=timedelta(minutes=-5))
    deleted_refresh_rows = []

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (expired_refresh, 2))
    monkeypatch.setattr(
        auth_token,
        "delete_authentication",
        lambda db, refresh_token=None, **kwargs: deleted_refresh_rows.append(refresh_token),
    )
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)

    response = auth_token.get_access_token_by_refresh_token(
        SimpleNamespace(
            cookies={
                account_slots.ACTIVE_SLOT_COOKIE: "2",
                account_slots.get_refresh_slot_cookie_name(2): expired_refresh,
            },
            url=SimpleNamespace(scheme="https"),
        ),
        SimpleNamespace(),
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 401
    assert json.loads(response.body) == {"detail": "Refresh token has expired"}
    assert deleted_refresh_rows == [expired_refresh]
    set_cookie_headers = _set_cookie_headers(response)
    assert any(header.startswith('omlorix_refresh_slot_2=""') for header in set_cookie_headers)
    assert any(header.startswith('omlorix_active_slot=""') for header in set_cookie_headers)


def test_confirmed_refresh_reuse_revokes_only_affected_session_and_logs(monkeypatch):
    old_refresh = _refresh_jwt()
    deleted = []
    logged = {}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (old_refresh, 1))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(
            state="reused",
            authentication=SimpleNamespace(id="compromised-session"),
            rotated_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "delete_authentication",
        lambda db, **kwargs: deleted.append(kwargs),
    )
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)

    def fake_log(db_log, event, severity, message, user_id, device_info, ip_address):
        logged.update({"event": event, "severity": severity, "user_id": user_id})

    monkeypatch.setattr(auth_token, "create_authentication_log", fake_log)

    response = auth_token.get_access_token_by_refresh_token(
        SimpleNamespace(cookies={account_slots.ACTIVE_SLOT_COOKIE: "1"}, url=SimpleNamespace(scheme="https")),
        SimpleNamespace(),
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 401
    assert json.loads(response.body) == {"detail": "Refresh token is no longer valid (revoked)"}
    assert deleted == [{"id": "compromised-session", "user_id": "user-1"}]
    assert logged == {"event": "refresh_reuse_detected", "severity": "warning", "user_id": "user-1"}
    assert any(header.startswith('omlorix_refresh_slot_1=""') for header in _set_cookie_headers(response))


def test_unknown_stale_refresh_token_does_not_revoke_any_session(monkeypatch):
    stale_refresh = _refresh_jwt("user-1")
    valid_refresh = _refresh_jwt("user-2")
    logged = {}
    deleted_refresh_rows = []
    auth_rows = {
        valid_refresh: SimpleNamespace(user_id="user-2", last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    }

    def fake_user(_db, user_id):
        return SimpleNamespace(
            id=user_id,
            email=f"{user_id}@example.com",
            first_name=user_id,
            last_name="",
            role="user",
            custom_profile_picture=False,
            deleted_at=None,
            is_active=True,
        )

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(account_slots, "get_jwt_material", lambda db=None: (SECRET, "HS512"))
    monkeypatch.setattr(
        account_slots,
        "get_authentication_by_token",
        lambda db, token, token_type: auth_rows.get(token),
    )
    monkeypatch.setattr(account_slots, "get_user", fake_user)
    monkeypatch.setattr(account_slots, "get_user_setting_value", lambda *args: False)
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(
        account_slots,
        "delete_authentication",
        lambda db, refresh_token=None, **kwargs: deleted_refresh_rows.append(refresh_token),
    )
    monkeypatch.setattr(auth_token, "get_active_refresh_token", account_slots.get_active_refresh_token)
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(state="unknown", authentication=None, rotated_at=None),
    )
    monkeypatch.setattr(
        auth_token,
        "delete_authentication",
        lambda *args, **kwargs: pytest.fail("unknown token must not delete a server-side session"),
    )
    monkeypatch.setattr(
        auth_token,
        "create_authentication_log",
        lambda db_log, event, severity, message, user_id, device_info, ip_address: logged.update(
            {"event": event, "severity": severity, "user_id": user_id}
        ),
    )
    monkeypatch.setattr(auth_token, "get_value_by_page_and_key", _settings)

    request = _request_with_cookies(
        "omlorix_active_slot=1; "
        f"omlorix_refresh_slot_1={stale_refresh}; "
        f"omlorix_refresh_slot_2={valid_refresh}"
    )
    response = Response()

    refresh_response = auth_token.get_access_token_by_refresh_token(
        request,
        response,
        db=object(),
        db_log=object(),
    )

    assert refresh_response.status_code == 401
    assert json.loads(refresh_response.body) == {"detail": "Refresh token is no longer valid (revoked)"}
    assert deleted_refresh_rows == []
    assert logged == {"event": "refresh_unknown_token", "severity": "warning", "user_id": "user-1"}
    set_cookie_headers = _set_cookie_headers(refresh_response)
    assert any(header.startswith('omlorix_refresh_slot_1=""') for header in set_cookie_headers)
    assert any(header.startswith('omlorix_active_slot=""') for header in set_cookie_headers)


def test_concurrent_refresh_race_returns_retry_without_mutating_cookies_or_sessions(monkeypatch):
    old_refresh = _refresh_jwt()
    logged = {}

    monkeypatch.setattr(auth_token, "_get_jwt_material", lambda: (SECRET, "HS512"))
    monkeypatch.setattr(auth_token, "get_active_refresh_token", lambda request, response, db: (old_refresh, 1))
    monkeypatch.setattr(
        auth_token,
        "resolve_refresh_token_for_rotation",
        lambda *args, **kwargs: SimpleNamespace(
            state="race",
            authentication=SimpleNamespace(id="session-1"),
            rotated_at=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        auth_token,
        "delete_authentication",
        lambda *args, **kwargs: pytest.fail("a refresh race must not delete a session"),
    )
    monkeypatch.setattr(
        auth_token,
        "create_authentication_log",
        lambda db_log, event, severity, message, user_id, device_info, ip_address: logged.update(
            {"event": event, "severity": severity, "user_id": user_id}
        ),
    )

    response = auth_token.get_access_token_by_refresh_token(
        SimpleNamespace(cookies={}, headers={}, url=SimpleNamespace(scheme="https")),
        SimpleNamespace(),
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "detail": {"type": "refresh_race", "retry_after_ms": 250}
    }
    assert _set_cookie_headers(response) == []
    assert logged == {"event": "refresh_race_detected", "severity": "info", "user_id": "user-1"}
