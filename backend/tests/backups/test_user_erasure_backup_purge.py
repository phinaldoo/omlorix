from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from app.backups import service as backup_service


def test_app_data_backup_excludes_restore_resistant_erasure_ledger(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = data_dir / ".erasure-ledger.jsonl"
    ledger.write_text('{"user_id":"deleted-user"}\n', encoding="utf-8")
    (data_dir / "ordinary.txt").write_text("included", encoding="utf-8")
    archive_path = tmp_path / "data.tar"

    backup_service._create_directory_tar(
        data_dir,
        archive_path,
        excluded_relative_paths={Path(".erasure-ledger.jsonl")},
    )

    import tarfile

    with tarfile.open(archive_path, "r") as archive:
        names = set(archive.getnames())
    assert "ordinary.txt" in names
    assert ".erasure-ledger.jsonl" not in names


def test_data_restore_preserves_erasure_ledger(tmp_path):
    target = tmp_path / "target"
    restored = tmp_path / "restored"
    rollback = target / ".rollback"
    target.mkdir()
    restored.mkdir()
    (target / ".erasure-ledger.jsonl").write_text("ledger", encoding="utf-8")
    (target / "old.txt").write_text("old", encoding="utf-8")
    (restored / "new.txt").write_text("new", encoding="utf-8")

    backup_service._install_staged_directory_contents(
        restored,
        target,
        rollback,
        preserved_names={".erasure-ledger.jsonl"},
    )

    assert (target / ".erasure-ledger.jsonl").read_text(encoding="utf-8") == "ledger"
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (target / "old.txt").exists()


def test_data_restore_rejects_archive_collision_with_erasure_ledger(tmp_path):
    target = tmp_path / "target"
    restored = target / ".restore_tmp"
    rollback = target / ".rollback"
    target.mkdir()
    restored.mkdir()
    ledger = target / ".erasure-ledger.jsonl"
    ledger.write_text("trusted-ledger", encoding="utf-8")
    ordinary = target / "ordinary.txt"
    ordinary.write_text("live-data", encoding="utf-8")
    (restored / ".erasure-ledger.jsonl").write_text(
        "archive-controlled",
        encoding="utf-8",
    )
    (restored / "replacement.txt").write_text("replacement", encoding="utf-8")

    with pytest.raises(RuntimeError, match="protected data entries"):
        backup_service._install_staged_directory_contents(
            restored,
            target,
            rollback,
            preserved_names={".erasure-ledger.jsonl"},
        )

    assert ledger.read_text(encoding="utf-8") == "trusted-ledger"
    assert ordinary.read_text(encoding="utf-8") == "live-data"
    assert not (target / "replacement.txt").exists()
    assert not restored.exists()
    assert not rollback.exists()


def test_archive_collision_is_rejected_before_any_restore_mutation(
    tmp_path,
    monkeypatch,
):
    mutation_attempts = []

    def materialize_archive(_archive_path, destination):
        app_data_tar = destination / backup_service.BACKUP_REQUIRED_PATHS["app_data_tar"]
        app_data_tar.parent.mkdir(parents=True, exist_ok=True)
        payload = b"archive-controlled"
        with tarfile.open(app_data_tar, mode="w") as archive:
            member = tarfile.TarInfo("./.erasure-ledger.jsonl")
            member.size = len(payload)
            archive.addfile(member, BytesIO(payload))

    monkeypatch.setattr(backup_service, "_extract_zstd_archive", materialize_archive)
    monkeypatch.setattr(
        backup_service,
        "_erasure_ledger_data_entries",
        lambda: {Path(".erasure-ledger.jsonl")},
    )
    monkeypatch.setattr(
        backup_service,
        "mark_restore_erasure_reconciliation_required",
        lambda: mutation_attempts.append("erasure-marker"),
    )
    monkeypatch.setattr(
        backup_service,
        "_restore_database",
        lambda *args, **kwargs: mutation_attempts.append("database"),
    )

    with pytest.raises(RuntimeError, match="protected data entries"):
        backup_service._restore_from_archive(
            SimpleNamespace(id="restore-job-id"),
            tmp_path / "backup.tar.zst",
        )

    assert mutation_attempts == []


def test_nested_erasure_ledger_reserves_one_matching_data_entry(tmp_path, monkeypatch):
    """Nested custom paths use identical archive and restore granularity."""
    data_dir = tmp_path / "data"
    ledger_path = data_dir / "erasure" / "ledger.jsonl"
    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "ERASURE_LEDGER_PATH", ledger_path)

    assert backup_service._erasure_ledger_data_entries() == {Path("erasure")}
