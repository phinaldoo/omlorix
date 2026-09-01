import importlib
import sys
from pathlib import Path

import psycopg2
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _set_database_env(monkeypatch, *, mode: str, auto_create: str | None = None) -> None:
    monkeypatch.setenv("MODE", mode)
    if auto_create is None:
        monkeypatch.delenv("OMLORIX_AUTO_CREATE_DATABASES", raising=False)
    else:
        monkeypatch.setenv("OMLORIX_AUTO_CREATE_DATABASES", auto_create)

    values = {
        "DATABASE_USER": "omlorix",
        "DATABASE_PASSWORD": "secret",
        "DATABASE_HOST": "db.example.test",
        "DATABASE_PORT": "5432",
        "DATABASE_NAME": "omlorix",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    for suffix in ("URL",):
        monkeypatch.delenv(f"DATABASE_{suffix}", raising=False)


def _import_database_module():
    module_name = "app.database"
    original = sys.modules.pop(module_name, None)
    try:
        return importlib.import_module(module_name)
    finally:
        sys.modules.pop(module_name, None)
        if original is not None:
            sys.modules[module_name] = original


def test_production_import_enables_database_autocreate_by_default(monkeypatch):
    _set_database_env(monkeypatch, mode="production")
    connect_calls: list[dict[str, str]] = []

    class FakeCursor:
        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return (1,)

        def close(self):
            return None

    class FakeConnection:
        autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    def fake_connect(**kwargs):
        connect_calls.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    module = _import_database_module()

    assert module._database_autocreate_enabled() is True
    assert len(connect_calls) == 1
    assert connect_calls[0]["dbname"] == "postgres"


def test_production_import_can_explicitly_disable_database_autocreate(monkeypatch):
    _set_database_env(monkeypatch, mode="production", auto_create="false")
    connect_calls: list[dict[str, str]] = []

    def fake_connect(**kwargs):
        connect_calls.append(kwargs)
        raise AssertionError("psycopg2.connect should not be called when database auto-create is disabled")

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    module = _import_database_module()

    assert module._database_autocreate_enabled() is False
    assert connect_calls == []


def test_production_import_can_explicitly_enable_database_autocreate(monkeypatch):
    _set_database_env(monkeypatch, mode="production", auto_create="true")
    connect_calls: list[dict[str, str]] = []

    class FakeCursor:
        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return (1,)

        def close(self):
            return None

    class FakeConnection:
        autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    def fake_connect(**kwargs):
        connect_calls.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    module = _import_database_module()

    assert module._database_autocreate_enabled() is True
    assert len(connect_calls) == 1
    assert connect_calls[0]["dbname"] == "postgres"


def test_database_autocreate_preserves_database_url_security_policy(monkeypatch):
    """The bootstrap coordinator must connect with the configured TLS policy."""
    _set_database_env(monkeypatch, mode="production")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql://omlorix:secret@ignored.example:5432/omlorix"
            "?host=database.example&sslmode=verify-full"
            "&sslrootcert=%2Fcerts%2Froot.crt&channel_binding=require"
            "&require_auth=scram-sha-256"
        ),
    )
    connect_calls: list[dict[str, str]] = []

    class FakeCursor:
        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return (1,)

        def close(self):
            return None

    class FakeConnection:
        autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    def fake_connect(**kwargs):
        connect_calls.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    _import_database_module()

    assert len(connect_calls) == 1
    assert connect_calls[0]["dbname"] == "postgres"
    assert connect_calls[0]["host"] == "database.example"
    assert connect_calls[0]["sslmode"] == "verify-full"
    assert connect_calls[0]["sslrootcert"] == "/certs/root.crt"
    assert connect_calls[0]["channel_binding"] == "require"
    assert connect_calls[0]["require_auth"] == "scram-sha-256"


@pytest.mark.parametrize(
    "driver",
    [
        "postgresql+asyncpg",
        "postgresql+psycopg",
    ],
)
def test_database_url_rejects_non_psycopg2_postgres_dialects(monkeypatch, driver):
    """DBAPI-specific kwargs must not cross into the psycopg2 connection path."""
    _set_database_env(monkeypatch, mode="production", auto_create="false")
    database_module = _import_database_module()
    unsupported_url = f"{driver}://omlorix:secret@database.example:5432/omlorix"

    with pytest.raises(RuntimeError, match="must use the psycopg2 dialect"):
        database_module.build_postgres_connection_kwargs(
            {
                "driver": driver,
                "url": unsupported_url,
            }
        )

    monkeypatch.setenv(
        "DATABASE_URL",
        unsupported_url,
    )

    with pytest.raises(RuntimeError, match="must use the psycopg2 PostgreSQL dialect"):
        _import_database_module()


def test_production_import_rejects_invalid_database_autocreate_value(monkeypatch):
    _set_database_env(monkeypatch, mode="production", auto_create="definitely")
    connect_calls: list[dict[str, str]] = []

    def fake_connect(**kwargs):
        connect_calls.append(kwargs)
        raise AssertionError("psycopg2.connect should not be called for invalid database auto-create values")

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    with pytest.raises(RuntimeError, match="Invalid OMLORIX_AUTO_CREATE_DATABASES"):
        _import_database_module()

    assert connect_calls == []
