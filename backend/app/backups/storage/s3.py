from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from app.backups.storage.base import BackupStorageAdapter


class S3BackupStorageAdapter(BackupStorageAdapter):
    def __init__(self, config: dict):
        self.bucket = (config.get("bucket") or "").strip()
        if not self.bucket:
            raise ValueError("S3 backup config requires 'bucket'")

        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            import boto3
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("boto3 is required for S3 backup destinations") from exc

        session = boto3.session.Session()
        self.client = session.client(
            "s3",
            region_name=config.get("region") or None,
            endpoint_url=config.get("endpoint_url") or None,
            aws_access_key_id=config.get("access_key_id") or None,
            aws_secret_access_key=config.get("secret_access_key") or None,
            aws_session_token=config.get("session_token") or None,
        )

    def _key(self, remote_path: str) -> str:
        normalized = str(remote_path).strip().lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{normalized}" if normalized else self.prefix
        return normalized

    def _parse_uri(self, storage_uri: str) -> str:
        if not storage_uri.startswith("s3://"):
            raise ValueError("Invalid S3 storage URI")
        body = storage_uri[len("s3://") :]
        if "/" not in body:
            raise ValueError("Invalid S3 storage URI")
        bucket, key = body.split("/", 1)
        if bucket != self.bucket:
            raise ValueError("S3 URI bucket does not match destination bucket")
        return key

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Local backup source file not found: {source}")
        key = self._key(remote_path)
        self.client.upload_file(str(source), self.bucket, key)
        return f"s3://{self.bucket}/{key}"

    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        key = self._parse_uri(storage_uri)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(target))
        return target

    def delete_file(self, storage_uri: str) -> None:
        key = self._parse_uri(storage_uri)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def test_connection(self) -> dict:
        probe_key = self._key(
            f"_probe/probe-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        body = b"omlorix-backup-probe"
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
