import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.ip_analytics.models import (
    count_ip_statistics,
    get_active_ip_block,
    get_blocked_ip_statistics,
    get_ip_statistics_settings_page,
    list_blocked_ips_page,
    persist_ip_statistics_settings_page,
    update_blocked_ip,
)
from app.ip_analytics.schemas import (
    AdminBlockedIPPage,
    AdminIPAddressSecurityEventPage,
    AdminIPAddressStatisticsDeleteRequest,
    AdminIPAddressStatisticsFilterOptions,
    AdminIPAddressStatisticsImportResult,
    AdminIPAddressStatisticsMutationResult,
    AdminIPAddressStatisticsOverview,
    AdminIPAddressStatisticsSettings,
    AdminIPAddressStatisticsSettingsUpdate,
    BlockIP,
    EditIPBlock,
)
from app.auth.models import (
    LOCALHOST_IP_BLOCK_ERROR,
    block_ip_address,
    deblock_ip_address,
    delete_expired_blocked_ip_addresses,
    get_ip_address_statistics_retention_days,
    get_ip_address_statistics_settings,
    is_ip_address_statistics_enabled,
    is_loopback_ip_address,
    normalize_ip_address_for_storage,
    normalize_ip_security_event_type,
    record_ip_address_security_event,
)
from app.dependencies import get_db, get_db_log, verified_admin
from app.ip_analytics.service import (
    build_overview,
    delete_statistics,
    enrich_pending_with_session_factory,
    filter_options,
    import_payload,
    iter_export_json,
    list_events,
    provider_status,
)
from app.logging.models import create_audit_log, get_audit_request_ip
from app.utils.schemas import OperationResult

def _coerce_ip_address_statistics_retention_days(value: Any) -> int:
    """Normalize stored IP statistics retention settings before API responses."""
    try:
        retention_days = int(value or 90)
    except (TypeError, ValueError):
        retention_days = 90
    return max(1, min(retention_days, 3650))


def _analytics_period(days: int, db: Session) -> tuple[datetime, datetime]:
    """Return the effective UTC period after applying configured retention."""

    end = datetime.now(timezone.utc)
    retention_days = get_ip_address_statistics_retention_days(db)
    return end - timedelta(days=min(days, retention_days)), end


logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.get(
    "/ip-address/statistics/settings", response_model=AdminIPAddressStatisticsSettings
)
def get_ip_address_statistics_settings_route(
    db: Session = Depends(get_db), admin_user=Depends(verified_admin)
):
    settings = get_ip_address_statistics_settings(db)
    provider = provider_status(
        db,
        analytics_enabled=bool(
            settings.get("enabled") and settings.get("regulatory_confirmed")
        ),
    )
    return {
        "enabled": bool(settings.get("enabled", False)),
        "regulatory_confirmed": bool(settings.get("regulatory_confirmed", False)),
        "regulatory_justification": str(settings.get("regulatory_justification", "")),
        "policy_reference": str(settings.get("policy_reference", "")),
        "retention_policy": str(settings.get("retention_policy", "")),
        "retention_days": _coerce_ip_address_statistics_retention_days(
            settings.get("retention_days")
        ),
        "geo_provider": provider["provider"],
        "geo_provider_configured": provider["configured"],
    }


