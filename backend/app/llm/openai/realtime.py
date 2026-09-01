from __future__ import annotations

import logging

from app.llm.openai.model_list import (
    OPENAI_DEPRECATED_MODELS,
    OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS,
)
from app.llm.openai.utils import _resolve_openai_client_kwargs
from app.llm.schemas import ProviderEnum
from fastapi import HTTPException
from openai import Client
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# OpenAI documents these built-in voices for Realtime.  Keep the catalog next
# to the transport that consumes it instead of in the shared admin schema.
OPENAI_REALTIME_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)


def get_openai_realtime_models(
    *,
    db: Session | None = None,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str = ProviderEnum.openai.value,
) -> list[str]:
    """Get OpenAI realtime models."""
    provider_identifier = (openai_provider_id or "").strip() or None
    if provider_identifier and db is None:
        raise HTTPException(
            status_code=500,
            detail="Database session is required to resolve OpenAI realtime provider credentials",
        )

    client_kwargs = _resolve_openai_client_kwargs(
        db,
        openai_provider_id=provider_identifier,
        byok=byok,
        openai_provider_type=openai_provider_type,
    )
    client = Client(**client_kwargs)

    deprecated_models = set(OPENAI_DEPRECATED_MODELS)
    transcription_only_models = set(OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS)
    discovered: list[str] = []
    try:
        models = client.models.list()
        for model in models or []:
            model_id = str(getattr(model, "id", "") or "").strip()
            if not model_id:
                continue
            normalized = model_id.lower()
            # The upstream list endpoint may continue returning a model after
            # its shutdown date. Deprecation is authoritative in Omlorix, so do
            # not expose those identifiers in settings or runtime validation.
            if (
                "realtime" in normalized
                and model_id not in deprecated_models
                and model_id not in transcription_only_models
                and model_id not in discovered
            ):
                discovered.append(model_id)
    except Exception as exc:
        logger.exception("Failed to list OpenAI realtime models")
        raise HTTPException(status_code=424, detail=f"Failed to list OpenAI realtime models: {exc}") from exc

    return discovered


def get_realtime_settings_schema(*, tool_options: list[dict] | None = None):
    """Return only controls consumed by OpenAI-compatible realtime sessions."""

    from app.llm.realtime_schema import (
        input_transcription_field,
        max_output_tokens_field,
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
                        [
                            {"value": voice, "label": voice.title()}
                            for voice in OPENAI_REALTIME_VOICES
                        ],
                        description=(
                            "Voice used for assistant speech output in realtime "
                            "sessions. OpenAI examples use values like alloy; "
                            "Google Live supports prebuilt voices such as Kore or Puck."
                        ),
                    ),
                    tools_field(tool_options or []),
                    max_output_tokens_field(),
                    input_transcription_field(),
                ],
            )
        ]
    )
