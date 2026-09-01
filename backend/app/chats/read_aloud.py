from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.chats.models import ChatMessages, Chats
from app.chats.read_aloud_constants import READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
from app.database import SessionLocal
from app.files.models import Files
from app.files.utils import (
    MATERIALIZED_TEMP_DIR,
    delete_storage_reference,
    persist_generated_file_bytes,
)
from app.llm.models import LLMProvider
from app.llm.speech import TTS_PROVIDER_TYPES, get_tts_model_capabilities_for_provider
from app.settings.models import get_settings_page
from app.tools.audio_generation.utils import PROVIDER_GENERATORS
from app.utils.background import start_named_worker, stop_named_worker


logger = logging.getLogger(__name__)

READ_ALOUD_CACHE_TTL_HOURS = 24
READ_ALOUD_CLEANUP_INTERVAL_SECONDS = 3600

_SAFE_FILE_PART_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


def _normalize_read_aloud_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a raw read-aloud config dict into a standardized config with provider_id, model_name, voice, and response_format."""
    payload = raw if isinstance(raw, dict) else {}
    provider_id = str(payload.get("read_aloud_provider_id") or "").strip() or READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
    model_name = str(payload.get("read_aloud_model") or "").strip() or None
    voice = str(payload.get("read_aloud_voice") or "").strip() or None
    response_format = str(payload.get("read_aloud_response_format") or "").strip().lower() or None
    return {
        "provider_id": provider_id,
        "model_name": model_name,
        "voice": voice,
        "response_format": response_format,
    }


def get_read_aloud_runtime_config(db: Session) -> dict[str, Any]:
    """Resolve the full runtime configuration for read-aloud TTS, including provider readiness and browser-native detection."""
    record = get_settings_page(db, "read_aloud")
    record_data = record.data if record and isinstance(record.data, dict) else {}
    config = _normalize_read_aloud_config(record_data)

    provider_id = config["provider_id"]
    if provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
        return {
            **config,
            "provider": None,
            "provider_type": "browser_native",
            "ready": True,
            "use_browser_native": True,
        }

    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    provider_type = str(provider.provider or "").strip().lower() if provider else ""
    voice_required = True
    if provider and config["model_name"]:
        try:
            capabilities = get_tts_model_capabilities_for_provider(
                str(config["model_name"]),
                provider_type=provider_type,
                provider_row=provider,
            )
            voice_required = bool(capabilities.get("voice_required", True))
        except Exception:
            voice_required = True
    ready = bool(
        provider
        and provider_type in TTS_PROVIDER_TYPES
        and str(getattr(provider, "api_key", "") or "").strip()
        and config["model_name"]
        and (config["voice"] or not voice_required)
    )

    return {
        **config,
        "provider": provider,
        "provider_type": provider_type or None,
        "ready": ready,
        "use_browser_native": False,
    }


def _normalize_text(text: str | None) -> str:
    """Strip and convert text to a string, returning empty string for None."""
    return str(text or "").strip()


def sanitize_read_aloud_text(text: str | None) -> str:
    """Sanitize text for TTS by removing code blocks, inline code markers, and collapsing whitespace."""
    normalized = str(text or "")
    normalized = re.sub(r"```[\s\S]*?```", " ", normalized)
    normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _coerce_assistant_content_blocks(raw_content: Any) -> list[dict[str, Any]]:
    """Coerce raw assistant message content into a list of content block dicts."""
    if isinstance(raw_content, str):
        stripped = raw_content.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
            except Exception:
                return [{"type": "content", "content": raw_content}]
            raw_content = parsed
        else:
            return []

    if isinstance(raw_content, list):
        blocks = []
        for block in raw_content:
            if isinstance(block, dict):
                blocks.append(block)
            elif block is not None:
                blocks.append({"type": "content", "content": str(block)})
        return blocks

    if isinstance(raw_content, dict):
        return [raw_content]

    if raw_content is None:
        return []
    return [{"type": "content", "content": str(raw_content)}]


def extract_assistant_message_read_aloud_text(raw_content: Any) -> str:
    """Extract and sanitize readable text from assistant message content blocks for TTS."""
    chunks: list[str] = []
    for block in _coerce_assistant_content_blocks(raw_content):
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "content").strip().lower() != "content":
            continue
        cleaned = sanitize_read_aloud_text(block.get("content"))
        if cleaned:
            chunks.append(cleaned)
    return sanitize_read_aloud_text(" ".join(chunks))


def get_owned_assistant_message_read_aloud_text(
    db: Session,
    *,
    user_id: str,
    message_id: str,
) -> str:
    """Retrieve the sanitized read-aloud text for an assistant message owned by the given user."""
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        raise ValueError("Assistant message ID is required for read aloud.")

    message = (
        db.query(ChatMessages)
        .join(Chats, ChatMessages.chat_id == Chats.id)
        .filter(
            ChatMessages.id == normalized_message_id,
            Chats.user_id == user_id,
        )
        .first()
    )
    if message is None:
        raise ValueError("Assistant message not found.")
    if str(getattr(message, "role", "") or "").strip().lower() != "assistant":
        raise ValueError("Read aloud is only available for assistant messages.")

    speech_text = extract_assistant_message_read_aloud_text(getattr(message, "content", None))
    if not speech_text:
        raise ValueError("Assistant message has no readable text available for read aloud.")
    return speech_text


def _build_cache_key(
    *,
    message_id: str | None,
    text: str,
    provider_id: str,
    model_name: str,
    voice: str | None,
    response_format: str | None,
) -> str:
    """Build a SHA-256 cache key from the TTS parameters for read-aloud audio caching."""
    payload = {
        "message_id": str(message_id or "").strip(),
        "text": _normalize_text(text),
        "provider_id": provider_id,
        "model_name": model_name,
        "voice": str(voice or "").strip(),
        "response_format": str(response_format or "").strip().lower(),
    }
    serialized = "|".join(f"{key}={payload[key]}" for key in sorted(payload))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _find_cached_read_aloud_file(
    db: Session,
    *,
    user_id: str,
    cache_key: str,
) -> Files | None:
    """Search for an existing cached read-aloud audio file matching the cache key within the TTL window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=READ_ALOUD_CACHE_TTL_HOURS)
    candidates = (
        db.query(Files)
        .filter(
            Files.user_id == user_id,
            Files.file_category == "audio",
            Files.created_at >= cutoff,
        )
        .order_by(Files.created_at.desc())
        .all()
    )

    for file_record in candidates:
        meta = file_record.meta if isinstance(file_record.meta, dict) else {}
        if not meta.get("read_aloud_cache"):
            continue
        if str(meta.get("read_aloud_cache_key") or "").strip() == cache_key:
            return file_record
    return None


