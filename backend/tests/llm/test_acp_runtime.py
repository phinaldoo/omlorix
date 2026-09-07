from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace

import pytest

from app.llm.acp.permissions import AcpPermissionRegistry
from app.llm.acp.runtime import (
    OmlorixAcpClient,
    _validate_protocol_version,
    build_acp_prompt_blocks,
    omlorix_client_capabilities,
    discover_acp_models,
    extract_acp_session_controls,
    normalize_acp_completion_metadata,
    resolve_workspace_path,
    run_acp_turn,
)
from app.llm.acp.schemas import AcpModelSettings
from app.llm.acp import utils as acp_utils


FAKE_ACP_AGENT = textwrap.dedent(
    r"""
    import json
    import sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    current_model = "codex-default"
    current_reasoning = "medium"
    current_mode = "agent"

    def config_options():
        return [
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": current_model,
                "options": [
                    {"value": "codex-default", "name": "Codex Default"},
                    {"value": "codex-fast", "name": "Codex Fast"}
                ]
            },
            {
                "id": "reasoning_effort",
                "name": "Reasoning effort",
                "category": "thought_level",
                "type": "select",
                "currentValue": current_reasoning,
                "options": [
                    {"value": "low", "name": "Low"},
                    {"value": "medium", "name": "Medium"},
                    {"value": "high", "name": "High"}
                ]
            }
        ]

    def modes():
        return {
            "currentModeId": current_mode,
            "availableModes": [
                {"id": "read-only", "name": "Read-only"},
                {"id": "agent", "name": "Agent"},
                {"id": "agent-full-access", "name": "Agent (full access)"}
            ]
        }

    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": True, "audio": True, "embeddedContext": True},
                        "mcpCapabilities": {"http": False, "sse": False},
                        "sessionCapabilities": {}
                    },
                    "agentInfo": {"name": "fake-acp", "title": "Fake ACP", "version": "1.0"},
                    "authMethods": []
                }
            })
        elif method == "session/new":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "sessionId": "session-1",
                    "modes": modes(),
                    "configOptions": config_options()
                }
            })
        elif method == "session/load":
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "historical output"}
                    }
                }
            })
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "modes": modes(),
                    "configOptions": config_options()
                }
            })
        elif method == "session/prompt":
            if any(block.get("type") != "text" for block in params.get("prompt") or []):
                send({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": params["sessionId"],
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": "__prompt_capture__" + json.dumps(params["prompt"])
                            }
                        }
                    }
                })
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "thinking"}
                    }
                }
            })
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello from ACP"}
                    }
                }
            })
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {"sessionUpdate": "usage_update", "size": 1000, "used": 14}
                }
            })
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "stopReason": "end_turn",
                    "usage": {
                        "inputTokens": 8,
                        "cachedReadTokens": 3,
                        "outputTokens": 2,
                        "thoughtTokens": 1,
                        "totalTokens": 14
                    },
                    "_meta": {
                        "quota": {
                            "model_usage": [{"model": "codex-default", "token_count": {"totalTokens": 14}}]
                        }
                    }
                }
            })
        elif method == "session/set_mode":
            current_mode = params["modeId"]
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {
                        "sessionUpdate": "current_mode_update",
                        "currentModeId": current_mode
                    }
                }
            })
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "session/set_config_option":
            if params["configId"] == "model":
                current_model = params["value"]
            elif params["configId"] == "reasoning_effort":
                current_reasoning = params["value"]
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": params["sessionId"],
                    "update": {
                        "sessionUpdate": "config_option_update",
                        "configOptions": config_options()
                    }
                }
            })
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "configOptions": config_options()
                }
            })
        elif method == "session/close":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    """
)


def test_personal_acp_statistics_keep_user_managed_privacy_marker(monkeypatch):
    """Private ACP statistics must remain identifiable after provider deletion."""

    captured = {}
    monkeypatch.setattr(
        acp_utils,
        "create_llm_generation_statistic",
        lambda _db, **kwargs: captured.update(kwargs),
    )
    model = SimpleNamespace(
        id="personal-model",
        meta={"user_managed": True, "owner_user_id": "user-1"},
    )
    provider = SimpleNamespace(id="personal-provider")

    acp_utils._record_acp_generation_statistic(
        object(),
        db_model=model,
        provider=provider,
        model_name="codex-default",
        metadata={"input_tokens": 12},
        user_id="user-1",
        success=True,
    )

    assert captured["meta"]["input_tokens"] == 12
    assert captured["meta"]["user_managed"] is True


