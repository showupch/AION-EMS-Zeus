"""Read-only, conservative heat-pump circuit efficiency evidence.

This module never allocates shared electrical consumption or labels compressor-only
ratios as full-system SPF. Period totals come from the existing Recorder pipeline.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

PERIODS = ("today", "week", "month", "year")


def circuit_efficiency(device: dict[str, Any], periods: dict[str, Any]) -> dict[str, Any]:
    """Return measured energy ratios, with explicit unavailable reasons.

    A total compressor counter cannot be treated as space-heating-only input.
    Even a valid compressor-input ratio is not a full-system SPF when auxiliary
    electricity has not been separately allocated to the relevant circuit.
    """
    result: dict[str, Any] = {
        "status": "read_only",
        "scope": "compressor_input_ratio_not_full_system_spf",
        "heating": {}, "dhw": {},
    }
    if not device.get("separate_heating_dhw_measurements", False):
        for circuit in ("heating", "dhw"):
            for period in PERIODS:
                result[circuit][period] = {"value": None, "reason": "Separate circuit measurements not enabled"}
        return result

    for circuit in ("heating", "dhw"):
        electrical_id = str(device.get(f"{circuit}_electrical_energy_entity") or "").strip().lower()
        thermal_id = str(device.get(f"{circuit}_thermal_energy_entity") or "").strip().lower()
        for period in PERIODS:
            reason = None
            value = None
            if not electrical_id or not thermal_id:
                reason = "Electrical or thermal circuit meter not mapped"
            elif electrical_id == thermal_id:
                reason = "Electrical and thermal mappings must be independent"
            elif circuit == "heating" and (
                "total_energy_consumption_compressor" in electrical_id
                or "total_compressor" in electrical_id
            ):
                # Read-only, measured counter difference; never a runtime estimate.
                # Requires an independently mapped DHW compressor energy meter.
                dhw_id = str(device.get("dhw_electrical_energy_entity") or "").strip().lower()
                try:
                    total = float(periods[f"heating_electrical_energy_{period}_kwh"])
                    dhw = float(periods[f"dhw_electrical_energy_{period}_kwh"])
                    thermal = float(periods[f"heating_thermal_energy_{period}_kwh"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    total = dhw = thermal = float("nan")
                if not dhw_id or dhw_id == electrical_id:
                    reason = "Independent DHW compressor energy meter required"
                elif not all(isfinite(x) for x in (total, dhw, thermal)):
                    reason = "Matching Recorder period evidence unavailable"
                elif total <= 0 or dhw < 0 or thermal <= 0:
                    reason = "No valid positive heating energy evidence"
                elif dhw >= total:
                    reason = "DHW compressor consumption must be below total compressor consumption"
                else:
                    value = round(thermal / (total - dhw), 3)
            else:
                raw_e = periods.get(f"{circuit}_electrical_energy_{period}_kwh")
                raw_t = periods.get(f"{circuit}_thermal_energy_{period}_kwh")
                try:
                    e = float(raw_e) if raw_e is not None else None
                    t = float(raw_t) if raw_t is not None else None
                except (TypeError, ValueError, OverflowError):
                    e = t = None
                if e is None or t is None or not isfinite(e) or not isfinite(t) or e <= 0 or t <= 0:
                    reason = "No matching positive Recorder electrical and thermal deltas"
                else:
                    value = round(t / e, 3)
            result[circuit][period] = {"value": value, "reason": reason}
    return result
