"""Tests for OpenAI models that have reached their shutdown date."""

from types import SimpleNamespace

from app.llm.openai import realtime
from app.llm.openai.model_list import (
    OPENAI_AUDIO_MODELS,
    OPENAI_COMPLETION_MODELS,
    OPENAI_DEPRECATED_MODELS,
    OPENAI_MODEL_DICT,
    OPENAI_REALTIME_MODELS,
    OPENAI_UNSUPPORTED_MODELS,
    OPENAI_UNSUPPORTED_REALTIME_MODELS,
)

SHUT_DOWN_MODELS = {
    "gpt-4-0314",
    "gpt-4-1106-preview",
    "gpt-4-0125-preview",
    "gpt-4-turbo-preview",
    "gpt-4-turbo-preview-completions",
    "gpt-4o-realtime-preview",
    "gpt-4o-realtime-preview-2025-06-03",
    "gpt-4o-realtime-preview-2024-12-17",
    "gpt-4o-mini-realtime-preview",
    "gpt-4o-audio-preview",
    "gpt-4o-mini-audio-preview",
}


def test_shut_down_models_are_deprecated_not_active() -> None:
    """Keep shut down IDs out of every active OpenAI model group."""
    deprecated = set(OPENAI_DEPRECATED_MODELS)
    catalog_ids = {
        model_id
        for group_name, capabilities in OPENAI_MODEL_DICT.items()
        for model_id in [group_name, *(capabilities.get("ids") or [])]
    }

    assert SHUT_DOWN_MODELS <= deprecated
    assert SHUT_DOWN_MODELS.isdisjoint(catalog_ids)
    assert SHUT_DOWN_MODELS.isdisjoint(OPENAI_COMPLETION_MODELS)
    assert SHUT_DOWN_MODELS.isdisjoint(OPENAI_REALTIME_MODELS)
    assert SHUT_DOWN_MODELS.isdisjoint(OPENAI_AUDIO_MODELS)
    assert SHUT_DOWN_MODELS <= set(OPENAI_UNSUPPORTED_MODELS)
    assert SHUT_DOWN_MODELS <= set(OPENAI_UNSUPPORTED_REALTIME_MODELS)


def test_realtime_discovery_only_returns_supported_provider_models(monkeypatch) -> None:
    """Return only eligible IDs supplied by OpenAI's model endpoint."""

    class FakeClient:
        """Provide the small OpenAI client surface used by discovery."""

        def __init__(self, **_kwargs) -> None:
            self.models = self

        def list(self):
            return [
                SimpleNamespace(id="gpt-4o-realtime-preview"),
                SimpleNamespace(id="gpt-live-transcribe"),
                SimpleNamespace(id="gpt-realtime-whisper"),
                SimpleNamespace(id="gpt-realtime-1.5"),
            ]

    monkeypatch.setattr(realtime, "Client", FakeClient)
    monkeypatch.setattr(realtime, "_resolve_openai_client_kwargs", lambda *_args, **_kwargs: {})

    models = realtime.get_openai_realtime_models(byok={"api_key": "test"})

    assert "gpt-4o-realtime-preview" not in models
    assert "gpt-live-transcribe" not in models
    assert "gpt-realtime-whisper" not in models
    assert models == ["gpt-realtime-1.5"]
