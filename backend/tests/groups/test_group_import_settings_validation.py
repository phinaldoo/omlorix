from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.init import get_group_settings
from app.groups.models import (
    Group,
    current_group_export_version,
    import_groups,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Group.__table__])
    return sessionmaker(bind=engine)()


def _payload(group_entry: dict) -> dict:
    return {
        "export_type": "group",
        "export_version": current_group_export_version,
        "data": {"groups": [group_entry]},
    }


def test_import_groups_rejects_invalid_known_setting_values():
    db = _session()

    result = import_groups(
        db,
        _payload(
            {
                "id": "bad-settings",
                "name": "Bad Settings",
                "settings": {
                    "data_controls": {"allow_user_data": "false"},
                    "files": {"max_files_upload_count": 0},
                },
            }
        ),
    )

    assert result["created"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["name"] == "Bad Settings"
    assert "settings.data_controls.allow_user_data" in result["errors"][0]["error"]
    assert db.query(Group).filter(Group.id == "bad-settings").first() is None


def test_import_groups_sanitizes_settings_before_storage():
    db = _session()

    result = import_groups(
        db,
        _payload(
            {
                "id": "clean-settings",
                "name": "Clean Settings",
                "settings": {
                    "data_controls": {
                        "allow_user_data": False,
                        "unsupported": False,
                    },
                    "unknown_page": {"surprise": True},
                    "files": {"max_files_upload_count": "2"},
                },
            }
        ),
    )

    assert result["errors"] == []
    assert result["created"] == [{"id": "clean-settings", "name": "Clean Settings"}]

    group = db.query(Group).filter(Group.id == "clean-settings").one()
    expected = deepcopy(DEFAULT_GROUP_SETTINGS)
    expected["data_controls"]["allow_user_data"] = False
    expected["files"]["max_files_upload_count"] = 2
    assert group.settings == expected

    settings = get_group_settings("clean-settings", db)
    assert settings["data_controls"]["allow_user_data"] is False
    assert "allow_files" not in settings["data_controls"]
    assert settings["files"]["max_files_upload_count"] == 2


def test_group_settings_read_path_drops_preexisting_malformed_values():
    db = _session()
    now = datetime.now(timezone.utc)
    db.add(
        Group(
            id="legacy-bad-settings",
            name="Legacy Bad Settings",
            settings={
                "data_controls": {"allow_user_data": "false", "allow_files": False},
                "unknown_page": {"surprise": True},
            },
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    settings = get_group_settings("legacy-bad-settings", db)

    group = db.query(Group).filter(Group.id == "legacy-bad-settings").one()
    expected = deepcopy(DEFAULT_GROUP_SETTINGS)
    assert group.settings == expected
    assert settings["data_controls"]["allow_user_data"] is DEFAULT_GROUP_SETTINGS["data_controls"]["allow_user_data"]
    assert "allow_files" not in settings["data_controls"]


def test_version_one_import_keeps_parent_and_child_snapshots_independent():
    """The current format imports each group's complete snapshot independently."""

    db = _session()
    parent_snapshot = deepcopy(DEFAULT_GROUP_SETTINGS)
    parent_snapshot["files"]["max_files_upload_count"] = 8
    child_snapshot = deepcopy(parent_snapshot)
    child_snapshot["sharing"]["enable_chat_sharing"] = False
    sibling_snapshot = deepcopy(parent_snapshot)
    sibling_snapshot["files"]["max_files_upload_count"] = 3
    payload = {
        "export_type": "group",
        "export_version": current_group_export_version,
        "data": {
            "groups": [
                {
                    "id": "snapshot-parent",
                    "name": "Snapshot Parent",
                    "settings": parent_snapshot,
                },
                {
                    "id": "snapshot-child",
                    "name": "Snapshot Child",
                    "parent_id": "snapshot-parent",
                    "settings": child_snapshot,
                },
                {
                    "id": "snapshot-sibling",
                    "name": "Snapshot Sibling",
                    "parent_id": "snapshot-parent",
                    "settings": sibling_snapshot,
                },
            ]
        },
    }

    result = import_groups(db, payload)

    assert result["errors"] == []
    parent_settings = get_group_settings("snapshot-parent", db)
    child_settings = get_group_settings("snapshot-child", db)
    sibling_settings = get_group_settings("snapshot-sibling", db)

    assert parent_settings["files"]["max_files_upload_count"] == 8
    assert parent_settings["sharing"]["enable_chat_sharing"] is True
    assert child_settings["files"]["max_files_upload_count"] == 8
    assert child_settings["sharing"]["enable_chat_sharing"] is False
    assert sibling_settings["files"]["max_files_upload_count"] == 3
    assert sibling_settings["sharing"]["enable_chat_sharing"] is True
