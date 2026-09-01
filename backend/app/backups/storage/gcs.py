from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from app.backups.storage.base import BackupStorageAdapter


class GCSBackupStorageAdapter(BackupStorageAdapter):
    def __init__(self, config: dict):
        self.bucket_name = (config.get("bucket") or "").strip()
        if not self.bucket_name:
            raise ValueError("GCS backup config requires 'bucket'")

        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            from google.cloud import storage
            from google.oauth2 import service_account
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("google-cloud-storage is required for GCS backup destinations") from exc

        credentials_json = config.get("credentials_json")
        credentials = None
        if credentials_json:
            if isinstance(credentials_json, str):
                credentials_info = json.loads(credentials_json)
            elif isinstance(credentials_json, dict):
                credentials_info = credentials_json
            else:
                raise ValueError("Invalid GCS credentials_json")
            credentials = service_account.Credentials.from_service_account_info(credentials_info)

        self.client = storage.Client(project=config.get("project") or None, credentials=credentials)
        self.bucket = self.client.bucket(self.bucket_name)

    def _key(self, remote_path: str) -> str:
        normalized = str(remote_path).strip().lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{normalized}" if normalized else self.prefix
        return normalized

    def _parse_uri(self, storage_uri: str) -> str:
        if not storage_uri.startswith("gs://"):
            raise ValueError("Invalid GCS storage URI")
        body = storage_uri[len("gs://") :]
        if "/" not in body:
            raise ValueError("Invalid GCS storage URI")
        bucket, key = body.split("/", 1)
        if bucket != self.bucket_name:
            raise ValueError("GCS URI bucket does not match destination bucket")
        return key

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Local backup source file not found: {source}")
        key = self._key(remote_path)
        blob = self.bucket.blob(key)
        blob.upload_from_filename(str(source))
        return f"gs://{self.bucket_name}/{key}"

    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        key = self._parse_uri(storage_uri)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = self.bucket.blob(key)
        blob.download_to_filename(str(target))
        return target

    def delete_file(self, storage_uri: str) -> None:
        key = self._parse_uri(storage_uri)
        blob = self.bucket.blob(key)
        blob.delete()

    def test_connection(self) -> dict:
        probe_key = self._key(
            f"_probe/probe-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        blob = self.bucket.blob(probe_key)
        blob.upload_from_string("omlorix-backup-probe")
        content = blob.download_as_text()
        blob.delete()
        return {
            "status": "ok",
            "provider": "gcs",
            "bucket": self.bucket_name,
            "probe_content": content,
        }
