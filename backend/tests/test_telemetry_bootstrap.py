from __future__ import annotations

import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_shared_bootstrap_initializes_process_telemetry(monkeypatch):
    import app.telemetry.bootstrap as telemetry_bootstrap

    calls: list[tuple[str, object]] = []

    class FakeTelemetryConfig:
        instrument_http_clients = True
        instrument_sqlalchemy = True
        sql_commenter_enabled = True

        @classmethod
        def from_env(cls):
            calls.append(("config", None))
            return cls()

    monkeypatch.setattr(telemetry_bootstrap, "TelemetryConfig", FakeTelemetryConfig)
    monkeypatch.setattr(
        telemetry_bootstrap,
        "init_telemetry",
        lambda config: calls.append(("init", config)) or True,
    )
    monkeypatch.setattr(telemetry_bootstrap, "is_telemetry_enabled", lambda: True)
    monkeypatch.setattr(
        telemetry_bootstrap,
        "instrument_http_clients",
        lambda: calls.append(("http", None)) or True,
    )
    monkeypatch.setattr(
        telemetry_bootstrap,
        "instrument_sqlalchemy",
        lambda engine, enable_commenter=False: (
            calls.append(("sql", (engine, enable_commenter))) or True
        ),
    )

    engine = object()
    audit_engine = object()
    database_stub = types.ModuleType("app.database")
    database_stub.engine = engine
    database_stub.audit_engine = audit_engine

    monkeypatch.setitem(sys.modules, "app.database", database_stub)

    result = telemetry_bootstrap.bootstrap_telemetry()

    assert result.initialized is True
    assert calls == [
        ("config", None),
        ("init", result.config),
        ("http", None),
        ("sql", (engine, True)),
        ("sql", (audit_engine, True)),
    ]


def test_shared_bootstrap_respects_failed_telemetry_initialization(monkeypatch):
    import app.telemetry.bootstrap as telemetry_bootstrap

    calls: list[str] = []

    class FakeTelemetryConfig:
        instrument_http_clients = True
        instrument_sqlalchemy = True
        sql_commenter_enabled = True

        @classmethod
        def from_env(cls):
            return cls()

    monkeypatch.setattr(telemetry_bootstrap, "TelemetryConfig", FakeTelemetryConfig)
    monkeypatch.setattr(telemetry_bootstrap, "init_telemetry", lambda _config: False)
    monkeypatch.setattr(telemetry_bootstrap, "is_telemetry_enabled", lambda: True)
    monkeypatch.setattr(
        telemetry_bootstrap,
        "instrument_http_clients",
        lambda: calls.append("http") or True,
    )
    monkeypatch.setattr(
        telemetry_bootstrap,
        "instrument_sqlalchemy",
        lambda *_args, **_kwargs: calls.append("sql") or True,
    )

    result = telemetry_bootstrap.bootstrap_telemetry()

    assert result.initialized is False
    assert calls == []


def test_scheduler_entrypoint_bootstraps_before_importing_worker():
    source = (REPO_ROOT / "backend/app/automations/scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "from app.telemetry.bootstrap import bootstrap_telemetry" in source
    assert (
        "from app.automations.worker import run_automation_scheduler_forever"
        not in source.split(
            'if __name__ == "__main__":',
            maxsplit=1,
        )[0]
    )


def test_rq_worker_commands_use_telemetry_worker_class():
    compose_files = sorted(REPO_ROOT.glob("docker-compose*.yml"))

    assert compose_files
    for compose_file in compose_files:
        source = compose_file.read_text(encoding="utf-8")
        if "rq worker" in source:
            assert "--worker-class app.automations.rq_worker.TelemetryWorker" in source
