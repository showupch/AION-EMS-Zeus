"""Read-only local-hour GHI/PV transfer candidate with chronological holdout evidence.

No Home Assistant imports, no device control, and no production forecast changes.
Horizontal irradiance is NOT plane-of-array irradiance. The model is experimental.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo


def _training(samples, target_date, threshold):
    by_day = defaultdict(list)
    for stamp, ghi, pv in samples:
        try:
            ghi, pv = float(ghi), float(pv)
            if (not isinstance(stamp, datetime) or stamp.tzinfo is None
                    or not isfinite(ghi) or not isfinite(pv)
                    or not threshold <= ghi <= 1500 or not 0 <= pv <= 100000
                    or stamp.date() >= target_date):
                continue
            by_day[stamp.date()].append((stamp.hour, ghi, pv))
        except (TypeError, ValueError, OverflowError):
            continue
    return {day: rows for day, rows in by_day.items() if len(rows) >= 4}


def _predict(hour, radiation, by_day, *, min_days=3):
    # Hourly transfer ratios are weighted by irradiance, to limit low-light noise.
    # Each day contributes at most one estimate per hour to avoid oversampling
    # rapidly updating devices or favoring a particular recording cadence.
    candidates = []
    for day, rows in by_day.items():
        nearby = [(irr, pv) for h, irr, pv in rows if abs(h-hour) <= 1]
        if nearby:
            denominator = sum(irr*irr for irr, _ in nearby)
            if denominator > 0:
                candidates.append((day, sum(irr*pv for irr, pv in nearby)/denominator))
    if len(candidates) < min_days:
        return None, len(candidates)
    return max(0.0, radiation * median(slope for _, slope in candidates)), len(candidates)


def estimate_hourly_radiation_shadow(samples, forecast_hours, *, target_date,
                                     local_timezone="UTC", min_irradiance_w_m2=30,
                                     min_training_days=5, min_training_buckets=40):
    """Return candidate and leave-one-completed-day-out evidence, never controls."""
    base = {"status": "insufficient_evidence", "production_applied": False,
            "control_permission": False, "frozen": False,
            "model": "local_hour_median_ghi_pv_transfer_shadow",
            "warning": "GHI is not plane-of-array radiation; hourly ratios are experimental. Forecast energy covers a UTC calendar day, not necessarily a local calendar day."}
    try:
        target = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        tz = ZoneInfo(local_timezone)
        if not isinstance(target, date):
            raise ValueError("invalid target")
    except (ValueError, TypeError, KeyError):
        return {**base, "status": "invalid_target"}
    by_day = _training(samples, target, min_irradiance_w_m2)
    count = sum(map(len, by_day.values()))
    result = {**base, "training_days": len(by_day), "training_buckets": count,
              "minimum_training_days": min_training_days,
              "minimum_training_buckets": min_training_buckets,
              "training_dates": [d.isoformat() for d in sorted(by_day)],
              "threshold_w_m2": min_irradiance_w_m2, "forecast_window": "00:00-24:00 UTC"}
    if len(by_day) < min_training_days or count < min_training_buckets:
        return result
    # Holdout is strictly earlier-day-only, preventing future observations
    # from entering the estimate for any evaluated historical day.
    holdout = []
    for day in sorted(by_day):
        prior = {d: rows for d, rows in by_day.items() if d < day}
        if len(prior) < min_training_days:
            continue
        predictions = []
        actuals = []
        for hour, ghi, pv in by_day[day]:
            estimate, _ = _predict(hour, ghi, prior)
            if estimate is not None:
                predictions.append(estimate)
                actuals.append(pv)
        if len(predictions) >= 4:
            holdout.append({"date": day.isoformat(), "matched_intervals": len(predictions),
                            "mean_absolute_power_error_w": round(sum(abs(a-p) for a,p in zip(actuals,predictions))/len(predictions),1),
                            "mean_signed_power_error_w": round(sum(p-a for a,p in zip(actuals,predictions))/len(predictions),1)})
    result["chronological_holdout"] = {"days": len(holdout), "results": holdout,
                                        "note": "Matched irradiance/PV intervals only; not frozen day-ahead kWh scores."}
    output = []
    try:
        for row in forecast_hours:
            stamp = datetime.fromisoformat(str(row["time_utc"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("naive forecast timestamp")
            local = stamp.astimezone(tz)
            ghi = float(row["shortwave_radiation_w_m2"])
            if not isfinite(ghi) or not 0 <= ghi <= 1500 or stamp.date() != target:
                raise ValueError("forecast hour outside target or invalid radiation")
            if ghi < min_irradiance_w_m2:
                power, n = 0.0, 0
            else:
                power, n = _predict(local.hour, ghi, by_day)
            output.append({"time_utc": stamp.isoformat(), "local_hour": local.hour,
                           "radiation_w_m2": round(ghi,2),
                           "estimated_pv_w": round(power,1) if power is not None else None,
                           "independent_training_days_for_hour": n})
    except (ValueError, TypeError, KeyError, OverflowError):
        return {**result, "status": "invalid_forecast_hours"}
    if len(output) != 24 or len({r["time_utc"] for r in output}) != 24:
        return {**result, "status": "incomplete_forecast_hours"}
    missing = [r["local_hour"] for r in output if r["estimated_pv_w"] is None]
    if missing:
        return {**result, "status": "insufficient_hourly_coverage", "missing_local_hours": missing,
                "hours": output}
    return {**result, "status": "experimental_available",
            "candidate_kwh": round(sum(r["estimated_pv_w"] for r in output)/1000,3),
            "hours": output}
