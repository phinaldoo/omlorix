import anyio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm import router as llm_router
from app.llm.openai import transcription as openai_transcription


class FakeUpload:
    def __init__(self, payload: bytes, *, declared_size=None):
        self._payload = payload
        self._offset = 0
        self.size = declared_size
        self.read_calls = 0
        self.read_bytes = 0
        self.max_read_size = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        self.max_read_size = max(self.max_read_size, size)
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        self.read_bytes += len(chunk)
        return chunk


@pytest.mark.anyio
async def test_audio_upload_rejects_declared_oversize_without_reading():
    upload = FakeUpload(b"", declared_size=10)

    with pytest.raises(HTTPException) as exc_info:
        await llm_router._read_audio_upload_with_limit(upload, 9)

    assert exc_info.value.status_code == 400
    assert upload.read_calls == 0


@pytest.mark.anyio
async def test_audio_upload_reads_in_bounded_chunks_and_stops_after_limit():
    payload = b"a" * (5 * llm_router._AUDIO_UPLOAD_READ_CHUNK_BYTES)
    upload = FakeUpload(payload, declared_size=None)

    with pytest.raises(HTTPException) as exc_info:
        await llm_router._read_audio_upload_with_limit(
            upload,
            (2 * llm_router._AUDIO_UPLOAD_READ_CHUNK_BYTES) + 1,
        )

    assert exc_info.value.status_code == 400
    assert upload.max_read_size == llm_router._AUDIO_UPLOAD_READ_CHUNK_BYTES
    assert upload.read_bytes < len(payload)


@pytest.mark.anyio
async def test_file_transcription_returns_stable_error_code_when_disabled(monkeypatch):
    upload = FakeUpload(b"audio")
    upload.filename = "recording.webm"
    monkeypatch.setattr(llm_router, "get_settings_page", lambda *_args: None)

    with pytest.raises(HTTPException) as exc_info:
        await llm_router.transcribe_audio_route(
            audio=upload,
            request=SimpleNamespace(),
            user=SimpleNamespace(id="user-1", group_id="group-1", role="member"),
            db=SimpleNamespace(),
            db_log=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": llm_router.TRANSCRIPTION_NOT_ENABLED_ERROR_CODE,
    }


@pytest.mark.anyio
async def test_openai_transcription_uses_native_async_sdk(monkeypatch):
    captured_request = {}

    class FakeTranscriptions:
        async def create(self, **kwargs):
            captured_request.update(kwargs)
            return type("Transcription", (), {"text": "hello"})()

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()
        closed = False

        async def close(self):
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr(
        openai_transcription,
        "_get_async_client",
        lambda *_args, **_kwargs: client,
    )

    text = await openai_transcription.transcribe_audio_bytes(
        b"audio",
        "audio.webm",
        api_key="key",
        model="gpt-transcribe",
    )

    assert text == "hello"
    assert client.closed is True
    assert captured_request["model"] == "gpt-transcribe"
    assert captured_request["response_format"] == "json"
    assert "prompt" not in captured_request
    assert "language" not in captured_request


@pytest.mark.anyio
async def test_google_transcription_uses_native_async_sdk(monkeypatch):
    from app.llm.google_aistudio import transcription as google_transcription

    captured_request = {}

    class FakeModels:
        async def generate_content(self, **kwargs):
            captured_request.update(kwargs)
            return SimpleNamespace(text="hello from Gemini")

    class FakeAsyncClient:
        models = FakeModels()
        closed = False

        async def aclose(self):
            self.closed = True

    async_client = FakeAsyncClient()
    base_client = SimpleNamespace(aio=async_client, closed=False)
    base_client.close = lambda: setattr(base_client, "closed", True)
    monkeypatch.setattr(
        google_transcription,
        "get_aistudio_client",
        lambda *_args, **_kwargs: base_client,
    )
    monkeypatch.setattr(
        google_transcription,
        "build_aistudio_generate_content_config",
        lambda: {"temperature": 0},
    )

    text = await google_transcription.transcribe_audio_bytes(
        b"audio",
        "audio.webm",
        api_key="key",
        model="gemini-test",
    )

    assert text == "hello from Gemini"
    assert async_client.closed is True
    assert base_client.closed is True
    assert captured_request["model"] == "gemini-test"


