from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import threading
from typing import Any
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.database import SessionLocal
from app.llm.google_aistudio.realtime import (
    build_google_aistudio_live_client_setup,
    build_google_aistudio_live_connect_config,
    build_google_aistudio_live_websocket_url,
    mint_google_aistudio_live_ephemeral_token,
)
from app.llm.models import touch_duration_rate_limit_admission
from app.llm.models import get_llm_provider
from app.llm.xai.realtime import (
    build_xai_realtime_headers,
    build_xai_realtime_websocket_url,
)
from app.realtime import service as realtime_service


logger = logging.getLogger(__name__)

GOOGLE_LIVE_PROXY_SETUP_TIMEOUT_SECONDS = 20
GOOGLE_LIVE_PROXY_HEARTBEAT_SECONDS = 10
GOOGLE_LIVE_PROXY_ADMISSION_RENEWAL_SECONDS = 60
GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
OPENAI_REALTIME_MONITOR_SETUP_TIMEOUT_SECONDS = 20
OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS = 1.0
OPENAI_REALTIME_MONITOR_HEARTBEAT_SECONDS = 10
OPENAI_REALTIME_MONITOR_ADMISSION_RENEWAL_SECONDS = 60
OPENAI_REALTIME_MONITOR_MAX_MESSAGE_BYTES = 2 * 1024 * 1024

# OpenAI WebRTC calls require one process-owned sideband monitor thread. These
# backend-owned ceilings always apply, including when no optional user duration
# quota is configured. Keep the per-user value below the process-wide value.
OPENAI_REALTIME_MONITOR_MAX_TOTAL = 128
OPENAI_REALTIME_MONITOR_MAX_PER_USER = 4


@dataclass
class _GoogleProxyController:
    """Thread-safe close handle for one process-owned Gemini proxy."""

    connection_id: str
    loop: asyncio.AbstractEventLoop
    close_event: asyncio.Event

    def request_close(self) -> None:
        """Wake the owning event loop even when called by a sync HTTP worker."""
        self.loop.call_soon_threadsafe(self.close_event.set)


