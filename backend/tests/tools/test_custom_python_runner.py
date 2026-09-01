from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.custom.utils import (  # noqa: E402
    BACKEND_DIR,
    CustomPythonToolExecutionError,
    execute_custom_python_tool_source,
    inspect_custom_python_tool_source,
)
from app.tools.custom import utils as custom_utils  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_enabled_custom_tool_audits_started_and_success_without_payloads(monkeypatch):
    events = []
    tool = SimpleNamespace(
        id="tool-1",
        name="safe_tool",
        source_code="source-must-not-be-audited",
        timeout_seconds=30,
    )
    monkeypatch.setattr(
        custom_utils,
        "get_custom_python_tool_by_name",
        lambda *_args, **_kwargs: tool,
    )
    monkeypatch.setattr(
        custom_utils,
        "_audit_custom_python_execution",
        lambda **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        custom_utils,
        "execute_custom_python_tool_source",
        lambda **_kwargs: {"content": "result-must-not-be-audited"},
    )

    output = custom_utils.execute_enabled_custom_python_tool(
        object(),
        tool_name="safe_tool",
        arguments={"secret": "argument-must-not-be-audited"},
        context={
            "user_id": "user-1",
            "chat_id": "chat-1",
            "generation_id": "generation-1",
            "model_settings": {"api_key": "key-must-not-be-audited"},
        },
    )

    assert output == {"content": "result-must-not-be-audited"}
    assert [event["action"] for event in events] == [
        "CUSTOM_PYTHON_TOOL_EXECUTION_STARTED",
        "CUSTOM_PYTHON_TOOL_EXECUTION_SUCCEEDED",
    ]
    assert events[0]["required"] is True
    assert events[1]["required"] is False
    assert events[0]["details"]["custom_tool_id"] == "tool-1"
    assert events[0]["details"]["invocation_id"] == events[1]["details"]["invocation_id"]
    assert "argument-must-not-be-audited" not in repr(events)
    assert "key-must-not-be-audited" not in repr(events)
    assert "source-must-not-be-audited" not in repr(events)
    assert "result-must-not-be-audited" not in repr(events)


def test_enabled_custom_tool_fails_closed_when_started_audit_is_unavailable(monkeypatch):
    tool = SimpleNamespace(
        id="tool-1",
        name="safe_tool",
        source_code="source",
        timeout_seconds=30,
    )
    executed = []
    monkeypatch.setattr(
        custom_utils,
        "get_custom_python_tool_by_name",
        lambda *_args, **_kwargs: tool,
    )

    def fail_started_audit(**kwargs):
        assert kwargs["required"] is True
        raise CustomPythonToolExecutionError("audit unavailable")

    monkeypatch.setattr(custom_utils, "_audit_custom_python_execution", fail_started_audit)
    monkeypatch.setattr(
        custom_utils,
        "execute_custom_python_tool_source",
        lambda **kwargs: executed.append(kwargs),
    )

    with pytest.raises(CustomPythonToolExecutionError, match="audit unavailable"):
        custom_utils.execute_enabled_custom_python_tool(
            object(),
            tool_name="safe_tool",
            context={"user_id": "user-1"},
        )

    assert executed == []


def test_custom_python_tools_run_with_integrated_runner():
    source = """
TOOL_DEFINITION = {
    "name": "security_probe",
    "description": "security probe",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    return {"content": [{"type": "text", "text": "ok"}]}
"""

    output = execute_custom_python_tool_source(source_code=source)

    assert output["result"][0]["text"] == "ok"


def test_custom_python_tools_can_import_backend_code_with_integrated_runner():
    source = """
from app.tools.custom.contract import serialize_content

TOOL_DEFINITION = {
    "name": "backend_import_probe",
    "description": "backend import probe",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    return {"content": serialize_content({"backend_import": True})}
"""

    output = execute_custom_python_tool_source(source_code=source)

    assert output["content"] == '{"backend_import":true}'


