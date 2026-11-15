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




def geometry_solar_diagnostics(geometry, target_date, latitude, longitude, timezone_name):
    """Independent read-only solar-position evidence; never alters exposure results."""
    from math import asin, atan2
    zone = ZoneInfo(str(timezone_name))
    day = date.fromisoformat(str(target_date))
    lat, lon = float(latitude), float(longitude)
    phi = radians(lat)

    def position(instant):
        local = instant.astimezone(zone)
        n = local.timetuple().tm_yday
        hour = local.hour + local.minute / 60 + local.second / 3600
        gamma = 2*pi/365 * (n-1+(hour-12)/24)
        eq = 229.18*(0.000075+0.001868*cos(gamma)-0.032077*sin(gamma)-0.014615*cos(2*gamma)-0.040849*sin(2*gamma))
        decl = (0.006918-0.399912*cos(gamma)+0.070257*sin(gamma)-0.006758*cos(2*gamma)+0.000907*sin(2*gamma)-0.002697*cos(3*gamma)+0.00148*sin(3*gamma))
        utc = instant.astimezone(timezone.utc)
        minutes_utc = utc.hour*60 + utc.minute + utc.second/60
        ha = radians((minutes_utc+eq+4*lon)/4-180)
        east = -cos(decl)*sin(ha)
        north = cos(phi)*sin(decl)-sin(phi)*cos(decl)*cos(ha)
        up = sin(phi)*sin(decl)+cos(phi)*cos(decl)*cos(ha)
        return degrees(asin(max(-1., min(1., up)))), (degrees(atan2(east, north))+360)%360, (east,north,up)

    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    crossings = []
    t = start
    prev_elev = position(t)[0]
    while t < end:
        nxt = min(t + timedelta(minutes=1), end)
        next_elev = position(nxt)[0]
        if (prev_elev <= 0 < next_elev) or (prev_elev > 0 >= next_elev):
            low, high = t, nxt
            rising = prev_elev <= 0
            for _ in range(12):
                mid = low + (high-low)/2
                if (position(mid)[0] > 0) == rising:
                    high = mid
                else:
                    low = mid
            crossing = (low + (high-low)/2).astimezone(zone)
            crossings.append({"type": "sunrise" if rising else "sunset", "local": crossing.isoformat(timespec="seconds")})
        t, prev_elev = nxt, next_elev

    rows = []
    for hour in geometry.get("hourly", []):
        stamp = datetime.strptime(hour["hour_local"], "%Y-%m-%d %H:%M %z").astimezone(timezone.utc)
        elevation, azimuth, (east,north,up) = position(stamp)
        factors = []
        for array in geometry.get("arrays", []):
            slope = radians(float(array["tilt_deg"]))
            facing = radians(float(array["azimuth_deg"]))
            incidence = max(0., up*cos(slope)+sin(slope)*(north*cos(facing)+east*sin(facing))) if up > 0 else 0.
            factors.append({"name": array["name"], "direct_beam_incidence_factor": round(incidence, 5)})
        rows.append({"hour_local": hour["hour_local"], "solar_elevation_deg": round(elevation, 3),
                     "solar_azimuth_deg": round(azimuth, 3), "arrays": factors})
    return {"method": "NOAA_fractional_year_geometric_horizon_no_refraction",
            "horizon_definition": "solar_center_elevation_0_deg",
            "sun_events": crossings, "hourly_solar_position": rows,
            "note": "Geometric horizon only; Home Assistant sun events may differ due to refraction and solar-disc correction."}

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

