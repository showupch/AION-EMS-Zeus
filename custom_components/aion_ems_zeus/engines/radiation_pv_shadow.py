"""Read-only forecast radiation to PV experiment. Never used by control.

Uses synchronized historical station radiation and PV power, with a morning /
afternoon transfer slope. Requires several independent days and adequate sampling.
The result is an experimental estimate, NOT frozen pre-day forecast evidence.
"""
from collections import defaultdict
from datetime import datetime
from math import isfinite


def estimate_radiation_pv(samples, forecast_hours, *, min_days=3, min_buckets=24, local_timezone="UTC", min_irradiance_w_m2=30):
    """samples: (aware local datetime, measured irradiance W/m2, PV W)."""
    groups = defaultdict(list)
    for stamp, irr, pv in samples:
        try:
            irr, pv = float(irr), float(pv)
            if (not isinstance(stamp, datetime) or stamp.tzinfo is None or
                not isfinite(irr) or not isfinite(pv) or
                not min_irradiance_w_m2 <= irr <= 1500 or not 0 <= pv <= 100000):
                continue
            groups[stamp.date()].append((stamp.hour, irr, pv))
        except (TypeError, ValueError, OverflowError):
            continue
    eligible = {day: rows for day, rows in groups.items() if len(rows) >= 4}
    rows = [row for day in eligible.values() for row in day]
    base = {"status":"insufficient_evidence", "training_days":len(eligible),
            "training_buckets":len(rows), "raw_daylight_buckets":sum(map(len,groups.values())),
            "raw_daylight_days":len(groups), "excluded_sparse_days":sum(len(v)<4 for v in groups.values()),
            "training_threshold_w_m2":min_irradiance_w_m2, "production_applied":False,
            "control_permission":False, "frozen":False,
            "note":"Experimental GHI-to-PV conversion; horizontal radiation is not plane-of-array radiation. Not a frozen forecast score."}
    if len(eligible) < min_days or len(rows) < min_buckets:
        return base
    # Split by local solar-day half to avoid treating east and west arrays identically.
    slopes = {}
    for label, segment in (("morning",[r for r in rows if r[0] < 12]),
                           ("afternoon",[r for r in rows if r[0] >= 12])):
        if len(segment) < 8 or len({day for day, samples_day in eligible.items()
                                   if any((hour < 12) == (label == "morning") for hour, _, _ in samples_day)}) < 2:
            return {**base, "reason":"insufficient_morning_or_afternoon_evidence"}
        denominator = sum(irr*irr for _,irr,_ in segment)
        if denominator <= 0: return base
        slopes[label] = max(0.0, sum(irr*pv for _,irr,pv in segment)/denominator)
    out=[]
    for row in forecast_hours:
        try:
            stamp=datetime.fromisoformat(str(row["time_utc"]).replace("Z","+00:00"))
            if stamp.tzinfo is None: return {**base,"reason":"naive_forecast_timestamp"}
            # Caller supplies local time conversion; UTC hour is not Swiss local hour.
            from zoneinfo import ZoneInfo
            local_hour=stamp.astimezone(ZoneInfo(local_timezone)).hour
            radiation=float(row["shortwave_radiation_w_m2"])
            if not isfinite(radiation) or not 0 <= radiation <= 1500: raise ValueError
            power=max(0.0,slopes["morning" if local_hour < 12 else "afternoon"]*radiation)
            out.append({"time_utc":stamp.isoformat(),"radiation_w_m2":round(radiation,2),
                        "estimated_pv_w":round(power,1)})
        except (KeyError,TypeError,ValueError,OverflowError):
            return {**base,"reason":"invalid_forecast_hour"}
    if len(out)!=24 or len({r["time_utc"] for r in out})!=24:
        return {**base,"reason":"incomplete_hourly_forecast"}
    return {**base,"status":"experimental_available", "candidate_kwh":round(sum(r["estimated_pv_w"] for r in out)/1000,3),
            "hours":out,"slope_morning_w_per_wm2":round(slopes["morning"],3),
            "slope_afternoon_w_per_wm2":round(slopes["afternoon"],3)}


def threshold_evidence_sensitivity(samples, thresholds=(10, 20, 30, 50), min_intervals_per_day=4):
    """Read-only paired-interval coverage audit; does not modify training policy."""
    results = []
    for threshold in thresholds:
        by_day = defaultdict(list)
        for stamp, irradiance, pv_power in samples:
            try:
                irradiance = float(irradiance)
                pv_power = float(pv_power)
                if (not isinstance(stamp, datetime) or stamp.tzinfo is None
                    or not isfinite(irradiance) or not isfinite(pv_power)
                    or not threshold <= irradiance <= 1500
                    or not 0 <= pv_power <= 100000):
                    continue
                by_day[stamp.date().isoformat()].append((stamp.hour, irradiance, pv_power))
            except (TypeError, ValueError, OverflowError):
                continue
        eligible = {day: rows for day, rows in by_day.items()
                    if len(rows) >= min_intervals_per_day}
        eligible_rows = [row for rows in eligible.values() for row in rows]
        morning = sum(hour < 12 for hour, _, _ in eligible_rows)
        afternoon = len(eligible_rows) - morning
        results.append({
            "threshold_w_m2": threshold,
            "raw_daylight_buckets": sum(map(len, by_day.values())),
            "raw_daylight_days": len(by_day),
            "eligible_training_days": len(eligible),
            "eligible_training_buckets": len(eligible_rows),
            "morning_buckets": morning,
            "afternoon_buckets": afternoon,
            "daylight_intervals_by_day": {day: len(rows) for day, rows in sorted(by_day.items())},
            "passes_count_gates": (len(eligible) >= 3 and len(eligible_rows) >= 24
                                   and morning >= 8 and afternoon >= 8),
        })
    return {"status": "diagnostic_only", "thresholds": results,
            "selected_training_threshold_w_m2": 30,
            "training_policy_changed": False, "production_applied": False}
