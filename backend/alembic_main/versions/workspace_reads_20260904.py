"""Indexes supporting bounded workspace catalog reads and live access checks."""

from alembic import op
from app.database import DATABASE_SCHEMA

revision = "workspace_reads_20260904"
down_revision = "memory_state_20260904"
branch_labels = None
depends_on = None

INDEXES = [
    ("notes", "ix_notes_catalog_page", "user_id", "updated_at", "id"),
    (
        "shared_note_subscriptions",
        "ix_note_subscriber_access",
        "subscriber_id",
        "note_id",
        "share_type",
    ),
    ("skills", "ix_skills_catalog_page", "user_id", "created_at", "id"),
    (
        "shared_skill_subscriptions",
        "ix_skill_subscriber_access",
        "subscriber_id",
        "skill_id",
        "share_type",
    ),
    ("todo_lists", "ix_todo_lists_catalog_page", "user_id", "order", "id"),
    (
        "shared_todo_list_subscriptions",
        "ix_todo_subscriber_access",
        "subscriber_id",
        "todo_list_id",
        "share_type",
    ),
    (
        "todos",
        "ix_todos_catalog_page",
        "todo_list",
        "is_done",
        "order",
        "created_at",
        "id",
    ),
    ("automations", "ix_automations_catalog_page", "user_id", "created_at", "id"),
]


def upgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    # Migration startup is fenced from application writes. Keeping this in the
    # migration transaction avoids a partially applied index set after failure.
    for table, name, *columns in INDEXES:
        op.create_index(name, table, columns, schema=schema)


def downgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    for table, name, *_ in reversed(INDEXES):
        op.drop_index(name, table_name=table, schema=schema)
