from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Iterable
import json
import logging
import re
import time
import uuid

from app.chats.streaming import cancel_registry
from app.llm.models import Models
from app.llm.provider_request import (
    ProviderRequest,
    REQUEST_TYPE_CHAT,
    call_provider_chat,
)
from app.llm.schemas import normalize_provider_value


logger = logging.getLogger(__name__)

PhaseEventCallback = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)
_MAX_REPAIR_OUTPUT_CHARS = 100_000
_MAX_REPAIR_CONTEXT_CHARS = 160_000


class DeepResearchCancelled(RuntimeError):
    """Raised when a persisted or chat-level cancellation interrupts a phase."""


class DeepResearchEmptyResponse(RuntimeError):
    """Raised when a provider finishes without usable phase output."""


class DeepResearchIncompleteStream(RuntimeError):
    """Raised when a provider stream ends without a terminal chat event.

    ``partial_result`` retains safe, already-normalized phase data so a retry
    can preserve usage, sources, generated artifacts, and activity accounting
    without treating the truncated assistant text as a valid phase response.
    """

    def __init__(self, phase: str, partial_result: Any | None = None) -> None:
        self.phase = str(phase or "unknown")
        self.partial_result = partial_result
        super().__init__(
            f"Deep Research phase '{self.phase}' ended before its terminal event."
        )


class DeepResearchStructuredOutputError(RuntimeError):
    """Raised when a model cannot satisfy a required phase JSON schema."""

    def __init__(
        self,
        schema_name: str,
        validation_summary: str = "",
        structured_value: Any | None = None,
    ) -> None:
        self.schema_name = str(schema_name)
        self.validation_summary = str(validation_summary or "")[:4_000]
        self.structured_value = structured_value
        super().__init__(
            f"The {self.schema_name} response did not match the required schema."
        )


def public_error_code(exc: BaseException) -> str:
    """Map internal failures to the stable, secret-free v2 error contract."""

    if isinstance(exc, DeepResearchCancelled):
        return "run_cancelled"
    if isinstance(exc, DeepResearchIncompleteStream):
        return "provider_incomplete_response"
    if isinstance(exc, DeepResearchEmptyResponse):
        return "provider_empty_response"
    if isinstance(exc, DeepResearchStructuredOutputError):
        return "structured_output_invalid"
    if isinstance(exc, TimeoutError):
        return "run_timed_out"
    if isinstance(exc, ValueError):
        return "artifact_validation_failed"
    return "internal_research_error"


@dataclass(slots=True)
class PhaseResult:
    """Normalized result from one provider-neutral nested chat phase."""

    text: str
    generated_files: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)
    raw_event_count: int = 0
    duration_seconds: float = 0.0
    structured_output: Any | None = None


def _clone_model_for_phase(
    model: Models,
    *,
    tools: Iterable[str],
    settings_override: dict[str, Any],
) -> Any:
    """Create a detached model view with only the tools allowed in this phase."""

    selected_tools = [str(name).strip() for name in tools if str(name).strip()]
    settings = deepcopy(model.settings) if isinstance(model.settings, dict) else {}
    settings.update(deepcopy(settings_override))
    settings["enabled_tools"] = list(selected_tools)
    settings["_runtime_enabled_tools"] = list(selected_tools)

    capabilities = (
        deepcopy(model.capabilities) if isinstance(model.capabilities, list) else []
    )
    # Stored model capabilities describe the model's normal-chat configuration:
    # today, providers commonly add ``tools`` only when the administrator has
    # selected persistent model tools. Deep Research supplies a narrower,
    # phase-owned allowlist at runtime, so its detached model must advertise
    # that allowlist to provider adapters or they will discard every injected
    # tool schema before making the request. Never mutate the persisted model,
    # and keep tool-free planning/repair phases tool-free.
    if selected_tools and "tools" not in capabilities:
        capabilities.append("tools")

    return SimpleNamespace(
        id=model.id,
        name=model.name,
        description=getattr(model, "description", None),
        model_icon=getattr(model, "model_icon", None),
        provider=model.provider,
        provider_id=model.provider_id,
        model_name=model.model_name,
        settings=settings,
        capabilities=capabilities,
        tools=list(selected_tools),
        access=deepcopy(getattr(model, "access", None) or {}),
        meta=deepcopy(getattr(model, "meta", None) or {}),
        status=getattr(model, "status", None),
        is_active=getattr(model, "is_active", True),
        created_at=getattr(model, "created_at", None),
    )


