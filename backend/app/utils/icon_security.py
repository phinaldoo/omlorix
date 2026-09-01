import html
import json
import re
from typing import Any
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from fastapi import HTTPException, status


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SVG_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z0-9:_-]+)")
_SVG_BLOCKED_ATTR_RE = re.compile(r"\bon\w+\s*=", re.IGNORECASE)
_SVG_BLOCKED_URI_RE = re.compile(r"javascript:", re.IGNORECASE)
_SVG_BLOCKED_SCRIPT_RE = re.compile(r"<\s*script\b", re.IGNORECASE)
_IMAGE_DATA_URL_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$",
    re.IGNORECASE,
)
_IMAGE_URL_RE = re.compile(
    r"^(?:https?://|/|\./|\.\./).+\.(?:png|jpe?g|gif|webp|svg)(?:[?#].*)?$",
    re.IGNORECASE,
)
_ICON_PRESET_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ALLOWED_SVG_TAGS = {
    "svg",
    "g",
    "path",
    "polyline",
    "polygon",
    "line",
    "rect",
    "circle",
    "ellipse",
}
_ALLOWED_SVG_ATTRIBUTES = {
    "aria-hidden",
    "class",
    "clip-rule",
    "cx",
    "cy",
    "d",
    "fill",
    "fill-opacity",
    "fill-rule",
    "height",
    "id",
    "points",
    "preserveAspectRatio",
    "r",
    "rx",
    "ry",
    "stroke",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "transform",
    "version",
    "viewBox",
    "width",
    "x",
    "x1",
    "x2",
    "xmlns",
    "y",
    "y1",
    "y2",
}
_MAX_INLINE_SVG_LENGTH = 50_000


def _decode_html_entities(value: str) -> str:
    decoded = value
    for _ in range(3):
        next_value = html.unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _is_safe_svg_path_markup(value: str) -> bool:
    """Validate a self-contained SVG icon without active content or resources."""

    if len(value.encode("utf-8")) > _MAX_INLINE_SVG_LENGTH:
        return False
    normalized_value = _decode_html_entities(value)
    if _SVG_BLOCKED_SCRIPT_RE.search(normalized_value):
        return False
    if _SVG_BLOCKED_ATTR_RE.search(normalized_value):
        return False
    if _SVG_BLOCKED_URI_RE.search(normalized_value):
        return False

    for match in _SVG_TAG_RE.finditer(normalized_value):
        tag = match.group(1).lower()
        if tag not in _ALLOWED_SVG_TAGS:
            return False

    try:
        root = DefusedET.fromstring(normalized_value)
    except (DefusedXmlException, ParseError, ValueError, TypeError):
        return False

    def local_name(name: str) -> str:
        """Resolve the SVG namespace and reject every other XML namespace."""

        if not name.startswith("{"):
            return name
        namespace, _, local = name[1:].partition("}")
        return local if namespace == "http://www.w3.org/2000/svg" else ""

    if local_name(str(root.tag)).lower() != "svg":
        return False
    for node in root.iter():
        if local_name(str(node.tag)).lower() not in _ALLOWED_SVG_TAGS:
            return False
        for attribute_name, attribute_value in node.attrib.items():
            if local_name(str(attribute_name)) not in _ALLOWED_SVG_ATTRIBUTES:
                return False
            lowered_value = str(attribute_value).strip().lower()
            if (
                "url(" in lowered_value
                or "javascript:" in lowered_value
                or "data:" in lowered_value
            ):
                return False
    return True


def sanitize_hex_color(value: Any, *, fallback: str = "#6366f1") -> str:
    if isinstance(fallback, str) and fallback.strip() == "":
        normalized_fallback = ""
    elif isinstance(fallback, str) and _HEX_COLOR_RE.match(fallback.strip()):
        normalized_fallback = fallback.strip()
    else:
        normalized_fallback = "#6366f1"

    if not isinstance(value, str):
        return normalized_fallback
    candidate = value.strip()
    if not candidate:
        return normalized_fallback
    return candidate if _HEX_COLOR_RE.match(candidate) else normalized_fallback


def sanitize_icon_input(value: Any, *, fallback: str = "") -> str:
    if isinstance(value, str):
        candidate = value.strip()
    elif value is None:
        candidate = ""
    else:
        candidate = str(value).strip()

    if not candidate:
        return fallback

    if candidate.startswith("{"):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return fallback
        if parsed is None:
            return fallback
        if not isinstance(parsed, dict):
            return fallback

        safe_payload: dict[str, str] = {}
        image_value = parsed.get("image") or parsed.get("src")
        if isinstance(image_value, str) and image_value.strip():
            image_candidate = image_value.strip()
            if _IMAGE_DATA_URL_RE.match(image_candidate) or _IMAGE_URL_RE.match(image_candidate):
                safe_payload["image"] = image_candidate

        preset_value = parsed.get("preset") or parsed.get("icon")
        if isinstance(preset_value, str) and preset_value.strip():
            preset_candidate = preset_value.strip()
            if _ICON_PRESET_RE.match(preset_candidate):
                safe_payload["preset"] = preset_candidate

        color_value = parsed.get("color")
        if isinstance(color_value, str) and _HEX_COLOR_RE.match(color_value.strip()):
            safe_payload["color"] = color_value.strip()

        if not any(key in safe_payload for key in ("image", "preset")):
            return fallback

        return json.dumps(safe_payload, separators=(",", ":"))

    if "<" in candidate or ">" in candidate:
        return candidate if _is_safe_svg_path_markup(candidate) else fallback

    if _IMAGE_DATA_URL_RE.match(candidate) or _IMAGE_URL_RE.match(candidate):
        return candidate

    return candidate if _ICON_PRESET_RE.match(candidate) else fallback


def require_safe_icon_input(value: Any, *, fallback: str = "") -> str:
    sanitized = sanitize_icon_input(value, fallback=fallback)
    if sanitized == fallback and str(value or "").strip() and str(value or "").strip() != fallback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid icon format",
        )
    return sanitized
