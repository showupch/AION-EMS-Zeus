"""Read-only per-array solar geometry evidence for Forecast V2.1.

Independent implementation of the standard solar-position approximation.
Outputs *relative clear-sky exposure*, not a weather-adjusted energy forecast.
No production forecast or controls consume this module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import acos, asin, atan2, cos, degrees, exp, isfinite, pi, radians, sin
from zoneinfo import ZoneInfo


def geometry_shadow(arrays, target_date, latitude, longitude, timezone_name):
    """Return hourly relative plane-of-array direct exposure for configured arrays."""
    result = {"status": "unavailable", "target_date": str(target_date),
              "model": "relative_direct_beam_geometry_only", "production_applied": False,
              "control_permission": False, "weather_applied": False,
              "forecast_kwh": None, "note": "Clear-sky relative geometry only; not a weather-adjusted PV forecast."}
    if not isinstance(arrays, list) or not arrays:
        return {**result, "status": "not_configured"}
    try:
        day = date.fromisoformat(str(target_date))
        lat, lon = float(latitude), float(longitude)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return {**result, "status": "invalid_location"}
        zone = ZoneInfo(str(timezone_name))
        prepared = []
        for entry in arrays[:12]:
            capacity = float(entry["capacity_kwp"])
            azimuth = float(entry["azimuth_deg"])
            tilt = float(entry["tilt_deg"])
            if not all(isfinite(v) for v in (capacity, azimuth, tilt)) or not (0 < capacity <= 1000 and 0 <= azimuth < 360 and 0 <= tilt <= 90):
                return {**result, "status": "invalid_array_geometry"}
            prepared.append({"name": str(entry.get("name") or "Array")[:80], "capacity_kwp": capacity,
                             "azimuth_deg": azimuth, "tilt_deg": tilt, "hourly": [], "exposure_sum": 0.0})
        # Iterate real UTC instants between local midnights; DST days may have 23/25 hours.
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        t = start
        hourly = []
        while t < end:
            mid = t + timedelta(minutes=30)
            local = mid.astimezone(zone)
            n = local.timetuple().tm_yday
            hour = local.hour + local.minute / 60.0
            # NOAA fractional-year approximation and equation of time.
            gamma = 2*pi/365 * (n-1+(hour-12)/24)
            eq = 229.18*(0.000075+0.001868*cos(gamma)-0.032077*sin(gamma)-0.014615*cos(2*gamma)-0.040849*sin(2*gamma))
            decl = (0.006918-0.399912*cos(gamma)+0.070257*sin(gamma)-0.006758*cos(2*gamma)+0.000907*sin(2*gamma)-0.002697*cos(3*gamma)+0.00148*sin(3*gamma))
            utc = mid.astimezone(timezone.utc)
            minutes_utc = utc.hour*60 + utc.minute
            ha = radians((minutes_utc+eq+4*lon)/4-180)
            phi = radians(lat)
            east = -cos(decl)*sin(ha)
            north = cos(phi)*sin(decl)-sin(phi)*cos(decl)*cos(ha)
            up = sin(phi)*sin(decl)+cos(phi)*cos(decl)*cos(ha)
            total = 0.0
            values = []
            for a in prepared:
                slope, facing = radians(a["tilt_deg"]), radians(a["azimuth_deg"])
                incident = max(0.0, up*cos(slope) + sin(slope)*(north*cos(facing)+east*sin(facing))) if up > 0 else 0.0
                relative = round(incident*a["capacity_kwp"], 5)
                a["exposure_sum"] += relative
                a["hourly"].append(relative)
                values.append(relative)
                total += relative
            hourly.append({"hour_local": local.strftime("%Y-%m-%d %H:%M %z"), "array_exposure_kwp_equivalent": values,
                           "total_exposure_kwp_equivalent": round(total, 5)})
            t += timedelta(hours=1)
        denominator = sum(a["exposure_sum"] for a in prepared)
        for a in prepared:
            a["relative_day_share_percent"] = round(100*a["exposure_sum"]/denominator, 2) if denominator else None
            a["exposure_sum"] = round(a["exposure_sum"], 4)
            a.pop("hourly")
        return {**result, "status": "ready" if denominator else "no_solar_exposure", "arrays": prepared,
                "total_capacity_kwp": round(sum(a["capacity_kwp"] for a in prepared), 4),
                "hour_count": len(hourly), "hourly": hourly,
                "limitations": ["No weather irradiance, clouds, shading, temperature, inverter clipping, or measured calibration",
                                "kWp-equivalent exposure is a relative weighting, NOT measured or forecast kWh"],
                "geometry_only": True}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {**result, "status": "invalid_configuration", "reason": type(exc).__name__}


# Weather-aware diagnostic is deliberately separate from the existing geometry
# and V2 forecast. Cloud cover is a coarse attenuation proxy, not measured
# irradiance or a calibrated PV power/energy prediction.
def weather_geometry_shadow(geometry, weather_summary, weather_factor_for=None):
    """Attenuate each geometry hour only with matching timestamped weather evidence.

    Do not infer missing weather, extrapolate daily weather into hourly evidence,
    or produce forecast kWh without measured calibration and irradiance.
    """
    output = {
        "status": "unavailable", "model": "per_array_weather_attenuated_exposure_proxy",
        "production_applied": False, "control_permission": False,
        "forecast_kwh": None, "calibrated": False, "irradiance_available": False,
        "note": "Relative geometry multiplied by weather condition/cloud proxy; not kWh or irradiance.",
    }
    if not isinstance(geometry, dict) or geometry.get("status") != "ready":
        return {**output, "status": "geometry_unavailable"}
    if not isinstance(weather_summary, dict) or weather_summary.get("forecast_granularity") != "hourly":
        return {**output, "status": "hourly_weather_unavailable", "reason": "No hourly forecast; daily evidence is not expanded to hours"}
    from math import isfinite
    raw_rows = weather_summary.get("forecast") or []
    if not isinstance(raw_rows, list):
        return {**output, "status": "hourly_weather_unavailable"}
    by_utc = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("datetime") or row.get("time") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                continue
            cloud = row.get("cloud_coverage", row.get("cloud_cover", row.get("cloudiness")))
            if cloud is not None:
                cloud = float(cloud)
                if not isfinite(cloud) or not 0 <= cloud <= 100:
                    continue
            condition = str(row.get("condition") or "").strip().lower()
            if cloud is None and not condition:
                continue
            by_utc[stamp.astimezone(timezone.utc)] = (condition, cloud)
        except (TypeError, ValueError, OverflowError):
            continue
    arrays = geometry.get("arrays") or []
    totals = [0.0] * len(arrays)
    hourly = []
    matched = 0
    for hour in geometry.get("hourly") or []:
        try:
            stamp = datetime.strptime(hour["hour_local"], "%Y-%m-%d %H:%M %z").astimezone(timezone.utc)
            values = hour["array_exposure_kwp_equivalent"]
        except (KeyError, ValueError, TypeError):
            continue
        # HA hourly forecasts are commonly stamped at HH:00 while geometry
        # samples the interval midpoint HH:30. Match the closest instant
        # within 30 minutes; compare UTC instants to preserve DST identity.
        nearest = min(by_utc, key=lambda instant: abs((instant - stamp).total_seconds()), default=None)
        evidence = by_utc[nearest] if nearest is not None and abs((nearest - stamp).total_seconds()) <= 1800 else None
        if evidence is None:
            hourly.append({"hour_local": hour.get("hour_local"), "weather_status": "missing", "attenuated_array_exposure": None})
            continue
        condition, cloud = evidence
        # The existing Zeus weather engine owns the condition/cloud mapping.
        # Never use a default clear-sky factor for unrecognized conditions.
        try:
            if weather_factor_for is None:
                continue
            factor = float(weather_factor_for(condition or None, cloud))
            if not isfinite(factor) or not 0 <= factor <= 1:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        attenuated = [round(float(value) * factor, 5) for value in values]
        for i, value in enumerate(attenuated):
            totals[i] += value
        matched += 1
        hourly.append({"hour_local": hour.get("hour_local"), "weather_status": "matched",
                       "condition": condition or None, "cloud_coverage": cloud,
                       "attenuation_factor": round(factor, 4),
                       "attenuated_array_exposure": attenuated,
                       "attenuated_total_exposure": round(sum(attenuated), 5)})
    expected = len(geometry.get("hourly") or [])
    return {**output, "status": "ready" if matched == expected and expected else "partial_weather_coverage" if matched else "no_matching_weather",
            "matched_hours": matched, "expected_hours": expected,
            "coverage_fraction": round(matched / expected, 4) if expected else 0,
            "array_exposure_sums": [{"name": a.get("name"), "exposure_proxy_sum": round(totals[i], 4)} for i, a in enumerate(arrays)],
            "hourly": hourly,
            "limitations": ["Cloud/condition attenuation is a heuristic, not physical irradiance", "No calibration, shading, temperature or inverter clipping", "Missing hours are excluded, not assumed clear"]}


# Stage 3: an independent *assumption-based* kWh diagnostic. The cloud
# attenuation above is not physical irradiance, so this is NOT calibrated and
# must never be selected as a production forecast or control input.
def array_energy_candidate_shadow(geometry, weather_shadow, *,
                                  assumed_clear_beam_kw_per_kwp=0.85,
                                  assumed_performance_ratio=0.80):
    """Estimate per-array hourly kWh from midpoint direct-beam exposure.

    Each geometry sample is an hour's plane-of-array incidence times kWp.
    A fixed assumed clear-beam irradiance and assumed DC-to-AC performance
    ratio turn this into a rough kWh *experiment*. Diffuse radiation is omitted;
    no historical calibration, measured irradiance, or shading is asserted.
    """
    result = {
        "status": "unavailable", "model": "assumption_based_direct_beam_energy_experiment",
        "production_applied": False, "control_permission": False,
        "calibrated": False, "accuracy_validated": False,
        "candidate_kwh": None, "forecast_kwh": None,
        "note": "Experimental kWh estimate from assumed clear-beam irradiance and performance ratio; NOT a validated PV forecast.",
    }
    if not isinstance(geometry, dict) or geometry.get("status") != "ready":
        return {**result, "status": "geometry_unavailable"}
    if not isinstance(weather_shadow, dict) or weather_shadow.get("status") != "ready":
        return {**result, "status": "weather_incomplete", "reason": "All hourly weather evidence is required; partial totals are not daily forecasts"}
    try:
        from math import isfinite
        beam, pr = float(assumed_clear_beam_kw_per_kwp), float(assumed_performance_ratio)
        if not (isfinite(beam) and isfinite(pr) and 0 < beam <= 1.0 and 0 < pr <= 1.0):
            return {**result, "status": "invalid_assumptions"}
        arrays = geometry.get("arrays") or []
        hours = weather_shadow.get("hourly") or []
        expected = geometry.get("hour_count")
        if not arrays or not isinstance(hours, list) or len(hours) != expected or expected not in (23, 24, 25):
            return {**result, "status": "invalid_hourly_evidence"}
        totals = [0.0] * len(arrays)
        hourly = []
        for h in hours:
            if h.get("weather_status") != "matched":
                return {**result, "status": "weather_incomplete"}
            values = h.get("attenuated_array_exposure")
            if not isinstance(values, list) or len(values) != len(arrays):
                return {**result, "status": "invalid_hourly_evidence"}
            energies = []
            for i, raw in enumerate(values):
                capacity = float(arrays[i]["capacity_kwp"])
                exposure = float(raw)
                if not (isfinite(capacity) and isfinite(exposure) and capacity > 0 and 0 <= exposure <= capacity + 0.0001):
                    return {**result, "status": "invalid_hourly_evidence"}
                energy = min(capacity, exposure * beam * pr)  # 1-hour sample
                totals[i] += energy
                energies.append(round(energy, 4))
            hourly.append({"hour_local": h.get("hour_local"), "array_candidate_kwh": energies,
                           "total_candidate_kwh": round(sum(energies), 4)})
        total = sum(totals)
        return {**result, "status": "candidate_available", "candidate_kwh": round(total, 3),
                "array_candidates": [{"name": a.get("name"), "capacity_kwp": a.get("capacity_kwp"),
                                      "candidate_kwh": round(totals[i], 3)} for i, a in enumerate(arrays)],
                "hour_count": len(hourly), "hourly": hourly,
                "assumptions": {"clear_beam_kw_per_kwp": beam, "performance_ratio": pr,
                                "interval_hours": 1, "diffuse_irradiance_included": False,
                                "weather_attenuation_is_heuristic": True},
                "limitations": ["No measured irradiance or historical calibration", "Diffuse radiation omitted; cloud factor is heuristic",
                                "No local shading, temperature, inverter clipping or measured array production",
                                "Do not compare as a validated forecast; frozen pre-day snapshots and actuals are required"]}
    except (TypeError, ValueError, KeyError, OverflowError):
        return {**result, "status": "invalid_hourly_evidence"}
