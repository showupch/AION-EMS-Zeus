"""Storage-backed Intelligence Memory for AION EMS Zeus."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store

STORAGE_VERSION = 1
STORAGE_KEY = "aion_ems_zeus.intelligence_memory"
MAX_DAYS = 400


class IntelligenceMemoryEngine:
    """Retain compact measured daily context and derive meaningful memories."""

    def __init__(self, hass: Any, event_bus: Any, core: Any) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.core = core
        self.store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {"days": {}, "events": [], "metadata": {"retention_days": MAX_DAYS}}
        self.last: dict[str, Any] = {"status": "Waiting", "day_count": 0, "records": {}, "similar_days": []}

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data.update(stored)
        self.data.setdefault("days", {})
        self.data.setdefault("events", [])
        self.data.setdefault("metadata", {"retention_days": MAX_DAYS})
        self.refresh()

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    def _daily_snapshot(self) -> dict[str, Any] | None:
        lake = getattr(self.core.data_lake, "data", {})
        day = datetime.now(timezone.utc).date().isoformat()
        source = dict((lake.get("daily_summaries", {}) or {}).get(day, {}) or {})
        if not source:
            return None
        solar = self._num(source.get("solar_energy_kwh"))
        home = self._num(source.get("house_energy_kwh"))
        imported = self._num(source.get("grid_import_energy_kwh"))
        exported = self._num(source.get("grid_export_energy_kwh"))
        charge = self._num(source.get("battery_charge_energy_kwh"))
        discharge = self._num(source.get("battery_discharge_energy_kwh"))
        self_consumption = None
        if solar is not None and solar > 0 and exported is not None:
            self_consumption = max(0.0, min(100.0, (solar - exported) / solar * 100.0))
        self_sufficiency = None
        if home is not None and home > 0 and imported is not None:
            self_sufficiency = max(0.0, min(100.0, (home - imported) / home * 100.0))
        weather = getattr(self.core, "weather", None)
        weather_summary = weather.summary() if weather and hasattr(weather, "summary") else {}
        forecast = getattr(self.core, "forecast", None)
        forecast_summary = forecast.summary() if forecast and hasattr(forecast, "summary") else {}
        finance = getattr(self.core, "finance", None)
        finance_summary = finance.summary() if finance and hasattr(finance, "summary") else {}
        return {
            "date": day,
            "solar_energy_kwh": solar,
            "house_energy_kwh": home,
            "grid_import_energy_kwh": imported,
            "grid_export_energy_kwh": exported,
            "battery_charge_energy_kwh": charge,
            "battery_discharge_energy_kwh": discharge,
            "self_consumption_percent": round(self_consumption, 1) if self_consumption is not None else None,
            "self_sufficiency_percent": round(self_sufficiency, 1) if self_sufficiency is not None else None,
            "peak_solar_power_w": self._num(source.get("peak_solar_power_w")),
            "avg_quality_score": self._num(source.get("avg_quality_score")),
            "savings": self._num(finance_summary.get("savings_today") or finance_summary.get("daily_savings")),
            "export_income": self._num(finance_summary.get("export_income_today") or finance_summary.get("daily_export_income")),
            "weather": weather_summary.get("condition") or weather_summary.get("summary"),
            "forecast_confidence": self._num(forecast_summary.get("confidence") or forecast_summary.get("confidence_percent")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "measured_daily_summary",
        }

    @staticmethod
    def _record(days: list[dict[str, Any]], field: str, mode: str = "max") -> dict[str, Any] | None:
        valid = [x for x in days if isinstance(x.get(field), (int, float))]
        if not valid:
            return None
        item = (min if mode == "min" else max)(valid, key=lambda x: x[field])
        ordered = sorted(valid, key=lambda x: x[field], reverse=(mode != "min"))
        previous = ordered[1] if len(ordered) > 1 else None
        return {"date": item["date"], "value": item[field], "previous_value": previous[field] if previous else None, "source": item.get("source")}

    def _records(self, days: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "highest_solar": self._record(days, "solar_energy_kwh"),
            "highest_export": self._record(days, "grid_export_energy_kwh"),
            "lowest_grid_import": self._record(days, "grid_import_energy_kwh", "min"),
            "best_self_consumption": self._record(days, "self_consumption_percent"),
            "best_self_sufficiency": self._record(days, "self_sufficiency_percent"),
            "highest_savings": self._record(days, "savings"),
            "best_battery_utilization": self._record(days, "battery_discharge_energy_kwh"),
        }

    def _similar_days(self, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(days) < 2:
            return []
        today = days[-1]
        fields = ("solar_energy_kwh", "house_energy_kwh", "grid_export_energy_kwh", "grid_import_energy_kwh")
        matches = []
        for item in days[:-1]:
            scores = []
            for field in fields:
                a, b = self._num(today.get(field)), self._num(item.get(field))
                if a is not None and b is not None:
                    scale = max(abs(a), abs(b), 1.0)
                    scores.append(max(0.0, 1.0 - abs(a - b) / scale))
            if scores:
                matches.append({**item, "similarity_percent": round(sum(scores) / len(scores) * 100, 1)})
        return sorted(matches, key=lambda x: x["similarity_percent"], reverse=True)[:8]

    def refresh(self) -> dict[str, Any]:
        days = sorted(self.data.get("days", {}).values(), key=lambda x: x.get("date", ""))
        records = self._records(days)
        similar = self._similar_days(days)
        self.last = {
            "status": "Ready" if days else "Learning",
            "version": "1.0-alpha.1",
            "day_count": len(days),
            "retention_days": MAX_DAYS,
            "records": records,
            "similar_days": similar,
            "recent_days": days[-31:],
            "memory_events": list(self.data.get("events", []))[-20:],
            "summary": f"Zeus remembers {len(days)} measured daily snapshot(s)." if days else "Zeus is waiting for the first measured daily snapshot.",
            "storage": "compact_storage_backed",
            "recorder_safe": True,
            "safety": "Historical intelligence only. No device control.",
        }
        return self.last

    async def async_capture_today(self) -> None:
        snapshot = self._daily_snapshot()
        if not snapshot:
            return
        days = self.data.setdefault("days", {})
        before = self._records(sorted(days.values(), key=lambda x: x.get("date", "")))
        days[snapshot["date"]] = snapshot
        for key in sorted(days)[:-MAX_DAYS]:
            days.pop(key, None)
        after = self._records(sorted(days.values(), key=lambda x: x.get("date", "")))
        events = self.data.setdefault("events", [])
        labels = {
            "highest_solar": "New highest solar day",
            "highest_export": "New highest export day",
            "lowest_grid_import": "New lowest grid import day",
            "best_self_consumption": "New self-consumption record",
            "best_self_sufficiency": "New self-sufficiency record",
            "highest_savings": "New daily savings record",
            "best_battery_utilization": "New battery utilization record",
        }
        for key, record in after.items():
            if record and record.get("date") == snapshot["date"] and record != before.get(key):
                event = {"timestamp": snapshot["updated_at"], "date": snapshot["date"], "type": key, "title": labels[key], "value": record.get("value"), "category": "Knowledge", "severity": "Important"}
                if not any(x.get("date") == event["date"] and x.get("type") == key for x in events):
                    events.append(event)
                    try:
                        self.event_bus.publish("IntelligenceMemoryRecord", "IntelligenceMemoryEngine", event)
                    except Exception:
                        pass
        self.data["events"] = events[-100:]
        await self.store.async_save(self.data)
        self.refresh()

    def summary(self) -> dict[str, Any]:
        return self.last
