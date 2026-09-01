from types import SimpleNamespace

import pytest

from app.llm import speech
from app.llm.elevenlabs import text_to_speech
from app.llm.schemas import ProviderEnum


class _EmptyModelCatalogResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return []


def test_empty_model_catalog_does_not_add_a_fallback(monkeypatch):
    """An empty ElevenLabs catalog must leave the model picker empty."""

    monkeypatch.setattr(
        text_to_speech.requests,
        "get",
        lambda *args, **kwargs: _EmptyModelCatalogResponse(),
    )

    assert text_to_speech.elevenlabs_text_to_speech_models_list("test-key") == []


def test_failed_model_discovery_does_not_add_a_fallback(monkeypatch):
    """A discovery error must not invent an ElevenLabs model ID."""

    def _raise_discovery_error(*args, **kwargs):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(
        speech,
        "elevenlabs_text_to_speech_models_list",
        _raise_discovery_error,
    )
    provider = SimpleNamespace(
        provider=ProviderEnum.elevenlabs.value,
        api_key="test-key",
    )

    assert speech.list_tts_models_for_provider(provider) == []


def test_audio_generation_requires_an_explicit_model():
    """Runtime synthesis must never substitute a default model."""

    with pytest.raises(ValueError, match="model is required"):
        text_to_speech.elevenlabs_generate_audio(
            api_key="test-key",
            model="",
            voice="test-voice",
            input_text="Hello",
        )
