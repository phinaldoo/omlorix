import base64
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.openrouter import image_generation


def test_openrouter_image_schema_exposes_no_ineffective_quality_field():
    schema = image_generation.get_image_generation_schema_part_2()

    fields = [field for section in schema.sections for field in section.fields]
    assert fields == []


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [
        ("1K", "1K"),
        ("2k", "2K"),
        ("512x512", "512"),
        ("1536x1024", "2K"),
        ("4096x2048", "4K"),
    ],
)
def test_normalize_image_resolution_preserves_enums_and_maps_pixels(
    stored_value,
    expected,
):
    assert image_generation._normalize_image_resolution(stored_value) == expected


def test_decode_image_entry_accepts_inline_data_url():
    payload = base64.b64encode(b"png-bytes").decode("ascii")

    decoded = image_generation._decode_image_entry(
        {"image_url": {"url": f"data:image/png;base64,{payload}"}}
    )

    assert decoded == b"png-bytes"


def test_decode_image_entry_rejects_http_url_without_fetching(monkeypatch):
    get_mock = Mock(side_effect=AssertionError("provider image URL must not be fetched"))
    monkeypatch.setattr(image_generation.requests, "get", get_mock)

    decoded = image_generation._decode_image_entry(
        {"image_url": {"url": "http://127.0.0.1/latest/meta-data"}}
    )

    assert decoded is None
    get_mock.assert_not_called()


def test_generate_image_openrouter_does_not_fetch_provider_returned_http_url(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "images": [
                                {
                                    "image_url": {
                                        "url": "https://example.com/generated.png"
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

    get_mock = Mock(side_effect=AssertionError("provider image URL must not be fetched"))
    monkeypatch.setattr(image_generation.requests, "get", get_mock)
    monkeypatch.setattr(image_generation.requests, "post", Mock(return_value=FakeResponse()))

    with pytest.raises(RuntimeError, match="usable image data"):
        image_generation.generate_image_openrouter(
            api_key="test-key",
            chat_history=[{"role": "user", "content": "draw a cat"}],
            model="test-model",
        )

    get_mock.assert_not_called()


def test_generate_image_openrouter_sends_selected_aspect_ratio(monkeypatch):
    encoded_image = base64.b64encode(b"generated-image").decode("ascii")

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "created": 1,
                "data": [{"b64_json": encoded_image, "media_type": "image/png"}],
            }

    post_mock = Mock(return_value=FakeResponse())
    monkeypatch.setattr(image_generation.requests, "post", post_mock)
    monkeypatch.setattr(
        image_generation,
        "get_openrouter_image_models",
        lambda *_args, **_kwargs: [
            {
                "id": "google/gemini-image",
                "supported_parameters": {
                    "aspect_ratio": {
                        "type": "enum",
                        "values": ["1:1", "16:9"],
                    }
                },
            }
        ],
    )

    image_generation.generate_image_openrouter(
        api_key="test-key",
        chat_history=[{"role": "user", "content": "draw a cat"}],
        model="google/gemini-image",
        aspect_ratio="16:9",
    )

    assert post_mock.call_args.args[0].endswith("/images")
    assert post_mock.call_args.kwargs["json"] == {
        "model": "google/gemini-image",
        "prompt": "draw a cat",
        "aspect_ratio": "16:9",
    }
    assert post_mock.call_args.kwargs["headers"]["HTTP-Referer"] == (
        "https://github.com/phinaldoo/omlorix"
    )
    assert post_mock.call_args.kwargs["headers"]["X-OpenRouter-Title"] == "Omlorix"


def test_generate_image_openrouter_maps_pixels_to_supported_resolution(monkeypatch):
    encoded_image = base64.b64encode(b"generated-image").decode("ascii")

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"created": 1, "data": [{"b64_json": encoded_image}]}

    post_mock = Mock(return_value=FakeResponse())
    monkeypatch.setattr(image_generation.requests, "post", post_mock)
    monkeypatch.setattr(
        image_generation,
        "get_openrouter_image_models",
        lambda *_args, **_kwargs: [
            {
                "id": "google/gemini-image",
                "supported_parameters": {
                    "resolution": {
                        "type": "enum",
                        "values": ["1K", "2K", "4K"],
                    }
                },
            }
        ],
    )

    image_generation.generate_image_openrouter(
        api_key="test-key",
        chat_history=[{"role": "user", "content": "draw a cat"}],
        model="google/gemini-image",
        image_size="1536x1024",
    )

    assert post_mock.call_args.kwargs["json"]["resolution"] == "2K"
    assert "size" not in post_mock.call_args.kwargs["json"]


def test_generate_image_openrouter_rejects_model_unsupported_ratio(monkeypatch):
    post_mock = Mock()
    monkeypatch.setattr(image_generation.requests, "post", post_mock)
    monkeypatch.setattr(
        image_generation,
        "get_openrouter_image_models",
        lambda *_args, **_kwargs: [
            {
                "id": "test-model",
                "supported_parameters": {
                    "aspect_ratio": {"type": "enum", "values": ["1:1"]}
                },
            }
        ],
    )

    with pytest.raises(ValueError, match="not supported"):
        image_generation.generate_image_openrouter(
            api_key="test-key",
            chat_history=[{"role": "user", "content": "draw a cat"}],
            model="test-model",
            aspect_ratio="16:9",
        )

    post_mock.assert_not_called()
