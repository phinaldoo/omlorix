"""Provider-neutral nested LLM requests used by feature orchestrators."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Generator, Iterable
import json
import uuid

from app.llm.models import Models
from app.llm.provider_request import ProviderRequest, REQUEST_TYPE_CHAT, call_provider_chat
from app.llm.schemas import normalize_provider_value
from app.tools.errors import ToolExecutionDiagnosticError


@dataclass(slots=True)
class NestedGenerationResult:
    """Small provider-independent result from a normal Omlorix chat request."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, str]] = field(default_factory=list)
    metadata: list[dict[str, Any]] = field(default_factory=list)


def _clone_model(model: Models, tools: Iterable[str], settings: dict[str, Any]) -> Any:
    """Detach runtime settings so nested requests never mutate model records."""
    selected_tools = [str(name).strip() for name in tools if str(name).strip()]
    model_settings = deepcopy(model.settings) if isinstance(model.settings, dict) else {}
    model_settings.update(deepcopy(settings))
    model_settings["enabled_tools"] = selected_tools
    model_settings["_runtime_enabled_tools"] = selected_tools
    capabilities = deepcopy(model.capabilities) if isinstance(model.capabilities, list) else []
    if selected_tools and "tools" not in capabilities:
        capabilities.append("tools")
    return SimpleNamespace(
        id=model.id, name=model.name, description=getattr(model, "description", None),
        model_icon=getattr(model, "model_icon", None), provider=model.provider,
        provider_id=model.provider_id, model_name=model.model_name, settings=model_settings,
        capabilities=capabilities, tools=selected_tools,
        access=deepcopy(getattr(model, "access", None) or {}),
        meta=deepcopy(getattr(model, "meta", None) or {}),
        status=getattr(model, "status", None), is_active=getattr(model, "is_active", True),
        created_at=getattr(model, "created_at", None),
    )


