from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from app.admin.settings.schema_categories.audio_generation import (
    AudioGenerationSettings,
    audio_generation_schema,
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
    dictation_schema,
)
from app.admin.settings.schema_categories.general import (
    GeneralSettings,
    general_schema,
)
from app.admin.settings.schema_categories.image_generation import (
    ImageGenerationSettings,
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
    music_generation_schema,
)
from app.admin.settings.schema_categories.notifications import (
    NotificationSettings,
    notification_settings_schema,
)
from app.admin.settings.schema_categories.read_aloud import (
    ReadAloudSettings,
    read_aloud_schema,
)
from app.admin.settings.schema_categories.realtime import (
    RealtimeSettings,
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
    video_generation_schema,
)
from app.admin.settings.schema_categories.weather_tool import (
    WeatherToolSettings,
    weather_tool_schema,
)
from app.auth.password_policy import normalize_stored_login_general_settings
from app.settings.defaults import DEFAULT_SETTINGS
from app.utils.schemas import FieldSchema, Sections


class AboutSettings(BaseModel):
    privacy_policy: str = ""
    privacy_policy_revision: int = Field(default=1, ge=1)
    privacy_policy_notice_mode: Literal["none", "modal"] = "none"
    privacy_policy_notice_message_html: str = ""
    privacy_policy_notice_updated_at: str = ""
    terms_of_service: str = ""
    terms_of_service_revision: int = Field(default=1, ge=1)
    terms_of_service_updated_at: str = ""


class StatusSettings(BaseModel):
    internet_connectivity: bool = True
    latest_version_notified: str = ""


class StatesSettings(BaseModel):
    server_setup: bool = False


class UserStatisticsSettings(BaseModel):
    enabled: bool = False
    regulatory_confirmed: bool = False
    tracked_user_ids: list[str] = Field(default_factory=list)
    track_all_users: bool = False


class IPAddressStatisticsSettings(BaseModel):
    enabled: bool = False
    regulatory_confirmed: bool = False
    regulatory_justification: str = ""
    policy_reference: str = ""
    retention_policy: str = ""
    retention_purpose: str = ""
    retention_days: int = Field(default=90, ge=1)


SETTINGS_PAGE_MODELS: dict[str, type[BaseModel]] = {
    "general": GeneralSettings,
    "login_general": LoginGeneralSettings,
    "login_customization": LoginCustomizationSettings,
    "login_social": LoginSocialSettings,
    "login_enterprise_sso": LoginEnterpriseSSOSettings,
    "login_ldap": LoginLDAPSettings,
    "security": SecuritySettings,
    "users": UsersSettings,
    "models": ModelDefaultsSettings,
    "dictation": DictationSettings,
    "read_aloud": ReadAloudSettings,
    "realtime": RealtimeSettings,
    "about": AboutSettings,
    "slide_presentation": SlidePresentationSettings,
    "weather_tool": WeatherToolSettings,
    "code_execution": CodeExecutionSettings,
    "image_generation": ImageGenerationSettings,
    "audio_generation": AudioGenerationSettings,
    "music_generation": MusicGenerationSettings,
    "video_generation": VideoGenerationSettings,
    "deep_research": DeepResearchSettings,
    "status": StatusSettings,
    "notifications": NotificationSettings,
    "states": StatesSettings,
    "user_statistics": UserStatisticsSettings,
    "ip_address_statistics": IPAddressStatisticsSettings,
}


def get_settings_page_model(page_name: str) -> type[BaseModel] | None:
    """Return the validation model for one settings page."""
    return SETTINGS_PAGE_MODELS.get(page_name)


