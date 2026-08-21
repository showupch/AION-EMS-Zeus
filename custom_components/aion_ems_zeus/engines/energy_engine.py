"""Unified energy calculation facade."""

from __future__ import annotations

from typing import Any

from ..energy_mapping import EnergyMappingEngine
from ..energy_flow import EnergyFlowEngine


class EnergyEngine:
    """Own energy mappings, normalization and live power-flow calculations."""

    def __init__(self, hass, event_bus, registry) -> None:
        self.mapping = EnergyMappingEngine(hass, event_bus, registry)
        self.flow = EnergyFlowEngine(event_bus, self.mapping, registry)

    def refresh(self) -> dict[str, Any]:
        self.mapping.refresh()
        return self.flow.refresh()

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.flow.summary().get("status", "Waiting"),
            "mapping": self.mapping.summary(),
            "flow": self.flow.summary(),
            "safety": "Read-only calculations. No device control.",
        }


__all__ = ["EnergyEngine"]
