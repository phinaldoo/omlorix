import base64
import uuid
import json
import logging
from pathlib import Path
from typing import Any

from app.chats.models import ChatMessages
from app.database import SessionLocal
from app.files.models import Files
from app.files.utils import (
    get_file_category,
    materialize_file_record,
    persist_generated_file_bytes,
    release_user_file_quota_reservation,
    reserve_user_file_quota,
)
from app.llm.models import LLMProvider
from app.settings.models import get_settings_page
from app.tools.image_generation.size_options import (
    ASSISTANT_SIZE_SELECTION_KEY,
    OLLAMA_IMAGE_DIMENSION_MAX,
    OLLAMA_IMAGE_DIMENSION_MIN,
    assistant_size_selection_enabled,
    get_effective_tool_size_values,
    validate_image_generation_settings_size,
    validate_requested_ollama_dimensions,
    validate_requested_tool_size,
)


logger = logging.getLogger(__name__)


IMAGE_GENERATION_TOOL_TYPES = {"image_generation", "image_edit"}
IMAGE_EDIT_SUPPORTED_PROVIDER_TYPES = {
    "openai",
    "openai_responses",
    "openai_chat_completions",
    "google_aistudio",
    "xai",
}
PROTECTED_IMAGE_GENERATION_SETTING_KEYS = {
    ASSISTANT_SIZE_SELECTION_KEY,
    "allowed_sizes",
}


def _coerce_optional_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _extract_attachment_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return [stripped]
        return _extract_attachment_ids(parsed)
    if isinstance(raw, dict):
        file_id = raw.get("id") or raw.get("file_id")
        return [str(file_id)] if file_id else []
    if isinstance(raw, list):
        ids: list[str] = []
        for item in raw:
            ids.extend(_extract_attachment_ids(item))
        return ids
    return [str(raw)]


def _collect_image_ids_from_content(raw_content: Any) -> list[str]:
    if raw_content is None:
        return []
    if isinstance(raw_content, str):
        stripped = raw_content.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return []
        return _collect_image_ids_from_content(parsed)
    if isinstance(raw_content, dict):
        return _extract_attachment_ids(raw_content.get("images"))
    if isinstance(raw_content, list):
        ids: list[str] = []
        for block in raw_content:
            if isinstance(block, dict):
                ids.extend(_extract_attachment_ids(block.get("images")))
        return ids
    return []


def _collect_image_ids_from_chat_history(chat_history: list | None) -> list[str]:
    if not isinstance(chat_history, list):
        return []
    collected: list[str] = []
    for message in chat_history:
        if isinstance(message, dict):
            collected.extend(_extract_attachment_ids(message.get("images")))
            collected.extend(_collect_image_ids_from_content(message.get("content")))
            continue
        collected.extend(_extract_attachment_ids(getattr(message, "images", None)))
        collected.extend(_collect_image_ids_from_content(getattr(message, "content", None)))
    return collected


def _collect_image_ids_from_chat_db(db, chat_id: str | None) -> list[str]:
    if not chat_id:
        return []
    rows = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )
    collected: list[str] = []
    for row in rows:
        collected.extend(_collect_image_ids_from_content(getattr(row, "content", None)))
    return collected


