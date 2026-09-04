"""Shared chat lifecycle; protocol adapters yield effects and decode SDK events.

The engine owns provider admission, context budgets, tools, persistence, and
resource cleanup. Adapters retain native reasoning blocks, citations, and
continuation state instead of translating them through a lossy text protocol.
"""

from dataclasses import dataclass
from functools import wraps
import inspect
import json
from typing import Callable

from app.llm.generation.context import ContextBuilder, ContextBudgetExceeded
from app.tools.results import ToolResult


@dataclass(slots=True)
class ProviderCall:
    execute: Callable
    kwargs: dict
    settings: dict
    protocol: str
    args: tuple = ()


@dataclass(slots=True)
class ToolCall:
    execute: Callable
    args: tuple
    kwargs: dict


def stream_tool_call(execute, *args, **kwargs):
    return (yield ToolCall(execute, args, kwargs))


class GenerationEngine:
    def __init__(self, *, db=None, generation_id=None):
        self.db = db
        self.generation_id = generation_id
        self.context = ContextBuilder()
        self.resources = []
        self.tool_calls = 0
        self.context_error = False

    def persist_message(self, *args, **kwargs):
        from app.chats.models import create_chat_message

        # Provider-specific content remains intact. The shared diagnostic
        # contains counts/provenance only, never another copy of prompt data.
        content = kwargs.get("content")
        if self.context.last_report and isinstance(content, list) and content:
            content[-1].setdefault("meta", {})["context_budget"] = (
                self.context.last_report
            )
        return create_chat_message(*args, **kwargs)

    def _provider(self, effect):
        from app.llm.provider_request import release_db_session_before_provider_io

        try:
            self.context.prepare(
                effect.kwargs, settings=effect.settings, protocol=effect.protocol
            )
        except ContextBudgetExceeded:
            self.context_error = True
            raise
        self._close_resources()
        release_db_session_before_provider_io(self.db)
        response = effect.execute(*effect.args, **effect.kwargs)
        if callable(getattr(response, "close", None)):
            self.resources.append(response)
        return response

    def events(self, response, generation_id=None, *, stream_factory=None, **kwargs):
        """Share cancellation, idle transaction release, and stream cleanup."""
        from app.chats.streaming import interruptible_provider_stream
        from app.llm.provider_request import release_db_session_before_provider_io

        factory = stream_factory or interruptible_provider_stream
        parameters = inspect.signature(factory).parameters
        # Compatibility adapters may supply an older stream helper. The engine
        # still releases the session before each event in that case.
        if "before_wait" in parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        ):
            kwargs["before_wait"] = lambda: release_db_session_before_provider_io(
                self.db
            )
        stream = iter(factory(response, generation_id or self.generation_id, **kwargs))
        try:
            while True:
                release_db_session_before_provider_io(self.db)
                try:
                    event = next(stream)
                except StopIteration:
                    return
                yield event
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def _tool(self, effect):
        from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION
        from app.chats.streaming import cancel_registry

        if self.generation_id and cancel_registry.is_cancelled(self.generation_id):
            raise RuntimeError("Generation cancelled")
        if self.tool_calls >= MAX_TOOL_CALLS_PER_GENERATION:
            raise RuntimeError("Tool call budget exhausted")
        self.tool_calls += 1
        stream = effect.execute(*effect.args, **effect.kwargs)
        try:
            payload = yield from stream
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        name = effect.kwargs.get("tool_name") or (
            effect.args[1] if len(effect.args) > 1 else ""
        )
        return ToolResult.from_payload(name, payload or {})

    def run(self, adapter):
        """Drive provider effects and forward UI events with backpressure.

        Throw effect failures back into the adapter, preserving its native
        retry/fallback and partial-response finalization behavior.
        """
        result, error = None, None
        try:
            while True:
                try:
                    effect = (
                        adapter.throw(error)
                        if error is not None
                        else adapter.send(result)
                    )
                except StopIteration as done:
                    return done.value
                result, error = None, None
                try:
                    if isinstance(effect, ProviderCall):
                        result = self._provider(effect)
                    elif isinstance(effect, ToolCall):
                        result = yield from self._tool(effect)
                    elif effect is not None:
                        if self.context_error and isinstance(effect, str):
                            try:
                                event = json.loads(effect)
                            except (TypeError, ValueError):
                                event = {}
                            if event.get("t") == "e":
                                event.update(
                                    code="context_budget_exceeded",
                                    i18n_key="chat_context_budget_exceeded",
                                    d="This request exceeds the model's context limit. Remove attachments, reduce the request, or choose a model with a larger context.",
                                )
                                effect = json.dumps(event) + "\n"
                        yield effect
                except Exception as exc:
                    error = exc
        finally:
            try:
                adapter.close()
            finally:
                self._close_resources()

    def _close_resources(self):
        resources, self.resources = self.resources, []
        for resource in reversed(resources):
            try:
                resource.close()
            except Exception:
                pass


def chat_adapter(function):
    """Keep public provider signatures while routing every entry through the engine."""
    signature = inspect.signature(function)
    public_signature = signature.replace(
        parameters=[
            parameter
            for name, parameter in signature.parameters.items()
            if name != "engine"
        ]
    )

    @wraps(function)
    def run(*args, **kwargs):
        arguments = public_signature.bind(*args, **kwargs).arguments
        engine = GenerationEngine(
            db=arguments.get("db"), generation_id=arguments.get("generation_id")
        )
        kwargs["engine"] = engine
        return engine.run(function(*args, **kwargs))

    run.__signature__ = public_signature
    return run
