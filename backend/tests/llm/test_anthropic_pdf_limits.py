import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.anthropic import attachments as anthropic_attachments
from app.llm.anthropic import utils as anthropic_utils


def test_oversized_pdf_uses_bounded_text_extract_instead_of_native_base64(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "large.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    file_info = {
        "path": str(pdf_path),
        "file_name": "large.pdf",
        "file_type": "application/pdf",
        "file_category": "document",
        "file_size": anthropic_utils.MAX_ANTHROPIC_NATIVE_DOCUMENT_BYTES + 1,
    }

    monkeypatch.setattr(
        anthropic_attachments,
        "get_file_info",
        lambda user_id, file_id: file_info if file_id == "large-pdf" else None,
    )
    monkeypatch.setattr(
        anthropic_attachments,
        "extract_text_from_file_info",
        lambda received_file_info: "bounded extracted text"
        if received_file_info is file_info
        else None,
    )

    result = anthropic_utils.upload_files(
        db=None,
        file_ids=["large-pdf"],
        user_id="user-1",
        input_formats_allowed=["pdf"],
        counters={
            "image": {"count": 0, "max": -1},
            "document": {"count": 0, "max": -1},
        },
    )

    assert result["unsupported"] is False
    assert result["counters"]["document"]["count"] == 1
    assert not any(part.get("type") == "document" for part in result["parts"])
    assert any(
        part == {"type": "text", "text": "bounded extracted text"}
        for part in result["parts"]
    )


def test_pdf_within_native_limit_still_embeds_as_anthropic_document(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "small.pdf"
    pdf_bytes = b"%PDF-1.7\nsmall"
    pdf_path.write_bytes(pdf_bytes)
    file_info = {
        "path": str(pdf_path),
        "file_name": "small.pdf",
        "file_type": "application/pdf",
        "file_category": "document",
        "file_size": len(pdf_bytes),
    }

    monkeypatch.setattr(
        anthropic_attachments,
        "get_file_info",
        lambda user_id, file_id: file_info if file_id == "small-pdf" else None,
    )

    result = anthropic_utils.upload_files(
        db=None,
        file_ids=["small-pdf"],
        user_id="user-1",
        input_formats_allowed=["pdf"],
        counters={
            "image": {"count": 0, "max": -1},
            "document": {"count": 0, "max": -1},
        },
    )

    document_parts = [part for part in result["parts"] if part.get("type") == "document"]
    assert len(document_parts) == 1
    assert document_parts[0]["source"]["media_type"] == "application/pdf"
    assert result["counters"]["document"]["count"] == 1
