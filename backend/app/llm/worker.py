import logging
import threading
from contextlib import suppress
from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException

from app.database import AuditSessionLocal, SessionLocal
from app.llm.models import (
    LLMProvider,
    apply_disabled_sync_status,
    delete_model_by_name,
    list_llm_provider,
    provider_regular_requests_disabled,
)
from app.llm.utils import list_provider_status_models
from app.llmstats.models import refresh_model_tokens_per_second_cache
from app.network.policy import OutboundRequestBlockedError, assert_llm_provider_allowed
from app.logging.models import create_admin_notification
from app.utils.background import start_named_worker, stop_named_worker


logger = logging.getLogger(__name__)


_WORKER_NAME = "llm_provider_monitor"
_DEFAULT_INTERVAL_SECONDS = 60 * 10
_MAX_NOTIFICATION_MODEL_LIST_CHARS = 10000


def _to_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    for attr in ("model_dump", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            with suppress(Exception):
                data = method()
                if isinstance(data, dict):
                    return data
    return getattr(value, "__dict__", {}) if value else {}


def _model_id(entry) -> str | None:
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        iterable = (entry.get(k) for k in ("id", "model", "name"))
    else:
        iterable = (getattr(entry, k, None) for k in ("id", "model", "name"))
    for candidate in iterable:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _model_ids(models: Iterable) -> set[str]:
    return {
        model_id
        for model_id in (_model_id(item) for item in models)
        if model_id
    }


def _notification_model_list(ids: list[str], *, max_chars: int = _MAX_NOTIFICATION_MODEL_LIST_CHARS) -> str:
    if not ids:
        return ""
    if max_chars <= 0:
        return ""

    selected: list[str] = []
    current_length = 0
    for model_id in ids:
        addition_length = len(model_id) if not selected else len(model_id) + 2
        if current_length + addition_length > max_chars:
            break
        selected.append(model_id)
        current_length += addition_length

    if selected:
        return ", ".join(selected)
    return ids[0][:max_chars]


def _notify(provider: LLMProvider, event_type: str, models: Iterable[str]) -> None:
    ids = [m for m in models if m]
    if not ids:
        return
    provider_label = provider.name or provider.id or "provider"
    preview = _notification_model_list(ids)
    event_type_lower = event_type.lower()
    if "error" in event_type_lower:
        notif_type = "error"
    elif "removed" in event_type_lower or "auto_deleted" in event_type_lower:
        notif_type = "warning"
    else:
        notif_type = "info"
    try:
        with SessionLocal() as session:
            create_admin_notification(
                session,
                event_type,
                f"[{provider_label}] {preview}",
                details={
                    "provider": provider.provider,
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "event_type": event_type,
                    "models": ids,
                },
                notification_type=notif_type,
            )
    except Exception:
        logger.exception(
            "[LLM Provider Worker] Failed to record admin notification for provider %s", provider.id
        )


def _list_models_with_retry(db, provider: LLMProvider) -> set[str] | None:
    """Fetch provider models with a second attempt on failure."""
    attempt = 1
    while attempt <= 2:
        try:
            return _model_ids(list_provider_status_models(db, provider.id))
        except HTTPException as exc:
            if attempt == 2:
                logger.warning(
                    "[LLM Provider Worker] Provider %s (%s) failed to list models after retry: %s",
                    provider.id,
                    provider.provider,
                    exc.detail if hasattr(exc, "detail") else exc,
                )
                return None
            logger.info(
                "[LLM Provider Worker] Provider %s (%s) model listing failed (attempt %s); retrying once: %s",
                provider.id,
                provider.provider,
                attempt,
                exc.detail if hasattr(exc, "detail") else exc,
            )
        except Exception as exc:
            if attempt == 2:
                logger.exception(
                    "[LLM Provider Worker] Unexpected error while listing models for provider %s on retry",
                    provider.id,
                )
                return None
            logger.exception(
                "[LLM Provider Worker] Unexpected error while listing models for provider %s (attempt %s); retrying once",
                provider.id,
                attempt,
            )
        attempt += 1
    return None


def _sync_provider(db, provider: LLMProvider) -> None:
    settings = _to_dict(provider.settings)
    if provider_regular_requests_disabled(provider):
        apply_disabled_sync_status(db, provider)
        logger.debug(
            "[LLM Provider Worker] Skipping provider %s because background sync is disabled.",
            provider.id,
        )
        return

    try:
        assert_llm_provider_allowed(db, provider, feature="LLM provider background sync")
    except OutboundRequestBlockedError as exc:
        latest_status = _to_dict(provider.status)
        updated_status = {
            **latest_status,
            "available": "unknown",
            "policy_blocked": True,
            "last_error": str(exc),
        }
        if updated_status != latest_status:
            db.query(LLMProvider).filter(LLMProvider.id == provider.id).update(
                {"status": updated_status}, synchronize_session=False
            )
            db.commit()
        return

    status_snapshot = _to_dict(provider.status)
    notify = bool(settings.get("enable_notify_model_changes", True))
    auto_delete = bool(settings.get("enable_auto_delete_missing_models", False))

    previous = {
        str(item).strip()
        for item in status_snapshot.get("model_list", [])
        if isinstance(item, str) and item.strip()
    }

    current = _list_models_with_retry(db, provider)
    if current is None:
        return

    removed = sorted(previous - current)
    added = sorted(current - previous)
    if removed or added:
        logger.info(
            "[LLM Provider Worker] Provider %s models synced. Added: %s, Removed: %s",
            provider.id,
            added,
            removed,
        )

    last_synced_at = status_snapshot.get("last_synced_at")
    is_first_sync = not last_synced_at

    if notify and not is_first_sync:
        if added:
            _notify(provider, "llm_model_added", added)
        if removed:
            _notify(provider, "llm_model_removed", removed)

    if auto_delete:
        for model_name in removed:
            try:
                deleted_any = delete_model_by_name(db, provider_id=provider.id, model_name=model_name)
                if deleted_any:
                    _notify(provider, "llm_model_auto_deleted", [model_name])
            except HTTPException as exc:
                logger.warning(
                    "[LLM Provider Worker] Failed to auto-delete model '%s' for provider %s: %s",
                    model_name,
                    provider.id,
                    exc.detail if hasattr(exc, "detail") else exc,
                )
                with suppress(Exception):
                    db.rollback()
            except Exception:
                logger.exception(
                    "[LLM Provider Worker] Unexpected error while auto-deleting model '%s' for provider %s",
                    model_name,
                    provider.id,
                )
                with suppress(Exception):
                    db.rollback()

    latest_status = _to_dict(provider.status)
    updated_status = {
        **latest_status,
        "model_list": sorted(current),
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "policy_blocked": False,
        "last_error": "",
    }
    if updated_status != latest_status:
        try:
            db.query(LLMProvider).filter(LLMProvider.id == provider.id).update(
                {"status": updated_status}, synchronize_session=False
            )
            db.commit()
        except Exception:
            with suppress(Exception):
                db.rollback()
            logger.exception(
                "[LLM Provider Worker] Failed to persist status for provider %s", provider.id
            )


def _llm_provider_worker(stop_event: threading.Event, *, interval_seconds: int = _DEFAULT_INTERVAL_SECONDS) -> None:
    logger.info("[LLM Provider Worker] Background worker started")
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            for provider in list_llm_provider(db):
                try:
                    _sync_provider(db, provider)
                except Exception:
                    logger.exception(
                        "[LLM Provider Worker] Failed to process provider %s", provider.id
                    )
                    with suppress(Exception):
                        db.rollback()
            try:
                updated_count = refresh_model_tokens_per_second_cache(db)
                if updated_count:
                    logger.debug(
                        "[LLM Provider Worker] Refreshed throughput cache for %s model(s)",
                        updated_count,
                    )
            except Exception:
                logger.exception("[LLM Provider Worker] Failed to refresh model throughput cache")
                with suppress(Exception):
                    db.rollback()
        except Exception:
            logger.exception("[LLM Provider Worker] Failed to list providers")
            with suppress(Exception):
                db.rollback()
        finally:
            with suppress(Exception):
                db.close()

        if stop_event.wait(interval_seconds):
            break

    logger.info("[LLM Provider Worker] Background worker stopped")


def start_llm_provider_worker(*, restart: bool = False, interval_seconds: int = _DEFAULT_INTERVAL_SECONDS):
    return start_named_worker(
        _WORKER_NAME,
        _llm_provider_worker,
        logger,
        restart=restart,
        kwargs={"interval_seconds": interval_seconds},
        start_message="[LLM Provider Worker] Monitoring thread started.",
        already_running_message="[LLM Provider Worker] Monitoring thread already running; skipping start.",
        failure_message="[LLM Provider Worker] Failed to start monitoring thread",
    )


def stop_llm_provider_worker(*, timeout: float = 5.0) -> None:
    stop_named_worker(
        _WORKER_NAME,
        logger,
        timeout=timeout,
        stopped_message="[LLM Provider Worker] Monitoring thread stopped.",
        not_running_message="[LLM Provider Worker] Monitoring thread was not running.",
        failure_message="[LLM Provider Worker] Failed to stop monitoring thread",
    )
