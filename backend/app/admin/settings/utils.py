import asyncio
import ipaddress
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.admin.concurrency.models import get_peak_concurrent_users_last_week
from app.ip_analytics.schemas import ISO_3166_1_ALPHA_2_COUNTRY_CODES
from app.admin.settings import models as settings_models
from app.admin.settings.models import (
    count_active_models,
    count_llm_providers,
    get_active_model,
    get_llm_provider,
    list_active_models,
    list_llm_providers,
    persist_settings_json_row,
)
from app.admin.settings.schema_categories.audio_generation import (
    AudioGenerationSettings,
    audio_generation_schema,
    build_audio_generation_model_field,
)
from app.admin.settings.schema_categories.code_execution import (
    CodeExecutionSettings,
    code_execution_schema,
)
from app.admin.settings.schema_categories.deep_research import (
    DeepResearchSettings,
    deep_research_schema,
)
from app.admin.settings.schema_categories.dictation import (
    DictationSettings,
    build_file_transcription_model_field,
    build_live_transcription_model_field,
    dictation_schema,
)
from app.admin.settings.schema_categories.general import (
    GeneralSettings,
    general_schema,
)
from app.admin.settings.schema_categories.groups_defaults import (
    GroupsDefaultsSettings,
    groups_defaults_schema,
)
from app.admin.settings.schema_categories.image_generation import (
    ImageGenerationSettings,
    build_image_generation_model_field,
    image_generation_schema,
)
from app.admin.settings.schema_categories.login_customization import (
    LoginCustomizationSettings,
    login_customization_schema,
)
from app.admin.settings.schema_categories.login_enterprise_sso import (
    LoginEnterpriseSSOSettings,
    login_enterprise_sso_schema,
)
from app.admin.settings.schema_categories.login_general import (
    LoginGeneralSettings,
    login_general_schema,
)
from app.admin.settings.schema_categories.login_ldap import (
    LoginLDAPSettings,
    login_ldap_schema,
)
from app.admin.settings.schema_categories.login_social import (
    LoginSocialSettings,
    login_social_schema,
)
from app.admin.settings.schema_categories.models import (
    ModelDefaultsSettings,
    models_schema,
)
from app.admin.settings.schema_categories.music_generation import (
    MusicGenerationSettings,
    build_music_generation_model_field,
    music_generation_schema,
)
from app.admin.settings.schema_categories.notifications import (
    NotificationSettings,
    notification_settings_schema,
)
from app.admin.settings.schema_categories.read_aloud import (
    ReadAloudSettings,
    build_read_aloud_model_field,
    read_aloud_schema,
)
from app.admin.settings.schema_categories.realtime import (
    RealtimeSettings,
    build_realtime_model_field,
    realtime_schema,
)
from app.admin.settings.schema_categories.security import (
    SecuritySettings,
    security_schema,
)
from app.admin.settings.schema_categories.slide_presentation import (
    SlidePresentationSettings,
    slide_presentation_schema,
)
from app.admin.settings.schema_categories.users import (
    UsersSettings,
    users_schema,
)
from app.admin.settings.schema_categories.video_generation import (
    VideoGenerationSettings,
    build_video_generation_model_field,
    video_generation_schema,
)
from app.admin.settings.schema_categories.weather_tool import (
    WeatherToolSettings,
    weather_tool_schema,
)
from app.auth.ldap_transport import (
    LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL,
    get_ldap_transport_security_policy,
)
from app.auth.password_policy import normalize_stored_login_general_settings
from app.chats.read_aloud_constants import READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
from app.groups.models import list_all_groups
from app.llm.google_aistudio.realtime import (
    get_google_aistudio_live_default_voice,
    get_google_aistudio_live_models,
)
from app.llm.models import (
    LLMProvider,
    get_llm_provider_status_summary,
    get_models_elevated_errors_summary,
)
from app.llm.openai.model_list import OPENAI_LIVE_TRANSCRIPTION_MODELS
from app.llm.openai.realtime import get_openai_realtime_models
from app.llm.schemas import ProviderEnum
from app.llm.speech import (
    OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES,
    TRANSCRIPTION_PROVIDER_TYPES,
    TTS_PROVIDER_TYPES,
    get_provider_display_label,
    get_transcription_runtime_for_provider,
    get_tts_model_capabilities_for_provider,
    get_tts_model_ids_for_provider,
)
from app.logging.models import (
    create_admin_notification,
    create_audit_log,
    get_admin_notifications,
)
from app.middleware.ip_restriction import get_country_by_ip
from app.network.policy import validate_and_normalize_public_webhook_url
from app.settings.models import (
    SENSITIVE_SETTING_RESPONSE_MASK,
    encrypt_sensitive_setting_value,
    ensure_sensitive_settings_page_encrypted,
    get_settings_page,
    get_settings_page_data,
    mask_sensitive_settings_page_data,
    preserve_masked_sensitive_settings_page_data,
)
from app.settings.utils import invalidate_settings_cache, sanitize_pinned_model_ids
from app.tools.image_generation.size_options import (
    ASSISTANT_SIZE_SELECTION_KEY,
    assistant_size_selection_enabled,
    filter_supported_tool_sizes,
    get_assistant_size_selection_kind,
    get_supported_tool_size_values,
)
from app.tools.utils import list_available_tool_options
from app.tools.websearch.models import (
    list_websearch_providers_scrape,
    list_websearch_providers_search,
    list_websearch_providers_with_types,
)
from app.users.models import get_active_user_count, get_pending_user_count
from app.utils.helpers import _mask_api_key_preview, _mask_secret_preview
from app.utils.ip_restrictions import (
    ip_restrictions_disabled_by_environment,
)
from app.utils.schemas import Option
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

flag_modified = settings_models.flag_modified


def raise_admin_provider_helper_error(
    *,
    exc: Exception,
    user_message: str,
    action: str,
    provider_id: str,
    provider_type: str | None = None,
    model_name: str | None = None,
) -> None:
    """Log provider failures privately and expose only a correlation ID."""

    correlation_id = str(uuid.uuid4())
    logger.error(
        "[Admin] %s failed correlation_id=%s action=%s provider_id=%s provider_type=%s model_name=%s error_type=%s",
        user_message,
        correlation_id,
        action,
        provider_id,
        provider_type,
        model_name,
        type(exc).__name__,
    )
    raise HTTPException(
        status_code=500,
        detail=f"{user_message}. Correlation ID: {correlation_id}.",
    ) from exc


IP_RESTRICTION_POLICY_KEYS = {
    "enable_ip_restrictions",
    "enable_ip_address_restrictions",
    "ip_address_restriction_mode",
    "only_allow_specific_ip",
    "allow_specific_ip",
    "block_specific_ip",
    "enable_ip_country_restrictions",
    "ip_country_restriction_mode",
    "only_allow_ip_from_specific_countries",
    "allow_country_ip",
    "block_country_ip",
    "allow_ip_if_no_country_found",
}

IP_LOCATION_PROVIDER_LABELS = {
    "ipinfo": "IP Info",
    "ipstack": "IPStack",
    "db-ip-free": "DB-IP (Free)",
}


def _normalize_exact_ip_for_admin_policy(value: Any) -> str | None:
    """Return a canonical visitor IP string for policy comparisons."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.lower() == "localhost":
        return "127.0.0.1"
    if "%" in raw_value:
        return None
    try:
        return ipaddress.ip_address(raw_value).compressed
    except ValueError:
        return None


def _normalize_exact_ip_list_for_admin_policy(values: Any) -> set[str]:
    """Normalize a scalar or list of exact IP settings into comparable strings."""
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized: set[str] = set()
    for value in values:
        normalized_ip = _normalize_exact_ip_for_admin_policy(value)
        if normalized_ip:
            normalized.add(normalized_ip)
    return normalized


def _bool_setting(value: Any) -> bool:
    """Coerce persisted settings values with the same truthy forms used elsewhere."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _sync_security_ip_policy_compatibility_flags(
    normalized_payload: Dict[str, Any],
    merged_values: Dict[str, Any],
) -> None:
    """Keep legacy allow-only booleans aligned with the explicit policy modes."""
    if {
        "enable_ip_address_restrictions",
        "ip_address_restriction_mode",
    }.intersection(normalized_payload):
        exact_enabled = _bool_setting(
            merged_values.get("enable_ip_address_restrictions")
        )
        exact_mode = (
            str(merged_values.get("ip_address_restriction_mode") or "").strip().lower()
        )
        normalized_payload["only_allow_specific_ip"] = bool(
            exact_enabled and exact_mode == "allowlist"
        )
        merged_values["only_allow_specific_ip"] = normalized_payload[
            "only_allow_specific_ip"
        ]

    if {
        "enable_ip_country_restrictions",
        "ip_country_restriction_mode",
    }.intersection(normalized_payload):
        country_enabled = _bool_setting(
            merged_values.get("enable_ip_country_restrictions")
        )
        country_mode = (
            str(merged_values.get("ip_country_restriction_mode") or "").strip().lower()
        )
        normalized_payload["only_allow_ip_from_specific_countries"] = bool(
            country_enabled and country_mode == "allowlist"
        )
        merged_values["only_allow_ip_from_specific_countries"] = normalized_payload[
            "only_allow_ip_from_specific_countries"
        ]


def _normalize_country_list_for_admin_policy(values: Any) -> set[str]:
    """Normalize country policy settings into uppercase ISO-style codes."""
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return {
        str(value or "").strip().upper() for value in values if str(value or "").strip()
    }


def _resolve_admin_country_for_policy(
    admin_ip: str,
    db: Session,
    *,
    provider: str,
    token: str | None,
) -> str:
    """Resolve the admin country with the effective, possibly not-yet-saved configuration."""
    country = asyncio.run(
        get_country_by_ip(
            admin_ip,
            db,
            provider_override=provider,
            token_override=token,
        )
    )
    return str(country or "Unknown").strip().upper() or "Unknown"


def _effective_ip_location_api_key(
    provider: str,
    api_key_updates: Dict[str, Any],
    db: Session,
) -> str:
    """Return the saved or incoming API key without exposing it in an error response."""
    saved_api_keys = _get_api_key_settings(db)
    saved_value = saved_api_keys.get(provider)

    # A masked preview means the admin kept the existing secret unchanged.
    # Otherwise, use the submitted value so a bulk save can be validated
    # correctly before the settings transaction commits.
    if provider in api_key_updates:
        incoming_value = api_key_updates.get(provider)
        if _is_masked_preview(incoming_value, saved_value):
            effective_value = saved_value
        else:
            effective_value = incoming_value
    else:
        effective_value = saved_value

    return str(effective_value or "").strip()


def _ip_country_policy_error_detail(
    code: str,
    message: str,
    *,
    provider: str | None = None,
) -> Dict[str, str]:
    """Build a stable, translatable admin API error without including secrets."""
    detail = {"code": code, "message": message}
    if provider:
        detail["provider"] = provider
    return detail


def _security_country_code_error_detail(
    exc: ValidationError,
    merged_values: Dict[str, Any],
) -> Dict[str, str] | None:
    """Return a structured error for an invalid country-policy list entry."""
    country_fields = {"allow_country_ip", "block_country_ip"}
    has_country_error = any(
        error.get("loc") and error["loc"][0] in country_fields for error in exc.errors()
    )
    if not has_country_error:
        return None

    # The country validator runs on the complete list, so inspect the submitted
    # list to identify the exact token that caused validation to fail.
    for field_name in country_fields:
        raw_values = merged_values.get(field_name)
        if not isinstance(raw_values, list):
            continue
        for item in raw_values:
            raw_code = str(item or "").strip()
            if raw_code.upper() in ISO_3166_1_ALPHA_2_COUNTRY_CODES:
                continue
            display_code = (raw_code or "(empty)")[:64]
            return {
                "code": "ip_country_code_invalid",
                "message": (
                    f"Invalid country code: {display_code}. "
                    "Use a two-letter ISO 3166-1 code such as DE or US."
                ),
                "country_code": display_code,
            }

    return None


def _security_ip_address_error_detail(
    exc: ValidationError,
    merged_values: Dict[str, Any],
) -> Dict[str, str] | None:
    """Return a structured error for an invalid exact-IP policy list entry."""
    exact_ip_fields = {"allow_specific_ip", "block_specific_ip"}
    has_exact_ip_error = any(
        error.get("loc") and error["loc"][0] in exact_ip_fields
        for error in exc.errors()
    )
    if not has_exact_ip_error:
        return None

    for field_name in exact_ip_fields:
        raw_values = merged_values.get(field_name)
        if not isinstance(raw_values, list):
            continue
        for item in raw_values:
            raw_ip = str(item or "").strip()
            is_valid = False
            if raw_ip.lower() == "localhost":
                is_valid = True
            elif raw_ip and "%" not in raw_ip:
                try:
                    ipaddress.ip_address(raw_ip)
                    is_valid = True
                except ValueError:
                    pass
            if is_valid:
                continue

            display_ip = (raw_ip or "(empty)")[:128]
            return {
                "code": "ip_address_invalid",
                "message": (
                    f"Invalid IP address: {display_ip}. "
                    "Use a valid IPv4 or IPv6 address, such as "
                    "203.0.113.10 or 2001:db8::1."
                ),
                "ip_address": display_ip,
            }

    return None


def _assert_security_ip_policy_keeps_admin_access(
    merged_values: Dict[str, Any],
    *,
    request_client_ip: str | None,
    db: Session,
    api_key_updates: Dict[str, Any] | None = None,
) -> None:
    """Reject IP policy changes that would lock out the current admin."""
    if ip_restrictions_disabled_by_environment():
        return
    if not _bool_setting(merged_values.get("enable_ip_restrictions")):
        return

    admin_ip = _normalize_exact_ip_for_admin_policy(request_client_ip)
    if not admin_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot update IP restrictions because Omlorix could not determine your current admin IP address. "
                "Fix trusted proxy configuration or use OMLORIX_DISABLE_IP_RESTRICTIONS=true as a temporary emergency bypass."
            ),
        )

    explicit_exact_enabled = merged_values.get("enable_ip_address_restrictions")
    exact_rules_enabled = (
        _bool_setting(explicit_exact_enabled)
        if explicit_exact_enabled is not None
        else bool(
            _bool_setting(merged_values.get("only_allow_specific_ip"))
            or merged_values.get("allow_specific_ip")
            or merged_values.get("block_specific_ip")
        )
    )
    exact_mode = (
        str(merged_values.get("ip_address_restriction_mode") or "").strip().lower()
    )
    if exact_mode not in {"allowlist", "blocklist"}:
        exact_mode = (
            "allowlist"
            if _bool_setting(merged_values.get("only_allow_specific_ip"))
            and merged_values.get("allow_specific_ip")
            else "blocklist"
        )

    blocked_ips = _normalize_exact_ip_list_for_admin_policy(
        merged_values.get("block_specific_ip")
    )
    if exact_rules_enabled and exact_mode == "blocklist" and admin_ip in blocked_ips:
        raise HTTPException(
            status_code=409,
            detail="Cannot save IP restrictions because the block list contains your current admin IP address.",
        )

    allowed_ips = _normalize_exact_ip_list_for_admin_policy(
        merged_values.get("allow_specific_ip")
    )
    exact_allow_enabled = exact_rules_enabled and exact_mode == "allowlist"
    if exact_allow_enabled and allowed_ips and admin_ip not in allowed_ips:
        raise HTTPException(
            status_code=409,
            detail="Cannot save IP restrictions because your current admin IP address is not in the allow list.",
        )

    allowed_countries = _normalize_country_list_for_admin_policy(
        merged_values.get("allow_country_ip")
    )
    blocked_countries = _normalize_country_list_for_admin_policy(
        merged_values.get("block_country_ip")
    )
    explicit_country_enabled = merged_values.get("enable_ip_country_restrictions")
    country_rules_enabled = (
        _bool_setting(explicit_country_enabled)
        if explicit_country_enabled is not None
        else bool(
            _bool_setting(merged_values.get("only_allow_ip_from_specific_countries"))
            or merged_values.get("allow_country_ip")
            or merged_values.get("block_country_ip")
        )
    )
    country_mode = (
        str(merged_values.get("ip_country_restriction_mode") or "").strip().lower()
    )
    if country_mode not in {"allowlist", "blocklist"}:
        country_mode = (
            "allowlist"
            if _bool_setting(merged_values.get("only_allow_ip_from_specific_countries"))
            and merged_values.get("allow_country_ip")
            else "blocklist"
        )
    country_allow_enabled = (
        country_rules_enabled
        and country_mode == "allowlist"
        and bool(allowed_countries)
    )
    country_block_enabled = (
        country_rules_enabled
        and country_mode == "blocklist"
        and bool(blocked_countries)
    )
    if not country_allow_enabled and not country_block_enabled:
        return

    # Report incomplete provider configuration separately from lookup
    # failures. The former is directly actionable and was previously hidden
    # behind the generic "unknown country" lockout message.
    provider = (
        str(merged_values.get("check_ip_location_provider") or "").strip().lower()
    )
    provider_label = IP_LOCATION_PROVIDER_LABELS.get(provider)
    if not provider_label:
        raise HTTPException(
            status_code=409,
            detail=_ip_country_policy_error_detail(
                "ip_country_provider_not_configured",
                (
                    "Cannot save country-based IP restrictions because no IP location provider is configured. "
                    "Select an IP location provider first."
                ),
            ),
        )

    api_key_updates = api_key_updates or {}
    provider_token: str | None = None
    if provider in SECURITY_API_KEY_FIELDS:
        provider_token = _effective_ip_location_api_key(provider, api_key_updates, db)
        if not provider_token:
            raise HTTPException(
                status_code=409,
                detail=_ip_country_policy_error_detail(
                    "ip_country_provider_api_key_missing",
                    (
                        f"Cannot save country-based IP restrictions because {provider_label} is selected, "
                        f"but its API key is not configured. Enter the {provider_label} API key first."
                    ),
                    provider=provider_label,
                ),
            )

    admin_country = _resolve_admin_country_for_policy(
        admin_ip,
        db,
        provider=provider,
        token=provider_token,
    )
    if admin_country == "UNKNOWN":
        if _bool_setting(merged_values.get("allow_ip_if_no_country_found")):
            return
        raise HTTPException(
            status_code=409,
            detail=_ip_country_policy_error_detail(
                "ip_country_lookup_failed",
                (
                    f"Omlorix could not resolve your current admin IP country using {provider_label}. "
                    "Verify the provider configuration, API key, network access, and trusted proxy settings, "
                    "or allow IPs without a country match."
                ),
                provider=provider_label,
            ),
        )

    if country_allow_enabled and admin_country not in allowed_countries:
        raise HTTPException(
            status_code=409,
            detail="Cannot save IP restrictions because your current admin IP country is not in the allow list.",
        )

    if country_block_enabled and admin_country in blocked_countries:
        raise HTTPException(
            status_code=409,
            detail="Cannot save IP restrictions because the country block list contains your current admin IP country.",
        )


