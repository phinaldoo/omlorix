from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.agents import utils as agents_utils  # noqa: E402


class FakeDb:
    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True

    def refresh(self, _value):
        return None


def _shared_agent():
    return SimpleNamespace(
        id="agent-1",
        user_id="owner-1",
        name="Agent",
        icon="bot",
        base_model_id="model-old",
        instruction="",
        skill_id=None,
        updated_at=None,
    )


def _subscription():
    return SimpleNamespace(share_type="collaborate")


def test_collaborator_cannot_switch_agent_to_owner_inaccessible_base_model(monkeypatch):
    db = FakeDb()
    agent = _shared_agent()

    monkeypatch.setattr(
        agents_utils,
        "get_user_agent_with_access",
        lambda *_args, **_kwargs: (agent, SimpleNamespace(id="model-old"), _subscription()),
    )

    def fake_get_accessible_base_model(_db, user_id, base_model_id, accessible_base_models=None):
        assert accessible_base_models is None
        if user_id == "editor-1":
            return SimpleNamespace(id=base_model_id)
        if user_id == "owner-1":
            raise HTTPException(status_code=403, detail="You do not have access to the selected base model")
        raise AssertionError(f"unexpected user_id: {user_id}")

    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", fake_get_accessible_base_model)
    monkeypatch.setattr(agents_utils, "_serialize_agent", lambda *_args, **_kwargs: pytest.fail("update should not serialize"))

    with pytest.raises(HTTPException) as exc_info:
        agents_utils.update_user_agent(
            db,
            user_id="editor-1",
            agent_id="agent-1",
            base_model_id="model-new",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Agent owner does not have access to the selected base model"
    assert db.committed is False
    assert agent.base_model_id == "model-old"


def test_collaborator_cannot_switch_agent_to_owner_inaccessible_skill(monkeypatch):
    db = FakeDb()
    agent = _shared_agent()

    monkeypatch.setattr(
        agents_utils,
        "get_user_agent_with_access",
        lambda *_args, **_kwargs: (agent, SimpleNamespace(id="model-old"), _subscription()),
    )
    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", lambda *_args, **_kwargs: SimpleNamespace(id="model-old"))

    def fake_validate_skill_access(_db, user_id, skill_id):
        if user_id == "editor-1":
            return skill_id
        if user_id == "owner-1":
            raise HTTPException(status_code=404, detail="Skill not found")
        raise AssertionError(f"unexpected user_id: {user_id}")

    monkeypatch.setattr(agents_utils, "_validate_skill_access", fake_validate_skill_access)
    monkeypatch.setattr(agents_utils, "_serialize_agent", lambda *_args, **_kwargs: pytest.fail("update should not serialize"))

    with pytest.raises(HTTPException) as exc_info:
        agents_utils.update_user_agent(
            db,
            user_id="editor-1",
            agent_id="agent-1",
            skill_id="skill-1",
            skill_id_provided=True,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Agent owner does not have access to the selected skill"
    assert db.committed is False
    assert agent.skill_id is None


def test_collaborator_can_update_resources_when_owner_also_has_access(monkeypatch):
    db = FakeDb()
    agent = _shared_agent()

    monkeypatch.setattr(
        agents_utils,
        "get_user_agent_with_access",
        lambda *_args, **_kwargs: (agent, SimpleNamespace(id="model-old"), _subscription()),
    )

    base_model_checks: list[tuple[str, str]] = []
    skill_checks: list[tuple[str, str | None]] = []

    def fake_get_accessible_base_model(_db, user_id, base_model_id, accessible_base_models=None):
        assert accessible_base_models is None
        base_model_checks.append((user_id, base_model_id))
        return SimpleNamespace(id=base_model_id)

    def fake_validate_skill_access(_db, user_id, skill_id):
        skill_checks.append((user_id, skill_id))
        return skill_id

    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", fake_get_accessible_base_model)
    monkeypatch.setattr(agents_utils, "_validate_skill_access", fake_validate_skill_access)
    monkeypatch.setattr(
        agents_utils,
        "_serialize_agent",
        lambda _db, **kwargs: {
            "id": kwargs["agent"].id,
            "base_model_id": kwargs["agent"].base_model_id,
            "skill_id": kwargs["agent"].skill_id,
        },
    )

    payload = agents_utils.update_user_agent(
        db,
        user_id="editor-1",
        agent_id="agent-1",
        base_model_id="model-new",
        skill_id="skill-1",
        skill_id_provided=True,
    )

    assert db.committed is True
    assert base_model_checks == [("editor-1", "model-new"), ("owner-1", "model-new"), ("editor-1", "model-new")]
    assert skill_checks == [("editor-1", "skill-1"), ("owner-1", "skill-1")]
    assert payload == {"id": "agent-1", "base_model_id": "model-new", "skill_id": "skill-1"}
