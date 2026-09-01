"""Regression tests for OpenAI Responses API storage request handling."""

import pytest

from app.llm.openai.utils import (
    _apply_openai_simple_generation_settings,
    _apply_openai_store_setting,
    _resolve_openai_store_setting,
)


@pytest.mark.parametrize(
    ("settings", "expected"),
    (
        (None, None),
        ({}, None),
        ({"store": None}, None),
        ({"store": True}, True),
        ({"store": False}, False),
        ({"store": "true"}, True),
        ({"store": "false"}, False),
        ({"store": "unsupported"}, None),
    ),
)
def test_resolve_openai_store_setting_returns_only_optional_booleans(
    settings,
    expected,
):
    """Legacy values are normalized without leaking invalid API values."""
    assert _resolve_openai_store_setting(settings) is expected


@pytest.mark.parametrize(
    "provider_type",
    ("openai", "openai_responses", "microsoft_azure"),
)
@pytest.mark.parametrize("store", (True, False))
def test_apply_openai_store_setting_preserves_explicit_booleans(
    provider_type,
    store,
):
    """Every Responses-based OpenAI provider honors an explicit preference."""
    request = {"model": "gpt-test"}

    resolved = _apply_openai_store_setting(
        request,
        {"store": store},
        provider_type=provider_type,
    )

    assert resolved is store
    assert request["store"] is store


@pytest.mark.parametrize("settings", ({}, {"store": None}, {"store": "invalid"}))
def test_apply_openai_store_setting_omits_unset_and_invalid_values(settings):
    """Optional storage values must never serialize as JSON null."""
    request = {"model": "gpt-test"}

    resolved = _apply_openai_store_setting(
        request,
        settings,
        provider_type="openai_responses",
    )

    assert resolved is None
    assert "store" not in request


@pytest.mark.parametrize(
    "provider_type",
    ("openai_chat_completions", "lmstudio"),
)
@pytest.mark.parametrize("store", (None, True, False))
def test_non_openai_responses_requests_do_not_receive_hosted_store_control(
    provider_type,
    store,
):
    """Other request contracts must not inherit Responses storage semantics."""
    request = {"model": "provider-model"}

    _apply_openai_store_setting(
        request,
        {"store": store},
        provider_type=provider_type,
    )

    assert "store" not in request


def test_simple_responses_requests_honor_explicit_store_false():
    """Auxiliary Responses calls share the main chat storage preference."""
    request = {"model": "gpt-test"}

    _apply_openai_simple_generation_settings(
        request,
        {"store": False, "prompt_cache_override": False},
        openai_provider_type="openai_responses",
    )

    assert request["store"] is False


def test_simple_responses_requests_omit_unset_store():
    """Auxiliary calls let the provider default apply when storage is unset."""
    request = {"model": "gpt-test"}

    _apply_openai_simple_generation_settings(
        request,
        {"store": None, "prompt_cache_override": False},
        openai_provider_type="openai_responses",
    )

    assert "store" not in request
