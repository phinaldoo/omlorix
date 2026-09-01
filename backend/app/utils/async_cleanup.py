from __future__ import annotations

import inspect
import logging
from typing import Any

import anyio


logger = logging.getLogger(__name__)


async def close_async_resource(
    resource: Any,
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    """Close a request-scoped resource even while its caller is cancelled."""

    if resource is None:
        return True
    close = getattr(resource, "aclose", None)
    if not callable(close):
        close = getattr(resource, "close", None)
    if not callable(close):
        return True

    completed = False
    with anyio.move_on_after(
        max(0.1, min(float(timeout_seconds), 30.0)),
        shield=True,
    ):
        result = close()
        if inspect.isawaitable(result):
            await result
        completed = True
    if not completed:
        logger.warning("Timed out while closing an async resource")
    return completed
