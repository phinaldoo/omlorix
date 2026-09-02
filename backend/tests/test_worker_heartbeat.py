from __future__ import annotations

import ast
from pathlib import Path
import time

from app import worker_heartbeat


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_worker_heartbeat_probe_uses_only_the_standard_library():
    source = (BACKEND_ROOT / "app" / "worker_heartbeat.py").read_text(encoding="utf-8")
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    )

    assert imported_roots == {"os", "pathlib", "sys", "time"}


def test_worker_heartbeat_probe_reads_timestamp_and_honors_overrides(
    monkeypatch, tmp_path
):
    heartbeat = tmp_path / "custom-heartbeat"
    monkeypatch.setenv("CONNECTOR_WORKER_HEARTBEAT_PATH", str(heartbeat))
    monkeypatch.setenv("CONNECTOR_WORKER_HEALTH_MAX_AGE_SECONDS", "30")

    heartbeat.write_text(str(time.time()), encoding="ascii")
    assert worker_heartbeat.main(["connector", "ingestion"]) == 0

    heartbeat.write_text(str(time.time() - 31), encoding="ascii")
    assert worker_heartbeat.main(["connector", "ingestion"]) == 1

    heartbeat.write_text("not-a-timestamp", encoding="ascii")
    assert worker_heartbeat.main(["connector", "ingestion"]) == 1
