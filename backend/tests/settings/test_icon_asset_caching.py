from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace


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

from fastapi import UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw
from starlette.responses import RedirectResponse

from app.settings import utils as settings_utils


def _point_icon_paths_at_tmpdir(monkeypatch, tmp_path):
    """Make the filesystem-backed branding helpers operate in an isolated directory."""
    logo_dir = tmp_path / "logo"
    monkeypatch.setattr(settings_utils, "_LOGO_DIR", logo_dir)
    monkeypatch.setattr(settings_utils, "_FAVICON_SVG_PATH", logo_dir / "favicon.svg")
    monkeypatch.setattr(settings_utils, "_ICON_PNG_PATH", logo_dir / "icon.png")
    return logo_dir


def _png_upload(width: int, height: int) -> UploadFile:
    """Create an in-memory PNG upload with predictable dimensions."""
    buffer = BytesIO()
    Image.new("RGBA", (width, height), (24, 99, 180, 255)).save(buffer, format="PNG")
    buffer.seek(0)
    return UploadFile(filename="icon.png", file=buffer)


def _edge_touching_mark_upload(width: int = 360, height: int = 360) -> UploadFile:
    """Create an icon whose dark mark visibly reaches the source's vertical edges."""
    buffer = BytesIO()
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((width // 3, 0, (width * 2) // 3, height - 1), fill=(0, 0, 0))
    image.save(buffer, format="PNG")
    image.close()
    buffer.seek(0)
    return UploadFile(filename="edge-touching-icon.png", file=buffer)


def _dark_content_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the bounds of pixels dark enough to represent foreground artwork."""
    mask = image.convert("L").point(lambda value: 255 if value < 128 else 0)
    return mask.getbbox()


def _svg_upload(payload: str) -> UploadFile:
    """Create an in-memory SVG upload for branding regression tests."""
    buffer = BytesIO(payload.encode("utf-8"))
    return UploadFile(filename="icon.svg", file=buffer)


def _logo_svg_upload(payload: str) -> UploadFile:
    """Create an in-memory SVG logo upload for branding regression tests."""
    buffer = BytesIO(payload.encode("utf-8"))
    return UploadFile(filename="logo.svg", file=buffer)


def test_checked_in_default_icon_derivatives_keep_transparent_corners():
    """Default non-Apple PNG assets must not reintroduce an opaque canvas."""
    default_logo_dir = Path(__file__).resolve().parents[2] / "app" / "data" / "logo"

    for filename in (
        "favicon-16x16.png",
        "favicon-32x32.png",
        "favicon-512x512.png",
        "icon.png",
    ):
        with Image.open(default_logo_dir / filename) as image:
            assert image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0


def test_icon_metadata_and_manifest_use_versioned_urls(monkeypatch, tmp_path):
    """Branding metadata and the web manifest should point at cache-busted icon URLs."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)
    logo_dir.mkdir(parents=True)
    icon_path = logo_dir / "favicon-32x32.png"
    icon_path.write_bytes(b"not-a-real-png")

    overview = settings_utils.get_branding_assets_overview()
    manifest = settings_utils.get_site_manifest(SimpleNamespace()).body.decode("utf-8")

    assert overview["icon"]["version"] != "0"
    assert overview["icon"]["url"].startswith("/api/v1/settings/icon/get?v=")
    assert overview["icon"]["sizes"]["32"].startswith("/api/v1/settings/icon/get?size=32&v=")
    assert "/api/v1/settings/icon/get?size=32&v=" in manifest


def test_unversioned_icon_request_redirects_to_versioned_asset(monkeypatch, tmp_path):
    """Stable icon URLs should redirect to immutable versioned URLs when an icon exists."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)
    logo_dir.mkdir(parents=True)
    (logo_dir / "favicon-32x32.png").write_bytes(b"not-a-real-png")

    response = settings_utils.get_icon(size=32)

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"].startswith("/api/v1/settings/icon/get?size=32&v=")
    assert response.headers["cache-control"] == "public, max-age=300"


def test_versioned_icon_request_uses_immutable_cache_headers(monkeypatch, tmp_path):
    """Versioned icon URLs can be cached for a long time because upload changes the version."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)
    logo_dir.mkdir(parents=True)
    (logo_dir / "favicon-32x32.png").write_bytes(b"not-a-real-png")

    response = settings_utils.get_icon(size=32, v="123")

    assert isinstance(response, FileResponse)
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_lazy_svg_touch_icon_generation_applies_ios_safe_area(monkeypatch, tmp_path):
    """A missing touch fallback generated during a request must still be safely inset."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)
    logo_dir.mkdir(parents=True)
    settings_utils._FAVICON_SVG_PATH.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'/>",
        encoding="utf-8",
    )

    def fake_rasterize_svg_to_png_bytes(**kwargs):
        """Return a white icon with a dark mark that reaches both vertical edges."""
        size = kwargs["output_width"]
        buffer = BytesIO()
        rendered = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(rendered)
        draw.rectangle((size // 3, 0, (size * 2) // 3, size - 1), fill=(0, 0, 0))
        rendered.save(buffer, format="PNG")
        rendered.close()
        return buffer.getvalue()

    monkeypatch.setattr(settings_utils, "rasterize_svg_to_png_bytes", fake_rasterize_svg_to_png_bytes)

    response = settings_utils.get_icon(size=settings_utils._APPLE_TOUCH_ICON_SIZE, v="123")

    assert isinstance(response, FileResponse)
    with Image.open(logo_dir / "apple-touch-icon.png") as touch_icon:
        touch_bbox = _dark_content_bbox(touch_icon)
        assert touch_bbox is not None
        assert touch_bbox[1] > 0
        assert touch_bbox[3] < touch_icon.height


def test_upload_icon_caps_generic_png_size(monkeypatch, tmp_path):
    """The legacy unsized PNG fallback should not preserve very large source dimensions."""
    _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)

    settings_utils.upload_icon(_png_upload(1400, 900))

    with Image.open(settings_utils._ICON_PNG_PATH) as saved_icon:
        assert saved_icon.width <= settings_utils._MAX_ICON_PNG_DIMENSION
        assert saved_icon.height <= settings_utils._MAX_ICON_PNG_DIMENSION


def test_upload_icon_adds_ios_safe_area_without_padding_other_variants(monkeypatch, tmp_path):
    """Only the Apple touch derivative should inset artwork that touches source edges."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)

    settings_utils.upload_icon(_edge_touching_mark_upload())

    expected_inset = round(
        settings_utils._APPLE_TOUCH_ICON_SIZE
        * (1 - settings_utils._APPLE_TOUCH_ICON_CONTENT_SCALE)
        / 2
    )
    with Image.open(logo_dir / "apple-touch-icon.png") as touch_icon:
        assert touch_icon.size == (
            settings_utils._APPLE_TOUCH_ICON_SIZE,
            settings_utils._APPLE_TOUCH_ICON_SIZE,
        )
        assert touch_icon.mode == "RGB"
        touch_bbox = _dark_content_bbox(touch_icon)
        assert touch_bbox is not None
        assert touch_bbox[1] >= expected_inset
        assert touch_icon.height - touch_bbox[3] >= expected_inset

    with Image.open(logo_dir / "favicon-512x512.png") as manifest_icon:
        manifest_bbox = _dark_content_bbox(manifest_icon)
        assert manifest_bbox is not None
        assert manifest_bbox[1] == 0
        assert manifest_bbox[3] == manifest_icon.height


def test_upload_icon_preserves_safe_complex_svg_features(monkeypatch, tmp_path):
    """Complex but safe SVG icons should keep their styling, clip paths, and embedded raster data."""
    _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)

    svg_payload = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     viewBox="0 0 4 4"
     style="fill-rule:evenodd;clip-rule:evenodd;">
  <defs>
    <clipPath id="clipA">
      <rect x="0" y="0" width="4" height="4" />
    </clipPath>
    <image id="imgA"
           width="1"
           height="1"
           xlink:href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgwJ/l7sRKwAAAABJRU5ErkJggg==" />
  </defs>
  <rect x="0" y="0" width="4" height="4" style="fill:rgb(255,0,0);" clip-path="url(#clipA)" />
  <use xlink:href="#imgA" x="1" y="1" width="1" height="1" />
</svg>"""

    result = settings_utils.upload_icon(_svg_upload(svg_payload))
    saved_svg = settings_utils._FAVICON_SVG_PATH.read_text(encoding="utf-8")

    assert result["status"] == "success"
    ET.fromstring(saved_svg)
    assert saved_svg.count('xmlns="http://www.w3.org/2000/svg"') == 1
    assert 'style="fill-rule:evenodd;clip-rule:evenodd"' in saved_svg
    assert '<clipPath id="clipA">' in saved_svg
    assert '<image id="imgA"' in saved_svg
    assert 'data:image/png;base64,' in saved_svg
    assert '<use xlink:href="#imgA"' in saved_svg


def test_upload_logo_stores_parseable_svg_without_duplicate_namespace(monkeypatch, tmp_path):
    """SVG logos should be valid XML after sanitization so browser previews can render them."""
    logo_dir = _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)

    svg_payload = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" style="display:block">
  <rect x="0" y="0" width="12" height="12" fill="currentColor" />
</svg>"""

    result = settings_utils.upload_logo(_logo_svg_upload(svg_payload), theme="dark")
    saved_logo = logo_dir / "logo_dark.svg"
    saved_svg = saved_logo.read_text(encoding="utf-8")

    assert result == {"status": "success", "theme": "dark"}
    ET.fromstring(saved_svg)
    assert saved_svg.count('xmlns="http://www.w3.org/2000/svg"') == 1
    assert '<rect x="0" y="0" width="12" height="12" fill="currentColor" />' in saved_svg


def test_upload_icon_generates_png_fallbacks_from_svg_renderer(monkeypatch, tmp_path):
    """SVG icon uploads should persist the PNG fallback set when the renderer succeeds."""
    _point_icon_paths_at_tmpdir(monkeypatch, tmp_path)

    def fake_rasterize_svg_to_png_bytes(**kwargs):
        """Return deterministic edge-touching artwork at the requested dimensions."""
        assert kwargs["output_width"] == kwargs["output_height"]
        size = kwargs["output_width"]
        png_buffer = BytesIO()
        rendered = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(rendered)
        draw.rectangle((size // 3, 0, (size * 2) // 3, size - 1), fill=(0, 0, 0))
        rendered.save(png_buffer, format="PNG")
        rendered.close()
        return png_buffer.getvalue()

    monkeypatch.setattr(settings_utils, "rasterize_svg_to_png_bytes", fake_rasterize_svg_to_png_bytes)

    result = settings_utils.upload_icon(
        _svg_upload("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><rect width='1' height='1'/></svg>")
    )

    assert result == {"status": "success", "format": "svg+png"}
    assert settings_utils._FAVICON_SVG_PATH.exists()
    assert (settings_utils._LOGO_DIR / "favicon-16x16.png").exists()
    assert (settings_utils._LOGO_DIR / "favicon-32x32.png").exists()
    assert (settings_utils._LOGO_DIR / "apple-touch-icon.png").exists()
    assert (settings_utils._LOGO_DIR / "favicon-512x512.png").exists()
    assert settings_utils._ICON_PNG_PATH.exists()

    with Image.open(settings_utils._LOGO_DIR / "apple-touch-icon.png") as touch_icon:
        touch_bbox = _dark_content_bbox(touch_icon)
        assert touch_bbox is not None
        assert touch_bbox[1] > 0
        assert touch_bbox[3] < touch_icon.height
