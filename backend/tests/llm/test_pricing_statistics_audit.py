from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.chats.models as chat_models
import app.llm.google_aistudio.utils as aistudio_utils
from app.llm.anthropic.model_list import ANTHROPIC_MODEL_PRICING, get_anthropic_pricing
from app.llm.google_aistudio.model_list import (
    AISTUDIO_MODEL_DICT,
    AISTUDIO_MODELS_NOT_SUPPORTED,
    IMAGE_GEN_MODELS,
)
from app.llm.google_aistudio.utils import (
    calculate_aistudio_token_costs,
    normalize_aistudio_usage_metadata,
)
from app.llm.openai.model_list import (
    OPENAI_ANNOUNCED_SHUTDOWN_DATES,
    OPENAI_COMPLETION_MODELS,
    OPENAI_MODEL_DICT,
    OPENAI_SHUT_DOWN_MODEL_IDS,
    OPENAI_UNSUPPORTED_MODELS,
)
from app.llm.openai.image_generation import IMAGE_GEN_MODELS as OPENAI_IMAGE_GEN_MODELS
from app.llm.openai.utils import (
    calculate_openai_token_costs,
    merge_openai_cost_breakdown,
)
from app.llm.openrouter.utils import merge_openrouter_usage, normalize_openrouter_usage
from app.llmstats.models import create_llm_generation_statistic


def test_google_tool_use_prompt_tokens_are_billed_as_input() -> None:
    """Google's separate agentic tool-input bucket must not disappear."""
    usage = {
        "prompt_token_count": 100,
        "prompt_tokens_details": [{"modality": "TEXT", "token_count": 100}],
        "tool_use_prompt_token_count": 40,
        "tool_use_prompt_tokens_details": [
            {"modality": "TEXT", "token_count": 30},
            {"modality": "AUDIO", "token_count": 10},
        ],
        "cached_content_token_count": 20,
        "cache_tokens_details": [{"modality": "TEXT", "token_count": 20}],
        "candidates_token_count": 20,
        "thoughts_token_count": 5,
        "total_token_count": 165,
    }

    normalized = normalize_aistudio_usage_metadata(usage)

    assert normalized["input_tokens"] == 140
    assert normalized["tool_use_prompt_tokens"] == 40
    assert normalized["input_token_text"] == 130
    assert normalized["input_token_audio"] == 10
    assert normalized["total_tokens"] == 165

    costs = calculate_aistudio_token_costs(
        "gemini-3.5-flash-lite",
        input_tokens_total=normalized["input_tokens"],
        input_text_tokens=normalized["input_token_text"],
        input_audio_tokens=normalized["input_token_audio"],
        cached_input_tokens=normalized["input_token_cached"],
        cached_input_text_tokens=normalized["input_token_cached_text"],
        output_tokens=normalized["output_tokens"],
        reasoning_tokens=normalized["reasoning_tokens"],
    )

    assert costs is not None
    assert costs["total_costs"] == pytest.approx(0.0000991)


def test_google_chat_persists_tool_use_prompt_tokens(monkeypatch) -> None:
    """The main chat recorder must keep Google's tool-use input diagnostic."""
    usage = SimpleNamespace(
        prompt_token_count=100,
        prompt_tokens_details=[],
        tool_use_prompt_token_count=40,
        tool_use_prompt_tokens_details=[],
        cached_content_token_count=0,
        cache_tokens_details=[],
        candidates_token_count=20,
        thoughts_token_count=5,
        total_token_count=165,
    )
    part = SimpleNamespace(
        text="Hello",
        thought=False,
        thought_signature=None,
        tool_call=None,
        tool_response=None,
        function_call=None,
    )
    chunk = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[part]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=usage,
    )
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content_stream=lambda **_kwargs: [chunk]),
        files=SimpleNamespace(delete=lambda **_kwargs: None),
    )
    persisted_calls: list[dict] = []

    monkeypatch.setattr(aistudio_utils, "get_aistudio_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(aistudio_utils, "get_user_setting_value", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(
        aistudio_utils,
        "reformat_chat_history",
        lambda *_args, **_kwargs: {"formatted": [], "uploaded_cleanup": []},
    )
    monkeypatch.setattr(
        aistudio_utils,
        "build_aistudio_generate_content_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        aistudio_utils,
        "get_default_system_instruction",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        aistudio_utils,
        "create_llm_generation_statistic",
        lambda *_args, **kwargs: persisted_calls.append(kwargs),
    )
    monkeypatch.setattr(
        chat_models,
        "create_chat_message",
        lambda *_args, **_kwargs: SimpleNamespace(id="assistant-1"),
    )

    db_model = SimpleNamespace(
        id="model-1",
        model_name="gemini-3.5-flash-lite",
        provider_id="provider-1",
        settings={},
        tools=[],
        capabilities=[],
    )
    events = list(
        aistudio_utils.aistudio_chat(
            "chat-1",
            [],
            MagicMock(),
            db_model=db_model,
            user_id="user-1",
            byok={
                "api_key": "test-key",
                "model_name": "gemini-3.5-flash-lite",
                "capabilities": [],
            },
        )
    )

    assert events
    assert len(persisted_calls) == 1
    persisted_meta = persisted_calls[0]["meta"]
    assert persisted_meta["input_tokens"] == 140
    assert persisted_meta["tool_use_prompt_tokens"] == 40
    assert persisted_meta["total_tokens"] == 165


