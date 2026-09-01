"""Backward-compatible schema API for Google AI Studio.

Large schema builders live in focused sibling modules. This module preserves
the historical public imports and runtime dependency-patching surface.
"""

# ruff: noqa: F401, E402

from datetime import date, datetime
from pydantic import BaseModel, model_validator
from typing import Literal
from typing import List
from enum import Enum

from app.llm.base_settings import BaseModelSettings
from app.llm.model_schemas import (
    MODEL_SCHEMA_INFORMATION_SECTION,
    apply_model_mcp_schema_values,
    combine_model_schema_sections,
    get_model_schema_modalities_section,
    MODEL_SCHEMA_FILE_SECTION,
    get_model_schema_access_section,
    get_model_schema_title_section,
    get_model_schema_tools_section,
    get_model_schema_skill_section,
)
from app.llm.google_aistudio.model_list import (
    AISTUDIO_MODEL_DICT,
)
from app.llm.google_aistudio.description import normalize_aistudio_model_description
from app.llm.reasoning_effort_options import build_reasoning_effort_options
from app.utils.schemas import (
    FieldSchema,
    FieldAttributes,
    Option,
    Section,
    Sections,
    _get_field_from_section,
    _remove_field_from_section,
    _set_schema_field_value,
)


# -------------------
# Create Provider
# -------------------
class CreateProviderGoogleAistudio(BaseModel):
    name: str
    api_key: str
    settings: "GoogleAistudioSettings"


class GoogleAistudioSettings(BaseModel):
    api_version: "GoogleAiStudioApiVersionEnum" = "v1beta"

    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True


class GoogleAiStudioApiVersionEnum(str, Enum):
    v1beta = "v1beta"
    v1 = "v1"
    v1alpha = "v1alpha"


class GoogleAiStudioSafetyThresholdEnum(str, Enum):
    unspecified = "HARM_BLOCK_THRESHOLD_UNSPECIFIED"
    off = "OFF"
    block_none = "BLOCK_NONE"
    block_only_high = "BLOCK_ONLY_HIGH"
    block_medium_and_above = "BLOCK_MEDIUM_AND_ABOVE"
    block_low_and_above = "BLOCK_LOW_AND_ABOVE"


# -------------------
# Provider Schema
# -------------------
GOOGLE_AISTUDIO_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Google AI Studio connection appears for administrators.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name used to identify this Google AI Studio provider.",
                    type="string",
                    placeholder="E.g. My Google AI Studio provider",
                    required=True,
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials",
            description="Configure the API key used to call Google AI Studio services.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="API key used to authenticate requests to Google AI Studio.",
                    type="string",
                    placeholder="E.g. AIzaSyExampleKey1234567890",
                    required=True,
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Platform configuration",
            description="Select the API version that matches your Google AI Studio project.",
            fields=[
                FieldSchema(
                    key="settings.api_version",
                    label="API version",
                    description="Version of the Google AI Studio API to target.",
                    type="select",
                    default=GoogleAiStudioApiVersionEnum.v1beta.value,
                    options=[
                        Option(
                            value=GoogleAiStudioApiVersionEnum.v1beta.value,
                            label="v1beta",
                            i18n_label="llm.shared.option.v1beta",
                        ),
                        Option(
                            value=GoogleAiStudioApiVersionEnum.v1.value,
                            label="v1",
                            i18n_label="llm.shared.option.v1",
                        ),
                        Option(
                            value=GoogleAiStudioApiVersionEnum.v1alpha.value,
                            label="v1alpha",
                            i18n_label="llm.shared.option.v1alpha",
                        ),
                    ],
                    required=True,
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix handles model availability changes from Google AI Studio.",
            fields=[
                FieldSchema(
                    key="settings.disable_background_sync",
                    label="Disable regular provider requests",
                    description="Skip recurring background requests to this provider, such as periodic model list synchronization.",
                    type="boolean",
                    default=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_auto_delete_missing_models",
                    label="Auto-delete missing models",
                    description="Automatically remove models that are no longer available from Google AI Studio.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when the available Google AI Studio models change.",
                    type="boolean",
                    default=True,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
            ],
        ),
    ]
)


# -------------------
# List models byok
# -------------------
class GoogleAiStudioListModelsByok(BaseModel):
    api_key: str
    api_version: str | None = None


# -------------------
# Model Settings Enums
# -------------------
class InputFormatEnum(str, Enum):
    audio = "audio"
    image = "image"
    video = "video"
    text = "text"
    pdf = "pdf"
    text_document = "text_document"


class OutputFormatEnum(str, Enum):
    text = "text"