def subhourly_geometry_comparison(arrays, target_date, latitude, longitude, timezone_name):
    """Read-only direct-beam geometry sensitivity; no weather or forecast kWh.

    Compare the existing midpoint exposure with 5-minute sampled exposure.
    A low-sun air-mass proxy is reported separately, never applied to V2/V2.1.
    """
    from math import sqrt
    zone = ZoneInfo(str(timezone_name))
    day = date.fromisoformat(str(target_date))
    lat, lon = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"status": "invalid_location", "production_applied": False}
    if not isinstance(arrays, list) or not arrays:
        return {"status": "not_configured", "production_applied": False}
    prepared = []
    for a in arrays[:12]:
        try:
            capacity = float(a["capacity_kwp"])
            azimuth = float(a["azimuth_deg"])
            tilt = float(a["tilt_deg"])
            if not all(isfinite(x) for x in (capacity, azimuth, tilt)):
                raise ValueError()
            if not (0 < capacity <= 1000 and 0 <= azimuth < 360 and 0 <= tilt <= 90):
                raise ValueError()
        except (TypeError, KeyError, ValueError, OverflowError):
            return {"status": "invalid_array", "production_applied": False}
        prepared.append((capacity, radians(azimuth), radians(tilt)))
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    phi = radians(lat)

    def exposure(t):
        local = t.astimezone(zone)
        n = local.timetuple().tm_yday
        hour = local.hour + local.minute/60 + local.second/3600
        gamma = 2*pi/365 * (n-1+(hour-12)/24)
        eq = 229.18*(0.000075+0.001868*cos(gamma)-0.032077*sin(gamma)-0.014615*cos(2*gamma)-0.040849*sin(2*gamma))
        decl = (0.006918-0.399912*cos(gamma)+0.070257*sin(gamma)-0.006758*cos(2*gamma)+0.000907*sin(2*gamma)-0.002697*cos(3*gamma)+0.00148*sin(3*gamma))
        utc = t.astimezone(timezone.utc)
        ha = radians(((utc.hour*60+utc.minute+utc.second/60)+eq+4*lon)/4-180)
        east = -cos(decl)*sin(ha)
        north = cos(phi)*sin(decl)-sin(phi)*cos(decl)*cos(ha)
        up = sin(phi)*sin(decl)+cos(phi)*cos(decl)*cos(ha)
        if up <= 0:
            return 0., 0.
        total = sum(cap*max(0.,up*cos(tilt)+sin(tilt)*(north*cos(az)+east*sin(az)))
                    for cap,az,tilt in prepared)
        # Illustrative horizon sensitivity only: not an irradiance model.
        proxy = min(1., max(0., up)/sin(radians(15)))
        return total, total*proxy
    rows = []
    t = start
    while t < end:
        stop = min(t+timedelta(hours=1), end)
        span = (stop-t).total_seconds()
        midpoint = t+(stop-t)/2
        midpoint_value, _ = exposure(midpoint)
        n = max(1,round(span/300))
        samples = [exposure(t+timedelta(seconds=span*(i+0.5)/n)) for i in range(n)]
        integrated = sum(v for v,_ in samples)/n
        low_sun = sum(v for _,v in samples)/n
        rows.append({
            "hour_local": midpoint.astimezone(zone).strftime("%Y-%m-%d %H:%M %z"),
            "midpoint_relative_exposure": round(midpoint_value,5),
            "five_minute_relative_exposure": round(integrated,5),
            "illustrative_low_sun_proxy_exposure": round(low_sun,5),
            "difference_percent": round(100*(integrated/midpoint_value-1),2) if midpoint_value else None,
        })
        t = stop
    return {"status": "ready", "target_date": str(day),
            "method": "NOAA_geometry_midpoint_vs_5_minute_samples",
            "hourly": rows,
            "midpoint_sum": round(sum(x["midpoint_relative_exposure"] for x in rows),4),
            "five_minute_sum": round(sum(x["five_minute_relative_exposure"] for x in rows),4),
            "limitations": "Relative direct-beam exposure only; low-sun proxy illustrative, not validated irradiance, weather or kWh",
            "production_applied": False, "control_permission": False}

