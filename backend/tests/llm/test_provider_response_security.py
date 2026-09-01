import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.openai.custom_headers import CUSTOM_HEADER_SECRET_PLACEHOLDER
from app.llm.schemas import serialize_llm_provider_detail


def test_provider_detail_response_does_not_expose_api_key():
    provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        name="Primary OpenAI",
        icon="openai",
        api_key="sk-secret-provider-key",
        settings={"base_url": "https://api.openai.com/v1"},
        status={"available": "unknown"},
    )

    payload = serialize_llm_provider_detail(provider).model_dump()

    assert "api_key" not in payload
    assert payload["has_api_key"] is True
    assert payload["api_key_preview"] == "sk-secr..."
    assert "sk-secret-provider-key" not in str(payload)


def test_provider_detail_response_handles_missing_api_key():
    provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        name="Primary OpenAI",
        icon=None,
        api_key="",
        settings={},
        status={},
    )

    payload = serialize_llm_provider_detail(provider).model_dump()

    assert "api_key" not in payload
    assert payload["has_api_key"] is False
    assert payload["api_key_preview"] is None


def test_provider_detail_response_redacts_custom_header_values():
    provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        name="Primary OpenAI",
        icon="openai",
        api_key="sk-secret-provider-key",
        settings={
            "timeout": 30,
            "custom_headers": ["X-API-Key: provider-header-secret", "X-Trace-ID: trace-secret"],
        },
        status={"available": "unknown"},
    )

    payload = serialize_llm_provider_detail(provider).model_dump()

    assert payload["settings"]["custom_headers"] == [
        f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
        f"X-Trace-ID: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
    ]
    assert "timeout" not in payload["settings"]
    assert "provider-header-secret" not in str(payload)
    assert "trace-secret" not in str(payload)
