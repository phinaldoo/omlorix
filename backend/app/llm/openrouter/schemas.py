"""Backward-compatible schema API for the OpenRouter integration.

Large schema builders are organized in focused sibling modules while this
module retains the established public import and dependency-patching surface.
"""

# ruff: noqa: F401, E402

import ast
import json
from datetime import date, datetime
from enum import Enum
from typing import List, Literal, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from app.llm.base_settings import BaseModelSettings

from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
    _set_schema_field_value,
    _remove_field_from_section,
    _remove_section_from_sections,
    _get_field_from_section,
)
from app.llm.model_schemas import (
    MODEL_SCHEMA_INFORMATION_SECTION,
    MODEL_SCHEMA_FILE_SECTION,
    apply_model_mcp_schema_values,
    combine_model_schema_sections,
    get_model_schema_access_section,
    get_model_schema_title_section,
    get_model_schema_modalities_section,
    get_model_schema_tools_section,
    get_model_schema_skill_section,
    get_parameter_basic_schema,
)
from app.llm.reasoning_effort_options import build_reasoning_effort_options


def _normalize_supported_parameters(raw_value) -> set[str]:
    if raw_value is None:
        return set()

    iterable = None
    if isinstance(raw_value, dict):
        iterable = raw_value.keys()
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        parsed = None
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                try:
                    parsed = ast.literal_eval(text)
                except Exception:
                    parsed = None

        if isinstance(parsed, dict):
            iterable = parsed.keys()
        elif isinstance(parsed, (list, tuple, set)):
            iterable = parsed
        elif text:
            iterable = [part.strip() for part in text.split(",") if part.strip()]
        else:
            iterable = []
    elif isinstance(raw_value, (list, tuple, set)):
        iterable = raw_value
    else:
        iterable = []

    supported: set[str] = set()
    for item in iterable or []:
        if item is None:
            continue
        normalized = str(item).strip().lower()
        if normalized:
            supported.add(normalized)
    return supported


def _prune_generation_parameters_section(
    schema_sections: list[Section],
    supported_parameters: set[str],
    *,
    section_title: str = "Generation parameters",
):
    generation_section = next(
        (
            section
            for section in (schema_sections or [])
            if section.title == section_title
        ),
        None,
    )
    if not generation_section:
        return

    pruned_fields: list[FieldSchema] = []
    for field in generation_section.fields or []:
        field_key = getattr(field, "key", None)
        if not isinstance(field_key, str) or not field_key.startswith("settings."):
            pruned_fields.append(field)
            continue

        param_name = field_key.split(".", 1)[1].strip().lower()
        # The model catalog reports capabilities shared with Chat Completions.
        # Only expose controls that the OpenRouter Responses request schema can
        # actually serialize; otherwise a valid-looking setting can make every
        # chat request fail with invalid_prompt.
        if (
            param_name in supported_parameters
            and param_name in openrouter_model_parameters
        ):
            pruned_fields.append(field)

    generation_section.fields = pruned_fields
    if not (generation_section.fields or []):
        _remove_section_from_sections(schema_sections, section_title)


def infer_reasoning_mode_from_settings(model_settings: dict | None) -> str | None:
    if not isinstance(model_settings, dict):
        return None

    raw_mode = model_settings.get("reasoning_mode")
    if isinstance(raw_mode, str):
        normalized = raw_mode.strip().lower()
        if normalized in {"effort", "budget"}:
            return normalized

    effort_value = model_settings.get("reasoning_effort")
    if isinstance(effort_value, str):
        has_effort = bool(effort_value.strip())
    else:
        has_effort = effort_value not in (None, "")
    if has_effort:
        return "effort"

    budget_value = model_settings.get("reasoning_max_tokens")
    if isinstance(budget_value, str):
        budget_value = budget_value.strip()
    has_budget = budget_value not in (None, "")
    if has_budget:
        return "budget"

    return None


def get_openrouter_parameters_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_openrouter_parameters_schema", globals()
    )
    return _implementation._impl_get_openrouter_parameters_schema(*args, **kwargs)


