from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth.email_delivery import EmailDeliveryConfig
from app.auth.models import (
    Authentication,
    NativeAuthGrant,
    PasswordResetToken,
    PendingAuthAction,
    WebAuthnChallenge,
)
from app.database import Base
from app.email import change as email_change
from app.email.models import (
    EMAIL_CHANGE_CANCELLED,
    EMAIL_CHANGE_COMPLETED,
    EMAIL_CHANGE_EXPIRED,
    OUTBOX_CANCELLED,
    EmailOutbox,
    PendingEmailChange,
    enqueue_email,
)
from app.email.validation import _otp_job_is_current, validate_outbox_row
from app.users import utils as user_utils
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User, cancel_scheduled_deletion, restore_user
from app.utils import encryption as encryption_utils
from app.workers.models import (
    AuditEventErasureGuard,
    AuditEventSubjectState,
    DurableWorkerJob,
    audit_event_subject_fingerprint,
)


def _email_change_session(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    monkeypatch.setattr(
        email_change,
        "load_login_email_delivery_config",
        lambda _db: EmailDeliveryConfig(
            email_from="security@example.com",
            application_name="Omlorix",
            smtp_host="smtp.example.com",
        ),
    )
    monkeypatch.setattr(email_change, "get_public_url", lambda _db: "https://chat.example.com")
    monkeypatch.setattr(email_change, "_language", lambda _user, _db: "en")
    monkeypatch.setattr(
        email_change,
        "create_email_change_secrets",
        lambda: ("verify-token-abcdefghijklmnopqrstuvwxyz", "cancel-token-abcdefghijklmnopqrstuvwxyz"),
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Authentication.__table__,
            PasswordResetToken.__table__,
            PendingAuthAction.__table__,
            NativeAuthGrant.__table__,
            WebAuthnChallenge.__table__,
            EmailOutbox.__table__,
            PendingEmailChange.__table__,
            AuditEventErasureGuard.__table__,
            AuditEventSubjectState.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    user = User(
        id="user-1",
        email="old@example.com",
        group_id="group-1",
        hashed_password="password-hash",
        first_name="Email",
        last_name="Owner",
        role="user",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=True,
        created_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.commit()
    return db, user


def _add_session_and_reset_token(db, user_id: str) -> PasswordResetToken:
    now = datetime.now(timezone.utc)
    reset = PasswordResetToken(
        id="reset-1",
        user_id=user_id,
        token_hash="reset-hash",
        requested_ip="203.0.113.10",
        requested_user_agent="Browser",
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add_all(
        [
            Authentication(
                id="session-1",
                user_id=user_id,
                device_info="Browser",
                ip_address="203.0.113.0/24",
                access_token="access-token",
                refresh_token="refresh-token",
                access_token_hash="access-hash",
                refresh_token_hash="refresh-hash",
                created_at=now,
                last_active_at=now,
            ),
            reset,
        ]
    )
    db.commit()
    return reset


def test_email_changes_only_after_verification_and_revokes_old_credentials(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    revoked = []
    monkeypatch.setattr(email_change, "revoke_user_sessions", revoked.append)
    try:
        reset = _add_session_and_reset_token(db, user.id)
        request_row = email_change.request_email_change(
            db,
            user,
            " New.Address@Example.com ",
        )
        db.commit()
        db.refresh(user)

        assert user.email == "old@example.com"
        assert request_row.status == "pending"
        assert db.query(EmailOutbox).count() == 2
        raw_old_email = db.execute(
            text(
                "SELECT old_email FROM pending_email_changes "
                "WHERE id = :request_id"
            ),
            {"request_id": request_row.id},
        ).scalar_one()
        assert "old@example.com" not in str(raw_old_email).lower()

        historical_notice = enqueue_email(
            db,
            user_id=user.id,
            recipient="historical@example.com",
            template_type="email_change",
            language_code="en",
            idempotency_key="email-change:changed:historical:old",
            payload={"kind": "changed", "request_id": "historical"},
        )
        db.commit()

        result = email_change.confirm_email_change(
            db,
            "verify-token-abcdefghijklmnopqrstuvwxyz",
        )
        db.refresh(user)
        db.refresh(reset)
        db.refresh(request_row)

        assert result["sessions_revoked"] is True
        assert user.email == "new.address@example.com"
        assert request_row.status == EMAIL_CHANGE_COMPLETED
        assert reset.consumed_at is not None
        assert reset.requested_ip is None
        assert db.query(Authentication).count() == 0
        assert revoked == [user.id]

        jobs = db.query(EmailOutbox).all()
        cancelled = [job for job in jobs if job.status == "cancelled"]
        pending = [job for job in jobs if job.status == "pending"]
        assert len(cancelled) == 2
        assert historical_notice in pending
        assert all(job.recipient is None and job.payload is None for job in cancelled)
        assert {job.recipient for job in pending} == {
            "historical@example.com",
            "old@example.com",
            "new.address@example.com",
        }

        with pytest.raises(HTTPException) as exc_info:
            email_change.confirm_email_change(
                db,
                "verify-token-abcdefghijklmnopqrstuvwxyz",
            )
        assert exc_info.value.status_code == 400
    finally:
        db.close()


def test_old_address_can_cancel_a_pending_email_change(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    revoked = []
    monkeypatch.setattr(email_change, "revoke_user_sessions", revoked.append)
    try:
        reset = _add_session_and_reset_token(db, user.id)
        request_row = email_change.request_email_change(
            db,
            user,
            "new@example.com",
        )
        db.commit()

        result = email_change.cancel_email_change(
            db,
            "cancel-token-abcdefghijklmnopqrstuvwxyz",
        )
        db.refresh(user)
        db.refresh(request_row)
        db.refresh(reset)

        assert result["status"] == "success"
        assert result["sessions_revoked"] is True
        assert user.email == "old@example.com"
        assert request_row.status == EMAIL_CHANGE_CANCELLED
        assert db.query(Authentication).count() == 0
        assert reset.consumed_at is not None
        assert reset.requested_ip is None
        assert revoked == [user.id]
        assert any(
            job.template_type == "email_change"
            and job.status == "pending"
            and job.payload["kind"] == "cancelled"
            for job in db.query(EmailOutbox).all()
        )
    finally:
        db.close()


def test_expired_cancel_link_cannot_revoke_credentials_or_send_notice(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    revoked = []
    monkeypatch.setattr(email_change, "revoke_user_sessions", revoked.append)
    try:
        reset = _add_session_and_reset_token(db, user.id)
        request_row = email_change.request_email_change(
            db,
            user,
            "new@example.com",
        )
        request_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            email_change.cancel_email_change(
                db,
                "cancel-token-abcdefghijklmnopqrstuvwxyz",
            )

        db.refresh(request_row)
        db.refresh(reset)
        jobs = db.query(EmailOutbox).all()
        assert exc_info.value.status_code == 400
        assert request_row.status == EMAIL_CHANGE_EXPIRED
        assert db.query(Authentication).count() == 1
        assert reset.consumed_at is None
        assert reset.requested_ip == "203.0.113.10"
        assert revoked == []
        assert len(jobs) == 2
        assert all(job.status == OUTBOX_CANCELLED for job in jobs)
        assert all(job.recipient is None and job.payload is None for job in jobs)
    finally:
        db.close()


def test_email_change_requests_are_rate_limited_under_the_user_lock(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        email_change.request_email_change(db, user, "first@example.com")
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            email_change.request_email_change(db, user, "second@example.com")

        assert exc_info.value.status_code == 429
        assert int(exc_info.value.headers["Retry-After"]) >= 1
        assert db.query(PendingEmailChange).count() == 1
    finally:
        db.close()


def test_queued_email_otp_is_rejected_after_current_email_changes(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        settings = deepcopy(user.settings)
        settings["secret"].update(
            {
                "2fa_otp_hash": "otp-hash",
                "2fa_otp_expires_at": expires_at.isoformat(),
                "2fa_otp_provider": "email",
                "2fa_otp_purpose": "login",
                "2fa_otp_destination": "old@example.com",
            }
        )
        user.settings = settings
        db.commit()
        row = SimpleNamespace(user_id=user.id, recipient="old@example.com")
        payload = {
            "otp_hash": "otp-hash",
            "provider": "email",
            "purpose": "login",
        }

        assert _otp_job_is_current(db, row, payload, datetime.now(timezone.utc))

        user.email = "new@example.com"
        db.commit()

        assert not _otp_job_is_current(
            db,
            row,
            payload,
            datetime.now(timezone.utc),
        )
    finally:
        db.close()


def test_password_reset_consumes_pending_email_change_and_request_jobs(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    revoked = []
    monkeypatch.setattr(user_utils, "revoke_user_sessions", revoked.append)
    try:
        reset = _add_session_and_reset_token(db, user.id)
        request_row = email_change.request_email_change(
            db,
            user,
            "new@example.com",
        )
        db.commit()

        result = user_utils._commit_password_change_transaction(
            db,
            user=user,
            new_password_hash="replacement-password-hash",
            reset_token=reset,
            security_event_type="password_reset_completed",
        )
        db.refresh(request_row)
        db.refresh(reset)

        assert result == {"status": "success", "reauth_required": True}
        assert request_row.status == EMAIL_CHANGE_CANCELLED
        assert reset.consumed_at is not None
        assert db.query(Authentication).count() == 0
        assert revoked == [user.id]
        request_jobs = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.template_type == "email_change")
            .all()
        )
        assert request_jobs
        assert all(job.status == OUTBOX_CANCELLED for job in request_jobs)
        assert all(job.recipient is None and job.payload is None for job in request_jobs)

        with pytest.raises(HTTPException) as exc_info:
            email_change.confirm_email_change(
                db,
                "verify-token-abcdefghijklmnopqrstuvwxyz",
            )
        assert exc_info.value.status_code == 400
    finally:
        db.close()


def test_password_change_rechecks_old_password_after_relocking(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        user.hashed_password = "concurrent-password-hash"
        db.commit()
        monkeypatch.setattr(
            "app.auth.utils.verify_password",
            lambda _plain, password_hash: password_hash == "superseded-password-hash",
        )

        with pytest.raises(HTTPException) as exc_info:
            user_utils._commit_password_change_transaction(
                db,
                user=SimpleNamespace(id=user.id),
                new_password_hash="attacker-selected-password-hash",
                verified_current_password="previous-password",
            )

        db.expire_all()
        persisted_user = db.query(User).filter(User.id == user.id).one()
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Old password is incorrect."
        assert persisted_user.hashed_password == "concurrent-password-hash"
        assert db.query(EmailOutbox).count() == 0
    finally:
        db.close()


def test_password_change_rechecks_password_reuse_after_relocking(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        user.hashed_password = "concurrent-password-hash"
        db.commit()
        monkeypatch.setattr(
            "app.auth.utils.verify_password",
            lambda plain, password_hash: (
                plain == "concurrent-password"
                and password_hash == "concurrent-password-hash"
            ),
        )

        with pytest.raises(HTTPException) as exc_info:
            user_utils._commit_password_change_transaction(
                db,
                user=SimpleNamespace(id=user.id),
                new_password_hash="replacement-password-hash",
                new_password_plaintext_for_reuse_check="concurrent-password",
            )

        db.expire_all()
        persisted_user = db.query(User).filter(User.id == user.id).one()
        assert exc_info.value.status_code == 400
        assert (
            exc_info.value.detail
            == "New password must be different from the current password."
        )
        assert persisted_user.hashed_password == "concurrent-password-hash"
        assert db.query(EmailOutbox).count() == 0
    finally:
        db.close()


def test_stale_settings_cannot_commit_profile_change_without_its_outbox_job(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        user.settings = {}
        db.commit()
        monkeypatch.setattr(
            user_utils,
            "get_user_group_setting_value",
            lambda *_args, **_kwargs: True,
        )

        def fail_enqueue(*_args, **_kwargs):
            raise RuntimeError("outbox unavailable")

        monkeypatch.setattr(
            email_change,
            "enqueue_email",
            fail_enqueue,
        )

        with pytest.raises(RuntimeError, match="outbox unavailable"):
            user_utils.update_user_personal_details(
                user.id,
                db,
                type(
                    "PersonalDetails",
                    (),
                    {
                        "first_name": "Changed",
                        "last_name": user.last_name,
                        "email": "new@example.com",
                    },
                )(),
            )

        db.expire_all()
        persisted_user = db.query(User).filter(User.id == user.id).one()
        assert persisted_user.first_name == "Email"
        assert persisted_user.email == "old@example.com"
        assert persisted_user.settings == {}
        assert db.query(PendingEmailChange).count() == 0
        assert db.query(EmailOutbox).count() == 0
    finally:
        db.close()


def test_soft_deletion_notices_are_state_bound_and_cancelled_on_restore(monkeypatch):
    db, user = _email_change_session(monkeypatch)
    try:
        now = datetime.now(timezone.utc)
        scheduled_for = now + timedelta(days=30)
        user.deleted_at = now
        user.deletion_scheduled_for = scheduled_for
        user.is_active = False
        scheduled = enqueue_email(
            db,
            user_id=user.id,
            recipient=user.email,
            template_type="security_event",
            idempotency_key="security:scheduled-deletion:user-1",
            payload={
                "event_type": "account_deletion_scheduled",
                "purge_at": scheduled_for.isoformat(),
            },
        )
        db.commit()

        assert validate_outbox_row(db, scheduled)[0] is True
        cancel_scheduled_deletion(db, user.id)
        db.refresh(scheduled)
        db.refresh(user)
        assert user.deleted_at is not None
        assert user.deletion_scheduled_for is None
        assert scheduled.status == OUTBOX_CANCELLED
        assert scheduled.recipient is None and scheduled.payload is None

        deactivated = enqueue_email(
            db,
            user_id=user.id,
            recipient=user.email,
            template_type="security_event",
            idempotency_key="security:deactivated:user-1",
            payload={"event_type": "account_deactivated"},
        )
        db.commit()
        assert validate_outbox_row(db, deactivated)[0] is True

        subject_state = AuditEventSubjectState(
            subject_fingerprint=audit_event_subject_fingerprint(user.id),
            erased_at=now,
        )
        db.add(subject_state)
        db.commit()

        restore_user(db, user.id)
        db.refresh(deactivated)
        db.refresh(user)
        assert user.deleted_at is None
        assert user.is_active is True
        db.refresh(subject_state)
        assert subject_state.erased_at is None
        assert deactivated.status == OUTBOX_CANCELLED
        assert deactivated.recipient is None and deactivated.payload is None
    finally:
        db.close()
