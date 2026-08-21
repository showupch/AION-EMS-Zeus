"""Read-only Home Assistant weather context for Zeus forecasting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class WeatherEngine:
    """Select and normalize an available Home Assistant weather entity."""

    CONDITION_FACTORS = {
        "sunny": 1.00,
        "clear-night": 0.00,
        "partlycloudy": 0.72,
        "cloudy": 0.38,
        "fog": 0.28,
        "rainy": 0.22,
        "pouring": 0.12,
        "lightning": 0.15,
        "lightning-rainy": 0.10,
        "snowy": 0.18,
        "snowy-rainy": 0.12,
        "windy": 0.80,
        "windy-variant": 0.65,
        "exceptional": 0.50,
    }

    def __init__(self, hass, event_bus, registry) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.last: dict[str, Any] = {"status": "Waiting", "summary": "Searching for a weather entity."}

    def _configured_entity(self) -> str | None:
        source = self.registry.data.get("sources", {}).get("weather", {})
        value = source.get("entity_id") if source.get("enabled", True) else None
        if not value:
            mappings = self.registry.data.get("entity_mappings", {})
            value = mappings.get("weather_entity") or mappings.get("weather")
            if isinstance(value, dict):
                value = value.get("entity_id")
        return value if isinstance(value, str) and value.startswith("weather.") else None

    def candidates(self) -> list[dict[str, Any]]:
        return [{
            "entity_id": state.entity_id,
            "name": state.attributes.get("friendly_name", state.entity_id),
            "condition": state.state,
            "available": state.state not in ("unknown", "unavailable"),
            "supported_features": state.attributes.get("supported_features", 0),
        } for state in sorted(self.hass.states.async_all("weather"), key=lambda item: item.entity_id)]

    def _select_entity(self) -> str | None:
        configured = self._configured_entity()
        if configured and self.hass.states.get(configured):
            return configured
        candidates = sorted(state.entity_id for state in self.hass.states.async_all("weather"))
        return candidates[0] if candidates else None

    @classmethod
    def factor_for(cls, condition: str | None, cloud_coverage: Any = None) -> float:
        try:
            cloud = max(0.0, min(100.0, float(cloud_coverage)))
            return round(max(0.08, 1.0 - cloud / 115.0), 3)
        except (TypeError, ValueError):
            return cls.CONDITION_FACTORS.get(str(condition or "").lower(), 0.55)

    @classmethod
    def cloud_for_condition(cls, condition: str | None) -> float | None:
        """Return a conservative cloud-cover estimate when HA exposes no cloud metric."""
        mapping = {
            "sunny": 5.0, "clear-night": 5.0, "partlycloudy": 45.0,
            "cloudy": 85.0, "fog": 90.0, "rainy": 90.0, "pouring": 98.0,
            "lightning": 90.0, "lightning-rainy": 95.0, "snowy": 90.0,
            "snowy-rainy": 95.0, "windy": 30.0, "windy-variant": 50.0,
        }
        return mapping.get(str(condition or "").lower())

    def refresh(self) -> dict[str, Any]:
        entity_id = self._select_entity()
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            self.last = {
                "status": "Waiting",
                "entity_id": None,
                "available": False,
                "forecast_available": False,
                "solar_factor": 1.0,
                "configured": bool(self._configured_entity()),
                "candidates": self.candidates(),
                "summary": "No Home Assistant weather entity is available; forecast uses history only.",
                "safety": "Read-only weather context.",
            }
            return self.last

        attrs = state.attributes
        forecast = attrs.get("forecast") if isinstance(attrs.get("forecast"), list) else []
        cloud = attrs.get("cloud_coverage")
        if cloud is None:
            cloud = attrs.get("cloud_cover", attrs.get("cloudiness"))
        condition = state.state
        cloud_source = "measured"
        if cloud is None:
            cloud = self.cloud_for_condition(condition)
            cloud_source = "condition_estimate" if cloud is not None else "unavailable"
        factor = self.factor_for(condition, cloud)
        self.last = {
            "status": "Ready",
            "entity_id": entity_id,
            "available": state.state not in ("unknown", "unavailable"),
            "condition": condition,
            "temperature": attrs.get("temperature"),
            "temperature_unit": attrs.get("temperature_unit"),
            "humidity": attrs.get("humidity"),
            "cloud_coverage": cloud,
            "cloud_coverage_source": cloud_source,
            "wind_speed": attrs.get("wind_speed"),
            "precipitation": attrs.get("precipitation", attrs.get("precipitation_amount", attrs.get("rainfall"))),
            "forecast_available": bool(forecast),
            "forecast": forecast[:48],
            "solar_factor": factor,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "configured": bool(self._configured_entity()),
            "candidates": self.candidates(),
            "provider": attrs.get("attribution"),
            "summary": f"{condition}; solar forecast factor {factor:.0%}.",
            "note": "Zeus uses current weather and any forecast attributes exposed by the selected Home Assistant weather entity.",
            "safety": "Read-only weather context.",
        }
        return self.last

    async def async_refresh_forecast(self) -> dict[str, Any]:
        """Refresh forecast rows using the modern Home Assistant weather service.

        Newer HA versions no longer expose forecast rows in entity attributes.
        Keep current-condition handling synchronous, but augment it here when the
        service is available so Grid Outlook can use real future weather.
        """
        entity_id = self._select_entity()
        if not entity_id:
            return self.last
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True, return_response=True,
            )
            payload = (response or {}).get(entity_id, {}) if isinstance(response, dict) else {}
            rows = payload.get("forecast") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                return self.last
            normalized = []
            for row in rows[:168]:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                if item.get("cloud_coverage") is None:
                    item["cloud_coverage"] = item.get("cloud_cover", item.get("cloudiness"))
                if item.get("precipitation") is None:
                    item["precipitation"] = item.get("precipitation_amount", item.get("rainfall"))
                normalized.append(item)
            if normalized:
                self.last["forecast"] = normalized
                self.last["forecast_available"] = True
                # If current weather omits cloud/precipitation, use the nearest
                # provider forecast row as current weather context rather than
                # leaving every weather-intelligence field empty.
                first = normalized[0]
                if self.last.get("cloud_coverage") is None and first.get("cloud_coverage") is not None:
                    self.last["cloud_coverage"] = first.get("cloud_coverage")
                if self.last.get("precipitation") is None and first.get("precipitation") is not None:
                    self.last["precipitation"] = first.get("precipitation")
            return self.last
        except Exception:
            return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


__all__ = ["WeatherEngine"]
