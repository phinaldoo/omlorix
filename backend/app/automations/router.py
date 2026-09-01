from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
import time
from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
import jwt
from jwt import InvalidTokenError as JWTError
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.auth.token import _get_jwt_material
from app.auth.token import ensure_user_runtime_auth_allowed
from app.logging.models import create_audit_log, get_audit_request_ip
from app.redis_client import get_redis_client
from app.settings.public_urls import normalize_public_url, normalize_public_urls
from app.settings.utils import get_value_by_page_and_key
from app.users.models import User, get_user
from app.users.init import get_user_setting_value
from app.groups.init import get_user_group_setting_value
from app.automations.models import (
    AutomationWebhookDelivery,
    AutomationWebhookTrigger,
    create_automation,
    create_webhook_delivery,
    create_webhook_trigger,
    generate_webhook_secret,
    delete_webhook_trigger,
    get_automation,
    get_webhook_trigger,
    get_webhook_trigger_for_automation,
    hash_webhook_secret,
    list_webhook_deliveries,
    list_automations,
    rotate_webhook_trigger_secret,
    update_automation_last_triggered,
    update_automation,
    update_webhook_delivery,
    update_webhook_trigger,
    update_webhook_trigger_last_triggered,
    verify_webhook_secret,
    delete_automation,
)
from app.automations.queue import enqueue_automation_execution
from app.automations.schemas import (
    AutomationCreate,
    AutomationUpdate,
    AutomationResponse,
    AutomationListResponse,
    AutomationStatusResponse,
    AutomationWebhookDeliveriesResponse,
    AutomationWebhookCredentialsResponse,
    AutomationWebhookStatusResponse,
    AutomationWebhookTriggerCreate,
    AutomationWebhookTriggerResponse,
    AutomationWebhookTriggerUpdate,
)
from app.utils.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    page_from_limited_items,
)


automations_router = APIRouter(prefix="/api/v1/automations", tags=["automations"])
logger = logging.getLogger(__name__)

_WEBHOOK_LOCAL_RATE_LIMIT_LOCK = threading.Lock()
_WEBHOOK_LOCAL_RATE_LIMITS: dict[str, tuple[int, int]] = {}
_SENSITIVE_PAYLOAD_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "x-api-key",
    "x-omlorix-webhook-secret",
}
_MAX_PREVIEW_STRING_LENGTH = 500
_MAX_PREVIEW_COLLECTION_ITEMS = 30
_WEBHOOK_RESERVATION_TOKEN_TYPE = "automation_webhook_reservation"
_WEBHOOK_RESERVATION_TTL_SECONDS = 60 * 60


def _configured_webhook_base_url(db: Session) -> str | None:
    """Return the canonical configured origin for externally advertised links.

    The first ``general.public_url`` value is the application's canonical URL.
    ``PUBLIC_URL`` remains a supported environment fallback for deployments
    that configure the public origin before settings storage is available.
    Invalid or temporarily unavailable settings do not make the route fail:
    Host validation still constrains the request-derived bootstrap fallback.
    """

    try:
        configured_urls = normalize_public_urls(
            get_value_by_page_and_key("general", "public_url", db),
            allow_empty=True,
        )
    except Exception:
        logger.debug(
            "Unable to load general.public_url while building an automation webhook URL",
            exc_info=True,
        )
        configured_urls = []
    if configured_urls:
        return configured_urls[0]

    env_public_url = os.getenv("PUBLIC_URL")
    if env_public_url:
        try:
            return normalize_public_url(env_public_url)
        except ValueError:
            logger.warning(
                "Ignoring invalid PUBLIC_URL while building an automation webhook URL"
            )
    return None


def _public_webhook_url(
    request: Request,
    trigger_id: str,
    *,
    configured_base_url: str | None,
) -> str:
    """Build a webhook URL without trusting a request Host over configuration."""

    try:
        request_url = request.url_for(
            "trigger_automation_webhook_route",
            trigger_id=trigger_id,
        )
        if configured_base_url:
            # Reuse the router-generated path so route changes remain reflected,
            # but source the externally visible origin only from configuration.
            return f"{configured_base_url.rstrip('/')}{request_url.path}"
        return str(request_url)
    except Exception:
        if configured_base_url:
            return (
                f"{configured_base_url.rstrip('/')}"
                f"/api/v1/automations/webhooks/{trigger_id}"
            )
        # Before first-run setup there may be no canonical public URL. The
        # request origin is the only usable fallback in that bootstrap state.
        base_url = str(request.base_url).rstrip("/")
        return f"{base_url}/api/v1/automations/webhooks/{trigger_id}"


