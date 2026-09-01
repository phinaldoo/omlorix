"""Database operations and aggregations for administrator IP analytics."""

from datetime import datetime
from typing import Any

from app.auth.models import BlockedIP, IPAddressSecurityStatistic
from app.settings.models import get_settings_page
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


def get_ip_statistics_settings_page(db: Session):
    """Return the persisted IP-statistics settings row."""

    return get_settings_page(db, "ip_address_statistics")


def persist_ip_statistics_settings_page(db: Session, settings_page) -> None:
    """Persist in-place changes to the JSON-backed IP-statistics settings."""

    flag_modified(settings_page, "data")
    db.commit()
    db.refresh(settings_page)


def get_active_ip_block(db: Session, ip_address: str) -> BlockedIP | None:
    """Return the managed ban for one normalized address, if present."""

    return db.query(BlockedIP).filter(BlockedIP.ip_address == ip_address).first()


def count_ip_statistics(db: Session) -> int:
    """Count stored IP analytics rows for export audit metadata."""

    return int(db.query(IPAddressSecurityStatistic).count())


def update_blocked_ip(
    db: Session,
    *,
    entry: BlockedIP,
    ip_address: str,
    expires_at: datetime,
    reason: str,
) -> BlockedIP:
    """Persist edits to one managed IP ban."""

    entry.ip_address = ip_address
    entry.expires_at = expires_at
    entry.reason = reason
    db.commit()
    db.refresh(entry)
    return entry


def list_blocked_ips_page(
    db: Session,
    *,
    page: int,
    per_page: int,
) -> tuple[list[BlockedIP], int]:
    """Return one newest-first page of managed IP bans and its total size."""

    query = db.query(BlockedIP)
    total = query.count()
    rows = (
        query.order_by(BlockedIP.blocked_at.desc().nullslast())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return rows, total


def get_blocked_ip_statistics(
    db: Session,
    *,
    ip_addresses: list[str],
) -> tuple[dict[str, str | None], dict[str, dict[str, Any]]]:
    """Aggregate country and denied-request metrics for a page of bans."""

    country_rows = (
        db.query(
            IPAddressSecurityStatistic.ip_address,
            func.max(IPAddressSecurityStatistic.country_code).label("country_code"),
        )
        .filter(
            IPAddressSecurityStatistic.ip_address.in_(ip_addresses),
            IPAddressSecurityStatistic.country_code.isnot(None),
        )
        .group_by(IPAddressSecurityStatistic.ip_address)
        .all()
    )
    country_by_ip = {row.ip_address: row.country_code for row in country_rows}

    attempt_rows = (
        db.query(
            IPAddressSecurityStatistic.ip_address,
            func.coalesce(func.sum(IPAddressSecurityStatistic.request_count), 0).label(
                "count"
            ),
            func.max(IPAddressSecurityStatistic.last_seen_at).label("last_at"),
        )
        .filter(
            IPAddressSecurityStatistic.ip_address.in_(ip_addresses),
            IPAddressSecurityStatistic.event_type == "request_denied",
            IPAddressSecurityStatistic.reason_code == "active_ban",
        )
        .group_by(IPAddressSecurityStatistic.ip_address)
        .all()
    )
    attempt_stats_by_ip = {
        row.ip_address: {"count": row.count or 0, "last_at": row.last_at}
        for row in attempt_rows
    }
    return country_by_ip, attempt_stats_by_ip
