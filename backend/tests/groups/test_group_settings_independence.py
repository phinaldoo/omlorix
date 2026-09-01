"""Group settings remain stable when hierarchy links or parent policies change."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.init import get_group_settings, update_group_settings
from app.groups.models import Group, GroupManager
from app.admin.groups.models import update_group
from app.users.models import User


def _session():
    """Create the minimal in-memory database required by group settings."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, Group.__table__, GroupManager.__table__],
    )
    return sessionmaker(bind=engine)()


def _group(group_id: str, *, parent_id: str | None, settings: dict) -> Group:
    """Build a group row with an explicit settings payload."""

    now = datetime.now(timezone.utc)
    return Group(
        id=group_id,
        name=group_id.title(),
        kind="standard",
        parent_id=parent_id,
        settings=settings,
        created_at=now,
        updated_at=now,
    )


def test_parent_changes_do_not_change_child_settings():
    """A child owns its settings even while it remains linked to a parent."""

    db = _session()
    parent_settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    parent_settings["files"]["max_files_upload_count"] = 7
    child_settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    child_settings["files"]["max_files_upload_count"] = 3
    db.add_all(
        [
            _group("parent", parent_id=None, settings=parent_settings),
            _group("child", parent_id="parent", settings=child_settings),
        ]
    )
    db.commit()

    update_group_settings("parent", "files", "max_files_upload_count", 12, db)

    assert get_group_settings("parent", db)["files"]["max_files_upload_count"] == 12
    assert get_group_settings("child", db)["files"]["max_files_upload_count"] == 3


def test_unlinking_group_preserves_its_complete_snapshot():
    """Removing a parent changes hierarchy metadata and nothing else."""

    db = _session()
    parent_settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    child_settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    child_settings["sharing"]["enable_chat_sharing"] = False
    db.add_all(
        [
            _group("parent", parent_id=None, settings=parent_settings),
            _group("child", parent_id="parent", settings=child_settings),
        ]
    )
    db.commit()
    before = get_group_settings("child", db)

    update_group("child", None, None, db, parent_id="")

    child = db.query(Group).filter(Group.id == "child").one()
    assert child.parent_id is None
    assert get_group_settings("child", db) == before


def test_read_repairs_sparse_rows_with_defaults_not_parent_values():
    """Sparse rows become complete without consulting the parent."""

    db = _session()
    parent_settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    parent_settings["files"]["max_files_upload_count"] = 9
    db.add_all(
        [
            _group("parent", parent_id=None, settings=parent_settings),
            _group("child", parent_id="parent", settings={}),
        ]
    )
    db.commit()

    child_settings = get_group_settings("child", db)

    assert child_settings == DEFAULT_GROUP_SETTINGS
    assert child_settings["files"]["max_files_upload_count"] != 9
    assert db.query(Group).filter(Group.id == "child").one().settings == DEFAULT_GROUP_SETTINGS
