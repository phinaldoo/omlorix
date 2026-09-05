"""Track temporary presentation documents independently of expiring tokens."""

from alembic import op
import sqlalchemy as sa

from app.database import DATABASE_SCHEMA

revision = "playback_documents_20260905"
down_revision = "workspace_reads_20260904"
branch_labels = None
depends_on = None


def upgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    op.create_table(
        "presentation_playback_documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("storage_provider", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_index(
        "ix_presentation_playback_documents_expires_at",
        "presentation_playback_documents", ["expires_at"], schema=schema,
    )


def downgrade():
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    op.drop_table("presentation_playback_documents", schema=schema)
