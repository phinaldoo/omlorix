from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.database import Base
from app.groups.models import GroupManager
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import (
    User,
    create_user,
    get_user,
    hard_delete_user,
    soft_delete_user,
    user_exists_by_email,
)
from app.users.schemas import UserCreate, UserPersonalDetails
from app.utils import encryption as encryption_utils
from app.workers.models import DurableWorkerJob


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            # User lifecycle changes verify that group ownership is not
            # stranded, so the focused schema needs the assignment table.
            GroupManager.__table__,
            # Account state changes cancel pending non-lifecycle work in the
            # same transaction.
            DurableWorkerJob.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _fixed_encryption_key(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)


def _user_values(email: str, *, user_id: str) -> dict:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "id": user_id,
        "email": email,
        "group_id": "group-1",
        "hashed_password": "hashed-password",
        "first_name": "Test",
        "last_name": "User",
        "role": "user",
        "settings": deepcopy(DEFAULT_USER_SETTINGS),
        "is_active": True,
        "created_at": created_at,
        "last_active_at": created_at,
    }


def test_user_create_schema_normalizes_email():
    payload = UserCreate(
        email="  Mixed.User@Example.COM ",
        password="CorrectHorseBatteryStaple1!",
        first_name="Mixed",
        last_name="User",
    )

    assert payload.email == "mixed.user@example.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email-address"),
        ("email", "e2e-owner@example.test"),
        ("password", " leading-whitespace"),
        ("password", "x" * 1025),
    ],
)
def test_user_create_schema_rejects_invalid_signup_values(field, value):
    """Modified clients must still pass the server-side signup contract."""

    signup_values = {
        "email": "valid@example.com",
        "password": "CorrectHorseBatteryStaple1!",
        "first_name": "Valid",
        "last_name": "User",
    }
    signup_values[field] = value

    with pytest.raises(ValidationError):
        UserCreate(**signup_values)


def test_user_personal_details_schema_normalizes_email():
    payload = UserPersonalDetails(email="  Profile.User@Example.COM ")

    assert payload.email == "profile.user@example.com"


def test_create_and_lookup_user_use_canonical_email(db_session):
    user = create_user(
        db_session,
        "  Mixed.User@Example.COM ",
        "hashed-password",
        "Mixed",
        "User",
        "user",
        "group-1",
    )

    assert user.email == "mixed.user@example.com"
    assert user.role == "owner"
    assert user_exists_by_email(db_session, " MIXED.user@example.com ")
    assert get_user(db_session, email=" mixed.user@EXAMPLE.com ").id == user.id


def test_only_first_created_user_becomes_owner(db_session):
    first_user = create_user(
        db_session,
        "first@example.com",
        "hashed-password",
        "First",
        "User",
        "pending",
        "group-1",
    )
    second_user = create_user(
        db_session,
        "second@example.com",
        "hashed-password",
        "Second",
        "User",
        "user",
        "group-1",
    )

    assert first_user.role == "owner"
    assert second_user.role == "user"


def test_database_rejects_a_second_owner(db_session):
    first_owner = _user_values("owner-1@example.com", user_id="owner-1")
    first_owner["role"] = "owner"
    db_session.add(User(**first_owner))
    db_session.commit()
    second_owner = _user_values("owner-2@example.com", user_id="owner-2")
    second_owner["role"] = "owner"
    db_session.add(User(**second_owner))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_administrator_soft_deletion_requires_explicit_owner_authorization(db_session):
    """Persistence remains fail-closed while permitting the authorized owner path."""

    owner_values = _user_values("owner@example.com", user_id="owner-1")
    owner_values["role"] = "owner"
    db_session.add(User(**owner_values))
    admin_values = _user_values("admin@example.com", user_id="admin-1")
    admin_values["role"] = "admin"
    db_session.add(User(**admin_values))
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        soft_delete_user(db_session, "admin-1")

    assert exc_info.value.status_code == 409

    deleted_admin = soft_delete_user(
        db_session,
        "admin-1",
        allow_administrative_target=True,
    )
    assert deleted_admin.deleted_at is not None
    assert deleted_admin.is_active is False
    assert deleted_admin.role == "admin"

    with pytest.raises(HTTPException) as owner_error:
        soft_delete_user(
            db_session,
            "owner-1",
            allow_administrative_target=True,
        )

    assert owner_error.value.status_code == 409
    assert owner_error.value.detail == "Cannot delete the owner account."

    with pytest.raises(HTTPException) as hard_owner_error:
        hard_delete_user(
            db_session,
            "owner-1",
            allow_administrative_target=True,
        )

    assert hard_owner_error.value.status_code == 409
    assert hard_owner_error.value.detail == "Cannot delete the owner account."


def test_canonical_email_index_blocks_case_and_whitespace_variants(db_session):
    db_session.execute(User.__table__.insert().values(**_user_values("  Mixed.User@Example.COM ", user_id="user-1")))
    db_session.commit()

    db_session.add(User(**_user_values("mixed.user@example.com", user_id="user-2")))
    with pytest.raises(IntegrityError):
        db_session.commit()
