"""Authenticated OpenAI Realtime transport for one-way live dictation.

This module intentionally does not share the two-way voice-call runtime. Live
dictation has no chat, assistant response, tools, or persisted conversation
state; it streams microphone PCM, returns transcript events, and closes.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.database import SessionLocal
from app.llm.models import (
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    finalize_duration_rate_limit_admission,
    get_llm_provider,
    renew_dictation_duration_rate_limit_lease,
)
from app.llm.openai.model_list import OPENAI_LIVE_TRANSCRIPTION_MODELS
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.openai.utils import _resolve_openai_client_kwargs
from app.llm.schemas import ProviderEnum, provider_api_key_is_optional
from app.llm.xai.schemas import XAI_DEFAULT_BASE_URL
from app.llm.xai.transcription import XAI_TRANSCRIPTION_MODELS
from app.settings.models import get_settings_page


logger = logging.getLogger(__name__)

LIVE_TRANSCRIPTION_SAMPLE_RATE = 24_000
LIVE_TRANSCRIPTION_BYTES_PER_SAMPLE = 2
LIVE_TRANSCRIPTION_BYTES_PER_SECOND = (
    LIVE_TRANSCRIPTION_SAMPLE_RATE * LIVE_TRANSCRIPTION_BYTES_PER_SAMPLE
)
LIVE_TRANSCRIPTION_MAX_CHUNK_BYTES = 256 * 1024
LIVE_TRANSCRIPTION_MAX_ENCODED_CHUNK_CHARS = (
    ((LIVE_TRANSCRIPTION_MAX_CHUNK_BYTES + 2) // 3) * 4
)
LIVE_TRANSCRIPTION_DEFAULT_MAX_SECONDS = 60 * 60
# Renew often enough to keep a healthy session comfortably inside the
# 90-second dictation lease, even if the event loop experiences brief jitter.
LIVE_TRANSCRIPTION_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
    ProviderEnum.xai.value,
}
LIVE_TRANSCRIPTION_DELAYS = {"minimal", "low", "medium", "high", "xhigh"}


@dataclass(frozen=True)
class LiveTranscriptionRuntime:
    """Resolved non-secret and provider connection data for one socket."""

    provider_id: str
    provider_type: str
    model: str
    delay: str
    websocket_url: str
    headers: dict[str, str]


@dataclass
class XAITranscriptAccumulator:
    """Stitch xAI's interim, chunk-final, and utterance-final STT events."""

    completed_utterances: str = ""
    finalized_chunks: str = ""
    interim: str = ""

    @staticmethod
    def _join(*parts: str) -> str:
        """Join transcript pieces without accumulating provider whitespace."""
        return " ".join(
            str(part or "").strip()
            for part in parts
            if str(part or "").strip()
        )

    def update(self, text: Any, *, is_final: bool, speech_final: bool) -> str:
        """Apply one xAI partial event and return the full visible transcript."""
        normalized_text = str(text or "").strip()
        if is_final and speech_final:
            # An utterance-final event is already stitched by xAI, so it
            # replaces its preceding chunk-final pieces instead of duplicating
            # them. Earlier utterances remain locked for continued dictation.
            self.completed_utterances = self._join(
                self.completed_utterances,
                normalized_text,
            )
            self.finalized_chunks = ""
            self.interim = ""
        elif is_final:
            self.finalized_chunks = self._join(
                self.finalized_chunks,
                normalized_text,
            )
            self.interim = ""
        else:
            self.interim = normalized_text
        return self._join(
            self.completed_utterances,
            self.finalized_chunks,
            self.interim,
        )


def _build_openai_realtime_websocket_url(base_url: Any) -> str:
    """Convert an OpenAI-compatible HTTP API root to a transcription WS URL.

    Realtime conversation models are selected in the WebSocket URL with a
    ``model`` query parameter. Transcription-only sessions are different: the
    connection must use ``intent=transcription`` and the transcription model is
    selected later in ``session.update``. Passing ``gpt-live-transcribe`` as the
    connection model makes OpenAI close the socket with ``invalid_model``.
    """
    raw_base_url = str(base_url or "https://api.openai.com/v1").strip().rstrip("/")
    parsed = urlparse(raw_base_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Live transcription provider base URL is invalid",
        )
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    path = parsed.path.rstrip("/")
    if not path.endswith("/realtime"):
        path = f"{path}/realtime"
    # Preserve provider-specific query parameters such as Azure's API version,
    # but replace any caller-supplied mode selector with the only mode this
    # endpoint supports.
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"intent", "model"}
    ]
    query.append(("intent", "transcription"))
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            path,
            "",
            urlencode(query),
            "",
        )
    )


