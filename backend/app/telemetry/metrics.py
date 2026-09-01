"""
Custom metrics for Omlorix application monitoring.

Provides business-level metrics for chat operations, LLM usage, authentication, and system health.
"""

import logging
import time
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager

from opentelemetry import metrics

from app.telemetry.config import get_meter


logger = logging.getLogger(__name__)

_METRIC_PROVIDER_LABELS = frozenset({
    "anthropic",
    "anthropic_base",
    "byok",
    "elevenlabs",
    "google_aistudio",
    "lmstudio",
    "microsoft_azure",
    "ollama",
    "openai",
    "openai_chat_completions",
    "openai_responses",
    "openrouter",
    "xai",
    "unknown",
})
_METRIC_MAX_MODEL_LABEL_LENGTH = 80


def _low_cardinality_provider_label(provider: object) -> str:
    value = str(provider or "unknown").strip().lower()
    if value in _METRIC_PROVIDER_LABELS:
        return value
    return "other"


def _low_cardinality_model_label(model: object, provider_label: str) -> str:
    if provider_label in {"byok", "other"}:
        return provider_label

    value = str(model or "unknown").strip()
    if not value:
        return "unknown"
    if len(value) > _METRIC_MAX_MODEL_LABEL_LENGTH:
        return "other"
    return value


