from __future__ import annotations

from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.files import cli as files_cli
from app.files.storage.local import LocalUserFileStorageAdapter
from app.tools.deep_research import storage as deep_research_storage


PROVIDERS = ("local", "s3", "gcs", "azure", "webdav")


class MemoryStorageAdapter:
    """Small provider-neutral adapter used to exercise migration orchestration."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})
        self.upload_calls = 0
        self.delete_calls = 0

    def upload_file(self, local_path: Path, storage_key: str) -> dict:
        self.upload_calls += 1
        payload = Path(local_path).read_bytes()
        self.objects[storage_key] = payload
        return {"size_bytes": len(payload), "key": storage_key}

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        if storage_key not in self.objects:
            raise FileNotFoundError(storage_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.objects[storage_key])
        return target

    def delete_file(self, storage_key: str) -> None:
        self.delete_calls += 1
        self.objects.pop(storage_key, None)

    def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    def test_connection(self) -> dict:
        return {"status": "ok"}


class DeleteFailingStorageAdapter(MemoryStorageAdapter):
    """Exercise post-commit cleanup reporting without losing source bytes."""

    def delete_file(self, storage_key: str) -> None:
        self.delete_calls += 1
        raise RuntimeError("source delete unavailable")


class CorruptingDownloadStorageAdapter(MemoryStorageAdapter):
    """Return corrupt bytes so upload verification cannot report success."""

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"corrupt")
        return target


class FakeUpdateQuery:
    """Implement the conditional update surface used by the migration CLI."""

    def __init__(self, db: "FakeSession"):
        self.db = db

    def filter(self, *conditions):
        return self

    def update(self, values, synchronize_session=False):
        if self.db.update_count != 1:
            return self.db.update_count
        for column, value in values.items():
            setattr(self.db.file_row, column.key, value)
        return 1


class FakeSession:
    """Track database lifecycle calls without requiring the application schema."""

    def __init__(self, file_row, *, update_count: int = 1):
        self.file_row = file_row
        self.update_count = update_count
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def query(self, model):
        return FakeUpdateQuery(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeArtifactQuery:
    """Provide the small query/update surface used by artifact-set migration."""

    def __init__(self, db: "FakeArtifactSession"):
        self.db = db

    def filter(self, *conditions):
        return self

    def order_by(self, *columns):
        return self

    def limit(self, batch_size):
        return self

    def all(self):
        # Artifact iterators request a second keyset page after processing the
        # first one. Update queries never call ``all``, so a shared counter is
        # enough for these focused single-owner tests.
        if self.db.page_reads:
            return []
        self.db.page_reads += 1
        return [self.db.row]

    def update(self, values, synchronize_session=False):
        for column, value in values.items():
            setattr(self.db.row, column.key, value)
        return 1


class FakeArtifactSession:
    """Track one Deep Research or presentation owner without a database."""

    def __init__(self, row):
        self.row = row
        self.commits = 0
        self.rollbacks = 0
        self.page_reads = 0

    def query(self, model):
        return FakeArtifactQuery(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _file_row(provider: str):
    """Build one complete-enough file row for the CLI migration workflow."""
    return SimpleNamespace(
        id="file-1",
        user_id="user-1",
        file_name="file-1.txt",
        storage_provider=provider,
        storage_key="user-1/file-1.txt",
        storage_meta=None,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
    )


def _args(source_provider: str, destination_provider: str, **overrides):
    """Build parsed-command equivalents while keeping tests focused."""
    values = {
        "from_provider": source_provider,
        "to_provider": destination_provider,
        "dry_run": False,
        "delete_source": False,
        "user_id": None,
        "created_after": None,
        "created_before": None,
        "batch_size": 200,
        "max_files": 0,
        "retries": 1,
        "force": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_migration_fakes(
    monkeypatch,
    *,
    file_row,
    source_adapter,
    destination_adapter,
    destination_provider: str,
    update_count: int = 1,
):
    """Wire one file and two adapters into the command under test."""
    db = FakeSession(file_row, update_count=update_count)
    adapters = {
        file_row.storage_provider: source_adapter,
        destination_provider: destination_adapter,
    }
    monkeypatch.setattr(files_cli, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        files_cli,
        "_iter_storage_records",
        lambda *args, **kwargs: iter([file_row]),
    )
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_adapter_for_provider",
        lambda provider: adapters[provider],
    )
    return db


@pytest.mark.parametrize(
    ("source_provider", "destination_provider"),
    list(itertools.permutations(PROVIDERS, 2)),
)
def test_migrate_files_supports_every_distinct_provider_pair(
    monkeypatch,
    capsys,
    source_provider,
    destination_provider,
):
    """Every local/external and external/external direction uses one contract."""
    storage_key = "user-1/file-1.txt"
    payload = b"provider-neutral migration payload"
    file_row = _file_row(source_provider)
    source = MemoryStorageAdapter({storage_key: payload})
    destination = MemoryStorageAdapter()
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider=destination_provider,
    )

    exit_code = files_cli._cmd_migrate_files(
        _args(source_provider, destination_provider)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["migrated"] == 1
    assert output["failed"] == 0
    assert destination.objects[storage_key] == payload
    assert source.objects[storage_key] == payload
    assert file_row.storage_provider == destination_provider
    assert file_row.storage_key == storage_key
    assert file_row.storage_meta["sha256"]
    assert file_row.storage_meta["migration"]["source_provider"] == source_provider
    assert db.commits == 1
    assert db.closed is True


def test_migrate_files_resumes_only_after_destination_checksum_matches(
    monkeypatch,
    capsys,
):
    """A previous upload can resume without another provider write."""
    storage_key = "user-1/file-1.txt"
    payload = b"already copied"
    file_row = _file_row("s3")
    source = MemoryStorageAdapter({storage_key: payload})
    destination = MemoryStorageAdapter({storage_key: payload})
    _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="gcs",
    )

    exit_code = files_cli._cmd_migrate_files(_args("s3", "gcs"))

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["resumed"] == 1
    assert destination.upload_calls == 0
    assert file_row.storage_meta["resume"] is True


def test_local_migration_reads_legacy_root_level_source_file(tmp_path):
    """Old flat local layouts can be normalized into the keyed destination."""
    legacy_file = tmp_path / "legacy.txt"
    legacy_file.write_bytes(b"legacy local bytes")
    source = LocalUserFileStorageAdapter(tmp_path)
    destination = MemoryStorageAdapter()
    storage_key = "user-1/legacy.txt"

    legacy_source_path = files_cli._legacy_local_source_path(source, storage_key)
    metadata, resumed = files_cli._copy_storage_object(
        source_adapter=source,
        destination_adapter=destination,
        storage_key=storage_key,
        force=False,
        legacy_local_source_path=legacy_source_path,
    )

    assert legacy_source_path == legacy_file
    assert resumed is False
    assert metadata["sha256"]
    assert destination.objects[storage_key] == b"legacy local bytes"
    assert legacy_file.exists()


def test_legacy_local_command_deletes_actual_flat_source_when_requested(
    tmp_path, monkeypatch, capsys
):
    """Legacy cleanup removes and counts the flat file rather than a missing key."""
    legacy_file = tmp_path / "file-1.txt"
    legacy_file.write_bytes(b"legacy local bytes")
    file_row = _file_row("local")
    file_row.storage_key = ""
    source = LocalUserFileStorageAdapter(tmp_path)
    destination = MemoryStorageAdapter()
    _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="webdav",
    )

    exit_code = files_cli._cmd_migrate_files(
        _args("local", "webdav", delete_source=True)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["migrated"] == 1
    assert output["deleted_source"] == 1
    assert not legacy_file.exists()
    assert destination.objects["user-1/file-1.txt"] == b"legacy local bytes"


def test_local_cleanup_keeps_flat_legacy_file_when_keyed_source_was_used(
    tmp_path, monkeypatch, capsys
):
    """A stale flat duplicate is not deleted when the keyed object was copied."""
    legacy_file = tmp_path / "file-1.txt"
    legacy_file.write_bytes(b"stale legacy bytes")
    keyed_file = tmp_path / "user-1" / "file-1.txt"
    keyed_file.parent.mkdir(parents=True)
    keyed_file.write_bytes(b"current keyed bytes")
    file_row = _file_row("local")
    source = LocalUserFileStorageAdapter(tmp_path)
    destination = MemoryStorageAdapter()
    _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="webdav",
    )

    exit_code = files_cli._cmd_migrate_files(
        _args("local", "webdav", delete_source=True)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["deleted_source"] == 1
    assert not keyed_file.exists()
    assert legacy_file.read_bytes() == b"stale legacy bytes"
    assert destination.objects["user-1/file-1.txt"] == b"current keyed bytes"


def test_deep_research_workspace_migrates_as_verified_artifact_set(tmp_path):
    """Every file beneath a run prefix moves before its storage metadata."""
    user_id = "user-1"
    run_id = "run-1"
    workspace = tmp_path / user_id / "deep_research" / run_id
    (workspace / "artifacts").mkdir(parents=True)
    (workspace / "final-report.md").write_text("# Result", encoding="utf-8")
    (workspace / "artifacts" / "chart.png").write_bytes(b"png-bytes")
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=run_id,
        user_id=user_id,
        created_at=now,
        updated_at=now,
        result_meta={},
        final_report_path="final-report.md",
        final_html_path=None,
        manifest_path=None,
        artifacts=[],
    )
    db = FakeArtifactSession(run)
    source = LocalUserFileStorageAdapter(tmp_path)
    destination = MemoryStorageAdapter()

    stats = files_cli._migrate_deep_research_records(
        db,
        source_provider="local",
        destination_provider="webdav",
        source_adapter=source,
        destination_adapter=destination,
        user_id=None,
        created_after=None,
        created_before=None,
        only_migrated_from=None,
        max_records=0,
        dry_run=False,
        delete_source=False,
        force=False,
        retries=1,
    )

    assert stats["migrated"] == 1
    assert stats["objects"] == 2
    assert run.result_meta["storage"]["provider"] == "webdav"
    assert run.result_meta["storage"]["migration"]["source_provider"] == "local"
    assert set(run.result_meta["storage"]["uploaded_files"]) == {
        "artifacts/chart.png",
        "final-report.md",
    }
    assert (
        destination.objects[f"{user_id}/deep_research/{run_id}/final-report.md"]
        == b"# Result"
    )
    assert (
        destination.objects[f"{user_id}/deep_research/{run_id}/artifacts/chart.png"]
        == b"png-bytes"
    )


def test_interrupted_deep_research_run_without_final_storage_meta_is_probed():
    """Verified checkpoint files remain migratable after a failed final upload."""
    user_id = "user-1"
    run_id = "run-1"
    prefix = f"{user_id}/deep_research/{run_id}"
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=run_id,
        user_id=user_id,
        status="failed",
        created_at=now,
        updated_at=now,
        result_meta={
            "checkpoints": {"research": {"files": ["research-notes.md"]}}
        },
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        artifacts=[],
    )
    db = FakeArtifactSession(run)
    source = MemoryStorageAdapter(
        {f"{prefix}/research-notes.md": b"durable checkpoint"}
    )
    destination = MemoryStorageAdapter()

    stats = files_cli._migrate_deep_research_records(
        db,
        source_provider="webdav",
        destination_provider="s3",
        source_adapter=source,
        destination_adapter=destination,
        user_id=None,
        created_after=None,
        created_before=None,
        only_migrated_from=None,
        max_records=0,
        dry_run=False,
        delete_source=False,
        force=False,
        retries=1,
    )

    assert stats["migrated"] == 1
    assert stats["objects"] == 1
    assert run.result_meta["storage"]["provider"] == "s3"
    assert destination.objects[f"{prefix}/research-notes.md"] == b"durable checkpoint"


def test_artifact_set_without_source_objects_never_moves_database_reference():
    """An empty fallback manifest is an error, not a successful migration."""
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id="run-1",
        user_id="user-1",
        status="failed",
        created_at=now,
        updated_at=now,
        result_meta={"checkpoints": {"research": {"files": ["missing.md"]}}},
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        artifacts=[],
    )
    db = FakeArtifactSession(run)

    stats = files_cli._migrate_deep_research_records(
        db,
        source_provider="webdav",
        destination_provider="s3",
        source_adapter=MemoryStorageAdapter(),
        destination_adapter=MemoryStorageAdapter(),
        user_id=None,
        created_after=None,
        created_before=None,
        only_migrated_from=None,
        max_records=0,
        dry_run=False,
        delete_source=False,
        force=False,
        retries=1,
    )

    assert stats["migrated"] == 0
    assert stats["failed"] == 1
    assert "storage" not in run.result_meta
    assert db.commits == 0


def test_known_deep_research_paths_skip_invalid_candidates():
    """One malformed legacy path does not discard the valid fallback manifest."""
    run = SimpleNamespace(
        final_report_path="../unsafe.md",
        final_html_path="final-report.html",
        manifest_path=None,
        result_meta={
            "archive_path": "workspace.zip",
            "checkpoints": {
                "research": {"files": ["notes/valid.md", "/absolute.txt"]}
            },
        },
        artifacts=[
            {"relative_path": "artifacts/chart.png"},
            {"relative_path": "..\\unsafe.png"},
        ],
    )

    paths = files_cli._known_deep_research_paths(run)

    assert "final-report.html" in paths
    assert "notes/valid.md" in paths
    assert "artifacts/chart.png" in paths
    assert "workspace.zip" in paths
    assert paths.count("workspace.zip") == 1
    assert "../unsafe.md" not in paths
    assert "/absolute.txt" not in paths


def test_new_deep_research_upload_persists_verified_object_manifest(
    tmp_path, monkeypatch
):
    """New external runs record every remotely verified workspace object."""
    workspace = tmp_path / "workspace"
    (workspace / "artifacts").mkdir(parents=True)
    (workspace / "final-report.md").write_text("# Result", encoding="utf-8")
    (workspace / "artifacts" / "chart.png").write_bytes(b"chart")
    adapter = MemoryStorageAdapter()
    monkeypatch.setattr(
        deep_research_storage,
        "get_deep_research_storage_provider",
        lambda: "webdav",
    )
    monkeypatch.setattr(
        deep_research_storage,
        "get_user_file_storage_adapter",
        lambda: adapter,
    )

    result = deep_research_storage.upload_deep_research_artifacts(
        workspace_dir=workspace,
        user_id="user-1",
        session_id="run-1",
    )

    assert result["uploaded_files"] == [
        "artifacts/chart.png",
        "final-report.md",
    ]
    assert [item["relative_path"] for item in result["objects"]] == result[
        "uploaded_files"
    ]
    assert all(len(item["sha256"]) == 64 for item in result["objects"])


def test_new_deep_research_upload_rejects_corrupt_remote_copy(
    tmp_path, monkeypatch
):
    """A run cannot claim durable external storage after checksum corruption."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.md").write_text("expected", encoding="utf-8")
    monkeypatch.setattr(
        deep_research_storage,
        "get_deep_research_storage_provider",
        lambda: "webdav",
    )
    monkeypatch.setattr(
        deep_research_storage,
        "get_user_file_storage_adapter",
        lambda: CorruptingDownloadStorageAdapter(),
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        deep_research_storage.upload_deep_research_artifacts(
            workspace_dir=workspace,
            user_id="user-1",
            session_id="run-1",
        )


def test_deep_research_legacy_provider_depends_on_completion_status(monkeypatch):
    """Completed legacy runs are local while interrupted checkpoints use config."""
    monkeypatch.setattr(
        deep_research_storage,
        "get_deep_research_storage_provider",
        lambda: "webdav",
    )
    completed = SimpleNamespace(
        id="completed-run",
        user_id="user-1",
        status="completed",
        result_meta={},
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        artifacts=[],
    )
    interrupted = SimpleNamespace(
        id="failed-run",
        user_id="user-1",
        status="failed",
        result_meta={"checkpoints": {"research": {"files": ["notes.md"]}}},
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        artifacts=[],
    )

    completed_descriptor = deep_research_storage.deep_research_run_cleanup_descriptor(
        completed
    )
    interrupted_descriptor = (
        deep_research_storage.deep_research_run_cleanup_descriptor(interrupted)
    )

    assert completed_descriptor["storage_provider"] == "local"
    assert interrupted_descriptor["storage_provider"] == "webdav"


@pytest.mark.parametrize("unsafe_provider", ["../webdav", "a/b", "a\\b"])
def test_deep_research_materialization_rejects_unsafe_provider(unsafe_provider):
    """Provider metadata cannot escape the Deep Research materialization root."""
    with pytest.raises(ValueError, match="storage_provider"):
        deep_research_storage.materialize_deep_research_artifact(
            "user-1",
            "run-1",
            "report.md",
            storage_provider=unsafe_provider,
        )


def test_slide_presentation_migration_updates_index_then_deletes_source(tmp_path):
    """Presentation assets move together and cleanup uses their manifest."""
    user_id = "user-1"
    presentation_id = "presentation-1"
    prefix = f"{user_id}/presentations/{presentation_id}"
    presentation_dir = tmp_path / prefix
    (presentation_dir / "images").mkdir(parents=True)
    (presentation_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (presentation_dir / "images" / "slide_1.png").write_bytes(b"slide")
    now = datetime.now(timezone.utc)
    presentation = SimpleNamespace(
        id=presentation_id,
        user_id=user_id,
        created_at=now,
        last_updated_at=now,
        slide_count=1,
        storage_provider="local",
        storage_prefix=prefix,
        storage_meta={},
    )
    db = FakeArtifactSession(presentation)
    source = LocalUserFileStorageAdapter(tmp_path)
    destination = MemoryStorageAdapter()

    stats = files_cli._migrate_presentation_records(
        db,
        source_provider="local",
        destination_provider="webdav",
        source_adapter=source,
        destination_adapter=destination,
        user_id=None,
        created_after=None,
        created_before=None,
        only_migrated_from=None,
        max_records=0,
        dry_run=False,
        delete_source=True,
        force=False,
        retries=1,
    )

    assert stats["migrated"] == 1
    assert stats["deleted_source_objects"] == 2
    assert presentation.storage_provider == "webdav"
    assert presentation.storage_meta["migration"]["source_provider"] == "local"
    assert not (presentation_dir / "metadata.json").exists()
    assert not (presentation_dir / "images" / "slide_1.png").exists()
    assert destination.objects[f"{prefix}/metadata.json"] == b"{}"
    assert destination.objects[f"{prefix}/images/slide_1.png"] == b"slide"


def test_migrate_files_rejects_conflicting_destination_without_force(
    monkeypatch,
    capsys,
):
    """An unrelated destination object must not be overwritten implicitly."""
    storage_key = "user-1/file-1.txt"
    file_row = _file_row("azure")
    source = MemoryStorageAdapter({storage_key: b"authoritative source"})
    destination = MemoryStorageAdapter({storage_key: b"different destination"})
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="webdav",
    )

    exit_code = files_cli._cmd_migrate_files(_args("azure", "webdav"))

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    error = json.loads(captured.err)
    assert exit_code == 1
    assert output["failed"] == 1
    assert "different content" in error["error"]
    assert destination.objects[storage_key] == b"different destination"
    assert destination.upload_calls == 0
    assert file_row.storage_provider == "azure"
    assert db.commits == 0


def test_migrate_files_deletes_source_only_after_database_commit(
    monkeypatch,
    capsys,
):
    """Requested cleanup happens after the durable reference points at target."""
    storage_key = "user-1/file-1.txt"
    file_row = _file_row("webdav")
    source = MemoryStorageAdapter({storage_key: b"move me"})
    destination = MemoryStorageAdapter()
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="local",
    )

    exit_code = files_cli._cmd_migrate_files(
        _args("webdav", "local", delete_source=True)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["deleted_source"] == 1
    assert db.commits == 1
    assert source.delete_calls == 1
    assert storage_key not in source.objects
    assert destination.objects[storage_key] == b"move me"


def test_migrate_files_reports_post_commit_source_cleanup_failure(
    monkeypatch,
    capsys,
):
    """A cleanup error is visible while the verified destination stays active."""
    storage_key = "user-1/file-1.txt"
    file_row = _file_row("s3")
    source = DeleteFailingStorageAdapter({storage_key: b"keep on failure"})
    destination = MemoryStorageAdapter()
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="azure",
    )

    exit_code = files_cli._cmd_migrate_files(
        _args("s3", "azure", delete_source=True, retries=2)
    )

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    cleanup_error = json.loads(captured.err)
    assert exit_code == 1
    assert output["migrated"] == 0
    assert output["source_cleanup_failed"] == 1
    assert cleanup_error["source_storage_key"] == storage_key
    assert cleanup_error["database_reference_reverted"] is True
    assert source.objects[storage_key] == b"keep on failure"
    assert source.delete_calls == 2
    assert destination.objects[storage_key] == b"keep on failure"
    assert file_row.storage_provider == "s3"
    assert db.commits == 2


