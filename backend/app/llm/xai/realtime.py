"""Helpers for xAI Speech-to-Speech WebSocket sessions."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import HTTPException

from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.xai.schemas import XAI_DEFAULT_BASE_URL
from app.llm.xai.text_to_speech import (
    XAI_TTS_FALLBACK_VOICES,
    list_xai_voice_entries,
)


XAI_REALTIME_MODELS = [
    "grok-voice-latest",
    "grok-voice-think-fast-2.0",
    "grok-voice-think-fast-1.0",
]
# xAI documents Speech-to-Speech as using the same catalog as Text to Speech.
# Custom voice IDs are provider-owned opaque identifiers, so accept a bounded
# token shape instead of coupling Omlorix to today's eight-character examples.
XAI_REALTIME_VOICES = list(XAI_TTS_FALLBACK_VOICES)
XAI_CUSTOM_VOICE_ID_PATTERN = re.compile(r"^[a-z0-9_-]{1,128}$")
XAI_INCOMPATIBLE_REALTIME_VOICES = {
    # The shared realtime setting historically defaulted to OpenAI's Alloy.
    # Replacing it avoids sending a known-invalid xAI voice on first setup.
    "alloy",
}


def normalize_xai_realtime_voice(voice: Any) -> str:
    """Accept current built-ins/custom IDs and safely replace stale values."""
    normalized = str(voice or "").strip().lower()
    if (
        normalized not in XAI_INCOMPATIBLE_REALTIME_VOICES
        and XAI_CUSTOM_VOICE_ID_PATTERN.fullmatch(normalized)
    ):
        return normalized
    return "eve"


def get_xai_realtime_voice_options(
    *,
    db,
    provider_id: str | None,
) -> list[dict[str, str]]:
    """Return the provider's current built-in and accessible custom voices."""
    provider = None
    if provider_id:
        provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if provider and str(getattr(provider, "api_key", "") or "").strip():
        entries = list_xai_voice_entries(provider)
        if entries:
            return [
                {
                    "value": str(entry["id"]),
                    "label": str(entry.get("name") or entry["id"]),
                }
                for entry in entries
            ]
    return [
        {"value": voice, "label": voice.title()}
        for voice in XAI_REALTIME_VOICES
    ]


def get_xai_realtime_models(
    *,
    db=None,
    provider_id: str | None = None,
) -> list[str]:
    """Return Omlorix's supported xAI realtime model IDs."""
    del db
    del provider_id
    return list(XAI_REALTIME_MODELS)


def get_realtime_settings_schema(
    *,
    db,
    provider_id: str,
    tool_options: list[dict] | None = None,
):
    """Return controls implemented by xAI's Speech-to-Speech transport."""

    from app.llm.realtime_schema import (
        input_transcription_field,
        language_code_field,
        prefix_padding_field,
        silence_duration_field,
        tools_field,
        voice_field,
    )
    from app.utils.schemas import Section, Sections

    return Sections(
        sections=[
            Section(
                title="Realtime advanced settings",
                description="Additional controls for provider-specific configuration.",
                fields=[
                    voice_field(
                        get_xai_realtime_voice_options(
                            db=db,
                            provider_id=provider_id,
                        ),
                        description=(
                            "Voice used for assistant speech output in realtime "
                            "sessions. OpenAI examples use values like alloy; "
                            "Google Live supports prebuilt voices such as Kore or Puck."
                        ),
                    ),
                    tools_field(tool_options or []),
                    input_transcription_field(),
                    language_code_field(
                        description=(
                            "Optional language hint for xAI speech transcription, "
                            "such as en or de."
                        ),
                        i18n_description="schema_xai_realtime_language_desc",
                        placeholder="E.g. en or de",
                    ),
                    prefix_padding_field(
                        description=(
                            "Amount of audio retained before xAI detects speech, "
                            "in milliseconds."
                        ),
                        i18n_description="schema_xai_realtime_prefix_padding_desc",
                    ),
                    silence_duration_field(
                        description=(
                            "Silence xAI waits before ending a spoken turn, in "
                            "milliseconds."
                        ),
                        i18n_description="schema_xai_realtime_silence_duration_desc",
                    ),
                ],
            )
        ]
    )


def build_xai_realtime_websocket_url(
    provider: LLMProvider,
    model_name: str,
) -> str:
    """Build a native xAI realtime URL from provider settings."""
    settings = provider.settings if isinstance(provider.settings, dict) else {}
    parsed = urlparse(
        str(settings.get("base_url") or XAI_DEFAULT_BASE_URL).strip().rstrip("/")
    )
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="xAI realtime base URL is invalid")
    path = parsed.path.rstrip("/")
    if not path.endswith("/realtime"):
        path = f"{path}/realtime"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "model"
    ]
    query.append(("model", str(model_name or XAI_REALTIME_MODELS[0]).strip()))
    return urlunparse(
        (
            "wss" if parsed.scheme in {"https", "wss"} else "ws",
            parsed.netloc,
            path,
            "",
            urlencode(query),
            "",
        )
    )


def build_xai_realtime_headers(provider: LLMProvider) -> dict[str, str]:
    """Build safe upstream WebSocket headers for xAI."""
    settings = provider.settings if isinstance(provider.settings, dict) else {}
    headers: dict[str, str] = {
        "Authorization": f"Bearer {str(provider.api_key or '').strip()}",
    }
    forbidden = {
        "connection",
        "content-length",
        "host",
        "upgrade",
    }
    for key, value in custom_headers_to_dict(settings.get("custom_headers")).items():
        normalized_key = str(key).strip()
        if (
            normalized_key
            and normalized_key.lower() not in forbidden
            and not normalized_key.lower().startswith("sec-websocket-")
        ):
            headers[normalized_key] = str(value)
    return headers


def build_xai_realtime_session_config(
    *,
    instructions: str,
    voice: str,
    settings: dict[str, Any],
    tool_schemas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build xAI's server-owned Speech-to-Speech session configuration."""
    input_audio: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": 24_000},
        "transport": "json",
    }
    if bool(settings.get("input_transcription_enabled", True)):
        transcription: dict[str, Any] = {"model": "grok-transcribe"}
        language_hint = str(settings.get("language_code") or "").strip()
        if language_hint:
            transcription["language_hint"] = language_hint
        input_audio["transcription"] = transcription

    session: dict[str, Any] = {
        "instructions": instructions,
        "voice": normalize_xai_realtime_voice(voice),
        "audio": {
            "input": input_audio,
            "output": {
                "format": {"type": "audio/pcm", "rate": 24_000},
                "transport": "json",
            },
        },
    }
    session["turn_detection"] = {"type": "server_vad"}
    prefix_padding_ms = settings.get("prefix_padding_ms")
    silence_duration_ms = settings.get("silence_duration_ms")
    if isinstance(prefix_padding_ms, int) and 0 <= prefix_padding_ms <= 10_000:
        session["turn_detection"]["prefix_padding_ms"] = prefix_padding_ms
    if isinstance(silence_duration_ms, int) and 0 <= silence_duration_ms <= 10_000:
        session["turn_detection"]["silence_duration_ms"] = silence_duration_ms
    if tool_schemas:
        session["tools"] = tool_schemas
    return {"type": "session.update", "session": session}