def _build_original_filename(message_id: str | None, extension: str | None, text: str) -> str:
    """Build a safe original filename for a read-aloud audio file."""
    ext = str(extension or "").strip().lower().lstrip(".") or "mp3"
    base_part = str(message_id or "").strip() or hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    safe_part = _SAFE_FILE_PART_PATTERN.sub("-", base_part).strip("-") or "assistant-message"
    return f"read-aloud-{safe_part}.{ext}"


def cleanup_expired_read_aloud_audio(db: Session | None = None) -> int:
    """Remove expired read-aloud cached audio files from storage and database."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    removed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=READ_ALOUD_CACHE_TTL_HOURS)
    try:
        candidates = (
            db.query(Files)
            .filter(Files.file_category == "audio", Files.created_at < cutoff)
            .all()
        )
        for file_record in candidates:
            meta = file_record.meta if isinstance(file_record.meta, dict) else {}
            if not meta.get("read_aloud_cache"):
                continue
            try:
                delete_storage_reference(
                    storage_provider=file_record.storage_provider,
                    storage_key=file_record.storage_key,
                    user_id=file_record.user_id,
                    file_name=file_record.file_name,
                )
                materialized = MATERIALIZED_TEMP_DIR / f"{file_record.id}{Path(file_record.file_name or '').suffix or '.bin'}"
                materialized.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to delete cached read aloud audio file %s", file_record.id, exc_info=True)
            db.delete(file_record)
            removed += 1
        if removed:
            db.commit()
        else:
            db.rollback()
        return removed
    finally:
        if close_db:
            db.close()


def get_or_create_read_aloud_file(
    db: Session,
    *,
    user_id: str,
    message_id: str | None,
    text: str,
) -> tuple[Files, bool]:
    """Return a cached read-aloud audio file or generate a new one via TTS. Returns (file_record, was_cached)."""
    cleaned_text = _normalize_text(text)
    if not cleaned_text:
        raise ValueError("Assistant read aloud text is required.")

    config = get_read_aloud_runtime_config(db)
    if config["use_browser_native"]:
        raise RuntimeError("Read aloud is configured for browser-native speech synthesis.")
    if not config["ready"]:
        raise RuntimeError("Read aloud TTS is not fully configured in admin settings.")

    cache_key = _build_cache_key(
        message_id=message_id,
        text=cleaned_text,
        provider_id=str(config["provider_id"]),
        model_name=str(config["model_name"]),
        voice=config["voice"],
        response_format=config["response_format"],
    )

    cached_file = _find_cached_read_aloud_file(db, user_id=user_id, cache_key=cache_key)
    if cached_file is not None:
        return cached_file, True

    provider = config["provider"]
    if provider is None:
        raise RuntimeError("Read aloud provider is unavailable.")
    provider_type = str(config.get("provider_type") or "").strip().lower()
    generator = PROVIDER_GENERATORS.get(provider_type)
    if generator is None:
        raise RuntimeError(f"Unsupported read aloud provider type: {provider_type}")

    generation = generator(
        provider,
        str(config["model_name"]),
        cleaned_text,
        None,
        False,
        {
            "voice": config["voice"],
            "response_format": config["response_format"],
        },
    )
    audio_bytes = generation.get("audio_bytes", b"")
    if not isinstance(audio_bytes, (bytes, bytearray)):
        raise RuntimeError("Read aloud generation returned an invalid audio payload.")
    audio_bytes = bytes(audio_bytes)
    if not audio_bytes:
        raise RuntimeError("Read aloud generation returned an empty audio payload.")

    extension = str(generation.get("extension") or config.get("response_format") or "mp3").strip().lower().lstrip(".")
    file_type = str(generation.get("file_type") or "audio/mpeg").strip().lower()
    original_filename = _build_original_filename(message_id, extension, cleaned_text)
    meta = {
        "origin": "assistant",
        "read_aloud_cache": True,
        "read_aloud_cache_key": cache_key,
        "assistant_message_id": str(message_id or "").strip() or None,
        "original_filename": original_filename,
        "provider_id": provider.id,
        "provider_type": provider.provider,
        "model": config["model_name"],
        "voice": generation.get("voice") or config["voice"] or "",
        "response_format": generation.get("response_format") or config["response_format"] or "",
        "text_sha256": hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest(),
    }
    file_record = persist_generated_file_bytes(
        db,
        user_id=user_id,
        original_filename=original_filename,
        file_bytes=audio_bytes,
        file_type=file_type,
        file_category="audio",
        meta=meta,
    )
    return file_record, False


def _read_aloud_cleanup_worker(stop_event: threading.Event):
    """Background worker loop that periodically cleans up expired read-aloud audio cache."""
    while not stop_event.is_set():
        try:
            cleanup_expired_read_aloud_audio()
        except Exception:
            logger.exception("[Read Aloud] Cached audio cleanup failed")
        if stop_event.wait(READ_ALOUD_CLEANUP_INTERVAL_SECONDS):
            break


def start_read_aloud_cleanup_worker():
    """Start the read-aloud cache cleanup background worker."""
    return start_named_worker(
        "read_aloud_cleanup",
        _read_aloud_cleanup_worker,
        logger,
        start_message="[Read Aloud] Cached audio cleanup worker started",
        already_running_message="[Read Aloud] Cached audio cleanup worker already running",
        failure_message="[Read Aloud] Failed to start cached audio cleanup worker",
    )


def stop_read_aloud_cleanup_worker(timeout: float = 5.0):
    """Stop the read-aloud cache cleanup background worker."""
    stop_named_worker(
        "read_aloud_cleanup",
        logger,
        timeout=timeout,
        stopped_message="[Read Aloud] Cached audio cleanup worker stopped",
        not_running_message="[Read Aloud] Cached audio cleanup worker was not running",
        failure_message="[Read Aloud] Failed to stop cached audio cleanup worker",
    )
