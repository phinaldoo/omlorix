import anyio
from contextlib import contextmanager
import hashlib
import math
import threading

from sqlalchemy import BigInteger, Column, String, Boolean, JSON, cast, exists, func, TypeDecorator, or_, Integer, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm.attributes import flag_modified
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import DateTime
from types import SimpleNamespace
from typing import Any
import logging
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from app.database import Base, SessionLocal
from app.groups.models import Group
from app.settings.models import Settings
from app.settings.utils import (
    get_default_model_id,
    invalidate_settings_cache,
    sanitize_pinned_model_ids,
    update_page_key_value_by_page_and_key,
)
from app.automations.models import Automation, migrate_automations_model
from app.agents.models import UserAgent, migrate_user_agents_base_model
from app.utils.icon_security import require_safe_icon_input
from app.users.models import User
from app.llm.schemas import (
    MODEL_CAPABLE_PROVIDERS,
    ProviderEnum,
    PROVIDER_MODEL_SETTINGS_MODELS,
    PROVIDER_SETTINGS_MODELS,
    get_default_provider_icon,
    normalize_provider_value,
    provider_api_key_is_optional,
    provider_supports_custom_icon,
    resolve_provider_icon,
)
from app.llm.base_settings import remove_custom_provider_timeout
from app.llm.openai.custom_headers import redact_custom_headers_in_settings
from app.llm.anthropic.settings import remove_deprecated_anthropic_request_settings
from app.logging.models import create_admin_notification
from app.llmstats.models import LLMGenerationStatistic
from app.utils.encryption import encrypt_value, decrypt_value
from app.utils.helpers import _is_masked_api_key, _mask_api_key_preview


_PROVIDER_STATUS_VALUES = {"up", "down", "unknown"}
_RATE_LIMIT_WINDOW_LOCK_PREFIX = "rate-limit-window:"
_RATE_LIMIT_WINDOW_LOCKS: dict[str, threading.RLock] = {}
_RATE_LIMIT_WINDOW_LOCKS_GUARD = threading.Lock()
_RATE_LIMIT_OPEN_ADMISSION_GRACE_PERIOD = timedelta(minutes=30)
# Active file and live dictation work renew this lease throughout provider
# processing. A short lease is important because a browser tab or application
# process can vanish without executing the normal finalizer. Reserving the
# user's remaining budget still prevents concurrent work from overspending it,
# while a stranded reservation can recover quickly.
_DURATION_RATE_LIMIT_DICTATION_LEASE = timedelta(seconds=90)
_DURATION_RATE_LIMIT_REALTIME_LEASE = timedelta(minutes=3)
# Active dictation work renews well before the short lease expires. The same
# interval is shared by file uploads and live transcription so every consumer
# protects its reservation for the complete provider-processing lifecycle.
_DURATION_RATE_LIMIT_DICTATION_RENEWAL_INTERVAL_SECONDS = 20.0


logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_TIMEZONE = "UTC"


class EncryptedString(TypeDecorator):
    """Encrypts values on the way in and decrypts them on the way out."""
    
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return encrypt_value(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return decrypt_value(value)
        return value


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce JSON-like values to boolean."""
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


def provider_regular_requests_disabled(provider_or_settings: Any) -> bool:
    """Return whether recurring provider requests are disabled for a provider."""
    settings = provider_or_settings
    if not isinstance(settings, dict):
        settings = getattr(provider_or_settings, "settings", None)
    if not isinstance(settings, dict):
        return False
    return _coerce_bool(settings.get("disable_background_sync"), default=False)


def normalize_llm_provider_status(provider: Any, status: Any | None = None) -> dict[str, Any]:
    """Normalize provider status and force `unknown` when regular requests are disabled."""
    raw_status = status if isinstance(status, dict) else getattr(provider, "status", None)
    status_payload = dict(raw_status) if isinstance(raw_status, dict) else {}

    if provider_regular_requests_disabled(provider):
        status_payload["available"] = "unknown"
        return status_payload

    availability = str(status_payload.get("available") or "unknown").strip().lower()
    status_payload["available"] = availability if availability in _PROVIDER_STATUS_VALUES else "unknown"
    return status_payload


def apply_disabled_sync_status(db, provider) -> None:
    """Persist a disabled-sync status snapshot for a provider."""
    status_payload = normalize_llm_provider_status(provider)
    status_payload["available"] = "unknown"
    status_payload["policy_blocked"] = False
    status_payload["last_error"] = ""
    provider.status = status_payload
    db.add(provider)
    db.commit()
    db.refresh(provider)


def _coerce_string_list(value: Any) -> list[str]:
    """Coerce a value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    if isinstance(value, dict):
        return [str(item).strip() for item in value.keys() if str(item).strip()]
    return [str(value).strip()]


def _serialize_datetime_value(value: Any) -> str | None:
    """Serialize a datetime value to ISO format string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt_value = value
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return None


def _sanitize_jsonable(value: Any, default):
    """Sanitize a value to JSON-serializable format."""
    if value is None:
        return default
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    for attr in ("model_dump", "dict"):
        attr_fn = getattr(value, attr, None)
        if callable(attr_fn):
            try:
                data = attr_fn()
            except Exception:
                continue
            if isinstance(data, (dict, list)):
                return data
    try:
        return list(value)
    except Exception:
        return default


def _normalize_model_access(raw: Any) -> dict:
    """Normalize model access configuration."""
    base = {"everyone": False, "users": [], "groups": []}
    if not isinstance(raw, dict):
        return base
    normalized = {
        "everyone": bool(raw.get("everyone", False)),
        "users": _coerce_string_list(raw.get("users")),
        "groups": _coerce_string_list(raw.get("groups")),
    }
    return normalized


# ---------------------------------------------------------------------------
# LLMProvider
# ---------------------------------------------------------------------------
class LLMProvider(Base):
    __tablename__ = "llm_provider"
    __table_args__ = (
        Index("ix_llm_provider_provider", "provider"),
        UniqueConstraint("name", name="uq_llm_provider_name"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    provider = Column(String,nullable=False)
    name = Column(String, nullable=False)
    icon = Column(String, nullable=True)  # Icon can be a preset name like "openai" or custom SVG code
    api_key = Column(EncryptedString, nullable=False)
    settings = Column(JSON, nullable=False)
    status = Column(JSON, nullable=False, default=lambda: {"available": "unknown", "model_list": []})
    created_at = Column(DateTime, nullable=False, server_default=func.now(), default=func.now())



# -------------------
# Create llm provider
# -------------------
def create_llm_provider(db, provider: str, name: str, api_key: str, settings: dict, status: dict | None = None, icon: str | None = None):
    """Create an LLM provider without persisting removed timeout settings."""
    provider = normalize_provider_value(provider)
    existing_provider = db.query(LLMProvider).filter(LLMProvider.name == name).first()
    if existing_provider:
        raise HTTPException(status_code=400, detail="Provider with this name already exists. Please choose a different name.")

    # Resolve the policy before sanitizing the value.  This makes the model
    # layer authoritative for imports, background code, and any API caller
    # that bypasses the provider router.
    resolved_icon = require_safe_icon_input(
        resolve_provider_icon(provider, icon),
        fallback=get_default_provider_icon(provider),
    )

    llm_provider = LLMProvider(
        provider=provider,
        name=name,
        icon=resolved_icon,
        api_key=api_key,
        settings=remove_custom_provider_timeout(settings),
        status=status,
    )
    db.add(llm_provider)
    db.commit()
    db.refresh(llm_provider)
    return llm_provider



# -------------------
# List all provider
# -------------------
def list_llm_provider(db, provider: str | None = None):
    """List LLM providers, optionally filtered by provider type."""
    if provider:
        provider_value = normalize_provider_value(provider)
        rows = (
            db.query(LLMProvider)
            .filter(LLMProvider.provider == provider_value)
            .order_by(LLMProvider.name.asc())
            .all()
        )
        return rows
    rows = db.query(LLMProvider).order_by(LLMProvider.name.asc()).all()
    return rows



# -------------------
# Get provider
# -------------------
def get_llm_provider(db, provider_id: str, mask_api_key: bool = False):
    """Get an LLM provider by ID or name."""
    identifier = provider_id.strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Invalid provider_id")

    query = db.query(LLMProvider)
    llm_provider = query.filter(LLMProvider.id == identifier).first()
    if not llm_provider:
        llm_provider = query.filter(LLMProvider.name == identifier).first()
    if not llm_provider:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    if mask_api_key:
        masked_key = _mask_api_key_preview(llm_provider.api_key, visible_chars=7)
        return SimpleNamespace(
            id=llm_provider.id,
            provider=llm_provider.provider,
            name=llm_provider.name,
            icon=resolve_provider_icon(llm_provider.provider, llm_provider.icon),
            api_key=masked_key or "",
            settings=llm_provider.settings,
            status=llm_provider.status,
            created_at=llm_provider.created_at,
        )
    return llm_provider



def update_llm_provider(db, provider_id: str, **updates):
    """Update an LLM provider."""
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="Invalid provider_id")

    if not updates:
        raise HTTPException(status_code=400, detail="No update fields provided")

    if "status" in updates:
        # Provider status is maintained by the background worker; ignore manual attempts.
        updates.pop("status", None)

    llm_provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not llm_provider:
        raise HTTPException(status_code=404, detail="LLM provider not found")

    if "name" in updates and updates["name"] is not None:
        new_name = updates["name"].strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Provider name cannot be empty")
        conflict = (
            db.query(LLMProvider)
            .filter(LLMProvider.name == new_name, LLMProvider.id != provider_id)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=400, detail="Provider with this name already exists. Please choose a different name.")
        updates["name"] = new_name

    if "provider" in updates and updates["provider"] is not None:
        provider_value = updates["provider"]
        provider_value = provider_value.value if hasattr(provider_value, "value") else provider_value
        updates["provider"] = normalize_provider_value(provider_value)

    if "settings" in updates and updates["settings"] is not None:
        if not isinstance(updates["settings"], dict):
            raise HTTPException(status_code=400, detail="Settings must be a dictionary")
        updates["settings"] = remove_custom_provider_timeout(updates["settings"])

    # Native provider icons are fixed.  Apply that rule even when an older
    # record contains a custom icon or the caller omitted the icon entirely.
    # For custom compatible endpoints, an omitted icon means "keep the
    # existing selection" while an explicitly supplied value is sanitized.
    effective_provider = updates.get("provider") or llm_provider.provider
    if not provider_supports_custom_icon(effective_provider):
        updates["icon"] = get_default_provider_icon(effective_provider)
    elif "icon" in updates and updates["icon"] is not None:
        updates["icon"] = require_safe_icon_input(
            resolve_provider_icon(effective_provider, updates["icon"]),
            fallback=get_default_provider_icon(effective_provider),
        )

    if "api_key" in updates and updates["api_key"] is not None:
        api_key = updates["api_key"]
        if not isinstance(api_key, str):
            raise HTTPException(status_code=400, detail="Provider api_key must be a string")

        api_key = api_key.strip()
        if not api_key or _is_masked_api_key(api_key, llm_provider.api_key):
            updates.pop("api_key", None)
        else:
            updates["api_key"] = api_key

    for field, value in list(updates.items()):
        if value is None:
            updates.pop(field)

    if not updates:
        return llm_provider

    for field, value in updates.items():
        if hasattr(llm_provider, field):
            setattr(llm_provider, field, value)

    db.add(llm_provider)
    db.commit()
    db.refresh(llm_provider)
    return llm_provider



# -------------------
# Delete llm provider
# -------------------
def delete_llm_provider(db, provider_id: str):
    """Delete an LLM provider and all its models."""
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="Invalid provider_id")

    llm_provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not llm_provider:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    # Remove all models that belong to the provider so deletion succeeds
    provider_models = db.query(Models).filter(Models.provider_id == provider_id).all()
    for model in provider_models:
        delete_model(db, model_id=model.id)
    db.delete(llm_provider)
    db.commit()
    return {"deleted": True, "provider_id": provider_id}





def _notify_provider_status_change(provider, previous_value: str | None, new_value: str) -> None:
    """Notify about provider status changes."""
    significant_values = {"up", "down"}
    if previous_value not in significant_values or new_value not in significant_values:
        return
    if previous_value == new_value:
        return

    provider_label = provider.name or provider.id or "LLM provider"
    if new_value == "down":
        message = f"[{provider_label}] Provider became unreachable."
        notif_type = "error"
    else:
        message = f"[{provider_label}] Provider is reachable again."
        notif_type = "info"

    details = {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "provider_slug": provider.provider,
        "previous_status": previous_value,
        "new_status": new_value,
    }

    try:
        with SessionLocal() as session:
            create_admin_notification(
                session,
                "llm_provider_availability",
                message,
                details=details,
                notification_type=notif_type,
            )
    except Exception:
        logger.exception(
            "Failed to record admin notification for provider %s availability change",
            provider.id,
        )


def update_provider_availability(db, provider_id: str, status: Any):
    """Update provider availability status."""
    POSSIBLE_VALUES = {"up", "down", "unknown"}
    provider = get_llm_provider(db, provider_id)

    current_status = provider.status if isinstance(provider.status, dict) else {}
    current_value = current_status.get("available")
    new_value = status.lower() if status.lower() in POSSIBLE_VALUES else "unknown"
    if current_value == new_value:
        return provider

    new_status = dict(current_status)
    new_status["available"] = new_value
    provider.status = new_status

    db.add(provider)
    db.commit()
    db.refresh(provider)

    _notify_provider_status_change(provider, current_value, new_value)

    return provider




# -------------------
# Provider availability summary
# -------------------
def get_llm_provider_status_summary(db) -> tuple[bool, int]:
    """Return whether all providers are available and how many are down."""
    providers = db.query(LLMProvider).all()
    down_count = 0

    for provider in providers:
        availability = normalize_llm_provider_status(provider).get("available")
        if availability == "down":
            down_count += 1

    all_available = down_count == 0
    return all_available, down_count


# -------------------
# Models with elevated errors summary
# -------------------
def get_models_elevated_errors_summary(db) -> tuple[bool, int]:
    """Return whether all models are healthy and how many have elevated errors."""
    models = db.query(Models).filter(Models.is_active == True).all()
    error_count = 0

    for model in models:
        meta = model.meta if isinstance(model.meta, dict) else {}
        increased_errors = meta.get("increased_errors", False)
        if increased_errors is True or (isinstance(increased_errors, str) and increased_errors.lower() in ("true", "1", "yes")):
            error_count += 1

    all_healthy = error_count == 0
    return all_healthy, error_count


current_llm_provider_export_version = 1.0


current_llm_model_export_version = 1.0


def export_llm_providers(db):
    """Export administrator-managed LLM providers."""
    providers = db.query(LLMProvider).all()
    export_data = []

    for provider in providers:
        provider_value = normalize_provider_value(provider.provider)
        api_key_required = not provider_api_key_is_optional(provider_value)
        api_key_configured = bool((provider.api_key or "").strip())
        export_data.append(
            {
                "id": provider.id,
                "provider": provider_value,
                "name": provider.name,
                "icon": resolve_provider_icon(provider_value, provider.icon),
                "credentials": {
                    "api_key_exported": False,
                    "api_key_required": api_key_required,
                    "api_key_configured": api_key_configured,
                },
                "settings": redact_custom_headers_in_settings(
                    remove_custom_provider_timeout(provider.settings)
                ),
                "status": provider.status or {},
            }
        )

    return {
        "export_type": "llm_provider",
        "export_version": current_llm_provider_export_version,
        "data": {
            "providers": export_data,
        },
    }


def import_llm_providers(db, payload: dict):
    """Import LLM providers from payload."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid import payload. Expected an object." )

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "llm_provider":
        raise HTTPException(status_code=400, detail=f"Unsupported export_type '{export_type}'.")

    if export_version != current_llm_provider_export_version:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_version '{export_version}'. Expected '{current_llm_provider_export_version}'.",
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

        provider_key = provider_entry.get("provider")
        try:
            provider_enum = ProviderEnum(provider_key)
        except Exception:
            errors.append({"index": index, "error": f"Unsupported provider '{provider_key}'."})
            continue

        settings_model = PROVIDER_SETTINGS_MODELS.get(provider_enum)
        if settings_model is None:
            errors.append({"index": index, "error": f"No settings schema registered for provider '{provider_key}'."})
            continue

        raw_settings = provider_entry.get("settings") or {}
        try:
            validated_settings = settings_model.model_validate(raw_settings)
        except ValidationError as exc:
            errors.append({"index": index, "error": exc.errors()})
            continue

        name = provider_entry.get("name")
        api_key_raw = provider_entry.get("api_key")
        icon = provider_entry.get("icon")

        if not isinstance(name, str) or not name.strip():
            errors.append({"index": index, "error": "Provider name is required."})
            continue

        requires_api_key = not provider_api_key_is_optional(provider_enum)

        if requires_api_key:
            if not isinstance(api_key_raw, str) or not api_key_raw.strip():
                errors.append({"index": index, "name": name, "error": "Provider api_key is required."})
                continue
            api_key = api_key_raw.strip()
        else:
            if api_key_raw is None:
                api_key = ""
            elif isinstance(api_key_raw, str):
                api_key = api_key_raw.strip()
            else:
                errors.append({"index": index, "name": name, "error": "Provider api_key must be a string if provided."})
                continue

        # Apply the same icon policy used by normal creates.  This prevents an
        # export from reintroducing a custom icon on a native provider while
        # preserving the selected icon for compatible custom endpoints.
        resolved_icon = resolve_provider_icon(provider_enum.value, icon)

        try:
            provider_obj = create_llm_provider(
                db,
                provider_enum.value,
                name.strip(),
                api_key,
                validated_settings.model_dump(exclude_unset=True),
                "unknown",
                resolved_icon,
            )
        except HTTPException as exc:
            errors.append({"index": index, "name": name, "error": exc.detail})
            continue
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})
            continue

        created.append({
            "id": provider_obj.id,
            "name": provider_obj.name,
            "provider": normalize_provider_value(provider_obj.provider),
        })

    return {
        "created": created,
        "errors": errors,
    }


