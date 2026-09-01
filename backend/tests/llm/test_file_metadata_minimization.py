import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.helper import build_file_metadata_payload, build_file_metadata_text


def test_file_metadata_payload_includes_actionable_id_but_excludes_storage_names():
    payload = build_file_metadata_payload(
        "internal-file-id",
        {
            "file_name": "a0f18e4c-782a-4790-89c7-467fe8d5ed5a.pdf",
            "file_type": "application/pdf",
            "file_category": "document",
            "file_size": 12345,
            "meta": {"original_filename": "Quarterly Plan.pdf"},
        },
        native_context_included=False,
        model_context_representation="metadata_only",
        text_content_included=False,
    )

    assert payload == {
        "file_id": "internal-file-id",
        "file_name": "Quarterly Plan.pdf",
        "file_mime_type": "application/pdf",
        "file_category": "document",
        "file_size": 12345,
        "native_context_included": False,
        "model_context_representation": "metadata_only",
        "text_content_included": False,
    }


def test_file_metadata_payload_does_not_fall_back_to_stored_filename():
    payload = build_file_metadata_payload(
        "internal-file-id",
        {
            "file_name": "stored-object-name.txt",
            "file_type": "text/plain",
            "file_category": "document",
            "file_size": 50,
            "meta": {},
        },
    )

    assert payload == {
        "file_id": "internal-file-id",
        "file_mime_type": "text/plain",
        "file_category": "document",
        "file_size": 50,
    }


def test_file_metadata_text_and_preprocessing_hint_are_minimized():
    text = build_file_metadata_text(
        "internal-image-id",
        {
            "file_name": "stored-image.webp",
            "file_type": "image/webp",
            "file_category": "image",
            "file_size": 2048,
            "meta": {"original_filename": "Logo.webp"},
        },
        native_context_included=False,
        model_context_representation="metadata_only",
        text_content_included=False,
        provider_supported_image_mime_types={"image/png", "image/jpeg"},
    )

    payload = json.loads(text.removeprefix("Metadata of the file: "))
    assert payload["file_name"] == "Logo.webp"
    assert payload["suggested_code_execution_preprocessing"] == {
        "tool": "code_execution",
        "action": "convert_image_to_png",
        "output_filename": "Logo.png",
        "output_mime_type": "image/png",
        "reason": "This model input path does not natively support image/webp.",
    }
    assert payload["file_id"] == "internal-image-id"
    assert "stored-image.webp" not in text
    assert "stored_file_name" not in payload
    assert "original_file_name" not in payload
