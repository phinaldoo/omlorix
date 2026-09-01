"""Business logic for privacy-controlled IP security analytics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any, Iterable
import uuid

from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import (
    BlockedIP,
    IPAddressSecurityStatistic,
    IP_SECURITY_EVENT_BAN_CREATED,
    IP_SECURITY_EVENT_RATE_LIMITED,
    IP_SECURITY_EVENT_REQUEST_DENIED,
    IP_SECURITY_EVENT_TYPES,
    get_ip_address_statistics_retention_days,
    get_ip_address_statistics_settings,
    normalize_ip_address_for_storage,
    normalize_ip_security_event_type,
)
from app.middleware.ip_restriction import get_country_by_ip
from app.settings.utils import get_value_by_page_and_key
from app.utils.export_versions import matches_export_version


COUNTRY_RESULT_LIMIT = 12
EVENT_PAGE_MAX = 100
GEO_ENRICHMENT_BATCH_SIZE = 50
IP_ANALYTICS_EXPORT_VERSION = 1.0
DEFINITIONS = {
    "active_bans": "IP addresses currently present in the managed ban list.",
    "denied_requests": "Requests rejected by an active ban or configured IP/country access policy.",
    "rate_limited_requests": "Requests rejected with HTTP 429 by the application rate limiter.",
    "ban_created": "A manual or automatic action that added an address to the managed ban list.",
    "ban_removed": "A manual action or expiry that removed an address from the managed ban list.",
    "activity_level": "Low: fewer than 10 denied requests; medium: 10–49; high: 50 or more in the selected period.",
}


def activity_level(denied_requests: int) -> str:
    """Classify observed volume without implying that a country or IP is risky."""

    if denied_requests >= 50:
        return "high"
    if denied_requests >= 10:
        return "medium"
    return "low"


def provider_status(db: Session, *, analytics_enabled: bool) -> dict[str, Any]:
    """Describe Geo-IP configuration without exposing provider credentials."""

    provider = str(
        get_value_by_page_and_key("security", "check_ip_location_provider", db) or ""
    ).strip().lower()
    configured = provider == "db-ip-free"
    if provider in {"ipinfo", "ipstack"}:
        configured = bool(get_value_by_page_and_key("api_keys", provider, db))
    status = "disabled" if not analytics_enabled else ("configured" if configured else "missing")
    return {
        "configured": configured,
        "provider": provider or None,
        "status": status,
        "sends_ip_to_external_provider": bool(configured and provider),
    }


def event_to_dict(row: IPAddressSecurityStatistic) -> dict[str, Any]:
    """Serialize an analytics row into the public admin event schema."""

    return {
        "id": row.id,
        "ip_address": row.ip_address,
        "country_code": row.country_code,
        "event_type": row.event_type,
        "event_source": row.event_source,
        "reason_code": row.reason_code,
        "route_category": row.route_category,
        "reason": row.reason,
        "request_count": int(row.request_count or 1),
        "is_automatic": bool(row.is_automatic),
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at or row.created_at,
        "bucket_start": row.bucket_start,
        "geo_provider": row.geo_provider,
        "geo_lookup_status": row.geo_lookup_status or "pending",
        "country_resolved_at": row.country_resolved_at,
    }


def apply_event_filters(
    query,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    ip_address: str | None = None,
    country_code: str | None = None,
    event_type: str | None = None,
    event_source: str | None = None,
):
    """Apply the shared analytics filter contract to a SQLAlchemy query."""

    if period_start is not None:
        query = query.filter(IPAddressSecurityStatistic.created_at >= period_start)
    if period_end is not None:
        query = query.filter(IPAddressSecurityStatistic.created_at <= period_end)
    if ip_address:
        query = query.filter(IPAddressSecurityStatistic.ip_address == ip_address)
    if country_code:
        if country_code.upper() == "UNKNOWN":
            query = query.filter(IPAddressSecurityStatistic.country_code.is_(None))
        else:
            query = query.filter(IPAddressSecurityStatistic.country_code == country_code.upper())
    if event_type:
        query = query.filter(IPAddressSecurityStatistic.event_type == event_type)
    if event_source:
        query = query.filter(IPAddressSecurityStatistic.event_source == event_source)
    return query


def _summary_from_query(
    db: Session,
    query,
    *,
    active_ban_ip: str | None = None,
) -> dict[str, Any]:
    """Calculate typed totals from a filtered analytics query.

    Ban rows do not carry the country or event metadata used by the analytics
    filters. An exact IP filter can still be applied, which keeps IP drilldown
    summaries scoped to the address the administrator selected.
    """

    subquery = query.with_entities(IPAddressSecurityStatistic).subquery()
    denied = int(
        db.query(func.coalesce(func.sum(subquery.c.request_count), 0))
        .filter(subquery.c.event_type == IP_SECURITY_EVENT_REQUEST_DENIED)
        .scalar()
        or 0
    )
    rate_limited = int(
        db.query(func.coalesce(func.sum(subquery.c.request_count), 0))
        .filter(subquery.c.event_type == IP_SECURITY_EVENT_RATE_LIMITED)
        .scalar()
        or 0
    )
    manual_bans = int(
        db.query(func.count(subquery.c.id))
        .filter(
            subquery.c.event_type == IP_SECURITY_EVENT_BAN_CREATED,
            subquery.c.is_automatic.is_(False),
        )
        .scalar()
        or 0
    )
    automatic_bans = int(
        db.query(func.count(subquery.c.id))
        .filter(
            subquery.c.event_type == IP_SECURITY_EVENT_BAN_CREATED,
            subquery.c.is_automatic.is_(True),
        )
        .scalar()
        or 0
    )
    unique_ips = int(db.query(func.count(func.distinct(subquery.c.ip_address))).scalar() or 0)
    denied_distinct_ips = int(
        db.query(func.count(func.distinct(subquery.c.ip_address)))
        .filter(subquery.c.event_type == IP_SECURITY_EVENT_REQUEST_DENIED)
        .scalar()
        or 0
    )
    unresolved_ips = int(
        db.query(func.count(func.distinct(subquery.c.ip_address)))
        .filter(subquery.c.country_code.is_(None))
        .scalar()
        or 0
    )
    known_countries = int(
        db.query(func.count(func.distinct(subquery.c.country_code)))
        .filter(subquery.c.country_code.isnot(None))
        .scalar()
        or 0
    )
    active_bans_query = db.query(BlockedIP)
    if active_ban_ip:
        active_bans_query = active_bans_query.filter(
            BlockedIP.ip_address == active_ban_ip
        )
    return {
        "active_bans": int(active_bans_query.count()),
        "known_origin_countries": known_countries,
        "denied_requests": denied,
        "rate_limited_requests": rate_limited,
        "manual_bans_created": manual_bans,
        "automatic_bans_created": automatic_bans,
        "unique_ips": unique_ips,
        "denied_distinct_ips": denied_distinct_ips,
        "unresolved_ips": unresolved_ips,
        "top_country_code": None,
        "top_country_denied_requests": 0,
        "top_country_distinct_ips": 0,
        "denied_requests_per_ip": round(denied / denied_distinct_ips, 2)
        if denied_distinct_ips
        else 0.0,
    }


def build_overview(
    db: Session,
    *,
    days: int,
    ip_address: str | None = None,
    country_code: str | None = None,
    event_type: str | None = None,
    event_source: str | None = None,
) -> dict[str, Any]:
    """Build the complete, explicitly-defined analytics overview."""

    now = datetime.now(timezone.utc)
    retention_days = get_ip_address_statistics_retention_days(db)
    requested_start = now - timedelta(days=days)
    retention_cutoff = now - timedelta(days=retention_days)
    effective_start = max(requested_start, retention_cutoff)
    settings = get_ip_address_statistics_settings(db)
    enabled = bool(settings.get("enabled")) and bool(settings.get("regulatory_confirmed"))

    base = apply_event_filters(
        db.query(IPAddressSecurityStatistic),
        period_start=effective_start,
        period_end=now,
        ip_address=ip_address,
        country_code=country_code,
        event_type=event_type,
        event_source=event_source,
    )
    summary = _summary_from_query(db, base, active_ban_ip=ip_address)

    country_query = apply_event_filters(
        db.query(
            IPAddressSecurityStatistic.country_code.label("country_code"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            IPAddressSecurityStatistic.event_type
                            == IP_SECURITY_EVENT_REQUEST_DENIED,
                            IPAddressSecurityStatistic.request_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("denied_requests"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            IPAddressSecurityStatistic.event_type
                            == IP_SECURITY_EVENT_RATE_LIMITED,
                            IPAddressSecurityStatistic.request_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("rate_limited_requests"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (IPAddressSecurityStatistic.event_type == IP_SECURITY_EVENT_BAN_CREATED)
                            & IPAddressSecurityStatistic.is_automatic.is_(False),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("manual_bans_created"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (IPAddressSecurityStatistic.event_type == IP_SECURITY_EVENT_BAN_CREATED)
                            & IPAddressSecurityStatistic.is_automatic.is_(True),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("automatic_bans_created"),
            func.count(func.distinct(IPAddressSecurityStatistic.ip_address)).label("distinct_ips"),
            func.count(
                func.distinct(
                    case(
                        (
                            IPAddressSecurityStatistic.event_type
                            == IP_SECURITY_EVENT_REQUEST_DENIED,
                            IPAddressSecurityStatistic.ip_address,
                        ),
                        else_=None,
                    )
                )
            ).label("denied_distinct_ips"),
            func.count(IPAddressSecurityStatistic.id).label("stored_rows"),
            func.max(IPAddressSecurityStatistic.last_seen_at).label("last_seen_at"),
        ),
        period_start=effective_start,
        period_end=now,
        ip_address=ip_address,
        country_code=country_code,
        event_type=event_type,
        event_source=event_source,
    ).group_by(IPAddressSecurityStatistic.country_code)
    rows = country_query.all()
    countries = []
    total_denied = int(summary["denied_requests"])
    for row in rows:
        denied = int(row.denied_requests or 0)
        distinct_ips = int(row.distinct_ips or 0)
        denied_distinct_ips = int(row.denied_distinct_ips or 0)
        countries.append(
            {
                "country_code": row.country_code,
                "denied_requests": denied,
                "rate_limited_requests": int(row.rate_limited_requests or 0),
                "manual_bans_created": int(row.manual_bans_created or 0),
                "automatic_bans_created": int(row.automatic_bans_created or 0),
                "distinct_ips": distinct_ips,
                "denied_distinct_ips": denied_distinct_ips,
                "stored_rows": int(row.stored_rows or 0),
                "share_of_denied_requests": round((denied / total_denied) * 100, 2)
                if total_denied
                else 0.0,
                "denied_requests_per_ip": round(denied / denied_distinct_ips, 2)
                if denied_distinct_ips
                else 0.0,
                "last_seen_at": row.last_seen_at,
                "activity_level": activity_level(denied),
            }
        )
    countries.sort(
        key=lambda item: (
            item["denied_requests"],
            item["rate_limited_requests"],
            item["distinct_ips"],
        ),
        reverse=True,
    )
    top = next((item for item in countries if item["country_code"] and item["denied_requests"]), None)
    if top:
        summary["top_country_code"] = top["country_code"]
        summary["top_country_denied_requests"] = top["denied_requests"]
        summary["top_country_distinct_ips"] = top["denied_distinct_ips"]

    return {
        "enabled": bool(settings.get("enabled", False)),
        "regulatory_confirmed": bool(settings.get("regulatory_confirmed", False)),
        "period_days": days,
        "period_start_utc": effective_start,
        "period_end_utc": now,
        "retention_days": retention_days,
        "retention_cutoff_utc": retention_cutoff,
        "period_truncated_by_retention": requested_start < retention_cutoff,
        "countries_truncated": len(countries) > COUNTRY_RESULT_LIMIT,
        "country_total": len(countries),
        "definitions": DEFINITIONS,
        "provider": provider_status(db, analytics_enabled=enabled),
        "summary": summary,
        "countries": countries[:COUNTRY_RESULT_LIMIT],
    }


def list_events(
    db: Session,
    *,
    page: int,
    per_page: int,
    period_start: datetime,
    period_end: datetime,
    ip_address: str | None = None,
    country_code: str | None = None,
    event_type: str | None = None,
    event_source: str | None = None,
) -> dict[str, Any]:
    """Return a stable, newest-first page of filtered events."""

    query = apply_event_filters(
        db.query(IPAddressSecurityStatistic),
        period_start=period_start,
        period_end=period_end,
        ip_address=ip_address,
        country_code=country_code,
        event_type=event_type,
        event_source=event_source,
    )
    total = int(query.count())
    rows = (
        query.order_by(
            IPAddressSecurityStatistic.last_seen_at.desc(),
            IPAddressSecurityStatistic.id.desc(),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "items": [event_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, math.ceil(total / per_page)),
    }


def filter_options(db: Session) -> dict[str, Any]:
    """Return only values that are currently available for UI filters."""

    sources = [
        row[0]
        for row in db.query(IPAddressSecurityStatistic.event_source)
        .filter(IPAddressSecurityStatistic.event_source.isnot(None))
        .distinct()
        .order_by(IPAddressSecurityStatistic.event_source.asc())
        .all()
        if row[0]
    ]
    countries = [
        row[0]
        for row in db.query(IPAddressSecurityStatistic.country_code)
        .filter(IPAddressSecurityStatistic.country_code.isnot(None))
        .distinct()
        .order_by(IPAddressSecurityStatistic.country_code.asc())
        .all()
        if row[0]
    ]
    return {
        "event_types": sorted(IP_SECURITY_EVENT_TYPES),
        "event_sources": sources,
        "country_codes": countries,
    }


async def enrich_pending_country_codes(db: Session, *, limit: int = GEO_ENRICHMENT_BATCH_SIZE) -> int:
    """Resolve a bounded batch of pending IPs outside request handling."""

    settings = get_ip_address_statistics_settings(db)
    if not (settings.get("enabled") and settings.get("regulatory_confirmed")):
        return 0
    provider = str(
        get_value_by_page_and_key("security", "check_ip_location_provider", db) or ""
    ).strip().lower()
    if not provider_status(db, analytics_enabled=True)["configured"]:
        return 0

    pending_ips = [
        row[0]
        for row in db.query(IPAddressSecurityStatistic.ip_address)
        .filter(IPAddressSecurityStatistic.country_code.is_(None))
        .filter(
            or_(
                IPAddressSecurityStatistic.geo_lookup_status == "pending",
                and_(
                    IPAddressSecurityStatistic.geo_lookup_status == "failed",
                    IPAddressSecurityStatistic.country_resolved_at
                    <= datetime.now(timezone.utc) - timedelta(hours=24),
                ),
            )
        )
        .group_by(IPAddressSecurityStatistic.ip_address)
        .order_by(func.min(IPAddressSecurityStatistic.created_at).asc())
        .limit(limit)
        .all()
    ]
    semaphore = asyncio.Semaphore(5)

    async def resolve_country(ip_address: str) -> tuple[str, str]:
        """Bound concurrent provider traffic to protect the external service."""

        async with semaphore:
            return ip_address, await get_country_by_ip(ip_address, db)

    lookup_results = await asyncio.gather(
        *(resolve_country(ip_address) for ip_address in pending_ips)
    )
    updated = 0
    for ip_address, country in lookup_results:
        resolved_at = datetime.now(timezone.utc)
        if country and country != "Unknown":
            changed = (
                db.query(IPAddressSecurityStatistic)
                .filter(
                    IPAddressSecurityStatistic.ip_address == ip_address,
                    IPAddressSecurityStatistic.country_code.is_(None),
                )
                .update(
                    {
                        "country_code": str(country).upper(),
                        "country_resolved_at": resolved_at,
                        "geo_provider": provider,
                        "geo_lookup_status": "resolved",
                    },
                    synchronize_session=False,
                )
            )
            updated += int(changed or 0)
        else:
            db.query(IPAddressSecurityStatistic).filter(
                IPAddressSecurityStatistic.ip_address == ip_address,
                IPAddressSecurityStatistic.country_code.is_(None),
            ).update(
                {
                    "geo_provider": provider,
                    "geo_lookup_status": "failed",
                    "country_resolved_at": resolved_at,
                },
                synchronize_session=False,
            )
        db.commit()
    return updated


async def enrich_pending_with_session_factory(session_factory) -> None:
    """Background-task adapter that owns and closes its database session."""

    db = session_factory()
    try:
        await enrich_pending_country_codes(db)
    finally:
        db.close()


def delete_statistics(
    db: Session,
    *,
    days: int | None = None,
    ip_address: str | None = None,
) -> int:
    """Delete all, period-scoped, or single-IP analytics rows."""

    query = db.query(IPAddressSecurityStatistic)
    if days is not None:
        query = query.filter(
            IPAddressSecurityStatistic.created_at
            >= datetime.now(timezone.utc) - timedelta(days=days)
        )
    if ip_address:
        normalized = normalize_ip_address_for_storage(ip_address)
        query = query.filter(IPAddressSecurityStatistic.ip_address == normalized)
    deleted = int(query.delete(synchronize_session=False) or 0)
    db.commit()
    return deleted


def export_payload(db: Session) -> dict[str, Any]:
    """Create a versioned backup payload containing settings and analytics rows."""

    rows: Iterable[IPAddressSecurityStatistic] = (
        db.query(IPAddressSecurityStatistic)
        .order_by(IPAddressSecurityStatistic.created_at.asc())
        .all()
    )
    events = []
    for row in rows:
        item = event_to_dict(row)
        item["aggregation_key"] = row.aggregation_key
        for key in ("created_at", "last_seen_at", "bucket_start", "country_resolved_at"):
            if item[key] is not None:
                item[key] = item[key].isoformat()
        events.append(item)
    settings = get_ip_address_statistics_settings(db)
    return {
        "format": "omlorix-ip-analytics",
        "export_version": IP_ANALYTICS_EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            key: settings.get(key)
            for key in (
                "enabled",
                "regulatory_confirmed",
                "regulatory_justification",
                "policy_reference",
                "retention_policy",
                "retention_days",
            )
        },
        "events": events,
    }


def iter_export_json(db: Session):
    """Stream a versioned JSON backup without materializing every event."""

    settings = get_ip_address_statistics_settings(db)
    header = {
        "format": "omlorix-ip-analytics",
        "export_version": IP_ANALYTICS_EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            key: settings.get(key)
            for key in (
                "enabled",
                "regulatory_confirmed",
                "regulatory_justification",
                "policy_reference",
                "retention_policy",
                "retention_days",
            )
        },
    }
    serialized_header = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    yield serialized_header[:-1] + ',"events":['
    first = True
    rows = (
        db.query(IPAddressSecurityStatistic)
        .order_by(IPAddressSecurityStatistic.created_at.asc())
        .yield_per(500)
    )
    for row in rows:
        item = event_to_dict(row)
        item["aggregation_key"] = row.aggregation_key
        for key in ("created_at", "last_seen_at", "bucket_start", "country_resolved_at"):
            if item[key] is not None:
                item[key] = item[key].isoformat()
        if not first:
            yield ","
        yield json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        first = False
    yield "]}"


def _parse_import_datetime(value: Any, *, required: bool = False) -> datetime | None:
    """Parse an import timestamp and normalize it to UTC."""

    if value in (None, ""):
        if required:
            raise ValueError("A required event timestamp is missing.")
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def import_payload(db: Session, payload: Any) -> dict[str, int | str]:
    """Validate and import a versioned analytics backup without duplicating rows."""

    if not isinstance(payload, dict):
        raise ValueError("The import file must contain a JSON object.")
    if (
        payload.get("format") != "omlorix-ip-analytics"
        or not matches_export_version(
            payload.get("export_version"), IP_ANALYTICS_EXPORT_VERSION
        )
    ):
        raise ValueError("Unsupported IP analytics import format or version.")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("The import file does not contain an events array.")

    imported = 0
    skipped = 0
    accepted_ids: set[str] = set()
    accepted_aggregation_keys: set[str] = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        normalized_ip = normalize_ip_address_for_storage(raw.get("ip_address"))
        event_type = normalize_ip_security_event_type(raw.get("event_type"))
        if not normalized_ip or not event_type:
            skipped += 1
            continue
        row_id = str(raw.get("id") or uuid.uuid4())
        if row_id in accepted_ids or db.query(IPAddressSecurityStatistic.id).filter(
            IPAddressSecurityStatistic.id == row_id
        ).first():
            skipped += 1
            continue
        aggregation_key = str(raw.get("aggregation_key") or "").strip() or None
        if aggregation_key and (
            aggregation_key in accepted_aggregation_keys
            or db.query(IPAddressSecurityStatistic.id).filter(
                IPAddressSecurityStatistic.aggregation_key == aggregation_key
            ).first()
        ):
            skipped += 1
            continue
        try:
            created_at = _parse_import_datetime(raw.get("created_at"), required=True)
            last_seen_at = _parse_import_datetime(raw.get("last_seen_at")) or created_at
            row = IPAddressSecurityStatistic(
                id=row_id,
                ip_address=normalized_ip,
                event_type=event_type,
                event_source=str(raw.get("event_source") or "").strip()[:64] or None,
                reason_code=str(raw.get("reason_code") or "").strip()[:64] or None,
                route_category=str(raw.get("route_category") or "").strip()[:64] or None,
                country_code=str(raw.get("country_code") or "").strip().upper()[:8] or None,
                country_resolved_at=_parse_import_datetime(raw.get("country_resolved_at")),
                geo_provider=str(raw.get("geo_provider") or "").strip()[:32] or None,
                geo_lookup_status=str(raw.get("geo_lookup_status") or "pending").strip()[:32],
                reason=str(raw.get("reason") or "").strip() or None,
                request_count=max(1, int(raw.get("request_count") or 1)),
                is_automatic=bool(raw.get("is_automatic")),
                bucket_start=_parse_import_datetime(raw.get("bucket_start")),
                aggregation_key=aggregation_key,
                created_at=created_at,
                last_seen_at=last_seen_at,
            )
        except (TypeError, ValueError):
            skipped += 1
            continue
        db.add(row)
        accepted_ids.add(row_id)
        if aggregation_key:
            accepted_aggregation_keys.add(aggregation_key)
        imported += 1

    # Settings are restored only when their legal-basis fields form a valid
    # combination; secrets are intentionally not part of this backup format.
    raw_settings = payload.get("settings")
    if isinstance(raw_settings, dict):
        from app.settings.models import get_settings_page
        from sqlalchemy.orm.attributes import flag_modified

        settings_page = get_settings_page(db, "ip_address_statistics")
        if settings_page:
            restored = dict(settings_page.data or {})
            for key in (
                "regulatory_justification",
                "policy_reference",
                "retention_policy",
            ):
                if key in raw_settings:
                    restored[key] = str(raw_settings.get(key) or "")
            try:
                restored["retention_days"] = max(
                    1, min(int(raw_settings.get("retention_days") or 90), 3650)
                )
            except (TypeError, ValueError):
                restored["retention_days"] = 90
            confirmed = bool(raw_settings.get("regulatory_confirmed"))
            documented = bool(
                str(restored.get("regulatory_justification") or "").strip()
                or str(restored.get("policy_reference") or "").strip()
            )
            restored["regulatory_confirmed"] = confirmed and documented
            restored["enabled"] = bool(raw_settings.get("enabled")) and confirmed and documented
            settings_page.data = restored
            settings_page.updated_at = datetime.now(timezone.utc)
            flag_modified(settings_page, "data")

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("The IP analytics import contains conflicting rows.") from exc
    return {
        "status": "success",
        "affected_rows": imported,
        "imported_rows": imported,
        "skipped_rows": skipped,
    }
