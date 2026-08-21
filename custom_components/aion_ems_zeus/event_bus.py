"""Recorder-safe event bus."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .const import DOMAIN


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

    def publish(self, event: str, engine: str, payload: dict[str, Any] | None = None) -> None:
        record = {
            "event": event,
            "engine": engine,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": compact(payload or {}),
        }
        self.events.append(record)
        self.events = self.events[-25:]
        self.hass.bus.async_fire(f"{DOMAIN}_{event}", compact(record))

    def recent(self, limit: int = 3) -> list[dict[str, Any]]:
        return self.events[-limit:]
