"""Add durable system-email delivery and account-security state.

Revision ID: email_security_20260829
Revises: baseline_main_20260815
Create Date: 2026-08-29
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA
from app.utils.sqlalchemy_encryption import EncryptedJSON, EncryptedString


revision: str = "email_security_20260829"
down_revision: Union[str, Sequence[str], None] = "baseline_main_20260815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str:
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def upgrade() -> None:
    schema = _app_schema()
    op.create_table(
        "email_delivery_outbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("recipient", EncryptedString(), nullable=True),
        sa.Column("template_type", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("payload", EncryptedJSON(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=96), nullable=True),
        sa.Column("last_error_type", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_email_outbox_idempotency_key"
        ),
        sa.UniqueConstraint("message_id", name="uq_email_outbox_message_id"),
        schema=schema,
    )
    op.create_index(
        "ix_email_outbox_claim",
        "email_delivery_outbox",
        ["status", "priority", "available_at", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_email_outbox_lease_expiry",
        "email_delivery_outbox",
        ["status", "lease_expires_at"],
        schema=schema,
    )
    op.create_index(
        "ix_email_outbox_user_id",
        "email_delivery_outbox",
        ["user_id"],
        schema=schema,
    )
    op.create_index(
        "ix_email_outbox_expires_at",
        "email_delivery_outbox",
        ["expires_at"],
        schema=schema,
    )
    op.create_index(
        "ix_email_outbox_updated_at",
        "email_delivery_outbox",
        ["updated_at"],
        schema=schema,
    )

    op.create_table(
        "pending_email_changes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("new_email", EncryptedString(), nullable=False),
        sa.Column("old_email", EncryptedString(), nullable=False),
        sa.Column("verify_token_hash", sa.String(length=64), nullable=False),
        sa.Column("cancel_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "verify_token_hash", name="uq_email_change_verify_token_hash"
        ),
        sa.UniqueConstraint(
            "cancel_token_hash", name="uq_email_change_cancel_token_hash"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_email_change_user_status",
        "pending_email_changes",
        ["user_id", "status"],
        schema=schema,
    )
    op.create_index(
        "ix_email_change_user_created_at",
        "pending_email_changes",
        ["user_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_email_change_expires_at",
        "pending_email_changes",
        ["expires_at"],
        schema=schema,
    )

    op.create_table(
        "trusted_device_notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("device_token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_summary", EncryptedString(), nullable=True),
        sa.Column("network_summary", EncryptedString(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "device_token_hash",
            name="uq_trusted_device_user_token",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_trusted_device_user_id",
        "trusted_device_notifications",
        ["user_id"],
        schema=schema,
    )
    op.create_index(
        "ix_trusted_device_last_seen_at",
        "trusted_device_notifications",
        ["last_seen_at"],
        schema=schema,
    )

    op.create_table(
        "email_security_rate_limits",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key"),
        schema=schema,
    )
    op.create_index(
        "ix_email_security_rate_limit_expires_at",
        "email_security_rate_limits",
        ["expires_at"],
        schema=schema,
    )

    op.create_table(
        "pending_auth_actions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "purpose",
            name="uq_pending_auth_actions_user_purpose",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_pending_auth_actions_lookup",
        "pending_auth_actions",
        ["purpose", "token_hash"],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "ix_pending_auth_actions_expires_at",
        "pending_auth_actions",
        ["expires_at"],
        schema=schema,
    )

    op.create_table(
        "email_security_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_epoch", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    state_table = sa.table(
        "email_security_state",
        sa.column("id", sa.Integer()),
        sa.column("action_epoch", sa.String(length=64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.bulk_insert(
        state_table,
        [
            {
                "id": 1,
                "action_epoch": uuid.uuid4().hex + uuid.uuid4().hex,
                "updated_at": datetime.now(timezone.utc),
            }
        ],
    )


def downgrade() -> None:
    schema = _app_schema()
    op.drop_table("email_security_state", schema=schema)
    op.drop_index(
        "ix_pending_auth_actions_expires_at",
        table_name="pending_auth_actions",
        schema=schema,
    )
    op.drop_index(
        "ix_pending_auth_actions_lookup",
        table_name="pending_auth_actions",
        schema=schema,
    )
    op.drop_table("pending_auth_actions", schema=schema)
    op.drop_index(
        "ix_email_security_rate_limit_expires_at",
        table_name="email_security_rate_limits",
        schema=schema,
    )
    op.drop_table("email_security_rate_limits", schema=schema)
    op.drop_index(
        "ix_trusted_device_last_seen_at",
        table_name="trusted_device_notifications",
        schema=schema,
    )
    op.drop_index(
        "ix_trusted_device_user_id",
        table_name="trusted_device_notifications",
        schema=schema,
    )
    op.drop_table("trusted_device_notifications", schema=schema)
    op.drop_index(
        "ix_email_change_expires_at",
        table_name="pending_email_changes",
        schema=schema,
    )
    op.drop_index(
        "ix_email_change_user_created_at",
        table_name="pending_email_changes",
        schema=schema,
    )
    op.drop_index(
        "ix_email_change_user_status",
        table_name="pending_email_changes",
        schema=schema,
    )
    op.drop_table("pending_email_changes", schema=schema)
    for index_name in (
        "ix_email_outbox_updated_at",
        "ix_email_outbox_expires_at",
        "ix_email_outbox_user_id",
        "ix_email_outbox_lease_expiry",
        "ix_email_outbox_claim",
    ):
        op.drop_index(
            index_name,
            table_name="email_delivery_outbox",
            schema=schema,
        )
    op.drop_table("email_delivery_outbox", schema=schema)
