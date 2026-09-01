from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
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


from app.database import Base
from app.projects.models import Project, ProjectMember, update_project_shared
from app.users.models import User
from app.utils import encryption as encryption_utils


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user(user_id: str, email: str) -> User:
    now = _now()
    return User(
        id=user_id,
        email=email,
        group_id="group-1",
        account_type="regular",
        hashed_password="hash",
        first_name="Test",
        last_name="User",
        role="user",
        settings={},
        created_at=now,
        last_active_at=now,
    )


def _project(project_id: str, owner_user_id: str, *, separate_memory_enabled: bool = False) -> Project:
    now = _now()
    return Project(
        id=project_id,
        user_id=owner_user_id,
        title="Project",
        settings={
            "icon": "",
            "icon_color": "",
            "system_instruction": "",
            "separate_memory_enabled": separate_memory_enabled,
        },
        created_at=now,
        last_updated_at=now,
    )


@pytest.fixture()
def db_session(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, Project.__table__, ProjectMember.__table__])
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _add_shared_project(db, *, separate_memory_enabled: bool = False):
    owner = _user("owner-1", "owner@example.com")
    member = _user("member-1", "member@example.com")
    project = _project("project-1", owner.id, separate_memory_enabled=separate_memory_enabled)
    db.add_all([owner, member])
    db.commit()
    db.add(project)
    db.commit()
    db.add(ProjectMember(project_id=project.id, user_id=member.id, role="member"))
    db.commit()
    return owner, member, project


def test_project_member_cannot_enable_separate_project_memory(db_session):
    _owner, member, project = _add_shared_project(db_session)

    with pytest.raises(HTTPException) as exc_info:
        update_project_shared(
            db_session,
            member.id,
            project.id,
            settings={"separate_memory_enabled": True},
        )

    assert exc_info.value.status_code == 403
    db_session.refresh(project)
    assert project.settings["separate_memory_enabled"] is False


def test_project_owner_can_enable_separate_project_memory(db_session):
    owner, _member, project = _add_shared_project(db_session)

    updated_project = update_project_shared(
        db_session,
        owner.id,
        project.id,
        settings={"separate_memory_enabled": True},
    )

    assert updated_project.settings["separate_memory_enabled"] is True
    db_session.refresh(project)
    assert project.settings["separate_memory_enabled"] is True


def test_project_member_can_update_non_memory_settings(db_session):
    _owner, member, project = _add_shared_project(db_session, separate_memory_enabled=True)

    updated_project = update_project_shared(
        db_session,
        member.id,
        project.id,
        title="Member edit",
        settings={"icon": "folder"},
    )

    assert updated_project.title == "Member edit"
    assert updated_project.settings["icon"] == "folder"
    assert updated_project.settings["separate_memory_enabled"] is True
