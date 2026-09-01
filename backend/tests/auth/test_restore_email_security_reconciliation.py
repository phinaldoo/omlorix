from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import session_store
from app.auth.models import (
    Authentication,
    NativeAuthGrant,
    PendingAuthAction,
    PasswordResetToken,
    WebAuthnChallenge,
)
from app.database import Base
from app.email.models import (
    EMAIL_CHANGE_PENDING,
    OUTBOX_CANCELLED,
    EmailOutbox,
    EmailSecurityRateLimit,
    EmailSecurityState,
    PendingEmailChange,
    TrustedDeviceNotification,
    enqueue_email,
    hash_email_security_action,
    reconcile_email_security_after_restore,
)
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User
from app.utils import encryption as encryption_utils


def test_restore_invalidates_replayable_authentication_and_email_state(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    cache_revocations = []
    monkeypatch.setattr(
        session_store,
        "revoke_all_sessions",
        lambda: cache_revocations.append(True),
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Authentication.__table__,
            PasswordResetToken.__table__,
            WebAuthnChallenge.__table__,
            NativeAuthGrant.__table__,
            PendingAuthAction.__table__,
            EmailOutbox.__table__,
            PendingEmailChange.__table__,
            TrustedDeviceNotification.__table__,
            EmailSecurityRateLimit.__table__,
            EmailSecurityState.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    user = User(
        id="restore-user",
        email="restore@example.com",
        group_id="group-1",
        hashed_password="password-hash",
        first_name="Restore",
        last_name="User",
        role="user",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=True,
        created_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.flush()
    db.add_all(
        [
            Authentication(
                id="restore-session",
                user_id=user.id,
                device_info="Browser",
                ip_address="203.0.113.0/24",
                access_token="access",
                refresh_token="refresh",
                access_token_hash="a" * 64,
                refresh_token_hash="r" * 64,
                created_at=now,
                last_active_at=now,
            ),
            PasswordResetToken(
                id="restore-reset",
                user_id=user.id,
                token_hash="p" * 64,
                requested_ip="ip_hash",
                requested_user_agent="Browser",
                created_at=now,
                expires_at=now + timedelta(minutes=30),
            ),
            WebAuthnChallenge(
                id="restore-challenge",
                user_id=user.id,
                flow="authentication",
                challenge="challenge",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            NativeAuthGrant(
                token_hash="n" * 64,
                purpose="signin",
                provider="password",
                user_id=user.id,
                authentication_id="restore-session",
                code_challenge="code-challenge",
                state_hash="s" * 64,
                account_mode="primary",
                accepts_terms_of_service=False,
                twofa_satisfied=False,
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            PendingAuthAction(
                id="restore-action",
                user_id=user.id,
                purpose="password_signin_token",
                token_hash="t" * 64,
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            PendingEmailChange(
                id="restore-change",
                user_id=user.id,
                new_email="new@example.com",
                old_email=user.email,
                verify_token_hash="v" * 64,
                cancel_token_hash="c" * 64,
                status=EMAIL_CHANGE_PENDING,
                created_at=now,
                expires_at=now + timedelta(hours=24),
            ),
            TrustedDeviceNotification(
                id="restore-device",
                user_id=user.id,
                device_token_hash="d" * 64,
                device_summary="Browser",
                first_seen_at=now,
                last_seen_at=now,
            ),
            EmailSecurityRateLimit(
                bucket_key="b" * 64,
                attempt_count=1,
                window_started_at=now,
                cooldown_until=now + timedelta(minutes=1),
                expires_at=now + timedelta(minutes=15),
                updated_at=now,
            ),
            EmailSecurityState(id=1, action_epoch="old-epoch", updated_at=now),
        ]
    )
    queued = enqueue_email(
        db,
        user_id=user.id,
        recipient=user.email,
        template_type="password_reset",
        idempotency_key="restore-password-reset",
        payload={"token_id": "restore-reset", "reset_link": "secret-link"},
    )
    db.commit()
    browser_action_hash_before = hash_email_security_action(
        db,
        purpose="password_signin_token",
        secret_value="restored-browser-token",
    )

    result = reconcile_email_security_after_restore(db)

    assert result == {
        "sessions": 1,
        "password_reset_tokens": 1,
        "webauthn_challenges": 1,
        "native_auth_grants": 1,
        "pending_auth_actions": 1,
        "pending_email_changes": 1,
        "trusted_devices": 1,
        "rate_limits": 1,
        "queued_email": 1,
    }
    db.refresh(queued)
    assert queued.status == OUTBOX_CANCELLED
    assert queued.recipient is None
    assert queued.payload is None
    assert db.query(Authentication).count() == 0
    assert db.query(PendingAuthAction).count() == 0
    assert db.query(PendingEmailChange).count() == 0
    assert db.query(EmailSecurityState).one().action_epoch != "old-epoch"
    assert hash_email_security_action(
        db,
        purpose="password_signin_token",
        secret_value="restored-browser-token",
    ) != browser_action_hash_before
    reset = db.query(PasswordResetToken).one()
    assert reset.consumed_at is not None
    assert reset.requested_ip is None
    assert reset.requested_user_agent is None
    assert cache_revocations == [True]
    db.close()