class _GoogleProxyRegistry:
    """Track one active proxy and at most one reconnect candidate per runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, _GoogleProxyController] = {}
        self._candidates: dict[str, _GoogleProxyController] = {}

    def reserve_candidate(
        self,
        session_id: str,
        controller: _GoogleProxyController,
    ) -> bool:
        with self._lock:
            if session_id in self._candidates:
                return False
            self._candidates[session_id] = controller
            return True

    def promote(
        self,
        session_id: str,
        controller: _GoogleProxyController,
    ) -> _GoogleProxyController | None:
        with self._lock:
            candidate = self._candidates.get(session_id)
            if not candidate or candidate.connection_id != controller.connection_id:
                return None
            self._candidates.pop(session_id, None)
            previous = self._active.get(session_id)
            self._active[session_id] = controller
            return previous

    def release(self, session_id: str, connection_id: str) -> bool:
        """Release a candidate/current controller and report current ownership."""
        with self._lock:
            candidate = self._candidates.get(session_id)
            if candidate and candidate.connection_id == connection_id:
                self._candidates.pop(session_id, None)
            current = self._active.get(session_id)
            if current and current.connection_id == connection_id:
                self._active.pop(session_id, None)
                return True
            return False

    def request_close(self, session_id: str) -> bool:
        with self._lock:
            # Setup candidates must be stoppable too. Otherwise a stop arriving
            # between the persisted claim and promotion cannot wake the proxy.
            controller = self._active.get(session_id) or self._candidates.get(session_id)
        if not controller:
            return False
        controller.request_close()
        return True


_google_proxy_registry = _GoogleProxyRegistry()


@dataclass
class _OpenAIRealtimeMonitorController:
    """Thread-safe lifecycle state for one server-side Realtime monitor."""

    connection_id: str
    close_event: threading.Event
    startup_event: threading.Event
    user_id: str = ""
    started: bool = False

    def request_close(self) -> None:
        """Wake the monitor without depending on its private asyncio loop."""
        self.close_event.set()


class _OpenAIRealtimeMonitorRegistry:
    """Keep one server-owned sideband monitor per direct WebRTC call."""

    def __init__(
        self,
        *,
        max_total: int = OPENAI_REALTIME_MONITOR_MAX_TOTAL,
        max_per_user: int = OPENAI_REALTIME_MONITOR_MAX_PER_USER,
    ) -> None:
        self._lock = threading.Lock()
        self._controllers: dict[str, _OpenAIRealtimeMonitorController] = {}
        self._max_total = max(1, int(max_total))
        self._max_per_user = min(
            self._max_total,
            max(1, int(max_per_user)),
        )

    def reserve(
        self,
        session_id: str,
        controller: _OpenAIRealtimeMonitorController,
    ) -> bool:
        with self._lock:
            if session_id in self._controllers:
                return False
            if len(self._controllers) >= self._max_total:
                return False
            owner_id = str(controller.user_id or "").strip()
            owner_monitor_count = sum(
                1
                for existing in self._controllers.values()
                if str(existing.user_id or "").strip() == owner_id
            )
            if owner_monitor_count >= self._max_per_user:
                return False
            self._controllers[session_id] = controller
            return True

    def release(self, session_id: str, connection_id: str) -> None:
        with self._lock:
            current = self._controllers.get(session_id)
            if current and current.connection_id == connection_id:
                self._controllers.pop(session_id, None)

    def request_close(self, session_id: str) -> bool:
        with self._lock:
            controller = self._controllers.get(session_id)
        if controller is None:
            return False
        controller.request_close()
        return True


_openai_monitor_registry = _OpenAIRealtimeMonitorRegistry()


def signal_google_proxy_stop(session_id: str) -> bool:
    """Ask a locally owned Gemini proxy to close immediately."""
    return _google_proxy_registry.request_close(str(session_id or "").strip())


def signal_openai_realtime_monitor_stop(session_id: str) -> bool:
    """Ask a locally owned OpenAI sideband monitor to close immediately."""
    return _openai_monitor_registry.request_close(str(session_id or "").strip())


def _claim_openai_realtime_monitor(
    session_id: str,
    connection_id: str,
) -> tuple[str, dict[str, str], float]:
    """Claim persisted monitor ownership and build the authenticated URL."""
    db = SessionLocal()
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider
                    not in realtime_service.OPENAI_REALTIME_PROVIDER_TYPES
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                    or not runtime.provider_session_handle
                    or runtime.is_expired()
                    or runtime.provider_connection_owner_id
                    not in {None, connection_id}
                ):
                    raise RuntimeError("Realtime call is unavailable for monitoring")

                request_settings = (
                    realtime_service._load_openai_realtime_request_settings(
                        db,
                        provider_id=runtime.provider_id,
                        provider_type=runtime.provider,
                    )
                )
                http_base_url = realtime_service._resolve_realtime_http_base_url(
                    request_settings.get("base_url")
                )
                parsed_base_url = urlparse(http_base_url)
                websocket_scheme = (
                    "wss" if parsed_base_url.scheme == "https" else "ws"
                )
                websocket_path = f"{parsed_base_url.path.rstrip('/')}/realtime"
                monitor_url = urlunparse(
                    (
                        websocket_scheme,
                        parsed_base_url.netloc,
                        websocket_path,
                        "",
                        urlencode({"call_id": runtime.provider_session_handle}),
                        "",
                    )
                )
                headers = realtime_service._build_realtime_headers(request_settings)
                headers.pop("Content-Type", None)

                now = realtime_service._utc_now()
                runtime.provider_connection_owner_id = connection_id
                runtime.provider_connection_last_seen_at = now
                runtime.last_activity_at = now
                realtime_service.persist_realtime_runtime_state(
                    db,
                    runtime,
                    provider_state_authoritative=True,
                )
                remaining_lifetime_seconds = max(
                    (runtime.absolute_expires_at() - now).total_seconds(),
                    0.0,
                )
                return monitor_url, headers, remaining_lifetime_seconds
    finally:
        db.close()


def _heartbeat_openai_realtime_monitor_once(
    session_id: str,
    connection_id: str,
) -> bool:
    """Persist liveness observed from the authenticated sideband socket."""
    db = SessionLocal()
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                    or runtime.is_expired()
                ):
                    return False
                now = realtime_service._utc_now()
                runtime.provider_connection_last_seen_at = now
                runtime.last_activity_at = now
                realtime_service.persist_realtime_runtime_state(
                    db,
                    runtime,
                    provider_state_authoritative=True,
                )
                return True
    finally:
        db.close()


def _renew_openai_realtime_admission_once(
    session_id: str,
    connection_id: str,
) -> bool:
    """Renew quota only while this process owns a live sideband socket."""
    db = SessionLocal()
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                    or runtime.is_expired()
                ):
                    return False
                admission_id = runtime.rate_limit_admission_id

        if not admission_id or touch_duration_rate_limit_admission(db, admission_id):
            return True

        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                ):
                    return False
                realtime_service.request_provider_session_termination(
                    db,
                    runtime,
                    reason="reservation_expired",
                )
                return False
    finally:
        db.close()


def _finalize_openai_realtime_monitor(
    session_id: str,
    connection_id: str,
) -> None:
    """End the call if its authoritative sideband monitor disappears."""
    db = SessionLocal()
    terminated = False
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                ):
                    return
                terminated = realtime_service._mark_runtime_inactive_locked(
                    db,
                    runtime,
                    reason="provider_disconnected",
                    status="stopped",
                    # Even a clean sideband close is not sufficient proof that
                    # the peer-media call ended. The hangup endpoint is
                    # idempotent, so always confirm termination explicitly.
                    provider_already_terminated=False,
                )
    finally:
        db.close()
    if terminated:
        realtime_service.session_registry.remove(session_id)


async def _wait_for_thread_event(event: threading.Event) -> None:
    """Wait for a threading event without leaving a blocked executor worker."""
    while not event.is_set():
        await asyncio.sleep(0.25)


async def _observe_openai_realtime_events(upstream) -> None:
    """Keep the sideband transport live while discarding provider events."""
    async for _message in upstream:
        pass


async def _heartbeat_openai_realtime_monitor(
    session_id: str,
    connection_id: str,
) -> None:
    """Publish sideband liveness at a bounded interval."""
    while True:
        await asyncio.sleep(OPENAI_REALTIME_MONITOR_HEARTBEAT_SECONDS)
        if not await asyncio.to_thread(
            _heartbeat_openai_realtime_monitor_once,
            session_id,
            connection_id,
        ):
            return


async def _renew_openai_realtime_admission(
    session_id: str,
    connection_id: str,
) -> None:
    """Renew the reservation independently of monitor heartbeats."""
    while True:
        await asyncio.sleep(OPENAI_REALTIME_MONITOR_ADMISSION_RENEWAL_SECONDS)
        if not await asyncio.to_thread(
            _renew_openai_realtime_admission_once,
            session_id,
            connection_id,
        ):
            return


async def _run_openai_realtime_monitor(
    session_id: str,
    controller: _OpenAIRealtimeMonitorController,
) -> None:
    """Own one OpenAI sideband socket for the lifetime of a WebRTC call."""
    upstream = None
    try:
        monitor_url, headers, remaining_lifetime_seconds = await asyncio.to_thread(
            _claim_openai_realtime_monitor,
            session_id,
            controller.connection_id,
        )
        upstream = await connect(
            monitor_url,
            additional_headers=headers,
            max_size=OPENAI_REALTIME_MONITOR_MAX_MESSAGE_BYTES,
            open_timeout=OPENAI_REALTIME_MONITOR_SETUP_TIMEOUT_SECONDS,
            close_timeout=5,
        )
        provider_task = asyncio.create_task(
            _observe_openai_realtime_events(upstream)
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_openai_realtime_monitor(
                session_id,
                controller.connection_id,
            )
        )
        renewal_task = asyncio.create_task(
            _renew_openai_realtime_admission(
                session_id,
                controller.connection_id,
            )
        )
        deadline_task = asyncio.create_task(asyncio.sleep(remaining_lifetime_seconds))
        close_task = asyncio.create_task(_wait_for_thread_event(controller.close_event))
        done, pending = await asyncio.wait(
            {
                provider_task,
                heartbeat_task,
                renewal_task,
                deadline_task,
                close_task,
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception() if not task.cancelled() else None
            if exception:
                raise exception
    except ConnectionClosed:
        pass
    except Exception:
        logger.exception(
            "OpenAI Realtime sideband monitor failed for session %s",
            session_id,
        )
    finally:
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:
                logger.debug(
                    "Failed to close OpenAI sideband for session %s",
                    session_id,
                )
        await asyncio.to_thread(
            _finalize_openai_realtime_monitor,
            session_id,
            controller.connection_id,
        )


def _openai_realtime_monitor_thread(
    session_id: str,
    controller: _OpenAIRealtimeMonitorController,
) -> None:
    """Run one async monitor in an isolated daemon thread."""
    # Acknowledge that the daemon owns the monitor before beginning the
    # provider handshake. The handshake retains its independent, longer
    # timeout and can therefore finish without blocking the signaling request.
    controller.started = True
    controller.startup_event.set()
    try:
        asyncio.run(_run_openai_realtime_monitor(session_id, controller))
    finally:
        controller.startup_event.set()
        _openai_monitor_registry.release(session_id, controller.connection_id)


def _launch_openai_realtime_monitor(
    session_id: str,
    user_id: str | None = None,
) -> _OpenAIRealtimeMonitorController | None:
    """Reserve and launch a daemon that owns one OpenAI sideband monitor."""
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    controller = _OpenAIRealtimeMonitorController(
        connection_id=str(uuid.uuid4()),
        close_event=threading.Event(),
        startup_event=threading.Event(),
        # Callers outside the HTTP router retain a stable scope rather than
        # bypassing the per-owner cap with an empty identifier.
        user_id=str(user_id or "__unscoped__").strip(),
    )
    if not _openai_monitor_registry.reserve(normalized_session_id, controller):
        return None
    thread = threading.Thread(
        target=_openai_realtime_monitor_thread,
        args=(normalized_session_id, controller),
        daemon=True,
        name=f"realtime-monitor-{normalized_session_id[:12]}",
    )
    try:
        thread.start()
    except Exception:
        _openai_monitor_registry.release(
            normalized_session_id,
            controller.connection_id,
        )
        logger.exception(
            "Failed to launch OpenAI Realtime monitor for session %s",
            normalized_session_id,
        )
        return None
    return controller


def _openai_realtime_monitor_start_result(
    controller: _OpenAIRealtimeMonitorController,
    startup_acknowledged: bool,
) -> bool:
    """Resolve a monitor launch while preserving timeout cleanup semantics."""
    if not startup_acknowledged:
        controller.request_close()
        return False
    return controller.started


def start_openai_realtime_monitor(
    session_id: str,
    *,
    user_id: str | None = None,
) -> bool:
    """Start a server-owned sideband monitor from synchronous code."""
    controller = (
        _launch_openai_realtime_monitor(session_id)
        if user_id is None
        else _launch_openai_realtime_monitor(session_id, user_id)
    )
    if controller is None:
        return False
    startup_acknowledged = controller.startup_event.wait(
        timeout=OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS,
    )
    return _openai_realtime_monitor_start_result(
        controller,
        startup_acknowledged,
    )


async def start_openai_realtime_monitor_async(
    session_id: str,
    *,
    user_id: str | None = None,
) -> bool:
    """Start a sideband monitor without blocking a caller's event loop."""
    controller = (
        _launch_openai_realtime_monitor(session_id)
        if user_id is None
        else _launch_openai_realtime_monitor(session_id, user_id)
    )
    if controller is None:
        return False
    startup_acknowledged = await asyncio.to_thread(
        controller.startup_event.wait,
        timeout=OPENAI_REALTIME_MONITOR_STARTUP_ACK_TIMEOUT_SECONDS,
    )
    return _openai_realtime_monitor_start_result(
        controller,
        startup_acknowledged,
    )