def test_custom_python_tools_normalize_script_widget_output():
    source = """
TOOL_DEFINITION = {
    "name": "study_widget",
    "description": "render a study widget",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    return {
        "content": "study widget",
        "widget": {
            "type": "study",
            "html": "<style>.card{font-weight:700}</style><div class='card'>Ready</div><script>document.body.dataset.ready='true';</script>",
            "model_context": {"status": "ready"},
        },
    }
"""

    output = execute_custom_python_tool_source(source_code=source)

    assert output["widget"]["type"] == "study"
    assert output["widget"]["render_mode"] == "iframe"
    assert output["widget"]["allow_scripts"] is True
    assert output["widget"]["model_context"] == {"status": "ready"}


def test_custom_python_tools_reject_script_widget_outside_iframe():
    source = """
TOOL_DEFINITION = {
    "name": "bad_widget",
    "description": "bad widget",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    return {
        "widget": {
            "type": "bad",
            "html": "<script>window.bad = true;</script>",
            "allow_scripts": True,
            "render_mode": "inline",
        },
    }
"""

    with pytest.raises(CustomPythonToolExecutionError, match="render_mode='iframe'"):
        execute_custom_python_tool_source(source_code=source)


def test_custom_python_tools_allow_import_time_file_access():
    source = """
TOOL_DEFINITION = {
    "name": "file_probe",
    "description": "file probe",
    "parameters": {"type": "object", "properties": {}},
}
LEAK = open("__AGENTS_PATH__").read()
""".replace("__AGENTS_PATH__", str(REPO_ROOT / "AGENTS.md"))

    definition = inspect_custom_python_tool_source(source)

    assert definition["name"] == "file_probe"


def test_custom_python_tools_allow_subprocess_access():
    source = """
TOOL_DEFINITION = {
    "name": "subprocess_probe",
    "description": "subprocess probe",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    import subprocess
    import sys
    text = subprocess.check_output([sys.executable, "-c", "print('child-ok')"], text=True)
    return {"content": text.strip()}
"""

    output = execute_custom_python_tool_source(source_code=source)

    assert output["content"] == "child-ok"


def test_custom_python_tools_allow_low_level_file_access():
    path = str(REPO_ROOT / "AGENTS.md")
    source = """
TOOL_DEFINITION = {
    "name": "low_level_file_probe",
    "description": "low level file probe",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}

def run_tool(arguments, context):
    import os
    fd = os.open(arguments["path"], os.O_RDONLY)
    try:
        return {"content": os.read(fd, 6).decode("utf-8", "ignore")}
    finally:
        os.close(fd)
"""

    output = execute_custom_python_tool_source(source_code=source, arguments={"path": path})

    assert output["content"] == "# AGEN"


def test_custom_python_tools_use_temporary_working_directory():
    source = """
from pathlib import Path

TOOL_DEFINITION = {
    "name": "working_directory_probe",
    "description": "working directory probe",
    "parameters": {"type": "object", "properties": {}},
}

def run_tool(arguments, context):
    Path("relative-output.txt").write_text("ok", encoding="utf-8")
    return {
        "content": {
            "cwd": str(Path.cwd()),
            "file_exists": Path("relative-output.txt").exists(),
        }
    }
"""

    output = execute_custom_python_tool_source(source_code=source)

    content = json.loads(output["content"])

    assert content["file_exists"] is True
    assert content["cwd"] != str(BACKEND_DIR)
    assert not (BACKEND_DIR / "relative-output.txt").exists()


def test_custom_python_tool_timeout_kills_spawned_child_process(tmp_path):
    marker_path = tmp_path / "child-survived.txt"
    source = f"""
TOOL_DEFINITION = {{
    "name": "timeout_child_probe",
    "description": "timeout child probe",
    "parameters": {{"type": "object", "properties": {{}}}},
}}

def run_tool(arguments, context):
    import subprocess
    import sys
    import time
    subprocess.Popen([
        sys.executable,
        "-c",
        "import pathlib, time; time.sleep(2); pathlib.Path({str(marker_path)!r}).write_text('alive')",
    ])
    time.sleep(10)
    return {{"content": "unexpected"}}
"""

    with pytest.raises(CustomPythonToolExecutionError, match="exceeded the timeout"):
        execute_custom_python_tool_source(source_code=source, timeout_seconds=1)

    time.sleep(2.5)
    assert not marker_path.exists()
