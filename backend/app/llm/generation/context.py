"""One model-dependent budget for the complete native provider request.

Text uses a conservative UTF-8 byte bound (not an English-only chars/token
ratio). Native media uses explicit estimates because tokenization is provider
specific. No instruction, current user turn, or tool call/result pair is sliced.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Any


class ContextBudgetExceeded(ValueError):
    """Required context exceeds the configured model input budget."""


def _data(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value


def estimate_tokens(value):
    value = _data(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        return 8192
    if isinstance(value, dict):
        kind = str(value.get("type", ""))
        if kind in {"image", "input_image", "image_url"}:
            return 8192
        if kind in {"input_audio", "audio", "video", "input_video"}:
            return 32768
        if kind in {"document", "input_file", "file"}:
            return 16384
        if "inline_data" in value or "file_data" in value:
            return 16384
        return 8 + sum(
            len(str(key)) + estimate_tokens(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(estimate_tokens(item) + 4 for item in value)
    return len(str(value)) if value is not None else 0


def _positive(*values, default):
    for value in values:
        try:
            if int(value or 0) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _user_turn(message):
    value = _data(message)
    if not isinstance(value, dict) or value.get("role") != "user":
        return False
    parts = value.get("content", value.get("parts", []))
    # Anthropic/Gemini encode tool responses in a user-role message. They must
    # remain attached to the assistant call in the same indivisible turn.
    return not (
        isinstance(parts, list)
        and any(
            isinstance(_data(part), dict)
            and (
                _data(part).get("type") == "tool_result"
                or "function_response" in _data(part)
            )
            for part in parts
        )
    )


@dataclass(slots=True)
class ContextSegment:
    source: str
    priority: int
    required: bool
    content: list[Any]

    def diagnostic(self):
        encoded = json.dumps(self.content, default=str, sort_keys=True).encode()
        return {
            "source": self.source,
            "priority": self.priority,
            "revision": hashlib.sha256(encoded).hexdigest()[:16],
            "estimated_tokens": estimate_tokens(self.content),
        }


class ContextBuilder:
    def __init__(self):
        self.prefix_count = 0
        self.prefix_sections = []
        self.last_report = None

    def prepare(self, kwargs, *, settings, protocol):
        payload = kwargs.get("json", kwargs) if protocol == "openrouter" else kwargs
        history_key = (
            "input"
            if "input" in payload
            else "contents"
            if "contents" in payload
            else "messages"
        )
        history = payload.get(history_key)
        if not isinstance(history, list):
            return
        config = _data(payload.get("config", {})) or {}
        window = _positive(
            settings.get("input_token_limit"),
            settings.get("input_tokens_limit"),
            default=8192,
        )
        if protocol == "ollama":
            window = min(
                window,
                _positive(
                    (payload.get("options") or {}).get("num_ctx"),
                    settings.get("num_ctx"),
                    default=window,
                ),
            )
        output = _positive(
            payload.get("max_output_tokens"),
            payload.get("max_completion_tokens"),
            payload.get("max_tokens"),
            config.get("max_output_tokens"),
            (payload.get("options") or {}).get("num_predict"),
            default=min(4096, window // 4),
        )
        output = min(
            output, _positive(settings.get("output_token_limit"), default=output)
        )
        if output >= window:
            raise ContextBudgetExceeded("context_budget_exceeded")
        if protocol == "anthropic":
            payload["max_tokens"] = output
        elif protocol == "google_aistudio":
            if isinstance(payload.get("config"), dict):
                payload["config"]["max_output_tokens"] = output
            elif payload.get("config") is not None:
                payload["config"].max_output_tokens = output
        elif protocol == "ollama":
            payload["options"] = dict(payload.get("options") or {})
            payload["options"].update(num_predict=output, num_ctx=window)
        elif protocol == "openai_chat_completions":
            key = "max_tokens" if "max_tokens" in payload else "max_completion_tokens"
            payload[key] = output
        else:
            payload["max_output_tokens"] = output
        budget = max(0, window - output - min(1024, window // 20))
        fixed = {
            key: value
            for key, value in payload.items()
            if key in {"instructions", "system", "tools", "config"}
        }
        # Sampling/config scalars add a small overestimate, preserving the
        # invariant that schema/instruction overhead cannot be omitted.
        fixed_tokens = estimate_tokens(fixed)
        prefix = []
        body = history
        while (
            body
            and isinstance(_data(body[0]), dict)
            and _data(body[0]).get("role") in {"system", "developer"}
        ):
            prefix.append(body[0])
            body = body[1:]
        segments = [ContextSegment("instructions", 100, True, prefix)] if prefix else []
        if self.prefix_sections:
            for source, start, end, required, priority in self.prefix_sections:
                if end > start:
                    segments.append(
                        ContextSegment(
                            source, priority, required, list(body[start:end])
                        )
                    )
        elif self.prefix_count:
            segments.append(
                ContextSegment("workspace", 90, True, list(body[: self.prefix_count]))
            )
        body = body[self.prefix_count :]
        # Never join the first history message to an optional attachment.
        history_started = False
        for message in body:
            if not history_started or _user_turn(message):
                segments.append(ContextSegment("history", 30, False, []))
                history_started = True
            segments[-1].content.append(message)
        if segments:
            segments[-1].required = True
            segments[-1].priority = 100
            segments[-1].source = "current_turn"
        total = fixed_tokens + sum(
            estimate_tokens(segment.content) for segment in segments
        )
        removed = set()
        for segment in sorted(segments, key=lambda item: item.priority):
            if total <= budget:
                break
            if segment.required:
                continue
            total -= estimate_tokens(segment.content)
            removed.add(id(segment))
        if total > budget:
            raise ContextBudgetExceeded("context_budget_exceeded")
        retained = [segment for segment in segments if id(segment) not in removed]
        payload[history_key] = [
            message for segment in retained for message in segment.content
        ]
        self.last_report = {
            "input_budget": budget,
            "output_reserve": output,
            "estimated_input_tokens": total,
            "fixed_tokens": fixed_tokens,
            "removed_segments": len(removed),
            "segments": [segment.diagnostic() for segment in retained[-50:]],
        }
