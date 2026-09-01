"""
Custom span utilities for detailed tracing.

Provides decorators and context managers for creating spans with semantic conventions.
"""

import functools
import logging
from typing import Any, Callable, Dict, Optional, TypeVar, Union
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, Span, SpanKind

from app.telemetry.config import get_tracer


logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def add_span_attributes(attributes: Dict[str, Any], span: Optional[Span] = None):
    """
    Add attributes to the current or specified span.
    
    Args:
        attributes: Dictionary of attributes to add
        span: Optional span to add attributes to (uses current span if not provided)
    """
    target_span = span or trace.get_current_span()
    if target_span and target_span.is_recording():
        for key, value in attributes.items():
            if value is not None:
                # Convert non-primitive types to string
                if not isinstance(value, (str, int, float, bool)):
                    value = str(value)
                target_span.set_attribute(key, value)


def record_exception(
    exception: Exception,
    span: Optional[Span] = None,
    escaped: bool = True,
    attributes: Optional[Dict[str, Any]] = None,
):
    """
    Record an exception on the current or specified span.
    
    Args:
        exception: The exception to record
        span: Optional span (uses current span if not provided)
        escaped: Whether the exception escaped the span's scope
        attributes: Additional attributes to add to the exception event
    """
    target_span = span or trace.get_current_span()
    if target_span and target_span.is_recording():
        target_span.record_exception(exception, escaped=escaped, attributes=attributes)
        target_span.set_status(Status(StatusCode.ERROR, str(exception)))


@contextmanager
def trace_llm_request(
    provider: str,
    model: str,
    operation: str = "completion",
    stream: bool = False,
    user_id: Optional[str] = None,
    chat_id: Optional[str] = None,
):
    """
    Context manager for tracing LLM API requests.
    
    Args:
        provider: LLM provider name (e.g., "openai", "anthropic", "ollama")
        model: Model name being used
        operation: Type of operation (completion, chat, embedding)
        stream: Whether this is a streaming request
        user_id: Optional user ID for attribution
        chat_id: Optional chat ID for context
        
    Yields:
        The created span for adding additional attributes
    """
    tracer = get_tracer("omlorix.llm")
    
    with tracer.start_as_current_span(
        f"llm.{operation}",
        kind=SpanKind.CLIENT,
    ) as span:
        # Set semantic attributes
        span.set_attribute("llm.provider", provider)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.operation", operation)
        span.set_attribute("llm.stream", stream)
        
        if user_id:
            span.set_attribute("user.id", user_id)
        if chat_id:
            span.set_attribute("chat.id", chat_id)
        
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", type(e).__name__)
            raise


@contextmanager
def trace_db_operation(
    operation: str,
    table: Optional[str] = None,
    statement_type: str = "query",
):
    """
    Context manager for tracing database operations.
    
    Args:
        operation: Description of the operation
        table: Table name being operated on
        statement_type: Type of SQL statement (query, insert, update, delete)
        
    Yields:
        The created span
    """
    tracer = get_tracer("omlorix.database")
    
    span_name = f"db.{statement_type}"
    if table:
        span_name = f"db.{statement_type}.{table}"
    
    with tracer.start_as_current_span(
        span_name,
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("db.operation", operation)
        span.set_attribute("db.statement_type", statement_type)
        if table:
            span.set_attribute("db.table", table)
        
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


@contextmanager
def trace_external_api(
    service: str,
    operation: str,
    url: Optional[str] = None,
    method: str = "GET",
):
    """
    Context manager for tracing external API calls.
    
    Args:
        service: Name of the external service
        operation: Description of the operation
        url: URL being called (sensitive parts should be removed)
        method: HTTP method
        
    Yields:
        The created span
    """
    tracer = get_tracer("omlorix.external")
    
    with tracer.start_as_current_span(
        f"external.{service}.{operation}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("external.service", service)
        span.set_attribute("external.operation", operation)
        span.set_attribute("http.method", method)
        if url:
            # Sanitize URL to remove sensitive data
            span.set_attribute("http.url", url.split("?")[0])
        
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


@contextmanager
def trace_background_task(
    task_name: str,
    task_type: str = "worker",
):
    """
    Context manager for tracing background tasks.
    
    Args:
        task_name: Name of the background task
        task_type: Type of task (worker, scheduled, cleanup)
        
    Yields:
        The created span
    """
    tracer = get_tracer("omlorix.background")
    
    with tracer.start_as_current_span(
        f"task.{task_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("task.name", task_name)
        span.set_attribute("task.type", task_type)
        
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def trace_function(
    name: Optional[str] = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
):
    """
    Decorator for tracing function execution.
    
    Args:
        name: Optional span name (defaults to function name)
        kind: Span kind
        attributes: Static attributes to add to the span
        
    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        span_name = name or f"{func.__module__}.{func.__qualname__}"
        tracer = get_tracer(func.__module__)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name, kind=kind) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name, kind=kind) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore
    
    return decorator


def trace_endpoint(
    operation: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
):
    """
    Decorator for tracing API endpoints with additional context.
    
    Args:
        operation: Optional operation name (defaults to function name)
        attributes: Additional attributes to add
        
    Returns:
        Decorated function
    """
    return trace_function(
        name=operation,
        kind=SpanKind.SERVER,
        attributes=attributes,
    )


class SpanContext:
    """
    Helper class for managing span context in complex operations.
    
    Usage:
        ctx = SpanContext("my_operation")
        ctx.add_attribute("key", "value")
        with ctx:
            # do work
            ctx.add_event("checkpoint", {"progress": 50})
    """
    
    def __init__(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        tracer_name: str = "omlorix",
    ):
        self.name = name
        self.kind = kind
        self.tracer = get_tracer(tracer_name)
        self._span: Optional[Span] = None
        self._attributes: Dict[str, Any] = {}
    
    def add_attribute(self, key: str, value: Any):
        """Add an attribute to be set when the span starts."""
        self._attributes[key] = value
        if self._span and self._span.is_recording():
            self._span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
    
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """Add an event to the current span."""
        if self._span and self._span.is_recording():
            self._span.add_event(name, attributes=attributes)
    
    def set_error(self, exception: Exception):
        """Mark the span as error and record the exception."""
        if self._span and self._span.is_recording():
            self._span.record_exception(exception)
            self._span.set_status(Status(StatusCode.ERROR, str(exception)))
    
    def __enter__(self) -> "SpanContext":
        self._span = self.tracer.start_span(self.name, kind=self.kind)
        self._span.__enter__()
        
        for key, value in self._attributes.items():
            self._span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            if exc_val:
                self._span.record_exception(exc_val)
                self._span.set_status(Status(StatusCode.ERROR, str(exc_val)))
            else:
                self._span.set_status(Status(StatusCode.OK))
            self._span.__exit__(exc_type, exc_val, exc_tb)
        return False
    
    @property
    def span(self) -> Optional[Span]:
        """Get the underlying span object."""
        return self._span
