import asyncio
import io
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", ModuleType("zstandard"))

from app.chats import meeting_transcripts  # noqa: E402


class _FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        self.refreshed.append(obj)


def test_validate_meeting_transcript_governance_requires_consent():
    with pytest.raises(HTTPException) as exc_info:
        meeting_transcripts._validate_meeting_transcript_governance(
            consent_confirmed=False,
            legal_basis="consent",
            legal_basis_details="Signed consent form",
            retention_days=30,
        )

    assert exc_info.value.status_code == 400
    assert "consent" in exc_info.value.detail.lower()


def test_validate_meeting_transcript_governance_returns_retention_metadata():
    governance = meeting_transcripts._validate_meeting_transcript_governance(
        consent_confirmed=True,
        legal_basis="legitimate_interest",
        legal_basis_details="Internal meeting policy POL-42",
        retention_days=45,
    )

    assert governance.legal_basis == "legitimate_interest"
    assert governance.legal_basis_label == "Legitimate interest"
    assert governance.legal_basis_details == "Internal meeting policy POL-42"
    assert governance.retention_days == 45
    assert governance.retention_expires_at > governance.consent_confirmed_at


def test_transcription_runtime_returns_stable_error_code_when_disabled(monkeypatch):
    monkeypatch.setattr(meeting_transcripts, "get_settings_page", lambda *_args: None)

    with pytest.raises(HTTPException) as exc_info:
        meeting_transcripts._get_transcription_runtime(_FakeDb())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": meeting_transcripts.TRANSCRIPTION_NOT_ENABLED_ERROR_CODE,
    }


def test_meeting_transcription_releases_session_before_provider_wait(monkeypatch):
    db = object()
    provider = SimpleNamespace(
        provider="openai",
        api_key="secret",
        settings={"base_url": "https://provider.example/v1"},
    )
    events = []
    captured = {}

    def release_session(received_db):
        assert received_db is db
        events.append("released")
        return True

    async def transcribe(snapshot, **kwargs):
        events.append("provider")
        captured["snapshot"] = snapshot
        captured["kwargs"] = kwargs
        return "Hello team"

    monkeypatch.setattr(
        meeting_transcripts,
        "release_db_session_before_long_wait",
        release_session,
    )
    monkeypatch.setattr(
        meeting_transcripts,
        "transcribe_audio_bytes_for_provider",
        transcribe,
    )

    result = asyncio.run(
        meeting_transcripts._transcribe_media_bytes(
            db,
            b"audio-bytes",
            "meeting.mp3",
            {"provider": provider, "model": "gpt-4o-transcribe"},
        )
    )

    snapshot = captured["snapshot"]
    assert result == "Hello team"
    assert events == ["released", "provider"]
    assert snapshot is not provider
    assert snapshot.provider == "openai"
    assert snapshot.api_key == "secret"
    assert snapshot.settings == provider.settings
    assert snapshot.settings is not provider.settings
    assert captured["kwargs"] == {
        "model_name": "gpt-4o-transcribe",
        "audio_bytes": b"audio-bytes",
        "filename": "meeting.mp3",
    }