def _create_webhook_reservation_token(
    db: Session,
    *,
    user_id: str,
    trigger_id: str,
    secret: str,
) -> tuple[str, datetime]:
    """Sign stateless credentials so clients cannot choose a weak webhook secret."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_WEBHOOK_RESERVATION_TTL_SECONDS)
    payload = {
        "type": _WEBHOOK_RESERVATION_TOKEN_TYPE,
        "user_id": user_id,
        "trigger_id": trigger_id,
        "secret_hash": hash_webhook_secret(secret),
        "exp": expires_at,
    }
    signing_secret, algorithm = _get_jwt_material()
    return jwt.encode(payload, signing_secret, algorithm=algorithm), expires_at


def _verify_webhook_reservation(
    db: Session,
    *,
    user_id: str,
    trigger_id: str,
    secret: str,
    reservation_token: str,
) -> None:
    """Verify that pre-created credentials were issued for the current user."""
    signing_secret, algorithm = _get_jwt_material()
    try:
        payload = jwt.decode(reservation_token, signing_secret, algorithms=[algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Webhook reservation is invalid or expired") from exc
    valid = (
        payload.get("type") == _WEBHOOK_RESERVATION_TOKEN_TYPE
        and str(payload.get("user_id") or "") == user_id
        and str(payload.get("trigger_id") or "") == trigger_id
        and str(payload.get("secret_hash") or "") == hash_webhook_secret(secret)
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Webhook reservation is invalid or expired")


def _audit_automation_event(
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="automations",
    )


def _ensure_automations_enabled(user_id: str, db: Session) -> None:
    """Raise 403 if automations are disabled for the user's group."""
    allowed = get_user_group_setting_value(user_id, "automations", "enabled_automations", db)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Automations are disabled for your group.",
        )


def _webhook_trigger_to_response(
    request: Request,
    trigger: AutomationWebhookTrigger | None,
    *,
    webhook_base_url: str | None,
    secret: str | None = None,
) -> AutomationWebhookTriggerResponse | None:
    if not trigger:
        return None
    return AutomationWebhookTriggerResponse(
        id=trigger.id,
        automation_id=trigger.automation_id,
        user_id=trigger.user_id,
        name=trigger.name,
        is_enabled=trigger.is_enabled,
        token_prefix=trigger.token_prefix,
        payload_mode=trigger.payload_mode,
        include_headers=trigger.include_headers,
        allowed_header_names=trigger.allowed_header_names or [],
        max_body_bytes=trigger.max_body_bytes,
        rate_limit_per_minute=trigger.rate_limit_per_minute,
        url=_public_webhook_url(
            request,
            trigger.id,
            configured_base_url=webhook_base_url,
        ),
        secret=secret,
        last_triggered_at=trigger.last_triggered_at,
        created_at=trigger.created_at,
        last_updated_at=trigger.last_updated_at,
    )


def _delivery_to_response(delivery: AutomationWebhookDelivery) -> dict:
    return {
        "id": delivery.id,
        "trigger_id": delivery.trigger_id,
        "automation_id": delivery.automation_id,
        "user_id": delivery.user_id,
        "status": delivery.status,
        "status_code": delivery.status_code,
        "error": delivery.error,
        "request_ip": delivery.request_ip,
        "user_agent": delivery.user_agent,
        "payload_preview": delivery.payload_preview,
        "chat_id": delivery.chat_id,
        "created_at": delivery.created_at,
    }


