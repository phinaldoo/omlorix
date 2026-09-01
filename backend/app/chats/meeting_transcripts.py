from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
import mimetypes
import os
import resource
import shutil
import subprocess
import tempfile
from typing import Any

import anyio
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.chats.models import create_chat, create_chat_message, get_chat
from app.chats.utils import _build_file_lookup_for_user, _serialize_chat_rows
from app.files.utils import persist_generated_file_bytes
from app.groups.init import get_user_group_setting_value
from app.users.roles import is_admin_role
from app.llm.speech import (
    get_transcription_runtime_for_provider,
    snapshot_transcription_provider,
    transcribe_audio_bytes_for_provider,
)
from app.llm.transcription_errors import (
    TRANSCRIPTION_NOT_ENABLED_ERROR_CODE,
    build_transcription_error_detail,
)
from app.projects.models import get_project_with_access
from app.settings.models import get_settings_page
from app.utils.blocking_io import run_blocking_io
from app.utils.db import release_db_session_before_long_wait


_KNOWN_AUDIO_EXTENSIONS = {
    "aac",
    "aiff",
    "flac",
    "m4a",
    "mid",
    "midi",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "ogg",
    "opus",
    "wav",
    "webm",
}
_KNOWN_VIDEO_EXTENSIONS = {
    "avi",
    "m4v",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "ogv",
    "webm",
}
_NORMALIZED_AUDIO_EXTENSION = "mp3"
_NORMALIZED_AUDIO_MIME = "audio/mpeg"
_NORMALIZED_AUDIO_BITRATE = "32k"
_NORMALIZED_AUDIO_SAMPLE_RATE = "16000"
_MAX_MEETING_RETENTION_DAYS = 3650
_FFMPEG_TIMEOUT_SECONDS = 120
_FFMPEG_LOG_EXCERPT_BYTES = 4096
_FFMPEG_OUTPUT_HEADROOM_BYTES = 1024 * 1024
_MEETING_LEGAL_BASIS_LABELS = {
    "consent": "Consent",
    "contract": "Contract",
    "legitimate_interest": "Legitimate interest",
    "legal_obligation": "Legal obligation",
    "public_task": "Public task",
    "other": "Other",
}


@dataclass(frozen=True)
class MeetingTranscriptGovernance:
    consent_confirmed: bool
    consent_confirmed_at: str
    legal_basis: str
    legal_basis_label: str
    legal_basis_details: str
    retention_days: int
    retention_expires_at: str


def _parse_browser_datetime(browser_date_iso: str | None) -> datetime | None:
    """Parse a browser-provided ISO datetime string into a timezone-aware datetime object."""
    raw_value = str(browser_date_iso or "").strip()
    if not raw_value:
        return None
    normalized = raw_value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_browser_date_label(browser_date_iso: str | None, browser_date_label: str | None) -> tuple[str, str, str | None]:
    """Format a browser date into a human-readable label, date slug, and normalized ISO string."""
    cleaned_label = " ".join(str(browser_date_label or "").strip().split())
    parsed = _parse_browser_datetime(browser_date_iso)
    if cleaned_label:
        title_label = cleaned_label[:80]
    elif parsed is not None:
        title_label = parsed.strftime("%B %d, %Y").replace(" 0", " ")
    else:
        now = datetime.now(timezone.utc)
        title_label = now.strftime("%B %d, %Y").replace(" 0", " ")
        parsed = now

    date_slug = parsed.strftime("%Y-%m-%d") if parsed is not None else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    normalized_iso = parsed.isoformat() if parsed is not None else None
    return title_label, date_slug, normalized_iso


