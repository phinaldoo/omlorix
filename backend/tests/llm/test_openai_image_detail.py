from pathlib import Path

from app.llm.openai import schemas as openai_schemas
from app.llm.openai import utils as openai_responses_utils
from app.llm.openai_chat_completions import utils as openai_chat_utils


def _write_image(tmp_path: Path) -> Path:
    """Create a tiny image-like payload; upload code only needs readable bytes."""
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"not-a-real-png-but-good-enough-for-payload-tests")
    return image_path


def _file_info(image_path: Path) -> dict:
    """Return the file metadata shape expected by the OpenAI upload helpers."""
    return {
        "file_name": image_path.name,
        "file_size": image_path.stat().st_size,
        "file_type": "image/png",
        "file_category": "image",
        "path": str(image_path),
    }


def _image_part(parts: list[dict], part_type: str) -> dict:
    """Pick the generated image part from a mixed metadata/image payload."""
    return next(part for part in parts if part.get("type") == part_type)


def test_openai_responses_upload_applies_image_detail(tmp_path, monkeypatch):
    image_path = _write_image(tmp_path)
    monkeypatch.setattr(
        openai_responses_utils,
        "get_file_info",
        lambda _user_id, _file_id: _file_info(image_path),
    )

    result = openai_responses_utils.upload_files(
        db=None,
        file_ids=["image-1"],
        user_id="user-1",
        counters={"image": {"count": 0, "max": -1}},
        input_formats_allowed=["image"],
        image_detail="original",
    )

    image_part = _image_part(result["parts"], "input_image")
    assert image_part["detail"] == "original"
    assert image_part["image_url"].startswith("data:image/png;base64,")


def test_openai_chat_completions_upload_applies_image_detail(tmp_path, monkeypatch):
    image_path = _write_image(tmp_path)
    monkeypatch.setattr(
        openai_chat_utils,
        "get_file_info",
        lambda _user_id, _file_id: _file_info(image_path),
    )

    result = openai_chat_utils.upload_files(
        db=None,
        file_ids=["image-1"],
        user_id="user-1",
        counters={"image": {"count": 0, "max": -1}},
        input_formats_allowed=["image"],
        image_detail="high",
    )

    image_part = _image_part(result["parts"], "image_url")
    assert image_part["image_url"]["detail"] == "high"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_responses_upload_inlines_svg_xml_as_text(tmp_path, monkeypatch):
    """SVG context must be XML text rather than an unsupported vision part."""
    svg_path = tmp_path / "diagram.svg"
    svg_source = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
    svg_path.write_text(svg_source, encoding="utf-8")
    file_info = {
        "file_name": svg_path.name,
        "file_size": svg_path.stat().st_size,
        "file_type": "image/svg+xml",
        "file_category": "document",
        "path": str(svg_path),
        "meta": {"original_filename": svg_path.name},
    }
    monkeypatch.setattr(
        openai_responses_utils,
        "get_file_info",
        lambda _user_id, _file_id: file_info,
    )

    result = openai_responses_utils.upload_files(
        db=None,
        file_ids=["svg-1"],
        user_id="user-1",
        counters={"document": {"count": 0, "max": -1}},
        input_formats_allowed=["text"],
    )

    assert result["unsupported"] is False
    assert not [part for part in result["parts"] if part.get("type") == "input_image"]
    text_parts = [part["text"] for part in result["parts"] if part.get("type") == "input_text"]
    assert any('"model_context_representation": "text_extract"' in text for text in text_parts)
    assert any(svg_source in text for text in text_parts)


def test_openai_responses_upload_inlines_html_source_as_text(tmp_path, monkeypatch):
    """Uploaded HTML must reach the model as inert text, never a native file."""
    html_path = tmp_path / "page.html"
    html_source = '<!doctype html><html><body><script>alert("inert")</script></body></html>'
    html_path.write_text(html_source, encoding="utf-8")
    file_info = {
        "file_name": html_path.name,
        "file_size": html_path.stat().st_size,
        "file_type": "text/html",
        "file_category": "document",
        "path": str(html_path),
        "meta": {"original_filename": html_path.name},
    }
    monkeypatch.setattr(
        openai_responses_utils,
        "get_file_info",
        lambda _user_id, _file_id: file_info,
    )

    result = openai_responses_utils.upload_files(
        db=None,
        file_ids=["html-1"],
        user_id="user-1",
        counters={"document": {"count": 0, "max": -1}},
        input_formats_allowed=["text"],
    )

    assert result["unsupported"] is False
    assert not [part for part in result["parts"] if part.get("type") == "input_file"]
    text_parts = [part["text"] for part in result["parts"] if part.get("type") == "input_text"]
    assert any('"model_context_representation": "text_extract"' in text for text in text_parts)
    assert any(html_source in text for text in text_parts)


def test_openai_image_detail_is_exposed_as_model_setting():
    schema = openai_schemas.get_parameters_schema_filled({"image_detail": "low"})

    image_fields = [
        field
        for section in schema.sections
        if section.title == "Image inputs"
        for field in section.fields
    ]

    assert image_fields
    assert image_fields[0].key == "settings.image_detail"
    assert image_fields[0].value == "low"
    assert [option.value for option in image_fields[0].options] == [
        "auto",
        "low",
        "high",
        "original",
    ]