def _google_provider_setup(db, runtime) -> tuple[str, dict[str, Any]]:
    """Mint a single-use server-held token and return its upstream setup."""
    native_google_search_enabled = bool(
        runtime.model_settings.get("native_websearch")
        and "web_search" in runtime.tools
    )
    provider_config = build_google_aistudio_live_connect_config(
        instructions=realtime_service.build_realtime_instructions(runtime),
        model_name=runtime.realtime_model,
        voice=runtime.voice,
        settings=runtime.settings,
        tool_schemas=runtime.tool_schemas,
        session_handle=runtime.google_session_handle,
        native_google_search_enabled=native_google_search_enabled,
    )
    token_name = mint_google_aistudio_live_ephemeral_token(
        db=db,
        provider_id=runtime.provider_id,
        model_name=runtime.realtime_model,
        session_config=provider_config,
    )
    return (
        build_google_aistudio_live_websocket_url(token_name),
        {
            "setup": build_google_aistudio_live_client_setup(
                model_name=runtime.realtime_model,
            )
        },
    )


def _is_google_setup_complete(message: str | bytes) -> bool:
    try:
        raw = message.decode("utf-8") if isinstance(message, bytes) else message
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and "setupComplete" in payload


def _claim_google_proxy_connection(session_id: str, connection_id: str):
    """Claim persisted provider ownership using a thread-confined DB session."""
    db = SessionLocal()
    try:
        return realtime_service.claim_google_proxy_connection(
            db,
            session_id=session_id,
            connection_id=connection_id,
        )
    finally:
        db.close()