@admin_router.post(
    "/ip-address/statistics/settings", response_model=AdminIPAddressStatisticsSettings
)
def update_ip_address_statistics_settings_route(
    payload: AdminIPAddressStatisticsSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    settings_page = get_ip_statistics_settings_page(db)
    if not settings_page:
        raise HTTPException(
            status_code=404, detail="IP address statistics settings page not found"
        )

    # Validate against a detached copy so rejected input never dirties the
    # request-scoped ORM instance or becomes eligible for an accidental flush.
    current_data = (
        dict(settings_page.data) if isinstance(settings_page.data, dict) else {}
    )

    updates: dict[str, Any] = {}

    if payload.enabled is not None:
        current_data["enabled"] = payload.enabled
        updates["enabled"] = payload.enabled
    if payload.regulatory_confirmed is not None:
        current_data["regulatory_confirmed"] = payload.regulatory_confirmed
        updates["regulatory_confirmed"] = payload.regulatory_confirmed

    for key in ("regulatory_justification", "policy_reference", "retention_policy"):
        value = getattr(payload, key)
        if value is not None:
            current_data[key] = value
            updates[key] = value

    if payload.retention_days is not None:
        current_data["retention_days"] = payload.retention_days
        updates["retention_days"] = payload.retention_days

    regulatory_confirmed_effective = updates.get(
        "regulatory_confirmed", current_data.get("regulatory_confirmed", False)
    )
    regulatory_justification_effective = updates.get(
        "regulatory_justification",
        current_data.get("regulatory_justification", ""),
    )
    policy_reference_effective = updates.get(
        "policy_reference", current_data.get("policy_reference", "")
    )

    if regulatory_confirmed_effective and not (
        str(regulatory_justification_effective).strip()
        or str(policy_reference_effective).strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot confirm IP address statistics regulatory status without documenting the legal basis. Provide either regulatory_justification or policy_reference.",
        )

    # Validation: prevent enabling without regulatory confirmation and documentation
    if updates.get("enabled", current_data.get("enabled", False)):
        if not regulatory_confirmed_effective:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot enable IP address statistics without regulatory confirmation. Set regulatory_confirmed to true and provide regulatory_justification or policy_reference.",
            )

        if (
            not str(regulatory_justification_effective).strip()
            and not str(policy_reference_effective).strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot enable IP address statistics without documentation. Provide either regulatory_justification or policy_reference.",
            )

    # Attach only the validated effective state immediately before persistence.
    settings_page.data = current_data
    settings_page.updated_at = datetime.now(timezone.utc)
    persist_ip_statistics_settings_page(db, settings_page)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_IP_ADDRESS_STATISTICS_SETTINGS",
        details={"updates": updates},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="security",
    )

    provider = provider_status(
        db,
        analytics_enabled=bool(
            current_data.get("enabled") and current_data.get("regulatory_confirmed")
        ),
    )
    return {
        "enabled": bool(current_data.get("enabled", False)),
        "regulatory_confirmed": bool(current_data.get("regulatory_confirmed", False)),
        "regulatory_justification": str(
            current_data.get("regulatory_justification", "")
        ),
        "policy_reference": str(current_data.get("policy_reference", "")),
        "retention_policy": str(current_data.get("retention_policy", "")),
        "retention_days": _coerce_ip_address_statistics_retention_days(
            current_data.get("retention_days")
        ),
        "geo_provider": provider["provider"],
        "geo_provider_configured": provider["configured"],
    }