# -------------------
# Model Settings
# -------------------
class GoogleAiStudioModelSettings(BaseModelSettings[InputFormatEnum, OutputFormatEnum]):
    training_data: Literal["true", "false", "unknown"]

    # Thinking
    thinking: bool | None = None
    thinking_budget: int | None = (
        None  # For 2.5 Pro this cannot be 0/disabled, -1 is dynamic thinking
    )
    thinking_dynamic: bool | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    include_thinking: bool | None = True

    max_image_count: int = (
        -1
    )  # Per request (chat history + current message), if none -> unlimited
    max_video_count: int = (
        -1
    )  # Per request (chat history + current message), if none -> unlimited
    max_audio_count: int = (
        -1
    )  # Per request (chat history + current message), if none -> unlimited
    max_document_count: int = (
        -1
    )  # Per request (chat history + current message), if none -> unlimited
    native_youtube_video: bool  # Rate limits: only 1 video for 2.5 Pro, 10 Videos for 2.5 Flash per Request
    max_youtube_video_count: int = -1  # Per request (chat history + current message), if none -> unlimited (native url of aistudio, not the metdata like transcript)
    native_websearch: bool = False

    # Generation params (optional)
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_output_tokens: int | None = None
    stop_sequences: List[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    video_fps: float | None = None
    media_resolution: Literal["low", "medium", "high"] | None = None
    safety_harassment: GoogleAiStudioSafetyThresholdEnum = (
        GoogleAiStudioSafetyThresholdEnum.unspecified
    )
    safety_hate_speech: GoogleAiStudioSafetyThresholdEnum = (
        GoogleAiStudioSafetyThresholdEnum.unspecified
    )
    safety_sexually_explicit: GoogleAiStudioSafetyThresholdEnum = (
        GoogleAiStudioSafetyThresholdEnum.unspecified
    )
    safety_dangerous_content: GoogleAiStudioSafetyThresholdEnum = (
        GoogleAiStudioSafetyThresholdEnum.unspecified
    )
    safety_civic_integrity: GoogleAiStudioSafetyThresholdEnum = (
        GoogleAiStudioSafetyThresholdEnum.unspecified
    )

    @model_validator(
        mode="after"
    )  # For models which enforce thinking, thinking toggle is not shown
    def set_thinking_default(self):
        if self.thinking is None:
            self.thinking = False
        return self


# ---------------
# Image Support
# ---------------
google_ai_studio_image_mime_types = [
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
]


# ---------------
# Document Support
# ---------------
google_ai_studio_document_mime_types = [
    "application/pdf",
    "application/json",
    "application/xml",
    "application/rtf",
    "text/plain",
    "text/html",
    "text/css",
    "text/csv",
    "text/markdown",
    "text/calendar",
    "text/javascript",
    "text/richtext",
    "text/xml",
]


# ---------------
# Audio Support
# ---------------
google_ai_studio_audio_mime_types = [
    "audio/wav",
    "audio/mp3",
    "audio/aiff",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
]


# ---------------
# Video Support
# ---------------
google_ai_studio_video_mime_types = [
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/quicktime",
    "video/avi",
    "video/x-flv",
    "video/mpg",
    "video/webm",
    "video/wmv",
    "video/3gpp",
]


# -------------------
# Google AI Studio supported languages
# -------------------
google_aistudio_supported_languages = [
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "zh",  # Chinese (Simplified & Traditional)
    "hr",  # Croatian
    "cs",  # Czech
    "da",  # Danish
    "nl",  # Dutch
    "en",  # English
    "et",  # Estonian
    "fi",  # Finnish
    "fr",  # French
    "de",  # German
    "el",  # Greek
    "iw",  # Hebrew
    "hi",  # Hindi
    "hu",  # Hungarian
    "id",  # Indonesian
    "it",  # Italian
    "ja",  # Japanese
    "ko",  # Korean
    "lv",  # Latvian
    "lt",  # Lithuanian
    "no",  # Norwegian
    "pl",  # Polish
    "pt",  # Portuguese
    "ro",  # Romanian
    "ru",  # Russian
    "sr",  # Serbian
    "sk",  # Slovak
    "sl",  # Slovenian
    "es",  # Spanish
    "sw",  # Swahili
    "sv",  # Swedish
    "th",  # Thai
    "tr",  # Turkish
    "uk",  # Ukrainian
    "vi",  # Vietnamese
]


GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION = Sections(
    sections=[
        Section(
            title="Thinking",
            description="Enable thinking mode.",
            fields=[
                FieldSchema(
                    key="settings.thinking",
                    label="Thinking",
                    description="Enable thinking mode.",
                    type="boolean",
                    required=False,
                ),
                FieldSchema(
                    key="settings.thinking_dynamic",
                    label="Dynamic thinking",
                    description="Model decides when and how much to think.",
                    type="boolean",
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="settings.thinking_budget",
                    label="Thinking budget",
                    description="Maximum number of tokens allowed in the thinking process.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                    dependency2="settings.thinking_dynamic",
                    dependency2_value=False,
                ),
                FieldSchema(
                    key="settings.reasoning_effort",
                    label="Thinking level",
                    description="Thinking level of the model.",
                    type="select",
                    options=build_reasoning_effort_options(
                        ["minimal", "low", "medium", "high"]
                    ),
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                    dependency2="settings.thinking_dynamic",
                    dependency2_value=False,
                ),
                FieldSchema(
                    key="settings.include_thinking",
                    label="Include thinking",
                    description="Include the thinking trace in the model response.",
                    type="boolean",
                    default=True,
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                ),
            ],
        ),
    ]
)


GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS = [
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
        label="Default",
        i18n_label="llm.shared.option.default",
    ),
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.off.value,
        label="Off",
        i18n_label="llm.shared.option.off",
    ),
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.block_none.value,
        label="Block none",
        i18n_label="llm.shared.option.block_none",
    ),
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.block_only_high.value,
        label="Block only high",
        i18n_label="llm.shared.option.block_only_high",
    ),
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.block_medium_and_above.value,
        label="Block medium and above",
        i18n_label="llm.shared.option.block_medium_and_above",
    ),
    Option(
        value=GoogleAiStudioSafetyThresholdEnum.block_low_and_above.value,
        label="Block low and above",
        i18n_label="llm.shared.option.block_low_and_above",
    ),
]


