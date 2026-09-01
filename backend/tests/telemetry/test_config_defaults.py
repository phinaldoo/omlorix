from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PY = REPO_ROOT / "backend" / "app" / "telemetry" / "config.py"


def _telemetry_config_class() -> ast.ClassDef:
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TelemetryConfig":
            return node

    raise AssertionError("TelemetryConfig class not found")


def _class_default(name: str):
    for node in _telemetry_config_class().body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)

    raise AssertionError(f"{name} default not found")


def test_default_otlp_settings_are_secure():
    assert _class_default("otlp_endpoint") == "https://otel-collector:4317"
    assert _class_default("otlp_insecure") is False


def test_env_defaults_are_secure():
    source = CONFIG_PY.read_text(encoding="utf-8")

    assert 'os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel-collector:4317")' in source
    assert 'otlp_insecure=_bool("OTEL_EXPORTER_OTLP_INSECURE", False)' in source
