from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
from typing import Any

from app.files.storage.azure import AzureUserFileStorageAdapter
from app.files.storage.base import UserFileStorageAdapter
from app.files.storage.config import (
    DEFAULT_LOCAL_BASE_DIR,
    LOCAL_STORAGE_PROVIDER,
    UserFileStorageConfig,
    load_user_file_storage_config,
    normalize_storage_provider,
)
from app.files.storage.gcs import GCSUserFileStorageAdapter
from app.files.storage.local import LocalUserFileStorageAdapter
from app.files.storage.paths import (
    build_storage_prefix,
    ensure_user_scoped_storage_key,
    normalize_storage_component,
    normalize_storage_key,
    resolve_local_storage_path,
)
from app.files.storage.s3 import S3UserFileStorageAdapter
from app.files.storage.webdav import WebDAVUserFileStorageAdapter


def build_user_file_storage_adapter(config: UserFileStorageConfig) -> UserFileStorageAdapter:
    if config.provider == LOCAL_STORAGE_PROVIDER:
        return LocalUserFileStorageAdapter(base_path=config.local_base_path)
    if config.provider == "s3":
        return S3UserFileStorageAdapter(config.options)
    if config.provider == "gcs":
        return GCSUserFileStorageAdapter(config.options)
    if config.provider == "azure":
        return AzureUserFileStorageAdapter(config.options)
    if config.provider == "webdav":
        return WebDAVUserFileStorageAdapter(config.options)
    raise RuntimeError(f"Unsupported file storage provider: {config.provider}")


@lru_cache(maxsize=1)
def get_user_file_storage_config() -> UserFileStorageConfig:
    return load_user_file_storage_config()


@lru_cache(maxsize=1)
def get_user_file_storage_adapter() -> UserFileStorageAdapter:
    config = get_user_file_storage_config()
    return build_user_file_storage_adapter(config)


@lru_cache(maxsize=8)
def get_user_file_storage_config_for_provider(provider: str) -> UserFileStorageConfig:
    normalized = normalize_storage_provider(provider)
    return load_user_file_storage_config(normalized)


@lru_cache(maxsize=8)
def get_user_file_storage_adapter_for_provider(provider: str) -> UserFileStorageAdapter:
    config = get_user_file_storage_config_for_provider(provider)
    return build_user_file_storage_adapter(config)


def get_local_user_files_base_dir() -> Path:
    config = get_user_file_storage_config()
    base_path_raw = (os.getenv("FILE_STORAGE_LOCAL_BASE_PATH") or "").strip()
    if base_path_raw:
        return Path(base_path_raw)
    if config.provider == LOCAL_STORAGE_PROVIDER:
        return config.local_base_path
    return DEFAULT_LOCAL_BASE_DIR


def build_storage_key(user_id: str, file_name: str) -> str:
    safe_user = build_storage_prefix(user_id)
    safe_file = normalize_storage_component(file_name, field_name="file_name")
    return normalize_storage_key(f"{safe_user}/{safe_file}")


def upload_file_to_storage(local_path: Path, user_id: str, file_name: str) -> tuple[str, str, dict[str, Any]]:
    config = get_user_file_storage_config()
    adapter = get_user_file_storage_adapter()
    storage_key = build_storage_key(user_id, file_name)
    upload_meta = adapter.upload_file(local_path, storage_key) or {}
    return config.provider, storage_key, upload_meta


def download_file_from_storage(storage_provider: str, storage_key: str, target_path: Path) -> Path:
    adapter = get_user_file_storage_adapter_for_provider(storage_provider)
    return adapter.download_file(storage_key, target_path)


def delete_file_from_storage(storage_provider: str, storage_key: str) -> None:
    adapter = get_user_file_storage_adapter_for_provider(storage_provider)
    adapter.delete_file(storage_key)
