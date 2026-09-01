import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.auth import router as auth_router
from app.auth import utils as auth_utils
from app.auth.models import (
    NativeAuthGrant,
    PendingAuthAction,
    WebAuthnChallenge,
    delete_expired_pending_auth_actions,
    delete_user_transient_auth_state,
)
from app.database import Base
from app.email.models import EmailSecurityState
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users import init as user_settings
from app.users.models import User
from app.utils import encryption as encryption_utils


def _session(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            EmailSecurityState.__table__,
            PendingAuthAction.__table__,
            NativeAuthGrant.__table__,
            WebAuthnChallenge.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    db.add(
        User(
            id="indexed-action-user",
            email="action@example.com",
            group_id="group-1",
            hashed_password="password-hash",
            first_name="Action",
            last_name="User",
            role="user",
            settings=deepcopy(DEFAULT_USER_SETTINGS),
            is_active=True,
            created_at=now,
            last_active_at=now,
        )
    )
    db.commit()
    return db, engine


def test_pending_token_lookup_uses_index_without_scanning_users(monkeypatch):
    db, engine = _session(monkeypatch)
    token, _expires_at = auth_utils._set_pending_sso_token(
        "indexed-action-user",
        "oidc",
        db,
    )
    row = db.query(PendingAuthAction).one()
    assert row.purpose == "sso_login_token"
    assert row.token_hash != token

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        lookups = (
            auth_utils._find_user_by_sso_token,
            auth_utils._find_user_by_pending_sso_auth_code,
            auth_utils._find_user_by_pending_social_auth_code,
            auth_utils._find_user_by_pending_social_token,
            auth_utils._find_user_by_pending_signin_token,
            auth_utils._find_user_by_pending_passkey_token,
        )
        for lookup in lookups:
            statements.clear()
            assert lookup(db, "invalid-token") is None
            assert any(
                "pending_auth_actions" in statement for statement in statements
            )
            assert not any(" from users" in statement for statement in statements)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    db.close()


def test_pending_token_lookup_rechecks_one_users_encrypted_context(monkeypatch):
    db, _engine = _session(monkeypatch)
    token, _expires_at = auth_utils._set_pending_sso_token(
        "indexed-action-user",
        "oidc",
        db,
        allow_setup_material=True,
    )

    user = auth_utils._find_user_by_sso_token(db, token)

    assert user is not None
    assert user.id == "indexed-action-user"
    assert user.settings["sso_login"]["pending_setup_material_allowed"] is True
    db.rollback()
    db.close()


def test_replacing_pending_action_invalidates_the_previous_token(monkeypatch):
    db, _engine = _session(monkeypatch)
    first_token, _ = auth_utils._set_pending_passkey_token(
        "indexed-action-user",
        db,
    )
    second_token, _ = auth_utils._set_pending_passkey_token(
        "indexed-action-user",
        db,
    )

    assert db.query(PendingAuthAction).count() == 1
    assert (
        auth_utils._clear_pending_passkey_token(
            "indexed-action-user",
            db,
            raw_token=first_token,
        )
        is False
    )
    assert db.query(PendingAuthAction).count() == 1
    assert auth_utils._find_user_by_pending_passkey_token(db, first_token) is None
    assert auth_utils._find_user_by_pending_passkey_token(db, second_token).id == "indexed-action-user"
    db.rollback()
    db.close()


def test_expired_or_cleared_pending_action_cannot_resolve(monkeypatch):
    db, _engine = _session(monkeypatch)
    token, _ = auth_utils._set_pending_social_token(
        "indexed-action-user",
        "github",
        db,
    )
    row = db.query(PendingAuthAction).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert auth_utils._find_user_by_pending_social_token(db, token) is None

    replacement, _ = auth_utils._set_pending_social_token(
        "indexed-action-user",
        "github",
        db,
    )
    auth_utils._clear_pending_social_token("indexed-action-user", db)

    assert db.query(PendingAuthAction).count() == 0
    assert auth_utils._find_user_by_pending_social_token(db, replacement) is None
    db.close()


def test_pending_action_retention_is_bounded(monkeypatch):
    db, _engine = _session(monkeypatch)
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            PendingAuthAction(
                user_id="indexed-action-user",
                purpose=f"expired-{index}",
                token_hash=f"{index:064x}",
                created_at=now - timedelta(minutes=10),
                expires_at=now - timedelta(minutes=5),
            )
            for index in range(2)
        ]
    )
    db.commit()

    assert delete_expired_pending_auth_actions(db, now=now, batch_size=1) == 1
    assert db.query(PendingAuthAction).count() == 1
    assert delete_expired_pending_auth_actions(db, now=now, batch_size=1) == 1
    assert db.query(PendingAuthAction).count() == 0
    db.close()


