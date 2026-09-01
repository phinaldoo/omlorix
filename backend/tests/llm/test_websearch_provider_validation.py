import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")

    class _FakeMarkItDown:
        def __init__(self, *args, **kwargs):
            pass

    fake_markitdown.MarkItDown = _FakeMarkItDown
    sys.modules["markitdown"] = fake_markitdown

from app.llm.utils import _validate_websearch_providers


def test_websearch_validation_allows_perplexity_search_with_separate_scrape_provider():
    search_provider = SimpleNamespace(provider="perplexity")
    scrape_provider = SimpleNamespace(provider="aiohttp")

    def _fake_get_provider(_db, provider_id):
        return {
            "search-provider": search_provider,
            "scrape-provider": scrape_provider,
        }[provider_id]

    def _fake_types(provider):
        if provider is search_provider:
            return ["combined"]
        if provider is scrape_provider:
            return ["scrape"]
        return []

    with patch("app.llm.utils.get_websearch_provider", side_effect=_fake_get_provider), patch(
        "app.llm.utils._get_provider_types", side_effect=_fake_types
    ):
        _validate_websearch_providers(
            ["web_search"],
            {
                "native_websearch": False,
                "websearch_search_provider": "search-provider",
                "websearch_scrape_provider": "scrape-provider",
            },
            db=object(),
        )


def test_websearch_validation_rejects_perplexity_as_scrape_provider():
    provider = SimpleNamespace(provider="perplexity")

    with patch("app.llm.utils.get_websearch_provider", return_value=provider), patch(
        "app.llm.utils._get_provider_types", return_value=["combined"]
    ):
        with pytest.raises(HTTPException) as exc_info:
            _validate_websearch_providers(
                ["web_search"],
                {
                    "native_websearch": False,
                    "websearch_search_provider": "perplexity-search",
                    "websearch_scrape_provider": "perplexity-scrape",
                },
                db=object(),
            )

    assert exc_info.value.status_code == 400
    assert "does not support direct URL scraping" in str(exc_info.value.detail)


def test_websearch_validation_allows_custom_provider_as_scrape_provider():
    provider = SimpleNamespace(provider="custom")

    with patch("app.llm.utils.get_websearch_provider", return_value=provider), patch(
        "app.llm.utils._get_provider_types", return_value=["search", "scrape"]
    ):
        _validate_websearch_providers(
            ["web_search"],
            {
                "native_websearch": False,
                "websearch_search_provider": "custom-provider",
                "websearch_scrape_provider": "custom-provider",
            },
            db=object(),
        )
