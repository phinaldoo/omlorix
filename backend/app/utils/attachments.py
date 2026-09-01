from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote


def _ascii_attachment_fallback(filename: str, fallback: str) -> str:
    """Return a header-safe ASCII filename for the legacy filename parameter."""
    normalized = unicodedata.normalize("NFKD", str(filename or ""))
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[\r\n\t]+", " ", ascii_name)
    ascii_name = "".join("-" if char in '/\\:*?"<>|' else char for char in ascii_name)
    ascii_name = re.sub(r"\s+", " ", ascii_name).strip(" .")
    ascii_name = re.sub(r"\s+\.", ".", ascii_name)
    return ascii_name[:255] or fallback


def attachment_headers(filename: str, *, fallback: str = "download") -> dict[str, str]:
    """Build RFC 6266/RFC 5987 attachment headers without raw Unicode."""
    clean_fallback = _ascii_attachment_fallback(fallback, "download")
    raw_filename = str(filename or clean_fallback).replace("\x00", "").strip() or clean_fallback
    encoded = quote(raw_filename, safe="")
    ascii_fallback = _ascii_attachment_fallback(raw_filename, clean_fallback)
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{encoded}"
        )
    }
