"""Process-isolated ReportLab renderer for sanitized Canvas HTML."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


CANVAS_PDF_MAX_HTML_BYTES = 64 * 1024 * 1024
CANVAS_PDF_MAX_OUTPUT_BYTES = 50 * 1024 * 1024
CANVAS_PDF_RENDER_TIMEOUT_SECONDS = 20.0
_MAX_RESULT_BYTES = 64 * 1024


class ReportLabPdfError(ValueError):
    """Raised when the isolated Canvas PDF renderer fails safely."""


def _terminate_worker(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - backend production runs in Linux
            process.kill()
    except ProcessLookupError:
        return


def render_reportlab_pdf(*, story_html: str, title: str) -> bytes:
    """Render already-sanitized HTML to a bounded A4 PDF."""

    html_bytes = str(story_html or "").encode("utf-8")
    if len(html_bytes) > CANVAS_PDF_MAX_HTML_BYTES:
        raise ReportLabPdfError("Canvas PDF content exceeds the safe size limit.")

    worker_path = Path(__file__).with_name("_reportlab_worker.py").resolve()
    with tempfile.TemporaryDirectory(prefix="omlorix-reportlab-") as raw_temp_dir:
        temp_dir = Path(raw_temp_dir).resolve()
        html_path = temp_dir / "story.html"
        request_path = temp_dir / "request.json"
        result_path = temp_dir / "result.json"
        output_path = temp_dir / "canvas.pdf"
        html_path.write_bytes(html_bytes)
        request_path.write_text(
            json.dumps(
                {
                    "html_path": str(html_path),
                    "output_dir": str(temp_dir),
                    "title": str(title or "Canvas")[:255],
                    "max_html_bytes": CANVAS_PDF_MAX_HTML_BYTES,
                    "max_output_bytes": CANVAS_PDF_MAX_OUTPUT_BYTES,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
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
            raise ReportLabPdfError("Canvas PDF export support is unavailable.") from exc

        try:
            process.communicate(timeout=CANVAS_PDF_RENDER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _terminate_worker(process)
            process.communicate()
            raise ReportLabPdfError("Canvas PDF rendering exceeded the time limit.") from exc

        try:
            result_size = result_path.stat().st_size
        except OSError as exc:
            raise ReportLabPdfError("Canvas PDF rendering failed unexpectedly.") from exc
        if result_size <= 0 or result_size > _MAX_RESULT_BYTES:
            raise ReportLabPdfError("Canvas PDF renderer returned an invalid response.")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReportLabPdfError("Canvas PDF renderer returned an invalid response.") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            message = "Canvas PDF rendering failed."
            if isinstance(result, dict) and isinstance(result.get("error"), str):
                message = result["error"]
            raise ReportLabPdfError(message)

        if not output_path.is_file():
            raise ReportLabPdfError("Canvas PDF renderer returned no document.")
        output_size = output_path.stat().st_size
        if output_size <= 0 or output_size > CANVAS_PDF_MAX_OUTPUT_BYTES:
            raise ReportLabPdfError("Canvas PDF output exceeds the safe size limit.")
        content = output_path.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise ReportLabPdfError("Canvas PDF renderer returned invalid data.")
        return content