def _automation_to_response(
    automation,
    request: Request | None = None,
    *,
    db: Session | None = None,
    webhook_base_url: str | None,
    webhook_trigger: AutomationWebhookTrigger | None = None,
    webhook_secret: str | None = None,
) -> AutomationResponse:
    """Convert an automation and its optional one-time webhook secret to a response."""
    response_db = db
    if request is not None and webhook_trigger is None:
        try:
            response_db = response_db or getattr(automation, "_sa_instance_state").session
            if response_db is not None:
                webhook_trigger = get_webhook_trigger_for_automation(
                    response_db,
                    automation.user_id,
                    automation.id,
                )
        except Exception:
            webhook_trigger = None
    return AutomationResponse(
        id=automation.id,
        user_id=automation.user_id,
        title=automation.title,
        icon=automation.icon,
        icon_color=automation.icon_color,
        prompt=automation.prompt,
        model_id=automation.model_id,
        schedule_rules=automation.schedule_rules,
        schedule_timezone=automation.schedule_timezone,
        skill_id=automation.skill_id,
        note_ids=automation.note_ids or [],
        file_ids=automation.file_ids or [],
        mcp_server_ids=getattr(automation, "mcp_server_ids", None) or [],
        webhook_trigger=(
            _webhook_trigger_to_response(
                request,
                webhook_trigger,
                webhook_base_url=webhook_base_url,
                secret=webhook_secret,
            )
            if request is not None and response_db is not None
            else None
        ),
        is_active=automation.is_active,
        last_triggered_at=automation.last_triggered_at,
        created_at=automation.created_at,
        last_updated_at=automation.last_updated_at,
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _webhook_error_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str) and message.strip():
            return message.strip()
        try:
            return json.dumps(detail, ensure_ascii=False)[:255]
        except Exception:
            return "Webhook request was rejected"
    return str(detail or "Webhook request was rejected")


def _has_recurring_schedule_rules(schedule_rules: list[dict[str, Any]] | None) -> bool:
    if not isinstance(schedule_rules, list):
        return False
    for rule in schedule_rules:
        if not isinstance(rule, dict):
            continue
        if isinstance(rule.get("run_at"), str) and rule.get("run_at").strip():
            continue
        if isinstance(rule.get("days"), list) and isinstance(rule.get("times"), list):
            return True
    return False


def _resolve_automation_schedule_timezone(
    user_id: str,
    db: Session,
    schedule_rules: list[dict[str, Any]] | None,
    requested_timezone: str | None,
) -> str | None:
    normalized_requested = str(requested_timezone or "").strip() or None
    if not _has_recurring_schedule_rules(schedule_rules):
        return normalized_requested
    if normalized_requested:
        return normalized_requested
    user_timezone = get_user_setting_value(user_id, "general", "timezone", db)
    normalized_user_timezone = str(user_timezone or "").strip()
    return normalized_user_timezone or "UTC"


def _extract_webhook_secret(request: Request) -> str | None:
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    secret = request.headers.get("x-omlorix-webhook-secret")
    if secret:
        return secret.strip() or None
    return None


def _rate_limit_webhook_trigger(trigger_id: str, limit: int) -> tuple[bool, int]:
    safe_limit = max(1, int(limit or 1))
    now = int(time.time())
    window_start = now - (now % 60)
    key = f"omlorix:automation_webhook:{trigger_id}:{window_start}"
    redis_client = get_redis_client()
    if redis_client is not None:
        try:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, 61)
            ttl = int(redis_client.ttl(key) or 60)
            if ttl < 0:
                ttl = 60
            return count <= safe_limit, max(1, ttl)
        except Exception:
            pass

    expires_at = window_start + 60
    with _WEBHOOK_LOCAL_RATE_LIMIT_LOCK:
        _WEBHOOK_LOCAL_RATE_LIMITS.update({
            stored_key: value
            for stored_key, value in _WEBHOOK_LOCAL_RATE_LIMITS.items()
            if value[1] > now
        })
        count, _ = _WEBHOOK_LOCAL_RATE_LIMITS.get(key, (0, expires_at))
        count += 1
        _WEBHOOK_LOCAL_RATE_LIMITS[key] = (count, expires_at)
    return count <= safe_limit, max(1, expires_at - now)


