import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.admin.notifications.schemas import AdminDashboardResponse
from app.admin.settings.models import (
    get_llm_provider,
    list_llm_providers,
)
from app.admin.settings.schema_categories.admin import (
    AdminFileStorageStatisticsResponse,
    AdminModelOptionsResponse,
    AdminPrivacyPolicyUpdate,
    AdminSettingsSchemaQuery,
    AdminTermsOfServiceUpdate,
)
from app.admin.settings.utils import (
    get_admin_settings_dashboard_data,
    get_admin_settings_schema_response,
    get_live_transcription_model_options_response,
    get_realtime_model_options_response,
    get_transcription_model_options_response,
    raise_admin_provider_helper_error,
    update_admin_settings_values_for_page,
)
from app.auth.ldap_transport import (
    LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY,
)
from app.dependencies import get_db, get_db_log, verified_admin
from app.files.statistics import get_admin_file_storage_statistics
from app.llm.schemas import ProviderEnum, resolve_provider_icon
from app.llm.speech import (
    OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES,
    TTS_PROVIDER_TYPES,
)
from app.logging.models import (
    create_audit_log,
    get_audit_request_ip,
)
from app.settings.models import (
    get_settings_page,
    get_settings_page_data,
)
from app.users.deletion_policy import get_auth_log_user_deletion_retention_policy
from app.users.utils import get_audit_log_user_deletion_retention_policy
from app.utils.ip_restrictions import (
    IP_RESTRICTIONS_DISABLE_ENV,
    ip_restrictions_disabled_by_environment,
)
from app.utils.schemas import OperationResult, Option
from app.utils.utils import update_privacy_policy, update_terms_of_service

logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.get("/dashboard", response_model=AdminDashboardResponse)
def admin_get_settings_dashboard_data(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get admin settings dashboard data."""
    result = get_admin_settings_dashboard_data(db, db_log)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_SETTINGS_DASHBOARD_DATA",
        details={
            "user_id": admin_user.id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return result


@admin_router.get(
    "/file-storage/statistics", response_model=AdminFileStorageStatisticsResponse
)
def admin_get_file_storage_statistics_route(
    request: Request,
    search: str | None = Query(None),
    sort_field: str = Query("storage_bytes"),
    sort_direction: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get aggregate file-storage usage by user for admins."""
    result = get_admin_file_storage_statistics(
        db,
        search=search,
        sort_field=sort_field,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="FILE_STORAGE_STATISTICS_VIEWED",
        details={
            "has_search": bool(str(search or "").strip()),
            "search_length": len(str(search or "").strip()),
            "sort_field": result.get("sort_field"),
            "sort_direction": result.get("sort_direction"),
            "limit": limit,
            "offset": offset,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return result


@admin_router.get("/schema")
def admin_settings_schema(
    request: Request,
    query: AdminSettingsSchemaQuery = Depends(),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get admin settings schema."""
    result = get_admin_settings_schema_response(
        page=query.page,
        include_values=query.include_values,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_ADMIN_SETTINGS_SCHEMA",
        details={
            "page": query.page,
            "include_values": query.include_values,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.get("/ip-restrictions/status")
def admin_ip_restrictions_status(
    request: Request, db: Session = Depends(get_db), admin_user=Depends(verified_admin)
):
    """Return deployment-level IP restriction safety state for the admin UI."""
    return {
        "disabled_by_environment": ip_restrictions_disabled_by_environment(),
        "environment_variable": IP_RESTRICTIONS_DISABLE_ENV,
        "current_admin_ip": get_audit_request_ip(request, db),
    }


@admin_router.post("/values/", response_model=OperationResult)
def update_admin_settings_values(
    page: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update admin settings values."""
    retention_keys = {
        "auth_logs_retention_after_user_delete_mode",
        "auth_logs_retention_delete_after_days",
        "audit_logs_retention_after_user_delete_mode",
        "audit_logs_retention_delete_after_days",
    }
    previous_retention_policy = None
    # Updating a security page can backfill retention defaults even when the
    # incoming payload omits those keys, so capture the pre-update policy for
    # every security-page mutation.
    if page == "security":
        previous_retention_policy = {
            "authentication_logs": get_auth_log_user_deletion_retention_policy(db),
            "audit_logs_and_admin_notifications": get_audit_log_user_deletion_retention_policy(
                db
            ),
        }
    changed_keys = update_admin_settings_values_for_page(
        page=page,
        payload=payload,
        db=db,
        request_client_ip=get_audit_request_ip(request, db),
    )

    if not changed_keys:
        return OperationResult(status="success")
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_ADMIN_SETTINGS_VALUES",
        details={
            "page": page,
            "updated": changed_keys,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    changed_retention_keys = retention_keys.intersection(changed_keys)
    if changed_retention_keys:
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="USER_DELETION_RETENTION_POLICY_UPDATED",
            details={
                "updated": sorted(changed_retention_keys),
                "previous": previous_retention_policy,
                "effective": {
                    "authentication_logs": get_auth_log_user_deletion_retention_policy(
                        db
                    ),
                    "audit_logs_and_admin_notifications": get_audit_log_user_deletion_retention_policy(
                        db
                    ),
                },
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="security",
        )

    if page == "login_ldap" and LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY in changed_keys:
        persisted_ldap_settings = get_settings_page_data(db, "login_ldap")
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="LDAP_INSECURE_PLAINTEXT_BIND_OVERRIDE_UPDATED",
            details={
                "enabled": bool(
                    persisted_ldap_settings.get(
                        LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY, False
                    )
                ),
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="security",
        )

    return OperationResult(status="success")


@admin_router.get(
    "/settings/dictation/transcription/models",
    response_model=AdminModelOptionsResponse,
    dependencies=[Depends(verified_admin)],
)
def admin_list_transcription_models(
    provider_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List available transcription models."""
    return get_transcription_model_options_response(
        db=db,
        provider_id=provider_id,
    )


@admin_router.get(
    "/settings/dictation/live-transcription/models",
    response_model=AdminModelOptionsResponse,
    dependencies=[Depends(verified_admin)],
)
def admin_list_live_transcription_models(
    provider_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List models supported by Omlorix's live dictation transport."""
    return get_live_transcription_model_options_response(
        db=db,
        provider_id=provider_id,
    )


@admin_router.get(
    "/settings/realtime/models",
    response_model=AdminModelOptionsResponse,
    dependencies=[Depends(verified_admin)],
)
def admin_list_realtime_models(
    provider_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List available real-time models."""
    return get_realtime_model_options_response(
        db=db,
        provider_id=provider_id,
    )


@admin_router.get("/settings/image_generation")
def admin_get_image_generation_settings(
    request: Request,
    include_values: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get image generation settings."""
    result = get_admin_settings_schema_response(
        page="image_generation",
        include_values=include_values,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_IMAGE_GENERATION_SETTINGS",
        details={"include_values": include_values},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.patch("/settings/image_generation", response_model=OperationResult)
def admin_update_image_generation_settings(
    payload: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update image generation settings."""
    changed_keys = update_admin_settings_values_for_page(
        page="image_generation",
        payload=payload,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_IMAGE_GENERATION_SETTINGS",
        details={"updated": changed_keys},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")


@admin_router.get("/settings/audio_generation")
def admin_get_audio_generation_settings(
    request: Request,
    include_values: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get audio generation settings."""
    result = get_admin_settings_schema_response(
        page="audio_generation",
        include_values=include_values,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_AUDIO_GENERATION_SETTINGS",
        details={"include_values": include_values},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.patch("/settings/audio_generation", response_model=OperationResult)
def admin_update_audio_generation_settings(
    payload: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update audio generation settings."""
    changed_keys = update_admin_settings_values_for_page(
        page="audio_generation",
        payload=payload,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_AUDIO_GENERATION_SETTINGS",
        details={"updated": changed_keys},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")


@admin_router.get("/settings/audio_generation/providers")
def admin_list_audio_generation_providers(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List audio generation providers."""

    supported_types = set(TTS_PROVIDER_TYPES)
    providers = list_llm_providers(db, provider_types=supported_types)

    result = []
    for provider in providers:
        result.append(
            {
                "id": provider.id,
                "name": provider.name,
                "provider": provider.provider,
                "icon": resolve_provider_icon(
                    getattr(provider, "provider", None),
                    getattr(provider, "icon", None),
                ),
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_AUDIO_GENERATION_PROVIDERS",
        details={},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"providers": result}


@admin_router.get("/settings/audio_generation/models")
def admin_list_audio_generation_models(
    provider_id: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List audio generation models."""

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider_type = str(provider.provider or "").strip()
    if provider_type not in TTS_PROVIDER_TYPES:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for audio generation"
        )

    try:
        if provider_type in OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES:
            from app.llm.openai.text_to_speech import get_audio_generation_schema_part_1

            schema = get_audio_generation_schema_part_1(db, provider_id)
        elif provider_type == ProviderEnum.openrouter.value:
            from app.llm.openrouter.audio_generation import (
                get_audio_generation_schema_part_1,
            )

            schema = get_audio_generation_schema_part_1(db, provider_id)
        elif provider_type == ProviderEnum.google_aistudio.value:
            from app.llm.google_aistudio.text_to_speech import (
                get_audio_generation_schema_part_1,
            )

            schema = get_audio_generation_schema_part_1(db, provider_id)
        elif provider_type == ProviderEnum.xai.value:
            from app.llm.xai.text_to_speech import get_audio_generation_schema_part_1

            schema = get_audio_generation_schema_part_1(db, provider_id)
        else:
            from app.llm.elevenlabs.text_to_speech import (
                get_audio_generation_schema_part_1,
            )

            schema = get_audio_generation_schema_part_1(db, provider_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to list audio generation models",
            action="LIST_AUDIO_GENERATION_MODELS",
            provider_id=provider_id,
            provider_type=provider_type,
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_AUDIO_GENERATION_MODELS",
        details={"provider_id": provider_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/audio_generation/model_settings")
def admin_get_audio_generation_model_settings(
    provider_id: str = Query(...),
    model_name: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get audio generation model settings."""

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider_type = str(provider.provider or "").strip()
    if provider_type not in TTS_PROVIDER_TYPES:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for audio generation"
        )

    try:
        if provider_type in OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES:
            from app.llm.openai.text_to_speech import get_audio_generation_schema_part_2

            schema = get_audio_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == ProviderEnum.openrouter.value:
            from app.llm.openrouter.audio_generation import (
                get_audio_generation_schema_part_2,
            )

            schema = get_audio_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == ProviderEnum.google_aistudio.value:
            from app.llm.google_aistudio.text_to_speech import (
                get_audio_generation_schema_part_2,
            )

            schema = get_audio_generation_schema_part_2(model_name)
        elif provider_type == ProviderEnum.xai.value:
            from app.llm.xai.text_to_speech import get_audio_generation_schema_part_2

            schema = get_audio_generation_schema_part_2(
                model_name,
                provider=provider,
            )
        else:
            from app.llm.elevenlabs.text_to_speech import (
                get_audio_generation_schema_part_2,
            )

            schema = get_audio_generation_schema_part_2(
                api_key=provider.api_key,
                model_name=model_name,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to get audio generation model settings",
            action="GET_AUDIO_GENERATION_MODEL_SETTINGS",
            provider_id=provider_id,
            provider_type=provider_type,
            model_name=model_name,
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_AUDIO_GENERATION_MODEL_SETTINGS",
        details={"provider_id": provider_id, "model_name": model_name},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/audio_generation/voices")
def admin_search_audio_generation_voices(
    provider_id: str = Query(...),
    search: str | None = Query(None),
    page_size: int = Query(24, ge=1, le=100),
    next_page_token: str | None = Query(None),
    voice_ids: str | None = Query(None),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Search audio generation voices."""
    from app.llm.elevenlabs.text_to_speech import search_elevenlabs_voices
    from app.llm.xai.text_to_speech import search_xai_voices

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider_type = str(provider.provider or "").strip()
    if provider_type not in {
        ProviderEnum.elevenlabs.value,
        ProviderEnum.xai.value,
    }:
        raise HTTPException(
            status_code=400,
            detail="Voice search is only supported for ElevenLabs and xAI providers",
        )

    requested_voice_ids = [
        value.strip()
        for value in str(voice_ids or "").split(",")
        if value and value.strip()
    ]

    try:
        if provider_type == ProviderEnum.xai.value:
            payload = search_xai_voices(
                provider,
                search=search,
                page_size=page_size,
                next_page_token=next_page_token,
                voice_ids=requested_voice_ids or None,
            )
        else:
            payload = search_elevenlabs_voices(
                api_key=provider.api_key,
                search=search,
                next_page_token=next_page_token,
                page_size=page_size,
                voice_ids=requested_voice_ids or None,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to list provider voices",
            action="LIST_AUDIO_GENERATION_VOICES",
            provider_id=provider_id,
            provider_type=provider_type,
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_AUDIO_GENERATION_VOICES",
        details={
            "provider_id": provider_id,
            "search": search,
            "page_size": page_size,
            "has_next_page_token": bool(next_page_token),
            "voice_ids_count": len(requested_voice_ids),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return payload


@admin_router.get("/settings/music_generation")
def admin_get_music_generation_settings(
    request: Request,
    include_values: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get music generation settings."""
    result = get_admin_settings_schema_response(
        page="music_generation",
        include_values=include_values,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_MUSIC_GENERATION_SETTINGS",
        details={"include_values": include_values},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.patch("/settings/music_generation", response_model=OperationResult)
def admin_update_music_generation_settings(
    payload: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update music generation settings."""
    changed_keys = update_admin_settings_values_for_page(
        page="music_generation",
        payload=payload,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_MUSIC_GENERATION_SETTINGS",
        details={"updated": changed_keys},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")


@admin_router.get("/settings/music_generation/providers")
def admin_list_music_generation_providers(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List music generation providers."""

    providers = list_llm_providers(
        db,
        provider_types={ProviderEnum.google_aistudio.value},
    )

    result = []
    for provider in providers:
        result.append(
            {
                "id": provider.id,
                "name": provider.name,
                "provider": provider.provider,
                "icon": resolve_provider_icon(
                    getattr(provider, "provider", None),
                    getattr(provider, "icon", None),
                ),
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_MUSIC_GENERATION_PROVIDERS",
        details={},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"providers": result}


@admin_router.get("/settings/music_generation/models")
def admin_list_music_generation_models(
    provider_id: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List music generation models."""
    from app.llm.google_aistudio.music_generation import (
        get_music_generation_schema_part_1,
    )

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if str(provider.provider or "").strip() != ProviderEnum.google_aistudio.value:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for music generation"
        )

    try:
        schema = get_music_generation_schema_part_1(db, provider_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to list music generation models",
            action="LIST_MUSIC_GENERATION_MODELS",
            provider_id=provider_id,
            provider_type=str(provider.provider or "").strip(),
        )

    ip_address = get_audit_request_ip(request, db)
    user_agent = (
        request.headers.get("user-agent") if request and request.headers else None
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_MUSIC_GENERATION_MODELS",
        details={"provider_id": provider_id},
        ip_address=ip_address,
        user_agent=user_agent,
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/music_generation/model_settings")
def admin_get_music_generation_model_settings(
    provider_id: str = Query(...),
    model_name: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get music generation model settings."""
    from app.llm.google_aistudio.music_generation import (
        get_music_generation_schema_part_2,
    )

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if str(provider.provider or "").strip() != ProviderEnum.google_aistudio.value:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for music generation"
        )

    try:
        schema = get_music_generation_schema_part_2(model_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to get music generation model settings",
            action="GET_MUSIC_GENERATION_MODEL_SETTINGS",
            provider_id=provider_id,
            provider_type=str(provider.provider or "").strip(),
            model_name=model_name,
        )

    ip_address = get_audit_request_ip(request, db)
    user_agent = (
        request.headers.get("user-agent") if request and request.headers else None
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_MUSIC_GENERATION_MODEL_SETTINGS",
        details={"provider_id": provider_id, "model_name": model_name},
        ip_address=ip_address,
        user_agent=user_agent,
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/video_generation")
def admin_get_video_generation_settings(
    request: Request,
    include_values: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get video generation settings."""
    result = get_admin_settings_schema_response(
        page="video_generation",
        include_values=include_values,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_VIDEO_GENERATION_SETTINGS",
        details={"include_values": include_values},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.patch("/settings/video_generation", response_model=OperationResult)
def admin_update_video_generation_settings(
    payload: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update video generation settings."""
    changed_keys = update_admin_settings_values_for_page(
        page="video_generation",
        payload=payload,
        db=db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_VIDEO_GENERATION_SETTINGS",
        details={"updated": changed_keys},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")


@admin_router.get("/settings/video_generation/providers")
def admin_list_video_generation_providers(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List video generation providers."""

    supported_types = {
        "openai_responses",
        "openai_chat_completions",
        "google_aistudio",
        "openrouter",
        "xai",
    }
    providers = list_llm_providers(
        db,
        provider_types=supported_types,
        order_by_name=False,
    )

    result = []
    for provider in providers:
        result.append(
            {
                "id": provider.id,
                "name": provider.name,
                "provider": provider.provider,
                "icon": resolve_provider_icon(
                    getattr(provider, "provider", None),
                    getattr(provider, "icon", None),
                ),
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_VIDEO_GENERATION_PROVIDERS",
        details={},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"providers": result}


@admin_router.get("/settings/video_generation/models")
def admin_list_video_generation_models(
    provider_id: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List video generation models."""
    from app.utils.schemas import FieldSchema, Section, Sections

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider_type = provider.provider or ""
    options: list[Option] = []

    try:
        if provider_type in {"openai_responses", "openai_chat_completions"}:
            from app.llm.openai_responses.video_generation import (
                openai_compatible_video_generation_models_list,
            )

            model_items = openai_compatible_video_generation_models_list(provider)
            options = [
                Option(value=model_id, label=model_id)
                for model in (model_items or [])
                for model_id in [str(model.get("id") or "").strip()]
                if model_id
            ]
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.video_generation import (
                getGoogleAistudioVideoGenerationModels,
            )

            api_version = "v1alpha"
            if isinstance(provider.settings, dict):
                configured_version = str(
                    provider.settings.get("api_version") or ""
                ).strip()
                if configured_version:
                    api_version = configured_version

            model_items = getGoogleAistudioVideoGenerationModels(
                api_key=provider.api_key,
                api_version=api_version,
            )
            options = [
                Option(
                    value=model_id,
                    label=(str(model.get("name") or "").strip() or model_id),
                )
                for model in (model_items or [])
                for model_id in [str(model.get("id") or "").strip()]
                if model_id
            ]
        elif provider_type == "openrouter":
            from app.llm.openrouter.video_generation import (
                get_video_generation_schema_part_1,
            )

            schema = get_video_generation_schema_part_1(db, provider_id)
            create_audit_log(
                db_log=db_log,
                user_id=admin_user.id,
                action="LIST_VIDEO_GENERATION_MODELS",
                details={"provider_id": provider_id},
                ip_address=get_audit_request_ip(request, db),
                user_agent=request.headers.get("user-agent"),
                category="admin",
            )

            schema = schema.model_copy(deep=True)
            return schema.model_dump(exclude_none=True)
        elif provider_type == "xai":
            from app.llm.xai.video_generation import get_video_generation_schema_part_1

            schema = get_video_generation_schema_part_1(db, provider_id)
            create_audit_log(
                db_log=db_log,
                user_id=admin_user.id,
                action="LIST_VIDEO_GENERATION_MODELS",
                details={"provider_id": provider_id},
                ip_address=get_audit_request_ip(request, db),
                user_agent=request.headers.get("user-agent"),
                category="admin",
            )
            return schema.model_copy(deep=True).model_dump(exclude_none=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to list video generation models",
            action="LIST_VIDEO_GENERATION_MODELS",
            provider_id=provider_id,
            provider_type=provider_type,
        )

    if provider_type not in {
        "openai_responses",
        "openai_chat_completions",
        "google_aistudio",
        "openrouter",
        "xai",
    }:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for video generation"
        )

    schema = Sections(
        sections=[
            Section(
                title="Video Generation Models",
                i18n_title="admin.shared.section_video_generation.title",
                description="",
                i18n_description="admin.shared.section_value.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="admin.shared.model_name.label",
                        description="Choose the video generation model.",
                        i18n_description="admin.shared.model_name.description",
                        type="select",
                        options=options,
                        placeholder="Select a model",
                        i18n_placeholder="admin.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_VIDEO_GENERATION_MODELS",
        details={"provider_id": provider_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/video_generation/model_settings")
def admin_get_video_generation_model_settings(
    provider_id: str = Query(...),
    model_name: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get video generation model settings."""

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider_type = provider.provider or ""
    schema = None

    try:
        if provider_type in {"openai_responses", "openai_chat_completions"}:
            from app.llm.openai_responses.video_generation import (
                get_video_generation_schema_part_2,
            )

            schema = get_video_generation_schema_part_2(model_name)
        elif provider_type == "openrouter":
            from app.llm.openrouter.video_generation import (
                get_video_generation_schema_part_2,
            )

            schema = get_video_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.video_generation import (
                get_video_generation_schema_part_2,
            )

            schema = get_video_generation_schema_part_2(model_name)
        elif provider_type == "xai":
            from app.llm.xai.video_generation import get_video_generation_schema_part_2

            schema = get_video_generation_schema_part_2(model_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to get video generation model settings",
            action="GET_VIDEO_GENERATION_MODEL_SETTINGS",
            provider_id=provider_id,
            provider_type=provider_type,
            model_name=model_name,
        )

    if schema is None:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for video generation"
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_VIDEO_GENERATION_MODEL_SETTINGS",
        details={"provider_id": provider_id, "model_name": model_name},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/image_generation/providers")
def admin_list_image_generation_providers(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List image generation providers."""
    supported_types = {
        "openai",
        "openai_responses",
        "openai_chat_completions",
        "openrouter",
        "google_aistudio",
        "ollama",
        "xai",
    }
    providers = list_llm_providers(
        db,
        provider_types=supported_types,
        order_by_name=False,
    )
    result = []
    for p in providers:
        result.append(
            {
                "id": p.id,
                "name": p.name,
                "provider": p.provider,
                "icon": resolve_provider_icon(
                    getattr(p, "provider", None),
                    getattr(p, "icon", None),
                ),
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_IMAGE_GENERATION_PROVIDERS",
        details={},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"providers": result}


@admin_router.get("/settings/image_generation/models")
def admin_list_image_generation_models(
    provider_id: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List image generation models."""
    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider_type = provider.provider or ""
    schema = None

    try:
        if provider_type == "openai":
            from app.llm.openai.image_generation import (
                get_image_generation_schema_part_1,
            )

            schema = get_image_generation_schema_part_1(db, provider_id)
        elif provider_type in ("openai_responses", "openai_chat_completions"):
            from app.llm.openai_responses.image_generation import (
                get_image_generation_schema_part_1,
            )

            schema = get_image_generation_schema_part_1(db, provider_id)
        elif provider_type == "openrouter":
            from app.llm.openrouter.image_generation import (
                get_image_generation_schema_part_1,
            )

            schema = get_image_generation_schema_part_1(db, provider_id)
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.image_generation import (
                get_image_generation_schema_part_1,
            )

            schema = get_image_generation_schema_part_1(db, provider_id)
        elif provider_type == "xai":
            from app.llm.xai.image_generation import get_image_generation_schema_part_1

            schema = get_image_generation_schema_part_1(db, provider_id)
        elif provider_type == "ollama":
            from app.llm.ollama.image_generation import (
                get_image_generation_schema_part_1,
            )

            schema = get_image_generation_schema_part_1(db, provider_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to list image generation models",
            action="LIST_IMAGE_GENERATION_MODELS",
            provider_id=provider_id,
            provider_type=provider_type,
        )

    if schema is None:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for image generation"
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_IMAGE_GENERATION_MODELS",
        details={"provider_id": provider_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    schema = schema.model_copy(deep=True)
    return schema.model_dump(exclude_none=True)


@admin_router.get("/settings/image_generation/model_settings")
def admin_get_image_generation_model_settings(
    provider_id: str = Query(...),
    model_name: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get image generation model settings."""
    from app.tools.image_generation.size_options import (
        build_assistant_size_selection_fields,
    )
    from app.utils.schemas import Section, Sections, populate_sections_with_values

    provider = get_llm_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider_type = provider.provider or ""
    schema = None

    try:
        if provider_type == "openai":
            from app.llm.openai.image_generation import (
                get_image_generation_schema_part_2,
            )

            schema = get_image_generation_schema_part_2(model_name)
        elif provider_type in ("openai_responses", "openai_chat_completions"):
            from app.llm.openai_responses.image_generation import (
                get_image_generation_schema_part_2,
            )

            schema = get_image_generation_schema_part_2()
        elif provider_type == "openrouter":
            from app.llm.openrouter.image_generation import (
                get_image_generation_schema_part_2,
            )

            schema = get_image_generation_schema_part_2()
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.image_generation import (
                get_image_generation_schema_part_2,
            )

            schema = get_image_generation_schema_part_2(model_name)
        elif provider_type == "xai":
            from app.llm.xai.image_generation import get_image_generation_schema_part_2

            schema = get_image_generation_schema_part_2()
        elif provider_type == "ollama":
            from app.llm.ollama.image_generation import (
                get_image_generation_schema_part_2,
            )

            schema = get_image_generation_schema_part_2()
    except HTTPException:
        raise
    except Exception as exc:
        raise_admin_provider_helper_error(
            exc=exc,
            user_message="Failed to get image generation model settings",
            action="GET_IMAGE_GENERATION_MODEL_SETTINGS",
            provider_id=provider_id,
            provider_type=provider_type,
            model_name=model_name,
        )

    if schema is None:
        raise HTTPException(
            status_code=400, detail="Unsupported provider type for image generation"
        )

    schema = schema.model_copy(deep=True)
    assistant_size_fields = build_assistant_size_selection_fields(
        provider_type, model_name
    )
    if assistant_size_fields:
        # Some provider schemas historically supplied their own fixed size
        # field (currently xAI's aspect ratio). Replace duplicates with the
        # shared conditional field so every provider gets identical toggle
        # behavior and field ordering.
        shared_field_keys = {field.key for field in assistant_size_fields}
        for section in schema.sections:
            section.fields = [
                field for field in section.fields if field.key not in shared_field_keys
            ]
        if schema.sections:
            schema.sections[0].fields.extend(assistant_size_fields)
        else:
            schema = Sections(
                sections=[
                    Section(
                        title="Tool Settings",
                        description="Controls exposed to the image generation tool.",
                        fields=assistant_size_fields,
                    )
                ]
            )

    settings_record = get_settings_page(db, "image_generation")
    current_values = (
        settings_record.data
        if settings_record and isinstance(settings_record.data, dict)
        else {}
    )
    current_provider_id = str(current_values.get("provider_id") or "").strip()
    current_model_name = str(current_values.get("model_name") or "").strip()
    if (
        current_provider_id == str(provider_id).strip()
        and current_model_name == str(model_name).strip()
    ):
        populate_sections_with_values(
            schema, {"settings": current_values.get("settings") or {}}
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_IMAGE_GENERATION_MODEL_SETTINGS",
        details={"provider_id": provider_id, "model_name": model_name},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return schema.model_dump(exclude_none=True)


@admin_router.post("/privacy", response_model=OperationResult)
def admin_update_privacy_policy(
    payload: AdminPrivacyPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update the privacy policy."""
    policy_update = update_privacy_policy(
        db,
        payload.content,
        notice_mode=payload.notice_mode,
        notice_message_html=payload.notice_message_html,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_PRIVACY_POLICY",
        details={
            "length": len(payload.content),
            "revision": policy_update.get("revision"),
            "notice_mode": policy_update.get("notice_mode"),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")


@admin_router.post("/terms", response_model=OperationResult)
def admin_update_terms_of_service(
    payload: AdminTermsOfServiceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update terms of service."""
    result = update_terms_of_service(db, payload.content)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_TERMS_OF_SERVICE",
        details={
            "length": len(payload.content),
            "revision": result.get("revision"),
            "is_default_template": bool(result.get("is_default_template")),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return OperationResult(status="success")