def test_openrouter_costs_accumulate_across_tool_rounds() -> None:
    """A later routed request must not overwrite the first request's charge."""
    total: dict = {}
    first = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cost": 0.012,
        "cost_details": {
            "upstream_inference_cost": 0.01,
            "upstream_inference_prompt_cost": 0.008,
            "upstream_inference_completions_cost": 0.002,
        },
    }
    second = {
        "prompt_tokens": 150,
        "completion_tokens": 30,
        "total_tokens": 180,
        "cost": 0.018,
        "cost_details": {
            "upstream_inference_cost": 0.015,
            "upstream_inference_prompt_cost": 0.011,
            "upstream_inference_completions_cost": 0.004,
        },
    }

    merge_openrouter_usage(total, first)
    merge_openrouter_usage(total, second)

    assert total["input_tokens"] == 250
    assert total["output_tokens"] == 50
    assert total["total_tokens"] == 300
    assert total["total_costs"] == pytest.approx(0.03)
    assert total["upstream_inference_cost"] == pytest.approx(0.025)


def test_openrouter_detailed_usage_fields_are_preserved() -> None:
    """Cache writes, modalities, and nested BYOK state must survive persistence."""
    normalized = normalize_openrouter_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {
                "cached_tokens": 40,
                "cache_write_tokens": 10,
                "image_tokens": 5,
                "audio_tokens": 6,
                "video_tokens": 7,
            },
            "completion_tokens_details": {
                "reasoning_tokens": 8,
                "image_tokens": 2,
                "audio_tokens": 3,
                "video_tokens": 4,
            },
            "cost_details": {"is_byok": True},
        }
    )

    assert normalized["input_token_cached"] == 40
    assert normalized["cache_write_tokens"] == 10
    assert normalized["input_token_image"] == 5
    assert normalized["input_token_audio"] == 6
    assert normalized["input_token_video"] == 7
    assert normalized["output_image_tokens"] == 2
    assert normalized["output_audio_tokens"] == 3
    assert normalized["output_video_tokens"] == 4
    assert normalized["reasoning_tokens"] == 8
    assert normalized["meta_is_byok"] is True


def test_openrouter_responses_terminal_event_exposes_nested_usage() -> None:
    """Responses API usage is nested in the terminal event's response body."""
    event = {
        "type": "response.done",
        "response": {
            "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0.012,
                    "cost_details": {"upstream_inference_cost": 0.01},
            }
        },
    }
    normalized = normalize_openrouter_usage(event["response"]["usage"])

    assert normalized["input_tokens"] == 100
    assert normalized["output_tokens"] == 20
    assert normalized["total_tokens"] == 120
    assert normalized["total_costs"] == pytest.approx(0.012)
    assert normalized["upstream_inference_cost"] == pytest.approx(0.01)


def test_provider_diagnostics_survive_llm_statistic_persistence() -> None:
    """The persistence allowlist must retain normalized provider diagnostics."""
    db = MagicMock()

    create_llm_generation_statistic(
        db,
        model_name="test-model",
        model_id="test-model",
        provider="openrouter",
        provider_id="provider-1",
        success=True,
        category="chat",
        meta={
            "input_tokens": 140,
            "output_tokens": 20,
            "tool_use_prompt_tokens": 40,
            "meta_is_byok": True,
        },
    )

    persisted_stat = db.add.call_args_list[0].args[0]
    assert persisted_stat.meta["tool_use_prompt_tokens"] == 40
    assert persisted_stat.meta["meta_is_byok"] is True
    # Even without an explicit is_byok argument, OpenRouter's upstream flag is
    # diagnostic metadata and must not reclassify the row as Omlorix BYOK.
    assert persisted_stat.is_byok is False


def test_openai_long_context_pricing_is_merged_after_per_request_pricing() -> None:
    """Two 200K requests remain standard-tier even though their sum is 400K."""
    merged: dict[str, float] = {}
    for _ in range(2):
        request_cost = calculate_openai_token_costs(
            model_name="gpt-5.6-sol",
            service_tier="standard",
            input_tokens=200_000,
            cached_input_tokens=0,
            cache_write_tokens=0,
            output_tokens=10_000,
            reasoning_tokens=0,
            native_websearch_tool_calls_count=0,
        )
        merge_openai_cost_breakdown(merged, request_cost)

    incorrectly_aggregated = calculate_openai_token_costs(
        model_name="gpt-5.6-sol",
        service_tier="standard",
        input_tokens=400_000,
        cached_input_tokens=0,
        cache_write_tokens=0,
        output_tokens=20_000,
        reasoning_tokens=0,
        native_websearch_tool_calls_count=0,
    )

    assert merged["total_costs"] == pytest.approx(2.0)
    assert incorrectly_aggregated is not None
    assert incorrectly_aggregated["total_costs"] == pytest.approx(3.8)


