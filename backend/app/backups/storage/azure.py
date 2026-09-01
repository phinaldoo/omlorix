from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from app.backups.storage.base import BackupStorageAdapter


class AzureBlobBackupStorageAdapter(BackupStorageAdapter):
    def __init__(self, config: dict):
        self.container_name = (config.get("container") or "").strip()
        if not self.container_name:
            raise ValueError("Azure backup config requires 'container'")

        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            from azure.storage.blob import BlobServiceClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("azure-storage-blob is required for Azure backup destinations") from exc

        connection_string = config.get("connection_string")
        account_url = config.get("account_url")
        credential = config.get("credential")

        if connection_string:
            service_client = BlobServiceClient.from_connection_string(connection_string)
        else:
            if not account_url:
                raise ValueError("Azure backup config requires either 'connection_string' or 'account_url'")
            service_client = BlobServiceClient(account_url=account_url, credential=credential)

        self.container_client = service_client.get_container_client(self.container_name)

    def _key(self, remote_path: str) -> str:
        normalized = str(remote_path).strip().lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{normalized}" if normalized else self.prefix
        return normalized

    def _parse_uri(self, storage_uri: str) -> str:
        if not storage_uri.startswith("azure://"):
            raise ValueError("Invalid Azure storage URI")
        body = storage_uri[len("azure://") :]
        if "/" not in body:
            raise ValueError("Invalid Azure storage URI")
        container, key = body.split("/", 1)
        if container != self.container_name:
            raise ValueError("Azure URI container does not match destination container")
        return key

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Local backup source file not found: {source}")
        key = self._key(remote_path)
        blob = self.container_client.get_blob_client(key)
        with source.open("rb") as handle:
            blob.upload_blob(handle, overwrite=True)
        return f"azure://{self.container_name}/{key}"

    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        key = self._parse_uri(storage_uri)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = self.container_client.get_blob_client(key)
        with target.open("wb") as handle:
            data = blob.download_blob()
            handle.write(data.readall())
        return target

    def delete_file(self, storage_uri: str) -> None:
        key = self._parse_uri(storage_uri)
        blob = self.container_client.get_blob_client(key)
        blob.delete_blob()

    def test_connection(self) -> dict:
        probe_key = self._key(
            f"_probe/probe-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        blob = self.container_client.get_blob_client(probe_key)
        blob.upload_blob(b"omlorix-backup-probe", overwrite=True)
        content = blob.download_blob().readall().decode("utf-8", errors="replace")
        blob.delete_blob()
        return {
            "status": "ok",
            "provider": "azure",
            "container": self.container_name,
            "probe_content": content,
        }
