"""Access Windows - Time-based access control for groups.

This module provides utilities for evaluating whether a group member
can sign in based on configured time windows (e.g., school hours or
nighttime blocklist rules).
"""

from datetime import datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import logging

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.groups.access_windows_validation import (
    parse_access_window_time,
    validate_access_window_settings,
)
from app.groups.init import get_group_page_settings


logger = logging.getLogger(__name__)


class AccessWindowPolicyError(RuntimeError):
    """Raised when the group's access-window policy cannot be trusted."""


def _disabled_access_window_settings() -> Dict[str, Any]:
    return {
        "enabled": False,
        "timezone": "UTC",
        "mode": "allowlist",
        "rules": [],
        "show_next_available": True,
        "blocked_message": "",
    }


def _parse_time(time_str: str) -> Optional[dt_time]:
    """Parse HH:MM string into a time object."""
    return parse_access_window_time(time_str)


def _get_timezone(tz_str: str) -> ZoneInfo:
    """Get ZoneInfo from timezone string, defaulting to UTC."""
    if not tz_str:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_str)
    except Exception:
        logger.warning("Invalid timezone '%s', falling back to UTC", tz_str)
        return ZoneInfo("UTC")


def _time_in_range(current: dt_time, start: dt_time, end: dt_time) -> bool:
    """Check if current time is within start-end range.
    
    Handles overnight ranges (e.g., 22:00 to 06:00).
    """
    if start <= end:
        return start <= current <= end
    else:
        return current >= start or current <= end


def _day_matches(weekday: int, days: List[int]) -> bool:
    """Check if weekday is in the days list.
    
    Weekday: 0=Monday, 6=Sunday (Python convention).
    Days in rules also use 0=Monday, 6=Sunday.
    """
    if not days:
        return True
    return weekday in days


def _rule_matches_current_time(
    current_time: dt_time,
    current_weekday: int,
    start: dt_time,
    end: dt_time,
    days: List[int],
) -> bool:
    """Check whether a rule matches the current time and effective rule day."""
    if not _time_in_range(current_time, start, end):
        return False

    if start <= end:
        return _day_matches(current_weekday, days)

    if current_time >= start:
        return _day_matches(current_weekday, days)

    previous_weekday = (current_weekday - 1) % 7
    return _day_matches(previous_weekday, days)


def _evaluate_rules(
    now: datetime,
    rules: List[Dict[str, Any]],
    mode: str,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Evaluate access rules against current time.
    
    Returns:
        (is_allowed, matching_rule)
        
    For allowlist mode: allowed if ANY rule matches.
    For blocklist mode: blocked if ANY rule matches.
    """
    current_time = now.time()
    current_weekday = now.weekday()
    
    matching_rule = None
    
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        start = _parse_time(rule.get("start", ""))
        end = _parse_time(rule.get("end", ""))
        days = rule.get("days", [])
        
        if not start or not end:
            continue
        
        if _rule_matches_current_time(current_time, current_weekday, start, end, days):
            matching_rule = rule
            break
    
    if mode == "allowlist":
        return (matching_rule is not None, matching_rule)
    else:
        return (matching_rule is None, matching_rule)


def _calculate_next_allowed_time(
    now: datetime,
    settings: Dict[str, Any],
) -> Optional[datetime]:
    """Calculate when access will next be allowed.
    
    Returns None if no predictable next window exists.
    """
    rules = settings.get("rules", [])
    mode = settings.get("mode", "allowlist")

    if mode == "blocklist" and not rules:
        return None

    if mode == "allowlist":
        if not rules:
            return None
        
        candidates = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            start = _parse_time(rule.get("start", ""))
            days = rule.get("days", [])
            
            if not start:
                continue
            
            for day_offset in range(8):
                check_date = now + timedelta(days=day_offset)
                if days and check_date.weekday() not in days:
                    continue
                
                candidate = check_date.replace(
                    hour=start.hour,
                    minute=start.minute,
                    second=0,
                    microsecond=0,
                )
                
                if candidate > now:
                    candidates.append(candidate)
                    break
        
        if candidates:
            return min(candidates)
    
    return None


def get_group_access_settings(group_id: str, db: Session) -> Dict[str, Any]:
    """Get access window settings for a group."""
    try:
        settings = get_group_page_settings(group_id, "access_windows", db)
    except KeyError:
        return {
            **_disabled_access_window_settings(),
        }
    except (HTTPException, RuntimeError, SQLAlchemyError) as exc:
        raise AccessWindowPolicyError("Access window policy lookup failed") from exc

    try:
        return validate_access_window_settings(
            settings,
            field_prefix="settings.access_windows",
        )
    except ValueError as exc:
        raise AccessWindowPolicyError("Access window policy is invalid") from exc


def is_group_accessible_now(
    group_id: str,
    db: Session,
    is_admin: bool = False,
) -> Dict[str, Any]:
    """Check if a group is currently accessible.
    
    Returns:
        {
            "accessible": bool,
            "reason": str | None,
            "next_allowed_at": datetime | None,
            "blocked_message": str | None,
        }
    """
    if is_admin:
        # Access windows are a member-facing restriction. Administrators must
        # retain access even when the group's policy is malformed or currently
        # blocking regular members, otherwise a bad window can lock out the
        # people who need to fix it.
        return {
            "accessible": True,
            "reason": None,
            "next_allowed_at": None,
            "blocked_message": None,
        }

    try:
        settings = get_group_access_settings(group_id, db)
    except AccessWindowPolicyError:
        logger.exception("Access window policy lookup failed for group %s", group_id)
        return {
            "accessible": False,
            "reason": "policy_error",
            "next_allowed_at": None,
            "blocked_message": None,
        }
    
    if not settings.get("enabled", False):
        return {
            "accessible": True,
            "reason": None,
            "next_allowed_at": None,
            "blocked_message": None,
        }

    tz = _get_timezone(settings.get("timezone", "UTC"))
    now = datetime.now(tz)

    rules = settings.get("rules", [])
    mode = settings.get("mode", "allowlist")
    
    if mode == "allowlist" and not rules:
        return {
            "accessible": False,
            "reason": "no_rules_defined",
            "next_allowed_at": None,
            "blocked_message": settings.get("blocked_message") or None,
        }
    
    if mode == "blocklist" and not rules:
        return {
            "accessible": True,
            "reason": "no_rules_defined",
            "next_allowed_at": None,
            "blocked_message": None,
        }
    
    is_allowed, matching_rule = _evaluate_rules(now, rules, mode)
    
    if is_allowed:
        return {
            "accessible": True,
            "reason": "rule_matched" if matching_rule else "no_block_rule",
            "next_allowed_at": None,
            "blocked_message": None,
        }
    
    next_allowed = None
    if settings.get("show_next_available", True):
        next_allowed = _calculate_next_allowed_time(now, settings)
    
    return {
        "accessible": False,
        "reason": "outside_allowed_window" if mode == "allowlist" else "in_blocked_window",
        "next_allowed_at": next_allowed.isoformat() if next_allowed else None,
        "blocked_message": settings.get("blocked_message") or None,
    }
