"""Backward-compatible schema API for the OpenAI integration.

Focused sibling modules own the larger schema builders. Their dependencies
remain imported here so historical patches and imports continue to work.
"""

# ruff: noqa: F401, E402

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, List, Literal
from datetime import date, datetime
from enum import Enum
import logging

from app.llm.base_settings import BaseModelSettings
from app.llm.openai.custom_headers import normalize_custom_header_entries
from app.llm.openai.catalog import get_responses_model_capabilities
from app.llm.openai.provider_types import (
    XAI_PROVIDER_TYPE,
    allows_manual_openai_model_entry,
    is_azure_openai_provider_type,
    is_openai_chat_completions_provider_type,
    is_openai_custom_base_url_provider_type,
)
from app.llm.reasoning_effort_options import (
    build_reasoning_effort_options,
)
from app.llm.model_schemas import (
    MODEL_SCHEMA_INFORMATION_SECTION,
    MODEL_SCHEMA_FILE_SECTION,
    apply_model_mcp_schema_values,
    combine_model_schema_sections,
    get_model_schema_access_section,
    get_model_schema_modalities_section,
    get_model_schema_title_section,
    get_model_schema_tools_section,
    get_model_schema_skill_section,
    get_parameter_basic_schema,
)
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


# -------------------
# OpenAI
# -------------------
class CreateProviderOpenai(BaseModel):
    name: str
    api_key: str
    settings: "OpenaiSettings"


class OpenaiSettings(BaseModel):
    organization: str | None = None
    project: str | None = None
    custom_headers: list[str] = Field(default_factory=list)

    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True

    @field_validator("custom_headers", mode="before")
    @classmethod
    def _normalize_custom_headers(cls, value: Any) -> list[str]:
        return normalize_custom_header_entries(value)


class OpenAIInputFormatEnum(str, Enum):
    text = "text"
    image = "image"
    audio = "audio"
    # video = "video"
    pdf = "pdf"
    text_document = "text_document"


class OpenAIOutputFormatEnum(str, Enum):
    text = "text"


logger = logging.getLogger(__name__)
OPENAI_TOOL_SEARCH_SETTING_KEY = "settings.tool_search"
OPENAI_REASONING_MODE_SETTING_KEY = "settings.reasoning_mode"
OPENAI_REASONING_CONTEXT_SETTING_KEY = "settings.reasoning_context"
OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY = "settings.prompt_cache_override"
OPENAI_PROMPT_CACHE_SECTION_TITLE = "Prompt caching"
OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def _apply_azure_model_name_copy(
    schema: Sections, openai_provider_type: str | None
) -> None:
    """Apply Azure model name copy to schema."""
    if not is_azure_openai_provider_type(openai_provider_type):
        return
    field = _get_field_from_section(schema.sections, "Model Information", "model_name")
    if field:
        field.label = "Deployment name"
        field.description = (
            "Azure OpenAI deployment name used when calling the Azure endpoint."
        )


def _hide_openai_model_id_field(
    provider_id: str | None, openai_provider_type: str | None
) -> bool:
    """Check if OpenAI model ID field should be hidden."""
    return bool(provider_id) and openai_provider_type in (None, "openai")


def _normalize_openai_reasoning_effort_options(thinking_caps: dict | None) -> list[str]:
    """Normalize OpenAI reasoning effort options."""
    if not isinstance(thinking_caps, dict):
        return []
    raw_options = thinking_caps.get("thinking_effort") or []
    normalized: list[str] = []
    seen: set[str] = set()
    for option in raw_options:
        if option is None:
            continue
        value = str(option).strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value)
    return normalized


def _build_openai_reasoning_effort_options(thinking_caps: dict | None) -> list[Option]:
    """Build OpenAI reasoning effort options."""
    return build_reasoning_effort_options(
        _normalize_openai_reasoning_effort_options(thinking_caps)
    )


def _openai_reasoning_summary_dependency_values(
    effort_options: list[Option] | None,
) -> list[str]:
    """Get OpenAI reasoning summary dependency values."""
    values: list[str] = []
    seen: set[str] = set()
    for option in effort_options or []:
        raw_value = getattr(option, "value", None)
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered == "none" or lowered in seen:
            continue
        seen.add(lowered)
        values.append(value)
    return values


