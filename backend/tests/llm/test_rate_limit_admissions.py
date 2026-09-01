import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.models import (  # noqa: E402
    RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_BLOCK_REASON_IN_FLIGHT,
    RATE_LIMIT_QUOTA_UNIT_REQUESTS,
    RATE_LIMIT_QUOTA_UNIT_TOKENS,
    RATE_LIMIT_SCOPE_CHAT,
    RATE_LIMIT_TARGET_TYPE_MODEL,
    RateLimit,
    RateLimitAdmissionContext,
    RateLimitChatAdmission,
    RateLimitUsageWindow,
    admit_user_rate_limit,
    finalize_rate_limit_admission,
    serialized_rate_limit_window_admission,
)
from app.llmstats.models import LLMGenerationStatistic  # noqa: E402


class RateLimitAdmissionTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        RateLimit.__table__.create(bind=engine)
        RateLimitChatAdmission.__table__.create(bind=engine)
        RateLimitUsageWindow.__table__.create(bind=engine)
        LLMGenerationStatistic.__table__.create(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _create_model_rate_limit(self, db, *, quota_unit: str, quota_value: int) -> RateLimit:
        rate_limit = RateLimit(
            name=f"{quota_unit} quota",
            target_type=RATE_LIMIT_TARGET_TYPE_MODEL,
            model_ids=["model-1"],
            tool_keys=[],
            user_ids=["user-1"],
            group_ids=[],
            scope=RATE_LIMIT_SCOPE_CHAT,
            period="day",
            timezone="UTC",
            quota_unit=quota_unit,
            quota_value=quota_value,
            max_requests=max(quota_value, 1),
            is_active=True,
        )
        db.add(rate_limit)
        db.commit()
        return rate_limit

    def test_request_quota_reserves_usage_during_admission(self):
        db = self.Session()
        self._create_model_rate_limit(
            db,
            quota_unit=RATE_LIMIT_QUOTA_UNIT_REQUESTS,
            quota_value=1,
        )

        admission = admit_user_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            model_id="model-1",
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
        )

        self.assertIsInstance(admission, RateLimitAdmissionContext)

        usage_window = db.query(RateLimitUsageWindow).first()
        stored_admission = db.query(RateLimitChatAdmission).first()

        self.assertEqual(usage_window.request_count, 1)
        self.assertTrue(stored_admission.request_counted)

        blocked = admit_user_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            model_id="model-1",
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
        )

        self.assertIsInstance(blocked, dict)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["current_usage"], 1)
        self.assertEqual(blocked["remaining_usage"], 0)

    def test_token_quota_blocks_concurrent_open_admissions(self):
        db = self.Session()
        self._create_model_rate_limit(
            db,
            quota_unit=RATE_LIMIT_QUOTA_UNIT_TOKENS,
            quota_value=100,
        )

        first_admission = admit_user_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            model_id="model-1",
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
        )
        blocked = admit_user_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            model_id="model-1",
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
        )

        self.assertIsInstance(first_admission, RateLimitAdmissionContext)
        self.assertIsInstance(blocked, dict)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["block_reason"], RATE_LIMIT_BLOCK_REASON_IN_FLIGHT)
        self.assertEqual(blocked["current_usage"], 0)

        finalize_rate_limit_admission(
            db,
            first_admission.admission_id,
            final_status=RATE_LIMIT_ADMISSION_FAILED,
        )

        next_admission = admit_user_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            model_id="model-1",
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
        )
        self.assertIsInstance(next_admission, RateLimitAdmissionContext)


def test_serialized_rate_limit_admission_uses_postgres_advisory_lock():
    calls = []

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Db:
        def get_bind(self):
            return _Bind()

        def execute(self, statement, params):
            calls.append((str(statement), params))

    with serialized_rate_limit_window_admission(
        _Db(),
        rate_limit_id="rate-limit-1",
        user_id="user-1",
        window_start=datetime.now(timezone.utc),
    ):
        pass

    assert len(calls) == 1
    assert "pg_advisory_xact_lock" in calls[0][0]
    assert isinstance(calls[0][1]["lock_key"], int)


if __name__ == "__main__":
    unittest.main()