def _redact_value(value: Any, key_name: str | None = None, depth: int = 0) -> Any:
    normalized_key = str(key_name or "").strip().lower()
    if normalized_key in _SENSITIVE_PAYLOAD_KEYS or any(token in normalized_key for token in ("secret", "token", "password", "api_key")):
        return "[redacted]"
    if depth >= 5:
        return "[truncated]"
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_PREVIEW_COLLECTION_ITEMS]
        return {str(key)[:128]: _redact_value(item, str(key), depth + 1) for key, item in items}
    if isinstance(value, list):
        return [_redact_value(item, None, depth + 1) for item in value[:_MAX_PREVIEW_COLLECTION_ITEMS]]
    if isinstance(value, str):
        return value[:_MAX_PREVIEW_STRING_LENGTH] + ("..." if len(value) > _MAX_PREVIEW_STRING_LENGTH else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_PREVIEW_STRING_LENGTH]


def _filtered_headers(request: Request, trigger: AutomationWebhookTrigger) -> dict[str, str]:
    if not trigger.include_headers:
        return {}
    allowed = {name.lower() for name in (trigger.allowed_header_names or [])}
    if not allowed:
        return {}
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lower_name = name.lower()
        if lower_name in allowed:
            headers[lower_name] = str(_redact_value(value, lower_name))
    return headers


