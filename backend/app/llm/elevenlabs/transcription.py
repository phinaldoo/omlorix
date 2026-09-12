from __future__ import annotations

import io
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Union

import httpx
from elevenlabs.client import ElevenLabs

from app.utils.async_cleanup import close_async_resource

logger = logging.getLogger(__name__)

ELEVENLABS_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024  # 25MB
ELEVENLABS_TRANSCRIPTION_SUPPORTED_FILE_FORMATS = [
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "m4a",
    "wav",
    "webm",
    "ogg",
    "flac",
]


# Supported file-transcription models; maintain explicitly because SDK 2.66+
# accepts a plain string and no longer exposes model IDs in its type hints.
ELEVENLABS_TRANSCRIPTION_MODELS = ["scribe_v1", "scribe_v2"]

InputFile = Union[str, Path, BinaryIO]


@contextmanager
def _audio_file_context(input_file: InputFile) -> Iterator[BinaryIO]:
    if hasattr(input_file, "read"):
        yield input_file  # type: ignore[misc]
        return

    file_path = Path(input_file)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    suffix = file_path.suffix.lower().lstrip(".")
    if suffix not in ELEVENLABS_TRANSCRIPTION_SUPPORTED_FILE_FORMATS:
        raise ValueError(
            f"Unsupported file format '{file_path.suffix}'. Supported: {ELEVENLABS_TRANSCRIPTION_SUPPORTED_FILE_FORMATS}"
        )

    file_size = file_path.stat().st_size
    if file_size > ELEVENLABS_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES:
        raise ValueError(
            f"File is {file_size / (1024 * 1024):.2f}MB which exceeds the {ELEVENLABS_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES / (1024 * 1024):.0f}MB limit."
        )

    with file_path.open("rb") as file_handle:
        yield file_handle


def _extract_text_from_transcription_result(result) -> str:
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(result, dict):
        payload_text = result.get("text")
        if isinstance(payload_text, str):
            return payload_text
    raise RuntimeError("ElevenLabs transcription response did not include text")


def transcribe_audio(
    input_file: InputFile,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    enable_logging: bool = True,
) -> str:
    client = ElevenLabs(api_key=api_key)
    with _audio_file_context(input_file) as audio_file:
        transcription = client.speech_to_text.convert(
            file=audio_file,
            model_id=model,
            enable_logging=enable_logging,
        )
    return _extract_text_from_transcription_result(transcription)


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    enable_logging: bool = True,
) -> str:
    # Lazy import keeps the synchronous SDK path and lightweight schema imports
    # independent while using ElevenLabs' native async transport here.
    from elevenlabs.client import AsyncElevenLabs

    http_client = httpx.AsyncClient()
    try:
        client = AsyncElevenLabs(api_key=api_key, httpx_client=http_client)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        transcription = await client.speech_to_text.convert(
            file=audio_file,
            model_id=model,
            enable_logging=enable_logging,
        )
        return _extract_text_from_transcription_result(transcription)
    finally:
        await close_async_resource(http_client)
