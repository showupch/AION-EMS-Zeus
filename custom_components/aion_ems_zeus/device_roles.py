"""Shared device-role classification for Zeus load-facing engines."""
from __future__ import annotations
from typing import Any

_SOURCE_TOKENS = (
    "solar", "photovoltaic", "pv inverter", "pv_inverter", "solar inverter", "solar_inverter",
    "microinverter", "inverter", "fronius symo", "fronius hybrid", "battery inverter",
    "generator", "wind turbine", "wind inverter", "smart meter", "grid meter", "energy meter", "power meter",
)

def is_consuming_load(device: dict[str, Any]) -> bool:
    """True only for end-use loads; generation/source/meter devices are excluded."""
    if not isinstance(device, dict) or not device.get("enabled", True):
        return False
    if device.get("hybrid_inverter") is True:
        return False
    fields = ("type", "category", "role", "device_class", "name", "manufacturer", "model")
    text = " ".join(str(device.get(k) or "") for k in fields).lower().replace("-", " ")
    return not any(token in text for token in _SOURCE_TOKENS)
