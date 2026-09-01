from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR


LOCAL_STORAGE_PROVIDER = "local"
SUPPORTED_STORAGE_PROVIDERS = {"local", "s3", "gcs", "azure", "webdav"}
DEFAULT_LOCAL_BASE_DIR = DATA_DIR / "userFiles"


@dataclass(frozen=True)
class UserFileStorageConfig:
    provider: str
    local_base_path: Path
    options: dict[str, Any]


def normalize_storage_provider(value: str | None) -> str:
    provider = (value or LOCAL_STORAGE_PROVIDER).strip().lower()
    if provider == "s3-compatible":
        provider = "s3"
    if provider not in SUPPORTED_STORAGE_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_STORAGE_PROVIDERS))
        raise RuntimeError(
            "Invalid FILE_STORAGE_PROVIDER value. "
            f"Got '{provider}'. Supported providers: {supported}."
        )
    return provider


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _parse_timeout(value: str, default: int = 30) -> int:
    """Parse timeout value from environment variable.

    Returns default if value is empty. Raises RuntimeError with clear message
    if value cannot be converted to integer or is out of valid range.
    """
    if not value:
        return default
    try:
        timeout = int(value)
        if timeout < 1 or timeout > 300:
            raise RuntimeError(
                f"FILE_STORAGE_WEBDAV_TIMEOUT must be between 1 and 300 seconds. Got '{value}'."
            )
        return timeout
    except ValueError:
        raise RuntimeError(
            f"FILE_STORAGE_WEBDAV_TIMEOUT must be an integer. Got '{value}'."
        )


def _parse_verify_ssl(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        "FILE_STORAGE_WEBDAV_VERIFY_SSL must be a boolean value "
        "(true/false, 1/0, yes/no, on/off)."
    )


def load_user_file_storage_config(provider: str | None = None) -> UserFileStorageConfig:
    resolved_provider = (
        normalize_storage_provider(provider)
        if provider is not None
        else normalize_storage_provider(os.getenv("FILE_STORAGE_PROVIDER"))
    )
    local_base_path_raw = _env("FILE_STORAGE_LOCAL_BASE_PATH")
    local_base_path = Path(local_base_path_raw) if local_base_path_raw else DEFAULT_LOCAL_BASE_DIR

    options: dict[str, Any] = {}
    if resolved_provider == "s3":
        bucket = _env("FILE_STORAGE_S3_BUCKET")
        if not bucket:
            raise RuntimeError(
                "File storage provider 's3' requires FILE_STORAGE_S3_BUCKET. "
                "Set FILE_STORAGE_S3_BUCKET=<bucket-name>."
            )
        options = {
            "bucket": bucket,
            "prefix": _env("FILE_STORAGE_S3_PREFIX"),
            "region": _env("FILE_STORAGE_S3_REGION"),
            "endpoint_url": _env("FILE_STORAGE_S3_ENDPOINT_URL"),
            "access_key_id": _env("FILE_STORAGE_S3_ACCESS_KEY_ID"),
            "secret_access_key": _env("FILE_STORAGE_S3_SECRET_ACCESS_KEY"),
            "session_token": _env("FILE_STORAGE_S3_SESSION_TOKEN"),
        }
    elif resolved_provider == "gcs":
        bucket = _env("FILE_STORAGE_GCS_BUCKET")
        if not bucket:
            raise RuntimeError(
                "File storage provider 'gcs' requires FILE_STORAGE_GCS_BUCKET. "
                "Set FILE_STORAGE_GCS_BUCKET=<bucket-name>."
            )
        options = {
            "bucket": bucket,
            "prefix": _env("FILE_STORAGE_GCS_PREFIX"),
            "project": _env("FILE_STORAGE_GCS_PROJECT"),
            "credentials_json": _env("FILE_STORAGE_GCS_CREDENTIALS_JSON"),
        }
    elif resolved_provider == "azure":
        container = _env("FILE_STORAGE_AZURE_CONTAINER")
        if not container:
            raise RuntimeError(
                "File storage provider 'azure' requires FILE_STORAGE_AZURE_CONTAINER. "
                "Set FILE_STORAGE_AZURE_CONTAINER=<container-name>."
            )
        options = {
            "container": container,
            "prefix": _env("FILE_STORAGE_AZURE_PREFIX"),
            "connection_string": _env("FILE_STORAGE_AZURE_CONNECTION_STRING"),
            "account_url": _env("FILE_STORAGE_AZURE_ACCOUNT_URL"),
            "credential": _env("FILE_STORAGE_AZURE_CREDENTIAL"),
        }
    elif resolved_provider == "webdav":
        url = _env("FILE_STORAGE_WEBDAV_URL")
        if not url:
            raise RuntimeError(
                "File storage provider 'webdav' requires FILE_STORAGE_WEBDAV_URL. "
                "Set FILE_STORAGE_WEBDAV_URL=https://your-nas.com:5006/webdav/"
            )
        options = {
            "url": url,
            "username": _env("FILE_STORAGE_WEBDAV_USERNAME"),
            "password": _env("FILE_STORAGE_WEBDAV_PASSWORD"),
            "prefix": _env("FILE_STORAGE_WEBDAV_PREFIX"),
            "verify_ssl": _parse_verify_ssl(_env("FILE_STORAGE_WEBDAV_VERIFY_SSL")),
            "timeout": _parse_timeout(_env("FILE_STORAGE_WEBDAV_TIMEOUT"), 30),
        }

    return UserFileStorageConfig(
        provider=resolved_provider,
        local_base_path=local_base_path,
        options=options,
    )
