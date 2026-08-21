"""Device import defaults."""

from __future__ import annotations

DEFAULTS = {
    "dishwasher": {"category": "flexible_load", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:dishwasher", "room_id": "kitchen"},
    "washing_machine": {"category": "flexible_load", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:washing-machine", "room_id": "laundry"},
    "dryer": {"category": "flexible_load", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:tumble-dryer", "room_id": "laundry"},
    "ev_charger": {"category": "vehicle", "group_ids": ["ev_charging", "flexible_loads"], "priority": "high", "icon": "mdi:ev-station", "room_id": "garage"},
    "heat_pump": {"category": "climate", "group_ids": ["heating"], "priority": "high", "icon": "mdi:heat-pump", "room_id": "utility"},
    "water_heater": {"category": "flexible_load", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:water-boiler", "room_id": "utility"},
    "battery": {"category": "storage", "group_ids": ["battery"], "priority": "high", "icon": "mdi:battery", "room_id": "utility"},
    "solar_inverter": {"category": "generation", "group_ids": ["solar"], "priority": "high", "icon": "mdi:solar-power-variant", "room_id": "utility"},
    "pool_pump": {"category": "flexible_load", "group_ids": ["flexible_loads"], "priority": "low", "icon": "mdi:pool", "room_id": "unassigned"},
    "air_conditioner": {"category": "climate", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:air-conditioner", "room_id": "unassigned"},
    "smart_plug": {"category": "load", "group_ids": ["flexible_loads"], "priority": "medium", "icon": "mdi:power-socket-eu", "room_id": "unassigned"},
    "custom": {"category": "other", "group_ids": [], "priority": "medium", "icon": "mdi:power-plug", "room_id": "unassigned"},
}


class DeviceImportManager:
    """Holds latest import result and defaults."""

    def __init__(self):
        self.last_validation = {"status": "Not run", "message": "No device import has run.", "issues": []}

    def defaults_for(self, device_type: str):
        return DEFAULTS.get(device_type, DEFAULTS["custom"])
