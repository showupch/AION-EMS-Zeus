"""Manufacturer-independent Zeus device profile definitions.

Profiles describe expected capabilities and editable defaults. They never execute
Home Assistant services or device commands. User entity mappings remain the
source of live evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DEVICE_PROFILES: dict[str, dict[str, Any]] = {
    "generic_water_heater": {
        "device_type": "water_heater",
        "label": "Generic Water Heater",
        "manufacturer": "Generic",
        "capabilities": ["electrical_power", "electrical_energy", "dhw_temperature"],
        "smart_control": None,
    },
    "my_pv_elwa": {
        "device_type": "water_heater",
        "label": "my-PV ELWA",
        "manufacturer": "my-PV",
        "capabilities": [
            "electrical_power", "electrical_energy", "dhw_temperature",
            "element_temperature", "solar_surplus", "temperature_lockout",
            "variable_power_modbus",
        ],
        "smart_control": {
            "strategy": "solar_surplus_resistive_water_heater",
            "solar_start_threshold_w": 800.0,
            "solar_factor": 0.90,
            "solar_export_reserve_w": 400.0,
            "minimum_power_w": 500.0,
            "maximum_power_w": 2000.0,
            "boiler_stop_temperature_c": 68.0,
            "boiler_restart_temperature_c": 60.0,
            "element_taper_start_c": 68.0,
            "element_hard_stop_c": 70.0,
            "grid_backup_start_c": 50.0,
            "grid_backup_stop_c": 55.0,
            "keepalive_interval_s": 30.0,
        },
    },
    "generic_heat_pump": {
        "device_type": "heat_pump",
        "label": "Generic Heat Pump",
        "manufacturer": "Generic",
        "capabilities": ["electrical_power", "electrical_energy", "thermal_power", "thermal_energy", "cop"],
        "state_normalization": {"compressor_state": {}, "operating_mode": {}},
        "smart_control": None,
    },
    "buderus_heat_pump": {
        "device_type": "heat_pump",
        "label": "Buderus",
        "manufacturer": "Buderus",
        "capabilities": ["electrical_power", "electrical_energy", "thermal_power", "thermal_energy", "cop", "dhw_temperature", "compressor_state"],
        "state_normalization": {"compressor_state": {}, "operating_mode": {}},
        "smart_control": None,
    },
    "viessmann_vitocal": {
        "device_type": "heat_pump",
        "label": "Viessmann Vitocal",
        "manufacturer": "Viessmann",
        "capabilities": ["electrical_power", "electrical_energy", "thermal_power", "thermal_energy", "cop", "jaz", "dhw_temperature", "dhw_target_temperature", "compressor_state"],
        # Martin/Vitocal field evidence (HA native entity semantics):
        # - binary_sensor.* Kompressor Status uses canonical on/off + device_class running
        # - select.* Betriebsmodus exposes German textual options
        # - sensor.* Systemzustand can be mapped to Zeus System / Activity State
        #   (e.g. standby, Warmwasser_aktiv). Unknown values remain evidence-only.
        "state_normalization": {
            "compressor_state": {},
            "operating_mode": {
                "Aus": "idle",
                "Nur Warmwasser": "dhw",
                "Heizen Kühlen Warmwasser (Zeitprogramm)": "automatic",
            },
        },
        "smart_control": None,
    },
}


def get_device_profile(profile_id: str | None) -> dict[str, Any] | None:
    profile = DEVICE_PROFILES.get(str(profile_id or "").strip())
    return deepcopy(profile) if profile else None


def effective_elwa_control(device: dict[str, Any]) -> dict[str, float]:
    """Return editable ELWA policy values with safe proven defaults.

    Existing installations without a profile keep the exact validated ELWA
    simulator behavior. Explicit per-device values always win over defaults.
    """
    profile = get_device_profile(device.get("device_profile"))
    profile_defaults = (profile or {}).get("smart_control") or {}
    fallback = (DEVICE_PROFILES["my_pv_elwa"].get("smart_control") or {})

    def number(key: str, device_key: str) -> float:
        raw = device.get(device_key)
        if raw in (None, ""):
            raw = profile_defaults.get(key, fallback[key])
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(fallback[key])

    return {
        "solar_start_threshold_w": max(0.0, number("solar_start_threshold_w", "control_solar_start_threshold_w")),
        "solar_factor": max(0.0, min(1.0, number("solar_factor", "control_solar_factor"))),
        "solar_export_reserve_w": max(0.0, number("solar_export_reserve_w", "control_solar_export_reserve_w")),
        "minimum_power_w": max(0.0, number("minimum_power_w", "control_min_power_w")),
        "maximum_power_w": max(0.0, number("maximum_power_w", "control_max_power_w")),
        "boiler_stop_temperature_c": number("boiler_stop_temperature_c", "control_stop_temperature_c"),
        "boiler_restart_temperature_c": number("boiler_restart_temperature_c", "control_restart_temperature_c"),
        "element_taper_start_c": number("element_taper_start_c", "control_element_taper_start_c"),
        "element_hard_stop_c": number("element_hard_stop_c", "control_element_hard_stop_c"),
        "grid_backup_start_c": number("grid_backup_start_c", "control_grid_backup_start_c"),
        "grid_backup_stop_c": number("grid_backup_stop_c", "control_grid_backup_stop_c"),
        "keepalive_interval_s": max(1.0, number("keepalive_interval_s", "control_keepalive_interval_s")),
    }
