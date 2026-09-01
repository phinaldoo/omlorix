import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())


from app.prompts import models as prompt_models
from app.prompts import router as prompts_router


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _Db:
    def __init__(self, result):
        self._result = result

    def query(self, _model):
        return _Query(self._result)


class _SubscriptionDb(_Db):
    def __init__(self, result):
        super().__init__(result)
        self.deleted = []
        self.committed = False

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.committed = True


class _PreviewDb:
    def __init__(self, *results):
        self._results = list(results)

    def query(self, _model):
        result = self._results.pop(0) if self._results else None
        return _Query(result)


def _prompt(**overrides):
    values = {
        "id": "prompt-1",
        "user_id": "owner-1",
        "title": "Shared prompt",
        "description": "Description",
        "content": "Prompt content",
        "revision": 1,
        "last_edited_by_user_id": "owner-1",
        "clone_share_id": "clone-token",
        "live_share_id": "live-token",
        "collaborate_share_id": "collaborate-token",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _subscription(**overrides):
    values = {
        "share_type": "live",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_list_prompts_redacts_share_ids_for_subscribers(monkeypatch):
    owner_prompt = _prompt(id="own-prompt", user_id="viewer-1")
    subscribed_prompt = _prompt(id="subscribed-prompt", user_id="owner-1")

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(prompts_router, "list_user_prompts", lambda db, user_id, **kwargs: [owner_prompt])
    monkeypatch.setattr(prompts_router, "get_subscribed_prompts", lambda db, user_id, **kwargs: [(subscribed_prompt, _subscription())])
    monkeypatch.setattr(prompts_router, "get_prompt_subscriber_count", lambda db, prompt_id: 1)
    monkeypatch.setattr(prompts_router, "get_user", lambda db, user_id: SimpleNamespace(first_name="Owner", last_name=""))

    response = prompts_router.list_prompts_route(db=SimpleNamespace(), user=SimpleNamespace(id="viewer-1"))
    by_id = {item.id: item for item in response.items}

    assert by_id["own-prompt"].clone_share_id == "clone-token"
    assert by_id["own-prompt"].live_share_id == "live-token"
    assert by_id["own-prompt"].collaborate_share_id == "collaborate-token"
    assert by_id["subscribed-prompt"].clone_share_id is None
    assert by_id["subscribed-prompt"].live_share_id is None
    assert by_id["subscribed-prompt"].collaborate_share_id is None
    assert by_id["subscribed-prompt"].user_id is None


def test_list_prompts_uses_bounded_merge_window(monkeypatch):
    captured = {}

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(prompts_router, "get_prompt_subscriber_count", lambda db, prompt_id: 0)
    monkeypatch.setattr(prompts_router, "get_user", lambda db, user_id: None)

    def list_user_prompts(db, user_id, **kwargs):
        captured["own_limit"] = kwargs.get("limit")
        return [
            _prompt(id="own-1", user_id=user_id, updated_at=datetime(2026, 1, 4, tzinfo=timezone.utc)),
            _prompt(id="own-2", user_id=user_id, updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ]

    def get_subscribed_prompts(db, user_id, **kwargs):
        captured["subscribed_limit"] = kwargs.get("limit")
        return [
            (_prompt(id="sub-1", user_id="owner-1", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc)), _subscription()),
        ]

    monkeypatch.setattr(prompts_router, "list_user_prompts", list_user_prompts)
    monkeypatch.setattr(prompts_router, "get_subscribed_prompts", get_subscribed_prompts)

    response = prompts_router.list_prompts_route(
        limit=1,
        offset=1,
        db=SimpleNamespace(),
        user=SimpleNamespace(id="viewer-1"),
    )

    assert captured == {"own_limit": 3, "subscribed_limit": 3}
    assert [item.id for item in response.items] == ["own-2"]
    assert response.has_more is True


def test_get_prompt_redacts_share_ids_for_subscribers(monkeypatch):
    prompt = _prompt()

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(prompts_router, "get_subscription_for_prompt", lambda db, user_id, prompt_id: _subscription())
    monkeypatch.setattr(prompts_router, "get_user", lambda db, user_id: SimpleNamespace(first_name="Owner", last_name=""))

    response = prompts_router.get_prompt_route("prompt-1", db=_Db(prompt), user=SimpleNamespace(id="viewer-1"))

    assert response.is_subscribed is True
    assert response.share_type == "live"
    assert not hasattr(response, "can_edit")
    assert response.clone_share_id is None
    assert response.live_share_id is None
    assert response.collaborate_share_id is None
    assert response.user_id is None


def test_unsubscribe_from_shared_prompt_requires_existing_subscription():
    db = _SubscriptionDb(None)

    with pytest.raises(HTTPException) as exc_info:
        prompt_models.unsubscribe_from_shared_prompt(db, "viewer-1", "prompt-1")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Prompt not found"
    assert db.deleted == []
    assert db.committed is False


def test_unsubscribe_from_shared_prompt_deletes_existing_subscription():
    subscription = SimpleNamespace(prompt_id="prompt-1", subscriber_id="viewer-1")
    db = _SubscriptionDb(subscription)

    result = prompt_models.unsubscribe_from_shared_prompt(db, "viewer-1", "prompt-1")

    assert result == {"ok": True, "deleted": True}
    assert db.deleted == [subscription]
    assert db.committed is True


def test_delete_prompt_route_does_not_audit_missing_non_owner_subscription(monkeypatch):
    prompt = _prompt()
    audit_calls = []

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(
        prompts_router,
        "unsubscribe_from_shared_prompt",
        lambda db, user_id, prompt_id: (_ for _ in ()).throw(HTTPException(status_code=404, detail="Prompt not found")),
    )
    monkeypatch.setattr(prompts_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        prompts_router.delete_prompt_route(
            "prompt-1",
            request=SimpleNamespace(headers={}),
            db=_Db(prompt),
            db_log=SimpleNamespace(),
            user=SimpleNamespace(id="viewer-1"),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Prompt not found"
    assert audit_calls == []


def test_list_prompts_redacts_owner_id_for_subscribers(monkeypatch):
    subscribed_prompt = _prompt(id="subscribed-prompt", user_id="owner-1")

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(prompts_router, "list_user_prompts", lambda db, user_id, **kwargs: [])
    monkeypatch.setattr(prompts_router, "get_subscribed_prompts", lambda db, user_id, **kwargs: [(subscribed_prompt, _subscription())])
    monkeypatch.setattr(prompts_router, "get_user", lambda db, user_id: SimpleNamespace(first_name="Owner", last_name="User"))

    response = prompts_router.list_prompts_route(db=SimpleNamespace(), user=SimpleNamespace(id="viewer-1"))

    assert len(response.items) == 1
    assert response.items[0].user_id is None
    assert response.items[0].owner_name == "Owner User"


def test_update_prompt_redacts_owner_id_for_subscribers(monkeypatch):
    prompt = _prompt()
    updated_prompt = _prompt(content="Updated content")

    monkeypatch.setattr(prompts_router, "ensure_prompts_enabled", lambda user, db: None)
    monkeypatch.setattr(prompts_router, "can_user_edit_prompt", lambda db, user_id, prompt_id: True)
    monkeypatch.setattr(prompts_router, "update_user_prompt", lambda **kwargs: updated_prompt)
    monkeypatch.setattr(prompts_router, "get_subscription_for_prompt", lambda db, user_id, prompt_id: _subscription(share_type="collaborate"))
    monkeypatch.setattr(prompts_router, "get_user", lambda db, user_id: SimpleNamespace(first_name="", last_name="", email="owner@example.test"))
    monkeypatch.setattr(prompts_router, "create_audit_log", lambda *args, **kwargs: None)

    response = prompts_router.update_prompt_route(
        "prompt-1",
        payload=SimpleNamespace(title=None, description=None, content="Updated content", expected_revision=1),
        request=SimpleNamespace(headers={}, client=None),
        db=_Db(prompt),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="viewer-1"),
    )

    assert response.user_id is None
    assert response.owner_name == "Unknown"
    assert not hasattr(response, "can_edit")
    assert response.share_type == "collaborate"


def test_shared_prompt_preview_uses_privacy_safe_owner_name(monkeypatch):
    prompt = _prompt()

    monkeypatch.setattr(prompt_models, "get_shared_prompt_by_share_id", lambda db, share_id, share_type=None: prompt)
    monkeypatch.setattr(prompt_models, "detect_share_type_from_id", lambda db, share_id: prompt_models.ShareType.LIVE)

    preview = prompt_models.get_shared_prompt_preview(
        _PreviewDb(None, SimpleNamespace(first_name="", last_name="", email="owner@example.test")),
        "share-1",
        requesting_user_id="viewer-1",
    )

    assert preview["owner_name"] == "Unknown"
