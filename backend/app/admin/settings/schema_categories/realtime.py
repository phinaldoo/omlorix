"""Shared validation and base fields for realtime conversation settings.

Provider-specific UI fields deliberately live beside their integrations under
``app.llm``.  This module owns the persisted settings envelope plus only the
steps every realtime setup needs: enable the feature and choose a provider.
The schema loader adds the model step and the selected provider's fragment.
"""

from typing import Any, Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class RealtimeSettings(BaseModel):
    """Validate realtime speech conversation defaults and tuning controls."""

    realtime_enabled: bool = False
    realtime_provider_id: str | None = None
    realtime_model: str | None = None
    realtime_voice: str | None = "alloy"
    realtime_tools: list[str] = Field(default_factory=list)
    realtime_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    realtime_max_output_tokens: int | None = Field(default=None, ge=1, le=4096)
    realtime_input_transcription_enabled: bool = True
    realtime_output_transcription_enabled: bool = True
    realtime_language_code: str | None = None
    realtime_enable_session_resumption: bool = True
    realtime_enable_context_window_compression: bool = True
    realtime_compression_trigger_tokens: int | None = Field(default=None, ge=1)
    realtime_compression_target_tokens: int | None = Field(default=None, ge=1)
    realtime_enable_affective_dialog: bool = False
    realtime_enable_proactive_audio: bool = False
    realtime_activity_handling: Literal[
        "START_OF_ACTIVITY_INTERRUPTS", "NO_INTERRUPTION"
    ] = "START_OF_ACTIVITY_INTERRUPTS"
    realtime_turn_coverage: Literal[
        "TURN_INCLUDES_ONLY_ACTIVITY", "TURN_INCLUDES_ALL_INPUT"
    ] = "TURN_INCLUDES_ONLY_ACTIVITY"
    realtime_start_sensitivity: Literal[
        "", "START_SENSITIVITY_HIGH", "START_SENSITIVITY_LOW"
    ] = ""
    realtime_end_sensitivity: Literal[
        "", "END_SENSITIVITY_HIGH", "END_SENSITIVITY_LOW"
    ] = ""
    realtime_prefix_padding_ms: int | None = Field(default=80, ge=0)
    realtime_silence_duration_ms: int | None = Field(default=700, ge=0)

    @field_validator("realtime_tools", mode="before")
    @classmethod
    def normalize_legacy_realtime_tools(cls, value: Any) -> Any:
        """Treat legacy null tool selections as an empty selection."""

        return [] if value is None else value


def build_realtime_model_field(provider_id: str) -> FieldSchema:
    """Build the model step for the currently selected realtime provider."""

    return FieldSchema(
        key="realtime_model",
        label="Realtime model",
        description="Select the realtime model for speech sessions.",
        type="select",
        options=[],
        placeholder="Select a realtime model",
        dependency="realtime_enabled",
        dependency_value=True,
        dependency2="realtime_provider_id",
        dependency2_value=provider_id,
    )


realtime_schema = Sections(
    sections=[
        Section(
            title="Realtime conversations",
            description=(
                "Configure the provider, model, and default voice for realtime "
                "speech-to-speech calls."
            ),
            fields=[
                FieldSchema(
                    key="realtime_enabled",
                    label="Enable realtime conversations",
                    description=(
                        "Show the call button in chat and allow speech-to-speech "
                        "sessions."
                    ),
                    type="boolean",
                ),
                FieldSchema(
                    key="realtime_provider_id",
                    label="Realtime provider",
                    description="Select a provider",
                    type="select",
                    options=[],
                    placeholder="Choose a realtime provider",
                    dependency="realtime_enabled",
                    dependency_value=True,
                ),
            ],
        ),
    ]
)
