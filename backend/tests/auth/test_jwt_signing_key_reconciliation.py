from __future__ import annotations

import hashlib
import secrets
import warnings
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.fernet import Fernet
from jwt.warnings import InsecureKeyLengthWarning
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import jwt_material
from app.auth.models import AuthenticationSigningKeyState
from app.database import Base
from app.logging import models as logging_models
from app.settings import models as settings_models
from app.settings.models import Settings


def _session():
    """Create the smallest database needed by signing-key reconciliation."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[AuthenticationSigningKeyState.__table__],
    )
    return sessionmaker(bind=engine)()


def test_jwt_material_comes_only_from_environment(monkeypatch: pytest.MonkeyPatch):
    """Runtime signing material must not depend on an application database read."""
    secret = "e" * 64
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    jwt_material.get_jwt_material.cache_clear()

    assert jwt_material.get_jwt_material() == (secret, "HS512")


def test_jwt_material_enforces_utf8_byte_length(monkeypatch: pytest.MonkeyPatch):
    """The HS512 boundary counts encoded bytes rather than Unicode characters."""
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 63)
    jwt_material.get_jwt_material.cache_clear()
    with pytest.raises(RuntimeError, match="at least 64 bytes"):
        jwt_material.get_jwt_material()

    multibyte_secret = "€" * 22
    monkeypatch.setenv("JWT_SECRET_KEY", multibyte_secret)
    jwt_material.get_jwt_material.cache_clear()
    assert len(multibyte_secret) < 64
    assert len(multibyte_secret.encode("utf-8")) >= 64
    assert jwt_material.get_jwt_material() == (multibyte_secret, "HS512")


def test_freshly_generated_jwt_round_trip_has_no_insecure_key_warning(
    monkeypatch: pytest.MonkeyPatch,
):
    """A new 64-random-byte configuration is warning-free with PyJWT HS512."""
    generated_secret = secrets.token_urlsafe(jwt_material.JWT_SECRET_MIN_BYTES)
    monkeypatch.setenv("JWT_SECRET_KEY", generated_secret)
    jwt_material.get_jwt_material.cache_clear()
    secret, algorithm = jwt_material.get_jwt_material()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        token = jwt.encode({"sub": "fresh-config"}, secret, algorithm=algorithm)
        payload = jwt.decode(token, secret, algorithms=[algorithm])

    assert payload["sub"] == "fresh-config"
    assert not any(
        issubclass(item.category, InsecureKeyLengthWarning) for item in caught
    )


def test_settings_startup_prunes_database_jwt_key(monkeypatch: pytest.MonkeyPatch):
    """Application settings must not retain an old JWT signing-key value."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Settings.__table__])
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(
        settings_models,
        "DEFAULT_SETTINGS",
        {"secret": {"passkey_padding_secret": ""}},
    )
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    db.add(
        Settings(
            page_name="secret",
            data={"secret_key": "database-owned-key", "passkey_padding_secret": ""},
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    try:
        settings_models.initialize_settings(db)
        stored = db.query(Settings).filter(Settings.page_name == "secret").one()
        assert "secret_key" not in stored.data
    finally:
        db.close()


def test_production_requires_independent_ip_hash_salt(monkeypatch: pytest.MonkeyPatch):
    """Audit IP hashing must not silently borrow authentication signing material."""
    monkeypatch.setenv("MODE", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-material-must-not-be-used-as-ip-salt")
    monkeypatch.setattr(logging_models, "_IP_HASH_SALT", "")

    with pytest.raises(RuntimeError, match="LOG_IP_HASH_SALT"):
        logging_models.validate_ip_hash_salt_configuration()

    monkeypatch.setattr(logging_models, "_IP_HASH_SALT", "independent-audit-ip-salt")
    logging_models.validate_ip_hash_salt_configuration()


def test_first_fingerprint_adoption_revokes_sessions(monkeypatch: pytest.MonkeyPatch):
    """The first environment authority adoption cannot trust pre-existing sessions."""
    secret = "f" * 64
    events: list[str] = []
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    jwt_material.get_jwt_material.cache_clear()
    monkeypatch.setattr(
        jwt_material,
        "delete_authentication_all",
        lambda _db, *, commit: events.append(f"delete:{commit}"),
    )
    monkeypatch.setattr(jwt_material, "revoke_all_sessions", lambda: events.append("revoke-cache"))

    db = _session()
    try:
        assert jwt_material.reconcile_jwt_signing_key(db) is True
        state = db.get(AuthenticationSigningKeyState, 1)
        assert state is not None
        assert state.fingerprint == hashlib.sha256(secret.encode("utf-8")).hexdigest()
        assert events == ["delete:False", "revoke-cache"]
    finally:
        db.close()


def test_unchanged_fingerprint_keeps_sessions(monkeypatch: pytest.MonkeyPatch):
    """Ordinary restarts must not sign users out when the operator key is unchanged."""
    secret = "s" * 64
    events: list[str] = []
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    jwt_material.get_jwt_material.cache_clear()
    monkeypatch.setattr(
        jwt_material,
        "delete_authentication_all",
        lambda _db, *, commit: events.append(f"delete:{commit}"),
    )
    monkeypatch.setattr(jwt_material, "revoke_all_sessions", lambda: events.append("revoke-cache"))

    db = _session()
    try:
        assert jwt_material.reconcile_jwt_signing_key(db) is True
        events.clear()
        assert jwt_material.reconcile_jwt_signing_key(db) is False
        assert events == []
    finally:
        db.close()


def test_changed_fingerprint_revokes_sessions_once(monkeypatch: pytest.MonkeyPatch):
    """An operator key change must atomically advance state and revoke all sessions."""
    first_secret = "o" * 64
    second_secret = "n" * 64
    events: list[str] = []
    monkeypatch.setattr(
        jwt_material,
        "delete_authentication_all",
        lambda _db, *, commit: events.append(f"delete:{commit}"),
    )
    monkeypatch.setattr(jwt_material, "revoke_all_sessions", lambda: events.append("revoke-cache"))

    db = _session()
    try:
        monkeypatch.setenv("JWT_SECRET_KEY", first_secret)
        jwt_material.get_jwt_material.cache_clear()
        assert jwt_material.reconcile_jwt_signing_key(db) is True
        events.clear()

        monkeypatch.setenv("JWT_SECRET_KEY", second_secret)
        jwt_material.get_jwt_material.cache_clear()
        assert jwt_material.reconcile_jwt_signing_key(db) is True
        state = db.get(AuthenticationSigningKeyState, 1)
        assert state.fingerprint == hashlib.sha256(second_secret.encode("utf-8")).hexdigest()
        assert events == ["delete:False", "revoke-cache"]
    finally:
        db.close()
