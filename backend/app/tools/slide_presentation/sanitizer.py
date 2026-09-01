"""Sanitization helpers for generated slide presentation HTML."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from typing import Any

from bs4 import BeautifulSoup, Comment
from tinycss2 import parse_declaration_list, parse_stylesheet, serialize

from app.files.models import get_file
from app.files.utils import materialize_file_record


_CSP_CONTENT = (
    "default-src 'none'; "
    "img-src data:; "
    "style-src 'unsafe-inline'; "
    "font-src data:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "script-src 'none'"
)

_DANGEROUS_TAGS = {
    "applet",
    "base",
    "button",
    "embed",
    "form",
    "frame",
    "frameset",
    "iframe",
    "input",
    "link",
    "meta",
    "noscript",
    "object",
    "script",
    "select",
    "source",
    "textarea",
    "track",
    "video",
    "audio",
}

_URL_ATTRS = {
    "action",
    "background",
    "data",
    "formaction",
    "href",
    "poster",
    "src",
    "xlink:href",
    "xml:base",
}

_ALWAYS_DROP_ATTRS = {
    "autofocus",
    "contenteditable",
    "form",
    "ping",
    "srcdoc",
}

_ALLOWED_DATA_IMAGE_PREFIXES = (
    "data:image/gif;",
    "data:image/jpeg;",
    "data:image/jpg;",
    "data:image/png;",
    "data:image/webp;",
)

_OMLORIX_FILE_PREFIX = "omlorix-file://"
_OMLORIX_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _split_css_selector_tokens(tokens: list[Any]) -> list[list[Any]]:
    """Split a selector list at top-level commas retained by tinycss2."""
    selectors: list[list[Any]] = []
    current: list[Any] = []
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            selectors.append(current)
            current = []
        else:
            current.append(token)
    selectors.append(current)
    return selectors


def _css_selector_specificity(tokens: list[Any]) -> tuple[int, int, int]:
    """Calculate CSS selector specificity for presentation cascade checks."""
    ids = classes = elements = 0
    index = 0
    expect_type = True
    while index < len(tokens):
        token = tokens[index]
        token_type = token.type
        if token_type == "whitespace":
            expect_type = True
            index += 1
            continue
        if token_type == "hash" and getattr(token, "is_identifier", False):
            ids += 1
            expect_type = False
        elif token_type == "[] block":
            classes += 1
            expect_type = False
        elif token_type == "literal" and token.value == ".":
            if index + 1 < len(tokens) and tokens[index + 1].type == "ident":
                classes += 1
                index += 1
            expect_type = False
        elif token_type == "literal" and token.value == ":":
            # A double colon introduces a pseudo-element. A single colon is a
            # pseudo-class, except :where(), whose specificity is always zero.
            if (
                index + 1 < len(tokens)
                and tokens[index + 1].type == "literal"
                and tokens[index + 1].value == ":"
            ):
                elements += 1
                index += 2
                if index < len(tokens) and tokens[index].type in {"ident", "function"}:
                    pass
                else:
                    index -= 1
                expect_type = False
            elif index + 1 < len(tokens):
                pseudo = tokens[index + 1]
                if pseudo.type == "function":
                    name = str(pseudo.name or "").lower()
                    if name in {"is", "not", "has"}:
                        options = _split_css_selector_tokens(list(pseudo.arguments))
                        option_specificities = [
                            _css_selector_specificity(option) for option in options
                        ]
                        if option_specificities:
                            option_ids, option_classes, option_elements = max(
                                option_specificities
                            )
                            ids += option_ids
                            classes += option_classes
                            elements += option_elements
                    elif name != "where":
                        classes += 1
                    index += 1
                elif pseudo.type == "ident":
                    classes += 1
                    index += 1
                expect_type = False
        elif token_type == "ident" and expect_type:
            elements += 1
            expect_type = False
        elif token_type == "literal" and token.value in {">", "+", "~", "||"}:
            expect_type = True
        index += 1
    return ids, classes, elements


def _effective_slide_declarations(
    css: str,
    *,
    soup: BeautifulSoup,
    slides: list[Any],
) -> list[dict[str, str]]:
    """Resolve the actual author cascade independently for every slide.

    A selector mentioning ``.slide`` is not sufficient evidence that it
    applies to a particular canvas. Resolve selectors against the sanitized
    document so attribute qualifiers, ancestors, and conditional pseudo
    classes cannot accidentally satisfy the fixed-canvas contract.
    """
    winners: list[
        dict[str, tuple[tuple[bool, bool, tuple[int, int, int], int], str]]
    ] = [{} for _ in slides]
    slide_indexes = {id(slide): index for index, slide in enumerate(slides)}
    source_order = 0
    for rule in parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        # Conditional/nested at-rules do not provide an unconditional canvas
        # contract and therefore must not participate in validation.
        if rule.type != "qualified-rule":
            continue
        matching_specificities: dict[int, tuple[int, int, int]] = {}
        for selector_tokens in _split_css_selector_tokens(list(rule.prelude)):
            selector = serialize(selector_tokens).strip()
            if not selector:
                continue
            try:
                matching_elements = soup.select(selector)
            except Exception:
                # Unsupported or dynamic selectors such as :hover do not
                # provide an unconditional render-time canvas contract.
                continue
            specificity = _css_selector_specificity(selector_tokens)
            for element in matching_elements:
                slide_index = slide_indexes.get(id(element))
                if slide_index is None:
                    continue
                current = matching_specificities.get(slide_index)
                if current is None or specificity > current:
                    matching_specificities[slide_index] = specificity
        if not matching_specificities:
            continue
        declarations = parse_declaration_list(
            rule.content,
            skip_comments=True,
            skip_whitespace=True,
        )
        for declaration in declarations:
            if declaration.type != "declaration":
                continue
            source_order += 1
            name = str(declaration.lower_name or "").strip()
            value = serialize(declaration.value).strip().lower()
            for slide_index, specificity in matching_specificities.items():
                cascade_key = (
                    bool(declaration.important),
                    False,
                    specificity,
                    source_order,
                )
                current = winners[slide_index].get(name)
                if current is None or cascade_key >= current[0]:
                    winners[slide_index][name] = (cascade_key, value)

    # Inline declarations participate in the same author cascade. They beat
    # normal stylesheet rules, while stylesheet !important still beats a
    # normal inline declaration.
    for slide_index, slide in enumerate(slides):
        for declaration in parse_declaration_list(
            str(slide.get("style") or ""),
            skip_comments=True,
            skip_whitespace=True,
        ):
            if declaration.type != "declaration":
                continue
            source_order += 1
            name = str(declaration.lower_name or "").strip()
            value = serialize(declaration.value).strip().lower()
            cascade_key = (
                bool(declaration.important),
                True,
                (0, 0, 0),
                source_order,
            )
            current = winners[slide_index].get(name)
            if current is None or cascade_key >= current[0]:
                winners[slide_index][name] = (cascade_key, value)

    return [
        {name: winner[1] for name, winner in slide_winners.items()}
        for slide_winners in winners
    ]


_MAX_PRESENTATION_ASSETS = 20
_MAX_PRESENTATION_ASSET_BYTES = 20 * 1024 * 1024
_MAX_PRESENTATION_TOTAL_ASSET_BYTES = 40 * 1024 * 1024
# Keep render, review, preview, PDF, and archive work bounded for every deck.
# The generator prompt also advertises this limit, but the backend is the
# authoritative enforcement boundary for generated and editor-supplied HTML.
MAX_PRESENTATION_SLIDES = 50
MAX_PRESENTATION_HTML_BYTES = 64 * 1024 * 1024
_EMBEDDABLE_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

_CSS_IMPORT_RE = re.compile(r"@import\b[^;]*(?:;|$)", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6}\s?|.)", re.DOTALL)
_CSS_FETCH_FUNCTION_RE = re.compile(
    r"(?:url|image|image-set|-webkit-image-set|cross-fade)\s*\([^;{}]*\)",
    re.IGNORECASE,
)
_CSS_UNSAFE_RE = re.compile(r"expression\s*\(|javascript\s*:|vbscript\s*:|behavior\s*:", re.IGNORECASE)
_CSS_REMOTE_LITERAL_RE = re.compile(r"https?://|//|data:", re.IGNORECASE)


def sanitize_slide_presentation_title(value: str, *, fallback: str = "") -> str:
    """Return a display title that is also safe to use as a file basename."""

    title = re.sub(r"[^\w\-. ]+", "", str(value or "")).strip()[:120]
    return title or fallback


def _decode_css_escapes(css: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escaped = match.group(1)
        hex_part = escaped.strip()
        if re.fullmatch(r"[0-9a-fA-F]{1,6}", hex_part):
            try:
                return chr(int(hex_part, 16))
            except ValueError:
                return ""
        return escaped[:1]

    return _CSS_ESCAPE_RE.sub(replace, css)


def _clean_css(css: str) -> str:
    """Remove CSS constructs that can load remote resources or execute code."""
    cleaned = _CSS_COMMENT_RE.sub("", str(css or ""))
    cleaned = _decode_css_escapes(cleaned)
    cleaned = _CSS_IMPORT_RE.sub("", cleaned)
    cleaned = _CSS_FETCH_FUNCTION_RE.sub("", cleaned)
    cleaned = _CSS_UNSAFE_RE.sub("", cleaned)
    cleaned = _CSS_REMOTE_LITERAL_RE.sub("", cleaned)
    return cleaned


def _is_safe_url_attr(attr_name: str, value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True

    if attr_name in {"href", "xlink:href"} and normalized.startswith("#"):
        return True

    if attr_name in {"src", "poster", "background", "href", "xlink:href"}:
        return normalized.startswith(_ALLOWED_DATA_IMAGE_PREFIXES)

    return False


def _iter_attr_names(attrs: dict) -> Iterable[str]:
    return list(attrs.keys())


def _ensure_csp_meta(soup: BeautifulSoup) -> None:
    html_tag = soup.find("html")
    if html_tag is None:
        return

    head = soup.find("head")
    if head is None:
        head = soup.new_tag("head")
        html_tag.insert(0, head)

    charset_meta = soup.new_tag("meta")
    charset_meta.attrs["charset"] = "utf-8"
    csp_meta = soup.new_tag("meta")
    csp_meta.attrs["http-equiv"] = "Content-Security-Policy"
    csp_meta.attrs["content"] = _CSP_CONTENT
    head.insert(0, charset_meta)
    head.insert(1, csp_meta)


def validate_slide_presentation_asset_file_ids(
    db: Any,
    user_id: str,
    file_ids: list[str] | None,
) -> list[str]:
    """Return deduplicated, owned image IDs suitable for slide embedding.

    The model receives only opaque IDs. This function is the authorization
    boundary that converts those IDs into presentation assets, so every entry
    is reloaded through the current user's ownership-filtered query.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_file_id in file_ids or []:
        file_id = str(raw_file_id or "").strip()
        if not file_id or file_id in seen:
            continue
        if len(normalized) >= _MAX_PRESENTATION_ASSETS:
            raise ValueError(f"A presentation may contain at most {_MAX_PRESENTATION_ASSETS} uploaded assets.")
        if not _OMLORIX_FILE_ID_RE.fullmatch(file_id):
            raise ValueError("A presentation asset contained an invalid file ID.")
        record = get_file(db, file_id, str(user_id))
        if record is None:
            raise ValueError("A referenced presentation asset was not found for this user.")
        mime_type = str(getattr(record, "file_type", "") or "").strip().lower()
        category = str(getattr(record, "file_category", "") or "").strip().lower()
        if category != "image" or mime_type not in _EMBEDDABLE_IMAGE_MIME_TYPES:
            raise ValueError("Slide presentation assets must be PNG, JPEG, GIF, or WebP images.")
        file_size = int(getattr(record, "file_size", 0) or 0)
        if file_size <= 0 or file_size > _MAX_PRESENTATION_ASSET_BYTES:
            raise ValueError("A slide presentation image is empty or exceeds the 20 MB asset limit.")
        seen.add(file_id)
        normalized.append(file_id)
    return normalized


