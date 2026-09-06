"""Scoped tools and budgets for trusted, purpose-specific subagent runs.

The scope exists only while advancing its provider generator. It is never
serialized into model settings or inherited by the parent generation.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
import inspect
import json
import logging

from app.tools.results import ToolResult
from app.tools.errors import (
    GENERIC_TOOL_ERROR_MESSAGE,
    SafeToolExecutionError,
    SubagentToolExecutionError,
    ToolErrorTracker,
)


_active_session = ContextVar("subagent_session", default=None)
logger = logging.getLogger(__name__)


def current_session(generation_id=None):
    session = _active_session.get()
    return (
        session
        if session and (generation_id is None or session.generation_id == generation_id)
        else None
    )


@dataclass
class SubagentSession:
    generation_id: str
    tools: tuple[str, ...]
    user_id: str = ""
    parent_generation_id: str | None = None
    schemas: dict = field(default_factory=dict)
    handlers: dict = field(default_factory=dict)
    attachments: dict = field(default_factory=dict)
    latest_attachment_ids: set = field(default_factory=set)
    pruned_attachment_ids: set = field(default_factory=set)
    max_calls: int = 12
    calls: int = 0
    final_request_started: bool = False
    fatal_error: bool = False
    disabled_tools: set[str] = field(default_factory=set)
    error_tracker: ToolErrorTracker = field(default_factory=ToolErrorTracker)

    def raise_if_failed(self):
        if self.fatal_error:
            raise SubagentToolExecutionError()

    def budget(self):
        return {"calls_remaining": max(0, self.max_calls - self.calls)}

    def file_info(self, user_id, file_id):
        return self.attachments.get(file_id) if str(user_id) == self.user_id else None

    def check_cancellation(self):
        from app.chats.streaming import cancel_registry

        if self.parent_generation_id and cancel_registry.is_cancelled(
            self.parent_generation_id
        ):
            cancel_registry.cancel(self.generation_id)
        if cancel_registry.is_cancelled(self.generation_id):
            raise RuntimeError("Generation cancelled")

    def prune_obsolete_attachments(self, kwargs):
        """Context-pressure fallback; never reintroduce images on later turns."""
        obsolete = self.attachments.keys() - self.latest_attachment_ids
        newly_pruned = obsolete - self.pruned_attachment_ids
        if not newly_pruned:
            return False
        self.pruned_attachment_ids.update(newly_pruned)
        self._prune_history(kwargs)
        return True

    def _prune_history(self, kwargs):
        payload = kwargs.get("json", kwargs)
        if self.pruned_attachment_ids:
            for key in ("messages", "input", "contents"):
                if key in payload:
                    payload[key] = _prune_obsolete_images(
                        payload[key], self.pruned_attachment_ids
                    )

    def prepare_request(self, kwargs, *, protocol="openai"):
        self.check_cancellation()
        self.raise_if_failed()
        # Retain historical screenshots for prefix reuse unless an earlier
        # request needed to prune them to fit the configured context budget.
        self._prune_history(kwargs)
        # OpenRouter's HTTP adapter wraps its provider payload in json.
        kwargs = kwargs.get("json", kwargs)
        if self.calls < self.max_calls:
            return
        if self.final_request_started:
            raise RuntimeError("Subagent tool budget exhausted")
        self.final_request_started = True
        # Preserve tool schemas/order in the final request's cacheable prefix.
        if protocol == "google_aistudio":
            from google.genai import types

            config = kwargs["config"]
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            )
            if isinstance(config, dict):
                kwargs["config"] = {
                    **config,
                    "tool_config": tool_config.model_dump(exclude_none=True),
                }
            else:
                kwargs["config"] = config.model_copy(
                    update={"tool_config": tool_config}
                )
        elif protocol == "ollama":
            # Native Ollama has no tool-choice control. Removing tools remains
            # necessary there; never send unsupported SDK parameters.
            kwargs.pop("tools", None)
        elif protocol == "anthropic":
            kwargs["tool_choice"] = {"type": "none"}
        else:
            kwargs["tool_choice"] = "none"
            if "functions" in kwargs:
                kwargs["function_call"] = "none"

    def execute(self, effect):
        self.check_cancellation()
        self.raise_if_failed()
        arguments = (
            inspect.signature(effect.execute)
            .bind(*effect.args, **effect.kwargs)
            .arguments
        )
        name = arguments.get("tool_name", "")
        if name not in self.tools or self.calls >= self.max_calls:
            return self.receipt(
                name, {"content": "Tool unavailable or call budget exhausted."}
            )
        self.calls += 1
        if name in self.disabled_tools:
            return self.receipt(
                name,
                {
                    "content": GENERIC_TOOL_ERROR_MESSAGE,
                    "tool_meta": {
                        "scoped_tool_error": True,
                        "error_code": "tool_unavailable",
                        "retry_allowed": False,
                    },
                },
            )
        handler = self.handlers.get(name)
        stream = None
        try:
            stream = (
                handler(arguments.get("tool_arguments") or {})
                if handler
                else effect.execute(*effect.args, **effect.kwargs)
            )
            payload = yield from stream
        except Exception as exc:
            logger.warning("Scoped subagent tool failed: %s", name, exc_info=True)
            error = self.error_tracker.record(name, exc)
            if not isinstance(exc, SafeToolExecutionError):
                # Infrastructure/programming failures cannot be repaired by
                # regenerating model output. Deliver one failed receipt for
                # statistics, then stop before any further provider request.
                self.fatal_error = True
            elif error.stop_tool_calls:
                self.disabled_tools.add(name)
            payload = {
                "content": error.model_output,
                "tool_meta": {
                    "scoped_tool_error": True,
                    "error_code": error.error_code or "internal",
                    "retry_allowed": error.retry_allowed,
                },
            }
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        return self.receipt(name, payload or {})

    def receipt(self, name, payload):
        result = ToolResult.from_payload(name, payload)
        budget = self.budget()
        result.model_content = json.dumps(
            {"output": result.model_content, **budget}, ensure_ascii=False
        )
        result.history_receipt = {"output": result.history_receipt, **budget}
        return result


def scoped_stream(stream, session):
    """Advance and close a stream under its scope without leaking across yields."""
    try:
        while True:
            token = _active_session.set(session)
            try:
                event = next(stream)
            except StopIteration as completed:
                return completed.value
            finally:
                _active_session.reset(token)
            yield event
    finally:
        token = _active_session.set(session)
        try:
            stream.close()
        finally:
            _active_session.reset(token)


def _prune_obsolete_images(value, obsolete):
    """Drop superseded scoped screenshots, preserving text, tool pairs and assets.

    All provider attachment encoders place a metadata text block containing the
    exact file ID beside the encoded image. Walk native blocks without translating
    reasoning signatures or tool results into a different provider protocol.
    """

    def mapping(item):
        if isinstance(item, dict):
            return item
        fields = getattr(type(item), "model_fields", None)
        return {key: getattr(item, key) for key in fields} if fields else {}

    def text(item):
        data = mapping(item)
        return str(data.get("text") or "")

    def is_image(item):
        data = mapping(item)
        if data.get("type") in {"image", "input_image", "image_url"}:
            return True
        for key in ("inline_data", "file_data"):
            mime = mapping(data.get(key)).get("mime_type", "")
            if str(mime).startswith("image/"):
                return True
        return False

    if isinstance(value, list):
        output = []
        obsolete_image = False
        for item in value:
            label = text(item)
            if label:
                obsolete_image = any(file_id in label for file_id in obsolete)
            if obsolete_image and is_image(item):
                obsolete_image = False
                continue
            output.append(_prune_obsolete_images(item, obsolete))
        return output
    data = mapping(value)
    if not data:
        return value
    updates = {
        key: _prune_obsolete_images(item, obsolete) for key, item in data.items()
    }
    # Ollama groups encoded images on the message instead of using image blocks.
    # Only filter when metadata maps one-to-one; never guess which user asset
    # belongs to an image when a provider representation is ambiguous.
    if isinstance(data.get("images"), list) and isinstance(data.get("content"), str):
        image_ids = []
        for fragment in data["content"].split("Metadata of the file: ")[1:]:
            try:
                metadata, _ = json.JSONDecoder().raw_decode(fragment)
            except ValueError:
                continue
            if (
                isinstance(metadata, dict)
                and metadata.get("file_category") == "image"
                and metadata.get("native_context_included")
            ):
                image_ids.append(metadata.get("file_id"))
        if len(image_ids) == len(data["images"]):
            updates["images"] = [
                image
                for file_id, image in zip(image_ids, data["images"])
                if file_id not in obsolete
            ]
    if isinstance(value, dict):
        return updates
    return value.model_copy(update=updates)
