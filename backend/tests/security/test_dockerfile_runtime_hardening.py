from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_dockerignore_files_stay_synchronized():
    """Keep root and backend build-context protections from drifting apart."""
    root_dockerignore = (REPO_ROOT / ".dockerignore").read_bytes()
    backend_dockerignore = (REPO_ROOT / "backend/.dockerignore").read_bytes()

    assert backend_dockerignore == root_dockerignore


def test_backend_prod_dockerfile_keeps_build_tools_out_of_runtime_image():
    source = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    runtime_stage = re.search(
        r"FROM python:3\.14\.\d+-slim@sha256:[a-f0-9]{64} AS runtime(?P<body>.*?)FROM runtime AS app-code",
        source,
        re.DOTALL,
    )

    assert runtime_stage is not None
    assert " AS mcp-deps" not in source
    assert "RUN npm ci --omit=dev" not in source
    assert "COPY --from=mcp-deps" not in source
    assert "COPY --from=python-runtime-deps /install /usr/local" in source

    # The final production target must not overlay the separately compiled
    # development tree that contains pytest, fakeredis, and Ruff.
    prod_section = source.split("FROM app-code AS prod", maxsplit=1)[1]
    assert "python-dev-deps" not in prod_section

    runtime_body = runtime_stage.group("body")
    assert "build-essential" not in runtime_body
    assert "libpq-dev" not in runtime_body
    assert "nodejs" not in runtime_body
    assert "npm" not in runtime_body
    assert "fonts-noto-core" in runtime_body
    assert "apt.postgresql.org" in runtime_body
    assert "postgresql-client-18" in runtime_body
