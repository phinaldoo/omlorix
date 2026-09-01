import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base
from app.skills import models as skill_models
from app.skills.models import AdminSkills, SharedSkillSubscription, Skills


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Skills.__table__, AdminSkills.__table__, SharedSkillSubscription.__table__],
    )
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _admin_skill(skill_id: str = "admin-secret") -> AdminSkills:
    now = datetime.now(timezone.utc)
    return AdminSkills(
        id=skill_id,
        icon="sparkles",
        name="Admin Secret",
        description="Admin-only skill",
        content="admin base instructions",
        created_at=now,
        updated_at=now,
    )


def _write_admin_skill_files(root, skill_id: str) -> None:
    references = root / skill_models.ADMIN_SKILLS_USER_ID / skill_id / "references"
    assets = root / skill_models.ADMIN_SKILLS_USER_ID / skill_id / "assets"
    references.mkdir(parents=True)
    assets.mkdir(parents=True)
    (references / "secret.txt").write_text("TOP_SECRET_ADMIN_SKILL_FILE", encoding="utf-8")
    (assets / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_unassigned_admin_skill_files_are_not_exposed(monkeypatch, tmp_path):
    db = _session()
    skill_id = "admin-secret"
    db.add(_admin_skill(skill_id))
    db.commit()
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path)
    _write_admin_skill_files(tmp_path, skill_id)

    def group_setting(user_id, page_name, key_name, db_session):  # noqa: ARG001
        if key_name == "enabled_skills":
            return True
        if key_name == "admin_skill_ids":
            return []
        raise AssertionError(f"Unexpected setting key: {key_name}")

    monkeypatch.setattr(skill_models, "get_user_group_setting_value", group_setting)

    assert skill_models.get_skill_content_for_user(db, "low-priv-user", skill_id) is None
    assert skill_models.get_skill_file_descriptors_by_category_for_user(
        db, "low-priv-user", skill_id
    ) == {
        "image": [],
        "video": [],
        "audio": [],
        "document": [],
    }
    descriptor = skill_models.build_skill_file_descriptor(skill_id, "references/secret.txt")
    assert (
        skill_models.resolve_skill_file_info_for_user(
            db, user_id="low-priv-user", descriptor=descriptor
        )
        is None
    )


def test_assigned_admin_skill_files_remain_available(monkeypatch, tmp_path):
    db = _session()
    skill_id = "admin-assigned"
    db.add(_admin_skill(skill_id))
    db.commit()
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path)
    _write_admin_skill_files(tmp_path, skill_id)

    def group_setting(user_id, page_name, key_name, db_session):  # noqa: ARG001
        if key_name == "enabled_skills":
            return True
        if key_name == "admin_skill_ids":
            return [skill_id]
        raise AssertionError(f"Unexpected setting key: {key_name}")

    monkeypatch.setattr(skill_models, "get_user_group_setting_value", group_setting)

    # Only the authored skill instructions are returned for the system prompt.
    # Files remain available through their access-controlled descriptors and
    # are processed once by the provider attachment pipeline.
    content = skill_models.get_skill_content_for_user(db, "assigned-user", skill_id)
    assert content == "admin base instructions"

    descriptors = skill_models.get_skill_file_descriptors_by_category_for_user(
        db, "assigned-user", skill_id
    )
    assert descriptors["document"] == [
        skill_models.build_skill_file_descriptor(skill_id, "references/secret.txt")
    ]
    assert descriptors["image"] == [
        skill_models.build_skill_file_descriptor(skill_id, "assets/diagram.png")
    ]

    file_info = skill_models.resolve_skill_file_info_for_user(
        db,
        user_id="assigned-user",
        descriptor=descriptors["document"][0],
    )
    assert file_info is not None
    assert (
        Path(file_info["path"]).read_text(encoding="utf-8")
        == "TOP_SECRET_ADMIN_SKILL_FILE"
    )
