"""Add the durable audit-event outbox for extended worker services.

Revision ID: extended_workers_20260829
Revises: worker_architecture_20260829
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA
from app.utils.sqlalchemy_encryption import EncryptedJSON, EncryptedString


revision: str = "extended_workers_20260829"
down_revision: Union[str, Sequence[str], None] = "worker_architecture_20260829"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def upgrade() -> None:
    schema = _app_schema()
    op.create_table(
        "audit_event_outbox",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("reason", EncryptedString(), nullable=True),
        sa.Column("details", EncryptedJSON(), nullable=True),
        sa.Column("ip_address", EncryptedString(), nullable=True),
        sa.Column("user_agent", EncryptedString(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index(
        "ix_audit_event_outbox_delivery",
        "audit_event_outbox",
        ["status", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_audit_event_outbox_user",
        "audit_event_outbox",
        ["user_id", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _app_schema()
    op.drop_index(
        "ix_audit_event_outbox_user",
        table_name="audit_event_outbox",
        schema=schema,
    )
    op.drop_index(
        "ix_audit_event_outbox_delivery",
        table_name="audit_event_outbox",
        schema=schema,
    )
    op.drop_table("audit_event_outbox", schema=schema)
