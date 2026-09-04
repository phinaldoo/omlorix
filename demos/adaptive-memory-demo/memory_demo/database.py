from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

DEMO_USER_ID = "demo-user"


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
                    ON conversations(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS messages_conversation_created_idx
                    ON messages(conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    stability TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_confirmed_at TEXT NOT NULL,
                    review_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    source_message_id TEXT,
                    source_excerpt TEXT NOT NULL,
                    UNIQUE(user_id, memory_key)
                );

                CREATE INDEX IF NOT EXISTS memories_user_status_idx
                    ON memories(user_id, status, expires_at);

                CREATE TABLE IF NOT EXISTS memory_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(memory_id, version)
                );

                CREATE TABLE IF NOT EXISTS profile_snapshots (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    derived_from_memory_ids TEXT NOT NULL,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, version)
                );

                CREATE INDEX IF NOT EXISTS profiles_user_version_idx
                    ON profile_snapshots(user_id, version DESC);

                CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    memory_id TEXT,
                    action TEXT NOT NULL,
                    kind TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS events_user_created_idx
                    ON memory_events(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    source_message_id TEXT,
                    operation TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS usage_user_created_idx
                    ON usage_events(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS demo_state (
                    user_id TEXT PRIMARY KEY,
                    clock_offset_days INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO demo_state(user_id, clock_offset_days) VALUES (?, 0)",
                (DEMO_USER_ID,),
            )

    def clock_offset_days(self) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT clock_offset_days FROM demo_state WHERE user_id = ?",
                (DEMO_USER_ID,),
            ).fetchone()
        return int(row["clock_offset_days"]) if row else 0

    def set_clock_offset_days(self, days: int) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO demo_state(user_id, clock_offset_days) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET clock_offset_days = excluded.clock_offset_days
                """,
                (DEMO_USER_ID, days),
            )

    def ensure_conversation(self, conversation_id: str | None = None) -> str:
        now = iso()
        with self.connection() as connection:
            if conversation_id:
                row = connection.execute(
                    "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                    (conversation_id, DEMO_USER_ID),
                ).fetchone()
                if row:
                    return str(row["id"])
            row = connection.execute(
                "SELECT id FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (DEMO_USER_ID,),
            ).fetchone()
            if row:
                return str(row["id"])
            new_id = f"conv_{uuid4().hex}"
            connection.execute(
                "INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (new_id, DEMO_USER_ID, "Memory Lab", now, now),
            )
            return new_id

    def create_conversation(self) -> str:
        conversation_id = f"conv_{uuid4().hex}"
        now = iso()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, DEMO_USER_ID, "Memory Lab", now, now),
            )
        return conversation_id

    def conversation_exists(self, conversation_id: str) -> bool:
        with self.connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                    (conversation_id, DEMO_USER_ID),
                ).fetchone()
                is not None
            )

    def append_message(
        self, conversation_id: str, role: str, content: str, *, created_at: datetime | None = None
    ) -> str:
        message_id = f"msg_{uuid4().hex}"
        timestamp = iso(created_at)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO messages(id, conversation_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, DEMO_USER_ID, role, content, timestamp),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                (timestamp, conversation_id, DEMO_USER_ID),
            )
        return message_id

    def list_messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, created_at
                FROM (
                    SELECT id, role, content, created_at
                    FROM messages
                    WHERE conversation_id = ? AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC
                """,
                (conversation_id, DEMO_USER_ID, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_memories(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND status = 'active'
                ORDER BY importance DESC, updated_at DESC
                """,
                (DEMO_USER_ID,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_profile(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT content, version, created_at
                FROM profile_snapshots
                WHERE user_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (DEMO_USER_ID,),
            ).fetchone()
        return dict(row) if row else None

    def list_events(self, limit: int = 40) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, action, kind, created_at
                FROM memory_events
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (DEMO_USER_ID, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_usage(
        self,
        *,
        source_message_id: str | None,
        operation: str,
        model: str,
        input_tokens: int,
        cached_input_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost_usd: float,
        created_at: datetime | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO usage_events(
                    user_id, source_message_id, operation, model, input_tokens,
                    cached_input_tokens, cache_write_tokens, output_tokens,
                    total_tokens, estimated_cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEMO_USER_ID,
                    source_message_id,
                    operation,
                    model,
                    input_tokens,
                    cached_input_tokens,
                    cache_write_tokens,
                    output_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    iso(created_at),
                ),
            )

    def usage_metrics(self) -> dict[str, Any]:
        with self.connection() as connection:
            totals = connection.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
                FROM usage_events WHERE user_id = ?
                """,
                (DEMO_USER_ID,),
            ).fetchone()
            last_source = connection.execute(
                """
                SELECT source_message_id FROM usage_events
                WHERE user_id = ? AND source_message_id IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (DEMO_USER_ID,),
            ).fetchone()
            last_cost = 0.0
            if last_source:
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(estimated_cost_usd), 0) AS cost
                    FROM usage_events WHERE user_id = ? AND source_message_id = ?
                    """,
                    (DEMO_USER_ID, last_source["source_message_id"]),
                ).fetchone()
                last_cost = float(row["cost"])
        return {
            "total_tokens": int(totals["total_tokens"]),
            "estimated_cost_usd": float(totals["total_cost"]),
            "last_turn_cost_usd": last_cost,
        }

    def add_event(
        self,
        action: str,
        *,
        memory_id: str | None = None,
        kind: str | None = None,
        created_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        params = (DEMO_USER_ID, memory_id, action, kind, iso(created_at))
        if connection is not None:
            connection.execute(
                "INSERT INTO memory_events(user_id, memory_id, action, kind, created_at) VALUES (?, ?, ?, ?, ?)",
                params,
            )
            return
        with self.connection() as owned_connection:
            owned_connection.execute(
                "INSERT INTO memory_events(user_id, memory_id, action, kind, created_at) VALUES (?, ?, ?, ?, ?)",
                params,
            )

    def reset_demo(self) -> str:
        with self.connection() as connection:
            connection.execute("DELETE FROM conversations WHERE user_id = ?", (DEMO_USER_ID,))
            connection.execute("DELETE FROM memories WHERE user_id = ?", (DEMO_USER_ID,))
            connection.execute("DELETE FROM profile_snapshots WHERE user_id = ?", (DEMO_USER_ID,))
            connection.execute("DELETE FROM memory_events WHERE user_id = ?", (DEMO_USER_ID,))
            connection.execute("DELETE FROM usage_events WHERE user_id = ?", (DEMO_USER_ID,))
            connection.execute(
                "UPDATE demo_state SET clock_offset_days = 0 WHERE user_id = ?", (DEMO_USER_ID,)
            )
        return self.ensure_conversation()

    def export_bundle(self, conversation_id: str) -> dict[str, Any]:
        profile = self.latest_profile()
        memories = self.list_memories()
        with self.connection() as connection:
            memory_versions = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, memory_id, version, action, content, source_message_id, created_at
                    FROM memory_versions
                    WHERE user_id = ?
                    ORDER BY id ASC
                    """,
                    (DEMO_USER_ID,),
                ).fetchall()
            ]
            profile_snapshots = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, version, content, derived_from_memory_ids,
                           source_message_id, created_at
                    FROM profile_snapshots
                    WHERE user_id = ?
                    ORDER BY version ASC
                    """,
                    (DEMO_USER_ID,),
                ).fetchall()
            ]
            usage = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT operation, model, input_tokens, cached_input_tokens,
                           cache_write_tokens, output_tokens, total_tokens,
                           estimated_cost_usd, source_message_id, created_at
                    FROM usage_events
                    WHERE user_id = ?
                    ORDER BY id ASC
                    """,
                    (DEMO_USER_ID,),
                ).fetchall()
            ]
        return {
            "format": "omlorix-adaptive-memory-demo",
            "version": 1,
            "exported_at": iso(),
            "user_id": DEMO_USER_ID,
            "conversation_id": conversation_id,
            "clock_offset_days": self.clock_offset_days(),
            "messages": self.list_messages(conversation_id, limit=10_000),
            "profile": profile,
            "profile_snapshots": profile_snapshots,
            "memories": memories,
            "memory_versions": memory_versions,
            "events": self.list_events(limit=10_000),
            "usage": usage,
        }