def test_migrate_files_keeps_source_reference_when_record_changes(
    monkeypatch,
    capsys,
):
    """An optimistic database conflict leaves the source authoritative."""
    storage_key = "user-1/file-1.txt"
    file_row = _file_row("gcs")
    source = MemoryStorageAdapter({storage_key: b"concurrent update"})
    destination = MemoryStorageAdapter()
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="s3",
        update_count=0,
    )

    exit_code = files_cli._cmd_migrate_files(_args("gcs", "s3", delete_source=True))

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["failed"] == 1
    assert file_row.storage_provider == "gcs"
    assert source.objects[storage_key] == b"concurrent update"
    assert source.delete_calls == 0
    # The verified target is deliberately retained so a retry can resume.
    assert destination.objects[storage_key] == b"concurrent update"
    assert db.rollbacks >= 1


def test_migrate_files_dry_run_does_not_read_or_write_objects(
    monkeypatch,
    capsys,
):
    """Dry-run reports matching rows without touching provider contents."""
    file_row = _file_row("local")
    source = MemoryStorageAdapter()
    destination = MemoryStorageAdapter()
    db = _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="azure",
    )

    exit_code = files_cli._cmd_migrate_files(
        _args("local", "azure", dry_run=True, delete_source=True)
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["would_migrate"] == 1
    assert output["migrated"] == 0
    assert source.delete_calls == 0
    assert destination.upload_calls == 0
    assert db.commits == 0


def test_legacy_local_migration_command_uses_general_workflow(monkeypatch):
    """Existing automation keeps working through the generalized command."""
    args = _args("ignored", "ignored")
    captured = {}

    def fake_migrate_files(received_args):
        captured["from_provider"] = received_args.from_provider
        captured["to_provider"] = received_args.to_provider
        return 7

    monkeypatch.setattr(files_cli, "_cmd_migrate_files", fake_migrate_files)

    assert files_cli._cmd_migrate_local(args) == 7
    assert captured == {"from_provider": "local", "to_provider": None}


def test_storage_probe_returns_failure_for_error_diagnostics(monkeypatch, capsys):
    """WebDAV-style diagnostic errors must produce a failing process status."""
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_config",
        lambda: SimpleNamespace(
            provider="webdav",
            local_base_path=Path("/unused"),
            options={"url": "https://storage.example.invalid"},
        ),
    )
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_adapter",
        lambda: SimpleNamespace(
            test_connection=lambda: {
                "status": "error",
                "provider": "webdav",
                "error": "connection failed",
            }
        ),
    )

    exit_code = files_cli._cmd_storage_probe(SimpleNamespace())

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["probe"]["status"] == "error"


