"""Evidence-gated weather-conditioned PV baseline. Not wired to production.

Consumes real hourly averaged irradiance/PV observations. A target-hour
irradiance forecast is required; cloud percentages are not irradiance.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo


def candidate_from_irradiance(pairs, *, target_hour_utc, forecast_irradiance_w_m2,
                              capacity_w, site_timezone, min_days=5, min_samples=5,
                              max_age_days=45):
    """Conservative empirical conversion, requiring independent historical days.

    Returns a candidate only if a comparable local-hour cohort is supported.
    Ratios are descriptive observational evidence, not validated predictions.
    """
    result = {"status": "insufficient_evidence", "candidate_w": None,
              "sample_count": 0, "training_days": 0,
              "method": "observed_hourly_pv_to_irradiance_local_hour_cohort_median",
              "production_applied": False, "control_permission": False}
    if not isinstance(target_hour_utc, datetime) or target_hour_utc.tzinfo is None:
        result["reason"] = "invalid_target_hour"; return result
    try:
        irradiance = float(forecast_irradiance_w_m2)
        capacity = float(capacity_w)
    except (ValueError, TypeError):
        result["reason"] = "missing_forecast_irradiance_or_capacity"; return result
    if not all(isfinite(v) for v in (irradiance, capacity)) or not 0 <= irradiance <= 1500 or capacity <= 0:
        result["reason"] = "invalid_forecast_irradiance_or_capacity"; return result
    try:
        tz = ZoneInfo(site_timezone)
    except (TypeError, KeyError, ValueError):
        result["reason"] = "invalid_site_timezone"; return result
    if min_samples < min_days or min_days < 1:
        result["reason"] = "invalid_evidence_thresholds"; return result
    target = target_hour_utc.astimezone(timezone.utc)
    local_target = target.astimezone(tz)
    # Use installation-local solar hour and calendar date; UTC hour is not
    # comparable across daylight-saving changes.
    by_day = defaultdict(list)
    for row in pairs:
        try:
            stamp = datetime.fromisoformat(row["hour_utc"]).astimezone(timezone.utc)
            pv = float(row["pv_mean_w"])
            rad = float(row["irradiance_mean_w_m2"])
            pv_cov = float(row["pv_coverage"])
            rad_cov = float(row["irradiance_coverage"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        age = target - stamp
        if not timedelta(0) < age <= timedelta(days=max_age_days): continue
        local_stamp = stamp.astimezone(tz)
        if local_stamp.hour != local_target.hour: continue
        if not all(isfinite(v) for v in (pv, rad, pv_cov, rad_cov)): continue
        if not (0 <= pv <= capacity * 1.05 and 50 <= rad <= 1200): continue
        if min(pv_cov, rad_cov) < 0.8: continue
        by_day[local_stamp.date()].append(pv / rad)
    ratios = [median(values) for values in by_day.values()]
    result["sample_count"] = sum(map(len, by_day.values()))
    result["training_days"] = len(by_day)
    result["site_timezone"] = site_timezone
    result["local_hour"] = local_target.hour
    if len(by_day) < min_days or result["sample_count"] < min_samples:
        result["reason"] = "insufficient_independent_days_or_samples"
        return result
    # No arbitrary uplift: observed median conversion, physical capacity cap.
    result.update(status="candidate_available", candidate_w=round(min(capacity, irradiance * median(ratios)), 1), reason=None)
    return result
