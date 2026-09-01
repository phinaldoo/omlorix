"""Add aggregate import staging quota reservations.

Revision ID: import_staging_quota_20260830
Revises: audit_event_erasure_20260830
Create Date: 2026-08-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA


revision: str = "import_staging_quota_20260830"
down_revision: Union[str, Sequence[str], None] = "audit_event_erasure_20260830"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def upgrade() -> None:
    schema = _app_schema()
    op.create_table(
        "import_staging_reservations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("staged_name", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("import_kind", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("worker_job_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="ck_import_staging_reservation_size_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["worker_job_id"],
            [f"{schema}.durable_worker_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("staged_name"),
        sa.UniqueConstraint("worker_job_id"),
        schema=schema,
    )
    op.create_index(
        "ix_import_staging_reservation_principal",
        "import_staging_reservations",
        ["principal_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_import_staging_reservation_expiry",
        "import_staging_reservations",
        ["expires_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _app_schema()
    op.drop_index(
        "ix_import_staging_reservation_expiry",
        table_name="import_staging_reservations",
        schema=schema,
    )
    op.drop_index(
        "ix_import_staging_reservation_principal",
        table_name="import_staging_reservations",
        schema=schema,
    )
    op.drop_table("import_staging_reservations", schema=schema)
