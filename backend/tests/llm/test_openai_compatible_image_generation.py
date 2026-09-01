import base64
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.openai_responses import image_generation


def _install_capturing_openai_client(monkeypatch):
    """Install a fake client and return its captured Images API payload."""
    captured_payload = {}

    class FakeImages:
        def generate(self, **payload):
            captured_payload.update(payload)
            encoded = base64.b64encode(b"generated-image").decode("ascii")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])

    class FakeClient:
        def __init__(self, **_kwargs):
            self.images = FakeImages()

    monkeypatch.setattr(image_generation, "Client", FakeClient)
    return captured_payload


def test_openai_compatible_generation_forwards_selected_size(monkeypatch):
    """The shared tool size must reach OpenAI-compatible image endpoints."""
    captured_payload = _install_capturing_openai_client(monkeypatch)

    result = image_generation.generate_image_openai_responses(
        base_url="https://images.example.test/v1",
        api_key="test-key",
        model="gpt-image-2",
        prompt="draw a cat",
        size="1536x1024",
    )

    assert result == b"generated-image"
    assert captured_payload["size"] == "1536x1024"


def test_openai_compatible_schema_exposes_quality_values_with_off_default():
    """Custom endpoints can select any OpenAI quality family or omit it."""
    schema = image_generation.get_image_generation_schema_part_2()
    quality_field = next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "settings.quality"
    )

    assert quality_field.default == "off"
    assert [option.value for option in quality_field.options] == [
        "off",
        "auto",
        "low",
        "medium",
        "high",
        "standard",
        "hd",
    ]


def test_openai_compatible_generation_forwards_selected_quality(monkeypatch):
    """A selected quality is sent as a normal Images API parameter."""
    captured_payload = _install_capturing_openai_client(monkeypatch)

    image_generation.generate_image_openai_responses(
        base_url="https://images.example.test/v1",
        api_key="test-key",
        model="custom-image-model",
        prompt="draw a cat",
        quality="high",
    )

    assert captured_payload["quality"] == "high"


def test_openai_compatible_generation_omits_quality_when_off(monkeypatch):
    """The schema's Off value must not reach compatibility providers."""
    captured_payload = _install_capturing_openai_client(monkeypatch)

    image_generation.generate_image_openai_responses(
        base_url="https://images.example.test/v1",
        api_key="test-key",
        model="custom-image-model",
        prompt="draw a cat",
        quality="off",
    )

    assert "quality" not in captured_payload