def get_openrouter_model_schema_parameter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import parameter_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_openrouter_model_schema_parameter", globals()
    )
    return _implementation._impl_get_openrouter_model_schema_parameter(*args, **kwargs)


# Enums
class InputFormatEnum(str, Enum):
    audio = "audio"
    image = "image"
    video = "video"
    text = "text"
    pdf = "pdf"
    text_document = "text_document"


CONDITIONAL_INPUT_MODALITIES = {"audio", "image", "video"}


class OutputFormatEnum(str, Enum):
    text = "text"


# -------------------
# OpenRouter
# -------------------
class CreateProviderOpenrouter(BaseModel):
    name: str
    api_key: str
    settings: "OpenrouterSettings"


class ListOpenrouterModelsRequest(BaseModel):
    openrouter_provider_id: str | None = None
    api_key: str | None = None
    parameters: list[str] | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.openrouter_provider_id:
            raise ValueError(
                "BYOK model listing requires an API key instead of a stored provider ID."
            )
        if not self.api_key:
            raise ValueError("Provide 'api_key'.")
        return self


class OpenrouterSettings(BaseModel):
    ranking_url: str | None = None
    ranking_title: str | None = None
    eu_routing: bool = False

    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True


OPENROUTER_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this OpenRouter connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name used to identify this OpenRouter provider.",
                    type="string",
                    placeholder="E.g. My OpenRouter provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials",
            description="Configure the API key used to authenticate against OpenRouter.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="API key used to authenticate requests to OpenRouter.",
                    type="string",
                    placeholder="E.g. sk-or-xxxxxxxxxxxxxxxx",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Provider metadata",
            description="Optional metadata displayed in the admin UI to help contextualize this provider.",
            fields=[
                FieldSchema(
                    key="settings.eu_routing",
                    label="EU routing",
                    description="Route all OpenRouter requests through https://eu.openrouter.ai. This is only available for enterprise customers and must be manually enabled by OpenRouter.",
                    type="boolean",
                    default=False,
                ),
                FieldSchema(
                    key="settings.ranking_url",
                    label="Ranking URL",
                    description="Public application URL sent to OpenRouter as HTTP-Referer. Leave empty to use Omlorix's GitHub repository.",
                    type="string",
                    placeholder="E.g. https://example.com",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.ranking_title",
                    label="Ranking title",
                    description="Application name sent to OpenRouter as X-OpenRouter-Title. Leave empty to use Omlorix.",
                    type="string",
                    placeholder="E.g. My Omlorix",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix reacts when OpenRouter model availability changes.",
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
                    description="Automatically remove OpenRouter models that are no longer available.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when the OpenRouter model roster changes.",
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


OPENROUTER_MODEL_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Model Provider Settings",
            description="Additional controls for provider-specific configuration.",
            fields=[
                FieldSchema(
                    key="settings.allow_fallbacks",
                    label="Allow Fallbacks",
                    description="When the locked provider fails, allow OpenRouter to use a fallback provider with potentially different pricing and functionality.",
                    type="boolean",
                    required=False,
                    default=False,
                ),
            ],
        ),
    ]
)


def _coerce_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_modalities_list(modalities) -> set[str]:
    normalized: set[str] = set()
    if modalities is None:
        return normalized
    if isinstance(modalities, dict):
        iterable = modalities.values()
    elif isinstance(modalities, (list, tuple, set)):
        iterable = modalities
    else:
        iterable = [modalities]
    for item in iterable or []:
        if isinstance(item, str):
            value = item.strip().lower()
            if value:
                normalized.add(value)
    return normalized


def _filter_modalities_schema_options(
    modalities_schema: Sections, supported_modalities: set[str]
):
    if not modalities_schema or not isinstance(modalities_schema.sections, list):
        return
    field = _get_field_from_section(
        modalities_schema.sections,
        "Modalities & platform limits",
        "settings.input_formats",
    )
    if not field or not isinstance(field.options, list):
        return
    filtered_options: list[Option] = []
    for option in field.options:
        value = getattr(option, "value", None)
        normalized = value.strip().lower() if isinstance(value, str) else None
        if (
            normalized in CONDITIONAL_INPUT_MODALITIES
            and normalized not in supported_modalities
        ):
            continue
        filtered_options.append(option)
    field.options = filtered_options


