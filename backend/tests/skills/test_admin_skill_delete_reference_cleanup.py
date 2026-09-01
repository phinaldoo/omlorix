import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents.models import UserAgent
from app.database import Base
from app.groups.models import Group
from app.llm.models import Models
from app.skills import models as skill_models
from app.skills.models import AdminSkills, delete_admin_skill


def _session():
    """Create a tiny in-memory database with only the tables this cleanup touches."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AdminSkills.__table__,
            Group.__table__,
            Models.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE user_agents (
                id VARCHAR PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                icon VARCHAR NOT NULL,
                base_model_id VARCHAR NOT NULL,
                instruction VARCHAR NOT NULL,
                skill_id VARCHAR,
                clone_share_id VARCHAR,
                live_share_id VARCHAR,
                collaborate_share_id VARCHAR,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _admin_skill(skill_id: str) -> AdminSkills:
    """Build an admin skill row for deletion tests."""
    now = datetime.now(timezone.utc)
    return AdminSkills(
        id=skill_id,
        icon="sparkles",
        name="Admin Guardrail",
        description="Admin-only guardrail",
        content="Follow the guardrail.",
        created_at=now,
        updated_at=now,
    )


def _model(model_id: str, settings: dict) -> Models:
    """Build a minimal model row with configurable JSON settings."""
    return Models(
        id=model_id,
        name=model_id,
        description="Test model",
        model_icon="bot",
        provider="openai",
        provider_id="provider-1",
        model_name="gpt-test",
        settings=settings,
        capabilities=["completion"],
        tools={},
        access={"everyone": True},
        meta={},
        status="normal",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


def _agent(agent_id: str, skill_id: str | None) -> UserAgent:
    """Build a saved custom agent with an optional selected skill."""
    now = datetime.now(timezone.utc)
    return UserAgent(
        id=agent_id,
        user_id="user-1",
        name=agent_id,
        icon="sparkles",
        base_model_id="model-1",
        instruction="Agent instructions",
        skill_id=skill_id,
        created_at=now,
        updated_at=now,
    )


def test_delete_admin_skill_removes_model_and_agent_references(monkeypatch, tmp_path):
    """Deleting an admin skill clears stale fixed model skills and saved agents."""
    db = _session()
    deleted_skill_id = "admin-skill-delete-me"
    kept_skill_id = "admin-skill-keep"
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path)

    db.add(_admin_skill(deleted_skill_id))
    db.add(_model("model-single-and-list", {
        "skill_id": deleted_skill_id,
        "skill_ids": [deleted_skill_id, kept_skill_id],
        "temperature": 0.2,
    }))
    db.add(_model("model-list-only", {
        "skill_id": kept_skill_id,
        "skill_ids": [deleted_skill_id],
    }))
    db.add(_model("model-unrelated", {
        "skill_id": kept_skill_id,
        "skill_ids": [kept_skill_id],
    }))
    db.add(_agent("agent-with-deleted-skill", deleted_skill_id))
    db.add(_agent("agent-with-kept-skill", kept_skill_id))
    db.commit()

    result = delete_admin_skill(db, deleted_skill_id)

    assert result == {"deleted": True, "skill_id": deleted_skill_id}
    assert db.query(AdminSkills).filter(AdminSkills.id == deleted_skill_id).first() is None

    model_single_and_list = db.query(Models).filter(Models.id == "model-single-and-list").one()
    assert model_single_and_list.settings == {
        "skill_ids": [kept_skill_id],
        "temperature": 0.2,
    }

    model_list_only = db.query(Models).filter(Models.id == "model-list-only").one()
    assert model_list_only.settings == {
        "skill_id": kept_skill_id,
    }

    model_unrelated = db.query(Models).filter(Models.id == "model-unrelated").one()
    assert model_unrelated.settings == {
        "skill_id": kept_skill_id,
        "skill_ids": [kept_skill_id],
    }

    cleared_agent = db.query(UserAgent).filter(UserAgent.id == "agent-with-deleted-skill").one()
    assert cleared_agent.skill_id is None

    kept_agent = db.query(UserAgent).filter(UserAgent.id == "agent-with-kept-skill").one()
    assert kept_agent.skill_id == kept_skill_id