def _run_fake_turn(
    tmp_path: Path,
    *,
    existing_session_id: str | None = None,
    selected_model_id: str | None = None,
    attachments: list[dict] | None = None,
    new_session_attachments: list[dict] | None = None,
    supports_attachments: bool = True,
    selected_security_level: str | None = None,
    selected_reasoning_effort: str | None = None,
    response_less_load: bool = False,
) -> list[dict]:
    events: list[dict] = []
    agent_source = FAKE_ACP_AGENT
    if not supports_attachments:
        agent_source = agent_source.replace(
            '{"image": True, "audio": True, "embeddedContext": True}',
            '{"image": False, "audio": False, "embeddedContext": False}',
        )
    if response_less_load:
        agent_source = agent_source.replace(
            '''"result": {
                "modes": modes(),
                "configOptions": config_options()
            }''',
            '"result": None',
            1,
        )
    asyncio.run(
        run_acp_turn(
            command=sys.executable,
            arguments=["-u", "-c", agent_source],
            process_cwd=str(tmp_path),
            environment={},
            session_cwd=str(tmp_path),
            additional_directories=[],
            existing_session_id=existing_session_id,
            mode=None,
            selected_model_id=selected_model_id,
            selected_security_level=selected_security_level,
            selected_reasoning_effort=selected_reasoning_effort,
            prompt="hello",
            new_session_prompt="User:\nhello",
            attachments=attachments,
            new_session_attachments=new_session_attachments,
            user_id="user-1",
            generation_id="generation-1",
            permission_mode="deny",
            permission_timeout_seconds=15,
            prompt_timeout_seconds=30,
            emit=events.append,
            is_cancelled=lambda: False,
        )
    )
    return events


def test_acp_runtime_streams_a_complete_turn(tmp_path: Path):
    """The runtime negotiates ACP, creates a session, and forwards live updates."""
    events = _run_fake_turn(tmp_path)

    assert {event["kind"] for event in events} >= {
        "session",
        "session_update",
        "complete",
    }
    message_updates = [
        event["update"]
        for event in events
        if event.get("update", {}).get("sessionUpdate") == "agent_message_chunk"
    ]
    assert message_updates[0]["content"]["text"] == "hello from ACP"
    completion = next(event for event in events if event.get("kind") == "complete")
    expected_metadata = {
        "model_id": "codex-default",
        "input_tokens": 11,
        "input_token_cached": 3,
        "output_tokens": 3,
        "reasoning_tokens": 1,
        "total_tokens": 14,
        "stop_reason": "end_turn",
    }
    for key, value in expected_metadata.items():
        assert completion["metadata"][key] == value
    assert completion["metadata"]["generation_time"] >= 0
    assert completion["metadata"]["time_to_first_token"] >= 0


def test_acp_runtime_suppresses_history_replayed_by_session_load(tmp_path: Path):
    """Resuming an ACP session must not duplicate its historical transcript in Omlorix."""
    events = _run_fake_turn(tmp_path, existing_session_id="session-1")

    streamed_text = [
        event["update"]["content"]["text"]
        for event in events
        if event.get("update", {}).get("sessionUpdate") == "agent_message_chunk"
    ]
    assert streamed_text == ["hello from ACP"]
    assert any(
        event.get("kind") == "session" and event.get("session_id") == "session-1"
        for event in events
    )


def test_acp_runtime_recreates_response_less_load_when_controls_are_requested(
    tmp_path: Path,
):
    """A response-less load cannot prevent persisted controls from being applied."""
    events = _run_fake_turn(
        tmp_path,
        existing_session_id="session-existing",
        selected_model_id="codex-fast",
        response_less_load=True,
    )

    assert any(
        event.get("kind") == "warning"
        and event.get("i18n_key") == "acp_session_recreated"
        for event in events
    )
    completion = next(event for event in events if event.get("kind") == "complete")
    assert completion["metadata"]["model_id"] == "codex-fast"


def test_acp_runtime_applies_advertised_session_model(tmp_path: Path):
    """A selected ACP model is validated and applied before the prompt starts."""
    events = _run_fake_turn(tmp_path, selected_model_id="codex-fast")

    updates = [
        event["update"]
        for event in events
        if event.get("update", {}).get("sessionUpdate") == "config_option_update"
    ]
    assert updates[0]["configOptions"][0]["currentValue"] == "codex-fast"
    completion = next(event for event in events if event.get("kind") == "complete")
    assert completion["metadata"]["model_id"] == "codex-fast"


def test_acp_runtime_applies_security_model_and_reasoning_controls(tmp_path: Path):
    """All three advertised controls are validated and applied before prompting."""
    events = _run_fake_turn(
        tmp_path,
        selected_model_id="codex-fast",
        selected_security_level="read-only",
        selected_reasoning_effort="high",
    )

    mode_update = next(
        event["update"]
        for event in events
        if event.get("update", {}).get("sessionUpdate") == "current_mode_update"
    )
    assert mode_update["currentModeId"] == "read-only"
    config_updates = [
        event["update"]["configOptions"]
        for event in events
        if event.get("update", {}).get("sessionUpdate") == "config_option_update"
    ]
    assert config_updates[-1][0]["currentValue"] == "codex-fast"
    assert config_updates[-1][1]["currentValue"] == "high"
    completion = next(event for event in events if event.get("kind") == "complete")
    assert completion["metadata"]["model_id"] == "codex-fast"
    assert completion["metadata"]["acp_security_level"] == "read-only"
    assert completion["metadata"]["acp_reasoning_effort"] == "high"


