import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.models import (  # noqa: E402
    RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
    RATE_LIMIT_SCOPE_CHAT,
    RATE_LIMIT_TARGET_TYPE_TOOL,
    RateLimit,
    RateLimitUsageWindow,
    admit_user_tool_rate_limit,
    check_rate_limit_conflicts,
    has_applicable_rate_limits,
)
from app.llm.schemas import CreateRateLimitRequest  # noqa: E402


class ToolRateLimitTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        RateLimit.__table__.create(bind=engine)
        RateLimitUsageWindow.__table__.create(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_schema_requires_tool_keys_for_tool_target(self):
        with self.assertRaises(ValueError):
            CreateRateLimitRequest.model_validate(
                {
                    "name": "Tool quota",
                    "target_type": "tool",
                    "tool_keys": [],
                    "user_ids": ["user-1"],
                    "period": "day",
                    "quota_unit": "invocations",
                    "quota_value": 1,
                }
            )

    def test_applicable_rate_limit_existence_uses_exact_user_and_group_membership(self):
        db = self.Session()
        db.add_all(
            [
                RateLimit(
                    name="Inactive direct assignment",
                    target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
                    model_ids=[],
                    tool_keys=["web_search"],
                    user_ids=["user-direct"],
                    group_ids=[],
                    scope=RATE_LIMIT_SCOPE_CHAT,
                    period="day",
                    timezone="UTC",
                    quota_unit=RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
                    quota_value=1,
                    max_requests=1,
                    is_active=False,
                ),
                RateLimit(
                    name="Active group assignment",
                    target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
                    model_ids=[],
                    tool_keys=["web_search"],
                    user_ids=["another-user"],
                    group_ids=["group-match"],
                    scope=RATE_LIMIT_SCOPE_CHAT,
                    period="day",
                    timezone="UTC",
                    quota_unit=RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
                    quota_value=1,
                    max_requests=1,
                    is_active=True,
                ),
                RateLimit(
                    name="Active direct assignment",
                    target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
                    model_ids=[],
                    tool_keys=["web_search"],
                    user_ids=["first-user", "user-match"],
                    group_ids=[],
                    scope=RATE_LIMIT_SCOPE_CHAT,
                    period="day",
                    timezone="UTC",
                    quota_unit=RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
                    quota_value=1,
                    max_requests=1,
                    is_active=True,
                ),
            ]
        )
        db.commit()

        self.assertTrue(
            has_applicable_rate_limits(db, "unassigned-user", "group-match")
        )
        self.assertTrue(
            has_applicable_rate_limits(db, "user-match", "different-group")
        )
        self.assertFalse(
            has_applicable_rate_limits(db, "user-direct", "different-group")
        )
        self.assertFalse(
            has_applicable_rate_limits(db, "user", "group-match-suffix")
        )

    def test_schema_rejects_invocations_for_model_target(self):
        with self.assertRaises(ValueError):
            CreateRateLimitRequest.model_validate(
                {
                    "name": "Model quota",
                    "target_type": "model",
                    "model_ids": ["model-1"],
                    "user_ids": ["user-1"],
                    "period": "day",
                    "quota_unit": "invocations",
                    "quota_value": 1,
                }
            )

    def test_tool_invocation_limit_increments_and_blocks(self):
        db = self.Session()
        db.add(
            RateLimit(
                name="Search quota",
                target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
                model_ids=[],
                tool_keys=["web_search"],
                user_ids=["user-1"],
                group_ids=[],
                scope=RATE_LIMIT_SCOPE_CHAT,
                period="day",
                timezone="UTC",
                quota_unit=RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
                quota_value=1,
                max_requests=1,
                is_active=True,
            )
        )
        db.commit()

        self.assertIsNone(
            admit_user_tool_rate_limit(
                db,
                user_id="user-1",
                group_id=None,
                tool_key="web_search",
            )
        )
        blocked = admit_user_tool_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            tool_key="web_search",
        )

        self.assertIsInstance(blocked, dict)
        self.assertEqual(blocked["code"], "user_tool_rate_limited")
        self.assertEqual(blocked["target_type"], "tool")
        self.assertEqual(blocked["tool_key"], "web_search")
        self.assertEqual(blocked["current_usage"], 1)
        self.assertEqual(blocked["remaining_usage"], 0)

    def test_tool_conflicts_are_separate_from_model_conflicts(self):
        db = self.Session()
        db.add(
            RateLimit(
                name="Search quota",
                target_type=RATE_LIMIT_TARGET_TYPE_TOOL,
                model_ids=[],
                tool_keys=["web_search"],
                user_ids=["user-1"],
                group_ids=[],
                scope=RATE_LIMIT_SCOPE_CHAT,
                period="day",
                timezone="UTC",
                quota_unit=RATE_LIMIT_QUOTA_UNIT_INVOCATIONS,
                quota_value=1,
                max_requests=1,
                is_active=True,
            )
        )
        db.commit()

        tool_conflicts = check_rate_limit_conflicts(
            db,
            target_type="tool",
            model_ids=[],
            tool_keys=["web_search"],
            user_ids=["user-1"],
            group_ids=[],
        )
        model_conflicts = check_rate_limit_conflicts(
            db,
            target_type="model",
            model_ids=["web_search"],
            user_ids=["user-1"],
            group_ids=[],
        )

        self.assertEqual(len(tool_conflicts), 1)
        self.assertEqual(tool_conflicts[0]["overlapping_tool_keys"], ["web_search"])
        self.assertEqual(model_conflicts, [])


if __name__ == "__main__":
    unittest.main()
