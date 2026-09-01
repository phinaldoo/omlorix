"""Persistence coverage for xAI-specific text-to-speech controls."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.admin.settings import utils as admin_utils
from app.admin.settings.schema_categories.audio_generation import AudioGenerationSettings
from app.settings.models import Settings


def _configure_audio_settings_test(monkeypatch, *, provider_type: str = "xai"):
    """Build a minimal settings/database surface for audio configuration tests."""
    provider = SimpleNamespace(
        id="tts-provider",
        provider=provider_type,
        api_key="test-key",
        settings={},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = provider
    record = Settings(
        page_name="audio_generation",
        data=AudioGenerationSettings().model_dump(),
    )
    monkeypatch.setattr(admin_utils, "get_settings_page", lambda *_args: record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)
    monkeypatch.setattr(
        admin_utils,
        "_get_audio_generation_model_options",
        lambda *_args: [{"value": "grok-tts", "label": "Grok Text to Speech"}],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_audio_generation_model_capabilities_for_provider",
        lambda *_args, **_kwargs: {
            "voices": ["eve", "ara", "rex", "sal", "leo"],
            "response_formats": ["mp3", "wav", "pcm", "mulaw", "alaw"],
            "voice_required": True,
            "supports_custom_voice": True,
        },
    )
    return db, record


def test_xai_tts_controls_are_normalized_and_persisted(monkeypatch):
    """Select/number values from the browser should retain their native types."""
    db, record = _configure_audio_settings_test(monkeypatch)

    changed = admin_utils.update_admin_settings_values_for_page(
        "audio_generation",
        {
            "provider_id": "tts-provider",
            "model_name": "grok-tts",
            "voice": "custom01",
            "response_format": "mp3",
            "language": "de",
            "sample_rate": "44100",
            "bit_rate": "192000",
            "speed": "1.2",
            "optimize_streaming_latency": "1",
            "text_normalization": "true",
        },
        db,
    )

    assert "sample_rate" in changed
    assert record.data["voice"] == "custom01"
    assert record.data["language"] == "de"
    assert record.data["sample_rate"] == 44100
    assert record.data["bit_rate"] == 192000
    assert record.data["speed"] == 1.2
    assert record.data["optimize_streaming_latency"] == 1
    assert record.data["text_normalization"] is True


def test_non_xai_provider_clears_stale_xai_tts_controls(monkeypatch):
    """Switching TTS providers must not leak xAI-only request parameters."""
    db, record = _configure_audio_settings_test(monkeypatch, provider_type="openai")
    record.data.update(
        {
            "language": "de",
            "sample_rate": 44100,
            "bit_rate": 192000,
            "speed": 1.2,
            "optimize_streaming_latency": 1,
            "text_normalization": True,
        }
    )

    admin_utils.update_admin_settings_values_for_page(
        "audio_generation",
        {
            "provider_id": "tts-provider",
            "model_name": "grok-tts",
            "voice": "eve",
            "response_format": "mp3",
        },
        db,
    )

    for key in (
        "language",
        "sample_rate",
        "bit_rate",
        "speed",
        "optimize_streaming_latency",
        "text_normalization",
    ):
        assert record.data[key] is None
