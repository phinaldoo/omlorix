from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from app.files.storage.base import UserFileStorageAdapter


class GCSUserFileStorageAdapter(UserFileStorageAdapter):
    def __init__(self, config: dict[str, Any]):
        self.bucket_name = (config.get("bucket") or "").strip()
        if not self.bucket_name:
            raise ValueError("GCS file storage requires a bucket")

        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            from google.cloud import storage
            from google.oauth2 import service_account
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("google-cloud-storage is required for GCS file storage") from exc

        credentials_json = config.get("credentials_json")
        credentials = None
        if credentials_json:
            if isinstance(credentials_json, str):
                credentials_info = json.loads(credentials_json)
            elif isinstance(credentials_json, dict):
                credentials_info = credentials_json
            else:
                raise ValueError("Invalid GCS credentials_json value")
            credentials = service_account.Credentials.from_service_account_info(credentials_info)

        self.client = storage.Client(project=config.get("project") or None, credentials=credentials)
        self.bucket = self.client.bucket(self.bucket_name)

    def _key(self, storage_key: str) -> str:
        normalized = str(storage_key).strip().lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{normalized}" if normalized else self.prefix
        return normalized

    def upload_file(self, local_path: Path, storage_key: str) -> dict[str, Any]:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Upload source file not found: {source}")
        key = self._key(storage_key)
        blob = self.bucket.blob(key)
        blob.upload_from_filename(str(source))
        return {
            "size_bytes": int(blob.size or source.stat().st_size),
            "etag": str(blob.etag or ""),
            "key": key,
        }

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        key = self._key(storage_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = self.bucket.blob(key)
        blob.download_to_filename(str(target))
        return target

    def delete_file(self, storage_key: str) -> None:
        key = self._key(storage_key)
        blob = self.bucket.blob(key)
        blob.delete()

    def exists(self, storage_key: str) -> bool:
        key = self._key(storage_key)
        blob = self.bucket.blob(key)
        return bool(blob.exists())

    def test_connection(self) -> dict[str, Any]:
        probe_key = self._key(
            f"_probe/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        blob = self.bucket.blob(probe_key)
        blob.upload_from_string("omlorix-user-file-storage-probe")
        content = blob.download_as_text()
        blob.delete()
        return {
            "status": "ok",
            "provider": "gcs",
            "bucket": self.bucket_name,
            "probe_content": content,
        }
