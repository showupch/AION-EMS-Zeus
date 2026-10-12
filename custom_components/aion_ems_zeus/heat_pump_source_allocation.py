"""Conservative, explicitly modelled hourly Heat Pump source allocation.

Uses hourly Recorder statistics with complete, synchronized power means.
Never claims direct appliance source metering or PV origin of battery discharge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite


def _number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    from homeassistant.util import dt as dt_util
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(value)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        return dt_util.as_utc(parsed) if parsed else None
    return dt_util.as_utc(value) if value else None


def _hourly(points, unit):
    """Return UTC hour -> nonnegative W, without filling missing hours."""
    result = {}
    if unit not in ("W", "kW"):
        return result
    factor = 1000.0 if unit == "kW" else 1.0
    for point in points or []:
        at = _timestamp(point.get("start"))
        val = _number(point.get("mean"))
        if at is None or val is None or val < -0.01:
            continue
        if at.minute != 0:
            continue
        result[at.replace(minute=0, second=0, microsecond=0)] = max(0.0, val * factor)
    return result


def allocate_month(statistics, ids, units, start, end, measured_kwh):
    """Return allocation only for strong synchronized evidence.

    The model distributes each hour's measured house source mix
    proportionally across house loads. Battery energy is counted as local
    supply but NOT attributed to PV. Coverage and energy reconciliation
    must both pass; otherwise leave every allocation Unavailable.
    """
    from datetime import timedelta
    keys = ("hp", "house", "solar", "import", "export", "charge", "discharge")
    if any(not ids.get(k) for k in keys):
        return None
    series = {k: _hourly(statistics.get(ids[k]), units.get(k)) for k in keys}
    duration = (end - start).total_seconds() / 3600
    if duration <= 0 or measured_kwh is None or measured_kwh <= 0:
        return None
    total_hp = total_grid = total_pv = total_battery = 0.0
    matched = 0
    grid_hourly = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t < end:
        if t >= start and all(t in series[k] for k in keys):
            hp, house, solar, imp, exp, charge, discharge = (series[k][t] for k in keys)
            if house > 0 and hp <= house * 1.10 and abs((solar + imp + discharge) - (house + exp + charge)) <= max(250.0, house * 0.20):
                # Sources used by the house after battery charging and export.
                direct_pv = max(0.0, solar - charge - exp)
                available = direct_pv + imp + discharge
                if available > 0 and abs(available - house) <= max(250.0, house * 0.20):
                    factor = min(1.0, house / available)
                    total_hp += hp / 1000
                    grid_hour = hp * (imp * factor / house) / 1000
                    total_grid += grid_hour
                    grid_hourly.append((t, grid_hour))
                    total_pv += hp * (direct_pv * factor / house) / 1000
                    total_battery += hp * (discharge * factor / house) / 1000
                    matched += 1
        t += timedelta(hours=1)
    if matched / max(1, duration) < 0.90 or total_hp <= 0:
        return None
    # Prevent power-derived allocations being attached to a different HP meter.
    if abs(total_hp - measured_kwh) / measured_kwh > 0.15:
        return None
    scale = measured_kwh / total_hp
    grid = total_grid * scale
    pv = total_pv * scale
    battery = total_battery * scale
    return {
        "pv_share": round(100 * pv / measured_kwh, 1),
        "grid_share": round(100 * grid / measured_kwh, 1),
        "self_sufficiency": round(100 * (pv + battery) / measured_kwh, 1),
        "allocated_grid_kwh": round(grid, 3),
        "allocated_grid_hourly": [(stamp.isoformat(), round(kwh * scale, 6)) for stamp, kwh in grid_hourly],
        "battery_share": round(100 * battery / measured_kwh, 1),
        "unallocated_share": round(max(0.0, 100.0 - 100.0 * (pv + battery + grid) / measured_kwh), 1),
        "evidence_coverage_percent": round(100 * matched / duration, 1),
        "attribution_method": "Modelled proportional allocation from synchronized hourly Recorder means",
    }