def _sync_openai_reasoning_summary_dependency(
    schema: Sections, section_title: str
) -> None:
    """Sync OpenAI reasoning summary dependency."""
    summary_field = _get_field_from_section(
        schema.sections,
        section_title,
        "settings.reasoning_summary",
    )
    if not summary_field:
        return

    effort_field = _get_field_from_section(
        schema.sections,
        section_title,
        "settings.reasoning_effort",
    )
    summary_field.dependency = "settings.reasoning_effort"
    summary_field.dependency_value = _openai_reasoning_summary_dependency_values(
        effort_field.options if effort_field else None
    )


def _openai_reasoning_toggle_supported(
    caps: dict | None,
    *,
    openai_provider_type: str | None,
) -> bool:
    """Check if OpenAI reasoning toggle is supported."""
    if is_openai_chat_completions_provider_type(openai_provider_type):
        return True
    thinking_caps = caps.get("thinking") if isinstance(caps, dict) else {}
    if not isinstance(thinking_caps, dict) or not thinking_caps.get("thinking"):
        return False
    explicit_support = thinking_caps.get("reasoning_toggle_supported")
    if explicit_support is not None:
        return bool(explicit_support)
    return not _normalize_openai_reasoning_effort_options(thinking_caps)


def _openai_tool_search_supported(
    caps: dict | None, *, openai_provider_type: str | None
) -> bool:
    """Check if OpenAI tool search is supported."""
    if is_openai_chat_completions_provider_type(openai_provider_type):
        return False
    return bool(isinstance(caps, dict) and caps.get("supports_tool_search"))


def _schema_option_values(
    schema: Sections, section_title: str, field_key: str
) -> list[str]:
    """Get schema option values."""
    field = _get_field_from_section(schema.sections, section_title, field_key)
    if not field or not field.options:
        return []
    values: list[str] = []
    for option in field.options:
        value = getattr(option, "value", None)
        if isinstance(value, str) and value.strip():
            values.append(value)
    return values


def _upsert_openai_tool_search_field(
    schema: Sections,
    *,
    section_title: str,
    dependency_key: str,
    dependency_values: list[str],
) -> None:
    """Upsert OpenAI tool search field."""
    section = next(
        (item for item in schema.sections or [] if item.title == section_title), None
    )
    if not section:
        return

    section.fields = [
        field for field in section.fields if field.key != OPENAI_TOOL_SEARCH_SETTING_KEY
    ]
    if not dependency_values:
        return

    insert_at = len(section.fields)
    for index, field in enumerate(section.fields):
        if field.key in {
            "settings.native_websearch",
            "settings.enabled_tools",
            "tools",
        }:
            insert_at = index + 1

    section.fields.insert(
        insert_at,
        FieldSchema(
            key=OPENAI_TOOL_SEARCH_SETTING_KEY,
            label="Hosted tool search",
            description="Enable OpenAI's hosted tool_search so the model can discover deferred tools during a response.",
            type="boolean",
            required=False,
            dependency=dependency_key,
            dependency_value=dependency_values,
            default=False,
        ),
    )


