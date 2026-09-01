"""Admin group-form coverage for searchable manager role assignments."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ENCRYPTION_KEY"] = os.environ.get("ENCRYPTION_KEY") or Fernet.generate_key().decode()

from app.database import Base
from app.groups.models import Group, GroupManager, replace_group_manager_assignments
from app.admin.groups import router as group_router
from app.groups.management import (
    MANAGER_EDITABLE_RULES,
    ROLE_CAPABILITIES,
    _direct_regular_members,
    _ensure_manager_setting_is_editable,
    list_group_promotion_candidates,
)
from app.admin.groups.schemas import GROUP_FORM_SCHEMA, GroupCreate, GroupValuesUpdatePayload
from app.admin.groups import models as group_models
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User


def _session():
    """Create the relational tables used by the assignment workflow."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, Group.__table__, GroupManager.__table__],
    )
    return sessionmaker(bind=engine)()


def _group(group_id: str = "group-1") -> Group:
    """Build a group fixture; reads complete its sparse settings from defaults."""

    now = datetime.now(timezone.utc)
    return Group(
        id=group_id,
        name="Test Group",
        kind="standard",
        parent_id=None,
        settings={},
        created_at=now,
        updated_at=now,
    )


def _user(user_id: str, **overrides) -> User:
    """Build an eligible permanent user unless a test overrides eligibility."""

    now = datetime.now(timezone.utc)
    values = {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "hashed_password": "hashed",
        "first_name": user_id.title(),
        "last_name": "User",
        "role": "user",
        "group_id": "group-1",
        "account_type": "regular",
        "is_active": True,
        "settings": deepcopy(DEFAULT_USER_SETTINGS),
        "created_at": now,
        "last_active_at": now,
    }
    values.update(overrides)
    return User(**values)


def test_management_schema_uses_searchable_user_multiselects():
    """Keep delegated roles and temporary-account controls on one management page."""

    general = next(section for section in GROUP_FORM_SCHEMA.sections if section.key == "general")
    management = next(section for section in GROUP_FORM_SCHEMA.sections if section.key == "management")
    fields = {field.key: field for field in management.fields}
    role_fields = [
        fields["owner_user_ids"],
        fields["manager_user_ids"],
        fields["coordinator_user_ids"],
    ]

    assert [field.key for field in general.fields] == ["name", "parent_id"]
    assert [field.key for field in management.fields] == [
        "owner_user_ids",
        "manager_user_ids",
        "coordinator_user_ids",
        "settings.temporary_accounts.enabled",
        "settings.temporary_accounts.max_active_accounts",
        "settings.temporary_accounts.credential_length",
    ]
    assert not any(section.key == "temporary_access" for section in GROUP_FORM_SCHEMA.sections)
    assert all(field.type == "select" for field in role_fields)
    assert all(field.multiple is True for field in role_fields)
    assert all(field.searchable is True for field in role_fields)
    assert all(
        field.metadata.get("remote_options", {}).get("url")
        == "/api/v1/groups/manager-candidates"
        for field in role_fields
    )
    assert ROLE_CAPABILITIES["coordinator"] == {
        "view_group",
        "view_members",
        "manage_temporary_accounts",
    }
    assert all(
        "view_members" in capabilities
        for capabilities in ROLE_CAPABILITIES.values()
    )
    assert all(
        "manage_members" not in capabilities
        for capabilities in ROLE_CAPABILITIES.values()
    )
    assert "promote_members" in ROLE_CAPABILITIES["owner"]
    assert all(
        "manage_managers" not in capabilities
        for capabilities in ROLE_CAPABILITIES.values()
    )
    assert "teacher" not in ROLE_CAPABILITIES


