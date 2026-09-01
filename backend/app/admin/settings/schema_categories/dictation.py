"""Schemas for file and live dictation settings."""

from typing import Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, constr


class DictationSettings(BaseModel):
    """Validate completed-file and live microphone transcription settings."""

    transcription_enabled: bool = False
    transcription_provider_id: str | None = None
    transcription_model: str | None = None
    live_transcription_enabled: bool = False
    live_transcription_provider_id: str | None = None
    live_transcription_model: str | None = None
    live_transcription_delay: Literal["minimal", "low", "medium", "high", "xhigh"] = (
        "low"
    )
    live_transcription_xai_language: constr(max_length=35) | None = None
    live_transcription_xai_endpointing_ms: int = Field(default=10, ge=0, le=5000)
    live_transcription_xai_keyterms: list[constr(min_length=1, max_length=50)] = Field(
        default_factory=list,
        max_length=100,
    )
    live_transcription_xai_filler_words: bool = False
    live_transcription_xai_smart_turn: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    live_transcription_xai_smart_turn_timeout_ms: int | None = Field(
        default=None,
        ge=1,
        le=5000,
    )
    live_transcription_xai_vad_threshold: float = Field(default=0.08, ge=0.0, le=1.0)


def build_file_transcription_model_field(provider_id: str) -> FieldSchema:
    """Build the file-transcription model step for one selected provider.

    The field is intentionally not part of :data:`dictation_schema`.  Keeping
    it out of the base schema means an empty provider selection cannot render
    a misleading, empty model picker.  The admin schema loader adds this step
    after it has validated the selected provider.
    """

    return FieldSchema(
        key="transcription_model",
        label="File transcription model",
        description=(
            "Choose the model used for meeting media and fallback "
            "microphone recordings."
        ),
        type="select",
        options=[],
        placeholder="Select a transcription model",
        dependency="transcription_enabled",
        dependency_value=True,
        dependency2="transcription_provider_id",
        dependency2_value=provider_id,
        i18n_label="schema_models_transcription_model",
        i18n_description="schema_models_transcription_model_desc",
    )


def build_live_transcription_model_field(provider_id: str) -> FieldSchema:
    """Build the live-dictation model step for one selected provider."""

    return FieldSchema(
        key="live_transcription_model",
        label="Live chat model",
        description="Choose the model used for streamed chat microphone transcription.",
        type="select",
        options=[],
        placeholder="Select a live transcription model",
        dependency="live_transcription_enabled",
        dependency_value=True,
        dependency2="live_transcription_provider_id",
        dependency2_value=provider_id,
        i18n_label="schema_models_live_transcription_model",
        i18n_description="schema_models_live_transcription_model_desc",
        i18n_placeholder="models_select_live_transcription_placeholder",
    )


dictation_schema = Sections(
    sections=[
        Section(
            title="File & meeting transcription",
            description=(
                "Used for meeting uploads and recorded meeting audio. It also "
                "provides the fallback for chatbox and message-edit microphone "
                "dictation when live transcription is unavailable."
            ),
            i18n_title="schema_models_sec1_title",
            i18n_description="schema_models_sec1_desc",
            fields=[
                FieldSchema(
                    key="transcription_enabled",
                    label="Enable file & meeting transcription",
                    description=(
                        "Enable transcription of uploaded or recorded meeting "
                        "media and the non-live microphone fallback."
                    ),
                    type="boolean",
                    i18n_label="schema_models_transcription_enabled",
                    i18n_description="schema_models_transcription_enabled_desc",
                ),
                FieldSchema(
                    key="transcription_provider_id",
                    label="File transcription provider",
                    description=(
                        "Select the provider used for meeting media and fallback "
                        "microphone recordings."
                    ),
                    type="select",
                    options=[],
                    placeholder="Choose a transcription provider",
                    dependency="transcription_enabled",
                    dependency_value=True,
                    i18n_label="schema_models_transcription_provider_id",
                    i18n_description="schema_models_transcription_provider_id_desc",
                ),
            ],
        ),
        Section(
            title="Live chat dictation",
            description=(
                "Used first for chatbox and message-edit microphone dictation "
                "in supported browsers. When both sections are enabled, "
                "meetings still use File & meeting transcription."
            ),
            i18n_title="schema_models_live_transcription_sec_title",
            i18n_description="schema_models_live_transcription_sec_desc",
            fields=[
                FieldSchema(
                    key="live_transcription_enabled",
                    label="Enable live chat dictation",
                    description=(
                        "Stream microphone audio and place partial text directly "
                        "in the chat textarea. Falls back to File & meeting "
                        "transcription when unavailable."
                    ),
                    type="boolean",
                    i18n_label="schema_models_live_transcription_enabled",
                    i18n_description="schema_models_live_transcription_enabled_desc",
                ),
                FieldSchema(
                    key="live_transcription_provider_id",
                    label="Live chat provider",
                    description=(
                        "Select the configured provider used for streamed chat "
                        "microphone transcription. Provider-specific controls "
                        "appear after selection."
                    ),
                    type="select",
                    options=[],
                    placeholder="Choose a live transcription provider",
                    dependency="live_transcription_enabled",
                    dependency_value=True,
                    i18n_label="schema_models_live_transcription_provider_id",
                    i18n_description="schema_models_live_transcription_provider_id_desc",
                    i18n_placeholder="models_select_live_transcription_provider_placeholder",
                ),
            ],
        ),
    ]
)
