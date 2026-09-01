from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.skills import models as skill_models
from app.skills import router as skills_router
from app.skills import utils as skill_utils
from app.skills.models import SharedSkillSubscription, Skills
from app.skills.schemas import SkillUpdate


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Skills.__table__, SharedSkillSubscription.__table__],
    )
    return sessionmaker(bind=engine)()


def _seed_shared_skill(db, *, share_type: str = "collaborate") -> Skills:
    skill = Skills(
        id="skill-1",
        user_id="owner-1",
        icon="sparkles",
        name="shared-skill",
        description="Shared description",
        content="Original instructions",
        clone_share_id="clone-token",
        live_share_id="live-token",
        collaborate_share_id="collaborate-token",
    )
    db.add(skill)
    db.add(
        SharedSkillSubscription(
            id="subscription-1",
            skill_id=skill.id,
            subscriber_id="collaborator-1",
            share_type=share_type,
        )
    )
    db.commit()
    return skill


def _patch_skill_roots(monkeypatch, root):
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", root)
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_router, "SKILLS_ROOT", root)


def test_skills_table_contains_only_typed_share_identifiers():
    """The current schema must not reintroduce the removed generic share fields."""
    column_names = set(Skills.__table__.columns.keys())

    assert "share" not in column_names
    assert "share_id" not in column_names
    assert {
        "clone_share_id",
        "live_share_id",
        "collaborate_share_id",
    } <= column_names


def test_collaborator_access_requires_an_active_editable_share():
    db = _session()
    skill = _seed_shared_skill(db)

    resolved, subscription = skill_models.get_skill_with_access(
        db,
        "collaborator-1",
        skill.id,
        require_edit=True,
    )
    assert resolved.id == skill.id
    assert subscription is not None
    assert subscription.share_type == "collaborate"

    subscription.share_type = "live"
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        skill_models.get_skill_with_access(
            db,
            "collaborator-1",
            skill.id,
            require_edit=True,
        )
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    skill.live_share_id = None
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        skill_models.get_skill_with_access(db, "collaborator-1", skill.id)
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_skill_access_converts_invalid_path_segments_to_http_errors():
    db = _session()

    with pytest.raises(HTTPException) as user_error:
        skill_models.get_skill_with_access(db, "../user", "skill-1")
    with pytest.raises(HTTPException) as skill_error:
        skill_models.get_skill_with_access(db, "user-1", "../skill")

    assert user_error.value.status_code == status.HTTP_400_BAD_REQUEST
    assert skill_error.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_collaborator_updates_owner_skill_and_markdown(monkeypatch, tmp_path):
    db = _session()
    skill = _seed_shared_skill(db)
    _patch_skill_roots(monkeypatch, tmp_path)
    skill_utils.write_skill_markdown_file(
        "owner-1",
        skill.id,
        name=skill.name,
        description=skill.description,
        content=skill.content,
        license_value="MIT",
        compatibility="Omlorix 1.x",
        metadata={"team": "platform"},
    )

    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args, **_kwargs: None)
    audit_details = []
    monkeypatch.setattr(
        skills_router,
        "create_audit_log",
        lambda **kwargs: audit_details.append(kwargs["details"]),
    )
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")

    response = await skills_router.update_skill_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        skill_id=skill.id,
        skill_data=SkillUpdate(title="updated-skill", content="Updated instructions"),
        user=SimpleNamespace(id="collaborator-1"),
        db=db,
        db_log=object(),
    )

    assert response.user_id == "owner-1"
    assert response.title == "updated-skill"
    assert response.content == "Updated instructions"
    assert response.is_subscribed is True
    assert response.share_type == "collaborate"
    assert not hasattr(response, "can_edit")
    assert response.license == "MIT"
    assert response.compatibility == "Omlorix 1.x"
    assert response.metadata == {"team": "platform"}
    assert (tmp_path / "owner-1" / skill.id / "SKILL.md").is_file()
    assert not (tmp_path / "collaborator-1" / skill.id).exists()
    assert audit_details == [
        {
            "skill_id": skill.id,
            "title": "updated-skill",
            "owner_id": "owner-1",
            "collaborative_edit": True,
        }
    ]