def test_current_catalogs_include_new_models_and_exclude_shutdown_ids() -> None:
    assert "gemini-3.7-flash" in AISTUDIO_MODEL_DICT
    assert "gemini-3.6-flash" in AISTUDIO_MODEL_DICT
    assert "gemini-3.5-flash-lite" in AISTUDIO_MODEL_DICT
    assert "claude-opus-5" in ANTHROPIC_MODEL_PRICING
    assert get_anthropic_pricing("claude-fable-5-1") == {
        "input": 10,
        "output": 50,
        "native_web_search_tool_call": 0.01,
        "cache_read_input_multiplier": 0.025,
    }
    assert "claude-haiku-3-5" not in ANTHROPIC_MODEL_PRICING
    assert get_anthropic_pricing("claude-haiku-4-5-20251001") == {
        "input": 1,
        "output": 5,
        "native_web_search_tool_call": 0.01,
    }
    openai_catalog_ids = {
        model_id
        for model in OPENAI_MODEL_DICT.values()
        for model_id in model.get("ids", [])
    }
    assert "gemini-flash-latest" in AISTUDIO_MODELS_NOT_SUPPORTED
    assert all(
        "gemini-flash-latest" not in model.get("ids", [])
        for model in AISTUDIO_MODEL_DICT.values()
    )
    assert "chat-latest" not in openai_catalog_ids
    assert "chat-latest" not in OPENAI_COMPLETION_MODELS
    assert "chat-latest" in OPENAI_UNSUPPORTED_MODELS
    assert "gpt-5.2-chat-latest" not in openai_catalog_ids
    assert "gpt-5.3-chat-latest" not in openai_catalog_ids
    assert "gpt-daybreak-blue-latest" in openai_catalog_ids
    assert "gpt-daybreak-red-latest" in openai_catalog_ids
    assert OPENAI_MODEL_DICT["gpt-5.6-cyber"]["pricing"]["standard"] == {
        "input": 12.5,
        "cached_input": 1.25,
        "cache_write": 15.625,
        "output": 75.0,
    }
    assert "gemini-pro-latest" not in AISTUDIO_MODEL_DICT["gemini-3.1-pro"]["ids"]
    assert not (OPENAI_SHUT_DOWN_MODEL_IDS & set(OPENAI_COMPLETION_MODELS))
    assert OPENAI_ANNOUNCED_SHUTDOWN_DATES["gpt-4.1-nano"] == "2026-10-23"
    assert "shutdown_date" not in AISTUDIO_MODEL_DICT["gemini-2.5-pro"]
    assert AISTUDIO_MODEL_DICT["gemini-3.6-flash"]["pricing"]["output"] == 3.75
    assert next(
        model
        for model in IMAGE_GEN_MODELS
        if "gemini-2.5-flash-image" in model["ids"]
    )["shutdown_date"] == "2026-10-02"
    image_15 = next(
        model for model in OPENAI_IMAGE_GEN_MODELS if "gpt-image-1.5" in model["ids"]
    )
    assert "gpt-image-1.5-2025-12-16" in image_15["ids"]
    assert image_15["pricing"]["image_tokens"]["cached_input"] == 2.0


@pytest.mark.parametrize(
    ("catalog", "expected_groups", "expected_ids"),
    [
        (OPENAI_MODEL_DICT, 35, 66),
        (AISTUDIO_MODEL_DICT, 18, 19),
    ],
)
def test_priced_catalogs_have_unique_ids_and_valid_prices(
    catalog: dict,
    expected_groups: int,
    expected_ids: int,
) -> None:
    """Static provider catalogs must remain structurally safe for pricing."""

    def _assert_non_negative_prices(value) -> None:
        if isinstance(value, dict):
            for nested_value in value.values():
                _assert_non_negative_prices(nested_value)
        elif isinstance(value, (int, float)):
            assert value >= 0

    model_ids = [
        model_id
        for model in catalog.values()
        for model_id in model.get("ids", [])
    ]

    assert len(catalog) == expected_groups
    assert len(model_ids) == expected_ids
    assert len(model_ids) == len(set(model_ids))
    assert all(model.get("ids") for model in catalog.values())
    for model in catalog.values():
        _assert_non_negative_prices(model.get("pricing", {}))


def test_anthropic_prices_are_non_negative() -> None:
    """The API supplies model metadata, leaving only prices in local code."""
    assert all(
        input_price >= 0 and output_price >= 0
        for input_price, output_price in ANTHROPIC_MODEL_PRICING.values()
    )
