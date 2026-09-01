"""End-to-end regression coverage for Anthropic automatic prompt caching."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.chats.schemas import SendChatRequestModelSettings
from app.llm.anthropic import parameter_schema as anthropic_parameter_schema
from app.llm.anthropic import schemas as anthropic_schemas
from app.llm.anthropic.prompt_caching import apply_anthropic_prompt_cache
from app.llm.anthropic.request_settings import _apply_anthropic_simple_settings
from app.llm.anthropic.schema_definitions import AnthropicModelSettings
from app.llm.anthropic.usage import calculate_anthropic_token_costs


class _EmptyQuery:
    """Return empty collections for schema option queries."""

    def all(self):
        return []

    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return None


class _EmptyDB:
    """Minimal database facade used by common model-schema builders."""

    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


def _field_map(schema) -> dict:
    """Flatten a section schema into its fields by stable setting key."""
    return {
        field.key: field
        for section in schema.sections or []
        for field in section.fields or []
    }


def test_prompt_cache_is_an_opt_in_model_setting() -> None:
    """Existing models must not incur cache-write charges unless enabled."""
    assert AnthropicModelSettings.model_fields["prompt_cache_enabled"].default is False

    request_kwargs = {"model": "claude-test"}
    apply_anthropic_prompt_cache(request_kwargs, None)
    apply_anthropic_prompt_cache(request_kwargs, {})
    apply_anthropic_prompt_cache(request_kwargs, {"prompt_cache_enabled": False})
    apply_anthropic_prompt_cache(request_kwargs, {"prompt_cache_enabled": "true"})

    assert "cache_control" not in request_kwargs


def test_enabled_prompt_cache_uses_only_automatic_five_minute_control() -> None:
    """The request must rely on Anthropic's default five-minute ephemeral TTL."""
    request_kwargs = {"model": "claude-test", "max_tokens": 10}

    _apply_anthropic_simple_settings(
        request_kwargs,
        {"prompt_cache_enabled": True, "max_tokens": 20},
    )

    assert request_kwargs == {
        "model": "claude-test",
        "max_tokens": 20,
        "cache_control": {"type": "ephemeral"},
    }
    assert "ttl" not in request_kwargs["cache_control"]


@pytest.mark.parametrize("provider_type", ["anthropic", "anthropic_base"])
def test_prompt_cache_setting_is_available_for_both_anthropic_provider_types(
    provider_type: str,
) -> None:
    """First-party and compatible models expose the same explicit opt-in."""
    schema = anthropic_schemas.get_anthropic_model_schema(
        _EmptyDB(),
        None,
        "claude-test",
        anthropic_provider_type=provider_type,
        model_info={"id": "claude-test", "display_name": "Claude Test"},
    )

    field = _field_map(schema)["settings.prompt_cache_enabled"]
    assert field.type == "boolean"
    assert field.default is False
    assert field.value is False
    assert field.i18n_label == "llm.anthropic.prompt_cache.enabled_label"


def test_prompt_cache_setting_is_not_exposed_as_a_per_chat_override(
    monkeypatch,
) -> None:
    """Only administrators may enable cache writes in saved model settings."""
    model = type(
        "Model",
        (),
        {
            "model_name": "claude-test",
            "provider_id": "provider-1",
            "settings": {"prompt_cache_enabled": True},
            "tools": [],
        },
    )()
    monkeypatch.setattr("app.llm.models.get_model", lambda *_args: model)
    monkeypatch.setattr(
        anthropic_parameter_schema,
        "get_anthropic_model_info",
        lambda *_args, **_kwargs: {"model_group_dict": {}},
    )
    monkeypatch.setattr(
        anthropic_parameter_schema,
        "get_parameter_basic_schema",
        lambda *_args, **_kwargs: type("Schema", (), {"sections": []})(),
    )

    schema = anthropic_parameter_schema.get_anthropic_model_schema_parameter(
        None,
        "user-1",
        "model-1",
        None,
    )

    assert "settings.prompt_cache_enabled" not in _field_map(schema)
    with pytest.raises(ValidationError):
        SendChatRequestModelSettings.model_validate(
            {"settings": {"prompt_cache_enabled": True}}
        )


def test_automatic_five_minute_cache_pricing_has_disjoint_buckets() -> None:
    """Ordinary, cache-read, and five-minute-write tokens are priced once."""
    costs = calculate_anthropic_token_costs(
        "claude-sonnet-4-5",
        input_tokens=111_000,
        cached_input_tokens=100_000,
        cache_write_tokens=10_000,
        ephemeral_5m_input_tokens=10_000,
        output_tokens=1_000,
        native_websearch_tool_calls_count=0,
    )

    # Sonnet 4.5 prices: ordinary input $3/M, cache reads $0.30/M,
    # five-minute cache writes $3.75/M, and output $15/M.
    assert costs["cached_input_tokens_cost"] == pytest.approx(0.03)
    assert costs["cache_write_tokens_cost"] == pytest.approx(0.0375)
    assert costs["input_tokens_cost"] == pytest.approx(0.0705)
    assert costs["output_tokens_cost"] == pytest.approx(0.015)
    assert costs["total_costs"] == pytest.approx(0.0855)


def test_anthropic_prompt_cache_schema_copy_is_translated_in_every_locale() -> None:
    """Every supported locale must provide the provider-specific UI copy."""
    locale_root = Path(__file__).resolve().parents[3] / "frontend" / "i18n"
    required_keys = {
        "llm.anthropic.prompt_cache.section_title",
        "llm.anthropic.prompt_cache.section_description",
        "llm.anthropic.prompt_cache.enabled_label",
        "llm.anthropic.prompt_cache.enabled_description",
    }

    locale_dirs = sorted(path for path in locale_root.iterdir() if path.is_dir())
    assert locale_dirs
    for locale_dir in locale_dirs:
        translations = json.loads((locale_dir / "schema.json").read_text())
        assert required_keys <= translations.keys(), locale_dir.name


def test_direct_chat_request_builder_applies_prompt_cache_setting() -> None:
    """Protect the separate chat builder from drifting from the adapter path."""
    chat_path = (
        Path(__file__).resolve().parents[2] / "app" / "llm" / "anthropic" / "chat.py"
    )
    source = chat_path.read_text()

    assert "apply_anthropic_prompt_cache(request_kwargs, settings)" in source
