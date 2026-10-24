"""Conservative V2 hourly PV baseline from reconciled completed days.

Pure helper: does not alter Zeus energy accounting or stored measurements.
"""
from __future__ import annotations

import math
import statistics
from datetime import date


def reconciled_hourly_baseline(
    target_date: date,
    hour: int,
    day_hour_power: dict,
    authoritative_daily_kwh: dict,
    *,
    min_daylight_hours: int = 8,
    min_days: int = 2,
    max_age_days: int = 21,
) -> dict | None:
    """Return baseline only when per-day power can be checked against measured energy.

    Hourly samples are *power* estimates, not an energy meter. Require broad
    daylight sampling and plausible agreement with the canonical daily total;
    reject instead of silently learning from a different PV accounting source.
    """
    eligible = []
    for day, measured in authoritative_daily_kwh.items():
        if not isinstance(day, date) or day >= target_date or (target_date - day).days > max_age_days:
            continue
        try:
            measured = float(measured)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(measured) or measured <= 0:
            continue
        hours = day_hour_power.get(day, {})
        if not isinstance(hours, dict):
            continue
        valid = {}
        for h, power in hours.items():
            try:
                h = int(h)
                power = float(power)
            except (TypeError, ValueError):
                continue
            if 0 <= h <= 23 and math.isfinite(power) and power >= 0:
                valid[h] = power
        # Missing daylight hours can cause false low totals and inflated scaling.
        if sum(h in valid for h in range(8, 18)) < min_daylight_hours:
            continue
        snapshot_kwh = sum(valid.values()) / 1000.0
        ratio = snapshot_kwh / measured
        if not 0.65 <= ratio <= 1.35:
            continue
        eligible.append((day, valid, measured, ratio))
    if len(eligible) < min_days:
        return None
    # An upper-tail baseline needs at least five independent days. Within a
    # 21-day window, matching weekdays provide at most three observations;
    # selecting them would systematically trigger the insufficient-evidence
    # fallback. Use all eligible completed days instead.
    selected = eligible
    # Normalize each day to its measured total, preserving the observed hourly
    # profile. This uses the authoritative total exactly once, not an additional
    # learned bias correction.
    watts = []
    for _, hours, measured, _ in selected:
        # An absent hour is missing evidence, not measured zero output.
        # Otherwise sparse recorder days depress the hourly median and
        # systematically underforecast at affected hours.
        if hour not in hours:
            continue
        total = sum(hours.values()) / 1000.0
        if total > 0:
            watts.append(hours[hour] * measured / total)
    if len(watts) < min_days:
        return None
    # The median is a typical *weather-affected* historical PV hour.
    # Applying forecast cloud attenuation to it double-counts historical
    # cloudiness. A clear-sky proxy requires a sufficiently populated
    # upper-tail sample; with fewer than five days, refuse this V2 baseline
    # and let the caller use the existing Legacy fallback.
    if len(watts) < max(min_days, 5):
        return None
    ordered = sorted(watts)
    # Nearest-rank 80th percentile (observed sample, not extrapolation).
    # Retain the conservative historical ceiling and existing cloud guards.
    upper = ordered[math.ceil(0.8 * len(ordered)) - 1]
    return {
        "equal_day_w": round(upper, 1),
        "historical_days": len(watts),
        "method": "recent_canonical_daily_energy_reconciled_upper_envelope_p80",
    }
