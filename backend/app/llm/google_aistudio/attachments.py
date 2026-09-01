"""Google AI Studio attachment upload and conversion handling.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "upload_files": (
        "_build_aistudio_file_part",
        "build_file_metadata_text",
        "google_ai_studio_audio_mime_types",
        "google_ai_studio_document_mime_types",
        "google_ai_studio_image_mime_types",
        "google_ai_studio_video_mime_types",
        "logger",
        "os",
        "tempfile",
        "types",
        "wait_for_aistudio_file_active",
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
    "_build_aistudio_file_part",
    "build_file_metadata_text",
    "google_ai_studio_audio_mime_types",
    "google_ai_studio_document_mime_types",
    "google_ai_studio_image_mime_types",
    "google_ai_studio_video_mime_types",
    "logger",
    "os",
    "tempfile",
    "types",
    "wait_for_aistudio_file_active",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_upload_files(
    db,
    client,
    file_ids,
    user_id,
    uploaded_cleanup,
    counters: dict | None = None,
    input_formats_allowed=None,
    video_metadata=None,
    file_active_deadline_monotonic: float | None = None,
):
    from app.files.schemas import TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
    from app.files.utils import (
        extract_text_file,
        extract_text_from_file_info,
        get_file_info,
        normalize_file_mime_type,
    )

    if not file_ids:
        return {
            "parts": [],
            "uploaded_cleanup": uploaded_cleanup,
            "counters": counters,
            "unsupported": False,
            "unsupported_file_ids": [],
        }
    parts = []
    unsupported_detected = False
    unsupported_file_ids: set[str] = set()

    def _append_metadata_only_part(file_id_value, file_info_value=None):
        if not isinstance(file_info_value, dict):
            return
        parts.append(
            types.Part(
                text=build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="metadata_only",
                    text_content_included=False,
                    provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                )
            )
        )

    def _try_append_text_part(file_id_value, file_name_value, file_info_value=None):
        text_content = None
        extracted_metadata = None
        if isinstance(file_info_value, dict):
            text_content = extract_text_from_file_info(file_info_value)

        if not isinstance(text_content, str) or not text_content.strip():
            try:
                extracted = extract_text_file(db, str(file_id_value))
            except Exception:
                return False
            if not extracted:
                return False
            text_content = extracted.get("content")
            if not isinstance(text_content, str) or not text_content.strip():
                return False
            extracted_metadata = (
                extracted.get("metadata") if isinstance(extracted, dict) else None
            )

        truncated = False
        if len(text_content) > 200000:
            text_content = text_content[:200000]
            truncated = True
        metadata = None
        if isinstance(file_info_value, dict):
            metadata = file_info_value.get("meta")
        if not isinstance(metadata, dict) and isinstance(extracted_metadata, dict):
            metadata = (
                extracted_metadata.get("meta")
                if isinstance(extracted_metadata.get("meta"), dict)
                else extracted_metadata
            )
        meta_info = metadata if isinstance(metadata, dict) else {}
        original_name = (
            meta_info.get("original_filename") if isinstance(meta_info, dict) else None
        )
        metadata_name = None
        if isinstance(extracted_metadata, dict) and isinstance(
            extracted_metadata.get("name"), str
        ):
            metadata_name = extracted_metadata.get("name")
        if metadata_name is None and isinstance(file_info_value, dict):
            metadata_name = file_info_value.get("file_name")
        display_name = original_name or metadata_name or file_name_value
        prefix = f"{display_name}\n\n" if display_name else ""
        suffix = "\n\n...[truncated]" if truncated else ""
        parts.append(
            types.Part(
                text=build_file_metadata_text(
                    file_id_value,
                    file_info_value,
                    native_context_included=False,
                    model_context_representation="text_extract",
                    text_content_included=True,
                    provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                )
            )
        )
        parts.append(types.Part(text=f"{prefix}{text_content}{suffix}"))
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
        file_path_value: str,
        file_name_value: str,
        metadata_text: str | None = None,
    ) -> bool:
        from app.llm.pdf_utils import (
            render_pdf_pages_to_png_bytes,
            should_convert_pdf_to_images,
        )

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
                "[Google AI Studio] Failed PDF-to-image conversion for %s: %s",
                file_name_value,
                exc,
            )
            return False
        if not page_images:
            return False

        uploaded_parts: list[types.Part] = []
        created_cleanup: list[str] = []
        completed = True
        for image_bytes in page_images:
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".png"
                ) as temp_file:
                    temp_file.write(image_bytes)
                    temp_path = temp_file.name
                uploaded = client.files.upload(file=temp_path)
            except Exception as exc:
                logger.warning(
                    "[Google AI Studio] Failed to upload rendered PDF page for %s: %s",
                    file_name_value,
                    exc,
                )
                completed = False
                break
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
            if uploaded_cleanup is not None:
                if name := getattr(uploaded, "name", None):
                    created_cleanup.append(name)
            try:
                uploaded = wait_for_aistudio_file_active(
                    client,
                    uploaded,
                    deadline_monotonic=file_active_deadline_monotonic,
                )
            except Exception as exc:
                logger.warning(
                    "[Google AI Studio] Uploaded PDF page for %s did not become ACTIVE: %s",
                    file_name_value,
                    exc,
                )
                completed = False
                break
            uri = getattr(uploaded, "uri", None)
            if not uri:
                continue
            uploaded_parts.append(
                _build_aistudio_file_part(file_uri=uri, mime_type="image/png")
            )

        if not uploaded_parts:
            return False
        if uploaded_cleanup is not None:
            uploaded_cleanup.extend(created_cleanup)
        if metadata_text:
            parts.append(types.Part(text=metadata_text))
        parts.extend(uploaded_parts)
        if counters:
            image_counter = counters.get("image")
            if image_counter:
                image_counter["count"] += len(uploaded_parts)
        return completed

    for file_id in file_ids:
        file_info = get_file_info(user_id, str(file_id))
        if not file_info:
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        file_name = file_info.get("file_name")
        file_size = file_info.get("file_size")
        file_type = normalize_file_mime_type(file_info.get("file_type"))
        file_category = file_info.get("file_category")
        file_path = str(file_info.get("path") or "").strip()
        # File size must be < 50MB
        if file_size > 50 * 1024 * 1024:
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        if not file_path or not os.path.exists(file_path):
            if _try_append_text_part(file_id, file_name, file_info):
                continue
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue

        if file_category == "image":
            if file_type not in google_ai_studio_image_mime_types:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            else:
                if input_formats_allowed and "image" not in input_formats_allowed:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if counters:
                    image_counter = counters.get("image")
                    if image_counter:
                        if (
                            image_counter.get("max", -1) >= 0
                            and image_counter["count"] >= image_counter["max"]
                        ):
                            _append_metadata_only_part(file_id, file_info)
                            unsupported_detected = True
                            unsupported_file_ids.add(str(file_id))
                            continue
                        image_counter["count"] += 1
        elif file_category == "document":
            document_counter = counters.get("document") if counters else None

            # HTML and SVG are conversation source, not executable/native
            # provider files. Supplying their markup as a text part is both
            # consistent across models and independent of native document
            # capability declarations.
            if file_type in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES:
                if document_counter:
                    if (
                        document_counter.get("max", -1) >= 0
                        and document_counter["count"] >= document_counter["max"]
                    ):
                        _append_metadata_only_part(file_id, file_info)
                        unsupported_detected = True
                        unsupported_file_ids.add(str(file_id))
                        continue
                if _try_append_text_part(file_id, file_name, file_info):
                    if document_counter:
                        document_counter["count"] += 1
                    continue
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue

            if file_type not in google_ai_studio_document_mime_types:
                if document_counter:
                    if (
                        document_counter.get("max", -1) >= 0
                        and document_counter["count"] >= document_counter["max"]
                    ):
                        _append_metadata_only_part(file_id, file_info)
                        unsupported_detected = True
                        unsupported_file_ids.add(str(file_id))
                        continue
                if _try_append_text_part(file_id, file_name, file_info):
                    if document_counter:
                        document_counter["count"] += 1
                    continue
                if (
                    input_formats_allowed
                    and "documents" not in input_formats_allowed
                    and "pdf" not in input_formats_allowed
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
                            provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                        ),
                    ):
                        continue
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            else:
                if (
                    input_formats_allowed
                    and "documents" not in input_formats_allowed
                    and "pdf" not in input_formats_allowed
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
                            provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                        ),
                    ):
                        continue
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if document_counter:
                    if (
                        document_counter.get("max", -1) >= 0
                        and document_counter["count"] >= document_counter["max"]
                    ):
                        _append_metadata_only_part(file_id, file_info)
                        unsupported_detected = True
                        unsupported_file_ids.add(str(file_id))
                        continue
                    document_counter["count"] += 1
                try:
                    uploaded = client.files.upload(file=file_path)
                except Exception:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if uploaded_cleanup is not None:
                    if name := getattr(uploaded, "name", None):
                        uploaded_cleanup.append(name)
                try:
                    uploaded = wait_for_aistudio_file_active(
                        client,
                        uploaded,
                        deadline_monotonic=file_active_deadline_monotonic,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Google AI Studio] Uploaded file %s did not become ACTIVE: %s",
                        file_name,
                        exc,
                    )
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if not (uri := getattr(uploaded, "uri", None)):
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                parts.append(
                    types.Part(
                        text=build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                            provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                        )
                    )
                )
                parts.append(
                    _build_aistudio_file_part(file_uri=uri, mime_type=file_type)
                )
                continue
        elif file_type in google_ai_studio_document_mime_types:
            document_counter = counters.get("document") if counters else None
            if (
                input_formats_allowed
                and "documents" not in input_formats_allowed
                and "pdf" not in input_formats_allowed
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
                        provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                    ),
                ):
                    continue
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if document_counter:
                if (
                    document_counter.get("max", -1) >= 0
                    and document_counter["count"] >= document_counter["max"]
                ):
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                document_counter["count"] += 1
            try:
                uploaded = client.files.upload(file=file_path)
            except Exception:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if uploaded_cleanup is not None:
                if name := getattr(uploaded, "name", None):
                    uploaded_cleanup.append(name)
            try:
                uploaded = wait_for_aistudio_file_active(
                    client,
                    uploaded,
                    deadline_monotonic=file_active_deadline_monotonic,
                )
            except Exception as exc:
                logger.warning(
                    "[Google AI Studio] Uploaded file %s did not become ACTIVE: %s",
                    file_name,
                    exc,
                )
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            if not (uri := getattr(uploaded, "uri", None)):
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            parts.append(
                types.Part(
                    text=build_file_metadata_text(
                        file_id,
                        file_info,
                        native_context_included=True,
                        model_context_representation="native_file",
                        text_content_included=False,
                        provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                    )
                )
            )
            parts.append(_build_aistudio_file_part(file_uri=uri, mime_type=file_type))
            continue
        elif file_category == "audio":
            if file_type not in google_ai_studio_audio_mime_types:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            else:
                if input_formats_allowed and "audio" not in input_formats_allowed:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if counters:
                    audio_counter = counters.get("audio")
                    if audio_counter:
                        if (
                            audio_counter.get("max", -1) >= 0
                            and audio_counter["count"] >= audio_counter["max"]
                        ):
                            _append_metadata_only_part(file_id, file_info)
                            unsupported_detected = True
                            unsupported_file_ids.add(str(file_id))
                            continue
                        audio_counter["count"] += 1
        elif file_category == "video":
            if file_type not in google_ai_studio_video_mime_types:
                _append_metadata_only_part(file_id, file_info)
                unsupported_detected = True
                unsupported_file_ids.add(str(file_id))
                continue
            else:
                if input_formats_allowed and "video" not in input_formats_allowed:
                    _append_metadata_only_part(file_id, file_info)
                    unsupported_detected = True
                    unsupported_file_ids.add(str(file_id))
                    continue
                if counters:
                    video_counter = counters.get("video")
                    if video_counter:
                        if (
                            video_counter.get("max", -1) >= 0
                            and video_counter["count"] >= video_counter["max"]
                        ):
                            _append_metadata_only_part(file_id, file_info)
                            unsupported_detected = True
                            unsupported_file_ids.add(str(file_id))
                            continue
                        video_counter["count"] += 1
        else:
            if _try_append_text_part(file_id, file_name, file_info):
                continue
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        try:
            uploaded = client.files.upload(file=file_path)
        except Exception:
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        if uploaded_cleanup is not None:
            if name := getattr(uploaded, "name", None):
                uploaded_cleanup.append(name)
        try:
            uploaded = wait_for_aistudio_file_active(
                client,
                uploaded,
                deadline_monotonic=file_active_deadline_monotonic,
            )
        except Exception as exc:
            logger.warning(
                "[Google AI Studio] Uploaded file %s did not become ACTIVE: %s",
                file_name,
                exc,
            )
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        if not (uri := getattr(uploaded, "uri", None)):
            _append_metadata_only_part(file_id, file_info)
            unsupported_detected = True
            unsupported_file_ids.add(str(file_id))
            continue
        parts.append(
            types.Part(
                text=build_file_metadata_text(
                    file_id,
                    file_info,
                    native_context_included=True,
                    model_context_representation="native_file",
                    text_content_included=False,
                    provider_supported_image_mime_types=google_ai_studio_image_mime_types,
                )
            )
        )
        parts.append(
            _build_aistudio_file_part(
                file_uri=uri,
                mime_type=file_type,
                video_metadata=video_metadata if file_category == "video" else None,
            )
        )
    return {
        "parts": parts,
        "uploaded_cleanup": uploaded_cleanup,
        "counters": counters,
        "unsupported": unsupported_detected,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
