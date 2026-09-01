import logging
import uuid
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import Column, String, JSON, DateTime, Index, TypeDecorator
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import ValidationError

from app.database import Base
from app.utils.encryption import encrypt_value, decrypt_value
from app.llm.models import Models
from app.tools.websearch.schemas import get_websearch_provider_definition
from app.network.policy import OutboundRequestBlockedError, assert_websearch_provider_allowed


logger = logging.getLogger(__name__)


_SENSITIVE_SETTING_KEYS = {"api_key"}
current_websearch_provider_export_version = 1.0


def _assert_websearch_provider_settings_allowed(
    db: Session,
    *,
    provider: str,
    settings: Dict[str, Any],
    feature: str,
) -> None:
    try:
        assert_websearch_provider_allowed(
            db,
            SimpleNamespace(provider=str(provider or "").strip().lower(), settings=settings or {}),
            feature=feature,
            include_all_targets=True,
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


class SettingsWithEncryptedApiKeys(TypeDecorator):
    """Encrypt/decrypt sensitive keys (currently api_key) within JSON settings."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return _transform_sensitive_settings(value, encrypt=True)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return _transform_sensitive_settings(value, encrypt=False)


def _transform_sensitive_settings(value: Any, *, encrypt: bool):
    if isinstance(value, dict):
        transformed: Dict[Any, Any] = {}
        for key, item in value.items():
            if key in _SENSITIVE_SETTING_KEYS and isinstance(item, str):
                if encrypt:
                    transformed[key] = encrypt_value(item)
                else:
                    try:
                        transformed[key] = decrypt_value(item)
                    except ValueError:
                        logger.warning(
                            "Failed to decrypt sensitive setting for key '%s'; keeping stored value as-is.",
                            key,
                        )
                        transformed[key] = item
            else:
                transformed[key] = _transform_sensitive_settings(item, encrypt=encrypt)
        return transformed
    if isinstance(value, list):
        return [_transform_sensitive_settings(item, encrypt=encrypt) for item in value]
    return value


def _websearch_provider_api_key_is_required(provider: str) -> bool:
    try:
        definition = get_websearch_provider_definition(str(provider or "").strip().lower())
    except KeyError:
        return False

    field_info = getattr(definition.settings_model, "model_fields", {}).get("api_key")
    if field_info is None:
        return False
    return bool(getattr(field_info, "is_required", lambda: False)())


def _redact_sensitive_settings_for_export(settings: Any) -> dict[str, Any]:
    """Return provider settings without live secret values."""
    if not isinstance(settings, dict):
        return {}

    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                if key in _SENSITIVE_SETTING_KEYS:
                    continue
                redacted[key] = _redact(item)
            return redacted
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    return _redact(deepcopy(settings))


def _serialize_websearch_provider_for_export(
    provider: "WebSearchProvider",
) -> dict[str, Any]:
    """Serialize a provider without secrets."""

    settings = provider.settings if isinstance(provider.settings, dict) else {}
    api_key_required = _websearch_provider_api_key_is_required(provider.provider)
    api_key_configured = bool(str(settings.get("api_key") or "").strip())
    export_settings = _redact_sensitive_settings_for_export(settings)

    return {
        "id": provider.id,
        "provider": provider.provider,
        "name": provider.name,
        "credentials": {
            "api_key_exported": False,
            "api_key_required": api_key_required,
            "api_key_configured": api_key_configured,
        },
        "settings": export_settings,
    }



# -------------------
# Clear Web Search Provider References from Model Settings
# -------------------
def _clear_websearch_provider_from_models(db: Session, provider_id: str) -> int:
    """Unset deleted provider references from model settings and tools."""

    if not provider_id:
        return 0

    affected_models = db.query(Models).filter(Models.settings.isnot(None)).all()
    updated = 0

    for model in affected_models:
        settings = model.settings if isinstance(model.settings, dict) else {}
        scrape_match = settings.get("websearch_scrape_provider") == provider_id
        search_match = settings.get("websearch_search_provider") == provider_id

        if not (scrape_match or search_match):
            continue

        new_settings = dict(settings)
        if scrape_match:
            new_settings["websearch_scrape_provider"] = None
        if search_match:
            new_settings["websearch_search_provider"] = None
        model.settings = new_settings

        raw_tools = model.tools
        if raw_tools is not None:
            raw_tools_list = list(raw_tools)
            for item in raw_tools_list:
                if item == "web_search":
                    raw_tools_list.remove(item)
                    model.tools = raw_tools_list
                    if len(model.tools) == 0:
                        capabilities = model.capabilities if isinstance(model.capabilities, list) else []
                        filtered_caps = [cap for cap in capabilities if cap != "tools"]
                        if len(filtered_caps) != len(capabilities):
                            model.capabilities = filtered_caps or ["completion"]
                    break
        db.add(model)
        updated += 1

    return updated



# ---------------------------------------------------------------------------
# Web Search Provider
# ---------------------------------------------------------------------------
class WebSearchProvider(Base):
    __tablename__ = "websearch_provider"
    __table_args__ = (
        Index("ix_websearch_provider_id", "id"),
        Index("ix_websearch_provider_provider", "provider"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    provider = Column(String, nullable=False)
    name = Column(String, nullable=False, unique=True)
    settings = Column(SettingsWithEncryptedApiKeys, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


# -------------------
# Create Web Search Provider
# -------------------
def create_websearch_provider(
    db: Session,
    provider: str,
    name: str,
    settings: Dict[str, Any],
):
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Provider name cannot be empty")
    normalized_name = name.strip()
    normalized_provider = str(provider or "").strip().lower()
    _assert_websearch_provider_settings_allowed(
        db,
        provider=normalized_provider,
        settings=settings or {},
        feature="Web search provider configuration",
    )
    now = datetime.now(timezone.utc)
    websearch_provider = WebSearchProvider(
        provider=normalized_provider,
        name=normalized_name,
        settings=settings or {},
        created_at=now,
        updated_at=now,
    )
    db.add(websearch_provider)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Web search provider name '{normalized_name}' already exists") from exc
    db.refresh(websearch_provider)
    return websearch_provider


def export_websearch_providers(db: Session) -> dict[str, Any]:
    """Export all web search providers."""
    providers = db.query(WebSearchProvider).all()
    export_data = [_serialize_websearch_provider_for_export(provider) for provider in providers]

    return {
        "export_type": "websearch_provider",
        "export_version": current_websearch_provider_export_version,
        "data": {
            "providers": export_data,
        },
    }


def import_websearch_providers(db: Session, payload: dict) -> dict[str, list[dict[str, Any]]]:
    """Import web search providers from an export payload."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid import payload. Expected an object.")

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "websearch_provider":
        raise HTTPException(status_code=400, detail=f"Unsupported export_type '{export_type}'.")

    if export_version != current_websearch_provider_export_version:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{current_websearch_provider_export_version}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(status_code=400, detail="Invalid export payload. Missing 'data' object.")

    raw_providers = data_block.get("providers")
    if not isinstance(raw_providers, list):
        raise HTTPException(status_code=400, detail="Invalid export payload. 'providers' must be a list.")

    created = []
    errors = []

    for index, provider_entry in enumerate(raw_providers):
        if not isinstance(provider_entry, dict):
            errors.append({"index": index, "error": "Provider entry must be an object."})
            continue

        provider_key = str(provider_entry.get("provider") or "").strip().lower()
        if not provider_key:
            errors.append({"index": index, "error": "Provider type is required."})
            continue

        try:
            definition = get_websearch_provider_definition(provider_key)
        except KeyError:
            errors.append({"index": index, "error": f"Unsupported provider '{provider_key}'."})
            continue

        raw_settings = provider_entry.get("settings") or {}
        if not isinstance(raw_settings, dict):
            errors.append({"index": index, "error": "Provider settings must be an object."})
            continue

        name = provider_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append({"index": index, "error": "Provider name is required."})
            continue

        api_key_required = _websearch_provider_api_key_is_required(definition.key)
        api_key_raw = raw_settings.get("api_key")
        if api_key_required and (not isinstance(api_key_raw, str) or not api_key_raw.strip()):
            errors.append({"index": index, "name": name, "error": "Provider api_key is required."})
            continue

        try:
            validated_settings = definition.settings_model.model_validate(raw_settings)
        except ValidationError as exc:
            errors.append({"index": index, "name": name, "error": exc.errors()})
            continue
        except Exception as exc:
            error_payload = exc.errors() if callable(getattr(exc, "errors", None)) else str(exc)
            errors.append({"index": index, "name": name, "error": error_payload})
            continue

        try:
            provider_obj = create_websearch_provider(
                db,
                definition.key,
                name.strip(),
                validated_settings.model_dump(exclude_unset=True),
            )
        except HTTPException as exc:
            errors.append({"index": index, "name": name, "error": exc.detail})
            continue
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})
            continue

        created.append(
            {
                "id": provider_obj.id,
                "name": provider_obj.name,
                "provider": provider_obj.provider,
            }
        )

    return {
        "created": created,
        "errors": errors,
    }



