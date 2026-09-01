from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp import models


class _Db:
    """Minimal persistence stub that records the value present at commit time."""

    def __init__(self):
        self.server = None
        self.committed_timeouts: list[int] = []

    def add(self, server):
        self.server = server

    def commit(self):
        self.committed_timeouts.append(self.server.timeout_seconds)

    def refresh(self, server):
        self.server = server


def test_persistence_helpers_clamp_timeout_before_commit(monkeypatch):
    """Direct helper callers cannot send an oversized timeout to the database."""
    db = _Db()
    server = models.create_mcp_server(
        db,
        owner_type=models.OWNER_USER,
        owner_user_id="user-1",
        name="Remote",
        description=None,
        namespace=None,
        transport=models.TRANSPORT_STREAMABLE_HTTP,
        enabled=True,
        url="https://mcp.example.com/mcp",
        command=None,
        args=[],
        headers={},
        env={},
        allowed_tools=[],
        timeout_seconds=601,
    )

    monkeypatch.setattr(models, "get_mcp_server", lambda _db, _server_id: server)
    models.update_mcp_server(db, server.id, timeout_seconds=10_000)

    assert db.committed_timeouts == [600, 600]
