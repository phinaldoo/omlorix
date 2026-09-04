"""Separate memory processing state from versioned profile projections."""

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA

revision = "memory_state_20260904"
down_revision = "memory_isolation_20260904"
branch_labels = None
depends_on = None


def _schema():
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def _status_columns():
    return [
        sa.Column("last_processed_message_id", sa.String()),
        sa.Column("last_source_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_status", sa.String(24)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    schema = _schema()
    state = op.create_table(
        "memory_states",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("facts_revision", sa.Integer(), nullable=False, server_default="0"),
        *_status_columns(),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"
        ),
        schema=schema,
    )
    profile = sa.table(
        "memory_profiles", sa.column("user_id"), *_status_columns(), schema=schema
    )
    names = ["user_id", *(column.name for column in _status_columns())]
    op.execute(
        state.insert().from_select(
            names, sa.select(*(profile.c[name] for name in names))
        )
    )
    # NULL explicitly means unmaterialized. Never trust a legacy empty profile
    # that may have been created solely to record a failed processing attempt.
    with op.batch_alter_table("memory_profiles", schema=schema) as batch:
        batch.add_column(sa.Column("source_revision", sa.Integer(), nullable=True))
        for column in _status_columns():
            batch.drop_column(column.name)


def downgrade():
    schema = _schema()
    with op.batch_alter_table("memory_profiles", schema=schema) as batch:
        for column in _status_columns():
            batch.add_column(column)
        batch.drop_column("source_revision")
    state = sa.table(
        "memory_states", sa.column("user_id"), *_status_columns(), schema=schema
    )
    profile = sa.table(
        "memory_profiles", sa.column("user_id"), *_status_columns(), schema=schema
    )
    op.execute(
        profile.update().values(
            {
                column.name: sa.select(state.c[column.name])
                .where(state.c.user_id == profile.c.user_id)
                .scalar_subquery()
                for column in _status_columns()
            }
        )
    )
    op.drop_table("memory_states", schema=schema)
