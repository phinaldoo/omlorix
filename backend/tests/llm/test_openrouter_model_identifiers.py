"""Regression tests for canonical and legacy OpenRouter model identifiers."""

import pytest

from app.llm.openrouter import utils as openrouter_utils


class _FakeResponse:
    """Provide the small requests.Response surface used by the helpers."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        """Represent a successful OpenRouter response."""

    def json(self):
        """Return the response payload supplied by the test."""
        return self._payload


def _catalog_model():
    """Return an OpenRouter entry shaped like ``list_models_openrouter`` output."""
    return {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "provider": "nvidia",
        "model": "nemotron-3-ultra-550b-a55b:free",
        "name": "NVIDIA: Nemotron 3 Ultra",
        "description": "Test model",
        "architecture": {"input_modalities": ["text"]},
        "knowledge_cutoff": "2025-06-01",
    }


def test_model_information_resolves_legacy_short_name_from_catalog(monkeypatch):
    """Editing an old short-name row must call the canonical metadata URL."""
    requested_urls = []
    monkeypatch.setattr(
        openrouter_utils,
        "get_openrouter_provider_information",
        lambda _db, _provider_id: {"api_key": "test-key", "settings": {}},
    )
    monkeypatch.setattr(
        openrouter_utils,
        "list_models_openrouter",
        lambda *_args, **_kwargs: [_catalog_model()],
    )

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return _FakeResponse(
            {
                "data": {
                    "endpoints": [
                        {
                            "provider_name": "NVIDIA",
                            "supported_parameters": ["tools"],
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(openrouter_utils.requests, "get", fake_get)

    result = openrouter_utils.get_model_information_endpoint(
        object(),
        "nemotron-3-ultra-550b-a55b:free",
        "provider-id",
        "NVIDIA",
    )

    assert requested_urls == [
        "https://openrouter.ai/api/v1/models/nvidia/nemotron-3-ultra-550b-a55b:free/endpoints"
    ]
    assert result["author"] == "nvidia"
    assert result["slug"] == "nemotron-3-ultra-550b-a55b:free"
    assert result["endpoint"]["provider_name"] == "NVIDIA"


def test_provider_listing_resolves_legacy_short_name_from_catalog(monkeypatch):
    """The provider picker must also support rows saved with a short model name."""
    requested_urls = []
    monkeypatch.setattr(
        openrouter_utils,
        "get_openrouter_provider_information",
        lambda _db, _provider_id: {
            "provider": object(),
            "api_key": "test-key",
            "settings": {},
        },
    )
    monkeypatch.setattr(
        openrouter_utils,
        "list_models_openrouter",
        lambda *_args, **_kwargs: [_catalog_model()],
    )

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return _FakeResponse(
            {
                "data": {
                    "endpoints": [
                        {
                            "name": "NVIDIA endpoint",
                            "provider_name": "NVIDIA",
                            "pricing": {},
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(openrouter_utils.requests, "get", fake_get)

    providers = openrouter_utils.get_model_providers(
        object(),
        "provider-id",
        "nemotron-3-ultra-550b-a55b:free",
    )

    assert requested_urls == [
        "https://openrouter.ai/api/v1/models/nvidia/nemotron-3-ultra-550b-a55b:free/endpoints"
    ]
    assert providers[0]["provider_name"] == "NVIDIA"


def test_provider_listing_rejects_ambiguous_legacy_short_name(monkeypatch):
    """A legacy slug shared by multiple authors must not depend on catalog order."""
    duplicate_slug = "shared-model:free"
    monkeypatch.setattr(
        openrouter_utils,
        "list_models_openrouter",
        lambda *_args, **_kwargs: [
            {
                "id": f"author-one/{duplicate_slug}",
                "provider": "author-one",
                "model": duplicate_slug,
            },
            {
                "id": f"author-two/{duplicate_slug}",
                "provider": "author-two",
                "model": duplicate_slug,
            },
        ],
    )

    with pytest.raises(ValueError, match="Ambiguous legacy OpenRouter model slug") as exc_info:
        openrouter_utils.get_model_providers(
            object(),
            "provider-id",
            duplicate_slug,
        )

    assert "author-one/shared-model:free" in str(exc_info.value)
    assert "author-two/shared-model:free" in str(exc_info.value)
