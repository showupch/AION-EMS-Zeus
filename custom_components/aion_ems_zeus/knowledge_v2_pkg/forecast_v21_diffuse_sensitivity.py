"""Read-only diffuse sensitivity from immutable pre-day frozen weather inputs.

This is NOT a newly frozen forecast. It is a retrospective scenario based on
frozen evidence and must not enter forward MAE or production selection.
"""
from __future__ import annotations
from math import isfinite


def diffuse_sensitivity(clear_sky: dict, frozen_hourly: object, *,
                        diffuse_share: float = 0.15, diffuse_floor: float = 0.12,
                        performance_ratio: float = 0.8) -> dict:
    base = {"status": "unavailable", "candidate_kwh": None,
            "production_applied": False, "control_permission": False,
            "accuracy_validated": False, "frozen_prediction": False,
            "comparison_eligible": False,
            "model": "diffuse_floor_frozen_weather_sensitivity"}
    if clear_sky.get("status") != "ready":
        return {**base, "status": "missing_clear_sky_geometry"}
    if not isinstance(frozen_hourly, list):
        return {**base, "status": "missing_frozen_weather"}
    try:
        if not all(isfinite(float(v)) for v in (diffuse_share, diffuse_floor, performance_ratio)):
            return {**base, "status": "invalid_parameters"}
        if not (0 <= diffuse_share <= 1 and 0 <= diffuse_floor <= 1 and 0 < performance_ratio <= 1):
            return {**base, "status": "invalid_parameters"}
        hours = clear_sky["hourly"]
        by_hour = {r["hour_local"]: r for r in frozen_hourly if isinstance(r, dict) and isinstance(r.get("hour_local"), str)}
        if len(by_hour) != len(hours) or len(by_hour) != len(frozen_hourly):
            return {**base, "status": "incomplete_frozen_weather"}
        rows = []
        for hour in hours:
            frozen = by_hour.get(hour["hour_local"])
            if frozen is None:
                return {**base, "status": "unmatched_frozen_weather"}
            ghi = float(hour["clear_sky_ghi_w_m2"])
            clear_poa = float(hour["clear_sky_weighted_poa_w_m2_kwp"])
            if not all(isfinite(v) and v >= 0 for v in (ghi, clear_poa)):
                return {**base, "status": "invalid_clear_sky_evidence"}
            if ghi > 0:
                factor = frozen.get("weather_attenuation_factor")
                if factor is None:
                    return {**base, "status": "missing_hourly_attenuation"}
                factor = float(factor)
                if not isfinite(factor) or not 0 <= factor <= 1:
                    return {**base, "status": "invalid_hourly_attenuation"}
            else:
                factor = 0.0
            # The existing POA proxy already includes assumed diffuse energy.
            # Split it heuristically to avoid adding diffuse energy twice.
            direct = clear_poa * (1 - diffuse_share)
            diffuse = clear_poa * diffuse_share
            diffuse_factor = diffuse_floor + (1 - diffuse_floor) * factor
            energy = (direct * factor + diffuse * diffuse_factor) * performance_ratio / 1000
            rows.append({"hour_local": hour["hour_local"],
                         "scenario_kwh": round(energy, 5),
                         "frozen_weather_factor": round(factor, 5)})
        return {**base, "status": "scenario_available",
                "candidate_kwh": round(sum(r["scenario_kwh"] for r in rows), 3),
                "hourly": rows,
                "assumptions": {"diffuse_share": diffuse_share, "diffuse_floor": diffuse_floor,
                                "performance_ratio": performance_ratio},
                "note": "Read-only hypothesis calculated from frozen inputs; not a pre-day frozen forecast"}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**base, "status": "invalid_frozen_evidence"}
