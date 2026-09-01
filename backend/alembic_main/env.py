from logging.config import fileConfig
import importlib
from sqlalchemy import engine_from_config
from sqlalchemy import text
from sqlalchemy import pool

from alembic import context
from app.model_modules import MODEL_MODULES

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    # Keep the migration CLI logger enabled so migration failures remain visible
    # after Alembic installs its own logging configuration.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata


def import_model_modules():
    """Import the explicit ORM model registry for Alembic metadata."""
    imported: list[str] = []
    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)
        imported.append(module_name)
    return imported


import_model_modules()

from app.database import (  # noqa: E402
    AUDIT_DATABASE_SCHEMA,
    Base,
    DATABASE_SCHEMA,
    DATABASE_URL,
    LOGS_DATABASE_SCHEMA,
)


target_metadata = Base.metadata
# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))


def include_managed_schema_object(object_, name, type_, reflected, compare_to) -> bool:
    """Limit autogeneration to application-owned objects and schemas.

    Alembic's own version tables are migration bookkeeping, not ORM objects.
    Excluding them prevents a newly generated revision from trying to drop the
    table that records its revision. Reflected audit objects are also excluded
    because they belong to the separate audit migration history.
    """

    del compare_to
    if type_ == "table" and name in {"alembic_version", "alembic_version_audit"}:
        return False
    if reflected:
        schema_name = getattr(object_, "schema", None)
        if schema_name is not None and schema_name not in {
            DATABASE_SCHEMA,
            LOGS_DATABASE_SCHEMA,
        }:
            return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        include_object=include_managed_schema_object,
        version_table_schema=DATABASE_SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DATABASE_SCHEMA}"'))
            connection.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{AUDIT_DATABASE_SCHEMA}"')
            )
            connection.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{LOGS_DATABASE_SCHEMA}"')
            )
            # Keep ``public`` as the reflection default. All current baseline
            # operations are schema-qualified, and using an application schema
            # as PostgreSQL's default makes Alembic reflect those tables with a
            # ``None`` schema, producing false autogenerate differences.
            connection.execute(text("SET search_path TO public"))
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            include_object=include_managed_schema_object,
            version_table_schema=DATABASE_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
