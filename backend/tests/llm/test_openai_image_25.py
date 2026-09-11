import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.openai import image_generation as images
from app.llm.openai.image_settings import ImageOutputSettings


@pytest.mark.parametrize("model", sorted(images.GPT_IMAGE_25_IDS))
@pytest.mark.parametrize("edit", [False, True])
def test_image_25_request_and_format(monkeypatch, model, edit):
    client = MagicMock()
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"image").decode())], usage=SimpleNamespace(output_tokens=100))
    client.images.generate.return_value = client.images.edit.return_value = response
    monkeypatch.setattr(images, "Client", lambda **kwargs: client)
    kwargs = dict(api_key="test", model=model, prompt="test", size="1024x1024", quality="max", output_options={"output_format": "webp", "output_compression": 80, "background": "transparent"})
    result = images.edit_image_openai(**kwargs, reference_images=[b"reference"]) if edit else images.generate_image_openai(**kwargs)
    payload = (client.images.edit if edit else client.images.generate).call_args.kwargs
    assert payload["quality"] == "max"
    assert payload["output_format"] == "webp"
    assert payload["output_compression"] == 80
    assert "response_format" not in payload
    assert result["image_bytes"] == b"image"
    assert result["file_type"] == "image/webp"
    assert result["cost"] == pytest.approx(0.003)
    client.close.assert_called_once()


def test_image_25_dimensions_and_output_validation():
    for size in ["auto", "1024x1024", "1280x720", "3840x2160", "2160x3840"]:
        assert images.validate_gpt_image_size(size)
    for size in ["1000x1000", "512x512", "3840x3840", "3840x1024", "4096x2048", "0x0"]:
        assert not images.validate_gpt_image_size(size)
    with pytest.raises(ValueError):
        ImageOutputSettings(output_format="jpeg", background="transparent")
    with pytest.raises(ValueError):
        ImageOutputSettings(output_compression=101)
    assert ImageOutputSettings(output_compression=80).output_compression is None


def test_image_25_schema_defaults_and_compatible_parity():
    from app.llm.openai_responses.image_generation import get_image_generation_schema_part_2
    native = images.get_image_generation_schema_part_2("gpt-image-2.5-flare")
    assert get_image_generation_schema_part_2("gpt-image-2.5-flare") == native
    fields = {field.key: field for section in native.sections for field in section.fields}
    assert fields["settings.quality"].default == "auto"
    assert [option.value for option in fields["settings.quality"].options] == ["auto", "low", "medium", "high", "xhigh", "max"]
    assert fields["settings.enable_image_edit"].default is True
