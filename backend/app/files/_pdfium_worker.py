"""Restricted command-line worker for :mod:`app.files.pdfium`.

This module intentionally imports no application code.  It is launched with
Python isolated mode, applies resource limits before importing PDFium, and
communicates only through files in a parent-created private temporary folder.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any


_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESULT_BYTES = 32 * 1024 * 1024
_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
_CPU_SECONDS = 15
_OUTPUT_FILE_BYTES = 64 * 1024 * 1024
_MAX_OPEN_FILES = 64


class _WorkerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _apply_resource_limits() -> None:
    """Apply portable POSIX limits when the host exposes ``resource``."""

    try:
        import resource
    except ImportError:  # pragma: no cover - backend production runs on Linux
        return

    limits = (
        (getattr(resource, "RLIMIT_AS", None), _ADDRESS_SPACE_BYTES),
        (getattr(resource, "RLIMIT_CPU", None), _CPU_SECONDS),
        (getattr(resource, "RLIMIT_FSIZE", None), _OUTPUT_FILE_BYTES),
        (getattr(resource, "RLIMIT_NOFILE", None), _MAX_OPEN_FILES),
    )
    for resource_name, value in limits:
        if resource_name is None:
            continue
        try:
            current_soft, current_hard = resource.getrlimit(resource_name)
            bounded_hard = value if current_hard < 0 else min(value, current_hard)
            bounded_soft = min(value, bounded_hard)
            resource.setrlimit(resource_name, (bounded_soft, bounded_hard))
        except (OSError, ValueError):
            continue


def _load_request(request_path: Path) -> dict[str, Any]:
    try:
        request_size = request_path.stat().st_size
    except OSError as exc:
        raise _WorkerError("invalid_request", "PDF worker request is unavailable.") from exc
    if request_size <= 0 or request_size > _MAX_REQUEST_BYTES:
        raise _WorkerError("invalid_request", "PDF worker request is invalid.")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _WorkerError("invalid_request", "PDF worker request is invalid.") from exc
    if not isinstance(request, dict):
        raise _WorkerError("invalid_request", "PDF worker request is invalid.")
    return request


def _bounded_positive_int(request: dict[str, Any], key: str) -> int:
    try:
        value = int(request[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise _WorkerError("invalid_request", f"Invalid PDF worker limit: {key}.") from exc
    if value <= 0:
        raise _WorkerError("invalid_request", f"Invalid PDF worker limit: {key}.")
    return value


def _validated_paths(request: dict[str, Any]) -> tuple[Path, Path]:
    try:
        input_path = Path(str(request["input_path"])).resolve(strict=True)
        output_dir = Path(str(request["output_dir"])).resolve(strict=True)
    except (KeyError, OSError, RuntimeError) as exc:
        raise _WorkerError("invalid_request", "PDF worker paths are invalid.") from exc
    if not input_path.is_file() or not output_dir.is_dir():
        raise _WorkerError("invalid_request", "PDF worker paths are invalid.")
    max_file_bytes = _bounded_positive_int(request, "max_file_bytes")
    try:
        file_size = input_path.stat().st_size
    except OSError as exc:
        raise _WorkerError("invalid_pdf", "PDF file could not be opened.") from exc
    if file_size <= 0:
        raise _WorkerError("invalid_pdf", "PDF file is empty.")
    if file_size > max_file_bytes:
        raise _WorkerError("limit", "PDF file is too large to process.")
    return input_path, output_dir


def _open_document(input_path: Path, max_pages: int):
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except Exception as exc:
        raise _WorkerError("unavailable", "PDF processing support is unavailable.") from exc

    try:
        document = pdfium.PdfDocument(input_path)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
            raise _WorkerError(
                "password", "Password-protected PDF files cannot be processed."
            ) from exc
        raise _WorkerError("invalid_pdf", "PDF file could not be opened.") from exc
    except Exception as exc:
        raise _WorkerError("invalid_pdf", "PDF file could not be opened.") from exc

    page_count = len(document)
    if page_count <= 0:
        document.close()
        raise _WorkerError("invalid_pdf", "PDF file does not contain any pages.")
    if page_count > max_pages:
        document.close()
        raise _WorkerError("limit", "PDF file contains too many pages to process.")
    return document


def _page_size(page) -> tuple[float, float]:
    width, height = map(float, page.get_size())
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise _WorkerError("invalid_pdf", "PDF page has invalid dimensions.")
    return width, height


def _inspect(request: dict[str, Any], input_path: Path) -> dict[str, Any]:
    max_pages = _bounded_positive_int(request, "max_pages")
    document = _open_document(input_path, max_pages)
    try:
        pages = []
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                width, height = _page_size(page)
            finally:
                page.close()
            pages.append({"page": page_index + 1, "width": width, "height": height})
        return {"page_count": len(document), "pages": pages}
    finally:
        document.close()


def _display_box(
    raw_box: tuple[float, float, float, float],
    *,
    page_bbox: tuple[float, float, float, float],
    rotation: int,
) -> tuple[float, float, float, float] | None:
    """Convert a PDF bottom-left char box to rendered top-left coordinates."""

    left, bottom, right, top = map(float, raw_box)
    page_left, page_bottom, page_right, page_top = map(float, page_bbox)
    if not all(
        math.isfinite(value)
        for value in (
            left,
            bottom,
            right,
            top,
            page_left,
            page_bottom,
            page_right,
            page_top,
        )
    ):
        return None

    page_width = abs(page_right - page_left)
    page_height = abs(page_top - page_bottom)
    x0 = min(left - page_left, right - page_left)
    x1 = max(left - page_left, right - page_left)
    y0 = min(page_height - (top - page_bottom), page_height - (bottom - page_bottom))
    y1 = max(page_height - (top - page_bottom), page_height - (bottom - page_bottom))

    if rotation == 90:
        transformed = (page_height - y1, x0, page_height - y0, x1)
    elif rotation == 180:
        transformed = (page_width - x1, page_height - y1, page_width - x0, page_height - y0)
    elif rotation == 270:
        transformed = (y0, page_width - x1, y1, page_width - x0)
    else:
        transformed = (x0, y0, x1, y1)

    tx0, ty0, tx1, ty1 = transformed
    left = min(tx0, tx1)
    top = min(ty0, ty1)
    right = max(tx0, tx1)
    bottom = max(ty0, ty1)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _extract(request: dict[str, Any], input_path: Path) -> dict[str, Any]:
    import pypdfium2.raw as pdfium_c

    max_pages = _bounded_positive_int(request, "max_pages")
    max_words = _bounded_positive_int(request, "max_words")
    max_text_chars = _bounded_positive_int(request, "max_text_chars")
    try:
        page_number = int(request["page_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _WorkerError("invalid_request", "PDF page number is invalid.") from exc

    document = _open_document(input_path, max_pages)
    try:
        if page_number < 1 or page_number > len(document):
            raise _WorkerError("page_not_found", "PDF page not found.")
        page = document[page_number - 1]
        text_page = None
        try:
            width, height = _page_size(page)
            page_bbox = tuple(map(float, page.get_bbox()))
            rotation = int(page.get_rotation()) % 360
            if rotation not in {0, 90, 180, 270}:
                rotation = 0
            text_page = page.get_textpage()
            character_count = int(text_page.count_chars())
            if character_count > max_text_chars:
                raise _WorkerError("limit", "PDF page contains too much text to preview.")

            text_handle = text_page.raw
            loose_box = pdfium_c.FS_RECTF()
            words: list[dict[str, Any]] = []
            seen_words: set[tuple[Any, ...]] = set()
            word_chars: list[str] = []
            word_boxes: list[tuple[float, float, float, float]] = []
            text_char_count = 0
            block_index = 0
            line_index = 0
            word_index = 0
            previous_was_carriage_return = False
            consecutive_line_breaks = 0

            def flush_word() -> None:
                nonlocal word_index
                if not word_chars or not word_boxes:
                    word_chars.clear()
                    word_boxes.clear()
                    return
                text = "".join(word_chars)
                left = min(box[0] for box in word_boxes)
                top = min(box[1] for box in word_boxes)
                right = max(box[2] for box in word_boxes)
                bottom = max(box[3] for box in word_boxes)
                key = (
                    text,
                    round(left, 1),
                    round(top, 1),
                    round(right, 1),
                    round(bottom, 1),
                )
                if key not in seen_words:
                    if len(words) >= max_words:
                        raise _WorkerError(
                            "limit", "PDF page contains too much text to preview."
                        )
                    seen_words.add(key)
                    words.append(
                        {
                            "text": text,
                            "x": left,
                            "y": top,
                            "width": right - left,
                            "height": bottom - top,
                            "block": block_index,
                            "line": line_index,
                            "word": word_index,
                        }
                    )
                    word_index += 1
                word_chars.clear()
                word_boxes.clear()

            for character_index in range(character_count):
                codepoint = int(pdfium_c.FPDFText_GetUnicode(text_handle, character_index))
                if 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
                    codepoint = 0xFFFD
                character = chr(codepoint) if codepoint else ""
                is_line_break = character in {"\n", "\r", "\x02"}
                if not character or character.isspace() or is_line_break:
                    flush_word()
                    if is_line_break:
                        is_crlf_tail = character == "\n" and previous_was_carriage_return
                        if not is_crlf_tail:
                            if consecutive_line_breaks:
                                block_index += 1
                            line_index += 1
                            word_index = 0
                            consecutive_line_breaks += 1
                        previous_was_carriage_return = character == "\r"
                    else:
                        previous_was_carriage_return = False
                        consecutive_line_breaks = 0
                    continue

                previous_was_carriage_return = False
                consecutive_line_breaks = 0
                text_char_count += 1
                if text_char_count > max_text_chars:
                    raise _WorkerError("limit", "PDF page contains too much text to preview.")
                if not pdfium_c.FPDFText_GetLooseCharBox(text_handle, character_index, loose_box):
                    continue
                display_box = _display_box(
                    (loose_box.left, loose_box.bottom, loose_box.right, loose_box.top),
                    page_bbox=page_bbox,
                    rotation=rotation,
                )
                if display_box is None:
                    continue
                word_chars.append(character)
                word_boxes.append(display_box)
            flush_word()

            return {
                "page": page_number,
                "width": width,
                "height": height,
                "words": words,
            }
        finally:
            if text_page is not None:
                text_page.close()
            page.close()
    finally:
        document.close()


def _render(request: dict[str, Any], input_path: Path, output_dir: Path) -> dict[str, Any]:
    max_pages = _bounded_positive_int(request, "max_pages")
    max_side_pixels = _bounded_positive_int(request, "max_side_pixels")
    max_pixels = _bounded_positive_int(request, "max_pixels")
    max_page_png_bytes = _bounded_positive_int(request, "max_page_png_bytes")
    max_total_png_bytes = _bounded_positive_int(request, "max_total_png_bytes")
    try:
        scale = float(request["scale"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _WorkerError("invalid_request", "PDF render scale is invalid.") from exc
    if not math.isfinite(scale) or scale <= 0 or scale > 16:
        raise _WorkerError("invalid_request", "PDF render scale is invalid.")

    document = _open_document(input_path, max_pages)
    forms_initialized = False
    try:
        raw_page_numbers = request.get("page_numbers")
        if raw_page_numbers is not None:
            if not isinstance(raw_page_numbers, list) or not raw_page_numbers:
                raise _WorkerError("invalid_request", "PDF page numbers are invalid.")
            try:
                page_indices = [int(value) - 1 for value in raw_page_numbers]
            except (TypeError, ValueError) as exc:
                raise _WorkerError("invalid_request", "PDF page numbers are invalid.") from exc
            if any(index < 0 or index >= len(document) for index in page_indices):
                raise _WorkerError("page_not_found", "PDF page not found.")
            if len(set(page_indices)) != len(page_indices):
                raise _WorkerError("invalid_request", "PDF page numbers are invalid.")
        else:
            page_limit = _bounded_positive_int(request, "page_limit")
            page_indices = list(range(min(len(document), page_limit)))

        try:
            document.init_forms()
            forms_initialized = True
        except Exception:
            forms_initialized = False

        files: list[str] = []
        total_png_bytes = 0
        started_at = time.monotonic()
        for output_index, page_index in enumerate(page_indices):
            if time.monotonic() - started_at > _CPU_SECONDS:
                raise _WorkerError("limit", "PDF rendering exceeded the time limit.")
            page = document[page_index]
            bitmap = None
            try:
                width, height = _page_size(page)
                width_pixels = math.ceil(width * scale)
                height_pixels = math.ceil(height * scale)
                if (
                    width_pixels <= 0
                    or height_pixels <= 0
                    or width_pixels > max_side_pixels
                    or height_pixels > max_side_pixels
                ):
                    raise _WorkerError("limit", "PDF page is too large to render safely.")
                if width_pixels * height_pixels > max_pixels:
                    raise _WorkerError(
                        "limit", "PDF page has too many pixels to render safely."
                    )

                bitmap = page.render(
                    scale=scale,
                    may_draw_forms=forms_initialized,
                    fill_color=(255, 255, 255, 255),
                    draw_annots=True,
                    limit_image_cache=True,
                )
                if bitmap.width > max_side_pixels or bitmap.height > max_side_pixels:
                    raise _WorkerError(
                        "limit", "PDF page rendered larger than the safe size limit."
                    )
                if bitmap.width * bitmap.height > max_pixels:
                    raise _WorkerError(
                        "limit", "PDF page rendered more pixels than the safe limit."
                    )

                file_name = f"page-{output_index + 1:04d}.png"
                output_path = output_dir / file_name
                image = bitmap.to_pil()
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output_path, format="PNG", optimize=False)
                png_size = output_path.stat().st_size
                if png_size <= 0 or png_size > max_page_png_bytes:
                    raise _WorkerError("limit", "PDF page rendered output is too large.")
                total_png_bytes += png_size
                if total_png_bytes > max_total_png_bytes:
                    raise _WorkerError("limit", "PDF rendered output is too large.")
                files.append(file_name)
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        return {"files": files}
    finally:
        if forms_initialized:
            try:
                document.close_forms()
            except Exception:
                pass
        document.close()


def _write_result(output_dir: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_RESULT_BYTES:
        encoded = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "limit",
                    "message": "PDF processing output exceeded the safe size limit.",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
    temp_path = output_dir / "result.json.tmp"
    result_path = output_dir / "result.json"
    temp_path.write_bytes(encoded)
    os.replace(temp_path, result_path)


def main(argv: list[str]) -> int:
    _apply_resource_limits()
    output_dir: Path | None = None
    try:
        if len(argv) != 2:
            return 2
        request = _load_request(Path(argv[1]))
        input_path, output_dir = _validated_paths(request)
        operation = str(request.get("operation") or "")
        if operation == "inspect":
            result = _inspect(request, input_path)
        elif operation == "extract":
            result = _extract(request, input_path)
        elif operation == "render":
            result = _render(request, input_path, output_dir)
        else:
            raise _WorkerError("invalid_request", "Unknown PDF worker operation.")
        _write_result(output_dir, {"ok": True, "result": result})
        return 0
    except _WorkerError as exc:
        if output_dir is not None:
            _write_result(
                output_dir,
                {"ok": False, "error": {"code": exc.code, "message": str(exc)}},
            )
            return 1
        return 2
    except Exception:
        if output_dir is not None:
            _write_result(
                output_dir,
                {
                    "ok": False,
                    "error": {
                        "code": "processing_error",
                        "message": "PDF processing failed.",
                    },
                },
            )
            return 1
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
