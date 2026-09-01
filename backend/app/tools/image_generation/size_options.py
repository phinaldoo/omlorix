import json
from typing import Any

from app.utils.schemas import FieldAttributes, FieldSchema, Option


OLLAMA_IMAGE_DIMENSION_MIN = 64
OLLAMA_IMAGE_DIMENSION_MAX = 4096
ASSISTANT_SIZE_SELECTION_KEY = "allow_assistant_size_selection"

# OpenRouter normalizes these common ratios across image providers. Individual
# providers may clamp the request to their supported subset.
OPENROUTER_IMAGE_ASPECT_RATIOS = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "4:5",
    "5:4",
    "2:1",
    "1:2",
    "21:9",
    "9:21",
]


def assistant_size_selection_enabled(settings: dict[str, Any] | None) -> bool:
    """Return whether the assistant may choose per-request image dimensions."""
    if not isinstance(settings, dict) or ASSISTANT_SIZE_SELECTION_KEY not in settings:
        return True

    raw_value = settings.get(ASSISTANT_SIZE_SELECTION_KEY)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


def _coerce_ollama_tool_dimension(raw: Any, field_name: str) -> int | None:
    """Coerce an optional Ollama tool dimension into a bounded integer."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"Ollama image {field_name} must be an integer.")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"Ollama image {field_name} must be an integer.")
        value = int(raw)
    elif isinstance(raw, str):
        normalized = raw.strip()
        if not normalized:
            return None
        try:
            value = int(normalized, 10)
        except ValueError as exc:
            raise ValueError(f"Ollama image {field_name} must be an integer.") from exc
    else:
        raise ValueError(f"Ollama image {field_name} must be an integer.")

    if value < OLLAMA_IMAGE_DIMENSION_MIN or value > OLLAMA_IMAGE_DIMENSION_MAX:
        raise ValueError(
            f"Ollama image {field_name} must be between "
            f"{OLLAMA_IMAGE_DIMENSION_MIN} and {OLLAMA_IMAGE_DIMENSION_MAX} pixels."
        )
    return value


def validate_requested_ollama_dimensions(
    width: Any,
    height: Any,
) -> tuple[int | None, int | None]:
    """Validate LLM-requested Ollama dimensions before forwarding them to Ollama."""
    return (
        _coerce_ollama_tool_dimension(width, "width"),
        _coerce_ollama_tool_dimension(height, "height"),
    )


def normalize_image_size_selection(raw: Any) -> list[str]:
    """Normalize stored or submitted image size selections."""
    if raw is None:
        return []
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = [part.strip() for part in stripped.split(",")]
        return normalize_image_size_selection(parsed)
    if isinstance(raw, (list, tuple, set)):
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    value = str(raw or "").strip()
    return [value] if value else []


def get_supported_tool_size_values(provider_type: str, model_name: str) -> list[str]:
    """Return the discrete tool-exposed size choices for a provider/model."""
    normalized_provider = str(provider_type or "").strip().lower()
    normalized_model = str(model_name or "").strip()
    if not normalized_provider or not normalized_model:
        return []

    if normalized_provider in {
        "openai",
        "openai_responses",
        "openai_chat_completions",
    }:
        from app.llm.openai.image_generation import IMAGE_GEN_MODELS

        lowered = normalized_model.lower()
        for model in IMAGE_GEN_MODELS:
            model_name_match = lowered == str(model.get("name") or "").strip().lower()
            model_id_match = any(
                lowered == str(model_id or "").strip().lower()
                for model_id in model.get("ids", [])
            )
            if model_name_match or model_id_match:
                return [
                    value
                    for value in model.get("size", [])
                    if str(value or "").strip()
                ]
        return []

    if normalized_provider == "google_aistudio":
        from app.llm.google_aistudio.model_list import IMAGE_GEN_MODELS

        normalized_google_model = normalized_model.replace("models/", "", 1)
        for model in IMAGE_GEN_MODELS:
            ids = {
                str(model_id or "").strip()
                for model_id in (model.get("ids") or [])
                if str(model_id or "").strip()
            }
            if normalized_google_model not in ids:
                continue
            category = str(model.get("category", "model")).strip().lower()
            raw_values = (
                model.get("aspect_ratio", [])
                if category == "model"
                else model.get("aspectRatio", [])
            )
            return [value for value in raw_values if str(value or "").strip()]
        return []

    if normalized_provider == "xai":
        from app.llm.xai.image_generation import XAI_IMAGE_ASPECT_RATIOS

        return list(XAI_IMAGE_ASPECT_RATIOS)

    if normalized_provider == "openrouter":
        return list(OPENROUTER_IMAGE_ASPECT_RATIOS)

    return []


def get_assistant_size_selection_kind(
    provider_type: str,
    model_name: str,
) -> str | None:
    """Return the provider-specific dimension control exposed to the tool."""
    normalized_provider = str(provider_type or "").strip().lower()
    normalized_model = str(model_name or "").strip()
    if not normalized_provider or not normalized_model:
        return None
    if normalized_provider == "ollama":
        return "dimensions"
    if not get_supported_tool_size_values(normalized_provider, normalized_model):
        return None
    if normalized_provider in {"google_aistudio", "xai", "openrouter"}:
        return "aspect_ratio"
    return "size"


def filter_supported_tool_sizes(
    provider_type: str,
    model_name: str,
    raw_selection: Any,
) -> list[str]:
    """Return the selected sizes filtered to valid choices for the model."""
    supported_values = get_supported_tool_size_values(provider_type, model_name)
    if not supported_values:
        return []
    requested_values = set(normalize_image_size_selection(raw_selection))
    return [value for value in supported_values if value in requested_values]


def get_effective_tool_size_values(
    provider_type: str,
    model_name: str,
    settings: dict[str, Any] | None,
) -> list[str]:
    """Return the size choices the tool is allowed to expose."""
    if not assistant_size_selection_enabled(settings):
        return []
    supported_values = get_supported_tool_size_values(provider_type, model_name)
    if not supported_values:
        return []
    if not isinstance(settings, dict) or "allowed_sizes" not in settings:
        return supported_values
    return filter_supported_tool_sizes(
        provider_type,
        model_name,
        settings.get("allowed_sizes"),
    )


def validate_requested_tool_size(
    provider_type: str,
    model_name: str,
    settings: dict[str, Any] | None,
    requested_size: str | None,
) -> str | None:
    """Validate a requested tool size against provider support and admin restrictions."""
    normalized_size = str(requested_size or "").strip()
    if not normalized_size:
        return None

    if not assistant_size_selection_enabled(settings):
        raise ValueError(
            "Assistant image-size selection is disabled in admin settings."
        )

    supported_values = get_supported_tool_size_values(provider_type, model_name)
    if not supported_values:
        return normalized_size

    if normalized_size not in supported_values:
        raise ValueError(
            f"Unsupported image size '{normalized_size}' for the configured image model."
        )

    effective_values = get_effective_tool_size_values(provider_type, model_name, settings)
    if normalized_size not in effective_values:
        raise ValueError(
            f"Image size '{normalized_size}' is disabled in admin settings for the configured image model."
        )

    return normalized_size


def validate_image_generation_settings_size(
    provider_type: str,
    model_name: str,
    settings: dict[str, Any] | None,
) -> None:
    """Validate configured provider defaults against provider capabilities."""
    if not isinstance(settings, dict):
        return

    normalized_provider = str(provider_type or "").strip().lower()
    size_keys = (
        ("aspect_ratio", "aspectRatio")
        if normalized_provider == "google_aistudio"
        else ("aspect_ratio",)
        if normalized_provider in {"xai", "openrouter"}
        else ("size",)
    )
    supported_values = get_supported_tool_size_values(provider_type, model_name)
    for key in size_keys:
        if key not in settings:
            continue
        configured_value = str(settings.get(key) or "").strip()
        if configured_value and supported_values and configured_value not in supported_values:
            raise ValueError(
                f"Unsupported image size '{configured_value}' for the configured image model."
            )


def build_allowed_tool_sizes_field(
    provider_type: str,
    model_name: str,
) -> FieldSchema | None:
    """Build the translated admin field that restricts tool size choices."""
    supported_values = get_supported_tool_size_values(provider_type, model_name)
    if not supported_values:
        return None

    normalized_provider = str(provider_type or "").strip().lower()
    label = "Sizes available to the assistant"
    i18n_label = "image_generation_allowed_tool_sizes_label"
    description = (
        "Choose which output sizes the assistant may request when assistant size selection is enabled. "
        "This does not change the configured default. If none are selected, Omlorix omits the size "
        "parameter from the image-generation tool."
    )
    i18n_description = "image_generation_allowed_tool_sizes_description"
    if normalized_provider in {"google_aistudio", "xai", "openrouter"}:
        label = "Aspect ratios available to the assistant"
        i18n_label = "image_generation_allowed_tool_aspect_ratios_label"
        description = (
            "Choose which aspect ratios the assistant may request when assistant selection is enabled. "
            "This does not change the configured default. If none are selected, Omlorix omits the "
            "aspect-ratio parameter from the image-generation tool."
        )
        i18n_description = "image_generation_allowed_tool_aspect_ratios_description"

    return FieldSchema(
        key="settings.allowed_sizes",
        label=label,
        description=description,
        type="select",
        options=[Option(value=value, label=value) for value in supported_values],
        multiple=True,
        default=supported_values,
        i18n_label=i18n_label,
        i18n_description=i18n_description,
        dependency=f"settings.{ASSISTANT_SIZE_SELECTION_KEY}",
        dependency_value=True,
    )


def build_fixed_image_size_fields(
    provider_type: str,
    model_name: str,
) -> list[FieldSchema]:
    """Build the fixed size controls shown while assistant selection is disabled."""
    selection_kind = get_assistant_size_selection_kind(provider_type, model_name)
    dependency_key = f"settings.{ASSISTANT_SIZE_SELECTION_KEY}"

    # Ollama accepts independent numeric dimensions instead of a finite size
    # enum. Keep those inputs bounded to the same range enforced for tool calls.
    if selection_kind == "dimensions":
        common_attributes = FieldAttributes(
            min=OLLAMA_IMAGE_DIMENSION_MIN,
            max=OLLAMA_IMAGE_DIMENSION_MAX,
            step=1,
        )
        return [
            FieldSchema(
                key="settings.width",
                label="Fixed width",
                description=(
                    "Width used when assistant dimension selection is disabled, and as the "
                    "fallback when the assistant does not request a width."
                ),
                type="number",
                attributes=common_attributes,
                default=1024,
                i18n_label="image_generation_fixed_width_label",
                i18n_description="image_generation_fixed_width_description",
                dependency=dependency_key,
                dependency_value=False,
            ),
            FieldSchema(
                key="settings.height",
                label="Fixed height",
                description=(
                    "Height used when assistant dimension selection is disabled, and as the "
                    "fallback when the assistant does not request a height."
                ),
                type="number",
                attributes=common_attributes,
                default=1024,
                i18n_label="image_generation_fixed_height_label",
                i18n_description="image_generation_fixed_height_description",
                dependency=dependency_key,
                dependency_value=False,
            ),
        ]

    supported_values = get_supported_tool_size_values(provider_type, model_name)
    if selection_kind not in {"size", "aspect_ratio"} or not supported_values:
        return []

    # Prefer a provider-supported automatic mode as the non-surprising
    # fallback. Providers without one use the conventional square output when
    # available, then their first declared option.
    default_value = (
        "auto"
        if "auto" in supported_values
        else "1:1"
        if "1:1" in supported_values
        else supported_values[0]
    )
    is_aspect_ratio = selection_kind == "aspect_ratio"
    return [
        FieldSchema(
            key="settings.aspect_ratio" if is_aspect_ratio else "settings.size",
            label="Fixed aspect ratio" if is_aspect_ratio else "Fixed size",
            description=(
                "Aspect ratio used when assistant selection is disabled, and as the fallback "
                "when the assistant does not request one."
                if is_aspect_ratio
                else "Output size used when assistant selection is disabled, and as the fallback "
                "when the assistant does not request one."
            ),
            type="select",
            options=[
                Option(value=value, label=value, translatable=False)
                for value in supported_values
            ],
            default=default_value,
            i18n_label=(
                "image_generation_fixed_aspect_ratio_label"
                if is_aspect_ratio
                else "image_generation_fixed_size_label"
            ),
            i18n_description=(
                "image_generation_fixed_aspect_ratio_description"
                if is_aspect_ratio
                else "image_generation_fixed_size_description"
            ),
            dependency=dependency_key,
            dependency_value=False,
        )
    ]


def build_assistant_size_selection_fields(
    provider_type: str,
    model_name: str,
) -> list[FieldSchema]:
    """Build mutually exclusive fixed and assistant-controlled size settings."""
    selection_kind = get_assistant_size_selection_kind(provider_type, model_name)
    if not selection_kind:
        return []

    if selection_kind == "aspect_ratio":
        label = "Allow the assistant to choose the aspect ratio"
        description = (
            "When enabled, the image-generation tool lets the assistant choose an aspect ratio for "
            "each request and shows an allowlist below. When disabled, a fixed aspect ratio is used."
        )
        i18n_label = "image_generation_allow_assistant_aspect_ratio_label"
        i18n_description = "image_generation_allow_assistant_aspect_ratio_description"
    elif selection_kind == "dimensions":
        label = "Allow the assistant to choose dimensions"
        description = (
            f"When enabled, the image-generation tool lets the assistant choose a width and height "
            f"from {OLLAMA_IMAGE_DIMENSION_MIN} to {OLLAMA_IMAGE_DIMENSION_MAX} pixels for each request. "
            f"When disabled, the fixed width and height below are used."
        )
        i18n_label = "image_generation_allow_assistant_dimensions_label"
        i18n_description = "image_generation_allow_assistant_dimensions_description"
    else:
        label = "Allow the assistant to choose the size"
        description = (
            "When enabled, the image-generation tool lets the assistant choose an output size for "
            "each request and shows an allowlist below. When disabled, a fixed size is used."
        )
        i18n_label = "image_generation_allow_assistant_size_label"
        i18n_description = "image_generation_allow_assistant_size_description"

    fields = [
        FieldSchema(
            key=f"settings.{ASSISTANT_SIZE_SELECTION_KEY}",
            label=label,
            description=description,
            type="boolean",
            default=True,
            i18n_label=i18n_label,
            i18n_description=i18n_description,
        )
    ]
    # Both branches are kept in the schema so the frontend can switch between
    # them immediately without discarding either configured value.
    fields.extend(build_fixed_image_size_fields(provider_type, model_name))
    allowed_sizes_field = build_allowed_tool_sizes_field(provider_type, model_name)
    if allowed_sizes_field:
        fields.append(allowed_sizes_field)
    return fields