def test_acp_discovery_returns_all_session_controls(tmp_path: Path):
    """Discovery exposes model, legacy security modes, and reasoning options."""
    payload = asyncio.run(
        discover_acp_models(
            command=sys.executable,
            arguments=["-u", "-c", FAKE_ACP_AGENT],
            session_cwd=str(tmp_path),
            additional_directories=[],
        )
    )

    assert payload["session_id"] == "session-1"
    assert payload["current_model_id"] == "codex-default"
    assert [option["id"] for option in payload["models"]] == [
        "codex-default",
        "codex-fast",
    ]
    assert payload["current_security_level"] == "agent"
    assert [option["id"] for option in payload["security_levels"]] == [
        "read-only",
        "agent",
        "agent-full-access",
    ]
    assert payload["current_reasoning_effort"] == "medium"
    assert [option["id"] for option in payload["reasoning_efforts"]] == [
        "low",
        "medium",
        "high",
    ]


def test_acp_discovery_recreates_a_response_less_loaded_session(tmp_path: Path):
    """Discovery falls back to a new session when load returns no option metadata."""
    response_less_agent = FAKE_ACP_AGENT.replace(
        '''"result": {
                "modes": modes(),
                "configOptions": config_options()
            }''',
        '"result": None',
        1,
    )

    payload = asyncio.run(
        discover_acp_models(
            command=sys.executable,
            arguments=["-u", "-c", response_less_agent],
            session_cwd=str(tmp_path),
            additional_directories=[],
            existing_session_id="session-existing",
        )
    )

    assert payload["session_id"] == "session-1"
    assert payload["current_model_id"] == "codex-default"


def test_acp_combined_codex_variants_become_independent_controls():
    """Codex-style model[effort] values render as model plus reasoning selectors."""
    controls = extract_acp_session_controls(
        {
            "configOptions": [
                {
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": "gpt-5.6-sol[high]",
                    "options": [
                        {
                            "value": "gpt-5.6-sol[low]",
                            "name": "GPT-5.6-Sol (low)",
                            "description": "Latest frontier model. Fast responses.",
                        },
                        {
                            "value": "gpt-5.6-sol[high]",
                            "name": "GPT-5.6-Sol (high)",
                            "description": "Latest frontier model. Greater reasoning.",
                        },
                        {
                            "value": "gpt-5.6-terra[low]",
                            "name": "GPT-5.6-Terra (low)",
                            "description": "Balanced coding model. Fast responses.",
                        },
                        {
                            "value": "gpt-5.6-terra[high]",
                            "name": "GPT-5.6-Terra (high)",
                            "description": "Balanced coding model. Greater reasoning.",
                        },
                    ],
                }
            ],
            "modes": {
                "currentModeId": "agent",
                "availableModes": [
                    {"id": "read-only", "name": "Read-only"},
                    {"id": "agent", "name": "Agent"},
                ],
            },
        }
    )

    assert controls["model"]["current_value"] == "gpt-5.6-sol"
    assert [option["id"] for option in controls["model"]["options"]] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]
    assert controls["reasoning"]["current_value"] == "high"
    assert [option["id"] for option in controls["reasoning"]["options"]] == [
        "low",
        "high",
    ]
    assert controls["reasoning"]["reasoning_efforts_by_model"] == {
        "gpt-5.6-sol": ["low", "high"],
        "gpt-5.6-terra": ["low", "high"],
    }
    assert (
        controls["model"]["combined_variants"]["gpt-5.6-terra\0high"]
        == "gpt-5.6-terra[high]"
    )


def test_acp_stable_mode_config_supersedes_legacy_session_modes():
    """Stable mode-category options provide the security selector when present."""
    controls = extract_acp_session_controls(
        {
            "configOptions": [
                {
                    "id": "approval_mode",
                    "name": "Security",
                    "category": "mode",
                    "type": "select",
                    "currentValue": "read-only",
                    "options": [
                        {"value": "read-only", "name": "Read-only"},
                        {"value": "agent", "name": "Agent"},
                    ],
                }
            ],
            "modes": {
                "currentModeId": "legacy-mode",
                "availableModes": [
                    {"id": "legacy-mode", "name": "Legacy mode"},
                ],
            },
        }
    )

    assert controls["security"]["transport"] == "config_option"
    assert controls["security"]["config_id"] == "approval_mode"
    assert controls["security"]["current_value"] == "read-only"


def test_acp_client_advertises_stable_session_config_support():
    """Agents can detect that Omlorix understands stable config options."""
    payload = omlorix_client_capabilities().model_dump(
        by_alias=True,
        exclude_none=True,
    )
    assert payload["session"]["configOptions"]["boolean"] == {}


