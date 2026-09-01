"""Database queries for administrator notifications."""

from collections.abc import Iterator

from app.logging.models import AdminNotifications
from sqlalchemy import desc
from sqlalchemy.orm import Session


def count_admin_notifications(db: Session) -> int:
    """Return the number of administrator notification rows."""

    return int(db.query(AdminNotifications).count())


def iter_admin_notifications(
    db: Session,
    *,
    batch_size: int,
) -> Iterator[AdminNotifications]:
    """Yield newest-first notifications with streaming query options."""

    query = db.query(AdminNotifications).order_by(desc(AdminNotifications.timestamp))
    if hasattr(query, "execution_options"):
        query = query.execution_options(stream_results=True)
    if hasattr(query, "yield_per"):
        query = query.yield_per(batch_size)
    yield from query
