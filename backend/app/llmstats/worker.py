"""Background worker for pruning expired BYOK usage statistics."""
from __future__ import annotations

import logging
import os
import threading

from app.database import SessionLocal
from app.llmstats.models import purge_expired_byok_statistics
from app.utils.background import start_named_worker, stop_named_worker


logger = logging.getLogger(__name__)

BYOK_STATS_RETENTION_INTERVAL_SECONDS = max(
    3600,
    int(os.getenv("BYOK_STATS_RETENTION_INTERVAL_SECONDS", "86400") or "86400"),
)


def run_byok_stats_retention_once() -> dict[str, int]:
    db = SessionLocal()
    try:
        result = purge_expired_byok_statistics(db)
        deleted = int(result.get("llm_deleted", 0)) + int(result.get("tool_deleted", 0))
        if deleted:
            logger.info("[LLM Stats] Pruned %s expired BYOK usage statistic rows", deleted)
        return result
    except Exception:
        db.rollback()
        logger.exception("[LLM Stats] Failed to prune expired BYOK usage statistics")
        raise
    finally:
        db.close()


def _byok_stats_retention_worker(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            run_byok_stats_retention_once()
        except Exception:
            pass
        if stop_event.wait(BYOK_STATS_RETENTION_INTERVAL_SECONDS):
            break


def start_byok_stats_retention_worker():
    """Start background worker that periodically prunes expired BYOK stats."""
    return start_named_worker(
        "llmstats_byok_retention",
        _byok_stats_retention_worker,
        logger,
        start_message="[LLM Stats] BYOK retention worker started",
        already_running_message="[LLM Stats] BYOK retention worker already running",
        failure_message="[LLM Stats] Failed to start BYOK retention worker",
    )


def stop_byok_stats_retention_worker(timeout: float = 5.0) -> None:
    stop_named_worker(
        "llmstats_byok_retention",
        logger,
        timeout=timeout,
        stopped_message="[LLM Stats] BYOK retention worker stopped",
        not_running_message="[LLM Stats] BYOK retention worker was not running",
        failure_message="[LLM Stats] Failed to stop BYOK retention worker",
    )
