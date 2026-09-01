import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections.models import (
    ConnectionOAuthState,
    consume_connection_oauth_audit_subject,
    consume_connection_oauth_state,
    resolve_connection_oauth_audit_subject,
    save_connection_oauth_state,
)
from app.database import Base
from app.utils import encryption


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[ConnectionOAuthState.__table__])
    Session = sessionmaker(bind=engine)
    return Session()


def _configure_test_encryption():
    encryption._ENCRYPTION_KEY = Fernet.generate_key()
    encryption._CIPHER_SUITE = None


def test_consume_connection_oauth_state_returns_unexpired_state_and_deletes_record():
    _configure_test_encryption()
    db = _db_session()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    save_connection_oauth_state(
        db,
        state="state-valid",
        provider="slack",
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://example.com/callback",
        payload={"scopes": ["channels:read"]},
        secrets={"client_id": "client-123"},
        expires_at=expires_at,
    )

    payload = consume_connection_oauth_state(db, "state-valid")

    assert payload is not None
    assert payload["provider"] == "slack"
    assert payload["user_id"] == "user-1"
    assert payload["redirect_uri"] == "https://example.com/callback"
    assert payload["payload"] == {"scopes": ["channels:read"]}
    assert payload["secrets"]["client_id"] == "client-123"
    assert db.query(ConnectionOAuthState).count() == 0


def test_consume_connection_oauth_state_rejects_expired_state_and_deletes_record():
    _configure_test_encryption()
    db = _db_session()
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    save_connection_oauth_state(
        db,
        state="state-expired",
        provider="google_drive",
        user_id="user-2",
        return_path="/workspace/connections",
        redirect_uri="https://example.com/callback",
        expires_at=expires_at,
    )

    payload = consume_connection_oauth_state(db, "state-expired")

    assert payload is None
    assert db.query(ConnectionOAuthState).count() == 0


def test_oauth_audit_subject_resolves_only_matching_valid_owner_and_provider():
    _configure_test_encryption()
    db = _db_session()
    save_connection_oauth_state(
        db,
        state="state-valid",
        provider="slack",
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://example.com/callback",
        secrets={"client_secret": "must-not-be-returned"},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    assert resolve_connection_oauth_audit_subject(
        db,
        state="state-valid",
        provider="github",
    ) is None
    subject = resolve_connection_oauth_audit_subject(
        db,
        state="state-valid",
        provider="slack",
    )

    assert subject == {"user_id": "user-1", "provider": "slack"}
    assert "state-valid" not in repr(subject)
    assert "must-not-be-returned" not in repr(subject)
    assert db.query(ConnectionOAuthState).count() == 1


def test_oauth_audit_subject_consumption_is_provider_bound_and_one_time():
    _configure_test_encryption()
    db = _db_session()
    save_connection_oauth_state(
        db,
        state="state-valid",
        provider="google_drive",
        user_id="user-2",
        return_path="/workspace/connections",
        redirect_uri="https://example.com/callback",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    assert consume_connection_oauth_audit_subject(
        db,
        state="state-valid",
        provider="slack",
    ) is None
    assert db.query(ConnectionOAuthState).count() == 1

    assert consume_connection_oauth_audit_subject(
        db,
        state="state-valid",
        provider="google_drive",
    ) == {"user_id": "user-2", "provider": "google_drive"}
    assert consume_connection_oauth_audit_subject(
        db,
        state="state-valid",
        provider="google_drive",
    ) is None
    assert db.query(ConnectionOAuthState).count() == 0


def test_expired_oauth_audit_subject_is_cleared_without_attribution():
    _configure_test_encryption()
    db = _db_session()
    save_connection_oauth_state(
        db,
        state="state-expired",
        provider="google_drive",
        user_id="user-2",
        return_path="/workspace/connections",
        redirect_uri="https://example.com/callback",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert consume_connection_oauth_audit_subject(
        db,
        state="state-expired",
        provider="google_drive",
    ) is None
    assert db.query(ConnectionOAuthState).count() == 0
