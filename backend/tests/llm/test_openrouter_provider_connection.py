"""Regression tests for OpenRouter connection settings and draft routing."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.llm.openrouter import utils as openrouter_utils
from app.llm.openrouter.common import build_openrouter_headers
from app.network import policy as outbound_policy


def test_provider_information_uses_omlorix_repository_as_attribution_fallback(monkeypatch):
    """Empty ranking settings must attribute normal chat requests to Omlorix."""
    provider = SimpleNamespace(
        provider="openrouter",
        api_key="sk-or-test",
        settings={},
    )
    monkeypatch.setattr(openrouter_utils, "get_llm_provider", lambda _db, _id: provider)

    provider_information = openrouter_utils.get_openrouter_provider_information(
        object(),
        "provider-id",
    )

    assert provider_information["ranking_url"] == "https://github.com/phinaldoo/omlorix"
    assert provider_information["ranking_title"] == "Omlorix"


def test_openrouter_headers_use_current_attribution_names_and_defaults():
    """All request paths share OpenRouter's current attribution contract."""
    headers = build_openrouter_headers("test-key", {})

    assert headers["HTTP-Referer"] == "https://github.com/phinaldoo/omlorix"
    assert headers["X-OpenRouter-Title"] == "Omlorix"
    assert "X-Title" not in headers


def test_openrouter_headers_preserve_configured_attribution():
    """Administrators can override both public attribution values."""
    headers = build_openrouter_headers(
        "test-key",
        {
            "ranking_url": "https://chat.example.test",
            "ranking_title": "Example Omlorix",
        },
    )

    assert headers["HTTP-Referer"] == "https://chat.example.test"
    assert headers["X-OpenRouter-Title"] == "Example Omlorix"


class _FakeResponse:
    """Provide the requests.Response surface used by OpenRouter discovery."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        """Represent a successful OpenRouter response."""

    def json(self):
        """Return the response payload supplied by the test."""
        return self._payload


@pytest.mark.parametrize(
    ("settings", "expected_base_url"),
    [
        ({"eu_routing": True}, "https://eu.openrouter.ai/api/v1"),
        ({"eu_routing": False}, "https://openrouter.ai/api/v1"),
    ],
)
def test_draft_model_listing_uses_selected_openrouter_host(
    monkeypatch,
    settings,
    expected_base_url,
):
    """Key validation and catalog discovery must share the draft routing host."""
    get_mock = Mock(
        side_effect=[
            _FakeResponse({"data": {"is_free_tier": False}}),
            _FakeResponse({"data": [{"id": "openai/test-model"}]}),
        ]
    )
    monkeypatch.setattr(openrouter_utils.requests, "get", get_mock)

    models = openrouter_utils.list_models_openrouter(
        object(),
        api_key="sk-or-test",
        provider_settings=settings,
    )

    assert [call.args[0] for call in get_mock.call_args_list] == [
        f"{expected_base_url}/key",
        f"{expected_base_url}/models/user",
    ]
    assert models[0]["id"] == "openai/test-model"


@pytest.mark.parametrize(
    ("eu_routing", "expected_target"),
    [
        (True, "https://eu.openrouter.ai/api/v1"),
        ("true", "https://eu.openrouter.ai/api/v1"),
        (False, "https://openrouter.ai/api/v1"),
        (None, "https://openrouter.ai/api/v1"),
    ],
)
def test_outbound_policy_checks_selected_openrouter_host(
    eu_routing,
    expected_target,
):
    """Policy evaluation must authorize the host the provider will contact."""
    assert outbound_policy.get_llm_provider_target(
        "openrouter",
        {"eu_routing": eu_routing},
    ) == expected_target