def clear_sky_irradiance_sensitivity(arrays, target_date, latitude, longitude, timezone_name):
    """Read-only Haurwitz clear-sky GHI and direct-plane sensitivity.

    Haurwitz estimates horizontal global irradiance, not DNI or DHI.
    The direct-plane conversion below uses an explicitly assumed diffuse
    fraction; it is an exploratory scenario, not a validated PV forecast.
    """
    zone = ZoneInfo(str(timezone_name))
    day = date.fromisoformat(str(target_date))
    lat, lon = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"status": "invalid_location", "production_applied": False}
    if not isinstance(arrays, list) or not arrays:
        return {"status": "not_configured", "production_applied": False}
    prepared = []
    for a in arrays[:12]:
        try:
            capacity = float(a["capacity_kwp"])
            azimuth = float(a["azimuth_deg"])
            tilt = float(a["tilt_deg"])
            if not all(isfinite(v) for v in (capacity, azimuth, tilt)):
                raise ValueError()
            if not (0 < capacity <= 1000 and 0 <= azimuth < 360 and 0 <= tilt <= 90):
                raise ValueError()
        except (TypeError, KeyError, ValueError, OverflowError):
            return {"status": "invalid_array", "production_applied": False}
        prepared.append((capacity, radians(azimuth), radians(tilt)))
    phi = radians(lat)
    diffuse_fraction = 0.15  # sensitivity assumption, not measured
    def sample(t):
        local = t.astimezone(zone)
        n = local.timetuple().tm_yday
        hour = local.hour + local.minute/60 + local.second/3600
        gamma = 2*pi/365*(n-1+(hour-12)/24)
        eq = 229.18*(0.000075+0.001868*cos(gamma)-0.032077*sin(gamma)-0.014615*cos(2*gamma)-0.040849*sin(2*gamma))
        decl = (0.006918-0.399912*cos(gamma)+0.070257*sin(gamma)-0.006758*cos(2*gamma)+0.000907*sin(2*gamma)-0.002697*cos(3*gamma)+0.00148*sin(3*gamma))
        utc = t.astimezone(timezone.utc)
        ha = radians(((utc.hour*60+utc.minute+utc.second/60)+eq+4*lon)/4-180)
        east = -cos(decl)*sin(ha)
        north = cos(phi)*sin(decl)-sin(phi)*cos(decl)*cos(ha)
        up = sin(phi)*sin(decl)+cos(phi)*cos(decl)*cos(ha)
        if up <= 0:
            return 0.,0.,0.
        # Haurwitz empirical clear-sky GHI (W/m2).
        ghi = 1098*up*exp(-0.059/up)
        dhi = diffuse_fraction*ghi
        dni = (ghi-dhi)/up
        poa = 0.
        for capacity, az, tilt in prepared:
            incidence = max(0.,up*cos(tilt)+sin(tilt)*(north*cos(az)+east*sin(az)))
            poa += capacity*(dni*incidence+dhi*(1+cos(tilt))/2)
        return ghi,poa,up
    start = datetime.combine(day,datetime.min.time(),tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(timezone.utc)
    rows=[]
    t=start
    while t<end:
        stop=min(t+timedelta(hours=1),end)
        duration=(stop-t).total_seconds()
        n=max(1,round(duration/300))
        values=[sample(t+timedelta(seconds=duration*(i+0.5)/n)) for i in range(n)]
        mid=t+(stop-t)/2
        rows.append({
            "hour_local":mid.astimezone(zone).strftime("%Y-%m-%d %H:%M %z"),
            "clear_sky_ghi_w_m2":round(sum(x[0] for x in values)/n,2),
            "clear_sky_weighted_poa_w_m2_kwp":round(sum(x[1] for x in values)/n,2),
            "daylight_fraction":round(sum(x[2]>0 for x in values)/n,3)
        })
        t=stop
    return {
        "status":"ready", "target_date":str(day),
        "method":"Haurwitz_clear_sky_GHI_5_minute_integrated",
        "diffuse_fraction_assumed":diffuse_fraction,
        "hourly":rows,
        "production_applied":False,"control_permission":False,
        "limitations":"Haurwitz GHI with assumed diffuse fraction; no measured DNI/DHI, weather, horizon, temperature, clipping, shading, or calibration; weighted POA is not kWh"
    }

def physical_candidate_from_frozen_weather(clear_sky, frozen_hourly, capacity_kwp,
                                           performance_ratio=0.8):
    """Read-only scenario; never use current weather to revise a frozen forecast.

    A physical candidate is withheld unless every daylight hour has an
    explicit, valid frozen attenuation factor. Historical snapshots lacking
    this evidence are not backfilled.
    """
    base = {"status": "unavailable", "candidate_kwh": None,
            "production_applied": False, "control_permission": False,
            "accuracy_validated": False,
            "model": "haurwitz_5_minute_ghi_assumed_diffuse_frozen_weather_sensitivity"}
    if clear_sky.get("status") != "ready":
        return {**base, "status": "missing_clear_sky_geometry"}
    if not isinstance(frozen_hourly, list):
        return {**base, "status": "missing_frozen_weather"}
    try:
        capacity = float(capacity_kwp)
        ratio = float(performance_ratio)
        if not (isfinite(capacity) and capacity > 0 and isfinite(ratio) and 0 < ratio <= 1):
            return {**base, "status": "invalid_parameters"}
        weather_by_hour = {row.get("hour_local"): row for row in frozen_hourly
                           if isinstance(row, dict) and isinstance(row.get("hour_local"), str)}
        if len(weather_by_hour) != len(clear_sky["hourly"]):
            return {**base, "status": "incomplete_frozen_weather"}
        result = []
        for hour in clear_sky["hourly"]:
            weather = weather_by_hour.get(hour["hour_local"])
            if weather is None:
                return {**base, "status": "unmatched_frozen_weather"}
            ghi = float(hour["clear_sky_ghi_w_m2"])
            factor = weather.get("weather_attenuation_factor")
            if ghi > 0:
                if factor is None:
                    return {**base, "status": "missing_hourly_attenuation"}
                factor = float(factor)
                if not isfinite(factor) or not 0 <= factor <= 1:
                    return {**base, "status": "invalid_hourly_attenuation"}
            else:
                factor = 0.
            # Sum(capacity_kWp * assumed POA W/m²) / 1000 W/m²,
            # multiplied by PR and a one-hour interval -> kWh.
            energy = max(0., float(hour["clear_sky_weighted_poa_w_m2_kwp"])) / 1000 * ratio * factor
            result.append({"hour_local": hour["hour_local"],
                           "clear_sky_scenario_kwh": round(energy, 4),
                           "frozen_weather_attenuation_factor": round(factor, 5)})
        return {**base, "status": "scenario_available",
                "candidate_kwh": round(sum(row["clear_sky_scenario_kwh"] for row in result), 3),
                "hourly": result,
                "assumptions": {"performance_ratio": ratio, "diffuse_fraction": 0.15,
                                "hourly_interval_hours": 1,
                                "weather_factor": "frozen_existing_heuristic",
                                "no_measured_dni_dhi": True},
                "limitations": "Scenario only; uncalibrated clear-sky decomposition, temperature, shading, clipping and weather attenuation"}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**base, "status": "invalid_frozen_evidence"}