def export_llm_models(db):
    """Export administrator-managed LLM models only."""
    models = db.query(Models).all()
    export_data = []

    for model in models:
        model_settings = model.settings or {}
        if normalize_provider_value(model.provider) in {
            ProviderEnum.anthropic.value,
            ProviderEnum.anthropic_base.value,
        }:
            model_settings = remove_deprecated_anthropic_request_settings(model_settings)
        export_data.append(
            {
                "id": model.id,
                "name": model.name,
                "description": model.description,
                "model_icon": model.model_icon,
                "provider": normalize_provider_value(model.provider),
                "provider_id": model.provider_id,
                "model_name": model.model_name,
                "settings": model_settings,
                "capabilities": _coerce_string_list(model.capabilities or []),
                "tools": model.tools or [],
                "access": model.access or {},
                "status": model.status,
                "is_active": bool(getattr(model, "is_active", True)),
                "created_at": _serialize_datetime_value(getattr(model, "created_at", None)),
            }
        )

    return {
        "export_type": "llm_model",
        "export_version": current_llm_model_export_version,
        "data": {
            "models": export_data,
        },
    }


def import_llm_models(db, payload: dict):
    """Import LLM models from payload."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid import payload. Expected an object.")

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "llm_model":
        raise HTTPException(status_code=400, detail=f"Unsupported export_type '{export_type}'.")

    if export_version != current_llm_model_export_version:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_version '{export_version}'. Expected '{current_llm_model_export_version}'.",
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(status_code=400, detail="Invalid export payload. Missing 'data' object.")

    raw_models = data_block.get("models")
    if not isinstance(raw_models, list):
        raise HTTPException(status_code=400, detail="Invalid export payload. 'models' must be a list.")

    created: list[dict] = []
    errors: list[dict] = []

    for index, model_entry in enumerate(raw_models):
        if not isinstance(model_entry, dict):
            errors.append({"index": index, "error": "Model entry must be an object."})
            continue

        provider_id = model_entry.get("provider_id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            errors.append({"index": index, "error": "provider_id is required."})
            continue

        try:
            provider_obj = get_llm_provider(db, provider_id.strip())
        except HTTPException as exc:
            errors.append({"index": index, "provider_id": provider_id, "error": exc.detail})
            continue

        provider_key = provider_obj.provider
        try:
            provider_enum = ProviderEnum(provider_key)
        except Exception:
            errors.append({"index": index, "provider_id": provider_id, "error": f"Unsupported provider '{provider_key}'."})
            continue

        requested_provider = model_entry.get("provider")
        if isinstance(requested_provider, str) and requested_provider.strip() and requested_provider.strip() != provider_key:
            errors.append({
                "index": index,
                "provider_id": provider_id,
                "error": f"Provider mismatch. Expected '{provider_key}'.",
            })
            continue

        settings_model = PROVIDER_MODEL_SETTINGS_MODELS.get(provider_enum)
        if settings_model is None:
            errors.append({"index": index, "provider_id": provider_id, "error": f"No settings schema registered for provider '{provider_key}'."})
            continue

        raw_settings = model_entry.get("settings") or {}
        try:
            validated_settings = settings_model.model_validate(raw_settings)
        except ValidationError as exc:
            errors.append({"index": index, "provider_id": provider_id, "error": exc.errors()})
            continue

        name = model_entry.get("name")
        description = model_entry.get("description")
        model_icon = model_entry.get("model_icon")
        model_name = model_entry.get("model_name") or model_entry.get("model")

        if not isinstance(name, str) or not name.strip():
            errors.append({"index": index, "error": "Model name is required."})
            continue
        if not isinstance(description, str) or not description.strip():
            errors.append({"index": index, "name": name, "error": "Model description is required."})
            continue
        if len(description.strip()) > 100:
            errors.append({"index": index, "name": name, "error": "Model description must be 100 characters or fewer."})
            continue
        if not isinstance(model_icon, str) or not model_icon.strip():
            errors.append({"index": index, "name": name, "error": "Model icon is required."})
            continue
        if not isinstance(model_name, str) or not model_name.strip():
            errors.append({"index": index, "name": name, "error": "Provider model identifier is required."})
            continue

        tools = _sanitize_jsonable(model_entry.get("tools"), default=[])
        settings_payload = validated_settings.model_dump(exclude_unset=True)
        from app.llm.capabilities import determine_model_capabilities

        capabilities = determine_model_capabilities(
            provider_enum,
            settings_payload,
            tools,
            model_name=model_name.strip(),
            existing_capabilities=(
                _coerce_string_list(model_entry.get("capabilities"))
                or ["completion"]
            ),
        )
        access = _normalize_model_access(model_entry.get("access"))
        status_value = model_entry.get("status") or "normal"
        is_active = bool(model_entry.get("is_active", True))
        created_at = _serialize_datetime_value(model_entry.get("created_at"))

        try:
            model_obj = create_model(
                db,
                name=name.strip(),
                description=description.strip(),
                model_icon=model_icon.strip(),
                provider=provider_key,
                provider_id=provider_id.strip(),
                model_name=model_name.strip(),
                settings=settings_payload,
                capabilities=capabilities,
                tools=tools,
                access=access,
                status=str(status_value),
                is_active=is_active,
                created_at=created_at,
            )
        except HTTPException as exc:
            errors.append({"index": index, "name": name, "error": exc.detail})
            continue
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})
            continue

        created.append(
            {
                "id": model_obj.id,
                "name": model_obj.name,
                "provider_id": provider_id.strip(),
            }
        )

    return {
        "created": created,
        "errors": errors,
    }



# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Models(Base):
    __tablename__ = "models"
    __table_args__ = (
        Index("ix_models_provider_id", "provider_id"),
        Index("ix_models_provider", "provider"),
        Index("ix_models_model_name", "model_name"),
        Index("ix_models_status", "status"),
        Index("ix_models_is_active", "is_active"),
        Index("ix_models_created_at", "created_at"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    model_icon = Column(String, nullable=False) # Model icon is saved as a preset key, SVG, or compact image data URL
    provider = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    settings = Column(JSON, nullable=True)
    capabilities = Column(JSON, nullable=False) # completion, vision, tools, thinking, documents, audio, video
    tools = Column(JSON, nullable=True)
    access = Column(JSON, nullable=False)
    meta = Column(JSON, nullable=True)
    status = Column(String, nullable=False) # either "normal", "alpha", "experimental"  
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), default=func.now())


def remove_admin_skill_from_model_settings(db, skill_id: str) -> int:
    """Remove a deleted admin skill from every model's fixed skill settings.

    Model fixed skills are persisted in the JSON ``settings`` payload as either
    the current single-select ``skill_id`` field or the legacy/multi-select
    ``skill_ids`` field.  Clearing both shapes keeps old rows and any imported
    snapshots from continuing to trust a deleted admin skill.
    """
    normalized_skill_id = str(skill_id or "").strip()
    if not normalized_skill_id:
        return 0

    updated = 0
    for model in db.query(Models).all():
        settings = model.settings
        if not isinstance(settings, dict):
            continue

        next_settings = dict(settings)
        changed = False

        if str(next_settings.get("skill_id") or "") == normalized_skill_id:
            next_settings.pop("skill_id", None)
            changed = True

        skill_ids = next_settings.get("skill_ids")
        if isinstance(skill_ids, list):
            filtered_skill_ids = [
                item for item in skill_ids
                if str(item or "").strip() != normalized_skill_id
            ]
            if len(filtered_skill_ids) != len(skill_ids):
                changed = True
                if filtered_skill_ids:
                    next_settings["skill_ids"] = filtered_skill_ids
                else:
                    next_settings.pop("skill_ids", None)

        if not changed:
            continue

        model.settings = next_settings
        flag_modified(model, "settings")
        updated += 1

    return updated



# -------------------
# Create model
# -------------------
def create_model(
    db,
    name: str,
    description: str,
    model_icon: str,
    provider: str,
    provider_id: str,
    model_name: str,
    settings: dict,
    capabilities: list,
    tools: dict,
    access: dict,
    status: str,
    is_active: bool = True,
    created_at: str | datetime | None = None,
):
    """Create an LLM model."""
    description_value = description.strip()
    if not description_value:
        raise HTTPException(status_code=400, detail="Model description is required.")
    if len(description_value) > 100:
        raise HTTPException(status_code=400, detail="Model description must be 100 characters or fewer.")

    if isinstance(created_at, datetime):
        created_at_value = created_at.isoformat()
    elif isinstance(created_at, str) and created_at.strip():
        created_at_value = created_at
    else:
        created_at_value = datetime.now(timezone.utc).isoformat()
    model = Models(
        name=name,
        description=description_value,
        model_icon=require_safe_icon_input(model_icon, fallback="omlorix"),
        provider=provider,
        provider_id=provider_id,
        model_name=model_name,
        settings=settings,
        capabilities=capabilities,
        tools=tools,
        access=access,
        status=status,
        is_active=is_active,
        created_at=created_at_value,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    _ensure_default_model_present(db, model.id, access)
    return model


def _ensure_default_model_present(
    db: SessionLocal,
    candidate_model_id: str | None,
    candidate_access: dict | None,
) -> None:
    """Ensure the global default model is set using the most recent public model."""
    if not candidate_model_id:
        return
    current_default = get_default_model_id(db)
    if current_default:
        return
    allow_everyone = False
    if isinstance(candidate_access, dict):
        allow_everyone = bool(candidate_access.get("everyone"))
    if not allow_everyone:
        return
    try:
        update_page_key_value_by_page_and_key("models", "default_model", candidate_model_id, db)
    except HTTPException:
        # If the models settings page does not yet exist, ignore silently.
        return


# -------------------
# List models
# -------------------
def list_models(db):
    """List all active models."""
    rows = db.query(Models).filter(Models.is_active == True).all()
    return rows



# -------------------
# Get model
# -------------------
def get_model(db, model_id):
    """Get a model by ID."""
    # Check if the model id is valid and get the provider
    db_model = db.query(Models).filter(Models.id == model_id, Models.is_active == True).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found!")
    return db_model



# -------------------
# Duplicate model
# -------------------
def duplicate_model(db, model_id: str):
    """Create a duplicate of the given model with identical fields except name gets a ' Copy' suffix.

    Returns the newly created model row.
    """
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    original = db.query(Models).filter(Models.id == model_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Model not found")
    duplicated = Models(
        name=f"{original.name} Copy",
        description=original.description,
        model_icon=original.model_icon,
        provider=original.provider,
        provider_id=original.provider_id,
        model_name=original.model_name,
        settings=original.settings,
        capabilities=original.capabilities,
        tools=original.tools,
        access=original.access,
        status=original.status,
        is_active=original.is_active,
    )
    db.add(duplicated)
    db.commit()
    db.refresh(duplicated)
    return duplicated


# -------------------
# Delete model
# -------------------
def delete_model(db, model_id: str | None = None, provider_id: str | None = None):
    """Delete a model by ID."""
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    model = db.query(Models).filter(Models.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if get_default_model_id(db) == model_id:
        raise HTTPException(
            status_code=409,
            detail="Update the default model before deleting this entry.",
        )
    groups = db.query(Group).all()
    for group in groups:
        settings = dict(group.settings or {})
        chat_settings = dict(settings.get("chat") or {})
        memory_settings = dict(settings.get("memories") or {})
        changed = False
        if chat_settings.get("byok_title_generation_model_id") == model_id:
            chat_settings["byok_title_generation_model_id"] = ""
            changed = True
        if memory_settings.get("memory_model_id") == model_id:
            memory_settings["memory_model_id"] = ""
            changed = True
        if changed:
            settings["chat"] = chat_settings
            settings["memories"] = memory_settings
            group.settings = settings
            group.updated_at = datetime.now(timezone.utc)
    other_models = db.query(Models).filter(Models.id != model_id).all()
    for other_model in other_models:
        model_settings = other_model.settings if isinstance(other_model.settings, dict) else {}
        if not model_settings:
            continue
        tg_model_id = model_settings.get("title_generation_model_id")
        if tg_model_id != model_id:
            continue
        tg_enabled = bool(model_settings.get("title_generation"))
        tg_mode = model_settings.get("title_generation_model")
        changed = False
        if tg_enabled and tg_mode == "specific":
            model_settings["title_generation_model_id"] = ""
            model_settings["title_generation"] = False
            changed = True
        elif tg_mode == "current":
            model_settings["title_generation_model_id"] = ""
            changed = True
        else:
            model_settings["title_generation_model_id"] = ""
            changed = True
        if changed:
            other_model.settings = model_settings
    db.query(ModelSettingPresets).filter(ModelSettingPresets.model_id == model_id).delete(synchronize_session=False)
    fallback_model_id = get_default_model_id(db)
    if not fallback_model_id:
        automation_reference = db.query(Automation.id).filter(Automation.model_id == model_id).first()
        agent_reference = db.query(UserAgent.id).filter(UserAgent.base_model_id == model_id).first()
        if automation_reference or agent_reference:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete model because it is still in use and no default model is configured.",
            )
    else:
        migrate_automations_model(db, model_id, fallback_model_id)
        migrate_user_agents_base_model(db, model_id, fallback_model_id)
    settings_cache_changed = _remove_deleted_model_from_pinned_lists(db, model_id)
    rate_limit_cleanup = _remove_deleted_model_from_rate_limits(db, model_id)
    db.delete(model)
    db.commit()
    if settings_cache_changed:
        invalidate_settings_cache()
    return {
        "deleted": True,
        "model_id": model_id,
        "rate_limit_ids_updated": rate_limit_cleanup["updated_ids"],
        "rate_limit_ids_deleted": rate_limit_cleanup["deleted_ids"],
    }


# -------------------
# Delete model by name
# -------------------
def delete_model_by_name(db, provider_id, model_name):
    """Delete a model by provider ID and model name."""
    if not isinstance(model_name, str) or not model_name:
        raise HTTPException(status_code=400, detail="Invalid model_name")
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="Invalid provider_id")
    models = db.query(Models).filter(Models.model_name == model_name, Models.provider_id == provider_id).all()
    deleted_any = False
    for model in models:
        delete_model(db, model_id=model.id)
        deleted_any = True
    return deleted_any


def _remove_deleted_model_from_pinned_lists(db, model_id: str) -> bool:
    """Remove a deleted model from admin and user pinned-model preferences."""

    settings_cache_changed = False

    settings_row = db.query(Settings).filter(Settings.page_name == "models").first()
    if settings_row and isinstance(settings_row.data, dict):
        current_default_pins = sanitize_pinned_model_ids(settings_row.data.get("default_pinned_models"))
        next_default_pins = [pinned_model_id for pinned_model_id in current_default_pins if pinned_model_id != model_id]
        if next_default_pins != current_default_pins:
            settings_row.data["default_pinned_models"] = next_default_pins
            settings_row.updated_at = datetime.now(timezone.utc)
            flag_modified(settings_row, "data")
            settings_cache_changed = True

    users = db.query(User).all()
    for user in users:
        if not isinstance(user.settings, dict):
            continue
        chat_settings = user.settings.get("chat")
        if not isinstance(chat_settings, dict):
            continue
        current_pinned_models = sanitize_pinned_model_ids(chat_settings.get("pinned_models"))
        next_pinned_models = [pinned_model_id for pinned_model_id in current_pinned_models if pinned_model_id != model_id]
        if next_pinned_models == current_pinned_models:
            continue
        chat_settings["pinned_models"] = next_pinned_models
        flag_modified(user, "settings")

    return settings_cache_changed


def update_model_entry(
    db,
    model_id: str,
    *,
    model_name: str | None = None,
    name: str | None = None,
    description: str | None,
    model_icon: str | None,
    status: str | None,
    tools=None,
    access=None,
    settings=None,
    capabilities: list[str] | None = None,
    is_active: bool | None = None,
    commit: bool = True,
):
    """Update a model entry."""
    model = db.query(Models).filter(Models.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model_name is not None:
        model_name_value = model_name.strip()
        if not model_name_value:
            raise HTTPException(status_code=400, detail="Provider model identifier is required.")
        model.model_name = model_name_value

    if name is not None:
        name_value = name.strip()
        if not name_value:
            raise HTTPException(status_code=400, detail="Model name is required.")
        model.name = name_value

    if description is not None:
        desc_value = description.strip()
        if len(desc_value) > 100:
            raise HTTPException(status_code=400, detail="Model description must be 100 characters or fewer.")
        model.description = desc_value

    if model_icon is not None:
        model.model_icon = require_safe_icon_input(model_icon, fallback="omlorix")

    if status is not None:
        model.status = status

    if tools is not None:
        model.tools = jsonable_encoder(tools)

    if access is not None:
        normalized_access = jsonable_encoder(access)
        if get_default_model_id(db) == model_id:
            allow_everyone = False
            if isinstance(normalized_access, dict):
                allow_everyone = bool(normalized_access.get("everyone"))
            elif hasattr(normalized_access, "get"):
                allow_everyone = bool(normalized_access.get("everyone"))
            if not allow_everyone:
                raise HTTPException(
                    status_code=400,
                    detail="The default model must remain visible to everyone.",
                )
        model.access = normalized_access

    if settings is not None:
        model.settings = jsonable_encoder(settings)

    if capabilities is not None:
        encoded_caps = [
            cap.strip()
            for cap in (capabilities or [])
            if isinstance(cap, str) and cap.strip()
        ]
        model.capabilities = encoded_caps or ["completion"]

    if is_active is not None:
        model.is_active = bool(is_active)

    db.add(model)
    if commit:
        db.commit()
        db.refresh(model)
    else:
        db.flush()
    return model









# ---------------------------------------------------------------------------
# LLMProvider
# ---------------------------------------------------------------------------
class ModelSettingPresets(Base):
    __tablename__ = "model_setting_presets"
    __table_args__ = (
        Index("ix_model_setting_presets_model_id", "model_id"),
        Index("ix_model_setting_presets_name", "name"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    model_id = Column(String, nullable=False)
    settings = Column(JSON, nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), default=func.now())
    created_at = Column(DateTime, nullable=False, server_default=func.now(), default=func.now())



# -------------------
# List user model setting presets
# -------------------
def list_user_model_setting_presets(db, user_id: str, model_id: str):
    """List user model setting presets for a model."""
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    rows = (
        db.query(ModelSettingPresets)
        .filter(ModelSettingPresets.user_id == user_id, ModelSettingPresets.model_id == model_id)
        .order_by(ModelSettingPresets.name.asc())
        .all()
    )
    return rows



# -------------------
# Get user model setting preset
# -------------------
def get_user_model_setting_preset(db, user_id: str, model_id: str, preset_id: str):
    """Get a user model setting preset by ID."""
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    if not isinstance(preset_id, str) or not preset_id:
        raise HTTPException(status_code=400, detail="Invalid preset_id")
    row = (
        db.query(ModelSettingPresets)
        .filter(
            ModelSettingPresets.user_id == user_id,
            ModelSettingPresets.model_id == model_id,
            ModelSettingPresets.id == preset_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Model setting preset not found")
    return row



# -------------------
# Create user model setting preset
# -------------------
def create_user_model_setting_preset(db, user_id: str, model_id: str, name: str, settings: dict):
    """Create a user model setting preset."""
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=400, detail="Invalid name")
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="Invalid settings")
    model = db.query(Models).filter(Models.id == model_id).first()
    if model and normalize_provider_value(model.provider) in {
        ProviderEnum.anthropic.value,
        ProviderEnum.anthropic_base.value,
    }:
        settings = remove_deprecated_anthropic_request_settings(settings)
    model_setting_preset = ModelSettingPresets(
        user_id=user_id,
        model_id=model_id,
        name=name,
        settings=settings,
    )
    db.add(model_setting_preset)
    db.commit()
    db.refresh(model_setting_preset)
    return model_setting_preset



def delete_user_model_setting_preset(db, user_id: str, model_id: str, preset_id: str):
    """Delete a user model setting preset."""
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    if not isinstance(preset_id, str) or not preset_id:
        raise HTTPException(status_code=400, detail="Invalid preset_id")
    model_setting_preset = db.query(ModelSettingPresets).filter(ModelSettingPresets.user_id == user_id, ModelSettingPresets.model_id == model_id, ModelSettingPresets.id == preset_id).first()
    if not model_setting_preset:
        raise HTTPException(status_code=404, detail="Model setting preset not found")
    db.delete(model_setting_preset)
    db.commit()
    return {"deleted": True, "preset_id": preset_id}



# ---------------------------------------------------------------------------
# LLMProviderGroup - Load balancing groups of providers
# ---------------------------------------------------------------------------
class LLMProviderGroup(Base):
    __tablename__ = "llm_provider_group"
    __table_args__ = (
        Index("ix_llm_provider_group_name", "name"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    icon = Column(String, nullable=True)
    members = Column(JSON, nullable=False, default=list)  # [{provider_id: str, weight: int}, ...]
    created_at = Column(DateTime, nullable=False, server_default=func.now(), default=func.now())


def _validate_provider_group_members(db, members: list) -> list:
    """Validate and normalize provider group members."""
    if not isinstance(members, list) or len(members) < 2:
        raise HTTPException(status_code=400, detail="Provider group must have at least 2 members")

    seen_ids = set()
    validated = []
    group_provider_type: str | None = None
    model_capable_provider_values = {
        provider.value for provider in MODEL_CAPABLE_PROVIDERS
    }

    for member in members:
        if not isinstance(member, dict):
            raise HTTPException(status_code=400, detail="Each member must be an object with provider_id and weight")

        
        provider_id = member.get("provider_id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise HTTPException(status_code=400, detail="Each member must have a valid provider_id")
        
        provider_id = provider_id.strip()
        
        # Check provider exists
        provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=400, detail=f"Provider '{provider_id}' not found")

        provider_type = normalize_provider_value(provider.provider)
        if not provider_type:
            raise HTTPException(status_code=400, detail=f"Provider '{provider_id}' is missing a provider type")
        if provider_type not in model_capable_provider_values:
            raise HTTPException(
                status_code=400,
                detail="provider_group_provider_not_model_capable",
            )
        if group_provider_type is None:
            group_provider_type = provider_type
        elif provider_type != group_provider_type:
            raise HTTPException(
                status_code=400,
                detail=(
                    "All providers in a group must share the same provider type. "
                    f"Expected '{group_provider_type}' but received '{provider_type}' from provider '{provider_id}'."
                ),
            )

        if provider_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate provider '{provider_id}' in group")
        seen_ids.add(provider_id)

        
        weight = member.get("weight", 1)
        if not isinstance(weight, int) or weight < 1:
            weight = 1
        
        validated.append({"provider_id": provider_id, "weight": weight})
    
    return validated


# -------------------
# Create provider group
# -------------------
def create_provider_group(db, name: str, members: list, icon: str | None = None):
    """Create a provider group."""
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Provider group name is required")
    
    name = name.strip()
    
    existing = db.query(LLMProviderGroup).filter(LLMProviderGroup.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Provider group with this name already exists")
    
    validated_members = _validate_provider_group_members(db, members)
    
    group = LLMProviderGroup(
        name=name,
        icon=require_safe_icon_input(icon, fallback="omlorix") if icon else None,
        members=validated_members,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


# -------------------
# Get provider group
# -------------------
def get_provider_group(db, group_id: str):
    """Get a provider group by ID or name."""
    if not isinstance(group_id, str) or not group_id.strip():
        raise HTTPException(status_code=400, detail="Invalid group_id")
    
    group = db.query(LLMProviderGroup).filter(LLMProviderGroup.id == group_id).first()
    if not group:
        # Try by name
        group = db.query(LLMProviderGroup).filter(LLMProviderGroup.name == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Provider group not found")
    return group


# -------------------
# List provider groups
# -------------------
def list_provider_groups(db):
    """List all provider groups."""
    return db.query(LLMProviderGroup).order_by(LLMProviderGroup.name.asc()).all()


# -------------------
# Update provider group
# -------------------
def update_provider_group(db, group_id: str, name: str | None = None, members: list | None = None, icon: str | None = None):
    """Update a provider group."""
    group = get_provider_group(db, group_id)
    
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Provider group name cannot be empty")
        
        conflict = db.query(LLMProviderGroup).filter(
            LLMProviderGroup.name == name,
            LLMProviderGroup.id != group.id
        ).first()
        if conflict:
            raise HTTPException(status_code=400, detail="Provider group with this name already exists")
        group.name = name
    
    if members is not None:
        validated_members = _validate_provider_group_members(db, members)
        group.members = validated_members
    
    if icon is not None:
        group.icon = require_safe_icon_input(icon, fallback="omlorix")
    
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


# -------------------
# Delete provider group
# -------------------
def delete_provider_group(db, group_id: str):
    """Delete a provider group."""
    group = get_provider_group(db, group_id)
    
    # Check if any models use this group
    models_using_group = db.query(Models).filter(Models.provider_id == group_id).all()
    if models_using_group:
        model_names = [model.name for model in models_using_group]
        models_list = ", ".join(model_names)
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete provider group. {len(models_using_group)} model(s) are using this group: {models_list}"
        )
    
    db.delete(group)
    db.commit()
    return {"deleted": True, "group_id": group_id}


# -------------------
# Get provider groups containing a specific provider
# -------------------
def get_provider_groups_for_provider(db, provider_id: str) -> list:
    """Find all provider groups that contain the given provider_id as a member."""
    if not isinstance(provider_id, str) or not provider_id.strip():
        return []
    
    provider_id = provider_id.strip()
    all_groups = db.query(LLMProviderGroup).all()
    matching_groups = []
    
    for group in all_groups:
        members = group.members or []
        for member in members:
            if isinstance(member, dict) and member.get("provider_id") == provider_id:
                other_members = [m for m in members if m.get("provider_id") != provider_id]
                matching_groups.append({
                    "id": group.id,
                    "name": group.name,
                    "icon": group.icon,
                    "member_count": len(members),
                    "other_member_count": len(other_members),
                })
                break
    
    return matching_groups


# -------------------
# Remove provider from groups or delete groups as needed
# -------------------
def remove_provider_from_groups(db, provider_id: str) -> dict:
    """
    Remove a provider from all groups it belongs to.
    If a group would have fewer than 2 members after removal, delete the group entirely.
    
    Returns a summary of actions taken.
    """
    if not isinstance(provider_id, str) or not provider_id.strip():
        return {"updated_groups": [], "deleted_groups": []}
    
    provider_id = provider_id.strip()
    all_groups = db.query(LLMProviderGroup).all()
    
    updated_groups = []
    deleted_groups = []
    
    for group in all_groups:
        members = group.members or []
        is_member = any(
            isinstance(m, dict) and m.get("provider_id") == provider_id
            for m in members
        )
        
        if not is_member:
            continue
        
        remaining_members = [m for m in members if m.get("provider_id") != provider_id]
        
        if len(remaining_members) < 2:
            # Group would have fewer than 2 members - delete it
            # First check if any models use this group
            models_using_group = db.query(Models).filter(Models.provider_id == group.id).all()
            for model in models_using_group:
                # Delete models that use this group
                delete_model(db, model_id=model.id)
            
            db.delete(group)
            deleted_groups.append({
                "id": group.id,
                "name": group.name,
            })
        else:
            # Update group with remaining members
            group.members = remaining_members
            db.add(group)
            updated_groups.append({
                "id": group.id,
                "name": group.name,
                "remaining_members": len(remaining_members),
            })
    
    db.commit()
    return {
        "updated_groups": updated_groups,
        "deleted_groups": deleted_groups,
    }


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------
RATE_LIMIT_SCOPE_CHAT = "chat"
RATE_LIMIT_TARGET_TYPE_MODEL = "model"
RATE_LIMIT_TARGET_TYPE_TOOL = "tool"
RATE_LIMIT_TARGET_TYPE_DICTATION = "dictation"
RATE_LIMIT_TARGET_TYPE_REALTIME = "realtime"
RATE_LIMIT_TARGET_TYPES = {
    RATE_LIMIT_TARGET_TYPE_MODEL,
    RATE_LIMIT_TARGET_TYPE_TOOL,
    RATE_LIMIT_TARGET_TYPE_DICTATION,
    RATE_LIMIT_TARGET_TYPE_REALTIME,
}
RATE_LIMIT_QUOTA_UNIT_REQUESTS = "requests"
RATE_LIMIT_QUOTA_UNIT_TOKENS = "tokens"
RATE_LIMIT_QUOTA_UNIT_INVOCATIONS = "invocations"
RATE_LIMIT_QUOTA_UNIT_MINUTES = "minutes"
RATE_LIMIT_QUOTA_UNITS = {
    RATE_LIMIT_QUOTA_UNIT_REQUESTS,
    RATE_LIMIT_QUOTA_UNIT_TOKENS,
    RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
    RATE_LIMIT_QUOTA_UNIT_MINUTES,
}
RATE_LIMIT_ADMISSION_OPEN = "open"
RATE_LIMIT_ADMISSION_COMPLETED = "completed"
RATE_LIMIT_ADMISSION_FAILED = "failed"
RATE_LIMIT_BLOCK_REASON_IN_FLIGHT = "in_flight"
RATE_LIMIT_ADMISSION_ACTION_MESSAGE = "message"
RATE_LIMIT_ADMISSION_ACTION_REGENERATE = "regenerate"
RATE_LIMIT_ADMISSION_ACTION_CONTINUE = "continue"
RATE_LIMIT_ADMISSION_ACTIONS = {
    RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
    RATE_LIMIT_ADMISSION_ACTION_REGENERATE,
    RATE_LIMIT_ADMISSION_ACTION_CONTINUE,
}
SUPPORTED_CHAT_TOKEN_ACCOUNTING_PROVIDERS = {
    "anthropic",
    "anthropic_base",
    "google_aistudio",
    "lmstudio",
    "microsoft_azure",
    "ollama",
    "openai",
    "openai_chat_completions",
    "openai_responses",
    "openrouter",
}


@dataclass(frozen=True)
class RateLimitAdmissionContext:
    admission_id: str
    rate_limit_id: str
    user_id: str
    quota_unit: str
    quota_value: int
    window_start: datetime
    window_end: datetime
    action_type: str


@dataclass(frozen=True)
class DurationRateLimitAdmissionContext:
    """An atomic reservation against a minute-based feature quota."""

    admission_id: str
    rate_limit_id: str
    target_type: str
    user_id: str
    reserved_seconds: int
    window_start: datetime
    window_end: datetime


_current_rate_limit_admission_context: ContextVar[RateLimitAdmissionContext | None] = ContextVar(
    "current_rate_limit_admission_context",
    default=None,
)


class RateLimit(Base):
    __tablename__ = "rate_limits"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    target_type = Column(String, nullable=False, default=RATE_LIMIT_TARGET_TYPE_MODEL)
    model_ids = Column(JSON, nullable=False)
    tool_keys = Column(JSON, nullable=False, default=list)
    user_ids = Column(JSON, nullable=False, default=list)
    group_ids = Column(JSON, nullable=False, default=list)
    scope = Column(String, nullable=False, default=RATE_LIMIT_SCOPE_CHAT)
    period = Column(String, nullable=False)
    timezone = Column(String, nullable=False, default=DEFAULT_RATE_LIMIT_TIMEZONE)
    quota_unit = Column(String, nullable=False, default=RATE_LIMIT_QUOTA_UNIT_REQUESTS)
    quota_value = Column(BigInteger, nullable=False, default=0)
    max_requests = Column(Integer, nullable=False)
    max_input_tokens = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    max_total_tokens = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class RateLimitChatAdmission(Base):
    __tablename__ = "rate_limit_chat_admissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    rate_limit_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    group_id = Column(String, nullable=True, index=True)
    selected_model_id = Column(String, nullable=False, index=True)
    chat_id = Column(String, nullable=True, index=True)
    user_message_id = Column(String, nullable=True, index=True)
    action_type = Column(String, nullable=False)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False)
    quota_unit = Column(String, nullable=False)
    quota_value = Column(BigInteger, nullable=False)
    usage_snapshot_before = Column(JSON, nullable=False, default=dict)
    status = Column(String, nullable=False, default=RATE_LIMIT_ADMISSION_OPEN, index=True)
    request_counted = Column(Boolean, nullable=False, default=False)
    admitted_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at = Column(DateTime, nullable=True)
    overshot_budget = Column(Boolean, nullable=False, default=False)
    overshoot_amount = Column(BigInteger, nullable=False, default=0)


class RateLimitUsageWindow(Base):
    __tablename__ = "rate_limit_usage_windows"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    rate_limit_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    window_start = Column(DateTime, nullable=False, index=True)
    request_count = Column(BigInteger, nullable=False, default=0)
    token_count = Column(BigInteger, nullable=False, default=0)
    invocation_count = Column(BigInteger, nullable=False, default=0)
    # Feature quotas are configured in whole minutes but stored in seconds so
    # short dictations and calls are accounted for accurately.
    duration_seconds = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("rate_limit_id", "user_id", "window_start", name="uq_rate_limit_usage_window"),
    )


class RateLimitDurationAdmission(Base):
    """Track in-flight reservations for dictation and realtime usage.

    Reserving before provider work prevents parallel browser tabs from each
    seeing and spending the same remaining minute budget. Finalization moves
    only actual elapsed time into the durable usage window and releases the
    unused portion of the reservation.
    """

    __tablename__ = "rate_limit_duration_admissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    rate_limit_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    group_id = Column(String, nullable=True, index=True)
    target_type = Column(String, nullable=False, index=True)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False)
    reserved_seconds = Column(BigInteger, nullable=False)
    consumed_seconds = Column(BigInteger, nullable=False, default=0)
    status = Column(String, nullable=False, default=RATE_LIMIT_ADMISSION_OPEN, index=True)
    admitted_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    # Realtime heartbeats renew this lease. If the browser or application
    # process disappears, a later admission can reclaim the stranded
    # reservation instead of blocking the user for the whole quota window.
    last_activity_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at = Column(DateTime, nullable=True)


def _delete_rate_limit_record(db, rate_limit: RateLimit) -> None:
    """Delete a configured rate limit and its operational tracking rows.

    Rate-limit admissions and usage windows intentionally use plain string IDs
    instead of database foreign keys. Cleaning them up explicitly prevents
    orphaned operational rows whenever a configured limit is removed.
    """

    db.query(RateLimitChatAdmission).filter(
        RateLimitChatAdmission.rate_limit_id == rate_limit.id
    ).delete(synchronize_session=False)
    db.query(RateLimitUsageWindow).filter(
        RateLimitUsageWindow.rate_limit_id == rate_limit.id
    ).delete(synchronize_session=False)
    db.query(RateLimitDurationAdmission).filter(
        RateLimitDurationAdmission.rate_limit_id == rate_limit.id
    ).delete(synchronize_session=False)
    db.delete(rate_limit)


def _remove_deleted_model_from_rate_limits(db, model_id: str) -> dict[str, list[str]]:
    """Remove a deleted model from every configured model rate limit.

    Limits that still target another model are updated in place so their
    configuration and accumulated usage remain intact. A model limit with no
    remaining models is no longer meaningful, so it and its operational rows
    are deleted in the same transaction as the model.
    """

    updated_ids: list[str] = []
    deleted_ids: list[str] = []
    model_rate_limits = (
        db.query(RateLimit)
        .filter(RateLimit.target_type == RATE_LIMIT_TARGET_TYPE_MODEL)
        .all()
    )

    for rate_limit in model_rate_limits:
        current_model_ids = list(rate_limit.model_ids or [])
        remaining_model_ids = [
            configured_model_id
            for configured_model_id in current_model_ids
            if configured_model_id != model_id
        ]
        if remaining_model_ids == current_model_ids:
            continue

        if remaining_model_ids:
            # Assign a new list so SQLAlchemy reliably persists the JSON change.
            rate_limit.model_ids = remaining_model_ids
            db.add(rate_limit)
            updated_ids.append(rate_limit.id)
            continue

        deleted_ids.append(rate_limit.id)
        _delete_rate_limit_record(db, rate_limit)

    return {"updated_ids": updated_ids, "deleted_ids": deleted_ids}


def _session_dialect_name(db) -> str:
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower()


def _rate_limit_window_lock_identity(rate_limit_id: str, user_id: str, window_start: datetime) -> str:
    normalized_rate_limit_id = str(rate_limit_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    serialized_window_start = _serialize_datetime_value(window_start) or ""
    if not normalized_rate_limit_id or not normalized_user_id or not serialized_window_start:
        return ""
    return f"{normalized_rate_limit_id}:{normalized_user_id}:{serialized_window_start}"


def _rate_limit_window_lock_key(lock_identity: str) -> int:
    digest = hashlib.sha256(f"{_RATE_LIMIT_WINDOW_LOCK_PREFIX}{lock_identity}".encode("utf-8")).digest()
    unsigned_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    if unsigned_value >= 2**63:
        return unsigned_value - 2**64
    return unsigned_value


def _local_rate_limit_window_lock(lock_identity: str) -> threading.RLock:
    with _RATE_LIMIT_WINDOW_LOCKS_GUARD:
        lock = _RATE_LIMIT_WINDOW_LOCKS.get(lock_identity)
        if lock is None:
            lock = threading.RLock()
            _RATE_LIMIT_WINDOW_LOCKS[lock_identity] = lock
        return lock


@contextmanager
def serialized_rate_limit_window_admission(
    db,
    *,
    rate_limit_id: str,
    user_id: str,
    window_start: datetime,
):
    """Serialize rate-limit quota checks and reservations for one user/window."""
    lock_identity = _rate_limit_window_lock_identity(rate_limit_id, user_id, window_start)
    if not lock_identity:
        yield
        return

    if _session_dialect_name(db) == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _rate_limit_window_lock_key(lock_identity)},
        )
        yield
        return

    lock = _local_rate_limit_window_lock(lock_identity)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


_RATE_LIMIT_PERIODS = {"day", "week", "month"}


def _normalize_rate_limit_ids(raw_value: Any, field_name: str, *, allow_empty: bool = True) -> list[str]:
    """Normalize rate limit IDs to a list of unique strings."""
    values = _coerce_string_list(raw_value)
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if not allow_empty and not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} must contain at least one value")

    return normalized


def _validate_rate_limit_name(name: str) -> str:
    """Validate rate limit name."""
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Rate limit name is required")
    return name.strip()


def _validate_rate_limit_period(period: str) -> str:
    """Validate rate limit period."""
    raw_period = getattr(period, "value", period)
    period_value = str(raw_period or "").strip().lower()
    if period_value not in _RATE_LIMIT_PERIODS:
        raise HTTPException(status_code=400, detail="Rate limit period must be one of: day, week, month")
    return period_value


def _validate_rate_limit_timezone(timezone_name: str | None) -> str:
    timezone_value = str(timezone_name or "").strip() or DEFAULT_RATE_LIMIT_TIMEZONE
    try:
        ZoneInfo(timezone_value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Rate limit timezone must be a valid IANA timezone") from exc
    return timezone_value


def _validate_rate_limit_scope(scope: str | None) -> str:
    scope_value = str(scope or RATE_LIMIT_SCOPE_CHAT).strip().lower()
    if scope_value != RATE_LIMIT_SCOPE_CHAT:
        raise HTTPException(status_code=400, detail="Unsupported rate limit scope")
    return scope_value


def _validate_rate_limit_target_type(target_type: str | None) -> str:
    target_type_value = str(target_type or RATE_LIMIT_TARGET_TYPE_MODEL).strip().lower()
    if target_type_value not in RATE_LIMIT_TARGET_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Rate limit target_type must be one of: model, tool, dictation, realtime",
        )
    return target_type_value


def _validate_rate_limit_quota_unit(quota_unit: str) -> str:
    quota_unit_value = str(quota_unit or "").strip().lower()
    if quota_unit_value not in RATE_LIMIT_QUOTA_UNITS:
        raise HTTPException(
            status_code=400,
            detail="Rate limit quota_unit must be one of: requests, tokens, invocations, minutes",
        )
    return quota_unit_value


def _validate_rate_limit_quota_value(quota_value: int) -> int:
    if not isinstance(quota_value, int) or quota_value <= 0:
        raise HTTPException(status_code=400, detail="Rate limit quota_value must be greater than 0")
    return quota_value


def _validate_rate_limit_max_requests(max_requests: int) -> int:
    """Validate rate limit max_requests."""
    if not isinstance(max_requests, int) or max_requests <= 0:
        raise HTTPException(status_code=400, detail="Rate limit max_requests must be greater than 0")
    return max_requests


def _coerce_rate_limit_quota_fields(
    *,
    quota_unit: str | None,
    quota_value: int | None,
    max_requests: int | None,
) -> tuple[str, int]:
    if quota_unit is None and quota_value is None and max_requests is not None:
        return RATE_LIMIT_QUOTA_UNIT_REQUESTS, _validate_rate_limit_max_requests(max_requests)
    if quota_unit is None and quota_value is not None:
        raise HTTPException(status_code=400, detail="quota_unit is required when quota_value is provided")
    quota_unit_value = _validate_rate_limit_quota_unit(quota_unit or RATE_LIMIT_QUOTA_UNIT_REQUESTS)
    if quota_value is None:
        if quota_unit_value == RATE_LIMIT_QUOTA_UNIT_REQUESTS and max_requests is not None:
            quota_value = _validate_rate_limit_max_requests(max_requests)
        else:
            raise HTTPException(status_code=400, detail="quota_value is required")
    return quota_unit_value, _validate_rate_limit_quota_value(quota_value)


def _validate_rate_limit_targets(user_ids: list[str], group_ids: list[str]) -> None:
    """Validate rate limit targets."""
    if not user_ids and not group_ids:
        raise HTTPException(status_code=400, detail="Rate limit must target at least one user or group")


def _validate_rate_limit_model_ids(db, model_ids: Any) -> list[str]:
    """Validate rate limit model IDs."""
    normalized = _normalize_rate_limit_ids(model_ids, "model_ids", allow_empty=False)
    existing_ids = {
        row_id
        for (row_id,) in db.query(Models.id).filter(Models.id.in_(normalized)).all()
    }
    missing = [model_id for model_id in normalized if model_id not in existing_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model ids: {', '.join(missing)}",
        )
    return normalized


def _validate_rate_limit_tool_keys(db, tool_keys: Any) -> list[str]:
    normalized = _normalize_rate_limit_ids(tool_keys, "tool_keys", allow_empty=False)
    try:
        from app.tools.registry import list_rate_limit_tool_keys

        known_tool_keys = set(list_rate_limit_tool_keys(db))
    except Exception:
        logger.warning("Failed to load tool registry while validating rate limit tool keys", exc_info=True)
        known_tool_keys = set()
    missing = [tool_key for tool_key in normalized if tool_key not in known_tool_keys]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool keys: {', '.join(missing)}",
        )
    return normalized


def _validate_rate_limit_user_ids(db, user_ids: Any) -> list[str]:
    """Validate rate limit user IDs."""
    normalized = _normalize_rate_limit_ids(user_ids, "user_ids", allow_empty=True)
    if not normalized:
        return normalized
    existing_ids = {
        row_id
        for (row_id,) in db.query(User.id).filter(User.id.in_(normalized)).all()
    }
    missing = [user_id for user_id in normalized if user_id not in existing_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown user ids: {', '.join(missing)}",
        )
    return normalized


def _validate_rate_limit_group_ids(db, group_ids: Any) -> list[str]:
    """Validate rate limit group IDs."""
    normalized = _normalize_rate_limit_ids(group_ids, "group_ids", allow_empty=True)
    if not normalized:
        return normalized
    existing_ids = {
        row_id
        for (row_id,) in db.query(Group.id).filter(Group.id.in_(normalized)).all()
    }
    missing = [group_id for group_id in normalized if group_id not in existing_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown group ids: {', '.join(missing)}",
        )
    return normalized


def _prepare_rate_limit_payload(
    db,
    *,
    name: str,
    model_ids: Any,
    user_ids: Any,
    group_ids: Any,
    scope: str | None,
    period: str,
    timezone_name: str | None,
    quota_unit: str | None,
    quota_value: int | None,
    target_type: str | None = None,
    tool_keys: Any = None,
    max_requests: int | None = None,
) -> dict:
    """Prepare and validate rate limit payload."""
    target_type_value = _validate_rate_limit_target_type(target_type)
    quota_unit_value, quota_value_value = _coerce_rate_limit_quota_fields(
        quota_unit=quota_unit,
        quota_value=quota_value,
        max_requests=max_requests,
    )

    if target_type_value == RATE_LIMIT_TARGET_TYPE_MODEL:
        normalized_model_ids = _validate_rate_limit_model_ids(db, model_ids)
        normalized_tool_keys: list[str] = []
        if quota_unit_value in {RATE_LIMIT_QUOTA_UNIT_INVOCATIONS, RATE_LIMIT_QUOTA_UNIT_MINUTES}:
            raise HTTPException(status_code=400, detail="Model rate limits must use requests or tokens")
    elif target_type_value == RATE_LIMIT_TARGET_TYPE_TOOL:
        normalized_model_ids = []
        normalized_tool_keys = _validate_rate_limit_tool_keys(db, tool_keys)
        if quota_unit_value != RATE_LIMIT_QUOTA_UNIT_INVOCATIONS:
            raise HTTPException(status_code=400, detail="Tool rate limits must use quota_unit=invocations")
    else:
        # Dictation and realtime are singleton feature targets. Their target is
        # represented by target_type itself, so no model or tool IDs are stored.
        normalized_model_ids = []
        normalized_tool_keys = []
        if quota_unit_value != RATE_LIMIT_QUOTA_UNIT_MINUTES:
            raise HTTPException(
                status_code=400,
                detail="Dictation and realtime rate limits must use quota_unit=minutes",
            )

    normalized_payload = {
        "name": _validate_rate_limit_name(name),
        "target_type": target_type_value,
        "model_ids": normalized_model_ids,
        "tool_keys": normalized_tool_keys,
        "user_ids": _validate_rate_limit_user_ids(db, user_ids),
        "group_ids": _validate_rate_limit_group_ids(db, group_ids),
        "scope": _validate_rate_limit_scope(scope),
        "period": _validate_rate_limit_period(period),
        "timezone": _validate_rate_limit_timezone(timezone_name),
        "quota_unit": quota_unit_value,
        "quota_value": quota_value_value,
        "max_requests": quota_value_value if quota_unit_value == RATE_LIMIT_QUOTA_UNIT_REQUESTS else 1,
    }
    if target_type_value == RATE_LIMIT_TARGET_TYPE_MODEL and quota_unit_value == RATE_LIMIT_QUOTA_UNIT_TOKENS:
        _validate_rate_limit_models_support_token_quotas(db, normalized_payload["model_ids"])
    _validate_rate_limit_targets(normalized_payload["user_ids"], normalized_payload["group_ids"])
    return normalized_payload


def _get_window_bounds(
    period: str,
    timezone_name: str | None = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Get UTC window bounds for a rate limit period anchored to a configured timezone."""
    timezone_value = _validate_rate_limit_timezone(timezone_name)
    tz_info = ZoneInfo(timezone_value)
    utc_now = now or datetime.now(timezone.utc)
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    else:
        utc_now = utc_now.astimezone(timezone.utc)
    local_now = utc_now.astimezone(tz_info)

    if period == "day":
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
    elif period == "week":
        monday = local_now - timedelta(days=local_now.weekday())
        local_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(weeks=1)
    elif period == "month":
        local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if local_start.month == 12:
            local_end = local_start.replace(year=local_start.year + 1, month=1)
        else:
            local_end = local_start.replace(month=local_start.month + 1)
    else:
        raise HTTPException(status_code=400, detail="Unsupported rate limit period")

    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _get_window_start(period: str, timezone_name: str | None = None) -> datetime:
    """Get the UTC start of the time window for a period."""
    return _get_window_bounds(period, timezone_name)[0]


