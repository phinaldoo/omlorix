import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.llm.anthropic.model_list import (
    get_anthropic_knowledge_cutoff,
    supports_anthropic_native_websearch,
)
from app.llm.anthropic.usage import calculate_anthropic_token_costs
from app.llm.anthropic.request_settings import _apply_anthropic_thinking_settings


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.anthropic.utils import (  # noqa: E402
    _build_anthropic_thinking_params,
    _get_anthropic_thinking_capabilities,
    _resolve_anthropic_thinking_enabled,
)


def test_unset_thinking_is_not_forced_for_non_thinking_anthropic_model():
    capabilities = _get_anthropic_thinking_capabilities("claude-3-5-haiku-20241022")

    assert capabilities["thinking"] is False
    assert capabilities["thinking_disabled_allowed"] is False
    assert _resolve_anthropic_thinking_enabled(None, capabilities) is False


def test_unset_thinking_is_forced_only_when_anthropic_model_requires_thinking():
    capabilities = _get_anthropic_thinking_capabilities("claude-fable-5")

    assert capabilities["thinking"] is True
    assert capabilities["thinking_disabled_allowed"] is False
    assert _resolve_anthropic_thinking_enabled(None, capabilities) is True


def test_unset_thinking_is_not_forced_for_adaptive_optional_model():
    """Opus 4.6 supports adaptive thinking but does not require it."""
    capabilities = _get_anthropic_thinking_capabilities("claude-opus-4-6")

    assert capabilities["thinking"] is False
    assert _resolve_anthropic_thinking_enabled(None, capabilities) is False


def test_opus_5_allows_disabling_thinking_through_high_effort():
    """Opus 5 accepts disabled thinking for the lower three effort levels."""
    capabilities = _get_anthropic_thinking_capabilities("claude-opus-5")

    assert capabilities["thinking_disabled_allowed"] is True
    assert capabilities["thinking_disabled_forbidden_efforts"] == ["xhigh", "max"]
    assert _build_anthropic_thinking_params(
        {"thinking": False, "reasoning_effort": "high"},
        "claude-opus-5",
    ) == {"type": "disabled"}


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_opus_5_rejects_disabling_thinking_at_top_efforts(effort: str):
    """Reject locally the Opus 5 combinations that Anthropic returns as 400."""
    with pytest.raises(HTTPException) as exc_info:
        _build_anthropic_thinking_params(
            {"thinking": False, "reasoning_effort": effort},
            "claude-opus-5",
        )

    assert exc_info.value.status_code == 422
    assert effort in str(exc_info.value.detail)


@pytest.mark.parametrize("settings", [
    {},
    {"thinking": False},
    {"thinking": True, "thinking_budget": 2048, "thinking_adaptive": False},
])
def test_opus_55_requires_adaptive_thinking_even_with_legacy_settings(settings):
    assert _build_anthropic_thinking_params(settings, "claude-opus-5-5") == {
        "type": "adaptive",
    }


def test_opus_55_metadata_and_cache_read_cost():
    model = "claude-opus-5-5"
    assert get_anthropic_knowledge_cutoff(model) == "2026-06-01"
    assert supports_anthropic_native_websearch(model)
    # One million ordinary input, cache-read, and output tokens respectively.
    costs = calculate_anthropic_token_costs(model, 2_000_000, 1_000_000, 1_000_000, 0)
    assert costs["total_costs"] == pytest.approx(24.20)


def test_opus_55_request_uses_output_config_for_effort():
    request = {}
    _apply_anthropic_thinking_settings(
        request,
        {"thinking": False, "reasoning_effort": "high", "thinking_budget": 2048},
        "claude-opus-5-5",
    )
    assert request == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }
