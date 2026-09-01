from datetime import datetime, timezone, timedelta
import json
import logging
import threading

from sqlalchemy import select

from app.chats.models import (
    Chats,
    ChatMessages,
    _cancel_active_generation_for_chat,
    _cleanup_deep_research_artifacts_after_commit,
    _cleanup_orphaned_meeting_transcript_files_after_commit,
    _delete_deep_research_runs_for_chats,
    _message_content_snapshot,
)
from app.database import SessionLocal
from app.groups.init import get_group_setting_value
from app.groups.models import Group
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock
from app.users.models import User
from app.utils.background import worker_manager, start_named_worker


logger = logging.getLogger(__name__)
_WORKER_LOCK_NAME = "chats_auto_delete_worker"
_WORKER_LOCK_TTL_SECONDS = 90 * 60


def _coerce_positive_days(value, default: int = 30) -> int:
    """Coerce a value to a positive integer number of days, returning default if invalid."""
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return default


def _as_utc(dt: datetime | None) -> datetime | None:
    """Convert a datetime to UTC, adding tzinfo if naive. Return None for non-datetime inputs."""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _meta_to_dict(meta_value):
    """Convert a meta value (dict, JSON string, or None) to a dict."""
    if isinstance(meta_value, dict):
        return meta_value
    if meta_value is None:
        return {}
    if isinstance(meta_value, str):
        try:
            parsed = json.loads(meta_value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _parse_meta_timestamp(value) -> datetime | None:
    """Parse a meta timestamp string into a UTC datetime."""
    if not isinstance(value, str):
        return None
    ts = value.strip()
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(ts)
    except Exception:
        return None
    return _as_utc(parsed)


def _delete_chat_ids(db, chat_ids: list[str]) -> int:
    """Bulk-delete chats and their messages by chat IDs."""
    if not chat_ids:
        return 0
    chats_to_delete = db.query(Chats).filter(Chats.id.in_(chat_ids)).all()
    if not chats_to_delete:
        return 0
    ready_chat_ids: list[str] = []
    for chat in chats_to_delete:
        if _cancel_active_generation_for_chat(str(chat.id)):
            ready_chat_ids.append(str(chat.id))
        else:
            logger.warning(
                "[Chats Prune] Skipping active chat deletion while generation is still stopping: %s",
                chat.id,
            )
    if not ready_chat_ids:
        return 0
    chat_owner_by_chat_id = {chat.id: chat.user_id for chat in chats_to_delete}
    messages_to_delete = db.query(ChatMessages).filter(ChatMessages.chat_id.in_(ready_chat_ids)).all()
    message_contents_by_user_id: dict[str, list] = {}
    for message in messages_to_delete:
        owner_user_id = chat_owner_by_chat_id.get(message.chat_id)
        if not owner_user_id:
            continue
        message_contents_by_user_id.setdefault(owner_user_id, []).extend(_message_content_snapshot([message]))
    deep_research_cleanup = _delete_deep_research_runs_for_chats(
        db,
        None,
        ready_chat_ids,
    )
    db.query(ChatMessages).filter(ChatMessages.chat_id.in_(ready_chat_ids)).delete(synchronize_session=False)
    db.query(Chats).filter(Chats.id.in_(ready_chat_ids)).delete(synchronize_session=False)
    db.commit()
    _cleanup_deep_research_artifacts_after_commit(deep_research_cleanup)
    for user_id, user_message_contents in message_contents_by_user_id.items():
        _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id, user_message_contents)
    return len(ready_chat_ids)


def _collect_shadow_retention_chat_ids(chats: list[Chats], cutoff: datetime) -> list[str]:
    """Collect IDs of shadow-deleted chats that were deleted before the cutoff."""
    eligible_ids: list[str] = []
    for chat in chats:
        meta = _meta_to_dict(getattr(chat, "meta", None))
        if not bool(meta.get("shadow_deleted")):
            continue
        deleted_at = _parse_meta_timestamp(meta.get("shadow_deleted_at")) or _as_utc(getattr(chat, "last_updated_at", None))
        if deleted_at and deleted_at <= cutoff:
            eligible_ids.append(chat.id)
    return eligible_ids


def _collect_temp_retention_chat_ids(chats: list[Chats], cutoff: datetime) -> list[str]:
    """Collect IDs of temporary chats last updated before the cutoff."""
    eligible_ids: list[str] = []
    for chat in chats:
        meta = _meta_to_dict(getattr(chat, "meta", None))
        if meta.get("status") != "temp":
            continue
        last_updated_at = _as_utc(getattr(chat, "last_updated_at", None))
        if last_updated_at and last_updated_at <= cutoff:
            eligible_ids.append(chat.id)
    return eligible_ids


def _load_group_chat_prune_settings(group_id: str, db) -> dict:
    """Load chat retention and pruning settings for a group."""
    settings: dict = {
        "enabled_auto_delete": False,
        "auto_delete_days": 30,
        "shadow_delete_enabled": False,
        "shadow_retention_enabled": False,
        "shadow_retention_days": 30,
        "save_temp_chats": False,
        "temp_retention_enabled": False,
        "temp_retention_days": 30,
    }

    try:
        settings["enabled_auto_delete"] = bool(get_group_setting_value(group_id, "chat", "auto_delete_chats", db))
    except Exception:
        pass
    try:
        settings["auto_delete_days"] = _coerce_positive_days(
            get_group_setting_value(group_id, "chat", "auto_delete_chats_days", db),
            default=30,
        )
    except Exception:
        pass

    try:
        settings["shadow_delete_enabled"] = bool(get_group_setting_value(group_id, "chat", "shadow_chat_deletion", db))
    except Exception:
        pass
    try:
        settings["shadow_retention_enabled"] = bool(
            get_group_setting_value(group_id, "chat", "shadow_chat_deletion_retention_enabled", db)
        )
    except Exception:
        pass
    try:
        settings["shadow_retention_days"] = _coerce_positive_days(
            get_group_setting_value(group_id, "chat", "shadow_chat_deletion_retention_days", db),
            default=30,
        )
    except Exception:
        pass

    try:
        settings["save_temp_chats"] = bool(get_group_setting_value(group_id, "chat", "save_temp_chats", db))
    except Exception:
        pass
    try:
        settings["temp_retention_enabled"] = bool(
            get_group_setting_value(group_id, "chat", "save_temp_chats_retention_enabled", db)
        )
    except Exception:
        pass
    try:
        settings["temp_retention_days"] = _coerce_positive_days(
            get_group_setting_value(group_id, "chat", "save_temp_chats_retention_days", db),
            default=30,
        )
    except Exception:
        pass

    return settings