def _get_window_end(period: str, timezone_name: str | None = None) -> datetime:
    """Get the UTC end of the time window for a period."""
    return _get_window_bounds(period, timezone_name)[1]


def supports_chat_token_accounting_for_provider(provider: str | None) -> bool:
    provider_value = normalize_provider_value(str(provider or "").strip().lower())
    return provider_value in SUPPORTED_CHAT_TOKEN_ACCOUNTING_PROVIDERS


def _validate_rate_limit_models_support_token_quotas(db, model_ids: list[str]) -> None:
    unsupported_model_ids: list[str] = []
    model_lookup = {
        model.id: model
        for model in db.query(Models).filter(Models.id.in_(model_ids)).all()
    }
    for model_id in model_ids:
        model = model_lookup.get(model_id)
        if not model or not supports_chat_token_accounting_for_provider(getattr(model, "provider", None)):
            unsupported_model_ids.append(model_id)
    if unsupported_model_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Token-based rate limits are not supported for models: {', '.join(unsupported_model_ids)}",
        )


def _matching_rate_limit_query(db):
    return (
        db.query(RateLimit)
        .filter(
            RateLimit.is_active == True,
            RateLimit.scope == RATE_LIMIT_SCOPE_CHAT,
        )
        .order_by(RateLimit.created_at.desc(), RateLimit.id.desc())
    )


