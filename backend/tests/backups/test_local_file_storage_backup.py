from pathlib import Path
from types import SimpleNamespace
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.backups import service as backup_service  # noqa: E402


def test_directory_tar_skips_its_own_nested_output(tmp_path):
    data_dir = tmp_path / "data"
    output_tar = data_dir / "backups" / "staging" / "job-1" / "app-data.tar"
    data_dir.mkdir()
    (data_dir / "ordinary.txt").write_text("data", encoding="utf-8")

    backup_service._create_directory_tar(data_dir, output_tar)

    with tarfile.open(output_tar, mode="r") as archive:
        members = [member.name for member in archive.getmembers()]

    assert members == ["ordinary.txt"]


def test_backup_components_exclude_nested_backup_work_tree(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backups"
    staging_dir = backup_dir / "staging" / "job-1"
    log_dir = tmp_path / "logs"
    (backup_dir / "archives").mkdir(parents=True)
    log_dir.mkdir()
    (data_dir / "ordinary.txt").write_text("data", encoding="utf-8")
    (backup_dir / "archives" / "old-backup.tar.zst").write_text("old", encoding="utf-8")
    (log_dir / "server.log").write_text("log", encoding="utf-8")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUP_LOCAL_DIR", backup_dir)
    monkeypatch.setattr(backup_service, "BACKUP_STAGING_DIR", backup_dir / "staging")
    monkeypatch.setattr(backup_service, "BACKUP_ARCHIVE_DIR", backup_dir / "archives")
    monkeypatch.setattr(backup_service, "BACKUP_DOWNLOAD_CACHE_DIR", backup_dir / "download-cache")
    monkeypatch.setattr(backup_service, "LOG_DIR", log_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(data_dir / "userFiles"))

    def fake_dump_database(_config, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("dump", encoding="utf-8")

    monkeypatch.setattr(backup_service, "_dump_database", fake_dump_database)
    monkeypatch.setattr(backup_service, "_create_crypto_probe", lambda _staging_dir: None)

    components = backup_service._create_backup_components(staging_dir)

    with tarfile.open(components["app_data_tar"], mode="r") as archive:
        members = [member.name for member in archive.getmembers()]

    assert members == ["ordinary.txt"]


def test_app_data_tar_includes_external_local_file_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "app" / "app" / "data"
    external_user_files = tmp_path / "app" / "data" / "userFiles"
    data_dir.mkdir(parents=True)
    external_user_files.mkdir(parents=True)
    (data_dir / "inside-data-dir.txt").write_text("data", encoding="utf-8")
    (data_dir / "userFiles").mkdir()
    (data_dir / "userFiles" / "stale.txt").write_text("stale", encoding="utf-8")
    (external_user_files / "user-1").mkdir()
    (external_user_files / "user-1" / "upload.txt").write_text("upload", encoding="utf-8")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(external_user_files))

    external_source = backup_service._external_local_file_storage_backup_source()
    assert external_source == ("userFiles", external_user_files)

    tar_path = tmp_path / "app_data.tar"
    backup_service._create_directory_tar(data_dir, tar_path, extra_sources=dict([external_source]))

    with tarfile.open(tar_path, mode="r") as archive:
        members = sorted(member.name for member in archive.getmembers())

    assert members == ["inside-data-dir.txt", "userFiles", "userFiles/user-1/upload.txt"]


def test_restore_reconstructs_external_local_file_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "app" / "app" / "data"
    external_user_files = tmp_path / "app" / "data" / "userFiles"
    restored_user_dir = data_dir / "userFiles" / "user-1"
    restored_user_dir.mkdir(parents=True)
    (restored_user_dir / "upload.txt").write_text("restored", encoding="utf-8")
    external_user_files.mkdir(parents=True)
    (external_user_files / "old.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(external_user_files))

    rollback_dir = backup_service._restore_external_local_file_storage_from_data_dir("restore1")

    assert (external_user_files / "user-1" / "upload.txt").read_text(encoding="utf-8") == "restored"
    assert not (external_user_files / "old.txt").exists()
    assert rollback_dir is not None
    assert (rollback_dir / "old.txt").read_text(encoding="utf-8") == "old"


def test_app_data_tar_marks_empty_external_local_file_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "app" / "app" / "data"
    external_user_files = tmp_path / "app" / "data" / "userFiles"
    data_dir.mkdir(parents=True)
    external_user_files.mkdir(parents=True)

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(external_user_files))

    external_source = backup_service._external_local_file_storage_backup_source()
    assert external_source == ("userFiles", external_user_files)

    tar_path = tmp_path / "app_data.tar"
    backup_service._create_directory_tar(data_dir, tar_path, extra_sources=dict([external_source]))

    with tarfile.open(tar_path, mode="r") as archive:
        members = archive.getmembers()

    assert [member.name for member in members] == ["userFiles"]
    assert members[0].isdir()


def test_restore_skips_external_local_file_storage_when_backup_has_no_user_files(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "app" / "app" / "data"
    external_user_files = tmp_path / "app" / "data" / "userFiles"
    data_dir.mkdir(parents=True)
    external_user_files.mkdir(parents=True)
    (external_user_files / "old.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(external_user_files))

    rollback_dir = backup_service._restore_external_local_file_storage_from_data_dir("restore1")

    assert rollback_dir is None
    assert (external_user_files / "old.txt").read_text(encoding="utf-8") == "old"


def test_restore_empty_external_local_file_storage_payload_clears_existing_files(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "app" / "app" / "data"
    external_user_files = tmp_path / "app" / "data" / "userFiles"
    (data_dir / "userFiles").mkdir(parents=True)
    external_user_files.mkdir(parents=True)
    (external_user_files / "old.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setenv("FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("FILE_STORAGE_LOCAL_BASE_PATH", str(external_user_files))

    rollback_dir = backup_service._restore_external_local_file_storage_from_data_dir("restore1")

    assert not (external_user_files / "old.txt").exists()
    assert rollback_dir is not None
    assert (rollback_dir / "old.txt").read_text(encoding="utf-8") == "old"