def _build_google_proxy_setup(runtime) -> tuple[str, dict[str, Any]]:
    """Build provider setup without running credential DB reads on the loop."""
    db = SessionLocal()
    try:
        return _google_provider_setup(db, runtime)
    finally:
        db.close()


def _heartbeat_google_proxy_once(session_id: str, connection_id: str) -> bool:
    """Persist one lightweight, fenced proxy-ownership heartbeat."""
    db = SessionLocal()
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                    or runtime.is_expired()
                ):
                    return False
                now = realtime_service._utc_now()
                runtime.provider_connection_last_seen_at = now
                runtime.last_activity_at = now
                realtime_service.persist_realtime_runtime_state(
                    db,
                    runtime,
                    provider_state_authoritative=True,
                )
                return True
    finally:
        db.close()


def _renew_google_proxy_admission_once(
    session_id: str,
    connection_id: str,
) -> bool:
    """Renew quota without letting a slow quota store delay proxy heartbeats.

    No provider state captured before the potentially blocking admission call
    is written afterward. If renewal fails, provider state is restored again
    under the transition lock before termination is requested.
    """
    db = SessionLocal()
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                    or runtime.is_expired()
                ):
                    return False
                admission_id = runtime.rate_limit_admission_id

        if not admission_id or touch_duration_rate_limit_admission(db, admission_id):
            return True

        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    runtime is None
                    or not runtime.active
                    or runtime.provider_connection_owner_id != connection_id
                    or runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                ):
                    return False
                realtime_service.request_provider_session_termination(
                    db,
                    runtime,
                    reason="reservation_expired",
                )
                return False
    finally:
        db.close()


