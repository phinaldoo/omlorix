from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base  # noqa: E402
from app.prompts.models import (  # noqa: E402
    Prompts,
    SharedPromptSubscription,
    create_user_prompt,
    update_user_prompt,
)


@pytest.fixture
def session_factory():
    """Create independent sessions against one in-memory prompt database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[Prompts.__table__, SharedPromptSubscription.__table__],
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_stale_prompt_write_is_rejected_without_overwriting_latest_content(
    session_factory,
):
    """Keep optimistic locking while storing only the current prompt state."""
    owner_session = session_factory()
    prompt = create_user_prompt(
        owner_session,
        "owner-1",
        "Initial",
        "Description",
        "Original",
    )
    assert prompt.revision == 1

    first_editor = session_factory()
    second_editor = session_factory()
    first = update_user_prompt(
        first_editor,
        "owner-1",
        prompt.id,
        content="First editor content",
        expected_revision=1,
        actor_user_id="collaborator-1",
    )
    assert first.revision == 2
    assert first.last_edited_by_user_id == "collaborator-1"

    with pytest.raises(HTTPException) as exc_info:
        update_user_prompt(
            second_editor,
            "owner-1",
            prompt.id,
            content="Stale second editor content",
            expected_revision=1,
            actor_user_id="collaborator-2",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "prompt_revision_conflict"

    verification = session_factory()
    stored = verification.query(Prompts).filter(Prompts.id == prompt.id).one()
    assert stored.content == "First editor content"
    assert stored.revision == 2
