"""Tests for the unified account import/export group setting."""

from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.admin.groups.schemas import GROUP_FORM_SCHEMA


def test_data_controls_section_contains_only_complete_account_portability():
    """Per-category portability switches must not be rendered or persisted."""
    sections = {section.key: section for section in GROUP_FORM_SCHEMA.sections}
    data_control_keys = [field.key for field in sections["data_controls"].fields]

    assert data_control_keys == ["settings.data_controls.allow_user_data"]
    assert DEFAULT_GROUP_SETTINGS["data_controls"] == {
        "allow_user_data": True,
    }

    all_field_keys = {
        field.key
        for section in GROUP_FORM_SCHEMA.sections
        for field in section.fields
    }
    assert not {
        "settings.data_controls.allow_skills",
        "settings.data_controls.allow_prompts",
        "settings.data_controls.allow_automations",
        "settings.data_controls.allow_todos",
        "settings.data_controls.allow_notes",
        "settings.data_controls.allow_memories",
        "settings.data_controls.allow_files",
    } & all_field_keys