async def _read_webhook_payload(request: Request, trigger: AutomationWebhookTrigger) -> tuple[dict[str, Any], dict[str, Any]]:
    content_length_raw = request.headers.get("content-length")
    if content_length_raw:
        try:
            if int(content_length_raw) > trigger.max_body_bytes:
                raise HTTPException(status_code=413, detail="Webhook payload is too large")
        except ValueError:
            pass

    body_bytes = await request.body()
    if len(body_bytes) > trigger.max_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    parsed_body: Any = None
    if body_bytes:
        if content_type == "application/json" or content_type.endswith("+json"):
            try:
                parsed_body = json.loads(body_bytes.decode("utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON") from exc
        elif content_type == "application/x-www-form-urlencoded":
            form = await request.form()
            parsed_body = {key: form.getlist(key) if len(form.getlist(key)) > 1 else form.get(key) for key in form.keys()}
        else:
            parsed_body = body_bytes.decode("utf-8", errors="replace")

    query = dict(request.query_params)
    query.pop("secret", None)
    context = {
        "trigger_id": trigger.id,
        "automation_id": trigger.automation_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "content_type": content_type or None,
        "query": query,
        "headers": _filtered_headers(request, trigger),
        "body": parsed_body,
    }
    preview = _redact_value(context)
    return context, preview


# -------------------
# List automations
# -------------------
@automations_router.get("/list", response_model=AutomationListResponse)
def list_automations_route(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """List all automations for the current user."""
    _ensure_automations_enabled(user.id, db)
    automations = list_automations(db, user.id, limit=limit + 1, offset=offset)
    automations, has_more = page_from_limited_items(automations, limit=limit)
    # Every automation in this page advertises the same canonical origin. Load
    # it once so serializing a large page does not repeat settings-cache calls.
    webhook_base_url = _configured_webhook_base_url(db)
    return AutomationListResponse(
        automations=[
            _automation_to_response(
                automation,
                request,
                db=db,
                webhook_base_url=webhook_base_url,
            )
            for automation in automations
        ],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


# -------------------
# Webhook trigger management
# -------------------
@automations_router.post("/webhook/credentials", response_model=AutomationWebhookCredentialsResponse)
def reserve_automation_webhook_credentials_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Reserve the final webhook URL and secret for an unsaved create form."""
    _ensure_automations_enabled(user.id, db)
    trigger_id = str(uuid.uuid4())
    secret = generate_webhook_secret()
    reservation_token, expires_at = _create_webhook_reservation_token(
        db,
        user_id=user.id,
        trigger_id=trigger_id,
        secret=secret,
    )
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_WEBHOOK_CREDENTIALS_RESERVED",
        {
            "trigger_id": trigger_id,
            "expires_at": expires_at.isoformat(),
        },
    )
    return AutomationWebhookCredentialsResponse(
        trigger_id=trigger_id,
        url=_public_webhook_url(
            request,
            trigger_id,
            configured_base_url=_configured_webhook_base_url(db),
        ),
        secret=secret,
        reservation_token=reservation_token,
        expires_at=expires_at,
    )


@automations_router.get("/{automation_id}/webhook", response_model=AutomationWebhookStatusResponse)
def get_automation_webhook_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Get the webhook trigger for an automation, if configured."""
    _ensure_automations_enabled(user.id, db)
    automation = get_automation(db, automation_id, user.id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    trigger = get_webhook_trigger_for_automation(db, user.id, automation_id)
    return AutomationWebhookStatusResponse(
        status="success",
        trigger=_webhook_trigger_to_response(
            request,
            trigger,
            webhook_base_url=_configured_webhook_base_url(db),
        ),
    )


@automations_router.post("/{automation_id}/webhook", response_model=AutomationWebhookStatusResponse)
def create_automation_webhook_route(
    automation_id: str,
    payload: AutomationWebhookTriggerCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Create a webhook trigger for an automation. The returned secret is shown once."""
    _ensure_automations_enabled(user.id, db)
    existing = get_webhook_trigger_for_automation(db, user.id, automation_id)
    if existing:
        raise HTTPException(status_code=409, detail="Webhook trigger already exists for this automation")
    trigger, secret = create_webhook_trigger(
        db,
        user.id,
        automation_id,
        name=payload.name,
        is_enabled=True if payload.is_enabled is None else payload.is_enabled,
        payload_mode=payload.payload_mode,
        include_headers=bool(payload.include_headers),
        allowed_header_names=payload.allowed_header_names or [],
        max_body_bytes=payload.max_body_bytes,
        rate_limit_per_minute=payload.rate_limit_per_minute,
    )
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_WEBHOOK_CREATED",
        {
            "automation_id": automation_id,
            "trigger_id": trigger.id,
            "is_enabled": trigger.is_enabled,
            "payload_mode": trigger.payload_mode,
            "include_headers": trigger.include_headers,
        },
    )
    return AutomationWebhookStatusResponse(
        status="success",
        message="Webhook trigger created successfully",
        trigger=_webhook_trigger_to_response(
            request,
            trigger,
            webhook_base_url=_configured_webhook_base_url(db),
            secret=secret,
        ),
    )


@automations_router.put("/{automation_id}/webhook", response_model=AutomationWebhookStatusResponse)
def update_automation_webhook_route(
    automation_id: str,
    payload: AutomationWebhookTriggerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Update an automation webhook trigger."""
    _ensure_automations_enabled(user.id, db)
    trigger = get_webhook_trigger_for_automation(db, user.id, automation_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    updated = update_webhook_trigger(
        db,
        user.id,
        trigger.id,
        name=payload.name,
        is_enabled=payload.is_enabled,
        payload_mode=payload.payload_mode,
        include_headers=payload.include_headers,
        allowed_header_names=payload.allowed_header_names,
        max_body_bytes=payload.max_body_bytes,
        rate_limit_per_minute=payload.rate_limit_per_minute,
    )
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_WEBHOOK_UPDATED",
        {
            "automation_id": automation_id,
            "trigger_id": trigger.id,
            "updated_fields": sorted(getattr(payload, "model_fields_set", set())),
            "is_enabled": updated.is_enabled,
        },
    )
    return AutomationWebhookStatusResponse(
        status="success",
        message="Webhook trigger updated successfully",
        trigger=_webhook_trigger_to_response(
            request,
            updated,
            webhook_base_url=_configured_webhook_base_url(db),
        ),
    )


@automations_router.post("/{automation_id}/webhook/rotate", response_model=AutomationWebhookStatusResponse)
def rotate_automation_webhook_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Rotate an automation webhook secret. The returned secret is shown once."""
    _ensure_automations_enabled(user.id, db)
    trigger = get_webhook_trigger_for_automation(db, user.id, automation_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    rotated, secret = rotate_webhook_trigger_secret(db, user.id, trigger.id)
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_WEBHOOK_SECRET_ROTATED",
        {"automation_id": automation_id, "trigger_id": trigger.id},
    )
    return AutomationWebhookStatusResponse(
        status="success",
        message="Webhook secret rotated successfully",
        trigger=_webhook_trigger_to_response(
            request,
            rotated,
            webhook_base_url=_configured_webhook_base_url(db),
            secret=secret,
        ),
    )


@automations_router.delete("/{automation_id}/webhook")
def delete_automation_webhook_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete an automation webhook trigger."""
    _ensure_automations_enabled(user.id, db)
    trigger = get_webhook_trigger_for_automation(db, user.id, automation_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    delete_webhook_trigger(db, user.id, trigger.id)
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_WEBHOOK_DELETED",
        {"automation_id": automation_id, "trigger_id": trigger.id},
    )
    return {"status": "success", "message": "Webhook trigger deleted successfully"}


@automations_router.get("/{automation_id}/webhook/deliveries", response_model=AutomationWebhookDeliveriesResponse)
def list_automation_webhook_deliveries_route(
    automation_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """List recent deliveries for an automation webhook trigger."""
    _ensure_automations_enabled(user.id, db)
    trigger = get_webhook_trigger_for_automation(db, user.id, automation_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    deliveries = list_webhook_deliveries(db, user.id, trigger.id, limit=limit)
    return AutomationWebhookDeliveriesResponse(deliveries=[_delivery_to_response(delivery) for delivery in deliveries])


# -------------------
# Public webhook trigger
# -------------------
@automations_router.post("/webhooks/{trigger_id}", name="trigger_automation_webhook_route")
async def trigger_automation_webhook_route(
    trigger_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Public endpoint that enqueues an automation execution from a webhook."""
    trigger = get_webhook_trigger(db, trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")

    automation = get_automation(db, trigger.automation_id, trigger.user_id)
    if not automation:
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=trigger.automation_id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=404,
            error="Automation not found",
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=404, detail="Webhook trigger not found")

    secret = _extract_webhook_secret(request)
    if not verify_webhook_secret(trigger, secret):
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=401,
            error="Invalid webhook secret",
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        _audit_automation_event(
            db_log,
            request,
            trigger.user_id,
            "AUTOMATION_WEBHOOK_AUTH_FAILED",
            {"automation_id": automation.id, "trigger_id": trigger.id},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    if not trigger.is_enabled or not automation.is_active:
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=409,
            error="Webhook trigger or automation is disabled",
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=409, detail="Webhook trigger or automation is disabled")

    try:
        owner = get_user(db, user_id=trigger.user_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=404,
            error="Webhook owner not found",
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=404, detail="Webhook trigger not found") from exc

    try:
        ensure_user_runtime_auth_allowed(
            owner,
            db,
            ip_address=_client_ip(request),
            event_source="automation_webhook",
        )
        _ensure_automations_enabled(trigger.user_id, db)
    except HTTPException as exc:
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=exc.status_code,
            error=_webhook_error_message(exc.detail),
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        _audit_automation_event(
            db_log,
            request,
            trigger.user_id,
            "AUTOMATION_WEBHOOK_ACCESS_BLOCKED",
            {
                "automation_id": automation.id,
                "trigger_id": trigger.id,
                "status_code": exc.status_code,
            },
        )
        raise

    allowed, retry_after = _rate_limit_webhook_trigger(trigger.id, trigger.rate_limit_per_minute)
    response.headers["X-RateLimit-Limit"] = str(trigger.rate_limit_per_minute)
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + retry_after)
    if not allowed:
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=429,
            error="Webhook rate limit exceeded",
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(
            status_code=429,
            detail="Webhook rate limit exceeded. Please retry later.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        webhook_context, payload_preview = await _read_webhook_payload(request, trigger)
    except HTTPException as exc:
        create_webhook_delivery(
            db,
            trigger_id=trigger.id,
            automation_id=automation.id,
            user_id=trigger.user_id,
            status="rejected",
            status_code=exc.status_code,
            error=str(exc.detail),
            request_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise
    delivery = create_webhook_delivery(
        db,
        trigger_id=trigger.id,
        automation_id=automation.id,
        user_id=trigger.user_id,
        status="accepted",
        status_code=202,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        payload_preview=payload_preview,
    )

    webhook_context["delivery_id"] = delivery.id
    idempotency_key = request.headers.get("idempotency-key") or request.headers.get("x-github-delivery")
    slot_key = (
        f"webhook:{trigger.id}:idempotency:{idempotency_key}"
        if idempotency_key
        else f"webhook:{trigger.id}:{delivery.id}"
    )
    update_webhook_delivery(db, delivery.id, status="queued", status_code=202)
    enqueue_result = enqueue_automation_execution(
        automation.id,
        trigger.user_id,
        slot_key,
        trigger_context={
            "type": "webhook",
            "trigger_id": trigger.id,
            "delivery_id": delivery.id,
            "payload_mode": trigger.payload_mode,
            "webhook": webhook_context,
        },
    )
    if enqueue_result.status == "duplicate":
        update_webhook_delivery(
            db,
            delivery.id,
            status="duplicate",
            status_code=202,
        )
        return {"status": "duplicate", "message": "Webhook delivery was already queued", "delivery_id": delivery.id}
    if enqueue_result.status == "failed":
        update_webhook_delivery(
            db,
            delivery.id,
            status="failed",
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="Failed to queue webhook delivery")

    update_automation_last_triggered(db, automation.id)
    update_webhook_trigger_last_triggered(db, trigger.id)
    _audit_automation_event(
        db_log,
        request,
        trigger.user_id,
        "AUTOMATION_WEBHOOK_TRIGGERED",
        {"automation_id": automation.id, "trigger_id": trigger.id, "delivery_id": delivery.id},
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return {"status": "queued", "delivery_id": delivery.id}


# -------------------
# Get single automation
# -------------------
@automations_router.get("/{automation_id}", response_model=AutomationResponse)
def get_automation_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Get a specific automation by ID."""
    _ensure_automations_enabled(user.id, db)
    automation = get_automation(db, automation_id, user.id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    return _automation_to_response(
        automation,
        request,
        db=db,
        webhook_base_url=_configured_webhook_base_url(db),
    )


# -------------------
# Create automation
# -------------------
@automations_router.post("/create", response_model=AutomationStatusResponse)
def create_automation_route(
    payload: AutomationCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Create a new automation."""
    _ensure_automations_enabled(user.id, db)
    schedule_rules = [rule.model_dump() for rule in payload.schedule_rules] if payload.schedule_rules else []
    schedule_timezone = _resolve_automation_schedule_timezone(
        user.id,
        db,
        schedule_rules,
        payload.schedule_timezone,
    )
    # Keep the automation and optional webhook in one transaction. This lets the
    # create form configure a webhook before an automation ID exists and avoids
    # leaving a partial automation behind if webhook creation fails.
    automation = create_automation(
        db=db,
        user_id=user.id,
        title=payload.title,
        prompt=payload.prompt,
        model_id=payload.model_id,
        icon=payload.icon,
        icon_color=payload.icon_color,
        schedule_rules=schedule_rules,
        schedule_timezone=schedule_timezone,
        skill_id=payload.skill_id,
        note_ids=payload.note_ids or [],
        file_ids=payload.file_ids or [],
        mcp_server_ids=payload.mcp_server_ids or [],
        is_active=payload.is_active if payload.is_active is not None else True,
        commit=False,
    )
    webhook_trigger = None
    webhook_secret = None
    if payload.webhook_trigger is not None:
        webhook_payload = payload.webhook_trigger
        _verify_webhook_reservation(
            db,
            user_id=user.id,
            trigger_id=webhook_payload.trigger_id,
            secret=webhook_payload.secret,
            reservation_token=webhook_payload.reservation_token,
        )
        webhook_trigger, webhook_secret = create_webhook_trigger(
            db,
            user.id,
            automation.id,
            name=webhook_payload.name,
            is_enabled=True if webhook_payload.is_enabled is None else webhook_payload.is_enabled,
            payload_mode=webhook_payload.payload_mode,
            include_headers=bool(webhook_payload.include_headers),
            allowed_header_names=webhook_payload.allowed_header_names or [],
            max_body_bytes=webhook_payload.max_body_bytes,
            rate_limit_per_minute=webhook_payload.rate_limit_per_minute,
            commit=False,
            trigger_id=webhook_payload.trigger_id,
            secret=webhook_payload.secret,
        )
    db.commit()
    db.refresh(automation)
    if webhook_trigger is not None:
        db.refresh(webhook_trigger)
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_CREATED",
        {
            "automation_id": automation.id,
            "model_id": payload.model_id,
            "schedule_rule_count": len(schedule_rules),
            "schedule_timezone": automation.schedule_timezone,
            "skill_id": payload.skill_id,
            "note_count": len(payload.note_ids or []),
            "file_count": len(payload.file_ids or []),
            "connection_count": len(payload.mcp_server_ids or []),
            "is_active": automation.is_active,
            "webhook_trigger_created": webhook_trigger is not None,
        },
    )
    if webhook_trigger is not None:
        _audit_automation_event(
            db_log,
            request,
            user.id,
            "AUTOMATION_WEBHOOK_CREATED",
            {
                "automation_id": automation.id,
                "trigger_id": webhook_trigger.id,
                "is_enabled": webhook_trigger.is_enabled,
                "payload_mode": webhook_trigger.payload_mode,
                "include_headers": webhook_trigger.include_headers,
            },
        )
    return AutomationStatusResponse(
        status="success",
        message="Automation created successfully",
        automation=_automation_to_response(
            automation,
            request,
            db=db,
            webhook_base_url=_configured_webhook_base_url(db),
            webhook_trigger=webhook_trigger,
            webhook_secret=webhook_secret,
        ),
    )


# -------------------
# Update automation
# -------------------
@automations_router.put("/update", response_model=AutomationStatusResponse)
def update_automation_route(
    payload: AutomationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Update an existing automation."""
    _ensure_automations_enabled(user.id, db)
    schedule_rules = None
    if payload.schedule_rules is not None:
        schedule_rules = [rule.model_dump() for rule in payload.schedule_rules]
    schedule_timezone = (
        _resolve_automation_schedule_timezone(
            user.id,
            db,
            schedule_rules,
            payload.schedule_timezone,
        )
        if payload.schedule_rules is not None or payload.schedule_timezone is not None
        else None
    )
    
    automation = update_automation(
        db=db,
        user_id=user.id,
        automation_id=payload.automation_id,
        title=payload.title,
        prompt=payload.prompt,
        model_id=payload.model_id,
        icon=payload.icon,
        icon_color=payload.icon_color,
        schedule_rules=schedule_rules,
        schedule_timezone=schedule_timezone,
        skill_id=(
            ""
            if "skill_id" in payload.model_fields_set and payload.skill_id is None
            else payload.skill_id
        ),
        note_ids=payload.note_ids,
        file_ids=payload.file_ids,
        mcp_server_ids=payload.mcp_server_ids,
        is_active=payload.is_active,
    )
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_UPDATED",
        {
            "automation_id": payload.automation_id,
            "updated_fields": sorted(getattr(payload, "model_fields_set", set())),
            "schedule_rule_count": len(schedule_rules) if schedule_rules is not None else None,
            "schedule_timezone": automation.schedule_timezone,
            "note_count": len(payload.note_ids or []) if payload.note_ids is not None else None,
            "file_count": len(payload.file_ids or []) if payload.file_ids is not None else None,
            "connection_count": len(payload.mcp_server_ids or []) if payload.mcp_server_ids is not None else None,
            "is_active": automation.is_active,
        },
    )
    return AutomationStatusResponse(
        status="success",
        message="Automation updated successfully",
        automation=_automation_to_response(
            automation,
            request,
            db=db,
            webhook_base_url=_configured_webhook_base_url(db),
        ),
    )


# -------------------
# Delete automation
# -------------------
@automations_router.delete("/delete")
def delete_automation_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete an automation."""
    _ensure_automations_enabled(user.id, db)
    delete_automation(db, user.id, automation_id)
    _audit_automation_event(db_log, request, user.id, "AUTOMATION_DELETED", {"automation_id": automation_id})
    return {"status": "success", "message": "Automation deleted successfully"}


# -------------------
# Toggle automation active status
# -------------------
@automations_router.post("/{automation_id}/toggle", response_model=AutomationStatusResponse)
def toggle_automation_route(
    automation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Toggle the active status of an automation."""
    _ensure_automations_enabled(user.id, db)
    automation = get_automation(db, automation_id, user.id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    
    updated_automation = update_automation(
        db=db,
        user_id=user.id,
        automation_id=automation_id,
        is_active=not automation.is_active,
    )
    _audit_automation_event(
        db_log,
        request,
        user.id,
        "AUTOMATION_TOGGLED",
        {"automation_id": automation_id, "is_active": updated_automation.is_active},
    )
    return AutomationStatusResponse(
        status="success",
        message=f"Automation {'activated' if updated_automation.is_active else 'deactivated'} successfully",
        automation=_automation_to_response(
            updated_automation,
            request,
            db=db,
            webhook_base_url=_configured_webhook_base_url(db),
        ),
    )
