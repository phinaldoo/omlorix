import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


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