def resolve_slide_presentation_file_references(
    html: str,
    *,
    db: Any,
    user_id: str,
    allowed_file_ids: list[str] | None,
) -> str:
    """Replace authorized ``omlorix-file://`` image references with data URIs.

    Inlining makes the canonical deck and exported PowerPoint self-contained.
    Only IDs explicitly attached to the tool call (or preserved on the Canvas
    artifact) are accepted, and ownership is checked again before bytes are
    read. References in scripts, links, CSS, or non-image elements are rejected.
    """
    allowed_ids = validate_slide_presentation_asset_file_ids(db, user_id, allowed_file_ids)
    allowed_set = set(allowed_ids)
    records = {file_id: get_file(db, file_id, str(user_id)) for file_id in allowed_ids}
    soup = BeautifulSoup(str(html or ""), "html.parser")
    total_asset_bytes = 0

    for tag in soup.find_all(True):
        for attr_name in list(tag.attrs.keys()):
            raw_value = tag.attrs.get(attr_name)
            value = " ".join(raw_value) if isinstance(raw_value, list) else str(raw_value or "")
            if not value.lower().startswith(_OMLORIX_FILE_PREFIX):
                continue
            normalized_attr = str(attr_name).lower()
            if (tag.name or "").lower() not in {"img", "image"} or normalized_attr not in {"src", "href", "xlink:href"}:
                raise ValueError("Uploaded presentation files may only be referenced as image sources.")
            file_id = value[len(_OMLORIX_FILE_PREFIX) :].split("?", 1)[0].split("#", 1)[0].strip()
            if file_id not in allowed_set:
                raise ValueError("A presentation image reference was not included in the tool's file_ids argument.")
            record = records.get(file_id)
            if record is None:
                raise ValueError("A referenced presentation image was not found for this user.")
            path = materialize_file_record(record, str(user_id))
            image_bytes = path.read_bytes()
            if not image_bytes or len(image_bytes) > _MAX_PRESENTATION_ASSET_BYTES:
                raise ValueError("A slide presentation image is empty or exceeds the 20 MB asset limit.")
            total_asset_bytes += len(image_bytes)
            if total_asset_bytes > _MAX_PRESENTATION_TOTAL_ASSET_BYTES:
                raise ValueError("Slide presentation images exceed the 40 MB combined asset limit.")
            mime_type = str(record.file_type or "").strip().lower()
            encoded = base64.b64encode(image_bytes).decode("ascii")
            tag.attrs[attr_name] = f"data:{mime_type};base64,{encoded}"

    return str(soup).strip()


