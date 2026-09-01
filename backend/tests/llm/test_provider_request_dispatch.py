import asyncio
from types import SimpleNamespace
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.llm.provider_request import (
    ProviderRequest,
    REQUEST_TYPE_CHAT,
    REQUEST_TYPE_TITLE_GENERATION,
    _bounded_sync_stream_workers,
    call_provider_chat,
    call_provider_chat_async,
    call_provider_title_generation,
    release_db_session_before_provider_io,
)


def test_sync_stream_executor_has_independent_concurrency_bound(monkeypatch):
    monkeypatch.delenv("PROVIDER_SYNC_STREAM_WORKERS", raising=False)
    monkeypatch.delenv("GENERATION_WORKER_BATCH_SIZE", raising=False)
    assert _bounded_sync_stream_workers() == 16

    monkeypatch.setenv("GENERATION_WORKER_BATCH_SIZE", "1")
    assert _bounded_sync_stream_workers() == 4

    monkeypatch.setenv("PROVIDER_SYNC_STREAM_WORKERS", "32")
    assert _bounded_sync_stream_workers() == 32

    monkeypatch.setenv("PROVIDER_SYNC_STREAM_WORKERS", "invalid")
    assert _bounded_sync_stream_workers() == 16

    monkeypatch.setenv("PROVIDER_SYNC_STREAM_WORKERS", "1000")
    assert _bounded_sync_stream_workers() == 200


def test_chat_dispatch_forwards_openai_compatible_provider_type():
    calls = []

    def fake_openai_chat(**kwargs):
        calls.append(kwargs)
        return iter(['{"t":"d"}\n'])

    model = SimpleNamespace(
        provider="openai_responses", model_name="gpt-4.1", provider_id="provider-1"
    )
    stream = call_provider_chat(
        ProviderRequest(
            request_type=REQUEST_TYPE_CHAT,
            db=object(),
            provider=model.provider,
            model=model,
            chat_history=[],
            user_id="user-1",
            extra={
                "chat_id": "chat-1",
                "provider_callables": {"openai_responses": fake_openai_chat},
            },
        )
    )

    assert list(stream) == ['{"t":"d"}\n']
    assert calls[0]["openai_provider_type"] == "openai_responses"
    assert calls[0]["db_model"] is model
    assert calls[0]["chat_id"] == "chat-1"


def test_async_chat_dispatch_uses_native_provider_adapter():
    calls = []

    async def fake_openai_chat(**kwargs):
        calls.append(kwargs)
        yield '{"t":"c","d":"hello"}\n'
        yield '{"t":"d","d":"f"}\n'

    request = ProviderRequest(
        request_type=REQUEST_TYPE_CHAT,
        db=object(),
        provider="openai_responses",
        model=SimpleNamespace(model_name="gpt-5", provider_id="provider-1"),
        extra={
            "chat_id": "chat-1",
            "provider_async_callables": {"openai": fake_openai_chat},
        },
    )

    async def collect():
        return [line async for line in call_provider_chat_async(request)]

    assert asyncio.run(collect()) == [
        '{"t":"c","d":"hello"}\n',
        '{"t":"d","d":"f"}\n',
    ]
    assert calls[0]["openai_provider_type"] == "openai_responses"


def test_async_chat_dispatch_runs_sync_fallback_on_one_executor_thread():
    loop_thread = threading.get_ident()
    provider_threads = []

    def fake_openai_chat(**_kwargs):
        provider_threads.append(threading.get_ident())
        yield '{"t":"c","d":"hello"}\n'
        provider_threads.append(threading.get_ident())
        yield '{"t":"d","d":"f"}\n'

    request = ProviderRequest(
        request_type=REQUEST_TYPE_CHAT,
        db=object(),
        provider="openai",
        model=SimpleNamespace(model_name="gpt-5", provider_id="provider-1"),
        extra={"provider_callables": {"openai": fake_openai_chat}},
    )

    async def collect():
        return [line async for line in call_provider_chat_async(request)]

    assert asyncio.run(collect()) == [
        '{"t":"c","d":"hello"}\n',
        '{"t":"d","d":"f"}\n',
    ]
    assert provider_threads
    assert len(set(provider_threads)) == 1
    assert provider_threads[0] != loop_thread


def test_provider_io_boundary_returns_clean_session_connection_to_pool():
    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    session = sessionmaker(bind=engine)()
    try:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert engine.pool.checkedout() == 1

        assert release_db_session_before_provider_io(session) is True
        assert engine.pool.checkedout() == 0

        # The same reset Session remains usable for terminal persistence.
        assert session.execute(text("SELECT 2")).scalar_one() == 2
    finally:
        session.close()
        engine.dispose()


def test_provider_io_boundary_never_discards_pending_orm_changes():
    calls = []
    session = SimpleNamespace(
        new=[object()],
        dirty=[],
        deleted=[],
        commit=lambda: calls.append("commit"),
        close=lambda: calls.append("close"),
    )

    assert release_db_session_before_provider_io(session) is False
    assert calls == []


def test_openai_stream_releases_session_on_consumer_wait(monkeypatch):
    from app.llm.openai import chat as openai_chat

    released = []
    captured = {}
    session = object()

    def fake_interruptible(response, generation_id, **kwargs):
        captured.update(
            response=response,
            generation_id=generation_id,
            before_wait=kwargs.get("before_wait"),
        )
        kwargs["before_wait"]()
        return iter(["event"])

    monkeypatch.setattr(
        openai_chat,
        "interruptible_provider_stream",
        fake_interruptible,
    )
    monkeypatch.setattr(
        openai_chat,
        "release_db_session_before_provider_io",
        lambda db: released.append(db),
    )

    assert list(
        openai_chat._interruptible_openai_response_stream(
            "response",
            "generation-1",
            session,
        )
    ) == ["event"]
    assert captured["response"] == "response"
    assert captured["generation_id"] == "generation-1"
    assert callable(captured["before_wait"])
    assert released == [session]


def test_title_dispatch_passes_saved_model_settings(monkeypatch):
    from app.llm.openai import utils as openai_utils

    captured = {}

    def fake_title_generation(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "A title"

    monkeypatch.setattr(openai_utils, "openai_title_generation", fake_title_generation)

    model = SimpleNamespace(
        provider="openai_responses",
        model_name="gpt-4.1-mini",
        provider_id="provider-1",
        settings={"temperature": 0.2, "max_output_tokens": 80},
    )

    title = call_provider_title_generation(
        ProviderRequest(
            request_type=REQUEST_TYPE_TITLE_GENERATION,
            db=object(),
            provider=model.provider,
            model=model,
            prompt="hello",
            system_instruction="make a title",
            user_id="user-1",
        )
    )

    assert title == "A title"
    assert captured["kwargs"]["openai_provider_type"] == "openai_responses"
    assert captured["kwargs"]["model_settings"] == model.settings
    assert captured["args"][1] == "gpt-4.1-mini"
