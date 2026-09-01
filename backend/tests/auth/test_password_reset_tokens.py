import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth.models import (
    PasswordResetToken,
    consume_password_reset_token,
    delete_stale_password_reset_tokens,
    invalidate_user_password_reset_tokens,
)
from app.auth import utils as auth_utils
from app.email.models import (
    EmailSecurityRateLimit,
    consume_email_security_cooldown,
)


def _password_reset_session():
    engine = create_engine("sqlite:///:memory:")
    PasswordResetToken.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_validate_password_reset_token_accepts_naive_utc_database_timestamp(monkeypatch):
    token = SimpleNamespace(
        consumed_at=None,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
    )

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)

    assert auth_utils.validate_password_reset_token(object(), "reset-token") == {"valid": True}


def test_validate_password_reset_token_rejects_naive_expired_database_timestamp(monkeypatch):
    token = SimpleNamespace(
        consumed_at=None,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)

    assert auth_utils.validate_password_reset_token(object(), "reset-token") == {"valid": False}


def test_validate_password_reset_token_audits_invalid_without_sensitive_material(monkeypatch):
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda purpose, client_ip, user_agent, db=None: False)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: None)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    response = auth_utils.validate_password_reset_token(object(), "reset-token", db_log=object(), request=request)

    assert response == {"valid": False}
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] is None
    assert audit_logs[0]["action"] == "PASSWORD_RESET_VALIDATE_FAILED"
    assert audit_logs[0]["reason"] == "invalid_token"
    assert audit_logs[0]["details"] == {"status": "failure", "validation_result": "invalid"}
    assert "reset-token" not in str(audit_logs)


def test_validate_password_reset_token_audits_expired_without_sensitive_material(monkeypatch):
    token = SimpleNamespace(
        user_id="user-id",
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda purpose, client_ip, user_agent, db=None: False)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    response = auth_utils.validate_password_reset_token(object(), "reset-token", db_log=object(), request=request)

    assert response == {"valid": False}
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] == "user-id"
    assert audit_logs[0]["action"] == "PASSWORD_RESET_VALIDATE_FAILED"
    assert audit_logs[0]["reason"] == "expired_or_consumed_token"
    assert audit_logs[0]["details"] == {"status": "failure", "validation_result": "expired_or_consumed"}
    assert "reset-token" not in str(audit_logs)


def test_validate_password_reset_token_throttles_before_lookup(monkeypatch):
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda purpose, client_ip, user_agent, db=None: True)
    monkeypatch.setattr(
        auth_utils,
        "get_password_reset_token_by_hash",
        lambda *args, **kwargs: pytest.fail("throttled validation must not look up tokens"),
    )
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.validate_password_reset_token(object(), "reset-token", db_log=object(), request=request)

    assert exc_info.value.status_code == 429
    assert len(audit_logs) == 1
    assert audit_logs[0]["action"] == "PASSWORD_RESET_VALIDATE_THROTTLED"
    assert audit_logs[0]["reason"] == "rate_limited"
    assert audit_logs[0]["details"] == {"status": "failure", "status_code": 429, "endpoint": "validate"}
    assert "reset-token" not in str(audit_logs)


def test_password_reset_metadata_is_minimized(monkeypatch):
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: "test-secret")

    raw_ip = "203.0.113.42"
    raw_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.6367.119 Safari/537.36 user@example.com"
    )

    minimized_ip = auth_utils._minimize_password_reset_ip(raw_ip)
    minimized_user_agent = auth_utils._minimize_password_reset_user_agent(raw_user_agent)

    assert minimized_ip.startswith("ip_")
    assert raw_ip not in minimized_ip
    assert minimized_user_agent == "Chrome on macOS"
    assert "124.0.6367.119" not in minimized_user_agent
    assert "user@example.com" not in minimized_user_agent


def test_invalidate_password_reset_tokens_clears_metadata():
    db = _password_reset_session()
    token = PasswordResetToken(
        user_id="user-1",
        token_hash="hash-1",
        requested_ip="ip_abcdef1234567890",
        requested_user_agent="Chrome on macOS",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        consumed_at=None,
    )
    db.add(token)
    db.commit()

    assert invalidate_user_password_reset_tokens(db, "user-1") == 1

    db.refresh(token)
    assert token.consumed_at is not None
    assert token.requested_ip is None
    assert token.requested_user_agent is None


