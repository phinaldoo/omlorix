import importlib.util
import sys
import types
from pathlib import Path


def _load_metrics_module(monkeypatch):
    opentelemetry_stub = types.ModuleType("opentelemetry")
    opentelemetry_stub.metrics = types.SimpleNamespace()
    config_stub = types.ModuleType("app.telemetry.config")
    config_stub.get_meter = lambda name: None

    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_stub)
    monkeypatch.setitem(sys.modules, "app.telemetry.config", config_stub)

    module_path = Path(__file__).resolve().parents[1] / "app" / "telemetry" / "metrics.py"
    spec = importlib.util.spec_from_file_location("business_metrics_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _ChatMetricSpy:
    def __init__(self):
        self.created = 0
        self.deleted = 0
        self.messages = []

    def record_chat_created(self, user_id=None):
        self.created += 1

    def record_chat_deleted(self, user_id=None):
        self.deleted += 1

    def record_message(self, role, length, user_id=None, chat_id=None, model=None):
        self.messages.append(
            {
                "role": role,
                "length": length,
                "model": model,
            }
        )


class _LLMMetricSpy:
    def __init__(self):
        self.requests = []

    def record_request(self, **kwargs):
        self.requests.append(kwargs)


class _AuthMetricSpy:
    def __init__(self):
        self.login_attempts = []
        self.logouts = 0
        self.ip_blocks = []

    def record_login_attempt(self, success, method="password", reason=None):
        self.login_attempts.append((success, method, reason))

    def record_logout(self):
        self.logouts += 1

    def record_ip_block(self, reason="unknown"):
        self.ip_blocks.append(reason)


class _SystemMetricSpy:
    def __init__(self):
        self.uploads = []
        self.background_tasks = []

    def record_file_upload(self, size_bytes, file_type="unknown"):
        self.uploads.append((size_bytes, file_type))

    def record_background_task(self, task_name, status, duration_ms=None):
        self.background_tasks.append((task_name, status, duration_ms))


def test_business_metric_helpers_forward_low_cardinality_values(monkeypatch):
    metrics = _load_metrics_module(monkeypatch)
    chat_spy = _ChatMetricSpy()
    llm_spy = _LLMMetricSpy()
    auth_spy = _AuthMetricSpy()
    system_spy = _SystemMetricSpy()

    monkeypatch.setattr(metrics, "get_chat_metrics", lambda: chat_spy)
    monkeypatch.setattr(metrics, "get_llm_metrics", lambda: llm_spy)
    monkeypatch.setattr(metrics, "get_auth_metrics", lambda: auth_spy)
    monkeypatch.setattr(metrics, "get_system_metrics", lambda: system_spy)

    metrics.record_chat_created_metric(user_id="user-1")
    metrics.record_chat_deleted_metric(user_id="user-1")
    metrics.record_chat_message_metric("user", "hello", model="model-1", chat_id="chat-1")
    metrics.record_llm_request_metric(
        provider="",
        model="",
        success=False,
        duration_ms=12.5,
        input_tokens=7,
        output_tokens=11,
        error_type="ProviderError",
    )
    metrics.record_auth_login_attempt_metric(False, method="ldap", reason="invalid_credentials")
    metrics.record_auth_logout_metric()
    metrics.record_auth_ip_block_metric("signin")
    metrics.record_file_upload_metric(123, "text/plain")
    metrics.record_background_task_metric("worker", "completed", duration_ms=45.0)

    assert chat_spy.created == 1
    assert chat_spy.deleted == 1
    assert chat_spy.messages == [{"role": "user", "length": 5, "model": "model-1"}]
    assert llm_spy.requests == [
        {
            "provider": "unknown",
            "model": "unknown",
            "success": False,
            "duration_ms": 12.5,
            "input_tokens": 7,
            "output_tokens": 11,
            "error_type": "ProviderError",
        }
    ]
    assert auth_spy.login_attempts == [(False, "ldap", "invalid_credentials")]
    assert auth_spy.logouts == 1
    assert auth_spy.ip_blocks == ["signin"]
    assert system_spy.uploads == [(123, "text/plain")]
    assert system_spy.background_tasks == [("worker", "completed", 45.0)]


def test_llm_request_metric_buckets_untrusted_labels(monkeypatch):
    metrics = _load_metrics_module(monkeypatch)
    llm_spy = _LLMMetricSpy()

    monkeypatch.setattr(metrics, "get_llm_metrics", lambda: llm_spy)

    metrics.record_llm_request_metric(
        provider="attacker-provider-0001",
        model="attacker-model-0001",
        success=False,
        duration_ms=1.0,
        input_tokens=1,
        output_tokens=1,
        error_type="ProviderError",
    )
    metrics.record_llm_request_metric(
        provider="byok",
        model="".join(["x"] * 100),
        success=False,
        duration_ms=1.0,
        input_tokens=1,
        output_tokens=1,
        error_type="ProviderError",
    )

    assert [request["provider"] for request in llm_spy.requests] == ["other", "byok"]
    assert [request["model"] for request in llm_spy.requests] == ["other", "byok"]


def test_business_metric_helpers_are_best_effort(monkeypatch):
    metrics = _load_metrics_module(monkeypatch)

    def fail():
        raise RuntimeError("exporter unavailable")

    monkeypatch.setattr(metrics, "get_chat_metrics", fail)

    metrics.record_chat_created_metric(user_id="user-1")
