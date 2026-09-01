from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from app.files.storage.base import UserFileStorageAdapter


class WebDAVUserFileStorageAdapter(UserFileStorageAdapter):
    """WebDAV storage adapter for WebDAV servers."""

    def __init__(self, config: dict[str, Any]):
        self.url = (config.get("url") or "").strip()
        if not self.url:
            raise ValueError("WebDAV file storage requires a URL")

        self.username = config.get("username") or ""
        self.password = config.get("password") or ""
        self.prefix = str(config.get("prefix") or "").strip().strip("/")
        self.verify_ssl = config.get("verify_ssl", True)
        self.timeout = config.get("timeout", 30)
        self.logger = logging.getLogger(__name__)

        try:
            from webdav3.client import Client, Urn
        except ImportError as exc:
            raise RuntimeError(
                "webdavclient3 is required for WebDAV file storage"
            ) from exc

        self._urn_type = Urn

        options = {
            "webdav_hostname": self.url,
            "webdav_login": self.username,
            "webdav_password": self.password,
            "webdav_timeout": self.timeout,
            # Some WebDAV servers reject the library's parent-directory probe
            # with 403 even though MKCOL/PUT is allowed. Directory creation is
            # handled by this adapter, while exists() below uses an explicit
            # HEAD request for reliable file checks.
            "disable_check": True,
            "verify": self.verify_ssl,
        }

        self.client = Client(options)

        # webdavclient3 passes ``Client.verify`` explicitly to each Requests
        # call. Setting only the session default is insufficient because the
        # explicit per-request value takes precedence over that default.
        # Keep both values aligned so FILE_STORAGE_WEBDAV_VERIFY_SSL is
        # effective with the installed client and remains clear to future
        # maintainers if the library changes its request implementation.
        self.client.verify = self.verify_ssl
        if hasattr(self.client, "session"):
            self.client.session.verify = self.verify_ssl

    def _key(self, storage_key: str) -> str:
        """Convert storage key to WebDAV path with optional prefix."""
        normalized = str(storage_key).strip().lstrip("/")
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
                    self.logger.exception(f"Failed to create directory {part}: {exc}")
                    raise

    def upload_file(self, local_path: Path, storage_key: str) -> dict[str, Any]:
        """Upload a local file to WebDAV server."""
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"Upload source file not found: {source}")

        key = self._key(storage_key)
        self._ensure_parent_dirs(key)

        # Upload file
        self.client.upload_sync(remote_path=key, local_path=str(source))

        # Get file info
        size_bytes = source.stat().st_size
        return {
            "size_bytes": size_bytes,
            "key": key,
        }

    def download_file(self, storage_key: str, target_path: Path) -> Path:
        """Download a file from WebDAV server."""
        key = self._key(storage_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        self.client.download_sync(remote_path=key, local_path=str(target))
        return target

    def delete_file(self, storage_key: str) -> None:
        """Delete a file from WebDAV server."""
        key = self._key(storage_key)
        try:
            self.client.clean(key)
        except Exception as exc:
            # Only ignore "not found" errors; re-raise others
            error_msg = str(exc).lower()
            if "not found" not in error_msg and "404" not in error_msg:
                raise

    def exists(self, storage_key: str) -> bool:
        """Check if a file exists on WebDAV server.

        ``webdavclient3.Client.check`` is intentionally not used here. Its
        ``disable_check`` option is needed for directory creation against some
        servers and makes ``check`` return True without making a request.
        An explicit HEAD request keeps existence checks correct for migration
        resume, conflict detection, and cleanup.
        """
        key = self._key(storage_key)
        try:
            response = self.client.execute_request(
                action="check",
                path=self._urn_type(key).quote(),
            )
            try:
                return int(response.status_code) == 200
            finally:
                response.close()
        except Exception as exc:
            # Treat 404-like responses as "not found"
            error_msg = str(exc).lower()
            if "not found" in error_msg or "404" in error_msg:
                return False
            # Re-raise unexpected errors (connection, auth, etc.)
            self.logger.warning(f"Error checking existence of {key}: {exc}")
            raise

    def test_connection(self) -> dict[str, Any]:
        """Test WebDAV connection by uploading, reading, and deleting a probe file."""
        probe_filename = f"_probe/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.txt"
        probe_key = self._key(probe_filename)
        content = "omlorix-user-file-storage-probe"

        # Create probe directory recursively
        self._ensure_parent_dirs(probe_key)

        # Upload probe file
        import tempfile

        download_temp_path = None
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write(content)
            temp_path = f.name

        try:
            try:
                self.client.upload_sync(remote_path=probe_key, local_path=temp_path)

                # Download and verify
                with tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".txt"
                ) as f:
                    download_temp_path = f.name

                self.client.download_sync(
                    remote_path=probe_key, local_path=download_temp_path
                )
                read_back = Path(download_temp_path).read_text(encoding="utf-8")

                if read_back != content:
                    return {
                        "status": "error",
                        "provider": "webdav",
                        "url": self.url,
                        "error": "Probe file content verification failed",
                    }

                self.client.clean(probe_key)

                # Clean up probe directory (best effort)
                probe_dir = str(Path(probe_key).parent)
                try:
                    self.client.clean(probe_dir)
                except Exception:
                    pass  # Best effort cleanup, don't fail if directory removal fails

                return {
                    "status": "ok",
                    "provider": "webdav",
                    "url": self.url,
                    "probe_content": read_back,
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "provider": "webdav",
                    "url": self.url,
                    "error": str(exc),
                }
        finally:
            Path(temp_path).unlink(missing_ok=True)
            if download_temp_path:
                Path(download_temp_path).unlink(missing_ok=True)