def test_consume_password_reset_token_is_single_use_and_clears_metadata():
    db = _password_reset_session()
    token = PasswordResetToken(
        user_id="user-1",
        token_hash="hash-1",
        requested_ip="ip_abcdef1234567890",
        requested_user_agent="Chrome on macOS",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        consumed_at=None,
    )
    db.add(token)
    db.commit()

    assert consume_password_reset_token(db, token.id) is True
    db.commit()

    db.refresh(token)
    assert token.consumed_at is not None
    assert token.requested_ip is None
    assert token.requested_user_agent is None
    assert consume_password_reset_token(db, token.id) is False


def test_consume_password_reset_token_rejects_expired_token():
    db = _password_reset_session()
    token = PasswordResetToken(
        user_id="user-1",
        token_hash="hash-1",
        requested_ip="ip_abcdef1234567890",
        requested_user_agent="Chrome on macOS",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        consumed_at=None,
    )
    db.add(token)
    db.commit()

    assert consume_password_reset_token(db, token.id) is False

    db.refresh(token)
    assert token.consumed_at is None
    assert token.requested_ip == "ip_abcdef1234567890"
    assert token.requested_user_agent == "Chrome on macOS"


def test_delete_stale_password_reset_tokens_removes_expired_and_old_consumed_tokens():
    db = _password_reset_session()
    now = datetime.now(timezone.utc)
    tokens = [
        PasswordResetToken(
            user_id="user-1",
            token_hash="expired",
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(minutes=1),
            consumed_at=None,
        ),
        PasswordResetToken(
            user_id="user-1",
            token_hash="old-consumed",
            created_at=now - timedelta(hours=2),
            expires_at=now + timedelta(minutes=10),
            consumed_at=now - timedelta(hours=1),
        ),
        PasswordResetToken(
            user_id="user-1",
            token_hash="active",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            consumed_at=None,
        ),
        PasswordResetToken(
            user_id="user-1",
            token_hash="recent-consumed",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            consumed_at=now - timedelta(minutes=5),
        ),
    ]
    db.add_all(tokens)
    db.commit()

    assert delete_stale_password_reset_tokens(db, consumed_retention=timedelta(minutes=30)) == 2

    remaining = {token.token_hash for token in db.query(PasswordResetToken).all()}
    assert remaining == {"active", "recent-consumed"}


def test_password_reset_throttle_uses_hashed_identifier_key(monkeypatch):
    recorded_keys = []

    class FakeRedis:
        def eval(self, _script, _numkeys, window_key, cooldown_key, *_args):
            recorded_keys.extend((window_key, cooldown_key))
            return 1

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "test-salt")
    monkeypatch.setattr(auth_utils, "get_redis_client", lambda: FakeRedis())

    identifier_hash = auth_utils._hash_password_reset_identifier("User@Example.com")

    assert identifier_hash is not None
    assert auth_utils._is_password_reset_throttled("203.0.113.4", identifier_hash) is False
    assert any(key.startswith("omlorix:password_reset:window:reset:id:resetid_") for key in recorded_keys)
    assert any(key.startswith("omlorix:password_reset:cooldown:reset:id:resetid_") for key in recorded_keys)
    assert all("user@example.com" not in key for key in recorded_keys)


def test_password_reset_throttle_uses_shared_database_when_redis_is_disabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    EmailSecurityRateLimit.__table__.create(bind=engine)
    limiter_sessions = sessionmaker(bind=engine)

    monkeypatch.setattr(auth_utils, "get_redis_client", lambda: None)
    monkeypatch.setattr(auth_utils, "SessionLocal", limiter_sessions)

    assert auth_utils._mark_password_reset_attempt("reset:ip:203.0.113.4") is True
    assert auth_utils._mark_password_reset_attempt("reset:ip:203.0.113.4") is False

    db = limiter_sessions()
    try:
        row = db.query(EmailSecurityRateLimit).one()
        assert row.attempt_count == 1
        assert "203.0.113.4" not in row.bucket_key
        assert len(row.bucket_key) == 64
    finally:
        db.close()


