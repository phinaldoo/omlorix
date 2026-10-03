"""Migration coverage for portable agent plugin persistence."""

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alembic_main.versions import add_agent_plugins_20260808 as migration


def test_agent_plugin_migration_round_trip(monkeypatch):
    """Both aggregate tables can be created and removed on SQLite."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        names = set(sa.inspect(connection).get_table_names())
        assert {"agent_plugins", "agent_plugin_components"} <= names

        plugin_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("agent_plugins")
        }
        assert {"manifest", "compatibility", "content_sha256", "enabled"} <= plugin_columns

        migration.downgrade()
        names = set(sa.inspect(connection).get_table_names())
        assert "agent_plugins" not in names
        assert "agent_plugin_components" not in names
