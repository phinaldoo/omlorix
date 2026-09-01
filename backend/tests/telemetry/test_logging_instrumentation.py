import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.telemetry.config as telemetry_config
import app.telemetry.instrumentor as telemetry_instrumentor
from app.telemetry.config import TelemetryConfig


def _reset_telemetry_state() -> None:
    telemetry_config._initialized = False
    telemetry_config._tracer_provider = None
    telemetry_config._meter_provider = None
    telemetry_config._prometheus_reader = None


def _config_with_logs(logs_enabled: bool) -> TelemetryConfig:
    return TelemetryConfig(
        enabled=True,
        traces_enabled=False,
        metrics_enabled=False,
        logs_enabled=logs_enabled,
    )


def test_init_telemetry_instruments_logging_when_enabled(monkeypatch):
    _reset_telemetry_state()
    calls = []

    monkeypatch.setattr(telemetry_config, "_setup_propagation", lambda: None)
    monkeypatch.setattr(
        telemetry_instrumentor,
        "instrument_logging",
        lambda: calls.append("instrumented") or True,
    )

    try:
        assert telemetry_config.init_telemetry(_config_with_logs(True)) is True
        assert calls == ["instrumented"]
    finally:
        _reset_telemetry_state()


def test_init_telemetry_skips_logging_when_disabled(monkeypatch):
    _reset_telemetry_state()
    calls = []

    monkeypatch.setattr(telemetry_config, "_setup_propagation", lambda: None)
    monkeypatch.setattr(
        telemetry_instrumentor,
        "instrument_logging",
        lambda: calls.append("instrumented") or True,
    )

    try:
        assert telemetry_config.init_telemetry(_config_with_logs(False)) is True
        assert calls == []
    finally:
        _reset_telemetry_state()


def test_instrument_logging_uses_trace_correlation_format(monkeypatch):
    telemetry_instrumentor._logging_instrumented = False
    captured = {}

    class FakeLoggingInstrumentor:
        def instrument(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        telemetry_instrumentor,
        "LoggingInstrumentor",
        FakeLoggingInstrumentor,
    )

    try:
        assert telemetry_instrumentor.instrument_logging() is True
        assert captured["set_logging_format"] is True
        assert "trace_id=%(otelTraceID)s" in captured["logging_format"]
        assert "span_id=%(otelSpanID)s" in captured["logging_format"]
    finally:
        telemetry_instrumentor._logging_instrumented = False
