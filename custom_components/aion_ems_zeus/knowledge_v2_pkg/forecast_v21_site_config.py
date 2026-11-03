"""Phase 5: read-only audit of explicit V2.1 mappings and registered devices.

Never infer PV DC semantics from an entity name or a generic device type.
Registry devices are displayed as suggestions only; an administrator must
explicitly map sensors and certify whether a DC sensor is PV-only.
No persistence, service calls, entity creation or production forecast changes.
"""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from .forecast_v21_site_reader import MEASUREMENT_KEYS, collect_site_observation


def _valid_entity(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("sensor.") and len(value) > 7 and not any(c.isspace() for c in value)


def inspect_configured_site(hass: Any, settings: Mapping[str, Any] | None,
                            devices: Sequence[Mapping[str, Any]] | None = None, *,
                            now=None) -> dict[str, Any]:
    """Audit explicit mappings and return candidates without auto-selecting them."""
    cfg = settings if isinstance(settings, Mapping) else {}
    raw = cfg.get("mappings", {})
    raw = raw if isinstance(raw, Mapping) else {}
    mappings = {k: raw[k] for k in MEASUREMENT_KEYS if _valid_entity(raw.get(k))}
    invalid = sorted(k for k in MEASUREMENT_KEYS if k in raw and k not in mappings)
    semantics = cfg.get("pv_dc_semantics", "unknown")
    if semantics not in ("pv_only", "combined", "unknown"):
        semantics = "unknown"
    # Do not assert curtailment-free operation without a measured signal.
    curtailed = None
    signal = cfg.get("curtailment_entity")
    if isinstance(signal, str) and signal.startswith(("binary_sensor.", "input_boolean.", "switch.")):
        state = hass.states.get(signal)
        if state is not None and state.state in ("on", "off"):
            curtailed = state.state == "on"
    audit = collect_site_observation(hass, mappings, pv_dc_semantics=semantics,
                                     curtailed=curtailed, now=now)
    candidates = []
    for device in devices or ():
        if not isinstance(device, Mapping):
            continue
        device_type = str(device.get("type") or "")
        if device_type not in ("inverter", "solar_inverter", "pv_inverter"):
            continue
        mapping = device.get("mappings")
        mapping = mapping if isinstance(mapping, Mapping) else {}
        power = mapping.get("power")
        if _valid_entity(power):
            candidates.append({"device_name": str(device.get("name") or "Inverter")[:80],
                               "power_entity": power, "role": "unverified_inverter_power",
                               "auto_mapped": False})
    return {"status": "read_only_mapping_audit", "configured_keys": sorted(mappings),
            "invalid_mapping_keys": invalid, "missing_keys": sorted(set(MEASUREMENT_KEYS)-set(mappings)),
            "pv_dc_semantics": semantics, "curtailment_known": curtailed is not None,
            "candidate_inverter_entities": candidates[:20],
            "instant_observation": audit, "persistent": False,
            "production_applied": False, "control_permission": False,
            "note": "Registered devices are suggestions only; no sensor is auto-mapped."}
