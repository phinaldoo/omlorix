from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_observability_overlay_enables_backend_telemetry():
    source = (REPO_ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8")

    assert "  fastapi:\n" in source
    assert 'OTEL_ENABLED: "true"' in source
    assert 'OTEL_TRACES_ENABLED: "true"' in source
    assert 'OTEL_METRICS_ENABLED: "true"' in source
    assert 'OTEL_PROMETHEUS_EXPORTER_ENABLED: "true"' in source
    assert "OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317" in source
    assert 'OTEL_EXPORTER_OTLP_INSECURE: "true"' in source
    assert 'PROMETHEUS_METRICS_TOKEN_FILE: /run/omlorix-metrics/token' in source
    assert 'PROMETHEUS_METRICS_PUBLIC: "true"' not in source
    assert "otel-collector:\n        condition: service_healthy" in source