def _coerce_meeting_consent_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _validate_meeting_transcript_governance(
    *,
    consent_confirmed: Any,
    legal_basis: str | None,
    legal_basis_details: str | None,
    retention_days: Any,
) -> MeetingTranscriptGovernance:
    if not _coerce_meeting_consent_value(consent_confirmed):
        raise HTTPException(
            status_code=400,
            detail="Confirm participant notice and consent before transcribing a meeting.",
        )

    normalized_legal_basis = str(legal_basis or "").strip().lower()
    legal_basis_label = _MEETING_LEGAL_BASIS_LABELS.get(normalized_legal_basis)
    if not legal_basis_label:
        raise HTTPException(status_code=400, detail="Choose a legal basis for the meeting transcript.")

    normalized_details = " ".join(str(legal_basis_details or "").strip().split())
    if not normalized_details:
        raise HTTPException(status_code=400, detail="Add legal-basis details before transcribing a meeting.")

    try:
        normalized_retention_days = int(retention_days)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Set a retention window between 1 and 3650 days for the meeting transcript.",
        ) from exc

    if normalized_retention_days < 1 or normalized_retention_days > _MAX_MEETING_RETENTION_DAYS:
        raise HTTPException(
            status_code=400,
            detail="Set a retention window between 1 and 3650 days for the meeting transcript.",
        )

    consent_confirmed_at = datetime.now(timezone.utc)
    retention_expires_at = consent_confirmed_at + timedelta(days=normalized_retention_days)
    return MeetingTranscriptGovernance(
        consent_confirmed=True,
        consent_confirmed_at=consent_confirmed_at.isoformat(),
        legal_basis=normalized_legal_basis,
        legal_basis_label=legal_basis_label,
        legal_basis_details=normalized_details,
        retention_days=normalized_retention_days,
        retention_expires_at=retention_expires_at.isoformat(),
    )


def _detect_media_kind(filename: str, content_type: str | None) -> tuple[str, str]:
    """Detect whether an uploaded file is audio or video based on filename and content type."""
    safe_name = Path(filename or "meeting").name or "meeting"
    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    lowered_type = str(content_type or "").strip().lower()
    guessed_type, _ = mimetypes.guess_type(safe_name)
    guessed_type = str(guessed_type or "").lower()

    if lowered_type.startswith("video/"):
        return "video", lowered_type
    if lowered_type.startswith("audio/"):
        return "audio", lowered_type
    if guessed_type.startswith("video/"):
        return "video", guessed_type
    if guessed_type.startswith("audio/"):
        return "audio", guessed_type
    if extension in _KNOWN_VIDEO_EXTENSIONS and extension not in _KNOWN_AUDIO_EXTENSIONS:
        return "video", f"video/{extension or 'mp4'}"
    if extension in _KNOWN_AUDIO_EXTENSIONS and extension not in _KNOWN_VIDEO_EXTENSIONS:
        return "audio", f"audio/{extension or 'mpeg'}"
    if extension in _KNOWN_VIDEO_EXTENSIONS:
        return "video", f"video/{extension or 'mp4'}"
    if extension in _KNOWN_AUDIO_EXTENSIONS:
        return "audio", f"audio/{extension or 'mpeg'}"

    raise HTTPException(status_code=400, detail="Please upload an audio or video file for meeting transcription")


def _get_transcription_runtime(db: Session) -> dict[str, Any]:
    """Load and validate the transcription provider configuration from admin settings."""
    dictation_settings = get_settings_page(db, "dictation")
    dictation_settings_data = (
        dictation_settings.data
        if dictation_settings and isinstance(dictation_settings.data, dict)
        else {}
    )
    transcription_enabled = bool(dictation_settings_data.get("transcription_enabled"))
    transcription_provider_id = str(dictation_settings_data.get("transcription_provider_id") or "").strip()
    transcription_model = str(dictation_settings_data.get("transcription_model") or "").strip()

    if not transcription_enabled:
        raise HTTPException(
            status_code=400,
            detail={"code": TRANSCRIPTION_NOT_ENABLED_ERROR_CODE},
        )
    if not transcription_provider_id:
        raise HTTPException(status_code=400, detail="Transcription provider is not configured")
    if not transcription_model:
        raise HTTPException(status_code=400, detail="Transcription model is not configured")

    runtime = get_transcription_runtime_for_provider(db, transcription_provider_id)
    provider = runtime["provider"]
    provider_models = runtime["models"]
    allowed_formats = runtime["allowed_formats"]
    upload_limit_bytes = runtime["upload_limit_bytes"]

    if transcription_model not in provider_models:
        raise HTTPException(status_code=400, detail="Unsupported transcription model")

    return {
        "provider": provider,
        "model": transcription_model,
        "allowed_formats": set(allowed_formats),
        "upload_limit_bytes": int(upload_limit_bytes),
    }


def meeting_upload_limit_bytes(db: Session, user_id: str) -> int:
    try:
        max_mb_setting = get_user_group_setting_value(
            user_id,
            "chat",
            "max_upload_size",
            db,
        )
        max_upload_mb = int(max_mb_setting) if max_mb_setting is not None else 1024
    except Exception:
        max_upload_mb = 1024
    return max(1, max_upload_mb) * 1024 * 1024