def test_security_boundary_deletes_every_user_auth_continuation(monkeypatch):
    db, _engine = _session(monkeypatch)
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            PendingAuthAction(
                user_id="indexed-action-user",
                purpose="social_login_token",
                token_hash="a" * 64,
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            NativeAuthGrant(
                token_hash="b" * 64,
                purpose="social_exchange",
                provider="google",
                user_id="indexed-action-user",
                code_challenge="c" * 43,
                state_hash="d" * 64,
                account_mode="primary",
                accepts_terms_of_service=False,
                twofa_satisfied=True,
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            WebAuthnChallenge(
                user_id="indexed-action-user",
                flow="authentication",
                challenge="challenge",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
        ]
    )
    db.commit()

    assert delete_user_transient_auth_state(
        db,
        "indexed-action-user",
        commit=True,
    ) == 3
    assert db.query(PendingAuthAction).count() == 0
    assert db.query(NativeAuthGrant).count() == 0
    assert db.query(WebAuthnChallenge).count() == 0
    db.close()


def test_session_issuance_lock_refreshes_the_current_auth_authority(monkeypatch):
    db, engine = _session(monkeypatch)
    stale_user = db.query(User).filter(User.id == "indexed-action-user").one()
    assert stale_user.auth_management_mode == "local"

    concurrent = sessionmaker(bind=engine)()
    try:
        concurrent.query(User).filter(User.id == stale_user.id).update(
            {User.auth_management_mode: "external"},
            synchronize_session=False,
        )
        concurrent.commit()
    finally:
        concurrent.close()

    locked_user = auth_utils._lock_user_for_session_issuance(db, stale_user)

    assert locked_user is stale_user
    assert locked_user.auth_management_mode == "external"
    db.rollback()
    db.close()


def test_pending_action_issuance_rolls_back_if_context_write_fails(monkeypatch):
    db, _engine = _session(monkeypatch)
    monkeypatch.setattr(
        auth_utils,
        "update_user_settings_bulk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("settings write failed")
        ),
    )

    with pytest.raises(RuntimeError, match="settings write failed"):
        auth_utils._set_pending_sso_token(
            "indexed-action-user",
            "oidc",
            db,
        )

    assert db.query(PendingAuthAction).count() == 0
    db.close()


class _ExchangeRequest:
    cookies = {}

    async def json(self):
        return {"code": "exchange-token"}


class _ExchangeResponse:
    def __init__(self):
        self.deleted: list[str] = []

    def delete_cookie(self, name, *args, **kwargs):
        self.deleted.append(name)


