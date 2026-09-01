import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools import helper as tool_helper
from app.tools.code_execution import utils as code_execution_utils
from app.tools.errors import ToolErrorTracker


def test_code_execution_prefers_the_chat_bound_service_connection():
    connections = [
        {"id": "random-first", "base_url": "http://random.local"},
        {"id": "bound", "base_url": "http://bound.local/"},
        {"id": "random-last", "base_url": "http://other.local"},
    ]

    ordered = code_execution_utils._prefer_bound_service_connection(
        connections,
        "http://bound.local",
    )

    assert [connection["id"] for connection in ordered] == [
        "bound",
        "random-first",
        "random-last",
    ]


class _FakeDb:
    def close(self):
        pass


class _DummyHttpClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.parametrize("legacy_feature", ["external_pip_packages", "pip_packages"])
def test_runtime_health_parses_legacy_pip_capabilities(legacy_feature):
    """Runtime probes must preserve the same legacy capability contract as refreshes."""
    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"features": {legacy_feature: True}}

    class _Client:
        @staticmethod
        def get(url, headers):
            assert url == "http://code.local/health"
            assert headers == {"Authorization": "Bearer secret"}
            return _Response()

    assert code_execution_utils._check_service_health(
        _Client(),
        "http://code.local",
        {"Authorization": "Bearer secret"},
    ) == {code_execution_utils.SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES: True}


def test_runtime_health_exposes_sandbox_timeout_as_transport_metadata():
    """The advertised watchdog sizes HTTP waiting but never a request field."""

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"execution_timeout_seconds": 45, "capabilities": {}}

    class _Client:
        @staticmethod
        def get(url, headers):
            assert url == "http://code.local/health"
            return _Response()

    health = code_execution_utils._check_service_health(_Client(), "http://code.local", {})

    assert health["execution_timeout_seconds"] == 45
    assert code_execution_utils._code_execution_transport_timeout_seconds(health) == 55


def test_tool_arguments_cannot_control_execution_timeout():
    request = code_execution_utils.normalize_code_execution_tool_args(
        {
            "type": "public",
            "language": "python",
            "code": "print('hello')",
            "timeout": 120000,
        }
    )

    assert "timeout" not in request


def test_create_container_does_not_send_network_policy():
    captured = {}

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"container_id": "container-1"}

    class _Client:
        @staticmethod
        def post(url, *, json, headers):
            captured.update(url=url, json=json, headers=headers)
            return _Response()

    container_id = code_execution_utils._create_container(
        client=_Client(),
        base_url="http://code.local",
        headers={"Authorization": "Bearer secret"},
    )

    assert container_id == "container-1"
    assert captured["json"] == {}
    assert "enable_network" not in captured["json"]


def test_create_container_classifies_only_explicit_session_capacity_429():
    """The gateway's session-limit response receives safe special handling."""

    class _Response:
        status_code = 429
        text = '{"detail":"You have reached the maximum number of active container sessions."}'

    class _Client:
        @staticmethod
        def post(_url, *, json, headers):
            return _Response()

    with pytest.raises(code_execution_utils._CodeExecutionSessionCapacityError):
        code_execution_utils._create_container(
            client=_Client(),
            base_url="http://code.local",
            headers={"Authorization": "Bearer secret"},
        )

    # A different 429, such as the gateway's short-window creation rate limit,
    # must keep the generic internal path and must not expose upstream details.
    _Response.text = '{"detail":"Container creation rate limit exceeded."}'
    with pytest.raises(RuntimeError) as exc_info:
        code_execution_utils._create_container(
            client=_Client(),
            base_url="http://code.local",
            headers={"Authorization": "Bearer secret"},
        )
    assert not isinstance(
        exc_info.value,
        code_execution_utils._CodeExecutionSessionCapacityError,
    )


def test_execute_code_exhausts_service_failover_before_safe_capacity_error(monkeypatch):
    """One full service must not prevent execution on another configured service."""

    attempted_base_urls = []
    status_updates = []
    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {"max_output_length": 50000},
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(code_execution_utils, "_prepare_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {"id": "full-1", "name": "Full 1", "base_url": "http://full-1.local"},
            {"id": "full-2", "name": "Full 2", "base_url": "http://full-2.local"},
        ],
    )
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_check_service_health", lambda **_kwargs: {})
    monkeypatch.setattr(code_execution_utils.httpx, "Client", _DummyHttpClient)
    monkeypatch.setattr(
        code_execution_utils,
        "record_service_connection_runtime_status",
        lambda *_args, **kwargs: status_updates.append(kwargs),
    )

    def reject_container_creation(**kwargs):
        attempted_base_urls.append(kwargs["base_url"])
        raise code_execution_utils._CodeExecutionSessionCapacityError("trusted 429")

    monkeypatch.setattr(code_execution_utils, "_ensure_container", reject_container_creation)

    with pytest.raises(code_execution_utils.CodeExecutionSessionCapacityError):
        code_execution_utils.execute_code("print('chart')", user_id="user-1")

    assert attempted_base_urls == ["http://full-1.local", "http://full-2.local"]
    # Both successful health probes can mark their connections available. A
    # request-scoped capacity response must not mark either service globally down.
    assert [update["available"] for update in status_updates] == [True, True]


