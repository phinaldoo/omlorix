"""Regression tests for provider-independent model metadata fallbacks."""

import pytest

from app.llm.metadata import resolve_model_metadata_id


@pytest.mark.parametrize("missing_provider_value", [None, "", "   "])
def test_configured_model_survives_missing_provider_metadata(missing_provider_value):
    """Compatibility providers must not erase the requested model identifier."""
    assert (
        resolve_model_metadata_id(
            missing_provider_value,
            "claude-opus-4-6-thinking",
        )
        == "claude-opus-4-6-thinking"
    )


def test_provider_reported_model_remains_authoritative():
    """A canonical model returned by the provider should win over the request alias."""
    assert (
        resolve_model_metadata_id(
            "claude-opus-4-6-20260801",
            "claude-opus-4-6-thinking",
        )
        == "claude-opus-4-6-20260801"
    )


def test_model_metadata_normalization_rejects_only_empty_candidates():
    """Identifiers are trimmed and an entirely empty candidate set returns None."""
    assert resolve_model_metadata_id("  gpt-5.6-sol  ") == "gpt-5.6-sol"
    assert resolve_model_metadata_id(None, " ") is None
