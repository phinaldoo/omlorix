import threading
import time

import anyio
import pytest

from app.utils.blocking_io import run_blocking_io


@pytest.mark.anyio
async def test_bulk_io_uses_its_configured_capacity_bound(monkeypatch):
    monkeypatch.setenv("ASGI_BULK_IO_MAX_WORKERS", "1")
    lock = threading.Lock()
    active = 0
    peak_active = 0

    def blocking_work():
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.02)
        finally:
            with lock:
                active -= 1

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_blocking_io, blocking_work)
        task_group.start_soon(run_blocking_io, blocking_work)

    assert peak_active == 1
