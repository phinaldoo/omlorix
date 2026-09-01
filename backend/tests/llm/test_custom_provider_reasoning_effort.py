"""Reasoning effort coverage for custom OpenAI and Anthropic providers."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm.anthropic import schemas as anthropic_schemas
from app.llm.anthropic import models as anthropic_models
from app.llm.anthropic import parameter_schema as anthropic_parameter_schema
from app.llm.anthropic import utils as anthropic_utils
from app.llm import utils as llm_utils
from app.llm.anthropic.thinking import ANTHROPIC_REASONING_EFFORT_LEVELS
from app.llm.anthropic.utils import _build_anthropic_thinking_params
from app.llm.schemas import ProviderEnum
from app.llm.openai.schemas import (
    OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS,
    OPENAI_THINKING_MODEL_SCHEMA,
    _apply_openai_model_caps_to_schema,
)
from app.llm.openai import schemas as openai_schemas
from app.llm.openai.utils import _build_openai_reasoning_payload
from app.llm.openai_chat_completions.utils import (
    _apply_openai_chat_completion_simple_settings,
    _apply_openai_chat_completions_reasoning_effort,
)
from app.utils.schemas import Sections


def _reasoning_effort_values(schema) -> list[str]:
    """Extract the reasoning effort option values from a model schema."""
    for section in schema.sections or []:
        for field in section.fields or []:
            if field.key == "settings.reasoning_effort":
                return [option.value for option in field.options or []]
    return []


@pytest.mark.parametrize(
    "provider_type",
    ["openai_responses", "openai_chat_completions"],
)
def test_custom_openai_unknown_models_expose_every_reasoning_effort(provider_type):
    """Custom endpoints need a complete fallback because catalog caps are unavailable."""
    schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)

    _apply_openai_model_caps_to_schema(schema, None, openai_provider_type=provider_type)

    assert _reasoning_effort_values(schema) == list(OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS)


@pytest.mark.parametrize("effort", OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS)
def test_custom_openai_responses_sends_every_reasoning_effort(effort):
    """Responses API custom providers send effort inside the reasoning object."""
    assert _build_openai_reasoning_payload(
        {"reasoning": True, "reasoning_effort": effort},
        model_name="custom-reasoning-model",
        provider_type="openai_responses",
    ) == {"effort": effort}


@pytest.mark.parametrize("effort", OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS)
def test_custom_openai_chat_completions_sends_every_reasoning_effort(effort):
    """Chat Completions custom providers send effort as a top-level parameter."""
    request_kwargs = {}

    _apply_openai_chat_completions_reasoning_effort(
        request_kwargs,
        {"reasoning_effort": effort},
    )

    assert request_kwargs == {"reasoning_effort": effort}


def test_chat_completions_translates_shared_output_token_limit_to_wire_key():
    request_kwargs = {"model": "custom-chat-completions-model"}

    _apply_openai_chat_completion_simple_settings(
        request_kwargs,
        {"max_output_tokens": 321},
    )

    assert request_kwargs["max_completion_tokens"] == 321
    assert "max_output_tokens" not in request_kwargs


def test_chat_completions_parameter_schema_exposes_structured_logit_bias(monkeypatch):
    model = SimpleNamespace(
        model_name="custom-chat-completions-model",
        provider_id="provider-1",
        settings={"allow_custom_generation_parameter": True},
        tools=[],
    )
    monkeypatch.setattr("app.llm.models.get_model", lambda _db, _model_id: model)
    monkeypatch.setattr(
        openai_schemas,
        "get_parameter_basic_schema",
        lambda *_args, **_kwargs: Sections(sections=[]),
    )

    schema = openai_schemas.get_openai_model_schema_parameter(
        None,
        "user-1",
        "model-1",
        None,
        openai_provider_type="openai_chat_completions",
    )
    fields = [field for section in schema.sections for field in section.fields]
    logit_bias = next(field for field in fields if field.key == "settings.logit_bias")

    assert logit_bias.input_type == "dict[str,float]"


@pytest.mark.parametrize("effort", ANTHROPIC_REASONING_EFFORT_LEVELS)
def test_anthropic_base_unknown_models_send_every_reasoning_effort(effort):
    """Anthropic-compatible endpoints receive the effort selected in the Base URL UI."""
    assert _build_anthropic_thinking_params(
        {
            "thinking": True,
            "thinking_adaptive": False,
            "reasoning_effort": effort,
        },
        "custom-claude-compatible-model",
        allow_compatible_fallback=True,
    ) == {"type": "enabled", "effort": effort}


def test_anthropic_base_unknown_model_parameter_schema_exposes_every_effort(monkeypatch):
    """Saved Base URL models retain the full selector in per-chat settings."""
    model = SimpleNamespace(
        model_name="custom-claude-compatible-model",
        provider_id="provider-1",
        settings={},
        tools=[],
    )
    monkeypatch.setattr("app.llm.models.get_model", lambda _db, _model_id: model)
    monkeypatch.setattr(
        anthropic_parameter_schema,
        "get_parameter_basic_schema",
        lambda *_args, **_kwargs: Sections(sections=[]),
    )

    schema = anthropic_schemas.get_anthropic_model_schema_parameter(
        None,
        "user-1",
        "model-1",
        None,
        anthropic_provider_type="anthropic_base",
    )

    assert _reasoning_effort_values(schema) == list(ANTHROPIC_REASONING_EFFORT_LEVELS)


def test_anthropic_base_models_keep_their_provider_type(monkeypatch):
    """Persisting the Base URL type keeps later schema and request dispatch provider-aware."""
    captured = {}
    monkeypatch.setattr(
        anthropic_models,
        "list_anthropic_models",
        lambda *_args, **_kwargs: [{"id": "custom-claude-compatible-model"}],
    )

    def _capture_model(*args, **_kwargs):
        captured["provider"] = args[4]
        return SimpleNamespace(provider=args[4])

    monkeypatch.setattr(anthropic_models, "create_model", _capture_model)

    created = anthropic_utils.create_anthropic_model(
        db=None,
        model="custom-claude-compatible-model",
        name="Custom Claude",
        description="Compatible model",
        model_icon="anthropic",
        settings={"max_tokens": 4096},
        tools=[],
        access={},
        status="active",
        anthropic_provider_id="provider-1",
        anthropic_provider_type="anthropic_base",
    )

    assert captured["provider"] == "anthropic_base"
    assert created.provider == "anthropic_base"


def test_anthropic_model_creation_survives_unavailable_discovery(monkeypatch):
    """An unsupported or temporarily failing Models endpoint is non-blocking."""
    monkeypatch.setattr(
        anthropic_models,
        "list_anthropic_models",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=503, detail="Unavailable")
        ),
    )
    monkeypatch.setattr(
        anthropic_models,
        "create_model",
        lambda *args, **_kwargs: SimpleNamespace(model_name=args[6]),
    )

    created = anthropic_utils.create_anthropic_model(
        db=None,
        model="custom-model",
        name="Custom model",
        description="Compatible model",
        model_icon="anthropic",
        settings={"max_tokens": 4096},
        tools=[],
        access={},
        status="active",
        anthropic_provider_id="provider-1",
        anthropic_provider_type="anthropic_base",
    )

    assert created.model_name == "custom-model"


def test_anthropic_model_creation_rejects_absent_model_after_successful_listing(
    monkeypatch,
):
    """A successful discovery response remains authoritative."""
    monkeypatch.setattr(
        anthropic_models,
        "list_anthropic_models",
        lambda *_args, **_kwargs: [{"id": "available-model"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        anthropic_utils.create_anthropic_model(
            db=None,
            model="missing-model",
            name="Missing model",
            description="Unavailable model",
            model_icon="anthropic",
            settings={"max_tokens": 4096},
            tools=[],
            access={},
            status="active",
            anthropic_provider_id="provider-1",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "Model 'missing-model' is not available for this provider."
    )


@pytest.mark.parametrize(
    ("provider_type", "expected_provider"),
    [
        (ProviderEnum.anthropic, "anthropic"),
        (ProviderEnum.anthropic_base, "anthropic_base"),
    ],
)
def test_create_provider_model_preserves_anthropic_dispatch_type(
    monkeypatch,
    provider_type,
    expected_provider,
):
    """Top-level model creation dispatch persists the selected Anthropic provider type."""
    monkeypatch.setattr(llm_utils, "is_provider_group", lambda *_args: False)
    monkeypatch.setattr(
        llm_utils,
        "get_llm_provider",
        lambda *_args: SimpleNamespace(provider=expected_provider),
    )
    monkeypatch.setattr(
        anthropic_models,
        "list_anthropic_models",
        lambda *_args, **_kwargs: [{"id": "claude-test"}],
    )

    def _capture_persisted_model(*args, **_kwargs):
        return SimpleNamespace(provider=args[4])

    monkeypatch.setattr(anthropic_models, "create_model", _capture_persisted_model)
    payload = SimpleNamespace(
        provider=provider_type,
        provider_id="provider-1",
        model=SimpleNamespace(
            model="claude-test",
            name="Claude Test",
            description="Dispatch test",
            model_icon="anthropic",
            tools=[],
            status="normal",
        ),
        settings={},
        access={},
    )

    created = llm_utils.create_provider_model(None, payload)

    assert created["model"].provider == expected_provider


def test_api_backed_anthropic_settings_preserve_selected_effort():
    """A saved effort selected from API metadata remains effective at runtime."""
    assert _build_anthropic_thinking_params(
        {"thinking": True, "reasoning_effort": "high"},
        "unknown-main-anthropic-model",
    ) == {"type": "enabled", "effort": "high"}
