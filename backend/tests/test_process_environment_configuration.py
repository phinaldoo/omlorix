"""Guard the process-environment-only backend configuration boundary."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_fastapi_startup_does_not_run_database_migrations() -> None:
    """Keep schema changes in the dedicated deployment migration service."""

    main_source = (REPO_ROOT / "backend" / "app" / "main.py").read_text(
        encoding="utf-8"
    )

    assert "app.migrations.runner" not in main_source
    assert "run_all_migrations" not in main_source


def test_backend_python_does_not_parse_dotenv_files() -> None:
    """Keep dotenv parsing in deployment tooling, outside Python processes."""

    python_roots = (
        REPO_ROOT / "backend" / "app",
        REPO_ROOT / "backend" / "alembic_main",
        REPO_ROOT / "backend" / "alembic_audit",
    )
    forbidden_fragments = ("from dotenv", "import dotenv", "load_dotenv")
    offenders: list[str] = []

    for python_root in python_roots:
        for source_path in python_root.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            if any(fragment in source for fragment in forbidden_fragments):
                offenders.append(str(source_path.relative_to(REPO_ROOT)))

    assert offenders == []

    direct_requirements = (REPO_ROOT / "backend" / "app" / "requirements.in").read_text(
        encoding="utf-8"
    )
    assert "python-dotenv" not in direct_requirements
