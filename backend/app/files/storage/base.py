from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class UserFileStorageAdapter(ABC):
    @abstractmethod
    def upload_file(self, local_path: Path, storage_key: str) -> dict[str, Any]:
        """Upload a local file and return provider-specific metadata."""

    @abstractmethod
    def download_file(self, storage_key: str, target_path: Path) -> Path:
        """Download storage key into target path and return target path."""

    @abstractmethod
    def delete_file(self, storage_key: str) -> None:
        """Delete file at storage key."""

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        """Return whether a storage key exists."""

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Run a read/write/delete probe and return diagnostics."""