def rate_limit_targets_user(rate_limit: RateLimit, user_id: str, group_id: str | None) -> bool:
    """Return whether a rate-limit assignment targets a user or their group."""

    user_match = user_id in set(rate_limit.user_ids or [])
    group_match = bool(group_id) and group_id in set(rate_limit.group_ids or [])
    return bool(user_match or group_match)


def has_applicable_rate_limits(db, user_id: str, group_id: str | None) -> bool:
    """Return whether any active chat rate limit applies to the account.

    This lightweight check is used by the initial chat setup response to make
    the settings sidebar stable before the detailed usage cards are requested.
    """

    normalized_user_id = str(user_id or "").strip()
    normalized_group_id = str(group_id or "").strip()
    if not normalized_user_id:
        return False

    bind = db.get_bind()
    dialect_name = str(getattr(getattr(bind, "dialect", None), "name", "") or "")

    if dialect_name == "postgresql":
        # Production stores assignments as JSON arrays. JSONB containment keeps
        # the membership test inside PostgreSQL and returns after the first
        # match instead of materializing every active policy in application
        # memory during chat bootstrap.
        membership_clauses = [
            cast(RateLimit.user_ids, JSONB).contains([normalized_user_id]),
        ]
        if normalized_group_id:
            membership_clauses.append(
                cast(RateLimit.group_ids, JSONB).contains([normalized_group_id])
            )
    else:
        # SQLite is the supported local-development fallback. Its JSON1
        # table-valued function provides exact array membership without the
        # false positives of string/LIKE comparisons.
        user_members = func.json_each(RateLimit.user_ids).table_valued("value").alias(
            "rate_limit_user_members"
        )
        membership_clauses = [
            exists(
                select(1)
                .select_from(user_members)
                .where(user_members.c.value == normalized_user_id)
            )
        ]
        if normalized_group_id:
            group_members = func.json_each(RateLimit.group_ids).table_valued("value").alias(
                "rate_limit_group_members"
            )
            membership_clauses.append(
                exists(
                    select(1)
                    .select_from(group_members)
                    .where(group_members.c.value == normalized_group_id)
                )
            )

    return (
        db.query(RateLimit.id)
        .filter(
            RateLimit.is_active.is_(True),
            RateLimit.scope == RATE_LIMIT_SCOPE_CHAT,
            or_(*membership_clauses),
        )
        .first()
        is not None
    )


