import base64
import os
import tempfile
from typing import Any

from google import genai
from google.genai import types

from app.llm.models import LLMProvider
from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
)
from app.llm.video_generation.shared import (
    collect_video_candidates,
    extract_job_id,
    to_plain_data,
    wait_for_job_result,
)
from app.llm.google_aistudio.model_list import (
    GOOGLE_AISTUDIO_VIDEO_GENERATION_MODELS,
    GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_LAST_UPDATED,
    GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_URL,
)

DEFAULT_API_VERSION = "v1beta"


def _normalize_model_id(model_name: str) -> str:
    return (model_name or "").strip().replace("models/", "", 1)


def _resolve_model_capabilities(model_name: str) -> dict[str, Any] | None:
    normalized = _normalize_model_id(model_name)
    if not normalized:
        return None
    for model in GOOGLE_AISTUDIO_VIDEO_GENERATION_MODELS:
        ids = [str(item or "").strip() for item in model.get("ids", [])]
        if normalized in ids:
            return model
    return None


def get_google_aistudio_video_model_pricing(model_name: str | None) -> dict[str, Any] | None:
    item = _resolve_model_capabilities(str(model_name or "")) or {}
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return None
    pricing_copy = dict(pricing)
    pricing_copy.setdefault("currency", "USD")
    pricing_copy.setdefault("unit", "second")
    pricing_copy.setdefault("source_url", GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_URL)
    pricing_copy.setdefault("source_last_updated", GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_LAST_UPDATED)
    return pricing_copy


def get_google_aistudio_video_model_capabilities(model_name: str | None) -> dict[str, Any]:
    item = _resolve_model_capabilities(str(model_name or "")) or {}
    pricing = get_google_aistudio_video_model_pricing(model_name)
    return {
        "label": str(item.get("name") or _normalize_model_id(str(model_name or "")) or "Google Video Model").strip(),
        "duration_seconds": [str(value) for value in item.get("duration_seconds", []) if str(value).strip()],
        "aspect_ratio": [str(value) for value in item.get("aspect_ratio", []) if str(value).strip()],
        "resolution_supported": [str(value) for value in item.get("resolution_supported", []) if str(value).strip()],
        "resolution_support": bool(item.get("resolution_support", False)),
        "pricing": pricing,
    }


def calculate_google_aistudio_video_generation_cost(
    model_name: str | None,
    *,
    duration_seconds: int | str | None,
    resolution: str | None,
) -> dict[str, Any] | None:
    pricing = get_google_aistudio_video_model_pricing(model_name)
    if not pricing:
        return None

    try:
        normalized_duration = max(int(duration_seconds or 0), 0)
    except (TypeError, ValueError):
        return None
    if normalized_duration <= 0:
        return None

    normalized_resolution = str(resolution or "720p").strip() or "720p"
    per_second = pricing.get("per_second")
    if not isinstance(per_second, dict):
        return None

    price_per_second = per_second.get(normalized_resolution)
    if price_per_second is None:
        price_per_second = per_second.get("default")
    if price_per_second is None and len(per_second) == 1:
        price_per_second = next(iter(per_second.values()))
    if price_per_second is None:
        return None

    try:
        normalized_price = float(price_per_second)
    except (TypeError, ValueError):
        return None

    total_cost = round(normalized_duration * normalized_price, 10)
    return {
        "cost": total_cost,
        "duration_seconds": normalized_duration,
        "resolution": normalized_resolution,
        "price_per_second": normalized_price,
        "pricing_model": "per_second",
        "billing_unit": str(pricing.get("unit") or "second").strip() or "second",
        "currency": str(pricing.get("currency") or "USD").strip() or "USD",
        "pricing_source_url": str(pricing.get("source_url") or GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_URL).strip(),
        "pricing_last_updated": str(
            pricing.get("source_last_updated") or GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_LAST_UPDATED
        ).strip(),
    }


def _pick_default_option(options: list[str], fallback: str | int) -> str | int:
    if not options:
        return fallback
    if str(fallback) in options:
        return str(fallback)
    return options[0]


