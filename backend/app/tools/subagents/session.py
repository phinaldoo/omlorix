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
    max_calls: int = 12
    calls: int = 0
    final_request_started: bool = False

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

    def prepare_request(self, kwargs):
        self.check_cancellation()
        # OpenRouter's HTTP adapter wraps its provider payload in json.
        kwargs = kwargs.get("json", kwargs)
        obsolete = self.attachments.keys() - self.latest_attachment_ids
        if obsolete:
            for key in ("messages", "input", "contents"):
                if key in kwargs:
                    kwargs[key] = _prune_obsolete_images(kwargs[key], obsolete)
        if self.calls < self.max_calls:
            return
        if self.final_request_started:
            raise RuntimeError("Subagent tool budget exhausted")
        self.final_request_started = True
        # The last result still reaches the model, with tools disabled. Gemini
        # stores its tool configuration inside GenerateContentConfig.
        if "config" in kwargs:
            config = kwargs["config"]
            if isinstance(config, dict):
                kwargs["config"] = {**config, "tools": None, "tool_config": None}
            else:
                kwargs["config"] = config.model_copy(
                    update={"tools": None, "tool_config": None}
                )
        for key in (
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "functions",
            "function_call",
        ):
            kwargs.pop(key, None)

    def execute(self, effect):
        self.check_cancellation()
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
        handler = self.handlers.get(name)
        stream = (
            handler(arguments.get("tool_arguments") or {})
            if handler
            else effect.execute(*effect.args, **effect.kwargs)
        )
        try:
            payload = yield from stream
        except Exception as exc:
            from app.tools.errors import SafeToolExecutionError

            logger.warning("Scoped subagent tool failed: %s", name, exc_info=True)
            # Do not expose provider, storage or execution internals. The model
            # can correct its next call or finish using a previous valid result.
            payload = {
                "content": exc.safe_message
                if isinstance(exc, SafeToolExecutionError)
                else "Tool execution failed. Use the last successful result or correct the request."
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
