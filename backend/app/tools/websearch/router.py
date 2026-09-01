from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.utils.helpers import (
    _mask_api_key_preview, 
    _is_masked_api_key,
    _set_schema_field_placeholder,
    _set_schema_field_required
)
from app.tools.websearch.models import (
    WebSearchProvider,
    create_websearch_provider,
    update_websearch_provider,
    delete_websearch_provider,
    list_websearch_providers,
    get_websearch_provider,
    _get_provider_types,
    export_websearch_providers,
    import_websearch_providers,
)
from app.tools.websearch.schemas import (
    CreateWebSearchProviderRequest,
    UpdateWebSearchProviderRequest,
    WEBSEARCH_PROVIDER_SETTINGS_MODELS,
    WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS,
    normalize_websearch_provider_settings,
    WebSearchProviderListItem,
    WebSearchProviderDetail,
)
from app.tools.websearch.schemas import get_websearch_provider_definition
from app.tools.websearch.provider_url_suggestions import attach_provider_url_suggestions
from app.tools.websearch.audit import (
    build_aiohttp_tls_audit_details,
    build_import_aiohttp_tls_audit_details,
)



def _prepare_settings_payload(provider: WebSearchProvider, raw_settings, definition):
    if raw_settings is None:
        return None

    existing_settings = provider.settings or {}
    existing_api_key = existing_settings.get("api_key")

    if isinstance(raw_settings, definition.settings_model):
        provided_settings = raw_settings.model_dump(exclude_unset=False)
    else:
        if raw_settings is None:
            provided_settings = {}
        elif isinstance(raw_settings, dict):
            provided_settings = raw_settings
        else:
            raise HTTPException(status_code=400, detail="Settings must be a dictionary")

    merged_settings = {**existing_settings, **provided_settings}

    if "api_key" in provided_settings:
        new_api_key = provided_settings.get("api_key")
        if not new_api_key or _is_masked_api_key(new_api_key, existing_api_key):
            merged_settings["api_key"] = existing_api_key

    settings_obj = definition.settings_model.model_validate(merged_settings)
    return jsonable_encoder(settings_obj)


def _serialize_websearch_provider_detail(provider: WebSearchProvider) -> WebSearchProviderDetail:
    settings = normalize_websearch_provider_settings(provider.provider, provider.settings)
    return WebSearchProviderDetail(
        id=provider.id,
        provider=provider.provider,
        name=provider.name,
        type=_get_provider_types(provider),
        settings=settings,
    )


websearch_router = APIRouter(prefix="/api/v1/websearch", tags=["websearch"])


def _audit_websearch_provider_event(
    db_log: Session,
    request: Request,
    admin_user,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="websearch_provider",
    )
# -------------------
# Get Provider Schema
# -------------------
@websearch_router.get("/provider/schema", dependencies=[Depends(verified_admin)])
def get_websearch_provider_schema(
    provider: str | None = None,
    provider_id: str | None = None,
    db: Session = Depends(get_db),
):
    if provider is None:
        return {}

    schema = WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS.get(provider.lower())
    if schema is None:
        return {}

    schema_copy = schema.model_copy(deep=True)
    schema_copy = attach_provider_url_suggestions(schema_copy, provider)

    if provider_id:
        try:
            provider_row = get_websearch_provider(db, provider_id)
        except HTTPException:
            provider_row = None
        if provider_row and (provider_row.settings or {}).get("api_key"):
            placeholder = _mask_api_key_preview(provider_row.settings["api_key"])
            if placeholder:
                _set_schema_field_placeholder(schema_copy, "api_key", placeholder)
                _set_schema_field_required(schema_copy, "api_key", False)

    return schema_copy.model_dump()


# -------------------
# List Available Provider Definitions
# -------------------
@websearch_router.get("/providers/available", dependencies=[Depends(verified_admin)])
def list_websearch_providers_available():
    return [
        {
            "id": provider
        }
        for provider in WEBSEARCH_PROVIDER_SETTINGS_MODELS.keys()
    ]


# -------------------
# Export Web Search Providers
# -------------------
@websearch_router.get("/providers/export", dependencies=[Depends(verified_admin)])
def export_websearch_providers_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    result = export_websearch_providers(db)
    providers = result.get("data", {}).get("providers", []) if isinstance(result, dict) else []
    configured_api_key_count = sum(
        1
        for provider in providers
        if provider.get("credentials", {}).get("api_key_configured") is True
    )
    required_api_key_count = sum(
        1
        for provider in providers
        if provider.get("credentials", {}).get("api_key_required") is True
    )
    provider_types = sorted(
        {
            provider.get("provider")
            for provider in providers
            if isinstance(provider.get("provider"), str) and provider.get("provider")
        }
    )
    _audit_websearch_provider_event(
        db_log,
        request,
        admin_user,
        "EXPORT_WEBSEARCH_PROVIDERS",
        {
            "export_version": result.get("export_version"),
            "provider_count": len(providers),
            "provider_types": provider_types,
            "required_api_key_count": required_api_key_count,
            "configured_api_key_count": configured_api_key_count,
            "api_keys_exported": False,
        },
    )
    return result


