import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request


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

from app.files import router as files_router
from app.files import sharing
from app.files.models import FileArtifactShare, Files
from app.files.schemas import ArtifactShareAccessRequest
from app.utils.cache_headers import NO_STORE_HEADERS


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/files/canvas/shared/access",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


class _FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]


class _ArtifactShareAccessDb:
    def __init__(self, share, file_record):
        self.share = share
        self.file_record = file_record
        self.commits = 0

    def query(self, model):
        if model is FileArtifactShare:
            return _FakeQuery(self.share)
        if model is Files:
            return _FakeQuery(self.file_record)
        return _FakeQuery(None)

    def commit(self):
        self.commits += 1


class ArtifactShareSecurityTests:
    def setup_method(self):
        sharing._ARTIFACT_SHARE_PASSWORD_ATTEMPTS.clear()
        sharing._ARTIFACT_SHARE_ACCESS_COUNTS.clear()

    @pytest.mark.parametrize(
        ("file_type", "file_name", "expected_type"),
        [
            ("text/markdown", "project-plan.md", "markdown"),
            ("text/x-markdown", "project-plan.markdown", "markdown"),
            ("text/html", "site.html", "html"),
            ("application/xhtml+xml", "site.xhtml", "html"),
            # Cloud storage may retain the filename while reporting a generic
            # MIME type. Canvas preview already handles this, so shares must
            # do the same for owner-uploaded files.
            ("application/octet-stream", "site.html", "html"),
            ("text/css", "theme.css", "css"),
            ("text/x-mermaid", "diagram.mmd", "mermaid"),
            ("application/octet-stream", "diagram.mermaid", "mermaid"),
            ("application/pdf", "report.pdf", "pdf"),
            ("application/octet-stream", "report.pdf", "pdf"),
        ],
    )
    def test_owner_uploaded_canvas_file_types_are_shareable(
        self, file_type, file_name, expected_type
    ):
        """Canvas-compatible uploads must not require assistant provenance."""
        file_record = SimpleNamespace(
            file_type=file_type,
            file_name=file_name,
            meta={"origin": "user", "original_filename": file_name},
        )

        assert sharing._get_shareable_canvas_artifact_type(file_record) == expected_type
        assert sharing._is_shareable_canvas_artifact(file_record) is True

    def test_non_canvas_upload_remains_unshareable(self):
        """Filename detection remains restricted to Canvas-supported formats."""
        file_record = SimpleNamespace(
            file_type="application/octet-stream",
            file_name="confidential.xlsx",
            meta={"origin": "user", "original_filename": "confidential.xlsx"},
        )

        assert sharing._get_shareable_canvas_artifact_type(file_record) is None
        assert sharing._is_shareable_canvas_artifact(file_record) is False

    def test_create_allows_an_owner_uploaded_html_file_with_generic_mime(self, monkeypatch):
        """The create route accepts the same filename-detected HTML as Canvas preview."""
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="site.html",
            file_type="application/octet-stream",
            file_size=1024,
            meta={"origin": "user", "original_filename": "site.html"},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = file_record
        monkeypatch.setattr(
            sharing,
            "ensure_artifact_file_sharing_allowed_for_user",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(sharing, "get_public_url", lambda _db: "https://chat.example")

        sharing.create_artifact_share(db=db, user_id="user-1", file_id="file-1")

        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_password_normalization_enforces_chat_share_length_bounds(self):
        with pytest.raises(HTTPException) as short_error:
            sharing._normalize_password("short", required=True)

        assert short_error.value.status_code == 400
        assert "at least 8" in short_error.value.detail

        with pytest.raises(HTTPException) as long_error:
            sharing._normalize_password("x" * 257, required=True)

        assert long_error.value.status_code == 400
        assert "at most 256" in long_error.value.detail

    def test_password_attempt_limiter_blocks_repeated_failures_per_share_and_ip(self, monkeypatch):
        monkeypatch.setattr(sharing, "get_redis_client", lambda: None)

        for _ in range(sharing.ARTIFACT_SHARE_PASSWORD_ATTEMPT_LIMIT):
            attempt_key = sharing._enforce_password_attempt_limit("share-1", "203.0.113.10")
            sharing._record_password_failure(attempt_key)

        with pytest.raises(HTTPException) as blocked:
            sharing._enforce_password_attempt_limit("share-1", "203.0.113.10")

        assert blocked.value.status_code == 429

    def test_public_access_limiter_blocks_repeated_access_per_share_and_ip(self, monkeypatch):
        monkeypatch.setattr(sharing, "get_redis_client", lambda: None)

        for _ in range(sharing.ARTIFACT_SHARE_ACCESS_LIMIT):
            sharing.enforce_shared_artifact_access_rate_limit("share-1", "203.0.113.10")

        with pytest.raises(HTTPException) as blocked:
            sharing.enforce_shared_artifact_access_rate_limit("share-1", "203.0.113.10")

        assert blocked.value.status_code == 429

    def test_share_serializer_exposes_owner_access_telemetry(self):
        share = SimpleNamespace(
            id="share-1",
            password_hash=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=None,
            last_accessed_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
            access_count=7,
        )

        payload = sharing._serialize_share_row(share, "https://chat.example")

        assert payload["last_accessed_at"] == datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        assert payload["access_count"] == 7

    def test_successful_shared_artifact_access_updates_access_telemetry(self, tmp_path, monkeypatch):
        content_path = tmp_path / "artifact.md"
        content_path.write_text("# Shared\n", encoding="utf-8")
        expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        share = SimpleNamespace(
            id="share-1",
            file_id="file-1",
            user_id="user-1",
            password_hash=None,
            expires_at=expires_at,
            last_accessed_at=None,
            access_count=2,
        )
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            meta={"canvas": True},
        )
        db = _ArtifactShareAccessDb(share, file_record)
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "_resolve_artifact_content_path", lambda _file_record: content_path)

        result = sharing.resolve_shared_artifact_access(db=db, share_id="share-1")

        assert result["content"] == "# Shared\n"
        assert result["expires_at"] == expires_at
        assert result["has_password"] is False
        assert share.last_accessed_at is not None
        assert share.access_count == 3
        assert db.commits == 1

    def test_create_rejects_oversized_shareable_artifact(self, monkeypatch):
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            file_size=sharing.ARTIFACT_SHARE_MAX_CONTENT_BYTES + 1,
            meta={"canvas": True},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = file_record
        monkeypatch.setattr(sharing, "ensure_artifact_file_sharing_allowed_for_user", lambda *args, **kwargs: None)

        with pytest.raises(HTTPException) as denied:
            sharing.create_artifact_share(db=db, user_id="user-1", file_id="file-1")

        assert denied.value.status_code == 413
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_create_uses_requested_expiry_duration(self, monkeypatch):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            file_size=1024,
            meta={"canvas": True},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = file_record
        monkeypatch.setattr(sharing, "ensure_artifact_file_sharing_allowed_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "get_public_url", lambda _db: "https://chat.example")
        monkeypatch.setattr(sharing, "_utcnow", lambda: now)

        sharing.create_artifact_share(
            db=db,
            user_id="user-1",
            file_id="file-1",
            expires_in_hours=72,
        )

        created_share = db.add.call_args.args[0]
        assert created_share.created_at == now
        assert created_share.expires_at == now + timedelta(hours=72)
        db.commit.assert_called_once()

    def test_change_share_expiry_updates_expiry_timestamp(self, monkeypatch):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        next_expiry = datetime(2026, 1, 3, 9, 30, tzinfo=timezone.utc)
        share = SimpleNamespace(id="share-1", expires_at=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = share
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "_utcnow", lambda: now)

        result = sharing.change_artifact_share_expiry(
            db=db,
            user_id="user-1",
            share_id="share-1",
            expires_at=next_expiry,
        )

        assert share.expires_at == next_expiry
        assert result == {"share_id": "share-1", "expires_at": next_expiry}
        db.commit.assert_called_once()

    def test_change_share_expiry_rejects_expiry_beyond_maximum_lifetime(self, monkeypatch):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        too_late = now + timedelta(hours=sharing.ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS, seconds=1)
        share = SimpleNamespace(id="share-1", expires_at=now + timedelta(hours=24))
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = share
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "_utcnow", lambda: now)

        with pytest.raises(HTTPException) as denied:
            sharing.change_artifact_share_expiry(
                db=db,
                user_id="user-1",
                share_id="share-1",
                expires_at=too_late,
            )

        assert denied.value.status_code == 400
        assert "within" in denied.value.detail
        assert share.expires_at == now + timedelta(hours=24)
        db.commit.assert_not_called()

    def test_remove_share_expiry_is_rejected(self, monkeypatch):
        original_expiry = datetime(2026, 1, 3, 9, 30, tzinfo=timezone.utc)
        share = SimpleNamespace(id="share-1", expires_at=original_expiry)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = share
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)

        with pytest.raises(HTTPException) as denied:
            sharing.remove_artifact_share_expiry(
                db=db,
                user_id="user-1",
                share_id="share-1",
            )

        assert denied.value.status_code == 400
        assert "must keep an expiry" in denied.value.detail
        assert share.expires_at == original_expiry
        db.commit.assert_not_called()

    def test_shared_artifact_access_rejects_oversized_metadata_before_reading(self, monkeypatch):
        share = SimpleNamespace(
            id="share-1",
            file_id="file-1",
            user_id="user-1",
            password_hash=None,
            expires_at=None,
            last_accessed_at=None,
            access_count=2,
        )
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            file_size=sharing.ARTIFACT_SHARE_MAX_CONTENT_BYTES + 1,
            meta={"canvas": True},
        )
        db = _ArtifactShareAccessDb(share, file_record)
        mock_resolve_path = MagicMock()
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "_resolve_artifact_content_path", mock_resolve_path)

        with pytest.raises(HTTPException) as denied:
            sharing.resolve_shared_artifact_access(db=db, share_id="share-1")

        assert denied.value.status_code == 413
        mock_resolve_path.assert_not_called()
        assert share.access_count == 2
        assert db.commits == 0

    def test_shared_artifact_access_rejects_oversized_materialized_file(self, tmp_path, monkeypatch):
        content_path = tmp_path / "artifact.md"
        content_path.write_bytes(b"x" * (sharing.ARTIFACT_SHARE_MAX_CONTENT_BYTES + 1))
        share = SimpleNamespace(
            id="share-1",
            file_id="file-1",
            user_id="user-1",
            password_hash=None,
            expires_at=None,
            last_accessed_at=None,
            access_count=2,
        )
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            file_size=None,
            meta={"canvas": True},
        )
        db = _ArtifactShareAccessDb(share, file_record)
        monkeypatch.setattr(sharing, "ensure_artifact_sharing_enabled_for_user", lambda *args, **kwargs: None)
        monkeypatch.setattr(sharing, "_resolve_artifact_content_path", lambda _file_record: content_path)

        with pytest.raises(HTTPException) as denied:
            sharing.resolve_shared_artifact_access(db=db, share_id="share-1")

        assert denied.value.status_code == 413
        assert share.access_count == 2
        assert db.commits == 0

    def test_artifact_share_creation_requires_artifact_sharing(self, monkeypatch):
        db = MagicMock()
        monkeypatch.setattr(sharing, "get_user_group_setting_value", lambda *_args: False)

        with pytest.raises(HTTPException) as denied:
            sharing.create_artifact_share(db=db, user_id="user-1", file_id="file-1")

        assert denied.value.status_code == 403
        assert denied.value.detail == "Canvas sharing is disabled for your group"
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_artifact_share_creation_requires_sharing_even_with_existing_links(self, monkeypatch):
        db = MagicMock()
        monkeypatch.setattr(sharing, "artifact_file_has_existing_share_state", lambda *args, **kwargs: True)
        monkeypatch.setattr(
            sharing,
            "ensure_artifact_file_sharing_allowed_for_user",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPException(status_code=403, detail="Canvas sharing is disabled for your group")
            ),
        )

        with pytest.raises(HTTPException) as denied:
            sharing.create_artifact_share(db=db, user_id="user-1", file_id="file-1")

        assert denied.value.status_code == 403
        assert denied.value.detail == "Canvas sharing is disabled for your group"
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_shared_artifact_access_returns_not_found_when_artifact_sharing_is_disabled(self, tmp_path, monkeypatch):
        content_path = tmp_path / "artifact.md"
        content_path.write_text("# Shared\n", encoding="utf-8")
        share = SimpleNamespace(
            id="share-1",
            file_id="file-1",
            user_id="user-1",
            password_hash=None,
            expires_at=None,
            last_accessed_at=None,
            access_count=2,
        )
        file_record = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="artifact.md",
            file_type="text/markdown",
            meta={"canvas": True},
        )
        db = _ArtifactShareAccessDb(share, file_record)

        monkeypatch.setattr(sharing, "get_user_group_setting_value", lambda *_args: False)
        monkeypatch.setattr(sharing, "_resolve_artifact_content_path", lambda _file_record: content_path)

        with pytest.raises(HTTPException) as denied:
            sharing.resolve_shared_artifact_access(db=db, share_id="share-1")

        assert denied.value.status_code == 404
        assert denied.value.detail == "Shared canvas not found"
        assert share.access_count == 2
        assert db.commits == 0

    def test_access_route_audits_successful_public_artifact_access(self):
        payload = ArtifactShareAccessRequest(share_id="share-1")
        result = {
            "share_id": "share-1",
            "file_name": "artifact.md",
            "artifact_type": "markdown",
            "mime_type": "text/markdown",
            "content": "# Shared",
        }

        with patch.object(
            files_router,
            "_shared_artifact_audit_subject",
            return_value={"share_id": "share-1", "file_id": "file-1", "owner_user_id": "user-1"},
        ), patch.object(files_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            files_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(
            files_router,
            "resolve_shared_artifact_access",
            return_value=result,
        ) as mock_resolve, patch.object(
            files_router,
            "create_audit_log",
        ) as mock_audit:
            header_response = Response()
            response = files_router.access_shared_artifact_route(
                payload=payload,
                request=_request(),
                response=header_response,
                db=MagicMock(),
                db_log=MagicMock(),
            )

        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["client_ip"] == "203.0.113.10"
        assert response.share_id == "share-1"
        for name, value in NO_STORE_HEADERS.items():
            assert header_response.headers[name] == value
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "CANVAS_SHARE_ACCESSED"
        assert mock_audit.call_args.kwargs["category"] == "share"

    def test_access_route_audits_denied_public_artifact_access(self):
        payload = ArtifactShareAccessRequest(share_id="share-1", password="wrong")

        with patch.object(
            files_router,
            "_shared_artifact_audit_subject",
            return_value={"share_id": "share-1", "file_id": "file-1", "owner_user_id": "user-1"},
        ), patch.object(files_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            files_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(
            files_router,
            "resolve_shared_artifact_access",
            side_effect=HTTPException(status_code=401, detail="Invalid password"),
        ), patch.object(
            files_router,
            "create_audit_log",
        ) as mock_audit:
            with pytest.raises(HTTPException):
                files_router.access_shared_artifact_route(
                    payload=payload,
                    request=_request(),
                    response=Response(),
                    db=MagicMock(),
                    db_log=MagicMock(),
                )

        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "CANVAS_SHARE_ACCESS_DENIED"
        assert mock_audit.call_args.kwargs["details"]["status_code"] == 401

    @pytest.mark.parametrize(
        ("share_id", "audit_subject", "expected_status"),
        [
            ("   ", {}, 400),
            ("does-not-exist", {"share_id": "does-not-exist"}, 404),
        ],
    )
    def test_access_route_uses_anonymous_audit_user_for_unresolved_share_ids(
        self,
        share_id,
        audit_subject,
        expected_status,
    ):
        payload = ArtifactShareAccessRequest(share_id=share_id)

        with patch.object(
            files_router,
            "_shared_artifact_audit_subject",
            return_value=audit_subject,
        ), patch.object(files_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            files_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(
            files_router,
            "resolve_shared_artifact_access",
            side_effect=HTTPException(status_code=expected_status, detail="Shared canvas not found"),
        ), patch.object(
            files_router,
            "create_audit_log",
        ) as mock_audit:
            with pytest.raises(HTTPException) as denied:
                files_router.access_shared_artifact_route(
                    payload=payload,
                    request=_request(),
                    response=Response(),
                    db=MagicMock(),
                    db_log=MagicMock(),
                )

        assert denied.value.status_code == expected_status
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["user_id"] == "anonymous"
        assert mock_audit.call_args.kwargs["action"] == "CANVAS_SHARE_ACCESS_DENIED"
        assert mock_audit.call_args.kwargs["details"]["status_code"] == expected_status

    def test_access_route_rate_limit_stops_before_resolution_or_audit(self):
        payload = ArtifactShareAccessRequest(share_id="share-1")

        with patch.object(files_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            files_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(
            files_router,
            "enforce_shared_artifact_access_rate_limit",
            side_effect=HTTPException(status_code=429, detail="Too many shared canvas access attempts."),
        ), patch.object(
            files_router,
            "_shared_artifact_audit_subject",
        ) as mock_audit_subject, patch.object(
            files_router,
            "resolve_shared_artifact_access",
        ) as mock_resolve, patch.object(
            files_router,
            "create_audit_log",
        ) as mock_audit:
            with pytest.raises(HTTPException) as blocked:
                files_router.access_shared_artifact_route(
                    payload=payload,
                    request=_request(),
                    response=Response(),
                    db=MagicMock(),
                    db_log=MagicMock(),
                )

        assert blocked.value.status_code == 429
        mock_audit_subject.assert_not_called()
        mock_resolve.assert_not_called()
        mock_audit.assert_not_called()
