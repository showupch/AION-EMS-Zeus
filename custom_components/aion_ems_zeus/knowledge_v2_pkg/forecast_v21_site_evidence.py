"""Independent Zeus V2.1 on-site evidence validation (shadow-only).

Pure functions; no Home Assistant access, writes, network calls or control outputs.
Inputs must be synchronized measured samples, not historical forecasts.
This module NEVER modifies the V2 forecast or estimates future weather.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from statistics import median
from typing import Any


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return v if isfinite(v) and v >= 0 else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo is not None else None


def inspect_site_evidence(samples: list[dict[str, Any]], *, max_skew_seconds: float = 30,
                          min_irradiance_wm2: float = 50, min_samples: int = 7) -> dict[str, Any]:
    """Audit synchronized observations; optional sensors never block basic operation.

    Expected optional keys: timestamp, irradiance_wm2, lux, pv_dc_w,
    inverter_ac_w, battery_dc_w, pv_dc_semantics, curtailed, sensor_timestamps.
    `pv_dc_semantics` MUST equal `pv_only` to compare PV DC and AC.
    Battery DC is never added to PV DC or AC. Sensor timestamps are ISO-8601
    offset-aware values; unknown/naive timestamps are rejected.
    """
    if not isfinite(max_skew_seconds) or max_skew_seconds < 0:
        raise ValueError("invalid max_skew_seconds")
    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    counts = {k: 0 for k in ("invalid_timestamp", "invalid_measurement", "stale_or_unsynchronized",
                              "curtailed", "ambiguous_dc_semantics", "usable_irradiance_pv",
                              "usable_lux_irradiance", "usable_dc_ac")}
    ratios, efficiencies, irradiance_pv = [], [], []
    for sample in samples:
        if not isinstance(sample, dict) or _time(sample.get("timestamp")) is None:
            counts["invalid_timestamp"] += 1
            continue
        values = {key: _finite_nonnegative(sample.get(key)) for key in
                  ("irradiance_wm2", "lux", "pv_dc_w", "inverter_ac_w", "battery_dc_w")}
        if any(sample.get(key) is not None and val is None for key, val in values.items()):
            counts["invalid_measurement"] += 1
            continue
        stamps = sample.get("sensor_timestamps", {})
        if not isinstance(stamps, dict):
            counts["stale_or_unsynchronized"] += 1
            continue
        times = [_time(sample["timestamp"])]
        for key in ("irradiance_wm2", "lux", "pv_dc_w", "inverter_ac_w"):
            if values[key] is not None:
                # A timestamp is mandatory for every supplied measurement.
                times.append(_time(stamps.get(key)))
        if any(t is None for t in times) or (max(times)-min(times)).total_seconds() > max_skew_seconds:
            counts["stale_or_unsynchronized"] += 1
            continue
        if sample.get("curtailed") is not False:
            # Unknown curtailment is not asserted safe for calibration.
            counts["curtailed"] += 1
            continue
        irr, lux, dc, ac = (values[k] for k in ("irradiance_wm2", "lux", "pv_dc_w", "inverter_ac_w"))
        if irr is not None and irr >= min_irradiance_wm2:
            if lux is not None:
                ratios.append(lux / irr)
                counts["usable_lux_irradiance"] += 1
            if ac is not None:
                irradiance_pv.append(ac / irr)
                counts["usable_irradiance_pv"] += 1
        if dc is not None and ac is not None and dc > 0:
            if sample.get("pv_dc_semantics") != "pv_only":
                counts["ambiguous_dc_semantics"] += 1
            else:
                efficiencies.append(ac / dc)
                counts["usable_dc_ac"] += 1
    def summary(values: list[float]) -> dict[str, Any]:
        return {"samples": len(values), "median": round(median(values), 4) if len(values) >= min_samples else None,
                "qualified": len(values) >= min_samples}
    return {"status": "shadow_evidence_only", "samples_seen": len(samples), "rejections": counts,
            "lux_per_wm2": summary(ratios), "ac_w_per_irradiance_wm2": summary(irradiance_pv),
            "ac_dc_ratio_observed": summary(efficiencies),
            "note": "Observed ratios are diagnostics, not universal conversions or forecast corrections.",
            "production_applied": False, "control_permission": False}