def test_manager_feature_controls_are_complete_and_group_scoped():
    """Expose group-owned feature controls without delegating data or infra policy."""

    feature_paths = {
        "projects.enable_projects",
        "projects.allow_project_share",
        "todo.enabled_todo",
        "todo.allow_todo_list_share",
        "notes.enabled_notes",
        "notes.allow_notes_share",
        "memories.enabled_memories",
        "skills.enabled_skills",
        "skills.allow_skill_share",
        "prompts.enabled_prompts",
        "prompts.allow_prompt_share",
        "bookmarks.enabled_bookmarks",
        "bookmarks.allow_bookmark_share",
        "agents.allow_agents",
        "agents.allow_agent_share",
        "automations.enabled_automations",
        "sharing.enable_chat_sharing",
        "sharing.enable_artifact_sharing",
    }

    assert feature_paths <= set(MANAGER_EDITABLE_RULES)
    assert all(
        MANAGER_EDITABLE_RULES[path]["mode"] == "free"
        for path in feature_paths
    )
    assert not any(path.startswith("data_controls.") for path in MANAGER_EDITABLE_RULES)
    assert not any(path.startswith("tools_mcp.") for path in MANAGER_EDITABLE_RULES)

    _ensure_manager_setting_is_editable("projects", "enable_projects")
    with pytest.raises(HTTPException) as forbidden_error:
        _ensure_manager_setting_is_editable("data_controls", "allow_user_data")
    assert forbidden_error.value.status_code == 403


def test_managed_group_roster_lists_regular_members_read_only_and_pageable():
    """Delegated users can page through members without mutation behavior."""

    db = _session()
    db.add(_group())
    db.add_all(
        [
            _user("member-b", first_name="Beta", last_name="Member"),
            _user("member-a", first_name="Alpha", last_name="Member"),
            _user("temporary", account_type="temporary"),
            _user("elsewhere", group_id="other-group"),
        ]
    )
    db.commit()

    first_page = _direct_regular_members(db, "group-1", offset=0, limit=1)
    second_page = _direct_regular_members(db, "group-1", offset=1, limit=1)

    assert [entry["id"] for entry in first_page] == ["member-a"]
    assert [entry["id"] for entry in second_page] == ["member-b"]


