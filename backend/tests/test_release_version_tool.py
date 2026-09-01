"""Tests for version selection used by the GitHub release workflow."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUMP_VERSION_SCRIPT = PROJECT_ROOT / ".github" / "scripts" / "bump_version.py"


def run_git(repository: Path, *args: str) -> None:
    """Run one deterministic Git command in the temporary repository."""
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_release_bump_uses_newest_server_tag_when_branch_metadata_lags(tmp_path: Path) -> None:
    """A published tag must prevent a duplicate version after a deferred sync."""
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "version.py").write_text(
        'APP_VERSION = "0.9.19"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("OMLORIX_VERSION=0.9.19\n", encoding="utf-8")

    run_git(tmp_path, "init", "-q")
    run_git(tmp_path, "config", "user.name", "Release Test")
    run_git(tmp_path, "config", "user.email", "release-test@example.invalid")
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-q", "-m", "initial version")
    run_git(tmp_path, "tag", "v0.9.20-beta.2")
    run_git(tmp_path, "tag", "v0.9.20")
    # The app prefix must not accidentally use independently versioned launcher tags.
    run_git(tmp_path, "tag", "server-launcher-v9.0.0")

    result = subprocess.run(
        [
            sys.executable,
            str(BUMP_VERSION_SCRIPT),
            "patch",
            "--channel",
            "stable",
            "--base-tag-prefix",
            "v",
            "--root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "base_version=0.9.20" in result.stdout
    assert "version=0.9.21" in result.stdout
    assert 'APP_VERSION = "0.9.21"' in (
        tmp_path / "backend" / "app" / "version.py"
    ).read_text(encoding="utf-8")
    assert (tmp_path / ".env.example").read_text(encoding="utf-8") == (
        "OMLORIX_VERSION=0.9.21\n"
    )
