"""Tests for the BYOK section of the administrator group form."""

from app.admin.groups.schemas import GROUP_FORM_SCHEMA


def test_byok_fields_are_ordered_and_provider_defaults_require_web_search():
    """Keep the BYOK form flow compact and gate provider defaults by tool access."""
    byok_section = next(section for section in GROUP_FORM_SCHEMA.sections if section.key == "byok")
    fields = byok_section.fields

    assert [field.key for field in fields] == [
        "settings.chat.allow_byok",
        "settings.chat.byok_title_generation_model_id",
        "settings.chat.byok_allowed_tools",
        "settings.chat.byok_default_scrape_provider",
        "settings.chat.byok_default_search_provider",
    ]

    provider_fields = fields[-2:]
    assert all(field.dependency == "settings.chat.allow_byok" for field in provider_fields)
    assert all(field.dependency_value is True for field in provider_fields)
    assert all(field.dependency2 == "settings.chat.byok_allowed_tools" for field in provider_fields)
    assert all(field.dependency2_value == "web_search" for field in provider_fields)


def test_memory_group_settings_expose_switch_and_dependent_model_select():
    section = next(
        section for section in GROUP_FORM_SCHEMA.sections if section.key == "memories"
    )

    assert [field.key for field in section.fields] == [
        "settings.memories.enabled_memories",
        "settings.memories.memory_model_id",
    ]
    enabled, model = section.fields
    assert enabled.type == "boolean"
    assert enabled.default is True
    assert model.type == "select"
    assert model.default == ""
    assert model.dependency == enabled.key
    assert model.dependency_value is True