@pytest.mark.anyio
async def test_openai_transcription_closes_transport_when_cancelled(monkeypatch):
    request_started = anyio.Event()

    class FakeTranscriptions:
        async def create(self, **_kwargs):
            request_started.set()
            await anyio.sleep_forever()

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())
        closed = False

        async def close(self):
            await anyio.sleep(0)
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr(
        openai_transcription,
        "_get_async_client",
        lambda *_args, **_kwargs: client,
    )

    async def transcribe():
        await openai_transcription.transcribe_audio_bytes(
            b"audio",
            "audio.webm",
            api_key="key",
            model="gpt-transcribe",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(transcribe)
        await request_started.wait()
        task_group.cancel_scope.cancel()

    assert client.closed is True


@pytest.mark.anyio
async def test_elevenlabs_transcription_uses_native_async_sdk(monkeypatch):
    import elevenlabs.client as elevenlabs_client
    from app.llm.elevenlabs import transcription as elevenlabs_transcription

    captured_request = {}

    class FakeSpeechToText:
        async def convert(self, **kwargs):
            captured_request.update(kwargs)
            return SimpleNamespace(text="hello from ElevenLabs")

    class FakeAsyncClient:
        speech_to_text = FakeSpeechToText()

    client = FakeAsyncClient()
    class FakeHttpClient:
        closed = False

        async def aclose(self):
            self.closed = True

    http_client = FakeHttpClient()
    captured_client_kwargs = {}
    monkeypatch.setattr(
        elevenlabs_client,
        "AsyncElevenLabs",
        lambda **kwargs: captured_client_kwargs.update(kwargs) or client,
    )
    monkeypatch.setattr(elevenlabs_transcription.httpx, "AsyncClient", lambda: http_client)

    text = await elevenlabs_transcription.transcribe_audio_bytes(
        b"audio",
        "audio.webm",
        api_key="key",
        model="scribe-test",
    )

    assert text == "hello from ElevenLabs"
    assert http_client.closed is True
    assert captured_client_kwargs["httpx_client"] is http_client
    assert captured_request["model_id"] == "scribe-test"


def test_openai_transcription_docs_use_documented_model_example():
    """The interface documentation should name a real documented model."""
    documentation = inspect.getdoc(openai_transcription.transcribe_audio)

    assert "gpt-4o-transcribe" in documentation
    assert "``gpt-transcribe``" not in documentation


@pytest.mark.anyio
async def test_file_transcription_renews_reservation_during_provider_work(
    monkeypatch,
):
    """The non-live upload path must keep its reservation until completion."""
    upload = FakeUpload(b"audio", declared_size=5)
    upload.filename = "recording.webm"
    admission = SimpleNamespace(admission_id="admission-1")
    lease_events = []
    finalized = []

    async def fake_lease_renewal(admission_id):
        lease_events.append(("started", admission_id))
        try:
            await anyio.Event().wait()
        finally:
            lease_events.append(("stopped", admission_id))

    async def fake_transcribe(*_args, **_kwargs):
        # Give the lifecycle task a chance to enter before provider completion.
        await anyio.sleep(0)
        return "transcribed"

    monkeypatch.setattr(
        llm_router,
        "get_settings_page",
        lambda *_args: SimpleNamespace(
            data={
                "transcription_enabled": True,
                "transcription_provider_id": "provider-1",
                "transcription_model": "gpt-4o-transcribe",
            }
        ),
    )
    monkeypatch.setattr(
        llm_router,
        "get_transcription_runtime_for_provider",
        lambda *_args: {
            "provider": SimpleNamespace(id="provider-1"),
            "models": {"gpt-4o-transcribe"},
            "allowed_formats": {"webm"},
            "upload_limit_bytes": 1024,
        },
    )
    monkeypatch.setattr(
        llm_router,
        "measure_audio_duration_seconds",
        lambda *_args, **_kwargs: 30.0,
    )
    monkeypatch.setattr(
        llm_router,
        "admit_user_duration_rate_limit",
        lambda *_args, **_kwargs: admission,
    )
    monkeypatch.setattr(
        llm_router,
        "renew_dictation_duration_rate_limit_lease",
        fake_lease_renewal,
    )
    monkeypatch.setattr(
        llm_router,
        "transcribe_audio_bytes_for_provider",
        fake_transcribe,
    )
    monkeypatch.setattr(
        llm_router,
        "finalize_duration_rate_limit_admission",
        lambda _db, admission_id, **kwargs: finalized.append(
            (admission_id, kwargs)
        ),
    )
    monkeypatch.setattr(llm_router, "_audit_llm_event", lambda *_args: None)

    result = await llm_router.transcribe_audio_route(
        audio=upload,
        duration_seconds=None,
        request=SimpleNamespace(),
        user=SimpleNamespace(id="user-1", group_id="group-1", role="member"),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
    )

    assert result == {"text": "transcribed"}
    assert lease_events == [
        ("started", "admission-1"),
        ("stopped", "admission-1"),
    ]
    assert finalized == [
        (
            "admission-1",
            {
                "consumed_seconds": 30,
                "final_status": llm_router.RATE_LIMIT_ADMISSION_COMPLETED,
            },
        )
    ]
