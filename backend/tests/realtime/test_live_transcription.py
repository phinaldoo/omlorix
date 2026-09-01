import asyncio
import base64
import json
from types import SimpleNamespace

from app.llm.openai.model_list import (
    OPENAI_LIVE_TRANSCRIPTION_MODELS,
    OPENAI_TRANSCRIPTION_MODELS,
    OPENAI_UNSUPPORTED_MODELS,
)
from app.admin.settings.schema_categories.dictation import DictationSettings
from app.settings.defaults import DEFAULT_SETTINGS
from app.realtime import transcription


def test_new_transcription_models_are_separated_by_transport():
    """File and live transcription IDs must not leak into chat discovery."""
    assert "gpt-transcribe" in OPENAI_TRANSCRIPTION_MODELS
    assert "gpt-live-transcribe" in OPENAI_LIVE_TRANSCRIPTION_MODELS
    assert "gpt-transcribe" in OPENAI_UNSUPPORTED_MODELS
    assert "gpt-live-transcribe" in OPENAI_UNSUPPORTED_MODELS


def test_live_transcription_settings_have_persisted_defaults():
    """Generic settings import/export can carry every live dictation field."""
    dictation_defaults = DictationSettings().model_dump()
    persisted_defaults = DEFAULT_SETTINGS["dictation"]

    assert dictation_defaults["live_transcription_enabled"] is False
    assert dictation_defaults["live_transcription_provider_id"] is None
    assert dictation_defaults["live_transcription_model"] is None
    assert dictation_defaults["live_transcription_delay"] == "low"
    assert persisted_defaults["live_transcription_enabled"] is False
    assert persisted_defaults["live_transcription_delay"] == "low"


def test_realtime_websocket_url_preserves_custom_api_root():
    """The transport must select transcription intent, not the STT model."""
    url = transcription._build_openai_realtime_websocket_url(
        "https://provider.example/openai/v1/?api-version=current&model=wrong",
    )

    assert (
        url
        == "wss://provider.example/openai/v1/realtime?api-version=current&intent=transcription"
    )
    assert "gpt-live-transcribe" not in url


def test_live_runtime_uses_only_supported_model_and_delay(monkeypatch):
    """Resolve credentials without exposing unrelated transcription controls."""
    settings = SimpleNamespace(
        data={
            "live_transcription_enabled": True,
            "live_transcription_provider_id": "provider-1",
            "live_transcription_model": "gpt-live-transcribe",
            "live_transcription_delay": "low",
        }
    )
    provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        api_key="stored-key",
    )
    monkeypatch.setattr(
        transcription,
        "get_settings_page",
        lambda *_args: settings,
    )
    monkeypatch.setattr(
        transcription,
        "get_llm_provider",
        lambda *_args: provider,
    )
    monkeypatch.setattr(
        transcription,
        "_resolve_openai_client_kwargs",
        lambda *_args, **_kwargs: {
            "api_key": "stored-key",
            "base_url": "https://api.openai.com/v1",
            "organization": "org-1",
            "project": "project-1",
        },
    )

    runtime = transcription.load_live_transcription_runtime(object())
    session_update = transcription._build_session_update(runtime)

    assert runtime.model == "gpt-live-transcribe"
    assert runtime.delay == "low"
    assert runtime.headers["Authorization"] == "Bearer stored-key"
    assert runtime.headers["OpenAI-Organization"] == "org-1"
    assert runtime.headers["OpenAI-Project"] == "project-1"
    assert session_update == {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {
                        "model": "gpt-live-transcribe",
                        "delay": "low",
                    },
                    "turn_detection": None,
                }
            },
        },
    }