OPENAI_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this OpenAI connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this OpenAI provider configuration.",
                    type="string",
                    placeholder="E.g. My OpenAI provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials & endpoints",
            description="Configure the credentials and optional routing used for OpenAI requests.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="OpenAI API key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. sk-openai-xxxxxxxxxxxxxxxx",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.organization",
                    label="Organization ID",
                    description="Optional OpenAI organization identifier to scope requests.",
                    type="string",
                    placeholder="E.g. org-abc123",
                ),
                FieldSchema(
                    key="settings.project",
                    label="Project ID",
                    description="Optional OpenAI project identifier associated with this provider.",
                    type="string",
                    placeholder="E.g. proj-xyz789",
                ),
                FieldSchema(
                    key="settings.custom_headers",
                    label="Custom HTTP headers",
                    description="Optional headers sent with every OpenAI request. Add one entry per header in the format Header-Name: value.",
                    type="string_list",
                    placeholder="Add Header-Name: value and press Enter",
                    default=[],
                ),
            ],
        ),
        Section(
            title="Request handling",
            description="Set operational controls applied to every OpenAI API call.",
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
                    description="Automatically remove provider models that no longer exist on OpenAI.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when OpenAI model availability changes.",
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
OPENAI_THINKING_MODEL_SCHEMA = Sections(
    sections=[
        Section(
            title="Reasoning & advanced capabilities",
            description="Tune reasoning, summarization, and service-tier behaviors for this model.",
            fields=[
                FieldSchema(
                    key="settings.reasoning",
                    label="Enable reasoning",
                    description="Toggle reasoning features for models that support them.",
                    type="boolean",
                    required=False,
                ),
                FieldSchema(
                    key="settings.reasoning_effort",
                    label="Reasoning effort",
                    description="Select the reasoning effort level for reasoning-capable models.",
                    type="select",
                    # Unknown models on custom Responses and Chat Completions
                    # endpoints do not have catalog metadata. Offer the full
                    # OpenAI-compatible set; known models still replace this
                    # list with their model-specific capabilities below.
                    options=build_reasoning_effort_options(
                        OPENAI_COMPATIBLE_REASONING_EFFORT_LEVELS
                    ),
                    dependency="settings.reasoning",
                    dependency_value=True,
                    required=False,
                ),
                FieldSchema(
                    key="settings.reasoning_summary",
                    label="Reasoning summary",
                    description="Controls reasoning summary verbosity.",
                    type="select",
                    options=[
                        Option(value=option, label=option or "none")
                        for option in ["", "concise", "detailed", "auto"]
                    ],
                    default="auto",
                    dependency="settings.reasoning_effort",
                    dependency_value=["low", "medium", "high"],
                    required=False,
                ),
                FieldSchema(
                    key=OPENAI_REASONING_MODE_SETTING_KEY,
                    label="Reasoning mode",
                    description="Choose standard execution or pro mode for more difficult tasks.",
                    i18n_label="llm.openai.reasoning_mode.label",
                    i18n_description="llm.openai.reasoning_mode.description",
                    type="select",
                    options=[
                        Option(
                            value="standard",
                            label="Standard",
                            i18n_label="llm.openai.reasoning_mode.standard",
                        ),
                        Option(
                            value="pro",
                            label="Pro",
                            i18n_label="llm.openai.reasoning_mode.pro",
                        ),
                    ],
                    default="standard",
                    required=False,
                ),
                FieldSchema(
                    key=OPENAI_REASONING_CONTEXT_SETTING_KEY,
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
                ),
            ],
        ),
        Section(
            title=OPENAI_PROMPT_CACHE_SECTION_TITLE,
            description="Configure GPT-5.6 prompt-cache routing and minimum lifetime.",
            i18n_title="llm.openai.prompt_cache.section_title",
            i18n_description="llm.openai.prompt_cache.section_description",
            fields=[
                FieldSchema(
                    key=OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
                    label="Override prompt caching defaults",
                    description="Send explicit prompt caching settings with OpenAI requests. Disable this to use the provider defaults.",
                    i18n_label="llm.openai.prompt_cache.override_label",
                    i18n_description="llm.openai.prompt_cache.override_description",
                    type="boolean",
                    default=True,
                    required=False,
                ),
                FieldSchema(
                    key="settings.prompt_cache_ttl",
                    label="Prompt cache minimum lifetime",
                    description="Minimum lifetime requested for GPT-5.6 cache entries.",
                    i18n_label="llm.openai.prompt_cache.ttl_label",
                    i18n_description="llm.openai.prompt_cache.ttl_description",
                    type="select",
                    options=[
                        Option(
                            value="30m",
                            label="30 minutes",
                            i18n_label="llm.openai.prompt_cache.30m",
                        )
                    ],
                    default="30m",
                    dependency=OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
                    dependency_value=True,
                    required=False,
                ),
                FieldSchema(
                    key="settings.prompt_cache_key",
                    label="Prompt cache key",
                    description="Optional stable routing key. Leave empty to let Omlorix generate a privacy-preserving per-user key.",
                    i18n_label="llm.openai.prompt_cache.key_label",
                    i18n_description="llm.openai.prompt_cache.key_description",
                    type="string",
                    placeholder="Enter a prompt cache key",
                    i18n_placeholder="llm.openai.prompt_cache.key_placeholder",
                    dependency=OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
                    dependency_value=True,
                    required=False,
                ),
            ],
        ),
    ]
)


def _object_to_dict(obj: Any) -> dict:
    """Convert object to dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            return obj.model_dump()
        except Exception:
            return {}
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        try:
            return obj.dict()
        except Exception:
            return {}
    if hasattr(obj, "__dict__"):
        return {
            key: value for key, value in obj.__dict__.items() if not key.startswith("_")
        }
    return {}


def get_parameters_schema_filled(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation_schema as _implementation

    _implementation._sync_compat_dependencies("get_parameters_schema_filled", globals())
    return _implementation._impl_get_parameters_schema_filled(*args, **kwargs)


def _build_settings_payload(model_settings: dict | None) -> dict:
    """Construct a payload compatible with schema population helpers."""
    normalized = model_settings if isinstance(model_settings, dict) else {}
    payload = dict(normalized or {})
    payload["settings"] = normalized or {}
    return payload


def get_openai_model_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import model_schema as _implementation

    _implementation._sync_compat_dependencies("get_openai_model_schema", globals())
    return _implementation._impl_get_openai_model_schema(*args, **kwargs)


def _get_openai_model_caps(
    model_name: str | None,
    *,
    openai_provider_type: str | None = None,
) -> dict | None:
    """Get model capabilities from the effective provider's catalog."""

    return get_responses_model_capabilities(model_name, openai_provider_type)


def _apply_openai_model_caps_to_schema(
    schema: Sections,
    caps: dict | None,
    *,
    openai_provider_type: str = "openai",
):
    """Apply OpenAI model capabilities to schema."""
    if not schema:
        return

    if not caps:
        # Unknown OpenAI-compatible models may support generic reasoning, but
        # GPT-5.6-only controls must not be sent optimistically to arbitrary
        # endpoints. A recognized catalog entry enables these fields below.
        _remove_field_from_section(
            schema.sections,
            "Reasoning & advanced capabilities",
            OPENAI_REASONING_MODE_SETTING_KEY,
        )
        _remove_field_from_section(
            schema.sections,
            "Reasoning & advanced capabilities",
            OPENAI_REASONING_CONTEXT_SETTING_KEY,
        )
        schema.sections = [
            section
            for section in schema.sections
            if section.title != OPENAI_PROMPT_CACHE_SECTION_TITLE
        ]
        return

    reasoning_section = "Reasoning & advanced capabilities"
    thinking_caps = caps.get("thinking") or {}
    reasoning_supported = thinking_caps.get("thinking")
    if not reasoning_supported:
        schema.sections = [
            section for section in schema.sections if section.title != reasoning_section
        ]
    else:
        effort_options = _build_openai_reasoning_effort_options(thinking_caps)
        effort_field = _get_field_from_section(
            schema.sections, reasoning_section, "settings.reasoning_effort"
        )
        if effort_field:
            if effort_options:
                effort_field.options = effort_options
                default_effort = str(
                    thinking_caps.get("default_thinking_effort") or ""
                ).strip()
                if default_effort and default_effort in {
                    str(option.value) for option in effort_options
                }:
                    effort_field.default = default_effort
            else:
                _remove_field_from_section(
                    schema.sections, reasoning_section, "settings.reasoning_effort"
                )
        _sync_openai_reasoning_summary_dependency(schema, reasoning_section)

        # Reasoning mode and persisted reasoning are GPT-5.6 Responses API
        # features. Keep them out of Chat Completions and older model forms so
        # administrators cannot save a setting the selected endpoint rejects.
        if not caps.get(
            "supports_reasoning_mode"
        ) or is_openai_chat_completions_provider_type(openai_provider_type):
            _remove_field_from_section(
                schema.sections,
                reasoning_section,
                OPENAI_REASONING_MODE_SETTING_KEY,
            )
        if not caps.get(
            "reasoning_context"
        ) or is_openai_chat_completions_provider_type(openai_provider_type):
            _remove_field_from_section(
                schema.sections,
                reasoning_section,
                OPENAI_REASONING_CONTEXT_SETTING_KEY,
            )

    prompt_cache_caps = caps.get("prompt_caching")
    if not isinstance(prompt_cache_caps, dict):
        schema.sections = [
            section
            for section in schema.sections
            if section.title != OPENAI_PROMPT_CACHE_SECTION_TITLE
        ]
    else:
        override_field = _get_field_from_section(
            schema.sections,
            OPENAI_PROMPT_CACHE_SECTION_TITLE,
            OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
        )
        if override_field:
            override_field.default = not is_openai_custom_base_url_provider_type(
                openai_provider_type
            )
        ttl_field = _get_field_from_section(
            schema.sections,
            OPENAI_PROMPT_CACHE_SECTION_TITLE,
            "settings.prompt_cache_ttl",
        )
        supported_ttls = prompt_cache_caps.get("ttl") or []
        if ttl_field and supported_ttls:
            ttl_field.options = [
                Option(
                    value=value,
                    label="30 minutes" if value == "30m" else value,
                    i18n_label="llm.openai.prompt_cache.30m"
                    if value == "30m"
                    else None,
                )
                for value in supported_ttls
            ]
        elif ttl_field:
            # xAI exposes cache routing through ``prompt_cache_key`` but does
            # not support OpenAI's explicit cache-retention TTL control.
            _remove_field_from_section(
                schema.sections,
                OPENAI_PROMPT_CACHE_SECTION_TITLE,
                "settings.prompt_cache_ttl",
            )

    modalities_section = "Modalities & platform limits"
    input_formats = caps.get("input_formats")
    if input_formats:
        input_field = _get_field_from_section(
            schema.sections, modalities_section, "settings.input_formats"
        )
        if input_field:
            input_field.options = [
                Option(value=item, label=item) for item in input_formats
            ]
        if "image" not in input_formats:
            schema.sections = [
                section
                for section in schema.sections
                if section.title != "Image inputs"
            ]
    output_formats = caps.get("output_formats")
    if output_formats:
        output_field = _get_field_from_section(
            schema.sections, modalities_section, "settings.output_formats"
        )
        if output_field:
            output_field.options = [
                Option(value=item, label=item) for item in output_formats
            ]

    generation_section = "Generation parameters"
    temperature_caps = caps.get("temperature")
    if temperature_caps is not None and not temperature_caps.get("temperature"):
        _remove_field_from_section(
            schema.sections, generation_section, "settings.temperature"
        )
    top_p_caps = caps.get("top_p")
    if top_p_caps is not None and not top_p_caps.get("top_p"):
        _remove_field_from_section(
            schema.sections, generation_section, "settings.top_p"
        )
    frequency_penalty_caps = caps.get("frequency_penalty")
    if frequency_penalty_caps is not None and not frequency_penalty_caps.get(
        "frequency_penalty"
    ):
        _remove_field_from_section(
            schema.sections,
            generation_section,
            "settings.frequency_penalty",
        )
    presence_penalty_caps = caps.get("presence_penalty")
    if presence_penalty_caps is not None and not presence_penalty_caps.get(
        "presence_penalty"
    ):
        _remove_field_from_section(
            schema.sections,
            generation_section,
            "settings.presence_penalty",
        )

    verbosity_caps = caps.get("verbosity")
    settings_section = "Generation parameters"
    if verbosity_caps is not None:
        if not verbosity_caps.get("verbosity"):
            _remove_field_from_section(
                schema.sections, settings_section, "settings.verbosity"
            )
        else:
            supported_verbosity_levels = verbosity_caps.get("verbosity_level") or []
            verbosity_field = _get_field_from_section(
                schema.sections, settings_section, "settings.verbosity"
            )
            if verbosity_field:
                verbosity_field.options = [
                    Option(value=item, label=item)
                    for item in supported_verbosity_levels
                ]

                # Keep the default valid relative to the dynamically populated options.
                if supported_verbosity_levels:
                    preferred_default = (
                        supported_verbosity_levels[1]
                        if len(supported_verbosity_levels) > 1
                        else supported_verbosity_levels[0]
                    )
                    if verbosity_field.default not in supported_verbosity_levels:
                        verbosity_field.default = preferred_default
    else:
        _remove_field_from_section(
            schema.sections, settings_section, "settings.verbosity"
        )

    # Filter priority processing options based on supported service tiers
    supported_tiers = caps.get("supported_service_tier")
    if supported_tiers is not None:
        priority_field = _get_field_from_section(
            schema.sections, settings_section, "settings.priority_processing"
        )
        if priority_field:
            priority_field.options = [
                Option(value=tier, label=tier)
                for tier in ["flex", "standard", "priority"]
                if tier in supported_tiers
            ]
            # Update default if current default is not supported
            if priority_field.default and priority_field.default not in supported_tiers:
                priority_field.default = (
                    supported_tiers[0] if supported_tiers else "standard"
                )


def get_openai_model_schema_parameter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import parameter_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_openai_model_schema_parameter", globals()
    )
    return _implementation._impl_get_openai_model_schema_parameter(*args, **kwargs)


