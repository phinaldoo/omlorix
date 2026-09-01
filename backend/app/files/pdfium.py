"""Process-isolated access to the permissively licensed PDFium renderer.

PDF parsers process attacker-controlled binary input and PDFium is not safe to
use concurrently from multiple threads.  Every operation therefore runs in a
short-lived worker with CPU, address-space, file-size, output-size, and wall
clock limits.  The worker only returns JSON metadata and inert PNG files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from typing import Any


PDFIUM_MAX_RESULT_JSON_BYTES = 32 * 1024 * 1024
PDFIUM_DEFAULT_TIMEOUT_SECONDS = 10.0


class PdfiumProcessingError(ValueError):
    """Raised when PDFium cannot safely process the supplied document."""


class PdfiumPasswordError(PdfiumProcessingError):
    """Raised when a document requires a password."""


class PdfiumPageNotFoundError(PdfiumProcessingError):
    """Raised when a requested 1-based page number does not exist."""


class PdfiumUnavailableError(RuntimeError):
    """Raised when the PDFium runtime is not installed or cannot start."""


_ERROR_TYPES = {
    "password": PdfiumPasswordError,
    "page_not_found": PdfiumPageNotFoundError,
    "unavailable": PdfiumUnavailableError,
}


def _terminate_worker(process: subprocess.Popen) -> None:
    """Kill a timed-out worker and its process group where supported."""

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - the backend normally runs in Linux
            process.kill()
    except ProcessLookupError:
        return


def _read_worker_result(result_path: Path) -> dict[str, Any]:
    """Read one size-bounded worker response from the private temp directory."""

    try:
        result_size = result_path.stat().st_size
    except OSError as exc:
        raise PdfiumProcessingError("PDF processing failed unexpectedly.") from exc
    if result_size <= 0 or result_size > PDFIUM_MAX_RESULT_JSON_BYTES:
        raise PdfiumProcessingError("PDF processing returned an invalid response.")
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PdfiumProcessingError("PDF processing returned an invalid response.") from exc
    if not isinstance(payload, dict):
        raise PdfiumProcessingError("PDF processing returned an invalid response.")
    return payload


def _run_pdfium_worker(
    request: dict[str, Any],
    *,
    timeout_seconds: float = PDFIUM_DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], Path, tempfile.TemporaryDirectory]:
    """Run one worker operation and retain its output directory for the caller."""

    if timeout_seconds <= 0:
        raise ValueError("PDF processing timeout must be greater than zero.")

    temp_dir = tempfile.TemporaryDirectory(prefix="omlorix-pdfium-")
    output_dir = Path(temp_dir.name).resolve()
    request_path = output_dir / "request.json"
    result_path = output_dir / "result.json"
    worker_path = Path(__file__).with_name("_pdfium_worker.py").resolve()

    request_payload = dict(request)
    request_payload["output_dir"] = str(output_dir)
    request_path.write_text(
        json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(worker_path), str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        temp_dir.cleanup()
        raise PdfiumUnavailableError("PDF processing support is unavailable.") from exc

    try:
        process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_worker(process)
        process.communicate()
        temp_dir.cleanup()
        raise PdfiumProcessingError("PDF processing exceeded the time limit.") from exc

    if not result_path.is_file():
        temp_dir.cleanup()
        message = "PDF processing failed unexpectedly."
        if process.returncode:
            message = f"{message} Worker exit code: {process.returncode}."
        raise PdfiumProcessingError(message)

    try:
        payload = _read_worker_result(result_path)
    except Exception:
        temp_dir.cleanup()
        raise

    if payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "processing_error")
        message = str(error.get("message") or "PDF processing failed.")
        exception_type = _ERROR_TYPES.get(code, PdfiumProcessingError)
        temp_dir.cleanup()
        raise exception_type(message)

    result = payload.get("result")
    if not isinstance(result, dict):
        temp_dir.cleanup()
        raise PdfiumProcessingError("PDF processing returned an invalid response.")
    return result, output_dir, temp_dir


def _resolved_pdf_path(file_path: str | Path) -> Path:
    """Resolve an existing regular file before passing it to the worker."""

    try:
        path = Path(file_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PdfiumProcessingError("PDF file could not be opened.") from exc
    if not path.is_file():
        raise PdfiumProcessingError("PDF file could not be opened.")
    return path


def inspect_pdf_document(
    file_path: str | Path,
    *,
    max_file_bytes: int,
    max_pages: int,
    timeout_seconds: float = PDFIUM_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return page dimensions for a bounded unencrypted PDF."""

    result, _output_dir, temp_dir = _run_pdfium_worker(
        {
            "operation": "inspect",
            "input_path": str(_resolved_pdf_path(file_path)),
            "max_file_bytes": int(max_file_bytes),
            "max_pages": int(max_pages),
        },
        timeout_seconds=timeout_seconds,
    )
    temp_dir.cleanup()
    return result