def _activate_google_proxy_connection(session_id: str, connection_id: str) -> float:
    """Validate and persist an established upstream connection off the loop."""
    db = SessionLocal()
    try:
        # Use the same lock order as mark_runtime_inactive. A stop that wins the
        # race persists termination_pending; activation must then refuse to
        # clear it and the proxy finally block closes the upstream socket.
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                current_runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    current_runtime is None
                    or not current_runtime.active
                    or current_runtime.is_expired()
                    or current_runtime.provider_connection_state
                    != realtime_service.REALTIME_PROVIDER_CONNECTION_CONNECTING
                ):
                    raise RuntimeError("Realtime session stopped during Gemini setup")
                if current_runtime.provider_connection_owner_id != connection_id:
                    raise RuntimeError("Realtime provider connection ownership changed")
                now = realtime_service._utc_now()
                remaining_lifetime_seconds = max(
                    (current_runtime.absolute_expires_at() - now).total_seconds(),
                    0.0,
                )
                current_runtime.provider_connection_state = (
                    realtime_service.REALTIME_PROVIDER_CONNECTION_ACTIVE
                )
                current_runtime.provider_connection_owner_id = connection_id
                current_runtime.provider_connection_started_at = (
                    current_runtime.provider_connection_started_at or now
                )
                current_runtime.provider_connection_last_seen_at = now
                current_runtime.provider_stop_requested_at = None
                current_runtime.provider_stop_reason = None
                current_runtime.last_activity_at = now
                reservation_is_valid = (
                    not current_runtime.rate_limit_admission_id
                    or touch_duration_rate_limit_admission(
                        db,
                        current_runtime.rate_limit_admission_id,
                    )
                )
                if not reservation_is_valid:
                    realtime_service.request_provider_session_termination(
                        db,
                        current_runtime,
                        reason="reservation_expired",
                    )
                    signal_google_proxy_stop(session_id)
                    raise RuntimeError("Realtime duration reservation is no longer active")
                realtime_service.persist_realtime_runtime_state(
                    db,
                    current_runtime,
                    provider_state_authoritative=True,
                )
                return remaining_lifetime_seconds
    finally:
        db.close()


