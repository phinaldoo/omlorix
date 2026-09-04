"""Add content-free deletion guards and isolate memory jobs from chat generation.

Revision ID: memory_isolation_20260904
Revises: memory_profiles_20260904
"""

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA

revision = "memory_isolation_20260904"
down_revision = "memory_profiles_20260904"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def _move_jobs(source: str, destination: str) -> None:
    jobs = sa.table(
        "durable_worker_jobs",
        sa.column("queue", sa.String()),
        sa.column("kind", sa.String()),
        schema=_schema(),
    )
    # Startup migration runs before worker services. Preserve payloads, retry
    # state and idempotency keys, including terminal rows that suppress replay.
    op.execute(
        jobs.update()
        .where(jobs.c.queue == source, jobs.c.kind == "memory_consolidation")
        .values(queue=destination)
    )


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "memory_deletions",
        sa.Column("memory_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("memory_key", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{schema}.projects.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND project_id IS NULL) OR "
            "(user_id IS NULL AND project_id IS NOT NULL)",
            name="ck_memory_deletions_exactly_one_scope",
        ),
        schema=schema,
    )
    for name, columns in (
        ("ix_memory_deletions_user_key", ["user_id", "memory_key", "deleted_at"]),
        ("ix_memory_deletions_project_key", ["project_id", "memory_key", "deleted_at"]),
        ("ix_memory_deletions_deleted", ["deleted_at", "memory_id"]),
    ):
        op.create_index(name, "memory_deletions", columns, schema=schema)
    _move_jobs("generation", "memory")


def downgrade() -> None:
    _move_jobs("memory", "generation")
    op.drop_table("memory_deletions", schema=_schema())