def get_aistudio_safety_schema_filled(model_settings: dict | None = None):
    schema = Sections(
        sections=[
            Section(
                title="Safety settings",
                description="Set Gemini harm-block thresholds that will be sent with every Google AI Studio request.",
                fields=[
                    FieldSchema(
                        key="settings.safety_harassment",
                        label="Harassment",
                        description="Blocking threshold for harassment content.",
                        type="select",
                        default=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
                        options=GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS,
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.safety_hate_speech",
                        label="Hate speech",
                        description="Blocking threshold for hate speech content.",
                        type="select",
                        default=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
                        options=GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS,
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.safety_sexually_explicit",
                        label="Sexually explicit",
                        description="Blocking threshold for sexually explicit content.",
                        type="select",
                        default=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
                        options=GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS,
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.safety_dangerous_content",
                        label="Dangerous content",
                        description="Blocking threshold for dangerous content.",
                        type="select",
                        default=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
                        options=GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS,
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.safety_civic_integrity",
                        label="Civic integrity",
                        description="Blocking threshold for civic integrity content.",
                        type="select",
                        default=GoogleAiStudioSafetyThresholdEnum.unspecified.value,
                        options=GOOGLE_AISTUDIO_SAFETY_THRESHOLD_OPTIONS,
                        required=False,
                    ),
                ],
            ),
        ]
    )
    for field_key in (
        "settings.safety_harassment",
        "settings.safety_hate_speech",
        "settings.safety_sexually_explicit",
        "settings.safety_dangerous_content",
        "settings.safety_civic_integrity",
    ):
        value = (model_settings or {}).get(field_key.split(".")[-1])
        if value is not None:
            _set_schema_field_value(schema, field_key, value)
    return schema


def get_parameters_schema_filled(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation_schema as _implementation

    _implementation._sync_compat_dependencies("get_parameters_schema_filled", globals())
    return _implementation._impl_get_parameters_schema_filled(*args, **kwargs)


def get_aistudio_model_info(db, provider_id, model_name: str):
    from app.llm.google_aistudio.utils import get_aistudio_model

    """Return the canonical model group name and schema for *model_name*.

    Example: ``gemini-2.5-pro-preview-05-06`` -> ("gemini-2.5-pro", {...}).
    """
    model_group = None
    for group_name, schema in AISTUDIO_MODEL_DICT.items():
        identifiers: list[str] = schema.get("ids", []) or []
        if model_name == group_name or model_name in identifiers:
            model_group = group_name
            break

    model_group_dict = AISTUDIO_MODEL_DICT.get(model_group)
    model_info = get_aistudio_model(db, model_name, provider_id)
    return {
        "model_name": model_name,
        "display_name": model_info.display_name,
        "description": normalize_aistudio_model_description(model_info.description),
        "input_token_limit": model_info.input_token_limit,
        "output_token_limit": model_info.output_token_limit,
        "model_group_dict": model_group_dict,
    }


def get_aistudio_model_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import model_schema as _implementation

    _implementation._sync_compat_dependencies("get_aistudio_model_schema", globals())
    return _implementation._impl_get_aistudio_model_schema(*args, **kwargs)


from app.llm.model_schemas import get_parameter_basic_schema


def get_aistudio_model_schema_parameter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import parameter_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_aistudio_model_schema_parameter", globals()
    )
    return _implementation._impl_get_aistudio_model_schema_parameter(*args, **kwargs)
