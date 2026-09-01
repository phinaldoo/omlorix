from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.auth.email_delivery import (
    EmailDeliveryConfig,
    EmailDeliveryConfigurationError,
    validate_email_delivery_config,
)
from app.auth import email_delivery as email_delivery_config
from app.email.delivery import EmailDeliverySendError, SMTPDeliveryClient
from app.email import delivery as email_delivery_transport
from app.email import worker as email_worker
from app.email.models import (
    EmailOutbox,
    OUTBOX_DEAD,
    OUTBOX_PROCESSING,
    OUTBOX_RETRY,
    OUTBOX_SENT,
    claim_email_batch,
    enqueue_email,
    lock_email_for_delivery,
    erase_user_email_state,
    mark_email_failed,
    mark_email_sent,
    renew_email_lease,
)
from app.utils import encryption as encryption_utils


def _email_session(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[EmailOutbox.__table__])
    return sessionmaker(bind=engine)()


def test_outbox_is_idempotent_encrypted_and_redacted_after_delivery(monkeypatch):
    db = _email_session(monkeypatch)
    try:
        row = enqueue_email(
            db,
            recipient="Security.User@Example.com",
            template_type="security_event",
            payload={"event_type": "password_changed", "secret_marker": "never-plaintext"},
            idempotency_key="security:user-1:password-change-1",
            user_id="user-1",
        )
        duplicate = enqueue_email(
            db,
            recipient="ignored@example.com",
            template_type="security_event",
            payload={"event_type": "password_changed"},
            idempotency_key="security:user-1:password-change-1",
            user_id="user-1",
        )
        db.commit()

        assert duplicate.id == row.id
        assert db.query(EmailOutbox).count() == 1
        raw_recipient, raw_payload = db.execute(
            text(
                "SELECT recipient, payload FROM email_delivery_outbox "
                "WHERE id = :row_id"
            ),
            {"row_id": row.id},
        ).one()
        assert "security.user@example.com" not in str(raw_recipient).lower()
        assert "never-plaintext" not in str(raw_payload)

        claimed = claim_email_batch(
            db,
            worker_id="worker-a",
            now=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
        assert [item.id for item in claimed] == [row.id]
        mark_email_sent(db, claimed[0])
        db.refresh(row)

        assert row.status == OUTBOX_SENT
        assert row.recipient is None
        assert row.payload is None
        assert row.sent_at is not None
    finally:
        db.close()


def test_outbox_retries_and_recovers_expired_leases_without_losing_ownership(monkeypatch):
    db = _email_session(monkeypatch)
    try:
        base = datetime.now(timezone.utc)
        row = enqueue_email(
            db,
            recipient="user@example.com",
            template_type="security_event",
            payload={"event_type": "new_device"},
            idempotency_key="security:user-1:new-device-1",
            max_attempts=3,
            available_at=base,
        )
        db.commit()

        claimed = claim_email_batch(
            db,
            worker_id="worker-a",
            lease_seconds=10,
            now=base + timedelta(seconds=1),
        )
        assert claimed[0].status == OUTBOX_PROCESSING
        assert renew_email_lease(
            db,
            claimed[0],
            worker_id="worker-a",
            lease_seconds=30,
            now=base + timedelta(seconds=5),
        )
        assert lock_email_for_delivery(
            db,
            claimed[0],
            worker_id="worker-b",
        ) is None
        locked = lock_email_for_delivery(
            db,
            claimed[0],
            worker_id="worker-a",
        )
        assert locked is not None and locked.id == row.id
        db.rollback()
        assert claim_email_batch(
            db,
            worker_id="worker-b",
            now=base + timedelta(seconds=20),
        ) == []

        assert mark_email_failed(
            db,
            claimed[0],
            error_type="provider_temporary",
            retryable=True,
            retry_delay_seconds=30,
            now=base + timedelta(seconds=21),
        ) == OUTBOX_RETRY
        assert claim_email_batch(
            db,
            worker_id="worker-b",
            now=base + timedelta(seconds=40),
        ) == []
        retried = claim_email_batch(
            db,
            worker_id="worker-b",
            now=base + timedelta(seconds=52),
        )
        assert [item.id for item in retried] == [row.id]
        assert retried[0].attempt_count == 2
    finally:
        db.close()


def test_expired_final_email_lease_is_terminalized_without_redelivery(monkeypatch):
    db = _email_session(monkeypatch)
    try:
        base = datetime.now(timezone.utc)
        row = enqueue_email(
            db,
            recipient="user@example.com",
            template_type="security_event",
            payload={"event_type": "password_changed"},
            idempotency_key="security:user-1:final-attempt",
            max_attempts=1,
            available_at=base,
        )
        db.commit()

        claimed = claim_email_batch(
            db,
            worker_id="worker-a",
            lease_seconds=10,
            now=base + timedelta(seconds=1),
        )
        assert [item.id for item in claimed] == [row.id]
        assert claimed[0].attempt_count == 1

        # Simulate a process crash after SMTP delivery but before ``sent`` was
        # committed. The expired final lease is an unknown outcome and must not
        # be claimed for a second SMTP attempt.
        assert claim_email_batch(
            db,
            worker_id="worker-b",
            now=base + timedelta(seconds=12),
        ) == []
        db.refresh(row)
        assert row.status == OUTBOX_DEAD
        assert row.attempt_count == 1
        assert row.last_error_type == "lease_expired"
        assert row.recipient is None
        assert row.payload is None

        assert claim_email_batch(
            db,
            worker_id="worker-c",
            now=base + timedelta(hours=1),
        ) == []
    finally:
        db.close()


def test_permanent_erasure_removes_active_and_terminal_user_outbox_state(monkeypatch):
    db = _email_session(monkeypatch)
    try:
        enqueue_email(
            db,
            recipient="user@example.com",
            template_type="security_event",
            payload={"event_type": "password_changed"},
            idempotency_key="security:password:user-1",
            user_id="user-1",
        )
        terminal = enqueue_email(
            db,
            recipient="user@example.com",
            template_type="security_event",
            payload={"event_type": "passkey_added"},
            idempotency_key="security:passkey:user-1",
            user_id="user-1",
        )
        terminal.status = OUTBOX_SENT
        terminal.recipient = None
        terminal.payload = None
        db.commit()

        assert erase_user_email_state(db, "user-1") == 2
        detached = enqueue_email(
            db,
            recipient="user@example.com",
            template_type="security_event",
            payload={"event_type": "account_deleted"},
            idempotency_key="security:account_deleted:detached-notice",
            user_id=None,
        )
        db.commit()

        assert db.query(EmailOutbox).all() == [detached]
        assert detached.user_id is None
        assert "user-1" not in detached.idempotency_key
    finally:
        db.close()


def test_outbox_rejects_recipient_lists_and_smtp_uses_one_explicit_envelope(monkeypatch):
    db = _email_session(monkeypatch)
    try:
        with pytest.raises(ValueError):
            enqueue_email(
                db,
                recipient="victim@example.com, attacker@example.com",
                template_type="security_event",
                payload={"event_type": "password_changed"},
                idempotency_key="invalid-recipient-list",
            )
    finally:
        db.close()

    config = EmailDeliveryConfig(
        email_from="security@example.com",
        application_name="Omlorix",
        smtp_host="smtp.example.com",
    )
    message = EmailMessage()
    message["From"] = config.sender_header
    message["To"] = "owner@example.com"
    message["Subject"] = "Security alert"
    message.set_content("Test")

    class _SMTP:
        def __init__(self):
            self.calls = []

        def send_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    smtp = _SMTP()
    client = SMTPDeliveryClient(config)
    client.server = smtp
    client.send_message(message)

    assert smtp.calls[0][1] == {
        "from_addr": "security@example.com",
        "to_addrs": ["owner@example.com"],
    }

    message.replace_header("To", "one@example.com, two@example.com")
    with pytest.raises(EmailDeliverySendError) as exc_info:
        client.send_message(message)
    assert exc_info.value.retryable is False
    assert exc_info.value.error_type == "invalid_recipient"


@pytest.mark.parametrize("implicit_tls", [False, True])
def test_smtp_transport_uses_verified_tls_context(monkeypatch, implicit_tls):
    verified_context = object()
    observed = {}

    class _SMTP:
        def __init__(self, host, port, *, timeout, context=None):
            observed["connect"] = {
                "host": host,
                "port": port,
                "timeout": timeout,
                "context": context,
            }

        def starttls(self, *, context):
            observed["starttls_context"] = context

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        email_delivery_transport.ssl,
        "create_default_context",
        lambda: verified_context,
    )
    monkeypatch.setattr(email_delivery_transport.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(email_delivery_transport.smtplib, "SMTP_SSL", _SMTP)
    config = EmailDeliveryConfig(
        email_from="security@example.com",
        application_name="Omlorix",
        smtp_host="smtp.example.com",
        smtp_use_ssl=implicit_tls,
        smtp_use_tls=not implicit_tls,
    )

    with SMTPDeliveryClient(config):
        pass

    if implicit_tls:
        assert observed["connect"]["context"] is verified_context
        assert "starttls_context" not in observed
    else:
        assert observed["connect"]["context"] is None
        assert observed["starttls_context"] is verified_context


def test_authenticated_smtp_cannot_send_credentials_in_cleartext():
    config = EmailDeliveryConfig(
        email_from="security@example.com",
        application_name="Omlorix",
        smtp_host="smtp.example.com",
        smtp_username="mailer",
        smtp_password="secret",
        smtp_use_ssl=False,
        smtp_use_tls=False,
    )

    with pytest.raises(EmailDeliveryConfigurationError):
        validate_email_delivery_config(config)


def test_unauthenticated_smtp_requires_tls_unless_operator_explicitly_overrides():
    plaintext_config = EmailDeliveryConfig(
        email_from="security@example.com",
        application_name="Omlorix",
        smtp_host="local-relay",
        smtp_use_ssl=False,
        smtp_use_tls=False,
    )

    with pytest.raises(EmailDeliveryConfigurationError):
        validate_email_delivery_config(plaintext_config)

    validate_email_delivery_config(
        EmailDeliveryConfig(
            email_from="security@example.com",
            application_name="Omlorix",
            smtp_host="local-relay",
            smtp_use_ssl=False,
            smtp_use_tls=False,
            allow_insecure_smtp=True,
        )
    )

    with pytest.raises(EmailDeliveryConfigurationError):
        validate_email_delivery_config(
            EmailDeliveryConfig(
                email_from="security@example.com",
                application_name="Omlorix",
                smtp_host="local-relay",
                smtp_username="mailer",
                smtp_password="secret",
                smtp_use_ssl=False,
                smtp_use_tls=False,
                allow_insecure_smtp=True,
            )
        )


def test_api_readiness_does_not_load_smtp_password(monkeypatch):
    reads = []
    values = {
        ("general", "application_name"): "Omlorix",
        ("login_general", "email_from_address"): "security@example.com",
        ("login_general", "smtp_host"): "smtp.example.com",
        ("login_general", "smtp_username"): "mailer",
        ("login_general", "smtp_password"): "worker-only-secret",
        ("login_general", "smtp_use_tls"): True,
        ("login_general", "smtp_use_ssl"): False,
    }

    def _get_value(page, key, _db):
        reads.append((page, key))
        return values.get((page, key))

    monkeypatch.setattr(
        email_delivery_config,
        "get_value_by_page_and_key",
        _get_value,
    )

    api_config = email_delivery_config.load_login_email_delivery_config(object())
    assert api_config.smtp_password == ""
    assert ("login_general", "smtp_password") not in reads

    reads.clear()
    worker_config = email_delivery_config.load_login_email_delivery_config(
        object(),
        include_secrets=True,
    )
    assert worker_config.smtp_password == "worker-only-secret"
    assert reads.count(("login_general", "smtp_password")) == 1


def test_worker_never_revives_a_sent_row_when_post_commit_callbacks_fail(monkeypatch):
    db = _email_session(monkeypatch)
    factory = sessionmaker(bind=db.get_bind())
    try:
        row = enqueue_email(
            db,
            recipient="former-user@example.com",
            template_type="security_event",
            payload={"event_type": "account_deleted"},
            idempotency_key="security:account_deleted:post-commit-failure",
            user_id=None,
        )
        row_id = row.id
        db.commit()

        config = EmailDeliveryConfig(
            email_from="security@example.com",
            application_name="Omlorix",
            smtp_host="smtp.example.com",
        )
        sends = []

        class _Client:
            server = object()

            def __init__(self, _config):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def send_message(self, message):
                sends.append(message["To"])

        class _FailingMetrics:
            def delivery(self, *_args):
                raise RuntimeError("telemetry unavailable")

        real_mark_sent = email_worker.mark_email_sent

        def _commit_then_raise(session, claimed_row):
            real_mark_sent(session, claimed_row)
            raise RuntimeError("post-commit callback failed")

        monkeypatch.setattr(email_worker, "SessionLocal", factory)
        monkeypatch.setattr(email_worker, "_load_delivery_config", lambda: config)
        monkeypatch.setattr(email_worker, "SMTPDeliveryClient", _Client)
        monkeypatch.setattr(email_worker, "mark_email_sent", _commit_then_raise)
        monkeypatch.setattr(email_worker, "_write_heartbeat", lambda **_kwargs: None)

        assert email_worker._process_batch("worker-a", _FailingMetrics()) == 1

        db.expire_all()
        stored = db.query(EmailOutbox).filter(EmailOutbox.id == row_id).one()
        assert sends == ["former-user@example.com"]
        assert stored.status == OUTBOX_SENT
        assert stored.recipient is None
        assert stored.payload is None
    finally:
        db.close()
