"""Settings API facade for the unified Zeus application."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..period_authority import trusted_data_epoch


class SettingsAPI:
    """Expose configuration state without allowing UI code to touch storage."""

    SCHEMA_VERSION = 1

    def __init__(self, registry, energy_mapping) -> None:
        self.registry = registry
        self.energy_mapping = energy_mapping
        self.last: dict[str, Any] = {"status": "Waiting"}

    def refresh(self) -> dict[str, Any]:
        data = self.registry.data
        self.last = {
            "status": "Ready",
            "schema_version": self.SCHEMA_VERSION,
            "energy_sources": deepcopy(data.get("entity_mappings", {})),
            "devices": deepcopy(data.get("devices", [])),
            "rooms": deepcopy(data.get("rooms", [])),
            "groups": deepcopy(data.get("groups", [])),
            "sources": deepcopy(data.get("sources", {})),
            "home_settings": deepcopy(data.get("home_settings", {})),
            "mapping_validation": self.energy_mapping.summary(),
            "persistence": "Home Assistant storage-backed Registry",
            "safety": "Settings changes are validated by backend services before persistence.",
            "data_epoch": trusted_data_epoch().isoformat() if trusted_data_epoch() else None,
            "data_epoch_mode": "custom" if trusted_data_epoch() else "full_history",
            "recorder_history_preserved": True,
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


__all__ = ["SettingsAPI"]
