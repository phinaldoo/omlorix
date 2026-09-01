"""Restricted ReportLab worker for sanitized Canvas Markdown HTML."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
from xml.sax.saxutils import escape


_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
_CPU_SECONDS = 20
_OUTPUT_FILE_BYTES = 64 * 1024 * 1024
_MAX_OPEN_FILES = 64
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_HTML_NODES = 20_000
_MAX_FLOWABLES = 10_000
_MAX_TABLE_CELLS = 5_000
_MAX_TABLE_COLUMNS = 100
_MAX_PAGES = 1_000
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 50 * 1024 * 1024
_CODE_TAB_SIZE = 4
_DATA_PNG_RE = re.compile(r"^data:image/png;base64,([A-Za-z0-9+/=]+)$")


class _RenderError(ValueError):
    pass


def _apply_resource_limits() -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - backend production runs in Linux
        return
    for resource_name, value in (
        (getattr(resource, "RLIMIT_AS", None), _ADDRESS_SPACE_BYTES),
        (getattr(resource, "RLIMIT_CPU", None), _CPU_SECONDS),
        (getattr(resource, "RLIMIT_FSIZE", None), _OUTPUT_FILE_BYTES),
        (getattr(resource, "RLIMIT_NOFILE", None), _MAX_OPEN_FILES),
    ):
        if resource_name is None:
            continue
        try:
            _soft, hard = resource.getrlimit(resource_name)
            bounded_hard = value if hard < 0 else min(value, hard)
            resource.setrlimit(resource_name, (min(value, bounded_hard), bounded_hard))
        except (OSError, ValueError):
            continue


def _load_request(path: Path) -> dict:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _RenderError("Canvas PDF renderer request is unavailable.") from exc
    if size <= 0 or size > _MAX_REQUEST_BYTES:
        raise _RenderError("Canvas PDF renderer request is invalid.")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _RenderError("Canvas PDF renderer request is invalid.") from exc
    if not isinstance(request, dict):
        raise _RenderError("Canvas PDF renderer request is invalid.")
    return request


def _validated_paths(request: dict) -> tuple[Path, Path, Path]:
    try:
        html_path = Path(str(request["html_path"])).resolve(strict=True)
        output_dir = Path(str(request["output_dir"])).resolve(strict=True)
        max_html_bytes = int(request["max_html_bytes"])
        max_output_bytes = int(request["max_output_bytes"])
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _RenderError("Canvas PDF renderer request is invalid.") from exc
    if (
        not html_path.is_file()
        or not output_dir.is_dir()
        or html_path.parent != output_dir
        or max_html_bytes <= 0
        or max_output_bytes <= 0
        or html_path.stat().st_size > max_html_bytes
    ):
        raise _RenderError("Canvas PDF renderer request is invalid.")
    return html_path, output_dir / "canvas.pdf", output_dir


@dataclass(frozen=True)
class _InlineStyle:
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    href: str = ""


@dataclass
class _Word:
    parts: list[tuple[str, _InlineStyle]]

    @property
    def text(self) -> str:
        return "".join(text for text, _style in self.parts)


class _FontRegistry:
    def __init__(self):
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        self.pdfmetrics = pdfmetrics
        self.TTFont = TTFont
        self.registered: dict[str, object] = {}
        bundled_font_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"

        broad = self._first_existing(str(bundled_font_dir / "NotoSans-wdth-wght.ttf"))
        italic = self._first_existing(
            str(bundled_font_dir / "NotoSans-Italic-wdth-wght.ttf")
        )
        if not broad or not italic:
            raise _RenderError("Canvas PDF fonts are unavailable.")

        self._register_tt("OmlorixSans", broad)
        self._register_tt("OmlorixSansBold", broad)
        self._register_tt("OmlorixSansItalic", italic)
        self._register_tt("OmlorixSansBoldItalic", italic)
        pdfmetrics.registerFontFamily(
            "OmlorixSans",
            normal="OmlorixSans",
            bold="OmlorixSansBold",
            italic="OmlorixSansItalic",
            boldItalic="OmlorixSansBoldItalic",
        )

        arabic = self._first_existing(
            str(bundled_font_dir / "NotoSansArabic-wdth-wght.ttf")
        )
        devanagari = self._first_existing(
            str(bundled_font_dir / "NotoSansDevanagari-wdth-wght.ttf")
        )
        if not arabic or not devanagari:
            raise _RenderError("Canvas PDF complex-script fonts are unavailable.")
        self._register_family("OmlorixArabic", arabic, arabic)
        self._register_family(
            "OmlorixDevanagari",
            devanagari,
            devanagari,
        )
        cjk = self._first_existing(str(bundled_font_dir / "NotoSansSC-wght.ttf"))
        if not cjk:
            raise _RenderError("Canvas PDF CJK font is unavailable.")
        # CJK does not need contextual shaping. Keeping this font unshapable
        # also preserves the source Unicode code points in the PDF ToUnicode
        # map instead of substituting compatibility characters.
        class _UnshapableCjkFont(self.TTFont):
            def hbFont(self, *_args, **_kwargs):
                raise AttributeError

        cjk_font = _UnshapableCjkFont("OmlorixCJK", cjk, shapable=False)
        pdfmetrics.registerFont(cjk_font)
        self.registered["OmlorixCJK"] = cjk_font

    @staticmethod
    def _first_existing(*candidates: str | None) -> str | None:
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    def _register_tt(self, name: str, path: str) -> None:
        if name in self.registered:
            return
        font = self.TTFont(name, path, shapable=True)
        self.pdfmetrics.registerFont(font)
        self.registered[name] = font

    def _register_family(self, family: str, regular: str, bold: str) -> None:
        regular_name = family
        bold_name = f"{family}Bold"
        self._register_tt(regular_name, regular)
        self._register_tt(bold_name, bold)
        self.pdfmetrics.registerFontFamily(
            family,
            normal=regular_name,
            bold=bold_name,
            italic=regular_name,
            boldItalic=bold_name,
        )

    @staticmethod
    def font_for_character(character: str, style: _InlineStyle) -> str:
        codepoint = ord(character)
        if 0x0600 <= codepoint <= 0x08FF or 0xFB50 <= codepoint <= 0xFEFF:
            return "OmlorixArabicBold" if style.bold else "OmlorixArabic"
        if 0x0900 <= codepoint <= 0x097F:
            return "OmlorixDevanagariBold" if style.bold else "OmlorixDevanagari"
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x31F0 <= codepoint <= 0x31FF
            or 0xFF66 <= codepoint <= 0xFF9F
        ):
            return "OmlorixCJK"
        if (
            0x2E80 <= codepoint <= 0x2FFF
            or 0x31C0 <= codepoint <= 0x31EF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
        ):
            return "OmlorixCJK"
        if style.code and codepoint < 256:
            if style.bold and style.italic:
                return "Courier-BoldOblique"
            if style.bold:
                return "Courier-Bold"
            if style.italic:
                return "Courier-Oblique"
            return "Courier"
        if style.bold and style.italic:
            return "OmlorixSansBoldItalic"
        if style.bold:
            return "OmlorixSansBold"
        if style.italic:
            return "OmlorixSansItalic"
        return "OmlorixSans"


def _strong_direction(text: str) -> str | None:
    for character in text:
        bidi_class = unicodedata.bidirectional(character)
        if bidi_class in {"R", "AL"}:
            return "R"
        if bidi_class == "L":
            return "L"
    return None


def _visual_order(words: list[_Word]) -> tuple[list[_Word], str]:
    if not words:
        return words, "L"
    directions = [_strong_direction(word.text) for word in words]
    base_direction = next((direction for direction in directions if direction), "L")

    for index, direction in enumerate(directions):
        if direction is not None:
            continue
        previous = next((value for value in reversed(directions[:index]) if value), None)
        following = next((value for value in directions[index + 1 :] if value), None)
        directions[index] = previous or following or base_direction

    runs: list[tuple[str, list[_Word]]] = []
    for word, direction in zip(words, directions):
        resolved = direction or base_direction
        if not runs or runs[-1][0] != resolved:
            runs.append((resolved, [word]))
        else:
            runs[-1][1].append(word)
    visual_runs = [
        (direction, list(reversed(run_words)) if direction == "R" else run_words)
        for direction, run_words in runs
    ]
    if base_direction == "R":
        visual_runs.reverse()
    return [word for _direction, run_words in visual_runs for word in run_words], base_direction


def _flatten_inline(nodes, base_style: _InlineStyle | None = None):
    from bs4 import NavigableString

    style = base_style or _InlineStyle()
    for node in nodes:
        if isinstance(node, NavigableString):
            yield str(node), style
            continue
        name = str(getattr(node, "name", "") or "").lower()
        if name == "br":
            yield None, style
            continue
        if name == "img":
            continue
        child_style = style
        if name in {"b", "strong"}:
            child_style = replace(child_style, bold=True)
        elif name in {"i", "em"}:
            child_style = replace(child_style, italic=True)
        elif name in {"del", "s"}:
            child_style = replace(child_style, strike=True)
        elif name in {"code", "kbd"}:
            child_style = replace(child_style, code=True)
        elif name == "a":
            child_style = replace(child_style, href=str(node.get("href") or ""))
        yield from _flatten_inline(node.contents, child_style)


def _words_from_nodes(nodes) -> list[list[_Word]]:
    segments: list[list[_Word]] = [[]]
    current_parts: list[tuple[str, _InlineStyle]] = []

    def flush_word() -> None:
        if current_parts:
            segments[-1].append(_Word(list(current_parts)))
            current_parts.clear()

    for text, style in _flatten_inline(nodes):
        if text is None:
            flush_word()
            segments.append([])
            continue
        sanitized = "".join(
            character
            for character in text
            if character in {"\t", "\n", "\r"}
            or (ord(character) >= 0x20 and ord(character) != 0x7F)
        )
        for part in re.findall(r"\s+|[^\s]+", sanitized):
            if part.isspace():
                flush_word()
            else:
                current_parts.append((part, style))
    flush_word()
    return segments


def _render_piece(text: str, style: _InlineStyle, fonts: _FontRegistry) -> str:
    if not text:
        return ""
    runs: list[tuple[str, str]] = []
    for character in text:
        font_name = fonts.font_for_character(character, style)
        if runs and runs[-1][0] == font_name:
            runs[-1] = (font_name, runs[-1][1] + character)
        else:
            runs.append((font_name, character))
    rendered = "".join(
        f'<font name="{font_name}">{escape(run_text)}</font>'
        for font_name, run_text in runs
    )
    if style.strike:
        rendered = f"<strike>{rendered}</strike>"
    if style.href:
        rendered = f'<a href="{escape(style.href, {chr(34): "&quot;"})}">{rendered}</a>'
    return rendered


def _paragraph_markup(nodes, fonts: _FontRegistry) -> tuple[str, str]:
    rendered_segments: list[str] = []
    paragraph_direction = "L"
    for segment in _words_from_nodes(nodes):
        visual_words, direction = _visual_order(segment)
        if segment and not rendered_segments:
            paragraph_direction = direction
        rendered_segments.append(
            " ".join(
                "".join(_render_piece(text, style, fonts) for text, style in word.parts)
                for word in visual_words
            )
        )
    return "<br/>".join(rendered_segments), paragraph_direction


def _preformatted_markup(text: str, fonts: _FontRegistry) -> str:
    """Return safe code markup while retaining indentation and spacing."""

    sanitized = "".join(
        character
        for character in str(text or "")
        if character in {"\t", "\n", "\r"}
        or (ord(character) >= 0x20 and ord(character) != 0x7F)
    )
    normalized = sanitized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.expandtabs(_CODE_TAB_SIZE)
    code_style = _InlineStyle(code=True)
    return "\n".join(
        _render_piece(line.replace(" ", "\u00a0"), code_style, fonts)
        for line in normalized.split("\n")
    )


class _Renderer:
    def __init__(self, fonts: _FontRegistry):
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph

        self.colors = colors
        self.TA_LEFT = TA_LEFT
        self.TA_RIGHT = TA_RIGHT
        self.page_size = A4
        self.margin = 18 * mm
        self.content_width = A4[0] - (2 * self.margin)
        self.content_height = A4[1] - (2 * self.margin)
        self.fonts = fonts
        self.total_image_bytes = 0
        self.flowable_count = 0

        quote_border_color = colors.HexColor("#d1d5db")

        class QuoteParagraph(Paragraph):
            """Splittable paragraph that repeats the quote rail on each page."""

            def draw(self):
                self.canv.saveState()
                try:
                    self.canv.setStrokeColor(quote_border_color)
                    self.canv.setLineWidth(2)
                    self.canv.line(0, 0, 0, self.height)
                finally:
                    self.canv.restoreState()
                super().draw()

        self._quote_paragraph_type = QuoteParagraph

        body = ParagraphStyle(
            "CanvasBody",
            fontName="OmlorixSans",
            fontSize=10.5,
            leading=14.2,
            textColor=colors.HexColor("#1f2937"),
            spaceAfter=7,
            shaping=1,
            splitLongWords=1,
        )
        self.styles = {
            "body": body,
            "h1": ParagraphStyle(
                "CanvasH1",
                parent=body,
                fontName="OmlorixSansBold",
                fontSize=22,
                leading=26.4,
                textColor=colors.HexColor("#111827"),
                spaceAfter=10,
                keepWithNext=1,
            ),
            "h2": ParagraphStyle(
                "CanvasH2",
                parent=body,
                fontName="OmlorixSansBold",
                fontSize=16,
                leading=19.2,
                textColor=colors.HexColor("#111827"),
                spaceBefore=12,
                spaceAfter=6,
                keepWithNext=1,
            ),
            "h3": ParagraphStyle(
                "CanvasH3",
                parent=body,
                fontName="OmlorixSansBold",
                fontSize=13,
                leading=15.6,
                textColor=colors.HexColor("#111827"),
                spaceBefore=9,
                spaceAfter=5,
                keepWithNext=1,
            ),
            "table": ParagraphStyle(
                "CanvasTable",
                parent=body,
                fontSize=8.5,
                leading=10.5,
                spaceAfter=0,
            ),
            "code": ParagraphStyle(
                "CanvasCode",
                parent=body,
                fontName="OmlorixSans",
                fontSize=8.5,
                leading=11,
                backColor=colors.HexColor("#f3f4f6"),
                borderPadding=6,
                spaceBefore=7,
                spaceAfter=7,
            ),
            "quote": ParagraphStyle(
                "CanvasQuote",
                parent=body,
                textColor=colors.HexColor("#4b5563"),
                fontName="OmlorixSansItalic",
                leftIndent=8,
                spaceBefore=7,
                spaceAfter=7,
            ),
        }

    def _count(self, count: int = 1) -> None:
        self.flowable_count += count
        if self.flowable_count > _MAX_FLOWABLES:
            raise _RenderError("Canvas PDF contains too many layout elements.")

    def paragraph(self, nodes, style_name: str = "body", *, force_code: bool = False):
        from reportlab.platypus import Paragraph, XPreformatted

        if force_code:
            plain_text = "".join(str(node) for node in nodes)
            markup = _preformatted_markup(plain_text, self.fonts)
            direction = "L"
        else:
            markup, direction = _paragraph_markup(nodes, self.fonts)
        if not markup:
            return None
        style = self.styles[style_name].clone(f"{style_name}-{self.flowable_count}")
        style.alignment = self.TA_RIGHT if direction == "R" else self.TA_LEFT
        self._count()
        if force_code:
            flowable = XPreformatted
        elif style_name == "quote":
            flowable = self._quote_paragraph_type
        else:
            flowable = Paragraph
        return flowable(markup, style)

    def image(self, tag):
        from PIL import Image as PilImage
        from reportlab.platypus import Image

        match = _DATA_PNG_RE.fullmatch(str(tag.get("src") or ""))
        if not match:
            return None
        try:
            payload = base64.b64decode(match.group(1), validate=True)
        except (ValueError, TypeError):
            return None
        if len(payload) <= 0 or len(payload) > _MAX_IMAGE_BYTES:
            raise _RenderError("Canvas PDF image exceeds the safe size limit.")
        self.total_image_bytes += len(payload)
        if self.total_image_bytes > _MAX_TOTAL_IMAGE_BYTES:
            raise _RenderError("Canvas PDF images exceed the safe size limit.")
        buffer = BytesIO(payload)
        try:
            with PilImage.open(buffer) as source:
                width, height = source.size
        except Exception as exc:
            raise _RenderError("Canvas PDF image is invalid.") from exc
        if width <= 0 or height <= 0 or width * height > 40_000_000:
            raise _RenderError("Canvas PDF image dimensions are invalid.")
        # ReportLab's default document frame reserves six points of padding on
        # every edge in addition to the configured page margins.
        available_width = self.content_width - 12
        available_height = self.content_height - 12
        scale = min(1.0, available_width / width, available_height / height)
        buffer.seek(0)
        flowable = Image(buffer, width=width * scale, height=height * scale)
        flowable.hAlign = "CENTER"
        flowable._omlorix_image_buffer = buffer
        self._count()
        return flowable

    def mixed_block(self, tag, style_name: str):
        flowables = []
        inline_nodes = []

        def flush_inline() -> None:
            paragraph = self.paragraph(inline_nodes, style_name)
            if paragraph is not None:
                flowables.append(paragraph)
            inline_nodes.clear()

        for child in tag.contents:
            if getattr(child, "name", None) == "img":
                flush_inline()
                image = self.image(child)
                if image is not None:
                    flowables.append(image)
            else:
                inline_nodes.append(child)
        flush_inline()
        return flowables

    def table(self, tag):
        from reportlab.platypus import Table, TableStyle

        rows = tag.find_all("tr")
        if not rows:
            return None
        occupied: dict[tuple[int, int], bool] = {}
        grid: list[list[object]] = []
        spans: list[tuple[int, int, int, int]] = []
        header_rows: set[int] = set()
        cell_count = 0
        max_column = 0

        for row_index, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                cells = row.find_all(["th", "td"])
            row_values: list[object] = []
            column_index = 0
            for cell in cells:
                while occupied.get((row_index, column_index)):
                    column_index += 1
                try:
                    colspan = max(1, min(100, int(cell.get("colspan") or 1)))
                    rowspan = max(1, min(100, int(cell.get("rowspan") or 1)))
                except (TypeError, ValueError):
                    colspan = rowspan = 1
                if column_index + colspan > _MAX_TABLE_COLUMNS:
                    raise _RenderError("Canvas PDF table contains too many columns.")
                while len(row_values) <= column_index:
                    row_values.append("")
                paragraph = self.paragraph(cell.contents, "table")
                row_values[column_index] = paragraph or ""
                for y in range(row_index, row_index + rowspan):
                    for x in range(column_index, column_index + colspan):
                        if y != row_index or x != column_index:
                            occupied[(y, x)] = True
                if colspan > 1 or rowspan > 1:
                    spans.append(
                        (column_index, row_index, column_index + colspan - 1, row_index + rowspan - 1)
                    )
                if str(cell.name).lower() == "th":
                    header_rows.add(row_index)
                column_index += colspan
                max_column = max(max_column, column_index)
                cell_count += 1
                if cell_count > _MAX_TABLE_CELLS:
                    raise _RenderError("Canvas PDF table contains too many cells.")
            grid.append(row_values)

        if max_column <= 0:
            return None
        for row_index, row_values in enumerate(grid):
            while len(row_values) < max_column:
                row_values.append("")
            for column_index in range(max_column):
                if occupied.get((row_index, column_index)) and row_values[column_index] == "":
                    row_values[column_index] = ""

        commands = [
            ("GRID", (0, 0), (-1, -1), 0.5, self.colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for header_row in header_rows:
            commands.append(
                ("BACKGROUND", (0, header_row), (-1, header_row), self.colors.HexColor("#f3f4f6"))
            )
        for x0, y0, x1, y1 in spans:
            if y1 < len(grid):
                commands.append(("SPAN", (x0, y0), (x1, y1)))
        flowable = Table(
            grid,
            colWidths=[self.content_width / max_column] * max_column,
            repeatRows=1 if 0 in header_rows else 0,
            hAlign="LEFT",
        )
        flowable.setStyle(TableStyle(commands))
        flowable.spaceBefore = 7
        flowable.spaceAfter = 7
        self._count()
        return flowable

    def list_flowable(self, tag):
        from reportlab.platypus import ListFlowable, ListItem

        items = []
        for item_tag in tag.find_all("li", recursive=False):
            direct_nodes = [
                child
                for child in item_tag.contents
                if str(getattr(child, "name", "") or "").lower() not in {"ul", "ol"}
            ]
            item_flowables = []
            paragraph = self.paragraph(direct_nodes, "body")
            if paragraph is not None:
                item_flowables.append(paragraph)
            for nested in item_tag.find_all(["ul", "ol"], recursive=False):
                nested_list = self.list_flowable(nested)
                if nested_list is not None:
                    item_flowables.append(nested_list)
            if item_flowables:
                items.append(ListItem(item_flowables, leftIndent=0))
        if not items:
            return None
        ordered = str(tag.name).lower() == "ol"
        try:
            start = int(tag.get("start") or 1)
        except (TypeError, ValueError):
            start = 1
        flowable = ListFlowable(
            items,
            bulletType="1" if ordered else "bullet",
            start=start if ordered else None,
            leftIndent=18,
            bulletFontName="OmlorixSans",
            bulletFontSize=10.5,
            spaceAfter=7,
        )
        self._count()
        return flowable

    def flowables(self, soup):
        from bs4 import NavigableString
        from reportlab.platypus import HRFlowable

        flowables = []
        for child in soup.contents:
            if isinstance(child, NavigableString):
                if str(child).strip():
                    paragraph = self.paragraph([child], "body")
                    if paragraph is not None:
                        flowables.append(paragraph)
                continue
            name = str(getattr(child, "name", "") or "").lower()
            if name in {"html", "body"}:
                flowables.extend(self.flowables(child))
            elif name in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
                style_name = name if name in {"h1", "h2"} else "h3" if name.startswith("h") else "body"
                flowables.extend(self.mixed_block(child, style_name))
            elif name == "img":
                image = self.image(child)
                if image is not None:
                    flowables.append(image)
            elif name == "pre":
                paragraph = self.paragraph([child.get_text()], "code", force_code=True)
                if paragraph is not None:
                    flowables.append(paragraph)
            elif name == "blockquote":
                quote_flowables = self.mixed_block(child, "quote")
                if quote_flowables:
                    flowables.extend(quote_flowables)
            elif name in {"ul", "ol"}:
                list_flowable = self.list_flowable(child)
                if list_flowable is not None:
                    flowables.append(list_flowable)
            elif name == "table":
                table = self.table(child)
                if table is not None:
                    flowables.append(table)
            elif name == "hr":
                self._count()
                flowables.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.5,
                        color=self.colors.HexColor("#d1d5db"),
                        spaceBefore=7,
                        spaceAfter=7,
                    )
                )
            else:
                paragraph = self.paragraph(child.contents, "body")
                if paragraph is not None:
                    flowables.append(paragraph)
        return flowables


def _render(request: dict, html_path: Path, output_path: Path) -> None:
    from bs4 import BeautifulSoup
    from reportlab.platypus import SimpleDocTemplate

    try:
        html_text = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _RenderError("Canvas PDF content is invalid.") from exc
    soup = BeautifulSoup(html_text, "html.parser")
    if len(soup.find_all(True)) > _MAX_HTML_NODES:
        raise _RenderError("Canvas PDF contains too many layout elements.")

    fonts = _FontRegistry()
    renderer = _Renderer(fonts)
    flowables = renderer.flowables(soup)
    if not flowables:
        paragraph = renderer.paragraph([soup.new_string(" ")], "body")
        flowables = [paragraph] if paragraph is not None else []

    title = str(request.get("title") or "Canvas")[:255]
    max_output_bytes = int(request["max_output_bytes"])

    def configure_page(canvas, document) -> None:
        if document.page > _MAX_PAGES:
            raise _RenderError("Canvas PDF contains too many pages.")
        canvas.setTitle(title)
        canvas.setAuthor("Omlorix")
        canvas.setCreator("Omlorix")

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=renderer.page_size,
        leftMargin=renderer.margin,
        rightMargin=renderer.margin,
        topMargin=renderer.margin,
        bottomMargin=renderer.margin,
        title=title,
        author="Omlorix",
        creator="Omlorix",
        pageCompression=1,
    )
    document.build(flowables, onFirstPage=configure_page, onLaterPages=configure_page)
    if output_path.stat().st_size > max_output_bytes:
        raise _RenderError("Canvas PDF output exceeds the safe size limit.")


def _write_result(output_dir: Path, payload: dict) -> None:
    temp_path = output_dir / "result.json.tmp"
    result_path = output_dir / "result.json"
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temp_path, result_path)


def main(argv: list[str]) -> int:
    _apply_resource_limits()
    output_dir: Path | None = None
    try:
        if len(argv) != 2:
            return 2
        request = _load_request(Path(argv[1]))
        html_path, output_path, output_dir = _validated_paths(request)
        _render(request, html_path, output_path)
        _write_result(output_dir, {"ok": True})
        return 0
    except _RenderError as exc:
        if output_dir is not None:
            _write_result(output_dir, {"ok": False, "error": str(exc)})
            return 1
        return 2
    except Exception:
        if output_dir is not None:
            _write_result(output_dir, {"ok": False, "error": "Canvas PDF rendering failed."})
            return 1
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
