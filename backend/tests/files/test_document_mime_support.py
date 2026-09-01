from __future__ import annotations

import mimetypes
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.files import utils as file_utils  # noqa: E402


def test_odb_mimetypes_alias_is_allowed_for_upload_validation():
    guessed_mime, _ = mimetypes.guess_type("database.odb")

    assert file_utils.validate_file_type("application/vnd.oasis.opendocument.database")
    assert file_utils.validate_file_type("application/vnd.oasis.opendocument.base")
    assert guessed_mime is None or file_utils.validate_file_type(guessed_mime)


def test_safe_svg_is_an_allowed_text_document(tmp_path):
    """Safe SVG uploads must be accepted and categorized for text extraction."""
    svg_path = tmp_path / "diagram.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h10v10z"/></svg>',
        encoding="utf-8",
    )

    assert file_utils.validate_file_type("image/svg+xml")
    assert file_utils.validate_file_type("image/svg+xml; charset=utf-8")
    assert file_utils.get_file_category("image/svg+xml") == "document"
    assert file_utils.get_file_category("image/svg+xml; charset=utf-8") == "document"
    assert file_utils.detect_and_validate_upload_mime(svg_path) == "image/svg+xml"


def test_active_svg_is_still_rejected(tmp_path):
    """Allowing SVG text must not bypass the existing active-content scan."""
    svg_path = tmp_path / "active.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        file_utils.detect_and_validate_upload_mime(svg_path)

    assert exc_info.value.status_code == 400


def test_namespaced_active_svg_is_rejected(tmp_path):
    """An XML namespace prefix must not disguise a dangerous SVG element."""
    svg_path = tmp_path / "namespaced-active.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="urn:active">'
        "<s:script>alert(1)</s:script>"
        "</svg>",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        file_utils.detect_and_validate_upload_mime(svg_path)

    assert exc_info.value.status_code == 400


def test_namespaced_event_attribute_is_rejected_by_local_name(tmp_path):
    """Namespace-qualified event attributes must remain active-content failures."""
    svg_path = tmp_path / "namespaced-event.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:e="urn:active" '
        'e:onload="alert(1)"/>',
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        file_utils.detect_and_validate_upload_mime(svg_path)

    assert exc_info.value.status_code == 400