def test_create_meeting_transcript_persists_governance_metadata(monkeypatch):
    db = _FakeDb()
    captured = {}

    monkeypatch.setattr(meeting_transcripts, "_get_transcription_runtime", lambda _db: {"provider": object(), "model": "demo"})
    monkeypatch.setattr(meeting_transcripts, "_detect_media_kind", lambda *_args, **_kwargs: ("audio", "audio/mpeg"))
    monkeypatch.setattr(meeting_transcripts, "get_user_group_setting_value", lambda *_args, **_kwargs: 25)
    monkeypatch.setattr(
        meeting_transcripts,
        "_normalize_media_for_transcription",
        lambda **_kwargs: (b"audio-bytes", "meeting.mp3", False),
    )

    async def fake_transcribe(*_args, **_kwargs):
        return "Hello team"

    monkeypatch.setattr(meeting_transcripts, "_transcribe_media_bytes", fake_transcribe)
    monkeypatch.setattr(
        meeting_transcripts,
        "create_chat",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="chat-1",
            title="",
            project_id=None,
            last_updated_at=None,
        ),
    )

    def fake_persist_generated_file_bytes(**kwargs):
        captured["file_meta"] = kwargs["meta"]
        return SimpleNamespace(
            id="file-1",
            file_name=kwargs["original_filename"],
            file_category=kwargs["file_category"],
            file_type=kwargs["file_type"],
            file_size=len(kwargs["file_bytes"]),
            project_id=kwargs["project_id"],
            folder_id=None,
            created_at=None,
            meta=kwargs["meta"],
        )

    monkeypatch.setattr(meeting_transcripts, "persist_generated_file_bytes", fake_persist_generated_file_bytes)

    def fake_create_chat_message(_db, _chat_id, *_args, **kwargs):
        captured["message_content"] = kwargs["content"]
        return SimpleNamespace(id="message-1")

    monkeypatch.setattr(meeting_transcripts, "create_chat_message", fake_create_chat_message)
    monkeypatch.setattr(meeting_transcripts, "_build_file_lookup_for_user", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        meeting_transcripts,
        "_serialize_chat_rows",
        lambda *_args, **_kwargs: [{"id": "message-1", "documents": [{"id": "file-1"}]}],
    )

    upload = UploadFile(filename="meeting.mp3", file=io.BytesIO(b"1234"))

    result = asyncio.run(
        meeting_transcripts.create_meeting_transcript(
            db=db,
            user_id="user-1",
            user_role="member",
            media=upload,
            browser_date_iso="2026-05-17T09:00:00+00:00",
            browser_date_label="May 17, 2026",
            consent_confirmed=True,
            legal_basis="contract",
            legal_basis_details="Customer support call terms",
            retention_days=30,
        )
    )

    assert result["chat_id"] == "chat-1"
    assert captured["file_meta"]["meeting_consent_confirmed"] is True
    assert captured["file_meta"]["meeting_legal_basis"] == "contract"
    assert captured["file_meta"]["meeting_legal_basis_details"] == "Customer support call terms"
    assert captured["file_meta"]["meeting_retention_days"] == 30
    message_meta = captured["message_content"][0]["meta"]
    assert message_meta["meeting_legal_basis_label"] == "Contract"
    assert message_meta["meeting_retention_days"] == 30


def test_ffmpeg_extract_audio_uses_bounded_subprocess(monkeypatch, tmp_path):
    source_path = tmp_path / "meeting.webm"
    target_path = tmp_path / "meeting.mp3"
    source_path.write_bytes(b"media")
    captured = {}

    monkeypatch.setattr(meeting_transcripts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        target_path.write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(meeting_transcripts.subprocess, "run", fake_run)

    meeting_transcripts._run_ffmpeg_extract_audio(source_path, target_path, max_output_bytes=1024)

    assert "-nostdin" in captured["command"]
    assert "-fs" in captured["command"]
    assert captured["kwargs"]["timeout"] == meeting_transcripts._FFMPEG_TIMEOUT_SECONDS
    assert captured["kwargs"]["stdout"] == meeting_transcripts.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is not None
    assert "capture_output" not in captured["kwargs"]
    assert callable(captured["kwargs"]["preexec_fn"])


def test_normalize_checks_converted_size_before_reading(monkeypatch, tmp_path):
    source_path = tmp_path / "meeting.webm"
    normalized_path = tmp_path / "meeting.mp3"
    source_path.write_bytes(b"media")
    normalized_path.write_bytes(b"x" * 2048)

    def fake_extract(_source_path, _target_path, *, max_output_bytes):
        assert _source_path == source_path
        assert _target_path == normalized_path
        assert max_output_bytes == 1024

    def fail_read_bytes(self):
        if self == normalized_path:
            raise AssertionError("normalized file should not be read before size validation")
        return Path.read_bytes(self)

    monkeypatch.setattr(meeting_transcripts, "_run_ffmpeg_extract_audio", fake_extract)
    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    with pytest.raises(HTTPException) as exc_info:
        meeting_transcripts._normalize_media_for_transcription(
            source_path=source_path,
            original_filename="meeting.webm",
            media_kind="video",
            runtime={"allowed_formats": {"mp3"}, "upload_limit_bytes": 1024},
        )

    assert exc_info.value.status_code == 400
    assert "exceeds" in exc_info.value.detail


def test_sqlite_meeting_wrapper_does_not_retry_internal_type_error(monkeypatch):
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    attempts = []

    async def fail_create(**_kwargs):
        attempts.append("inline")
        raise TypeError("transcript workflow failed")

    async def fail_retry(*_args, **_kwargs):
        attempts.append("worker")
        pytest.fail("a failed transcript workflow must not be retried")

    monkeypatch.setattr(
        meeting_transcripts,
        "create_meeting_transcript",
        fail_create,
    )
    monkeypatch.setattr(meeting_transcripts, "run_blocking_io", fail_retry)

    with pytest.raises(TypeError, match="transcript workflow failed"):
        asyncio.run(
            meeting_transcripts.create_meeting_transcript_off_event_loop(
                db=db,
                user_id="user-1",
                user_role="member",
                media=UploadFile(filename="meeting.mp3", file=io.BytesIO(b"media")),
            )
        )

    assert attempts == ["inline"]
