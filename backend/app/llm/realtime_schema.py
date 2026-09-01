"""Reusable field builders for provider-owned realtime admin schemas.

The builders keep labels, translation keys, constraints, and accessibility
metadata consistent without putting provider capability unions back into the
shared admin settings schema.
"""

from collections.abc import Sequence

from app.utils.schemas import FieldAttributes, FieldSchema, Option


def voice_field(
    options: Sequence[Option | dict],
    *,
    description: str,
) -> FieldSchema:
    """Build a provider-populated realtime voice picker."""

    return FieldSchema(
        key="realtime_voice",
        label="Default voice",
        description=description,
        type="select",
        options=options,
        placeholder="Select a voice",
        i18n_placeholder="llm.shared.voice.placeholder",
        dependency="realtime_enabled",
        dependency_value=True,
    )


def tools_field(options: Sequence[Option | dict] = ()) -> FieldSchema:
    """Build the tool allow-list used by every supported realtime transport."""

    return FieldSchema(
        key="realtime_tools",
        label="Realtime tools",
        description=(
            "Choose which tools the realtime model can use during calls. "
            "Leave empty to disable tool access."
        ),
        type="select",
        options=options,
        multiple=True,
        placeholder="Select realtime tools",
        dependency="realtime_enabled",
        dependency_value=True,
        i18n_label="schema_models_realtime_tools",
        i18n_description="schema_models_realtime_tools_desc",
        i18n_placeholder="schema_models_realtime_tools_placeholder",
    )


def input_transcription_field() -> FieldSchema:
    """Build the server-side user-speech transcription toggle."""

    return FieldSchema(
        key="realtime_input_transcription_enabled",
        label="Input transcription",
        description="Enable server-side transcription updates for user speech.",
        type="boolean",
        dependency="realtime_enabled",
        dependency_value=True,
    )


def output_transcription_field() -> FieldSchema:
    """Build the server-side assistant-speech transcription toggle."""

    return FieldSchema(
        key="realtime_output_transcription_enabled",
        label="Output transcription",
        description="Enable server-side transcription updates for assistant speech.",
        type="boolean",
        dependency="realtime_enabled",
        dependency_value=True,
    )


def max_output_tokens_field() -> FieldSchema:
    """Build the bounded response-token control."""

    return FieldSchema(
        key="realtime_max_output_tokens",
        label="Realtime max output tokens",
        description="Cap the size of each generated realtime response when supported.",
        type="number",
        input_type="int",
        placeholder="Leave empty for provider default",
        dependency="realtime_enabled",
        dependency_value=True,
        attributes=FieldAttributes(min=1, max=4096, step=1),
    )


def language_code_field(
    *,
    description: str,
    i18n_description: str | None = None,
    placeholder: str = "E.g. en-US",
) -> FieldSchema:
    """Build a provider-specific speech language hint."""

    return FieldSchema(
        key="realtime_language_code",
        label="Speech language code",
        description=description,
        i18n_description=i18n_description,
        type="string",
        placeholder=placeholder,
        dependency="realtime_enabled",
        dependency_value=True,
    )


def prefix_padding_field(
    *,
    description: str,
    i18n_description: str | None = None,
) -> FieldSchema:
    """Build a provider-specific speech prefix-padding control."""

    return FieldSchema(
        key="realtime_prefix_padding_ms",
        label="Prefix padding (ms)",
        description=description,
        i18n_description=i18n_description,
        type="number",
        input_type="int",
        dependency="realtime_enabled",
        dependency_value=True,
        attributes=FieldAttributes(min=0, step=10),
    )


def silence_duration_field(
    *,
    description: str,
    i18n_description: str | None = None,
) -> FieldSchema:
    """Build a provider-specific end-of-turn silence control."""

    return FieldSchema(
        key="realtime_silence_duration_ms",
        label="Silence duration (ms)",
        description=description,
        i18n_description=i18n_description,
        type="number",
        input_type="int",
        dependency="realtime_enabled",
        dependency_value=True,
        attributes=FieldAttributes(min=0, step=10),
    )