# -------------------
# Import Web Search Providers
# -------------------
@websearch_router.post("/providers/import")
def import_websearch_providers_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    result = import_websearch_providers(db, payload)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_WEBSEARCH_PROVIDERS",
        details={
            "created_count": len(result.get("created", [])),
            "error_count": len(result.get("errors", [])),
            **build_import_aiohttp_tls_audit_details(payload, result),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="websearch_provider",
    )
    return result


# -------------------
# Create Web Search Provider
# -------------------   
@websearch_router.post(
    "/providers",
    response_model=WebSearchProviderDetail,
    dependencies=[Depends(verified_admin)],
)
def create_websearch_provider_route(
    payload: CreateWebSearchProviderRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    definition = get_websearch_provider_definition(payload.provider)
    settings_dict = jsonable_encoder(payload.settings)

    result = create_websearch_provider(
        db,
        definition.key,
        payload.name,
        settings_dict,
    )
    _audit_websearch_provider_event(
        db_log,
        request,
        admin_user,
        "CREATE_WEBSEARCH_PROVIDER",
        {
            "provider_id": result.id,
            "provider": result.provider,
            "name": result.name,
            **build_aiohttp_tls_audit_details(result.provider, settings_dict),
        },
    )
    return _serialize_websearch_provider_detail(result)


# -------------------
# Update Web Search Provider
# -------------------   
@websearch_router.put(
    "/provider/{provider_id}",
    response_model=WebSearchProviderDetail,
    dependencies=[Depends(verified_admin)],
)
def update_websearch_provider_route(
    provider_id: str,
    payload: UpdateWebSearchProviderRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    provider = (
        db.query(WebSearchProvider)
        .filter(WebSearchProvider.id == provider_id)
        .first()
    )
    if not provider:
        raise HTTPException(status_code=404, detail="Web search provider not found")

    try:
        definition = get_websearch_provider_definition(provider.provider)
    except KeyError:
        raise HTTPException(status_code=404, detail="Web search provider is no longer supported")

    settings_dict = _prepare_settings_payload(provider, payload.settings, definition)

    result = update_websearch_provider(
        db,
        provider_id,
        name=payload.name,
        settings=settings_dict,
    )
    _audit_websearch_provider_event(
        db_log,
        request,
        admin_user,
        "UPDATE_WEBSEARCH_PROVIDER",
        {
            "provider_id": result.id,
            "provider": result.provider,
            "name": result.name,
            "updated_fields": sorted(getattr(payload, "model_fields_set", set())),
            **build_aiohttp_tls_audit_details(result.provider, settings_dict),
        },
    )
    return _serialize_websearch_provider_detail(result)



# -------------------
# List Web Search Providers
# -------------------  
@websearch_router.get(
    "/providers",
    response_model=List[WebSearchProviderListItem],
    dependencies=[Depends(verified_admin)],
)
def list_websearch_providers_route(provider: str | None = None, db: Session = Depends(get_db)):
    rows = list_websearch_providers(db, provider)
    return [
        WebSearchProviderListItem(
            id=row.id,
            provider=row.provider,
            name=row.name
        )
        for row in rows
    ]



# -------------------
# Get Web Search Provider
# -------------------  
@websearch_router.get(
    "/provider",
    response_model=WebSearchProviderDetail,
    dependencies=[Depends(verified_admin)],
)
def get_websearch_provider_route(provider_id: str, db: Session = Depends(get_db)):
    provider = get_websearch_provider(db, provider_id)
    return _serialize_websearch_provider_detail(provider)



# -------------------
# Delete Web Search Provider
# -------------------  
@websearch_router.delete("/provider/{provider_id}", dependencies=[Depends(verified_admin)])
def delete_websearch_provider_route(
    provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    provider = get_websearch_provider(db, provider_id)
    result = delete_websearch_provider(db, provider_id)
    _audit_websearch_provider_event(
        db_log,
        request,
        admin_user,
        "DELETE_WEBSEARCH_PROVIDER",
        {
            "provider_id": provider_id,
            "provider": provider.provider,
            "name": provider.name,
            "models_updated": result.get("models_updated") if isinstance(result, dict) else None,
        },
    )
    return result
