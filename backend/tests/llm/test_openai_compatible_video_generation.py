from types import SimpleNamespace

import pytest

from app.admin.settings import utils as admin_settings_utils
from app.llm.openai_responses import video_generation
from app.tools.video_generation import utils as video_tool_utils


CUSTOM_OPENAI_PROVIDER_TYPES = {
    "openai_responses",
    "openai_chat_completions",
}


def _provider(
    provider_type: str = "openai_responses",
    *,
    base_url: str | None = "https://video.example.test/v1/",
):
    settings = {
        "custom_headers": ["X-Tenant: omlorix"],
    }
    if base_url is not None:
        settings["base_url"] = base_url
    return SimpleNamespace(
        provider=provider_type,
        api_key="secret",
        settings=settings,
    )


def test_native_openai_is_not_a_video_generation_provider() -> None:
    """Native OpenAI must stay unavailable in UI validation and runtime dispatch."""
    assert "openai" not in admin_settings_utils.VIDEO_GENERATION_PROVIDER_TYPES
    assert "openai" not in video_tool_utils.PROVIDER_GENERATORS
    assert "openai" not in video_tool_utils.VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES

    with pytest.raises(ValueError, match="Unsupported video generation provider"):
        video_tool_utils._generate_video_payload(
            _provider("openai"),
            "native-video-model",
            "Make a video",
            {},
        )


def test_custom_openai_provider_types_keep_video_generation_support() -> None:
    """Both custom OpenAI protocols share the compatible Videos adapter."""
    assert CUSTOM_OPENAI_PROVIDER_TYPES <= (
        admin_settings_utils.VIDEO_GENERATION_PROVIDER_TYPES
    )
    assert CUSTOM_OPENAI_PROVIDER_TYPES <= set(video_tool_utils.PROVIDER_GENERATORS)
    assert CUSTOM_OPENAI_PROVIDER_TYPES <= (
        video_tool_utils.VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES
    )


def test_compatible_model_discovery_uses_custom_endpoint_and_headers(
    monkeypatch,
) -> None:
    """Model discovery must never fall back to OpenAI's native endpoint."""
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = SimpleNamespace(
                list=lambda: [
                    SimpleNamespace(
                        id="vendor/video-model",
                        created=123,
                        object="model",
                        owned_by="vendor",
                    )
                ]
            )

    monkeypatch.setattr(video_generation, "Client", FakeClient)

    models = video_generation.openai_compatible_video_generation_models_list(
        _provider()
    )

    assert captured == {
        "api_key": "secret",
        "base_url": "https://video.example.test/v1/",
        "default_headers": {"X-Tenant": "omlorix"},
    }
    assert models == [
        {
            "id": "vendor/video-model",
            "created": 123,
            "object": "model",
            "owned_by": "vendor",
        }
    ]


def test_compatible_video_generation_requires_a_custom_base_url() -> None:
    """Missing custom routing must fail instead of reaching api.openai.com."""
    provider = _provider(base_url=None)

    with pytest.raises(ValueError, match="base URL is required"):
        video_generation.openai_compatible_video_base_url(provider)

    with pytest.raises(ValueError, match="base URL is required"):
        video_generation.openai_compatible_video_generation_models_list(provider)


def test_compatible_payload_keeps_provider_specific_model_and_size() -> None:
    """Compatible providers keep arbitrary model IDs and supported dimensions."""
    payload = video_generation._build_video_generation_payload(
        "vendor/video-model-v3",
        "A paper bird takes flight",
        {
            "duration_seconds": 17,
            "size": "1536x864",
        },
    )

    assert payload == {
        "model": "vendor/video-model-v3",
        "prompt": "A paper bird takes flight",
        "seconds": "17",
        "size": "1536x864",
    }
