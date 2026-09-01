from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any
import uuid

from app.files.storage.base import UserFileStorageAdapter
from app.files.storage.paths import resolve_local_storage_path


class LocalUserFileStorageAdapter(UserFileStorageAdapter):
    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_target(self, storage_key: str) -> Path:
        return resolve_local_storage_path(self.base_path, storage_key, create_parent=True)

    def upload_file(self, local_path: Path, storage_key: str) -> dict[str, Any]:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Upload source file not found: {source}")
        target = self._resolve_target(storage_key)
        if source.resolve() == target.resolve():
            size_bytes = source.stat().st_size
            return {"size_bytes": size_bytes}
        shutil.copy2(source, target)
        size_bytes = target.stat().st_size
        return {"size_bytes": size_bytes}

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        source = self._resolve_target(storage_key)
        if not source.exists():
            raise FileNotFoundError(f"File not found: {source}")
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def delete_file(self, storage_key: str) -> None:
        target = self._resolve_target(storage_key)
        if target.exists():
            target.unlink()

    def exists(self, storage_key: str) -> bool:
        return self._resolve_target(storage_key).exists()

    def test_connection(self) -> dict[str, Any]:
        key = f"_probe/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        target = self._resolve_target(key)
        content = "omlorix-user-file-storage-probe"
        target.write_text(content, encoding="utf-8")
        read_back = target.read_text(encoding="utf-8")
        target.unlink(missing_ok=True)
        return {
            "status": "ok",
            "provider": "local",
            "base_path": str(self.base_path),
            "probe_content": read_back,
        }
