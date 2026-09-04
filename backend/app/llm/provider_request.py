from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import inspect
import logging
import os
import threading
from typing import Any

from fastapi import HTTPException

from app.utils.db import release_db_session_before_long_wait


logger = logging.getLogger(__name__)


def _bounded_sync_stream_workers() -> int:
    """Bound concurrent compatibility streams inside each process."""

    raw_value = os.getenv(
        "PROVIDER_SYNC_STREAM_WORKERS",
        os.getenv("GENERATION_WORKER_BATCH_SIZE", "16"),
    )
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 16
    return max(4, min(parsed, 200))


# Provider SDK calls have application-level timeouts, so a process-wide bounded
# pool is preferable to creating one unbounded thread per active generation.
_SYNC_STREAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=_bounded_sync_stream_workers(),
    thread_name_prefix="provider-stream",
)
_ASYNC_STREAM_QUEUE_SIZE = 8


def _normalize_provider_value(provider: Any) -> str:
    raw_value = getattr(provider, "value", provider)
    if raw_value is None:
        raw_value = ""
    return str(raw_value).strip()


REQUEST_TYPE_CHAT = "chat"
REQUEST_TYPE_TITLE_GENERATION = "title_generation"
REQUEST_TYPE_MEMORY_CONSOLIDATION = "memory_consolidation"


@dataclass(slots=True)
class ProviderRequest:
    """Common request envelope for provider text-generation calls."""

    request_type: str
    db: Any
    provider: str
    model: Any
    prompt: str | None = None
    system_instruction: str | None = None
    chat_history: Any = None
    user_id: str | None = None
    project_id: str | None = None
    generation_id: str | None = None
    temp_request_flag: bool = False
    byok: dict | None = None
    settings_override: dict | None = None
    reference_id: str | None = None
    system_instruction_sections: list[dict[str, str]] | None = None
    assistant_metadata: dict | None = None
    note_ids: list[str] | None = None
    reference_parts: list[str] | None = None
    chat_reference_context: str | None = None
    retry_count: int | None = None
    user_role: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_provider(self) -> str:
        return _normalize_provider_value(self.provider)

    @property
    def model_name(self) -> str:
        if isinstance(self.model, str):
            return self.model
        return str(getattr(self.model, "model_name", "") or "")

    @property
    def provider_id(self) -> str | None:
        # One-shot jobs may resolve a provider group before dispatch. Honour
        # that concrete provider without mutating the shared ORM model row.
        provider_id = self.extra.get("provider_id")
        if not provider_id and not isinstance(self.model, str):
            provider_id = getattr(self.model, "provider_id", None)
        return str(provider_id).strip() if provider_id else None

    @property
    def model_settings(self) -> dict | None:
        if isinstance(self.model, str):
            settings = self.extra.get("model_settings")
        else:
            settings = getattr(self.model, "settings", None)
        if not isinstance(settings, dict) and isinstance(self.byok, dict):
            settings = self.byok.get("settings")
        return settings if isinstance(settings, dict) else None


def _chat_kwargs(request: ProviderRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "chat_id": request.extra.get("chat_id"),
        "chat_history": request.chat_history,
        "db": request.db,
        "db_model": request.model,
        "user_id": request.user_id,
        "project_id": request.project_id,
        "generation_id": request.generation_id,
        "temp_request_flag": request.temp_request_flag,
        "byok": request.byok,
        "settings_override": request.settings_override,
        "reference_id": request.reference_id,
        "system_instruction_sections": request.system_instruction_sections,
        "assistant_metadata": request.assistant_metadata,
        "note_ids": request.note_ids,
        "reference_parts": request.reference_parts,
        "chat_reference_context": request.chat_reference_context,
        "user_role": request.user_role,
    }
    if request.retry_count is not None:
        kwargs["retry_count"] = request.retry_count
    return kwargs


def release_db_session_before_provider_io(db: Any) -> bool:
    """Release a clean synchronous Session before a potentially long I/O wait.

    Committing a clean setup transaction returns the checked-out connection and
    keeps the Session reusable for the short persistence operations that follow
    the provider response. Refuse to finalize when ORM changes are pending:
    changing their transaction boundary would be worse than temporarily
    retaining the connection, and callers can move that write explicitly.
    """

    return release_db_session_before_long_wait(db)


