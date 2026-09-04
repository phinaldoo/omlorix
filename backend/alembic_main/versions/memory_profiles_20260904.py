"""Add bounded atomic memories and materialized user profiles.

Revision ID: memory_profiles_20260904
Revises: import_staging_quota_20260830
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA


revision: str = "memory_profiles_20260904"
down_revision: Union[str, Sequence[str], None] = "import_staging_quota_20260830"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def _qualified(table_name: str) -> str:
    preparer = op.get_bind().dialect.identifier_preparer
    return (
        f"{preparer.quote_schema(_app_schema())}."
        f"{preparer.quote_identifier(table_name)}"
    )


def upgrade() -> None:
    schema = _app_schema()
    memories = _qualified("memories")

    # Add nullable lifecycle fields first so existing installations can be
    # backfilled in SQL before the invariants become mandatory.
    with op.batch_alter_table("memories", schema=schema) as batch:
        batch.add_column(sa.Column("memory_key", sa.String(length=120), nullable=True))
        batch.add_column(
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="other")
        )
        batch.add_column(
            sa.Column("stability", sa.String(length=16), nullable=False, server_default="slow")
        )
        batch.add_column(
            sa.Column("importance", sa.Integer(), nullable=False, server_default="3")
        )
        batch.add_column(
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="normal")
        )
        batch.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("source_message_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("source_excerpt", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("evidence_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("review_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            sa.text(
                f"""
                UPDATE {memories}
                SET memory_key = 'legacy.' || SUBSTRING(id FROM 1 FOR 113),
                    source_excerpt = SUBSTRING(content FROM 1 FOR 500),
                    evidence_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                    last_confirmed_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                    review_at = CURRENT_TIMESTAMP + INTERVAL '180 days',
                    expires_at = CURRENT_TIMESTAMP + INTERVAL '540 days'
                WHERE memory_key IS NULL
                """
            )
        )
    else:
        op.execute(
            sa.text(
                f"""
                UPDATE {memories}
                SET memory_key = 'legacy.' || SUBSTR(id, 1, 113),
                    source_excerpt = SUBSTR(content, 1, 500),
                    evidence_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                    last_confirmed_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                    review_at = DATETIME(CURRENT_TIMESTAMP, '+180 days'),
                    expires_at = DATETIME(CURRENT_TIMESTAMP, '+540 days')
                WHERE memory_key IS NULL
                """
            )
        )

    # Preserve all existing facts. The cap applies to new writes; projection
    # selection is bounded independently and never deletes user data.

    with op.batch_alter_table("memories", schema=schema) as batch:
        batch.alter_column("memory_key", existing_type=sa.String(length=120), nullable=False)
        batch.alter_column(
            "last_confirmed_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch.alter_column("review_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_index(
        "uq_memories_user_memory_key",
        "memories",
        ["user_id", "memory_key"],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "uq_memories_project_memory_key",
        "memories",
        ["project_id", "memory_key"],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "ix_memories_expiry",
        "memories",
        ["status", "expires_at", "user_id"],
        schema=schema,
    )
    op.create_index(
        "ix_memories_source_message",
        "memories",
        ["source_message_id"],
        schema=schema,
    )

    op.create_table(
        "memory_profiles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fact_versions", sa.JSON(), nullable=False),
        sa.Column("active_fact_count", sa.Integer(), nullable=False),
        sa.Column("review_fact_count", sa.Integer(), nullable=False),
        sa.Column("last_processed_message_id", sa.String(), nullable=True),
        sa.Column("last_source_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=24), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{schema}.users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema=schema,
    )
    op.create_index(
        "ix_memory_profiles_updated",
        "memory_profiles",
        ["updated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _app_schema()
    op.drop_index(
        "ix_memory_profiles_updated",
        table_name="memory_profiles",
        schema=schema,
    )
    op.drop_table("memory_profiles", schema=schema)
    op.drop_index("ix_memories_source_message", table_name="memories", schema=schema)
    op.drop_index("ix_memories_expiry", table_name="memories", schema=schema)
    op.drop_index(
        "uq_memories_project_memory_key", table_name="memories", schema=schema
    )
    op.drop_index("uq_memories_user_memory_key", table_name="memories", schema=schema)
    with op.batch_alter_table("memories", schema=schema) as batch:
        batch.drop_column("expires_at")
        batch.drop_column("review_at")
        batch.drop_column("last_confirmed_at")
        batch.drop_column("evidence_at")
        batch.drop_column("source_excerpt")
        batch.drop_column("source_message_id")
        batch.drop_column("version")
        batch.drop_column("status")
        batch.drop_column("sensitivity")
        batch.drop_column("confidence")
        batch.drop_column("importance")
        batch.drop_column("stability")
        batch.drop_column("kind")
        batch.drop_column("memory_key")