def validate_settings_page_values(
    page_name: str,
    payload: dict[str, Any],
    *,
    current_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a partial settings payload against the page schema."""
    model = get_settings_page_model(page_name)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No settings schema registered for page '{page_name}'.",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Settings payload for page '{page_name}' must be an object.",
        )

    allowed_fields = set(model.model_fields.keys())
    unknown_keys = sorted(set(payload.keys()) - allowed_fields)
    if unknown_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown fields for '{page_name}': {', '.join(unknown_keys)}",
        )

    merged_values = deepcopy(DEFAULT_SETTINGS.get(page_name, {}))
    if isinstance(current_values, dict):
        normalized_current_values = current_values
        if page_name == "login_general":
            # Only normalize the persisted side of the merge. An explicit
            # payload value below the floor is applied afterward and rejected.
            normalized_current_values = normalize_stored_login_general_settings(
                current_values
            )
        merged_values.update(normalized_current_values)
    merged_values.update(payload)

    try:
        validated = model(**merged_values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_format_settings_validation_error(page_name, exc),
        ) from exc

    validated_values = validated.model_dump()
    field_map = SETTINGS_PAGE_FIELD_SCHEMAS.get(page_name, {})
    try:
        for field_key, field in field_map.items():
            if field_key not in validated_values:
                continue
            validated_values[field_key] = _validate_schema_field_value(field, validated_values[field_key])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{page_name}.{field_key}: {exc}",
        ) from exc

    return validated_values


def _format_settings_validation_error(page_name: str, exc: ValidationError) -> str:
    errors: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        if location:
            errors.append(f"{page_name}.{location}: {error.get('msg', 'Invalid value')}")
        else:
            errors.append(f"{page_name}: {error.get('msg', 'Invalid value')}")
    return "; ".join(errors) or f"Invalid settings payload for '{page_name}'."


def _field_map_from_sections(schema: Sections | None) -> dict[str, FieldSchema]:
    if not schema or not getattr(schema, "sections", None):
        return {}

    field_map: dict[str, FieldSchema] = {}
    for section in schema.sections:
        for field in section.fields:
            if isinstance(field.key, str) and field.key:
                field_map[field.key] = field
    return field_map


def _validate_schema_field_value(field: FieldSchema, value: Any) -> Any:
    if value is None:
        return value

    if field.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{field.label} must be true or false")
        return value

    if field.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field.label} must be a valid number")
        attributes = field.attributes
        if attributes:
            if attributes.min is not None and value < attributes.min:
                raise ValueError(f"{field.label} must be at least {attributes.min}")
            if attributes.max is not None and value > attributes.max:
                raise ValueError(f"{field.label} must be at most {attributes.max}")
        return value

    if field.type in {"string", "textarea"}:
        if not isinstance(value, str):
            raise ValueError(f"{field.label} must be a string")
        return value

    if field.type == "select":
        if field.multiple:
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"{field.label} must be a list of strings")
            allowed_values = {option.value for option in field.options or []}
            if allowed_values and any(item not in allowed_values for item in value):
                raise ValueError(f"{field.label} contains an unsupported option")
            return value

        if not isinstance(value, str):
            raise ValueError(f"{field.label} must be a string")
        allowed_values = {option.value for option in field.options or []}
        if value and allowed_values and value not in allowed_values:
            raise ValueError(f"{field.label} contains an unsupported option")
        return value

    if field.type in {"string_list", "select_multi", "context_files"}:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field.label} must be a list of strings")
        return value

    if field.type == "access_rules":
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"{field.label} must be a list of rule objects")
        return value

    if field.type == "boolean_map":
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(map_value, bool)
            for key, map_value in value.items()
        ):
            raise ValueError(f"{field.label} must be an object of boolean values")
        return value

    if field.type == "json":
        if not isinstance(value, (dict, list)):
            raise ValueError(f"{field.label} must be a JSON object or array")
        return value

    return value


SETTINGS_PAGE_FIELD_SCHEMAS: dict[str, dict[str, FieldSchema]] = {
    "general": _field_map_from_sections(general_schema),
    "login_general": _field_map_from_sections(login_general_schema),
    "login_customization": _field_map_from_sections(login_customization_schema),
    "login_social": _field_map_from_sections(login_social_schema),
    "login_enterprise_sso": _field_map_from_sections(login_enterprise_sso_schema),
    "login_ldap": _field_map_from_sections(login_ldap_schema),
    "security": _field_map_from_sections(security_schema),
    "users": _field_map_from_sections(users_schema),
    "models": _field_map_from_sections(models_schema),
    "dictation": _field_map_from_sections(dictation_schema),
    "read_aloud": _field_map_from_sections(read_aloud_schema),
    "realtime": _field_map_from_sections(realtime_schema),
    "notifications": _field_map_from_sections(notification_settings_schema),
    "slide_presentation": _field_map_from_sections(slide_presentation_schema),
    "weather_tool": _field_map_from_sections(weather_tool_schema),
    "code_execution": _field_map_from_sections(code_execution_schema),
    "image_generation": _field_map_from_sections(image_generation_schema),
    "audio_generation": _field_map_from_sections(audio_generation_schema),
    "music_generation": _field_map_from_sections(music_generation_schema),
    "video_generation": _field_map_from_sections(video_generation_schema),
    "deep_research": _field_map_from_sections(deep_research_schema),
}