def validate_meeting_transcript_admission(
    db: Session,
    *,
    user_id: str,
    filename: str,
    content_type: str | None,
    chat_id: str | None,
    project_id: str | None,
    consent_confirmed: Any,
    legal_basis: str | None,
    legal_basis_details: str | None,
    retention_days: Any,
) -> int:
    """Fail fast before a large upload is admitted to durable staging."""

    _validate_meeting_transcript_governance(
        consent_confirmed=consent_confirmed,
        legal_basis=legal_basis,
        legal_basis_details=legal_basis_details,
        retention_days=retention_days,
    )
    _detect_media_kind(filename, content_type)
    _get_transcription_runtime(db)
    normalized_chat_id = str(chat_id or "").strip()
    normalized_project_id = str(project_id or "").strip() or None
    if normalized_chat_id:
        chat = get_chat(db, normalized_chat_id, user_id)
        if (
            normalized_project_id
            and chat.project_id
            and str(chat.project_id) != normalized_project_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Chat and project context do not match",
            )
    elif normalized_project_id:
        get_project_with_access(db, user_id, normalized_project_id)
    return meeting_upload_limit_bytes(db, user_id)


def _limited_resource_value(requested_value: int, hard_limit: int) -> int:
    """Clamp resource limits without raising when a parent hard limit is already lower."""
    if hard_limit == resource.RLIM_INFINITY:
        return requested_value
    return max(1, min(requested_value, hard_limit))


def _ffmpeg_resource_limiter(max_output_bytes: int):
    """Build a child-process resource limiter for ffmpeg."""

    def limit_resources() -> None:
        requested_cpu_seconds = max(1, _FFMPEG_TIMEOUT_SECONDS + 5)
        _cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
        cpu_seconds = _limited_resource_value(requested_cpu_seconds, cpu_hard)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_hard))

        requested_file_size = max(1, max_output_bytes + _FFMPEG_OUTPUT_HEADROOM_BYTES)
        _file_soft, file_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        file_size_limit = _limited_resource_value(requested_file_size, file_hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_limit, file_hard))

        os.nice(5)

    return limit_resources


def _read_process_log_excerpt(log_file) -> str:
    """Return a bounded stderr excerpt from an ffmpeg log file."""
    log_file.seek(0)
    excerpt = log_file.read(_FFMPEG_LOG_EXCERPT_BYTES)
    if isinstance(excerpt, bytes):
        return excerpt.decode("utf-8", errors="replace").strip()
    return str(excerpt or "").strip()