def test_code_execution_capacity_reaches_model_as_safe_non_retryable_error(monkeypatch):
    """The wrapper must preserve capacity classification for all model adapters."""

    monkeypatch.setattr(
        code_execution_utils,
        "execute_code",
        lambda **_kwargs: (_ for _ in ()).throw(
            code_execution_utils.CodeExecutionSessionCapacityError(
                detail="trusted internal 429 detail",
            )
        ),
    )

    with pytest.raises(code_execution_utils.CodeExecutionSessionCapacityError) as exc_info:
        code_execution_utils.execute_code_tool_call(
            {"type": "public", "language": "python", "code": "print('chart')"},
            user_id="user-1",
            chat_id="chat-1",
        )

    response = ToolErrorTracker().record("code_execution", exc_info.value)

    assert response.error_code == "code_execution_session_capacity_unavailable"
    assert response.retry_allowed is False
    assert response.stop_tool_calls is True
    assert "no container session slot is available" in response.model_output
    assert "trusted internal 429 detail" not in response.model_output


def _drain_tool_generator(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def test_execute_code_returns_sandbox_runtime_error_as_result(monkeypatch):
    traceback = (
        'Traceback (most recent call last):\n'
        '  File "<user_code>", line 13, in <module>\n'
        "FileNotFoundError: No CSV file found in the working directory."
    )

    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {"max_output_length": 50000},
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(code_execution_utils, "_get_chat_bound_base_url", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(code_execution_utils, "_prepare_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [{"id": "svc-code", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_check_service_health", lambda **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "record_service_connection_runtime_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_ensure_container", lambda **_kwargs: "container-1")
    monkeypatch.setattr(code_execution_utils.httpx, "Client", _DummyHttpClient)
    monkeypatch.setattr(
        code_execution_utils,
        "_execute_request",
        lambda **_kwargs: {
            "execution_id": "exec-1",
            "stdout": "CSV files found: []\n",
            "stderr": traceback,
            "error": traceback,
            "error_type": "FileNotFoundError",
            "execution_time": 0.25,
            "timed_out": False,
            "files": [],
        },
    )

    payload = code_execution_utils.execute_code("raise FileNotFoundError()", user_id="user-1", chat_id="chat-1")
    result = payload["result"]

    assert result["execution_error"] is True
    assert result["execution_succeeded"] is False
    assert result["tool_transport_succeeded"] is True
    assert result["error_type"] == "FileNotFoundError"
    assert "No CSV file found" in result["error"]


def test_execute_code_429_does_not_record_shared_connection_down(monkeypatch):
    """A payload-specific 429 may fail over, but cannot mutate global health."""
    status_updates = []

    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {"max_output_length": 50000},
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(code_execution_utils, "_prepare_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [{"id": "svc-code", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_check_service_health", lambda **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_ensure_container", lambda **_kwargs: "container-1")
    monkeypatch.setattr(code_execution_utils.httpx, "Client", _DummyHttpClient)
    monkeypatch.setattr(
        code_execution_utils,
        "record_service_connection_runtime_status",
        lambda *_args, **kwargs: status_updates.append(kwargs),
    )

    def reject_execution(**_kwargs):
        raise RuntimeError("Code execution service returned status 429: saturated")

    monkeypatch.setattr(code_execution_utils, "_execute_request", reject_execution)

    with pytest.raises(RuntimeError, match="status 429"):
        code_execution_utils.execute_code("print('expensive')", user_id="user-1")

    # The successful health probe may mark the connection up. The request 429
    # must not produce a second, global available=False update.
    assert [update["available"] for update in status_updates] == [True]


def test_execute_code_with_pip_packages_skips_unsupported_services(monkeypatch):
    executed_base_urls = []

    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {"max_output_length": 50000},
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(code_execution_utils, "_prepare_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {"id": "unsupported", "name": "Unsupported", "base_url": "http://unsupported.local"},
            {"id": "capable", "name": "Capable", "base_url": "http://capable.local"},
        ],
    )
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        code_execution_utils,
        "_check_service_health",
        lambda **kwargs: {
            code_execution_utils.SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES:
                kwargs["base_url"] == "http://capable.local"
        },
    )
    monkeypatch.setattr(code_execution_utils, "record_service_connection_runtime_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_ensure_container", lambda **_kwargs: "container-1")
    monkeypatch.setattr(code_execution_utils.httpx, "Client", _DummyHttpClient)

    def execute_request(**kwargs):
        executed_base_urls.append(kwargs["base_url"])
        assert "timeout" not in kwargs["request_payload"]
        assert kwargs["request_payload"]["pip_packages"] == ["cowsay"]
        return {
            "execution_id": "exec-1",
            "stdout": "ok",
            "stderr": "",
            "error": None,
            "error_type": None,
            "execution_time": 0.1,
            "timed_out": False,
            "files": [],
        }

    monkeypatch.setattr(code_execution_utils, "_execute_request", execute_request)

    payload = code_execution_utils.execute_code(
        "import cowsay",
        user_id="user-1",
        pip_packages=[" cowsay "],
    )

    assert executed_base_urls == ["http://capable.local"]
    assert payload["result"]["service_connection"]["id"] == "capable"


def test_execute_code_with_pip_packages_fails_before_execution_when_unsupported(monkeypatch):
    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {"max_output_length": 50000},
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(code_execution_utils, "_prepare_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {"id": "legacy", "name": "Legacy", "base_url": "http://legacy.local"},
        ],
    )
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils, "_check_service_health", lambda **_kwargs: {})
    monkeypatch.setattr(code_execution_utils, "record_service_connection_runtime_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(code_execution_utils.httpx, "Client", _DummyHttpClient)
    monkeypatch.setattr(
        code_execution_utils,
        "_ensure_container",
        lambda **_kwargs: pytest.fail("unsupported service must not receive the execution"),
    )

    with pytest.raises(RuntimeError, match="supports external pip package installation"):
        code_execution_utils.execute_code(
            "import cowsay",
            user_id="user-1",
            pip_packages=["cowsay"],
        )


def test_execute_code_tool_call_surfaces_raw_runtime_error(monkeypatch):
    traceback = 'Traceback (most recent call last):\n  File "<user_code>", line 1\nZeroDivisionError: division by zero'
    monkeypatch.setattr(
        code_execution_utils,
        "execute_code",
        lambda **_kwargs: {
            "result": {
                "language": "python",
                "stdout": "",
                "stderr": traceback,
                "error": traceback,
                "error_type": "ZeroDivisionError",
                "execution_error": True,
                "execution_succeeded": False,
                "tool_transport_succeeded": True,
                "timed_out": False,
                "files_generated": 0,
                "input_files_loaded": 0,
            },
            "saved_files": [],
        },
    )

    payload = code_execution_utils.execute_code_tool_call(
        {"type": "public", "language": "python", "code": "1 / 0"},
        user_id="user-1",
        chat_id="chat-1",
    )

    assert "ZeroDivisionError" in payload["content"]
    assert "Traceback" in payload["content"]
    assert payload["tool_meta"]["code_execution"] is True
    assert payload["tool_meta"]["execution_error"] is True


def test_resolve_tool_call_does_not_treat_code_execution_error_payload_as_tool_failure(monkeypatch):
    traceback = 'Traceback (most recent call last):\nFileNotFoundError: No CSV file found in the working directory.'

    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        code_execution_utils,
        "execute_code_tool_call",
        lambda *_args, **_kwargs: {
            "tool_type": "public",
            "service_available": True,
            "exec_result": {
                "result": {
                    "language": "python",
                    "stdout": "CSV files found: []\n",
                    "stderr": traceback,
                    "error": traceback,
                    "error_type": "FileNotFoundError",
                    "execution_error": True,
                    "execution_succeeded": False,
                    "tool_transport_succeeded": True,
                    "timed_out": False,
                    "files_generated": 0,
                    "input_files_loaded": 0,
                    "output_files": [],
                },
                "saved_files": [],
            },
            "tool_meta": {"code_execution": True, "execution_error": True, "error_type": "FileNotFoundError"},
            "content": f"error (FileNotFoundError): {traceback}",
        },
    )

    result = _drain_tool_generator(
        tool_helper.resolve_tool_call(
            db=object(),
            tool_name="code_execution",
            tool_arguments={"type": "public", "language": "python", "code": "open('missing.csv')"},
            user_id="user-1",
            group_id=None,
            project_id=None,
        )
    )

    assert "No CSV file found" in result["content"]
    assert result["result"]["execution_error"] is True
    assert result["tool_meta"]["code_execution"] is True
