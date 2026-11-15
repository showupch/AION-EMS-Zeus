"""Forward-only, read-only hourly irradiance/PV evidence. Never used by controls."""
from datetime import datetime, timezone, timedelta
from math import isfinite

def capture_hourly_pair(store, *, now, irradiance, pv, irradiance_entity, pv_entity,
                        max_age_minutes=30, retention_days=120):
    """Require explicit source mappings and fresh, independently measured W/m² and W."""
    if not irradiance_entity or not pv_entity:
        return {"status": "skipped", "reason": "sources_not_configured"}
    if not isinstance(now, datetime) or now.tzinfo is None:
        return {"status": "skipped", "reason": "invalid_time"}
    result = {}
    for name, state, entity_id, unit in (
        ("irradiance", irradiance, irradiance_entity, "W/m²"),
        ("pv", pv, pv_entity, "W"),
    ):
        if state is None or getattr(state, "entity_id", None) != entity_id:
            return {"status": "skipped", "reason": f"{name}_entity_missing"}
        timestamp = getattr(state, "last_updated", None)
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            return {"status": "skipped", "reason": f"{name}_timestamp_missing"}
        age = now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)
        if not timedelta(0) <= age <= timedelta(minutes=max_age_minutes):
            return {"status": "skipped", "reason": f"{name}_stale"}
        attrs = getattr(state, "attributes", {}) or {}
        observed_unit = str(attrs.get("unit_of_measurement") or "").replace("㎡", "m²")
        if observed_unit != unit:
            return {"status": "skipped", "reason": f"{name}_unit_mismatch"}
        try:
            value = float(state.state)
        except (ValueError, TypeError, AttributeError):
            return {"status": "skipped", "reason": f"{name}_invalid"}
        if not isfinite(value) or value < 0 or (name == "irradiance" and value > 1500):
            return {"status": "skipped", "reason": f"{name}_invalid"}
        result[name] = (value, timestamp.astimezone(timezone.utc))
    if abs((result["irradiance"][1] - result["pv"][1]).total_seconds()) > 1800:
        return {"status": "skipped", "reason": "timestamps_not_aligned"}
    key = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    records = store.setdefault("verified_hourly_irradiance_pv", {})
    if key in records:
        return {"status": "already_captured", "hour": key}
    records[key] = {
        "irradiance_w_m2": round(result["irradiance"][0], 2),
        "pv_w": round(result["pv"][0], 2),
        "irradiance_entity": irradiance_entity,
        "pv_entity": pv_entity,
        "irradiance_updated_utc": result["irradiance"][1].isoformat(),
        "pv_updated_utc": result["pv"][1].isoformat(),
        "provenance": "observed_ha_sensor_state",
        "status": "unvalidated_hourly_sample",
    }
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=retention_days)).isoformat()
    for old in list(records):
        if old < cutoff:
            del records[old]
    return {"status": "captured", "hour": key}
