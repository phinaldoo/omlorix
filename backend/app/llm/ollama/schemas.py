"""Backward-compatible schema API for the Ollama integration.

Large schema builders live in focused sibling modules while this module keeps
the established public imports and dependency-patching surface stable.
"""

# ruff: noqa: F401, F811, F841, E402

from datetime import date, datetime
from enum import Enum
import logging
from typing import Any, List, Literal

from pydantic import BaseModel, Field, model_validator

from app.llm.base_settings import BaseModelSettings
from app.utils.schemas import FieldSchema, Option, Section, Sections
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
    get_parameter_basic_schema,
)
from app.utils.schemas import (
    FieldSchema,
    FieldAttributes,
    Option,
    Section,
    Sections,
    _get_field_from_section,
    _remove_field_from_section,
    _remove_section_from_sections,
    _set_schema_field_value,
    populate_sections_with_values,
)
from app.llm.ollama.model_list import (
    OLLAMA_REASONING_EFFORT_VALUES,
    ollama_model_supports_reasoning_effort,
)
from app.llm.reasoning_effort_options import build_reasoning_effort_options


# -------------------
# Ollama
# -------------------
class CreateProviderOllama(BaseModel):
    name: str
    api_key: str
    settings: "OllamaSettings"
    is_active: bool = True


class OllamaSettings(BaseModel):
    base_url: str

    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True


class InputFormatEnum(str, Enum):
    text = "text"
    image = "image"
    pdf = "pdf"
    text_document = "text_document"


class OutputFormatEnum(str, Enum):
    text = "text"


OLLAMA_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Ollama connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="The display name used to identify this Ollama provider.",
                    type="string",
                    placeholder="E.g. My Ollama provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials & endpoint",
            description="Configure the credentials and network path used to reach your Ollama server.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="API token used to authenticate against the Ollama server.",
                    type="string",
                    placeholder="E.g. ollama",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.base_url",
                    label="Base URL",
                    description="Root URL of your Ollama instance.",
                    type="string",
                    placeholder="E.g. https://ollama.example.com",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix reacts when Ollama model availability changes.",
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
                    description="Automatically remove models that are no longer available on the Ollama host.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when Ollama models are added or removed.",
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


class OllamaListModelByok(BaseModel):
    base_url: str
    api_key: str


class OllamaModelActionRequest(BaseModel):
    ollama_provider_id: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


class OllamaModelSettings(BaseModelSettings[InputFormatEnum, OutputFormatEnum]):
    reasoning: bool | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    thinking_enabled: bool | None = (
        None  # Only relevant if model supports thinking, otherwise it will be ignored
    )
    is_ollama_cloud: bool = False

    max_image_count: int = (
        -1
    )  # Per chat (chat history + current message), if none -> unlimited
    max_document_count: int = (
        -1
    )  # Per chat (chat history + current message), if none -> unlimited

    # Generation params (optional)
    num_keep: int | None = None
    seed: int | None = None
    num_predict: int | None = None
    top_k: int | None = None
    top_p: float | None = None
    min_p: float | None = None
    typical_p: float | None = None
    repeat_last_n: int | None = None
    temperature: float | None = None
    repeat_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    penalize_newline: bool | None = None
    stop: List[str] | None = None
    numa: bool | None = None
    num_ctx: int | None = None
    num_batch: int | None = None
    num_gpu: int | None = None
    main_gpu: int | None = None
    use_mmap: bool | None = None
    num_thread: int | None = None
    keep_alive: int | None = None

    @model_validator(mode="after")
    def sync_reasoning_fields(self):
        if self.reasoning is None and self.thinking_enabled is not None:
            self.reasoning = self.thinking_enabled
        if self.thinking_enabled is None and self.reasoning is not None:
            self.thinking_enabled = self.reasoning
        return self


OLLAMA_MODEL_SCHEMA_THINKING_SECTION = Sections(
    sections=[
        Section(
            title="Reasoning",
            description="Configure Ollama reasoning support for models that expose a thinking trace.",
            fields=[
                FieldSchema(
                    key="settings.reasoning",
                    label="Enable reasoning",
                    description="Enable reasoning for models that support it.",
                    type="boolean",
                    required=False,
                ),
                FieldSchema(
                    key="settings.reasoning_effort",
                    label="Reasoning effort",
                    description="Select the reasoning effort level used for GPT-OSS models.",
                    type="select",
                    options=build_reasoning_effort_options(
                        OLLAMA_REASONING_EFFORT_VALUES
                    ),
                    required=False,
                    dependency="settings.reasoning",
                    dependency_value=True,
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
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {}


logger = logging.getLogger(__name__)


def get_ollama_model_info(db, provider_id: str | None, model_name: str | None):
    """Get Ollama model info."""
    from app.llm.ollama.utils import get_model_capabilities, get_model_info

    model_info_raw = None
    try:
        model_info_raw = get_model_info(db, model_name, provider_id)
    except Exception as exc:  # noqa: BLE001 - surface fallback path
        logger.warning(
            "Failed to retrieve Ollama model info for provider=%s model=%s: %s",
            provider_id,
            model_name,
            exc,
        )

    model_info_dict = _object_to_dict(model_info_raw)
    details_obj = model_info_dict.get("details") or getattr(
        model_info_raw, "details", None
    )
    details = _object_to_dict(details_obj)

    capabilities = model_info_dict.get("capabilities")
    if not capabilities:
        try:
            capabilities = get_model_capabilities(db, model_name, provider_id)
        except Exception as exc:  # noqa: BLE001 - we just default to []
            logger.warning(
                "Failed to retrieve capabilities for provider=%s model=%s: %s",
                provider_id,
                model_name,
                exc,
            )
            capabilities = []

    return {
        "capabilities": capabilities or [],
    }


def get_parameters_schema_filled(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import generation_schema as _implementation

    _implementation._sync_compat_dependencies("get_parameters_schema_filled", globals())
    return _implementation._impl_get_parameters_schema_filled(*args, **kwargs)


def get_ollama_model_schema(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import model_schema as _implementation

    _implementation._sync_compat_dependencies("get_ollama_model_schema", globals())
    return _implementation._impl_get_ollama_model_schema(*args, **kwargs)


def get_ollama_model_schema_parameter(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import parameter_schema as _implementation

    _implementation._sync_compat_dependencies(
        "get_ollama_model_schema_parameter", globals()
    )
    return _implementation._impl_get_ollama_model_schema_parameter(*args, **kwargs)