def test_email_security_cooldown_buckets_are_durable_and_independent_by_purpose(
    tmp_path,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'email-security-cooldown.db'}")
    EmailSecurityRateLimit.__table__.create(bind=engine)
    limiter_sessions = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    first_session = limiter_sessions()
    try:
        assert consume_email_security_cooldown(
            first_session,
            bucket="twofa-otp:user-1:email:login",
            cooldown_seconds=30,
            now=now,
        ) == 0
        first_session.commit()
    finally:
        first_session.close()

    second_session = limiter_sessions()
    try:
        assert consume_email_security_cooldown(
            second_session,
            bucket="twofa-otp:user-1:email:login",
            cooldown_seconds=30,
            now=now,
        ) == 30
        assert consume_email_security_cooldown(
            second_session,
            bucket="twofa-otp:user-1:email:step_up",
            cooldown_seconds=30,
            now=now,
        ) == 0
        second_session.commit()
    finally:
        second_session.close()

    verification_session = limiter_sessions()
    try:
        assert verification_session.query(EmailSecurityRateLimit).count() == 2
    finally:
        verification_session.close()


def test_password_reset_identifier_hash_does_not_use_fallback_salts(monkeypatch):
    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "")
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: pytest.fail("password reset hashing must not use secret.secret_key fallback"),
    )

    first_hash = auth_utils._hash_password_reset_identifier("User@Example.com", object())
    second_hash = auth_utils._hash_password_reset_identifier("user@example.com", object())

    assert first_hash is not None
    assert first_hash == second_hash


def test_password_reset_identifier_hash_is_replica_stable_with_shared_log_salt(monkeypatch):
    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "")
    monkeypatch.setenv("LOG_IP_HASH_SALT", "deployment-wide-pseudonymization-secret")
    monkeypatch.setattr(
        auth_utils,
        "_GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT",
        "replica-a-local-salt",
    )
    first_hash = auth_utils._hash_password_reset_identifier("User@Example.com")

    monkeypatch.setattr(
        auth_utils,
        "_GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT",
        "replica-b-local-salt",
    )
    second_hash = auth_utils._hash_password_reset_identifier("user@example.com")

    assert first_hash == second_hash


def test_password_reset_token_attempt_throttle_uses_ip_and_user_agent_hash(monkeypatch):
    recorded_keys = []

    class FakeRedis:
        def incr(self, key):
            recorded_keys.append(key)
            return 1

        def expire(self, key, _seconds):
            recorded_keys.append(key)

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "test-salt")
    monkeypatch.setattr(auth_utils, "get_redis_client", lambda: FakeRedis())

    throttled = auth_utils._is_password_reset_token_attempt_throttled(
        "validate",
        "203.0.113.4",
        "Sensitive Browser user@example.com",
    )

    assert throttled is False
    assert any("reset:token:validate:ip:203.0.113.4" in key for key in recorded_keys)
    assert any("reset:token:validate:ipua:203.0.113.4:ua_" in key for key in recorded_keys)
    assert "Sensitive Browser" not in str(recorded_keys)
    assert "user@example.com" not in str(recorded_keys)


def test_password_reset_token_attempt_throttle_does_not_use_fallback_salts(monkeypatch):
    recorded_keys = []

    class FakeRedis:
        def incr(self, key):
            recorded_keys.append(key)
            return 1

        def expire(self, key, _seconds):
            recorded_keys.append(key)

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "")
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: pytest.fail("token attempt hashing must not use secret.secret_key fallback"),
    )
    monkeypatch.setattr(auth_utils, "get_redis_client", lambda: FakeRedis())

    throttled = auth_utils._is_password_reset_token_attempt_throttled(
        "validate",
        "203.0.113.4",
        "Sensitive Browser user@example.com",
        object(),
    )

    assert throttled is False
    assert any("reset:token:validate:ipua:203.0.113.4:ua_" in key for key in recorded_keys)


def test_password_reset_throttled_notification_uses_identifier_hash(monkeypatch):
    notifications = []
    logs = []

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "test-salt")
    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "is_password_reset_ready", lambda db: True)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db=None: "203.0.113.9")
    monkeypatch.setattr(auth_utils, "_is_password_reset_throttled", lambda client_ip, identifier_hash: True)
    monkeypatch.setattr(auth_utils, "_equalize_password_reset_response_timing", lambda started_at: None)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: logs.append((args, kwargs)))
    monkeypatch.setattr(auth_utils, "_notify_suspicious_auth_activity", lambda *args, **kwargs: notifications.append((args, kwargs)))

    response = auth_utils.request_password_reset(
        object(),
        object(),
        SimpleNamespace(headers={"User-Agent": "Test Agent"}),
        "User@Example.com",
    )

    expected_hash = auth_utils._hash_password_reset_identifier("user@example.com")

    assert response == {
        "status": "ok",
        "message": "If an account exists, a reset link has been sent.",
    }
    assert logs
    assert notifications
    assert notifications[0][0] == (
        "password_reset_request_throttled",
        "Password reset request throttled for IP 203.0.113.9",
    )
    assert notifications[0][1]["details"] == {
        "identifier_hash": expected_hash,
        "ip_address": "203.0.113.9",
    }
    assert "user@example.com" not in str(notifications[0][1]["details"])