def _build_xai_stt_websocket_url(
    base_url: Any,
    *,
    settings: dict[str, Any] | None = None,
) -> str:
    """Convert the xAI API root and native dictation settings to a STT URL."""
    parsed = urlparse(
        str(base_url or XAI_DEFAULT_BASE_URL).strip().rstrip("/")
    )
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Live transcription provider base URL is invalid",
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/stt"):
        path = f"{path}/stt"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower()
        not in {
            "sample_rate",
            "encoding",
            "interim_results",
            "endpointing",
            "language",
            "multichannel",
            "channels",
            "diarize",
            "keyterm",
            "filler_words",
            "smart_turn",
            "smart_turn_timeout",
            "vad_threshold",
        }
    ]
    query.extend(
        [
            ("sample_rate", str(LIVE_TRANSCRIPTION_SAMPLE_RATE)),
            ("encoding", "pcm"),
            ("interim_results", "true"),
        ]
    )
    native_settings = settings if isinstance(settings, dict) else {}
    endpointing = native_settings.get("live_transcription_xai_endpointing_ms", 10)
    if isinstance(endpointing, int) and 0 <= endpointing <= 5000:
        query.append(("endpointing", str(endpointing)))
    language = str(
        native_settings.get("live_transcription_xai_language") or ""
    ).strip()
    if language:
        query.append(("language", language[:35]))
    keyterms = native_settings.get("live_transcription_xai_keyterms")
    if isinstance(keyterms, list):
        for keyterm in keyterms[:100]:
            normalized_keyterm = str(keyterm or "").strip()
            if normalized_keyterm:
                query.append(("keyterm", normalized_keyterm[:50]))
    if bool(native_settings.get("live_transcription_xai_filler_words", False)):
        query.append(("filler_words", "true"))
    smart_turn = native_settings.get("live_transcription_xai_smart_turn")
    if isinstance(smart_turn, (int, float)) and not isinstance(smart_turn, bool):
        if 0.0 <= float(smart_turn) <= 1.0:
            query.append(("smart_turn", str(float(smart_turn))))
            smart_turn_timeout = native_settings.get(
                "live_transcription_xai_smart_turn_timeout_ms"
            )
            if isinstance(smart_turn_timeout, int) and 1 <= smart_turn_timeout <= 5000:
                query.append(("smart_turn_timeout", str(smart_turn_timeout)))
    vad_threshold = native_settings.get("live_transcription_xai_vad_threshold", 0.08)
    if isinstance(vad_threshold, (int, float)) and not isinstance(vad_threshold, bool):
        if 0.0 <= float(vad_threshold) <= 1.0:
            query.append(("vad_threshold", str(float(vad_threshold))))
    return urlunparse(
        (
            "wss" if parsed.scheme in {"https", "wss"} else "ws",
            parsed.netloc,
            path,
            "",
            urlencode(query),
            "",
        )
    )


