from types import SimpleNamespace

from app.admin.groups import models as group_models
from app.admin.groups.schemas import GROUP_FORM_SCHEMA
from app.groups.sensitive import (
    filter_settings_for_response,
    mask_sensitive_settings_for_response,
    resolve_sensitive_setting_update,
)
from app.utils.schemas import populate_sections_with_values


def _artificial_analysis_field(schema):
    """Return the Artificial Analysis secret field from a group form schema."""
    return next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "settings.leaderboard.artificial_analysis_api_key"
    )


def _artificial_analysis_data_level_field(schema):
    """Return the Artificial Analysis data-level select from the form schema."""
    return next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "settings.leaderboard.artificial_analysis_data_level"
    )


def test_group_form_schema_exposes_free_and_full_leaderboard_data_levels():
    field = _artificial_analysis_data_level_field(GROUP_FORM_SCHEMA)

    assert field.type == "select"
    assert field.default == "free"
    assert [option.value for option in field.options] == ["free", "full"]
    assert field.dependency == "settings.leaderboard.enabled"
    assert field.dependency_value is True


def test_mask_sensitive_settings_for_response_redacts_plaintext_secret():
    settings = {
        "leaderboard": {
            "enabled": True,
            "artificial_analysis_api_key": "aa-secret-value",
        },
        "chat": {"allow_temporary_chat": True},
    }

    result = mask_sensitive_settings_for_response(settings)

    assert result["leaderboard"]["artificial_analysis_api_key"] != "aa-secret-value"
    assert "secret-value" not in result["leaderboard"]["artificial_analysis_api_key"]
    assert result["leaderboard"]["enabled"] is True
    assert result["chat"]["allow_temporary_chat"] is True
    assert settings["leaderboard"]["artificial_analysis_api_key"] == "aa-secret-value"


def test_filter_settings_for_response_only_returns_allowed_paths_and_masks_secrets():
    settings = {
        "leaderboard": {
            "enabled": True,
            "artificial_analysis_api_key": "aa-secret-value",
        },
        "chat": {
            "allow_temporary_chat": True,
            "chat_box_warning_message": "Use responsibly",
        },
        "files": {"allow_file_uploads": False},
    }

    result = filter_settings_for_response(
        settings,
        {
            "chat.allow_temporary_chat": {"mode": "tighten_only_bool"},
            "leaderboard.artificial_analysis_api_key": {"mode": "free"},
        },
    )

    assert result == {
        "chat": {"allow_temporary_chat": True},
        "leaderboard": {"artificial_analysis_api_key": "aa-..."},
    }


def test_group_form_schema_marks_an_existing_api_key_without_exposing_it():
    schema = GROUP_FORM_SCHEMA.model_copy(deep=True)

    populate_sections_with_values(
        schema,
        {
            "settings": {
                "leaderboard": {
                    "artificial_analysis_api_key": "aa-secret-value",
                }
            }
        },
    )

    field = _artificial_analysis_field(schema)
    assert field.masked_value_set is True
    assert field.placeholder == "aa-..."
    assert field.value is None
    assert "secret-value" not in field.model_dump_json()


def test_group_form_schema_uses_a_fixed_mask_for_short_existing_secrets():
    schema = GROUP_FORM_SCHEMA.model_copy(deep=True)

    populate_sections_with_values(
        schema,
        {"settings": {"leaderboard": {"artificial_analysis_api_key": "short"}}},
    )

    field = _artificial_analysis_field(schema)
    assert field.masked_value_set is True
    assert field.placeholder == "********"
    assert "short" not in field.model_dump_json()


def test_sensitive_group_update_preserves_only_matching_ui_markers():
    current = "aa-secret-value"

    assert resolve_sensitive_setting_update(
        "leaderboard", "artificial_analysis_api_key", "aa-...", current
    ) == current
    assert resolve_sensitive_setting_update(
        "leaderboard", "artificial_analysis_api_key", "new-secret", current
    ) == "new-secret"
    assert resolve_sensitive_setting_update(
        "leaderboard", "artificial_analysis_api_key", "", current
    ) == ""


def test_group_values_update_keeps_an_untouched_masked_api_key(monkeypatch):
    current = {
        "leaderboard": {
            "enabled": True,
            "artificial_analysis_api_key": "aa-secret-value",
        }
    }
    captured = {}

    monkeypatch.setattr(group_models, "get_group_settings", lambda *_args, **_kwargs: current)

    def capture_update(_group_id, _name, settings, _db, **_kwargs):
        captured["settings"] = settings
        return {"ok": True}

    monkeypatch.setattr(group_models, "update_group", capture_update)

    group_models.update_group_values(
        "group-1",
        None,
        {
            "leaderboard": {
                "enabled": False,
                "artificial_analysis_api_key": "aa-...",
            }
        },
        object(),
    )

    assert captured["settings"]["leaderboard"] == {
        "enabled": False,
        "artificial_analysis_api_key": "aa-secret-value",
    }


def test_group_list_serializer_does_not_return_full_settings(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "group list serialization must not load full settings"
        )

    monkeypatch.setattr(group_models, "get_group_settings", fail_if_called)
    group = SimpleNamespace(
        id="group-1",
        name="Group One",
        parent_id=None,
        created_at=None,
        updated_at=None,
    )

    result = group_models._serialize_group_with_context(
        None,
        group,
        parent_name_map={},
        path_map={"group-1": ["Group One"]},
        depth_map={"group-1": 0},
        member_count_map={"group-1": 0},
        manager_count_map={"group-1": 0},
    )

    assert result["settings"] == {}
    assert "description" not in result


def test_group_list_serializer_omits_persistence_timestamps():
    """The admin groups table should not receive unused timestamp fields."""
    group = SimpleNamespace(
        id="group-1",
        name="Group One",
        parent_id=None,
        created_at="created",
        updated_at="updated",
    )

    result = group_models._serialize_group_with_context(
        None,
        group,
        parent_name_map={},
        path_map={"group-1": ["Group One"]},
        depth_map={"group-1": 0},
        member_count_map={"group-1": 0},
        manager_count_map={"group-1": 0},
        include_timestamps=False,
    )

    assert "created_at" not in result
    assert "updated_at" not in result
