"""Client implementation for the MCP Tasks extension.

The Python MCP SDK intentionally keeps behavioral extensions outside its core
package. Omlorix therefore implements the small Tasks client surface here using
the SDK's public ``ClientExtension`` and typed custom-request APIs. A task
returned from ``tools/call`` is polled until it reaches a terminal state, and
mid-flight input requests are dispatched through the same MRTR callbacks used
by ordinary MCP requests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import time
from typing import Any, Literal

from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.client.session import ClientRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolResult,
    ErrorData,
    InputRequest,
    InputResponse,
    Request,
    RequestParams,
    Result,
)
from pydantic import Field, TypeAdapter


MCP_TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
_MIN_POLL_INTERVAL_SECONDS = 0.1
_DEFAULT_TASK_WAIT_BUDGET_SECONDS = 30 * 60.0


class MCPTaskCancelledError(RuntimeError):
    """Raised when a remote server reports cooperative task cancellation."""


class TaskDescriptor(Result):
    """Common task state returned by ``tools/call`` and ``tasks/get``."""

    task_id: str = Field(alias="taskId", min_length=1)
    status: Literal["working", "input_required", "completed", "failed", "cancelled"]
    status_message: str | None = Field(alias="statusMessage", default=None)
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int | None = Field(alias="ttlMs", default=None, ge=0)
    poll_interval_ms: int | None = Field(alias="pollIntervalMs", default=None, gt=0)
    input_requests: dict[str, InputRequest] | None = Field(alias="inputRequests", default=None)
    result: dict[str, Any] | None = None
    error: ErrorData | None = None


class CreateTaskResult(Result):
    """Alternate ``tools/call`` result claimed by the Tasks extension."""

    task_id: str = Field(alias="taskId", min_length=1)
    status: Literal["working", "input_required", "completed", "failed", "cancelled"]
    status_message: str | None = Field(alias="statusMessage", default=None)
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int | None = Field(alias="ttlMs", default=None, ge=0)
    poll_interval_ms: int | None = Field(alias="pollIntervalMs", default=None, gt=0)
    result_type: Literal["task"] = Field(alias="resultType", default="task")


class GetTaskParams(RequestParams):
    """Parameters for retrieving a durable task state."""

    task_id: str = Field(alias="taskId", min_length=1)


class GetTaskRequest(Request[GetTaskParams, Literal["tasks/get"]]):
    """Typed custom request for the extension's polling method."""

    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskParams
    name_param = "taskId"


class UpdateTaskParams(RequestParams):
    """Parameters for satisfying task-scoped MRTR input requests."""

    task_id: str = Field(alias="taskId", min_length=1)
    input_responses: dict[str, InputResponse | ErrorData] = Field(alias="inputResponses")


class UpdateTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    """Typed custom request for submitting task input."""

    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams
    name_param = "taskId"


class CancelTaskParams(RequestParams):
    """Parameters for cooperative task cancellation."""

    task_id: str = Field(alias="taskId", min_length=1)


class CancelTaskRequest(Request[CancelTaskParams, Literal["tasks/cancel"]]):
    """Typed custom request for cancelling a task."""

    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: CancelTaskParams
    name_param = "taskId"


_TASK_RESULT_ADAPTER = TypeAdapter(TaskDescriptor)


async def _get_task(ctx: ClaimContext, task_id: str) -> TaskDescriptor:
    """Fetch and validate the current state for one remote task."""
    return await ctx.session.send_request(
        GetTaskRequest(params=GetTaskParams(task_id=task_id)),
        _TASK_RESULT_ADAPTER,
        request_read_timeout_seconds=ctx.read_timeout_seconds,
    )


async def _update_task(
    ctx: ClaimContext,
    task_id: str,
    responses: dict[str, InputResponse | ErrorData],
) -> None:
    """Submit responses for every input request currently blocking a task."""
    await ctx.session.send_request(
        UpdateTaskRequest(
            params=UpdateTaskParams(
                task_id=task_id,
                input_responses=responses,
            )
        ),
        Result,
        request_read_timeout_seconds=ctx.read_timeout_seconds,
    )


async def cancel_task(ctx: ClaimContext, task_id: str) -> None:
    """Request cooperative cancellation of a remote MCP task."""
    await ctx.session.send_request(
        CancelTaskRequest(params=CancelTaskParams(task_id=task_id)),
        Result,
        request_read_timeout_seconds=ctx.read_timeout_seconds,
    )


async def _dispatch_task_input_requests(
    ctx: ClaimContext,
    input_requests: dict[str, InputRequest],
) -> dict[str, InputResponse | ErrorData]:
    """Route task input through the SDK's configured MRTR callback table."""
    responses: dict[str, InputResponse | ErrorData] = {}
    for key, request in input_requests.items():
        request_context = ClientRequestContext(
            session=ctx.session,
            request_id=key,
            meta=request.params.meta if request.params is not None else None,
        )
        responses[key] = await ctx.session.dispatch_input_request(
            request_context,
            request,
        )
    return responses