def test_acp_runtime_sends_capability_gated_native_attachment_blocks(tmp_path: Path):
    """The negotiated prompt contains native media and embedded document resources."""
    image_data = "aW1hZ2UtYnl0ZXM="
    document_text = "The launch date is Tuesday."
    events = _run_fake_turn(
        tmp_path,
        attachments=[
            {
                "file_id": "image-1",
                "name": "diagram.png",
                "mime_type": "image/png",
                "category": "image",
                "size": 11,
                "data": image_data,
            },
            {
                "file_id": "document-1",
                "name": "brief.txt",
                "mime_type": "text/plain",
                "category": "document",
                "size": len(document_text),
                "text": document_text,
            },
        ],
        new_session_attachments=[
            {
                "file_id": "image-1",
                "name": "diagram.png",
                "mime_type": "image/png",
                "category": "image",
                "size": 11,
                "data": image_data,
            },
            {
                "file_id": "document-1",
                "name": "brief.txt",
                "mime_type": "text/plain",
                "category": "document",
                "size": len(document_text),
                "text": document_text,
            },
        ],
    )

    capture = next(
        event["update"]["content"]["text"]
        for event in events
        if str(event.get("update", {}).get("content", {}).get("text") or "").startswith(
            "__prompt_capture__"
        )
    )
    prompt_blocks = json.loads(capture.removeprefix("__prompt_capture__"))
    assert [block["type"] for block in prompt_blocks] == [
        "text",
        "text",
        "image",
        "text",
        "resource",
    ]
    assert prompt_blocks[2]["data"] == image_data
    assert prompt_blocks[2]["mimeType"] == "image/png"
    assert prompt_blocks[4]["resource"]["text"] == document_text
    assert prompt_blocks[4]["resource"]["uri"] == "omlorix://files/document-1"
    assert all(str(tmp_path) not in json.dumps(block) for block in prompt_blocks)


def test_acp_attachment_blocks_fall_back_to_text_without_media_capabilities():
    """Text-only agents receive extracted content and safe attachment metadata."""
    capabilities = SimpleNamespace(
        prompt_capabilities=SimpleNamespace(
            image=False,
            audio=False,
            embedded_context=False,
        )
    )
    blocks = build_acp_prompt_blocks(
        "Review these files.",
        [
            {
                "file_id": "image-1",
                "name": "diagram.png",
                "mime_type": "image/png",
                "category": "image",
                "size": 20,
                "data": "aW1hZ2U=",
            },
            {
                "file_id": "document-1",
                "name": "brief.txt",
                "mime_type": "text/plain",
                "category": "document",
                "size": 5,
                "text": "Hello",
            },
        ],
        capabilities,
    )
    dumped = [block.model_dump(by_alias=True, exclude_none=True) for block in blocks]

    assert all(block["type"] == "text" for block in dumped)
    assert any("metadata_only" in block["text"] for block in dumped)
    assert any(
        "[Extracted content from brief.txt]\nHello" == block["text"] for block in dumped
    )


def test_acp_runtime_warns_when_attachment_content_cannot_be_delivered(tmp_path: Path):
    """Users are told when negotiated capabilities permit metadata only."""
    image = {
        "file_id": "image-1",
        "name": "diagram.png",
        "mime_type": "image/png",
        "category": "image",
        "size": 5,
        "data": "aW1hZ2U=",
    }
    events = _run_fake_turn(
        tmp_path,
        attachments=[image],
        new_session_attachments=[image],
        supports_attachments=False,
    )

    warning = next(event for event in events if event.get("kind") == "warning")
    assert warning["i18n_key"] == "acp_attachment_content_unavailable"


def test_acp_attachment_resolution_is_access_checked_bounded_and_path_private(
    tmp_path: Path,
    monkeypatch,
):
    """File IDs are resolved for the caller and storage paths stay backend-only."""
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image-bytes")
    document_path = tmp_path / "brief.txt"
    document_path.write_text("Useful document text", encoding="utf-8")
    requested: list[tuple[str, str]] = []

    def fake_get_file_info(user_id: str, file_id: str):
        requested.append((user_id, file_id))
        if file_id == "image-1":
            return {
                "path": str(image_path),
                "file_name": "stored-image",
                "file_type": "image/png",
                "file_category": "image",
                "file_size": image_path.stat().st_size,
                "meta": {"original_filename": "diagram.png"},
            }
        if file_id == "document-1":
            return {
                "path": str(document_path),
                "file_name": "stored-document",
                "file_type": "text/plain",
                "file_category": "document",
                "file_size": document_path.stat().st_size,
                "meta": {"original_filename": "brief.txt"},
            }
        return None

    monkeypatch.setattr(acp_utils, "get_file_info", fake_get_file_info)
    history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "user",
                    "content": "",
                    "images": ["image-1"],
                    "documents": [{"id": "document-1"}, "unavailable-1"],
                }
            ],
        }
    ]
    attachments = acp_utils._prepare_acp_attachments(
        history,
        "owner-1",
        replay=False,
    )

    assert requested == [
        ("owner-1", "image-1"),
        ("owner-1", "document-1"),
        ("owner-1", "unavailable-1"),
    ]
    assert base64.b64decode(attachments[0]["data"]) == b"image-bytes"
    assert attachments[1]["text"] == "Useful document text"
    assert attachments[2]["omission_reason"]
    assert all("path" not in attachment for attachment in attachments)
    assert (
        acp_utils._compose_prompt(
            history,
            None,
            replay=False,
            has_attachments=True,
        )
        == "Please work with the attached file or files."
    )


