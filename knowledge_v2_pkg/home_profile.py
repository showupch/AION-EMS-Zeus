"""Home Profile Engine for AION EMS Zeus v12.5.

Derives a compact, explainable profile from measured Intelligence Memory days
and the Device Registry. It never controls devices and does not create a new
sampling loop.
"""
from __future__ import annotations

from datetime import date
from statistics import mean, median
from typing import Any


class HomeProfileEngine:
    """Learn stable characteristics of the home from measured history."""

    VERSION = "1.0-alpha.1"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Learning",
            "version": self.VERSION,
            "learning_days": 0,
            "confidence_percent": 0,
            "summary": "Zeus is waiting for measured daily history.",
        }

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _values(cls, days: list[dict[str, Any]], key: str) -> list[float]:
        values: list[float] = []
        for item in days:
            value = cls._num(item.get(key))
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _round(value: float | None, digits: int = 1) -> float | None:
        return round(value, digits) if value is not None else None

    @classmethod
    def _metric_profile(cls, values: list[float]) -> dict[str, Any]:
        if not values:
            return {"available": False, "sample_count": 0}
        ordered = sorted(values)
        lower = ordered[max(0, int((len(ordered) - 1) * 0.25))]
        upper = ordered[max(0, int((len(ordered) - 1) * 0.75))]
        return {
            "available": True,
            "sample_count": len(values),
            "average": cls._round(mean(values)),
            "median": cls._round(median(values)),
            "typical_low": cls._round(lower),
            "typical_high": cls._round(upper),
            "minimum": cls._round(min(values)),
            "maximum": cls._round(max(values)),
        }

    @staticmethod
    def _weekday_split(days: list[dict[str, Any]], key: str) -> dict[str, Any]:
        weekday: list[float] = []
        weekend: list[float] = []
        for item in days:
            try:
                day = date.fromisoformat(str(item.get("date")))
                value = float(item.get(key))
            except (TypeError, ValueError):
                continue
            (weekend if day.weekday() >= 5 else weekday).append(value)
        return {
            "weekday_average": round(mean(weekday), 1) if weekday else None,
            "weekend_average": round(mean(weekend), 1) if weekend else None,
            "weekday_samples": len(weekday),
            "weekend_samples": len(weekend),
        }

    def _registry_profile(self) -> dict[str, Any]:
        registry_data = getattr(getattr(self.core, "registry", None), "data", {}) or {}
        devices = [d for d in registry_data.get("devices", []) if d.get("enabled", True)]
        flexible_keywords = ("dishwasher", "washing", "washer", "dryer", "ev", "charger", "water_heater", "water heater", "heat_pump", "heat pump", "pool")
        flexible: list[dict[str, Any]] = []
        categories: dict[str, int] = {}
        for device in devices:
            label = str(device.get("friendly_name") or device.get("name") or device.get("device_name") or device.get("id") or "Device")
            category = str(device.get("category") or device.get("device_type") or "other")
            categories[category] = categories.get(category, 0) + 1
            haystack = " ".join(str(device.get(k) or "") for k in ("category", "device_type", "name", "friendly_name", "id")).lower()
            if any(keyword in haystack for keyword in flexible_keywords):
                flexible.append({"id": device.get("id") or device.get("device_id"), "name": label, "category": category})
        return {
            "device_count": len(devices),
            "flexible_load_count": len(flexible),
            "flexible_loads": flexible[:12],
            "categories": categories,
        }

    def refresh(self) -> dict[str, Any]:
        memory = getattr(self.core, "intelligence_memory", None)
        memory_summary = memory.summary() if memory and hasattr(memory, "summary") else {}
        days = list(memory_summary.get("recent_days") or [])
        days = [item for item in days if isinstance(item, dict)]
        learning_days = len(days)
        confidence = min(100, round(learning_days / 30 * 100))

        solar = self._metric_profile(self._values(days, "solar_energy_kwh"))
        home = self._metric_profile(self._values(days, "house_energy_kwh"))
        grid_import = self._metric_profile(self._values(days, "grid_import_energy_kwh"))
        grid_export = self._metric_profile(self._values(days, "grid_export_energy_kwh"))
        battery_charge = self._metric_profile(self._values(days, "battery_charge_energy_kwh"))
        battery_discharge = self._metric_profile(self._values(days, "battery_discharge_energy_kwh"))
        self_consumption = self._metric_profile(self._values(days, "self_consumption_percent"))
        self_sufficiency = self._metric_profile(self._values(days, "self_sufficiency_percent"))
        registry = self._registry_profile()

        patterns: list[str] = []
        if solar.get("available"):
            patterns.append(f"Typical measured solar production is {solar['typical_low']}–{solar['typical_high']} kWh/day.")
        if home.get("available"):
            patterns.append(f"Typical measured home demand is {home['typical_low']}–{home['typical_high']} kWh/day.")
        if grid_export.get("available") and grid_export.get("average", 0) > 0:
            patterns.append(f"Average measured grid export is {grid_export['average']} kWh/day.")
        if registry.get("flexible_load_count"):
            patterns.append(f"Zeus recognizes {registry['flexible_load_count']} flexible load(s) for recommendation context.")

        status = "Ready" if learning_days >= 7 else ("Learning" if learning_days else "Waiting")
        self.last = {
            "status": status,
            "version": self.VERSION,
            "learning_days": learning_days,
            "confidence_percent": confidence,
            "confidence_label": "High" if confidence >= 80 else "Medium" if confidence >= 40 else "Low",
            "solar_profile": solar,
            "household_profile": {**home, **self._weekday_split(days, "house_energy_kwh")},
            "grid_profile": {"import": grid_import, "export": grid_export},
            "battery_profile": {"charge": battery_charge, "discharge": battery_discharge},
            "efficiency_profile": {"self_consumption": self_consumption, "self_sufficiency": self_sufficiency},
            "registry_profile": registry,
            "patterns": patterns[:8],
            "summary": (
                f"Home profile uses {learning_days} measured day(s) with {confidence}% confidence."
                if learning_days else "Zeus is waiting for measured daily history."
            ),
            "source": "Intelligence Memory measured daily snapshots and Device Registry",
            "recorder_safe": True,
            "safety": "Profile intelligence only. No device control.",
        }
        try:
            self.event_bus.publish("HomeProfileUpdated", "HomeProfileEngine", {"learning_days": learning_days, "confidence": confidence})
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