@admin_router.get(
    "/ip-address/statistics/overview", response_model=AdminIPAddressStatisticsOverview
)
async def get_ip_address_statistics_overview_route(
    request: Request,
    background_tasks: BackgroundTasks,
    days: int = Query(default=30, ge=1, le=365),
    ip_address: str | None = Query(default=None, max_length=45),
    country_code: str | None = Query(default=None, min_length=2, max_length=8),
    event_type: str | None = Query(default=None, max_length=64),
    event_source: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Return aggregate analytics without performing provider I/O inline."""

    ip_address = ip_address if isinstance(ip_address, str) else None
    country_code = country_code if isinstance(country_code, str) else None
    event_type = event_type if isinstance(event_type, str) else None
    event_source = event_source if isinstance(event_source, str) else None
    normalized_ip = None
    if ip_address:
        normalized_ip = normalize_ip_address_for_storage(ip_address)
        if not normalized_ip:
            raise HTTPException(
                status_code=422, detail="Enter a valid IPv4 or IPv6 address."
            )
    normalized_event_type = None
    if event_type:
        normalized_event_type = normalize_ip_security_event_type(event_type)
        if not normalized_event_type:
            raise HTTPException(
                status_code=422, detail="Unknown IP analytics event type."
            )

    audit_ip_address = get_audit_request_ip(request, db)
    delete_expired_blocked_ip_addresses(db)
    response = build_overview(
        db,
        days=days,
        ip_address=normalized_ip,
        country_code=country_code,
        event_type=normalized_event_type,
        event_source=event_source,
    )
    settings = get_ip_address_statistics_settings(db)
    if settings.get("enabled") and settings.get("regulatory_confirmed"):
        from app.workers.events import enqueue_ip_enrichment, external_audit_event_enabled

        if external_audit_event_enabled():
            enqueue_ip_enrichment(db)
        else:
            background_tasks.add_task(
                enrich_pending_with_session_factory,
                request.app.state.db,
            )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="VIEW_IP_ADDRESS_STATISTICS_OVERVIEW",
        details={
            "filters": {
                "days": days,
                "ip_address": normalized_ip,
                "country_code": country_code,
                "event_type": normalized_event_type,
                "event_source": event_source,
            },
            "result_counts": {
                "countries": len(response["countries"]),
            },
        },
        ip_address=audit_ip_address,
        user_agent=request.headers.get("user-agent"),
        category="security",
    )
    return response


@admin_router.get(
    "/ip-address/statistics/events", response_model=AdminIPAddressSecurityEventPage
)
def list_ip_address_statistics_events_route(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    ip_address: str | None = Query(default=None, max_length=45),
    country_code: str | None = Query(default=None, min_length=2, max_length=8),
    event_type: str | None = Query(default=None, max_length=64),
    event_source: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Return a paginated event timeline using the same dashboard filters."""

    normalized_ip = normalize_ip_address_for_storage(ip_address) if ip_address else None
    if ip_address and not normalized_ip:
        raise HTTPException(
            status_code=422, detail="Enter a valid IPv4 or IPv6 address."
        )
    normalized_event_type = (
        normalize_ip_security_event_type(event_type) if event_type else None
    )
    if event_type and not normalized_event_type:
        raise HTTPException(status_code=422, detail="Unknown IP analytics event type.")
    period_start, period_end = _analytics_period(days, db)
    result = list_events(
        db,
        page=page,
        per_page=per_page,
        period_start=period_start,
        period_end=period_end,
        ip_address=normalized_ip,
        country_code=country_code,
        event_type=normalized_event_type,
        event_source=event_source,
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="VIEW_IP_ADDRESS_STATISTICS_EVENTS",
        details={
            "filters": {
                "days": days,
                "page": page,
                "per_page": per_page,
                "ip_address": normalized_ip,
                "country_code": country_code,
                "event_type": normalized_event_type,
                "event_source": event_source,
            },
            "result_count": len(result["items"]),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="security",
    )
    return result


@admin_router.get(
    "/ip-address/statistics/filters",
    response_model=AdminIPAddressStatisticsFilterOptions,
)
def get_ip_address_statistics_filter_options_route(
    db: Session = Depends(get_db), admin_user=Depends(verified_admin)
):
    """Return the finite values available in analytics filter controls."""

    return filter_options(db)


@admin_router.delete(
    "/ip-address/statistics", response_model=AdminIPAddressStatisticsMutationResult
)
def delete_ip_address_statistics_route(
    payload: AdminIPAddressStatisticsDeleteRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete analytics only; managed bans remain untouched."""

    normalized_ip = None
    if payload.ip_address:
        normalized_ip = normalize_ip_address_for_storage(payload.ip_address)
        if not normalized_ip:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Enter a valid IPv4 or IPv6 address.",
            )

    deleted = delete_statistics(db, days=payload.days, ip_address=normalized_ip)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_IP_ADDRESS_STATISTICS",
        details={
            "scope": {
                "days": payload.days,
                "ip_address": normalized_ip,
            },
            "deleted_rows": deleted,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="security",
    )
    return {"status": "success", "affected_rows": deleted}


@admin_router.get("/ip-address/statistics/export")
def export_ip_address_statistics_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Download a versioned JSON backup without Geo-IP API credentials."""

    event_count = count_ip_statistics(db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_IP_ADDRESS_STATISTICS",
        details={"event_count": event_count},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="security",
    )
    filename = (
        f"omlorix-ip-analytics-{datetime.now(timezone.utc).date().isoformat()}.json"
    )
    return StreamingResponse(
        iter_export_json(db),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@admin_router.post(
    "/ip-address/statistics/import", response_model=AdminIPAddressStatisticsImportResult
)
async def import_ip_address_statistics_route(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Import a JSON backup with a conservative 20 MiB upload limit."""

    raw = await file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="IP analytics import files must be 20 MiB or smaller.",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
        result = import_payload(db, payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_IP_ADDRESS_STATISTICS",
        details={
            "imported_rows": result["imported_rows"],
            "skipped_rows": result["skipped_rows"],
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="security",
    )
    return result


@admin_router.post("/ip-address/block", status_code=status.HTTP_201_CREATED)
def upsert_ip_block_route(
    payload: BlockIP,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Block or unblock an IP address."""
    if payload.banned:
        audit_ip_address = get_audit_request_ip(request, db)
        normalized_target_ip = normalize_ip_address_for_storage(payload.ip_address)
        normalized_admin_ip = normalize_ip_address_for_storage(audit_ip_address)
        if (
            normalized_target_ip
            and normalized_admin_ip
            and normalized_target_ip == normalized_admin_ip
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You cannot block the IP address used by your current admin session.",
            )

        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.duration_days)
        result = block_ip_address(payload.ip_address, expires_at, payload.reason, db)
        if result.get("status") != "success":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=result.get("message") or "Failed to block IP address",
            )
        try:
            record_ip_address_security_event(
                db,
                payload.ip_address,
                "ban_created",
                event_source="admin_manual",
                reason_code="manual",
                reason=payload.reason,
                is_automatic=False,
                aggregate=False,
            )
        except Exception:
            logger.error(
                "Failed to record IP address security event",
                extra={"ip_address": payload.ip_address, "reason": payload.reason},
                exc_info=True,
            )
            db.rollback()
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="BLOCK_IP_ADDRESS",
            reason=payload.reason,
            details={
                "ip_address": payload.ip_address,
                "expires_at": expires_at.isoformat(),
            },
            ip_address=audit_ip_address,
            user_agent=request.headers.get("user-agent"),
            category="admin",
        )
        return result

    result = deblock_ip_address(payload.ip_address, db)
    if result.get("status") == "ip_not_blocked":
        raise HTTPException(
            status_code=404, detail="IP address is not currently blocked"
        )
    if result.get("status") != "success":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=result.get("message") or "Failed to unblock IP address",
        )
    try:
        # The enforcement mutation above is already committed. Analytics is
        # optional and must not turn a successful unblock into a 500 response.
        record_ip_address_security_event(
            db,
            payload.ip_address,
            "ban_removed",
            event_source="admin_manual",
            reason_code="manual",
            reason="IP ban removed by administrator",
            is_automatic=False,
            aggregate=False,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to record administrator IP-unblock analytics event",
            extra={"ip_address": payload.ip_address},
        )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UNBLOCK_IP_ADDRESS",
        details={
            "ip_address": payload.ip_address,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.put("/ip-address/blocked/{ip_address}", response_model=OperationResult)
def edit_ip_block_route(
    ip_address: str,
    payload: EditIPBlock,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Edit a saved IP ban while preserving the original block timestamp."""
    audit_ip_address = get_audit_request_ip(request, db)
    normalized_original_ip = normalize_ip_address_for_storage(ip_address)
    normalized_target_ip = normalize_ip_address_for_storage(payload.ip_address)
    normalized_admin_ip = normalize_ip_address_for_storage(audit_ip_address)

    if not normalized_original_ip:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid original IPv4 or IPv6 address.",
        )
    if not normalized_target_ip:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid IPv4 or IPv6 address.",
        )
    if (
        normalized_target_ip
        and normalized_admin_ip
        and normalized_target_ip == normalized_admin_ip
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot block the IP address used by your current admin session.",
        )
    if is_loopback_ip_address(normalized_target_ip):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=LOCALHOST_IP_BLOCK_ERROR,
        )

    entry = get_active_ip_block(db, normalized_original_ip)
    if entry is None:
        raise HTTPException(
            status_code=404, detail="IP address is not currently blocked"
        )

    if normalized_target_ip != normalized_original_ip:
        existing_target = get_active_ip_block(db, normalized_target_ip)
        if existing_target is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The target IP address is already blocked.",
            )

    previous_ip_address = entry.ip_address
    previous_expires_at = entry.expires_at
    previous_reason = entry.reason
    entry = update_blocked_ip(
        db,
        entry=entry,
        ip_address=normalized_target_ip,
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.duration_days),
        reason=payload.reason,
    )

    if previous_ip_address != entry.ip_address:
        try:
            # Record both sides of the address change in one optional
            # transaction. If analytics storage fails, the committed ban edit
            # and its mandatory audit record still succeed.
            record_ip_address_security_event(
                db,
                previous_ip_address,
                "ban_removed",
                event_source="admin_edit",
                reason_code="address_changed",
                reason="Managed ban address changed by administrator",
                is_automatic=False,
                aggregate=False,
                commit=False,
            )
            record_ip_address_security_event(
                db,
                entry.ip_address,
                "ban_created",
                event_source="admin_edit",
                reason_code="address_changed",
                reason=entry.reason,
                is_automatic=False,
                aggregate=False,
                commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to record administrator IP-ban address-change analytics events",
                extra={
                    "previous_ip_address": previous_ip_address,
                    "ip_address": entry.ip_address,
                },
            )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EDIT_IP_ADDRESS_BLOCK",
        reason=payload.reason,
        details={
            "previous": {
                "ip_address": previous_ip_address,
                "expires_at": previous_expires_at.isoformat()
                if previous_expires_at
                else None,
                "reason": previous_reason,
            },
            "updated": {
                "ip_address": entry.ip_address,
                "expires_at": entry.expires_at.isoformat()
                if entry.expires_at
                else None,
                "reason": entry.reason,
            },
        },
        ip_address=audit_ip_address,
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return OperationResult(status="success")


@admin_router.get("/ip-address/blocked", response_model=AdminBlockedIPPage)
async def list_blocked_ip_addresses_route(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List blocked IP addresses."""
    delete_expired_blocked_ip_addresses(db)
    blocked_ips, total = list_blocked_ips_page(db, page=page, per_page=per_page)
    stats_enabled = is_ip_address_statistics_enabled(db)

    country_by_ip: dict[str, str | None] = {}
    if stats_enabled and blocked_ips:
        page_ip_addresses = [entry.ip_address for entry in blocked_ips]
        # The list remains fast and deterministic: it reuses previously
        # enriched rows while the retention worker handles provider lookups.
        country_by_ip, blocked_attempt_stats_by_ip = get_blocked_ip_statistics(
            db,
            ip_addresses=page_ip_addresses,
        )
    else:
        blocked_attempt_stats_by_ip = {}

    payload: list[dict[str, Any]] = []
    for entry in blocked_ips:
        blocked_attempt_count = 0
        last_blocked_attempt_at = None
        country_code = None

        if stats_enabled:
            stats = blocked_attempt_stats_by_ip.get(entry.ip_address, {})
            blocked_attempt_count = stats.get("count", 0)
            last_blocked_attempt_at = stats.get("last_at")
            country_code = country_by_ip.get(entry.ip_address)

        payload.append(
            {
                "id": entry.id,
                "ip_address": entry.ip_address,
                "blocked_at": entry.blocked_at,
                "expires_at": entry.expires_at,
                "reason": entry.reason,
                "country_code": country_code,
                "blocked_attempt_count": int(blocked_attempt_count),
                "last_blocked_attempt_at": last_blocked_attempt_at,
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_BLOCKED_IP_ADDRESSES",
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {
        "items": payload,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
    }