def load_live_transcription_runtime(db: Session) -> LiveTranscriptionRuntime:
    """Validate settings and resolve credentials for live dictation."""
    record = get_settings_page(db, "dictation")
    data = record.data if record and isinstance(record.data, dict) else {}
    if not bool(data.get("live_transcription_enabled")):
        raise HTTPException(status_code=400, detail="Live transcription is disabled")

    provider_id = str(data.get("live_transcription_provider_id") or "").strip()
    model = str(data.get("live_transcription_model") or "").strip()
    delay = str(data.get("live_transcription_delay") or "low").strip().lower()
    if not provider_id:
        raise HTTPException(
            status_code=400,
            detail="Live transcription provider is not configured",
        )
    provider = get_llm_provider(db, provider_id)
    supported_models = (
        XAI_TRANSCRIPTION_MODELS
        if provider and provider.provider == ProviderEnum.xai.value
        else OPENAI_LIVE_TRANSCRIPTION_MODELS
    )
    if model not in supported_models:
        raise HTTPException(
            status_code=400,
            detail="Live transcription model is not supported",
        )
    if (
        provider
        and provider.provider != ProviderEnum.xai.value
        and delay not in LIVE_TRANSCRIPTION_DELAYS
    ):
        raise HTTPException(
            status_code=400,
            detail="Live transcription delay is invalid",
        )

    if not provider or provider.provider not in LIVE_TRANSCRIPTION_PROVIDER_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Live transcription provider type is not supported",
        )
    if not provider.api_key and not provider_api_key_is_optional(provider.provider):
        raise HTTPException(
            status_code=400,
            detail="Live transcription provider API key is missing",
        )

    if provider.provider == ProviderEnum.xai.value:
        provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
        client_kwargs = {
            "api_key": provider.api_key,
            "base_url": provider_settings.get("base_url") or XAI_DEFAULT_BASE_URL,
            "default_headers": custom_headers_to_dict(
                provider_settings.get("custom_headers")
            ),
        }
    else:
        client_kwargs = _resolve_openai_client_kwargs(
            db,
            openai_provider_id=provider.id,
            byok=None,
            openai_provider_type=provider.provider,
        )
    headers: dict[str, str] = {}
    default_headers = client_kwargs.get("default_headers")
    if isinstance(default_headers, dict):
        forbidden_headers = {
            "connection",
            "content-length",
            "host",
            "upgrade",
        }
        headers.update(
            {
                str(key): str(value)
                for key, value in default_headers.items()
                if (
                    str(key).strip()
                    and value is not None
                    and str(key).strip().lower() not in forbidden_headers
                    and not str(key).strip().lower().startswith("sec-websocket-")
                )
            }
        )
    api_key = str(client_kwargs.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    organization = str(client_kwargs.get("organization") or "").strip()
    project = str(client_kwargs.get("project") or "").strip()
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project

    return LiveTranscriptionRuntime(
        provider_id=provider.id,
        provider_type=provider.provider,
        model=model,
        delay=delay,
        websocket_url=(
            _build_xai_stt_websocket_url(
                client_kwargs.get("base_url"),
                settings=data,
            )
            if provider.provider == ProviderEnum.xai.value
            else _build_openai_realtime_websocket_url(
                client_kwargs.get("base_url")
            )
        ),
        headers=headers,
    )


def _build_session_update(runtime: LiveTranscriptionRuntime) -> dict[str, Any]:
    """Build the minimal OpenAI transcription-session configuration."""
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": LIVE_TRANSCRIPTION_SAMPLE_RATE,
                    },
                    "transcription": {
                        "model": runtime.model,
                        "delay": runtime.delay,
                    },
                    # Omlorix dictation is press-to-record. Explicit commit on
                    # stop gives deterministic one-utterance finalization.
                    "turn_detection": None,
                }
            },
        },
    }


