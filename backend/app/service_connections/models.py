"""Database model and focused persistence helpers for service connections."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Session

from app.database import Base
from app.utils.sqlalchemy_encryption import EncryptedString


SERVICE_CONNECTION_ENABLED_COLUMNS = frozenset(
    {
        "enabled_for_code_execution",
        "enabled_for_latex_pdf",
        "enabled_for_slide_renderer",
    }
)


def utc_now() -> datetime:
    """Return an aware UTC timestamp for connection lifecycle fields."""

    return datetime.now(timezone.utc)


class ServiceConnection(Base):
    """Operator-managed endpoint shared by server-side rendering capabilities."""

    __tablename__ = "service_connections"
    __table_args__ = (
        CheckConstraint(
            "weight >= 1 AND weight <= 100",
            name="ck_service_connections_weight",
        ),
        Index(
            "ix_service_connections_code_execution",
            "enabled_for_code_execution",
        ),
        Index(
            "ix_service_connections_latex_pdf",
            "enabled_for_latex_pdf",
        ),
        Index(
            "ix_service_connections_slide_renderer",
            "enabled_for_slide_renderer",
        ),
        Index("ix_service_connections_created_at", "created_at"),
    )

    id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    name = Column(String(120), nullable=False)
    base_url = Column(String(2048), nullable=False)
    # The type encrypts plaintext before binding and decrypts only after a row
    # is loaded. Empty credentials remain NULL instead of storing an encrypted
    # empty string, which makes the secret-presence check inexpensive.
    api_key = Column(EncryptedString, nullable=True)
    enabled_for_code_execution = Column(Boolean, nullable=False, default=False)
    enabled_for_latex_pdf = Column(Boolean, nullable=False, default=False)
    enabled_for_slide_renderer = Column(Boolean, nullable=False, default=False)
    weight = Column(Integer, nullable=False, default=1)
    # Health probes can add capability keys over time, so status deliberately
    # remains JSON while the stable routing fields above stay indexable.
    status = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


def list_service_connection_rows(
    db: Session,
    *,
    enabled_column: str | None = None,
) -> list[ServiceConnection]:
    """Return deterministic rows, optionally filtered by one indexed purpose."""

    query = db.query(ServiceConnection)
    if enabled_column is not None:
        if enabled_column not in SERVICE_CONNECTION_ENABLED_COLUMNS:
            raise ValueError("Unsupported service connection capability column.")
        query = query.filter(getattr(ServiceConnection, enabled_column).is_(True))
    return query.order_by(
        ServiceConnection.created_at.asc(), ServiceConnection.id.asc()
    ).all()


def has_enabled_service_connection_row(db: Session, *, enabled_column: str) -> bool:
    """Check for a configured purpose without loading rows or decrypting keys."""

    if enabled_column not in SERVICE_CONNECTION_ENABLED_COLUMNS:
        raise ValueError("Unsupported service connection capability column.")
    return (
        db.query(ServiceConnection.id)
        .filter(
            getattr(ServiceConnection, enabled_column).is_(True),
            ServiceConnection.base_url.isnot(None),
            ServiceConnection.base_url != "",
        )
        .first()
        is not None
    )


def list_enabled_service_connection_statuses(
    db: Session,
    *,
    enabled_column: str,
) -> list[dict]:
    """Load only status documents for capability checks that need no secrets."""

    if enabled_column not in SERVICE_CONNECTION_ENABLED_COLUMNS:
        raise ValueError("Unsupported service connection capability column.")
    rows = (
        db.query(ServiceConnection.status)
        .filter(getattr(ServiceConnection, enabled_column).is_(True))
        .all()
    )
    return [dict(row[0]) if isinstance(row[0], dict) else {} for row in rows]


def get_service_connection_row(
    db: Session,
    connection_id: str,
) -> ServiceConnection | None:
    """Fetch one service connection by its primary key."""

    return (
        db.query(ServiceConnection)
        .filter(ServiceConnection.id == str(connection_id or "").strip())
        .first()
    )


def save_service_connection_row(
    db: Session,
    connection: ServiceConnection,
) -> ServiceConnection:
    """Commit one new or changed connection and refresh generated values."""

    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def delete_service_connection_row(db: Session, connection: ServiceConnection) -> None:
    """Delete one previously resolved service connection."""

    db.delete(connection)
    db.commit()


def update_service_connection_status_row(
    db: Session,
    *,
    connection_id: str,
    status: dict,
    updated_at: datetime,
) -> ServiceConnection | None:
    """Update only health fields so runtime probes cannot overwrite configuration."""

    affected = (
        db.query(ServiceConnection)
        .filter(ServiceConnection.id == str(connection_id or "").strip())
        .update(
            {
                ServiceConnection.status: dict(status),
                ServiceConnection.updated_at: updated_at,
            },
            synchronize_session=False,
        )
    )
    if not affected:
        db.rollback()
        return None
    db.commit()
    return get_service_connection_row(db, connection_id)
