import io
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.admin.user_exports.files import models as user_file_transfer
from app.admin.user_exports.files.models import (
    _revoke_imported_canvas_asset_approvals,
)


def test_imported_canvas_asset_approval_metadata_is_bounded_and_fully_revoked():
    """Untrusted entries beyond the Canvas limit must retain no authority."""

    references = [
        {
            "file_id": f"asset-{index}",
            "status": "active",
            "authorized_by_user_id": "attacker",
            "public_status": "active",
            "public_authorized_by_user_id": "attacker",
        }
        for index in range(25)
    ]
    meta = {"canvas_asset_references": references}

    _revoke_imported_canvas_asset_approvals(meta)

    assert len(meta["canvas_asset_references"]) == 20
    assert all(
        reference["status"] == "revoked"
        and reference["authorized_by_user_id"] == ""
        and reference["public_status"] == "revoked"
        and reference["public_authorized_by_user_id"] == ""
        for reference in meta["canvas_asset_references"]
    )




def test_zip_manifest_reader_rejects_compressed_oversized_entry_before_opening(
    monkeypatch,
):
    """A tiny compressed ZIP must not expand an oversized JSON manifest."""
    manifest_limit = 1024
    monkeypatch.setattr(
        user_file_transfer,
        "MAX_IMPORT_MANIFEST_SIZE",
        manifest_limit,
        raising=False,
    )
    manifest_name = "manifest.json"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            manifest_name,
            b"{" + (b'"padding":"A",' * 4096) + b'"files":[]}',
        )
    payload.seek(0)

    with zipfile.ZipFile(payload) as archive:
        info = archive.getinfo(manifest_name)
        assert info.compress_size < manifest_limit
        assert info.file_size > manifest_limit
        monkeypatch.setattr(
            archive,
            "open",
            lambda *_args, **_kwargs: pytest.fail(
                "oversized manifest content must not be decompressed"
            ),
        )

        with pytest.raises(HTTPException) as rejected:
            user_file_transfer._read_manifest(archive)

    assert rejected.value.status_code == 400
    assert rejected.value.detail == "Invalid file package manifest."


def test_zip_manifest_reader_caps_content_read_when_declared_size_is_acceptable(
    monkeypatch,
):
    """The read cap must remain authoritative if ZIP metadata is inaccurate."""
    manifest_limit = 64
    monkeypatch.setattr(
        user_file_transfer,
        "MAX_IMPORT_MANIFEST_SIZE",
        manifest_limit,
        raising=False,
    )

    class TrackingHandle(io.BytesIO):
        """Record the requested read size for the bounded-reader assertion."""

        def __init__(self, value):
            super().__init__(value)
            self.read_sizes = []

        def read(self, size=-1):
            self.read_sizes.append(size)
            return super().read(size)

    handle = TrackingHandle(b"{" + (b" " * manifest_limit) + b"}")
    info = SimpleNamespace(file_size=manifest_limit, is_dir=lambda: False)

    class ArchiveWithInaccurateMetadata:
        """Expose a small declared size while returning more decompressed bytes."""

        def getinfo(self, entry_name):
            assert entry_name == "manifest.json"
            return info

        def open(self, entry_info):
            assert entry_info is info
            return handle

    with pytest.raises(HTTPException) as rejected:
        user_file_transfer._read_manifest(ArchiveWithInaccurateMetadata())

    assert rejected.value.status_code == 400
    assert rejected.value.detail == "Invalid file package manifest."
    assert handle.read_sizes == [manifest_limit + 1]