def _normalize_model_identifier(model_name: str | None) -> str | None:
    if not isinstance(model_name, str):
        return None
    trimmed = model_name.strip()
    if not trimmed:
        return None
    for suffix in OPENROUTER_NAME_EXTENSIONS:
        if suffix and trimmed.endswith(suffix):
            return trimmed[: -len(suffix)]
    return trimmed


def _normalize_knowledge_cutoff_value(value) -> str | None:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(value)).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None

        if normalized.isdigit():
            return _normalize_knowledge_cutoff_value(int(normalized))

        try:
            return (
                datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
        except ValueError:
            pass

        if len(normalized) >= 10:
            try:
                return date.fromisoformat(normalized[:10]).isoformat()
            except ValueError:
                pass

        return normalized

    return None


def get_openrouter_model_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import model_schema as _implementation

    _implementation._sync_compat_dependencies("get_openrouter_model_schema", globals())
    return _implementation._impl_get_openrouter_model_schema(*args, **kwargs)


OPENROUTER_THINKING_SECTION_SCHEMA = Sections(
    sections=[
        Section(
            title="Reasoning & advanced capabilities",
            description="Configure reasoning-related behavior for supported OpenRouter models.",
            fields=[
                FieldSchema(
                    key="settings.reasoning_enabled",
                    label="Enable reasoning",
                    description="Toggle to allow this model to use reasoning features when available.",
                    type="boolean",
                    required=False,
                ),
                FieldSchema(
                    key="settings.reasoning_mode",
                    label="Reasoning mode",
                    description="Choose whether reasoning is tuned by effort presets or a token budget.",
                    type="select",
                    options=[
                        Option(
                            value="effort",
                            label="Effort",
                            i18n_label="llm.shared.settings.reasoning_mode.option.effort",
                        ),
                        Option(
                            value="budget",
                            label="Token budget",
                            i18n_label="llm.shared.settings.reasoning_mode.option.budget",
                        ),
                    ],
                    required=False,
                    dependency="settings.reasoning_enabled",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="settings.reasoning_effort",
                    label="Reasoning effort",
                    description="Select the effort level to apply when reasoning is enabled.",
                    type="select",
                    options=build_reasoning_effort_options(["low", "medium", "high"]),
                    required=False,
                    dependency="settings.reasoning_enabled",
                    dependency_value=True,
                    dependency2="settings.reasoning_mode",
                    dependency2_value="effort",
                ),
                FieldSchema(
                    key="settings.reasoning_max_tokens",
                    label="Reasoning token budget",
                    description="Maximum number of tokens allocated for reasoning traces.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.reasoning_enabled",
                    dependency_value=True,
                    dependency2="settings.reasoning_mode",
                    dependency2_value="budget",
                ),
                FieldSchema(
                    key="settings.reasoning_exclude",
                    label="Hide reasoning traces",
                    description="Exclude the reasoning trace from final responses.",
                    type="boolean",
                    required=False,
                    dependency="settings.reasoning_enabled",
                    dependency_value=True,
                ),
            ],
        ),
    ]
)


# -------------------
# Model Settings
# -------------------
class OpenrouterModelSettings(BaseModelSettings[InputFormatEnum, OutputFormatEnum]):
    reasoning_enabled: bool = False
    reasoning_mode: Literal["effort", "budget"] | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    reasoning_max_tokens: int | None = Field(None, ge=1)
    reasoning_exclude: bool = False
    pdf_processing_engine: Literal["pdf-text", "mistral-ocr", "native"] | None = None
    # Attachment limits per request (history + current message)
    max_image_count: int = -1
    max_video_count: int = -1
    max_audio_count: int = -1
    max_document_count: int = -1
    native_youtube_video: bool = False

    # Parameters (all optional)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    top_k: int | None = Field(None, ge=1)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    repetition_penalty: float | None = Field(None, gt=0, le=2)
    min_p: float | None = Field(None, gt=0, le=1)
    top_a: float | None = Field(None, gt=0, le=1)
    seed: int | None = None
    max_tokens: int | None = Field(None, ge=1)
    logit_bias: dict[str, float] | None = None
    stop: list[str] | None = None
    tool_choice: Literal["none", "auto", "required"] | dict | None = None
    response_format: dict | None = None
    structured_outputs: dict | list[dict] | None = None
    parallel_tool_calls: bool | None = None
    verbosity: Literal["low", "medium", "high"] | None = None

    # Provider settings
    provider_mode: Literal["specific", "auto", "sort"] = "specific"
    only_provider: str | None = None
    provider_sort: Literal["price", "throughput", "latency"] | None = None
    allow_fallbacks: bool = False

    @model_validator(mode="after")
    def validate_provider_configuration(self):
        if self.provider_mode == "specific":
            if not self.only_provider:
                raise ValueError(
                    "'only_provider' is required when provider_mode is 'specific'."
                )
            self.provider_sort = None
        elif self.provider_mode == "auto":
            self.only_provider = None
            self.provider_sort = None
        elif self.provider_mode == "sort":
            if not self.provider_sort:
                raise ValueError(
                    "'provider_sort' is required when provider_mode is 'sort'."
                )
            if self.provider_sort not in ["price", "throughput", "latency"]:
                raise ValueError(
                    "'provider_sort' must be one of: price, throughput, latency."
                )
            self.only_provider = None
        return self

    @model_validator(mode="after")
    def validate_reasoning_configuration(self):
        if not self.reasoning_enabled:
            self.reasoning_mode = None
            if self.reasoning_effort is not None:
                raise ValueError("Enable reasoning to configure 'reasoning_effort'.")
            if self.reasoning_max_tokens is not None:
                raise ValueError(
                    "Enable reasoning to configure 'reasoning_max_tokens'."
                )
            if self.reasoning_exclude:
                raise ValueError("Enable reasoning to configure 'reasoning_exclude'.")
            return self

        mode = self.reasoning_mode
        if mode not in (None, "effort", "budget"):
            raise ValueError(
                "Invalid reasoning_mode. Allowed values are 'effort' or 'budget'."
            )

        if mode is None:
            inferred = infer_reasoning_mode_from_settings(self.model_dump())
            if inferred:
                mode = inferred
                self.reasoning_mode = inferred

        if mode == "effort":
            if self.reasoning_effort is None:
                raise ValueError(
                    "Reasoning effort must be provided when reasoning_mode is 'effort'."
                )
            self.reasoning_max_tokens = None
        elif mode == "budget":
            if self.reasoning_max_tokens is None:
                raise ValueError(
                    "Reasoning token budget must be provided when reasoning_mode is 'budget'."
                )
            self.reasoning_effort = None

        return self

    @model_validator(mode="after")
    def validate_title_generation(self):
        if self.title_generation:
            if not self.title_generation_model:
                raise ValueError(
                    "'title_generation_model' is required when title_generation is enabled."
                )
        else:
            if not self.title_generation_model:
                self.title_generation_model = "current"
        return self


# ---------------
# Image Support
# ---------------
openrouter_image_mime_types = ["image/png", "image/jpeg", "image/webp", "image/gif"]


# ---------------
# Audio Support
# ---------------
openrouter_audio_mime_types = ["audio/wav", "audio/mp3"]


# ---------------
# Document Support
# ---------------
openrouter_document_mime_types = [
    "application/pdf",
]


# ---------------
# Video Support
# ---------------
openrouter_video_mime_types = [
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/webm",
]


# ---------------
# Model Parameters
# ---------------
openrouter_model_parameters: Set[str] = {
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "presence_penalty",
    # The model catalog uses max_tokens as its capability name. The request
    # adapter translates it to max_output_tokens for /responses.
    "max_tokens",
    "tool_choice",
    "response_format",
    "parallel_tool_calls",
    "verbosity",
}


OPENROUTER_NAME_EXTENSIONS = [
    ":nitro",
    ":floor",
    ":exacto",
    ":extended",
    ":beta",
]