def _phase_user_message(input_text: str) -> dict[str, Any]:
    """Build the canonical history message accepted by every chat provider."""

    return {
        "id": f"deep-research-phase-{uuid.uuid4()}",
        "role": "user",
        "content": [{"type": "content", "content": input_text}],
    }


def _decode_stream_line(raw_line: str) -> dict[str, Any]:
    """Decode a normal Omlorix provider stream line without trusting its shape."""

    try:
        payload = json.loads(str(raw_line or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"t": "raw", "d": str(raw_line or "")}
    return (
        payload if isinstance(payload, dict) else {"t": "raw", "d": str(raw_line or "")}
    )


def _tool_name(payload: dict[str, Any]) -> str | None:
    """Extract a tool name from the provider-neutral tool-start event."""

    descriptor = payload.get("d")
    if isinstance(descriptor, str):
        return descriptor.strip() or None
    if isinstance(descriptor, dict):
        value = descriptor.get("name") or descriptor.get("tool_name")
        return str(value).strip() if value else None
    return None


def _tool_call_id(payload: dict[str, Any]) -> str | None:
    """Extract an optional provider tool-call identifier without arguments."""

    descriptor = payload.get("d")
    if isinstance(descriptor, dict):
        value = descriptor.get("id") or descriptor.get("call_id")
        return str(value).strip() if value else None
    return None


def _tool_arguments(payload: dict[str, Any]) -> Any | None:
    """Return only arguments the normal provider stream made public.

    Every provider applies Omlorix's private-argument denylist before emitting a
    ``t_c`` event. Reading arguments from that already-filtered descriptor keeps
    Deep Research aligned with the normal chat UI and prevents this adapter from
    accidentally persisting arguments that the provider intentionally hid.
    """

    descriptor = payload.get("d")
    if isinstance(descriptor, dict) and "args" in descriptor:
        return deepcopy(descriptor.get("args"))
    if "c" in payload:
        return deepcopy(payload.get("c"))
    return None


def _extract_citations(value: Any) -> list[dict[str, str]]:
    """Normalize citation metadata emitted by every normal chat provider."""

    citations: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(node: Any, inherited_title: str = "") -> None:
        if isinstance(node, dict):
            title = str(
                node.get("title") or node.get("name") or inherited_title or ""
            ).strip()
            url_value = (
                node.get("url")
                or node.get("uri")
                or node.get("source_url")
                or node.get("sourceUrl")
                or node.get("link")
            )
            if isinstance(url_value, str):
                url = url_value.strip()
                if url.startswith(("http://", "https://")) and url not in seen:
                    seen.add(url)
                    citation = {"url": url, "title": title or url}
                    snippet = node.get("snippet") or node.get("excerpt")
                    if isinstance(snippet, str) and snippet.strip():
                        citation["snippet"] = snippet.strip()[:2_000]
                    citations.append(citation)
            for child in node.values():
                walk(child, title)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child, inherited_title)

    walk(value)
    return citations


