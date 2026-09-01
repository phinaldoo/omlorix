from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from app.backups.storage.base import BackupStorageAdapter


class WebDAVBackupStorageAdapter(BackupStorageAdapter):
    """WebDAV storage adapter for backups on WebDAV servers."""

    def __init__(self, config: dict):
        self.url = (config.get("url") or "").strip()
        if not self.url:
            raise ValueError("WebDAV backup config requires 'url'")
        
        self.username = config.get("username") or ""
        self.password = config.get("password") or ""
        self.prefix = str(config.get("prefix") or "").strip().strip("/")
        self.verify_ssl = config.get("verify_ssl", True)
        self.timeout = config.get("timeout", 30)

        try:
            from webdav3.client import Client
        except ImportError as exc:
            raise RuntimeError("webdavclient3 is required for WebDAV backup destinations") from exc

        options = {
            "webdav_hostname": self.url,
            "webdav_login": self.username,
            "webdav_password": self.password,
            "webdav_timeout": self.timeout,
            "disable_check": not self.verify_ssl,
            "verify": self.verify_ssl,
        }
        
        self.client = Client(options)

        # webdavclient3 passes ``Client.verify`` directly to every Requests
        # call. Setting only ``session.verify`` is therefore insufficient:
        # the explicit per-request value takes precedence over the session
        # default. Keep both values aligned so ``verify_ssl`` is effective
        # with the currently pinned client and remains clear to future
        # maintainers if the library changes its request implementation.
        self.client.verify = self.verify_ssl
        if hasattr(self.client, 'session'):
            self.client.session.verify = self.verify_ssl

    def _key(self, remote_path: str) -> str:
        """Convert remote path to WebDAV path with optional prefix."""
        normalized = str(remote_path).strip().lstrip("/")
        parts = [self.prefix, normalized] if self.prefix else [normalized]
        parts = [p for p in parts if p]
        joined = "/".join(parts)
        return f"/{joined}" if joined else "/"

    def _ensure_parent_dirs(self, key: str) -> None:
        """Recursively create parent directories for the given key."""
        parent_path = Path(key).parent
        parts_to_create = []
        current = parent_path
        while str(current) not in ("", "/", "."):
            parts_to_create.append(str(current))
            current = current.parent
        for part in reversed(parts_to_create):
            try:
                self.client.mkdir(part)
            except Exception as exc:
                # Ignore "already exists" errors (405 Method Not Allowed from some servers)
                # TODO: Improve to structured exception handling using webdav3 library
                # specific exception types (e.g., ResponseErrorCode) when available
                error_msg = str(exc).lower()
                if "already exists" not in error_msg and "405" not in error_msg:
                    raise

    def _parse_uri(self, storage_uri: str) -> str:
        """Parse WebDAV URI and extract the path."""
        if not storage_uri.startswith("webdav://"):
            raise ValueError("Invalid WebDAV storage URI")
        body = storage_uri[len("webdav://") :]
        if not body:
            raise ValueError("Invalid WebDAV storage URI")
        return body

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        """Upload a local file to WebDAV server."""
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Local backup source file not found: {source}")
        
        key = self._key(remote_path)
        self._ensure_parent_dirs(key)
        
        # Upload file
        self.client.upload_sync(remote_path=key, local_path=str(source))
        
        return f"webdav://{key.lstrip('/')}"

    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        """Download a file from WebDAV server."""
        key = self._parse_uri(storage_uri)
        key = f"/{key}" if not key.startswith("/") else key
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        
        self.client.download_sync(remote_path=key, local_path=str(target))
        return target

    def delete_file(self, storage_uri: str) -> None:
        """Delete a file from WebDAV server."""
        key = self._parse_uri(storage_uri)
        key = f"/{key}" if not key.startswith("/") else key
        try:
            self.client.clean(key)
        except Exception as exc:
            # Only ignore "not found" errors; re-raise others
            error_msg = str(exc).lower()
            if "not found" not in error_msg and "404" not in error_msg:
                raise

    def test_connection(self) -> dict:
        """Test WebDAV connection by uploading, reading, and deleting a probe file."""
        probe_filename = f"_probe/probe-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        probe_key = self._key(probe_filename)
        content = "omlorix-backup-probe"
        
        # Create probe directory recursively
        self._ensure_parent_dirs(probe_key)

        # Upload probe file
        import tempfile
        download_temp_path = None
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write(content)
            temp_path = f.name
        
        try:
            self.client.upload_sync(remote_path=probe_key, local_path=temp_path)
            
            # Download and verify
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                download_temp_path = f.name
            
            self.client.download_sync(remote_path=probe_key, local_path=download_temp_path)
            read_back = Path(download_temp_path).read_text(encoding="utf-8")
            
            # Verify the downloaded content matches the original
            if read_back != content:
                raise RuntimeError(f"Probe content mismatch: expected '{content}', got '{read_back}'")
            
            # Clean up remote probe file
            self.client.clean(probe_key)
            
            return {
                "status": "ok",
                "provider": "webdav",
                "url": self.url,
                "probe_content": read_back,
            }
        finally:
            Path(temp_path).unlink(missing_ok=True)
            if download_temp_path:
                Path(download_temp_path).unlink(missing_ok=True)
