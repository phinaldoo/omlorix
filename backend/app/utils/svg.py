"""Helpers for rasterizing SVG assets into PNG bytes.

This module keeps the SVG rendering backend in one place so settings uploads,
favicon generation, and export code all use the same implementation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_XML_ENCODING_RE = re.compile(br"<\?xml\b[^>]*\bencoding\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _decode_svg_bytes(svg_bytes: bytes) -> str:
    """Decode SVG bytes using the XML declaration when one is available.

    XML declarations are ASCII-compatible, so checking the first chunk of bytes
    is enough to find a declared encoding before handing the SVG string to the
    renderer. If the declared codec is missing, unknown, or wrong for the actual
    bytes, fall back to UTF-8 and finally Latin-1 so uploads do not fail during
    string conversion.
    """

    encodings: list[str] = []
    match = _XML_ENCODING_RE.search(svg_bytes[:512])
    if match is not None:
        try:
            encodings.append(match.group(1).decode("ascii"))
        except UnicodeDecodeError:
            pass

    encodings.extend(["utf-8", "latin-1"])

    tried: set[str] = set()
    for encoding in encodings:
        normalized_encoding = encoding.lower()
        if normalized_encoding in tried:
            continue
        tried.add(normalized_encoding)

        try:
            return svg_bytes.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return svg_bytes.decode("latin-1")


def _coerce_png_bytes(payload: Any) -> bytes:
    """Normalize renderer output into raw PNG bytes.

    The `resvg_py` bindings are expected to return a bytes-like object, but we
    keep the coercion defensive so minor upstream return-type differences do
    not break icon uploads.
    """

    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray | memoryview):
        return bytes(payload)
    if isinstance(payload, list | tuple):
        return bytes(payload)
    raise TypeError(f"Unsupported SVG renderer payload type: {type(payload)!r}")


def rasterize_svg_to_png_bytes(
    *,
    svg_bytes: bytes | None = None,
    svg_path: str | Path | None = None,
    output_width: int | None = None,
    output_height: int | None = None,
) -> bytes:
    """Render SVG input into PNG bytes using the lightweight `resvg_py` wheel.

    Parameters mirror the existing CairoSVG usage so the call sites can request
    explicit favicon dimensions without knowing which renderer is behind them.
    Either `svg_bytes` or `svg_path` must be provided.
    """

    if svg_bytes is None and svg_path is None:
        raise ValueError("Either svg_bytes or svg_path must be provided.")

    import resvg_py

    render_kwargs: dict[str, Any] = {
        # Branding SVGs commonly rely on the absence of a canvas fill. Make the
        # transparent rendering contract explicit instead of depending on the
        # renderer's default, so generated favicon PNGs cannot silently acquire
        # an opaque black (or other implementation-defined) background after a
        # renderer upgrade.
        "background": "rgba(0,0,0,0)",
        "width": output_width,
        "height": output_height,
    }

    if svg_bytes is not None:
        render_kwargs["svg_string"] = _decode_svg_bytes(svg_bytes)
    else:
        render_kwargs["svg_path"] = str(Path(svg_path))

    png_payload = resvg_py.svg_to_bytes(**render_kwargs)
    return _coerce_png_bytes(png_payload)
