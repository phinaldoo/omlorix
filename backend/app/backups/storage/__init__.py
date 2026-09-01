from __future__ import annotations

from pathlib import Path

from app.backups.storage.azure import AzureBlobBackupStorageAdapter
from app.backups.storage.base import BackupStorageAdapter
from app.backups.storage.gcs import GCSBackupStorageAdapter
from app.backups.storage.local import LocalBackupStorageAdapter
from app.backups.storage.s3 import S3BackupStorageAdapter
from app.backups.storage.webdav import WebDAVBackupStorageAdapter


def build_storage_adapter(provider: str, config: dict, *, default_local_dir: Path) -> BackupStorageAdapter:
    normalized = (provider or "").strip().lower()
    if normalized == "local":
        base_path = config.get("base_path") if isinstance(config, dict) else None
        return LocalBackupStorageAdapter(base_path=base_path or default_local_dir)
    if normalized == "s3":
        return S3BackupStorageAdapter(config=config)
    if normalized == "gcs":
        return GCSBackupStorageAdapter(config=config)
    if normalized == "azure":
        return AzureBlobBackupStorageAdapter(config=config)
    if normalized == "webdav":
        return WebDAVBackupStorageAdapter(config=config)
    raise ValueError(f"Unsupported backup provider '{provider}'")
