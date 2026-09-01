from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.svg import rasterize_svg_to_png_bytes


def test_rasterize_svg_uses_svg_string_and_requested_dimensions(monkeypatch):
    """SVG bytes should be decoded and passed through with explicit dimensions."""

    calls: list[dict] = []

    def fake_svg_to_bytes(**kwargs):
        """Capture renderer input and return byte-like PNG data."""
        calls.append(kwargs)
        return bytearray(b"png-data")

    monkeypatch.setitem(sys.modules, "resvg_py", SimpleNamespace(svg_to_bytes=fake_svg_to_bytes))

    result = rasterize_svg_to_png_bytes(
        svg_bytes=b"<svg xmlns='http://www.w3.org/2000/svg' />",
        output_width=32,
        output_height=32,
    )

    assert result == b"png-data"
    assert calls == [
        {
            "svg_string": "<svg xmlns='http://www.w3.org/2000/svg' />",
            "background": "rgba(0,0,0,0)",
            "width": 32,
            "height": 32,
        }
    ]


def test_rasterize_svg_uses_declared_xml_encoding(monkeypatch):
    """SVG bytes should honor a non-UTF-8 XML encoding declaration."""

    calls: list[dict] = []

    def fake_svg_to_bytes(**kwargs):
        """Capture renderer input and return PNG bytes."""
        calls.append(kwargs)
        return b"png-data"

    monkeypatch.setitem(sys.modules, "resvg_py", SimpleNamespace(svg_to_bytes=fake_svg_to_bytes))

    svg = "<?xml version='1.0' encoding='iso-8859-1'?><svg><text>café</text></svg>"
    result = rasterize_svg_to_png_bytes(svg_bytes=svg.encode("iso-8859-1"))

    assert result == b"png-data"
    assert calls[0]["svg_string"] == svg


def test_rasterize_svg_falls_back_when_declared_encoding_fails(monkeypatch):
    """Invalid declared encodings should fall back before rendering."""

    calls: list[dict] = []

    def fake_svg_to_bytes(**kwargs):
        """Capture renderer input and return PNG bytes."""
        calls.append(kwargs)
        return b"png-data"

    monkeypatch.setitem(sys.modules, "resvg_py", SimpleNamespace(svg_to_bytes=fake_svg_to_bytes))

    svg_bytes = b'<?xml version="1.0" encoding="unknown-encoding"?><svg><text>\xff</text></svg>'
    result = rasterize_svg_to_png_bytes(svg_bytes=svg_bytes)

    assert result == b"png-data"
    assert calls[0]["svg_string"] == svg_bytes.decode("latin-1")


def test_rasterize_svg_uses_svg_path_when_bytes_are_not_provided(monkeypatch, tmp_path):
    """Path-based rendering should forward the filesystem path and coerce list output."""

    calls: list[dict] = []

    def fake_svg_to_bytes(**kwargs):
        """Capture path-based renderer input and return a list of byte values."""
        calls.append(kwargs)
        return [80, 78, 71]

    monkeypatch.setitem(sys.modules, "resvg_py", SimpleNamespace(svg_to_bytes=fake_svg_to_bytes))
    svg_path = tmp_path / "icon.svg"
    svg_path.write_text("<svg xmlns='http://www.w3.org/2000/svg' />", encoding="utf-8")

    result = rasterize_svg_to_png_bytes(svg_path=svg_path, output_width=96, output_height=64)

    assert result == b"PNG"
    assert calls == [
        {
            "svg_path": str(svg_path),
            "background": "rgba(0,0,0,0)",
            "width": 96,
            "height": 64,
        }
    ]
