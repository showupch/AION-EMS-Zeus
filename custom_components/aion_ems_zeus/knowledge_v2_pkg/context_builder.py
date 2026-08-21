"""Build a normalized, compact intelligence context from Zeus engines."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ContextBuilder:
    """Collect only the compact facts needed by the intelligence layer."""

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def build(self, core: Any) -> dict[str, Any]:
        flow = self._summary(core.energy_flow)
        forecast = self._summary(core.forecast)
        finance = self._summary(core.finance)
        learning = self._summary(core.learning)
        optimizer = self._summary(core.optimizer)
        weather = self._summary(core.weather)
        registry = self._summary(core.registry)
        quality = self._summary(core.data_quality)
        battery = self._summary(core.predictive_battery)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "energy": {
                "solar_w": flow.get("solar_power_w", flow.get("solar_w")),
                "home_w": flow.get("home_power_w", flow.get("home_w")),
                "grid_import_w": flow.get("grid_import_power_w", flow.get("grid_import_w")),
                "grid_export_w": flow.get("grid_export_power_w", flow.get("grid_export_w")),
                "battery_soc_percent": flow.get("battery_soc_percent", flow.get("battery_soc")),
                "battery_charge_w": flow.get("battery_charge_power_w"),
                "battery_discharge_w": flow.get("battery_discharge_power_w"),
                "operating_state": flow.get("operating_state", flow.get("status")),
            },
            "forecast": {
                "status": forecast.get("status"),
                "confidence": forecast.get("confidence"),
                "solar_next_24h_kwh": forecast.get("expected_solar_next_24h_kwh"),
                "consumption_next_24h_kwh": forecast.get("expected_consumption_next_24h_kwh"),
                "best_surplus_window": forecast.get("best_surplus_window"),
            },
            "finance": {
                "status": finance.get("status"),
                "savings_today": finance.get("savings_today", finance.get("saved_today")),
                "currency": finance.get("currency", "CHF"),
            },
            "learning": {
                "status": learning.get("status"),
                "confidence": learning.get("confidence"),
                "confidence_label": learning.get("confidence_label"),
                "history_days": learning.get("history_days", learning.get("days")),
            },
            "optimizer": {
                "status": optimizer.get("status"),
                "recommendation": optimizer.get("recommendation", optimizer.get("summary")),
                "reason": optimizer.get("reason"),
                "confidence": optimizer.get("confidence", optimizer.get("confidence_percent")),
            },
            "weather": {
                "status": weather.get("status"),
                "condition": weather.get("condition"),
                "temperature": weather.get("temperature"),
            },
            "battery": {
                "status": battery.get("status"),
                "recommendation": battery.get("recommendation"),
                "confidence": battery.get("confidence"),
            },
            "platform": {
                "device_count": registry.get("device_count", 0),
                "quality_score": quality.get("confidence_score", quality.get("score")),
                "quality_status": quality.get("status"),
            },
        }