VIDEO_GENERATION_PROVIDER_TYPES = {
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
    ProviderEnum.google_aistudio.value,
    ProviderEnum.openrouter.value,
    ProviderEnum.xai.value,
}
IMAGE_GENERATION_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
    ProviderEnum.openrouter.value,
    ProviderEnum.google_aistudio.value,
    ProviderEnum.ollama.value,
    ProviderEnum.xai.value,
}

DEEP_RESEARCH_PROVIDER_TYPES = {
    ProviderEnum.google_aistudio.value,
}
CUSTOM_DEEP_RESEARCH_MODEL_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
    ProviderEnum.microsoft_azure.value,
    ProviderEnum.google_aistudio.value,
    ProviderEnum.openrouter.value,
    ProviderEnum.anthropic.value,
    ProviderEnum.anthropic_base.value,
    ProviderEnum.ollama.value,
    ProviderEnum.lmstudio.value,
    ProviderEnum.xai.value,
}

AUDIO_GENERATION_PROVIDER_TYPES = set(TTS_PROVIDER_TYPES)
MUSIC_GENERATION_PROVIDER_TYPES = {
    ProviderEnum.google_aistudio.value,
}

LOGIN_CUSTOMIZATION_DEFAULTS = {
    "branding_title": "Welcome back",
    "branding_subtitle": "Sign in to continue to your account",
}

VIDEO_GENERATION_MODELS_BY_PROVIDER: dict[str, list[str]] = {
    ProviderEnum.openai_responses.value: [],
    ProviderEnum.openai_chat_completions.value: [],
    ProviderEnum.google_aistudio.value: [
        "veo-3.1-generate-preview",
        "veo-3.1-fast-generate-preview",
        "veo-3.1-lite-generate-preview",
        "veo-3.0-generate-001",
        "veo-3.0-fast-generate-001",
        "veo-2.0-generate-001",
    ],
    ProviderEnum.openrouter.value: [],
    ProviderEnum.xai.value: ["grok-imagine-video", "grok-imagine-video-1.5"],
}

