from types import SimpleNamespace

from app.llm.anthropic.schemas import (
    AnthropicModelSettings,
    get_parameters_schema_filled,
)
from app.llm.anthropic.settings import (
    ANTHROPIC_DEPRECATED_REQUEST_SETTINGS,
    remove_deprecated_anthropic_request_settings,
)
from app.llm.anthropic.utils import _apply_anthropic_simple_settings
from app.llm.models import (
    Models,
    create_user_model_setting_preset,
    export_llm_models,
)


def test_deprecated_anthropic_settings_are_absent_from_model_schema():
    """Neither validation nor the generated UI may expose deprecated fields."""
    assert ANTHROPIC_DEPRECATED_REQUEST_SETTINGS.isdisjoint(
        AnthropicModelSettings.model_fields
    )
    field_keys = {
        field.key.removeprefix("settings.")
        for section in get_parameters_schema_filled().sections
        for field in section.fields
    }
    assert ANTHROPIC_DEPRECATED_REQUEST_SETTINGS.isdisjoint(field_keys)


def test_deprecated_anthropic_settings_are_removed_from_all_storage_shapes():
    """Clean direct model settings and nested user-preset settings."""
    legacy = {
        "max_tokens": 100,
        "temperature": 0.2,
        "top_k": 10,
        "top_p": 0.9,
        "output_format": {"type": "json_schema"},
    }
    assert remove_deprecated_anthropic_request_settings(legacy) == {
        "max_tokens": 100
    }
    assert remove_deprecated_anthropic_request_settings({"settings": legacy}) == {
        "settings": {"max_tokens": 100}
    }


def test_simple_anthropic_requests_ignore_deprecated_settings():
    """Secondary generation paths must never forward legacy parameters."""
    request_kwargs = {"max_tokens": 10}
    _apply_anthropic_simple_settings(
        request_kwargs,
        {
            "max_tokens": 20,
            "output_format": {},
            "temperature": 0.2,
            "top_k": 10,
            "top_p": 0.9,
        },
    )
    assert request_kwargs == {"max_tokens": 20}


def test_anthropic_exports_and_new_presets_cannot_retain_deprecated_settings():
    """Protect data boundaries even before an older database is migrated."""
    legacy = {"max_tokens": 100, "temperature": 0.2, "top_p": 0.9}
    model = SimpleNamespace(
        id="anthropic-model",
        name="Claude",
        description="Claude model",
        model_icon="anthropic",
        provider="anthropic",
        provider_id="anthropic-provider",
        model_name="claude-test",
        settings=legacy,
        capabilities=["completion"],
        tools=[],
        access={"everyone": True},
        status="normal",
        is_active=True,
        created_at=None,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

        def all(self):
            return [model]

    class Database:
        added = None

        def query(self, model_type):
            assert model_type is Models
            return Query()

        def add(self, value):
            self.added = value

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    db = Database()
    exported = export_llm_models(db)["data"]["models"][0]
    assert exported["settings"] == {"max_tokens": 100}

    preset = create_user_model_setting_preset(
        db,
        "user-id",
        model.id,
        "Legacy",
        {"settings": legacy},
    )
    assert preset.settings == {"settings": {"max_tokens": 100}}
