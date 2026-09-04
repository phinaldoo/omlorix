"""Feature-owned compact history representation of tool results."""

from typing import Any
from copy import deepcopy
from app.tools.results import _copy_result_fields, _content_metadata


def _compact_automation_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    def compact_automation(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        compact = _copy_result_fields(
            item,
            (
                "id",
                "title",
                "icon",
                "icon_color",
                "model_id",
                "schedule_rules",
                "schedule_timezone",
                "skill_id",
                "note_ids",
                "file_ids",
                "mcp_server_ids",
                "is_active",
                "last_triggered_at",
                "created_at",
                "last_updated_at",
                "prompt_length",
                "prompt_sha256",
                "prompt_selection",
                "prompt_truncated",
                "schedule_rule_count",
                "note_count",
                "file_count",
                "mcp_server_count",
            ),
        )
        prompt = item.get("prompt")
        prompt_metadata = _content_metadata(prompt)
        if "content_length" in prompt_metadata:
            compact["prompt_length"] = prompt_metadata["content_length"]
            compact["prompt_sha256"] = prompt_metadata["content_sha256"]
        return compact

    compact = _copy_result_fields(
        payload,
        (
            "status",
            "operation",
            "message",
            "count",
            "limit",
            "offset",
            "has_more",
            "next_cursor",
            "code",
            "error",
        ),
    )
    if isinstance(payload.get("automation"), dict):
        compact["automation"] = compact_automation(payload["automation"])
    if isinstance(payload.get("automations"), list):
        compact["automations"] = [
            compact_automation(item) for item in payload["automations"][:100]
        ]
    if "categories" in payload:
        compact.update(
            _copy_result_fields(
                payload,
                (
                    "tool",
                    "instruction",
                    "webhook_policy",
                    "categories",
                    "inputs",
                ),
            )
        )
        for field in (
            "icon_options",
            "color_options",
            "available_models",
            "available_skills",
        ):
            value = payload.get(field)
            if isinstance(value, list):
                compact[field] = deepcopy(value[:100])
        server_options = payload.get("available_mcp_servers_by_model")
        if isinstance(server_options, dict):
            compact["available_mcp_servers_by_model"] = {
                str(model_id): deepcopy(options[:100])
                for model_id, options in list(server_options.items())[:10]
                if isinstance(options, list)
            }
    return compact or {"status": "completed"}


compact_result = _compact_automation_result
