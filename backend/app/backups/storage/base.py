from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BackupStorageAdapter(ABC):
    @abstractmethod
    def upload_file(self, local_path: Path, remote_path: str) -> str:
        """Upload local file to remote path and return storage URI."""

    @abstractmethod
    def download_file(self, storage_uri: str, target_path: Path) -> Path:
        """Download storage URI into target path and return target path."""

    @abstractmethod
    def delete_file(self, storage_uri: str) -> None:
        """Delete a remote file identified by URI."""

    @abstractmethod
    def test_connection(self) -> dict:
        """Run write/read/delete probe and return diagnostics."""
