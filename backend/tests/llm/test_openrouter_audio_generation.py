"""Tests for OpenRouter's dedicated text-to-speech endpoint."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.llm.openrouter import audio_generation


def _provider():
    return SimpleNamespace(
        api_key="test-key",
        settings={
            "api_base_url": "https://openrouter.ai/api/v1",
            "ranking_url": "https://chat.example",
            "ranking_title": "Omlorix",
        },
    )


def test_openrouter_audio_uses_dedicated_speech_endpoint(monkeypatch):
    response = SimpleNamespace(status_code=200, content=b"mp3-data", text="")
    post_mock = Mock(return_value=response)
    monkeypatch.setattr(audio_generation.requests, "post", post_mock)

    result = audio_generation.openrouter_generate_audio(
        provider=_provider(),
        model="mistralai/voxtral-mini-tts-2603",
        voice="en_paul_neutral",
        input_text="Hello",
        instructions=None,
        response_format="mp3",
    )

    assert post_mock.call_args.args[0] == "https://openrouter.ai/api/v1/audio/speech"
    assert post_mock.call_args.kwargs["json"] == {
        "model": "mistralai/voxtral-mini-tts-2603",
        "input": "Hello",
        "voice": "en_paul_neutral",
        "response_format": "mp3",
    }
    assert post_mock.call_args.kwargs["headers"]["HTTP-Referer"] == "https://chat.example"
    assert post_mock.call_args.kwargs["headers"]["X-OpenRouter-Title"] == "Omlorix"
    assert "X-Title" not in post_mock.call_args.kwargs["headers"]
    assert result["audio_bytes"] == b"mp3-data"
    assert result["file_type"] == "audio/mpeg"


def test_openrouter_audio_surfaces_json_error(monkeypatch):
    response = SimpleNamespace(
        status_code=429,
        content=b"",
        text='{"error":{"message":"Rate limited"}}',
        json=lambda: {"error": {"message": "Rate limited"}},
    )
    monkeypatch.setattr(audio_generation.requests, "post", Mock(return_value=response))

    with pytest.raises(RuntimeError, match="Rate limited"):
        audio_generation.openrouter_generate_audio(
            provider=_provider(),
            model="tts-model",
            voice="provider-voice",
            input_text="Hello",
            instructions=None,
            response_format="pcm16",
        )


def test_openrouter_audio_rejects_missing_provider_specific_voice(monkeypatch):
    post_mock = Mock()
    monkeypatch.setattr(audio_generation.requests, "post", post_mock)

    with pytest.raises(ValueError, match="voice is required"):
        audio_generation.openrouter_generate_audio(
            provider=_provider(),
            model="provider/tts-model",
            voice="  ",
            input_text="Hello",
            instructions=None,
            response_format="pcm16",
        )

    post_mock.assert_not_called()


def test_openrouter_audio_forwards_openai_speaking_instructions(monkeypatch):
    response = SimpleNamespace(status_code=200, content=b"mp3-data", text="")
    post_mock = Mock(return_value=response)
    monkeypatch.setattr(audio_generation.requests, "post", post_mock)

    audio_generation.openrouter_generate_audio(
        provider=_provider(),
        model="openai/gpt-4o-mini-tts-2025-12-15",
        voice="nova-custom",
        input_text="Hello",
        instructions="Speak warmly.",
        response_format="mp3",
    )

    assert post_mock.call_args.kwargs["json"]["provider"] == {
        "options": {"openai": {"instructions": "Speak warmly."}}
    }


def test_openrouter_audio_discovers_only_speech_models(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "data": [
                {
                    "id": "openai/gpt-4o-mini-tts",
                    "name": "OpenAI TTS",
                    "architecture": {"output_modalities": ["speech"]},
                },
                {
                    "id": "provider/speech-model",
                    "name": "Provider TTS",
                    "architecture": {"output_modalities": ["speech"]},
                },
                {
                    "id": "provider/audio-model",
                    "architecture": {"output_modalities": ["audio"]},
                },
            ]
        },
    )
    monkeypatch.setattr(audio_generation.requests, "get", Mock(return_value=response))

    models = audio_generation.openrouter_text_to_speech_models_list(_provider())

    assert [model["id"] for model in models] == [
        "openai/gpt-4o-mini-tts",
        "provider/speech-model",
    ]
    assert models[0]["support_custom_instructions"] is True
    assert models[1]["support_custom_instructions"] is False
    assert all(model["voice_required"] is True for model in models)