def test_acp_usage_falls_back_to_codex_quota_metadata():
    """Codex quota metadata remains usable when an ACP SDK omits standard usage."""
    metadata = normalize_acp_completion_metadata(
        {
            "stopReason": "end_turn",
            "_meta": {
                "quota": {
                    "model_usage": [{"model": "gpt-5.6-sol", "token_count": {}}],
                    "token_count": {
                        "inputTokens": 8432,
                        "cachedInputTokens": 9984,
                        "outputTokens": 12,
                        "reasoningOutputTokens": 4,
                        "totalTokens": 18432,
                    },
                }
            },
        }
    )

    assert metadata == {
        "model_id": "gpt-5.6-sol",
        "input_tokens": 18416,
        "input_token_cached": 9984,
        "output_tokens": 16,
        "reasoning_tokens": 4,
        "total_tokens": 18432,
        "stop_reason": "end_turn",
    }


def test_acp_request_system_instruction_replaces_model_default():
    assert acp_utils._effective_acp_system_instruction(
        {"system_instruction": "Administrator ACP instruction."},
        {"system_instruction": "Conversation ACP instruction."},
    ) == "Conversation ACP instruction."

    assert acp_utils._effective_acp_system_instruction(
        {"system_instruction": "Administrator ACP instruction."},
        {},
    ) == "Administrator ACP instruction."


def test_acp_usage_preserves_authoritative_zero_values():
    """A reported zero must not be replaced by a non-zero quota mirror."""
    metadata = normalize_acp_completion_metadata(
        {
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
            "_meta": {
                "quota": {
                    "token_count": {
                        "inputTokens": 50,
                        "outputTokens": 25,
                        "totalTokens": 75,
                    }
                }
            },
        }
    )

    assert metadata["input_tokens"] == 0
    assert metadata["output_tokens"] == 0
    assert metadata["total_tokens"] == 0


def _personal_acp_adapter_context(monkeypatch, chat):
    """Build the caller-owned profile/SSH rows required by the ACP adapter."""
    profile = SimpleNamespace(
        id="profile-1",
        user_id="user-1",
        ssh_connection_id="ssh-1",
        enabled=True,
        workspace_root="/srv/workspace",
        cwd=".",
        additional_directories=[],
        executable="opencode",
        arguments=["acp"],
        mode=None,
        permission_mode="ask",
        permission_timeout_seconds=300,
        prompt_timeout_seconds=1800,
    )
    ssh_connection = SimpleNamespace(id="ssh-1", user_id="user-1", enabled=True)

    class FakeQuery:
        def __init__(self, value):
            self.value = value

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.value

    class FakeDb:
        def query(self, model):
            if model is acp_utils.UserAcpProfile:
                return FakeQuery(profile)
            if model is acp_utils.SshConnection:
                return FakeQuery(ssh_connection)
            return FakeQuery(chat)

    provider = SimpleNamespace(
        id="provider-1",
        settings={"user_managed": True, "acp_profile_id": profile.id},
    )
    db_model = SimpleNamespace(
        id="acp-profile:1",
        provider_id=provider.id,
        model_name="ACP profile",
        settings={"acp_profile_id": profile.id},
        meta={
            "user_managed": True,
            "owner_user_id": "user-1",
            "acp_profile_id": profile.id,
        },
    )
    monkeypatch.setattr(acp_utils, "require_custom_acp_connections", lambda *_args: None)
    monkeypatch.setattr(acp_utils, "get_llm_provider", lambda _db, _id: provider)
    return FakeDb(), db_model


def test_acp_adapter_publishes_usage_as_persistable_assistant_metadata(
    tmp_path: Path, monkeypatch
):
    """Runtime usage reaches Omlorix's final stream event and history metadata."""
    chat = SimpleNamespace(meta={})
    runtime_request: dict = {}
    db, db_model = _personal_acp_adapter_context(monkeypatch, chat)

    async def fake_run_acp_turn(**kwargs):
        runtime_request.update(kwargs)
        emit = kwargs["emit"]
        emit({"kind": "session", "session_id": "session-1"})
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "usage_update",
                    "used": 18428,
                    "size": 258400,
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hello"},
                },
            }
        )
        emit(
            {
                "kind": "complete",
                "session_id": "session-1",
                "metadata": {
                    "model_id": "gpt-5.6-sol[high]",
                    "input_tokens": 18416,
                    "input_token_cached": 9984,
                    "output_tokens": 12,
                    "total_tokens": 18428,
                    "stop_reason": "end_turn",
                },
            }
        )

    monkeypatch.setattr(acp_utils, "run_acp_turn", fake_run_acp_turn)
    monkeypatch.setattr(
        acp_utils, "_record_acp_generation_statistic", lambda *args, **kwargs: None
    )

    events = [
        json.loads(line)
        for line in acp_utils.acp_chat(
            chat_id="chat-1",
            chat_history=[{"role": "user", "content": "Hey"}],
            db=db,
            db_model=db_model,
            user_id="user-1",
            generation_id="generation-1",
            temp_request_flag=True,
            acp_model_id="gpt-5.6-sol[high]",
            acp_security_level="agent",
            acp_reasoning_effort="high",
        )
    ]
    metadata = next(
        event["c"]
        for event in events
        if event.get("t") == "d" and event.get("d") == "f"
    )

    assert metadata["model"] == "gpt-5.6-sol[high]"
    assert metadata["input_tokens"] == 18416
    assert metadata["input_token_cached"] == 9984
    assert metadata["output_tokens"] == 12
    assert metadata["total_tokens"] == 18428
    assert metadata["context_tokens_used"] == 18428
    assert metadata["context_window_size"] == 258400
    assert metadata["acp_session_id"] == "session-1"
    assert metadata["acp_security_level"] == "agent"
    assert metadata["acp_reasoning_effort"] == "high"
    assert runtime_request["selected_model_id"] == "gpt-5.6-sol[high]"
    assert runtime_request["selected_security_level"] == "agent"
    assert runtime_request["selected_reasoning_effort"] == "high"


