from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import sys

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.notes import router as notes_router
from app.prompts import router as prompts_router
from app.skills import router as skills_router
from app.todos import router as todos_router


def _db_with_owned_live_share(model_name: str):
    """Build a tiny query double for an owned item that only has a live share."""
    item_query = MagicMock()
    item_query.filter.return_value.first.return_value = SimpleNamespace(
        clone_share_id=None,
        live_share_id="live-share",
        collaborate_share_id=None,
        share_id=None,
    )
    subscription_query = MagicMock()
    subscription_query.filter.return_value.count.return_value = 0
    db = MagicMock()
    db.query.side_effect = lambda model: (
        item_query if getattr(model, "__name__", "") == model_name else subscription_query
    )
    return db


@pytest.mark.parametrize(
    ("router_module", "model_name", "ensure_function_name", "guard_function_name"),
    [
        (notes_router, "Notes", "ensure_notes_sharing_allowed", "ensure_notes_sharing_allowed_or_existing"),
        (prompts_router, "Prompts", "ensure_prompt_sharing_allowed", "ensure_prompt_sharing_allowed_or_existing"),
        (skills_router, "Skills", "ensure_skills_sharing_allowed", "ensure_skills_sharing_allowed_or_existing"),
        (todos_router, "TodoLists", "ensure_todo_sharing_allowed", "ensure_todo_sharing_allowed_or_existing"),
    ],
)
def test_existing_live_share_does_not_unlock_new_collaborate_share(
    monkeypatch,
    router_module,
    model_name,
    ensure_function_name,
    guard_function_name,
):
    user = SimpleNamespace(id="owner-1")
    guard = getattr(router_module, guard_function_name)

    monkeypatch.setattr(
        router_module,
        ensure_function_name,
        lambda user_arg, db_arg: (_ for _ in ()).throw(HTTPException(status_code=403, detail="disabled")),
    )

    guard(user, _db_with_owned_live_share(model_name), "item-1", router_module.ShareType.LIVE)

    with pytest.raises(HTTPException) as blocked:
        guard(user, _db_with_owned_live_share(model_name), "item-1", router_module.ShareType.COLLABORATE)

    assert blocked.value.status_code == 403
