"""Active presentation workspaces survive maintenance across worker processes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.files import worker
from app.files.temp_workspaces import temporary_workspace, workspace_is_active


@pytest.fixture
def storage(tmp_path, monkeypatch):
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    monkeypatch.setattr(worker, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(worker, "MATERIALIZED_TEMP_DIR", materialized)
    return materialized


def age_tree(directory):
    for path in directory.rglob("*"):
        os.utime(path, (1, 1))
    os.utime(directory, (1, 1))


def test_cleanup_keeps_fresh_directories_and_removes_stale_unleased_files(storage):
    fresh = storage / "starting-workspace"
    fresh.mkdir()
    abandoned = storage / "abandoned"
    abandoned.mkdir()
    (abandoned / "old.png").write_bytes(b"old")
    age_tree(abandoned)
    worker.cleanup_temp_files()
    assert fresh.is_dir()
    assert not abandoned.exists()
    assert storage.is_dir()


def test_leased_workspace_and_old_images_survive_cleanup_until_exit(storage):
    with temporary_workspace(parent=storage, prefix="presentation-review-") as review:
        (review / "images").mkdir()
        image = review / "images" / "old.png"
        image.write_bytes(b"image")
        age_tree(review)
        worker.cleanup_temp_files()
        assert workspace_is_active(review)
        assert image.read_bytes() == b"image"
        (review / "presentation-candidate").mkdir()
    assert not review.exists()


def test_worker_crash_releases_lease_for_maintenance(storage):
    script = """
import sys
from pathlib import Path
from app.files.temp_workspaces import temporary_workspace
with temporary_workspace(parent=Path(sys.argv[1]), prefix="presentation-review-") as review:
    (review / "old.png").write_bytes(b"image")
    print(review, flush=True)
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(storage)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )
    try:
        # Readiness is bounded so a failed child import cannot hang the suite.
        import select

        assert select.select([process.stdout], [], [], 10)[0]
        path = process.stdout.readline().strip()
        assert path
        review = Path(path)
        assert review.parent == storage
        age_tree(review)
        worker.cleanup_temp_files()
        assert workspace_is_active(review)
        assert (review / "old.png").exists()
    finally:
        process.kill()
        process.communicate(timeout=10)
    assert not workspace_is_active(review)
    worker.cleanup_temp_files()
    assert not review.exists()
