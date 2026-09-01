"""Reject audit inserts from writers that predate subject erasure fencing.

Revision ID: audit_subject_fence_20260830
Revises: baseline_audit_20260815
Create Date: 2026-08-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import AUDIT_DATABASE_SCHEMA


revision: str = "audit_subject_fence_20260830"
down_revision: Union[str, Sequence[str], None] = "baseline_audit_20260815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_schema() -> str:
    return str(op.get_context().version_table_schema or AUDIT_DATABASE_SCHEMA)


def upgrade() -> None:
    schema = _audit_schema()
    # PostgreSQL can install this constant default without rewriting every
    # historical partition. Flip the default in the same DDL transaction so
    # old writers omit the column and fail the check after migration commits.
    op.add_column(
        "logs",
        sa.Column(
            "subject_fenced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema=schema,
    )
    op.alter_column(
        "logs",
        "subject_fenced",
        server_default=sa.text("false"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
        schema=schema,
    )
    op.create_check_constraint(
        "ck_logs_subject_fenced",
        "logs",
        "subject_fenced",
        schema=schema,
    )


def downgrade() -> None:
    schema = _audit_schema()
    op.drop_constraint(
        "ck_logs_subject_fenced",
        "logs",
        type_="check",
        schema=schema,
    )
    op.drop_column("logs", "subject_fenced", schema=schema)
