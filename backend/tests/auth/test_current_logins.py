import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import account_slots
from app.auth.models import Authentication, _token_hash
from app.auth.utils import delete_login, list_current_logins
from app.database import Base
from app.users.models import User


def _session_with_authentication_table():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, Authentication.__table__])
    session_factory = sessionmaker(bind=engine)
    return session_factory()


def _request_with_cookies(cookie_header: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "DELETE",
            "scheme": "https",
            "server": ("chat.example", 443),
            "path": "/api/v1/auth/login",
            "headers": [(b"cookie", cookie_header.encode("utf-8"))],
        }
    )


def _settings(page, key, _db):
    values = {
        ("general", "public_url"): "https://chat.example",
        ("security", "refresh_cookie_secure"): True,
        ("security", "refresh_cookie_samesite"): "lax",
        ("security", "refresh_token_expire_minutes"): 60,
    }
    return values.get((page, key))


def _set_cookie_headers(response: Response) -> list[str]:
    return response.headers.getlist("set-cookie")


def _assert_deleted_cookie(headers: list[str], name: str) -> None:
    assert any(
        header.lower().startswith(f"{name.lower()}=") and "max-age=0" in header.lower()
        for header in headers
    )


def _assert_set_cookie(headers: list[str], name: str, value: str) -> None:
    assert any(
        header.startswith(f"{name}={value};") and "Max-Age=0" not in header
        for header in headers
    )


def _insert_authentication_with_invalid_ciphertext(
    db,
    *,
    auth_id: str,
    user_id: str,
    access_token: str,
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.execute(
        text(
            """
            INSERT INTO authentication (
                id,
                user_id,
                device_info,
                ip_address,
                access_token,
                refresh_token,
                access_token_hash,
                refresh_token_hash,
                created_at,
                last_active_at
            )
            VALUES (
                :id,
                :user_id,
                :device_info,
                :ip_address,
                :access_token,
                :refresh_token,
                :access_token_hash,
                :refresh_token_hash,
                :created_at,
                :last_active_at
            )
            """
        ),
        {
            "id": auth_id,
            "user_id": user_id,
            "device_info": "Desktop browser",
            "ip_address": "203.0.113.10",
            "access_token": "not-valid-encrypted-data",
            "refresh_token": "also-not-valid-encrypted-data",
            "access_token_hash": _token_hash(access_token),
            "refresh_token_hash": _token_hash("refresh-token"),
            "created_at": now,
            "last_active_at": now,
        },
    )
    db.commit()


def test_current_logins_do_not_decrypt_stored_tokens():
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )

        logins = list_current_logins("user-1", db, token="access-token")

        assert logins == [
            {
                "id": "auth-1",
                "device_info": "Unknown Device",
                "ip_address": "203.0.113.0/24",
                "last_active_at": logins[0]["last_active_at"],
                "current": True,
            }
        ]
        assert logins[0]["last_active_at"] is not None
    finally:
        db.close()


def test_account_slot_audit_owner_lookup_does_not_decrypt_stored_tokens():
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )
        request = _request_with_cookies(
            f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-token"
        )

        assert account_slots._slot_user_id_from_cookie(request, db, 1) == "user-1"
    finally:
        db.close()


def test_deleting_a_login_does_not_decrypt_stored_tokens():
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )

        assert delete_login(
            "user-1",
            db,
            token="access-token",
            auth_id="auth-1",
        ) == []
        assert db.query(Authentication.id).count() == 0

        assert delete_login(
            "user-1",
            db,
            token="access-token",
            auth_id="auth-1",
        ) == []
        assert db.query(Authentication.id).count() == 0
    finally:
        db.close()


def test_login_revocation_rolls_back_when_audit_intent_cannot_be_staged():
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            delete_login(
                "user-1",
                db,
                token="access-token",
                auth_id="auth-1",
                before_commit=lambda _rows: (_ for _ in ()).throw(
                    RuntimeError("audit unavailable")
                ),
            )

        assert (
            db.query(Authentication.id)
            .filter(Authentication.id == "auth-1")
            .scalar()
            == "auth-1"
        )
    finally:
        db.close()


def test_deleting_all_logins_clears_matching_browser_cookies(monkeypatch):
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )
        response = Response()
        request = _request_with_cookies(
            "; ".join(
                [
                    f"{account_slots.ACTIVE_SLOT_COOKIE}=1",
                    f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-token",
                    f"{account_slots.LEGACY_REFRESH_COOKIE}=refresh-token",
                ]
            )
        )

        assert delete_login(
            "user-1",
            db,
            token="access-token",
            request=request,
            response=response,
        ) == {"status": "success"}
        assert db.query(Authentication.id).count() == 0

        set_cookies = _set_cookie_headers(response)
        _assert_deleted_cookie(set_cookies, account_slots.get_refresh_slot_cookie_name(1))
        _assert_deleted_cookie(set_cookies, account_slots.ACTIVE_SLOT_COOKIE)
        _assert_deleted_cookie(set_cookies, account_slots.LEGACY_REFRESH_COOKIE)

        assert delete_login(
            "user-1",
            db,
            token="access-token",
        ) == {"status": "success"}
        assert db.query(Authentication.id).count() == 0
    finally:
        db.close()


def test_deleting_current_login_clears_slot_and_falls_back_to_remaining_account(monkeypatch):
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _settings)
    monkeypatch.setattr(account_slots, "get_user_setting_value", lambda *args, **kwargs: False)
    db = _session_with_authentication_table()
    try:
        _insert_authentication_with_invalid_ciphertext(
            db,
            auth_id="auth-1",
            user_id="user-1",
            access_token="access-token",
        )

        def fake_get_authentication_by_token(_db, refresh_token, token_type):
            if token_type == "refresh_token" and refresh_token == "other-refresh":
                return SimpleNamespace(
                    user_id="user-2",
                    last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                )
            return None

        def fake_get_user(_db, user_id):
            if user_id != "user-2":
                raise AssertionError(f"Unexpected user lookup: {user_id}")
            return SimpleNamespace(
                id="user-2",
                email="other@example.com",
                first_name="Other",
                last_name="User",
                is_active=True,
                deleted_at=None,
                custom_profile_picture=False,
            )

        monkeypatch.setattr(account_slots, "get_authentication_by_token", fake_get_authentication_by_token)
        monkeypatch.setattr(account_slots, "get_user", fake_get_user)
        monkeypatch.setattr(
            account_slots,
            "_decode_refresh_slot_token",
            lambda refresh_token, _db: {"type": "refresh", "sub": "user-2", "exp": 1}
            if refresh_token == "other-refresh"
            else None,
        )

        response = Response()
        request = _request_with_cookies(
            "; ".join(
                [
                    f"{account_slots.ACTIVE_SLOT_COOKIE}=1",
                    f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-token",
                    f"{account_slots.get_refresh_slot_cookie_name(2)}=other-refresh",
                ]
            )
        )

        assert delete_login(
            "user-1",
            db,
            token="access-token",
            auth_id="auth-1",
            request=request,
            response=response,
        ) == []

        set_cookies = _set_cookie_headers(response)
        _assert_deleted_cookie(set_cookies, account_slots.get_refresh_slot_cookie_name(1))
        _assert_set_cookie(set_cookies, account_slots.ACTIVE_SLOT_COOKIE, "2")
    finally:
        db.close()