def get_video_generation_schema_part_2(model_name: str):
    selected_model = _normalize_model_id(model_name) or "veo-3.1-generate-preview"
    capabilities = get_google_aistudio_video_model_capabilities(selected_model)
    model_label = str(capabilities.get("label") or selected_model).strip()

    duration_options = [str(item) for item in capabilities.get("duration_seconds") or []]
    if not duration_options:
        duration_options = ["4", "6", "8"]

    aspect_ratio_options = [str(item) for item in capabilities.get("aspect_ratio") or []]
    if not aspect_ratio_options:
        aspect_ratio_options = ["16:9", "9:16"]

    resolution_options = [str(item) for item in capabilities.get("resolution_supported") or []]
    has_resolution = bool(capabilities.get("resolution_support")) and bool(resolution_options)

    generation_fields = [
        FieldSchema(
            key="duration_seconds",
            label="Duration (seconds)",
            description="Target video length in seconds.",
            type="select",
            options=[
                Option(value=opt, label=f"{opt} seconds")
                for opt in duration_options
            ],
            default=_pick_default_option(duration_options, "6"),
            i18n_label="schema_video_generation_duration_seconds",
            i18n_description="schema_video_generation_duration_seconds_desc",
        ),
        FieldSchema(
            key="aspect_ratio",
            label="Aspect Ratio",
            description="Target aspect ratio for generated videos.",
            type="select",
            options=[Option(value=opt, label=opt) for opt in aspect_ratio_options],
            default=_pick_default_option(aspect_ratio_options, "16:9"),
            i18n_label="schema_video_generation_aspect_ratio",
            i18n_description="schema_video_generation_aspect_ratio_desc",
        ),
        FieldSchema(
            key="enable_reference_files",
            label="Enable Reference Files",
            description=(
                "Allow the video_generation tool to pass chat reference images "
                "to Google video generation (first/last frame style inputs)."
            ),
            type="boolean",
            default=False,
        ),
    ]

    if has_resolution:
        generation_fields.append(
            FieldSchema(
                key="resolution",
                label="Resolution",
                description="Output resolution preset.",
                type="select",
                options=[Option(value=opt, label=opt) for opt in resolution_options],
                default=_pick_default_option(resolution_options, "720p"),
                i18n_label="schema_video_generation_resolution",
                i18n_description="schema_video_generation_resolution_desc",
            )
        )

    execution_fields = [
        FieldSchema(
            key="timeout_seconds",
            label="Job Timeout (seconds)",
            description="Maximum wait time for provider jobs before timeout.",
            type="number",
            attributes={"min": 60, "max": 3600},
            default=600,
            i18n_label="schema_video_generation_timeout_seconds",
            i18n_description="schema_video_generation_timeout_seconds_desc",
        ),
        FieldSchema(
            key="poll_interval_seconds",
            label="Poll Interval (seconds)",
            description="How often to poll provider job status.",
            type="number",
            attributes={"min": 1, "max": 30},
            default=5,
            i18n_label="schema_video_generation_poll_interval_seconds",
            i18n_description="schema_video_generation_poll_interval_seconds_desc",
        ),
        FieldSchema(
            key="max_retries",
            label="Max Retries",
            description="Maximum retry attempts for transient download/API failures.",
            type="number",
            attributes={"min": 0, "max": 10},
            default=2,
            i18n_label="schema_video_generation_max_retries",
            i18n_description="schema_video_generation_max_retries_desc",
        ),
    ]

    return Sections(
        sections=[
            Section(
                title="Generation Settings",
                description=f"Configure defaults for {model_label}.",
                i18n_title="schema_video_generation_sec1_title",
                i18n_description="schema_video_generation_sec1_desc",
                fields=generation_fields,
            ),
            Section(
                title="Execution Controls",
                description=f"Configure polling, timeouts, and retries for {model_label}.",
                i18n_title="schema_video_generation_sec2_title",
                i18n_description="schema_video_generation_sec2_desc",
                fields=execution_fields,
            ),
        ]
    )







def get_google_aistudio_video_generation_models(api_key: str, api_version: str = "v1alpha"):
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version=api_version))

    def _normalize_model_name(model: str) -> str:
        model = model or ""
        return model.replace("models/", "", 1)

    models = client.models.list()
    return_models = []

    for model in models:
        if "veo" in model.name:
            normalized_id = _normalize_model_name(model.name)
            return_models.append(
                {
                    "id": normalized_id,
                    "name": model.display_name
                }
            )
    return return_models


