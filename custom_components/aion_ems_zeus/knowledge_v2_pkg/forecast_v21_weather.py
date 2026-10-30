"""Read-only V2.1 weather/solar historical evidence diagnostics.

Measured historical weather is NOT an archived forecast. This module does not
adjust the production forecast, manufacture weather coverage or infer what a
weather provider predicted on an earlier date.
"""
from __future__ import annotations

from math import isfinite, pi, sin, cos, acos, tan, sqrt
from datetime import date
from statistics import median
from typing import Any

BANDS = (("0-20%", 0, 20), ("20-40%", 20, 40),
         ("40-60%", 40, 60), ("60-80%", 60, 80),
         ("80-100%", 80, 100.00001))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if isfinite(result) else None


def _solar_potential(day: date, latitude: float) -> float | None:
    """Top-of-atmosphere daily insolation proxy (kWh/m²), NOT predicted PV yield.

    Uses standard daily extraterrestrial horizontal irradiance geometry.
    It is a season/daylength denominator, not measured irradiance or a
    historical forecast. Never use it to modify the production forecast.
    """
    if not isfinite(latitude) or abs(latitude) > 66:
        return None
    n = day.timetuple().tm_yday
    lat = latitude * pi / 180
    declination = 0.409 * sin(2 * pi * n / 365 - 1.39)
    distance = 1 + 0.033 * cos(2 * pi * n / 365)
    cosine = -tan(lat) * tan(declination)
    sunset = acos(max(-1.0, min(1.0, cosine)))
    # FAO-56 daily extraterrestrial radiation MJ/m²/day -> kWh/m²/day
    mj = (24 * 60 / pi) * 0.0820 * distance * (
        sunset * sin(lat) * sin(declination)
        + cos(lat) * cos(declination) * sin(sunset)
    )
    return max(0.0, mj / 3.6)


def summarize_weather_history(days: list[dict[str, Any]], *, min_days: int = 7,
                              latitude: float | None = None) -> dict[str, Any]:
    """Summarize *completed* measured weather/solar pairs; no forecast correction."""
    buckets: dict[str, list[float]] = {label: [] for label, _, _ in BANDS}
    eligible = 0
    monthly: dict[str, list[float]] = {}
    cloud_sources: dict[str, int] = {}
    invalid_dates = 0
    dated_pairs = 0
    recent_pairs: list[tuple[date, float]] = []
    normalized: list[tuple[date, float, float, float]] = []
    lat = _number(latitude)
    lat_valid = lat is not None and abs(lat) <= 66

    for day in days:
        if not isinstance(day, dict) or day.get("completed_day") is not True or day.get("in_progress") is True:
            continue
        cloud = _number(day.get("cloud_coverage_avg"))
        solar = _number(day.get("solar_energy_kwh"))
        if cloud is None or solar is None or not (0 <= cloud <= 100 and solar >= 0):
            continue
        eligible += 1
        provenance = str(day.get("cloud_coverage_source") or "unknown")
        cloud_sources[provenance] = cloud_sources.get(provenance, 0) + 1
        try:
            day_date = date.fromisoformat(str(day.get("date")))
        except (TypeError, ValueError):
            invalid_dates += 1
        else:
            dated_pairs += 1
            month_key = day_date.strftime("%Y-%m")
            monthly.setdefault(month_key, []).append(solar)
            recent_pairs.append((day_date, solar))
            if lat_valid:
                potential = _solar_potential(day_date, lat)
                if potential is not None and potential > 0.25:
                    normalized.append((day_date, cloud, solar, solar / potential))
        for label, lower, upper in BANDS:
            if lower <= cloud < upper:
                buckets[label].append(solar)
                break
    bands = [{"cloud_band": label, "measured_days": len(values),
              "average_measured_solar_kwh": round(sum(values) / len(values), 2) if values else None,
              "sufficient_evidence": len(values) >= min_days}
             for label, values in buckets.items()]
    monthly_summary = [
        {"month": month, "measured_days": len(values),
         "average_measured_solar_kwh": round(sum(values) / len(values), 2),
         "median_measured_solar_kwh": round(median(values), 2),
         "sufficient_evidence": len(values) >= min_days}
        for month, values in sorted(monthly.items())
    ]
    recent_pairs.sort(key=lambda item: item[0])
    recent_7 = [value for _, value in recent_pairs[-7:]]
    previous_7 = [value for _, value in recent_pairs[-14:-7]]
    trend = {"recent_7_days": len(recent_7), "previous_7_days": len(previous_7),
             "recent_mean_kwh": round(sum(recent_7)/len(recent_7),2) if recent_7 else None,
             "previous_mean_kwh": round(sum(previous_7)/len(previous_7),2) if previous_7 else None,
             "comparison_is_seasonally_controlled": False}
    normalized_bands = []
    for label, lower, upper in BANDS:
        rows = [r for r in normalized if lower <= r[1] < upper]
        values = [r[3] for r in rows]
        normalized_bands.append({
            "cloud_band": label, "measured_days": len(values),
            "median_kwh_per_potential_kwh_m2": round(median(values), 3) if values else None,
            "sufficient_evidence": len(values) >= min_days,
        })
    normalized_summary = {
        "status": "descriptive_only" if lat_valid and normalized else "latitude_unavailable",
        "method": "daily_extraterrestrial_horizontal_irradiance_proxy",
        "latitude_deg": round(lat, 3) if lat_valid else None,
        "normalized_days": len(normalized),
        "cloud_bands": normalized_bands,
        "forecast_correction_applied": False,
        "note": "Seasonal geometry proxy only; not PV output, actual irradiance, or frozen forecast evidence.",
    }
    return {"status": "descriptive_only" if eligible >= min_days else "insufficient_historical_weather",
            "completed_weather_solar_pairs": eligible, "minimum_days_per_band": min_days,
            "cloud_bands": bands, "monthly_seasonality": monthly_summary,
            "cloud_evidence_provenance": cloud_sources,
            "dated_pairs": dated_pairs, "invalid_dates": invalid_dates,
            "recent_vs_previous_7_days": trend,
            "seasonally_normalized_weather": normalized_summary,
            "forecast_correction_applied": False,
            "can_train_frozen_forecast_bias": False,
            "note": "Historical measured weather is not frozen pre-day forecast weather; no calibration or control is authorized."}
