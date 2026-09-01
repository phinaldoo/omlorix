from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Union

from fastapi import HTTPException
from openai import Client
from sqlalchemy.orm import Session

from app.llm.openai.custom_headers import custom_headers_to_dict
from app.utils.async_cleanup import close_async_resource
from app.llm.openai.utils import _resolve_openai_client_kwargs
from app.llm.openai.model_list import OPENAI_UNSUPPORTED_TRANSCRIPTION_MODELS
from app.llm.schemas import ProviderEnum

logger = logging.getLogger(__name__)

OPENAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024  # 25MB

OPENAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS = [
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "m4a",
    "wav",
    "webm",
]
def get_openai_transcription_models(
    *,
    db: Session | None = None,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str = ProviderEnum.openai.value,
) -> list[str]:
    """
    Get OpenAI transcription models.
    
    Args:
        db: Database session for resolving provider credentials (required if openai_provider_id is provided).
        openai_provider_id: Optional provider ID to use for fetching models.
        byok: Optional bring-your-own-key credentials dict containing api_key, base_url, etc.
        openai_provider_type: The OpenAI provider type (default: ProviderEnum.openai.value).
    
    Returns:
        List of available transcription model IDs (strings) that are supported by the system.
    
    Raises:
        HTTPException: If database session is required but not provided when resolving provider credentials.
        HTTPException: If fetching the model list from OpenAI fails.
    """
    provider_identifier = (openai_provider_id or "").strip() or None

    if provider_identifier and db is None:
        raise HTTPException(
            status_code=500,
            detail="Database session is required to resolve OpenAI provider credentials",
        )

    client_kwargs = _resolve_openai_client_kwargs(
        db,
        openai_provider_id=provider_identifier,
        byok=byok,
        openai_provider_type=openai_provider_type,
    )
    client = Client(**client_kwargs)
    try:
        models = client.models.list()
    except Exception as exc:
        logger.exception("Failed to list OpenAI transcription models")
        raise HTTPException(status_code=424, detail=f"Failed to list OpenAI models: {exc}") from exc

    return [model.id for model in models if model.id not in OPENAI_UNSUPPORTED_TRANSCRIPTION_MODELS]

InputFile = Union[str, Path, BinaryIO]

_client_cache: dict[str, Client] = {}


