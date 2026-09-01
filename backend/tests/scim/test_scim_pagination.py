from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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

from app.scim import router as scim_router


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.20", 12345),
        }
    )


class _FakeQuery:
    def __init__(self, items):
        self._items = list(items)
        self._offset = 0
        self._limit = None
        self.count_calls = 0
        self.order_by_none_calls = 0

    def order_by(self, *args):
        if args == (None,):
            self.order_by_none_calls += 1
        return self

    def count(self):
        self.count_calls += 1
        return len(self._items)

    def offset(self, value):
        self._offset = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        if self._limit is None:
            return self._items[self._offset :]
        return self._items[self._offset : self._offset + self._limit]


def test_list_users_route_paginates_before_serializing(monkeypatch):
    users = [
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="user-2"),
        SimpleNamespace(id="user-3"),
    ]
    query = _FakeQuery(users)
    serialized_ids = []
    context_user_ids = []

    monkeypatch.setattr(scim_router, "_filter_users_query", lambda db, filter_text: query)

    def fake_context(db, page_users):
        context_user_ids.extend(user.id for user in page_users)
        return {
            "links_by_user_id": {},
            "memberships_by_user_id": {},
            "groups_by_id": {},
        }

    def fake_resource(user, db, request, **kwargs):
        serialized_ids.append(user.id)
        return {"id": user.id}

    monkeypatch.setattr(scim_router, "_user_scim_list_context", fake_context)
    monkeypatch.setattr(scim_router, "_user_to_scim_resource", fake_resource)

    response = scim_router.list_users(
        _request("GET", "/api/v1/scim/v2/Users"),
        startIndex=2,
        count=1,
        db=object(),
        settings={},
    )

    payload = json.loads(response.body)

    assert payload == {
        "schemas": [scim_router.SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": 3,
        "startIndex": 2,
        "itemsPerPage": 1,
        "Resources": [{"id": "user-2"}],
    }
    assert query.count_calls == 1
    assert query.order_by_none_calls == 1
    assert context_user_ids == ["user-2"]
    assert serialized_ids == ["user-2"]


def test_list_groups_route_paginates_before_serializing(monkeypatch):
    groups = [
        SimpleNamespace(id="group-1"),
        SimpleNamespace(id="group-2"),
        SimpleNamespace(id="group-3"),
    ]
    query = _FakeQuery(groups)
    serialized_ids = []
    context_group_ids = []

    monkeypatch.setattr(scim_router, "_filter_groups_query", lambda db, filter_text: query)

    def fake_context(db, page_groups):
        context_group_ids.extend(group.id for group in page_groups)
        return {
            "links_by_group_id": {},
            "memberships_by_group_id": {},
            "users_by_id": {},
        }

    def fake_resource(group, db, request, **kwargs):
        serialized_ids.append(group.id)
        return {"id": group.id}

    monkeypatch.setattr(scim_router, "_group_scim_list_context", fake_context)
    monkeypatch.setattr(scim_router, "_group_to_scim_resource", fake_resource)

    response = scim_router.list_groups_route(
        _request("GET", "/api/v1/scim/v2/Groups"),
        startIndex=2,
        count=1,
        db=object(),
        settings={},
    )

    payload = json.loads(response.body)

    assert payload == {
        "schemas": [scim_router.SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": 3,
        "startIndex": 2,
        "itemsPerPage": 1,
        "Resources": [{"id": "group-2"}],
    }
    assert query.count_calls == 1
    assert query.order_by_none_calls == 1
    assert context_group_ids == ["group-2"]
    assert serialized_ids == ["group-2"]


def test_user_to_scim_resource_uses_preloaded_groups(monkeypatch):
    request = _request("GET", "/api/v1/scim/v2/Users/user-1")
    timestamp = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        first_name="Ada",
        last_name="Lovelace",
        role="user",
        is_active=True,
        deleted_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    link = SimpleNamespace(external_id="ext-user", updated_at=timestamp)
    memberships = [SimpleNamespace(group_id="group-1")]
    groups_by_id = {
        "group-1": SimpleNamespace(id="group-1", name="Engineering"),
    }

    monkeypatch.setattr(scim_router, "_public_base_url", lambda request, db: "https://chat.example.com")
    monkeypatch.setattr(scim_router, "_scim_user_link", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected link lookup")))
    monkeypatch.setattr(
        scim_router,
        "_membership_rows_for_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected membership lookup")),
    )
    monkeypatch.setattr(scim_router, "get_group", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected group lookup")))

    payload = scim_router._user_to_scim_resource(
        user,
        db=object(),
        request=request,
        link=link,
        memberships=memberships,
        groups_by_id=groups_by_id,
    )

    assert payload["externalId"] == "ext-user"
    assert payload["groups"] == [
        {
            "value": "group-1",
            "$ref": "https://chat.example.com/api/v1/scim/v2/Groups/group-1",
            "display": "Engineering",
        }
    ]


def test_group_to_scim_resource_uses_preloaded_users(monkeypatch):
    request = _request("GET", "/api/v1/scim/v2/Groups/group-1")
    timestamp = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    group = SimpleNamespace(
        id="group-1",
        name="Engineering",
        created_at=timestamp,
        updated_at=timestamp,
    )
    link = SimpleNamespace(external_id="ext-group", updated_at=timestamp)
    memberships = [SimpleNamespace(user_id="user-1")]
    users_by_id = {
        "user-1": SimpleNamespace(id="user-1", email="user@example.com"),
    }

    monkeypatch.setattr(scim_router, "_public_base_url", lambda request, db: "https://chat.example.com")
    monkeypatch.setattr(scim_router, "_scim_group_link", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected link lookup")))
    monkeypatch.setattr(
        scim_router,
        "_membership_rows_for_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected membership lookup")),
    )
    monkeypatch.setattr(scim_router, "get_user", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected user lookup")))

    payload = scim_router._group_to_scim_resource(
        group,
        db=object(),
        request=request,
        link=link,
        memberships=memberships,
        users_by_id=users_by_id,
    )

    assert payload["externalId"] == "ext-group"
    assert payload["members"] == [
        {
            "value": "user-1",
            "$ref": "https://chat.example.com/api/v1/scim/v2/Users/user-1",
            "display": "user@example.com",
        }
    ]
