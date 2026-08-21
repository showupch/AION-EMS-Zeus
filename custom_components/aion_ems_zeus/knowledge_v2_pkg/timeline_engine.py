"""Recorder-safe knowledge timeline for AION EMS Zeus 12.1."""
from __future__ import annotations

from datetime import datetime
from typing import Any


class KnowledgeTimelineEngine:
    """Turn recent measured snapshots into a compact, human timeline."""

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": "1.0-beta.1",
            "event_count": 0,
            "events": [],
            "summary": "Waiting for measured energy history.",
        }

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def refresh(self) -> dict[str, Any]:
        snapshots = list(getattr(self.core.data_lake, "data", {}).get("snapshots", []) or [])[-288:]
        events: list[dict[str, Any]] = []
        previous_state: dict[str, bool] = {}
        best_solar = (0.0, None)

        for snap in snapshots:
            flows = snap.get("flows", {}) if isinstance(snap, dict) else {}
            timestamp = snap.get("timestamp")
            solar = self._number(flows.get("solar_power_w"))
            export = self._number(flows.get("grid_export_power_w"))
            battery_soc = self._number(flows.get("battery_soc_percent"))
            home = self._number(flows.get("house_power_w"))
            if solar > best_solar[0]:
                best_solar = (solar, timestamp)

            states = {
                "solar_active": solar >= 500,
                "exporting": export >= 250,
                "battery_full": battery_soc >= 98,
                "high_demand": home >= 3000,
            }
            labels = {
                "solar_active": ("Solar production started", f"Solar reached {solar/1000:.1f} kW."),
                "exporting": ("Grid export started", f"Export reached {export/1000:.1f} kW."),
                "battery_full": ("Battery reached full reserve", f"Battery SOC reached {battery_soc:.0f}%."),
                "high_demand": ("High home demand", f"Home demand reached {home/1000:.1f} kW."),
            }
            for key, active in states.items():
                if active and not previous_state.get(key, False):
                    title, detail = labels[key]
                    events.append({"timestamp": timestamp, "title": title, "detail": detail, "type": key})
                previous_state[key] = active

        if best_solar[1] and best_solar[0] > 0:
            events.append({
                "timestamp": best_solar[1],
                "title": "Highest recent solar production",
                "detail": f"Solar peaked at {best_solar[0]/1000:.1f} kW.",
                "type": "solar_peak",
            })

        memory = getattr(self.core, "intelligence_memory", None)
        if memory is not None:
            for item in memory.summary().get("memory_events", []):
                events.append({
                    "timestamp": item.get("timestamp"),
                    "title": item.get("title", "Intelligence Memory updated"),
                    "detail": f"Measured value: {item.get('value')}" if item.get("value") is not None else "A meaningful historical event was remembered.",
                    "type": item.get("type", "memory"),
                    "category": "Knowledge",
                    "severity": item.get("severity", "Important"),
                })

        anomaly = getattr(self.core, "anomaly_intelligence", None)
        if anomaly is not None:
            for item in anomaly.summary().get("observations", [])[:6]:
                events.append({
                    "timestamp": item.get("date"),
                    "title": item.get("title", "Home observation"),
                    "detail": item.get("detail", "Measured performance differs from the learned profile."),
                    "type": "anomaly_observation",
                    "category": "Knowledge",
                    "severity": item.get("severity", "Information"),
                })
        unique = {}
        for item in events:
            unique[(item.get("timestamp"), item.get("title"))] = item
        events = sorted(unique.values(), key=lambda item: item.get("timestamp") or "")[-20:]
        self.last = {
            "status": "Ready" if snapshots else "Learning",
            "version": "1.0-beta.1",
            "event_count": len(events),
            "events": events,
            "latest_event": events[-1] if events else None,
            "summary": f"Zeus identified {len(events)} meaningful recent energy event(s)." if events else "Zeus is collecting enough history to build a timeline.",
            "recorder_safe": True,
            "safety": "Observation only. No device control.",
        }
        try:
            self.event_bus.publish("KnowledgeTimelineUpdated", "KnowledgeTimelineEngine", {"event_count": len(events)})
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