def _dedupe_ids(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def get_image_size_tool_params(db=None) -> dict:
    """Return a dict of JSON-schema properties to inject into the
    image_generation tool schema so the LLM can choose the image size.

    The shape depends on the active provider:
      - OpenAI / Google AI Studio: ``{"size": {... enum ...}}``
      - Ollama: ``{"width": {... int ...}, "height": {... int ...}}``
      - Others (no predefined sizes): ``{}``
    """

    close_db = False
    if db is None:
        from app.database import SessionLocal as _SL
        db = _SL()
        close_db = True
    try:
        config = _get_image_generation_config(db)
        provider_id = config.get("provider_id", "")
        model_name = config.get("model_name", "")
        if not provider_id or not model_name:
            return {}

        provider = _resolve_provider(db, provider_id)
        if not provider:
            return {}

        provider_type = provider.provider or "openai"

        if provider_type in {
            "openai",
            "openai_responses",
            "openai_chat_completions",
        }:
            sizes = get_effective_tool_size_values(provider_type, model_name, config.get("settings"))
            if sizes:
                return {"size": {
                    "type": "string",
                    "description": "The size / dimensions of the generated image. Pick the option that best fits the content.",
                    "enum": sizes,
                }}
            return {}

        if provider_type in {"google_aistudio", "xai", "openrouter"}:
            sizes = get_effective_tool_size_values(provider_type, model_name, config.get("settings"))
            if sizes:
                return {"size": {
                    "type": "string",
                    "description": "The aspect ratio of the generated image. Pick the option that best fits the content.",
                    "enum": sizes,
                }}
            return {}

        if provider_type == "ollama":
            if not assistant_size_selection_enabled(config.get("settings")):
                return {}
            return {
                "width": {
                    "type": "integer",
                    "description": "Width of the generated image in pixels (e.g. 1024).",
                    "minimum": OLLAMA_IMAGE_DIMENSION_MIN,
                    "maximum": OLLAMA_IMAGE_DIMENSION_MAX,
                },
                "height": {
                    "type": "integer",
                    "description": "Height of the generated image in pixels (e.g. 1024).",
                    "minimum": OLLAMA_IMAGE_DIMENSION_MIN,
                    "maximum": OLLAMA_IMAGE_DIMENSION_MAX,
                },
            }

        # openrouter / openai_responses / others – no predefined sizes
        return {}
    except Exception:
        logger.exception("Failed to resolve image size tool params")
        return {}
    finally:
        if close_db:
            db.close()


def _is_openai_image_edit_enabled(settings: dict, model_name: str) -> bool:
    from app.llm.openai.image_generation import openai_model_supports_image_edit

    enabled_flag = _coerce_optional_bool((settings or {}).get("enable_image_edit"), default=False)
    if not enabled_flag:
        return False
    return openai_model_supports_image_edit(model_name)


def _is_openai_compatible_image_edit_enabled(settings: dict) -> bool:
    return _coerce_optional_bool((settings or {}).get("enable_image_edit"), default=False)


def _is_google_image_edit_enabled(settings: dict, model_name: str) -> bool:
    from app.llm.google_aistudio.image_generation import google_model_supports_image_edit

    enabled_flag = _coerce_optional_bool((settings or {}).get("enable_image_edit"), default=False)
    if not enabled_flag:
        return False
    return google_model_supports_image_edit(model_name)


def _is_provider_image_edit_enabled(provider_type: str, settings: dict, model_name: str) -> bool:
    normalized_provider = str(provider_type or "").strip().lower()
    if normalized_provider == "openai":
        return _is_openai_image_edit_enabled(settings, model_name)
    if normalized_provider in {"openai_responses", "openai_chat_completions"}:
        return _is_openai_compatible_image_edit_enabled(settings)
    if normalized_provider == "google_aistudio":
        return _is_google_image_edit_enabled(settings, model_name)
    if normalized_provider == "xai":
        return _is_openai_compatible_image_edit_enabled(settings)
    return False


def get_image_edit_tool_params(db=None) -> dict:
    """Return dynamic tool params for image edit features when enabled."""
    close_db = False
    if db is None:
        from app.database import SessionLocal as _SL

        db = _SL()
        close_db = True
    try:
        config = _get_image_generation_config(db)
        provider_id = config.get("provider_id", "")
        model_name = config.get("model_name", "")
        settings = config.get("settings", {}) or {}

        if not provider_id or not model_name:
            return {}

        provider = _resolve_provider(db, provider_id)
        if not provider:
            return {}

        provider_type = (provider.provider or "").strip().lower()
        if provider_type not in IMAGE_EDIT_SUPPORTED_PROVIDER_TYPES:
            return {}

        if not _is_provider_image_edit_enabled(provider_type, settings, model_name):
            return {}

        return {
            "type": {
                "type": "string",
                "description": (
                    "Choose whether to generate a new image or edit using reference images. "
                    "If set to image_edit, the configured provider's image edit flow is used."
                ),
                "enum": ["image_generation", "image_edit"],
            },
            "use_reference_images": {
                "type": "boolean",
                "description": (
                    "When true, all images from this chat conversation are passed as references. "
                    "This forces image edit mode even when type=image_generation."
                ),
            },
        }
    except Exception:
        logger.exception("Failed to resolve image edit tool params")
        return {}
    finally:
        if close_db:
            db.close()


def _get_image_generation_config(db=None) -> dict:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        config = {"provider_id": "", "model_name": "", "settings": {}}
        settings_record = get_settings_page(db, "image_generation")
        if settings_record and isinstance(settings_record.data, dict):
            config["provider_id"] = settings_record.data.get("provider_id", "") or ""
            config["model_name"] = settings_record.data.get("model_name", "") or ""
            config["settings"] = settings_record.data.get("settings", {}) or {}
        return config
    finally:
        if close_db:
            db.close()


def _merge_image_generation_config(
    base_config: dict[str, Any] | None,
    override_config: dict[str, Any] | None,
) -> dict[str, Any]:
    base = base_config if isinstance(base_config, dict) else {}
    override = override_config if isinstance(override_config, dict) else {}
    merged = {
        "provider_id": str(base.get("provider_id") or "").strip(),
        "model_name": str(base.get("model_name") or "").strip(),
        "settings": dict(base.get("settings") or {}),
    }
    if not override:
        return merged

    # Intentionally ignore provider/model overrides. Chat model settings only
    # override generation parameters for the configured tool model.
    override_settings = override.get("settings")
    if isinstance(override_settings, dict):
        for key, value in override_settings.items():
            if key in PROTECTED_IMAGE_GENERATION_SETTING_KEYS:
                continue
            if value is not None:
                merged["settings"][key] = value

    for key, value in override.items():
        if key in {"provider_id", "model_name", "settings"}:
            continue
        if key in PROTECTED_IMAGE_GENERATION_SETTING_KEYS:
            continue
        if value is not None:
            merged["settings"][key] = value

    return merged


def _resolve_provider(db, provider_id: str) -> LLMProvider | None:
    if not provider_id:
        return None
    return db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()


def _generate_via_openai(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    settings: dict,
    *,
    use_image_edit: bool = False,
    reference_images: list[dict] | None = None,
) -> dict:
    from app.llm.openai.image_generation import edit_image_openai, generate_image_openai

    quality = settings.get("quality")
    size = settings.get("size", "1024x1024")
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    custom_headers = provider_settings.get("custom_headers")
    if use_image_edit:
        result = edit_image_openai(
            api_key=provider.api_key,
            model=model_name,
            prompt=prompt,
            size=size,
            quality=quality,
            reference_images=reference_images or [],
            custom_headers=custom_headers,
        )
    else:
        result = generate_image_openai(
            api_key=provider.api_key,
            model=model_name,
            prompt=prompt,
            size=size,
            quality=quality,
            custom_headers=custom_headers,
        )
    # result is a dict with image_bytes, cost, cost_details
    return result


def _resolve_openai_compatible_extra_body(settings: dict) -> dict | None:
    extra_body = None
    extra_body_raw = settings.get("extra_body")
    if extra_body_raw:
        if isinstance(extra_body_raw, str):
            try:
                extra_body = json.loads(extra_body_raw)
            except (json.JSONDecodeError, ValueError):
                extra_body = None
        elif isinstance(extra_body_raw, dict):
            extra_body = extra_body_raw
    return extra_body


def _generate_via_openai_responses(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    settings: dict,
    *,
    use_image_edit: bool = False,
    reference_images: list[dict] | None = None,
) -> bytes:
    from app.llm.openai_responses.image_generation import (
        edit_image_openai_responses,
        generate_image_openai_responses,
    )

    base_url = provider.settings.get("base_url", "") if provider.settings else ""
    custom_headers = provider.settings.get("custom_headers") if provider.settings else None
    if not base_url:
        raise ValueError("Provider base_url is not configured")
    extra_body = _resolve_openai_compatible_extra_body(settings)

    if use_image_edit:
        return edit_image_openai_responses(
            base_url=base_url,
            api_key=provider.api_key,
            model=model_name,
            prompt=prompt,
            size=settings.get("size", "1024x1024"),
            quality=settings.get("quality"),
            reference_images=reference_images or [],
            extra_body=extra_body,
            custom_headers=custom_headers,
        )

    return generate_image_openai_responses(
        base_url=base_url,
        api_key=provider.api_key,
        model=model_name,
        prompt=prompt,
        size=settings.get("size"),
        # The provider adapter converts the schema's explicit ``off`` sentinel
        # to omission before the request is serialized.
        quality=settings.get("quality"),
        extra_body=extra_body,
        custom_headers=custom_headers,
    )


def _generate_via_openrouter(provider: LLMProvider, model_name: str, prompt: str, settings: dict) -> dict | bytes:
    from app.llm.openrouter.image_generation import generate_image_openrouter
    chat_history = [{"role": "user", "content": prompt}]
    result = generate_image_openrouter(
        api_key=provider.api_key,
        chat_history=chat_history,
        model=model_name,
        aspect_ratio=settings.get("aspect_ratio"),
        image_size=settings.get("image_size") or settings.get("size"),
        provider_settings=provider.settings if isinstance(provider.settings, dict) else {},
    )

    # Newer implementation returns a dict containing image_bytes + cost metadata.
    if isinstance(result, dict):
        image_bytes = result.get("image_bytes")
        if not image_bytes:
            raise RuntimeError("OpenRouter did not include image bytes in the response")
        if not isinstance(image_bytes, (bytes, bytearray)):
            raise RuntimeError("OpenRouter image bytes payload has unexpected type")
        # Keep type stable for callers (dict with metadata)
        return {
            "image_bytes": bytes(image_bytes),
            "cost": result.get("cost", 0.0),
            "cost_details": result.get("cost_details", {}),
        }

    # Legacy behavior: OpenRouter helper returned a list/str of encoded images.
    if not result:
        raise RuntimeError("OpenRouter did not return any images")

    image_data = result[0] if isinstance(result, list) else result
    if isinstance(image_data, str):
        if image_data.startswith("data:"):
            header, _, b64_part = image_data.partition(",")
            return base64.b64decode(b64_part)
        return base64.b64decode(image_data)
    if isinstance(image_data, bytes):
        return image_data

    raise RuntimeError("Unexpected image data format from OpenRouter")


def _generate_via_google_aistudio(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    settings: dict,
    *,
    use_image_edit: bool = False,
    reference_images: list[dict] | None = None,
) -> bytes:
    from app.llm.google_aistudio.image_generation import (
        edit_image_google_aistudio,
        generate_image_google_aistudio,
    )

    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    api_version = provider_settings.get("api_version")

    if use_image_edit:
        return edit_image_google_aistudio(
            api_key=provider.api_key,
            model=model_name,
            prompt=prompt,
            reference_images=reference_images or [],
            settings=settings,
            api_version=api_version,
        )

    return generate_image_google_aistudio(
        api_key=provider.api_key,
        model=model_name,
        prompt=prompt,
        settings=settings,
        api_version=api_version,
    )


def _generate_via_xai(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    settings: dict,
    *,
    use_image_edit: bool = False,
    reference_images: list[dict] | None = None,
) -> dict:
    """Dispatch to xAI's native JSON image endpoints."""
    from app.llm.xai.image_generation import edit_image, generate_image

    if use_image_edit:
        return edit_image(
            provider,
            model_name,
            prompt,
            settings,
            reference_images or [],
        )
    return generate_image(provider, model_name, prompt, settings)


def _generate_via_ollama(provider: LLMProvider, model_name: str, prompt: str, settings: dict) -> dict:
    from app.llm.ollama.image_generation import generate_image_ollama

    return generate_image_ollama(
        provider=provider,
        model=model_name,
        prompt=prompt,
        settings=settings,
    )

_PROVIDER_GENERATORS = {
    "openai": _generate_via_openai,
    "openai_responses": _generate_via_openai_responses,
    "openai_chat_completions": _generate_via_openai_responses,
    "openrouter": _generate_via_openrouter,
    "google_aistudio": _generate_via_google_aistudio,
    "xai": _generate_via_xai,
    "ollama": _generate_via_ollama,
}


def _resolve_reference_image_ids(
    *,
    db,
    chat_id: str | None,
    chat_history: list | None,
    explicit_image_ids: list[str] | None,
) -> list[str]:
    prioritized: list[str] = []
    if explicit_image_ids:
        prioritized.extend(explicit_image_ids)
    prioritized.extend(_collect_image_ids_from_chat_db(db, chat_id))
    prioritized.extend(_collect_image_ids_from_chat_history(chat_history))
    return _dedupe_ids(prioritized)


def _load_reference_images(db, user_id: str, image_ids: list[str]) -> list[dict]:
    if not image_ids:
        return []

    rows = (
        db.query(Files)
        .filter(Files.user_id == str(user_id), Files.id.in_(image_ids))
        .all()
    )
    row_map = {str(row.id): row for row in rows if row and row.id}

    loaded: list[dict] = []
    for image_id in image_ids:
        row = row_map.get(str(image_id))
        if not row:
            continue
        mime_type = str(row.file_type or "").strip().lower()
        if row.file_category != "image" and not mime_type.startswith("image/"):
            continue
        try:
            file_path = materialize_file_record(row, str(row.user_id))
            image_bytes = file_path.read_bytes()
        except Exception:
            logger.warning("Failed to read reference image file %s", image_id, exc_info=True)
            continue
        if not image_bytes:
            continue
        loaded.append(
            {
                "file_id": str(image_id),
                "filename": row.file_name,
                "mime_type": row.file_type,
                "bytes": image_bytes,
            }
        )
    return loaded


def image_generation(
    prompt: str,
    user_id: str,
    filename: str | None = None,
    size: str | None = None,
    width: int | None = None,
    height: int | None = None,
    config_override: dict | None = None,
    generation_type: str = "image_generation",
    use_reference_images: bool = False,
    reference_image_ids: list[str] | None = None,
    chat_id: str | None = None,
    chat_history: list | None = None,
) -> dict:
    if not user_id:
        raise ValueError("user_id is required for image generation")
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required for image generation")
    normalized_generation_type = str(generation_type or "image_generation").strip().lower() or "image_generation"
    if normalized_generation_type not in IMAGE_GENERATION_TOOL_TYPES:
        allowed_types = ", ".join(sorted(IMAGE_GENERATION_TOOL_TYPES))
        raise ValueError(f"generation_type must be one of: {allowed_types}")

    config = _merge_image_generation_config(
        _get_image_generation_config(),
        config_override,
    )
    provider_id = config.get("provider_id", "")
    model_name = config.get("model_name", "")
    settings = dict(config.get("settings") or {})

    if not provider_id:
        raise ValueError("Image generation is not configured. Please set a provider in admin settings.")
    if not model_name:
        raise ValueError("Image generation model is not configured. Please set a model in admin settings.")

    db = SessionLocal()
    quota_reservation = None
    try:
        quota_reservation = reserve_user_file_quota(
            db,
            user_id=str(user_id),
            purpose="image_generation",
        )
        provider = _resolve_provider(db, provider_id)
        if not provider:
            raise ValueError(f"Image generation provider not found: {provider_id}")
        if provider.provider != "ollama" and not provider.api_key:
            raise ValueError("Image generation provider API key is not configured")

        provider_type = str(provider.provider or "openai").strip().lower() or "openai"
        request_uses_references = bool(use_reference_images)
        should_use_image_edit = normalized_generation_type == "image_edit" or request_uses_references
        image_edit_enabled = _is_provider_image_edit_enabled(provider_type, settings, model_name)

        # If the LLM chose a size (or width/height), inject it into settings so
        # the provider generator picks it up (overrides the admin-configured default).
        if size is not None or width is not None or height is not None:
            if size:
                validated_size = validate_requested_tool_size(
                    provider_type,
                    model_name,
                    settings,
                    size,
                )
                if validated_size:
                    if provider_type in {"google_aistudio", "xai", "openrouter"}:
                        settings["aspect_ratio"] = validated_size
                        if provider_type == "google_aistudio":
                            settings["aspectRatio"] = validated_size
                    else:
                        settings["size"] = validated_size

            if provider_type == "ollama":
                if not assistant_size_selection_enabled(settings):
                    raise ValueError(
                        "Assistant image-dimension selection is disabled in admin settings."
                    )
                validated_width, validated_height = validate_requested_ollama_dimensions(
                    width,
                    height,
                )
                if validated_width is not None:
                    settings["width"] = str(validated_width)
                if validated_height is not None:
                    settings["height"] = str(validated_height)

        validate_image_generation_settings_size(provider_type, model_name, settings)

        reference_images: list[dict] = []
        if should_use_image_edit:
            if provider_type not in IMAGE_EDIT_SUPPORTED_PROVIDER_TYPES:
                supported = ", ".join(sorted(IMAGE_EDIT_SUPPORTED_PROVIDER_TYPES))
                raise ValueError(
                    f"Image edit is not supported for provider '{provider_type}'. "
                    f"Supported providers: {supported}."
                )
            if not image_edit_enabled:
                raise ValueError(
                    "Image edit is disabled for the configured image model in admin settings."
                )

            resolved_reference_ids = _resolve_reference_image_ids(
                db=db,
                chat_id=chat_id,
                chat_history=chat_history,
                explicit_image_ids=reference_image_ids,
            )
            reference_images = _load_reference_images(db, str(user_id), resolved_reference_ids)
            if not reference_images:
                raise ValueError(
                    "No reference images were found in this chat conversation. "
                    "Upload or generate images first, then retry image edit."
                )

        if provider_type == "openai":
            gen_result = _generate_via_openai(
                provider,
                model_name,
                prompt,
                settings,
                use_image_edit=should_use_image_edit,
                reference_images=reference_images,
            )
        elif provider_type in {"openai_responses", "openai_chat_completions"}:
            gen_result = _generate_via_openai_responses(
                provider,
                model_name,
                prompt,
                settings,
                use_image_edit=should_use_image_edit,
                reference_images=reference_images,
            )
        elif provider_type == "google_aistudio":
            gen_result = _generate_via_google_aistudio(
                provider,
                model_name,
                prompt,
                settings,
                use_image_edit=should_use_image_edit,
                reference_images=reference_images,
            )
        elif provider_type == "xai":
            gen_result = _generate_via_xai(
                provider,
                model_name,
                prompt,
                settings,
                use_image_edit=should_use_image_edit,
                reference_images=reference_images,
            )
        else:
            generator = _PROVIDER_GENERATORS.get(provider_type)
            if not generator:
                raise ValueError(f"Unsupported image generation provider type: {provider_type}")
            gen_result = generator(provider, model_name, prompt, settings)

        # Generators may return bytes (legacy) or a dict with image_bytes + cost info
        cost_info = None
        # Most existing providers return PNG bytes. Native xAI Imagine may
        # return PNG, JPEG, WebP, or GIF, so honor only a small allowlisted
        # format declaration from normalized provider adapters.
        generated_file_type = "image/png"
        generated_extension = ".png"
        supported_generated_formats = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        if isinstance(gen_result, dict):
            image_bytes = gen_result.get("image_bytes", b"")
            cost = gen_result.get("cost", 0.0)
            cost_details = gen_result.get("cost_details", {})
            result_file_type = str(gen_result.get("file_type") or "").strip().lower()
            result_extension = str(gen_result.get("extension") or "").strip().lower()
            expected_extension = supported_generated_formats.get(result_file_type)
            if expected_extension:
                generated_file_type = result_file_type
                generated_extension = (
                    result_extension
                    if result_extension == expected_extension
                    or (
                        result_file_type == "image/jpeg"
                        and result_extension == ".jpeg"
                    )
                    else expected_extension
                )
            if cost or cost_details:
                cost_info = {"cost": cost, **cost_details}
                cost_info["operation"] = "image_edit" if should_use_image_edit else "image_generation"
        else:
            image_bytes = gen_result

        if not image_bytes:
            raise RuntimeError("Image generation returned empty data")

        original_name = Path(str(filename).strip()).name if filename else "generated_image"
        if not original_name.lower().endswith(generated_extension):
            original_name = f"{Path(original_name).stem}{generated_extension}"

        file_type = generated_file_type
        file_category = get_file_category(file_type)
        stored_file_id = str(uuid.uuid4())
        stored_file_name = f"{stored_file_id}{generated_extension}"
        file_size = len(image_bytes)

        meta = {
            "original_filename": original_name,
            "origin": "assistant",
            "image_generation": True,
            "model": model_name,
            "provider_id": provider_id,
            "operation": "image_edit" if should_use_image_edit else "image_generation",
            "used_reference_images": bool(should_use_image_edit),
        }
        if should_use_image_edit:
            meta["reference_image_count"] = len(reference_images)

        file_record = persist_generated_file_bytes(
            db,
            user_id=str(user_id),
            original_filename=original_name,
            file_bytes=image_bytes,
            file_type=file_type,
            file_category=file_category,
            meta=meta,
            file_id=stored_file_id,
            file_name=stored_file_name,
            quota_reservation_id=(
                quota_reservation.reservation_id if quota_reservation else None
            ),
        )

        return {
            "file_id": file_record.id,
            "cost_info": cost_info,
        }
    finally:
        release_user_file_quota_reservation(
            db,
            quota_reservation.reservation_id if quota_reservation else None,
        )
        db.close()