def _rate_limit_applies_to_user(rate_limit: RateLimit, user_id: str, group_id: str | None, model_id: str) -> bool:
    if getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) != RATE_LIMIT_TARGET_TYPE_MODEL:
        return False
    model_ids = list(rate_limit.model_ids or [])
    if model_id not in model_ids:
        return False
    return rate_limit_targets_user(rate_limit, user_id, group_id)


def _tool_rate_limit_applies_to_user(rate_limit: RateLimit, user_id: str, group_id: str | None, tool_key: str) -> bool:
    if getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) != RATE_LIMIT_TARGET_TYPE_TOOL:
        return False
    tool_keys = set(rate_limit.tool_keys or [])
    if tool_key not in tool_keys:
        return False
    return rate_limit_targets_user(rate_limit, user_id, group_id)


def _feature_rate_limit_applies_to_user(
    rate_limit: RateLimit,
    user_id: str,
    group_id: str | None,
    target_type: str,
) -> bool:
    """Return whether a singleton feature policy applies to this user."""
    if getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) != target_type:
        return False
    return rate_limit_targets_user(rate_limit, user_id, group_id)


def _select_matching_rate_limit(db, user_id: str, group_id: str | None, model_id: str) -> RateLimit | None:
    for rate_limit in _matching_rate_limit_query(db).all():
        if _rate_limit_applies_to_user(rate_limit, user_id, group_id, model_id):
            return rate_limit
    return None