# -------------------
# Background: Auto-delete old chats (per-group)
# -------------------
def _auto_delete_chats_worker(stop_event: threading.Event):
    """
    Periodically deletes chats (and their messages) according to group policies.

    Group Settings (page "chat"):
    - auto_delete_chats: bool
    - auto_delete_chats_days: int (days)
    - shadow_chat_deletion_retention_enabled: bool
    - shadow_chat_deletion_retention_days: int (days)
    - save_temp_chats_retention_enabled: bool
    - save_temp_chats_retention_days: int (days)
    """
    DEFAULT_INTERVAL_SEC = 60 * 60  # Run hourly by default
    while not stop_event.is_set():
        interval_sec = DEFAULT_INTERVAL_SEC
        lock_owner = new_lock_owner()
        if not try_acquire_lock(_WORKER_LOCK_NAME, lock_owner, _WORKER_LOCK_TTL_SECONDS):
            if stop_event.wait(300):
                break
            continue

        db = SessionLocal()
        try:
            groups = db.query(Group).all()
            if not groups:
                # No groups configured - skip
                if stop_event.wait(max(300, int(interval_sec))):
                    break
                continue

            total_deleted = 0
            for grp in groups:
                prune_settings = _load_group_chat_prune_settings(grp.id, db)
                should_auto_delete = bool(prune_settings["enabled_auto_delete"])
                should_shadow_cleanup = bool(
                    prune_settings["shadow_delete_enabled"] and prune_settings["shadow_retention_enabled"]
                )
                should_temp_cleanup = bool(
                    prune_settings["save_temp_chats"] and prune_settings["temp_retention_enabled"]
                )

                if not any((should_auto_delete, should_shadow_cleanup, should_temp_cleanup)):
                    continue

                # Pass a SELECT directly to IN(). Calling .subquery() here would create a
                # FROM-clause object that SQLAlchemy must implicitly coerce back to SELECT.
                user_ids_select = select(User.id).where(User.group_id == grp.id)

                if should_auto_delete:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=prune_settings["auto_delete_days"])
                    old_chats = (
                        db.query(Chats)
                        .filter(Chats.user_id.in_(user_ids_select))
                        .filter(Chats.last_updated_at < cutoff)
                        .all()
                    )
                    try:
                        deleted_count = _delete_chat_ids(db, [chat.id for chat in old_chats])
                        if deleted_count:
                            total_deleted += deleted_count
                            logger.info(
                                "[Chats Prune] Group %s: Deleted %s chats inactive for > %s days.",
                                grp.id,
                                deleted_count,
                                prune_settings["auto_delete_days"],
                            )
                    except Exception:
                        db.rollback()
                        logger.error("[Chats Prune] Group %s: Failed auto-delete cleanup", grp.id, exc_info=True)

                if should_shadow_cleanup:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=prune_settings["shadow_retention_days"])
                    shadow_candidates = (
                        db.query(Chats)
                        .filter(Chats.user_id.in_(user_ids_select))
                        .filter(Chats.last_updated_at < cutoff)
                        .all()
                    )
                    try:
                        shadow_ids = _collect_shadow_retention_chat_ids(shadow_candidates, cutoff)
                        deleted_count = _delete_chat_ids(db, shadow_ids)
                        if deleted_count:
                            total_deleted += deleted_count
                            logger.info(
                                "[Chats Prune] Group %s: Deleted %s shadow-deleted chats older than %s days.",
                                grp.id,
                                deleted_count,
                                prune_settings["shadow_retention_days"],
                            )
                    except Exception:
                        db.rollback()
                        logger.error(
                            "[Chats Prune] Group %s: Failed shadow deletion retention cleanup",
                            grp.id,
                            exc_info=True,
                        )

                if should_temp_cleanup:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=prune_settings["temp_retention_days"])
                    temp_candidates = (
                        db.query(Chats)
                        .filter(Chats.user_id.in_(user_ids_select))
                        .filter(Chats.last_updated_at < cutoff)
                        .all()
                    )
                    try:
                        temp_ids = _collect_temp_retention_chat_ids(temp_candidates, cutoff)
                        deleted_count = _delete_chat_ids(db, temp_ids)
                        if deleted_count:
                            total_deleted += deleted_count
                            logger.info(
                                "[Chats Prune] Group %s: Deleted %s saved temporary chats older than %s days.",
                                grp.id,
                                deleted_count,
                                prune_settings["temp_retention_days"],
                            )
                    except Exception:
                        db.rollback()
                        logger.error(
                            "[Chats Prune] Group %s: Failed temporary chat retention cleanup",
                            grp.id,
                            exc_info=True,
                        )

            if total_deleted:
                logger.info("[Chats Prune] Total chats deleted this cycle: %s", total_deleted)

        except Exception:
            logger.error("[Chats Prune] Worker loop error", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass
            release_lock(_WORKER_LOCK_NAME, lock_owner)

        if stop_event.wait(max(300, int(interval_sec))):
            break


def start_auto_delete_chats_worker():
    """Start the chats auto-delete background worker (daemon thread)."""
    return start_named_worker(
        "chats_auto_delete",
        _auto_delete_chats_worker,
        logger,
        start_message="[Chats Prune] Background worker started.",
        already_running_message="[Chats Prune] Background worker already running; skipping start.",
        failure_message="[Chats Prune] Failed to start background worker",
    )


def stop_auto_delete_chats_worker(timeout: float = 5.0):
    """Signal the chats auto-delete worker to stop."""
    worker_manager.stop_worker("chats_auto_delete", timeout=timeout)
