import base64
from io import BytesIO

from openai import Client

from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.openai.model_list import OPENAI_UNSUPPORTED_IMAGE_GENERATION_MODELS
from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
)


def _resolve_openai_image_bytes(response) -> bytes:
    """Resolve OpenAI image bytes."""
    data = getattr(response, "data", None) or []
    first_image = data[0] if data else None
    image_payload = getattr(first_image, "b64_json", None) if first_image else None
    if not image_payload:
        raise RuntimeError("OpenAI did not return image data")
    return base64.b64decode(image_payload)


def _build_openai_cost_payload(response, model: str, quality: str | None, size: str) -> tuple[float, dict]:
    """Build OpenAI cost payload."""
    usage = getattr(response, "usage", None)
    effective_quality = quality or "standard"
    cost = 0.0
    cost_details = {}
    try:
        cost = calculate_generation_price(model, effective_quality, size, usage)
        cost_details = {
            "model": model,
            "quality": effective_quality,
            "size": size,
        }
        if usage:
            cost_details["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
            cost_details["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
            input_details = getattr(usage, "input_tokens_details", None)
            output_details = getattr(usage, "output_tokens_details", None)
            if input_details:
                cost_details["input_text_tokens"] = _get_value(input_details, "text_tokens")
                cost_details["input_image_tokens"] = _get_value(input_details, "image_tokens")
            if output_details:
                cost_details["output_text_tokens"] = _get_value(output_details, "text_tokens")
                cost_details["output_image_tokens"] = _get_value(output_details, "image_tokens")
            elif getattr(usage, "output_tokens", None):
                # When the API omits details, treat all output tokens as image tokens.
                cost_details["output_image_tokens"] = getattr(usage, "output_tokens", 0) or 0
    except Exception:
        pass
    return cost, cost_details


def generate_image_openai(
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    quality: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
):
    """Generate image using OpenAI."""
    client_kwargs = {"api_key": api_key}
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    if quality and model.lower() != "dall-e-2":
        payload["quality"] = quality

    if model.lower() in {"dall-e-2", "dall-e-3"}:
        payload["response_format"] = "b64_json"
    img = client.images.generate(**payload)
    image_bytes = _resolve_openai_image_bytes(img)
    cost, cost_details = _build_openai_cost_payload(img, model, quality, size)

    return {
        "image_bytes": image_bytes,
        "cost": cost,
        "cost_details": cost_details,
    }


def edit_image_openai(
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    reference_images: list[dict | bytes],
    quality: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
):
    """Edit image using OpenAI."""
    if not reference_images:
        raise ValueError("reference_images is required for image edit")

    image_streams: list[BytesIO] = []
    for index, reference_image in enumerate(reference_images):
        image_bytes = reference_image
        image_name = f"reference_{index + 1}.png"
        if isinstance(reference_image, dict):
            image_bytes = reference_image.get("bytes")
            candidate_name = str(reference_image.get("filename") or "").strip()
            if candidate_name:
                image_name = candidate_name
        if not isinstance(image_bytes, (bytes, bytearray)):
            continue
        stream = BytesIO(bytes(image_bytes))
        stream.name = image_name
        image_streams.append(stream)

    if not image_streams:
        raise ValueError("No valid reference images were provided for image edit")

    client_kwargs = {"api_key": api_key}
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "image": image_streams[0] if len(image_streams) == 1 else image_streams,
    }
    if quality and model.lower() != "dall-e-2":
        payload["quality"] = quality

    if model.lower() in {"dall-e-2", "dall-e-3"}:
        payload["response_format"] = "b64_json"

    try:
        img = client.images.edit(**payload)
    finally:
        for stream in image_streams:
            try:
                stream.close()
            except Exception:
                pass

    image_bytes = _resolve_openai_image_bytes(img)
    cost, cost_details = _build_openai_cost_payload(img, model, quality, size)

    return {
        "image_bytes": image_bytes,
        "cost": cost,
        "cost_details": cost_details,
    }


def get_openai_image_generation_models(provider: LLMProvider):
    """Get OpenAI image generation models."""
    client_kwargs = {"api_key": provider.api_key}
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    default_headers = custom_headers_to_dict(provider_settings.get("custom_headers"))
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    models = client.models.list()
    unsupported_ids = set(OPENAI_UNSUPPORTED_IMAGE_GENERATION_MODELS)
    items = []
    for model in models:
        model_id = getattr(model, "id", None) or getattr(model, "model", None)
        if not model_id or model_id in unsupported_ids:
            continue
        items.append(
            {
                "id": model_id,
                "created": getattr(model, "created", None),
                "object": getattr(model, "object", None),
                "owned_by": getattr(model, "owned_by", None),
            }
        )
    return items

# Pricing per million tokens in usd
IMAGE_GEN_MODELS = [
    {
        "name": "ChatGPT Image Latest",
        "ids": ["chatgpt-image-latest"],
        "deprecated": True,
        "shutdown_date": "2026-12-01",
        "quality": ["low", "medium", "high"],
        "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
        "pricing": {
            "text_tokens": {
                "input": 5.00,
                "cached_input": 1.25,
                "output": 10.00,
            },
            "image_tokens": {
                "input": 8.00,
                "cached_input": 2.00,
                "output": 32.00,
            }
        }
    },
    {
        "name": "GPT Image 1.5",
        "ids": ["gpt-image-1.5", "gpt-image-1.5-2025-12-16"],
        "deprecated": True,
        "shutdown_date": "2026-12-01",
        "quality": ["low", "medium", "high"],
        "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],       
        "pricing": {
            "text_tokens": {
                "input": 5.00,
                "cached_input": 1.25,
                "output": 10.00,
            },
            "image_tokens": {
                "input": 8.00,
                "cached_input": 2.00,
                "output": 32.00,
            }
        }
    },
    {
        "name": "GPT Image 1",
        "ids": ["gpt-image-1"],
        "deprecated": True,
        "shutdown_date": "2026-10-23",
        "quality": ["low", "medium", "high"],
        "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
        "pricing": {
            "text_tokens": {
                "input": 5.00,
                "cached_input": 1.25,
                "output": 0.00,
            },
            "image_tokens": {
                "input": 10.00,
                "cached_input": 2.50,
                "output": 40.00,
            }
        }
    },
    {
        "name": "GPT Image 1 mini",
        "ids": ["gpt-image-1-mini"],
        "deprecated": True,
        "shutdown_date": "2026-12-01",
        "quality": ["low", "medium", "high"],
        "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
        "pricing": {
            "text_tokens": {
                "input": 2.00,
                "cached_input": 0.20,
                "output": 0.00,
            },
            "image_tokens": {
                "input": 2.50,
                "cached_input": 0.25,
                "output": 8.00,
            }
        }
    },
    {
        "name": "GPT Image 2",
        "ids": ["gpt-image-2-2026-04-21", "gpt-image-2"],
        "quality": ["low", "medium", "high"],
        "size": ["1024x1024", "1536x1024", "1024x1536", "2560x1440", "3840x2160", "auto"],
        "pricing": {
            "text_tokens": {
                "input": 5.00,
                "cached_input": 1.25,
                "output": 0.00,
            },
            "image_tokens": {
                "input": 8.00,
                "cached_input": 2.00,
                "output": 30.00,
            }
        }
    }
]


OPENAI_IMAGE_EDIT_SUPPORTED_MODEL_IDS = {
    "chatgpt-image-latest",
    "gpt-image-1",
    "gpt-image-1.5",
    "gpt-image-1.5-2025-12-16",
    "gpt-image-1-mini",
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
}


def openai_model_supports_image_edit(model_name: str) -> bool:
    """Check if OpenAI model supports image edit."""
    lowered = (model_name or "").strip().lower()
    if not lowered:
        return False
    if lowered in OPENAI_IMAGE_EDIT_SUPPORTED_MODEL_IDS:
        return True

    for model in IMAGE_GEN_MODELS:
        model_title = str(model.get("name") or "").strip().lower()
        model_ids = {str(mid).strip().lower() for mid in model.get("ids", []) if str(mid).strip()}
        if lowered == model_title or lowered in model_ids:
            return bool(model_ids & OPENAI_IMAGE_EDIT_SUPPORTED_MODEL_IDS)

    return False


def get_image_generation_schema_part_1(db, provider_id: str):
    """Get image generation schema part 1."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []
    if provider and provider.api_key:
        try:
            models = get_openai_image_generation_models(provider)
            for model in models:
                model_id = model.get("id") or model.get("model")
                if not model_id:
                    continue
                model_options.append(
                    Option(
                        value=model_id,
                        label=model_id,
                    )
                )
        except Exception:
            model_options = []

    schema = Sections(
        sections=[
            Section(
                title="OpenAI Image Generation",
                i18n_title="llm.shared.section_openai_image_generation.title",
                description="",
                i18n_description="llm.shared.section_value.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose which OpenAI model to use for this provider.",
                        i18n_description="llm.shared.model_name.description",
                        type="select",
                        options=model_options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    ),
                ]
            )
        ]
    )
    return schema


def get_image_generation_schema_part_2(model_name: str):
    """Get image generation schema part 2."""
    def _find_model_config(name: str):
        lowered = (name or "").lower()
        for model in IMAGE_GEN_MODELS:
            if lowered == model["name"].lower():
                return model
            if any(lowered == mid.lower() for mid in model.get("ids", [])):
                return model
        return None

    def _to_options(values: list[str]):
        return [Option(value=value, label=value) for value in values or []]

    def _default_value(options: list[Option], preferred: str | None) -> str | None:
        if not options:
            return None
        if preferred and any(option.value == preferred for option in options):
            return preferred
        return options[0].value

    model_config = _find_model_config(model_name)
    quality_options = _to_options(model_config.get("quality", []) if model_config else [])
    quality_default = _default_value(quality_options, "medium")
    image_edit_supported = openai_model_supports_image_edit(model_name)

    fields = [
        FieldSchema(
            key="settings.quality",
            label="Quality",
            i18n_label="llm.shared.settings.quality.label",
            description="Controls fidelity vs. speed for the selected model",
            i18n_description="llm.shared.settings.quality.description",
            type="select",
            options=quality_options,
            placeholder="",
            i18n_placeholder="llm.shared.settings.quality.placeholder",
            default=quality_default,
        ),
    ]
    if image_edit_supported:
        fields.append(
            FieldSchema(
                key="settings.enable_image_edit",
                label="Enable Image Edit",
                i18n_label="llm.shared.settings.enable_image_edit.label",
                description=(
                    "Allow this model to use image-edit mode with reference images "
                    "in the image generation tool."
                ),
                type="boolean",
                default=True,
            )
        )

    schema = Sections(
        sections=[
            Section(
                title="OpenAI Image Generation",
                i18n_title="llm.shared.section_openai_image_generation.title",
                description="",
                i18n_description="llm.shared.section_value.description",
                fields=fields
            )
        ]
    )
    return schema



TOKENS_PER_MILLION = 1_000_000


def _get_value(source, key, default=0):
    """Get value from source."""
    if source is None:
        return default
    if isinstance(source, dict):
        value = source.get(key, default)
    else:
        value = getattr(source, key, default)
    return default if value is None else value


def calculate_generation_price(model_id, quality, size, usage, models=IMAGE_GEN_MODELS):
    """Calculate generation price."""
    model = next((m for m in models if model_id in m["ids"]), None)
    if not model:
        raise ValueError(f"Unknown model id: {model_id}")

    pricing = model["pricing"]

    # DALL-E style flat pricing per size + quality combo
    quality_pricing = pricing.get(quality)
    if isinstance(quality_pricing, dict):
        if size not in quality_pricing:
            raise ValueError(f"Size {size} not priced for quality {quality}")
        return quality_pricing[size]

    total_price = 0.0

    text_pricing = pricing.get("text_tokens", {})
    image_pricing = pricing.get("image_tokens", {})

    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)

    if text_pricing:
        input_text_tokens = _get_value(input_details, "text_tokens")
        total_price += (input_text_tokens / TOKENS_PER_MILLION) * text_pricing.get("input", 0)

        if output_details:
            output_text_tokens = _get_value(output_details, "text_tokens")
        else:
            # No breakdown provided -> no text tokens were generated.
            output_text_tokens = 0
        total_price += (output_text_tokens / TOKENS_PER_MILLION) * text_pricing.get("output", 0)

    if image_pricing:
        input_image_tokens = _get_value(input_details, "image_tokens")
        total_price += (input_image_tokens / TOKENS_PER_MILLION) * image_pricing.get("input", 0)

        if output_details:
            output_image_tokens = _get_value(output_details, "image_tokens")
        else:
            output_image_tokens = _get_value(usage, "output_tokens")
        total_price += (output_image_tokens / TOKENS_PER_MILLION) * image_pricing.get("output", 0)

    return total_price