def test_upstream_must_confirm_session_before_browser_capture_starts():
    """Ignore session.created and finish setup only after session.updated."""

    class FakeUpstream:
        def __init__(self):
            self.sent = []
            self.messages = [
                json.dumps({"type": "session.created"}),
                json.dumps({"type": "session.updated"}),
            ]

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        async def recv(self):
            return self.messages.pop(0)

    upstream = FakeUpstream()
    runtime = transcription.LiveTranscriptionRuntime(
        provider_id="provider-1",
        provider_type="openai",
        model="gpt-live-transcribe",
        delay="low",
        websocket_url=(
            "wss://api.openai.com/v1/realtime?intent=transcription"
        ),
        headers={},
    )

    asyncio.run(transcription._configure_upstream_session(upstream, runtime))

    assert upstream.messages == []
    assert upstream.sent[0]["session"]["type"] == "transcription"
    assert (
        upstream.sent[0]["session"]["audio"]["input"]["transcription"]["model"]
        == "gpt-live-transcribe"
    )


def test_provider_rate_limit_has_a_distinct_browser_error_code():
    """OpenAI throttling must never be labeled as an Omlorix minute limit."""
    assert (
        transcription._browser_provider_error_code("rate_limit_exceeded")
        == "provider_rate_limited"
    )
    assert (
        transcription._browser_provider_error_code("rate_limited")
        == "provider_rate_limited"
    )
    assert (
        transcription._browser_provider_error_code("insufficient_quota")
        == "provider_quota_exceeded"
    )
    assert (
        transcription._browser_provider_error_code("invalid_request_error")
        == "invalid_request_error"
    )


def test_live_admission_uses_decoded_pcm_duration(monkeypatch):
    """Quota accounting rounds actual 24 kHz PCM16 bytes up to seconds."""
    db = SimpleNamespace(close=lambda: None)
    captured = {}
    monkeypatch.setattr(transcription, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        transcription,
        "finalize_duration_rate_limit_admission",
        lambda _db, admission_id, **kwargs: captured.update(
            {"admission_id": admission_id, **kwargs}
        ),
    )

    transcription._finalize_live_admission(
        "admission-1",
        audio_bytes=transcription.LIVE_TRANSCRIPTION_BYTES_PER_SECOND + 1,
        failed=False,
    )

    assert captured["admission_id"] == "admission-1"
    assert captured["consumed_seconds"] == 2
    assert captured["final_status"] == transcription.RATE_LIMIT_ADMISSION_COMPLETED


def test_duration_limit_commits_buffer_and_waits_for_provider(monkeypatch):
    """A duration limit must preserve the accepted audio's final transcript."""

    class FakeWebSocket:
        def __init__(self):
            first_audio = b"a" * 24_000
            over_limit_audio = b"b" * 25_000
            ignored_audio = b"c" * 1_000
            self.incoming = asyncio.Queue()
            for audio in (first_audio, over_limit_audio, ignored_audio):
                self.incoming.put_nowait(
                    {
                        "type": "audio",
                        "audio": base64.b64encode(audio).decode("ascii"),
                    }
                )
            self.outgoing = []
            self.closed = False

        async def accept(self):
            return None

        async def receive_json(self):
            return await self.incoming.get()

        async def send_json(self, payload):
            self.outgoing.append(payload)

        async def close(self):
            self.closed = True

    class FakeUpstream:
        def __init__(self):
            self.sent = []
            self.messages = asyncio.Queue()
            self.closed = False

        async def send(self, raw_payload):
            payload = json.loads(raw_payload)
            self.sent.append(payload)
            if payload["type"] == "input_audio_buffer.commit":
                self.messages.put_nowait(
                    json.dumps(
                        {
                            "type": (
                                "conversation.item.input_audio_transcription."
                                "completed"
                            ),
                            "item_id": "item-1",
                            "transcript": "accepted audio",
                        }
                    )
                )

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.messages.get()

        async def close(self):
            self.closed = True

    websocket = FakeWebSocket()
    upstream = FakeUpstream()
    finalized = {}
    lease_events = []
    runtime = transcription.LiveTranscriptionRuntime(
        provider_id="provider-1",
        provider_type="openai",
        model="gpt-live-transcribe",
        delay="low",
        websocket_url="wss://api.openai.com/v1/realtime?intent=transcription",
        headers={},
    )

    async def fake_connect(*_args, **_kwargs):
        return upstream

    async def fake_configure(*_args, **_kwargs):
        return None

    async def fake_lease_renewal(admission_id):
        lease_events.append(("started", admission_id))
        try:
            await asyncio.Event().wait()
        finally:
            lease_events.append(("stopped", admission_id))

    monkeypatch.setattr(transcription, "connect", fake_connect)
    monkeypatch.setattr(
        transcription,
        "_configure_upstream_session",
        fake_configure,
    )
    monkeypatch.setattr(
        transcription,
        "_finalize_live_admission",
        lambda admission_id, **kwargs: finalized.update(
            {"admission_id": admission_id, **kwargs}
        ),
    )
    monkeypatch.setattr(
        transcription,
        "renew_dictation_duration_rate_limit_lease",
        fake_lease_renewal,
    )

    asyncio.run(
        transcription.proxy_live_transcription(
            websocket,
            runtime=runtime,
            admission_id="admission-1",
            max_duration_seconds=1,
        )
    )

    upstream_types = [payload["type"] for payload in upstream.sent]
    assert upstream_types == [
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
    ]
    assert {
        "type": "limit_reached",
        "code": "duration_limit",
    } in websocket.outgoing
    assert {
        "type": "transcript.completed",
        "item_id": "item-1",
        "transcript": "accepted audio",
    } in websocket.outgoing
    assert finalized == {
        "admission_id": "admission-1",
        "audio_bytes": 24_000,
        "failed": False,
    }
    assert lease_events == [
        ("started", "admission-1"),
        ("stopped", "admission-1"),
    ]
    assert upstream.closed is True
    assert websocket.closed is True


