"""Focused regression tests for normalized generated-media metadata."""

from types import SimpleNamespace

from app.tools.audio_generation import utils as audio_utils
from app.tools.image_generation import utils as image_utils


class _DB:
    """Minimal database session used by media tool orchestration tests."""

    def close(self) -> None:
        """Match the session cleanup contract."""


def test_audio_generation_preserves_explicit_null_bit_rate(monkeypatch):
    """A generator's explicit null must not revive a stale configured bit rate."""
    captured = {}
    provider = SimpleNamespace(
        id="xai-provider",
        provider="xai",
        api_key="secret",
    )
    monkeypatch.setattr(audio_utils, "SessionLocal", _DB)
    monkeypatch.setattr(audio_utils, "reserve_user_file_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        audio_utils,
        "release_user_file_quota_reservation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        audio_utils,
        "_get_audio_generation_config",
        lambda **_kwargs: {
            "provider_id": provider.id,
            "model_name": "grok-tts",
            "response_format": "wav",
            "bit_rate": 192_000,
        },
    )
    monkeypatch.setattr(audio_utils, "_resolve_provider", lambda *_args: provider)
    monkeypatch.setitem(
        audio_utils.PROVIDER_GENERATORS,
        "xai",
        lambda *_args, **_kwargs: {
            "audio_bytes": b"wav",
            "extension": "wav",
            "file_type": "audio/wav",
            "response_format": "wav",
            "bit_rate": None,
        },
    )

    def fake_persist(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="audio-file")

    monkeypatch.setattr(audio_utils, "persist_generated_file_bytes", fake_persist)

    audio_utils.audio_generation("hello", "user-1")

    assert captured["meta"]["bit_rate"] is None


def test_image_generation_rejects_jpeg_extension_for_png(monkeypatch):
    """A mismatched .jpeg declaration must use the PNG MIME's extension."""
    captured = {}
    provider = SimpleNamespace(
        id="xai-provider",
        provider="xai",
        api_key="secret",
    )
    monkeypatch.setattr(image_utils, "SessionLocal", _DB)
    monkeypatch.setattr(image_utils, "reserve_user_file_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        image_utils,
        "release_user_file_quota_reservation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        image_utils,
        "_get_image_generation_config",
        lambda: {
            "provider_id": provider.id,
            "model_name": "grok-imagine-image",
            "settings": {},
        },
    )
    monkeypatch.setattr(image_utils, "_resolve_provider", lambda *_args: provider)
    monkeypatch.setattr(
        image_utils,
        "_generate_via_xai",
        lambda *_args, **_kwargs: {
            "image_bytes": b"png",
            "file_type": "image/png",
            "extension": ".jpeg",
        },
    )

    def fake_persist(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="image-file")

    monkeypatch.setattr(image_utils, "persist_generated_file_bytes", fake_persist)

    image_utils.image_generation("draw this", "user-1")

    assert captured["file_type"] == "image/png"
    assert captured["original_filename"].endswith(".png")
    assert captured["file_name"].endswith(".png")