SECURITY_API_KEY_FIELDS = ("ipinfo", "ipstack")
SECURITY_API_KEY_PLACEHOLDERS = {
    "ipinfo": "Enter your IP Info API key",
    "ipstack": "Enter your IPStack API key",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce a value to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _get_api_key_settings(db: Session) -> Dict[str, Any]:
    """Get API key settings from database."""
    return get_settings_page_data(db, "api_keys")


def _is_masked_preview(value: Any, actual_value: Any) -> bool:
    """Check if a value is a masked preview."""
    if not isinstance(value, str) or not isinstance(actual_value, str):
        return False
    if not value.endswith("..."):
        return False
    preview_prefix = value[:-3]
    if not preview_prefix:
        return False
    return actual_value.startswith(preview_prefix)


def _normalize_secret_fields(
    page_key: str,
    normalized_payload: Dict[str, Any],
    secret_fields: tuple[str, ...],
    db: Session,
) -> None:
    """Normalize secret fields in schema."""
    if not any(field_key in normalized_payload for field_key in secret_fields):
        return

    page_settings = get_settings_page_data(db, page_key)

    for field_key in secret_fields:
        if field_key not in normalized_payload:
            continue

        existing_secret = page_settings.get(field_key)
        incoming_secret = normalized_payload.get(field_key)

        if _is_masked_preview(incoming_secret, existing_secret):
            normalized_payload.pop(field_key, None)
        elif incoming_secret is None:
            normalized_payload[field_key] = ""
        elif isinstance(incoming_secret, str):
            normalized_payload[field_key] = incoming_secret.strip()
        else:
            normalized_payload[field_key] = str(incoming_secret).strip()


def _sensitive_secret_placeholder(stored_secret: Any, fallback_placeholder: str) -> str:
    """Return a non-revealing placeholder for a saved secret field."""
    if isinstance(stored_secret, str) and stored_secret.strip():
        return SENSITIVE_SETTING_RESPONSE_MASK
    return fallback_placeholder


def _validate_login_social_apple_private_key(
    normalized_payload: Dict[str, Any], db: Session
) -> None:
    """Validate Apple private key input whenever Apple OAuth login can become active."""
    if not {
        "apple_private_key",
        "enable_apple_login",
    }.intersection(normalized_payload):
        return

    from app.auth.social import (
        APPLE_PRIVATE_KEY_ERROR_DETAIL,
        validate_apple_private_key,
    )

    existing_settings = get_settings_page_data(db, "login_social")
    merged_settings = {**existing_settings, **normalized_payload}
    raw_private_key = merged_settings.get("apple_private_key")

    if "apple_private_key" in normalized_payload:
        raw_private_key_text = str(raw_private_key or "")
        if raw_private_key_text.strip():
            normalized_key = validate_apple_private_key(raw_private_key)
            normalized_payload["apple_private_key"] = normalized_key
            merged_settings["apple_private_key"] = normalized_key

    apple_login_active = _coerce_bool(merged_settings.get("enable_apple_login"))

    if (
        apple_login_active
        and not str(merged_settings.get("apple_private_key") or "").strip()
    ):
        raise HTTPException(status_code=400, detail=APPLE_PRIVATE_KEY_ERROR_DETAIL)

    if apple_login_active:
        validate_apple_private_key(merged_settings.get("apple_private_key"))


def _get_openai_provider_options(db: Session) -> list[dict[str, str]]:
    """Get OpenAI provider options."""
    openai_provider_values = {
        ProviderEnum.openai.value,
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
    }
    rows = list_llm_providers(db, provider_types=openai_provider_values)
    options: list[dict[str, str]] = []
    for row in rows:
        label = (row.name or row.id or "").strip() or row.id
        options.append({"value": row.id, "label": label})
    return options


def _get_transcription_provider_rows(db: Session) -> list[LLMProvider]:
    """Get transcription provider rows."""
    return list_llm_providers(db, provider_types=TRANSCRIPTION_PROVIDER_TYPES)


def _get_transcription_provider_options(db: Session) -> list[dict[str, str]]:
    """Get transcription provider options."""
    rows = _get_transcription_provider_rows(db)
    options: list[dict[str, str]] = []
    for row in rows:
        base_label = (row.name or row.id or "").strip() or row.id
        options.append(
            {
                "value": row.id,
                "label": f"{base_label} ({get_provider_display_label(row.provider)})",
            }
        )
    return options


def _get_transcription_model_ids_for_provider(
    db: Session, provider_id: str | None
) -> list[str]:
    """Get transcription model IDs for a provider."""
    if not provider_id:
        return []
    try:
        runtime = get_transcription_runtime_for_provider(db, provider_id)
    except HTTPException:
        return []

    normalized: list[str] = []
    for model_id in runtime.get("models", []) or []:
        value = str(model_id or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _get_transcription_model_options(
    db: Session, provider_id: str | None
) -> list[dict[str, str]]:
    """Get transcription model options."""
    return [
        {"value": model_id, "label": model_id}
        for model_id in _get_transcription_model_ids_for_provider(db, provider_id)
    ]


def get_transcription_model_options_response(
    db: Session, provider_id: str | None
) -> dict[str, Any]:
    """Get transcription model options response."""
    normalized_provider_id = str(provider_id or "").strip() or None
    if normalized_provider_id:
        allowed_provider_ids = {
            option["value"]
            for option in _get_transcription_provider_options(db)
            if option.get("value")
        }
        if normalized_provider_id not in allowed_provider_ids:
            raise HTTPException(
                status_code=400,
                detail="Selected transcription provider is not available.",
            )

    return {
        "provider_id": normalized_provider_id,
        "options": _get_transcription_model_options(db, normalized_provider_id),
    }


LIVE_TRANSCRIPTION_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
    ProviderEnum.xai.value,
}


def _configure_live_transcription_fields_for_selection(
    schema: Any,
    db: Session,
    provider_id: str | None,
    model_id: str | None = None,
) -> None:
    """Append the selected provider's live-STT fragment after model choice."""

    if not provider_id or not model_id:
        return
    provider = get_llm_provider(db, provider_id)
    if not provider:
        return

    if provider.provider == ProviderEnum.xai.value:
        from app.llm.xai.transcription import (
            get_live_transcription_settings_schema,
        )
    elif provider.provider in {
        ProviderEnum.openai.value,
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
    }:
        from app.llm.openai.transcription import (
            get_live_transcription_settings_schema,
        )
    else:
        return

    _append_schema_fragment(schema, get_live_transcription_settings_schema())


def _get_live_transcription_provider_options(db: Session) -> list[dict[str, str]]:
    """Return only providers whose credentials can reach OpenAI Realtime."""
    rows = list_llm_providers(db, provider_types=LIVE_TRANSCRIPTION_PROVIDER_TYPES)
    return [
        {
            "value": row.id,
            "label": (
                f"{(row.name or row.id or '').strip() or row.id} "
                f"({'xAI' if row.provider == ProviderEnum.xai.value else 'OpenAI'})"
            ),
        }
        for row in rows
    ]


def _get_live_transcription_model_options(
    db: Session,
    provider_id: str | None,
) -> list[dict[str, str]]:
    """Return Omlorix's explicitly supported live transcription contract."""
    if not provider_id:
        return []
    provider = get_llm_provider(db, provider_id)
    if not provider or provider.provider not in LIVE_TRANSCRIPTION_PROVIDER_TYPES:
        return []
    if provider.provider == ProviderEnum.xai.value:
        from app.llm.xai.transcription import XAI_TRANSCRIPTION_MODELS

        models = XAI_TRANSCRIPTION_MODELS
    else:
        models = OPENAI_LIVE_TRANSCRIPTION_MODELS
    return [{"value": model_id, "label": model_id} for model_id in models]


def get_live_transcription_model_options_response(
    db: Session,
    provider_id: str | None,
) -> dict[str, Any]:
    """Build the admin model-picker response for streamed dictation."""
    normalized_provider_id = str(provider_id or "").strip() or None
    if normalized_provider_id:
        allowed_provider_ids = {
            option["value"]
            for option in _get_live_transcription_provider_options(db)
            if option.get("value")
        }
        if normalized_provider_id not in allowed_provider_ids:
            raise HTTPException(
                status_code=400,
                detail="Selected live transcription provider is not available.",
            )
    return {
        "provider_id": normalized_provider_id,
        "options": _get_live_transcription_model_options(db, normalized_provider_id),
    }


def _get_realtime_provider_rows(db: Session) -> list[LLMProvider]:
    """Get realtime provider rows."""
    provider_values = {
        ProviderEnum.openai.value,
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
        ProviderEnum.google_aistudio.value,
        ProviderEnum.xai.value,
    }
    return list_llm_providers(db, provider_types=provider_values)


def _get_realtime_provider_options(db: Session) -> list[dict[str, str]]:
    """Get realtime provider options."""
    rows = _get_realtime_provider_rows(db)
    options: list[dict[str, str]] = []
    for row in rows:
        provider_label = get_provider_display_label(row.provider)
        normalized_provider = str(row.provider or "").strip()
        if normalized_provider in {
            ProviderEnum.openai.value,
            ProviderEnum.openai_responses.value,
            ProviderEnum.openai_chat_completions.value,
        }:
            provider_label = "OpenAI"
        elif normalized_provider == ProviderEnum.google_aistudio.value:
            provider_label = "Google AI Studio"
        elif normalized_provider == ProviderEnum.xai.value:
            provider_label = "xAI"
        base_label = (row.name or row.id or "").strip() or row.id
        options.append({"value": row.id, "label": f"{base_label} ({provider_label})"})
    return options


def _get_realtime_model_ids_for_provider(
    db: Session, provider_id: str | None
) -> list[str]:
    """Get realtime model IDs for a provider."""
    if not provider_id:
        return []

    provider_row = get_llm_provider(db, provider_id)
    if not provider_row:
        return []

    provider_type = (provider_row.provider or "").strip()
    if provider_type in {
        ProviderEnum.openai.value,
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
    }:
        models = get_openai_realtime_models(
            db=db,
            openai_provider_id=provider_id,
            openai_provider_type=provider_type,
        )
    elif provider_type == ProviderEnum.google_aistudio.value:
        models = get_google_aistudio_live_models(
            db=db,
            google_provider_id=provider_id,
        )
    elif provider_type == ProviderEnum.xai.value:
        from app.llm.xai.realtime import get_xai_realtime_models

        models = get_xai_realtime_models(
            db=db,
            provider_id=provider_id,
        )
    else:
        return []

    normalized: list[str] = []
    for model_id in models or []:
        value = str(model_id or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _get_realtime_model_options(
    db: Session, provider_id: str | None
) -> list[dict[str, str]]:
    """Get realtime model options."""
    return [
        {"value": model_id, "label": model_id}
        for model_id in _get_realtime_model_ids_for_provider(db, provider_id)
    ]


def get_realtime_model_options_response(
    db: Session, provider_id: str | None
) -> dict[str, Any]:
    """Get realtime model options response."""
    normalized_provider_id = str(provider_id or "").strip() or None
    if normalized_provider_id:
        allowed_provider_ids = {
            option["value"]
            for option in _get_realtime_provider_options(db)
            if option.get("value")
        }
        if normalized_provider_id not in allowed_provider_ids:
            raise HTTPException(
                status_code=400,
                detail="Selected realtime provider is not available.",
            )

    return {
        "provider_id": normalized_provider_id,
        "options": _get_realtime_model_options(db, normalized_provider_id),
    }


def _get_video_generation_provider_rows(db: Session) -> list[LLMProvider]:
    """Get video generation provider rows."""
    return list_llm_providers(db, provider_types=VIDEO_GENERATION_PROVIDER_TYPES)


def _get_image_generation_provider_rows(db: Session) -> list[LLMProvider]:
    """Return configured providers with an image-generation integration."""

    return list_llm_providers(db, provider_types=IMAGE_GENERATION_PROVIDER_TYPES)


def _get_image_generation_provider_options(db: Session) -> list[dict[str, str]]:
    """Build stable image-provider options for the base settings step."""

    return [
        {
            "value": row.id,
            "label": (
                f"{(row.name or row.id or '').strip() or row.id} "
                f"({get_provider_display_label(row.provider)})"
            ),
        }
        for row in _get_image_generation_provider_rows(db)
    ]


def _get_image_generation_model_options(
    db: Session,
    provider_id: str | None,
) -> list[dict[str, Any]]:
    """Resolve the selected image provider's integration-owned model schema."""

    normalized_provider_id = str(provider_id or "").strip()
    if not normalized_provider_id:
        return []
    provider = get_llm_provider(db, normalized_provider_id)
    if not provider:
        return []

    provider_type = str(provider.provider or "").strip()
    try:
        if provider_type == ProviderEnum.openai.value:
            from app.llm.openai.image_generation import (
                get_image_generation_schema_part_1,
            )
        elif provider_type in {
            ProviderEnum.openai_responses.value,
            ProviderEnum.openai_chat_completions.value,
        }:
            from app.llm.openai_responses.image_generation import (
                get_image_generation_schema_part_1,
            )
        elif provider_type == ProviderEnum.openrouter.value:
            from app.llm.openrouter.image_generation import (
                get_image_generation_schema_part_1,
            )
        elif provider_type == ProviderEnum.google_aistudio.value:
            from app.llm.google_aistudio.image_generation import (
                get_image_generation_schema_part_1,
            )
        elif provider_type == ProviderEnum.xai.value:
            from app.llm.xai.image_generation import (
                get_image_generation_schema_part_1,
            )
        elif provider_type == ProviderEnum.ollama.value:
            from app.llm.ollama.image_generation import (
                get_image_generation_schema_part_1,
            )
        else:
            return []

        fragment = get_image_generation_schema_part_1(
            db,
            normalized_provider_id,
        )
    except Exception:
        logger.exception(
            "Failed to fetch image generation models for provider '%s' (%s)",
            normalized_provider_id,
            provider_type,
        )
        return []

    for section in fragment.sections or []:
        for field in section.fields or []:
            if field.key != "model_name":
                continue
            return [
                option.model_dump(exclude_none=True)
                for option in field.options or []
                if str(option.value or "").strip()
            ]
    return []


def _get_video_generation_provider_options(db: Session) -> list[dict[str, str]]:
    """Get video generation provider options."""
    rows = _get_video_generation_provider_rows(db)
    options: list[dict[str, str]] = []
    for row in rows:
        base_label = (row.name or row.id or "").strip() or row.id
        options.append(
            {
                "value": row.id,
                "label": f"{base_label} ({get_provider_display_label(row.provider)})",
            }
        )
    return options


def _get_video_generation_provider_type_by_id(db: Session) -> dict[str, str]:
    """Get video generation provider type by ID."""
    mapping: dict[str, str] = {}
    for row in _get_video_generation_provider_rows(db):
        if isinstance(row.id, str) and isinstance(row.provider, str):
            mapping[row.id] = row.provider
    return mapping


def _get_video_generation_models_for_provider(provider_row: LLMProvider) -> list[str]:
    """Get video generation models for a provider."""
    provider_type = (provider_row.provider or "").strip()
    fallback_models = VIDEO_GENERATION_MODELS_BY_PROVIDER.get(provider_type, [])

    try:
        if provider_type in {
            ProviderEnum.openai_responses.value,
            ProviderEnum.openai_chat_completions.value,
        }:
            from app.llm.openai_responses.video_generation import (
                openai_compatible_video_generation_models_list,
            )

            models = openai_compatible_video_generation_models_list(provider_row)
            dynamic_ids = [
                str(item.get("id") or "").strip()
                for item in (models or [])
                if isinstance(item, dict)
            ]
            return [model for model in dynamic_ids if model]

        if provider_type == ProviderEnum.google_aistudio.value:
            from app.llm.google_aistudio.video_generation import (
                getGoogleAistudioVideoGenerationModels,
            )

            api_version = "v1alpha"
            if isinstance(provider_row.settings, dict):
                configured_version = str(
                    provider_row.settings.get("api_version") or ""
                ).strip()
                if configured_version:
                    api_version = configured_version

            models = getGoogleAistudioVideoGenerationModels(
                api_key=provider_row.api_key,
                api_version=api_version,
            )
            dynamic_ids = [
                str(item.get("id") or "").strip()
                for item in (models or [])
                if isinstance(item, dict)
            ]
            return [model for model in dynamic_ids if model]

        if provider_type == ProviderEnum.openrouter.value:
            from app.llm.openrouter.video_generation import (
                openrouter_video_generation_models_list,
            )

            models = openrouter_video_generation_models_list(provider_row)
            dynamic_ids = [
                str(item.get("id") or "").strip()
                for item in (models or [])
                if isinstance(item, dict)
            ]
            return [model for model in dynamic_ids if model]

        if provider_type == ProviderEnum.xai.value:
            from app.llm.xai.video_generation import list_video_models

            return [
                model_id
                for item in list_video_models(provider_row)
                if isinstance(item, dict)
                and (model_id := str(item.get("id") or "").strip())
            ]
    except Exception:
        logger.exception(
            "Failed to fetch dynamic video generation models for provider '%s' (%s)",
            provider_row.id,
            provider_type,
        )

    return list(fallback_models)


def _get_video_generation_model_options(
    db: Session, provider_id: str | None = None
) -> list[dict[str, str]]:
    """Get video generation model options."""
    if provider_id:
        provider_row = get_llm_provider(db, provider_id)
        if not provider_row:
            return []
        model_ids = _get_video_generation_models_for_provider(provider_row)
        return [{"value": model_name, "label": model_name} for model_name in model_ids]

    merged: list[dict[str, str]] = []
    for row in _get_video_generation_provider_rows(db):
        for model_name in _get_video_generation_models_for_provider(row):
            merged.append(
                {
                    "value": model_name,
                    "label": f"{model_name} ({get_provider_display_label(row.provider)})",
                }
            )
    return merged


def _get_music_generation_provider_rows(db: Session) -> list[LLMProvider]:
    """Get music generation provider rows."""
    return list_llm_providers(db, provider_types=MUSIC_GENERATION_PROVIDER_TYPES)


def _get_music_generation_provider_options(db: Session) -> list[dict[str, str]]:
    """Get music generation provider options."""
    rows = _get_music_generation_provider_rows(db)
    options: list[dict[str, str]] = []
    for row in rows:
        base_label = (row.name or row.id or "").strip() or row.id
        options.append(
            {
                "value": row.id,
                "label": f"{base_label} ({get_provider_display_label(row.provider)})",
            }
        )
    return options


def _get_music_generation_model_options(
    db: Session, provider_id: str | None = None
) -> list[dict[str, str]]:
    """Get music generation model options."""
    normalized_provider_id = str(provider_id or "").strip()
    if not normalized_provider_id:
        return []

    provider_row = get_llm_provider(db, normalized_provider_id)
    if not provider_row:
        return []
    if str(provider_row.provider or "").strip() not in MUSIC_GENERATION_PROVIDER_TYPES:
        return []

    try:
        from app.llm.google_aistudio.music_generation import (
            get_google_aistudio_music_generation_models,
        )

        return [
            {
                "value": str(item.get("id") or "").strip(),
                "label": str(item.get("name") or item.get("id") or "").strip(),
            }
            for item in get_google_aistudio_music_generation_models(provider_row)
            if str(item.get("id") or "").strip()
        ]
    except Exception:
        logger.exception(
            "Failed to fetch music generation models for provider '%s' (%s)",
            provider_row.id,
            provider_row.provider,
        )
        return []


def _get_music_generation_model_capabilities(
    model_name: str,
    *,
    provider_type: str | None,
) -> dict[str, Any]:
    """Get music generation model capabilities."""
    normalized_provider_type = str(provider_type or "").strip()
    if normalized_provider_type != ProviderEnum.google_aistudio.value:
        return {
            "response_formats": ["mp3"],
            "supports_reference_images": False,
            "max_reference_images": 1,
        }

    from app.llm.google_aistudio.music_generation import (
        get_google_aistudio_music_model_capabilities,
    )

    return get_google_aistudio_music_model_capabilities(model_name)


def _get_audio_generation_provider_rows(db: Session) -> list[LLMProvider]:
    """Get audio generation provider rows."""
    return list_llm_providers(db, provider_types=AUDIO_GENERATION_PROVIDER_TYPES)


def _get_audio_generation_provider_options(db: Session) -> list[dict[str, str]]:
    """Get audio generation provider options."""
    rows = _get_audio_generation_provider_rows(db)
    options: list[dict[str, str]] = []
    for row in rows:
        base_label = (row.name or row.id or "").strip() or row.id
        options.append(
            {
                "value": row.id,
                "label": f"{base_label} ({get_provider_display_label(row.provider)})",
            }
        )
    return options


def _get_read_aloud_provider_options(db: Session) -> list[dict[str, str]]:
    """Get read aloud provider options."""
    return [
        {"value": READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID, "label": "Browser native"},
        *_get_audio_generation_provider_options(db),
    ]


def _get_audio_generation_models_for_provider(provider_row: LLMProvider) -> list[str]:
    """Get audio generation models for a provider."""
    try:
        return get_tts_model_ids_for_provider(provider_row)
    except Exception:
        logger.exception(
            "Failed to fetch audio generation models for provider '%s' (%s)",
            provider_row.id,
            provider_row.provider,
        )
    return []


def _get_audio_generation_model_options(
    db: Session, provider_id: str | None = None
) -> list[dict[str, str]]:
    """Get audio generation model options."""
    if provider_id:
        provider_row = get_llm_provider(db, provider_id)
        if not provider_row:
            return []
        model_ids = _get_audio_generation_models_for_provider(provider_row)
        return [{"value": model_name, "label": model_name} for model_name in model_ids]

    merged: list[dict[str, str]] = []
    for row in _get_audio_generation_provider_rows(db):
        for model_name in _get_audio_generation_models_for_provider(row):
            merged.append(
                {
                    "value": model_name,
                    "label": f"{model_name} ({get_provider_display_label(row.provider)})",
                }
            )
    return merged


def _get_audio_generation_model_capabilities(
    model_name: str, provider_type: str | None
) -> dict[str, Any]:
    """Get audio generation model capabilities."""
    return get_tts_model_capabilities_for_provider(
        model_name, provider_type=provider_type, provider_row=None
    )


def _get_audio_generation_model_capabilities_for_provider(
    model_name: str,
    provider_type: str | None,
    provider_row: LLMProvider | None,
) -> dict[str, Any]:
    """Get audio generation model capabilities for a provider."""
    try:
        return get_tts_model_capabilities_for_provider(
            model_name,
            provider_type=provider_type,
            provider_row=provider_row,
        )
    except Exception:
        logger.exception(
            "Failed to resolve audio generation model capabilities for '%s' (%s)",
            model_name,
            provider_type,
        )
    return {
        "voices": [],
        "response_formats": [],
        "voice_required": False,
        "support_custom_instructions": True,
    }


def _get_deep_research_provider_rows(db: Session) -> list[LLMProvider]:
    """Get deep research provider rows."""
    return list_llm_providers(db, provider_types=DEEP_RESEARCH_PROVIDER_TYPES)


def _format_deep_research_provider_label(provider_type: str) -> str:
    """Format deep research provider label."""
    normalized = str(provider_type or "").strip()
    return get_provider_display_label(normalized)


def _get_deep_research_provider_options(db: Session) -> list[dict[str, str]]:
    """Get deep research provider options."""
    options: list[dict[str, str]] = []
    for row in _get_deep_research_provider_rows(db):
        base_label = (row.name or row.id or "").strip() or row.id
        options.append(
            {
                "value": row.id,
                "label": f"{base_label} ({_format_deep_research_provider_label(row.provider)})",
            }
        )
    return options


def _get_audio_generation_models_for_provider(provider_row: LLMProvider) -> list[str]:
    """Get audio generation models for a provider."""
    try:
        return get_tts_model_ids_for_provider(provider_row)
    except Exception:
        logger.exception(
            "Failed to fetch audio generation models for provider '%s' (%s)",
            provider_row.id,
            provider_row.provider,
        )
    return []


def _get_audio_generation_model_options(
    db: Session, provider_id: str | None = None
) -> list[dict[str, str]]:
    """Get audio generation model options."""
    if provider_id:
        provider_row = get_llm_provider(db, provider_id)
        if not provider_row:
            return []
        model_ids = _get_audio_generation_models_for_provider(provider_row)
        return [{"value": model_name, "label": model_name} for model_name in model_ids]

    merged: list[dict[str, str]] = []
    for row in _get_audio_generation_provider_rows(db):
        for model_name in _get_audio_generation_models_for_provider(row):
            merged.append(
                {
                    "value": model_name,
                    "label": f"{model_name} ({get_provider_display_label(row.provider)})",
                }
            )
    return merged


def _get_deep_research_models_for_provider(provider_row: LLMProvider) -> list[str]:
    """Get deep research models for a provider."""
    provider_type = str(provider_row.provider or "").strip()
    if provider_type == ProviderEnum.google_aistudio.value:
        from app.llm.google_aistudio.model_list import (
            GOOGLE_AISTUDIO_DEEP_RESEARCH_MODELS,
        )

        return [
            str(model).strip()
            for model in GOOGLE_AISTUDIO_DEEP_RESEARCH_MODELS
            if str(model).strip()
        ]

    return []


def _get_deep_research_model_options(
    db: Session, provider_id: str | None = None
) -> list[dict[str, str]]:
    """Get deep research model options."""
    if provider_id:
        provider_row = get_llm_provider(db, provider_id)
        if not provider_row:
            return []
        return [
            {"value": model_name, "label": model_name}
            for model_name in _get_deep_research_models_for_provider(provider_row)
        ]

    merged: list[dict[str, str]] = []
    for row in _get_deep_research_provider_rows(db):
        provider_label = _format_deep_research_provider_label(row.provider)
        for model_name in _get_deep_research_models_for_provider(row):
            merged.append(
                {"value": model_name, "label": f"{model_name} ({provider_label})"}
            )
    return merged


def _get_custom_deep_research_model_options(db: Session) -> list[dict[str, str]]:
    """Get active models from providers supported by custom research."""
    rows = list_active_models(
        db,
        provider_types=CUSTOM_DEEP_RESEARCH_MODEL_PROVIDER_TYPES,
    )
    options: list[dict[str, Any]] = []
    for row in rows:
        label = (row.name or row.model_name or row.id or "").strip() or row.id
        provider_label = get_provider_display_label(row.provider)
        options.append({"value": row.id, "label": f"{label} ({provider_label})"})
    return options


def _get_websearch_provider_options_with_metadata(
    db: Session, provider_type: str
) -> list[dict[str, Any]]:
    """Get websearch provider options including combined-capability metadata."""
    metadata_by_id = {
        str(item.get("id") or ""): item
        for item in list_websearch_providers_with_types(db)
        if str(item.get("id") or "").strip()
    }
    base_options = _get_websearch_provider_options(db, provider_type)
    options: list[dict[str, Any]] = []
    for option in base_options:
        option_value = str(option.get("value") or "").strip()
        metadata = metadata_by_id.get(option_value) or {}
        options.append(
            {
                **option,
                "metadata": {
                    "has_combined": bool(metadata.get("has_combined")),
                    "has_scrape": bool(metadata.get("has_scrape")),
                    "has_search": bool(metadata.get("has_search")),
                    "types": metadata.get("types") or [],
                },
            }
        )
    return options


def _set_schema_field_placeholder(
    schema: Any, field_key: str, placeholder: Optional[str]
) -> bool:
    """Set placeholder for a schema field."""
    if not schema or not getattr(schema, "sections", None):
        return False
    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.placeholder = placeholder
                if isinstance(placeholder, str) and placeholder.endswith("..."):
                    field.i18n_placeholder = None
                if hasattr(field, "value"):
                    field.value = None
                return True
    return False


def _set_schema_field_options(
    schema: Any, field_key: str, options: list[dict[str, Any] | Option]
) -> bool:
    """Set typed options for a schema field."""
    if not schema or not getattr(schema, "sections", None):
        return False
    normalized_options = [
        option if isinstance(option, Option) else Option.model_validate(option)
        for option in (options or [])
    ]
    updated = False
    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.options = normalized_options
                updated = True
    return updated


def _set_schema_field_value(schema: Any, field_key: str, value: Any) -> bool:
    """Set value for a schema field."""
    if not schema or not getattr(schema, "sections", None):
        return False
    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.value = value
                return True
    return False


def _get_schema_field(schema: Any, field_key: str) -> Any | None:
    """Return one field from a composed schema by its stable key."""

    if not schema or not getattr(schema, "sections", None):
        return None
    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                return field
    return None


def _configure_realtime_fields_for_selection(
    schema: Any,
    db: Session,
    provider_id: str | None,
    model_id: str | None,
    tool_options: list[dict[str, Any]] | None = None,
) -> None:
    """Append only the selected realtime integration's supported controls."""

    if not provider_id or not model_id:
        return
    provider = get_llm_provider(db, provider_id)
    if not provider:
        return

    openai_provider_types = {
        ProviderEnum.openai.value,
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
    }
    if provider.provider in openai_provider_types:
        from app.llm.openai.realtime import get_realtime_settings_schema

        fragment = get_realtime_settings_schema(tool_options=tool_options)
    elif provider.provider == ProviderEnum.google_aistudio.value:
        from app.llm.google_aistudio.realtime import get_realtime_settings_schema

        fragment = get_realtime_settings_schema(
            model_name=model_id,
            tool_options=tool_options,
        )
    elif provider.provider == ProviderEnum.xai.value:
        from app.llm.xai.realtime import get_realtime_settings_schema

        fragment = get_realtime_settings_schema(
            db=db,
            provider_id=provider_id,
            tool_options=tool_options,
        )
    else:
        return

    _append_schema_fragment(schema, fragment)


def _append_schema_fragment(schema: Any, fragment: Any) -> bool:
    """Append a provider-owned schema fragment without sharing mutable state."""

    if not schema or not fragment or not getattr(fragment, "sections", None):
        return False
    schema.sections.extend(
        section.model_copy(deep=True) for section in fragment.sections or []
    )
    return True


def _insert_schema_field_after(
    schema: Any,
    *,
    after_key: str,
    field: Any,
) -> bool:
    """Insert a dynamic wizard step immediately after its parent field."""

    if not schema or not getattr(schema, "sections", None):
        return False
    for section in schema.sections or []:
        fields = list(getattr(section, "fields", []) or [])
        for index, candidate in enumerate(fields):
            if getattr(candidate, "key", None) != after_key:
                continue
            section.fields = [*fields[: index + 1], field, *fields[index + 1 :]]
            return True
    return False


def _populate_schema_values(schema: Any, values: dict[str, Any] | None) -> None:
    """Populate fields added after the initial base-schema value pass."""

    if not schema or not values:
        return
    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) in values:
                field.value = values[field.key]


def _get_read_aloud_settings_fragment(
    provider: LLMProvider,
    model_name: str,
) -> Any:
    """Adapt a provider-owned TTS schema to the read-aloud settings keys.

    Audio generation and read aloud intentionally share each provider's voice
    and response-format capabilities.  Reusing the integration-owned fragment
    prevents the two admin surfaces from drifting while the renamed keys keep
    their persisted settings independent.
    """

    provider_type = str(provider.provider or "").strip()
    if provider_type in OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES:
        from app.llm.openai.text_to_speech import get_audio_generation_schema_part_2

        source = get_audio_generation_schema_part_2(model_name, provider=provider)
    elif provider_type == ProviderEnum.openrouter.value:
        from app.llm.openrouter.audio_generation import (
            get_audio_generation_schema_part_2,
        )

        source = get_audio_generation_schema_part_2(model_name, provider=provider)
    elif provider_type == ProviderEnum.google_aistudio.value:
        from app.llm.google_aistudio.text_to_speech import (
            get_audio_generation_schema_part_2,
        )

        source = get_audio_generation_schema_part_2(model_name)
    elif provider_type == ProviderEnum.xai.value:
        from app.llm.xai.text_to_speech import get_audio_generation_schema_part_2

        source = get_audio_generation_schema_part_2(model_name, provider=provider)
    elif provider_type == ProviderEnum.elevenlabs.value:
        from app.llm.elevenlabs.text_to_speech import (
            get_audio_generation_schema_part_2,
        )

        source = get_audio_generation_schema_part_2(
            api_key=provider.api_key,
            model_name=model_name,
        )
    else:
        return None

    adapted_fields = []
    for section in source.sections or []:
        for source_field in section.fields or []:
            if source_field.key not in {"voice", "response_format"}:
                continue
            field = source_field.model_copy(deep=True)
            if source_field.key == "voice":
                field.key = "read_aloud_voice"
                field.label = "Read aloud voice"
                field.description = (
                    "Select the default voice used for assistant read aloud."
                )
                field.i18n_label = "schema_backend_read_aloud_voice"
                field.i18n_description = (
                    "schema_backend_select_the_default_voice_used_for_assistant_read_aloud"
                )
            else:
                field.key = "read_aloud_response_format"
                field.label = "Read aloud audio format"
                field.description = (
                    "Choose the audio format stored for cached read aloud playback."
                )
                field.i18n_label = "schema_backend_read_aloud_audio_format"
                field.i18n_description = (
                    "schema_backend_choose_the_audio_format_stored_for_cached_read_aloud_playback"
                )
            adapted_fields.append(field)

    if not adapted_fields:
        return None

    from app.utils.schemas import Section, Sections

    return Sections(
        sections=[
            Section(
                title="Read aloud",
                description="Choose how assistant messages are spoken aloud in chat.",
                fields=adapted_fields,
            )
        ]
    )


def _remove_schema_fields(schema: Any, field_keys: set[str]) -> bool:
    """Remove fields from schema."""
    if not schema or not getattr(schema, "sections", None):
        return False
    removed_any = False
    for section in schema.sections or []:
        current_fields = list(getattr(section, "fields", []) or [])
        next_fields = [
            field
            for field in current_fields
            if getattr(field, "key", None) not in field_keys
        ]
        if len(next_fields) != len(current_fields):
            section.fields = next_fields
            removed_any = True
    return removed_any


def _get_group_options(db: Session) -> list[dict[str, Any]]:
    """Get group options."""
    groups = list_all_groups(db) or []
    default_option: dict[str, Any] | None = None
    other_options: list[dict[str, Any]] = []

    for group in groups:
        group_id = getattr(group, "id", None)
        if not isinstance(group_id, str):
            continue
        label = getattr(group, "name", None) or group_id
        option = {"value": group_id, "label": str(label)}
        if group_id == "default":
            default_option = option
        else:
            other_options.append(option)

    other_options.sort(key=lambda opt: opt["label"].lower())

    if default_option:
        return [default_option, *other_options]
    return other_options


def _update_api_key_settings(db: Session, updates: Dict[str, Any]) -> List[str]:
    """Update API key settings."""
    if not updates:
        return []

    api_keys_record = get_settings_page(db, "api_keys")
    if not api_keys_record:
        raise HTTPException(status_code=404, detail="API keys settings page not found")
    if not isinstance(api_keys_record.data, dict):
        api_keys_record.data = {}

    current_api_key_values = _get_api_key_settings(db)

    changed_keys: List[str] = []
    for key, value in updates.items():
        normalized_value = value if isinstance(value, str) else (value or "")
        normalized_value = normalized_value or ""

        stored_value = current_api_key_values.get(key)
        if _is_masked_preview(normalized_value, stored_value):
            continue
        if stored_value == normalized_value:
            continue

        api_keys_record.data[key] = encrypt_sensitive_setting_value(
            "api_keys",
            key,
            normalized_value,
            treat_value_as_plaintext=False,
        )
        changed_keys.append(key)

    if not changed_keys:
        return []

    api_keys_record.updated_at = datetime.now(timezone.utc)
    persist_settings_json_row(db, api_keys_record, mark_modified=flag_modified)
    invalidate_settings_cache()
    return changed_keys


# -------------------
# Get Admin Dashboard Data
# -------------------
def get_admin_settings_dashboard_data(db, db_log):
    """Get admin settings dashboard data."""
    active_user_count = get_active_user_count(db)
    pending_user_count = get_pending_user_count(db)
    concurrency_metrics = get_peak_concurrent_users_last_week(db)
    providers_total_count = count_llm_providers(db)
    models_total_count = count_active_models(db)
    try:
        providers_available, providers_down_count = get_llm_provider_status_summary(db)
    except ValueError as exc:
        providers_available, providers_down_count = False, 0
        logger.error(
            "Failed to summarize LLM provider status due to decryption error: %s",
            exc,
        )
        details = {
            "error": str(exc),
            "action": "llm_provider_key_decryption",
        }
        try:
            create_admin_notification(
                db,
                "llm_provider_decryption_error",
                "Failed to decrypt one or more LLM provider API keys. Please review provider credentials.",
                details=details,
                notification_type="error",
            )
        except Exception:
            logger.exception(
                "Unable to record admin notification for LLM provider decryption failure"
            )
        try:
            create_audit_log(
                db_log=db_log,
                user_id="system",
                action="LLM_PROVIDER_DECRYPTION_FAILED",
                details=details,
                category="system",
            )
        except Exception:
            logger.exception(
                "Unable to record audit log for LLM provider decryption failure"
            )
    notifications = get_admin_notifications(db)
    notifications = [
        {
            "category": notification.category,
            "message": notification.message,
            "timestamp": notification.timestamp.isoformat()
            if notification.timestamp
            else None,
        }
        for notification in notifications
    ]
    internet_connectivity = True
    status_settings = get_settings_page(db, "status")
    if status_settings and isinstance(status_settings.data, dict):
        internet_connectivity = bool(
            status_settings.data.get("internet_connectivity", True)
        )
    connectivity_check_enabled = True
    general_settings = get_settings_page(db, "general")
    if general_settings and isinstance(general_settings.data, dict):
        connectivity_check_enabled = bool(
            general_settings.data.get("internet_connectivity_check_enabled", True)
        )

    # Get models with elevated errors
    try:
        models_healthy, models_error_count = get_models_elevated_errors_summary(db)
    except Exception as exc:
        models_healthy, models_error_count = True, 0
        logger.error("Failed to get models elevated errors summary: %s", exc)

    return {
        "active_user_count": active_user_count,
        "pending_user_count": pending_user_count,
        "max_concurrent_users_last_week": concurrency_metrics[
            "max_concurrent_users_last_week"
        ],
        "max_concurrent_users_is_partial": concurrency_metrics["is_partial_window"],
        "providers_available": providers_available,
        "providers_down_count": providers_down_count,
        "providers_total_count": providers_total_count,
        "notifications": notifications,
        "internet_connectivity": internet_connectivity,
        "internet_connectivity_check_enabled": connectivity_check_enabled,
        "models_healthy": models_healthy,
        "models_error_count": models_error_count,
        "models_total_count": models_total_count,
    }


# -------------------
# Get Admin Settings Schema Response
# -------------------
def _normalize_stored_deep_research_settings(
    stored_values: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a valid v2 Deep Research settings snapshot for read/merge paths.

    Settings rows can temporarily contain invalid values after a greenfield
    schema change or an interrupted development update. A malformed stored
    value must not make the settings schema endpoint unavailable. Explicit
    update payloads are still validated normally and therefore cannot use this
    helper to bypass the public settings contract.
    """

    normalized = dict(stored_values)
    defaults = DeepResearchSettings().model_dump()

    execution_mode = str(normalized.get("execution_mode") or "").strip()
    if execution_mode not in {"custom", "native"}:
        normalized["execution_mode"] = defaults["execution_mode"]

    # Discard removed keys from older settings documents so schema responses
    # and subsequent saves remain canonical.
    normalized.pop("quality_profile", None)
    normalized.pop("html_model_id", None)

    raw_revision_rounds = normalized.get("max_revision_rounds")
    try:
        revision_rounds = int(raw_revision_rounds)
    except (TypeError, ValueError):
        revision_rounds = defaults["max_revision_rounds"]
    if isinstance(raw_revision_rounds, bool) or not 1 <= revision_rounds <= 3:
        revision_rounds = defaults["max_revision_rounds"]
    normalized["max_revision_rounds"] = revision_rounds

    return normalized


def _normalize_stored_login_enterprise_sso_settings(
    stored_values: Dict[str, Any],
) -> Dict[str, Any]:
    """Upgrade legacy federation documents before strict read/merge validation.

    Legacy SAML used one entity ID for both sides of the trust; copy that value
    into the new IdP field to preserve the previous runtime behavior until an
    administrator supplies the IdP's canonical identifier. Activation flags
    deliberately remain untouched: they represent administrator intent, while
    each SSO runtime separately checks whether its configuration is usable.
    """

    normalized = dict(stored_values)

    raw_advanced = normalized.get("saml_advanced_settings")
    saml_advanced = dict(raw_advanced) if isinstance(raw_advanced, dict) else {}
    if not saml_advanced.get("idp_entity_id") and normalized.get("saml_entity_id"):
        saml_advanced["idp_entity_id"] = normalized["saml_entity_id"]
    normalized["saml_advanced_settings"] = saml_advanced

    return normalized


def get_admin_settings_schema_response(
    page: str, include_values: bool, db: Session
) -> Dict[str, Any]:
    """Get admin settings schema response."""
    page_map = {
        "general": (general_schema, GeneralSettings),
        "groups_defaults": (groups_defaults_schema, GroupsDefaultsSettings),
        "login_general": (login_general_schema, LoginGeneralSettings),
        "login_customization": (login_customization_schema, LoginCustomizationSettings),
        "login_social": (login_social_schema, LoginSocialSettings),
        "login_enterprise_sso": (
            login_enterprise_sso_schema,
            LoginEnterpriseSSOSettings,
        ),
        "login_ldap": (login_ldap_schema, LoginLDAPSettings),
        "security": (security_schema, SecuritySettings),
        "users": (users_schema, UsersSettings),
        "notifications": (notification_settings_schema, NotificationSettings),
        "models": (models_schema, ModelDefaultsSettings),
        "dictation": (dictation_schema, DictationSettings),
        "read_aloud": (read_aloud_schema, ReadAloudSettings),
        "realtime": (realtime_schema, RealtimeSettings),
        "slide_presentation": (slide_presentation_schema, SlidePresentationSettings),
        "weather_tool": (weather_tool_schema, WeatherToolSettings),
        "code_execution": (code_execution_schema, CodeExecutionSettings),
        "image_generation": (image_generation_schema, ImageGenerationSettings),
        "audio_generation": (audio_generation_schema, AudioGenerationSettings),
        "music_generation": (music_generation_schema, MusicGenerationSettings),
        "video_generation": (video_generation_schema, VideoGenerationSettings),
        "deep_research": (deep_research_schema, DeepResearchSettings),
    }

    schema_config = page_map.get(page)
    if schema_config is None:
        raise HTTPException(status_code=404, detail="Not Found")

    schema, model = schema_config
    storage_page = "login_general" if page == "groups_defaults" else page

    schema_copy = schema.model_copy(deep=True)
    response_payload_values = None

    if include_values:
        # Use decrypted page data so model validation and ordinary editable
        # values work normally. Sensitive values are masked immediately after
        # validation and before the response schema is populated.
        stored_values = get_settings_page_data(db, storage_page)
        allowed_fields = model.model_fields.keys()
        filtered_values = {
            key: stored_values.get(key)
            for key in allowed_fields
            if key in stored_values
        }
        if page == "login_general":
            # Persisted development-era values may predate the mandatory
            # password floor. Clamp only storage reads so the settings page
            # remains usable while explicit unsafe updates are still rejected.
            filtered_values = normalize_stored_login_general_settings(filtered_values)
        if page == "login_enterprise_sso":
            filtered_values = _normalize_stored_login_enterprise_sso_settings(
                filtered_values
            )
        if page == "deep_research":
            filtered_values = _normalize_stored_deep_research_settings(filtered_values)
        values = model(**filtered_values).model_dump(exclude_none=True)
        # Use the canonical sensitive-key registry for both scalar and nested
        # secrets. This avoids page-specific cleanup gaps such as enterprise
        # SSO configuration lists containing client_secret values.
        values = mask_sensitive_settings_page_data(storage_page, values)

        for section in schema_copy.sections:
            for field in section.fields:
                field.value = values.get(field.key)

        response_payload_values = values

    if page == "security":
        api_key_values = _get_api_key_settings(db)
        for api_key_field in SECURITY_API_KEY_FIELDS:
            stored_placeholder = _mask_api_key_preview(
                api_key_values.get(api_key_field)
            )
            placeholder_value = stored_placeholder or SECURITY_API_KEY_PLACEHOLDERS.get(
                api_key_field
            )
            if placeholder_value:
                _set_schema_field_placeholder(
                    schema_copy, api_key_field, placeholder_value
                )
            if include_values and response_payload_values is not None:
                response_payload_values.pop(api_key_field, None)

    if page in {"models", "dictation", "read_aloud", "realtime"}:
        # These pages share provider/model capability resolvers, but each now
        # receives only its own schema and validated value model. Schema field
        # mutations targeting another page are intentional no-ops.
        model_options = _get_public_model_options(db) if page == "models" else []
        _set_schema_field_options(schema_copy, "default_model", model_options)
        _set_schema_field_options(schema_copy, "default_pinned_models", model_options)
        transcription_provider_options = (
            _get_transcription_provider_options(db) if page == "dictation" else []
        )
        _set_schema_field_options(
            schema_copy, "transcription_provider_id", transcription_provider_options
        )
        live_transcription_provider_options = (
            _get_live_transcription_provider_options(db) if page == "dictation" else []
        )
        _set_schema_field_options(
            schema_copy,
            "live_transcription_provider_id",
            live_transcription_provider_options,
        )
        read_aloud_provider_options = (
            _get_read_aloud_provider_options(db) if page == "read_aloud" else []
        )
        _set_schema_field_options(
            schema_copy, "read_aloud_provider_id", read_aloud_provider_options
        )
        realtime_provider_options = (
            _get_realtime_provider_options(db) if page == "realtime" else []
        )
        _set_schema_field_options(
            schema_copy, "realtime_provider_id", realtime_provider_options
        )
        # Reuse the canonical tool registry so built-in and enabled custom
        # Python tools appear in the same accessible multi-select.
        realtime_tool_options = (
            [
                {
                    "value": option["name"],
                    "label": option.get("label") or option["name"],
                    "i18n_label": option.get("i18n_label"),
                }
                for option in list_available_tool_options(db)
                if option.get("name")
            ]
            if page == "realtime"
            else []
        )
        _set_schema_field_options(schema_copy, "realtime_tools", realtime_tool_options)
        selected_transcription_provider_id = None
        selected_live_transcription_provider_id = None
        selected_read_aloud_provider_id = READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
        selected_read_aloud_model = None
        selected_realtime_provider_id = None
        if include_values and response_payload_values is not None:
            current_value = response_payload_values.get("default_model")
            allowed_values = {option["value"] for option in model_options}
            if current_value and current_value not in allowed_values:
                response_payload_values.pop("default_model", None)
                _set_schema_field_value(schema_copy, "default_model", None)
            current_pinned_models = sanitize_pinned_model_ids(
                response_payload_values.get("default_pinned_models")
            )
            filtered_pinned_models = [
                model_id
                for model_id in current_pinned_models
                if model_id in allowed_values
            ]
            response_payload_values["default_pinned_models"] = filtered_pinned_models
            _set_schema_field_value(
                schema_copy, "default_pinned_models", filtered_pinned_models
            )
            transcription_provider_value = response_payload_values.get(
                "transcription_provider_id"
            )
            provider_allowed_values = {
                option["value"] for option in transcription_provider_options
            }
            if (
                transcription_provider_value
                and transcription_provider_value not in provider_allowed_values
            ):
                response_payload_values.pop("transcription_provider_id", None)
                _set_schema_field_value(schema_copy, "transcription_provider_id", None)
            selected_transcription_provider_id = response_payload_values.get(
                "transcription_provider_id"
            )
            live_transcription_provider_value = response_payload_values.get(
                "live_transcription_provider_id"
            )
            live_transcription_allowed_provider_values = {
                option["value"] for option in live_transcription_provider_options
            }
            if (
                live_transcription_provider_value
                and live_transcription_provider_value
                not in live_transcription_allowed_provider_values
            ):
                response_payload_values.pop("live_transcription_provider_id", None)
                _set_schema_field_value(
                    schema_copy,
                    "live_transcription_provider_id",
                    None,
                )
            selected_live_transcription_provider_id = response_payload_values.get(
                "live_transcription_provider_id"
            )
            read_aloud_provider_value = response_payload_values.get(
                "read_aloud_provider_id"
            )
            read_aloud_allowed_values = {
                option["value"] for option in read_aloud_provider_options
            }
            if (
                read_aloud_provider_value
                and read_aloud_provider_value not in read_aloud_allowed_values
            ):
                response_payload_values["read_aloud_provider_id"] = (
                    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                )
                _set_schema_field_value(
                    schema_copy,
                    "read_aloud_provider_id",
                    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID,
                )
            selected_read_aloud_provider_id = (
                response_payload_values.get("read_aloud_provider_id")
                or READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
            )
            realtime_provider_value = response_payload_values.get(
                "realtime_provider_id"
            )
            realtime_allowed_values = {
                option["value"] for option in realtime_provider_options
            }
            if (
                realtime_provider_value
                and realtime_provider_value not in realtime_allowed_values
            ):
                response_payload_values.pop("realtime_provider_id", None)
                _set_schema_field_value(schema_copy, "realtime_provider_id", None)
            selected_realtime_provider_id = response_payload_values.get(
                "realtime_provider_id"
            )
            allowed_realtime_tools = {
                option["value"] for option in realtime_tool_options
            }
            selected_realtime_tools = [
                tool_name
                for tool_name in response_payload_values.get("realtime_tools", [])
                if tool_name in allowed_realtime_tools
            ]
            response_payload_values["realtime_tools"] = selected_realtime_tools
            _set_schema_field_value(
                schema_copy, "realtime_tools", selected_realtime_tools
            )

        # Provider/model pickers are real wizard steps rather than permanently
        # empty controls.  Add each model field only after its provider has
        # survived availability validation.
        if page == "dictation" and selected_transcription_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="transcription_provider_id",
                field=build_file_transcription_model_field(
                    selected_transcription_provider_id
                ),
            )
        if page == "dictation" and selected_live_transcription_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="live_transcription_provider_id",
                field=build_live_transcription_model_field(
                    selected_live_transcription_provider_id
                ),
            )
        if (
            page == "read_aloud"
            and selected_read_aloud_provider_id
            != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
        ):
            _insert_schema_field_after(
                schema_copy,
                after_key="read_aloud_provider_id",
                field=build_read_aloud_model_field(selected_read_aloud_provider_id),
            )
        if page == "realtime" and selected_realtime_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="realtime_provider_id",
                field=build_realtime_model_field(selected_realtime_provider_id),
            )

        transcription_model_options = (
            _get_transcription_model_options(
                db,
                selected_transcription_provider_id,
            )
            if page == "dictation"
            else []
        )
        _set_schema_field_options(
            schema_copy, "transcription_model", transcription_model_options
        )
        live_transcription_model_options = (
            _get_live_transcription_model_options(
                db,
                selected_live_transcription_provider_id,
            )
            if page == "dictation"
            else []
        )
        _set_schema_field_options(
            schema_copy,
            "live_transcription_model",
            live_transcription_model_options,
        )
        read_aloud_model_options = []
        if (
            page == "read_aloud"
            and selected_read_aloud_provider_id != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
        ):
            read_aloud_model_options = _get_audio_generation_model_options(
                db,
                selected_read_aloud_provider_id,
            )
        _set_schema_field_options(
            schema_copy, "read_aloud_model", read_aloud_model_options
        )
        realtime_model_options = (
            _get_realtime_model_options(
                db,
                selected_realtime_provider_id,
            )
            if page == "realtime"
            else []
        )
        _set_schema_field_options(schema_copy, "realtime_model", realtime_model_options)
        if include_values and response_payload_values is not None:
            transcription_model_value = response_payload_values.get(
                "transcription_model"
            )
            allowed_transcription_models = {
                option.get("value")
                for option in transcription_model_options
                if option.get("value")
            }
            if (
                transcription_model_value
                and transcription_model_value not in allowed_transcription_models
            ):
                response_payload_values.pop("transcription_model", None)
                _set_schema_field_value(schema_copy, "transcription_model", None)
            live_transcription_model_value = response_payload_values.get(
                "live_transcription_model"
            )
            allowed_live_transcription_models = {
                option.get("value")
                for option in live_transcription_model_options
                if option.get("value")
            }
            if (
                live_transcription_model_value
                and live_transcription_model_value
                not in allowed_live_transcription_models
            ):
                response_payload_values.pop("live_transcription_model", None)
                _set_schema_field_value(
                    schema_copy,
                    "live_transcription_model",
                    None,
                )
            read_aloud_model_value = response_payload_values.get("read_aloud_model")
            allowed_read_aloud_models = {
                option.get("value")
                for option in read_aloud_model_options
                if option.get("value")
            }
            if (
                read_aloud_model_value
                and selected_read_aloud_provider_id
                != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                and read_aloud_model_value not in allowed_read_aloud_models
            ):
                response_payload_values.pop("read_aloud_model", None)
                response_payload_values.pop("read_aloud_voice", None)
                response_payload_values.pop("read_aloud_response_format", None)
                _set_schema_field_value(schema_copy, "read_aloud_model", None)
            selected_read_aloud_model = response_payload_values.get("read_aloud_model")
            realtime_model_value = response_payload_values.get("realtime_model")
            allowed_realtime_models = {
                option.get("value")
                for option in realtime_model_options
                if option.get("value")
            }
            if (
                realtime_model_value
                and realtime_model_value not in allowed_realtime_models
            ):
                response_payload_values.pop("realtime_model", None)
                _set_schema_field_value(schema_copy, "realtime_model", None)

        selected_realtime_model = None
        selected_live_transcription_model = None
        if include_values and response_payload_values is not None:
            selected_realtime_model = response_payload_values.get("realtime_model")
            selected_live_transcription_model = response_payload_values.get(
                "live_transcription_model"
            )

        if page == "dictation":
            _configure_live_transcription_fields_for_selection(
                schema_copy,
                db,
                selected_live_transcription_provider_id,
                selected_live_transcription_model,
            )

        if (
            page == "read_aloud"
            and selected_read_aloud_provider_id
            != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
            and selected_read_aloud_model
        ):
            read_aloud_provider = get_llm_provider(
                db,
                selected_read_aloud_provider_id,
            )
            if read_aloud_provider:
                _append_schema_fragment(
                    schema_copy,
                    _get_read_aloud_settings_fragment(
                        read_aloud_provider,
                        selected_read_aloud_model,
                    ),
                )

        if page == "realtime":
            _configure_realtime_fields_for_selection(
                schema_copy,
                db,
                selected_realtime_provider_id,
                selected_realtime_model,
                realtime_tool_options,
            )
            if (
                include_values
                and response_payload_values is not None
                and selected_realtime_provider_id
                and selected_realtime_model
            ):
                voice_field = _get_schema_field(schema_copy, "realtime_voice")
                allowed_voices = {
                    option.value
                    for option in (getattr(voice_field, "options", None) or [])
                    if str(option.value or "").strip()
                }
                current_voice = str(
                    response_payload_values.get("realtime_voice") or ""
                ).strip()
                if allowed_voices and current_voice not in allowed_voices:
                    provider = get_llm_provider(
                        db,
                        selected_realtime_provider_id,
                    )
                    if provider and provider.provider == ProviderEnum.xai.value:
                        from app.llm.xai.realtime import normalize_xai_realtime_voice

                        normalized_xai_voice = normalize_xai_realtime_voice(
                            current_voice
                        )
                        effective_voice = (
                            normalized_xai_voice
                            if normalized_xai_voice in allowed_voices
                            else sorted(allowed_voices)[0]
                        )
                    elif (
                        provider
                        and provider.provider == ProviderEnum.google_aistudio.value
                    ):
                        effective_voice = get_google_aistudio_live_default_voice(
                            current_voice
                        )
                    else:
                        effective_voice = (
                            "alloy"
                            if "alloy" in allowed_voices
                            else sorted(allowed_voices)[0]
                        )
                    response_payload_values["realtime_voice"] = effective_voice
                    _set_schema_field_value(
                        schema_copy,
                        "realtime_voice",
                        effective_voice,
                    )
        if (
            page == "read_aloud"
            and selected_read_aloud_provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
        ):
            if include_values and response_payload_values is not None:
                response_payload_values["read_aloud_provider_id"] = (
                    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                )
                response_payload_values.pop("read_aloud_model", None)
                response_payload_values.pop("read_aloud_voice", None)
                response_payload_values.pop("read_aloud_response_format", None)
                _set_schema_field_value(
                    schema_copy,
                    "read_aloud_provider_id",
                    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID,
                )
            _remove_schema_fields(
                schema_copy,
                {"read_aloud_model", "read_aloud_voice", "read_aloud_response_format"},
            )
        elif page == "read_aloud" and not selected_read_aloud_model:
            if include_values and response_payload_values is not None:
                response_payload_values.pop("read_aloud_voice", None)
                response_payload_values.pop("read_aloud_response_format", None)
            _remove_schema_fields(
                schema_copy,
                {"read_aloud_voice", "read_aloud_response_format"},
            )
        elif page == "read_aloud":
            provider_row = get_llm_provider(db, selected_read_aloud_provider_id)
            provider_type = (
                str(provider_row.provider or "").strip() if provider_row else None
            )
            capabilities = _get_audio_generation_model_capabilities_for_provider(
                selected_read_aloud_model,
                provider_type=provider_type,
                provider_row=provider_row,
            )
            read_aloud_voice_options = [
                {"value": str(voice), "label": str(voice)}
                for voice in capabilities.get("voices", [])
                if str(voice).strip()
            ]
            read_aloud_format_options = [
                {"value": str(fmt).strip().lower(), "label": str(fmt).strip().upper()}
                for fmt in capabilities.get("response_formats", [])
                if str(fmt).strip()
            ]
            _set_schema_field_options(
                schema_copy, "read_aloud_voice", read_aloud_voice_options
            )
            _set_schema_field_options(
                schema_copy,
                "read_aloud_response_format",
                read_aloud_format_options,
            )
            supports_custom_voice = bool(capabilities.get("supports_custom_voice"))
            if include_values and response_payload_values is not None:
                voice_value = response_payload_values.get("read_aloud_voice")
                if voice_value and not read_aloud_voice_options:
                    read_aloud_voice_options = [
                        {"value": str(voice_value), "label": str(voice_value)}
                    ]
                    _set_schema_field_options(
                        schema_copy, "read_aloud_voice", read_aloud_voice_options
                    )
                elif (
                    voice_value
                    and supports_custom_voice
                    and all(
                        option.get("value") != voice_value
                        for option in read_aloud_voice_options
                    )
                ):
                    # Preserve the selected team-owned voice long enough for
                    # the searchable picker to resolve its full metadata.
                    read_aloud_voice_options.append(
                        {"value": str(voice_value), "label": str(voice_value)}
                    )
                    _set_schema_field_options(
                        schema_copy,
                        "read_aloud_voice",
                        read_aloud_voice_options,
                    )
                allowed_voices = {
                    option.get("value")
                    for option in read_aloud_voice_options
                    if option.get("value")
                }
                if (
                    voice_value
                    and allowed_voices
                    and voice_value not in allowed_voices
                    and not supports_custom_voice
                ):
                    response_payload_values.pop("read_aloud_voice", None)
                    _set_schema_field_value(schema_copy, "read_aloud_voice", None)
                format_value = response_payload_values.get("read_aloud_response_format")
                allowed_formats = {
                    option.get("value")
                    for option in read_aloud_format_options
                    if option.get("value")
                }
                if (
                    format_value
                    and allowed_formats
                    and format_value not in allowed_formats
                ):
                    response_payload_values.pop("read_aloud_response_format", None)
                    _set_schema_field_value(
                        schema_copy, "read_aloud_response_format", None
                    )

    if page == "image_generation":
        provider_options = _get_image_generation_provider_options(db)
        selected_provider_id = None
        if include_values and response_payload_values is not None:
            candidate_provider_id = response_payload_values.get("provider_id")
            allowed_provider_ids = {
                option["value"] for option in provider_options if option.get("value")
            }
            if candidate_provider_id in allowed_provider_ids:
                selected_provider_id = candidate_provider_id
            elif candidate_provider_id:
                response_payload_values.pop("provider_id", None)
                response_payload_values.pop("model_name", None)
                response_payload_values["settings"] = {}

        _set_schema_field_options(schema_copy, "provider_id", provider_options)
        if selected_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="provider_id",
                field=build_image_generation_model_field(selected_provider_id),
            )
            model_options = _get_image_generation_model_options(
                db,
                selected_provider_id,
            )
            _set_schema_field_options(schema_copy, "model_name", model_options)
            if include_values and response_payload_values is not None:
                selected_model = response_payload_values.get("model_name")
                allowed_models = {
                    option.get("value")
                    for option in model_options
                    if option.get("value")
                }
                if selected_model and selected_model not in allowed_models:
                    response_payload_values.pop("model_name", None)
                    response_payload_values["settings"] = {}

    if page == "audio_generation":
        provider_options = _get_audio_generation_provider_options(db)
        selected_provider_id = None

        if include_values and response_payload_values is not None:
            selected_provider_id = response_payload_values.get("provider_id")
            if selected_provider_id not in {
                option["value"] for option in provider_options if option.get("value")
            }:
                selected_provider_id = None
        if selected_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="provider_id",
                field=build_audio_generation_model_field(selected_provider_id),
            )
        model_options = _get_audio_generation_model_options(db, selected_provider_id)

        _set_schema_field_options(schema_copy, "provider_id", provider_options)
        _set_schema_field_options(schema_copy, "model_name", model_options)

        if include_values and response_payload_values is not None:
            current_provider = response_payload_values.get("provider_id")
            current_model = response_payload_values.get("model_name")
            provider_allowed_values = {option["value"] for option in provider_options}
            current_provider_row = (
                get_llm_provider(db, current_provider) if current_provider else None
            )
            current_provider_type = (
                str(current_provider_row.provider or "").strip()
                if current_provider_row
                and isinstance(current_provider_row.provider, str)
                else None
            )

            if current_provider and current_provider not in provider_allowed_values:
                response_payload_values.pop("provider_id", None)
                response_payload_values.pop("model_name", None)
                response_payload_values.pop("voice", None)
                response_payload_values.pop("response_format", None)
                _set_schema_field_value(schema_copy, "provider_id", None)
                _set_schema_field_value(schema_copy, "model_name", None)
            elif current_model:
                allowed_models = {
                    option.get("value")
                    for option in _get_audio_generation_model_options(
                        db, current_provider
                    )
                    if option.get("value")
                }
                if current_model not in allowed_models:
                    response_payload_values.pop("model_name", None)
                    response_payload_values.pop("voice", None)
                    response_payload_values.pop("response_format", None)
                    _set_schema_field_value(schema_copy, "model_name", None)
                else:
                    capabilities = (
                        _get_audio_generation_model_capabilities_for_provider(
                            current_model,
                            current_provider_type,
                            current_provider_row,
                        )
                    )
                    allowed_voices = {
                        str(voice).strip()
                        for voice in capabilities.get("voices", [])
                        if str(voice).strip()
                    }
                    allowed_formats = {
                        str(fmt).strip().lower()
                        for fmt in capabilities.get("response_formats", [])
                        if str(fmt).strip()
                    }
                    current_voice = str(
                        response_payload_values.get("voice") or ""
                    ).strip()
                    current_format = (
                        str(response_payload_values.get("response_format") or "")
                        .strip()
                        .lower()
                    )
                    if (
                        current_voice
                        and allowed_voices
                        and current_voice not in allowed_voices
                    ):
                        response_payload_values.pop("voice", None)
                    if (
                        current_format
                        and allowed_formats
                        and current_format not in allowed_formats
                    ):
                        response_payload_values.pop("response_format", None)

    if page == "music_generation":
        provider_options = _get_music_generation_provider_options(db)
        selected_provider_id = None

        if include_values and response_payload_values is not None:
            selected_provider_id = response_payload_values.get("provider_id")
            if selected_provider_id not in {
                option["value"] for option in provider_options if option.get("value")
            }:
                selected_provider_id = None
        if selected_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="provider_id",
                field=build_music_generation_model_field(selected_provider_id),
            )
        model_options = _get_music_generation_model_options(db, selected_provider_id)

        _set_schema_field_options(schema_copy, "provider_id", provider_options)
        _set_schema_field_options(schema_copy, "model_name", model_options)

        if include_values and response_payload_values is not None:
            current_provider = response_payload_values.get("provider_id")
            current_model = response_payload_values.get("model_name")
            provider_allowed_values = {option["value"] for option in provider_options}
            current_provider_row = (
                get_llm_provider(db, current_provider) if current_provider else None
            )
            current_provider_type = (
                str(current_provider_row.provider or "").strip()
                if current_provider_row
                and isinstance(current_provider_row.provider, str)
                else None
            )

            if current_provider and current_provider not in provider_allowed_values:
                response_payload_values.pop("provider_id", None)
                response_payload_values.pop("model_name", None)
                response_payload_values.pop("response_format", None)
                response_payload_values.pop("enable_reference_images", None)
                response_payload_values.pop("max_reference_images", None)
                _set_schema_field_value(schema_copy, "provider_id", None)
                _set_schema_field_value(schema_copy, "model_name", None)
            elif current_model:
                allowed_models = {
                    option.get("value")
                    for option in _get_music_generation_model_options(
                        db, current_provider
                    )
                    if option.get("value")
                }
                if current_model not in allowed_models:
                    response_payload_values.pop("model_name", None)
                    response_payload_values.pop("response_format", None)
                    response_payload_values.pop("enable_reference_images", None)
                    response_payload_values.pop("max_reference_images", None)
                    _set_schema_field_value(schema_copy, "model_name", None)
                else:
                    capabilities = _get_music_generation_model_capabilities(
                        current_model,
                        provider_type=current_provider_type,
                    )
                    allowed_formats = {
                        str(fmt).strip().lower()
                        for fmt in capabilities.get("response_formats", [])
                        if str(fmt).strip()
                    }
                    current_format = (
                        str(response_payload_values.get("response_format") or "")
                        .strip()
                        .lower()
                    )
                    if (
                        current_format
                        and allowed_formats
                        and current_format not in allowed_formats
                    ):
                        response_payload_values.pop("response_format", None)

    if page == "video_generation":
        provider_options = _get_video_generation_provider_options(db)
        selected_provider_id = None

        if include_values and response_payload_values is not None:
            selected_provider_id = response_payload_values.get("provider_id")
            if selected_provider_id not in {
                option["value"] for option in provider_options if option.get("value")
            }:
                selected_provider_id = None
        if selected_provider_id:
            _insert_schema_field_after(
                schema_copy,
                after_key="provider_id",
                field=build_video_generation_model_field(selected_provider_id),
            )
        model_options = _get_video_generation_model_options(db, selected_provider_id)

        _set_schema_field_options(schema_copy, "provider_id", provider_options)
        _set_schema_field_options(schema_copy, "model_name", model_options)

        if include_values and response_payload_values is not None:
            current_provider = response_payload_values.get("provider_id")
            current_model = response_payload_values.get("model_name")
            provider_allowed_values = {option["value"] for option in provider_options}

            if current_provider and current_provider not in provider_allowed_values:
                response_payload_values.pop("provider_id", None)
                response_payload_values.pop("model_name", None)
                _set_schema_field_value(schema_copy, "provider_id", None)
                _set_schema_field_value(schema_copy, "model_name", None)
            elif current_model:
                allowed_models = {
                    option.get("value")
                    for option in _get_video_generation_model_options(
                        db, current_provider
                    )
                    if option.get("value")
                }
                if current_model not in allowed_models:
                    response_payload_values.pop("model_name", None)
                    _set_schema_field_value(schema_copy, "model_name", None)

    if page == "deep_research":
        execution_mode = "custom"
        provider_options = _get_deep_research_provider_options(db)
        native_selected_provider_id = None
        custom_model_options = _get_custom_deep_research_model_options(db)
        search_provider_options = _get_websearch_provider_options_with_metadata(
            db, "search"
        )
        scrape_provider_options = _get_websearch_provider_options_with_metadata(
            db, "scrape"
        )

        if include_values and response_payload_values is not None:
            execution_mode = (
                str(response_payload_values.get("execution_mode") or "custom").strip()
                or "custom"
            )
            native_selected_provider_id = response_payload_values.get(
                "native_provider_id"
            )
        model_options = _get_deep_research_model_options(
            db, native_selected_provider_id
        )

        _set_schema_field_options(schema_copy, "native_provider_id", provider_options)
        _set_schema_field_options(schema_copy, "native_model_name", model_options)
        _set_schema_field_options(schema_copy, "model_id", custom_model_options)
        _set_schema_field_options(
            schema_copy, "websearch_search_provider", search_provider_options
        )
        _set_schema_field_options(
            schema_copy, "websearch_scrape_provider", scrape_provider_options
        )

        if include_values and response_payload_values is not None:
            current_mode = (
                str(
                    response_payload_values.get("execution_mode")
                    or execution_mode
                    or "custom"
                ).strip()
                or "custom"
            )
            current_provider = response_payload_values.get("native_provider_id")
            current_native_model = response_payload_values.get("native_model_name")
            current_model = response_payload_values.get("model_id")
            current_search_provider = response_payload_values.get(
                "websearch_search_provider"
            )
            current_scrape_provider = response_payload_values.get(
                "websearch_scrape_provider"
            )
            provider_allowed_values = {option["value"] for option in provider_options}
            custom_model_allowed_values = {
                option["value"] for option in custom_model_options
            }
            search_provider_allowed_values = {
                option["value"] for option in search_provider_options
            }
            scrape_provider_allowed_values = {
                option["value"] for option in scrape_provider_options
            }

            if current_provider and current_provider not in provider_allowed_values:
                response_payload_values.pop("native_provider_id", None)
                response_payload_values.pop("native_model_name", None)
                _set_schema_field_value(schema_copy, "native_provider_id", None)
                _set_schema_field_value(schema_copy, "native_model_name", None)
            elif current_native_model:
                allowed_models = {
                    option.get("value")
                    for option in _get_deep_research_model_options(db, current_provider)
                    if option.get("value")
                }
                if current_native_model not in allowed_models:
                    response_payload_values.pop("native_model_name", None)
                    _set_schema_field_value(schema_copy, "native_model_name", None)
            if current_model and current_model not in custom_model_allowed_values:
                response_payload_values.pop("model_id", None)
                _set_schema_field_value(schema_copy, "model_id", None)
            if (
                current_search_provider
                and current_search_provider not in search_provider_allowed_values
            ):
                response_payload_values.pop("websearch_search_provider", None)
                _set_schema_field_value(schema_copy, "websearch_search_provider", None)
            if (
                current_scrape_provider
                and current_scrape_provider not in scrape_provider_allowed_values
            ):
                response_payload_values.pop("websearch_scrape_provider", None)
                _set_schema_field_value(schema_copy, "websearch_scrape_provider", None)
            if current_mode not in {"custom", "native"}:
                response_payload_values["execution_mode"] = "custom"
                _set_schema_field_value(schema_copy, "execution_mode", "custom")
    if page == "slide_presentation":
        model_options = _get_admin_managed_model_options(db)
        model_option_values = {option["value"] for option in model_options}

        for key in ("presentation_model_id",):
            _set_schema_field_options(schema_copy, key, model_options)

        if include_values and response_payload_values is not None:
            for key in ("presentation_model_id",):
                current_value = response_payload_values.get(key)
                if current_value and current_value not in model_option_values:
                    response_payload_values.pop(key, None)
                    _set_schema_field_value(schema_copy, key, None)
    if page == "login_general":
        group_options = _get_group_options(db)
        _set_schema_field_options(schema_copy, "default_user_group", group_options)
        login_general_data = get_settings_page_data(db, "login_general")
        login_general_secret_placeholders = {
            "smtp_password": "Enter SMTP password",
        }
        for (
            field_key,
            fallback_placeholder,
        ) in login_general_secret_placeholders.items():
            stored_secret = login_general_data.get(field_key)
            stored_placeholder = _mask_secret_preview(stored_secret)
            placeholder_value = stored_placeholder or fallback_placeholder
            _set_schema_field_placeholder(schema_copy, field_key, placeholder_value)
            if include_values and response_payload_values is not None:
                response_payload_values.pop(field_key, None)
        if include_values and response_payload_values is not None:
            legacy_login_passkeys_data = get_settings_page_data(db, "login_passkeys")
            for field_key in ("enable_passkeys",):
                if field_key not in response_payload_values:
                    legacy_value = legacy_login_passkeys_data.get(field_key)
                    if legacy_value is not None:
                        coerced = _coerce_bool(legacy_value)
                        response_payload_values[field_key] = coerced
                        _set_schema_field_value(schema_copy, field_key, coerced)
        if include_values and response_payload_values is not None:
            current_value = response_payload_values.get("default_user_group")
            allowed_values = {option["value"] for option in group_options}
            if current_value and current_value not in allowed_values:
                response_payload_values.pop("default_user_group", None)
                _set_schema_field_value(schema_copy, "default_user_group", None)

    if page == "groups_defaults":
        group_options = _get_group_options(db)
        _set_schema_field_options(schema_copy, "default_user_group", group_options)
        if include_values and response_payload_values is not None:
            current_value = response_payload_values.get("default_user_group")
            allowed_values = {option["value"] for option in group_options}
            if current_value and current_value not in allowed_values:
                response_payload_values.pop("default_user_group", None)
                _set_schema_field_value(schema_copy, "default_user_group", None)

    if page == "login_enterprise_sso":
        group_options = _get_group_options(db)
        group_field_keys = (
            "scim_default_group",
            "saml_default_group",
            "oidc_default_group",
        )
        for field_key in group_field_keys:
            _set_schema_field_options(schema_copy, field_key, group_options)
        if include_values and response_payload_values is not None:
            allowed_values = {option["value"] for option in group_options}
            for field_key in group_field_keys:
                current_value = response_payload_values.get(field_key)
                if current_value and current_value not in allowed_values:
                    response_payload_values.pop(field_key, None)
                    _set_schema_field_value(schema_copy, field_key, None)

    if page == "login_ldap":
        group_options = _get_group_options(db)
        _set_schema_field_options(schema_copy, "ldap_default_group", group_options)
        if include_values and response_payload_values is not None:
            current_value = response_payload_values.get("ldap_default_group")
            allowed_values = {option["value"] for option in group_options}
            if current_value and current_value not in allowed_values:
                response_payload_values.pop("ldap_default_group", None)
                _set_schema_field_value(schema_copy, "ldap_default_group", None)

    if page == "weather_tool":
        weather_settings = get_settings_page_data(db, "weather_tool")
        stored_api_key = weather_settings.get("api_key")
        stored_placeholder = _mask_api_key_preview(stored_api_key)
        placeholder_value = stored_placeholder or "Enter OpenWeatherMap API key"
        _set_schema_field_placeholder(schema_copy, "api_key", placeholder_value)
        if include_values and response_payload_values is not None:
            response_payload_values.pop("api_key", None)

    if page == "login_social":
        login_social_data = get_settings_page_data(db, "login_social")
        social_secret_placeholders = {
            "google_client_secret": "Enter Google Client Secret",
            "github_client_secret": "Enter GitHub Client Secret",
            "slack_client_secret": "Enter Slack Client Secret",
            "microsoft_client_secret": "Enter Microsoft Client Secret",
            "apple_private_key": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----",
        }
        for field_key, fallback_placeholder in social_secret_placeholders.items():
            stored_secret = login_social_data.get(field_key)
            placeholder_value = _sensitive_secret_placeholder(
                stored_secret, fallback_placeholder
            )
            _set_schema_field_placeholder(schema_copy, field_key, placeholder_value)
            if include_values and response_payload_values is not None:
                response_payload_values.pop(field_key, None)

    if page == "login_enterprise_sso":
        sso_settings = get_settings_page_data(db, "login_enterprise_sso")
        sso_secret_placeholders = {
            "oidc_client_secret": "Enter Client Secret",
            "scim_bearer_token": "Enter SCIM bearer token",
            "scim_previous_bearer_token": "Enter previous SCIM bearer token",
        }
        for field_key, fallback_placeholder in sso_secret_placeholders.items():
            stored_secret = sso_settings.get(field_key)
            placeholder_value = _sensitive_secret_placeholder(
                stored_secret, fallback_placeholder
            )
            _set_schema_field_placeholder(schema_copy, field_key, placeholder_value)
            if include_values and response_payload_values is not None:
                response_payload_values.pop(field_key, None)

    if page == "login_ldap":
        ldap_settings = get_settings_page_data(db, "login_ldap")
        stored_secret = ldap_settings.get("ldap_bind_password")
        placeholder_value = _sensitive_secret_placeholder(
            stored_secret, "Enter bind password"
        )
        _set_schema_field_placeholder(
            schema_copy, "ldap_bind_password", placeholder_value
        )
        if include_values and response_payload_values is not None:
            response_payload_values.pop("ldap_bind_password", None)

    # Dynamic model steps and provider fragments are composed after the base
    # value pass, so populate them once more before serializing the response.
    if include_values and response_payload_values is not None:
        _populate_schema_values(schema_copy, response_payload_values)

    sections_payload = schema_copy.model_dump(exclude_none=True).get("sections", [])
    response_payload: Dict[str, Any] = {"sections": sections_payload}

    if include_values and response_payload_values is not None:
        # Shared capability hydration must never widen a page's response
        # contract with values owned by a sibling settings domain.
        allowed_response_fields = set(model.model_fields)
        response_payload["values"] = {
            key: value
            for key, value in response_payload_values.items()
            if key in allowed_response_fields
        }

    return response_payload


# -------------------
# Update Admin Settings Values For Page
# -------------------
def update_admin_settings_values_for_page(
    page: str,
    payload: Dict[str, Any],
    db: Session,
    *,
    request_client_ip: str | None = None,
) -> List[str]:
    """Update admin settings values for a page."""
    page_model_map = {
        "general": GeneralSettings,
        "groups_defaults": GroupsDefaultsSettings,
        "login_general": LoginGeneralSettings,
        "login_customization": LoginCustomizationSettings,
        "login_social": LoginSocialSettings,
        "login_enterprise_sso": LoginEnterpriseSSOSettings,
        "login_ldap": LoginLDAPSettings,
        "security": SecuritySettings,
        "users": UsersSettings,
        "notifications": NotificationSettings,
        "models": ModelDefaultsSettings,
        "dictation": DictationSettings,
        "read_aloud": ReadAloudSettings,
        "realtime": RealtimeSettings,
        "slide_presentation": SlidePresentationSettings,
        "weather_tool": WeatherToolSettings,
        "code_execution": CodeExecutionSettings,
        "image_generation": ImageGenerationSettings,
        "audio_generation": AudioGenerationSettings,
        "music_generation": MusicGenerationSettings,
        "video_generation": VideoGenerationSettings,
        "deep_research": DeepResearchSettings,
    }

    model = page_model_map.get(page)
    if model is None:
        raise HTTPException(status_code=404, detail="Not Found")
    storage_page = "login_general" if page == "groups_defaults" else page

    api_key_updates: Dict[str, Any] = {}
    normalized_payload = dict(payload)
    submitted_payload_keys = set(normalized_payload)
    settings_record_override = None
    if page == "security":
        api_key_updates = {
            key: normalized_payload.pop(key)
            for key in SECURITY_API_KEY_FIELDS
            if key in normalized_payload
        }
    if page == "image_generation":
        if "provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["provider_id"] = raw_provider.strip() or None
            elif raw_provider in ("", None):
                normalized_payload["provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["provider_id"] = str(raw_provider).strip() or None

        if "model_name" in normalized_payload:
            raw_model = normalized_payload.get("model_name")
            if isinstance(raw_model, str):
                normalized_payload["model_name"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["model_name"] = None
            elif raw_model is not None:
                normalized_payload["model_name"] = str(raw_model).strip() or None

        raw_settings = normalized_payload.get("settings")
        normalized_settings = raw_settings if isinstance(raw_settings, dict) else {}
        provider_id = normalized_payload.get("provider_id")
        model_name = normalized_payload.get("model_name")
        provider_type = None
        if provider_id:
            provider_row = get_llm_provider(db, provider_id)
            if provider_row:
                provider_type = str(provider_row.provider or "").strip().lower() or None

        selection_kind = (
            get_assistant_size_selection_kind(provider_type, str(model_name))
            if provider_type and model_name
            else None
        )
        if selection_kind:
            if ASSISTANT_SIZE_SELECTION_KEY in normalized_settings:
                normalized_settings[ASSISTANT_SIZE_SELECTION_KEY] = (
                    assistant_size_selection_enabled(normalized_settings)
                )
            if "allowed_sizes" in normalized_settings:
                supported_sizes = get_supported_tool_size_values(
                    provider_type, str(model_name)
                )
                if supported_sizes:
                    normalized_settings["allowed_sizes"] = filter_supported_tool_sizes(
                        provider_type,
                        str(model_name),
                        normalized_settings.get("allowed_sizes"),
                    )
                else:
                    normalized_settings.pop("allowed_sizes", None)
        else:
            normalized_settings.pop(ASSISTANT_SIZE_SELECTION_KEY, None)
            normalized_settings.pop("allowed_sizes", None)

        normalized_payload["settings"] = normalized_settings

    if page in {"models", "dictation", "read_aloud", "realtime"}:
        existing_page_data_cache: dict[str, Any] | None = None

        def _get_existing_page_data() -> dict[str, Any]:
            """Return the persisted values for the independently owned page."""
            nonlocal existing_page_data_cache, settings_record_override
            if existing_page_data_cache is not None:
                return existing_page_data_cache
            if settings_record_override is None:
                settings_record_override = get_settings_page(db, page)
            if settings_record_override and isinstance(
                settings_record_override.data, dict
            ):
                existing_page_data_cache = settings_record_override.data
            else:
                existing_page_data_cache = {}
            return existing_page_data_cache

        if "default_model" in normalized_payload:
            raw_value = normalized_payload.get("default_model")
            if isinstance(raw_value, str):
                normalized_payload["default_model"] = raw_value.strip() or None
            elif raw_value in ("", None):
                normalized_payload["default_model"] = None
            elif raw_value is not None:
                normalized_payload["default_model"] = str(raw_value)

            validated_default = normalized_payload.get("default_model")
            if validated_default:
                allowed_values = {
                    option["value"] for option in _get_public_model_options(db)
                }
                if validated_default not in allowed_values:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected default model must be visible to everyone.",
                    )

        if "default_pinned_models" in normalized_payload:
            allowed_values = {
                option["value"] for option in _get_public_model_options(db)
            }
            normalized_pinned_models = sanitize_pinned_model_ids(
                normalized_payload.get("default_pinned_models")
            )
            invalid_models = [
                model_id
                for model_id in normalized_pinned_models
                if model_id not in allowed_values
            ]
            if invalid_models:
                raise HTTPException(
                    status_code=400,
                    detail="Selected default pinned models must be visible to everyone.",
                )
            normalized_payload["default_pinned_models"] = normalized_pinned_models

        if "transcription_provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("transcription_provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["transcription_provider_id"] = (
                    raw_provider.strip() or None
                )
            elif raw_provider in ("", None):
                normalized_payload["transcription_provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["transcription_provider_id"] = str(raw_provider)

            provider_value = normalized_payload.get("transcription_provider_id")
            if provider_value:
                allowed_providers = {
                    option["value"]
                    for option in _get_transcription_provider_options(db)
                }
                if provider_value not in allowed_providers:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected transcription provider is not available.",
                    )

            if "transcription_model" not in normalized_payload:
                existing_page_data = _get_existing_page_data()
                existing_provider = existing_page_data.get(
                    "transcription_provider_id"
                )
                existing_provider_value = (
                    existing_provider.strip()
                    if isinstance(existing_provider, str)
                    else str(existing_provider).strip()
                    if existing_provider is not None
                    else None
                ) or None

                # A model selection belongs to exactly one provider step. Clear it
                # whenever that parent changes, even if two providers happen to use
                # the same model identifier.
                if provider_value != existing_provider_value:
                    normalized_payload["transcription_model"] = None
                elif provider_value:
                    existing_model = existing_page_data.get("transcription_model")
                    existing_model_value = (
                        existing_model.strip()
                        if isinstance(existing_model, str)
                        else str(existing_model).strip()
                        if existing_model is not None
                        else ""
                    )
                    if existing_model_value:
                        allowed_models_for_provider = set(
                            _get_transcription_model_ids_for_provider(
                                db, provider_value
                            )
                        )
                        if existing_model_value not in allowed_models_for_provider:
                            normalized_payload["transcription_model"] = None

        if "transcription_model" in normalized_payload:
            raw_model = normalized_payload.get("transcription_model")
            if isinstance(raw_model, str):
                normalized_payload["transcription_model"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["transcription_model"] = None
            elif raw_model is not None:
                normalized_payload["transcription_model"] = str(raw_model)

            model_value = normalized_payload.get("transcription_model")
            if model_value:
                provider_value = normalized_payload.get("transcription_provider_id")
                if provider_value is None:
                    existing_provider = _get_existing_page_data().get(
                        "transcription_provider_id"
                    )
                    if isinstance(existing_provider, str):
                        provider_value = existing_provider.strip() or None
                if not provider_value:
                    raise HTTPException(
                        status_code=400,
                        detail="Select a transcription provider before choosing a model.",
                    )
                allowed_models = set(
                    _get_transcription_model_ids_for_provider(db, provider_value)
                )
                if model_value not in allowed_models:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected transcription model is not supported.",
                    )

        if "live_transcription_provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("live_transcription_provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["live_transcription_provider_id"] = (
                    raw_provider.strip() or None
                )
            elif raw_provider in ("", None):
                normalized_payload["live_transcription_provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["live_transcription_provider_id"] = str(raw_provider)

            provider_value = normalized_payload.get("live_transcription_provider_id")
            if provider_value:
                allowed_providers = {
                    option["value"]
                    for option in _get_live_transcription_provider_options(db)
                }
                if provider_value not in allowed_providers:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected live transcription provider is not available.",
                    )

            if "live_transcription_model" not in normalized_payload:
                existing_page_data = _get_existing_page_data()
                existing_provider = existing_page_data.get(
                    "live_transcription_provider_id"
                )
                existing_provider_value = (
                    existing_provider.strip()
                    if isinstance(existing_provider, str)
                    else str(existing_provider).strip()
                    if existing_provider is not None
                    else None
                ) or None

                if provider_value != existing_provider_value:
                    normalized_payload["live_transcription_model"] = None
                elif provider_value:
                    existing_model = existing_page_data.get(
                        "live_transcription_model"
                    )
                    existing_model_value = (
                        existing_model.strip()
                        if isinstance(existing_model, str)
                        else str(existing_model).strip()
                        if existing_model is not None
                        else ""
                    )
                    allowed_models = {
                        option["value"]
                        for option in _get_live_transcription_model_options(
                            db,
                            provider_value,
                        )
                    }
                    if (
                        existing_model_value
                        and existing_model_value not in allowed_models
                    ):
                        normalized_payload["live_transcription_model"] = None

        if "live_transcription_model" in normalized_payload:
            raw_model = normalized_payload.get("live_transcription_model")
            if isinstance(raw_model, str):
                normalized_payload["live_transcription_model"] = (
                    raw_model.strip() or None
                )
            elif raw_model in ("", None):
                normalized_payload["live_transcription_model"] = None
            elif raw_model is not None:
                normalized_payload["live_transcription_model"] = str(raw_model)

            model_value = normalized_payload.get("live_transcription_model")
            if model_value:
                provider_value = normalized_payload.get(
                    "live_transcription_provider_id"
                )
                if provider_value is None:
                    existing_provider = _get_existing_page_data().get(
                        "live_transcription_provider_id"
                    )
                    if isinstance(existing_provider, str):
                        provider_value = existing_provider.strip() or None
                if not provider_value:
                    raise HTTPException(
                        status_code=400,
                        detail="Select a live transcription provider before choosing a model.",
                    )
                allowed_models = {
                    option["value"]
                    for option in _get_live_transcription_model_options(
                        db,
                        provider_value,
                    )
                }
                if model_value not in allowed_models:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected live transcription model is not supported.",
                    )

        provider_in_payload = "read_aloud_provider_id" in normalized_payload

        if "read_aloud_provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("read_aloud_provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["read_aloud_provider_id"] = (
                    raw_provider.strip() or READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                )
            elif raw_provider in ("", None):
                normalized_payload["read_aloud_provider_id"] = (
                    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                )
            elif raw_provider is not None:
                normalized_payload["read_aloud_provider_id"] = (
                    str(raw_provider).strip() or READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
                )

            existing_provider = _get_existing_page_data().get(
                "read_aloud_provider_id"
            )
            existing_provider_value = (
                existing_provider.strip()
                if isinstance(existing_provider, str) and existing_provider.strip()
                else READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
            )
            provider_value = normalized_payload.get("read_aloud_provider_id")
            if provider_value != existing_provider_value:
                # Voice and response format are model/provider capabilities too.
                # Clear every omitted child when the provider step changes so a
                # hidden value cannot leak into the newly selected provider.
                normalized_payload.setdefault("read_aloud_model", None)
                normalized_payload.setdefault("read_aloud_voice", None)
                normalized_payload.setdefault("read_aloud_response_format", None)

        if "read_aloud_model" in normalized_payload:
            raw_model = normalized_payload.get("read_aloud_model")
            if isinstance(raw_model, str):
                normalized_payload["read_aloud_model"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["read_aloud_model"] = None
            elif raw_model is not None:
                normalized_payload["read_aloud_model"] = str(raw_model).strip() or None
            if normalized_payload.get("read_aloud_model") is None:
                if "read_aloud_voice" not in normalized_payload:
                    normalized_payload["read_aloud_voice"] = None
                if "read_aloud_response_format" not in normalized_payload:
                    normalized_payload["read_aloud_response_format"] = None

        if "read_aloud_voice" in normalized_payload:
            raw_voice = normalized_payload.get("read_aloud_voice")
            if isinstance(raw_voice, str):
                normalized_payload["read_aloud_voice"] = raw_voice.strip() or None
            elif raw_voice in ("", None):
                normalized_payload["read_aloud_voice"] = None
            elif raw_voice is not None:
                normalized_payload["read_aloud_voice"] = str(raw_voice).strip() or None

        if "read_aloud_response_format" in normalized_payload:
            raw_format = normalized_payload.get("read_aloud_response_format")
            if isinstance(raw_format, str):
                normalized_payload["read_aloud_response_format"] = (
                    raw_format.strip().lower() or None
                )
            elif raw_format in ("", None):
                normalized_payload["read_aloud_response_format"] = None
            elif raw_format is not None:
                normalized_payload["read_aloud_response_format"] = (
                    str(raw_format).strip().lower() or None
                )

        if (
            provider_in_payload
            and normalized_payload.get("read_aloud_provider_id")
            == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
        ):
            if "read_aloud_model" not in normalized_payload:
                normalized_payload["read_aloud_model"] = None
            if "read_aloud_voice" not in normalized_payload:
                normalized_payload["read_aloud_voice"] = None
            if "read_aloud_response_format" not in normalized_payload:
                normalized_payload["read_aloud_response_format"] = None
        elif provider_in_payload and "read_aloud_model" not in normalized_payload:
            provider_value = normalized_payload.get("read_aloud_provider_id")
            existing_model = _get_existing_page_data().get("read_aloud_model")
            existing_model_value = (
                existing_model.strip()
                if isinstance(existing_model, str)
                else str(existing_model).strip()
                if existing_model is not None
                else ""
            )
            if provider_value and existing_model_value:
                allowed_models_for_provider = {
                    option.get("value")
                    for option in _get_audio_generation_model_options(
                        db, provider_value
                    )
                    if option.get("value")
                }
                if existing_model_value not in allowed_models_for_provider:
                    normalized_payload["read_aloud_model"] = None
                    normalized_payload["read_aloud_voice"] = None
                    normalized_payload["read_aloud_response_format"] = None

        merged_read_aloud_provider_id = normalized_payload.get("read_aloud_provider_id")
        if merged_read_aloud_provider_id is None:
            existing_provider = _get_existing_page_data().get("read_aloud_provider_id")
            if isinstance(existing_provider, str) and existing_provider.strip():
                merged_read_aloud_provider_id = existing_provider.strip()
            else:
                merged_read_aloud_provider_id = READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID

        # Resolve merged model, voice, and format values.
        # Check explicit payload keys first; if provider is browser native, model/voice/format are None.
        if "read_aloud_model" in normalized_payload:
            merged_read_aloud_model = normalized_payload["read_aloud_model"]
        elif merged_read_aloud_provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
            merged_read_aloud_model = None
        else:
            existing_model = _get_existing_page_data().get("read_aloud_model")
            merged_read_aloud_model = (
                existing_model.strip()
                if isinstance(existing_model, str) and existing_model.strip()
                else None
            )

        if "read_aloud_voice" in normalized_payload:
            merged_read_aloud_voice = normalized_payload["read_aloud_voice"]
        elif merged_read_aloud_provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
            merged_read_aloud_voice = None
        else:
            existing_voice = _get_existing_page_data().get("read_aloud_voice")
            merged_read_aloud_voice = (
                existing_voice.strip()
                if isinstance(existing_voice, str) and existing_voice.strip()
                else None
            )

        if "read_aloud_response_format" in normalized_payload:
            merged_read_aloud_response_format = normalized_payload[
                "read_aloud_response_format"
            ]
        elif merged_read_aloud_provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
            merged_read_aloud_response_format = None
        else:
            existing_format = _get_existing_page_data().get(
                "read_aloud_response_format"
            )
            merged_read_aloud_response_format = (
                existing_format.strip().lower()
                if isinstance(existing_format, str) and existing_format.strip()
                else None
            )

        read_aloud_provider_row = None
        read_aloud_provider_type = None
        if merged_read_aloud_provider_id != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
            allowed_read_aloud_providers = {
                option["value"]
                for option in _get_read_aloud_provider_options(db)
                if option.get("value")
                and option.get("value") != READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
            }
            if merged_read_aloud_provider_id not in allowed_read_aloud_providers:
                raise HTTPException(
                    status_code=400,
                    detail="Selected read aloud provider is not available.",
                )

            read_aloud_provider_row = get_llm_provider(
                db,
                merged_read_aloud_provider_id,
            )
            if not read_aloud_provider_row:
                raise HTTPException(
                    status_code=400,
                    detail="Selected read aloud provider was not found.",
                )
            read_aloud_provider_type = str(
                read_aloud_provider_row.provider or ""
            ).strip()
            if read_aloud_provider_type not in AUDIO_GENERATION_PROVIDER_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="Read aloud provider must support text-to-speech.",
                )

        if merged_read_aloud_provider_id == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID:
            normalized_payload["read_aloud_provider_id"] = (
                READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
            )
            normalized_payload["read_aloud_model"] = None
            normalized_payload["read_aloud_voice"] = None
            normalized_payload["read_aloud_response_format"] = None
            if merged_read_aloud_model:
                raise HTTPException(
                    status_code=400,
                    detail="Select a custom read aloud provider before choosing a model.",
                )
        else:
            if merged_read_aloud_model:
                allowed_models = {
                    option.get("value")
                    for option in _get_audio_generation_model_options(
                        db, merged_read_aloud_provider_id
                    )
                    if option.get("value")
                }
                if merged_read_aloud_model not in allowed_models:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected read aloud model is not supported by the chosen provider.",
                    )

                capabilities = _get_audio_generation_model_capabilities_for_provider(
                    merged_read_aloud_model,
                    provider_type=read_aloud_provider_type,
                    provider_row=read_aloud_provider_row,
                )
                allowed_voices = {
                    str(voice).strip()
                    for voice in capabilities.get("voices", [])
                    if str(voice).strip()
                }
                allowed_formats = {
                    str(fmt).strip().lower()
                    for fmt in capabilities.get("response_formats", [])
                    if str(fmt).strip()
                }
                voice_required = bool(capabilities.get("voice_required"))
                supports_custom_voice = bool(capabilities.get("supports_custom_voice"))

                if (
                    merged_read_aloud_voice
                    and allowed_voices
                    and merged_read_aloud_voice not in allowed_voices
                    and not supports_custom_voice
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Selected read aloud voice is not supported by the chosen model.",
                    )

                if (
                    merged_read_aloud_response_format
                    and allowed_formats
                    and merged_read_aloud_response_format not in allowed_formats
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Selected read aloud audio format is not supported by the chosen model.",
                    )

        if "realtime_provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("realtime_provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["realtime_provider_id"] = (
                    raw_provider.strip() or None
                )
            elif raw_provider in ("", None):
                normalized_payload["realtime_provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["realtime_provider_id"] = str(raw_provider)

            provider_value = normalized_payload.get("realtime_provider_id")
            if provider_value:
                allowed_providers = {
                    option["value"] for option in _get_realtime_provider_options(db)
                }
                if provider_value not in allowed_providers:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected realtime provider is not available.",
                    )

            if "realtime_model" not in normalized_payload:
                existing_page_data = _get_existing_page_data()
                existing_provider = existing_page_data.get("realtime_provider_id")
                existing_provider_value = (
                    existing_provider.strip()
                    if isinstance(existing_provider, str)
                    else str(existing_provider).strip()
                    if existing_provider is not None
                    else None
                ) or None

                if provider_value != existing_provider_value:
                    normalized_payload["realtime_model"] = None
                    # Voice options are provider capabilities too. Clear an
                    # omitted voice alongside the model so a value belonging to
                    # the previous provider cannot survive this parent change.
                    normalized_payload.setdefault("realtime_voice", None)
                elif provider_value:
                    existing_model = existing_page_data.get("realtime_model")
                    existing_model_value = (
                        existing_model.strip()
                        if isinstance(existing_model, str)
                        else str(existing_model).strip()
                        if existing_model is not None
                        else ""
                    )
                    if existing_model_value:
                        allowed_models_for_provider = set(
                            _get_realtime_model_ids_for_provider(db, provider_value)
                        )
                        if existing_model_value not in allowed_models_for_provider:
                            normalized_payload["realtime_model"] = None

        if "realtime_model" in normalized_payload:
            raw_model = normalized_payload.get("realtime_model")
            if isinstance(raw_model, str):
                normalized_payload["realtime_model"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["realtime_model"] = None
            elif raw_model is not None:
                normalized_payload["realtime_model"] = str(raw_model)

            model_value = normalized_payload.get("realtime_model")
            if model_value:
                provider_value = normalized_payload.get("realtime_provider_id")
                if provider_value is None:
                    existing_provider = _get_existing_page_data().get(
                        "realtime_provider_id"
                    )
                    if isinstance(existing_provider, str):
                        provider_value = existing_provider.strip() or None
                if not provider_value:
                    raise HTTPException(
                        status_code=400,
                        detail="Select a realtime provider before choosing a model.",
                    )
                allowed_models = set(
                    _get_realtime_model_ids_for_provider(db, provider_value)
                )
                if model_value not in allowed_models:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected realtime model is not supported.",
                    )

        if "realtime_voice" in normalized_payload:
            raw_voice = normalized_payload.get("realtime_voice")
            if isinstance(raw_voice, str):
                normalized_payload["realtime_voice"] = raw_voice.strip() or None
            elif raw_voice in ("", None):
                normalized_payload["realtime_voice"] = None
            elif raw_voice is not None:
                normalized_payload["realtime_voice"] = str(raw_voice).strip() or None

        if "realtime_tools" in normalized_payload:
            raw_tools = normalized_payload.get("realtime_tools")
            if not isinstance(raw_tools, list):
                raise HTTPException(
                    status_code=400,
                    detail="Realtime tools must be submitted as a list.",
                )

            # Preserve registry order in storage. Stable ordering keeps the
            # provider session payload and settings exports deterministic.
            requested_tools = {
                str(tool_name).strip()
                for tool_name in raw_tools
                if str(tool_name).strip()
            }
            available_tools = [
                option["name"]
                for option in list_available_tool_options(db)
                if option.get("name")
            ]
            unknown_tools = requested_tools.difference(available_tools)
            if unknown_tools:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown realtime tools: {', '.join(sorted(unknown_tools))}",
                )
            normalized_payload["realtime_tools"] = [
                tool_name
                for tool_name in available_tools
                if tool_name in requested_tools
            ]

        # Read-aloud normalization historically lived in the combined models
        # branch and fills dependent values even when they were not submitted.
        # Do not let those internally generated keys leak into the other three
        # now-independent settings contracts. Explicit cross-page keys remain
        # present so the normal unknown-field validation still rejects them.
        if page != "read_aloud":
            for read_aloud_key in (
                "read_aloud_provider_id",
                "read_aloud_model",
                "read_aloud_voice",
                "read_aloud_response_format",
            ):
                if read_aloud_key not in submitted_payload_keys:
                    normalized_payload.pop(read_aloud_key, None)
    if page == "slide_presentation":
        model_options = _get_admin_managed_model_options(db)

        option_map = {"presentation_model_id": (model_options, "presentation model")}

        def _normalize_choice_field(field_key: str, allowed_values: set[str]) -> None:
            if field_key not in normalized_payload:
                return
            raw_value = normalized_payload.get(field_key)
            if isinstance(raw_value, str):
                normalized_payload[field_key] = raw_value.strip() or None
            elif raw_value in ("", None):
                normalized_payload[field_key] = None
            elif raw_value is not None:
                normalized_payload[field_key] = str(raw_value)
            value = normalized_payload.get(field_key)
            if value and value not in allowed_values:
                raise HTTPException(
                    status_code=400,
                    detail=f"Selected {option_map[field_key][1]} is not available.",
                )

        for key, (options, _) in option_map.items():
            allowed_values = {opt["value"] for opt in options if opt.get("value")}
            _normalize_choice_field(key, allowed_values)

    if page == "weather_tool" and "api_key" in normalized_payload:
        weather_settings = get_settings_page_data(db, "weather_tool")
        existing_api_key = weather_settings.get("api_key")
        incoming_api_key = normalized_payload.get("api_key")

        if _is_masked_preview(incoming_api_key, existing_api_key):
            normalized_payload.pop("api_key", None)
        elif incoming_api_key is None:
            normalized_payload["api_key"] = ""
        elif isinstance(incoming_api_key, str):
            normalized_payload["api_key"] = incoming_api_key.strip()
        else:
            normalized_payload["api_key"] = str(incoming_api_key).strip()

    if page == "audio_generation":
        xai_setting_keys = (
            "language",
            "sample_rate",
            "bit_rate",
            "speed",
            "optimize_streaming_latency",
            "text_normalization",
        )
        provider_in_payload = "provider_id" in normalized_payload

        if "provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["provider_id"] = raw_provider.strip() or None
            elif raw_provider in ("", None):
                normalized_payload["provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["provider_id"] = str(raw_provider).strip() or None

        if "model_name" in normalized_payload:
            raw_model = normalized_payload.get("model_name")
            if isinstance(raw_model, str):
                normalized_payload["model_name"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["model_name"] = None
            elif raw_model is not None:
                normalized_payload["model_name"] = str(raw_model).strip() or None

        if "voice" in normalized_payload:
            raw_voice = normalized_payload.get("voice")
            if isinstance(raw_voice, str):
                normalized_payload["voice"] = raw_voice.strip() or None
            elif raw_voice in ("", None):
                normalized_payload["voice"] = None
            elif raw_voice is not None:
                normalized_payload["voice"] = str(raw_voice).strip() or None

        if "response_format" in normalized_payload:
            raw_format = normalized_payload.get("response_format")
            if isinstance(raw_format, str):
                normalized_payload["response_format"] = (
                    raw_format.strip().lower() or None
                )
            elif raw_format in ("", None):
                normalized_payload["response_format"] = None
            elif raw_format is not None:
                normalized_payload["response_format"] = (
                    str(raw_format).strip().lower() or None
                )

        if "language" in normalized_payload:
            raw_language = normalized_payload.get("language")
            normalized_payload["language"] = (
                str(raw_language).strip() or None if raw_language is not None else None
            )

        for integer_key in ("sample_rate", "bit_rate", "optimize_streaming_latency"):
            if integer_key not in normalized_payload:
                continue
            raw_value = normalized_payload.get(integer_key)
            if raw_value in (None, ""):
                normalized_payload[integer_key] = None
                continue
            try:
                normalized_payload[integer_key] = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "admin_settings_validation_failed",
                        "message": "Validation failed.",
                    },
                ) from exc

        if "speed" in normalized_payload:
            raw_speed = normalized_payload.get("speed")
            if raw_speed in (None, ""):
                normalized_payload["speed"] = None
            else:
                try:
                    normalized_payload["speed"] = float(raw_speed)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "admin_settings_validation_failed",
                            "message": "Validation failed.",
                        },
                    ) from exc

        if "text_normalization" in normalized_payload:
            raw_normalization = normalized_payload.get("text_normalization")
            if raw_normalization in (None, ""):
                normalized_payload["text_normalization"] = None
            elif isinstance(raw_normalization, str):
                normalized_payload["text_normalization"] = (
                    raw_normalization.strip().lower()
                    in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
                )
            else:
                normalized_payload["text_normalization"] = bool(raw_normalization)

        if provider_in_payload and normalized_payload.get("provider_id") is None:
            if "model_name" not in normalized_payload:
                normalized_payload["model_name"] = None
            if "voice" not in normalized_payload:
                normalized_payload["voice"] = None
            if "response_format" not in normalized_payload:
                normalized_payload["response_format"] = None
            for key in xai_setting_keys:
                if key not in normalized_payload:
                    normalized_payload[key] = None

        merged_provider_id = normalized_payload.get("provider_id")
        if merged_provider_id is None:
            existing_record = get_settings_page(db, "audio_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_provider_id = existing_data.get("provider_id") or None

        merged_model_name = normalized_payload.get("model_name")
        if merged_model_name is None:
            existing_record = get_settings_page(db, "audio_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_model_name = existing_data.get("model_name") or None

        merged_voice = normalized_payload.get("voice")
        if merged_voice is None:
            existing_record = get_settings_page(db, "audio_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_voice = existing_data.get("voice") or None

        merged_response_format = normalized_payload.get("response_format")
        if merged_response_format is None:
            existing_record = get_settings_page(db, "audio_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_response_format = existing_data.get("response_format") or None

        provider_row = None
        provider_type = None
        if merged_provider_id:
            provider_row = get_llm_provider(db, merged_provider_id)
            if not provider_row:
                raise HTTPException(
                    status_code=400,
                    detail="Selected audio generation provider was not found.",
                )
            provider_type = str(provider_row.provider or "").strip()
            if provider_type not in AUDIO_GENERATION_PROVIDER_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="Audio generation provider must support text-to-speech.",
                )

        if merged_model_name and not merged_provider_id:
            raise HTTPException(
                status_code=400,
                detail="Select an audio generation provider before choosing a model.",
            )

        if merged_model_name and provider_type:
            allowed_models = {
                option.get("value")
                for option in _get_audio_generation_model_options(
                    db, merged_provider_id
                )
                if option.get("value")
            }
            if merged_model_name not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail="Selected audio generation model is not supported by the chosen provider.",
                )

            capabilities = _get_audio_generation_model_capabilities_for_provider(
                merged_model_name,
                provider_type,
                provider_row,
            )
            allowed_voices = {
                str(voice).strip()
                for voice in capabilities.get("voices", [])
                if str(voice).strip()
            }
            allowed_formats = {
                str(fmt).strip().lower()
                for fmt in capabilities.get("response_formats", [])
                if str(fmt).strip()
            }
            voice_required = bool(capabilities.get("voice_required"))
            supports_custom_voice = bool(capabilities.get("supports_custom_voice"))

            if voice_required and not str(merged_voice or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="Select a voice for the chosen audio generation provider and model.",
                )

            if (
                merged_voice
                and allowed_voices
                and merged_voice not in allowed_voices
                and not supports_custom_voice
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Selected audio generation voice is not supported by the chosen model.",
                )

            if (
                merged_response_format
                and allowed_formats
                and merged_response_format not in allowed_formats
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Selected audio generation format is not supported by the chosen model.",
                )

        # Provider-specific values must not leak into another provider after
        # an admin switches the configured TTS backend.
        if provider_type != ProviderEnum.xai.value:
            for key in xai_setting_keys:
                normalized_payload[key] = None

    if page == "music_generation":
        provider_in_payload = "provider_id" in normalized_payload

        if "provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["provider_id"] = raw_provider.strip() or None
            elif raw_provider in ("", None):
                normalized_payload["provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["provider_id"] = str(raw_provider).strip() or None

        if "model_name" in normalized_payload:
            raw_model = normalized_payload.get("model_name")
            if isinstance(raw_model, str):
                normalized_payload["model_name"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["model_name"] = None
            elif raw_model is not None:
                normalized_payload["model_name"] = str(raw_model).strip() or None

        if "response_format" in normalized_payload:
            raw_format = normalized_payload.get("response_format")
            if isinstance(raw_format, str):
                normalized_payload["response_format"] = (
                    raw_format.strip().lower() or None
                )
            elif raw_format in ("", None):
                normalized_payload["response_format"] = None
            elif raw_format is not None:
                normalized_payload["response_format"] = (
                    str(raw_format).strip().lower() or None
                )

        if provider_in_payload and normalized_payload.get("provider_id") is None:
            if "model_name" not in normalized_payload:
                normalized_payload["model_name"] = None
            if "response_format" not in normalized_payload:
                normalized_payload["response_format"] = None
            if "enable_reference_images" not in normalized_payload:
                normalized_payload["enable_reference_images"] = False
            if "max_reference_images" not in normalized_payload:
                normalized_payload["max_reference_images"] = 3

        merged_provider_id = normalized_payload.get("provider_id")
        if merged_provider_id is None:
            existing_record = get_settings_page(db, "music_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_provider_id = existing_data.get("provider_id") or None

        merged_model_name = normalized_payload.get("model_name")
        if merged_model_name is None:
            existing_record = get_settings_page(db, "music_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_model_name = existing_data.get("model_name") or None

        merged_response_format = normalized_payload.get("response_format")
        if merged_response_format is None:
            existing_record = get_settings_page(db, "music_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_response_format = existing_data.get("response_format") or None

        provider_row = None
        provider_type = None
        if merged_provider_id:
            provider_row = get_llm_provider(db, merged_provider_id)
            if not provider_row:
                raise HTTPException(
                    status_code=400,
                    detail="Selected music generation provider was not found.",
                )
            provider_type = str(provider_row.provider or "").strip()
            if provider_type not in MUSIC_GENERATION_PROVIDER_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="Music generation provider must be a Google AI Studio provider.",
                )

        if merged_model_name and not merged_provider_id:
            raise HTTPException(
                status_code=400,
                detail="Select a music generation provider before choosing a model.",
            )

        if merged_model_name and provider_type:
            allowed_models = {
                option.get("value")
                for option in _get_music_generation_model_options(
                    db, merged_provider_id
                )
                if option.get("value")
            }
            if merged_model_name not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail="Selected music generation model is not supported by the chosen provider.",
                )

            capabilities = _get_music_generation_model_capabilities(
                merged_model_name,
                provider_type=provider_type,
            )
            allowed_formats = {
                str(fmt).strip().lower()
                for fmt in capabilities.get("response_formats", [])
                if str(fmt).strip()
            }

            if (
                merged_response_format
                and allowed_formats
                and merged_response_format not in allowed_formats
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Selected music generation format is not supported by the chosen model.",
                )

            if not capabilities.get("supports_reference_images"):
                normalized_payload["enable_reference_images"] = False
                if "max_reference_images" not in normalized_payload:
                    normalized_payload["max_reference_images"] = 3

    if page == "video_generation":
        provider_in_payload = "provider_id" in normalized_payload

        if "provider_id" in normalized_payload:
            raw_provider = normalized_payload.get("provider_id")
            if isinstance(raw_provider, str):
                normalized_payload["provider_id"] = raw_provider.strip() or None
            elif raw_provider in ("", None):
                normalized_payload["provider_id"] = None
            elif raw_provider is not None:
                normalized_payload["provider_id"] = str(raw_provider).strip() or None

        if "model_name" in normalized_payload:
            raw_model = normalized_payload.get("model_name")
            if isinstance(raw_model, str):
                normalized_payload["model_name"] = raw_model.strip() or None
            elif raw_model in ("", None):
                normalized_payload["model_name"] = None
            elif raw_model is not None:
                normalized_payload["model_name"] = str(raw_model).strip() or None

        if (
            provider_in_payload
            and normalized_payload.get("provider_id") is None
            and "model_name" not in normalized_payload
        ):
            normalized_payload["model_name"] = None

        merged_provider_id = normalized_payload.get("provider_id")
        if merged_provider_id is None:
            existing_record = get_settings_page(db, "video_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_provider_id = existing_data.get("provider_id") or None

        merged_model_name = normalized_payload.get("model_name")
        if merged_model_name is None:
            existing_record = get_settings_page(db, "video_generation")
            existing_data = (
                existing_record.data
                if existing_record and isinstance(existing_record.data, dict)
                else {}
            )
            merged_model_name = existing_data.get("model_name") or None

        provider_row = None
        provider_type = None
        if merged_provider_id:
            provider_row = get_llm_provider(db, merged_provider_id)
            if not provider_row:
                raise HTTPException(
                    status_code=400,
                    detail="Selected video generation provider was not found.",
                )
            provider_type = provider_row.provider
            if provider_type not in VIDEO_GENERATION_PROVIDER_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Video generation provider must be a custom OpenAI-compatible, "
                        "OpenRouter, Google AI Studio, or xAI provider."
                    ),
                )

        if merged_model_name and not merged_provider_id:
            raise HTTPException(
                status_code=400,
                detail="Select a video generation provider before choosing a model.",
            )

        if merged_model_name and provider_type:
            allowed_models = {
                option.get("value")
                for option in _get_video_generation_model_options(
                    db, merged_provider_id
                )
                if option.get("value")
            }
            if merged_model_name not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail="Selected video generation model is not supported by the chosen provider.",
                )

    if page == "deep_research":
        if "execution_mode" in normalized_payload:
            raw_mode = normalized_payload.get("execution_mode")
            if isinstance(raw_mode, str):
                normalized_payload["execution_mode"] = raw_mode.strip() or "custom"
            else:
                normalized_payload["execution_mode"] = (
                    str(raw_mode or "custom").strip() or "custom"
                )

        provider_in_payload = "native_provider_id" in normalized_payload
        native_model_in_payload = "native_model_name" in normalized_payload
        for key in (
            "model_id",
            "native_provider_id",
            "native_model_name",
            "websearch_search_provider",
            "websearch_scrape_provider",
        ):
            if key not in normalized_payload:
                continue
            raw_value = normalized_payload.get(key)
            if isinstance(raw_value, str):
                normalized_payload[key] = raw_value.strip() or None
            elif raw_value in ("", None):
                normalized_payload[key] = None
            elif raw_value is not None:
                normalized_payload[key] = str(raw_value).strip() or None

        # Provider and model are saved by separate auto-save requests. Any
        # provider change invalidates the previously selected model, including
        # switching back to the empty provider. Clearing it here lets the
        # provider request succeed before the UI reloads provider-specific
        # model options.
        if provider_in_payload and not native_model_in_payload:
            normalized_payload["native_model_name"] = None

        merged_execution_mode = normalized_payload.get("execution_mode")
        existing_record = get_settings_page(db, "deep_research")
        existing_data = (
            existing_record.data
            if existing_record and isinstance(existing_record.data, dict)
            else {}
        )
        existing_data = _normalize_stored_deep_research_settings(existing_data)
        if merged_execution_mode is None:
            merged_execution_mode = existing_data.get("execution_mode") or "custom"
        if merged_execution_mode not in {"custom", "native"}:
            raise HTTPException(
                status_code=400,
                detail="Deep Research execution mode must be custom or native.",
            )

        if provider_in_payload:
            merged_provider_id = normalized_payload.get("native_provider_id")
        else:
            merged_provider_id = existing_data.get("native_provider_id") or None

        if "native_model_name" in normalized_payload:
            merged_native_model_name = normalized_payload.get("native_model_name")
        else:
            merged_native_model_name = existing_data.get("native_model_name") or None

        if merged_execution_mode == "native":
            # Selecting native mode is an intentional intermediate state: the
            # provider control only becomes visible after this update. Repair
            # a stale model-without-provider value instead of rejecting the
            # mode switch and trapping the administrator in custom mode.
            if not merged_provider_id and not native_model_in_payload:
                normalized_payload["native_model_name"] = None
                merged_native_model_name = None

            provider_row = None
            provider_type = None
            if merged_provider_id:
                provider_row = get_llm_provider(db, merged_provider_id)
                if not provider_row:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected deep research provider was not found.",
                    )
                provider_type = str(provider_row.provider or "").strip()
                if provider_type not in DEEP_RESEARCH_PROVIDER_TYPES:
                    raise HTTPException(
                        status_code=400,
                        detail="Native deep research provider must be Google AI Studio.",
                    )

            if merged_native_model_name and not merged_provider_id:
                raise HTTPException(
                    status_code=400,
                    detail="Select a deep research provider before choosing a model.",
                )

            if merged_native_model_name and provider_type:
                allowed_models = {
                    option.get("value")
                    for option in _get_deep_research_model_options(
                        db, merged_provider_id
                    )
                    if option.get("value")
                }
                if merged_native_model_name not in allowed_models:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected deep research model is not supported by the chosen provider.",
                    )
        else:
            merged_model_id = normalized_payload.get("model_id")
            if merged_model_id is None:
                merged_model_id = existing_data.get("model_id") or None

            if merged_model_id:
                custom_model = get_active_model(db, merged_model_id)
                if not custom_model:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected custom Deep Research model was not found.",
                    )
                if (
                    str(custom_model.provider or "").strip()
                    not in CUSTOM_DEEP_RESEARCH_MODEL_PROVIDER_TYPES
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Selected custom Deep Research model provider is not supported.",
                    )
            merged_search_provider = normalized_payload.get("websearch_search_provider")
            if merged_search_provider is None:
                merged_search_provider = (
                    existing_data.get("websearch_search_provider") or None
                )

            merged_scrape_provider = normalized_payload.get("websearch_scrape_provider")
            if merged_scrape_provider is None:
                merged_scrape_provider = (
                    existing_data.get("websearch_scrape_provider") or None
                )

            search_options = _get_websearch_provider_options_with_metadata(db, "search")
            scrape_options = _get_websearch_provider_options_with_metadata(db, "scrape")
            search_option_map = {
                str(option.get("value") or ""): option for option in search_options
            }
            scrape_option_values = {
                str(option.get("value") or "") for option in scrape_options
            }

            if (
                merged_search_provider
                and merged_search_provider not in search_option_map
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Selected Deep Research web search provider is invalid.",
                )

            if (
                merged_scrape_provider
                and merged_scrape_provider not in scrape_option_values
            ):
                search_option = search_option_map.get(str(merged_scrape_provider))
                has_combined = bool(
                    (search_option or {}).get("metadata", {}).get("has_combined")
                )
                if not has_combined:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected Deep Research web scrape provider is invalid.",
                    )

    changed_keys: List[str] = []

    if (
        page in {"login_general", "groups_defaults"}
        and "default_user_group" in normalized_payload
    ):
        raw_value = normalized_payload.get("default_user_group")
        normalized_value = "default" if raw_value in (None, "") else str(raw_value)
        allowed_groups = {option["value"] for option in _get_group_options(db)}
        if normalized_value not in allowed_groups:
            raise HTTPException(
                status_code=400,
                detail="Selected default user group does not exist.",
            )
        normalized_payload["default_user_group"] = normalized_value

    if page == "login_customization":
        for field_key, default_value in LOGIN_CUSTOMIZATION_DEFAULTS.items():
            if field_key not in normalized_payload:
                continue
            raw_value = normalized_payload.get(field_key)
            if raw_value is None:
                normalized_payload[field_key] = default_value
                continue
            if isinstance(raw_value, str):
                normalized_payload[field_key] = raw_value.strip() or default_value
                continue
            normalized_payload[field_key] = str(raw_value).strip() or default_value

    if page == "login_social":
        _normalize_secret_fields(
            page_key="login_social",
            normalized_payload=normalized_payload,
            secret_fields=(
                "google_client_secret",
                "github_client_secret",
                "slack_client_secret",
                "microsoft_client_secret",
                "apple_private_key",
            ),
            db=db,
        )
        _validate_login_social_apple_private_key(normalized_payload, db)

    if page == "login_enterprise_sso":
        allowed_groups = {option["value"] for option in _get_group_options(db)}
        for group_field in (
            "scim_default_group",
            "saml_default_group",
            "oidc_default_group",
        ):
            if group_field in normalized_payload:
                raw_value = normalized_payload.get(group_field)
                normalized_value = (
                    "default" if raw_value in (None, "") else str(raw_value)
                )
                if normalized_value not in allowed_groups:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Selected group for '{group_field}' does not exist.",
                    )
                normalized_payload[group_field] = normalized_value

        _normalize_secret_fields(
            page_key="login_enterprise_sso",
            normalized_payload=normalized_payload,
            secret_fields=(
                "oidc_client_secret",
                "scim_bearer_token",
                "scim_previous_bearer_token",
            ),
            db=db,
        )

    if page == "login_ldap":
        if "ldap_default_group" in normalized_payload:
            raw_value = normalized_payload.get("ldap_default_group")
            normalized_value = "default" if raw_value in (None, "") else str(raw_value)
            allowed_groups = {option["value"] for option in _get_group_options(db)}
            if normalized_value not in allowed_groups:
                raise HTTPException(
                    status_code=400,
                    detail="Selected LDAP default group does not exist.",
                )
            normalized_payload["ldap_default_group"] = normalized_value

        _normalize_secret_fields(
            page_key="login_ldap",
            normalized_payload=normalized_payload,
            secret_fields=("ldap_bind_password",),
            db=db,
        )

    if page == "login_general":
        _normalize_secret_fields(
            page_key="login_general",
            normalized_payload=normalized_payload,
            secret_fields=("smtp_password",),
            db=db,
        )

    if page == "notifications" and "webhook_url" in normalized_payload:
        normalized_payload["webhook_url"] = validate_and_normalize_public_webhook_url(
            normalized_payload.get("webhook_url")
        )

    if normalized_payload:
        settings_record = settings_record_override or get_settings_page(
            db, storage_page
        )
        if not settings_record:
            raise HTTPException(status_code=404, detail="Settings page not found")

        if not isinstance(settings_record.data, dict):
            settings_record.data = {}

        allowed_fields = set(model.model_fields.keys())
        unknown_keys = set(normalized_payload.keys()) - allowed_fields
        if unknown_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown fields for '{page}': {', '.join(sorted(unknown_keys))}",
            )

        # Only copy keys that are actually persisted. Filling every missing key
        # with ``None`` overrides Pydantic defaults and breaks partial updates
        # whenever a new non-nullable setting is introduced. Missing keys now
        # correctly use their model defaults and are backfilled on the next
        # successful write.
        current_values = {
            key: settings_record.data[key]
            for key in allowed_fields
            if key in settings_record.data
        }
        if page == "login_general":
            # Normalize the stored side of a partial update before applying the
            # administrator's payload. A supplied unsafe value still overrides
            # this dictionary and fails strict model validation below.
            current_values = normalize_stored_login_general_settings(current_values)
        if page == "login_enterprise_sso":
            # Upgrade legacy shapes before merging the administrator's partial
            # update. Provider activation remains independent of readiness.
            current_values = _normalize_stored_login_enterprise_sso_settings(
                current_values
            )
        if page == "deep_research":
            # Normalize only persisted data here. Values supplied by the
            # administrator remain subject to strict Pydantic validation.
            current_values = _normalize_stored_deep_research_settings(current_values)
        merged_values = {**current_values, **normalized_payload}
        if page == "security":
            _sync_security_ip_policy_compatibility_flags(
                normalized_payload, merged_values
            )

        # Validate and normalize before lockout checks. This makes malformed
        # policy values produce their own actionable response instead of being
        # mistaken for an IP-country lookup or allowlist failure.
        try:
            validated = model(**merged_values)
        except ValidationError as exc:
            if page == "security":
                country_error = _security_country_code_error_detail(exc, merged_values)
                if country_error:
                    raise HTTPException(status_code=400, detail=country_error) from exc
                ip_address_error = _security_ip_address_error_detail(exc, merged_values)
                if ip_address_error:
                    raise HTTPException(
                        status_code=400, detail=ip_address_error
                    ) from exc
            # Invalid settings are a client/data validation problem, not an
            # unhandled server failure. Keep the response stable and
            # translatable while retaining the detailed exception in the
            # server-side traceback chain for diagnostics.
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "admin_settings_validation_failed",
                    "message": "Validation failed.",
                },
            ) from exc

        validated_data = validated.model_dump()
        if page == "security" and IP_RESTRICTION_POLICY_KEYS.intersection(
            normalized_payload
        ):
            _assert_security_ip_policy_keeps_admin_access(
                validated_data,
                request_client_ip=request_client_ip,
                db=db,
                api_key_updates=api_key_updates,
            )
        if page == "login_ldap":
            transport_policy = get_ldap_transport_security_policy(validated_data)
            if not transport_policy.allows_bind:
                raise HTTPException(
                    status_code=400, detail=LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL
                )
        validated_data = preserve_masked_sensitive_settings_page_data(
            page,
            settings_record.data,
            validated_data,
        )
        _, validated_data = ensure_sensitive_settings_page_encrypted(
            page,
            validated_data,
            treat_values_as_plaintext=False,
        )

        changed_settings = [
            key
            for key in allowed_fields
            if current_values.get(key) != validated_data.get(key)
        ]

        if changed_settings:
            settings_record.data.update(
                {key: validated_data[key] for key in allowed_fields}
            )
            settings_record.updated_at = datetime.now(timezone.utc)
            persist_settings_json_row(db, settings_record, mark_modified=flag_modified)
            invalidate_settings_cache()
            if page == "general" and "public_url" in changed_settings:
                # The CORS middleware otherwise refreshes on its short TTL;
                # invalidate this process immediately after the committed write.
                from app.middleware.cors import invalidate_cors_allowed_origins

                invalidate_cors_allowed_origins()
            changed_keys.extend(changed_settings)

    if api_key_updates:
        changed_keys.extend(_update_api_key_settings(db, api_key_updates))

    return changed_keys


def _get_public_model_options(db: Session) -> List[Dict[str, str]]:
    """Get public model options."""
    rows = list_active_models(db)
    options: List[Dict[str, str]] = []
    for row in rows:
        access = row.access or {}
        if isinstance(access, dict) and access.get("everyone"):
            label = (row.name or row.model_name or row.id or "").strip() or row.id
            options.append({"value": row.id, "label": label})
    return options


def _get_admin_managed_model_options(db: Session) -> List[Dict[str, str]]:
    """Get active administrator-managed model options.

    User-managed models share the main ``models`` table so they can participate
    in the owner's normal model picker. They are not valid choices for global
    administrator settings, however, because those settings apply to every
    user and cannot safely point at one user's private runtime.
    """
    rows = list_active_models(db)
    options: List[Dict[str, str]] = []
    for row in rows:
        metadata = row.meta if isinstance(getattr(row, "meta", None), dict) else {}
        if metadata.get("user_managed") is True:
            continue
        label = (row.name or row.model_name or row.id or "").strip() or row.id
        options.append({"value": row.id, "label": label})
    return options


def _get_websearch_provider_options(
    db: Session, provider_type: str
) -> List[Dict[str, str]]:
    """Get websearch provider options."""
    if provider_type == "search":
        rows = list_websearch_providers_search(db)
    elif provider_type == "scrape":
        rows = list_websearch_providers_scrape(db)
    else:
        rows = []

    options: List[Dict[str, str]] = []
    for row in rows:
        label = (row.name or row.provider or row.id or "").strip() or row.id
        options.append({"value": row.id, "label": label})
    return options


def _get_searxng_search_provider_options(db: Session) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = []
    for row in list_websearch_providers_search(db):
        if str(getattr(row, "provider", "") or "").strip() != "searxng":
            continue
        label = (row.name or row.provider or row.id or "").strip() or row.id
        options.append({"value": row.id, "label": label})
    return options
