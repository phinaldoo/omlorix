"""Add portable agent plugin lifecycle tables.

Revision ID: agent_plugins_20260808
Revises: slide_storage_meta_20260806
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import DATABASE_SCHEMA


revision: str = "agent_plugins_20260808"
down_revision: Union[str, Sequence[str], None] = "slide_storage_meta_20260806"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _app_schema() -> str | None:
    """Return the application schema, or no schema for SQLite."""
    if op.get_bind().dialect.name == "sqlite":
        return None
    return str(op.get_context().version_table_schema or DATABASE_SCHEMA)


def upgrade() -> None:
    """Create plugin aggregates and portable component links."""
    schema = _app_schema()
    json_default = sa.text("'{}'::json") if op.get_bind().dialect.name == "postgresql" else sa.text("'{}'")
    op.create_table(
        "agent_plugins",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_type", sa.String(), nullable=False, server_default="user"),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default=json_default),
        sa.Column("compatibility", sa.JSON(), nullable=False, server_default=json_default),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("owner_type = 'user'", name="ck_agent_plugins_owner_type"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_agent_plugins_owner_name"),
        schema=schema,
    )
    op.create_index("ix_agent_plugins_owner_user_id", "agent_plugins", ["owner_user_id"], schema=schema)
    op.create_index("ix_agent_plugins_enabled", "agent_plugins", ["enabled"], schema=schema)
    op.create_table(
        "agent_plugin_components",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.Column("component_type", sa.String(), nullable=False),
        sa.Column("component_id", sa.String(), nullable=False),
        sa.Column("component_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("component_type IN ('skill', 'mcp_server')", name="ck_agent_plugin_components_type"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("component_type", "component_id", name="uq_agent_plugin_components_lookup"),
        schema=schema,
    )
    op.create_index("ix_agent_plugin_components_plugin_id", "agent_plugin_components", ["plugin_id"], schema=schema)
    op.create_index("ix_agent_plugin_components_lookup", "agent_plugin_components", ["component_type", "component_id"], schema=schema)


def downgrade() -> None:
    """Remove portable plugin metadata while leaving standalone components."""
    schema = _app_schema()
    op.drop_index("ix_agent_plugin_components_lookup", table_name="agent_plugin_components", schema=schema)
    op.drop_index("ix_agent_plugin_components_plugin_id", table_name="agent_plugin_components", schema=schema)
    op.drop_table("agent_plugin_components", schema=schema)
    op.drop_index("ix_agent_plugins_enabled", table_name="agent_plugins", schema=schema)
    op.drop_index("ix_agent_plugins_owner_user_id", table_name="agent_plugins", schema=schema)
    op.drop_table("agent_plugins", schema=schema)