def getGoogleAistudioVideoGenerationModels(api_key: str, api_version: str = "v1alpha"):
    """Backward-compatible camelCase alias used by admin settings wiring."""
    return get_google_aistudio_video_generation_models(api_key=api_key, api_version=api_version)

def _google_model_name(model_name: str) -> str:
    if model_name.startswith("models/"):
        return model_name
    return f"models/{model_name}"


def _resolve_api_version(provider: LLMProvider) -> str:
    if isinstance(provider.settings, dict):
        value = str(provider.settings.get("api_version") or DEFAULT_API_VERSION)
    else:
        value = DEFAULT_API_VERSION
    return value or DEFAULT_API_VERSION


def _build_generation_config(
    config: dict[str, Any],
    *,
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model_name = str(config.get("model_name") or "").strip()
    capabilities = _resolve_model_capabilities(model_name) or {}

    duration_options = {str(item) for item in capabilities.get("duration_seconds") or []}
    aspect_ratio_options = {str(item) for item in capabilities.get("aspect_ratio") or []}
    resolution_options = {str(item) for item in capabilities.get("resolution_supported") or []}
    has_resolution = bool(capabilities.get("resolution_support")) and bool(resolution_options)

    duration_value = config.get("duration_seconds")
    if duration_value is not None:
        duration_value = str(duration_value).strip()
        if duration_options and duration_value not in duration_options:
            duration_value = None

    aspect_ratio_value = config.get("aspect_ratio")
    if aspect_ratio_value is not None:
        aspect_ratio_value = str(aspect_ratio_value).strip()
        if aspect_ratio_options and aspect_ratio_value not in aspect_ratio_options:
            aspect_ratio_value = None

    resolution_value = config.get("resolution")
    if resolution_value is not None:
        resolution_value = str(resolution_value).strip()
        if (has_resolution and resolution_options and resolution_value not in resolution_options) or not has_resolution:
            resolution_value = None
    if resolution_value in {"1080p", "4k"} and "8" in duration_options:
        duration_value = "8"
    if reference_files and "8" in duration_options:
        duration_value = "8"

    generation_config: dict[str, Any] = {
        "duration_seconds": duration_value,
        "resolution": resolution_value,
        "aspect_ratio": aspect_ratio_value,
        "fps": config.get("fps"),
    }
    negative_prompt = config.get("negative_prompt")
    if negative_prompt:
        generation_config["negative_prompt"] = negative_prompt
    seed = config.get("seed")
    if seed is not None:
        generation_config["seed"] = seed
    return {k: v for k, v in generation_config.items() if v not in (None, "")}


def _normalize_image_mime_type(value: str | None) -> str:
    mime_type = str(value or "").strip().lower()
    if mime_type.startswith("image/"):
        return mime_type
    return "image/png"


def _google_image_from_reference(reference_file: dict[str, Any]):
    image_bytes = (reference_file or {}).get("bytes")
    if not isinstance(image_bytes, (bytes, bytearray)):
        return None, None

    mime_type = _normalize_image_mime_type((reference_file or {}).get("mime_type"))
    image_type = getattr(types, "Image", None)
    if image_type is None:
        return None, mime_type

    from_bytes = getattr(image_type, "from_bytes", None)
    if callable(from_bytes):
        try:
            return from_bytes(data=bytes(image_bytes), mime_type=mime_type), mime_type
        except TypeError:
            try:
                return from_bytes(bytes(image_bytes), mime_type), mime_type
            except Exception:
                pass

    from_file = getattr(image_type, "from_file", None)
    if callable(from_file):
        suffix = ".png"
        if mime_type in {"image/jpeg", "image/jpg"}:
            suffix = ".jpg"
        elif mime_type == "image/webp":
            suffix = ".webp"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(bytes(image_bytes))
                temp_path = temp_file.name
            try:
                return from_file(location=temp_path), mime_type
            except TypeError:
                return from_file(temp_path), mime_type
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    return None, mime_type


def _build_reference_generation_config(
    reference_files: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if not reference_files:
        return {}

    image_refs = [
        ref
        for ref in reference_files
        if str((ref or {}).get("mime_type") or "").strip().lower().startswith("image/")
        and isinstance((ref or {}).get("bytes"), (bytes, bytearray))
    ]
    if not image_refs:
        return {}

    first_image_obj, first_mime = _google_image_from_reference(image_refs[0])
    last_image_obj, last_mime = (None, None)
    if len(image_refs) > 1:
        last_image_obj, last_mime = _google_image_from_reference(image_refs[1])

    if first_image_obj is not None:
        config: dict[str, Any] = {"image": first_image_obj}
        if last_image_obj is not None:
            config["last_frame"] = last_image_obj
        return config

    # Fallback payload style based on documented first/last frame REST shape.
    first_b64 = base64.b64encode(bytes(image_refs[0]["bytes"])).decode("ascii")
    config = {
        "first_frame": {
            "reference_image": {
                "image_bytes": first_b64,
                "mime_type": first_mime or "image/png",
            }
        }
    }
    if len(image_refs) > 1:
        last_b64 = base64.b64encode(bytes(image_refs[1]["bytes"])).decode("ascii")
        config["last_frame"] = {
            "reference_image": {
                "image_bytes": last_b64,
                "mime_type": last_mime or "image/png",
            }
        }
    return config


def _generate_via_google_sdk(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    api_version = _resolve_api_version(provider)
    client = genai.Client(
        api_key=provider.api_key,
        http_options=types.HttpOptions(api_version=api_version),
    )

    model_api = getattr(client, "models", None)
    if model_api is None or not hasattr(model_api, "generate_videos"):
        raise RuntimeError("Installed google-genai client does not support generate_videos.")

    generation_config = _build_generation_config(config, reference_files=reference_files)
    generation_config.update(_build_reference_generation_config(reference_files))
    request_kwargs: dict[str, Any] = {
        "model": _google_model_name(model_name),
        "prompt": prompt,
    }
    if generation_config:
        try:
            request_kwargs["config"] = types.GenerateVideosConfig(**generation_config)
        except Exception:
            request_kwargs["config"] = generation_config

    operation = model_api.generate_videos(**request_kwargs)
    operation_payload = to_plain_data(operation)
    job_id = extract_job_id(operation_payload)

    urls, inline_videos = collect_video_candidates(operation_payload)
    if urls or inline_videos:
        cost_info = calculate_google_aistudio_video_generation_cost(
            model_name,
            duration_seconds=generation_config.get("duration_seconds"),
            resolution=generation_config.get("resolution") or "720p",
        )
        return {
            "provider_job_id": job_id,
            "payload": operation_payload,
            "urls": urls,
            "inline_videos": inline_videos,
            "request_config": dict(generation_config),
            "cost": float((cost_info or {}).get("cost") or 0.0),
            "cost_details": {"model": _normalize_model_id(model_name), **cost_info} if cost_info else {},
        }

    operations_api = getattr(client, "operations", None)
    if operations_api is None or not hasattr(operations_api, "get") or not job_id:
        raise RuntimeError("Google AI Studio did not return a pollable video operation.")

    timeout_seconds = int(config.get("timeout_seconds") or 600)
    poll_interval_seconds = int(config.get("poll_interval_seconds") or 5)

    final_payload = wait_for_job_result(
        lambda: to_plain_data(operations_api.get(name=job_id)),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        provider_name="Google AI Studio",
    )
    final_urls, final_inline_videos = collect_video_candidates(final_payload)
    if not final_urls and not final_inline_videos:
        raise RuntimeError("Google AI Studio completed the job but returned no downloadable video.")

    cost_info = calculate_google_aistudio_video_generation_cost(
        model_name,
        duration_seconds=generation_config.get("duration_seconds"),
        resolution=generation_config.get("resolution") or "720p",
    )
    return {
        "provider_job_id": job_id,
        "payload": final_payload,
        "urls": final_urls,
        "inline_videos": final_inline_videos,
        "request_config": dict(generation_config),
        "cost": float((cost_info or {}).get("cost") or 0.0),
        "cost_details": {"model": _normalize_model_id(model_name), **cost_info} if cost_info else {},
    }


def generate_video(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return _generate_via_google_sdk(
            provider,
            model_name,
            prompt,
            config,
            reference_files=reference_files,
        )
    except Exception as exc:  # pragma: no cover - provider behavior
        raise RuntimeError(f"Google AI Studio video generation failed: {exc}") from exc
