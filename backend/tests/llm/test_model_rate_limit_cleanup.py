import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.models import (  # noqa: E402
    RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
    RATE_LIMIT_ADMISSION_OPEN,
    RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
    RATE_LIMIT_QUOTA_UNIT_REQUESTS,
    RATE_LIMIT_SCOPE_CHAT,
    RATE_LIMIT_TARGET_TYPE_MODEL,
    RATE_LIMIT_TARGET_TYPE_TOOL,
    RateLimit,
    RateLimitChatAdmission,
    RateLimitDurationAdmission,
    RateLimitUsageWindow,
    _remove_deleted_model_from_rate_limits,
)


class ModelRateLimitCleanupTests(unittest.TestCase):
    """Verify model deletion keeps configured rate limits internally valid."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        RateLimit.__table__.create(bind=engine)
        RateLimitChatAdmission.__table__.create(bind=engine)
        RateLimitDurationAdmission.__table__.create(bind=engine)
        RateLimitUsageWindow.__table__.create(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _add_limit(
        self,
        db,
        *,
        name: str,
        model_ids: list[str],
        target_type: str = RATE_LIMIT_TARGET_TYPE_MODEL,
    ) -> RateLimit:
        """Create the smallest complete rate-limit row needed by these tests."""

        rate_limit = RateLimit(
            name=name,
            target_type=target_type,
            model_ids=model_ids,
            tool_keys=["web_search"] if target_type == RATE_LIMIT_TARGET_TYPE_TOOL else [],
            user_ids=["user-1"],
            group_ids=[],
            scope=RATE_LIMIT_SCOPE_CHAT,
            period="day",
            timezone="UTC",
            quota_unit=(
                RATE_LIMIT_QUOTA_UNIT_INVOCATIONS
                if target_type == RATE_LIMIT_TARGET_TYPE_TOOL
                else RATE_LIMIT_QUOTA_UNIT_REQUESTS
            ),
            quota_value=10,
            max_requests=10,
            is_active=True,
        )
        db.add(rate_limit)
        db.commit()
        return rate_limit

    def test_removes_model_from_limits_that_still_have_models(self):
        db = self.Session()
        updated_limit = self._add_limit(
            db,
            name="Shared model limit",
            model_ids=["deleted-model", "remaining-model"],
        )
        unaffected_limit = self._add_limit(
            db,
            name="Unrelated model limit",
            model_ids=["other-model"],
        )
        tool_limit = self._add_limit(
            db,
            name="Tool limit",
            model_ids=[],
            target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
        )

        result = _remove_deleted_model_from_rate_limits(db, "deleted-model")
        db.commit()

        self.assertEqual(result, {"updated_ids": [updated_limit.id], "deleted_ids": []})
        self.assertEqual(db.get(RateLimit, updated_limit.id).model_ids, ["remaining-model"])
        self.assertEqual(db.get(RateLimit, unaffected_limit.id).model_ids, ["other-model"])
        self.assertEqual(db.get(RateLimit, tool_limit.id).tool_keys, ["web_search"])

    def test_deletes_limit_and_tracking_rows_when_last_model_is_removed(self):
        db = self.Session()
        deleted_limit = self._add_limit(
            db,
            name="Single model limit",
            model_ids=["deleted-model"],
        )
        window_start = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            RateLimitUsageWindow(
                rate_limit_id=deleted_limit.id,
                user_id="user-1",
                window_start=window_start,
                request_count=1,
                token_count=0,
                invocation_count=0,
            )
        )
        db.add(
            RateLimitChatAdmission(
                rate_limit_id=deleted_limit.id,
                user_id="user-1",
                group_id=None,
                selected_model_id="deleted-model",
                chat_id=None,
                user_message_id=None,
                action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
                window_start=window_start,
                window_end=window_start + timedelta(days=1),
                quota_unit=RATE_LIMIT_QUOTA_UNIT_REQUESTS,
                quota_value=10,
                usage_snapshot_before={},
                status=RATE_LIMIT_ADMISSION_OPEN,
                request_counted=True,
                overshot_budget=False,
                overshoot_amount=0,
            )
        )
        db.add(
            RateLimitDurationAdmission(
                rate_limit_id=deleted_limit.id,
                user_id="user-1",
                group_id=None,
                target_type=RATE_LIMIT_TARGET_TYPE_MODEL,
                window_start=window_start,
                window_end=window_start + timedelta(days=1),
                reserved_seconds=60,
                consumed_seconds=0,
                status=RATE_LIMIT_ADMISSION_OPEN,
                admitted_at=window_start,
                last_activity_at=window_start,
            )
        )
        db.commit()

        result = _remove_deleted_model_from_rate_limits(db, "deleted-model")
        db.commit()

        self.assertEqual(result, {"updated_ids": [], "deleted_ids": [deleted_limit.id]})
        self.assertIsNone(db.get(RateLimit, deleted_limit.id))
        self.assertEqual(db.query(RateLimitUsageWindow).count(), 0)
        self.assertEqual(db.query(RateLimitChatAdmission).count(), 0)
        self.assertEqual(db.query(RateLimitDurationAdmission).count(), 0)


if __name__ == "__main__":
    unittest.main()
