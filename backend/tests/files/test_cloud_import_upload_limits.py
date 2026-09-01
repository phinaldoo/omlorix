from __future__ import annotations

import sys
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

from app.files import google_drive  # noqa: E402


class _StreamResponse:
    def __init__(self, chunks: list[bytes]):
        self.status_code = 200
        self.text = ""
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _StreamClient:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.stream_calls = 0

    def stream(self, *_args, **_kwargs):
        self.stream_calls += 1
        return _StreamResponse(self._chunks)


def test_google_drive_import_rejects_declared_size_above_group_limit_before_download():
    client = _StreamClient([b"unused"])

    with pytest.raises(HTTPException) as exc_info:
        google_drive._download_drive_file_to_path(
            client,
            "token",
            {
                "id": "file-1",
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "size": str(2 * 1024 * 1024),
            },
            max_upload_bytes=1024 * 1024,
            max_upload_mb=1,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "File size exceeds limit of 1 MB"
    assert client.stream_calls == 0


def test_google_drive_import_stops_stream_when_group_limit_is_exceeded():
    client = _StreamClient([b"a" * 700_000, b"b" * 500_000])

    with pytest.raises(HTTPException) as exc_info:
        google_drive._download_drive_file_to_path(
            client,
            "token",
            {
                "id": "file-1",
                "name": "report.pdf",
                "mimeType": "application/pdf",
            },
            max_upload_bytes=1024 * 1024,
            max_upload_mb=1,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "File size exceeds limit of 1 MB"
    assert client.stream_calls == 1


def test_google_drive_picker_session_exposes_only_ephemeral_browser_credentials(monkeypatch):
    """Picker receives the access token, never the refresh token or client secret."""

    connection = SimpleNamespace(
        secrets={
            "access_token": "short-lived-access-token",
            "refresh_token": "must-stay-server-side",
            "client_secret": "must-stay-server-side",
            "expires_at": 1_800_000_000,
        }
    )
    monkeypatch.setattr(google_drive, "ensure_group_allows_connection_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        google_drive,
        "google_picker_client_settings",
        lambda _db: {"developer_key": "browser-key", "app_id": "123456789", "client_id": "client"},
    )
    monkeypatch.setattr(google_drive, "get_user_connection_by_provider", lambda *args, **kwargs: connection)
    monkeypatch.setattr(google_drive, "_refresh_drive_connection_if_needed", lambda _db, item: item)

    payload = google_drive.get_google_drive_picker_session_payload(object(), user_id="user-1")

    assert payload == {
        "picker_ready": True,
        "connected": True,
        "developer_key": "browser-key",
        "app_id": "123456789",
        "access_token": "short-lived-access-token",
        "expires_at": 1_800_000_000,
    }
    assert "refresh_token" not in payload
    assert "client_secret" not in payload


def test_google_drive_picker_session_falls_back_when_admin_configuration_is_missing(monkeypatch):
    """An unconfigured Picker must not disrupt the existing Drive browser."""

    monkeypatch.setattr(google_drive, "ensure_group_allows_connection_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        google_drive,
        "get_user_connection_by_provider",
        lambda *args, **kwargs: SimpleNamespace(secrets={"access_token": "token"}),
    )

    def missing_picker_settings(_db):
        raise HTTPException(status_code=503, detail="not configured")

    monkeypatch.setattr(google_drive, "google_picker_client_settings", missing_picker_settings)

    payload = google_drive.get_google_drive_picker_session_payload(object(), user_id="user-1")

    assert payload == {
        "picker_ready": False,
        "connected": True,
        "error_code": "picker_not_configured",
    }
