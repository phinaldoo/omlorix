"""Add durable worker queues and file-processing cache metadata.

Revision ID: worker_architecture_20260829
Revises: email_security_20260829
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA
from app.utils.sqlalchemy_encryption import EncryptedJSON


revision: str = "worker_architecture_20260829"
down_revision: Union[str, Sequence[str], None] = "email_security_20260829"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def upgrade() -> None:
    schema = _app_schema()
    op.create_table(
        "durable_worker_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("queue", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("payload", EncryptedJSON(), nullable=True),
        sa.Column("result", EncryptedJSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=96), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("priority >= 0", name="ck_worker_job_priority_nonnegative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_worker_job_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_worker_job_max_attempts_positive"),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_worker_job_progress_range"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("queue", "idempotency_key", name="uq_worker_job_queue_idempotency"),
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_claim",
        "durable_worker_jobs",
        ["queue", "status", "priority", "available_at", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_lease",
        "durable_worker_jobs",
        ["queue", "status", "lease_expires_at"],
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_user",
        "durable_worker_jobs",
        ["user_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_updated",
        "durable_worker_jobs",
        ["updated_at"],
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_expires",
        "durable_worker_jobs",
        ["expires_at"],
        schema=schema,
    )
    op.create_index(
        "ix_worker_job_reconcile",
        "durable_worker_jobs",
        ["queue", "status", "kind", "reconciled_at"],
        schema=schema,
    )

    op.create_table(
        "file_processing_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("processor_version", sa.Integer(), nullable=False),
        sa.Column("cache_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("data", EncryptedJSON(), nullable=True),
        sa.Column("cache_path", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["file_id"], [f"{schema}.files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id",
            "operation",
            "processor_version",
            "cache_key",
            name="uq_file_processing_artifact_cache",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_file_processing_artifact_file",
        "file_processing_artifacts",
        ["file_id", "operation"],
        schema=schema,
    )
    op.create_index(
        "ix_file_processing_artifact_status",
        "file_processing_artifacts",
        ["status", "updated_at"],
        schema=schema,
    )
    op.create_index(
        "ix_file_processing_artifact_updated",
        "file_processing_artifacts",
        ["updated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _app_schema()
    op.drop_index(
        "ix_file_processing_artifact_updated",
        table_name="file_processing_artifacts",
        schema=schema,
    )
    op.drop_index(
        "ix_file_processing_artifact_status",
        table_name="file_processing_artifacts",
        schema=schema,
    )
    op.drop_index(
        "ix_file_processing_artifact_file",
        table_name="file_processing_artifacts",
        schema=schema,
    )
    op.drop_table("file_processing_artifacts", schema=schema)
    op.drop_index("ix_worker_job_reconcile", table_name="durable_worker_jobs", schema=schema)
    op.drop_index("ix_worker_job_expires", table_name="durable_worker_jobs", schema=schema)
    op.drop_index("ix_worker_job_updated", table_name="durable_worker_jobs", schema=schema)
    op.drop_index("ix_worker_job_user", table_name="durable_worker_jobs", schema=schema)
    op.drop_index("ix_worker_job_lease", table_name="durable_worker_jobs", schema=schema)
    op.drop_index("ix_worker_job_claim", table_name="durable_worker_jobs", schema=schema)
    op.drop_table("durable_worker_jobs", schema=schema)
