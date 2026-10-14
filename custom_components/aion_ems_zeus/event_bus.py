"""Recorder-safe event bus."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Any

from .const import DOMAIN

# These are frequent status notifications, not historical measurements.
# Limit their publication to the Home Assistant event bus while keeping
# every internal update and audit callback intact.
HIGH_VOLUME_STATUS_EVENTS = frozenset({
    "EnergyMappingUpdated",
    "EnergyFlowUpdated",
    "ObservationKnowledgeUpdated",
    "DataBusUpdated",
    "UpdateEngineRefreshed",
    "EnergyTopologyUpdated",
})
STATUS_EVENT_MIN_INTERVAL_SECONDS = 60.0


def compact(value: Any, depth: int = 0) -> Any:
    """Compact nested data for recorder safety."""
    if depth > 2:
        return "..."
    if isinstance(value, dict):
        out = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 8:
                out["_truncated"] = True
                break
            out[key] = compact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[:5]]
    if isinstance(value, str) and len(value) > 160:
        return value[:160] + "..."
    return value


class AionEventBus:
    """Internal recorder-safe event bus."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self.events: list[dict[str, Any]] = []
        self.audit_sink = None
        self._last_ha_status_event: dict[str, float] = {}
        self._suppressed_ha_status_events: dict[str, int] = {}

    def publish(self, event: str, engine: str, payload: dict[str, Any] | None = None) -> None:
        record = {
            "event": event,
            "engine": engine,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": compact(payload or {}),
        }
        self.events.append(record)
        self.events = self.events[-25:]
        sink = getattr(self, "audit_sink", None)
        if sink is not None:
            try:
                sink(record)
            except Exception:
                pass
        if event in HIGH_VOLUME_STATUS_EVENTS:
            now = monotonic()
            previous = self._last_ha_status_event.get(event)
            if previous is not None and now - previous < STATUS_EVENT_MIN_INTERVAL_SECONDS:
                self._suppressed_ha_status_events[event] = (
                    self._suppressed_ha_status_events.get(event, 0) + 1
                )
                return
            self._last_ha_status_event[event] = now
        self.hass.bus.async_fire(f"{DOMAIN}_{event}", compact(record))

    def recent(self, limit: int = 3) -> list[dict[str, Any]]:
        return self.events[-limit:]
