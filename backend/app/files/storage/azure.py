from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from app.files.storage.base import UserFileStorageAdapter


class AzureUserFileStorageAdapter(UserFileStorageAdapter):
    def __init__(self, config: dict[str, Any]):
        self.container_name = (config.get("container") or "").strip()
        if not self.container_name:
            raise ValueError("Azure file storage requires a container")
        self.prefix = str(config.get("prefix") or "").strip().strip("/")

        try:
            from azure.storage.blob import BlobServiceClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("azure-storage-blob is required for Azure file storage") from exc

        connection_string = config.get("connection_string")
        account_url = config.get("account_url")
        credential = config.get("credential")
        if connection_string:
            service_client = BlobServiceClient.from_connection_string(connection_string)
        else:
            if not account_url:
                raise ValueError(
                    "Azure file storage requires either FILE_STORAGE_AZURE_CONNECTION_STRING or FILE_STORAGE_AZURE_ACCOUNT_URL"
                )
            service_client = BlobServiceClient(account_url=account_url, credential=credential or None)
        self.container_client = service_client.get_container_client(self.container_name)

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
        blob = self.container_client.get_blob_client(key)
        with source.open("rb") as handle:
            blob.upload_blob(handle, overwrite=True)
        props = blob.get_blob_properties()
        return {
            "size_bytes": int(getattr(props, "size", source.stat().st_size)),
            "etag": str(getattr(props, "etag", "") or ""),
            "key": key,
        }

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        key = self._key(storage_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = self.container_client.get_blob_client(key)
        stream = blob.download_blob()
        with target.open("wb") as handle:
            for chunk in stream.chunks():
                if chunk:
                    handle.write(chunk)
        return target

    def delete_file(self, storage_key: str) -> None:
        key = self._key(storage_key)
        blob = self.container_client.get_blob_client(key)
        blob.delete_blob()

    def exists(self, storage_key: str) -> bool:
        key = self._key(storage_key)
        blob = self.container_client.get_blob_client(key)
        return bool(blob.exists())

    def test_connection(self) -> dict[str, Any]:
        probe_key = self._key(
            f"_probe/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        )
        blob = self.container_client.get_blob_client(probe_key)
        blob.upload_blob(b"omlorix-user-file-storage-probe", overwrite=True)
        content = blob.download_blob().readall().decode("utf-8", errors="replace")
        blob.delete_blob()
        return {
            "status": "ok",
            "provider": "azure",
            "container": self.container_name,
            "probe_content": content,
        }