def test_promotion_candidates_include_all_direct_users_with_eligibility():
    """The select shows every direct permanent user while disabling invalid targets."""

    db = _session()
    admin = _user("admin", role="admin")
    member = _user("member")
    inactive = _user("inactive", is_active=False)
    owner = _user("owner")
    temporary = _user("temporary", account_type="temporary")
    elsewhere = _user("elsewhere", group_id="other-group")
    db.add_all([_group(), admin, member, inactive, owner, temporary, elsewhere])
    db.add(GroupManager(
        group_id="group-1",
        user_id=owner.id,
        role="owner",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    db.commit()

    page = list_group_promotion_candidates(
        db,
        admin,
        "group-1",
        offset=0,
        limit=100,
    )
    by_id = {entry["id"]: entry for entry in page["items"]}

    assert page["total"] == 4  # admin, member, inactive, and owner
    assert set(by_id) == {"admin", "member", "inactive", "owner"}
    assert by_id["member"]["eligible"] is True
    assert by_id["inactive"]["eligible"] is False
    assert by_id["owner"]["current_role"] == "owner"
    assert by_id["owner"]["eligible"] is False


def test_manager_role_payload_rejects_the_same_user_in_multiple_roles():
    """Prevent ambiguous role precedence in both create and update payloads."""

    with pytest.raises(ValidationError):
        GroupCreate(
            name="Duplicate roles",
            owner_user_ids=["user-1"],
            manager_user_ids=["user-1"],
        )

    with pytest.raises(ValidationError):
        GroupValuesUpdatePayload(
            owner_user_ids=["user-1"],
            manager_user_ids=[],
            coordinator_user_ids=["user-1"],
        )


def test_admin_create_route_persists_initial_manager_roles(monkeypatch):
    """Create the group and its selected management roles in one request."""

    db = _session()
    owner = _user("owner")
    manager = _user("manager")
    db.add_all([owner, manager])
    db.commit()
    monkeypatch.setattr(group_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(group_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")

    response = group_router.create_group_route(
        GroupCreate(
            name="Managed Group",
            owner_user_ids=[owner.id],
            manager_user_ids=[manager.id],
        ),
        Request({"type": "http", "headers": []}),
        db,
        db,
        owner,
    )

    assert response["direct_manager_count"] == 2
    assert {
        (assignment.user_id, assignment.role)
        for assignment in db.query(GroupManager)
        .filter(GroupManager.group_id == response["id"])
        .all()
    } == {(owner.id, "owner"), (manager.id, "manager")}


def test_replacement_can_swap_owner_and_change_roles_atomically():
    """Evaluate the final submitted state instead of blocking an owner swap."""

    db = _session()
    old_owner = _user("old-owner")
    new_owner = _user("new-owner")
    coordinator = _user("coordinator")
    db.add_all([_group(), old_owner, new_owner, coordinator])
    db.add(
        GroupManager(
            group_id="group-1",
            user_id=old_owner.id,
            role="owner",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    result = replace_group_manager_assignments(
        db,
        group_id="group-1",
        owner_user_ids=[new_owner.id],
        manager_user_ids=[old_owner.id],
        coordinator_user_ids=[coordinator.id],
    )

    assert result["total"] == 3
    assert {
        (assignment.user_id, assignment.role)
        for assignment in db.query(GroupManager).all()
    } == {
        (new_owner.id, "owner"),
        (old_owner.id, "manager"),
        (coordinator.id, "coordinator"),
    }


def test_replacement_cannot_remove_the_final_active_owner():
    """Keep the existing final-owner lifecycle invariant in the admin form."""

    db = _session()
    owner = _user("owner")
    db.add_all([_group(), owner])
    db.add(
        GroupManager(
            group_id="group-1",
            user_id=owner.id,
            role="owner",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        replace_group_manager_assignments(
            db,
            group_id="group-1",
            owner_user_ids=[],
            manager_user_ids=[],
            coordinator_user_ids=[],
        )

    assert exc_info.value.status_code == 409
    assert db.query(GroupManager).filter(GroupManager.role == "owner").count() == 1


def test_group_form_hydrates_eligible_users_and_current_roles(monkeypatch):
    """Embed current selections without serializing the entire user directory."""

    db = _session()
    owner = _user("owner")
    unrelated = _user("unrelated")
    inactive = _user("inactive", is_active=False)
    db.add_all([_group(), owner, unrelated, inactive])
    db.add(
        GroupManager(
            group_id="group-1",
            user_id=owner.id,
            role="owner",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    # These unrelated dynamic selectors require additional feature tables.
    # The test keeps its schema focused on manager-user hydration.
    monkeypatch.setattr(group_models, "_hydrate_model_selects", lambda *_args: None)
    monkeypatch.setattr(group_models, "_hydrate_admin_skills_select", lambda *_args: None)
    monkeypatch.setattr(group_models, "_hydrate_byok_allowed_tools_select", lambda *_args: None)
    monkeypatch.setattr(group_models, "_hydrate_byok_websearch_provider_selects", lambda *_args: None)

    schema = group_models.get_group_form_schema(db, "group-1")
    management = next(section for section in schema.sections if section.key == "management")
    fields = {field.key: field for field in management.fields}

    assert fields["owner_user_ids"].value == [owner.id]
    assert fields["manager_user_ids"].value == []
    assert [option.value for option in fields["owner_user_ids"].options] == [owner.id]
    assert fields["owner_user_ids"].options[0].i18n_label is None
    assert fields["owner_user_ids"].options[0].translatable is False


def test_manager_candidate_options_are_bounded_and_search_email_server_side():
    """Return a small eligible page instead of embedding all users in schemas."""

    db = _session()
    db.add(_group())
    db.add_all(
        [
            _user("alpha", email="alpha@example.com"),
            _user("beta", email="beta@example.com"),
            _user("inactive", email="inactive@example.com", is_active=False),
            _user("temporary", email="temporary@example.com", account_type="temporary"),
        ]
    )
    db.commit()

    first_page = group_models.list_group_manager_candidate_options(
        db,
        search=None,
        offset=0,
        limit=1,
    )
    searched = group_models.list_group_manager_candidate_options(
        db,
        search="BETA@",
        offset=0,
        limit=100,
    )

    assert len(first_page["options"]) == 1
    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert [option["value"] for option in searched["options"]] == ["beta"]
