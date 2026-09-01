"""Validate model-authored visualization fragments before they reach the browser.

The browser still treats every fragment as untrusted and runs it in an opaque
origin.  These checks are a second, earlier boundary: they keep malformed full
documents, accidental network clients, and oversized payloads out of durable
chat history while returning repairable errors to the calling model.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import re
from typing import Any


VISUALIZATION_MAX_BYTES = 1024 * 1024
VISUALIZATION_MAX_TITLE_LENGTH = 120
VISUALIZATION_RUNTIME_VERSION = 1
VISUALIZATION_MODES = {"normal", "wide"}

_DOCUMENT_TAG_PATTERN = re.compile(r"<\s*(?:!doctype|html|head|body)\b", re.IGNORECASE)
_DIRECT_NETWORK_PATTERN = re.compile(
    # Match executable calls/constructors, not ordinary prose such as a chart
    # label that happens to contain the word "fetch".
    r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(",
    re.IGNORECASE,
)
_UNSUPPORTED_HOST_PATTERN = re.compile(r"\bwindow\s*\.\s*openai\b", re.IGNORECASE)
_CURRENT_SCRIPT_PATTERN = re.compile(r"\bdocument\s*\.\s*currentScript\b", re.IGNORECASE)
_CSS_EXTERNAL_PATTERN = re.compile(r"(?:url\s*\(|@import\s+)[^;}]*(?:https?:)?//", re.IGNORECASE)


class VisualizationValidationError(ValueError):
    """A stable, model-repairable visualization validation error."""


@dataclass(frozen=True)
class VisualizationFragmentInfo:
    """Normalized metadata derived from one valid visualization fragment."""

    root_id: str
    source_hash: str
    size_bytes: int
    warnings: tuple[str, ...]


class _FragmentInspector(HTMLParser):
    """Collect structural facts without attempting to sanitize authored HTML."""

    _FORBIDDEN_TAGS = {"base", "embed", "frame", "frameset", "iframe", "link", "meta", "object"}
    _RESOURCE_ATTRIBUTES = {"action", "formaction", "href", "poster", "src", "srcset"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.first_content_id = ""
        self.errors: list[str] = []
        self.has_heading = False
        self.has_accessible_graphic = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attributes = {str(name or "").lower(): str(value or "") for name, value in attrs}
        if normalized_tag in self._FORBIDDEN_TAGS:
            self.errors.append(f"<{normalized_tag}> is not allowed in a visualization fragment")
        if normalized_tag == "meta" and attributes.get("http-equiv", "").lower() == "content-security-policy":
            self.errors.append("visualizations cannot replace the host Content Security Policy")

        element_id = attributes.get("id", "").strip()
        if element_id:
            self.ids.append(element_id)
            if not self.first_content_id and normalized_tag not in {"script", "style"}:
                self.first_content_id = element_id

        if normalized_tag in {"h1", "h2", "h3"}:
            self.has_heading = True
        if normalized_tag in {"canvas", "svg"} and (
            attributes.get("aria-label")
            or attributes.get("aria-labelledby")
            or attributes.get("role") == "img"
        ):
            self.has_accessible_graphic = True

        for name, value in attributes.items():
            if name.startswith("on"):
                self.errors.append("inline event-handler attributes are not allowed; use addEventListener")
            if name == "srcdoc":
                self.errors.append("srcdoc is not allowed in visualization fragments")
            if name in self._RESOURCE_ATTRIBUTES:
                normalized_value = value.strip().lower()
                if normalized_value.startswith("javascript:"):
                    self.errors.append("javascript: URLs are not allowed")
                elif normalized_value and not normalized_value.startswith(("#", "data:", "blob:")):
                    self.errors.append(
                        "direct external resources are not allowed; embed data or use window.omlorix.visualization.requestExternalData"
                    )
            if normalized_tag == "script" and name == "src" and value.strip():
                self.errors.append("external script tags are not allowed; D3, TopoJSON, and Lucide are bundled")


def _normalize_capabilities(value: Any) -> dict[str, bool]:
    """Return every explicit visualization capability with safe defaults."""

    raw = value if isinstance(value, dict) else {}
    return {
        "scripts": bool(raw.get("scripts", True)),
        "external_data": bool(raw.get("external_data", False)),
        "chat_followup": bool(raw.get("chat_followup", False)),
        "download": bool(raw.get("download", False)),
    }


def validate_visualization_fragment(content: Any) -> tuple[str, VisualizationFragmentInfo]:
    """Validate and normalize one durable HTML fragment.

    The function intentionally rejects network primitives rather than trying to
    rewrite them.  The generated code receives a narrow host bridge for data and
    follow-up actions, which keeps the iframe CSP at ``connect-src 'none'``.
    """

    fragment = str(content or "").strip()
    if not fragment:
        raise VisualizationValidationError("content is required")
    size_bytes = len(fragment.encode("utf-8"))
    if size_bytes > VISUALIZATION_MAX_BYTES:
        raise VisualizationValidationError("visualization content must be 1 MB or smaller")
    if _DOCUMENT_TAG_PATTERN.search(fragment):
        raise VisualizationValidationError("content must be an HTML fragment without doctype, html, head, or body tags")
    if _DIRECT_NETWORK_PATTERN.search(fragment):
        raise VisualizationValidationError(
            "direct fetch, XMLHttpRequest, WebSocket, and EventSource calls are not allowed; use the Omlorix visualization bridge"
        )
    if _UNSUPPORTED_HOST_PATTERN.search(fragment):
        raise VisualizationValidationError("use window.omlorix.visualization instead of window.openai")
    if _CURRENT_SCRIPT_PATTERN.search(fragment):
        raise VisualizationValidationError("select the fragment root by its stable ID instead of document.currentScript")
    if _CSS_EXTERNAL_PATTERN.search(fragment):
        raise VisualizationValidationError("external CSS resources are not allowed in visualization fragments")

    inspector = _FragmentInspector()
    try:
        inspector.feed(fragment)
        inspector.close()
    except Exception as exc:
        raise VisualizationValidationError("content is not valid HTML") from exc
    if inspector.errors:
        raise VisualizationValidationError(inspector.errors[0])
    if not inspector.first_content_id:
        raise VisualizationValidationError("the visualization root element must have a stable id")
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for element_id in inspector.ids:
        if element_id in seen_ids:
            duplicate_ids.add(element_id)
        seen_ids.add(element_id)
    if duplicate_ids:
        raise VisualizationValidationError(f"duplicate element id is not allowed: {sorted(duplicate_ids)[0]}")

    warnings: list[str] = []
    if not inspector.has_heading:
        warnings.append("Add a concise visible h1, h2, or h3 when the visualization is a self-contained chart.")
    if re.search(r"<\s*(?:svg|canvas)\b", fragment, re.IGNORECASE) and not inspector.has_accessible_graphic:
        warnings.append("Give each SVG or canvas an accessible name or description.")

    return fragment, VisualizationFragmentInfo(
        root_id=inspector.first_content_id,
        source_hash=hashlib.sha256(fragment.encode("utf-8")).hexdigest(),
        size_bytes=size_bytes,
        warnings=tuple(warnings),
    )


def create_visualization_payload(
    *,
    title: Any,
    content: Any,
    mode: Any = "normal",
    capabilities: Any = None,
) -> dict[str, Any]:
    """Create the canonical provider-neutral visualization widget payload."""

    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise VisualizationValidationError("title is required")
    if len(normalized_title) > VISUALIZATION_MAX_TITLE_LENGTH:
        raise VisualizationValidationError(f"title must be {VISUALIZATION_MAX_TITLE_LENGTH} characters or fewer")
    normalized_mode = str(mode or "normal").strip().lower()
    if normalized_mode not in VISUALIZATION_MODES:
        raise VisualizationValidationError("mode must be one of: normal, wide")

    fragment, info = validate_visualization_fragment(content)
    normalized_capabilities = _normalize_capabilities(capabilities)
    metadata = {
        "title": normalized_title,
        "mode": normalized_mode,
        "root_id": info.root_id,
        "runtime_version": VISUALIZATION_RUNTIME_VERSION,
        "source_hash": info.source_hash,
        "size_bytes": info.size_bytes,
        "capabilities": normalized_capabilities,
        "warnings": list(info.warnings),
    }
    return {
        "type": "visualization",
        "html": fragment,
        "render_mode": "visualization",
        # Authored scripts remain disabled on first render.  This flag records
        # that the viewer may opt into the interactive enhancement.
        "allow_scripts": normalized_capabilities["scripts"],
        "model_context": {
            "status": "created",
            "title": normalized_title,
            "mode": normalized_mode,
            "root_id": info.root_id,
            "warnings": list(info.warnings),
        },
        "visualization": metadata,
    }