def prepare_slide_presentation_html(
    html: str,
    *,
    db: Any,
    user_id: str,
    allowed_file_ids: list[str] | None,
) -> str:
    """Resolve owned assets and sanitize a complete presentation document."""
    resolved = resolve_slide_presentation_file_references(
        html,
        db=db,
        user_id=user_id,
        allowed_file_ids=allowed_file_ids,
    )
    return sanitize_slide_presentation_html(resolved)


def sanitize_slide_presentation_html(html: str) -> str:
    """Return slide HTML safe for browser previews and server-side rendering.

    The slide generator produces static decks. This sanitizer preserves ordinary
    HTML, inline SVG, and inline CSS while removing active content and network
    fetches that could execute in the UI or be requested by renderers.
    """
    soup = BeautifulSoup(str(html or ""), "html.parser")

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in list(soup.find_all(True)):
        tag_name = (tag.name or "").lower()
        if tag_name in _DANGEROUS_TAGS:
            tag.decompose()
            continue

        if tag_name == "style":
            tag.string = _clean_css(tag.get_text() or "")

        for attr_name in _iter_attr_names(tag.attrs):
            normalized_attr = attr_name.lower()
            attr_value = tag.attrs.get(attr_name)

            if (
                normalized_attr.startswith("on")
                or normalized_attr in _ALWAYS_DROP_ATTRS
                or (
                    normalized_attr.startswith("data-")
                    and normalized_attr in {"data-src", "data-href"}
                )
            ):
                del tag.attrs[attr_name]
                continue

            if normalized_attr == "style":
                raw_style = " ".join(attr_value) if isinstance(attr_value, list) else str(attr_value)
                tag.attrs[attr_name] = _clean_css(raw_style)
                continue

            if normalized_attr == "srcset":
                del tag.attrs[attr_name]
                continue

            if normalized_attr in _URL_ATTRS and not _is_safe_url_attr(normalized_attr, str(attr_value)):
                del tag.attrs[attr_name]

    _ensure_csp_meta(soup)
    return str(soup).strip()


