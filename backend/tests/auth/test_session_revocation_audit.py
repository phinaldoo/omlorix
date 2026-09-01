from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.auth import account_slots
from app.auth import router as auth_router
from app.auth import session_audit
from app.auth import utils as auth_utils
from app.auth.schemas import DeleteSpecificLoginRequest


def _request(
    *,
    method: str = "DELETE",
    path: str = "/api/v1/auth/login",
    cookies: str | None = None,
) -> Request:
    headers = [(b"user-agent", b"Omlorix-Test/1.0")]
    if cookies:
        headers.append((b"cookie", cookies.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "client": ("203.0.113.10", 12345),
            "path": path,
            "headers": headers,
        }
    )


def _authentication(
    auth_id: str,
    *,
    access_token: str,
    device_info: str,
    ip_address: str,
    last_active_at: datetime,
):
    return SimpleNamespace(
        id=auth_id,
        device_info=device_info,
        ip_address=ip_address,
        last_active_at=last_active_at,
        access_token_hash=auth_utils._hash_token_value(access_token),
    )


def _capture_route_audits(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_router, "get_audit_request_ip", lambda *_args: "203.0.113.10")
    monkeypatch.setattr(
        auth_router,
        "stage_audit_log_event",
        lambda _db, **kwargs: calls.append(kwargs),
    )
    return calls


def _successful_delete_result(result, deleted_rows):
    def delete_login(
        _user_id,
        _db,
        _token,
        _auth_id,
        _request,
        _response,
        *,
        before_commit,
    ):
        before_commit(deleted_rows)
        return result

    return delete_login


def test_delete_login_route_audits_single_success_with_safe_metadata(monkeypatch):
    target = _authentication(
        "auth-2",
        access_token="access-token",
        device_info="Firefox on macOS",
        ip_address="198.51.100.0/24",
        last_active_at=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
    )
    audit_calls = _capture_route_audits(monkeypatch)
    monkeypatch.setattr(
        auth_router,
        "delete_login",
        _successful_delete_result(
            {"status": "success", "auth_id": "auth-2"},
            [target],
        ),
    )

    result = auth_router.delete_login_route(
        request=_request(),
        response=Response(),
        payload=DeleteSpecificLoginRequest(auth_id="auth-2"),
        db=object(),
        user=SimpleNamespace(id="user-1"),
        token="access-token",
    )

    assert result == {"status": "success", "auth_id": "auth-2"}
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "LOGIN_SESSIONS_REVOKED"
    assert audit_calls[0]["category"] == "auth_security"
    assert audit_calls[0]["details"] == {
        "revocation_scope": "single_session",
        "target_count": 1,
        "current_login_revoked": True,
        "requested_login_id": "auth-2",
        "target_login": {
            "login_id": "auth-2",
            "login_fingerprint": session_audit.login_session_audit_fingerprint(target),
            "device_info": "Firefox on macOS",
            "ip_address": "198.51.100.0/24",
            "last_active_at": "2026-05-17T10:00:00+00:00",
            "current": True,
        },
    }
    serialized_details = repr(audit_calls[0]["details"])
    assert "access-token" not in serialized_details
    assert target.access_token_hash not in serialized_details

    def concurrent_noop(
        *_args,
        before_commit,
        **_kwargs,
    ):
        return {"status": "success", "auth_id": "auth-2"}

    monkeypatch.setattr(auth_router, "delete_login", concurrent_noop)
    auth_router.delete_login_route(
        request=_request(),
        response=Response(),
        payload=DeleteSpecificLoginRequest(auth_id="auth-2"),
        db=object(),
        user=SimpleNamespace(id="user-1"),
        token="access-token",
    )
    assert len(audit_calls) == 1


