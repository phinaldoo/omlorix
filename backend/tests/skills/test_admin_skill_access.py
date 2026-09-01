from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.skills import models as skill_models
from app.skills.models import AdminSkills, Skills, SharedSkillSubscription, get_skill_content_for_user


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Skills.__table__, AdminSkills.__table__, SharedSkillSubscription.__table__],
    )
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _admin_skill(skill_id: str, content: str) -> AdminSkills:
    now = datetime.now(timezone.utc)
    return AdminSkills(
        id=skill_id,
        icon="",
        name=skill_id,
        description="",
        content=content,
        created_at=now,
        updated_at=now,
    )


def test_get_skill_content_for_user_requires_group_assigned_admin_skill(monkeypatch):
    db = _session()
    db.add(_admin_skill("allowed-admin-skill", "allowed instructions"))
    db.add(_admin_skill("restricted-admin-skill", "restricted instructions"))
    db.commit()

    monkeypatch.setattr(
        skill_models,
        "_get_user_admin_skill_ids",
        lambda _db, _user_id: ["allowed-admin-skill"],
    )

    assert get_skill_content_for_user(db, "user-id", "allowed-admin-skill") == "allowed instructions"
    assert get_skill_content_for_user(db, "user-id", "restricted-admin-skill") is None
