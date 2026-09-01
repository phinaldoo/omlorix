from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import models as auth_models
from app.auth.models import Authentication, RefreshTokenHistory
from app.database import Base
from app.users.models import User
from app.utils import encryption


def _session_with_refresh_family_tables():
    """Create the minimal SQLite schema used by refresh-family tests."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, Authentication.__table__, RefreshTokenHistory.__table__],
    )
    return sessionmaker(bind=engine)()


def _authentication(session_id: str, refresh_token: str) -> Authentication:
    """Build one device authentication row with a distinct token pair."""

    now = datetime.now(timezone.utc)
    return Authentication(
        id=session_id,
        user_id="user-1",
        device_info="Browser",
        ip_address="203.0.113.0/24",
        access_token=f"access-{session_id}",
        refresh_token=refresh_token,
        access_token_hash=auth_models._token_hash(f"access-{session_id}"),
        refresh_token_hash=auth_models._token_hash(refresh_token),
        created_at=now,
        last_active_at=now,
    )


def test_consumed_refresh_token_is_race_then_session_scoped_reuse(monkeypatch):
    """Rotation history distinguishes concurrency from a confirmed replay."""

    encryption._ENCRYPTION_KEY = Fernet.generate_key()
    encryption._CIPHER_SUITE = None
    monkeypatch.setattr(auth_models, "rotate_cached_session_tokens", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_models, "revoke_token_digests", lambda *args, **kwargs: None)

    db = _session_with_refresh_family_tables()
    try:
        affected = _authentication("session-1", "refresh-old")
        unaffected = _authentication("session-2", "refresh-other-device")
        db.add_all([affected, unaffected])
        db.commit()

        auth_models.rotate_authentication_tokens(
            db,
            "user-1",
            "refresh-old",
            "access-new",
            "refresh-new",
            session_id="session-1",
            previous_refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=70),
        )
        history = db.query(RefreshTokenHistory).one()

        race = auth_models.resolve_refresh_token_for_rotation(
            db,
            user_id="user-1",
            refresh_token="refresh-old",
            session_id="session-1",
            now=history.rotated_at + timedelta(seconds=2),
        )
        assert race.state == "race"
        assert race.authentication.id == "session-1"

        replay = auth_models.resolve_refresh_token_for_rotation(
            db,
            user_id="user-1",
            refresh_token="refresh-old",
            session_id="session-1",
            now=history.rotated_at + timedelta(seconds=6),
        )
        assert replay.state == "reused"

        auth_models.delete_authentication(db, id=replay.authentication.id, user_id="user-1")
        remaining_ids = {
            row.id for row in db.query(Authentication).filter(Authentication.user_id == "user-1").all()
        }
        assert remaining_ids == {"session-2"}
    finally:
        db.close()


def test_resolution_samples_race_time_after_locked_history_lookup(monkeypatch):
    """A waiter must compare against time observed after the winning rotation."""

    events = []
    observed_at = datetime.now(timezone.utc)
    authentication = SimpleNamespace(
        id="session-1",
        refresh_token_hash=auth_models._token_hash("refresh-new"),
    )
    history = SimpleNamespace(rotated_at=observed_at - timedelta(seconds=1))

    class RecordingDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            events.append("clock")
            return observed_at

    class RecordingQuery:
        def __init__(self, result, event):
            self.result = result
            self.event = event

        def filter(self, *args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            events.append(self.event)
            return self.result

    class RecordingDb:
        def query(self, model):
            if model is Authentication:
                return RecordingQuery(authentication, "authentication_lock")
            if model is RefreshTokenHistory:
                return RecordingQuery(history, "history_lookup")
            raise AssertionError(f"Unexpected model query: {model}")

    monkeypatch.setattr(auth_models, "datetime", RecordingDateTime)

    resolution = auth_models.resolve_refresh_token_for_rotation(
        RecordingDb(),
        user_id="user-1",
        refresh_token="refresh-old",
        session_id="session-1",
    )

    assert resolution.state == "race"
    assert events == ["authentication_lock", "history_lookup", "clock"]


def test_unknown_refresh_token_never_resolves_as_reuse(monkeypatch):
    """A signed but unrecorded token is not sufficient evidence of replay."""

    encryption._ENCRYPTION_KEY = Fernet.generate_key()
    encryption._CIPHER_SUITE = None
    db = _session_with_refresh_family_tables()
    try:
        db.add(_authentication("session-1", "refresh-current"))
        db.commit()

        resolution = auth_models.resolve_refresh_token_for_rotation(
            db,
            user_id="user-1",
            refresh_token="refresh-never-issued",
            session_id="session-1",
        )

        assert resolution.state == "unknown"
        assert db.query(Authentication).count() == 1
    finally:
        db.close()


def test_rotation_retains_consumed_hash_for_full_race_grace_without_token_expiry(monkeypatch):
    """Back-to-back rotations cannot prune the previous race marker early."""

    encryption._ENCRYPTION_KEY = Fernet.generate_key()
    encryption._CIPHER_SUITE = None
    monkeypatch.setattr(auth_models, "rotate_cached_session_tokens", lambda *args, **kwargs: None)

    db = _session_with_refresh_family_tables()
    try:
        db.add(_authentication("session-1", "refresh-first"))
        db.commit()

        auth_models.rotate_authentication_tokens(
            db,
            "user-1",
            "refresh-first",
            "access-second",
            "refresh-second",
            session_id="session-1",
        )
        auth_models.rotate_authentication_tokens(
            db,
            "user-1",
            "refresh-second",
            "access-third",
            "refresh-third",
            session_id="session-1",
        )

        history = db.query(RefreshTokenHistory).order_by(RefreshTokenHistory.rotated_at).all()
        assert len(history) == 2
        for consumed in history:
            retained_for = auth_models._normalize_utc(consumed.expires_at) - auth_models._normalize_utc(
                consumed.rotated_at
            )
            assert retained_for >= auth_models.REFRESH_TOKEN_RACE_GRACE
    finally:
        db.close()


def test_rotation_preserves_caller_expiry_longer_than_race_grace(monkeypatch):
    """Normal JWT expiry remains the history lifetime when it is longer."""

    encryption._ENCRYPTION_KEY = Fernet.generate_key()
    encryption._CIPHER_SUITE = None
    monkeypatch.setattr(auth_models, "rotate_cached_session_tokens", lambda *args, **kwargs: None)

    db = _session_with_refresh_family_tables()
    try:
        db.add(_authentication("session-1", "refresh-first"))
        db.commit()
        supplied_expiry = datetime.now(timezone.utc) + timedelta(days=7)

        auth_models.rotate_authentication_tokens(
            db,
            "user-1",
            "refresh-first",
            "access-second",
            "refresh-second",
            session_id="session-1",
            previous_refresh_expires_at=supplied_expiry,
        )

        history = db.query(RefreshTokenHistory).one()
        assert auth_models._normalize_utc(history.expires_at) == supplied_expiry
    finally:
        db.close()