def test_acp_adapter_logs_message_persistence_without_logging_content(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    """ACP diagnostics identify the committed row while keeping chat text private."""
    secret_user_text = "user prompt that must not appear in logs"
    secret_assistant_text = "assistant response that must not appear in logs"
    chat = SimpleNamespace(id="chat-1", meta={})
    db, db_model = _personal_acp_adapter_context(monkeypatch, chat)

    async def fake_run_acp_turn(**kwargs):
        emit = kwargs["emit"]
        emit({"kind": "session", "session_id": "session-1"})
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": secret_assistant_text},
                },
            }
        )
        emit(
            {
                "kind": "complete",
                "session_id": "session-1",
                "metadata": {"stop_reason": "end_turn"},
            }
        )

    def fake_create_chat_message(
        db,
        chat_id,
        model_id,
        role,
        *,
        reference_id,
        content,
        retry_count,
    ):
        return SimpleNamespace(
            id="message-1",
            chat_id=chat_id,
            model_id=model_id,
            role=role,
            reference_id=reference_id,
            retry_count=retry_count or 0,
            created_at="2026-07-18T00:00:00Z",
            content=json.dumps(content),
        )

    monkeypatch.setattr(acp_utils, "run_acp_turn", fake_run_acp_turn)
    monkeypatch.setattr(acp_utils, "_save_session", lambda *args: None)
    monkeypatch.setattr(
        acp_utils, "_record_acp_generation_statistic", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(acp_utils, "create_chat_message", fake_create_chat_message)
    caplog.set_level("INFO", logger="app.llm.acp.utils")

    list(
        acp_utils.acp_chat(
            chat_id="chat-1",
            chat_history=[{"role": "user", "content": secret_user_text}],
            db=db,
            db_model=db_model,
            user_id="user-1",
            generation_id="generation-1",
        )
    )

    log_text = caplog.text
    assert '"event": "assistant_message_insert_begin"' in log_text
    assert '"event": "assistant_message_insert_committed"' in log_text
    assert '"table": "chat_messages"' in log_text
    assert '"message_id": "message-1"' in log_text
    assert secret_user_text not in log_text
    assert secret_assistant_text not in log_text


def test_acp_adapter_preserves_event_order_and_ignores_discovery_session(
    tmp_path: Path,
    monkeypatch,
):
    """Persist ACP blocks chronologically and never resume a discovery-only session."""
    chat = SimpleNamespace(id="chat-1", meta={})
    runtime_request: dict = {}
    persisted: dict = {}
    db, db_model = _personal_acp_adapter_context(monkeypatch, chat)

    async def fake_run_acp_turn(**kwargs):
        runtime_request.update(kwargs)
        emit = kwargs["emit"]
        emit({"kind": "session", "session_id": "session-new"})
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "**Plan**"},
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "First answer."},
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tool-1",
                    "title": "Search",
                    "kind": "search",
                    "status": "in_progress",
                    "rawInput": {"query": "providers"},
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "title": "Search",
                    "kind": "search",
                    "status": "completed",
                    "rawOutput": {"matches": 7},
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "**Verify**"},
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Final answer."},
                },
            }
        )
        emit(
            {
                "kind": "complete",
                "session_id": "session-new",
                "metadata": {"stop_reason": "end_turn"},
            }
        )

    def fake_create_chat_message(
        db,
        chat_id,
        model_id,
        role,
        *,
        reference_id,
        content,
        retry_count,
    ):
        persisted["content"] = content
        return SimpleNamespace(
            id="message-1",
            chat_id=chat_id,
            model_id=model_id,
            role=role,
            reference_id=reference_id,
            retry_count=retry_count or 0,
            created_at="2026-07-18T00:00:00Z",
            content=json.dumps(content),
        )

    monkeypatch.setattr(acp_utils, "run_acp_turn", fake_run_acp_turn)
    monkeypatch.setattr(acp_utils, "_save_session", lambda *args: None)
    monkeypatch.setattr(
        acp_utils, "_record_acp_generation_statistic", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(acp_utils, "create_chat_message", fake_create_chat_message)

    list(
        acp_utils.acp_chat(
            chat_id="chat-1",
            chat_history=[{"role": "user", "content": "Inspect providers"}],
            db=db,
            db_model=db_model,
            user_id="user-1",
            generation_id="generation-1",
            acp_session_id="discovery-session",
        )
    )

    assert runtime_request["existing_session_id"] is None
    assert [block["type"] for block in persisted["content"]] == [
        "reasoning",
        "content",
        "tool_call",
        "reasoning",
        "content",
    ]
    tool_block = persisted["content"][2]
    assert tool_block["meta"]["status"] == "completed"
    assert tool_block["meta"]["raw_output"] == {"matches": 7}
    assert persisted["content"][-1]["meta"]["acp_session_id"] == "session-new"


def test_acp_adapter_persists_live_agent_config_updates(
    tmp_path: Path,
    monkeypatch,
):
    """Agent-side control changes remain selected when the chat resumes."""
    chat = SimpleNamespace(meta={})
    saved_sessions: list[tuple] = []
    db, db_model = _personal_acp_adapter_context(monkeypatch, chat)

    async def fake_run_acp_turn(**kwargs):
        emit = kwargs["emit"]
        emit({"kind": "session", "session_id": "session-1"})
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "config_option_update",
                    "configOptions": [
                        {
                            "id": "model",
                            "name": "Model",
                            "category": "model",
                            "type": "select",
                            "currentValue": "codex-fast",
                            "options": [
                                {"value": "codex-default", "name": "Default"},
                                {"value": "codex-fast", "name": "Fast"},
                            ],
                        },
                        {
                            "id": "reasoning",
                            "name": "Reasoning",
                            "category": "thought_level",
                            "type": "select",
                            "currentValue": "high",
                            "options": [
                                {"value": "low", "name": "Low"},
                                {"value": "high", "name": "High"},
                            ],
                        },
                    ],
                },
            }
        )
        emit(
            {
                "kind": "session_update",
                "update": {
                    "sessionUpdate": "current_mode_update",
                    "currentModeId": "read-only",
                },
            }
        )
        emit(
            {
                "kind": "complete",
                "session_id": "session-1",
                "metadata": {
                    "stop_reason": "end_turn",
                    "model_id": "codex-fast",
                    "acp_security_level": "read-only",
                    "acp_reasoning_effort": "high",
                },
            }
        )

    monkeypatch.setattr(acp_utils, "run_acp_turn", fake_run_acp_turn)
    monkeypatch.setattr(
        acp_utils,
        "_save_session",
        lambda *args: saved_sessions.append(args),
    )
    monkeypatch.setattr(
        acp_utils,
        "_record_acp_generation_statistic",
        lambda *args, **kwargs: None,
    )

    events = [
        json.loads(line)
        for line in acp_utils.acp_chat(
            chat_id="chat-1",
            chat_history=[{"role": "user", "content": "Update the controls"}],
            db=db,
            db_model=db_model,
            user_id="user-1",
            generation_id="generation-1",
        )
    ]

    updates = [event["d"] for event in events if event.get("t") == "acp_config"]
    assert updates[0]["current_model_id"] == "codex-fast"
    assert updates[0]["current_reasoning_effort"] == "high"
    assert updates[1]["current_security_level"] == "read-only"
    # The early session event records only the agent-validated session ID.
    assert len(saved_sessions[0]) == 6
    # The last three positional arguments are the selected model, security,
    # and reasoning values from the successful runtime completion.
    assert saved_sessions[-1][-3:] == ("codex-fast", "read-only", "high")


