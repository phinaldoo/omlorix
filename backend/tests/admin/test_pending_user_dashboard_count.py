from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
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

from app.admin.settings import router as admin_router
from app.admin.settings import utils as admin_utils
from app.database import Base
from app.llm.models import LLMProvider, Models
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User, get_pending_user_count
from app.utils import encryption as encryption_utils


@pytest.fixture()
def db_session(monkeypatch):
    """Provide the minimal persisted schema needed by the dashboard helper."""
    monkeypatch.setattr(
        encryption_utils,
        "_ENCRYPTION_KEY",
        Fernet.generate_key().decode("utf-8"),
    )
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            LLMProvider.__table__,
            Models.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _pending_user(
    *,
    user_id: str,
    email: str,
    deleted_at: datetime | None = None,
    is_active: bool = True,
) -> User:
    """Build a complete pending-user row for the focused persistence test."""
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    return User(
        id=user_id,
        email=email,
        group_id="group-1",
        hashed_password="hashed-password",
        first_name="Pending",
        last_name="User",
        role="pending",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=is_active,
        deleted_at=deleted_at,
        created_at=now,
        last_active_at=now,
    )


def test_dashboard_pending_count_excludes_soft_deleted_users(db_session, monkeypatch):
    """Keep live pending users in the count while omitting soft-deleted rows."""
    db_session.add_all(
        [
            _pending_user(
                user_id="pending-active",
                email="pending-active@example.com",
            ),
            _pending_user(
                user_id="pending-deleted",
                email="pending-deleted@example.com",
                deleted_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
                is_active=False,
            ),
        ]
    )
    db_session.commit()

    # Exercise the model query directly so the regression cannot be hidden by
    # endpoint mocks or response shaping.
    assert get_pending_user_count(db_session) == 1

    # Isolate unrelated dashboard cards while retaining the real dashboard
    # helper and its pending-user query.
    monkeypatch.setattr(admin_utils, "get_active_user_count", lambda _db: 0)
    monkeypatch.setattr(
        admin_utils,
        "get_peak_concurrent_users_last_week",
        lambda _db: {
            "max_concurrent_users_last_week": 0,
            "is_partial_window": True,
        },
    )
    monkeypatch.setattr(
        admin_utils,
        "get_llm_provider_status_summary",
        lambda _db: (True, 0),
    )
    monkeypatch.setattr(admin_utils, "get_admin_notifications", lambda _db: [])
    monkeypatch.setattr(admin_utils, "get_settings_page", lambda _db, _page: None)
    monkeypatch.setattr(
        admin_utils,
        "get_models_elevated_errors_summary",
        lambda _db: (True, 0),
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, _db: "198.51.100.10",
    )

    response = admin_router.admin_get_settings_dashboard_data(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=db_session,
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response["pending_user_count"] == 1