def _client_kwargs(
    api_key: str | None,
    *,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> dict[str, str | dict[str, str]]:
    """Build equivalent configuration for the synchronous and async SDKs."""

    resolved_key = (api_key or "").strip()
    if not resolved_key:
        raise RuntimeError("OpenAI API key is not configured for transcription.")

    client_kwargs: dict[str, str | dict[str, str]] = {"api_key": resolved_key}
    normalized_base_url = str(base_url or "").strip()
    if normalized_base_url:
        client_kwargs["base_url"] = normalized_base_url
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    return client_kwargs


def _get_client(
    api_key: str | None = None,
    *,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> Client:
    """Get OpenAI client."""
    client_kwargs = _client_kwargs(
        api_key,
        base_url=base_url,
        custom_headers=custom_headers,
    )
    cache_key = "|".join(
        [
            str(client_kwargs["api_key"]),
            str(client_kwargs.get("base_url") or ""),
            repr(sorted(dict(client_kwargs.get("default_headers") or {}).items())),
        ]
    )

    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached

    client = Client(**client_kwargs)
    _client_cache[cache_key] = client
    return client


def _get_async_client(
    api_key: str | None = None,
    *,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> Any:
    """Create a request-scoped native async OpenAI client."""

    # Kept lazy so lightweight schema imports do not initialize the async SDK.
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        **_client_kwargs(
            api_key,
            base_url=base_url,
            custom_headers=custom_headers,
        )
    )


async def _close_async_client(client: Any) -> None:
    await close_async_resource(client)


@contextmanager
def _audio_file_context(input_file: InputFile) -> Iterator[BinaryIO]:
    """Audio file context."""
    if hasattr(input_file, "read"):
        yield input_file  # type: ignore[misc]
        return

    file_path = Path(input_file)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    suffix = file_path.suffix.lower().lstrip(".")
    if suffix not in OPENAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS:
        raise ValueError(
            f"Unsupported file format '{file_path.suffix}'. Supported: {OPENAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS}"
        )

    file_size = file_path.stat().st_size
    if file_size > OPENAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES:
        raise ValueError(
            f"File is {file_size / (1024 * 1024):.2f}MB which exceeds the {OPENAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES / (1024 * 1024):.0f}MB limit."
        )

    with file_path.open("rb") as file_handle:
        yield file_handle


def transcribe_audio(
    input_file: InputFile,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> str:
    """
    Transcribe one completed audio file with an OpenAI-compatible model.
    
    Args:
        input_file: Audio file path (str or Path) or file-like object (BinaryIO).
        filename: Filename with extension for format detection (default: "audio.mp3").
        api_key: OpenAI API key for authentication.
        model: The model ID to use for transcription (for example,
            ``gpt-4o-transcribe`` or ``whisper-1``).
        base_url: Optional base URL for OpenAI API endpoint.
        custom_headers: Optional custom headers for the API request.
    
    Returns:
        Transcribed text string.
    
    Raises:
        FileNotFoundError: If the audio file path does not exist.
        ValueError: If the file format is unsupported or exceeds size limit.
        RuntimeError: If the API key is not configured.
        HTTPException: If the transcription request fails.
    """
    client = _get_client(api_key, base_url=base_url, custom_headers=custom_headers)
    with _audio_file_context(input_file) as audio_file:
        transcription = client.audio.transcriptions.create(
            model=model,
            file=(filename, audio_file),
            # Omlorix only needs the basic transcript. Requesting the stable
            # JSON shape intentionally avoids prompt, keyword, language, and
            # verbose-segment features that are outside dictation's contract.
            response_format="json",
        )
    return transcription.text


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> str:
    """
    Transcribe one completed audio payload with an OpenAI-compatible model.
    
    Args:
        audio_bytes: Raw audio bytes
        filename: Filename with extension for format detection
    
    Returns:
        Transcribed text string
    """
    import io

    client = _get_async_client(
        api_key,
        base_url=base_url,
        custom_headers=custom_headers,
    )
    try:
        audio_file = io.BytesIO(audio_bytes)
        transcription = await client.audio.transcriptions.create(
            model=model,
            file=(filename, audio_file),
            response_format="json",
        )
        return transcription.text
    finally:
        await _close_async_client(client)


def get_live_transcription_settings_schema():
    """Return OpenAI's live-dictation stability control."""

    from app.utils.schemas import FieldSchema, Section, Sections

    return Sections(
        sections=[
            Section(
                title="Live chat dictation",
                i18n_title="schema_models_live_transcription_sec_title",
                description="Additional controls for provider-specific configuration.",
                fields=[
                    FieldSchema(
                        key="live_transcription_delay",
                        label="Transcript delay",
                        description=(
                            "Balance partial-transcript latency against "
                            "transcription stability."
                        ),
                        type="select",
                        options=[
                            {
                                "value": "minimal",
                                "label": "Minimal",
                                "i18n_label": (
                                    "schema_option_live_transcription_delay_minimal"
                                ),
                            },
                            {
                                "value": "low",
                                "label": "Low",
                                "i18n_label": "schema_option_live_transcription_delay_low",
                            },
                            {
                                "value": "medium",
                                "label": "Medium",
                                "i18n_label": (
                                    "schema_option_live_transcription_delay_medium"
                                ),
                            },
                            {
                                "value": "high",
                                "label": "High",
                                "i18n_label": "schema_option_live_transcription_delay_high",
                            },
                            {
                                "value": "xhigh",
                                "label": "Extra high",
                                "i18n_label": (
                                    "schema_option_live_transcription_delay_xhigh"
                                ),
                            },
                        ],
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label="schema_models_live_transcription_delay",
                        i18n_description=(
                            "schema_models_live_transcription_delay_desc"
                        ),
                    )
                ],
            )
        ]
    )