class ChatMetrics:
    """Metrics for chat and messaging operations."""
    
    _instance: Optional["ChatMetrics"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        meter = get_meter("omlorix.chats")
        
        # Counters
        self._messages_total = meter.create_counter(
            name="omlorix.messages.total",
            description="Total number of messages processed",
            unit="1",
        )
        
        self._chats_created = meter.create_counter(
            name="omlorix.chats.created",
            description="Number of chat sessions created",
            unit="1",
        )
        
        self._chats_deleted = meter.create_counter(
            name="omlorix.chats.deleted",
            description="Number of chat sessions deleted",
            unit="1",
        )
        
        # Histograms
        self._message_length = meter.create_histogram(
            name="omlorix.message.length",
            description="Length of messages in characters",
            unit="characters",
        )
        
        self._response_time = meter.create_histogram(
            name="omlorix.response.time",
            description="Time to generate a response",
            unit="ms",
        )
        
        # Up/Down Counter for active chats
        self._active_chats = meter.create_up_down_counter(
            name="omlorix.chats.active",
            description="Number of currently active chat sessions",
            unit="1",
        )
        
        self._initialized = True
    
    def record_message(
        self,
        role: str,
        length: int,
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Record a message being sent or received."""
        attributes = {"role": role}
        if model:
            attributes["model"] = model
        
        self._messages_total.add(1, attributes)
        self._message_length.record(length, attributes)
    
    def record_chat_created(self, user_id: Optional[str] = None):
        """Record a new chat session being created."""
        self._chats_created.add(1)
        self._active_chats.add(1)
    
    def record_chat_deleted(self, user_id: Optional[str] = None):
        """Record a chat session being deleted."""
        self._chats_deleted.add(1)
        self._active_chats.add(-1)
    
    def record_response_time(self, duration_ms: float, model: Optional[str] = None):
        """Record the time taken to generate a response."""
        attributes = {}
        if model:
            attributes["model"] = model
        self._response_time.record(duration_ms, attributes)
    
    @contextmanager
    def measure_response_time(self, model: Optional[str] = None):
        """Context manager to measure response generation time."""
        start = time.time()
        try:
            yield
        finally:
            duration_ms = (time.time() - start) * 1000
            self.record_response_time(duration_ms, model)


class LLMMetrics:
    """Metrics for LLM provider operations."""
    
    _instance: Optional["LLMMetrics"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        meter = get_meter("omlorix.llm")
        
        # Counters
        self._requests_total = meter.create_counter(
            name="omlorix.llm.requests.total",
            description="Total LLM API requests",
            unit="1",
        )
        
        self._tokens_input = meter.create_counter(
            name="omlorix.llm.tokens.input",
            description="Total input tokens sent to LLM",
            unit="tokens",
        )
        
        self._tokens_output = meter.create_counter(
            name="omlorix.llm.tokens.output",
            description="Total output tokens received from LLM",
            unit="tokens",
        )
        
        self._errors_total = meter.create_counter(
            name="omlorix.llm.errors.total",
            description="Total LLM API errors",
            unit="1",
        )
        
        # Histograms
        self._request_duration = meter.create_histogram(
            name="omlorix.llm.request.duration",
            description="LLM request duration",
            unit="ms",
        )
        
        self._time_to_first_token = meter.create_histogram(
            name="omlorix.llm.ttft",
            description="Time to first token in streaming responses",
            unit="ms",
        )
        
        self._initialized = True
    
    def record_request(
        self,
        provider: str,
        model: str,
        success: bool = True,
        duration_ms: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: Optional[str] = None,
    ):
        """Record an LLM API request."""
        attributes = {
            "provider": provider,
            "model": model,
            "success": str(success).lower(),
        }
        
        self._requests_total.add(1, attributes)
        
        if input_tokens > 0:
            self._tokens_input.add(input_tokens, {"provider": provider, "model": model})
        
        if output_tokens > 0:
            self._tokens_output.add(output_tokens, {"provider": provider, "model": model})
        
        if duration_ms is not None:
            self._request_duration.record(duration_ms, attributes)
        
        if not success:
            error_attrs = {"provider": provider, "model": model}
            if error_type:
                error_attrs["error_type"] = error_type
            self._errors_total.add(1, error_attrs)
    
    def record_ttft(self, duration_ms: float, provider: str, model: str):
        """Record time to first token for streaming responses."""
        self._time_to_first_token.record(
            duration_ms,
            {"provider": provider, "model": model}
        )
    
    @contextmanager
    def measure_request(self, provider: str, model: str):
        """Context manager to measure LLM request duration."""
        start = time.time()
        success = True
        error_type = None
        try:
            yield
        except Exception as e:
            success = False
            error_type = type(e).__name__
            raise
        finally:
            duration_ms = (time.time() - start) * 1000
            self.record_request(
                provider=provider,
                model=model,
                success=success,
                duration_ms=duration_ms,
                error_type=error_type,
            )


class AuthMetrics:
    """Metrics for authentication and authorization operations."""
    
    _instance: Optional["AuthMetrics"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        meter = get_meter("omlorix.auth")
        
        # Counters
        self._login_attempts = meter.create_counter(
            name="omlorix.auth.login.attempts",
            description="Total login attempts",
            unit="1",
        )
        
        self._login_success = meter.create_counter(
            name="omlorix.auth.login.success",
            description="Successful logins",
            unit="1",
        )
        
        self._login_failures = meter.create_counter(
            name="omlorix.auth.login.failures",
            description="Failed login attempts",
            unit="1",
        )
        
        self._token_validations = meter.create_counter(
            name="omlorix.auth.token.validations",
            description="Token validation attempts",
            unit="1",
        )
        
        self._ip_blocks = meter.create_counter(
            name="omlorix.auth.ip.blocks",
            description="Requests blocked by IP restriction",
            unit="1",
        )
        
        # Up/Down Counter for active sessions
        self._active_sessions = meter.create_up_down_counter(
            name="omlorix.auth.sessions.active",
            description="Currently active user sessions",
            unit="1",
        )
        
        self._initialized = True
    
    def record_login_attempt(self, success: bool, method: str = "password", reason: Optional[str] = None):
        """Record a login attempt."""
        self._login_attempts.add(1, {"method": method})
        
        if success:
            self._login_success.add(1, {"method": method})
            self._active_sessions.add(1)
        else:
            attrs = {"method": method}
            if reason:
                attrs["reason"] = reason
            self._login_failures.add(1, attrs)
    
    def record_logout(self):
        """Record a user logout."""
        self._active_sessions.add(-1)
    
    def record_token_validation(self, valid: bool):
        """Record a token validation attempt."""
        self._token_validations.add(1, {"valid": str(valid).lower()})
    
    def record_ip_block(self, reason: str = "unknown"):
        """Record an IP-based request block."""
        self._ip_blocks.add(1, {"reason": reason})


class SystemMetrics:
    """System-level metrics for application health."""
    
    _instance: Optional["SystemMetrics"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        meter = get_meter("omlorix.system")
        
        # Counters
        self._background_tasks_started = meter.create_counter(
            name="omlorix.background.tasks.started",
            description="Background tasks started",
            unit="1",
        )
        
        self._background_tasks_completed = meter.create_counter(
            name="omlorix.background.tasks.completed",
            description="Background tasks completed",
            unit="1",
        )
        
        self._background_tasks_failed = meter.create_counter(
            name="omlorix.background.tasks.failed",
            description="Background tasks failed",
            unit="1",
        )
        
        self._db_connections = meter.create_up_down_counter(
            name="omlorix.db.connections",
            description="Active database connections",
            unit="1",
        )
        
        # Histograms
        self._db_query_duration = meter.create_histogram(
            name="omlorix.db.query.duration",
            description="Database query duration",
            unit="ms",
        )
        
        self._file_upload_size = meter.create_histogram(
            name="omlorix.files.upload.size",
            description="Size of uploaded files",
            unit="bytes",
        )
        
        self._initialized = True
    
    def record_background_task(self, task_name: str, status: str, duration_ms: Optional[float] = None):
        """Record a background task execution."""
        attrs = {"task_name": task_name}
        
        if status == "started":
            self._background_tasks_started.add(1, attrs)
        elif status == "completed":
            self._background_tasks_completed.add(1, attrs)
        elif status == "failed":
            self._background_tasks_failed.add(1, attrs)
    
    def record_db_connection(self, delta: int):
        """Record database connection change."""
        self._db_connections.add(delta)
    
    def record_db_query(self, duration_ms: float, operation: str = "query"):
        """Record database query duration."""
        self._db_query_duration.record(duration_ms, {"operation": operation})
    
    def record_file_upload(self, size_bytes: int, file_type: str = "unknown"):
        """Record a file upload."""
        self._file_upload_size.record(size_bytes, {"file_type": file_type})
    
    @contextmanager
    def measure_db_query(self, operation: str = "query"):
        """Context manager to measure database query duration."""
        start = time.time()
        try:
            yield
        finally:
            duration_ms = (time.time() - start) * 1000
            self.record_db_query(duration_ms, operation)


# Singleton instances for easy access
def get_chat_metrics() -> ChatMetrics:
    """Get the ChatMetrics singleton instance."""
    return ChatMetrics()


def get_llm_metrics() -> LLMMetrics:
    """Get the LLMMetrics singleton instance."""
    return LLMMetrics()


def get_auth_metrics() -> AuthMetrics:
    """Get the AuthMetrics singleton instance."""
    return AuthMetrics()


def get_system_metrics() -> SystemMetrics:
    """Get the SystemMetrics singleton instance."""
    return SystemMetrics()


def _safe_record(metric_name: str, callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        logger.debug("Failed to record %s metric", metric_name, exc_info=True)


def record_chat_created_metric(user_id: Optional[str] = None) -> None:
    """Record chat creation without letting telemetry affect application flow."""
    _safe_record("chat_created", lambda: get_chat_metrics().record_chat_created(user_id=user_id))


def record_chat_deleted_metric(user_id: Optional[str] = None) -> None:
    """Record chat deletion without letting telemetry affect application flow."""
    _safe_record("chat_deleted", lambda: get_chat_metrics().record_chat_deleted(user_id=user_id))


def record_chat_message_metric(
    role: str,
    content: Any,
    *,
    model: Optional[str] = None,
    user_id: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> None:
    """Record a persisted chat message using only low-cardinality attributes."""
    if content is None:
        length = 0
    elif isinstance(content, str):
        length = len(content)
    else:
        length = len(str(content))
    _safe_record(
        "chat_message",
        lambda: get_chat_metrics().record_message(
            role=role,
            length=length,
            user_id=user_id,
            chat_id=chat_id,
            model=model,
        ),
    )


def record_llm_request_metric(
    *,
    provider: str,
    model: str,
    success: bool,
    duration_ms: Optional[float] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error_type: Optional[str] = None,
) -> None:
    """Record an LLM request without exposing prompts or responses."""
    provider_name = _low_cardinality_provider_label(provider)
    model_name = _low_cardinality_model_label(model, provider_name)
    _safe_record(
        "llm_request",
        lambda: get_llm_metrics().record_request(
            provider=provider_name,
            model=model_name,
            success=success,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_type=error_type,
        ),
    )


def record_auth_login_attempt_metric(success: bool, method: str = "password", reason: Optional[str] = None) -> None:
    """Record a login attempt without letting telemetry affect authentication."""
    _safe_record(
        "auth_login_attempt",
        lambda: get_auth_metrics().record_login_attempt(
            success=success,
            method=str(method or "unknown"),
            reason=reason,
        ),
    )


def record_auth_logout_metric() -> None:
    """Record a logout without letting telemetry affect authentication."""
    _safe_record("auth_logout", lambda: get_auth_metrics().record_logout())


def record_auth_ip_block_metric(reason: str = "unknown") -> None:
    """Record an IP block without letting telemetry affect authentication."""
    _safe_record("auth_ip_block", lambda: get_auth_metrics().record_ip_block(reason=str(reason or "unknown")))


def record_file_upload_metric(size_bytes: int, file_type: str = "unknown") -> None:
    """Record a successful file upload without exposing filenames."""
    _safe_record(
        "file_upload",
        lambda: get_system_metrics().record_file_upload(
            size_bytes=max(0, int(size_bytes or 0)),
            file_type=str(file_type or "unknown"),
        ),
    )


def record_background_task_metric(task_name: str, status: str, duration_ms: Optional[float] = None) -> None:
    """Record a background task lifecycle event."""
    _safe_record(
        "background_task",
        lambda: get_system_metrics().record_background_task(
            task_name=str(task_name or "unknown"),
            status=str(status or "unknown"),
            duration_ms=duration_ms,
        ),
    )