def test_delete_login_route_audits_all_success_and_skips_empty_noop(monkeypatch):
    authentications = [
        _authentication(
            "auth-1",
            access_token="access-token",
            device_info="Safari on iPhone",
            ip_address="198.51.100.0/24",
            last_active_at=datetime(2026, 5, 16, 8, 30, tzinfo=timezone.utc),
        ),
        _authentication(
            "auth-2",
            access_token="other-token",
            device_info="Firefox on macOS",
            ip_address="198.51.100.0/24",
            last_active_at=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    audit_calls = _capture_route_audits(monkeypatch)
    monkeypatch.setattr(
        auth_router,
        "delete_login",
        _successful_delete_result({"status": "success"}, authentications),
    )

    result = auth_router.delete_login_route(
        request=_request(),
        response=Response(),
        payload=None,
        db=object(),
        user=SimpleNamespace(id="user-1"),
        token="access-token",
    )

    assert result == {"status": "success"}
    assert audit_calls[0]["details"] == {
        "revocation_scope": "all_sessions",
        "target_count": 2,
        "current_login_revoked": True,
    }

    monkeypatch.setattr(
        auth_router,
        "delete_login",
        lambda *_args, before_commit, **_kwargs: {"status": "success"},
    )
    auth_router.delete_login_route(
        request=_request(),
        response=Response(),
        payload=None,
        db=object(),
        user=SimpleNamespace(id="user-1"),
        token="access-token",
    )

    assert len(audit_calls) == 1


def test_revocation_fails_before_commit_when_audit_intent_cannot_be_staged(monkeypatch):
    target = _authentication(
        "auth-1",
        access_token="access-token",
        device_info="Firefox",
        ip_address="198.51.100.0/24",
        last_active_at=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        auth_router,
        "delete_login",
        _successful_delete_result({"status": "success"}, [target]),
    )
    monkeypatch.setattr(
        auth_router,
        "stage_audit_log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        auth_router.delete_login_route(
            request=_request(),
            response=Response(),
            payload=None,
            db=object(),
            user=SimpleNamespace(id="user-1"),
            token="access-token",
        )


def _slot(slot: int, user_id: str, refresh_token: str) -> account_slots.BrowserAccountSlot:
    return account_slots.BrowserAccountSlot(
        slot=slot,
        user_id=user_id,
        refresh_token=refresh_token,
        display_name="User",
        has_custom_profile_picture=False,
        has_profile_picture=False,
        last_active_at=None,
    )


def _patch_slot_side_effects(monkeypatch):
    monkeypatch.setattr(account_slots, "clear_refresh_slot_cookie", lambda *_args: None)
    monkeypatch.setattr(account_slots, "clear_access_token_cookie", lambda *_args: None)
    monkeypatch.setattr(account_slots, "clear_active_slot_cookie", lambda *_args: None)
    monkeypatch.setattr(account_slots, "set_active_slot_cookie", lambda *_args: None)
    monkeypatch.setattr(account_slots, "list_accounts_payload", lambda *_args, **_kwargs: {"active_slot": None})
    monkeypatch.setattr(account_slots, "get_audit_request_ip", lambda *_args: "203.0.113.10")


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_account_slot_deletion_audits_only_a_deleted_persisted_login(monkeypatch):
    _patch_slot_side_effects(monkeypatch)
    audit_calls = []
    deleted = {"value": True}
    request = _request(
        path="/api/v1/auth/accounts/1",
        cookies=(
            f"{account_slots.ACTIVE_SLOT_COOKIE}=1; "
            f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-one"
        ),
    )

    monkeypatch.setattr(
        account_slots,
        "get_authentication_user_id_by_token",
        lambda _db, refresh, _kind: "user-1" if refresh == "refresh-one" else None,
    )
    def delete_authentication(*_args, before_commit, **_kwargs):
        if not deleted["value"]:
            return False
        before_commit([SimpleNamespace(id="auth-1")])
        return True

    monkeypatch.setattr(account_slots, "delete_authentication", delete_authentication)
    monkeypatch.setattr(account_slots, "list_browser_accounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        account_slots,
        "stage_audit_log_event",
        lambda _db, **kwargs: audit_calls.append(kwargs),
    )

    account_slots.delete_account_slot(request, Response(), _FakeDb(), 1)

    assert audit_calls[0]["action"] == "ACCOUNT_SLOT_DELETED"
    assert audit_calls[0]["user_id"] == "user-1"
    assert audit_calls[0]["details"] == {
        "slot": 1,
        "removed_user_id": "user-1",
        "was_active": True,
        "fallback_slot": None,
    }
    assert "refresh-one" not in repr(audit_calls[0])

    deleted["value"] = False
    account_slots.delete_account_slot(request, Response(), _FakeDb(), 1)
    assert len(audit_calls) == 1


def test_account_slot_deletion_fails_before_cookie_cleanup_when_audit_cannot_be_staged(
    monkeypatch,
):
    _patch_slot_side_effects(monkeypatch)
    cookie_cleanup = []
    request = _request(
        path="/api/v1/auth/accounts/1",
        cookies=f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-one",
    )
    monkeypatch.setattr(
        account_slots,
        "get_authentication_user_id_by_token",
        lambda *_args: "user-1",
    )
    monkeypatch.setattr(account_slots, "list_browser_accounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        account_slots,
        "delete_authentication",
        lambda *_args, before_commit, **_kwargs: (
            before_commit([SimpleNamespace(id="auth-1")]) or True
        ),
    )
    monkeypatch.setattr(
        account_slots,
        "clear_refresh_slot_cookie",
        lambda *_args: cookie_cleanup.append("cleared"),
    )
    monkeypatch.setattr(
        account_slots,
        "stage_audit_log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        account_slots.delete_account_slot(request, Response(), _FakeDb(), 1)

    assert cookie_cleanup == []


def test_account_switch_audits_only_when_the_active_identity_changes(monkeypatch):
    _patch_slot_side_effects(monkeypatch)
    audit_calls = []
    target_user = {"id": "user-2"}
    request = _request(
        method="POST",
        path="/api/v1/auth/accounts/switch",
        cookies=(
            f"{account_slots.ACTIVE_SLOT_COOKIE}=1; "
            f"{account_slots.get_refresh_slot_cookie_name(1)}=refresh-one; "
            f"{account_slots.get_refresh_slot_cookie_name(2)}=refresh-two"
        ),
    )

    monkeypatch.setattr(
        account_slots,
        "get_authentication_user_id_by_token",
        lambda _db, refresh, _kind: (
            "user-1" if refresh == "refresh-one" else target_user["id"]
        ),
    )
    monkeypatch.setattr(
        account_slots,
        "_resolve_slot_from_refresh_token",
        lambda slot, refresh, _db: _slot(slot, target_user["id"], refresh),
    )
    monkeypatch.setattr(
        account_slots,
        "stage_audit_log_event",
        lambda _db, **kwargs: audit_calls.append(kwargs),
    )
    db = _FakeDb()

    account_slots.switch_active_account_slot(request, Response(), db, 2)

    assert audit_calls[0]["action"] == "ACTIVE_ACCOUNT_SWITCHED"
    assert audit_calls[0]["user_id"] == "user-1"
    assert audit_calls[0]["details"] == {
        "from_user_id": "user-1",
        "to_user_id": "user-2",
        "from_slot": 1,
        "to_slot": 2,
        "identity_changed": True,
    }
    assert db.commits == 1
    assert db.rollbacks == 0
    assert "refresh-one" not in repr(audit_calls[0])
    assert "refresh-two" not in repr(audit_calls[0])

    target_user["id"] = "user-1"
    account_slots.switch_active_account_slot(request, Response(), db, 2)
    assert len(audit_calls) == 1
    assert db.commits == 1


def _login_user():
    return SimpleNamespace(
        id="user-new",
        email="new@example.com",
        hashed_password="hashed-password",
        role="user",
        deleted_at=None,
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        group_id=None,
    )


def test_signin_audits_successful_occupied_slot_replacement(monkeypatch):
    from app.auth import token as auth_token
    from app.email import devices as email_devices

    assignment = account_slots.SlotAssignment(
        slot=2,
        replaced_refresh_token="old-refresh",
        replaced_user_id="user-old",
        replacement_reason="requested_slot",
    )
    audit_calls = []

    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda *_args: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "_enforce_session_auth_authority", lambda **_kwargs: None)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda *_args: None)
    monkeypatch.setattr(auth_utils, "resolve_slot_assignment", lambda *_args, **_kwargs: assignment)
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda **_kwargs: "new-refresh")
    monkeypatch.setattr(auth_token, "create_access_token", lambda **_kwargs: "new-access")
    monkeypatch.setattr(auth_utils, "_lock_user_for_session_issuance", lambda _db, user: user)
    monkeypatch.setattr(auth_utils, "_current_user_row_login_eligibility", lambda _user: None)
    create_calls = []

    def create_authentication(*_args, **kwargs):
        create_calls.append(kwargs)
        return object()

    monkeypatch.setattr(auth_utils, "create_authentication", create_authentication)

    def delete_authentication(*_args, before_commit, **kwargs):
        assert kwargs["commit"] is False
        before_commit(
            [
                SimpleNamespace(
                    user_id="user-old",
                    access_token_hash="old-access-hash",
                    refresh_token_hash="old-refresh-hash",
                )
            ]
        )
        return True

    monkeypatch.setattr(auth_utils, "delete_authentication", delete_authentication)
    monkeypatch.setattr(
        account_slots,
        "set_refresh_slot_cookie",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        account_slots,
        "set_active_slot_cookie",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "stage_audit_log_event",
        lambda _db, **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(auth_utils, "record_auth_login_attempt_metric", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "set_access_token_cookie",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auth_utils,
        "resolve_auth_cookie_settings",
        lambda *_args: object(),
    )
    monkeypatch.setattr(auth_utils, "cache_session", lambda *_args: None)
    monkeypatch.setattr(auth_utils, "revoke_token_digests", lambda **_kwargs: None)
    monkeypatch.setattr(email_devices, "register_login_device", lambda *_args, **_kwargs: None)

    class FakeDb:
        def add(self, _record):
            pass

        def commit(self):
            pass

    result = auth_utils._issue_authenticated_session(
        db=FakeDb(),
        db_log=object(),
        request=_request(method="POST", path="/api/v1/auth/signin"),
        response=Response(),
        user=_login_user(),
        log_event="signin",
        success_message="Signed in",
        replace_slot=2,
    )

    assert result == {"session_authenticated": True, "active_account_slot": 2}
    assert [call["action"] for call in audit_calls] == [
        "ACCOUNT_SLOT_REPLACED",
        "LOGIN_SUCCEEDED",
    ]
    assert create_calls[0]["commit"] is False
    assert audit_calls[0]["details"] == {
        "slot": 2,
        "replaced_user_id": "user-old",
        "replacement_reason": "requested_slot",
        "same_account": False,
    }
    assert audit_calls[1]["details"] == {
        "login_method": "password",
        "account_slot": 2,
        "replaced_account_slot": True,
    }
    assert "old-refresh" not in repr(audit_calls[0])
    assert "new-refresh" not in repr(audit_calls[0])
    assert "new-access" not in repr(audit_calls[0])


@pytest.mark.parametrize("failure_mode", ["audit", "stale_slot", "cookie_policy"])
def test_signin_slot_replacement_failure_rolls_back_before_cache_or_cookies(
    monkeypatch,
    failure_mode,
):
    """Stale assignment or audit failure cannot publish a replacement."""
    from app.auth import token as auth_token

    assignment = account_slots.SlotAssignment(
        slot=2,
        replaced_refresh_token="old-refresh",
        replaced_user_id="user-old",
        replacement_reason="requested_slot",
    )
    cache_calls = []
    revoke_calls = []
    create_calls = []

    class FakeDb:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def add(self, _record):
            pass

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    db = FakeDb()
    response = Response()
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda *_args: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "_enforce_session_auth_authority", lambda **_kwargs: None)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda *_args: None)
    monkeypatch.setattr(auth_utils, "resolve_slot_assignment", lambda *_args, **_kwargs: assignment)
    monkeypatch.setattr(auth_token, "create_refresh_token", lambda **_kwargs: "new-refresh")
    monkeypatch.setattr(auth_token, "create_access_token", lambda **_kwargs: "new-access")
    monkeypatch.setattr(auth_utils, "_lock_user_for_session_issuance", lambda _db, user: user)
    monkeypatch.setattr(auth_utils, "_current_user_row_login_eligibility", lambda _user: None)
    monkeypatch.setattr(
        auth_utils,
        "create_authentication",
        lambda *_args, **_kwargs: create_calls.append(True) or object(),
    )

    def resolve_cookie_settings(*_args):
        if failure_mode == "cookie_policy":
            raise RuntimeError("cookie policy unavailable")
        return object()

    monkeypatch.setattr(
        auth_utils,
        "resolve_auth_cookie_settings",
        resolve_cookie_settings,
    )

    def delete_authentication(*_args, before_commit, **_kwargs):
        if failure_mode == "stale_slot":
            return False
        before_commit(
            [
                SimpleNamespace(
                    user_id="user-old",
                    access_token_hash="old-access-hash",
                    refresh_token_hash="old-refresh-hash",
                )
            ]
        )

    def fail_slot_audit(_db, **kwargs):
        if failure_mode == "audit" and kwargs["action"] == "ACCOUNT_SLOT_REPLACED":
            raise RuntimeError("audit outbox unavailable")

    monkeypatch.setattr(auth_utils, "delete_authentication", delete_authentication)
    monkeypatch.setattr(auth_utils, "stage_audit_log_event", fail_slot_audit)
    monkeypatch.setattr(auth_utils, "cache_session", lambda *args: cache_calls.append(args))
    monkeypatch.setattr(auth_utils, "revoke_token_digests", lambda **kwargs: revoke_calls.append(kwargs))

    expected_exception = HTTPException if failure_mode == "stale_slot" else RuntimeError
    with pytest.raises(expected_exception):
        auth_utils._issue_authenticated_session(
            db=db,
            db_log=object(),
            request=_request(method="POST", path="/api/v1/auth/signin"),
            response=response,
            user=_login_user(),
            log_event="signin",
            success_message="Signed in",
            replace_slot=2,
        )

    assert db.commits == 0
    assert db.rollbacks == 1
    assert cache_calls == []
    assert revoke_calls == []
    assert create_calls == ([] if failure_mode == "cookie_policy" else [True])
    assert response.headers.get("set-cookie") is None
