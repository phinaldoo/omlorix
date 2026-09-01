"""xAI batch speech-to-text adapter."""

from __future__ import annotations

import mimetypes

import httpx

from app.llm.models import LLMProvider
from app.utils.async_cleanup import close_async_resource
from app.llm.xai.common import (
    require_xai_success,
    xai_base_url,
    xai_headers,
    xai_timeout,
)


XAI_TRANSCRIPTION_MODELS = ["grok-transcribe"]
XAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES = 500 * 1024 * 1024
XAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS = [
    "aac",
    "flac",
    "m4a",
    "mkv",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "ogg",
    "opus",
    "wav",
    "webm",
]


async def _post_transcription(
    provider: LLMProvider,
    *,
    audio_bytes: bytes,
    filename: str,
    mime_type: str,
) -> httpx.Response:
    """Send an xAI multipart request with a non-blocking HTTP transport."""

    client = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=xai_timeout(),
    )
    try:
        # Multipart fields deliberately precede ``file``. xAI's streaming
        # parser may ignore fields placed after the file part.
        return await client.post(
            f"{xai_base_url(provider)}/stt",
            headers=xai_headers(provider, include_content_type=False),
            files=[
                # Formatting requires an explicit language. Omlorix currently
                # auto-detects batch transcription language, so retain xAI's
                # unformatted default while preserving multipart field order.
                ("format", (None, "false")),
                ("file", (filename, audio_bytes, mime_type)),
            ],
        )
    finally:
        await close_async_resource(client)


async def transcribe_audio_bytes(
    provider: LLMProvider,
    audio_bytes: bytes,
    filename: str = "audio.mp3",
) -> str:
    """Transcribe one uploaded audio file with xAI's native STT endpoint."""
    if not audio_bytes:
        raise ValueError("Audio data is required for xAI transcription")
    mime_type, _ = mimetypes.guess_type(filename or "")
    mime_type = mime_type or "application/octet-stream"

    response = await _post_transcription(
        provider,
        audio_bytes=audio_bytes,
        filename=filename,
        mime_type=mime_type,
    )
    require_xai_success(response, "transcription")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("xAI transcription response was not a JSON object")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("xAI transcription response did not include text")
    return text


def get_live_transcription_settings_schema():
    """Return xAI-native streaming STT controls for admin settings."""

    from app.utils.schemas import FieldSchema, Section, Sections

    return Sections(
        sections=[
            Section(
                title="Live chat dictation",
                i18n_title="schema_models_live_transcription_sec_title",
                description="Additional controls for provider-specific configuration.",
                fields=[
                    FieldSchema(
                        key="live_transcription_xai_language",
                        label="xAI formatting language",
                        description=(
                            "Optional language code that enables xAI inverse text "
                            "normalization, such as en, de, or pt-BR."
                        ),
                        type="string",
                        placeholder="E.g. en or de",
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label="schema_models_live_transcription_xai_language",
                        i18n_description=(
                            "schema_models_live_transcription_xai_language_desc"
                        ),
                        i18n_placeholder=(
                            "schema_models_live_transcription_xai_language_placeholder"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_endpointing_ms",
                        label="xAI endpointing silence (ms)",
                        description=(
                            "Silence before xAI marks an utterance final. Use 0 to "
                            "finalize at any VAD silence boundary."
                        ),
                        type="number",
                        attributes={"min": 0, "max": 5000, "step": 1},
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label=(
                            "schema_models_live_transcription_xai_endpointing_ms"
                        ),
                        i18n_description=(
                            "schema_models_live_transcription_xai_endpointing_ms_desc"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_keyterms",
                        label="xAI key terms",
                        description=(
                            "Terms that bias xAI toward product names, proper nouns, "
                            "and specialized vocabulary. Add up to 100 terms of 50 "
                            "characters each."
                        ),
                        type="string_list",
                        placeholder="E.g. Omlorix",
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label="schema_models_live_transcription_xai_keyterms",
                        i18n_description=(
                            "schema_models_live_transcription_xai_keyterms_desc"
                        ),
                        i18n_placeholder=(
                            "schema_models_live_transcription_xai_keyterms_placeholder"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_filler_words",
                        label="Keep xAI filler words",
                        description=(
                            "Include filler words such as uh and um instead of "
                            "removing them."
                        ),
                        type="boolean",
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label=(
                            "schema_models_live_transcription_xai_filler_words"
                        ),
                        i18n_description=(
                            "schema_models_live_transcription_xai_filler_words_desc"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_smart_turn",
                        label="xAI Smart Turn threshold",
                        description=(
                            "Optional end-of-turn confidence from 0 to 1. Higher "
                            "values wait for stronger evidence that the thought is "
                            "complete."
                        ),
                        type="number",
                        attributes={"min": 0, "max": 1, "step": 0.01},
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label=(
                            "schema_models_live_transcription_xai_smart_turn"
                        ),
                        i18n_description=(
                            "schema_models_live_transcription_xai_smart_turn_desc"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_smart_turn_timeout_ms",
                        label="xAI Smart Turn timeout (ms)",
                        description=(
                            "Optional maximum silence before xAI forces a final "
                            "utterance while Smart Turn is enabled."
                        ),
                        type="number",
                        attributes={"min": 1, "max": 5000, "step": 1},
                        dependency="live_transcription_xai_smart_turn",
                        i18n_label=(
                            "schema_models_live_transcription_xai_smart_turn_timeout_ms"
                        ),
                        i18n_description=(
                            "schema_models_live_transcription_xai_smart_turn_timeout_ms_desc"
                        ),
                    ),
                    FieldSchema(
                        key="live_transcription_xai_vad_threshold",
                        label="xAI voice activity threshold",
                        description=(
                            "Speech probability from 0 to 1. Lower values capture "
                            "quieter speech but can include more background noise; "
                            "0 disables the gate."
                        ),
                        type="number",
                        attributes={"min": 0, "max": 1, "step": 0.01},
                        dependency="live_transcription_enabled",
                        dependency_value=True,
                        i18n_label=(
                            "schema_models_live_transcription_xai_vad_threshold"
                        ),
                        i18n_description=(
                            "schema_models_live_transcription_xai_vad_threshold_desc"
                        ),
                    ),
                ],
            )
        ]
    )
