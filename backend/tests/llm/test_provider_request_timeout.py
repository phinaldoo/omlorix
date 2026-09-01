from types import SimpleNamespace

import pytest
from app.llm.azure_openai.schemas import AzureOpenAISettings
from app.llm.base_settings import LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS
from app.llm.elevenlabs.schemas import ElevenlabsSettings
from app.llm.openai.schemas import OpenaiSettings
from app.llm.openai.utils import _resolve_openai_client_context
from app.llm.openai_responses.schemas import OpenaiResponsesSettings
from app.llm.schemas import PROVIDER_SETTINGS_SCHEMAS
from app.llm.speech import _generate_via_elevenlabs
from app.llm.xai.common import xai_timeout
from app.llm.xai.schemas import XAISettings


@pytest.mark.parametrize(
    ("settings_model", "required_values"),
    [
        (OpenaiSettings, {}),
        (OpenaiResponsesSettings, {}),
        (AzureOpenAISettings, {"azure_endpoint": "https://example.openai.azure.com"}),
        (ElevenlabsSettings, {}),
        (XAISettings, {}),
    ],
)
def test_provider_settings_discard_removed_custom_timeout(settings_model, required_values):
    """Legacy timeout input must not survive validation for any former owner."""

    settings = settings_model.model_validate({**required_values, "timeout": 1})
    assert "timeout" not in settings.model_dump()


def test_no_provider_form_exposes_custom_timeout():
    """The administrator schema must omit the timeout control for every provider."""

    field_keys = {
        field.key
        for schema in PROVIDER_SETTINGS_SCHEMAS.values()
        for section in schema.sections
        for field in section.fields
    }
    assert "settings.timeout" not in field_keys


@pytest.mark.parametrize(
    ("provider_type", "byok"),
    [
        ("openai", {"api_key": "sk-test"}),
        ("openai_responses", {"api_key": "sk-test"}),
        ("openai_chat_completions", {"api_key": "sk-test"}),
        (
            "microsoft_azure",
            {
                "api_key": "azure-test",
                "azure_endpoint": "https://example.openai.azure.com",
            },
        ),
        ("lmstudio", {"base_url": "http://localhost:1234/v1"}),
        ("xai", {"api_key": "xai-test", "base_url": "https://api.x.ai/v1"}),
    ],
)
def test_openai_compatible_clients_ignore_custom_timeout(provider_type, byok):
    """Every OpenAI-compatible SDK client receives the fixed 120s deadline."""

    context = _resolve_openai_client_context(
        None,
        byok={**byok, "timeout": 1},
        openai_provider_type=provider_type,
    )

    assert context["client_kwargs"]["timeout"] == LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS


def test_native_xai_and_elevenlabs_adapters_use_fixed_timeout(monkeypatch):
    """Native provider adapters must ignore timeout values in old stored JSON."""

    captured: dict[str, int] = {}

    def fake_elevenlabs_generate_audio(**kwargs):
        captured["elevenlabs"] = kwargs["timeout"]
        return {}

    monkeypatch.setattr(
        "app.llm.speech.elevenlabs_generate_audio", fake_elevenlabs_generate_audio
    )
    legacy_provider = SimpleNamespace(
        api_key="test-key",
        settings={"timeout": 1},
    )
    _generate_via_elevenlabs(
        legacy_provider, "eleven_multilingual_v2", "Hello", None, False, {"voice": "voice"}
    )
    assert xai_timeout() == LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS
    assert captured == {"elevenlabs": LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS}