def test_password_reset_request_stages_outbox_work_in_request_transaction(monkeypatch):
    process_calls = []

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "test-salt")
    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "is_password_reset_ready", lambda db: True)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db=None: "203.0.113.9")
    monkeypatch.setattr(auth_utils, "_is_password_reset_throttled", lambda client_ip, identifier_hash: False)
    monkeypatch.setattr(auth_utils, "_equalize_password_reset_response_timing", lambda started_at: None)
    monkeypatch.setattr(
        auth_utils,
        "_process_password_reset_request",
        lambda *args: process_calls.append(args),
    )

    db = object()
    db_log = object()
    response = auth_utils.request_password_reset(
        db,
        db_log,
        SimpleNamespace(headers={"User-Agent": "Test Agent", "Accept-Language": "de"}),
        "User@Example.com",
    )

    assert response["status"] == "ok"
    assert process_calls == [
        (db, db_log, "user@example.com", "203.0.113.9", "Test Agent", "de")
    ]


def test_password_reset_staging_failure_keeps_generic_response(monkeypatch):
    rollback_calls = []
    db = SimpleNamespace(rollback=lambda: rollback_calls.append(True))

    monkeypatch.setattr(auth_utils, "_PASSWORD_RESET_IDENTIFIER_HASH_SALT", "test-salt")
    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda _db: True)
    monkeypatch.setattr(auth_utils, "is_password_reset_ready", lambda _db: True)
    monkeypatch.setattr(
        auth_utils,
        "_client_ip_from_request",
        lambda _request, db=None: "203.0.113.9",
    )
    monkeypatch.setattr(
        auth_utils,
        "_is_password_reset_throttled",
        lambda _client_ip, _identifier_hash: False,
    )
    monkeypatch.setattr(
        auth_utils,
        "_equalize_password_reset_response_timing",
        lambda _started_at: None,
    )
    monkeypatch.setattr(
        auth_utils,
        "_process_password_reset_request",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    response = auth_utils.request_password_reset(
        db,
        object(),
        SimpleNamespace(headers={"User-Agent": "Test Agent"}),
        "user@example.com",
    )

    assert response == {
        "status": "ok",
        "message": "If an account exists, a reset link has been sent.",
    }
    assert rollback_calls == [True]


def test_password_reset_response_timing_uses_floor_and_jitter(monkeypatch):
    sleep_calls = []

    monkeypatch.setattr(auth_utils, "_password_reset_response_delay_seconds", lambda: 0.25)
    monkeypatch.setattr(auth_utils.time, "monotonic", lambda: 10.05)
    monkeypatch.setattr(auth_utils.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    auth_utils._equalize_password_reset_response_timing(10.0)

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.20)


def test_password_reset_worker_skips_ineligible_user(monkeypatch):
    user = SimpleNamespace(id="user-id", email="user@example.com")

    monkeypatch.setattr(auth_utils, "get_user", lambda db, email=None: user)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: {"status": "pending"})
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "create_password_reset_token",
        lambda *args, **kwargs: pytest.fail("ineligible users must not receive reset tokens"),
    )

    auth_utils._process_password_reset_request(
        object(), object(), "user@example.com", "127.0.0.1", "Test Agent", None
    )


def test_password_reset_user_lock_rebinds_and_rechecks_current_email():
    calls = []
    current_user = SimpleNamespace(id="user-id", email="new@example.com")

    class Query:
        def populate_existing(self):
            calls.append("populate_existing")
            return self

        def filter(self, *_args):
            calls.append("filter")
            return self

        def with_for_update(self):
            calls.append("with_for_update")
            return self

        def first(self):
            calls.append("first")
            return current_user

    class Db:
        def query(self, entity):
            assert entity is auth_utils.User
            calls.append("query")
            return Query()

    locked_user = auth_utils._lock_password_reset_user_for_identifier(
        Db(),
        "user-id",
        "old@example.com",
    )

    assert locked_user is None
    assert calls == [
        "query",
        "populate_existing",
        "filter",
        "with_for_update",
        "first",
    ]


