from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
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

from app.auth.models import (
    Authentication,
    NativeAuthGrant,
    PasswordResetToken,
    PendingAuthAction,
    WebAuthnChallenge,
)
from app.email.models import EmailOutbox, PendingEmailChange, TrustedDeviceNotification
from app.database import Base
from app.users import utils as user_utils
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User
from app.utils import encryption as encryption_utils
from app.workers.models import DurableWorkerJob


def _insert_invalid_authentication(db, *, user_id: str) -> None:
    """Insert stale ciphertext without invoking the encrypted column binder."""
    now = datetime(2026, 7, 30, 12, 0)
    db.execute(
        text(
            """
            INSERT INTO authentication (
                id,
                user_id,
                device_info,
                ip_address,
                access_token,
                refresh_token,
                access_token_hash,
                refresh_token_hash,
                created_at,
                last_active_at
            )
            VALUES (
                :id,
                :user_id,
                :device_info,
                :ip_address,
                :access_token,
                :refresh_token,
                :access_token_hash,
                :refresh_token_hash,
                :created_at,
                :last_active_at
            )
            """
        ),
        {
            "id": "auth-stale",
            "user_id": user_id,
            "device_info": "Desktop browser",
            "ip_address": "203.0.113.10",
            "access_token": "not-valid-encrypted-data",
            "refresh_token": "also-not-valid-encrypted-data",
            "access_token_hash": "access-hash",
            "refresh_token_hash": "refresh-hash",
            "created_at": now,
            "last_active_at": now,
        },
    )
    db.commit()


def test_soft_delete_removes_authentication_without_decrypting_tokens(monkeypatch):
    """Soft deletion succeeds even when a stored session has stale ciphertext."""
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)

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
                        TrustedDeviceNotification.__table__,
                        DurableWorkerJob.__table__,
            ],
    )
    db = sessionmaker(bind=engine)()

    revoked_user_ids: list[str] = []
    monkeypatch.setattr(
        user_utils,
        "revoke_user_sessions",
        lambda user_id: revoked_user_ids.append(user_id),
    )
    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: {
            ("users", "user_deletion_mode"): "retain",
            ("security", "auth_logs_retention_after_user_delete_mode"): "retain",
            ("security", "audit_logs_retention_after_user_delete_mode"): "retain",
        }.get((page, key)),
    )
    monkeypatch.setattr(
        user_utils,
        "get_auth_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        user_utils,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        user_utils,
        "cancel_auth_log_deletions_for_user",
        lambda _db_log, _user_id: None,
    )
    monkeypatch.setattr(
        user_utils,
        "cancel_audit_log_deletions_for_user",
        lambda _db_log, _user_id: None,
    )

    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    user = User(
        id="pending-user",
        email="pending-user@example.com",
        group_id="group-1",
        hashed_password="hashed-password",
        first_name="Pending",
        last_name="User",
        role="pending",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=True,
        created_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.commit()
    _insert_invalid_authentication(db, user_id=user.id)

    try:
        response = user_utils.delete_user(
            db,
            object(),
            user.id,
            check_self_deletion=False,
        )

        db.refresh(user)
        assert response["status"] == "success"
        assert user.deleted_at is not None
        assert user.is_active is False
        assert (
            db.query(Authentication)
            .filter(Authentication.user_id == user.id)
            .count()
            == 0
        )
        assert revoked_user_ids == [user.id]
    finally:
        db.close()
