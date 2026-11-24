"""V3 local irradiance / PV measurement bridge (read-only evidence)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from math import isfinite
from statistics import median


def _mapped(core):
    station = ((core.registry.data.get("sources") or {}).get("local_weather_station") or {})
    entities = station.get("entities") or {}
    mapping = (getattr(getattr(core, "energy_mapping", None), "mappings", {}) or {})
    if not mapping:
        mapping = (getattr(getattr(getattr(core, "energy_flow", None), "mapping", None), "mappings", {}) or {})
    return station, str(entities.get("solar_radiation") or ""), str(entities.get("illuminance") or ""), str(mapping.get("solar_power") or "")


def local_irradiance_evidence(core):
    """Summarize paired on-site sunlight/PV data already captured by Zeus.

    No learned coefficient is applied to forecasts. Sparse hourly snapshots
    are not assumed to be interval averages or historical forecasts.
    """
    station, irradiance_id, lux_id, pv_id = _mapped(core)
    result = {
        "status": "unavailable", "source": "existing_zeus_weather_history",
        "irradiance_entity": irradiance_id or None,
        "illuminance_entity": lux_id or None,
        "pv_power_entity": pv_id or None,
        "hourly_pairs": 0, "usable_daylight_pairs": 0,
        "morning_pairs": 0, "afternoon_pairs": 0,
        "morning_median_pv_w_per_irradiance_w_m2": None,
        "afternoon_median_pv_w_per_irradiance_w_m2": None,
        "forecast_correction_applied": False,
        "production_applied": False, "control_permission": False,
        "accuracy_validated": False,
        "note": "On-site observed irradiance/PV pairs are calibration evidence, not future irradiance or a validated forecast correction.",
    }
    if not station.get("enabled") or not irradiance_id or not pv_id or irradiance_id == pv_id:
        result["status"] = "missing_independent_mappings"
        return result
    history = getattr(core, "weather_history", None)
    records = (getattr(history, "data", {}) or {}).get("verified_hourly_irradiance_pv") or {}
    if not isinstance(records, dict):
        result["status"] = "invalid_history"
        return result
    morning, afternoon = [], []
    now = datetime.now(timezone.utc)
    for stamp, sample in records.items():
        if not isinstance(sample, dict):
            continue
        if sample.get("irradiance_entity") != irradiance_id or sample.get("pv_entity") != pv_id:
            continue
        try:
            instant = datetime.fromisoformat(stamp)
            if instant.tzinfo is None or instant > now or now-instant > timedelta(days=120):
                continue
            irradiance = float(sample["irradiance_w_m2"])
            pv = float(sample["pv_w"])
            if not all(map(isfinite, (irradiance, pv))) or not (0 <= irradiance <= 1500 and 0 <= pv <= 100000):
                continue
        except (ValueError, TypeError, OverflowError, KeyError):
            continue
        result["hourly_pairs"] += 1
        if irradiance < 50:
            continue
        # Use local civil time (including DST), not UTC noon.
        from homeassistant.util import dt as dt_util
        local_hour = dt_util.as_local(instant).hour
        (morning if local_hour < 12 else afternoon).append(pv/irradiance)
    result["morning_pairs"] = len(morning)
    result["afternoon_pairs"] = len(afternoon)
    result["usable_daylight_pairs"] = len(morning) + len(afternoon)
    if morning:
        result["morning_median_pv_w_per_irradiance_w_m2"] = round(median(morning), 3)
    if afternoon:
        result["afternoon_median_pv_w_per_irradiance_w_m2"] = round(median(afternoon), 3)
    result["status"] = "paired_measurements_available" if result["usable_daylight_pairs"] else "awaiting_daylight_pairs"
    result["calibration_eligible"] = len(morning) >= 10 and len(afternoon) >= 10
    return result


def local_irradiance_walkforward(core):
    """Chronological, day-separated test of observed irradiance-to-PV power.

    This evaluates an *observation mapping*, not a day-ahead forecast. Training
    always precedes the test day. Nothing is written to recorder or forecast.
    """
    station, irradiance_id, _, pv_id = _mapped(core)
    out = {
        "status": "insufficient_chronological_evidence",
        "method": "expanding_window_prior_days_only",
        "comparison": "global_irradiance_ratio_vs_morning_afternoon_ratios",
        "source": "existing_zeus_weather_history",
        "total_usable_samples": 0, "distinct_days": 0,
        "evaluated_days": 0, "evaluated_samples": 0,
        "global_mae_w": None, "split_mae_w": None,
        "global_mean_signed_error_w": None, "split_mean_signed_error_w": None,
        "improvement_percent": None,
        "minimum_prior_days": 5,
        "minimum_prior_samples_per_period": 10,
        "minimum_evaluated_days": 3,
        "forecast_correction_applied": False,
        "production_applied": False, "control_permission": False,
        "accuracy_validated": False,
        "note": "Historical measured power holdout only; no future irradiance forecast or day-ahead kWh validation.",
    }
    if not station.get("enabled") or not irradiance_id or not pv_id:
        out["status"] = "missing_independent_mappings"
        return out
    records = (getattr(getattr(core, "weather_history", None), "data", {}) or {}).get("verified_hourly_irradiance_pv") or {}
    if not isinstance(records, dict):
        out["status"] = "invalid_history"
        return out
    from homeassistant.util import dt as dt_util
    now = datetime.now(timezone.utc)
    days = {}
    for stamp, sample in records.items():
        if not isinstance(sample, dict) or sample.get("irradiance_entity") != irradiance_id or sample.get("pv_entity") != pv_id:
            continue
        try:
            instant = datetime.fromisoformat(stamp)
            if instant.tzinfo is None or instant > now or now-instant > timedelta(days=120):
                continue
            irr, pv = float(sample["irradiance_w_m2"]), float(sample["pv_w"])
            if not (isfinite(irr) and isfinite(pv) and 50 <= irr <= 1500 and 0 <= pv <= 100000):
                continue
        except (ValueError, TypeError, OverflowError, KeyError):
            continue
        local = dt_util.as_local(instant)
        days.setdefault(local.date().isoformat(), []).append((irr, pv, "morning" if local.hour < 12 else "afternoon"))
    out["total_usable_samples"] = sum(map(len, days.values()))
    out["distinct_days"] = len(days)
    prior = []
    prior_dates = []
    global_errors, split_errors = [], []
    evaluated = 0
    for date in sorted(days):
        morning = [pv/irr for irr,pv,period in prior if period == "morning"]
        afternoon = [pv/irr for irr,pv,period in prior if period == "afternoon"]
        if len(prior) >= 20 and len(morning) >= 10 and len(afternoon) >= 10:
            global_ratio = median([pv/irr for irr,pv,_ in prior])
            ratios = {"morning": median(morning), "afternoon": median(afternoon)}
            if len(prior_dates) >= 5:
                for irr, pv, period in days[date]:
                    global_errors.append(global_ratio*irr-pv)
                    split_errors.append(ratios[period]*irr-pv)
                evaluated += 1
        prior.extend(days[date])
        prior_dates.append(date)
    out["evaluated_days"] = evaluated
    out["evaluated_samples"] = len(global_errors)
    if evaluated < 3 or not global_errors:
        return out
    global_mae = sum(abs(e) for e in global_errors)/len(global_errors)
    split_mae = sum(abs(e) for e in split_errors)/len(split_errors)
    out.update({
        "status": "retrospective_observation_comparison_available",
        "global_mae_w": round(global_mae, 2),
        "split_mae_w": round(split_mae, 2),
        "global_mean_signed_error_w": round(sum(global_errors)/len(global_errors), 2),
        "split_mean_signed_error_w": round(sum(split_errors)/len(split_errors), 2),
        "improvement_percent": round(100*(global_mae-split_mae)/global_mae, 2) if global_mae else None,
    })
    return out


async def recorder_irradiance_walkforward(core):
    """Read-only Recorder holdout with clearly separated exploratory evidence.

    Preliminary metrics never satisfy the >=3-day validation gate and are
    never used by the production forecast.
    """
    from homeassistant.util import dt as dt_util
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    evidence = await core.weather_history.async_preview_recorder_irradiance_pairs(
        end - timedelta(days=6), end)
    out = {
        "status": evidence.get("status", "unavailable"),
        "source": "recorder_time_weighted_state_history",
        "audit_window_days": 6,
        "audit_window_limited": True,
        "recorder_pair_count": evidence.get("pair_count", 0),
        "raw_state_counts": evidence.get("raw_state_counts"),
        "verified_current_units": evidence.get("verified_current_units"),
        "effective_units": evidence.get("effective_units"),
        "unit_verification": evidence.get("unit_verification"),
        "recorder_explicit_units": evidence.get("recorder_explicit_units"),
        "recorded_units": evidence.get("recorded_units"),
        "diagnostic_note": evidence.get("diagnostic_note"),
        "reader_error_type": evidence.get("error_type"),
        "rejected_hours": evidence.get("rejected_hours"),
        "daily_audit": (evidence.get("daily_audit") or [])[-10:],
        "hourly_exclusion_audit": {
            "method": "local_civil_hour_solar_geometry_not_assumed",
            "classification": "night_window_proxy_not_solar_position",
            "night_window_local": "18:00-06:59",
            "night_window_rejected": 0,
            "day_window_rejected": 0,
            "day_window_missing_pv": 0,
            "day_window_missing_irradiance": 0,
            "day_window_both_missing": 0,
            "note": "Night window is a conservative clock-time proxy, not proof of darkness. Original quality gates remain unchanged.",
        },
        "evaluated_days": 0, "evaluated_samples": 0,
        "minimum_evaluated_days": 3,
        "global_mae_w": None, "split_mae_w": None,
        "improvement_percent": None,
        "preliminary": {
            "status": "not_available", "validated": False,
            "evaluated_days": 0, "evaluated_samples": 0,
            "global_mae_w": None, "split_mae_w": None,
            "global_mean_signed_error_w": None,
            "split_mean_signed_error_w": None,
            "improvement_percent": None,
            "morning": {"samples": 0, "global_mae_w": None, "split_mae_w": None},
            "afternoon": {"samples": 0, "global_mae_w": None, "split_mae_w": None},
            "note": "Exploratory observation mapping, not day-ahead forecast accuracy or production authorization.",
        },
        "production_applied": False, "control_permission": False,
        "accuracy_validated": False,
    }
    if evidence.get("status") != "ready":
        return out

    # This audit is descriptive only; do not change the reader's pairing gates.
    exclusion = out["hourly_exclusion_audit"]
    for hour in evidence.get("hourly_validation") or []:
        if hour.get("paired"):
            continue
        try:
            local_hour = dt_util.as_local(datetime.fromisoformat(hour["hour_utc"])).hour
        except (ValueError, TypeError, KeyError):
            continue
        if local_hour >= 18 or local_hour < 7:
            exclusion["night_window_rejected"] += 1
        else:
            exclusion["day_window_rejected"] += 1
            pv_ok = hour.get("pv_status") == "verified_hourly_average"
            irr_ok = hour.get("irradiance_status") == "verified_hourly_average"
            if not pv_ok and not irr_ok:
                exclusion["day_window_both_missing"] += 1
            elif not pv_ok:
                exclusion["day_window_missing_pv"] += 1
            elif not irr_ok:
                exclusion["day_window_missing_irradiance"] += 1

    days = {}
    for pair in evidence.get("pairs") or []:
        try:
            instant = datetime.fromisoformat(pair["hour_utc"])
            irr = float(pair["irradiance_mean_w_m2"])
            pv = float(pair["pv_mean_w"])
            if not (isfinite(irr) and isfinite(pv) and 50 <= irr <= 1500 and 0 <= pv <= 100000):
                continue
            local = dt_util.as_local(instant)
            period = "morning" if local.hour < 12 else "afternoon"
            days.setdefault(local.date().isoformat(), []).append((irr, pv, period))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    out["usable_daylight_pairs"] = sum(len(rows) for rows in days.values())
    out["distinct_days"] = len(days)
    prior, prior_dates, errors = [], [], []
    for date in sorted(days):
        morning = [pv / irr for irr, pv, period in prior if period == "morning"]
        afternoon = [pv / irr for irr, pv, period in prior if period == "afternoon"]
        if len(prior_dates) >= 5 and len(morning) >= 10 and len(afternoon) >= 10:
            global_ratio = median([pv / irr for irr, pv, _ in prior])
            split = {"morning": median(morning), "afternoon": median(afternoon)}
            for irr, pv, period in days[date]:
                errors.append((period, global_ratio * irr - pv, split[period] * irr - pv))
            out["evaluated_days"] += 1
        prior.extend(days[date])
        prior_dates.append(date)
    out["evaluated_samples"] = len(errors)
    prelim = out["preliminary"]
    prelim["evaluated_days"] = out["evaluated_days"]
    prelim["evaluated_samples"] = len(errors)
    if errors:
        def mean_abs(vals):
            return round(sum(abs(v) for v in vals) / len(vals), 2) if vals else None
        def mean_signed(vals):
            return round(sum(vals) / len(vals), 2) if vals else None
        ge = [row[1] for row in errors]
        se = [row[2] for row in errors]
        g = mean_abs(ge)
        sp = mean_abs(se)
        prelim.update({
            "status": "exploratory_holdout_available",
            "global_mae_w": g, "split_mae_w": sp,
            "global_mean_signed_error_w": mean_signed(ge),
            "split_mean_signed_error_w": mean_signed(se),
            "improvement_percent": round(100 * (g - sp) / g, 2) if g else None,
        })
        for period in ("morning", "afternoon"):
            rows = [row for row in errors if row[0] == period]
            prelim[period] = {
                "samples": len(rows),
                "global_mae_w": mean_abs([row[1] for row in rows]),
                "split_mae_w": mean_abs([row[2] for row in rows]),
            }
    if out["evaluated_days"] < out["minimum_evaluated_days"] or not errors:
        out["status"] = "insufficient_chronological_evidence"
        return out
    out.update({
        "status": "retrospective_observation_comparison_available",
        "global_mae_w": prelim["global_mae_w"],
        "split_mae_w": prelim["split_mae_w"],
        "improvement_percent": prelim["improvement_percent"],
    })
    return out