def _select_matching_tool_rate_limit(db, user_id: str, group_id: str | None, tool_key: str) -> RateLimit | None:
    for rate_limit in _matching_rate_limit_query(db).all():
        if _tool_rate_limit_applies_to_user(rate_limit, user_id, group_id, tool_key):
            return rate_limit
    return None


def _select_matching_feature_rate_limit(
    db,
    user_id: str,
    group_id: str | None,
    target_type: str,
) -> RateLimit | None:
    """Select the newest active dictation or realtime policy for a user."""
    normalized_target = _validate_rate_limit_target_type(target_type)
    if normalized_target not in {RATE_LIMIT_TARGET_TYPE_DICTATION, RATE_LIMIT_TARGET_TYPE_REALTIME}:
        raise HTTPException(status_code=400, detail="Unsupported minute-based rate limit target")
    for rate_limit in _matching_rate_limit_query(db).all():
        if _feature_rate_limit_applies_to_user(rate_limit, user_id, group_id, normalized_target):
            return rate_limit
    return None


def _coerce_usage_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _extract_counted_tokens_from_meta(meta: dict | None) -> tuple[int, int, int]:
    meta = meta if isinstance(meta, dict) else {}
    input_tokens = _coerce_usage_int(meta.get("input_tokens"))
    output_tokens = _coerce_usage_int(meta.get("output_tokens"))
    total_tokens = _coerce_usage_int(meta.get("total_tokens"))
    counted_tokens = input_tokens + output_tokens
    if counted_tokens <= 0 and total_tokens > 0:
        counted_tokens = total_tokens
    return input_tokens, output_tokens, counted_tokens


def normalize_rate_limit_token_usage(meta: dict | None) -> dict[str, int]:
    input_tokens, output_tokens, counted_tokens = _extract_counted_tokens_from_meta(meta)
    return {
        "counted_input_tokens": input_tokens,
        "counted_output_tokens": output_tokens,
        "counted_tokens": counted_tokens,
    }


def _build_rate_limit_usage_fallback(db, rate_limit: RateLimit, user_id: str, window_start: datetime, window_end: datetime) -> dict[str, int]:
    if getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) == RATE_LIMIT_TARGET_TYPE_TOOL:
        return {
            "request_count": 0,
            "token_count": 0,
            "invocation_count": 0,
            "duration_seconds": 0,
        }

    if getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) in {
        RATE_LIMIT_TARGET_TYPE_DICTATION,
        RATE_LIMIT_TARGET_TYPE_REALTIME,
    }:
        return {
            "request_count": 0,
            "token_count": 0,
            "invocation_count": 0,
            "duration_seconds": 0,
        }

    stats = (
        db.query(LLMGenerationStatistic)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.model_id.in_(list(rate_limit.model_ids or [])),
            LLMGenerationStatistic.created_at >= window_start,
            LLMGenerationStatistic.created_at < window_end,
        )
        .all()
    )

    request_count = 0
    token_count = 0
    for stat in stats:
        status = stat.status if isinstance(stat.status, dict) else {}
        error_flag = status.get("error")
        if error_flag in (None, False, "false"):
            request_count += 1
        counted_tokens = _coerce_usage_int(getattr(stat, "counted_tokens", 0))
        if counted_tokens <= 0:
            counted_tokens = _extract_counted_tokens_from_meta(stat.meta)[2]
        token_count += counted_tokens

    return {
        "request_count": request_count,
        "token_count": token_count,
        "invocation_count": 0,
        "duration_seconds": 0,
    }


