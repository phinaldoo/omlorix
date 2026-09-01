"""OpenAI attachment conversion and upload handling.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "upload_files": (
        "_build_openai_responses_image_part",
        "_normalize_openai_image_detail",
        "base64",
        "build_file_metadata_text",
        "extract_text_file",
        "extract_text_from_file_info",
        "get_file_info",
        "logger",
        "mimetypes",
        "openai_audio_mime_types",
        "openai_document_mime_types",
        "openai_image_mime_types",
        "os",
        "render_pdf_pages_to_png_bytes",
        "should_convert_pdf_to_images",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "_build_openai_responses_image_part",
    "_normalize_openai_image_detail",
    "base64",
    "build_file_metadata_text",
    "extract_text_file",
    "extract_text_from_file_info",
    "get_file_info",
    "logger",
    "mimetypes",
    "openai_audio_mime_types",
    "openai_document_mime_types",
    "openai_image_mime_types",
    "os",
    "render_pdf_pages_to_png_bytes",
    "should_convert_pdf_to_images",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_upload_files(
    db,
    file_ids,
    user_id,
    counters=None,
    input_formats_allowed=None,
    image_detail=None,
):
    """
    Uploads local user files to OpenAI for use with the Responses API.
    Returns structured file parts ready for inclusion in `contents` or `input`.

    Args:
        file_ids: list of internal file IDs (from your app/db)
        user_id: current user
        counters: optional dict for tracking max uploads per file category

    Returns:
        dict {
            "parts": [ {"type": "input_file", "file_data": "file-xxxx"} ],
            "counters": updated counters
        }
    """

    if not file_ids:
        return {
            "parts": [],
            "counters": counters,
            "unsupported": False,
            "unsupported_file_ids": [],
        }

    normalized_image_detail = _normalize_openai_image_detail(image_detail)
    parts = []
    unsupported_detected = False
    unsupported_file_ids: set[str] = set()

    def _append_metadata_only_part(file_id_value, file_info_value=None):
        if not isinstance(file_info_value, dict):
            return
        parts.append(
            {
                "type": "input_text",
                "text": build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="metadata_only",
                    text_content_included=False,
                    provider_supported_image_mime_types=openai_image_mime_types,
                ),
            }
        )

    def _try_inline_text_part(file_id_value, file_name_value, file_info_value=None):
        metadata = {}
        text_content = None
        if isinstance(file_info_value, dict):
            text_content = extract_text_from_file_info(file_info_value)
            metadata = file_info_value.get("meta") or {}

        if not isinstance(text_content, str) or not text_content.strip():
            extracted = extract_text_file(db, str(file_id_value))
            if not extracted:
                return False
            text_content = extracted.get("content")
            if not isinstance(text_content, str) or not text_content.strip():
                return False
            metadata = extracted.get("metadata") or {}

        content_to_use = text_content
        truncated = False
        if len(content_to_use) > 200000:
            content_to_use = content_to_use[:200000]
            truncated = True
        meta_info = (
            metadata.get("meta") if isinstance(metadata.get("meta"), dict) else {}
        )
        original_name = (
            meta_info.get("original_filename") if isinstance(meta_info, dict) else None
        )
        metadata_name = (
            metadata.get("name")
            if isinstance(metadata, dict) and isinstance(metadata.get("name"), str)
            else (
                file_info_value.get("file_name")
                if isinstance(file_info_value, dict)
                and isinstance(file_info_value.get("file_name"), str)
                else None
            )
        )
        display_name = original_name or metadata_name or file_name_value
        prefix = f"{display_name}\n\n" if display_name else ""
        suffix = "\n\n...[truncated]" if truncated else ""
        parts.append(
            {
                "type": "input_text",
                "text": build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="text_extract",
                    text_content_included=True,
                    provider_supported_image_mime_types=openai_image_mime_types,
                ),
            }
        )
        parts.append(
            {
                "type": "input_text",
                "text": f"{prefix}{content_to_use}{suffix}",
            }
        )
        return True

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

    def _try_append_pdf_as_images(
        file_path_value: str, file_name_value: str, metadata_text: str | None = None
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
                "[upload_files] Failed PDF-to-image conversion for %s: %s",
                file_name_value,
                exc,
            )
            return False
        if not page_images:
            return False
        if metadata_text:
            parts.append(
                {
                    "type": "input_text",
                    "text": metadata_text,
                }
            )
        for image_bytes in page_images:
            encoded = base64.b64encode(image_bytes).decode("utf-8")
            parts.append(
                _build_openai_responses_image_part(
                    f"data:image/png;base64,{encoded}",
                    normalized_image_detail,
                )
            )
        if counters:
            image_counter = counters.get("image")
            if image_counter:
                image_counter["count"] += len(page_images)
        return True

    for file_id in file_ids:
        try:
            file_info = get_file_info(user_id, str(file_id))
            if not file_info:
                continue

            file_name = file_info.get("file_name")
            file_size = file_info.get("file_size", 0)
            file_type = file_info.get("file_type") or mimetypes.guess_type(file_name)[0]
            file_category = file_info.get("file_category")
            file_path = str(file_info.get("path") or "").strip()
            if not file_name or not file_path or not os.path.exists(file_path):
                logger.warning(f"[upload_files] File missing: {file_name}")
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue

            # Enforce 50MB limit
            if file_size > 50 * 1024 * 1024:
                logger.warning(f"[upload_files] Skipped {file_name} (too large)")
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue

            # MIME-type validation + counters
            allowed = False
            counter_key = None
            if file_category == "image" and file_type in openai_image_mime_types:
                if input_formats_allowed and "image" not in input_formats_allowed:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                allowed, counter_key = True, "image"
            elif (
                file_category == "document" and file_type in openai_document_mime_types
            ):
                if (
                    input_formats_allowed
                    and "pdf" not in input_formats_allowed
                    and "documents" not in input_formats_allowed
                ):
                    if _try_append_pdf_as_images(
                        file_path,
                        file_name,
                        build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=False,
                            model_context_representation="rendered_images",
                            text_content_included=False,
                            provider_supported_image_mime_types=openai_image_mime_types,
                        ),
                    ):
                        continue
                    document_counter = counters.get("document") if counters else None
                    if document_counter:
                        max_allowed = document_counter.get("max", -1)
                        if (
                            max_allowed >= 0
                            and document_counter["count"] >= max_allowed
                        ):
                            _append_metadata_only_part(file_id, file_info)
                            unsupported_detected = True
                            unsupported_file_ids.add(str(file_id))
                            continue
                    if _try_inline_text_part(file_id, file_name, file_info):
                        if document_counter:
                            document_counter["count"] += 1
                        continue
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                allowed, counter_key = True, "document"
            elif file_category == "audio" and file_type in openai_audio_mime_types:
                if input_formats_allowed and "audio" not in input_formats_allowed:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                allowed, counter_key = True, "audio"

            if not allowed:
                if file_category in {"image", "audio"}:
                    logger.info(f"[upload_files] Skipped unsupported type: {file_name}")
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue

                document_counter = (
                    counters.get("document")
                    if counters and file_category == "document"
                    else None
                )
                if document_counter:
                    max_allowed = document_counter.get("max", -1)
                    if max_allowed >= 0 and document_counter["count"] >= max_allowed:
                        _append_metadata_only_part(file_id, file_info)
                        unsupported_detected = True
                        unsupported_file_ids.add(str(file_id))
                        continue

                if (
                    file_category == "document"
                    and file_type in openai_document_mime_types
                    and _try_append_pdf_as_images(
                        file_path,
                        file_name,
                        build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=False,
                            model_context_representation="rendered_images",
                            text_content_included=False,
                            provider_supported_image_mime_types=openai_image_mime_types,
                        ),
                    )
                ):
                    continue

                if _try_inline_text_part(file_id, file_name, file_info):
                    if document_counter:
                        document_counter["count"] += 1
                    continue

                logger.info(f"[upload_files] Skipped unsupported type: {file_name}")
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue

            # Respect max counters
            if counters and counter_key:
                counter = counters.get(counter_key)
                if counter:
                    max_allowed = counter.get("max", -1)
                    if max_allowed >= 0 and counter["count"] >= max_allowed:
                        logger.info(
                            f"[upload_files] Max {counter_key} uploads reached."
                        )
                        _append_metadata_only_part(file_id, file_info)
                        unsupported_detected = True
                        unsupported_file_ids.add(str(file_id))
                        continue
                    counter["count"] += 1

            if file_category == "image":
                try:
                    with open(file_path, "rb") as fh:
                        encoded = base64.b64encode(fh.read()).decode("utf-8")
                    mime = file_type or "image/png"
                    parts.append(
                        {
                            "type": "input_text",
                            "text": build_file_metadata_text(
                                file_id,
                                file_info,
                                native_context_included=True,
                                model_context_representation="native_file",
                                text_content_included=False,
                                provider_supported_image_mime_types=openai_image_mime_types,
                            ),
                        }
                    )
                    parts.append(
                        _build_openai_responses_image_part(
                            f"data:{mime};base64,{encoded}",
                            normalized_image_detail,
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        f"[upload_files] Failed to embed image {file_name}: {exc}"
                    )
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                continue

            try:
                with open(file_path, "rb") as fh:
                    file_bytes = fh.read()
            except Exception as exc:
                logger.exception(f"[upload_files] Failed to read {file_name}: {exc}")
                continue

            encoded_data = base64.b64encode(file_bytes).decode("utf-8")

            if file_category == "audio":
                audio_format = "wav"
                if file_type and "/" in file_type:
                    audio_format = file_type.split("/")[-1]
                parts.append(
                    {
                        "type": "input_text",
                        "text": build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                            provider_supported_image_mime_types=openai_image_mime_types,
                        ),
                    }
                )
                parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": encoded_data,
                            "format": audio_format,
                        },
                    }
                )
                continue

            mime = file_type or "application/pdf"
            parts.append(
                {
                    "type": "input_text",
                    "text": build_file_metadata_text(
                        file_id,
                        file_info,
                        native_context_included=True,
                        model_context_representation="native_file",
                        text_content_included=False,
                        provider_supported_image_mime_types=openai_image_mime_types,
                    ),
                }
            )
            parts.append(
                {
                    "type": "input_file",
                    "filename": file_name,
                    "file_data": f"data:{mime};base64,{encoded_data}",
                }
            )

        except Exception as exc:
            logger.exception(f"[upload_files] Failed to upload {file_id}: {exc}")
            continue

    return {
        "parts": parts,
        "counters": counters,
        "unsupported": unsupported_detected,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