def _poll_delay(task: TaskDescriptor) -> float:
    """Honor a server polling hint while retaining a small anti-spin floor."""
    try:
        seconds = float(task.poll_interval_ms or 1000) / 1000.0
    except (TypeError, ValueError):
        seconds = 1.0
    return max(_MIN_POLL_INTERVAL_SECONDS, seconds)


def _task_wait_budget_seconds(ttl_ms: int | None) -> float:
    """Convert a task TTL into a wait budget with a bounded default."""
    if ttl_ms is None:
        return _DEFAULT_TASK_WAIT_BUDGET_SECONDS
    return max(float(ttl_ms) / 1000.0, 0.0)


def _extend_task_deadline(
    task: TaskDescriptor,
    *,
    wait_started_at: float,
    largest_ttl_ms: int | None,
    deadline: float,
) -> tuple[int | None, float]:
    """Honor only explicit TTL growth without creating a sliding timeout."""
    if task.ttl_ms is None or (
        largest_ttl_ms is not None and task.ttl_ms <= largest_ttl_ms
    ):
        return largest_ttl_ms, deadline
    largest_ttl_ms = task.ttl_ms
    return largest_ttl_ms, max(
        deadline,
        wait_started_at + _task_wait_budget_seconds(largest_ttl_ms),
    )


def _completed_tool_result(task: TaskDescriptor) -> CallToolResult:
    """Validate the completed task payload as the original tool result."""
    if not isinstance(task.result, dict):
        raise ValueError(f"MCP task '{task.task_id}' completed without a tool result.")
    return CallToolResult.model_validate(task.result)


async def resolve_task(
    created: CreateTaskResult,
    ctx: ClaimContext,
) -> CallToolResult:
    """Drive a server-created task to completion for a normal Omlorix tool call."""
    task = TaskDescriptor.model_validate(
        created.model_dump(by_alias=True, exclude={"result_type"}, exclude_none=True)
    )
    answered_input_keys: set[str] = set()
    wait_started_at = time.monotonic()
    largest_ttl_ms = task.ttl_ms
    deadline = wait_started_at + _task_wait_budget_seconds(largest_ttl_ms)
    try:
        # ``CreateTaskResult`` is only the durable handle, even when the server
        # reports an immediately terminal status. Fetch the detailed task once
        # so its final result or error is always present.
        if task.status in _TERMINAL_TASK_STATES or task.status == "input_required":
            task = await _get_task(ctx, task.task_id)
            largest_ttl_ms, deadline = _extend_task_deadline(
                task,
                wait_started_at=wait_started_at,
                largest_ttl_ms=largest_ttl_ms,
                deadline=deadline,
            )
        while task.status not in _TERMINAL_TASK_STATES:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"MCP task '{task.task_id}' exceeded its advertised wait budget."
                )
            if task.status == "input_required":
                outstanding = task.input_requests or {}
                if not outstanding:
                    raise ValueError(
                        f"MCP task '{task.task_id}' requested input without providing any requests."
                    )
                unanswered = {
                    key: request
                    for key, request in outstanding.items()
                    if key not in answered_input_keys
                }
                if unanswered:
                    responses = await _dispatch_task_input_requests(ctx, unanswered)
                    await _update_task(ctx, task.task_id, responses)
                    answered_input_keys.update(responses)
                else:
                    # Updates are eventually consistent. If a poll repeats an
                    # input request we already answered, honor the server's
                    # polling interval rather than prompting or replying twice.
                    await asyncio.sleep(min(_poll_delay(task), max(deadline - now, 0.0)))
            else:
                await asyncio.sleep(min(_poll_delay(task), max(deadline - now, 0.0)))
            task = await _get_task(ctx, task.task_id)
            # TTL is measured from task creation. Extend only when the server
            # explicitly reports a larger lifetime; repeated polls with the
            # same TTL must never slide the deadline forever.
            largest_ttl_ms, deadline = _extend_task_deadline(
                task,
                wait_started_at=wait_started_at,
                largest_ttl_ms=largest_ttl_ms,
                deadline=deadline,
            )
    except asyncio.CancelledError:
        # Task cancellation is cooperative. Shield the cleanup request so the
        # outer Omlorix timeout does not abandon a task without notifying the
        # server, but never mask the original cancellation if cleanup fails.
        try:
            await asyncio.shield(cancel_task(ctx, task.task_id))
        except Exception:
            pass
        raise
    except TimeoutError:
        try:
            await cancel_task(ctx, task.task_id)
        except Exception:
            pass
        raise

    if task.status == "completed":
        return _completed_tool_result(task)
    if task.status == "failed":
        error = task.error or ErrorData(code=-32000, message="Remote MCP task failed.")
        raise MCPError(error.code, error.message, error.data)
    raise MCPTaskCancelledError(
        task.status_message or f"MCP task '{task.task_id}' was cancelled."
    )


class TasksClientExtension(ClientExtension):
    """Behavioral extension that claims and resolves server-created tasks."""

    identifier = MCP_TASKS_EXTENSION_ID

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return (
            ResultClaim(
                result_type="task",
                model=CreateTaskResult,
                resolve=resolve_task,
                protocol_versions=frozenset({"2026-07-28"}),
            ),
        )