@pytest.mark.anyio
async def test_collaborator_file_mutations_use_owner_namespace(monkeypatch):
    db = _session()
    skill = _seed_shared_skill(db)
    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skills_router, "resolve_user_file_upload_limits", lambda *_args, **_kwargs: (-1, None))
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")

    upload_calls = []

    async def fake_store(upload, **kwargs):
        upload_calls.append((upload.filename, kwargs["user_id"], kwargs["skill_id"]))
        return {"name": upload.filename, "size": 4}

    delete_calls = []
    monkeypatch.setattr(skills_router, "_store_validated_skill_upload", fake_store)
    monkeypatch.setattr(
        skills_router,
        "delete_skill_file",
        lambda user_id, skill_id, folder_type, filename: delete_calls.append(
            (user_id, skill_id, folder_type, filename)
        ),
    )

    upload_result = await skills_router.upload_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        skill_id=skill.id,
        folder_type="assets",
        files=[SimpleNamespace(filename="logo.png")],
        user=SimpleNamespace(id="collaborator-1"),
        db=db,
        db_log=object(),
    )
    delete_result = await skills_router.delete_skill_file_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        skill_id=skill.id,
        folder_type="assets",
        filename="logo.png",
        user=SimpleNamespace(id="collaborator-1"),
        db=db,
        db_log=object(),
    )

    assert upload_result == {"uploaded": [{"name": "logo.png", "size": 4}], "errors": []}
    assert upload_calls == [("logo.png", "owner-1", skill.id)]
    assert delete_result == {"deleted": True, "filename": "logo.png"}
    assert delete_calls == [("owner-1", skill.id, "assets", "logo.png")]


def test_clone_copies_complete_package_into_recipient_namespace(monkeypatch, tmp_path):
    db = _session()
    skill = _seed_shared_skill(db)
    _patch_skill_roots(monkeypatch, tmp_path)
    source = tmp_path / "owner-1" / skill.id
    (source / "scripts").mkdir(parents=True)
    (source / "references").mkdir()
    (source / "assets").mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Shared description\nlicense: MIT\n"
        "compatibility: Omlorix 1.x\nmetadata:\n  team: platform\n  author: Ada\n---\n\nOriginal instructions\n",
        encoding="utf-8",
    )
    (source / "scripts" / "run.sh").write_text("echo ok\n", encoding="utf-8")
    (source / "references" / "guide.txt").write_text("guide\n", encoding="utf-8")
    (source / "assets" / "logo.bin").write_bytes(b"logo")

    cloned = skill_models.clone_shared_skill(db, "recipient-1", "clone-token")
    destination = tmp_path / "recipient-1" / cloned.id

    assert cloned.user_id == "recipient-1"
    assert cloned.name == skill.name
    assert cloned.clone_share_id is None
    assert cloned.live_share_id is None
    assert cloned.collaborate_share_id is None
    assert (destination / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    assert (destination / "scripts" / "run.sh").read_bytes() == b"echo ok\n"
    assert (destination / "references" / "guide.txt").read_bytes() == b"guide\n"
    assert (destination / "assets" / "logo.bin").read_bytes() == b"logo"
    assert skill_utils.load_skill_markdown_fields("recipient-1", cloned.id) == {
        "name": "shared-skill",
        "description": "Shared description",
        "license": "MIT",
        "compatibility": "Omlorix 1.x",
        "metadata": {"team": "platform", "author": "Ada"},
        "author": "Ada",
    }


def test_clone_rejects_a_skill_without_a_complete_package(monkeypatch, tmp_path):
    db = _session()
    skill = _seed_shared_skill(db)
    _patch_skill_roots(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        skill_models.clone_shared_skill(db, "recipient-1", "clone-token")

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert db.query(Skills).filter(Skills.user_id == "recipient-1").count() == 0


def test_clone_rolls_back_database_and_partial_files(monkeypatch, tmp_path):
    db = _session()
    skill = _seed_shared_skill(db)
    _patch_skill_roots(monkeypatch, tmp_path)
    source = tmp_path / "owner-1" / skill.id
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: shared-skill\n---\n", encoding="utf-8")
    destinations = []

    def fail_copy(_source, destination):
        destinations.append(destination)
        destination.mkdir(parents=True)
        (destination / "partial").write_text("partial", encoding="utf-8")
        raise OSError("copy failed")

    monkeypatch.setattr(skill_models.shutil, "copytree", fail_copy)

    with pytest.raises(OSError, match="copy failed"):
        skill_models.clone_shared_skill(db, "recipient-1", "clone-token")

    assert db.query(Skills).filter(Skills.user_id == "recipient-1").count() == 0
    assert len(destinations) == 1
    assert not destinations[0].exists()
