from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AutomationScheduleState:
    run_at: datetime
    slot: str
    is_one_time: bool


def normalize_automation_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_schedule_run_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    return normalize_automation_datetime(parsed)


def format_schedule_slot(run_at: datetime) -> str:
    normalized = normalize_automation_datetime(run_at)
    if normalized is None:
        return ""
    return normalized.strftime("%Y%m%d:%H%M")


def compute_next_schedule_state(
    schedule_rules: Any,
    *,
    reference_time: datetime | None = None,
    include_reference: bool = True,
    schedule_timezone: str | None = None,
) -> AutomationScheduleState | None:
    if not isinstance(schedule_rules, list) or len(schedule_rules) == 0:
        return None

    normalized_reference = _truncate_to_schedule_minute(
        normalize_automation_datetime(reference_time) or datetime.now(timezone.utc)
    )
    resolved_timezone = _resolve_schedule_timezone(schedule_timezone)
    localized_reference = normalized_reference.astimezone(resolved_timezone)
    candidates: list[AutomationScheduleState] = []

    for rule in schedule_rules:
        if not isinstance(rule, dict):
            continue

        scheduled_at = parse_schedule_run_at(rule.get("run_at"))
        if scheduled_at is not None:
            if _is_candidate_due(scheduled_at, normalized_reference, include_reference):
                candidates.append(
                    AutomationScheduleState(
                        run_at=scheduled_at,
                        slot=format_schedule_slot(scheduled_at),
                        is_one_time=True,
                    )
                )
            continue

        days = rule.get("days")
        if not isinstance(days, list) or len(days) == 0:
            continue
        valid_days = {day for day in days if isinstance(day, int) and 0 <= day <= 6}
        if not valid_days:
            continue

        times = rule.get("times") or []
        if not isinstance(times, list) or len(times) == 0:
            continue

        for time_str in times:
            parsed_time = _parse_schedule_time(time_str)
            if parsed_time is None:
                continue
            hour, minute = parsed_time

            for offset in range(0, 8):
                candidate_day = localized_reference.date() + timedelta(days=offset)
                if candidate_day.weekday() not in valid_days:
                    continue
                candidate_dt = _build_schedule_candidate(
                    candidate_day.year,
                    candidate_day.month,
                    candidate_day.day,
                    hour,
                    minute,
                    resolved_timezone,
                )
                if candidate_dt is None:
                    continue
                if _is_candidate_due(candidate_dt, normalized_reference, include_reference):
                    candidates.append(
                        AutomationScheduleState(
                            run_at=candidate_dt,
                            slot=format_schedule_slot(candidate_dt),
                            is_one_time=False,
                        )
                    )
                    break

    if not candidates:
        return None

    return min(candidates, key=lambda candidate: (candidate.run_at, candidate.slot))


def _is_candidate_due(candidate: datetime, reference_time: datetime, include_reference: bool) -> bool:
    if include_reference:
        return candidate >= reference_time
    return candidate > reference_time


def _truncate_to_schedule_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _parse_schedule_time(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    try:
        hour_str, minute_str = value.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except (TypeError, ValueError):
        return None

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None

    return hour, minute


def _resolve_schedule_timezone(value: str | None) -> timezone | ZoneInfo:
    normalized = str(value or "").strip()
    if not normalized or normalized.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _build_schedule_candidate(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    schedule_timezone: timezone | ZoneInfo,
) -> datetime | None:
    naive_local = datetime(year, month, day, hour, minute)
    candidates: list[datetime] = []

    for fold in (0, 1):
        local_candidate = naive_local.replace(tzinfo=schedule_timezone, fold=fold)
        roundtrip = local_candidate.astimezone(timezone.utc).astimezone(schedule_timezone)
        if (
            roundtrip.year == year
            and roundtrip.month == month
            and roundtrip.day == day
            and roundtrip.hour == hour
            and roundtrip.minute == minute
        ):
            utc_candidate = local_candidate.astimezone(timezone.utc)
            if utc_candidate not in candidates:
                candidates.append(utc_candidate)

    if not candidates:
        return None

    return min(candidates)
