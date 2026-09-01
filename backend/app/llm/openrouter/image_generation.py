import base64
import re
import requests
from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
)
from app.llm.models import LLMProvider
from app.llm.openrouter.common import build_openrouter_api_url, build_openrouter_headers


def _decode_image_entry(image_entry: dict) -> bytes | None:
    if not isinstance(image_entry, dict):
        return None

    url_block = image_entry.get("image_url") or {}
    data_url = url_block.get("url") if isinstance(url_block, dict) else None

    def _decode_base64_payload(payload: str) -> bytes | None:
        if not isinstance(payload, str) or not payload:
            return None
        try:
            return base64.b64decode(payload.split(",", 1)[-1])
        except (ValueError, TypeError):
            return None

    if isinstance(data_url, str) and data_url:
        if data_url.startswith("data:"):
            return _decode_base64_payload(data_url)
        if data_url.startswith(("http://", "https://")):
            # OpenRouter image URLs are provider-controlled data. Fetching them
            # from the backend would cross an untrusted boundary and can expose
            # internal network resources (SSRF), so only inline/base64 image
            # payloads are accepted here.
            return None
        decoded = _decode_base64_payload(data_url)
        if decoded:
            return decoded

    b64_json = image_entry.get("b64_json")
    if isinstance(b64_json, str) and b64_json:
        try:
            return base64.b64decode(b64_json)
        except (ValueError, TypeError):
            return None

    return None


def _build_usage_cost_details(response_payload: dict, model: str) -> tuple[float, dict]:
    usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
    if not isinstance(usage, dict):
        return 0.0, {}

    def _as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    cost_details: dict = {
        "model": response_payload.get("model") or model,
        "provider": response_payload.get("provider"),
        "prompt_tokens": _as_int(usage.get("prompt_tokens")),
        "completion_tokens": _as_int(usage.get("completion_tokens")),
        "total_tokens": _as_int(usage.get("total_tokens")),
        "is_byok": usage.get("is_byok"),
    }

    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}

    cost_details.update(
        {
            "prompt_cached_tokens": _as_int(prompt_details.get("cached_tokens")),
            "prompt_audio_tokens": _as_int(prompt_details.get("audio_tokens")),
            "prompt_video_tokens": _as_int(prompt_details.get("video_tokens")),
            "completion_image_tokens": _as_int(completion_details.get("image_tokens")),
            "completion_reasoning_tokens": _as_int(completion_details.get("reasoning_tokens")),
        }
    )

    upstream = usage.get("cost_details") or {}
    if isinstance(upstream, dict):
        cost_details.update(
            {
                "upstream_inference_cost": _as_float(upstream.get("upstream_inference_cost")),
                "upstream_inference_prompt_cost": _as_float(upstream.get("upstream_inference_prompt_cost")),
                "upstream_inference_completions_cost": _as_float(upstream.get("upstream_inference_completions_cost")),
            }
        )

    return _as_float(usage.get("cost")), {k: v for k, v in cost_details.items() if v not in (None, "", [])}

def get_openrouter_image_models(api_key: str, provider_settings: dict | None = None):
    headers = {"Authorization": f"Bearer {api_key}"}
    url = build_openrouter_api_url("/images/models", provider_settings)
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    models = response.json()
    models_data = models.get("data", []) if isinstance(models, dict) else []

    result_models = []

    for model in models_data:
        model_architecture = model.get("architecture") or {}
        model_output_formats = model_architecture.get("output_modalities") or []
        if "image" in model_output_formats:
            if model['id'] == "openrouter/auto":
                continue
            result_models.append(model)

    return result_models


def _get_image_model_supported_parameters(
    api_key: str,
    model: str,
    provider_settings: dict | None,
) -> dict:
    """Return the capability descriptors advertised for one image model."""
    for model_entry in get_openrouter_image_models(
        api_key,
        provider_settings=provider_settings,
    ):
        if str(model_entry.get("id") or "").strip() != model:
            continue
        supported = model_entry.get("supported_parameters")
        return supported if isinstance(supported, dict) else {}
    raise ValueError(f"OpenRouter image model '{model}' was not found in /images/models")


