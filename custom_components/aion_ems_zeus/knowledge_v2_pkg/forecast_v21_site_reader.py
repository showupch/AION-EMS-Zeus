"""Read-only Home Assistant state adapter for Zeus V2.1 on-site evidence.

Mappings are explicit, never guessed from entity names. No historical data is
invented, no state is written, and no control service is invoked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from .forecast_v21_site_evidence import inspect_site_evidence

MEASUREMENT_KEYS = ("irradiance_wm2", "lux", "pv_dc_w", "inverter_ac_w", "battery_dc_w")


def collect_site_observation(hass: Any, mappings: Mapping[str, str], *,
                             pv_dc_semantics: str = "unknown",
                             curtailed: bool | None = None,
                             now: datetime | None = None,
                             max_age_seconds: float = 60) -> dict[str, Any]:
    """Collect a synchronized snapshot from explicitly mapped HA entity IDs.

    Returns both the candidate sample and a per-sensor rejection audit. The
    validator must still reject unknown curtailment for learning. Only HA state
    values with correct unit metadata are accepted; kW is converted to W.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("invalid max_age_seconds")
    sample: dict[str, Any] = {
        "timestamp": now.isoformat(), "sensor_timestamps": {},
        "pv_dc_semantics": pv_dc_semantics, "curtailed": curtailed,
    }
    rejected: dict[str, str] = {}
    expected = {"irradiance_wm2": {"W/m²", "W/m2"}, "lux": {"lx"},
                "pv_dc_w": {"W", "kW"}, "inverter_ac_w": {"W", "kW"},
                "battery_dc_w": {"W", "kW"}}
    for key in MEASUREMENT_KEYS:
        entity_id = mappings.get(key)
        if not entity_id:
            rejected[key] = "not_mapped"
            continue
        state = hass.states.get(entity_id)
        if state is None:
            rejected[key] = "entity_unavailable"
            continue
        if state.state in ("unknown", "unavailable", "none", ""):
            rejected[key] = "invalid_state"
            continue
        unit = state.attributes.get("unit_of_measurement")
        if unit not in expected[key]:
            rejected[key] = "unit_mismatch"
            continue
        try:
            value = float(state.state)
        except (TypeError, ValueError, OverflowError):
            rejected[key] = "non_numeric"
            continue
        if not isfinite(value) or value < 0:
            rejected[key] = "invalid_value"
            continue
        timestamp = state.last_updated
        if timestamp is None or timestamp.tzinfo is None:
            rejected[key] = "invalid_timestamp"
            continue
        age = (now - timestamp).total_seconds()
        if age < -5 or age > max_age_seconds:
            rejected[key] = "stale"
            continue
        sample[key] = value * (1000 if unit == "kW" else 1)
        sample["sensor_timestamps"][key] = timestamp.isoformat()
    return {"sample": sample, "rejected": rejected,
            "evidence": inspect_site_evidence([sample], min_samples=1),
            "note": "Read-only instant audit; no calibration or production forecast changes."}
