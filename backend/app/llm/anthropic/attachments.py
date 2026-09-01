"""Anthropic attachment validation and Messages API content conversion."""

import base64
import logging
from pathlib import Path

from app.files.utils import (
    extract_text_file,
    extract_text_from_file_info,
    get_file_info,
)
from app.llm.anthropic.schemas import (
    anthropic_document_mime_types,
    anthropic_image_mime_types,
)
from app.llm.helper import build_file_metadata_text
from app.llm.pdf_utils import (
    render_pdf_pages_to_png_bytes,
    should_convert_pdf_to_images,
)

logger = logging.getLogger(__name__)

# Anthropic caps Messages API requests at 32 MiB. Base64 expands binary PDFs
# by roughly one third, so native document embedding stays below that ceiling.
MAX_ANTHROPIC_NATIVE_DOCUMENT_BYTES = 20 * 1024 * 1024
ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024


def upload_files(
    db,
    file_ids,
    user_id,
    input_formats_allowed=None,
    counters: dict | None = None,
):
    """Upload files."""
    if not file_ids:
        return {
            "parts": [],
            "uploaded_cleanup": [],
            "unsupported": False,
            "counters": counters,
            "unsupported_file_ids": [],
        }

    parts = []
    unsupported_detected = False
    unsupported_file_ids: set[str] = set()

    def _append_metadata_only_part(file_id_value, file_info_value=None):
        if not isinstance(file_info_value, dict):
            return
        parts.append(
            {
                "type": "text",
                "text": build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="metadata_only",
                    text_content_included=False,
                    provider_supported_image_mime_types=anthropic_image_mime_types,
                ),
            }
        )

    def _append_text_extract_parts(
        file_id_value, file_info_value, text_content_value: str
    ) -> None:
        content_to_use = text_content_value
        if len(content_to_use) > 200000:
            content_to_use = content_to_use[:200000] + "\n\n...[truncated]"
        parts.append(
            {
                "type": "text",
                "text": build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="text_extract",
                    text_content_included=True,
                    provider_supported_image_mime_types=anthropic_image_mime_types,
                ),
            }
        )
        parts.append(
            {
                "type": "text",
                "text": content_to_use,
            }
        )

    def _has_capacity(counter_key: str) -> bool:
        if not counters:
            return True
        counter = counters.get(counter_key)
        if not counter:
            return True
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return True
        return counter["count"] < max_allowed

    def _increment(counter_key: str):
        if not counters:
            return
        counter = counters.get(counter_key)
        if counter:
            counter["count"] += 1

    def _remaining_capacity(counter_key: str) -> int | None:
        if not counters:
            return None
        counter = counters.get(counter_key)
        if not counter:
            return None
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return None
        return max(max_allowed - counter["count"], 0)

    def _get_file_size(file_path_value: Path, file_info_value: dict) -> int | None:
        raw_size = file_info_value.get("file_size")
        try:
            if raw_size is not None:
                return int(raw_size)
        except (TypeError, ValueError):
            pass
        try:
            return file_path_value.stat().st_size
        except OSError:
            return None

    def _append_text_extract_or_metadata(file_id_value, file_info_value) -> bool:
        text_content = extract_text_from_file_info(file_info_value)
        if not text_content:
            extracted = extract_text_file(db, str(file_id_value))
            text_content = extracted.get("content") if extracted else None
        if text_content:
            _append_text_extract_parts(file_id_value, file_info_value, text_content)
            _increment("document")
            return True
        _append_metadata_only_part(file_id_value, file_info_value)
        return False

    def _coerce_file_size(value) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _is_over_file_size_limit(file_info_value, file_path_value: Path) -> bool:
        recorded_size = None
        if isinstance(file_info_value, dict):
            recorded_size = _coerce_file_size(file_info_value.get("file_size"))
            meta = (
                file_info_value.get("meta")
                if isinstance(file_info_value.get("meta"), dict)
                else {}
            )
            if recorded_size is None:
                recorded_size = _coerce_file_size(meta.get("file_size"))
        if (
            recorded_size is not None
            and recorded_size > ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES
        ):
            return True
        try:
            actual_size = int(file_path_value.stat().st_size)
        except OSError:
            actual_size = None
        return (
            actual_size is not None and actual_size > ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES
        )

    def _try_append_pdf_as_images(
        file_path_value: Path,
        file_name_value: str,
        metadata_text: str | None = None,
    ) -> bool:
        if not should_convert_pdf_to_images(input_formats_allowed):
            return False
        remaining_images = _remaining_capacity("image")
        if remaining_images == 0:
            return False
        try:
            page_images = render_pdf_pages_to_png_bytes(
                file_path_value,
                max_pages=remaining_images,
            )
        except Exception as exc:
            logger.warning(
                "[Anthropic] Failed PDF-to-image conversion for %s: %s",
                file_name_value,
                exc,
            )
            return False
        if not page_images:
            return False
        if metadata_text:
            parts.append(
                {
                    "type": "text",
                    "text": metadata_text,
                }
            )
        for image_bytes in page_images:
            image_data = base64.b64encode(image_bytes).decode("utf-8")
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_data,
                    },
                }
            )
        if counters:
            image_counter = counters.get("image")
            if image_counter:
                image_counter["count"] += len(page_images)
        return True

    for file_id in file_ids:
        file_info = get_file_info(user_id, str(file_id))
        if not file_info:
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue

        file_path_str = file_info.get("path")
        if not file_path_str:
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue

        file_path = Path(file_path_str)
        if not file_path.exists():
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        if _is_over_file_size_limit(file_info, file_path):
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue

        file_type = file_info.get("file_type")

        # Check for image
        if file_type in anthropic_image_mime_types:
            if input_formats_allowed and "image" not in input_formats_allowed:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if not _has_capacity("image"):
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            try:
                with open(file_path, "rb") as f:
                    image_data = base64.b64encode(f.read()).decode("utf-8")
                parts.append(
                    {
                        "type": "text",
                        "text": build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                            provider_supported_image_mime_types=anthropic_image_mime_types,
                        ),
                    }
                )
                parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": file_type,
                            "data": image_data,
                        },
                    }
                )
                _increment("image")
            except Exception:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
        elif file_info.get("file_category") == "document":
            if (
                input_formats_allowed
                and "pdf" not in input_formats_allowed
                and "documents" not in input_formats_allowed
            ):
                if file_type == "application/pdf" and _try_append_pdf_as_images(
                    file_path,
                    file_info.get("file_name") or str(file_id),
                    build_file_metadata_text(
                        file_id,
                        file_info,
                        native_context_included=False,
                        model_context_representation="rendered_images",
                        text_content_included=False,
                        provider_supported_image_mime_types=anthropic_image_mime_types,
                    ),
                ):
                    continue
                text_content = extract_text_from_file_info(file_info)
                if not text_content:
                    extracted = extract_text_file(db, str(file_id))
                    text_content = extracted.get("content") if extracted else None
                if text_content:
                    _append_text_extract_parts(file_id, file_info, text_content)
                    _increment("document")
                    continue
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if file_type not in anthropic_document_mime_types:
                if not _has_capacity("document"):
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                text_content = extract_text_from_file_info(file_info)
                if not text_content:
                    extracted = extract_text_file(db, str(file_id))
                    text_content = extracted.get("content") if extracted else None
                if text_content:
                    _append_text_extract_parts(file_id, file_info, text_content)
                    _increment("document")
                    continue
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if not _has_capacity("document"):
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            document_size = _get_file_size(file_path, file_info)
            if (
                document_size is not None
                and document_size > MAX_ANTHROPIC_NATIVE_DOCUMENT_BYTES
            ):
                if not _append_text_extract_or_metadata(file_id, file_info):
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                continue
            try:
                with open(file_path, "rb") as f:
                    document_data = base64.b64encode(f.read()).decode("utf-8")
                parts.append(
                    {
                        "type": "text",
                        "text": build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                            provider_supported_image_mime_types=anthropic_image_mime_types,
                        ),
                    }
                )
                parts.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": file_type or "application/octet-stream",
                            "data": document_data,
                        },
                    }
                )
                _increment("document")
            except Exception:
                text_content = extract_text_from_file_info(file_info)
                if not text_content:
                    extracted = extract_text_file(db, str(file_id))
                    text_content = extracted.get("content") if extracted else None
                if text_content:
                    _append_text_extract_parts(file_id, file_info, text_content)
                    _increment("document")
                else:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
        else:
            if not _has_capacity("document"):
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            text_content = extract_text_from_file_info(file_info)
            if not text_content:
                extracted = extract_text_file(db, str(file_id))
                text_content = extracted.get("content") if extracted else None
            if text_content:
                _append_text_extract_parts(file_id, file_info, text_content)
                _increment("document")
            else:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))

    return {
        "parts": parts,
        "uploaded_cleanup": [],
        "unsupported": unsupported_detected,
        "counters": counters,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
