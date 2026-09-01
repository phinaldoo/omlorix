"""Add indexed subject fences for policy-aware audit-event erasure.

Revision ID: audit_event_erasure_20260830
Revises: extended_workers_20260829
Create Date: 2026-08-30
"""

from datetime import datetime, timezone
import hashlib
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA


revision: str = "audit_event_erasure_20260830"
down_revision: Union[str, Sequence[str], None] = "extended_workers_20260829"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def _insert_ignore(bind, table, values: list[dict[str, Any]], index_elements) -> None:
    if not values:
        return
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Audit subject backfill requires PostgreSQL or SQLite")
    for offset in range(0, len(values), 500):
        bind.execute(
            insert(table)
            .values(values[offset : offset + 500])
            .on_conflict_do_nothing(index_elements=index_elements)
        )


def _seed_deleted_user_states(schema: str) -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    states = sa.Table(
        "audit_event_subject_states",
        metadata,
        sa.Column("subject_fingerprint", sa.String(length=64)),
        sa.Column("erased_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    last_id = ""
    while True:
        rows = (
            bind.execute(
                sa.select(users.c.id, users.c.deleted_at)
                .where(
                    users.c.id > last_id,
                    users.c.deleted_at.is_not(None),
                )
                .order_by(users.c.id.asc())
                .limit(500)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        current = datetime.now(timezone.utc)
        _insert_ignore(
            bind,
            states,
            [
                {
                    "subject_fingerprint": hashlib.sha256(
                        str(row["id"]).encode("utf-8")
                    ).hexdigest(),
                    "erased_at": row["deleted_at"] or current,
                    "created_at": current,
                    "updated_at": current,
                }
                for row in rows
            ],
            [states.c.subject_fingerprint],
        )
        last_id = str(rows[-1]["id"])


def _cancel_legacy_audit_events(schema: str) -> None:
    """Fail closed for outbox rows created before subject indexing existed."""

    bind = op.get_bind()
    metadata = sa.MetaData()
    outbox = sa.Table(
        "audit_event_outbox",
        metadata,
        sa.Column("id", sa.String(length=32)),
        sa.Column("user_id", sa.String(length=64)),
        sa.Column("reason", sa.Text()),
        sa.Column("details", sa.JSON()),
        sa.Column("ip_address", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("subjects_indexed", sa.Boolean()),
        sa.Column("status", sa.String(length=24)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    jobs = sa.Table(
        "durable_worker_jobs",
        metadata,
        sa.Column("id", sa.String()),
        sa.Column("queue", sa.String(length=32)),
        sa.Column("kind", sa.String(length=64)),
        sa.Column("user_id", sa.String()),
        sa.Column("payload", sa.JSON()),
        sa.Column("result", sa.JSON()),
        sa.Column("status", sa.String(length=24)),
        sa.Column("idempotency_key", sa.String(length=200)),
        sa.Column("cancel_requested", sa.Boolean()),
        sa.Column("lease_owner", sa.String(length=96)),
        sa.Column("leased_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema=schema,
    )

    if bind.dialect.name == "postgresql":
        # Managed upgrades stop application processes first. This lock is a
        # second boundary for manually run migrations and waits out any writer
        # transaction that was already in flight.
        bind.execute(
            sa.text(
                f'LOCK TABLE "{schema}"."audit_event_outbox" '
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        )

    last_id = ""
    while True:
        event_ids = list(
            bind.execute(
                sa.select(outbox.c.id)
                .where(
                    outbox.c.id > last_id,
                    outbox.c.subjects_indexed.is_(False),
                )
                .order_by(outbox.c.id.asc())
                .limit(500)
            ).scalars()
        )
        if not event_ids:
            break
        current = datetime.now(timezone.utc)
        keys = [f"audit:{event_id}" for event_id in event_ids]
        active_job = jobs.c.status.in_(("pending", "processing", "retry"))
        bind.execute(
            jobs.update()
            .where(
                jobs.c.queue == "events",
                jobs.c.kind == "audit_log",
                jobs.c.idempotency_key.in_(keys),
            )
            .values(
                user_id=None,
                payload=None,
                result=None,
                status=sa.case((active_job, "cancelled"), else_=jobs.c.status),
                cancel_requested=sa.case(
                    (active_job, True), else_=jobs.c.cancel_requested
                ),
                lease_owner=None,
                leased_at=None,
                lease_expires_at=None,
                error_code=sa.case(
                    (active_job, "migration_privacy_fence"),
                    else_=jobs.c.error_code,
                ),
                finished_at=sa.case(
                    (active_job, current), else_=jobs.c.finished_at
                ),
                reconciled_at=sa.case(
                    (active_job, current), else_=jobs.c.reconciled_at
                ),
                updated_at=current,
            )
        )
        active_event = outbox.c.status.notin_(("delivered", "cancelled"))
        bind.execute(
            outbox.update()
            .where(outbox.c.id.in_(event_ids))
            .values(
                user_id="",
                reason=None,
                details=None,
                ip_address=None,
                user_agent=None,
                subjects_indexed=True,
                status=sa.case(
                    (active_event, "cancelled"), else_=outbox.c.status
                ),
                # A post-migration cross-database reconciliation uses this
                # marker to remove any audit row a legacy event had already
                # delivered before the migration boundary.
                error_code="migration_privacy_fence",
                updated_at=current,
            )
        )
        last_id = str(event_ids[-1])


def upgrade() -> None:
    schema = _app_schema()
    op.add_column(
        "audit_event_outbox",
        sa.Column(
            "subjects_indexed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=schema,
    )
    op.create_table(
        "audit_event_subject_states",
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("subject_fingerprint"),
        schema=schema,
    )
    op.create_index(
        "ix_audit_event_subject_state_erased",
        "audit_event_subject_states",
        ["erased_at"],
        schema=schema,
    )
    op.create_table(
        "audit_event_subject_references",
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            [f"{schema}.audit_event_outbox.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", "subject_fingerprint"),
        schema=schema,
    )
    op.create_index(
        "ix_audit_event_subject_reference_subject",
        "audit_event_subject_references",
        ["subject_fingerprint", "event_id"],
        schema=schema,
    )
    op.create_table(
        "audit_event_erasure_guards",
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("subject_fingerprint"),
        schema=schema,
    )
    op.create_table(
        "audit_erasure_reconciliation_checkpoints",
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        schema=schema,
    )
    _seed_deleted_user_states(schema)
    _cancel_legacy_audit_events(schema)
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_audit_event_outbox_unindexed_safe",
            "audit_event_outbox",
            "subjects_indexed OR ("
            "status = 'cancelled' AND user_id = '' AND reason IS NULL "
            "AND details IS NULL AND ip_address IS NULL AND user_agent IS NULL"
            ")",
            schema=schema,
        )


def downgrade() -> None:
    schema = _app_schema()
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_audit_event_outbox_unindexed_safe",
            "audit_event_outbox",
            type_="check",
            schema=schema,
        )
    op.drop_table("audit_erasure_reconciliation_checkpoints", schema=schema)
    op.drop_table("audit_event_erasure_guards", schema=schema)
    op.drop_index(
        "ix_audit_event_subject_reference_subject",
        table_name="audit_event_subject_references",
        schema=schema,
    )
    op.drop_table("audit_event_subject_references", schema=schema)
    op.drop_index(
        "ix_audit_event_subject_state_erased",
        table_name="audit_event_subject_states",
        schema=schema,
    )
    op.drop_table("audit_event_subject_states", schema=schema)
    op.drop_column("audit_event_outbox", "subjects_indexed", schema=schema)
