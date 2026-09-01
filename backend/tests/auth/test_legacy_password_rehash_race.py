from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import utils as auth_utils
from app.database import Base
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User
from app.utils import encryption as encryption_utils


def _session(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[User.__table__])
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    db.add(
        User(
            id="legacy-user",
            email="legacy@example.com",
            group_id="group-1",
            hashed_password="verified-legacy-hash",
            first_name="Legacy",
            last_name="User",
            role="user",
            settings=deepcopy(DEFAULT_USER_SETTINGS),
            is_active=True,
            created_at=now,
            last_active_at=now,
        )
    )
    db.commit()
    return db


def _request():
    return SimpleNamespace(headers={"User-Agent": "pytest"})


def _configure_signin_dependencies(monkeypatch, user, *, enabled=True):
    monkeypatch.setattr(
        auth_utils,
        "_client_ip_from_request",
        lambda *_args: "203.0.113.10",
    )
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda *_args: False)
    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: (
            enabled if (page, key) == ("login_general", "enable_signin") else False
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_find_user_for_signin_identifier",
        lambda *_args: user,
    )
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *_args: None)
    monkeypatch.setattr(
        auth_utils,
        "record_auth_login_attempt_metric",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(auth_utils, "hash_password", lambda _password: "migrated-hash")


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    (
        ("hashed_password", "concurrent-reset-hash"),
        ("email", "changed@example.com"),
    ),
)
def test_signin_legacy_rehash_never_overwrites_changed_identity(
    monkeypatch,
    changed_field,
    changed_value,
):
    db = _session(monkeypatch)
    try:
        user = db.query(User).filter(User.id == "legacy-user").one()
        _configure_signin_dependencies(monkeypatch, user)

        def verify_after_concurrent_change(_password, _verified_hash):
            db.query(User).filter(User.id == user.id).update(
                {getattr(User, changed_field): changed_value},
                synchronize_session=False,
            )
            db.commit()
            return True, True

        monkeypatch.setattr(
            auth_utils,
            "verify_password_with_migration",
            verify_after_concurrent_change,
        )
        monkeypatch.setattr(
            auth_utils,
            "_complete_signin_for_user",
            lambda *_args, **_kwargs: pytest.fail(
                "a stale legacy-password proof must not continue to 2FA or session issuance"
            ),
        )

        result = auth_utils.signin(
            db,
            object(),
            _request(),
            SimpleNamespace(email="legacy@example.com", password="old-password"),
            object(),
        )

        db.expire_all()
        current = db.query(User).filter(User.id == "legacy-user").one()
        assert result == {"status": "InvalidCredentials"}
        assert getattr(current, changed_field) == changed_value
        assert current.hashed_password != "migrated-hash"
    finally:
        db.close()


def test_signin_legacy_rehash_binds_session_to_preverification_identity(monkeypatch):
    db = _session(monkeypatch)
    try:
        user = db.query(User).filter(User.id == "legacy-user").one()
        _configure_signin_dependencies(monkeypatch, user)
        completed = []
        monkeypatch.setattr(
            auth_utils,
            "verify_password_with_migration",
            lambda *_args: (True, True),
        )
        monkeypatch.setattr(
            auth_utils,
            "_complete_signin_for_user",
            lambda *_args, **kwargs: completed.append(kwargs) or {"status": "success"},
        )

        result = auth_utils.signin(
            db,
            object(),
            _request(),
            SimpleNamespace(email="legacy@example.com", password="old-password"),
            object(),
        )

        db.expire_all()
        current = db.query(User).filter(User.id == "legacy-user").one()
        assert result == {"status": "success"}
        assert current.hashed_password == "migrated-hash"
        assert completed[0]["password_proof_binding"] == (
            "legacy@example.com",
            "migrated-hash",
        )
    finally:
        db.close()
