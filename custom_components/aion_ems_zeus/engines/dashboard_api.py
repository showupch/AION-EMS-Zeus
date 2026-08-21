"""Compact read-only API consumed by the Zeus application."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class DashboardAPI:
    """Aggregate engine state into one stable dashboard payload."""

    SCHEMA_VERSION = 4

    def __init__(self, core) -> None:
        self.core = core
        self.last: dict[str, Any] = {"status": "Waiting"}

    def refresh(self) -> dict[str, Any]:
        registry = self.core.registry.summary()
        energy = self.core.energy_engine.summary()
        analytics = self.core.analytics.summary()
        intelligence = self.core.intelligence.summary()
        self.last = {
            "status": "Ready",
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": self.core.version,
            "home": {
                "energy": energy.get("flow", {}),
                "briefing": self.core.daily_briefing.summary(),
                "recommendations": intelligence.get("recommendations", [])[:3],
                "health": self.core.diagnostics.summary(),
                "data_quality": self.core.data_quality.summary(),
            },
            "energy_flow": energy.get("flow", {}),
            "weather": self.core.weather.summary(),
            "forecast": self.core.forecast.summary(),
            "analytics": analytics,
            "device_analytics": self.core.device_analytics.summary(),
            "daily_briefing": self.core.daily_briefing.summary(),
            "finance": self.core.finance.summary(),
            "optimizer": self.core.optimizer.summary(),
            "data_quality": self.core.data_quality.summary(),
            "intelligence": intelligence,
            "devices": {
                "summary": registry,
                "items": self.core.registry.data.get("devices", []),
                "analytics": self.core.device_analytics.summary(),
            },
            "notifications": self.core.notifications.summary(),
            "system": {
                "update_engine": self.core.update_engine.summary(),
                "diagnostics": self.core.diagnostics.summary(),
                "data_quality": self.core.data_quality.summary(),
                "capability": self.core.capability.summary(),
            },
        }
        return self.last

    def recorder_summary(self) -> dict[str, Any]:
        """Return only stable dashboard metadata for Home Assistant Recorder.

        The complete dashboard payload remains in runtime memory and is consumed
        by the Zeus frontend through the dedicated domain sensors. Publishing the
        aggregate object as entity attributes would duplicate large analytics,
        registry and discovery structures on every state write.
        """
        data = self.last or {}
        devices = data.get("devices") if isinstance(data.get("devices"), dict) else {}
        device_items = devices.get("items") if isinstance(devices.get("items"), list) else []
        intelligence = data.get("intelligence") if isinstance(data.get("intelligence"), dict) else {}
        recommendations = intelligence.get("recommendations") if isinstance(intelligence.get("recommendations"), list) else []
        return {
            "status": data.get("status"),
            "schema_version": data.get("schema_version"),
            "generated_at": data.get("generated_at"),
            "version": data.get("version"),
            "sections": [
                "home", "energy_flow", "weather", "forecast", "analytics",
                "devices", "notifications", "system",
            ],
            "device_count": len(device_items),
            "recommendation_count": len(recommendations),
            "details_storage": "runtime_memory_and_dedicated_domain_entities",
            "recorder_safe": True,
        }

    def summary(self) -> dict[str, Any]:
        return self.last


__all__ = ["DashboardAPI"]
