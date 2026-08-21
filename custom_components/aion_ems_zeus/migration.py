"""AION EMS Helios migration preview."""

from __future__ import annotations

from typing import Any


class MigrationEngine:
    """Read-only Helios migration placeholder."""

    def __init__(self, hass, event_bus, registry) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.last_analysis = {"status": "Not run"}
        self.last_preview = {"status": "Not run", "devices": [], "warnings": []}
        self.last_smart = {"status": "Not run"}

    def analyze(self):
        helpers = [
            "sensor.ai_device_registry",
            "input_text.ai_setup_grid_power_entity",
            "input_text.ai_setup_battery_soc_entity",
        ]
        found = [{"entity_id": e, "exists": self.hass.states.get(e) is not None} for e in helpers]
        self.last_analysis = {"status": "Ready", "helpers": found, "summary": "Helios analysis completed.", "safety": "Read-only."}
        return self.last_analysis

    def build_preview(self):
        self.last_preview = {"status": "Ready", "source": "Helios analyzer", "devices": [], "warnings": [], "devices_found": 0, "system_entities_found": 0, "safety": "Preview only."}
        return self.last_preview

    def smart_report(self):
        self.last_smart = {"status": "Ready", "ready_device_count": 0, "blocked_device_count": 0, "recommendation": "No devices detected by v5 placeholder migration.", "safety": "Report only."}
        return self.last_smart

    async def async_apply_preview(self):
        return []

    def summary(self):
        return {"status": self.last_preview.get("status", "Not run"), "devices_found": len(self.last_preview.get("devices", [])), "warnings": self.last_preview.get("warnings", []), "safety": "No Helios changes."}
