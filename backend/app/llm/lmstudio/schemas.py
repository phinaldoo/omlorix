"""Backward-compatible schema API for the LM Studio integration.

Large schema builders live in focused sibling modules while this module keeps
the established public imports and runtime patching surface stable.
"""

from __future__ import annotations

# ruff: noqa: F401, E402

from datetime import date
from enum import Enum
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.llm.base_settings import BaseModelSettings
from app.llm.model_schemas import (
    MODEL_SCHEMA_FILE_SECTION,
    MODEL_SCHEMA_INFORMATION_SECTION,
    apply_model_mcp_schema_values,
    combine_model_schema_sections,
    get_model_schema_access_section,
    get_model_schema_modalities_section,
    get_model_schema_skill_section,
    get_model_schema_title_section,
    get_model_schema_tools_section,
)
from app.llm.reasoning_effort_options import build_reasoning_effort_options
from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
    _get_field_from_section,
    _remove_field_from_section,
    _set_schema_field_value,
    populate_sections_with_values,
)


class LMStudioSettings(BaseModel):
    base_url: str

    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True


class LMStudioInputFormatEnum(str, Enum):
    text = "text"
    image = "image"
    pdf = "pdf"
    text_document = "text_document"


class LMStudioOutputFormatEnum(str, Enum):
    text = "text"


LMSTUDIO_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this LM Studio connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="The display name used to identify this LM Studio provider.",
                    type="string",
                    placeholder="E.g. My LM Studio provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials & endpoint",
            description="Configure the credentials and native REST endpoint used to reach your LM Studio server.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="Optional API token used when LM Studio server authentication is enabled.",
                    type="string",
                    placeholder="E.g. lmstudio",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.base_url",
                    label="Base URL",
                    description="Root URL of your LM Studio server. Use the native server root, not the /v1 OpenAI-compatible path.",
                    type="string",
                    placeholder="E.g. http://localhost:1234",
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix reacts when LM Studio model availability changes.",
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
                    description="Automatically remove models that are no longer available on the LM Studio host.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when LM Studio models are added or removed.",
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


class LMStudioListModelByok(BaseModel):
    base_url: str
    api_key: str | None = None


class LMStudioModelSettings(
    BaseModelSettings[LMStudioInputFormatEnum, LMStudioOutputFormatEnum]
):
    input_formats: List[LMStudioInputFormatEnum] = Field(
        default_factory=lambda: [LMStudioInputFormatEnum.text]
    )
    output_formats: List[LMStudioOutputFormatEnum] = Field(
        default_factory=lambda: [LMStudioOutputFormatEnum.text]
    )
    training_data: Literal["true", "false", "unknown"] = "unknown"

    reasoning: bool = False
    reasoning_effort: (
        Literal[
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            # Retain legacy native values while saved settings are normalized.
            "on",
            "off",
        ]
        | None
    ) = None
    reasoning_context: Literal["auto", "current_turn", "all_turns"] = "auto"

    max_image_count: int = -1
    max_document_count: int = -1

    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    max_output_tokens: int | None = None


