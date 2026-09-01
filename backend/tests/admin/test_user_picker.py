from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.users import admin_management as user_utils
from app.users import models as user_models


def _user(index: int, email: str, first_name: str, last_name: str):
    return SimpleNamespace(
        id=f"user-{index}",
        email=email,
        first_name=first_name,
        last_name=last_name,
        role="user",
        group_id="group-1",
        is_active=True,
        created_at=datetime(2026, 1, index, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 2, index, tzinfo=timezone.utc),
    )


def test_user_picker_page_paginates_without_dropping_users(monkeypatch):
    users = [
        _user(index, f"user{index}@example.com", f"User{index}", "Example")
        for index in range(1, 6)
    ]
    monkeypatch.setattr(
        user_utils,
        "query_admin_users_page",
        lambda _db, **kwargs: (
            users[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]],
            len(users),
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_group_lookup_for_users",
        lambda _db, _users: {"group-1": "Staff"},
    )

    page = user_utils.get_user_list_page(object(), limit=2, offset=2)

    assert [entry["id"] for entry in page["users"]] == ["user-3", "user-4"]
    assert page["total"] == 5
    assert page["offset"] == 2
    assert page["limit"] == 2
    assert page["has_more"] is True


def test_user_picker_page_searches_before_paginating(monkeypatch):
    users = [
        _user(1, "ada@example.com", "Ada", "Lovelace"),
        _user(2, "grace@example.com", "Grace", "Hopper"),
        _user(3, "another-grace@example.com", "Grace", "Murray"),
    ]

    def query_page(_db, *, search, limit, offset):
        matching = [user for user in users if search in user.email]
        return matching[offset : offset + limit], len(matching)

    monkeypatch.setattr(user_utils, "query_admin_users_page", query_page)
    monkeypatch.setattr(
        user_utils,
        "_group_lookup_for_users",
        lambda _db, _users: {"group-1": "Staff"},
    )

    page = user_utils.get_user_list_page(object(), search="grace", limit=1, offset=1)

    assert [entry["email"] for entry in page["users"]] == ["another-grace@example.com"]
    assert page["total"] == 2
    assert page["has_more"] is False


def test_admin_user_list_limit_zero_returns_no_rows_without_querying(monkeypatch):
    monkeypatch.setattr(
        user_utils,
        "query_admin_users_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("limit=0 should return before querying")
        ),
    )

    assert user_utils.get_user_list(object(), limit=0) == []


def test_user_picker_limit_zero_keeps_total_without_returning_rows(monkeypatch):
    captured = {}

    def query_page(_db, *, search, limit, offset):
        captured.update(search=search, limit=limit, offset=offset)
        return [], 7

    monkeypatch.setattr(user_utils, "query_admin_users_page", query_page)

    page = user_utils.get_user_list_page(object(), limit=0, offset=3)

    assert captured == {"search": None, "limit": 0, "offset": 3}
    assert page == {
        "users": [],
        "total": 7,
        "offset": 3,
        "limit": 0,
        "has_more": True,
    }


def test_admin_user_query_applies_filter_count_offset_and_limit_in_database():
    calls = []

    class TrackingQuery:
        def filter(self, *_criteria):
            calls.append(("filter", len(_criteria)))
            return self

        def order_by(self, *_columns):
            calls.append(("order_by", len(_columns)))
            return self

        def count(self):
            calls.append(("count",))
            return 11

        def offset(self, value):
            calls.append(("offset", value))
            return self

        def limit(self, value):
            calls.append(("limit", value))
            return self

        def all(self):
            calls.append(("all",))
            return ["bounded-user-row"]

    class TrackingDb:
        def query(self, model):
            assert model is user_models.User
            calls.append(("query",))
            return TrackingQuery()

    rows, total = user_models.query_admin_users_page(
        TrackingDb(), search=" Person ", limit=25, offset=50
    )

    assert rows == ["bounded-user-row"]
    assert total == 11
    assert calls == [
        ("query",),
        ("filter", 1),
        ("filter", 1),
        ("order_by", 1),
        ("count",),
        ("order_by", 2),
        ("offset", 50),
        ("limit", 25),
        ("all",),
    ]
