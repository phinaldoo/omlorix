from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.realtime import worker as realtime_worker  # noqa: E402


def test_realtime_enforcement_pass_uses_fresh_database_session():
    """Every enforcement pass closes its database connection."""
    db = MagicMock()
    with patch.object(
        realtime_worker,
        "SessionLocal",
        return_value=db,
    ), patch.object(
        realtime_worker,
        "reconcile_expired_realtime_sessions",
    ) as mock_reconcile:
        realtime_worker.run_realtime_enforcement_once()

    mock_reconcile.assert_called_once_with(db)
    db.close.assert_called_once_with()


def test_realtime_enforcement_pass_rolls_back_failures():
    """Transient provider/database errors leave the next retry usable."""
    db = MagicMock()
    with patch.object(
        realtime_worker,
        "SessionLocal",
        return_value=db,
    ), patch.object(
        realtime_worker,
        "reconcile_expired_realtime_sessions",
        side_effect=RuntimeError("temporary failure"),
    ):
        try:
            realtime_worker.run_realtime_enforcement_once()
        except RuntimeError:
            pass
        else:
            raise AssertionError("enforcement failure was not propagated")

    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_realtime_enforcement_lease_renews_until_pass_finishes():
    """Long provider cleanup passes keep exclusive distributed ownership."""

    lease_stop_event = MagicMock()
    lease_stop_event.wait.return_value = False
    with patch.object(
        realtime_worker,
        "refresh_lock",
        side_effect=[True, False],
    ) as mock_refresh:
        realtime_worker._renew_realtime_enforcement_lock(
            lease_stop_event,
            "owner-1",
        )

    assert mock_refresh.call_count == 2
    mock_refresh.assert_called_with(
        realtime_worker.REALTIME_ENFORCEMENT_LOCK_NAME,
        "owner-1",
        realtime_worker.REALTIME_ENFORCEMENT_LOCK_TTL_SECONDS,
    )


def test_realtime_enforcement_worker_stops_lease_before_release():
    """The renewal thread is retired before the owner-safe lock release."""

    worker_stop_event = MagicMock()
    worker_stop_event.is_set.return_value = False
    worker_stop_event.wait.return_value = True
    lease_thread = MagicMock()
    lease_stop_event = MagicMock()

    with patch.object(
        realtime_worker,
        "new_lock_owner",
        return_value="owner-1",
    ), patch.object(
        realtime_worker,
        "try_acquire_lock",
        return_value=True,
    ), patch.object(
        realtime_worker,
        "run_realtime_enforcement_once",
    ), patch.object(
        realtime_worker.threading,
        "Event",
        return_value=lease_stop_event,
    ), patch.object(
        realtime_worker.threading,
        "Thread",
        return_value=lease_thread,
    ), patch.object(
        realtime_worker,
        "release_lock",
    ) as mock_release:
        realtime_worker._realtime_enforcement_worker(worker_stop_event)

    lease_thread.start.assert_called_once_with()
    lease_stop_event.set.assert_called_once_with()
    lease_thread.join.assert_called_once_with(timeout=2.0)
    mock_release.assert_called_once_with(
        realtime_worker.REALTIME_ENFORCEMENT_LOCK_NAME,
        "owner-1",
    )