def _get_or_backfill_usage_window(
    db,
    *,
    rate_limit: RateLimit,
    user_id: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[RateLimitUsageWindow | None, dict[str, int]]:
    usage_window = (
        db.query(RateLimitUsageWindow)
        .filter(
            RateLimitUsageWindow.rate_limit_id == rate_limit.id,
            RateLimitUsageWindow.user_id == user_id,
            RateLimitUsageWindow.window_start == window_start,
        )
        .first()
    )
    if usage_window:
        return usage_window, {
            "request_count": _coerce_usage_int(usage_window.request_count),
            "token_count": _coerce_usage_int(usage_window.token_count),
            "invocation_count": _coerce_usage_int(getattr(usage_window, "invocation_count", 0)),
            "duration_seconds": _coerce_usage_int(getattr(usage_window, "duration_seconds", 0)),
        }
    return None, _build_rate_limit_usage_fallback(db, rate_limit, user_id, window_start, window_end)


def _get_current_usage_for_quota_unit(quota_unit: str, usage_counts: dict[str, int]) -> int:
    if quota_unit == RATE_LIMIT_QUOTA_UNIT_TOKENS:
        return usage_counts["token_count"]
    if quota_unit == RATE_LIMIT_QUOTA_UNIT_INVOCATIONS:
        return usage_counts["invocation_count"]
    if quota_unit == RATE_LIMIT_QUOTA_UNIT_MINUTES:
        return usage_counts["duration_seconds"]
    return usage_counts["request_count"]


def _build_rate_limit_usage_snapshot_from_counts(
    rate_limit: RateLimit,
    *,
    window_start: datetime,
    window_end: datetime,
    usage_counts: dict[str, int],
) -> dict[str, Any]:
    quota_value = _coerce_usage_int(rate_limit.quota_value)
    raw_current_usage = _get_current_usage_for_quota_unit(rate_limit.quota_unit, usage_counts)
    is_duration_quota = rate_limit.quota_unit == RATE_LIMIT_QUOTA_UNIT_MINUTES
    current_usage = round(raw_current_usage / 60, 2) if is_duration_quota else raw_current_usage
    remaining_usage = (
        round(max((quota_value * 60) - raw_current_usage, 0) / 60, 2)
        if is_duration_quota
        else max(quota_value - raw_current_usage, 0)
    )
    return {
        "window_start": window_start,
        "window_end": window_end,
        "timezone": getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE,
        "request_count": usage_counts["request_count"],
        "token_count": usage_counts["token_count"],
        "invocation_count": usage_counts["invocation_count"],
        "duration_seconds": usage_counts["duration_seconds"],
        "current_usage": current_usage,
        "remaining_usage": remaining_usage,
        "current_usage_seconds": raw_current_usage if is_duration_quota else None,
        "remaining_usage_seconds": max((quota_value * 60) - raw_current_usage, 0) if is_duration_quota else None,
    }


def _build_usage_window_from_counts(
    rate_limit: RateLimit,
    *,
    user_id: str,
    window_start: datetime,
    usage_counts: dict[str, int],
) -> RateLimitUsageWindow:
    return RateLimitUsageWindow(
        rate_limit_id=rate_limit.id,
        user_id=user_id,
        window_start=window_start,
        request_count=usage_counts["request_count"],
        token_count=usage_counts["token_count"],
        invocation_count=usage_counts["invocation_count"],
        duration_seconds=usage_counts["duration_seconds"],
    )


def _has_recent_open_rate_limit_admission(
    db,
    *,
    rate_limit_id: str,
    user_id: str,
    window_start: datetime,
) -> bool:
    recent_cutoff = datetime.now(timezone.utc) - _RATE_LIMIT_OPEN_ADMISSION_GRACE_PERIOD
    return (
        db.query(RateLimitChatAdmission.id)
        .filter(
            RateLimitChatAdmission.rate_limit_id == rate_limit_id,
            RateLimitChatAdmission.user_id == user_id,
            RateLimitChatAdmission.window_start == window_start,
            RateLimitChatAdmission.status == RATE_LIMIT_ADMISSION_OPEN,
            RateLimitChatAdmission.admitted_at >= recent_cutoff,
        )
        .first()
        is not None
    )


def get_rate_limit_usage_snapshot(db, rate_limit: RateLimit, user_id: str) -> dict[str, Any]:
    rate_limit_timezone = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    window_start, window_end = _get_window_bounds(rate_limit.period, rate_limit_timezone)
    _, usage_counts = _get_or_backfill_usage_window(
        db,
        rate_limit=rate_limit,
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
    )
    usage_snapshot = _build_rate_limit_usage_snapshot_from_counts(
        rate_limit,
        window_start=window_start,
        window_end=window_end,
        usage_counts=usage_counts,
    )
    usage_snapshot["timezone"] = rate_limit_timezone
    return usage_snapshot


def set_current_rate_limit_admission_context(
    admission_context: RateLimitAdmissionContext | None,
) -> Token[RateLimitAdmissionContext | None]:
    return _current_rate_limit_admission_context.set(admission_context)


def reset_current_rate_limit_admission_context(token: Token[RateLimitAdmissionContext | None]) -> None:
    _current_rate_limit_admission_context.reset(token)


def get_current_rate_limit_admission_context() -> RateLimitAdmissionContext | None:
    return _current_rate_limit_admission_context.get()


def admit_user_rate_limit(
    db,
    *,
    user_id: str,
    group_id: str | None,
    model_id: str,
    action_type: str,
    chat_id: str | None = None,
    user_message_id: str | None = None,
) -> RateLimitAdmissionContext | None:
    if not user_id or not model_id:
        return None

    action_type_value = str(action_type or "").strip().lower()
    if action_type_value not in RATE_LIMIT_ADMISSION_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported rate limit action")

    rate_limit = _select_matching_rate_limit(db, user_id, group_id, model_id)
    if not rate_limit:
        return None

    rate_limit_timezone = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    window_start, window_end = _get_window_bounds(rate_limit.period, rate_limit_timezone)
    quota_value = _coerce_usage_int(rate_limit.quota_value)

    with serialized_rate_limit_window_admission(
        db,
        rate_limit_id=rate_limit.id,
        user_id=user_id,
        window_start=window_start,
    ):
        usage_window, usage_counts = _get_or_backfill_usage_window(
            db,
            rate_limit=rate_limit,
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
        )
        usage_snapshot = _build_rate_limit_usage_snapshot_from_counts(
            rate_limit,
            window_start=window_start,
            window_end=window_end,
            usage_counts=usage_counts,
        )
        current_usage = usage_snapshot["current_usage"]
        if current_usage >= quota_value:
            return {
                "blocked": True,
                "rate_limit_id": rate_limit.id,
                "name": rate_limit.name,
                "period": rate_limit.period,
                "timezone": rate_limit_timezone,
                "quota_unit": rate_limit.quota_unit,
                "quota_value": quota_value,
                "current_usage": current_usage,
                "remaining_usage": 0,
                "resets_at": usage_snapshot["window_end"].isoformat(),
            }

        if rate_limit.quota_unit == RATE_LIMIT_QUOTA_UNIT_TOKENS and _has_recent_open_rate_limit_admission(
            db,
            rate_limit_id=rate_limit.id,
            user_id=user_id,
            window_start=window_start,
        ):
            return {
                "blocked": True,
                "block_reason": RATE_LIMIT_BLOCK_REASON_IN_FLIGHT,
                "rate_limit_id": rate_limit.id,
                "name": rate_limit.name,
                "period": rate_limit.period,
                "timezone": rate_limit_timezone,
                "quota_unit": rate_limit.quota_unit,
                "quota_value": quota_value,
                "current_usage": current_usage,
                "remaining_usage": usage_snapshot["remaining_usage"],
                "resets_at": usage_snapshot["window_end"].isoformat(),
            }

        request_count_reserved = rate_limit.quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS
        if request_count_reserved:
            if not usage_window:
                usage_window = _build_usage_window_from_counts(
                    rate_limit,
                    user_id=user_id,
                    window_start=window_start,
                    usage_counts=usage_counts,
                )
            usage_window.request_count = _coerce_usage_int(getattr(usage_window, "request_count", 0)) + 1
            db.add(usage_window)

        admission = RateLimitChatAdmission(
            rate_limit_id=rate_limit.id,
            user_id=user_id,
            group_id=group_id,
            selected_model_id=model_id,
            chat_id=chat_id,
            user_message_id=user_message_id,
            action_type=action_type_value,
            window_start=window_start,
            window_end=window_end,
            quota_unit=rate_limit.quota_unit,
            quota_value=quota_value,
            usage_snapshot_before={
                "current_usage": current_usage,
                "request_count": usage_snapshot["request_count"],
                "token_count": usage_snapshot["token_count"],
            },
            status=RATE_LIMIT_ADMISSION_OPEN,
            request_counted=request_count_reserved,
            overshot_budget=False,
            overshoot_amount=0,
        )
        db.add(admission)
        db.commit()
        db.refresh(admission)
        return RateLimitAdmissionContext(
            admission_id=admission.id,
            rate_limit_id=admission.rate_limit_id,
            user_id=admission.user_id,
            quota_unit=admission.quota_unit,
            quota_value=_coerce_usage_int(admission.quota_value),
            window_start=admission.window_start,
            window_end=admission.window_end,
            action_type=admission.action_type,
        )


def _build_tool_rate_limited_payload(db, rate_limit: RateLimit, usage_snapshot: dict[str, Any], *, tool_key: str) -> dict[str, Any]:
    quota_value = _coerce_usage_int(rate_limit.quota_value)
    current_usage = _coerce_usage_int(usage_snapshot.get("current_usage"))
    rate_limit_timezone = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    try:
        from app.tools.registry import get_rate_limit_tool

        tool_info = get_rate_limit_tool(db, tool_key)
    except Exception:
        tool_info = None
    tool_label = str((tool_info or {}).get("label") or tool_key).strip() or tool_key
    resets_at = usage_snapshot["window_end"].isoformat()
    return {
        "blocked": True,
        "code": "user_tool_rate_limited",
        "message": (
            f"This tool is currently rate limited for the user. "
            f"Try again after {resets_at} ({rate_limit_timezone})."
        ),
        "rate_limit_id": rate_limit.id,
        "rate_limit_name": rate_limit.name,
        "target_type": RATE_LIMIT_TARGET_TYPE_TOOL,
        "tool_key": tool_key,
        "tool_name": tool_key,
        "tool_label": tool_label,
        "period": rate_limit.period,
        "timezone": rate_limit_timezone,
        "quota_unit": RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
        "quota_value": quota_value,
        "current_usage": current_usage,
        "remaining_usage": 0,
        "resets_at": resets_at,
    }


def admit_user_tool_rate_limit(
    db,
    *,
    user_id: str,
    group_id: str | None,
    tool_key: str,
) -> dict[str, Any] | None:
    if not user_id or not tool_key:
        return None

    try:
        from app.tools.registry import normalize_rate_limit_tool_key

        normalized_tool_key = normalize_rate_limit_tool_key(tool_key)
    except Exception:
        normalized_tool_key = str(tool_key or "").strip()
    if not normalized_tool_key:
        return None

    rate_limit = _select_matching_tool_rate_limit(db, user_id, group_id, normalized_tool_key)
    if not rate_limit:
        return None

    rate_limit_timezone = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    window_start, window_end = _get_window_bounds(rate_limit.period, rate_limit_timezone)
    quota_value = _coerce_usage_int(rate_limit.quota_value)

    with serialized_rate_limit_window_admission(
        db,
        rate_limit_id=rate_limit.id,
        user_id=user_id,
        window_start=window_start,
    ):
        usage_window, usage_counts = _get_or_backfill_usage_window(
            db,
            rate_limit=rate_limit,
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
        )
        usage_snapshot = _build_rate_limit_usage_snapshot_from_counts(
            rate_limit,
            window_start=window_start,
            window_end=window_end,
            usage_counts=usage_counts,
        )
        current_usage = _coerce_usage_int(usage_snapshot["current_usage"])
        if current_usage >= quota_value:
            return _build_tool_rate_limited_payload(db, rate_limit, usage_snapshot, tool_key=normalized_tool_key)

        if not usage_window:
            usage_window = _build_usage_window_from_counts(
                rate_limit,
                user_id=user_id,
                window_start=window_start,
                usage_counts=usage_counts,
            )
        usage_window.invocation_count = _coerce_usage_int(getattr(usage_window, "invocation_count", 0)) + 1
        db.add(usage_window)
        db.commit()
        return None


def _duration_admission_datetime(value: datetime | None) -> datetime:
    """Normalize database datetimes before lease and elapsed-time comparisons."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_admission_is_stale(
    admission: RateLimitDurationAdmission,
    *,
    now: datetime,
) -> bool:
    """Return whether an open duration reservation has lost its lease."""
    if _duration_admission_datetime(admission.window_end) <= now:
        return True
    lease = (
        _DURATION_RATE_LIMIT_REALTIME_LEASE
        if admission.target_type == RATE_LIMIT_TARGET_TYPE_REALTIME
        else _DURATION_RATE_LIMIT_DICTATION_LEASE
    )
    last_activity_at = _duration_admission_datetime(
        getattr(admission, "last_activity_at", None) or admission.admitted_at
    )
    return last_activity_at <= now - lease


def _finalize_open_duration_admission_locked(
    db,
    admission: RateLimitDurationAdmission,
    *,
    consumed_seconds: int,
    final_status: str,
    completed_at: datetime,
) -> bool:
    """Atomically finalize one open admission while its window lock is held.

    The conditional update is the idempotency boundary. Even if two database
    sessions read the admission as open before one obtains the window lock,
    only the first session can transition it to a terminal state and charge
    the usage window.
    """
    reserved_seconds = max(_coerce_usage_int(admission.reserved_seconds), 0)
    actual_seconds = min(max(int(consumed_seconds or 0), 0), reserved_seconds)
    normalized_status = (
        final_status
        if final_status in {RATE_LIMIT_ADMISSION_COMPLETED, RATE_LIMIT_ADMISSION_FAILED}
        else RATE_LIMIT_ADMISSION_COMPLETED
    )
    updated_rows = (
        db.query(RateLimitDurationAdmission)
        .filter(
            RateLimitDurationAdmission.id == admission.id,
            RateLimitDurationAdmission.status == RATE_LIMIT_ADMISSION_OPEN,
        )
        .update(
            {
                RateLimitDurationAdmission.consumed_seconds: actual_seconds,
                RateLimitDurationAdmission.status: normalized_status,
                RateLimitDurationAdmission.completed_at: completed_at,
            },
            synchronize_session=False,
        )
    )
    if updated_rows != 1:
        return False

    if actual_seconds > 0:
        usage_window = (
            db.query(RateLimitUsageWindow)
            .filter(
                RateLimitUsageWindow.rate_limit_id == admission.rate_limit_id,
                RateLimitUsageWindow.user_id == admission.user_id,
                RateLimitUsageWindow.window_start == admission.window_start,
            )
            .first()
        )
        if not usage_window:
            usage_window = RateLimitUsageWindow(
                rate_limit_id=admission.rate_limit_id,
                user_id=admission.user_id,
                window_start=admission.window_start,
                request_count=0,
                token_count=0,
                invocation_count=0,
                duration_seconds=0,
            )
        usage_window.duration_seconds = (
            _coerce_usage_int(getattr(usage_window, "duration_seconds", 0)) + actual_seconds
        )
        db.add(usage_window)
    return True


def _reclaim_stale_duration_admissions_locked(
    db,
    admissions: list[RateLimitDurationAdmission],
    *,
    now: datetime,
) -> list[RateLimitDurationAdmission]:
    """Finalize expired leases and return reservations that remain active."""
    active_admissions: list[RateLimitDurationAdmission] = []
    reclaimed_any = False
    for admission in admissions:
        if not _duration_admission_is_stale(admission, now=now):
            active_admissions.append(admission)
            continue

        # A failed dictation has no usable provider result, so its reservation
        # is released. A lost realtime browser may have consumed provider time;
        # conservatively charge wall time, capped to the original reservation.
        elapsed_seconds = max(
            int(math.ceil((now - _duration_admission_datetime(admission.admitted_at)).total_seconds())),
            0,
        )
        reclaimed_any = _finalize_open_duration_admission_locked(
            db,
            admission,
            consumed_seconds=(
                elapsed_seconds
                if admission.target_type == RATE_LIMIT_TARGET_TYPE_REALTIME
                else 0
            ),
            final_status=(
                RATE_LIMIT_ADMISSION_COMPLETED
                if admission.target_type == RATE_LIMIT_TARGET_TYPE_REALTIME
                else RATE_LIMIT_ADMISSION_FAILED
            ),
            completed_at=now,
        ) or reclaimed_any

    if reclaimed_any:
        # Make reclaimed usage visible to the snapshot query that follows while
        # retaining the surrounding advisory/local lock until final commit.
        db.flush()
    return active_admissions


def _build_feature_rate_limited_payload(
    rate_limit: RateLimit,
    usage_snapshot: dict[str, Any],
    *,
    target_type: str,
    current_seconds: int,
    reserved_seconds: int,
    remaining_seconds: int,
) -> dict[str, Any]:
    """Build an accurate structured denial for duration-based quotas.

    ``current_seconds`` is durable, completed usage. ``reserved_seconds`` is
    only a temporary hold owned by other in-flight sessions, and
    ``remaining_seconds`` is what is available after both values. Keeping
    those concepts separate prevents an abandoned or concurrent dictation
    from being presented to the user as already consumed quota.
    """
    quota_minutes = _coerce_usage_int(rate_limit.quota_value)
    quota_seconds = quota_minutes * 60
    quota_remaining_seconds = max(quota_seconds - current_seconds, 0)
    has_active_dictation = (
        target_type == RATE_LIMIT_TARGET_TYPE_DICTATION
        and reserved_seconds > 0
        and quota_remaining_seconds > 0
    )
    # Keep the shared admission contract stable for file transcription and
    # realtime-call callers. The live WebSocket route promotes
    # ``active_reservation`` to its dedicated browser-facing code.
    denial_code = f"user_{target_type}_rate_limited"
    target_label = "Dictation" if target_type == RATE_LIMIT_TARGET_TYPE_DICTATION else "Realtime call"
    resets_at = usage_snapshot["window_end"].isoformat()
    timezone_name = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    return {
        "blocked": True,
        "code": denial_code,
        "reason": "active_reservation" if has_active_dictation else "quota_exceeded",
        "message": (
                "Another dictation is already active. Stop it or wait a "
                "moment before trying again."
            if has_active_dictation
            else (
                f"Your {target_label.lower()} minute limit has been reached. "
                f"Try again after {resets_at} ({timezone_name})."
            )
        ),
        "rate_limit_id": rate_limit.id,
        "rate_limit_name": rate_limit.name,
        "target_type": target_type,
        "period": rate_limit.period,
        "timezone": timezone_name,
        "quota_unit": RATE_LIMIT_QUOTA_UNIT_MINUTES,
        "quota_value": quota_minutes,
        "current_usage": round(current_seconds / 60, 2),
        "remaining_usage": round(quota_remaining_seconds / 60, 2),
        "current_usage_seconds": current_seconds,
        "remaining_usage_seconds": quota_remaining_seconds,
        "reserved_usage_seconds": max(reserved_seconds, 0),
        "available_usage_seconds": max(remaining_seconds, 0),
        "resets_at": resets_at,
    }


def admit_user_duration_rate_limit(
    db,
    *,
    user_id: str,
    group_id: str | None,
    target_type: str,
    requested_seconds: int | None = None,
) -> DurationRateLimitAdmissionContext | dict[str, Any] | None:
    """Reserve time atomically for dictation or a realtime session.

    File dictation supplies its measured audio duration. Realtime calls and
    live dictation omit a requested duration and reserve the entire remaining
    budget, which both determines the browser's hard session deadline and
    prevents parallel sessions from double-spending the same minutes.
    """
    if not user_id:
        return None
    normalized_target = _validate_rate_limit_target_type(target_type)
    rate_limit = _select_matching_feature_rate_limit(db, user_id, group_id, normalized_target)
    if not rate_limit:
        return None

    timezone_name = getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE
    window_start, window_end = _get_window_bounds(rate_limit.period, timezone_name)
    quota_seconds = _coerce_usage_int(rate_limit.quota_value) * 60
    now = datetime.now(timezone.utc)

    with serialized_rate_limit_window_admission(
        db,
        rate_limit_id=rate_limit.id,
        user_id=user_id,
        window_start=window_start,
    ):
        open_admissions = (
            db.query(RateLimitDurationAdmission)
            .filter(
                RateLimitDurationAdmission.rate_limit_id == rate_limit.id,
                RateLimitDurationAdmission.user_id == user_id,
                RateLimitDurationAdmission.window_start == window_start,
                RateLimitDurationAdmission.status == RATE_LIMIT_ADMISSION_OPEN,
            )
            .all()
        )
        active_admissions = _reclaim_stale_duration_admissions_locked(
            db,
            open_admissions,
            now=now,
        )
        _, usage_counts = _get_or_backfill_usage_window(
            db,
            rate_limit=rate_limit,
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
        )
        current_seconds = _coerce_usage_int(usage_counts.get("duration_seconds"))
        reserved_seconds = sum(
            _coerce_usage_int(admission.reserved_seconds)
            for admission in active_admissions
        )
        remaining_seconds = max(quota_seconds - current_seconds - reserved_seconds, 0)
        requested = remaining_seconds if requested_seconds is None else max(int(requested_seconds), 1)

        # Seconds are the internal accounting unit, so users retain access to
        # any partial minute left after an earlier dictation or call.
        minimum_seconds = 1
        if remaining_seconds < minimum_seconds or requested > remaining_seconds:
            usage_snapshot = _build_rate_limit_usage_snapshot_from_counts(
                rate_limit,
                window_start=window_start,
                window_end=window_end,
                usage_counts=usage_counts,
            )
            blocked_payload = _build_feature_rate_limited_payload(
                rate_limit,
                usage_snapshot,
                target_type=normalized_target,
                current_seconds=current_seconds,
                reserved_seconds=reserved_seconds,
                remaining_seconds=remaining_seconds,
            )
            # A blocked decision can still reclaim stale reservations. Commit
            # that terminal state before releasing the window lock.
            db.commit()
            return blocked_payload

        admission = RateLimitDurationAdmission(
            rate_limit_id=rate_limit.id,
            user_id=user_id,
            group_id=group_id,
            target_type=normalized_target,
            window_start=window_start,
            window_end=window_end,
            reserved_seconds=requested,
            consumed_seconds=0,
            status=RATE_LIMIT_ADMISSION_OPEN,
            admitted_at=now,
            last_activity_at=now,
        )
        db.add(admission)
        db.commit()
        db.refresh(admission)
        return DurationRateLimitAdmissionContext(
            admission_id=admission.id,
            rate_limit_id=admission.rate_limit_id,
            target_type=admission.target_type,
            user_id=admission.user_id,
            reserved_seconds=_coerce_usage_int(admission.reserved_seconds),
            window_start=admission.window_start,
            window_end=admission.window_end,
        )


def finalize_duration_rate_limit_admission(
    db,
    admission_id: str | None,
    *,
    consumed_seconds: int,
    final_status: str = RATE_LIMIT_ADMISSION_COMPLETED,
) -> None:
    """Finalize one duration reservation and persist only actual usage."""
    if not admission_id:
        return
    admission = (
        db.query(RateLimitDurationAdmission)
        .filter(RateLimitDurationAdmission.id == admission_id)
        .first()
    )
    if not admission:
        return

    with serialized_rate_limit_window_admission(
        db,
        rate_limit_id=admission.rate_limit_id,
        user_id=admission.user_id,
        window_start=admission.window_start,
    ):
        finalized = _finalize_open_duration_admission_locked(
            db,
            admission,
            consumed_seconds=consumed_seconds,
            final_status=final_status,
            completed_at=datetime.now(timezone.utc),
        )
        if finalized:
            db.commit()


def touch_duration_rate_limit_admission(db, admission_id: str | None) -> bool:
    """Renew an active duration lease from server-observed provider activity."""
    if not admission_id:
        return False
    admission_window = (
        db.query(
            RateLimitDurationAdmission.rate_limit_id,
            RateLimitDurationAdmission.user_id,
            RateLimitDurationAdmission.window_start,
        )
        .filter(RateLimitDurationAdmission.id == admission_id)
        .first()
    )
    if not admission_window:
        return False

    # Lease renewal must serialize with stale-admission reclamation. The
    # first lookup only identifies the window; the admission ID and open status
    # are checked again after acquiring that window's lock.
    with serialized_rate_limit_window_admission(
        db,
        rate_limit_id=admission_window.rate_limit_id,
        user_id=admission_window.user_id,
        window_start=admission_window.window_start,
    ):
        updated_rows = (
            db.query(RateLimitDurationAdmission)
            .filter(
                RateLimitDurationAdmission.id == admission_id,
                RateLimitDurationAdmission.rate_limit_id == admission_window.rate_limit_id,
                RateLimitDurationAdmission.user_id == admission_window.user_id,
                RateLimitDurationAdmission.window_start == admission_window.window_start,
                RateLimitDurationAdmission.status == RATE_LIMIT_ADMISSION_OPEN,
            )
            .update(
                {RateLimitDurationAdmission.last_activity_at: datetime.now(timezone.utc)},
                synchronize_session=False,
            )
        )
        if updated_rows != 1:
            db.rollback()
            return False
        db.commit()
        return True


def _touch_duration_rate_limit_admission_with_new_session(
    admission_id: str,
) -> bool:
    """Renew one admission through an independent short-lived DB session."""
    db = SessionLocal()
    try:
        return touch_duration_rate_limit_admission(db, admission_id)
    finally:
        db.close()


async def renew_dictation_duration_rate_limit_lease(
    admission_id: str | None,
    *,
    interval_seconds: float = _DURATION_RATE_LIMIT_DICTATION_RENEWAL_INTERVAL_SECONDS,
) -> None:
    """Keep an open dictation reservation leased until the caller cancels.

    Provider uploads and final transcript generation can outlive the 90-second
    dictation lease. Consumers run this coroutine in a task for exactly as long
    as provider processing remains active, then cancel it before finalizing the
    existing reservation. Each renewal uses its own database session because
    SQLAlchemy request sessions must not be shared with a concurrent task.
    """
    if not admission_id:
        return

    renewal_interval = max(float(interval_seconds), 0.1)
    while True:
        await anyio.sleep(renewal_interval)
        try:
            renewed = await anyio.to_thread.run_sync(
                _touch_duration_rate_limit_admission_with_new_session,
                admission_id,
            )
        except Exception:
            # A transient database failure should not abort a provider request.
            # The next interval retries while the consumer remains active.
            logger.exception(
                "Failed to renew dictation duration rate-limit admission %s",
                admission_id,
            )
            continue
        if not renewed:
            # Finalized or reclaimed reservations cannot and must not be
            # resurrected by a delayed heartbeat.
            return


def finalize_rate_limit_admission(
    db,
    admission_id: str | None,
    *,
    final_status: str,
) -> None:
    if not admission_id:
        return
    admission = db.query(RateLimitChatAdmission).filter(RateLimitChatAdmission.id == admission_id).first()
    if not admission:
        return
    if admission.status != RATE_LIMIT_ADMISSION_OPEN:
        return
    usage_window = (
        db.query(RateLimitUsageWindow)
        .filter(
            RateLimitUsageWindow.rate_limit_id == admission.rate_limit_id,
            RateLimitUsageWindow.user_id == admission.user_id,
            RateLimitUsageWindow.window_start == admission.window_start,
        )
        .first()
    )
    request_count = _coerce_usage_int(getattr(usage_window, "request_count", 0))
    token_count = _coerce_usage_int(getattr(usage_window, "token_count", 0))
    current_usage = request_count if admission.quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else token_count
    quota_value = _coerce_usage_int(admission.quota_value)
    admission.status = final_status if final_status in {RATE_LIMIT_ADMISSION_COMPLETED, RATE_LIMIT_ADMISSION_FAILED} else RATE_LIMIT_ADMISSION_COMPLETED
    admission.completed_at = datetime.now(timezone.utc)
    admission.overshot_budget = current_usage > quota_value
    admission.overshoot_amount = max(current_usage - quota_value, 0)
    db.add(admission)
    db.commit()


def create_rate_limit(
    db,
    name: str,
    model_ids: list[str],
    user_ids: list[str],
    group_ids: list[str],
    scope: str | None,
    period: str,
    timezone_name: str | None,
    quota_unit: str | None,
    quota_value: int | None,
    tool_keys: list[str] | None = None,
    max_requests: int | None = None,
    target_type: str | None = None,
    is_active: bool = True,
):
    """Create a rate limit."""
    payload = _prepare_rate_limit_payload(
        db,
        name=name,
        target_type=target_type,
        model_ids=model_ids,
        tool_keys=tool_keys or [],
        user_ids=user_ids,
        group_ids=group_ids,
        scope=scope,
        period=period,
        timezone_name=timezone_name,
        quota_unit=quota_unit,
        quota_value=quota_value,
        max_requests=max_requests,
    )
    rate_limit = RateLimit(**payload)
    rate_limit.is_active = bool(is_active)
    db.add(rate_limit)
    db.commit()
    db.refresh(rate_limit)
    return rate_limit


def update_rate_limit(db, rate_limit_id: str, **updates):
    """Update a rate limit."""
    if not updates:
        raise HTTPException(status_code=400, detail="No update fields provided")

    rate_limit = get_rate_limit(db, rate_limit_id)

    payload = {
        "name": rate_limit.name,
        "target_type": getattr(rate_limit, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL),
        "model_ids": list(rate_limit.model_ids or []),
        "tool_keys": list(getattr(rate_limit, "tool_keys", None) or []),
        "user_ids": list(rate_limit.user_ids or []),
        "group_ids": list(rate_limit.group_ids or []),
        "scope": rate_limit.scope,
        "period": rate_limit.period,
        "timezone_name": getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE,
        "quota_unit": rate_limit.quota_unit,
        "quota_value": _coerce_usage_int(rate_limit.quota_value),
        "max_requests": rate_limit.max_requests,
    }

    for key in ("name", "target_type", "model_ids", "tool_keys", "user_ids", "group_ids", "scope", "period", "timezone_name", "quota_unit", "quota_value", "max_requests"):
        if key in updates and updates[key] is not None:
            payload[key] = updates[key]

    normalized = _prepare_rate_limit_payload(db, **payload)
    for key, value in normalized.items():
        setattr(rate_limit, key, value)

    if "is_active" in updates and updates["is_active"] is not None:
        rate_limit.is_active = bool(updates["is_active"])

    db.add(rate_limit)
    db.commit()
    db.refresh(rate_limit)
    return rate_limit


def get_rate_limit(db, rate_limit_id: str):
    """Get a rate limit by ID."""
    if not isinstance(rate_limit_id, str) or not rate_limit_id.strip():
        raise HTTPException(status_code=400, detail="Invalid rate_limit_id")

    rate_limit = db.query(RateLimit).filter(RateLimit.id == rate_limit_id.strip()).first()
    if not rate_limit:
        raise HTTPException(status_code=404, detail="Rate limit not found")
    return rate_limit


def list_rate_limits(db) -> list[RateLimit]:
    """List all rate limits."""
    return db.query(RateLimit).order_by(RateLimit.created_at.desc(), RateLimit.id.desc()).all()


def delete_rate_limit(db, rate_limit_id: str):
    """Delete a rate limit and its operational tracking rows."""
    rate_limit = get_rate_limit(db, rate_limit_id)
    _delete_rate_limit_record(db, rate_limit)
    db.commit()
    return {"deleted": True, "rate_limit_id": rate_limit_id}


def check_rate_limit_conflicts(
    db,
    model_ids: list[str],
    user_ids: list[str],
    group_ids: list[str],
    tool_keys: list[str] | None = None,
    exclude_rate_limit_id: str | None = None,
    target_type: str | None = None,
) -> list[dict]:
    """Check for rate limit conflicts."""
    target_type_value = _validate_rate_limit_target_type(target_type)
    singleton_feature = target_type_value in {
        RATE_LIMIT_TARGET_TYPE_DICTATION,
        RATE_LIMIT_TARGET_TYPE_REALTIME,
    }
    if target_type_value == RATE_LIMIT_TARGET_TYPE_MODEL:
        normalized_targets = set(_normalize_rate_limit_ids(model_ids, "model_ids", allow_empty=False))
        target_field = "model_ids"
        existing_field = "model_ids"
        overlap_key = "overlapping_model_ids"
    elif target_type_value == RATE_LIMIT_TARGET_TYPE_TOOL:
        normalized_targets = set(_normalize_rate_limit_ids(tool_keys or [], "tool_keys", allow_empty=False))
        target_field = "tool_keys"
        existing_field = "tool_keys"
        overlap_key = "overlapping_tool_keys"
    else:
        # The feature name itself is the singleton target, so conflicts depend
        # only on overlapping users/groups for the same target_type.
        normalized_targets = {target_type_value}
        target_field = "target_type"
        existing_field = "target_type"
        overlap_key = ""
    normalized_users = set(_normalize_rate_limit_ids(user_ids, "user_ids", allow_empty=True))
    normalized_groups = set(_normalize_rate_limit_ids(group_ids, "group_ids", allow_empty=True))

    if not normalized_users and not normalized_groups:
        return []

    query = db.query(RateLimit).filter(RateLimit.is_active == True)
    if exclude_rate_limit_id:
        query = query.filter(RateLimit.id != exclude_rate_limit_id)

    conflicts: list[dict] = []
    for existing in query.order_by(RateLimit.created_at.desc(), RateLimit.id.desc()).all():
        if getattr(existing, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) != target_type_value:
            continue
        if singleton_feature:
            overlapping_target_ids = [target_type_value]
        else:
            overlapping_target_ids = sorted(normalized_targets.intersection(set(getattr(existing, existing_field, None) or [])))
        if not overlapping_target_ids:
            continue

        overlapping_user_ids = sorted(normalized_users.intersection(set(existing.user_ids or [])))
        overlapping_group_ids = sorted(normalized_groups.intersection(set(existing.group_ids or [])))
        if not overlapping_user_ids and not overlapping_group_ids:
            continue

        conflicts.append(
            {
                "rate_limit_id": existing.id,
                "rate_limit_name": existing.name,
                "target_type": target_type_value,
                "overlapping_model_ids": overlapping_target_ids if target_field == "model_ids" else [],
                "overlapping_tool_keys": overlapping_target_ids if overlap_key == "overlapping_tool_keys" else [],
                "overlapping_user_ids": overlapping_user_ids,
                "overlapping_group_ids": overlapping_group_ids,
            }
        )

    return conflicts


def check_user_rate_limit(db, user_id: str, group_id: str | None, model_id: str) -> dict | None:
    """Check if a user has exceeded rate limits."""
    if not user_id or not model_id:
        return None

    rate_limit = _select_matching_rate_limit(db, user_id, group_id, model_id)
    if not rate_limit:
        return None

    usage_snapshot = get_rate_limit_usage_snapshot(db, rate_limit, user_id)
    current_usage = usage_snapshot["current_usage"]
    quota_value = _coerce_usage_int(rate_limit.quota_value)
    if current_usage < quota_value:
        return None

    return {
        "rate_limit_id": rate_limit.id,
        "name": rate_limit.name,
        "period": rate_limit.period,
        "timezone": getattr(rate_limit, "timezone", None) or DEFAULT_RATE_LIMIT_TIMEZONE,
        "quota_unit": rate_limit.quota_unit,
        "quota_value": quota_value,
        "current_usage": current_usage,
        "remaining_usage": 0,
        "max_requests": quota_value if rate_limit.quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else None,
        "current_count": current_usage if rate_limit.quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else None,
        "resets_at": usage_snapshot["window_end"].isoformat(),
    }
