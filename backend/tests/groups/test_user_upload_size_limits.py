from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.admin.groups.schemas import FIELD_SCHEMA_BY_KEY
from app.users.upload_limits import (
    CUSTOM_PROFILE_PICTURE_MAX_SIZE_MB,
)


def test_user_upload_size_limits_are_hardcoded_not_group_settings():
    """Keep upload size caps out of the group settings contract."""
    user_group_defaults = DEFAULT_GROUP_SETTINGS["users"]

    assert "custom_profile_picture_max_size_mb" not in user_group_defaults
    assert "settings.users.custom_profile_picture_max_size_mb" not in FIELD_SCHEMA_BY_KEY
    assert CUSTOM_PROFILE_PICTURE_MAX_SIZE_MB == 5
