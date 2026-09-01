"""Protect the runtime/development dependency packaging boundary."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"
PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)", re.MULTILINE)


def _pinned_versions(path: Path) -> dict[str, str]:
    """Return normalized package names and versions from a dependency file."""

    contents = path.read_text(encoding="utf-8")
    return {
        package.lower().replace("_", "-"): version
        for package, version in PIN_PATTERN.findall(contents)
    }


def test_runtime_lock_excludes_development_tools() -> None:
    """Keep security and test-only packages on the correct side of the image boundary."""

    runtime_versions = _pinned_versions(APP_ROOT / "requirements.txt")
    development_versions = _pinned_versions(APP_ROOT / "requirements-dev.txt")
    development_pins = _pinned_versions(APP_ROOT / "requirements-dev.in")

    # The production lock must contain the patched cryptography dependency and
    # must never regain tools that are used exclusively by the test suite.
    assert runtime_versions["cryptography"] == "50.0.0"
    assert runtime_versions["webauthn"] == "3.0.0"
    assert "pytest-asyncio" not in runtime_versions
    assert "fakeredis" not in runtime_versions
    assert "ruff" not in runtime_versions

    # c2pa-python 0.37.7 declares pytest as a runtime dependency. Keep that
    # upstream packaging exception aligned with the explicit development pin.
    assert runtime_versions["pytest"] == development_pins["pytest"]

    # Developers and CI still need the complete test toolchain, and lock
    # updates must follow the source pins instead of duplicated versions here.
    for package in ("pytest", "pytest-asyncio", "fakeredis", "ruff"):
        assert development_versions[package] == development_pins[package]


def test_onnx_runtime_telemetry_is_disabled_before_imports() -> None:
    """Keep every packaged backend process from initializing ONNX telemetry."""

    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    telemetry_guard = 'os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")'
    assert main_source.index(telemetry_guard) < main_source.index("from fastapi import")

    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "ORT_DISABLE_TELEMETRY=1" in dockerfile
