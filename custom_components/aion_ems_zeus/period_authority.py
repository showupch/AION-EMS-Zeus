"""Canonical local-calendar period authority for AION EMS Zeus.

All accounting engines must use these boundaries rather than independently
reconstructing Today / Week / Month / Year windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

_TRUSTED_DATA_EPOCH: datetime | None = None

from homeassistant.util import dt as dt_util


@dataclass(frozen=True)
class PeriodWindow:
    name: str
    start: datetime | None
    end: datetime
    definition: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat(),
            "definition": self.definition,
        }


def configure_data_epoch(value: str | datetime | None) -> datetime | None:
    """Configure the process-local trusted Zeus accounting epoch.

    The persisted source of truth remains the Zeus registry. This module-level
    boundary lets every accounting engine consume the same epoch without
    independently reading storage. Home Assistant Recorder history is untouched.
    """
    global _TRUSTED_DATA_EPOCH
    if value in (None, ""):
        _TRUSTED_DATA_EPOCH = None
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError as err:
                raise ValueError("Invalid Zeus data epoch") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    _TRUSTED_DATA_EPOCH = dt_util.as_local(parsed)
    return _TRUSTED_DATA_EPOCH


def trusted_data_epoch() -> datetime | None:
    return _TRUSTED_DATA_EPOCH


def _apply_epoch(start: datetime | None) -> datetime | None:
    epoch = trusted_data_epoch()
    if epoch is None:
        return start
    if start is None or epoch > start:
        return epoch
    return start


def canonical_period_start(name: str, now: datetime | None = None) -> datetime | None:
    """Return the canonical local start boundary for an accounting period."""
    now = now or dt_util.now()
    if name == "today":
        return _apply_epoch(dt_util.start_of_local_day(now))
    if name == "week":
        return _apply_epoch(dt_util.start_of_local_day(now - timedelta(days=now.weekday())))
    if name == "month":
        return _apply_epoch(dt_util.start_of_local_day(now.replace(day=1)))
    if name == "year":
        return _apply_epoch(dt_util.start_of_local_day(now.replace(month=1, day=1)))
    if name == "total":
        return trusted_data_epoch()
    raise ValueError(f"Unsupported canonical period: {name}")


def canonical_period_window(name: str, now: datetime | None = None) -> PeriodWindow:
    """Return one canonical local-calendar accounting window."""
    now = now or dt_util.now()
    definitions = {
        "today": "local midnight -> now",
        "week": "local Monday 00:00 -> now",
        "month": "local first day of month 00:00 -> now",
        "year": "local January 1 00:00 -> now",
        "total": "trusted Zeus dataset start -> now",
    }
    return PeriodWindow(name, canonical_period_start(name, now), now, definitions[name])


def canonical_period_windows(now: datetime | None = None) -> dict[str, PeriodWindow]:
    now = now or dt_util.now()
    return {name: canonical_period_window(name, now) for name in ("today", "week", "month", "year", "total")}


def date_in_period(day: date, name: str, today: date) -> bool:
    """Return whether a local calendar date belongs to the canonical period."""
    if day > today:
        return False
    epoch = trusted_data_epoch()
    if epoch is not None and day < epoch.date():
        return False
    if name == "today":
        return day == today
    if name == "week":
        return today - timedelta(days=today.weekday()) <= day <= today
    if name == "month":
        return day.year == today.year and day.month == today.month
    if name == "year":
        return day.year == today.year
    if name == "total":
        return day <= today
    raise ValueError(f"Unsupported canonical period: {name}")