async def iterate_sync_stream_async(
    stream_factory: Callable[[], Iterator[str]],
    *,
    executor: ThreadPoolExecutor | None = None,
) -> AsyncIterator[str]:
    """Adapt a blocking generator to an async stream with bounded backpressure.

    Construction and iteration both happen on the same executor thread. This is
    important for synchronous SQLAlchemy sessions and SDK clients, neither of
    which may be moved between threads while a generation is active.
    """

    loop = asyncio.get_running_loop()
    item_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
        maxsize=_ASYNC_STREAM_QUEUE_SIZE
    )
    producer_stopped = threading.Event()

    def _offer(kind: str, value: Any = None) -> bool:
        future = asyncio.run_coroutine_threadsafe(
            item_queue.put((kind, value)),
            loop,
        )
        while True:
            try:
                future.result(timeout=0.1)
                return True
            except FutureTimeoutError:
                if producer_stopped.is_set():
                    future.cancel()
                    return False

    def _produce() -> None:
        stream: Iterator[str] | None = None
        try:
            stream = iter(stream_factory())
            for item in stream:
                if producer_stopped.is_set() or not _offer("item", item):
                    return
        except Exception as exc:  # noqa: BLE001 - preserve provider exception semantics
            _offer("error", exc)
        finally:
            if stream is not None:
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - best-effort generator cleanup
                        logger.debug("Synchronous provider stream close failed", exc_info=True)
            _offer("done")

    producer = loop.run_in_executor(executor or _SYNC_STREAM_EXECUTOR, _produce)
    try:
        while True:
            kind, value = await item_queue.get()
            if kind == "item":
                yield value
                continue
            if kind == "error":
                raise value
            return
    finally:
        producer_stopped.set()
        # Do not wait indefinitely for a misbehaving synchronous SDK. Its own
        # request timeout or cancellation handle remains responsible for waking
        # the producer thread.
        try:
            await asyncio.wait_for(asyncio.shield(producer), timeout=0.1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


def _async_override_for_provider(request: ProviderRequest) -> Callable[..., Any] | None:
    overrides = request.extra.get("provider_async_callables")
    if not isinstance(overrides, dict):
        return None
    provider = request.normalized_provider
    if provider in {"openai_responses", "microsoft_azure", "lmstudio", "xai"}:
        return overrides.get(provider) or overrides.get("openai")
    if provider in {"anthropic", "anthropic_base"}:
        return overrides.get(provider) or overrides.get("anthropic")
    return overrides.get(provider)


async def call_provider_chat_async(request: ProviderRequest) -> AsyncIterator[str]:
    """Dispatch provider chat through a native async adapter or bounded fallback.

    Providers can register a native async generator in
    ``extra['provider_async_callables']``. Until a provider's complete
    orchestration (tools, persistence, statistics, and retries) is async-safe,
    its existing synchronous generator runs through the bounded compatibility
    adapter instead of blocking the worker event loop.
    """

    async_call = _async_override_for_provider(request)
    if async_call is None:
        async for line in iterate_sync_stream_async(lambda: call_provider_chat(request)):
            yield line
        return

    kwargs = _chat_kwargs(request)
    provider = request.normalized_provider
    if provider in {
        "openai_responses",
        "microsoft_azure",
        "lmstudio",
        "xai",
        "openai_chat_completions",
    }:
        kwargs["openai_provider_type"] = provider

    stream = async_call(**kwargs)
    if inspect.isawaitable(stream):
        stream = await stream
    if hasattr(stream, "__aiter__"):
        async for line in stream:
            yield line
        return
    async for line in iterate_sync_stream_async(lambda: stream):
        yield line


def call_provider_chat(request: ProviderRequest) -> Generator[str, None, Any]:
    """Dispatch a full chat request to the selected provider implementation."""
    provider = request.normalized_provider
    kwargs = _chat_kwargs(request)
    overrides = request.extra.get("provider_callables")
    if not isinstance(overrides, dict):
        overrides = {}

    if provider == "google_aistudio":
        call = overrides.get(provider)
        if call is None:
            from app.llm.google_aistudio.utils import aistudio_chat

            call = aistudio_chat
        return call(**kwargs)
    if provider == "ollama":
        call = overrides.get(provider)
        if call is None:
            from app.llm.ollama.utils import ollama_chat

            call = ollama_chat
        return call(**kwargs)
    if provider == "openai":
        call = overrides.get(provider)
        if call is None:
            from app.llm.openai.utils import openai_chat

            call = openai_chat
        return call(**kwargs)
    if provider in {"openai_responses", "microsoft_azure", "lmstudio", "xai"}:
        call = overrides.get(provider) or overrides.get("openai")
        if call is None:
            from app.llm.openai.utils import openai_chat

            call = openai_chat
        return call(**kwargs, openai_provider_type=provider)
    if provider == "openai_chat_completions":
        call = overrides.get(provider)
        if call is None:
            from app.llm.openai_chat_completions.utils import openai_chat_completions_chat

            call = openai_chat_completions_chat
        return call(**kwargs, openai_provider_type=provider)
    if provider == "openrouter":
        call = overrides.get(provider)
        if call is None:
            from app.llm.openrouter.utils import openrouter_chat

            call = openrouter_chat
        return call(**kwargs)
    if provider in {"anthropic", "anthropic_base"}:
        call = overrides.get(provider) or overrides.get("anthropic")
        if call is None:
            from app.llm.anthropic.utils import anthropic_chat

            call = anthropic_chat
        return call(**kwargs)

    raise HTTPException(status_code=400, detail="Provider not (yet) supported")


def call_provider_title_generation(request: ProviderRequest) -> str | None:
    """Dispatch a one-shot title-generation request to the selected provider."""
    provider = request.normalized_provider
    model_name = request.model_name
    prompt = request.prompt or ""
    system_instruction = request.system_instruction or ""
    generation_options = request.extra.get("simple_generation_options")
    if not isinstance(generation_options, dict):
        generation_options = {}

    if provider == "ollama":
        from app.llm.ollama.utils import ollama_title_generation

        if request.byok:
            return ollama_title_generation(
                request.db,
                model_name,
                prompt,
                system_instruction,
                byok_base_url=request.byok.get("base_url"),
                byok_api_key=request.byok.get("api_key"),
                user_id=request.user_id,
                model_settings=request.model_settings,
                settings_override=request.settings_override,
                **generation_options,
            )
        return ollama_title_generation(
            request.db,
            model_name,
            prompt,
            system_instruction,
            request.provider_id,
            user_id=request.user_id,
            model_settings=request.model_settings,
            settings_override=request.settings_override,
            **generation_options,
        )

    if provider in {"openai", "openai_responses", "microsoft_azure", "lmstudio", "xai"}:
        from app.llm.openai.utils import openai_title_generation

        return openai_title_generation(
            request.db,
            model_name,
            prompt,
            system_instruction,
            None if request.byok else request.provider_id,
            byok=request.byok,
            openai_provider_type=provider,
            user_id=request.user_id,
            model_settings=request.model_settings,
            settings_override=request.settings_override,
            **generation_options,
        )

    if provider == "openai_chat_completions":
        from app.llm.openai_chat_completions.utils import openai_chat_completions_title_generation

        return openai_chat_completions_title_generation(
            request.db,
            model_name,
            prompt,
            system_instruction,
            None if request.byok else request.provider_id,
            byok=request.byok,
            user_id=request.user_id,
            openai_provider_type=provider,
            model_settings=request.model_settings,
            settings_override=request.settings_override,
            **generation_options,
        )

    if provider == "google_aistudio":
        from app.llm.google_aistudio.utils import google_aistudio_title_generation

        return google_aistudio_title_generation(
            request.db,
            model_name,
            prompt,
            system_instruction,
            None if request.byok else request.provider_id,
            byok=request.byok,
            user_id=request.user_id,
            model_settings=request.model_settings,
            **generation_options,
        )

    if provider == "openrouter":
        from app.llm.openrouter.utils import openrouter_title_generation

        return openrouter_title_generation(
            request.db,
            model_name if request.byok else request.model,
            prompt,
            system_instruction,
            None if request.byok else request.provider_id,
            byok=request.byok,
            user_id=request.user_id,
            model_settings=request.model_settings,
            settings_override=request.settings_override,
            **generation_options,
        )

    if provider in {"anthropic", "anthropic_base"}:
        from app.llm.anthropic.utils import anthropic_title_generation

        return anthropic_title_generation(
            request.db,
            model_name if request.byok else request.model,
            prompt,
            system_instruction,
            None if request.byok else request.provider_id,
            byok=request.byok,
            user_id=request.user_id,
            model_settings=request.model_settings,
            settings_override=request.settings_override,
            **generation_options,
        )

    return None


def call_provider_memory_consolidation(request: ProviderRequest) -> str | None:
    """Run schema-constrained extraction without exposing any model tool."""

    response_schema = request.extra.get("response_schema")
    try:
        max_output_tokens = int(request.extra.get("max_output_tokens") or 8_192)
    except (TypeError, ValueError):
        max_output_tokens = 8_192
    request.extra["simple_generation_options"] = {
        "generation_category": REQUEST_TYPE_MEMORY_CONSOLIDATION,
        "output_char_limit": None,
        "max_output_tokens": max(256, min(max_output_tokens, 32_768)),
        "response_schema": response_schema if isinstance(response_schema, dict) else None,
        "raise_on_error": True,
    }
    return call_provider_title_generation(request)
