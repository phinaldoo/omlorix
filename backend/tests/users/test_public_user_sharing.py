import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.users import sharing


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


def _group(group_id: str, parent_id: str | None = None):
    return SimpleNamespace(id=group_id, parent_id=parent_id)


def _user(
    user_id: str,
    *,
    first_name: str = "",
    last_name: str = "",
    email: str | None = None,
    profile_visibility: str = "public",
    is_active: bool = True,
    role: str = "user",
    deleted_at=None,
):
    return SimpleNamespace(
        id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email or f"{user_id}@example.com",
        is_active=is_active,
        role=role,
        deleted_at=deleted_at,
        settings={"security": {"profile_visibility": profile_visibility}},
        last_active_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_get_allowed_public_user_group_ids_limits_results_to_same_lineage(monkeypatch):
    groups = {
        "root": _group("root"),
        "team-a": _group("team-a", "root"),
        "team-a-child": _group("team-a-child", "team-a"),
        "team-b": _group("team-b", "root"),
    }
    children = {
        "root": [groups["team-a"], groups["team-b"]],
        "team-a": [groups["team-a-child"]],
        "team-a-child": [],
        "team-b": [],
    }

    monkeypatch.setattr(sharing, "get_group", lambda _db, group_id: groups.get(group_id))
    monkeypatch.setattr(sharing, "get_group_children", lambda _db, group_id: list(children.get(group_id, [])))

    requester = SimpleNamespace(group_id="team-a")

    assert sharing.get_allowed_public_user_group_ids(object(), requester) == {"root", "team-a", "team-a-child"}


def test_get_public_users_for_sharing_applies_public_filter_search_and_pagination(monkeypatch):
    rows = [
        _user("user-1", first_name="Alice", last_name="Example", email="alice@example.com"),
        _user("user-2", first_name="Bob", last_name="Hidden", profile_visibility="private"),
        _user("user-3", first_name="Carla", last_name="Example", email="carla@example.com"),
        _user("user-4", first_name="Dan", last_name="Example", email="dan@example.com"),
    ]

    monkeypatch.setattr(sharing, "_public_user_candidate_query", lambda *_args, **_kwargs: _FakeQuery(rows))

    requester = SimpleNamespace(id="requester-1")

    page, meta = sharing.get_public_users_for_sharing(object(), requester, limit=2, offset=1)
    assert [user["id"] for user in page] == ["user-3", "user-4"]
    assert meta == {"total": 3, "limit": 2, "offset": 1, "has_more": False}

    search_page, search_meta = sharing.get_public_users_for_sharing(object(), requester, q="alice", limit=10, offset=0)
    assert search_page == [{"id": "user-1", "display_name": "Alice Example"}]
    assert search_meta == {"total": 1, "limit": 10, "offset": 0, "has_more": False}

    email_search_page, email_search_meta = sharing.get_public_users_for_sharing(
        object(),
        requester,
        q="carla@example.com",
        limit=10,
        offset=0,
    )
    assert email_search_page == []
    assert email_search_meta == {"total": 0, "limit": 10, "offset": 0, "has_more": False}


def test_resolve_invitable_users_for_sharing_ignores_unavailable_targets_when_valid_users_remain(monkeypatch):
    visible_user = _user("user-1", first_name="Visible", email="visible@example.com")

    monkeypatch.setattr(sharing, "_public_user_candidate_query", lambda *_args, **_kwargs: _FakeQuery([visible_user]))

    requester = SimpleNamespace(id="owner-1")

    resolved = sharing.resolve_invitable_users_for_sharing(object(), requester, ["user-1", "user-2"])

    assert [user.id for user in resolved] == ["user-1"]


def test_resolve_invitable_users_for_sharing_rejects_when_no_valid_targets_remain(monkeypatch):
    monkeypatch.setattr(sharing, "_public_user_candidate_query", lambda *_args, **_kwargs: _FakeQuery([]))

    requester = SimpleNamespace(id="owner-1")

    with pytest.raises(HTTPException) as exc_info:
        sharing.resolve_invitable_users_for_sharing(object(), requester, ["user-2"])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "One or more selected users are no longer available to invite"