def _finalize_google_proxy_connection(session_id: str, connection_id: str) -> None:
    """Persist proxy disconnect state using a thread-confined DB session."""
    db = SessionLocal()
    terminated = False
    try:
        with realtime_service.session_registry.connection_lock(session_id):
            with realtime_service.serialized_realtime_provider_connection(
                db,
                session_id,
            ):
                current_runtime = realtime_service.restore_realtime_session_runtime(
                    db,
                    session_id=session_id,
                )
                if (
                    current_runtime is None
                    or not current_runtime.active
                    or current_runtime.provider_connection_owner_id != connection_id
                ):
                    return
                should_finalize = (
                    current_runtime.provider_connection_state
                    == realtime_service.REALTIME_PROVIDER_CONNECTION_TERMINATION_PENDING
                    or current_runtime.is_expired()
                )
                if should_finalize:
                    terminated = realtime_service._mark_runtime_inactive_locked(
                        db,
                        current_runtime,
                        reason=(
                            current_runtime.provider_stop_reason
                            or "provider_disconnected"
                        ),
                        status=(
                            "expired" if current_runtime.is_expired() else "stopped"
                        ),
                        provider_already_terminated=True,
                    )
                else:
                    current_runtime.provider_connection_state = (
                        realtime_service.REALTIME_PROVIDER_CONNECTION_IDLE
                    )
                    current_runtime.provider_connection_owner_id = None
                    current_runtime.provider_connection_last_seen_at = (
                        realtime_service._utc_now()
                    )
                    realtime_service.persist_realtime_runtime_state(
                        db,
                        current_runtime,
                        provider_state_authoritative=True,
                    )
    finally:
        db.close()
    if terminated:
        realtime_service.session_registry.remove(session_id)


async def _forward_browser_to_google(client: WebSocket, upstream) -> None:
    """Relay bounded client frames while blocking a second setup envelope."""
    while True:
        message = await client.receive()
        if message.get("type") == "websocket.disconnect":
            return
        payload: str | bytes | None = message.get("text")
        if payload is None:
            payload = message.get("bytes")
        if payload is None:
            continue
        payload_size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        if payload_size > GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES:
            raise ValueError("Gemini Live client message is too large")
        try:
            json_payload = (
                payload.decode("utf-8")
                if isinstance(payload, bytes)
                else payload
            )
            parsed = json.loads(json_payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict) and "setup" in parsed:
            raise ValueError("Gemini Live setup is server-owned")
        await upstream.send(payload)


async def _forward_google_to_browser(client: WebSocket, upstream) -> None:
    """Relay bounded provider frames to the authenticated browser."""
    async for payload in upstream:
        payload_size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        if payload_size > GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES:
            raise ValueError("Gemini Live provider message is too large")
        if isinstance(payload, str):
            await client.send_text(payload)
        else:
            await client.send_bytes(payload)


async def _heartbeat_google_proxy(session_id: str, connection_id: str) -> None:
    """Publish server-observed transport ownership and enforce stops."""
    while True:
        await asyncio.sleep(GOOGLE_LIVE_PROXY_HEARTBEAT_SECONDS)
        should_continue = await asyncio.to_thread(
            _heartbeat_google_proxy_once,
            session_id,
            connection_id,
        )
        if not should_continue:
            return


async def _renew_google_proxy_admission(
    session_id: str,
    connection_id: str,
) -> None:
    """Renew the duration admission independently of ownership heartbeats."""
    while True:
        await asyncio.sleep(GOOGLE_LIVE_PROXY_ADMISSION_RENEWAL_SECONDS)
        should_continue = await asyncio.to_thread(
            _renew_google_proxy_admission_once,
            session_id,
            connection_id,
        )
        if not should_continue:
            return


