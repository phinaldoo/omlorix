import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.groups import access_windows


def test_overnight_rule_matches_post_midnight_against_previous_day():
    now = datetime(2026, 5, 30, 2, 0, tzinfo=ZoneInfo("UTC"))
    rules = [{"start": "22:00", "end": "06:00", "days": [4]}]

    is_allowed, matching_rule = access_windows._evaluate_rules(now, rules, "allowlist")

    assert is_allowed is True
    assert matching_rule == rules[0]


def test_overnight_rule_does_not_match_pre_start_early_morning_same_day():
    now = datetime(2026, 5, 30, 2, 0, tzinfo=ZoneInfo("UTC"))
    rules = [{"start": "22:00", "end": "06:00", "days": [5]}]

    is_allowed, matching_rule = access_windows._evaluate_rules(now, rules, "allowlist")

    assert is_allowed is False
    assert matching_rule is None
