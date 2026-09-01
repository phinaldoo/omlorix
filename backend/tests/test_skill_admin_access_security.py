import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.skills.models import AdminSkills, SharedSkillSubscription, Skills, get_skill_content_for_user


class _FakeQuery:
    def __init__(self, result=None):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeDb:
    def __init__(self, *, admin_skill=None):
        self.admin_skill = admin_skill

    def query(self, model):
        if model is AdminSkills:
            return _FakeQuery(self.admin_skill)
        if model in {Skills, SharedSkillSubscription}:
            return _FakeQuery(None)
        return _FakeQuery(None)


def test_admin_skill_content_requires_group_assignment_or_trusted_model_setting(monkeypatch):
    admin_skill = SimpleNamespace(id="admin-skill-secret", content="hidden admin instructions")
    db = _FakeDb(admin_skill=admin_skill)

    monkeypatch.setattr(
        "app.groups.init.get_user_group_setting_value",
        lambda user_id, page_name, key_name, db: ["different-admin-skill"],
    )

    assert get_skill_content_for_user(db, "user-1", admin_skill.id) is None
    assert (
        get_skill_content_for_user(
            db,
            "user-1",
            admin_skill.id,
            trusted_admin_skill_ids=[admin_skill.id],
        )
        == admin_skill.content
    )


def test_group_assigned_admin_skill_content_remains_available(monkeypatch):
    admin_skill = SimpleNamespace(id="assigned-admin-skill", content="assigned instructions")
    db = _FakeDb(admin_skill=admin_skill)

    monkeypatch.setattr(
        "app.groups.init.get_user_group_setting_value",
        lambda user_id, page_name, key_name, db: [admin_skill.id],
    )

    assert get_skill_content_for_user(db, "user-1", admin_skill.id) == admin_skill.content