# -------------------
# Delete Web Search Provider
# -------------------
def delete_websearch_provider(db: Session, provider_id: str):
    provider = get_websearch_provider(db, provider_id)
    models_updated = _clear_websearch_provider_from_models(db, provider.id)
    db.delete(provider)
    db.commit()
    return {"deleted": True, "provider_id": provider_id, "models_updated": models_updated}



# -------------------
# Update Web Search Provider
# -------------------   
def update_websearch_provider(
    db: Session,
    provider_id: str,
    *,
    name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
):
    provider = get_websearch_provider(db, provider_id)

    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Provider name cannot be empty")
        provider.name = name.strip()

    if settings is not None:
        _assert_websearch_provider_settings_allowed(
            db,
            provider=provider.provider,
            settings=settings,
            feature="Web search provider configuration",
        )
        provider.settings = settings

    provider.updated_at = datetime.now(timezone.utc)
    db.add(provider)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        conflict_name = provider.name if isinstance(provider.name, str) else name
        raise HTTPException(status_code=409, detail=f"Web search provider name '{conflict_name}' already exists") from exc
    db.refresh(provider)
    return provider



# -------------------
# List Web Search Providers
# -------------------   
def list_websearch_providers(db: Session, provider: str | None = None) -> List[WebSearchProvider]:
    query = db.query(WebSearchProvider)
    if provider:
        query = query.filter(WebSearchProvider.provider == provider.lower())
    return query.all()



