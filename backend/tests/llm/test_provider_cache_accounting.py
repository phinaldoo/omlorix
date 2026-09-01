"""Regression tests for inclusive input-token and cache-subset accounting."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.anthropic.utils import (  # noqa: E402
    calculate_anthropic_token_costs,
    normalize_anthropic_usage_metadata,
)
from app.llm.google_aistudio.utils import (  # noqa: E402
    calculate_aistudio_token_costs,
    normalize_aistudio_usage_metadata,
)
from app.llmstats.models import AVAILABLE_META_KEYS  # noqa: E402


def _google_detail(modality: str, token_count: int) -> SimpleNamespace:
    """Build the small Google SDK-shaped object needed by usage tests."""
    return SimpleNamespace(
        modality=SimpleNamespace(value=modality),
        token_count=token_count,
    )


def test_anthropic_normalization_adds_disjoint_input_buckets_to_total():
    usage = SimpleNamespace(
        input_tokens=50,
        cache_read_input_tokens=100_000,
        cache_creation_input_tokens=5_000,
        cache_creation=SimpleNamespace(
            ephemeral_5m_input_tokens=3_000,
            ephemeral_1h_input_tokens=2_000,
        ),
        output_tokens=100,
    )

    normalized = normalize_anthropic_usage_metadata(usage)

    assert normalized == {
        "input_tokens": 105_050,
        "input_token_cached": 100_000,
        "cache_write_tokens": 5_000,
        "ephemeral_5m_input_tokens": 3_000,
        "ephemeral_1h_input_tokens": 2_000,
        "output_tokens": 100,
        "total_tokens": 105_150,
    }


def test_anthropic_pricing_uses_read_and_ttl_write_rates_without_overlap():
    costs = calculate_anthropic_token_costs(
        "claude-sonnet-4-5",
        input_tokens=105_050,
        cached_input_tokens=100_000,
        cache_write_tokens=5_000,
        ephemeral_5m_input_tokens=3_000,
        ephemeral_1h_input_tokens=2_000,
        output_tokens=100,
        native_websearch_tool_calls_count=0,
    )

    # Sonnet 4.5: $3/M ordinary, $0.30/M read, $3.75/M 5m write,
    # $6/M 1h write, and $15/M output.
    assert costs["input_tokens_cost"] == pytest.approx(0.0534)
    assert costs["cached_input_tokens_cost"] == pytest.approx(0.03)
    assert costs["cache_write_tokens_cost"] == pytest.approx(0.02325)
    assert costs["output_tokens_cost"] == pytest.approx(0.0015)
    assert costs["total_costs"] == pytest.approx(0.0549)


def test_anthropic_unclassified_cache_writes_use_default_five_minute_rate():
    costs = calculate_anthropic_token_costs(
        "claude-sonnet-4-5",
        input_tokens=1_000,
        cached_input_tokens=2_000,
        cache_write_tokens=500,
        output_tokens=0,
        native_websearch_tool_calls_count=0,
    )

    # Invalid oversized cache reads are clamped, so costs cannot become negative.
    assert costs["input_tokens_cost"] == pytest.approx(0.0003)
    assert costs["total_costs"] >= 0


def test_anthropic_fable_51_uses_reduced_cache_read_rate():
    costs = calculate_anthropic_token_costs(
        "claude-fable-5-1",
        input_tokens=100_000,
        cached_input_tokens=100_000,
        cache_write_tokens=0,
        output_tokens=0,
        native_websearch_tool_calls_count=0,
    )

    assert costs["cached_input_tokens_cost"] == pytest.approx(0.025)
    assert costs["total_costs"] == pytest.approx(0.025)


def test_google_normalization_keeps_cache_as_modality_subsets_of_prompt_total():
    usage = SimpleNamespace(
        prompt_token_count=100_000,
        cached_content_token_count=50_000,
        prompt_tokens_details=[
            _google_detail("TEXT", 60_000),
            _google_detail("AUDIO", 40_000),
        ],
        cache_tokens_details=[
            _google_detail("TEXT", 30_000),
            _google_detail("AUDIO", 20_000),
        ],
        candidates_token_count=1_000,
        thoughts_token_count=200,
        total_token_count=101_200,
    )

    normalized = normalize_aistudio_usage_metadata(usage)

    assert normalized["input_tokens"] == 100_000
    assert normalized["input_token_cached"] == 50_000
    assert normalized["input_token_text"] == 60_000
    assert normalized["input_token_audio"] == 40_000
    assert normalized["input_token_cached_text"] == 30_000
    assert normalized["input_token_cached_audio"] == 20_000
    assert normalized["output_tokens"] == 1_000
    assert normalized["reasoning_tokens"] == 200
    assert normalized["total_tokens"] == 101_200


def test_google_pricing_subtracts_cached_tokens_before_ordinary_input_cost():
    costs = calculate_aistudio_token_costs(
        "gemini-2.5-pro",
        input_tokens_total=100_000,
        input_text_tokens=100_000,
        cached_input_tokens=90_000,
        cached_input_text_tokens=90_000,
    )

    # 10k ordinary at $1.25/M plus 90k cached at $0.125/M.
    assert costs["input_tokens_cost"] == pytest.approx(0.02375)
    assert costs["cached_input_tokens_cost"] == pytest.approx(0.01125)
    assert costs["total_costs"] == pytest.approx(0.02375)


def test_google_cache_modality_details_cannot_exceed_cached_total():
    usage = SimpleNamespace(
        prompt_token_count=100,
        cached_content_token_count=20,
        prompt_tokens_details=[_google_detail("TEXT", 50), _google_detail("AUDIO", 50)],
        cache_tokens_details=[_google_detail("TEXT", 20), _google_detail("AUDIO", 20)],
        candidates_token_count=0,
        thoughts_token_count=0,
        total_token_count=100,
    )

    normalized = normalize_aistudio_usage_metadata(usage)

    assert normalized["input_token_cached"] == 20
    assert (
        normalized["input_token_cached_text"]
        + normalized["input_token_cached_image"]
        + normalized["input_token_cached_audio"]
        + normalized["input_token_cached_video"]
    ) == 20


def test_google_high_context_tier_uses_complete_prompt_for_every_bucket():
    costs = calculate_aistudio_token_costs(
        "gemini-2.5-pro",
        input_tokens_total=210_000,
        input_text_tokens=110_000,
        input_image_tokens=100_000,
        output_tokens=1_000,
    )

    # Neither modality exceeds 200k by itself, but the complete prompt does.
    assert costs["input_tokens_cost"] == pytest.approx(0.525)
    assert costs["output_tokens_cost"] == pytest.approx(0.015)
    assert costs["total_costs"] == pytest.approx(0.54)


def test_statistics_whitelist_preserves_provider_cache_diagnostics():
    required_keys = {
        "input_token_cached",
        "cache_write_tokens",
        "ephemeral_5m_input_tokens",
        "ephemeral_1h_input_tokens",
        "input_token_cached_text",
        "input_token_cached_image",
        "input_token_cached_audio",
        "input_token_cached_video",
        "cached_input_tokens_cost",
        "cache_write_tokens_cost",
    }

    assert required_keys <= set(AVAILABLE_META_KEYS)