def extract_pdf_page_words(
    file_path: str | Path,
    *,
    page_number: int,
    max_file_bytes: int,
    max_pages: int,
    max_words: int,
    max_text_chars: int,
    timeout_seconds: float = PDFIUM_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return positioned word boxes for one 1-based page."""

    result, _output_dir, temp_dir = _run_pdfium_worker(
        {
            "operation": "extract",
            "input_path": str(_resolved_pdf_path(file_path)),
            "page_number": int(page_number),
            "max_file_bytes": int(max_file_bytes),
            "max_pages": int(max_pages),
            "max_words": int(max_words),
            "max_text_chars": int(max_text_chars),
        },
        timeout_seconds=timeout_seconds,
    )
    temp_dir.cleanup()
    return result


def render_pdf_pages(
    file_path: str | Path,
    *,
    page_limit: int,
    scale: float,
    max_file_bytes: int,
    max_document_pages: int,
    max_side_pixels: int,
    max_pixels: int,
    max_page_png_bytes: int,
    max_total_png_bytes: int,
    timeout_seconds: float = PDFIUM_DEFAULT_TIMEOUT_SECONDS,
) -> list[bytes]:
    """Render the first bounded set of pages to inert PNG byte strings."""

    result, output_dir, temp_dir = _run_pdfium_worker(
        {
            "operation": "render",
            "input_path": str(_resolved_pdf_path(file_path)),
            "page_limit": int(page_limit),
            "scale": float(scale),
            "max_file_bytes": int(max_file_bytes),
            "max_pages": int(max_document_pages),
            "max_side_pixels": int(max_side_pixels),
            "max_pixels": int(max_pixels),
            "max_page_png_bytes": int(max_page_png_bytes),
            "max_total_png_bytes": int(max_total_png_bytes),
        },
        timeout_seconds=timeout_seconds,
    )

    files = result.get("files")
    if not isinstance(files, list):
        temp_dir.cleanup()
        raise PdfiumProcessingError("PDF processing returned an invalid response.")

    rendered: list[bytes] = []
    total_bytes = 0
    try:
        for index, raw_name in enumerate(files):
            expected_name = f"page-{index + 1:04d}.png"
            if raw_name != expected_name:
                raise PdfiumProcessingError("PDF processing returned an invalid response.")
            png_path = (output_dir / expected_name).resolve()
            if png_path.parent != output_dir or not png_path.is_file():
                raise PdfiumProcessingError("PDF processing returned an invalid response.")
            png_size = png_path.stat().st_size
            if png_size <= 0 or png_size > max_page_png_bytes:
                raise PdfiumProcessingError("PDF page rendered output is too large.")
            total_bytes += png_size
            if total_bytes > max_total_png_bytes:
                raise PdfiumProcessingError("PDF rendered output is too large.")
            png_bytes = png_path.read_bytes()
            if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise PdfiumProcessingError("PDF renderer returned invalid image data.")
            rendered.append(png_bytes)
    finally:
        temp_dir.cleanup()
    return rendered


def render_pdf_page(
    file_path: str | Path,
    *,
    page_number: int,
    scale: float,
    max_file_bytes: int,
    max_document_pages: int,
    max_side_pixels: int,
    max_pixels: int,
    max_page_png_bytes: int,
    timeout_seconds: float = PDFIUM_DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Render one 1-based page without processing the preceding pages."""

    result, output_dir, temp_dir = _run_pdfium_worker(
        {
            "operation": "render",
            "input_path": str(_resolved_pdf_path(file_path)),
            "page_numbers": [int(page_number)],
            "scale": float(scale),
            "max_file_bytes": int(max_file_bytes),
            "max_pages": int(max_document_pages),
            "max_side_pixels": int(max_side_pixels),
            "max_pixels": int(max_pixels),
            "max_page_png_bytes": int(max_page_png_bytes),
            "max_total_png_bytes": int(max_page_png_bytes),
        },
        timeout_seconds=timeout_seconds,
    )
    files = result.get("files")
    try:
        if files != ["page-0001.png"]:
            raise PdfiumProcessingError("PDF processing returned an invalid response.")
        png_path = (output_dir / "page-0001.png").resolve()
        if png_path.parent != output_dir or not png_path.is_file():
            raise PdfiumProcessingError("PDF processing returned an invalid response.")
        png_size = png_path.stat().st_size
        if png_size <= 0 or png_size > max_page_png_bytes:
            raise PdfiumProcessingError("PDF page rendered output is too large.")
        png_bytes = png_path.read_bytes()
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PdfiumProcessingError("PDF renderer returned invalid image data.")
        return png_bytes
    finally:
        temp_dir.cleanup()