def _run_ffmpeg_extract_audio(source_path: Path, target_path: Path, *, max_output_bytes: int) -> None:
    """Extract and normalize audio from a media file using bounded ffmpeg execution."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise FileNotFoundError("ffmpeg is not installed")

    command = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        _NORMALIZED_AUDIO_SAMPLE_RATE,
        "-b:a",
        _NORMALIZED_AUDIO_BITRATE,
        "-fs",
        str(max(1, max_output_bytes + 1)),
        str(target_path),
    ]
    with tempfile.TemporaryFile() as process_log:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=process_log,
                text=False,
                check=False,
                timeout=_FFMPEG_TIMEOUT_SECONDS,
                preexec_fn=_ffmpeg_resource_limiter(max_output_bytes),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Audio extraction timed out") from exc

        if result.returncode != 0 or not target_path.exists() or target_path.stat().st_size <= 0:
            error_text = _read_process_log_excerpt(process_log) or "Audio extraction failed"
            raise RuntimeError(error_text)


def _normalize_media_for_transcription(
    *,
    source_path: Path,
    original_filename: str,
    media_kind: str,
    runtime: dict[str, Any],
) -> tuple[bytes, str, bool]:
    """Normalize uploaded media to a standard audio format for transcription.

    Returns a tuple of (audio_bytes, filename, was_converted).
    """
    normalized_name = f"{Path(original_filename).stem or 'meeting'}.{_NORMALIZED_AUDIO_EXTENSION}"
    normalized_path = source_path.with_suffix(f".{_NORMALIZED_AUDIO_EXTENSION}")

    try:
        upload_limit_bytes = int(runtime["upload_limit_bytes"])
        _run_ffmpeg_extract_audio(source_path, normalized_path, max_output_bytes=upload_limit_bytes)
        normalized_size = normalized_path.stat().st_size if normalized_path.exists() else 0
        if normalized_size > upload_limit_bytes:
            limit_mb = upload_limit_bytes / (1024 * 1024)
            size_mb = normalized_size / (1024 * 1024)
            raise HTTPException(
                status_code=400,
                detail=f"Prepared meeting audio is {size_mb:.2f}MB and exceeds the {limit_mb:.0f}MB transcription limit",
            )
        normalized_bytes = normalized_path.read_bytes()
        return normalized_bytes, normalized_name, True
    except HTTPException:
        raise
    except Exception as exc:
        raw_extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
        if media_kind == "video":
            if raw_extension not in runtime["allowed_formats"]:
                raise HTTPException(
                    status_code=400,
                    detail="The server could not extract audio from this video. Try MP4/WebM or enable ffmpeg on the server.",
                ) from exc

        raw_bytes = source_path.read_bytes()
        if raw_extension not in runtime["allowed_formats"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The uploaded media format is not supported by the configured transcription provider. "
                    "Try MP3, WAV, M4A, MP4, or WebM."
                ),
            ) from exc
        if len(raw_bytes) > runtime["upload_limit_bytes"]:
            limit_mb = runtime["upload_limit_bytes"] / (1024 * 1024)
            size_mb = len(raw_bytes) / (1024 * 1024)
            raise HTTPException(
                status_code=400,
                detail=f"Meeting media is {size_mb:.2f}MB and exceeds the {limit_mb:.0f}MB transcription limit",
            ) from exc
        return raw_bytes, original_filename, False
    finally:
        normalized_path.unlink(missing_ok=True)


async def _transcribe_media_bytes(db: Session, media_bytes: bytes, filename: str, runtime: dict[str, Any]) -> str:
    """Send audio bytes to the transcription provider and return the transcript text."""
    # Provider calls can take minutes. Copy the small set of provider fields
    # needed by the dispatch layer before ending the read transaction, then
    # return the checked-out connection to the pool for the duration of the
    # remote request. The Session remains reusable for persistence afterward.
    provider = snapshot_transcription_provider(runtime["provider"])
    model_name = runtime["model"]
    release_db_session_before_long_wait(db)

    return await transcribe_audio_bytes_for_provider(
        provider,
        model_name=model_name,
        audio_bytes=media_bytes,
        filename=filename,
    )


def _build_meeting_markdown(
    *,
    title: str,
    browser_date_label: str,
    browser_date_iso: str | None,
    uploaded_filename: str,
    uploaded_media_type: str,
    media_kind: str,
    converted_to_audio: bool,
    governance: MeetingTranscriptGovernance,
    transcript: str,
) -> str:
    """Build a markdown document from meeting transcript metadata and text."""
    generated_at = datetime.now(timezone.utc).isoformat()
    transcript_text = str(transcript or "").strip()
    word_count = len([token for token in transcript_text.split() if token])
    metadata_lines = [
        f"- Browser date: {browser_date_label}",
        f"- Uploaded file: {uploaded_filename}",
        f"- Uploaded media type: {uploaded_media_type or 'unknown'}",
        f"- Source kind: {media_kind}",
        f"- Converted to audio: {'yes' if converted_to_audio else 'no'}",
        f"- Participant notice and consent confirmed: {'yes' if governance.consent_confirmed else 'no'}",
        f"- Consent confirmed at: {governance.consent_confirmed_at}",
        f"- Legal basis: {governance.legal_basis_label}",
        f"- Legal basis details: {governance.legal_basis_details}",
        f"- Retention window: {governance.retention_days} days",
        f"- Retention expires at: {governance.retention_expires_at}",
        f"- Transcript generated at: {generated_at}",
        f"- Transcript word count: {word_count}",
    ]
    if browser_date_iso:
        metadata_lines.insert(1, f"- Browser timestamp: {browser_date_iso}")

    return "\n".join(
        [
            f"# {title}",
            "",
            "## Metadata",
            *metadata_lines,
            "",
            "## Transcript",
            transcript_text,
            "",
        ]
    )


async def create_meeting_transcript(
    *,
    db: Session,
    user_id: str,
    user_role: str | None,
    media: UploadFile,
    chat_id: str | None = None,
    project_id: str | None = None,
    browser_date_iso: str | None = None,
    browser_date_label: str | None = None,
    consent_confirmed: Any = False,
    legal_basis: str | None = None,
    legal_basis_details: str | None = None,
    retention_days: Any = None,
) -> dict[str, Any]:
    """Handle the full meeting transcription workflow: upload, normalize, transcribe, persist chat and file."""
    source_filename = Path(media.filename or "meeting").name
    if not source_filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    governance = _validate_meeting_transcript_governance(
        consent_confirmed=consent_confirmed,
        legal_basis=legal_basis,
        legal_basis_details=legal_basis_details,
        retention_days=retention_days,
    )
    runtime = _get_transcription_runtime(db)
    media_kind, uploaded_media_type = _detect_media_kind(source_filename, getattr(media, "content_type", None))

    max_upload_bytes = meeting_upload_limit_bytes(db, user_id)
    max_upload_mb = max(1, max_upload_bytes // (1024 * 1024))

    title_date_label, date_slug, normalized_browser_iso = _format_browser_date_label(
        browser_date_iso,
        browser_date_label,
    )
    transcript_title = f"Meeting transcript - {title_date_label}"
    transcript_filename = f"meeting-transcript-{date_slug}.md"

    with tempfile.TemporaryDirectory(prefix="meeting-transcript-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        source_path = temp_dir / source_filename
        bytes_written = 0

        with source_path.open("wb") as handle:
            while True:
                chunk = await media.read(8192)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Meeting upload exceeds the chat limit of {max_upload_mb} MB",
                    )
                handle.write(chunk)

        if bytes_written <= 0:
            raise HTTPException(status_code=400, detail="Uploaded media was empty")

        transcription_input_bytes, transcription_filename, converted_to_audio = _normalize_media_for_transcription(
            source_path=source_path,
            original_filename=source_filename,
            media_kind=media_kind,
            runtime=runtime,
        )

        try:
            transcript_text = await _transcribe_media_bytes(
                db,
                transcription_input_bytes,
                transcription_filename,
                runtime,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=build_transcription_error_detail(
                    exc,
                    is_admin=is_admin_role(user_role),
                    fallback_message="Meeting transcription failed. Please try again.",
                ),
            ) from exc

    transcript_text = str(transcript_text or "").strip()
    if not transcript_text:
        raise HTTPException(status_code=422, detail="No speech was detected in the uploaded media")

    markdown_content = _build_meeting_markdown(
        title=transcript_title,
        browser_date_label=title_date_label,
        browser_date_iso=normalized_browser_iso,
        uploaded_filename=source_filename,
        uploaded_media_type=uploaded_media_type,
        media_kind=media_kind,
        converted_to_audio=converted_to_audio,
        governance=governance,
        transcript=transcript_text,
    )

    normalized_chat_id = str(chat_id or "").strip()
    normalized_project_id = str(project_id or "").strip() or None
    created_chat = False

    if normalized_chat_id:
        chat = get_chat(db, normalized_chat_id, user_id)
        if normalized_project_id and chat.project_id and str(chat.project_id) != normalized_project_id:
            raise HTTPException(status_code=400, detail="Chat and project context do not match")
        normalized_project_id = str(chat.project_id or "").strip() or normalized_project_id
    else:
        if normalized_project_id:
            get_project_with_access(db, user_id, normalized_project_id)
        chat = create_chat(user_id, db, project_id=normalized_project_id)
        chat.title = transcript_title
        chat.last_updated_at = datetime.now(timezone.utc)
        db.add(chat)
        db.commit()
        db.refresh(chat)
        normalized_chat_id = chat.id
        created_chat = True

    file_record = persist_generated_file_bytes(
        db=db,
        user_id=user_id,
        original_filename=transcript_filename,
        file_bytes=markdown_content.encode("utf-8"),
        file_type="text/markdown",
        file_category="document",
        project_id=normalized_project_id,
        meta={
            "origin": "user",
            "original_filename": transcript_filename,
            "meeting_transcript": True,
            "meeting_title": transcript_title,
            "meeting_browser_date_label": title_date_label,
            "meeting_browser_date_iso": normalized_browser_iso,
            "meeting_source_filename": source_filename,
            "meeting_source_media_type": uploaded_media_type,
            "meeting_source_kind": media_kind,
            "meeting_converted_to_audio": converted_to_audio,
            "meeting_consent_confirmed": governance.consent_confirmed,
            "meeting_consent_confirmed_at": governance.consent_confirmed_at,
            "meeting_legal_basis": governance.legal_basis,
            "meeting_legal_basis_label": governance.legal_basis_label,
            "meeting_legal_basis_details": governance.legal_basis_details,
            "meeting_retention_days": governance.retention_days,
            "meeting_retention_expires_at": governance.retention_expires_at,
            "meeting_transcript_created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    message_record = create_chat_message(
        db,
        normalized_chat_id,
        "user",
        "user",
        content=[
            {
                "type": "user",
                "content": "",
                "documents": [
                    {
                        "id": file_record.id,
                        "file_id": file_record.id,
                        "original_name": transcript_filename,
                        "original_filename": transcript_filename,
                        "file_type": "text/markdown",
                        "mime_type": "text/markdown",
                        "file_size": file_record.file_size,
                        "meta": {
                            "original_filename": transcript_filename,
                            "mime_type": "text/markdown",
                            "file_size": file_record.file_size,
                            "meeting_transcript": True,
                            "meeting_legal_basis": governance.legal_basis,
                            "meeting_retention_days": governance.retention_days,
                        },
                    }
                ],
                "meta": {
                    "meeting_transcript": True,
                    "meeting_title": transcript_title,
                    "meeting_consent_confirmed": governance.consent_confirmed,
                    "meeting_consent_confirmed_at": governance.consent_confirmed_at,
                    "meeting_legal_basis": governance.legal_basis,
                    "meeting_legal_basis_label": governance.legal_basis_label,
                    "meeting_legal_basis_details": governance.legal_basis_details,
                    "meeting_retention_days": governance.retention_days,
                    "meeting_retention_expires_at": governance.retention_expires_at,
                },
            }
        ],
    )

    file_lookup = _build_file_lookup_for_user(user_id)
    serialized_message = _serialize_chat_rows([message_record], file_lookup, include_bookmarked=True)[0]

    return {
        "chat_id": normalized_chat_id,
        "created_chat": created_chat,
        "project_id": normalized_project_id,
        "title": transcript_title,
        "message": serialized_message,
        "file": {
            "id": file_record.id,
            "file_id": file_record.id,
            "file_name": file_record.file_name,
            "file_category": file_record.file_category,
            "file_type": file_record.file_type,
            "file_size": file_record.file_size,
            "project_id": file_record.project_id,
            "folder_id": file_record.folder_id,
            "created_at": file_record.created_at.isoformat() if file_record.created_at else None,
            "meta": file_record.meta if isinstance(file_record.meta, dict) else {},
        },
    }


def _create_meeting_transcript_with_thread_session(
    *,
    user_id: str,
    user_role: str | None,
    media: UploadFile,
    chat_id: str | None,
    project_id: str | None,
    browser_date_iso: str | None,
    browser_date_label: str | None,
    consent_confirmed: Any,
    legal_basis: str | None,
    legal_basis_details: str | None,
    retention_days: Any,
) -> dict[str, Any]:
    """Run the inline media stack with a thread-owned database session."""

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        async def _run() -> dict[str, Any]:
            return await create_meeting_transcript(
                db=session,
                user_id=user_id,
                user_role=user_role,
                media=media,
                chat_id=chat_id,
                project_id=project_id,
                browser_date_iso=browser_date_iso,
                browser_date_label=browser_date_label,
                consent_confirmed=consent_confirmed,
                legal_basis=legal_basis,
                legal_basis_details=legal_basis_details,
                retention_days=retention_days,
            )

        return anyio.run(_run)
    finally:
        session.close()


async def create_meeting_transcript_off_event_loop(
    *,
    db: Session,
    user_id: str,
    user_role: str | None,
    media: UploadFile,
    chat_id: str | None = None,
    project_id: str | None = None,
    browser_date_iso: str | None = None,
    browser_date_label: str | None = None,
    consent_confirmed: Any = False,
    legal_basis: str | None = None,
    legal_basis_details: str | None = None,
    retention_days: Any = None,
) -> dict[str, Any]:
    """Keep inline FFmpeg, storage, and synchronous ORM work off ASGI."""

    try:
        dialect_name = db.get_bind().dialect.name
    except (AttributeError, TypeError):
        dialect_name = ""

    if dialect_name == "sqlite":
        return await create_meeting_transcript(
            db=db,
            user_id=user_id,
            user_role=user_role,
            media=media,
            chat_id=chat_id,
            project_id=project_id,
            browser_date_iso=browser_date_iso,
            browser_date_label=browser_date_label,
            consent_confirmed=consent_confirmed,
            legal_basis=legal_basis,
            legal_basis_details=legal_basis_details,
            retention_days=retention_days,
        )

    return await run_blocking_io(
        partial(
            _create_meeting_transcript_with_thread_session,
            user_id=str(user_id),
            user_role=user_role,
            media=media,
            chat_id=chat_id,
            project_id=project_id,
            browser_date_iso=browser_date_iso,
            browser_date_label=browser_date_label,
            consent_confirmed=consent_confirmed,
            legal_basis=legal_basis,
            legal_basis_details=legal_basis_details,
            retention_days=retention_days,
        )
    )