def test_acp_adapter_resolves_file_only_message_before_runtime(
    tmp_path: Path,
    monkeypatch,
):
    """An Omlorix file-only turn reaches the runtime as a private native payload."""
    chat = SimpleNamespace(meta={})
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image-bytes")
    captured: dict = {}
    db, db_model = _personal_acp_adapter_context(monkeypatch, chat)

    def fake_get_file_info(user_id: str, file_id: str):
        assert user_id == "user-1"
        assert file_id == "image-1"
        return {
            "path": str(image_path),
            "file_name": "stored-image",
            "file_type": "image/png",
            "file_category": "image",
            "file_size": image_path.stat().st_size,
            "meta": {"original_filename": "diagram.png"},
        }

    async def fake_run_acp_turn(**kwargs):
        captured.update(kwargs)
        kwargs["emit"]({"kind": "session", "session_id": "session-1"})
        kwargs["emit"](
            {
                "kind": "complete",
                "session_id": "session-1",
                "metadata": {"stop_reason": "end_turn"},
            }
        )

    monkeypatch.setattr(acp_utils, "get_file_info", fake_get_file_info)
    monkeypatch.setattr(acp_utils, "run_acp_turn", fake_run_acp_turn)
    monkeypatch.setattr(
        acp_utils, "_record_acp_generation_statistic", lambda *args, **kwargs: None
    )

    list(
        acp_utils.acp_chat(
            chat_id="chat-1",
            chat_history=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "user",
                            "content": "",
                            "images": ["image-1"],
                        }
                    ],
                }
            ],
            db=db,
            db_model=db_model,
            user_id="user-1",
            generation_id="generation-1",
            temp_request_flag=True,
        )
    )

    assert (
        captured["new_session_prompt"] == "Please work with the attached file or files."
    )
    assert captured["new_session_attachments"][0]["name"] == "diagram.png"
    assert (
        base64.b64decode(captured["new_session_attachments"][0]["data"])
        == b"image-bytes"
    )
    assert "path" not in captured["new_session_attachments"][0]


