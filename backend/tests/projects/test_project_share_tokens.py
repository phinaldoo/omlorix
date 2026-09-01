import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
import pytest
from fastapi import HTTPException


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


from app.projects import router as projects_router
from app.projects.schemas import (
    InviteUsersToProjectRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)


def _project(**overrides):
    values = {
        "id": "project-1",
        "user_id": "owner-1",
        "title": "Shared project",
        "images": None,
        "videos": None,
        "audios": None,
        "documents": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "last_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "settings": {
            "icon": "",
            "icon_color": "",
            "system_instruction": "",
            "separate_memory_enabled": False,
        },
        "link_share_id": "share-token",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(user_id):
    return SimpleNamespace(id=user_id, group_id="group-1")


@pytest.mark.parametrize(
    ("route_name", "payload"),
    [
        ("create", ProjectCreateRequest(title="Project", separate_memory_enabled=True)),
        ("update", ProjectUpdateRequest(project_id="project-1", separate_memory_enabled=True)),
    ],
)
def test_project_routes_reject_enabling_project_memory_when_group_memory_is_disabled(
    monkeypatch,
    route_name,
    payload,
):
    """Direct API requests cannot bypass the group Memory feature policy."""
    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(
        projects_router,
        "get_group_setting_value",
        lambda group_id, page_name, key_name, db: False,
    )

    route = {
        "create": projects_router.create_project_route,
        "update": projects_router.update_project_route,
    }[route_name]

    with pytest.raises(HTTPException) as blocked:
        route(
            payload=payload,
            request=SimpleNamespace(headers={}),
            db=SimpleNamespace(),
            db_log=SimpleNamespace(),
            user=_user("owner-1"),
        )

    assert blocked.value.status_code == 403
    assert blocked.value.detail == "Memories feature disabled for your group"


def test_project_list_redacts_link_share_id_for_members(monkeypatch):
    project = _project()
    projects_data = [
        {
            "project": project,
            "is_owner": False,
            "is_shared": True,
            "member_count": 1,
            "owner_name": "Owner User",
        }
    ]

    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(projects_router, "list_projects_with_shared", lambda db, user_id: projects_data)

    response = projects_router.list_projects_route(db=SimpleNamespace(), user=_user("member-1"))
    shared_project = response["projects"][0]

    assert shared_project.is_owner is False
    assert shared_project.link_share_id is None
    assert shared_project.has_link_share is True


def test_project_list_keeps_link_share_id_for_owners(monkeypatch):
    project = _project()
    projects_data = [
        {
            "project": project,
            "is_owner": True,
            "is_shared": True,
            "member_count": 1,
            "owner_name": None,
        }
    ]

    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(projects_router, "list_projects_with_shared", lambda db, user_id: projects_data)

    response = projects_router.list_projects_route(db=SimpleNamespace(), user=_user("owner-1"))
    owned_project = response["projects"][0]

    assert owned_project.is_owner is True
    assert owned_project.link_share_id == "share-token"
    assert owned_project.has_link_share is True


def test_project_update_redacts_link_share_id_for_members(monkeypatch):
    project = _project(title="Updated project")

    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(projects_router, "get_project_with_access", lambda db, user_id, project_id: _project())
    monkeypatch.setattr(projects_router, "update_project_shared", lambda db, user_id, project_id, title, settings: project)

    response = projects_router.update_project_route(
        payload=ProjectUpdateRequest(project_id="project-1", title="Updated project"),
        request=SimpleNamespace(headers={}),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=_user("member-1"),
    )

    assert response["project"].link_share_id is None


def test_project_update_keeps_link_share_id_for_owners(monkeypatch):
    project = _project(title="Updated project")

    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(projects_router, "get_project_with_access", lambda db, user_id, project_id: _project())
    monkeypatch.setattr(projects_router, "update_project_shared", lambda db, user_id, project_id, title, settings: project)

    response = projects_router.update_project_route(
        payload=ProjectUpdateRequest(project_id="project-1", title="Updated project"),
        request=SimpleNamespace(headers={}),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=_user("owner-1"),
    )

    assert response["project"].link_share_id == "share-token"


def test_project_invite_surfaces_sharing_permission_from_share_creation(monkeypatch):
    monkeypatch.setattr(projects_router, "check_projects_access", lambda db, group_id: None)
    monkeypatch.setattr(projects_router, "is_project_owner", lambda db, user_id, project_id: True)
    monkeypatch.setattr(projects_router, "get_project_with_access", lambda db, user_id, project_id: _project())
    monkeypatch.setattr(
        projects_router,
        "create_project_link_share",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Project sharing is disabled for your group")
        ),
    )

    with pytest.raises(HTTPException) as blocked:
        projects_router.invite_users_to_project_route(
            payload=InviteUsersToProjectRequest(project_id="project-1", user_ids=["member-1"]),
            request=SimpleNamespace(headers={}),
            db=SimpleNamespace(),
            db_log=SimpleNamespace(),
            user=_user("owner-1"),
        )

    assert blocked.value.status_code == 403
