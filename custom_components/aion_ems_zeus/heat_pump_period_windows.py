"""Completed-hour, local-calendar windows for read-only heat-pump evidence.

This module does not schedule Recorder calls or change existing Zeus totals.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def completed_period_windows(now: datetime, tz_name: str) -> dict:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    zone = ZoneInfo(tz_name)
    local = now.astimezone(zone)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week = (day - timedelta(days=day.weekday())).replace(fold=0)
    month = day.replace(day=1)
    year = day.replace(month=1, day=1)
    end_utc = local.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    starts = {'day':day, 'week':week, 'month':month, 'year':year}
    return {key:(start.astimezone(timezone.utc),end_utc) for key,start in starts.items()}
