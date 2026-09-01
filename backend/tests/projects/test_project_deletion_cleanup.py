from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
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


from app.chats.models import Chats
from app.database import Base
from app.files.models import FileArtifactShare, Files
from app.memories.models import Memory
from app.projects import models as project_models
from app.projects.models import Project, ProjectMember
import app.utils.sqlalchemy_encryption as sqlalchemy_encryption
from app.users.models import User


@pytest.fixture(autouse=True)
def _disable_encryption(monkeypatch):
    monkeypatch.setattr(sqlalchemy_encryption, "encrypt_value", lambda value: value)
    monkeypatch.setattr(sqlalchemy_encryption, "decrypt_value", lambda value: value)


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
        settings={
            "icon": "",
            "icon_color": "",
            "system_instruction": "",
            "separate_memory_enabled": False,
        },
        created_at=now,
        last_updated_at=now,
    )


def _chat(chat_id: str, owner_user_id: str, project_id: str) -> Chats:
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


def _file(file_id: str, owner_user_id: str, project_id: str, file_name: str) -> Files:
    now = _now()
    return Files(
        id=file_id,
        user_id=owner_user_id,
        file_name=file_name,
        storage_provider="local",
        storage_key=f"{owner_user_id}/{file_name}",
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
            ProjectMember.__table__,
            Memory.__table__,
            Chats.__table__,
            Files.__table__,
            FileArtifactShare.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_delete_project_with_members_detaches_chats_and_removes_project_files(monkeypatch):
    db = _session()
    owner = _user("owner-1", "owner@example.com")
    member = _user("member-1", "member@example.com")
    project = _project("project-1", owner.id)
    owner_chat = _chat("chat-owner", owner.id, project.id)
    member_chat = _chat("chat-member", member.id, project.id)
    owner_file = _file("file-owner", owner.id, project.id, "owner.txt")
    member_file = _file("file-member", member.id, project.id, "member.txt")

    db.add_all([owner, member])
    db.commit()
    db.add(project)
    db.commit()
    db.add_all(
        [
            ProjectMember(project_id=project.id, user_id=member.id, role="member"),
            Memory(
                id="memory-1",
                project_id=project.id,
                content="keep context",
                content_key="keep context",
                created_at=_now(),
                updated_at=_now(),
            ),
            owner_chat,
            member_chat,
            owner_file,
            member_file,
        ]
    )
    db.commit()
    db.add_all(
        [
            FileArtifactShare(file_id=owner_file.id, user_id=owner.id),
            FileArtifactShare(file_id=member_file.id, user_id=member.id),
        ]
    )
    db.commit()

    cleaned_file_refs: list[tuple[str, str]] = []
    cleaned_automations: list[tuple[str, str, bool]] = []
    cleaned_storage: list[tuple[str, str, str]] = []

    import app.automations.models as automation_models
    import app.files.reference_cleanup as reference_cleanup

    monkeypatch.setattr(
        reference_cleanup,
        "cleanup_file_references",
        lambda db, user_id, file_id: cleaned_file_refs.append((user_id, file_id)),
    )
    monkeypatch.setattr(
        automation_models,
        "remove_file_from_automations",
        lambda db, user_id, file_id, commit=True: cleaned_automations.append((user_id, file_id, commit)),
    )
    monkeypatch.setattr(
        project_models,
        "_delete_project_file_storage",
        lambda *, storage_provider, storage_key, user_id, file_name, materialized_path: cleaned_storage.append(
            (user_id, storage_provider, storage_key)
        ),
    )

    assert project_models.delete_project_with_members(db, owner.id, project.id) is True

    stored_owner_chat = db.query(Chats).filter(Chats.id == owner_chat.id).first()
    stored_member_chat = db.query(Chats).filter(Chats.id == member_chat.id).first()

    assert db.query(Project).count() == 0
    assert db.query(ProjectMember).count() == 0
    assert db.query(Memory).count() == 0
    assert db.query(Files).count() == 0
    assert db.query(FileArtifactShare).count() == 0
    assert stored_owner_chat is not None
    assert stored_member_chat is not None
    assert stored_owner_chat.project_id is None
    assert stored_member_chat.project_id is None
    assert cleaned_file_refs == [
        (owner.id, owner_file.id),
        (member.id, member_file.id),
    ]
    assert cleaned_automations == [
        (owner.id, owner_file.id, False),
        (member.id, member_file.id, False),
    ]
    assert cleaned_storage == [
        (owner.id, "local", f"{owner.id}/owner.txt"),
        (member.id, "local", f"{member.id}/member.txt"),
    ]
