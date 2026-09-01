from types import SimpleNamespace

from app.llm.google_aistudio import utils as google_utils
from app.llm.google_aistudio.schemas import (
    GOOGLE_AISTUDIO_PROVIDER_SCHEMA,
    GoogleAiStudioListModelsByok,
    GoogleAistudioSettings,
)


def _provider_field_keys() -> set[str]:
    """Return every field exposed by the Google AI Studio provider form."""
    return {
        field.key
        for section in GOOGLE_AISTUDIO_PROVIDER_SCHEMA.sections
        for field in section.fields
    }


def test_google_aistudio_configuration_only_contains_developer_api_fields():
    """Keep the provider and BYOK contracts limited to AI Studio settings."""
    assert set(GoogleAistudioSettings.model_fields) == {
        "api_version",
        "disable_background_sync",
        "enable_auto_delete_missing_models",
        "enable_notify_model_changes",
    }
    assert set(GoogleAiStudioListModelsByok.model_fields) == {
        "api_key",
        "api_version",
    }
    assert _provider_field_keys() == {
        "name",
        "api_key",
        "settings.api_version",
        "settings.disable_background_sync",
        "settings.enable_auto_delete_missing_models",
        "settings.enable_notify_model_changes",
    }


def test_google_aistudio_client_uses_api_key_authentication(monkeypatch):
    """Build the Developer API client with only its key and API version."""
    provider = SimpleNamespace(
        api_key="test-api-key",
        settings={"api_version": "v1"},
    )
    captured: dict = {}

    monkeypatch.setattr(google_utils, "get_llm_provider", lambda _db, _id: provider)

    def _client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(google_utils.genai, "Client", _client)

    google_utils.get_aistudio_client(SimpleNamespace(), "provider-id")

    assert set(captured) == {"api_key", "http_options"}
    assert captured["api_key"] == "test-api-key"
    assert captured["http_options"].api_version == "v1"
