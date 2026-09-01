from __future__ import annotations

import mimetypes
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.files.utils import get_file_category, persist_generated_file_bytes
from app.network.outbound_http import public_web_request
from app.network.policy import assert_public_http_url_allowed


MAX_WEB_IMAGE_BYTES = 10 * 1024 * 1024
MAX_WEB_IMAGE_REDIRECTS = 5
MAX_WEB_IMAGE_PIXELS = 40_000_000
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _detect_image_type(content: bytes) -> tuple[str, str]:
    """Return a safe raster MIME type and suffix from binary signatures."""

    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif", ".avif"
    raise ValueError("The downloaded resource is not a supported raster image.")


def _validate_image_dimensions(content: bytes) -> None:
    """Decode enough image data to reject corrupt or decompression-bomb assets."""

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(content)) as probe:
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > MAX_WEB_IMAGE_PIXELS:
                raise ValueError("The web image dimensions are not allowed.")
            probe.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The downloaded web image is corrupt.") from exc


def _download_public_image(db, image_url: str) -> tuple[bytes, str, str, str]:
    """Download one public image with policy checks on every redirect."""

    current_url = str(image_url or "").strip()
    if not current_url:
        raise ValueError("image_url is required")

    for _redirect_count in range(MAX_WEB_IMAGE_REDIRECTS + 1):
        assert_public_http_url_allowed(
            db,
            url=current_url,
            feature="Deep Research web image import",
        )
        response = public_web_request(
            "GET",
            current_url,
            feature="Deep Research web image import",
            allow_redirects=False,
            timeout=(10, 30),
            stream=True,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif",
                "User-Agent": "Omlorix-DeepResearch/2",
            },
        )
        try:
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("The image redirect did not include a destination.")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            declared_length = response.headers.get("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_WEB_IMAGE_BYTES:
                        raise ValueError("The web image exceeds the 10 MB limit.")
                except ValueError as exc:
                    if "exceeds" in str(exc):
                        raise
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_WEB_IMAGE_BYTES:
                    raise ValueError("The web image exceeds the 10 MB limit.")
                chunks.append(chunk)
            content = b"".join(chunks)
            media_type, suffix = _detect_image_type(content)
            _validate_image_dimensions(content)
            return content, current_url, media_type, suffix
        finally:
            response.close()
    raise ValueError("The web image exceeded the redirect limit.")


def _safe_original_filename(final_url: str, suffix: str) -> str:
    """Create a semantic, traversal-safe filename from the final image URL."""

    raw_name = Path(urlsplit(final_url).path).name
    stem = Path(raw_name).stem or "web-image"
    stem = _SAFE_NAME_RE.sub("-", stem).strip(".-")[:100] or "web-image"
    return f"{stem}{suffix}"


def import_web_image(
    db,
    *,
    user_id: str,
    image_url: str,
    source_url: str,
    attribution: str,
    alt_text: str,
    caption: str | None = None,
    license_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Securely localize an evidence-bearing image discovered by Web Search."""

    normalized_source_url = str(source_url or "").strip()
    if not normalized_source_url:
        raise ValueError("source_url is required for web-image provenance")
    assert_public_http_url_allowed(
        db,
        url=normalized_source_url,
        feature="Deep Research web image source attribution",
    )
    normalized_attribution = str(attribution or "").strip()
    normalized_alt = str(alt_text or "").strip()
    normalized_caption = str(caption or "").strip()
    if not normalized_attribution:
        raise ValueError("attribution is required")
    if not normalized_alt:
        raise ValueError("alt_text is required")
    if not normalized_caption:
        raise ValueError("caption is required")

    content, final_url, media_type, suffix = _download_public_image(db, image_url)
    original_filename = _safe_original_filename(final_url, suffix)
    file_id = str(uuid.uuid4())
    file_record = persist_generated_file_bytes(
        db,
        user_id=str(user_id),
        original_filename=original_filename,
        file_bytes=content,
        file_type=media_type,
        file_category=get_file_category(media_type),
        project_id=project_id,
        file_id=file_id,
        file_name=f"{file_id}{suffix}",
        meta={
            "original_filename": original_filename,
            "origin": "deep_research_web_image",
            "deep_research_web_image": True,
            "source_url": normalized_source_url,
            "remote_image_url": final_url,
            "attribution": normalized_attribution[:1000],
            "alt_text": normalized_alt[:1000],
            "caption": normalized_caption[:2000],
            "license_name": str(license_name or "").strip()[:500] or None,
        },
    )
    return {
        "file_id": file_record.id,
        "name": original_filename,
        "media_type": media_type,
        "size_bytes": len(content),
        "artifact_uri": f"artifact://{original_filename}",
        "source_url": normalized_source_url,
        "attribution": normalized_attribution,
        "alt_text": normalized_alt,
        "caption": normalized_caption,
        "license_name": str(license_name or "").strip() or None,
        "instruction": (
            "Embed artifact_uri exactly as a Markdown image. Immediately below it, "
            "add the source_url and attribution; state the license only when verified."
        ),
    }