def _supported_enum_values(supported_parameters: dict, key: str) -> list[str]:
    """Extract an enum capability while preserving OpenRouter's spelling."""
    descriptor = supported_parameters.get(key)
    if not isinstance(descriptor, dict) or descriptor.get("type") != "enum":
        return []
    values = descriptor.get("values")
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _normalize_image_resolution(value: str) -> str | None:
    """Convert stored pixel dimensions to OpenRouter resolution tiers."""
    normalized = str(value or "").strip()
    enum_value = normalized.upper()
    if enum_value in {"512", "1K", "2K", "4K"}:
        return enum_value

    match = re.fullmatch(r"(\d+)\s*[xX×]\s*(\d+)", normalized)
    if not match:
        return None
    largest_dimension = max(int(match.group(1)), int(match.group(2)))
    for maximum, tier in ((512, "512"), (1024, "1K"), (2048, "2K"), (4096, "4K")):
        if largest_dimension <= maximum:
            return tier
    return None



def generate_image_openrouter(
    api_key: str,
    chat_history,
    model: str,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    referer: str | None = None,
    title: str | None = None,
    provider_settings: dict | None = None,
):
    """Generate an image through OpenRouter's dedicated Images API."""
    url = build_openrouter_api_url("/images", provider_settings)

    prompt_parts: list[str] = []
    for message in chat_history or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            prompt_parts.append(content.strip())
    prompt = "\n\n".join(prompt_parts).strip()
    if not prompt:
        raise ValueError("prompt is required for OpenRouter image generation")

    payload = {
        "model": model,
        "prompt": prompt,
    }
    request_headers = build_openrouter_headers(
        api_key,
        provider_settings,
        ranking_url=referer,
        ranking_title=title,
    )
    request_headers["Accept"] = "application/json"
    if aspect_ratio or image_size:
        supported_parameters = _get_image_model_supported_parameters(
            api_key,
            model,
            provider_settings,
        )
        if aspect_ratio:
            normalized_ratio = str(aspect_ratio).strip()
            supported_ratios = _supported_enum_values(
                supported_parameters,
                "aspect_ratio",
            )
            if normalized_ratio not in supported_ratios:
                raise ValueError(
                    f"Aspect ratio '{normalized_ratio}' is not supported by OpenRouter image model '{model}'"
                )
            payload["aspect_ratio"] = normalized_ratio
        if image_size:
            normalized_resolution = _normalize_image_resolution(str(image_size))
            supported_resolutions = _supported_enum_values(
                supported_parameters,
                "resolution",
            )
            if (
                normalized_resolution is None
                or normalized_resolution not in supported_resolutions
            ):
                raise ValueError(
                    f"Image resolution '{image_size}' is not supported by OpenRouter image model '{model}'"
                )
            payload["resolution"] = normalized_resolution
    response = requests.post(url, headers=request_headers, json=payload, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(
            "OpenRouter image generation failed: "
            f"{response.status_code} {response.text}"
        )
    try:
        result = response.json()
    except ValueError as exc:  # JSONDecodeError inherits ValueError
        snippet = response.text[:500]
        raise RuntimeError(
            "Failed to parse JSON response from OpenRouter images endpoint. "
            f"Raw body snippet: {snippet}"
        ) from exc

    cost, cost_details = _build_usage_cost_details(result, model)

    image_bytes: bytes | None = None
    for image_entry in result.get("data", []) if isinstance(result, dict) else []:
        decoded = _decode_image_entry(image_entry)
        if decoded:
            image_bytes = decoded
            break

    if not image_bytes:
        raise RuntimeError("OpenRouter did not return usable image data")

    return {
        "image_bytes": image_bytes,
        "cost": cost,
        "cost_details": cost_details,
    }



def get_image_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []
    if provider and provider.api_key:
        try:
            provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
            models = get_openrouter_image_models(provider.api_key, provider_settings=provider_settings)
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
                        description="Choose which model to use for this provider.",
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


def get_image_generation_schema_part_2():
    # OpenRouter's dedicated Images adapter currently forwards only the
    # capability-checked aspect-ratio/resolution contract. The admin route adds
    # the shared fixed/assistant-controlled aspect-ratio fields separately.
    # Returning no provider-specific fields prevents an ineffective generic
    # ``extra_body`` value from being presented as a Quality control.
    return Sections(sections=[])
