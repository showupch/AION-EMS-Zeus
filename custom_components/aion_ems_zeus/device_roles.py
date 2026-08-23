"""Shared device-role classification for Zeus load-facing engines."""
from __future__ import annotations
from typing import Any

_SOURCE_TOKENS = (
    "solar", "photovoltaic", "pv inverter", "pv_inverter", "solar inverter", "solar_inverter",
    "microinverter", "inverter", "fronius symo", "fronius hybrid", "battery inverter",
    "generator", "wind turbine", "wind inverter", "smart meter", "grid meter", "energy meter", "power meter",
)

_EXPLICIT_LOAD_TYPES = {
    "custom", "load", "appliance", "smart_plug", "plug",
    "ev_charger", "car_charger", "vehicle_charger",
    "dishwasher", "washing_machine", "washer", "dryer", "tumble_dryer",
    "fridge", "freezer", "refrigerator",
    "heat_pump", "water_heater", "dhw", "boiler",
    "computer", "computers", "tv", "blower", "fan",
}

def is_consuming_load(device: dict[str, Any]) -> bool:
    """True only for end-use loads; generation/source/meter devices are excluded.

    An explicit Zeus end-use type is authoritative.  Generic HA metadata such as
    a device_class/category containing "power meter" must not turn a dishwasher,
    EV charger, smart plug, etc. into a source device.
    """
    if not isinstance(device, dict) or not device.get("enabled", True):
        return False
    if device.get("hybrid_inverter") is True:
        return False

    device_type = str(device.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    role = str(device.get("role") or "").strip().lower().replace("-", "_").replace(" ", "_")

    # Strong source identity is authoritative even when an older registry entry
    # still carries a generic/custom load type. This prevents inverters such as
    # Fronius Symo from entering load rankings or DEA consuming-load sets.
    identity_text = " ".join(
        str(device.get(k) or "") for k in ("name", "friendly_name", "manufacturer", "model")
    ).lower().replace("-", " ")
    strong_source_identity = (
        "fronius" in identity_text
        or "inverter" in identity_text
        or "photovoltaic" in identity_text
        or "solar inverter" in identity_text
        or "pv inverter" in identity_text
        or "grid meter" in identity_text
        or "smart meter" in identity_text
    )
    if strong_source_identity:
        return False

    # Explicit source/generation classification also wins.
    source_type_text = " ".join(
        str(device.get(k) or "") for k in ("type", "role", "category")
    ).lower().replace("-", " ")
    if any(token in source_type_text for token in _SOURCE_TOKENS):
        return False

    # Explicit end-use type remains authoritative against generic HA metadata
    # such as a device_class of "power meter".
    if device_type in _EXPLICIT_LOAD_TYPES or role in _EXPLICIT_LOAD_TYPES:
        return True

    metadata_text = " ".join(
        str(device.get(k) or "") for k in ("device_class", "name", "friendly_name", "manufacturer", "model")
    ).lower().replace("-", " ")
    return not any(token in metadata_text for token in _SOURCE_TOKENS)
