from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid

from app.backups.storage.base import BackupStorageAdapter


class LocalBackupStorageAdapter(BackupStorageAdapter):
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _to_absolute(self, remote_path: str) -> Path:
        normalized = str(remote_path).strip().lstrip("/")
        target = (self.base_path / normalized).resolve()
        if self.base_path.resolve() not in target.parents and target != self.base_path.resolve():
            raise ValueError("Invalid local backup remote path")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _from_uri(self, storage_uri: str) -> Path:
        if not storage_uri.startswith("local://"):
            raise ValueError("Invalid local storage URI")
        relative = storage_uri[len("local://") :].lstrip("/")
        return self._to_absolute(relative)

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Local backup source file not found: {source}")
        destination = self._to_absolute(remote_path)
        shutil.copy2(source, destination)
        relative = destination.relative_to(self.base_path).as_posix()
        return f"local://{relative}"

    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        source = self._from_uri(storage_uri)
        if not source.exists():
            raise FileNotFoundError(f"Local backup artifact not found: {source}")
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def delete_file(self, storage_uri: str) -> None:
        target = self._from_uri(storage_uri)
        if target.exists():
            target.unlink()

    def test_connection(self) -> dict:
        probe_name = f"probe-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        probe_rel_path = f"_probe/{probe_name}"
        probe_dst = self._to_absolute(probe_rel_path)
        probe_dst.write_text("omlorix-backup-probe", encoding="utf-8")
        content = probe_dst.read_text(encoding="utf-8")
        probe_dst.unlink(missing_ok=True)
        return {
            "status": "ok",
            "provider": "local",
            "base_path": str(self.base_path),
            "probe_content": content,
        }
