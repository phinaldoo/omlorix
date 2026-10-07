"""Keep bounded interactive snapshots separate from generated message content."""
from alembic import op
import sqlalchemy as sa
from app.database import DATABASE_SCHEMA

revision = "visualization_state_20261007"
down_revision = "playback_documents_20260905"
branch_labels = None
depends_on = None


def upgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    op.add_column("chat_messages", sa.Column("visualization_states", sa.JSON(), nullable=True), schema=schema)


def downgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    op.drop_column("chat_messages", "visualization_states", schema=schema)