async def proxy_google_live_session(
    client: WebSocket,
    *,
    runtime,
) -> None:
    """Run a server-owned Gemini Live proxy for one authenticated runtime."""
    session_id = str(runtime.id)
    connection_id = str(uuid.uuid4())
    controller = _GoogleProxyController(
        connection_id=connection_id,
        loop=asyncio.get_running_loop(),
        close_event=asyncio.Event(),
    )
    if not _google_proxy_registry.reserve_candidate(session_id, controller):
        await client.close(code=1008, reason="Realtime reconnect already in progress")
        return
    promoted = False
    claimed = False
    upstream = None
    remaining_lifetime_seconds = 0.0
    try:
        # Candidate ownership must be released even if the ASGI server rejects
        # or fails the WebSocket handshake.
        await client.accept()
        claimed_runtime = await asyncio.to_thread(
            _claim_google_proxy_connection,
            session_id,
            connection_id,
        )
        if claimed_runtime is None:
            await client.close(
                code=1008,
                reason="Realtime provider connection is already active",
            )
            return
        runtime = claimed_runtime
        claimed = True
        upstream_url, setup_envelope = await asyncio.to_thread(
            _build_google_proxy_setup,
            runtime,
        )

        upstream = await connect(
            upstream_url,
            max_size=GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES,
            open_timeout=GOOGLE_LIVE_PROXY_SETUP_TIMEOUT_SECONDS,
            close_timeout=5,
        )
        await upstream.send(json.dumps(setup_envelope, separators=(",", ":")))
        first_message = await asyncio.wait_for(
            upstream.recv(),
            timeout=GOOGLE_LIVE_PROXY_SETUP_TIMEOUT_SECONDS,
        )
        if not _is_google_setup_complete(first_message):
            raise RuntimeError("Gemini Live did not confirm server-owned setup")

        remaining_lifetime_seconds = await asyncio.to_thread(
            _activate_google_proxy_connection,
            session_id,
            connection_id,
        )

        previous = _google_proxy_registry.promote(session_id, controller)
        promoted = True
        if previous and previous.connection_id != connection_id:
            previous.request_close()

        if isinstance(first_message, str):
            await client.send_text(first_message)
        else:
            await client.send_bytes(first_message)

        browser_task = asyncio.create_task(_forward_browser_to_google(client, upstream))
        provider_task = asyncio.create_task(_forward_google_to_browser(client, upstream))
        heartbeat_task = asyncio.create_task(
            _heartbeat_google_proxy(session_id, connection_id)
        )
        renewal_task = asyncio.create_task(
            _renew_google_proxy_admission(session_id, connection_id)
        )
        # Close the upstream socket at the immutable quota/provider deadline.
        # This monotonic timer is the normal enforcement path; the persisted
        # worker remains the crash/restart safety net.
        deadline_task = asyncio.create_task(
            asyncio.sleep(remaining_lifetime_seconds)
        )
        close_task = asyncio.create_task(controller.close_event.wait())
        done, pending = await asyncio.wait(
            {
                browser_task,
                provider_task,
                heartbeat_task,
                renewal_task,
                deadline_task,
                close_task,
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception() if not task.cancelled() else None
            if exception:
                raise exception
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    except Exception:
        logger.exception("Gemini Live proxy failed for realtime session %s", session_id)
        try:
            await client.close(code=1011, reason="Realtime provider connection failed")
        except Exception:
            pass
    finally:
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:
                logger.debug("Failed to close Gemini upstream for session %s", session_id)

        was_current = _google_proxy_registry.release(session_id, connection_id)
        if claimed and (was_current or not promoted):
            await asyncio.to_thread(
                _finalize_google_proxy_connection,
                session_id,
                connection_id,
            )


def _build_xai_proxy_setup(runtime) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Resolve xAI credentials and the privileged session update off-loop."""
    db = SessionLocal()
    try:
        provider = get_llm_provider(db, runtime.provider_id)
        if provider is None or provider.provider != "xai":
            raise RuntimeError("Configured xAI realtime provider is unavailable")
        return (
            build_xai_realtime_websocket_url(
                provider,
                runtime.realtime_model,
            ),
            build_xai_realtime_headers(provider),
            realtime_service.build_realtime_session_config(runtime),
        )
    finally:
        db.close()


async def _configure_xai_upstream(
    upstream,
    session_update: dict[str, Any],
) -> str:
    """Send server-owned xAI setup and return its acknowledgement event."""
    await upstream.send(json.dumps(session_update, separators=(",", ":")))
    while True:
        message = await asyncio.wait_for(
            upstream.recv(),
            timeout=GOOGLE_LIVE_PROXY_SETUP_TIMEOUT_SECONDS,
        )
        if not isinstance(message, str):
            continue
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "session.updated":
            return message
        if event_type == "error":
            error = event.get("error")
            detail = (
                str(error.get("message") or error.get("code") or "")
                if isinstance(error, dict)
                else str(event.get("message") or "")
            )
            raise RuntimeError(detail or "xAI rejected realtime session setup")


async def _forward_browser_to_xai(client: WebSocket, upstream) -> None:
    """Relay bounded xAI client events while protecting server-owned setup."""
    while True:
        message = await client.receive()
        if message.get("type") == "websocket.disconnect":
            return
        payload: str | bytes | None = message.get("text")
        if payload is None:
            payload = message.get("bytes")
        if payload is None:
            continue
        payload_size = (
            len(payload.encode("utf-8"))
            if isinstance(payload, str)
            else len(payload)
        )
        if payload_size > GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES:
            raise ValueError("xAI realtime client message is too large")
        try:
            raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("type") == "session.update":
            raise ValueError("xAI realtime setup is server-owned")
        await upstream.send(payload)


async def proxy_xai_realtime_session(
    client: WebSocket,
    *,
    runtime,
) -> None:
    """Run one authenticated, quota-controlled xAI realtime proxy."""
    session_id = str(runtime.id)
    connection_id = str(uuid.uuid4())
    controller = _GoogleProxyController(
        connection_id=connection_id,
        loop=asyncio.get_running_loop(),
        close_event=asyncio.Event(),
    )
    if not _google_proxy_registry.reserve_candidate(session_id, controller):
        await client.close(code=1008, reason="Realtime reconnect already in progress")
        return

    promoted = False
    claimed = False
    upstream = None
    try:
        await client.accept()
        claimed_runtime = await asyncio.to_thread(
            _claim_google_proxy_connection,
            session_id,
            connection_id,
        )
        if claimed_runtime is None:
            await client.close(
                code=1008,
                reason="Realtime provider connection is already active",
            )
            return
        runtime = claimed_runtime
        claimed = True
        upstream_url, headers, session_update = await asyncio.to_thread(
            _build_xai_proxy_setup,
            runtime,
        )
        upstream = await connect(
            upstream_url,
            additional_headers=headers,
            max_size=GOOGLE_LIVE_PROXY_MAX_MESSAGE_BYTES,
            open_timeout=GOOGLE_LIVE_PROXY_SETUP_TIMEOUT_SECONDS,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        )
        setup_message = await _configure_xai_upstream(upstream, session_update)
        remaining_lifetime_seconds = await asyncio.to_thread(
            _activate_google_proxy_connection,
            session_id,
            connection_id,
        )

        previous = _google_proxy_registry.promote(session_id, controller)
        promoted = True
        if previous and previous.connection_id != connection_id:
            previous.request_close()
        await client.send_text(setup_message)

        browser_task = asyncio.create_task(
            _forward_browser_to_xai(client, upstream)
        )
        provider_task = asyncio.create_task(
            _forward_google_to_browser(client, upstream)
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_google_proxy(session_id, connection_id)
        )
        renewal_task = asyncio.create_task(
            _renew_google_proxy_admission(session_id, connection_id)
        )
        deadline_task = asyncio.create_task(
            asyncio.sleep(remaining_lifetime_seconds)
        )
        close_task = asyncio.create_task(controller.close_event.wait())
        done, pending = await asyncio.wait(
            {
                browser_task,
                provider_task,
                heartbeat_task,
                renewal_task,
                deadline_task,
                close_task,
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception() if not task.cancelled() else None
            if exception:
                raise exception
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    except Exception:
        logger.exception("xAI realtime proxy failed for session %s", session_id)
        try:
            await client.close(code=1011, reason="Realtime provider connection failed")
        except Exception:
            pass
    finally:
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:
                logger.debug("Failed to close xAI upstream for session %s", session_id)
        was_current = _google_proxy_registry.release(session_id, connection_id)
        if claimed and (was_current or not promoted):
            await asyncio.to_thread(
                _finalize_google_proxy_connection,
                session_id,
                connection_id,
            )
