from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any, TypeVar

import anyio
from anyio.lowlevel import RunVar


_ResultT = TypeVar("_ResultT")


def _bulk_io_worker_limit() -> int:
    try:
        value = int(os.getenv("ASGI_BULK_IO_MAX_WORKERS", "8") or "8")
    except (TypeError, ValueError):
        value = 8
    return max(1, min(value, 32))


_BULK_IO_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar(
    "_omlorix_bulk_io_capacity_limiter"
)


def _bulk_io_limiter() -> anyio.CapacityLimiter:
    """Return a limiter owned by the current AnyIO event-loop run."""

    try:
        return _BULK_IO_LIMITER.get()
    except LookupError:
        # A separate limiter prevents large uploads, connector downloads,
        # encryption, and document writes from consuming Starlette's default
        # thread tokens. A RunVar avoids sharing backend-bound limiters between
        # asyncio and Trio (or between independent test/application runs).
        limiter = anyio.CapacityLimiter(_bulk_io_worker_limit())
        _BULK_IO_LIMITER.set(limiter)
        return limiter


async def run_blocking_io(
    func: Callable[..., _ResultT],
    *args: Any,
) -> _ResultT:
    """Run long blocking I/O under Omlorix's dedicated ASGI capacity bound."""

    return await anyio.to_thread.run_sync(
        func,
        *args,
        limiter=_bulk_io_limiter(),
    )