def test_locked_password_reset_eligibility_recheck_never_reads_group_policy(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "validate_user_login_eligibility",
        lambda *_args, **_kwargs: pytest.fail(
            "post-lock reset eligibility must not run committing policy reads"
        ),
    )
    user = SimpleNamespace(
        id="user-id",
        auth_management_mode="local",
        deleted_at=None,
        role="user",
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={
            "is_locked": True,
            "lock_until": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
            "type": "temporary",
            "reason": "expired",
        },
    )

    assert auth_utils._password_reset_locked_user_ineligible_status(user) is None
    assert user.lock == {
        "is_locked": False,
        "lock_until": None,
        "type": "",
        "reason": "",
    }


def test_password_reset_worker_ignores_non_email_identifier(monkeypatch):
    log_calls = []

    monkeypatch.setattr(
        auth_utils,
        "get_user",
        lambda *args, **kwargs: pytest.fail("password reset should not resolve non-email identifiers"),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_password_reset_token",
        lambda *args, **kwargs: pytest.fail("non-email identifiers must not receive reset tokens"),
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: log_calls.append((args, kwargs)))

    auth_utils._process_password_reset_request(
        object(), object(), "jdoe", "127.0.0.1", "Test Agent", None
    )

    assert len(log_calls) == 1
    assert log_calls[0][0][2] == "info"
    assert log_calls[0][0][3] == "Password reset requested for unknown account"


def test_password_reset_worker_handles_user_missing_during_lock_check(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        deleted_at=None,
        role="user",
        is_active=True,
        account_type="regular",
        group_id="group-id",
    )

    monkeypatch.setattr(auth_utils, "get_user", lambda db, email=None: user)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)
    monkeypatch.setattr(auth_utils, "is_group_accessible_now", lambda group_id, db, is_admin=False: {"accessible": True})
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: (_ for _ in ()).throw(HTTPException(status_code=404, detail="User not found")))
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "create_password_reset_token",
        lambda *args, **kwargs: pytest.fail("missing users must not receive reset tokens"),
    )

    auth_utils._process_password_reset_request(
        object(), object(), "user@example.com", "127.0.0.1", "Test Agent", None
    )


def test_confirm_password_reset_rejects_locked_user(monkeypatch):
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda *args, **kwargs: False)
    token = SimpleNamespace(
        user_id="user-id",
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    user = SimpleNamespace(id="user-id")
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: {"is_locked": True})
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_audit_password_reset_confirm", lambda **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid or expired password reset token."


def test_confirm_password_reset_rejects_user_missing_during_lock_check(monkeypatch):
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda *args, **kwargs: False)
    token = SimpleNamespace(
        user_id="user-id",
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    user = SimpleNamespace(
        id="user-id",
        deleted_at=None,
        role="user",
        is_active=True,
        account_type="regular",
        group_id="group-id",
    )
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", lambda page, key, db: True)
    monkeypatch.setattr(auth_utils, "is_group_accessible_now", lambda group_id, db, is_admin=False: {"accessible": True})
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: (_ for _ in ()).throw(HTTPException(status_code=404, detail="User not found")))
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_audit_password_reset_confirm", lambda **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid or expired password reset token."


def test_confirm_password_reset_audits_missing_token_without_sensitive_material(monkeypatch):
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda purpose, client_ip, user_agent, db=None: False)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, None, "new-password")

    assert exc_info.value.status_code == 400
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] is None
    assert audit_logs[0]["action"] == "PASSWORD_RESET_FAILED"
    assert audit_logs[0]["reason"] == "invalid_or_expired_token"
    assert audit_logs[0]["details"] == {"status": "failure", "status_code": 400}
    assert "new-password" not in str(audit_logs)


def test_confirm_password_reset_throttles_before_lookup(monkeypatch):
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda purpose, client_ip, user_agent, db=None: True)
    monkeypatch.setattr(
        auth_utils,
        "get_password_reset_token_by_hash",
        lambda *args, **kwargs: pytest.fail("throttled confirmation must not look up tokens"),
    )
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert exc_info.value.status_code == 429
    assert len(audit_logs) == 1
    assert audit_logs[0]["action"] == "PASSWORD_RESET_CONFIRM_THROTTLED"
    assert audit_logs[0]["reason"] == "rate_limited"
    assert audit_logs[0]["details"] == {"status": "failure", "status_code": 429, "endpoint": "confirm"}
    assert "reset-token" not in str(audit_logs)
    assert "new-password" not in str(audit_logs)


