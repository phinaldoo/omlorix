"""Shared OpenTelemetry startup for Omlorix backend processes.

Runtime configuration must be supplied through the process environment before
Python starts. Docker Compose, the Server Launcher, and the server CLI own that
configuration boundary; backend modules only read the resulting environment.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.telemetry.config import TelemetryConfig, init_telemetry, is_telemetry_enabled
from app.telemetry.instrumentor import instrument_http_clients, instrument_sqlalchemy


@dataclass(frozen=True)
class TelemetryBootstrap:
    """Report the telemetry configuration and whether initialization succeeded."""

    config: TelemetryConfig
    initialized: bool


def bootstrap_telemetry(*, instrument_database: bool = True) -> TelemetryBootstrap:
    """Initialize telemetry for API, scheduler, and worker processes.

    Args:
        instrument_database: Whether to instrument the application and audit
            SQLAlchemy engines during this call. The API delays this step until
            after it imports and creates both engines.

    Returns:
        The resolved telemetry configuration and active initialization state.
    """

    config = TelemetryConfig.from_env()
    initialized = bool(init_telemetry(config)) and is_telemetry_enabled()

    if initialized and config.instrument_http_clients:
        instrument_http_clients()

    if initialized and instrument_database and config.instrument_sqlalchemy:
        # Importing the database module creates both engines. Keep this import
        # conditional so callers can initialize telemetry before database setup.
        from app.database import audit_engine, engine

        instrument_sqlalchemy(engine, enable_commenter=config.sql_commenter_enabled)
        instrument_sqlalchemy(
            audit_engine, enable_commenter=config.sql_commenter_enabled
        )

    return TelemetryBootstrap(config=config, initialized=initialized)
