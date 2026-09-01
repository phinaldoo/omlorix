from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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

from app.database import Base
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.models import Group
from app.scim import router as scim_router
from app.scim.models import ScimGroupLink, ScimGroupMembership, ScimUserLink
from app.utils import encryption as encryption_utils
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Group.__table__,
            ScimUserLink.__table__,
            ScimGroupLink.__table__,
            ScimGroupMembership.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _fixed_public_base_url(monkeypatch):
    monkeypatch.setattr(scim_router, "_public_base_url", lambda request, db: "https://omlorix.test")
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("omlorix.test", 443),
            "method": method,
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.20", 12345),
        }
    )


def _group(group_id: str, name: str, created_at: datetime) -> Group:
    return Group(
        id=group_id,
        name=name,
        kind="standard",
        parent_id=None,
        settings=deepcopy(DEFAULT_GROUP_SETTINGS),
        created_at=created_at,
        updated_at=created_at,
    )


def _user(user_id: str, email: str, group_id: str, created_at: datetime) -> User:
    user = User(
        id=user_id,
        email=email,
        group_id=group_id,
        hashed_password="hashed-password",
        first_name="Test",
        last_name="User",
        role="user",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=True,
        created_at=created_at,
        last_active_at=created_at,
    )
    return user


def test_list_users_paginates_before_serializing_resources(db_session, monkeypatch):
    created_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    default_group = _group("default", "Default", created_at)
    db_session.add(default_group)
    for index in range(5):
        db_session.add(
            _user(
                f"user-{index + 1}",
                f"user{index + 1}@example.com",
                default_group.id,
                created_at + timedelta(minutes=index),
            )
        )
    db_session.commit()

    serialized_user_ids = []

    def fake_user_to_scim_resource(user, db, request, **kwargs):
        serialized_user_ids.append(user.id)
        return {"id": user.id}

    monkeypatch.setattr(scim_router, "_user_to_scim_resource", fake_user_to_scim_resource)

    response = scim_router.list_users(
        _request("GET", "/api/v1/scim/v2/Users"),
        startIndex=2,
        count=2,
        db=db_session,
        settings={},
    )

    payload = json.loads(response.body)
    assert serialized_user_ids == ["user-2", "user-3"]
    assert payload == {
        "schemas": [scim_router.SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": 5,
        "startIndex": 2,
        "itemsPerPage": 2,
        "Resources": [{"id": "user-2"}, {"id": "user-3"}],
    }


def test_list_users_returns_linked_groups_for_paged_results(db_session):
    created_at = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
    default_group = _group("default", "Default", created_at)
    engineering = _group("group-eng", "Engineering", created_at + timedelta(minutes=1))
    support = _group("group-support", "Support", created_at + timedelta(minutes=2))
    db_session.add_all([default_group, engineering, support])

    first_user = _user("user-1", "user1@example.com", default_group.id, created_at + timedelta(minutes=3))
    second_user = _user("user-2", "user2@example.com", default_group.id, created_at + timedelta(minutes=4))
    third_user = _user("user-3", "user3@example.com", default_group.id, created_at + timedelta(minutes=5))
    db_session.add_all([first_user, second_user, third_user])
    db_session.add(ScimUserLink(user_id=second_user.id, external_id="ext-user-2", created_at=created_at, updated_at=created_at))
    db_session.add_all(
        [
            ScimGroupMembership(
                user_id=second_user.id,
                group_id=engineering.id,
                priority=0,
                created_at=created_at,
                updated_at=created_at,
            ),
            ScimGroupMembership(
                user_id=second_user.id,
                group_id=support.id,
                priority=1,
                created_at=created_at + timedelta(seconds=1),
                updated_at=created_at + timedelta(seconds=1),
            ),
        ]
    )
    db_session.commit()

    response = scim_router.list_users(
        _request("GET", "/api/v1/scim/v2/Users"),
        startIndex=2,
        count=1,
        db=db_session,
        settings={},
    )

    payload = json.loads(response.body)
    assert payload["totalResults"] == 3
    assert payload["itemsPerPage"] == 1
    assert payload["Resources"][0]["id"] == second_user.id
    assert payload["Resources"][0]["externalId"] == "ext-user-2"
    assert payload["Resources"][0]["groups"] == [
        {
            "value": engineering.id,
            "$ref": f"https://omlorix.test/api/v1/scim/v2/Groups/{engineering.id}",
            "display": "Engineering",
        },
        {
            "value": support.id,
            "$ref": f"https://omlorix.test/api/v1/scim/v2/Groups/{support.id}",
            "display": "Support",
        },
    ]


def test_list_groups_paginates_before_serializing_resources(db_session, monkeypatch):
    created_at = datetime(2026, 1, 3, 15, 0, tzinfo=timezone.utc)
    for index in range(5):
        db_session.add(_group(f"group-{index + 1}", f"Group {index + 1}", created_at + timedelta(minutes=index)))
    db_session.commit()

    serialized_group_ids = []

    def fake_group_to_scim_resource(group, db, request, **kwargs):
        serialized_group_ids.append(group.id)
        return {"id": group.id}

    monkeypatch.setattr(scim_router, "_group_to_scim_resource", fake_group_to_scim_resource)

    response = scim_router.list_groups_route(
        _request("GET", "/api/v1/scim/v2/Groups"),
        startIndex=3,
        count=2,
        db=db_session,
        settings={},
    )

    payload = json.loads(response.body)
    assert serialized_group_ids == ["group-3", "group-4"]
    assert payload == {
        "schemas": [scim_router.SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": 5,
        "startIndex": 3,
        "itemsPerPage": 2,
        "Resources": [{"id": "group-3"}, {"id": "group-4"}],
    }


def test_list_groups_returns_linked_members_for_paged_results(db_session):
    created_at = datetime(2026, 1, 4, 10, 0, tzinfo=timezone.utc)
    default_group = _group("default", "Default", created_at)
    first_group = _group("group-1", "Group 1", created_at + timedelta(minutes=1))
    second_group = _group("group-2", "Group 2", created_at + timedelta(minutes=2))
    db_session.add_all([default_group, first_group, second_group])

    first_user = _user("user-1", "user1@example.com", default_group.id, created_at + timedelta(minutes=3))
    second_user = _user("user-2", "user2@example.com", default_group.id, created_at + timedelta(minutes=4))
    db_session.add_all([first_user, second_user])
    db_session.add(ScimGroupLink(group_id=second_group.id, external_id="ext-group-2", created_at=created_at, updated_at=created_at))
    db_session.add_all(
        [
            ScimGroupMembership(
                user_id=first_user.id,
                group_id=second_group.id,
                priority=0,
                created_at=created_at,
                updated_at=created_at,
            ),
            ScimGroupMembership(
                user_id=second_user.id,
                group_id=second_group.id,
                priority=1,
                created_at=created_at + timedelta(seconds=1),
                updated_at=created_at + timedelta(seconds=1),
            ),
        ]
    )
    db_session.commit()

    response = scim_router.list_groups_route(
        _request("GET", "/api/v1/scim/v2/Groups"),
        startIndex=3,
        count=1,
        db=db_session,
        settings={},
    )

    payload = json.loads(response.body)
    assert payload["totalResults"] == 3
    assert payload["itemsPerPage"] == 1
    assert payload["Resources"][0]["id"] == second_group.id
    assert payload["Resources"][0]["externalId"] == "ext-group-2"
    assert payload["Resources"][0]["members"] == [
        {
            "value": first_user.id,
            "$ref": f"https://omlorix.test/api/v1/scim/v2/Users/{first_user.id}",
            "display": first_user.email,
        },
        {
            "value": second_user.id,
            "$ref": f"https://omlorix.test/api/v1/scim/v2/Users/{second_user.id}",
            "display": second_user.email,
        },
    ]