def run_model_phase(
    db,
    *,
    model_id: str,
    user_id: str,
    run_id: str,
    phase: str,
    instructions: str,
    input_text: str,
    tools: list[str],
    chat_id: str | None,
    project_id: str | None,
    generation_id: str | None,
    user_role: str | None,
    settings_override: dict[str, Any],
    event_callback: PhaseEventCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> PhaseResult:
    """Run one phase through the same provider and tool pipeline as normal chat.

    Websearch and Code Execution are selected through ``tools`` and are therefore
    resolved by the normal Omlorix tool loop. This function only translates that
    existing stream into the phase-neutral result consumed by the orchestrator.
    """

    model = (
        db.query(Models)
        .filter(Models.id == str(model_id), Models.is_active == True)  # noqa: E712
        .first()
    )
    if model is None:
        raise RuntimeError("Configured Deep Research model no longer exists.")

    phase_model = _clone_model_for_phase(
        model,
        tools=tools,
        settings_override=settings_override,
    )
    nested_generation_id = f"deep-research:{run_id}:{phase}:{uuid.uuid4()}"
    cancel_registry.set_active(f"deep-research:{run_id}", nested_generation_id)

    callback = event_callback or (lambda _event: None)
    # The normal chat provider loop may produce several assistant turns inside
    # one stream: a short progress message, a tool call, another progress
    # message, another tool call, and finally the requested phase output. Keep
    # only the currently open assistant turn as the phase result. When a tool
    # call follows it, that text was necessarily intermediate commentary rather
    # than the terminal report/JSON response. The callback still receives every
    # content delta, so discarding it here does not hide live research activity.
    current_turn_text: list[str] = []
    generated_files: list[dict[str, str]] = []
    tool_calls: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []
    usage: list[dict[str, Any]] = []
    raw_event_count = 0
    phase_started = time.monotonic()
    saw_terminal_event = False

    request = ProviderRequest(
        request_type=REQUEST_TYPE_CHAT,
        db=db,
        provider=normalize_provider_value(model.provider),
        model=phase_model,
        chat_history=[_phase_user_message(input_text)],
        user_id=str(user_id),
        project_id=project_id,
        generation_id=nested_generation_id,
        temp_request_flag=True,
        settings_override={
            **deepcopy(settings_override),
            "enabled_tools": list(tools),
            "_runtime_enabled_tools": list(tools),
        },
        system_instruction_sections=[
            {
                "title": f"Deep Research {phase.replace('_', ' ').title()} Instructions",
                "content": instructions,
            }
        ],
        assistant_metadata={
            "deep_research": True,
            "deep_research_run_id": run_id,
            "deep_research_phase": phase,
        },
        user_role=user_role,
        extra={"chat_id": chat_id},
    )

    def finish_pending_tool_calls(*, success: bool) -> None:
        """Close tools at their real position in the nested provider stream.

        Chat providers emit the next reasoning/content chunk only after the
        preceding tool has returned. Publishing completion at that boundary
        preserves the exact interleaving shown by the normal chat renderer.
        """

        while pending_tool_calls:
            tool_call = pending_tool_calls.pop(0)
            callback(
                {
                    "event": "tool_completed" if success else "tool_failed",
                    "phase": phase,
                    "request_id": nested_generation_id,
                    "name": tool_call.get("name"),
                    "id": tool_call.get("id"),
                }
            )

    try:
        callback(
            {
                "event": "llm_request_started",
                "phase": phase,
                "request_id": nested_generation_id,
            }
        )
        stream = call_provider_chat(request)
        for raw_line in stream:
            raw_event_count += 1
            if cancellation_check and cancellation_check():
                cancel_registry.cancel(nested_generation_id)
            if generation_id and cancel_registry.is_cancelled(generation_id):
                cancel_registry.cancel(nested_generation_id)
            if cancel_registry.is_cancelled(nested_generation_id):
                raise DeepResearchCancelled("Deep Research was cancelled.")

            payload = _decode_stream_line(raw_line)
            stream_type = str(payload.get("t") or payload.get("type") or "").strip()
            if stream_type == "c" and isinstance(payload.get("d"), str):
                finish_pending_tool_calls(success=True)
                current_turn_text.append(payload["d"])
                callback(
                    {
                        "event": "content_delta",
                        "phase": phase,
                        "request_id": nested_generation_id,
                        "delta": payload["d"],
                    }
                )
            elif stream_type == "r" and isinstance(payload.get("d"), str):
                finish_pending_tool_calls(success=True)
                # Provider-visible reasoning summaries use the same public `r`
                # protocol as normal chat. Hidden chain-of-thought is never
                # available here and therefore cannot be exposed accidentally.
                callback(
                    {
                        "event": "reasoning_delta",
                        "phase": phase,
                        "request_id": nested_generation_id,
                        "delta": payload["d"],
                    }
                )
            elif stream_type == "r_f":
                finish_pending_tool_calls(success=True)
                callback(
                    {
                        "event": "reasoning_completed",
                        "phase": phase,
                        "request_id": nested_generation_id,
                        "duration_seconds": payload.get("d"),
                    }
                )
            elif stream_type == "t_c":
                # A following tool-call event means the previous tool already
                # returned, even when the model emitted no intervening text.
                # It also proves that any content emitted since the preceding
                # tool boundary was a progress preamble from a non-terminal
                # assistant turn. Start a fresh candidate for the response that
                # follows this tool call; only the final such candidate is
                # returned as the phase output.
                finish_pending_tool_calls(success=True)
                current_turn_text.clear()
                name = _tool_name(payload)
                call_id = _tool_call_id(payload)
                arguments = _tool_arguments(payload)
                # Only retain the public descriptor. Tool arguments may contain
                # research context, so retain them only when the normal provider
                # stream explicitly exposed them after its privacy checks.
                tool_call = {"name": name, "id": call_id, "arguments": arguments}
                tool_calls.append(tool_call)
                pending_tool_calls.append(tool_call)
                callback(
                    {
                        "event": "tool_started",
                        "phase": phase,
                        "request_id": nested_generation_id,
                        "name": name,
                        "id": call_id,
                        "arguments": arguments,
                    }
                )
            elif stream_type == "t_cd":
                callback({"event": "tool_progress", "phase": phase, "payload": payload})
            elif stream_type == "f":
                file_id = str(payload.get("d") or "").strip()
                file_name = str(payload.get("n") or "").strip()
                if file_id:
                    generated_files.append({"file_id": file_id, "name": file_name})
                    callback(
                        {
                            "event": "artifact_created",
                            "phase": phase,
                            "file_id": file_id,
                            "name": file_name,
                        }
                    )
            elif stream_type in {"usage", "usg"}:
                usage.append(payload)
            elif stream_type == "e":
                finish_pending_tool_calls(success=False)
                message = str(payload.get("d") or "Provider phase failed.").strip()
                raise RuntimeError(message)
            elif stream_type == "d":
                saw_terminal_event = True
                finish_pending_tool_calls(success=True)
                metadata = payload.get("c")
                if isinstance(metadata, dict):
                    # Normal Omlorix providers publish token usage and citations in
                    # the final ``d/f`` metadata event rather than a separate
                    # usage/source stream event.
                    usage.append(metadata)
                    sources.extend(_extract_citations(metadata.get("citations") or []))
                    status_value = metadata.get("status")
                    if str(status_value or "").strip().lower() in {
                        "error",
                        "failed",
                        "cancelled",
                        "canceled",
                    }:
                        if str(status_value).strip().lower() in {
                            "cancelled",
                            "canceled",
                        }:
                            raise DeepResearchCancelled("Deep Research was cancelled.")
                        raise RuntimeError("Provider phase failed.")
                    if metadata.get("timeout") is True:
                        raise TimeoutError("The provider phase timed out.")

        finish_pending_tool_calls(success=True)
        if not saw_terminal_event:
            # A response iterator can end after emitting partial text without a
            # provider exception (for example after a proxy disconnect or a
            # provider ``response.incomplete`` event that an adapter omitted).
            # Never label that partial output as a completed research request.
            partial_result = PhaseResult(
                text="".join(current_turn_text).strip(),
                generated_files=generated_files,
                tool_calls=tool_calls,
                sources=sources,
                usage=usage,
                raw_event_count=raw_event_count,
                duration_seconds=round(time.monotonic() - phase_started, 3),
            )
            raise DeepResearchIncompleteStream(phase, partial_result)
        callback(
            {
                "event": "llm_request_completed",
                "phase": phase,
                "request_id": nested_generation_id,
                "duration_seconds": round(time.monotonic() - phase_started, 3),
            }
        )
        return PhaseResult(
            text="".join(current_turn_text).strip(),
            generated_files=generated_files,
            tool_calls=tool_calls,
            sources=sources,
            usage=usage,
            raw_event_count=raw_event_count,
            duration_seconds=round(time.monotonic() - phase_started, 3),
        )
    except Exception:
        finish_pending_tool_calls(success=False)
        callback(
            {
                "event": "llm_request_failed",
                "phase": phase,
                "request_id": nested_generation_id,
            }
        )
        raise
    finally:
        cancel_registry.clear(nested_generation_id)


def _validation_summary(exc: BaseException) -> str:
    """Return a bounded, content-free validation summary for a repair prompt."""

    errors_method = getattr(exc, "errors", None)
    if callable(errors_method):
        try:
            errors = errors_method(
                include_url=False,
                include_context=False,
                include_input=False,
            )
            return json.dumps(errors, ensure_ascii=False)[:4_000]
        except (TypeError, ValueError):
            pass
    return type(exc).__name__


def _json_values(text: str) -> Iterable[Any]:
    """Yield complete JSON values found inside prose without greedy slicing."""

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            yield value


def parse_structured_output(text: str, schema_type):
    """Validate strict JSON, including fenced or prose-wrapped JSON values."""

    candidate = str(text or "").strip().lstrip("\ufeff")
    errors: list[BaseException] = []
    textual_candidates = [candidate]
    textual_candidates.extend(
        match.group(1).strip() for match in _JSON_FENCE_RE.finditer(candidate)
    )

    seen_text: set[str] = set()
    for textual_candidate in textual_candidates:
        if not textual_candidate or textual_candidate in seen_text:
            continue
        seen_text.add(textual_candidate)
        try:
            return schema_type.model_validate_json(textual_candidate)
        except Exception as exc:
            errors.append(exc)

    # A model may preface otherwise valid JSON with a short explanation. A
    # decoder-based scan finds the first complete value and avoids the old
    # greedy first-"{" to last-"}" extraction bug.
    for value in _json_values(candidate):
        try:
            return schema_type.model_validate(value)
        except Exception as exc:
            errors.append(exc)

    cause = errors[-1] if errors else ValueError("No JSON value found.")
    logger.warning(
        "Deep Research %s structured output validation failed: %s",
        getattr(schema_type, "__name__", "unknown"),
        _validation_summary(cause),
    )
    raise DeepResearchStructuredOutputError(
        getattr(schema_type, "__name__", "StructuredOutput"),
        _validation_summary(cause),
    ) from cause


def structured_output_repair_request(
    *,
    schema_type: type[Any],
    original_input: str,
    invalid_output: str,
    validation_summary: str,
    repair_context: str = "",
) -> tuple[str, str]:
    """Build a bounded, provider-neutral retry for an invalid phase response."""

    schema_name = str(getattr(schema_type, "__name__", "StructuredOutput"))
    instructions = (
        "Complete the original task and return exactly one JSON value that "
        f"validates against {schema_name}. Use the original phase input and "
        "preserve every valid part of the invalid response. Correct every "
        "listed validation error. Do not call tools, add commentary, or use "
        "Markdown fences. Return complete JSON only; never stop mid-value."
    )
    focused_context = str(repair_context or "").strip()
    focused_section = (
        f"Focused repair context:\n{focused_context[:20_000]}\n\n"
        if focused_context
        else ""
    )
    input_text = (
        f"Required JSON Schema:\n"
        f"{json.dumps(schema_type.model_json_schema(), ensure_ascii=False)}\n\n"
        f"Validation errors:\n{str(validation_summary or 'Invalid JSON.')[:4_000]}\n\n"
        f"{focused_section}"
        "Original phase input:\n"
        f"{str(original_input or '')[:_MAX_REPAIR_CONTEXT_CHARS]}\n\n"
        "Invalid phase response:\n"
        f"{str(invalid_output or '')[:_MAX_REPAIR_OUTPUT_CHARS]}"
    )
    return instructions, input_text
