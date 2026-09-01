import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

from app.agents import utils as agents_utils
from app.file_folders import router as folders_router
from app.notes import router as notes_router
from app.prompts import router as prompts_router
from app.skills import router as skills_router
from app.todos import router as todos_router


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


def _user(user_id="viewer-1"):
    return SimpleNamespace(id=user_id)


def _shared_item(**overrides):
    values = {
        "id": "item-1",
        "user_id": "owner-1",
        "name": "Shared item",
        "title": "Shared item",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Db:
    def __init__(self):
        self.added = []

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass


@pytest.mark.parametrize(
    "share_type_name",
    ["LIVE", "COLLABORATE"],
)
def test_accept_shared_agent_persists_only_share_type(monkeypatch, share_type_name):
    share_type = getattr(agents_utils.ShareType, share_type_name)
    db = _Db()

    monkeypatch.setattr(agents_utils, "detect_agent_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(
        agents_utils,
        "get_user_agent_by_share_id",
        lambda db, share_id, share_type: _shared_item(id="agent-1", base_model_id="model-1"),
    )
    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", lambda db, user_id, base_model_id: object())
    monkeypatch.setattr(agents_utils, "get_user_agent_subscription", lambda db, user_id, agent_id: None)

    result = agents_utils.accept_shared_agent(db, user_id="viewer-1", share_id="share-1")

    assert db.added[0].share_type == share_type.value
    assert result["share_type"] == share_type.value
    assert "can_edit" not in result


@pytest.mark.parametrize(
    ("module", "share_type_name"),
    [
        (notes_router, "LIVE"),
        (notes_router, "COLLABORATE"),
    ],
)
def test_accept_shared_note_passes_only_share_type(monkeypatch, module, share_type_name):
    share_type = getattr(module.ShareType, share_type_name)
    captured = {}

    monkeypatch.setattr(module, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "ensure_notes_sharing_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "detect_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(module, "get_shared_note_by_share_id", lambda db, share_id, share_type: _shared_item(id="note-1"))
    monkeypatch.setattr(
        module,
        "subscribe_to_shared_note",
        lambda db, user_id, item_id, share_type: captured.setdefault("share_type", share_type),
    )
    monkeypatch.setattr(module, "create_audit_log", lambda *args, **kwargs: None)

    response = module.accept_shared_note_route(
        "share-1",
        _request(),
        db=object(),
        db_log=object(),
        user=_user(),
    )

    assert captured["share_type"] == share_type
    assert response.share_type == share_type.value


@pytest.mark.parametrize(
    "share_type_name",
    ["LIVE", "COLLABORATE"],
)
def test_accept_shared_prompt_passes_only_share_type(monkeypatch, share_type_name):
    share_type = getattr(prompts_router.ShareType, share_type_name)
    captured = {}

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(prompts_router, "ensure_prompt_sharing_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(prompts_router, "detect_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(prompts_router, "get_shared_prompt_by_share_id", lambda db, share_id, share_type: _shared_item(id="prompt-1"))
    monkeypatch.setattr(
        prompts_router,
        "subscribe_to_shared_prompt",
        lambda db, user_id, item_id, share_type: captured.setdefault("share_type", share_type),
    )
    monkeypatch.setattr(prompts_router, "create_audit_log", lambda *args, **kwargs: None)

    response = prompts_router.accept_shared_prompt_route(
        "share-1",
        _request(),
        db=object(),
        db_log=object(),
        user=_user(),
    )

    assert captured["share_type"] == share_type
    assert response.share_type == share_type.value


@pytest.mark.parametrize(
    "share_type_name",
    ["LIVE", "COLLABORATE"],
)
def test_accept_shared_folder_passes_only_share_type(monkeypatch, share_type_name):
    share_type = getattr(folders_router.ShareType, share_type_name)
    captured = {}

    monkeypatch.setattr(folders_router, "detect_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(folders_router, "get_shared_folder_by_share_id", lambda db, share_id: _shared_item(id="folder-1"))
    monkeypatch.setattr(
        folders_router,
        "subscribe_to_shared_folder",
        lambda db, user_id, item_id, share_type: captured.setdefault("share_type", share_type),
    )
    monkeypatch.setattr(folders_router, "create_audit_log", lambda *args, **kwargs: None)

    folders_router.accept_shared_folder_route(
        "share-1",
        _request(),
        db=object(),
        db_log=object(),
        user=_user(),
    )

    assert captured["share_type"] == share_type


@pytest.mark.parametrize(
    "share_type_name",
    ["LIVE", "COLLABORATE"],
)
def test_accept_shared_todo_passes_only_share_type(monkeypatch, share_type_name):
    share_type = getattr(todos_router.ShareType, share_type_name)
    captured = {}

    monkeypatch.setattr(todos_router, "ensure_todo_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(todos_router, "ensure_todo_sharing_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(todos_router, "detect_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(todos_router, "get_shared_todo_list_by_share_id", lambda db, share_id: _shared_item(id="todo-list-1"))
    monkeypatch.setattr(
        todos_router,
        "subscribe_to_shared_todo_list",
        lambda db, user_id, item_id, share_type: captured.setdefault("share_type", share_type),
    )
    monkeypatch.setattr(todos_router, "create_audit_log", lambda *args, **kwargs: None)

    todos_router.accept_shared_todo_list_route(
        "share-1",
        _request(),
        db=object(),
        db_log=object(),
        user=_user(),
    )

    assert captured["share_type"] == share_type


@pytest.mark.parametrize(
    "share_type_name",
    ["LIVE", "COLLABORATE"],
)
def test_accept_shared_skill_passes_only_share_type(monkeypatch, share_type_name):
    share_type = getattr(skills_router.ShareType, share_type_name)
    captured = {}

    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(skills_router, "ensure_skills_sharing_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(skills_router, "detect_share_type_from_id", lambda db, share_id: share_type)
    monkeypatch.setattr(skills_router, "get_shared_skill_by_share_id", lambda db, share_id: _shared_item(id="skill-1"))
    monkeypatch.setattr(
        skills_router,
        "subscribe_to_shared_skill",
        lambda db, user_id, item_id, share_type: captured.setdefault("share_type", share_type),
    )
    monkeypatch.setattr(skills_router, "create_audit_log", lambda *args, **kwargs: None)

    asyncio.run(
        skills_router.accept_shared_skill_endpoint(
            _request(),
            "share-1",
            user=_user(),
            db=object(),
            db_log=object(),
        )
    )

    assert captured["share_type"] == share_type