# -------------------
# Get Provider Types
# -------------------   
def _get_provider_types(provider: WebSearchProvider) -> List[str]:
    """Return the normalized list of capabilities for a provider from registry."""
    provider_key = str(getattr(provider, "provider", "") or "").strip().lower()
    if provider_key:
        try:
            definition = get_websearch_provider_definition(provider_key)
        except KeyError:
            definition = None
        if definition:
            return [
                capability_value
                for capability in definition.capabilities
                if (capability_value := str(capability or "").strip().lower())
            ]
    return []


def _provider_has_combined(provider: WebSearchProvider) -> bool:
    """Check if a provider has combined capability."""
    return "combined" in _get_provider_types(provider)


def _provider_has_explicit_scrape(provider: WebSearchProvider) -> bool:
    """Check if a provider explicitly supports scrape (not just via combined)."""
    return "scrape" in _get_provider_types(provider)


def _provider_has_explicit_search(provider: WebSearchProvider) -> bool:
    """Check if a provider explicitly supports search (not just via combined)."""
    return "search" in _get_provider_types(provider)


def list_websearch_providers_scrape(db: Session) -> List[WebSearchProvider]:
    """
    Return providers available for scrape selection.
    Includes: providers with explicit 'scrape' capability.
    Excludes: combined-only providers (they should be selected via search dropdown).
    """
    rows = db.query(WebSearchProvider).all()
    return [row for row in rows if _provider_has_explicit_scrape(row)]


def list_websearch_providers_search(db: Session) -> List[WebSearchProvider]:
    """
    Return providers available for search selection.
    Includes: providers with 'search' capability OR 'combined' capability.
    """
    rows = db.query(WebSearchProvider).all()
    return [row for row in rows if _provider_has_explicit_search(row) or _provider_has_combined(row)]


def list_websearch_providers_with_types(db: Session) -> List[dict]:
    """
    Return all websearch providers with their full type information.
    Used for frontend to determine combined/scrape/search logic.
    """
    rows = db.query(WebSearchProvider).all()
    result = []
    for row in rows:
        types = _get_provider_types(row)
        result.append({
            "id": row.id,
            "name": row.name,
            "provider": row.provider,
            "types": types,
            "has_combined": "combined" in types,
            "has_scrape": "scrape" in types,
            "has_search": "search" in types,
        })
    return result

def get_websearch_provider(db: Session, provider_id: str) -> WebSearchProvider:
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="Invalid provider_id")
    provider = db.query(WebSearchProvider).filter(WebSearchProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Web search provider not found")
    return provider
