from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from app.files.storage.base import UserFileStorageAdapter


class S3UserFileStorageAdapter(UserFileStorageAdapter):
    def __init__(self, config: dict[str, Any]):
        self.bucket = (config.get("bucket") or "").strip()
        if not self.bucket:
            raise ValueError("S3 file storage requires a bucket")
        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            import boto3
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("boto3 is required for S3 file storage") from exc

        session = boto3.session.Session()
        self.client = session.client(
            "s3",
            region_name=config.get("region") or None,
            endpoint_url=config.get("endpoint_url") or None,
            aws_access_key_id=config.get("access_key_id") or None,
            aws_secret_access_key=config.get("secret_access_key") or None,
            aws_session_token=config.get("session_token") or None,
        )

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
        self.client.upload_file(str(source), self.bucket, key)
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return {
            "size_bytes": int(head.get("ContentLength") or source.stat().st_size),
            "etag": str(head.get("ETag") or "").strip('"') or None,
            "key": key,
        }

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        key = self._key(storage_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(target))
        return target

    def delete_file(self, storage_key: str) -> None:
        key = self._key(storage_key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, storage_key: str) -> bool:
        key = self._key(storage_key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def test_connection(self) -> dict[str, Any]:
        probe_key = self._key(
            f"_probe/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        body = b"omlorix-user-file-storage-probe"
        self.client.put_object(Bucket=self.bucket, Key=probe_key, Body=body)
        read_obj = self.client.get_object(Bucket=self.bucket, Key=probe_key)
        content = read_obj["Body"].read().decode("utf-8", errors="replace")
        self.client.delete_object(Bucket=self.bucket, Key=probe_key)
        return {
            "status": "ok",
            "provider": "s3",
            "bucket": self.bucket,
            "probe_content": content,
        }