def stream_nested_generation(
    db,
    *,
    model_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
    instructions: str,
    tools: Iterable[str] = (),
    chat_id: str | None = None,
    project_id: str | None = None,
    user_role: str | None = None,
    settings_override: dict[str, Any] | None = None,
    purpose: str = "nested-generation",
    phase: str = "nested generation",
) -> Generator[str, None, NestedGenerationResult]:
    """Stream nested assistant text and return its complete normalized result.

    Feature orchestrators normally need the final answer, but artifact builders
    can often show useful work before the nested request has finished.  This
    generator exposes only provider-normalized ``c`` text deltas while retaining
    the same terminal validation, diagnostic metadata, and result shape as
    :func:`run_nested_generation`.

    Callers that enable nested tools should treat streamed text as provisional:
    Omlorix clears pre-tool commentary from the final result when a tool call
    begins.  Presentation generation deliberately disables nested tools, so its
    streamed HTML and terminal HTML remain the same document.
    """
    model = db.query(Models).filter(Models.id == str(model_id), Models.is_active == True).first()  # noqa: E712
    if model is None:
        raise ToolExecutionDiagnosticError(
            f"Nested model request failed during {phase}. "
            f"Configured model ID: {model_id}. Cause: the configured model no longer exists.",
            statistic_meta={
                "nested_generation": {
                    "phase": phase,
                    "purpose": purpose,
                    "model_id": str(model_id),
                    "model_name": "",
                    "provider": "",
                }
            },
        )
    model_metadata = model.meta if isinstance(getattr(model, "meta", None), dict) else {}
    if model_metadata.get("user_managed") is True:
        # Nested feature models come from global administrator settings. A
        # private model cannot satisfy that contract because its runtime and
        # credentials belong to one user, even if its row remains active.
        raise ToolExecutionDiagnosticError(
            f"Nested model request failed during {phase}. "
            f"Configured model ID: {model_id}. Cause: user-managed models are "
            "not available for global feature configuration.",
            statistic_meta={
                "nested_generation": {
                    "phase": phase,
                    "purpose": purpose,
                    "model_id": str(model.id),
                    "model_name": str(model.name or model.model_name or model.id),
                    "provider": normalize_provider_value(model.provider),
                }
            },
        )
    selected_tools = list(tools)
    runtime_model = _clone_model(model, selected_tools, settings_override or {})
    request = ProviderRequest(
        request_type=REQUEST_TYPE_CHAT,
        db=db,
        provider=normalize_provider_value(model.provider),
        model=runtime_model,
        chat_history=messages,
        user_id=str(user_id),
        project_id=project_id,
        generation_id=f"{purpose}:{uuid.uuid4()}",
        temp_request_flag=True,
        settings_override={
            **deepcopy(settings_override or {}),
            "enabled_tools": selected_tools,
            "_runtime_enabled_tools": selected_tools,
        },
        system_instruction_sections=[{"title": "Task instructions", "content": instructions}],
        user_role=user_role,
        extra={"chat_id": chat_id},
    )
    current_text: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    files: list[dict[str, str]] = []
    metadata: list[dict[str, Any]] = []
    terminal = False
    try:
        for raw_line in call_provider_chat(request):
            try:
                payload = json.loads(str(raw_line or "").strip())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            event_type = str(payload.get("t") or "")
            if event_type == "c" and isinstance(payload.get("d"), str):
                delta = payload["d"]
                current_text.append(delta)
                # Yield the provider-normalized text rather than raw provider
                # protocol so feature streams remain provider-independent.
                yield delta
            elif event_type == "t_c":
                # Text preceding a tool call is commentary, not the terminal answer.
                current_text.clear()
                descriptor = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                tool_calls.append({
                    "name": descriptor.get("name") or descriptor.get("tool_name"),
                    "id": descriptor.get("id") or descriptor.get("call_id"),
                    "arguments": descriptor.get("args", payload.get("c")),
                })
            elif event_type == "f":
                files.append({"file_id": str(payload.get("d") or ""), "name": str(payload.get("n") or "")})
            elif event_type == "e":
                raise RuntimeError(str(payload.get("d") or "Nested model request failed."))
            elif event_type == "d" and payload.get("d") == "f":
                terminal = True
                if isinstance(payload.get("c"), dict):
                    metadata.append(payload["c"])
        if not terminal:
            raise RuntimeError("The nested model request ended before completion.")
    except ToolExecutionDiagnosticError:
        raise
    except Exception as exc:
        provider = normalize_provider_value(model.provider)
        display_name = str(model.name or model.model_name or model.id)
        upstream_name = str(model.model_name or "").strip()
        model_detail = display_name
        if upstream_name and upstream_name != display_name:
            model_detail = f"{display_name} ({upstream_name})"
        raise ToolExecutionDiagnosticError(
            f"Nested model request failed during {phase}. Model: {model_detail}. "
            f"Provider: {provider}. Cause: {str(exc) or type(exc).__name__}",
            statistic_meta={
                "nested_generation": {
                    "phase": phase,
                    "purpose": purpose,
                    "model_id": str(model.id),
                    "model_name": display_name,
                    "provider": provider,
                }
            },
        ) from exc
    return NestedGenerationResult(
        text="".join(current_text).strip(), tool_calls=tool_calls, files=files, metadata=metadata
    )


def run_nested_generation(
    db,
    *,
    model_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
    instructions: str,
    tools: Iterable[str] = (),
    chat_id: str | None = None,
    project_id: str | None = None,
    user_role: str | None = None,
    settings_override: dict[str, Any] | None = None,
    purpose: str = "nested-generation",
    phase: str = "nested generation",
) -> NestedGenerationResult:
    """Execute a nested request and collect its provider-neutral result.

    Keeping this compatibility wrapper means existing feature orchestrators do
    not need to understand generator return values.  Progressive features can
    consume :func:`stream_nested_generation` directly instead.
    """

    stream = stream_nested_generation(
        db,
        model_id=model_id,
        user_id=user_id,
        messages=messages,
        instructions=instructions,
        tools=tools,
        chat_id=chat_id,
        project_id=project_id,
        user_role=user_role,
        settings_override=settings_override,
        purpose=purpose,
        phase=phase,
    )
    while True:
        try:
            next(stream)
        except StopIteration as completed:
            return completed.value