def inspect_slide_presentation_html(html: str) -> dict[str, object]:
    """Classify and validate canonical slide HTML without executing it."""
    source = str(html or "")
    if len(source.encode("utf-8")) > MAX_PRESENTATION_HTML_BYTES:
        return {
            "is_presentation": False,
            "slide_count": 0,
            "reason": "presentation_html_too_large",
        }
    if not re.match(r"^\s*<!doctype\s+html\s*>", source, re.IGNORECASE):
        return {
            "is_presentation": False,
            "slide_count": 0,
            "reason": "missing_html_doctype",
        }

    soup = BeautifulSoup(source, "html.parser")
    slides = soup.select("section.slide")
    if len(slides) > MAX_PRESENTATION_SLIDES:
        return {
            "is_presentation": False,
            "slide_count": len(slides),
            "reason": "too_many_slides",
        }
    # Slide metadata uses human-facing 1-based numbers throughout the
    # presentation contract, including rendering and artifact URLs.
    for expected_index, slide in enumerate(slides, start=1):
        raw_index = slide.get("data-slide-index")
        try:
            index = int(str(raw_index))
        except (TypeError, ValueError):
            index = -1
        if index != expected_index:
            return {"is_presentation": False, "slide_count": len(slides), "reason": "invalid_slide_indexes"}
        if not str(slide.get("data-slide-title") or "").strip():
            return {
                "is_presentation": False,
                "slide_count": len(slides),
                "reason": "missing_slide_titles",
            }

    styles = soup.find_all("style")
    if len(styles) != 1:
        return {
            "is_presentation": False,
            "slide_count": len(slides),
            "reason": "invalid_style_block_count",
        }

    css = styles[0].get_text()
    effective_declarations = _effective_slide_declarations(
        css,
        soup=soup,
        slides=slides,
    )

    required_declarations = {
        "width": "1920px",
        "height": "1080px",
        "position": "relative",
        "overflow": "hidden",
        "box-sizing": "border-box",
    }
    has_static_canvas = all(
        all(
            slide_declarations.get(name) == value
            for name, value in required_declarations.items()
        )
        for slide_declarations in effective_declarations
    )
    is_presentation = bool(
        soup.find("html") and soup.find("body") and slides and has_static_canvas
    )
    return {
        "is_presentation": is_presentation,
        "slide_count": len(slides),
        "reason": "" if is_presentation else "missing_required_slide_markup",
    }


def validate_slide_presentation_html(html: str) -> int:
    """Return the slide count or raise when HTML is not a complete 16:9 deck."""
    inspection = inspect_slide_presentation_html(html)
    if not inspection["is_presentation"]:
        raise ValueError(f"Invalid slide presentation HTML: {inspection['reason']}")
    return int(inspection["slide_count"])
