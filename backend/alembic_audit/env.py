from logging.config import fileConfig
import importlib

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app.model_modules import MODEL_MODULES

# Alembic config object
config = context.config

if config.config_file_name is not None:
    # Keep the migration CLI logger enabled so migration failures remain visible
    # after Alembic installs its own logging configuration.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def import_model_modules() -> list[str]:
    """Import the explicit ORM model registry for Alembic metadata."""
    imported: list[str] = []
    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)
        imported.append(module_name)
    return imported


import_model_modules()

from app.database import (  # noqa: E402
    AUDIT_DATABASE_SCHEMA,
    AUDIT_DATABASE_URL,
    AuditBase,
)

target_metadata = AuditBase.metadata
config.set_main_option("sqlalchemy.url", AUDIT_DATABASE_URL.replace("%", "%%"))
AUDIT_VERSION_TABLE = "alembic_version_audit"


def include_managed_schema_object(object_, name, type_, reflected, compare_to) -> bool:
    """Limit autogeneration to audit-owned objects and ignore bookkeeping.

    The main application schema has an independent Alembic history. Filtering
    reflected objects here prevents audit revisions from attempting to remove
    main tables or either history's version table.
    """

    del compare_to
    if type_ == "table" and name in {"alembic_version", AUDIT_VERSION_TABLE}:
        return False
    if reflected:
        schema_name = getattr(object_, "schema", None)
        if schema_name is not None and schema_name != AUDIT_DATABASE_SCHEMA:
            return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=AUDIT_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        include_object=include_managed_schema_object,
        version_table=AUDIT_VERSION_TABLE,
        version_table_schema=AUDIT_DATABASE_SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{AUDIT_DATABASE_SCHEMA}"')
            )
            # Keep ``public`` as the reflection default so schema-qualified
            # audit metadata compares cleanly during autogeneration.
            connection.execute(text("SET search_path TO public"))
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            include_object=include_managed_schema_object,
            version_table=AUDIT_VERSION_TABLE,
            version_table_schema=AUDIT_DATABASE_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