def test_acp_runtime_rejects_unknown_session_model(tmp_path: Path):
    """Crafted model values cannot bypass the options advertised by the agent."""
    with pytest.raises(ValueError, match="not available"):
        _run_fake_turn(tmp_path, selected_model_id="invented")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"selected_security_level": "invented"},
            "security level is not available",
        ),
        (
            {"selected_reasoning_effort": "invented"},
            "reasoning effort is not available",
        ),
    ],
)
def test_acp_runtime_rejects_unknown_session_controls(
    tmp_path: Path,
    kwargs: dict,
    message: str,
):
    """Crafted security and reasoning values cannot bypass advertised options."""
    with pytest.raises(ValueError, match=message):
        _run_fake_turn(tmp_path, **kwargs)


def test_workspace_paths_cannot_escape_admin_root(tmp_path: Path):
    """Relative model paths remain contained by the administrator-approved root."""
    child = tmp_path / "project"
    child.mkdir()

    assert resolve_workspace_path(str(tmp_path), "project") == child.resolve()
    with pytest.raises(ValueError, match="escapes"):
        resolve_workspace_path(str(tmp_path), "../")


def test_acp_settings_reject_absolute_model_paths(tmp_path: Path):
    """User-owned ACP model paths remain relative to the SSH workspace root."""
    model = AcpModelSettings()
    assert model.input_formats == ["text", "image", "audio", "video", "documents"]
    with pytest.raises(ValueError, match="relative"):
        AcpModelSettings(cwd=str(tmp_path))


def test_acp_initialize_protocol_version_must_match():
    """Every initialize path shares the same strict protocol-version check."""
    _validate_protocol_version(SimpleNamespace(protocol_version=1))
    with pytest.raises(RuntimeError, match="protocol version mismatch"):
        _validate_protocol_version(SimpleNamespace(protocol_version=999))


def test_permission_registry_enforces_owner_and_advertised_options():
    """A different user or invented option cannot resolve an ACP permission ticket."""
    registry = AcpPermissionRegistry(redis_client_factory=lambda: None)
    ticket = registry.create(
        user_id="owner",
        generation_id="generation",
        options=[
            {"optionId": "allow-once", "kind": "allow_once", "name": "Allow once"}
        ],
        timeout_seconds=30,
    )

    assert not registry.resolve(
        ticket.permission_id, user_id="other", option_id="allow-once"
    )
    assert not registry.resolve(
        ticket.permission_id, user_id="owner", option_id="invented"
    )
    assert registry.resolve(
        ticket.permission_id, user_id="owner", option_id="allow-once"
    )
    assert ticket.event.is_set()
    assert not registry.resolve(
        ticket.permission_id, user_id="owner", option_id="allow-once"
    )


def test_permission_registry_resolves_across_processes_with_redis():
    """A decision received by another API replica reaches the owning ACP worker."""
    fakeredis = pytest.importorskip("fakeredis")
    shared_redis = fakeredis.FakeRedis(decode_responses=True)
    owner_registry = AcpPermissionRegistry(redis_client_factory=lambda: shared_redis)
    api_registry = AcpPermissionRegistry(redis_client_factory=lambda: shared_redis)
    ticket = owner_registry.create(
        user_id="owner",
        generation_id="generation",
        options=[
            {"optionId": "allow-once", "kind": "allow_once", "name": "Allow once"}
        ],
        timeout_seconds=30,
    )

    assert not api_registry.resolve(
        ticket.permission_id, user_id="other", option_id="allow-once"
    )
    assert not api_registry.resolve(
        ticket.permission_id, user_id="owner", option_id="invented"
    )
    assert api_registry.resolve(
        ticket.permission_id, user_id="owner", option_id="allow-once"
    )
    assert not api_registry.resolve(
        ticket.permission_id, user_id="owner", option_id="allow-once"
    )
    assert asyncio.run(owner_registry.wait(ticket, timeout_seconds=1)) == "allow-once"


def test_permission_auto_allow_requires_exact_one_time_option():
    """Persistent allow grants are denied when one-time approval is unavailable."""
    client = OmlorixAcpClient(
        emit=lambda _event: None,
        user_id="owner",
        generation_id="generation",
        permission_mode="allow",
        permission_timeout_seconds=30,
    )

    denied = asyncio.run(
        client.request_permission(
            "session",
            {"title": "Persistent-only tool"},
            [{"optionId": "always", "kind": "allow_always", "name": "Always"}],
        )
    )
    allowed = asyncio.run(
        client.request_permission(
            "session",
            {"title": "One-time tool"},
            [
                {"optionId": "always", "kind": "allow_always", "name": "Always"},
                {"optionId": "once", "kind": "allow_once", "name": "Once"},
            ],
        )
    )

    assert denied.outcome.outcome == "cancelled"
    assert allowed.outcome.option_id == "once"