def test_zero_audio_commit_completes_without_upstream_commit(monkeypatch):
    """Stopping before the first PCM chunk should finish as no speech."""

    class FakeWebSocket:
        def __init__(self):
            self.incoming = asyncio.Queue()
            self.incoming.put_nowait({"type": "commit"})
            self.outgoing = []
            self.closed = False

        async def accept(self):
            return None

        async def receive_json(self):
            return await self.incoming.get()

        async def send_json(self, payload):
            self.outgoing.append(payload)

        async def close(self):
            self.closed = True

    class FakeUpstream:
        def __init__(self):
            self.sent = []
            self.messages = asyncio.Queue()
            self.closed = False

        async def send(self, raw_payload):
            self.sent.append(json.loads(raw_payload))

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.messages.get()

        async def close(self):
            self.closed = True

    websocket = FakeWebSocket()
    upstream = FakeUpstream()
    finalized = {}
    runtime = transcription.LiveTranscriptionRuntime(
        provider_id="provider-1",
        provider_type="openai",
        model="gpt-live-transcribe",
        delay="low",
        websocket_url="wss://api.openai.com/v1/realtime?intent=transcription",
        headers={},
    )

    async def fake_connect(*_args, **_kwargs):
        return upstream

    async def fake_configure(*_args, **_kwargs):
        return None

    monkeypatch.setattr(transcription, "connect", fake_connect)
    monkeypatch.setattr(
        transcription,
        "_configure_upstream_session",
        fake_configure,
    )
    monkeypatch.setattr(
        transcription,
        "_finalize_live_admission",
        lambda admission_id, **kwargs: finalized.update(
            {"admission_id": admission_id, **kwargs}
        ),
    )

    asyncio.run(
        transcription.proxy_live_transcription(
            websocket,
            runtime=runtime,
            admission_id=None,
            max_duration_seconds=60,
        )
    )

    assert upstream.sent == []
    assert {
        "type": "transcript.completed",
        "item_id": None,
        "transcript": "",
    } in websocket.outgoing
    assert finalized == {
        "admission_id": None,
        "audio_bytes": 0,
        "failed": False,
    }
    assert upstream.closed is True
    assert websocket.closed is True
