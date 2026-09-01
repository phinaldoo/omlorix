import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException

from app.llm.models import (
    DEFAULT_RATE_LIMIT_TIMEZONE,
    _get_window_bounds,
    _validate_rate_limit_timezone,
)


class RateLimitTimezoneTests(unittest.TestCase):
    def test_validate_rate_limit_timezone_defaults_to_utc(self):
        self.assertEqual(_validate_rate_limit_timezone(None), DEFAULT_RATE_LIMIT_TIMEZONE)
        self.assertEqual(_validate_rate_limit_timezone(""), DEFAULT_RATE_LIMIT_TIMEZONE)

    def test_validate_rate_limit_timezone_rejects_invalid_values(self):
        with self.assertRaises(HTTPException) as exc_info:
            _validate_rate_limit_timezone("Mars/Olympus_Mons")

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("valid IANA timezone", str(exc_info.exception.detail))

    def test_daily_window_uses_configured_timezone_boundaries(self):
        window_start, window_end = _get_window_bounds(
            "day",
            "America/New_York",
            now=datetime(2026, 5, 9, 3, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(window_start, datetime(2026, 5, 8, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(window_end, datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc))

    def test_weekly_window_starts_on_local_monday_midnight(self):
        window_start, window_end = _get_window_bounds(
            "week",
            "Europe/Berlin",
            now=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(window_start, datetime(2026, 5, 3, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(window_end, datetime(2026, 5, 10, 22, 0, tzinfo=timezone.utc))

    def test_monthly_window_handles_dst_change_in_configured_timezone(self):
        window_start, window_end = _get_window_bounds(
            "month",
            "America/Los_Angeles",
            now=datetime(2026, 3, 31, 23, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(window_start, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(window_end, datetime(2026, 4, 1, 7, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