def test_managed_account_cannot_redeem_a_stale_social_auth_code(monkeypatch):
    user = SimpleNamespace(id="user-1", auth_management_mode="external")
    response = _ExchangeResponse()
    consumed: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(
        auth_router,
        "_find_user_by_pending_social_auth_code",
        lambda *_args: user,
    )
    monkeypatch.setattr(
        auth_router,
        "_clear_social_auth_exchange_state",
        lambda user_id, _db, *, raw_token=None: consumed.append(
            (user_id, raw_token)
        )
        or True,
    )
    monkeypatch.setattr(
        auth_router,
        "_issue_authenticated_session",
        lambda **_kwargs: pytest.fail("managed accounts must not issue social sessions"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_router.exchange_social_auth_code(
                request=_ExchangeRequest(),
                response=response,
                db=object(),
                db_log=object(),
            )
        )

    assert exc_info.value.status_code == 403
    assert consumed == [("user-1", "exchange-token")]
    assert "social_auth_code" in response.deleted


def test_managed_account_cannot_redeem_a_stale_passkey_continuation(monkeypatch):
    user = SimpleNamespace(id="user-1", auth_management_mode="external")
    cleared: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(
        auth_router,
        "_find_user_by_pending_passkey_token",
        lambda *_args: user,
    )
    monkeypatch.setattr(
        auth_router,
        "_clear_pending_passkey_token",
        lambda user_id, _db, *, raw_token=None: cleared.append(
            (user_id, raw_token)
        )
        or True,
    )
    monkeypatch.setattr(
        auth_router,
        "_clear_one_time_browser_cookie",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_router.passkey_complete_authentication(
            request=SimpleNamespace(cookies={}, headers={}),
            response=object(),
            payload=SimpleNamespace(passkey_token="passkey-token"),
            db=object(),
            db_log=object(),
        )

    assert exc_info.value.status_code == 403
    assert cleared == [("user-1", "passkey-token")]


def test_managed_account_cannot_redeem_a_stale_social_twofa_continuation(monkeypatch):
    user = SimpleNamespace(id="user-1", auth_management_mode="external")
    cleared: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda *_args: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_pending_social_token",
        lambda *_args: user,
    )
    monkeypatch.setattr(
        auth_utils,
        "_clear_pending_social_token",
        lambda user_id, _db, *, raw_token=None: cleared.append(
            (user_id, raw_token)
        )
        or True,
    )
    monkeypatch.setattr(
        auth_utils,
        "_clear_one_time_browser_cookie",
        lambda *_args, **_kwargs: None,
    )

    result = asyncio.run(
        auth_utils.complete_social_login_with_2fa(
            provider="google",
            social_token="social-token",
            otp_code=None,
            otp_type=None,
            otp_action=None,
            otp_destination=None,
            request=SimpleNamespace(headers={}, cookies={}),
            response=object(),
            db=object(),
            db_log=object(),
        )
    )

    assert result == {
        "status": "error",
        "detail": "Invalid or expired social login token",
    }
    assert cleared == [("user-1", "social-token")]


@pytest.mark.parametrize(
    ("exchange", "finder_name", "clearer_name"),
    (
        (
            auth_router.exchange_social_auth_code,
            "_find_user_by_pending_social_auth_code",
            "_clear_social_auth_exchange_state",
        ),
        (
            auth_router.exchange_sso_auth_code,
            "_find_user_by_pending_sso_auth_code",
            "_clear_sso_auth_exchange_state",
        ),
    ),
)
def test_auth_code_exchange_consumes_before_issuing_session(
    monkeypatch,
    exchange,
    finder_name,
    clearer_name,
):
    order: list[str] = []
    user = SimpleNamespace(id="user-1")
    response = _ExchangeResponse()
    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(auth_router, finder_name, lambda *_args: user)
    monkeypatch.setattr(
        user_settings,
        "get_user_setting_value",
        lambda *_args, **_kwargs: (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
    )
    monkeypatch.setattr(
        auth_router,
        "read_flow_context_cookie",
        lambda *_args, **_kwargs: {"account_mode": "primary", "replace_slot": None},
    )
    monkeypatch.setattr(
        auth_router,
        clearer_name,
        lambda *_args, **_kwargs: order.append("consume") or True,
    )
    monkeypatch.setattr(
        auth_router,
        "_issue_authenticated_session",
        lambda **_kwargs: order.append("issue") or {"status": "success"},
    )
    monkeypatch.setattr(
        auth_router,
        "clear_flow_context_cookie",
        lambda *_args, **_kwargs: None,
    )

    result = asyncio.run(
        exchange(
            request=_ExchangeRequest(),
            response=response,
            db=object(),
            db_log=object(),
        )
    )

    assert result == {"status": "success"}
    assert order == ["consume", "issue"]


@pytest.mark.parametrize(
    ("exchange", "finder_name", "clearer_name"),
    (
        (
            auth_router.exchange_social_auth_code,
            "_find_user_by_pending_social_auth_code",
            "_clear_social_auth_exchange_state",
        ),
        (
            auth_router.exchange_sso_auth_code,
            "_find_user_by_pending_sso_auth_code",
            "_clear_sso_auth_exchange_state",
        ),
    ),
)
def test_auth_code_exchange_never_issues_after_losing_consume_race(
    monkeypatch,
    exchange,
    finder_name,
    clearer_name,
):
    user = SimpleNamespace(id="user-1")
    response = _ExchangeResponse()
    monkeypatch.setattr(auth_router, "enforce_same_origin", lambda *_args: None)
    monkeypatch.setattr(auth_router, finder_name, lambda *_args: user)
    monkeypatch.setattr(
        user_settings,
        "get_user_setting_value",
        lambda *_args, **_kwargs: (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
    )
    monkeypatch.setattr(
        auth_router,
        "read_flow_context_cookie",
        lambda *_args, **_kwargs: {"account_mode": "primary", "replace_slot": None},
    )
    monkeypatch.setattr(auth_router, clearer_name, lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        auth_router,
        "_issue_authenticated_session",
        lambda **_kwargs: pytest.fail("A lost action race must not issue a session"),
    )

    with pytest.raises(HTTPException, match="Invalid or expired auth code"):
        asyncio.run(
            exchange(
                request=_ExchangeRequest(),
                response=response,
                db=object(),
                db_log=object(),
            )
        )