def test_confirm_password_reset_audits_success_without_sensitive_material(monkeypatch):
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda *args, **kwargs: False)
    token = SimpleNamespace(
        user_id="user-id",
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    user = SimpleNamespace(id="user-id")
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []
    auth_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(auth_utils, "_password_reset_ineligible_status", lambda user, db: None)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "hash_password", lambda password: "hashed-password")
    monkeypatch.setattr(auth_utils, "AuditSessionLocal", lambda: object())
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: auth_logs.append(args))

    users_utils = ModuleType("app.users.utils")
    users_utils._ensure_new_password_differs_from_current = lambda user, password: None
    users_utils._assert_password_policy = lambda password, db: None
    users_utils._commit_password_change_transaction = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "app.users.utils", users_utils)

    response = auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert response == {"status": "success", "reauth_required": True}
    assert auth_logs
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] == "user-id"
    assert audit_logs[0]["action"] == "PASSWORD_RESET"
    assert audit_logs[0]["details"] == {"status": "success", "reauth_required": True}
    assert audit_logs[0]["category"] == "auth"
    assert "reset-token" not in str(audit_logs)
    assert "new-password" not in str(audit_logs)


def test_confirm_password_reset_audits_invalid_token_without_sensitive_material(monkeypatch):
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda *args, **kwargs: False)
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: None)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "AuditSessionLocal", lambda: object())
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert exc_info.value.status_code == 400
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] is None
    assert audit_logs[0]["action"] == "PASSWORD_RESET_FAILED"
    assert audit_logs[0]["reason"] == "invalid_or_expired_token"
    assert audit_logs[0]["details"] == {"status": "failure", "status_code": 400}
    assert "reset-token" not in str(audit_logs)
    assert "new-password" not in str(audit_logs)


def test_confirm_password_reset_audits_atomic_consume_failure_as_invalid_token(monkeypatch):
    monkeypatch.setattr(auth_utils, "_is_password_reset_token_attempt_throttled", lambda *args, **kwargs: False)
    token = SimpleNamespace(
        id="token-id",
        user_id="user-id",
        consumed_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    user = SimpleNamespace(id="user-id")
    request = SimpleNamespace(headers={"User-Agent": "Test Agent"})
    audit_logs = []

    monkeypatch.setattr(auth_utils, "_is_password_reset_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_hash_password_reset_token", lambda raw_token: f"hash:{raw_token}")
    monkeypatch.setattr(auth_utils, "get_password_reset_token_by_hash", lambda db, token_hash: token)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(auth_utils, "_password_reset_ineligible_status", lambda user, db: None)
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "127.0.0.1")
    monkeypatch.setattr(auth_utils, "hash_password", lambda password: "hashed-password")
    monkeypatch.setattr(auth_utils, "AuditSessionLocal", lambda: object())
    monkeypatch.setattr(auth_utils, "create_audit_log", lambda **kwargs: audit_logs.append(kwargs))

    users_utils = ModuleType("app.users.utils")
    users_utils._ensure_new_password_differs_from_current = lambda user, password: None
    users_utils._assert_password_policy = lambda password, db: None
    users_utils._commit_password_change_transaction = lambda *args, **kwargs: (_ for _ in ()).throw(
        HTTPException(status_code=400, detail="Invalid or expired password reset token.")
    )
    monkeypatch.setitem(sys.modules, "app.users.utils", users_utils)

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.confirm_password_reset(object(), object(), request, "reset-token", "new-password")

    assert exc_info.value.status_code == 400
    assert len(audit_logs) == 1
    assert audit_logs[0]["user_id"] == "user-id"
    assert audit_logs[0]["action"] == "PASSWORD_RESET_FAILED"
    assert audit_logs[0]["reason"] == "invalid_or_expired_token"
    assert audit_logs[0]["details"] == {"status": "failure", "status_code": 400}
    assert "reset-token" not in str(audit_logs)
    assert "new-password" not in str(audit_logs)


def test_password_reset_validate_endpoint_uses_post_only():
    router_source = (Path(__file__).resolve().parents[2] / "app" / "auth" / "router.py").read_text()

    assert '@auth_router.post("/password-reset/validate")' in router_source
    assert '@auth_router.get("/password-reset/validate")' not in router_source
