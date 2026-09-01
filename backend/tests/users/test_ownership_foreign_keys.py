from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats.models import Chats
from app.database import Base
from app.files.models import Files
from app.memories.models import Memory
from app.projects.models import Project, ProjectMember
import app.utils.sqlalchemy_encryption as sqlalchemy_encryption
from app.users.models import User


@pytest.fixture(autouse=True)
def _disable_encryption(monkeypatch):
    monkeypatch.setattr(sqlalchemy_encryption, "encrypt_value", lambda value: value)
    monkeypatch.setattr(sqlalchemy_encryption, "decrypt_value", lambda value: value)


def _project_settings() -> dict[str, object]:
    return {
        "icon": "",
        "icon_color": "",
        "system_instruction": "",
        "separate_memory_enabled": False,
    }


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


def _project(project_id: str, owner_user_id: str) -> Project:
    now = _now()
    return Project(
        id=project_id,
        user_id=owner_user_id,
        title="Project",
        settings=_project_settings(),
        created_at=now,
        last_updated_at=now,
    )


def _chat(chat_id: str, owner_user_id: str, project_id: str | None) -> Chats:
    now = _now()
    return Chats(
        id=chat_id,
        user_id=owner_user_id,
        project_id=project_id,
        title="Chat",
        meta={"status": "normal"},
        created_at=now,
        last_updated_at=now,
    )


def _file(file_id: str, owner_user_id: str, project_id: str | None) -> Files:
    now = _now()
    return Files(
        id=file_id,
        user_id=owner_user_id,
        file_name="file.txt",
        storage_provider="local",
        storage_key=f"{owner_user_id}/file.txt",
        file_category="document",
        file_type="text/plain",
        file_size=1,
        project_id=project_id,
        created_at=now,
        last_updated_at=now,
    )


def _session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Project.__table__,
            Chats.__table__,
            Files.__table__,
            ProjectMember.__table__,
            Memory.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_user_delete_cascades_owned_projects_chats_files_and_memberships():
    db = _session()
    owner = _user("owner-1", "owner@example.com")
    member = _user("member-1", "member@example.com")
    project = _project("project-1", owner.id)

    db.add_all([owner, member])
    db.commit()

    db.add(project)
    db.commit()

    db.add_all(
        [
            _chat("chat-1", owner.id, project.id),
            _file("file-1", owner.id, project.id),
            ProjectMember(project_id=project.id, user_id=member.id, role="member"),
        ]
    )
    db.commit()

    db.delete(owner)
    db.commit()

    assert db.query(User).filter(User.id == member.id).count() == 1
    assert db.query(Project).count() == 0
    assert db.query(Chats).count() == 0
    assert db.query(Files).count() == 0
    assert db.query(ProjectMember).count() == 0


def test_project_delete_nulls_project_links_and_cascades_project_owned_rows():
    db = _session()
    owner = _user("owner-1", "owner@example.com")
    member = _user("member-1", "member@example.com")
    project = _project("project-1", owner.id)
    chat = _chat("chat-1", owner.id, project.id)
    file_row = _file("file-1", owner.id, project.id)

    db.add_all([owner, member])
    db.commit()

    db.add(project)
    db.commit()

    db.add_all(
        [
            chat,
            file_row,
            ProjectMember(project_id=project.id, user_id=member.id, role="member"),
            Memory(
                id="memory-1",
                project_id=project.id,
                content="remember this",
                content_key="remember this",
                created_at=_now(),
                updated_at=_now(),
            ),
        ]
    )
    db.commit()

    db.delete(project)
    db.commit()

    stored_chat = db.query(Chats).filter(Chats.id == chat.id).first()
    stored_file = db.query(Files).filter(Files.id == file_row.id).first()

    assert stored_chat is not None
    assert stored_chat.project_id is None
    assert stored_file is not None
    assert stored_file.project_id is None
    assert db.query(ProjectMember).count() == 0
    assert db.query(Memory).count() == 0


def test_foreign_keys_reject_orphan_chats_and_project_memberships():
    db = _session()
    member = _user("member-1", "member@example.com")
    db.add(member)
    db.commit()

    db.add(_chat("chat-1", "missing-user", None))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        ProjectMember(project_id="missing-project", user_id=member.id, role="member")
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_memories_require_one_owner_and_are_unique_within_each_scope():
    db = _session()
    owner = _user("owner-1", "owner@example.com")
    project = _project("project-1", owner.id)
    db.add(owner)
    db.commit()
    db.add(project)
    db.commit()

    # The same normalized content may exist once in each independent scope.
    db.add_all(
        [
            Memory(
                id="personal-memory",
                user_id=owner.id,
                content="Remember this",
                content_key="remember this",
                created_at=_now(),
                updated_at=_now(),
            ),
            Memory(
                id="project-memory",
                project_id=project.id,
                content="Remember this",
                content_key="remember this",
                created_at=_now(),
                updated_at=_now(),
            ),
        ]
    )
    db.commit()

    db.add(
        Memory(
            id="duplicate-personal-memory",
            user_id=owner.id,
            content="REMEMBER THIS",
            content_key="remember this",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        Memory(
            id="ownerless-memory",
            content="Invalid owner",
            content_key="invalid owner",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