class OpenAIListModelsByok(BaseModel):
    api_key: str
    organization: str | None = None
    project: str | None = None


class OpenAIModelSettings(
    BaseModelSettings[OpenAIInputFormatEnum, OpenAIOutputFormatEnum]
):
    input_formats: List[OpenAIInputFormatEnum] = Field(default_factory=list)
    output_formats: List[OpenAIOutputFormatEnum] = Field(default_factory=list)
    reasoning: bool = False
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = None
    reasoning_summary: Literal["", "concise", "detailed", "auto"] | None = "auto"
    reasoning_mode: Literal["standard", "pro"] = "standard"
    reasoning_context: Literal["auto", "current_turn", "all_turns"] = "auto"
    # Keep explicit cache routing enabled for existing models that predate the
    # toggle. Turning it off leaves prompt caching entirely to the provider.
    prompt_cache_override: bool = True
    prompt_cache_ttl: Literal["30m"] = "30m"
    prompt_cache_key: str | None = None
    training_data: Literal["true", "false", "unknown"] = "false"

    # Attachment limits per request (history + current message)
    max_image_count: int = -1  # -1 = unlimited
    max_video_count: int = -1  # -1 = unlimited
    max_audio_count: int = -1  # -1 = unlimited
    max_document_count: int = -1  # -1 = unlimited

    # WebSearch
    websearch_scrape_provider: str | None = None  # provider id
    websearch_search_provider: str | None = None  # provider id
    native_websearch: bool = False
    tool_search: bool = False

    # Response controls
    priority_processing: Literal["flex", "standard", "priority"] = "standard"
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    max_output_tokens: int | None = None
    # Compatibility fallback for model records created before Omlorix adopted
    # the provider-neutral max_output_tokens setting key.
    max_completion_tokens: int | None = None
    logit_bias: dict[str, float] | None = None
    # ``None`` is an internal "use the endpoint default" sentinel. Request
    # builders must omit it because the provider API accepts only booleans.
    store: bool | None = None
    send_user_identifier: bool = False
    verbosity: Literal["low", "medium", "high"] | None = None
    image_detail: Literal["auto", "low", "high", "original"] | None = None


class OpenAICustomBaseURLModelSettings(OpenAIModelSettings):
    """Model settings for generic OpenAI-compatible endpoints."""

    prompt_cache_override: bool = False


# ---------------
# Image Support
# ---------------
openai_image_mime_types = ["image/png", "image/jpeg", "image/webp", "image/gif"]


# ---------------
# Video Support
# ---------------
# openai_video_mime_types = [
#    "video/mp4",
#    "video/mpeg",
# ]


# ---------------
# Audio Support
# ---------------
openai_audio_mime_types = ["audio/wav", "audio/mp3"]


# ---------------
# Document Support
# ---------------
openai_document_mime_types = [
    "application/pdf",
]