def _coerce_model_settings(value: Any) -> dict[str, Any]:
    """Convert saved settings to a mutable, endpoint-compatible mapping."""
    if value is None:
        settings: dict[str, Any] = {}
    elif isinstance(value, dict):
        settings = dict(value)
    elif hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        settings = value.model_dump()
    elif hasattr(value, "dict") and callable(getattr(value, "dict")):
        settings = value.dict()
    elif hasattr(value, "__dict__"):
        settings = {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    else:
        settings = {}

    # Existing models may contain native "on"/"off" values. Normalize the
    # admin-facing value so a subsequent save cannot reintroduce an invalid
    # Responses payload.
    from app.llm.lmstudio.utils import normalize_lmstudio_responses_reasoning_effort

    if settings.get("reasoning_effort") is not None:
        normalized_effort = normalize_lmstudio_responses_reasoning_effort(
            settings.get("reasoning_effort")
        )
        if normalized_effort is None:
            settings.pop("reasoning_effort", None)
        else:
            settings["reasoning_effort"] = normalized_effort
    return settings


def _lmstudio_modalities_for_model(
    model_info: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    caps = (
        (model_info or {}).get("capabilities") if isinstance(model_info, dict) else {}
    )
    vision = bool(isinstance(caps, dict) and caps.get("vision"))
    input_formats = ["text", "text_document"]
    if vision:
        input_formats.extend(["image", "pdf"])
    return input_formats, ["text"]


def _lmstudio_tools_supported(model_info: dict[str, Any] | None) -> bool:
    """Return whether LM Studio can expose its fallback or native tool format."""
    if not isinstance(model_info, dict):
        return False
    # LM Studio documents default tool-use support for every LLM. The
    # trained_for_tool_use capability only indicates the higher-quality native
    # prompt template and must not be used to disable tools entirely.
    return str(model_info.get("type") or "").strip().lower() == "llm"


def _lmstudio_reasoning_options(model_info: dict[str, Any] | None) -> list[str]:
    """Return model-advertised reasoning modes valid for `/v1/responses`."""
    from app.llm.lmstudio.utils import normalize_lmstudio_responses_reasoning_effort

    caps = (
        (model_info or {}).get("capabilities") if isinstance(model_info, dict) else {}
    )
    reasoning = caps.get("reasoning") if isinstance(caps, dict) else {}
    raw_options = (
        reasoning.get("allowed_options") if isinstance(reasoning, dict) else []
    )
    normalized: list[str] = []
    seen: set[str] = set()
    for option in raw_options or []:
        value = normalize_lmstudio_responses_reasoning_effort(option)
        # "none" is represented by the separate Enable reasoning toggle.
        if not value or value == "none" or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _build_reasoning_schema(
    options: list[str], *, model_settings: dict[str, Any]
) -> list[Section]:
    if (
        not options
        and model_settings.get("reasoning") in (None, False)
        and not model_settings.get("reasoning_effort")
    ):
        return []
    effort_options = build_reasoning_effort_options(
        [
            option
            for option in options
            if option in {"minimal", "low", "medium", "high", "xhigh"}
        ]
    )
    section = Section(
        title="Reasoning",
        description="Configure reasoning for models that expose LM Studio reasoning modes.",
        fields=[
            FieldSchema(
                key="settings.reasoning",
                label="Enable reasoning",
                description="Enable reasoning when the selected LM Studio model supports it.",
                type="boolean",
                required=False,
            ),
            FieldSchema(
                key="settings.reasoning_effort",
                label="Reasoning effort",
                description="Select the reasoning effort used by the model.",
                type="select",
                options=effort_options,
                required=False,
                dependency="settings.reasoning",
                dependency_value=True,
            ),
            FieldSchema(
                key="settings.reasoning_context",
                label="Reasoning context",
                description="Control whether compatible reasoning state from earlier turns is reused.",
                i18n_label="llm.openai.reasoning_context.label",
                i18n_description="llm.openai.reasoning_context.description",
                type="select",
                options=[
                    Option(
                        value="auto",
                        label="Automatic",
                        i18n_label="llm.openai.reasoning_context.auto",
                    ),
                    Option(
                        value="current_turn",
                        label="Current turn",
                        i18n_label="llm.openai.reasoning_context.current_turn",
                    ),
                    Option(
                        value="all_turns",
                        label="All turns",
                        i18n_label="llm.openai.reasoning_context.all_turns",
                    ),
                ],
                default="auto",
                required=False,
                dependency="settings.reasoning",
                dependency_value=True,
            ),
        ],
    )
    return [section]


def _build_generation_section(model_settings: dict[str, Any]) -> list[Section]:
    defaults = dict(model_settings or {})
    return [
        Section(
            title="Generation parameters",
            description="Control the LM Studio OpenAI-compatible response request.",
            fields=[
                FieldSchema(
                    key="settings.temperature",
                    label="Temperature",
                    description="Sampling temperature for the response.",
                    type="string",
                    input_type="float",
                    required=False,
                    value=defaults.get("temperature"),
                ),
                FieldSchema(
                    key="settings.top_p",
                    label="Top P",
                    description="Nucleus sampling value.",
                    type="string",
                    input_type="float",
                    required=False,
                    value=defaults.get("top_p"),
                ),
                FieldSchema(
                    key="settings.frequency_penalty",
                    label="Frequency penalty",
                    description="Penalty applied to repeated tokens.",
                    type="string",
                    input_type="float",
                    required=False,
                    value=defaults.get("frequency_penalty"),
                ),
                FieldSchema(
                    key="settings.presence_penalty",
                    label="Presence penalty",
                    description="Penalty that encourages topic shifts.",
                    type="string",
                    input_type="float",
                    required=False,
                    value=defaults.get("presence_penalty"),
                ),
                FieldSchema(
                    key="settings.max_output_tokens",
                    label="Max output tokens",
                    description="Maximum number of output tokens to generate.",
                    type="string",
                    input_type="int",
                    required=False,
                    value=defaults.get("max_output_tokens"),
                ),
            ],
        )
    ]


def _apply_model_caps_to_schema(
    schema: Sections, model_info: dict[str, Any] | None
) -> None:
    input_formats, output_formats = _lmstudio_modalities_for_model(model_info)
    modalities_section = "Modalities & platform limits"
    input_field = _get_field_from_section(
        schema.sections, modalities_section, "settings.input_formats"
    )
    output_field = _get_field_from_section(
        schema.sections, modalities_section, "settings.output_formats"
    )
    if input_field:
        input_field.options = [Option(value=item, label=item) for item in input_formats]
    if output_field:
        output_field.options = [
            Option(value=item, label=item) for item in output_formats
        ]

    if "image" not in input_formats:
        _remove_field_from_section(
            schema.sections, "File attachments", "settings.max_image_count"
        )
    if "pdf" not in input_formats and "text_document" not in input_formats:
        _remove_field_from_section(
            schema.sections, "File attachments", "settings.max_document_count"
        )

    if not _lmstudio_tools_supported(model_info):
        schema.sections = [
            section
            for section in schema.sections
            if section.title != "Tools & enrichment"
        ]


def get_lmstudio_model_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import model_schema as _implementation

    _implementation._sync_compat_dependencies("get_lmstudio_model_schema", globals())
    return _implementation._impl_get_lmstudio_model_schema(*args, **kwargs)


def get_lmstudio_model_schema_parameter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import parameter_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_lmstudio_model_schema_parameter", globals()
    )
    return _implementation._impl_get_lmstudio_model_schema_parameter(*args, **kwargs)