class LiveTranscriptionProviderError(RuntimeError):
    """A structured provider error received while configuring the session."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _browser_provider_error_code(provider_code: Any) -> str:
    """Map OpenAI errors to stable, non-ambiguous browser-facing codes.

    Omlorix has its own per-user duration quotas. Provider throttling must use a
    different code so the frontend never tells a user that their Omlorix minute
    budget is exhausted when OpenAI is the layer rejecting the request.
    """
    normalized = str(provider_code or "").strip().lower()
    if normalized in {
        "rate_limited",
        "rate_limit_exceeded",
        "too_many_requests",
    } or "rate_limit" in normalized:
        return "provider_rate_limited"
    if normalized in {"insufficient_quota", "billing_hard_limit_reached"}:
        return "provider_quota_exceeded"
    return normalized or "provider_error"


async def _configure_upstream_session(
    upstream: Any,
    runtime: LiveTranscriptionRuntime,
) -> None:
    """Configure OpenAI and wait until it confirms the transcription session.

    Waiting for ``session.updated`` prevents the browser from starting its
    microphone when OpenAI has already rejected the transport or configuration.
    ``session.created`` and other harmless setup events are consumed here; no
    transcript event can arrive before audio capture begins.
    """
    if runtime.provider_type == ProviderEnum.xai.value:
        # xAI configures streaming STT in the URL and signals readiness with a
        # transcript.created event. Audio sent before that event can be lost.
        while True:
            raw_message = await asyncio.wait_for(upstream.recv(), timeout=20)
            if not isinstance(raw_message, str):
                continue
            try:
                event = json.loads(raw_message)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type == "transcript.created":
                return
            if event_type == "error":
                raise LiveTranscriptionProviderError(
                    _browser_provider_error_code(event.get("code")),
                    str(event.get("message") or "") or None,
                )

    await upstream.send(json.dumps(_build_session_update(runtime)))
    while True:
        raw_message = await asyncio.wait_for(upstream.recv(), timeout=20)
        if not isinstance(raw_message, str):
            continue
        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "session.updated":
            return
        if event_type != "error":
            continue
        provider_error = event.get("error")
        code = "provider_error"
        detail = None
        if isinstance(provider_error, dict):
            code = str(provider_error.get("code") or code)
            detail_value = provider_error.get("message")
            detail = str(detail_value) if detail_value else None
        raise LiveTranscriptionProviderError(
            _browser_provider_error_code(code),
            detail,
        )


def _finalize_live_admission(
    admission_id: str | None,
    *,
    audio_bytes: int,
    failed: bool,
) -> None:
    """Persist actual decoded audio time using a short independent session."""
    if not admission_id:
        return
    consumed_seconds = (
        max(1, int(math.ceil(audio_bytes / LIVE_TRANSCRIPTION_BYTES_PER_SECOND)))
        if audio_bytes
        else 0
    )
    db = SessionLocal()
    try:
        finalize_duration_rate_limit_admission(
            db,
            admission_id,
            consumed_seconds=consumed_seconds,
            final_status=(
                RATE_LIMIT_ADMISSION_FAILED
                if failed
                else RATE_LIMIT_ADMISSION_COMPLETED
            ),
        )
    finally:
        db.close()


async def proxy_live_transcription(
    websocket: WebSocket,
    *,
    runtime: LiveTranscriptionRuntime,
    admission_id: str | None,
    max_duration_seconds: int,
) -> None:
    """Proxy a bounded browser PCM stream to OpenAI and relay transcripts."""
    audio_bytes = 0
    committed = False
    completed = False
    failed = False
    max_audio_bytes = max(1, int(max_duration_seconds)) * LIVE_TRANSCRIPTION_BYTES_PER_SECOND

    # Runtime, admission, and audit checks happen before the handshake is
    # accepted. Safari may close a timed-out attempt during those checks. In
    # that case Uvicorn rejects a late websocket.accept() with RuntimeError;
    # release the reserved duration without emitting an ASGI traceback.
    try:
        await websocket.accept()
    except (RuntimeError, WebSocketDisconnect):
        await asyncio.to_thread(
            _finalize_live_admission,
            admission_id,
            audio_bytes=0,
            failed=True,
        )
        return

    # Renew for the complete proxy lifetime, including provider setup and final
    # transcript generation after the browser has stopped sending audio.
    lease_renewal_task = (
        asyncio.create_task(
            renew_dictation_duration_rate_limit_lease(admission_id)
        )
        if admission_id
        else None
    )
    upstream = None

    async def send_browser(payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            pass

    try:
        upstream = await connect(
            runtime.websocket_url,
            additional_headers=runtime.headers,
            max_size=2 * 1024 * 1024,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=20,
        )
        await _configure_upstream_session(upstream, runtime)
        await send_browser(
            {
                "type": "ready",
                "sample_rate": LIVE_TRANSCRIPTION_SAMPLE_RATE,
                "max_duration_seconds": max_duration_seconds,
            }
        )

        async def browser_to_provider() -> None:
            nonlocal audio_bytes, committed, completed
            while True:
                try:
                    payload = await websocket.receive_json()
                except WebSocketDisconnect:
                    return
                if not isinstance(payload, dict):
                    continue
                event_type = str(payload.get("type") or "").strip()
                if event_type == "close":
                    return
                if event_type == "commit":
                    if committed:
                        continue
                    if audio_bytes <= 0:
                        # OpenAI commits an appended audio turn; there is no
                        # upstream turn to commit when the user stops before
                        # the browser produces its first PCM chunk. Complete
                        # that valid no-speech interaction locally so the
                        # browser does not wait for its completion timeout.
                        committed = True
                        completed = True
                        await send_browser(
                            {
                                "type": "transcript.completed",
                                "item_id": None,
                                "transcript": "",
                            }
                        )
                        return
                    committed = True
                    await upstream.send(
                        json.dumps(
                            {
                                "type": (
                                    "audio.done"
                                    if runtime.provider_type
                                    == ProviderEnum.xai.value
                                    else "input_audio_buffer.commit"
                                )
                            }
                        )
                    )
                    continue
                if event_type != "audio" or committed:
                    continue

                encoded_audio = payload.get("audio")
                if (
                    not isinstance(encoded_audio, str)
                    or not encoded_audio
                    or len(encoded_audio)
                    > LIVE_TRANSCRIPTION_MAX_ENCODED_CHUNK_CHARS
                ):
                    await send_browser(
                        {"type": "error", "code": "invalid_audio"}
                    )
                    continue
                try:
                    decoded = base64.b64decode(encoded_audio, validate=True)
                except (binascii.Error, ValueError):
                    await send_browser(
                        {"type": "error", "code": "invalid_audio"}
                    )
                    continue
                if not decoded or len(decoded) > LIVE_TRANSCRIPTION_MAX_CHUNK_BYTES:
                    await send_browser(
                        {"type": "error", "code": "invalid_audio"}
                    )
                    continue
                if audio_bytes + len(decoded) > max_audio_bytes:
                    await send_browser(
                        {"type": "limit_reached", "code": "duration_limit"}
                    )
                    # Commit everything already accepted before the limit and
                    # remain connected for the provider's final transcript.
                    # The committed guard below ignores all later audio frames.
                    committed = True
                    await upstream.send(
                        json.dumps(
                            {
                                "type": (
                                    "audio.done"
                                    if runtime.provider_type
                                    == ProviderEnum.xai.value
                                    else "input_audio_buffer.commit"
                                )
                            }
                        )
                    )
                    continue

                audio_bytes += len(decoded)
                if runtime.provider_type == ProviderEnum.xai.value:
                    await upstream.send(decoded)
                else:
                    await upstream.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": encoded_audio,
                            }
                        )
                    )

        async def provider_to_browser() -> None:
            nonlocal completed, failed
            xai_transcripts: dict[str, XAITranscriptAccumulator] = {}
            try:
                async for raw_message in upstream:
                    if not isinstance(raw_message, str):
                        continue
                    try:
                        event = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get("type") or "")
                    if (
                        runtime.provider_type == ProviderEnum.xai.value
                        and event_type == "transcript.partial"
                    ):
                        item_id = str(
                            event.get("channel_index")
                            if event.get("channel_index") is not None
                            else "xai"
                        )
                        transcript = xai_transcripts.setdefault(
                            item_id,
                            XAITranscriptAccumulator(),
                        ).update(
                            event.get("text"),
                            is_final=bool(event.get("is_final")),
                            speech_final=bool(event.get("speech_final")),
                        )
                        await send_browser(
                            {
                                "type": "transcript.updated",
                                "item_id": item_id,
                                # Browser composers receive a provider-neutral
                                # full transcript and can safely replace their
                                # current partial without losing old sentences.
                                "transcript": transcript,
                            }
                        )
                    elif (
                        runtime.provider_type == ProviderEnum.xai.value
                        and event_type == "transcript.done"
                    ):
                        completed = True
                        await send_browser(
                            {
                                "type": "transcript.completed",
                                "item_id": (
                                    event.get("channel_index")
                                    if event.get("channel_index") is not None
                                    else "xai"
                                ),
                                "transcript": str(event.get("text") or ""),
                            }
                        )
                        return
                    elif event_type == "conversation.item.input_audio_transcription.delta":
                        await send_browser(
                            {
                                "type": "transcript.delta",
                                "item_id": event.get("item_id"),
                                "delta": str(event.get("delta") or ""),
                            }
                        )
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        completed = True
                        await send_browser(
                            {
                                "type": "transcript.completed",
                                "item_id": event.get("item_id"),
                                "transcript": str(event.get("transcript") or ""),
                            }
                        )
                        # A completed transcript is terminal for Omlorix's
                        # one-utterance session. Returning also lets a
                        # duration-limit commit finish without waiting for the
                        # browser to close first.
                        return
                    elif event_type == "error":
                        failed = True
                        provider_error = event.get("error")
                        provider_code = (
                            str(provider_error.get("code") or "provider_error")
                            if isinstance(provider_error, dict)
                            else "provider_error"
                        )
                        await send_browser(
                            {
                                "type": "error",
                                "code": _browser_provider_error_code(
                                    provider_code
                                ),
                            }
                        )
            except ConnectionClosed:
                return

        browser_task = asyncio.create_task(browser_to_provider())
        provider_task = asyncio.create_task(provider_to_browser())
        done, pending = await asyncio.wait(
            {browser_task, provider_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except LiveTranscriptionProviderError as exc:
        failed = True
        logger.warning(
            "Live transcription provider rejected session configuration: %s",
            exc.code,
        )
        await send_browser({"type": "error", "code": exc.code})
    except Exception:
        failed = True
        logger.exception("Live transcription proxy failed")
        await send_browser({"type": "error", "code": "connection_failed"})
    finally:
        if lease_renewal_task is not None:
            lease_renewal_task.cancel()
            await asyncio.gather(
                lease_renewal_task,
                return_exceptions=True,
            )
        if upstream is not None:
            await upstream.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass
        await asyncio.to_thread(
            _finalize_live_admission,
            admission_id,
            audio_bytes=audio_bytes,
            failed=failed or (committed and not completed),
        )