def test_migrate_files_defaults_destination_to_configured_provider(
    monkeypatch,
    capsys,
):
    """Omitting --to-provider follows the destination used for new uploads."""
    storage_key = "user-1/file-1.txt"
    file_row = _file_row("local")
    source = MemoryStorageAdapter({storage_key: b"configured target"})
    destination = MemoryStorageAdapter()
    _install_migration_fakes(
        monkeypatch,
        file_row=file_row,
        source_adapter=source,
        destination_adapter=destination,
        destination_provider="webdav",
    )
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_config",
        lambda: SimpleNamespace(provider="webdav"),
    )

    exit_code = files_cli._cmd_migrate_files(_args("local", None))

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["destination_provider"] == "webdav"
    assert destination.objects[storage_key] == b"configured target"


def test_migrate_files_can_select_only_rows_marked_from_a_provider(
    monkeypatch,
    capsys,
):
    """Round trips can exclude unrelated pre-existing destination rows."""
    selected = _file_row("webdav")
    selected.storage_meta = {
        "migration": {"source_provider": "local"},
    }
    unrelated = _file_row("webdav")
    unrelated.id = "unrelated"
    unrelated.storage_key = "user-1/unrelated.txt"

    source = MemoryStorageAdapter(
        {
            selected.storage_key: b"selected",
            unrelated.storage_key: b"unrelated",
        }
    )
    destination = MemoryStorageAdapter()
    db = FakeSession(selected)
    monkeypatch.setattr(files_cli, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        files_cli,
        "_iter_storage_records",
        lambda *args, **kwargs: iter([unrelated, selected]),
    )
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_adapter_for_provider",
        lambda provider: {
            "webdav": source,
            "local": destination,
        }[provider],
    )

    exit_code = files_cli._cmd_migrate_files(
        _args(
            "webdav",
            "local",
            only_migrated_from="local",
            delete_source=True,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["scanned"] == 1
    assert output["deleted_source"] == 1
    assert selected.storage_provider == "local"
    assert unrelated.storage_provider == "webdav"
    assert selected.storage_key in destination.objects
    assert unrelated.storage_key in source.objects


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"batch_size": 0}, "--batch-size"),
        ({"max_files": -1}, "--max-files"),
        ({"retries": 0}, "--retries"),
        (
            {"created_after": "2026-08-02", "created_before": "2026-08-01"},
            "--created-after",
        ),
    ],
)
def test_migrate_files_rejects_invalid_options_before_opening_database(
    monkeypatch,
    capsys,
    override,
    message,
):
    """Invalid bounds fail with usage status and no migration side effects."""
    monkeypatch.setattr(
        files_cli,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )
    monkeypatch.setattr(
        files_cli,
        "get_user_file_storage_adapter_for_provider",
        lambda provider: MemoryStorageAdapter(),
    )

    exit_code = files_cli._cmd_migrate_files(_args("local", "s3", **override))

    assert exit_code == 2
    assert message in capsys.readouterr().err
