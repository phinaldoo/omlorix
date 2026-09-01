from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
from app.database import Base
from app.files import router as files_router
from app.files import statistics as file_statistics
from app.files.models import Files
from app.files.statistics import BYTES_PER_GB, get_admin_file_storage_statistics
from app.groups.models import Group
from app.users.models import User


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Group.__table__, User.__table__, Files.__table__])
    Session = sessionmaker(bind=engine)
    return Session()


def _group(group_id: str = "default", *, settings: dict | None = None) -> Group:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Group(
        id=group_id,
        name=group_id.title(),
        settings=settings or {},
        created_at=now,
        updated_at=now,
    )


def _user(user_id: str, *, email: str, group_id: str = "default", role: str = "user") -> User:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return User(
        id=user_id,
        email=email,
        group_id=group_id,
        hashed_password="hashed",
        first_name="Test",
        last_name=user_id,
        role=role,
        is_active=True,
        settings={},
        lock={"is_locked": False, "lock_until": None, "type": "", "reason": ""},
        created_at=now,
        last_active_at=now,
    )


def _file(file_id: str, user_id: str, size: int, *, minutes: int = 0) -> Files:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return Files(
        id=file_id,
        user_id=user_id,
        file_name=f"{file_id}.txt",
        storage_provider="local",
        storage_key=f"{user_id}/{file_id}.txt",
        file_category="document",
        file_type="text/plain",
        file_size=size,
        meta={"origin": "user", "original_filename": f"{file_id}.txt"},
        created_at=created_at,
        last_updated_at=created_at,
    )


def test_user_file_storage_usage_counts_only_owned_files():
    db = _db_session()
    db.add(_group())
    db.add_all([
        _user("user-1", email="one@example.test"),
        _user("user-2", email="two@example.test"),
    ])
    db.add_all([
        _file("owned-1", "user-1", 100, minutes=1),
        _file("owned-2", "user-1", 200, minutes=2),
        _file("other-1", "user-2", 900, minutes=3),
    ])
    db.commit()

    payload = files_router.get_file_storage_usage_route(user=SimpleNamespace(id="user-1"), db=db)

    assert payload["user_id"] == "user-1"
    assert payload["file_count"] == 2
    assert payload["storage_bytes"] == 300
    assert payload["file_count_limit"] == 100
    assert payload["storage_bytes_limit"] == 5 * BYTES_PER_GB
    assert payload["uploads_allowed"] is True


def test_admin_file_storage_statistics_aggregates_and_applies_quota_limits():
    db = _db_session()
    db.add(_group())
    db.add(
        _group(
            "restricted",
            settings={
                "files": {
                    "allow_file_uploads": False,
                    "max_files_upload_count": 3,
                    "max_user_files_size_gb": 1,
                }
            },
        )
    )
    db.add_all([
        _user("user-1", email="one@example.test"),
        _user("user-2", email="two@example.test", group_id="restricted"),
    ])
    db.add_all([
        _file("one-a", "user-1", 100, minutes=1),
        _file("one-b", "user-1", 200, minutes=2),
        _file("two-a", "user-2", 900, minutes=3),
    ])
    db.commit()

    payload = get_admin_file_storage_statistics(
        db,
        limit=10,
        offset=0,
        sort_field="storage_bytes",
        sort_direction="desc",
    )

    assert payload["summary"] == {
        "total_files": 3,
        "total_storage_bytes": 1200,
        "users_with_files": 2,
    }
    assert [item["user_id"] for item in payload["items"]] == ["user-2", "user-1"]
    assert payload["items"][0]["storage_bytes"] == 900
    assert payload["items"][0]["uploads_allowed"] is False
    assert payload["items"][0]["file_count_limit"] == 3
    assert payload["items"][0]["storage_bytes_limit"] == BYTES_PER_GB
    assert payload["items"][1]["file_count"] == 2
    assert payload["has_more"] is False
    assert not {
        "group_id",
        "role",
        "is_active",
        "deleted_at",
    }.intersection(payload["items"][0])


def test_admin_file_storage_route_paginates_and_audits(monkeypatch):
    db = _db_session()
    audit_calls: list[dict] = []
    db.add(_group())
    db.add_all([
        _user("user-1", email="one@example.test"),
        _user("user-2", email="two@example.test"),
    ])
    db.add_all([
        _file("one-a", "user-1", 100, minutes=1),
        _file("two-a", "user-2", 200, minutes=2),
    ])
    db.commit()
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))
    monkeypatch.setattr(admin_router, "get_audit_request_ip", lambda *_args, **_kwargs: "198.51.100.10")

    payload = admin_router.admin_get_file_storage_statistics_route(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        search=None,
        sort_field="email",
        sort_direction="asc",
        limit=1,
        offset=0,
        db=db,
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["has_more"] is True
    assert len(payload["items"]) == 1
    assert audit_calls[0]["action"] == "FILE_STORAGE_STATISTICS_VIEWED"
    assert audit_calls[0]["details"]["has_search"] is False
    assert audit_calls[0]["details"]["sort_field"] == "email"


def test_file_storage_usage_falls_back_when_group_quota_values_are_invalid():
    db = _db_session()
    db.add(
        _group(
            "broken",
            settings={
                "files": {
                    "allow_file_uploads": "sometimes",
                    "max_files_upload_count": "many",
                    "max_user_files_size_gb": "large",
                }
            },
        )
    )
    db.add(_user("user-1", email="one@example.test", group_id="broken"))
    db.add(_file("owned-1", "user-1", 100, minutes=1))
    db.commit()

    payload = files_router.get_file_storage_usage_route(user=SimpleNamespace(id="user-1"), db=db)
    admin_payload = get_admin_file_storage_statistics(db, limit=10, offset=0)

    assert payload["uploads_allowed"] is True
    assert payload["file_count_limit"] == 100
    assert payload["storage_bytes_limit"] == 5 * BYTES_PER_GB
    assert admin_payload["items"][0]["file_count_limit"] == 100


def test_admin_file_storage_search_escapes_like_wildcards_instead_of_stripping_them():
    db = _db_session()
    db.add(_group())
    db.add_all([
        _user("user_1", email="team_one@example.test"),
        _user("user-2", email="teamtwo@example.test"),
    ])
    db.add_all([
        _file("one-a", "user_1", 100, minutes=1),
        _file("two-a", "user-2", 200, minutes=2),
    ])
    db.commit()

    payload = get_admin_file_storage_statistics(db, limit=10, offset=0, search="team_one")

    assert payload["total"] == 1
    assert payload["items"][0]["email"] == "team_one@example.test"


def test_admin_file_storage_statistics_caches_quota_settings_by_group(monkeypatch):
    db = _db_session()
    db.add(_group())
    db.add(_group("shared"))
    db.add_all([
        _user("user-1", email="one@example.test", group_id="shared"),
        _user("user-2", email="two@example.test", group_id="shared"),
    ])
    db.add_all([
        _file("one-a", "user-1", 100, minutes=1),
        _file("two-a", "user-2", 200, minutes=2),
    ])
    db.commit()
    calls: list[str] = []
    original = file_statistics.get_group_settings

    def counted_get_group_settings(group_id, db_session):
        calls.append(group_id)
        return original(group_id, db_session)

    monkeypatch.setattr(file_statistics, "get_group_settings", counted_get_group_settings)

    payload = get_admin_file_storage_statistics(db, limit=10, offset=0)

    assert payload["total"] == 2
    assert calls == ["shared"]
