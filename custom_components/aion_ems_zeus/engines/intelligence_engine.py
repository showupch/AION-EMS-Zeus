"""Confidence-aware explainable intelligence for AION EMS Zeus."""

from __future__ import annotations

from typing import Any


class IntelligenceEngine:
    """Turn live optimizer output into safe, explainable recommendations."""

    def __init__(self, optimizer, knowledge, briefing, data_quality, forecast, energy_flow, registry) -> None:
        self.optimizer = optimizer
        self.knowledge = knowledge
        self.briefing = briefing
        self.data_quality = data_quality
        self.forecast = forecast
        self.energy_flow = energy_flow
        self.registry = registry
        self.last: dict[str, Any] = {"status": "Waiting", "recommendations": []}

    @staticmethod
    def _confidence_label(score: int) -> str:
        return "Very high" if score >= 90 else "High" if score >= 75 else "Moderate" if score >= 55 else "Low"

    @staticmethod
    def _first_window(forecast: dict[str, Any]) -> str | None:
        hourly = forecast.get("hourly") or []
        ranked = sorted(
            (
                item for item in hourly
                if isinstance(item, dict)
                and isinstance(item.get("surplus_power_w"), (int, float))
            ),
            key=lambda item: item.get("surplus_power_w", 0),
            reverse=True,
        )
        return ranked[0].get("time") if ranked and ranked[0].get("surplus_power_w", 0) > 250 else None

    def _explain(self, item: dict[str, Any], system_confidence: int, best_window: str | None) -> dict[str, Any]:
        raw_action = str(item.get("action", "hold") or "hold").strip().lower().replace("_", " ").replace("-", " ")
        action_aliases = {
            "run now": "consider_starting",
            "consider now": "consider_starting",
            "start now": "consider_starting",
            "consider starting": "consider_starting",
            "delay": "consider_delaying_flexible_loads",
            "delay load": "consider_delaying_flexible_loads",
            "delay flexible loads": "consider_delaying_flexible_loads",
            "protect reserve": "protect_low_battery",
            "protect battery": "protect_low_battery",
            "protect low battery": "protect_low_battery",
            "hold": "hold",
        }
        action = action_aliases.get(raw_action, raw_action.replace(" ", "_"))
        try:
            base_confidence = int(float(item.get("confidence", 55)))
        except (TypeError, ValueError):
            base_confidence = 55
        confidence = max(0, min(100, round(base_confidence * system_confidence / 100)))
        result = dict(item)
        result["source_action"] = item.get("action", "Hold")
        result["normalized_action"] = action
        result["confidence"] = confidence
        result["confidence_percent"] = confidence
        result["confidence_label"] = self._confidence_label(confidence)
        result["best_window"] = best_window or "Now" if action == "consider_starting" else best_window or "No urgent action"

        if action == "consider_starting":
            result.update(
                title="Use solar surplus",
                why_now="The home is exporting energy that could be used locally.",
                expected_benefit="Increase solar self-consumption and reduce later grid use.",
                urgency="Now",
            )
        elif action == "consider_delaying_flexible_loads":
            result.update(
                title="Delay device",
                why_now="Grid import is high and there is no meaningful solar surplus.",
                expected_benefit="Avoid adding optional demand during an import peak.",
                urgency="Soon",
            )
        elif action == "protect_low_battery":
            result.update(
                title="Protect battery reserve",
                why_now=item.get("reason", "Battery state of charge is low."),
                expected_benefit="Preserve backup reserve and reduce deep discharge.",
                urgency="High",
            )
        else:
            result.update(
                title="Hold current strategy",
                why_now=item.get("reason", "No strong condition was detected."),
                expected_benefit="Avoid unnecessary device changes while conditions are balanced.",
                urgency="Low",
            )
        result["constraints"] = [
            "Recommendation only — Zeus will not control the device.",
            f"System data confidence: {system_confidence}%.",
        ]
        return result

    def _battery_strategy(self, flows: dict[str, Any]) -> dict[str, Any]:
        def power_w(value: Any) -> float:
            if isinstance(value, dict):
                value = value.get("w")
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        soc = flows.get("battery_soc_percent")
        try:
            soc = float(soc) if soc is not None else None
        except (TypeError, ValueError):
            soc = None
        charge = power_w(flows.get("battery_charge_power"))
        discharge = power_w(flows.get("battery_discharge_power"))
        grid_import = power_w(flows.get("grid_import_power"))
        grid_export = power_w(flows.get("grid_export_power"))
        if soc is None:
            return {"status": "Unavailable", "strategy": "Battery SOC is not mapped."}
        if soc < 20:
            strategy = "Protect reserve"
            reason = "Battery SOC is below the recommended reserve threshold."
        elif grid_export > 300 and soc < 95:
            strategy = "Prefer charging"
            reason = "Solar surplus is available and the battery has capacity."
        elif grid_import > 300 and soc > 35:
            strategy = "Support the home"
            reason = "Grid import is present while usable battery reserve remains."
        elif charge > 10:
            strategy = "Charging normally"
            reason = "The battery is currently absorbing energy."
        elif discharge > 10:
            strategy = "Discharging normally"
            reason = "The battery is currently supplying the home."
        else:
            strategy = "Hold"
            reason = "No strong battery action is indicated."
        return {"status": "Ready", "strategy": strategy, "reason": reason, "soc_percent": soc}

    def refresh(self) -> dict[str, Any]:
        optimizer = self.optimizer.refresh()
        knowledge = self.knowledge.refresh()
        briefing = self.briefing.refresh()
        quality = self.data_quality.summary()
        forecast = self.forecast.summary()
        flow = self.energy_flow.summary()
        flows = flow.get("flows", {})
        try:
            system_confidence = int(float(quality.get("confidence_score", 50) or 50))
        except (TypeError, ValueError):
            system_confidence = 50
        best_window = self._first_window(forecast)
        raw_recommendations = optimizer.get("recommendations", [])
        if not isinstance(raw_recommendations, list):
            raw_recommendations = []
        explained = [
            self._explain(item, system_confidence, best_window)
            for item in raw_recommendations
            if isinstance(item, dict)
        ]

        # Keep one meaningful recommendation per device. Generic Hold results are
        # intentionally collapsed into a single system-status card so six idle
        # flexible devices do not create six identical blocks.
        recommendations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in explained:
            if item.get("normalized_action") == "hold":
                continue
            device_key = str(item.get("device_id") or item.get("device_name") or "system")
            key = (device_key, str(item.get("normalized_action") or item.get("source_action") or ""))
            if key in seen:
                continue
            seen.add(key)
            recommendations.append(item)

        if not recommendations:
            hold = next((item for item in explained if item.get("normalized_action") == "hold"), {})
            recommendations = [{
                "device_id": "system",
                "device_name": "Energy system",
                "device_type": "system",
                "device_icon": "mdi:check-circle-outline",
                "is_system_status": True,
                "title": "No device action needed",
                "source_action": "Hold",
                "normalized_action": "hold",
                "why_now": hold.get("why_now") or "No strong live optimization opportunity is present.",
                "reason": hold.get("reason") or "No strong live optimization opportunity is present.",
                "expected_benefit": "Zeus will notify you when a device-specific opportunity appears.",
                "urgency": "Low",
                "confidence": hold.get("confidence", system_confidence),
                "confidence_percent": hold.get("confidence_percent", system_confidence),
                "confidence_label": hold.get("confidence_label", self._confidence_label(system_confidence)),
                "best_window": "No urgent action",
                "estimated_saving": 0,
                "currency": hold.get("currency", "CHF"),
                "constraints": ["Recommendation only — Zeus will not control devices."],
            }]

        recommendations.sort(key=lambda item: (item.get("urgency") == "High", item.get("confidence", 0)), reverse=True)
        top = recommendations[0] if recommendations else None
        self.last = {
            "status": "Ready",
            "mode": "explainable_recommendation_only",
            "recommendation_count": len(recommendations),
            "recommendations": recommendations,
            "top_recommendation": top,
            "system_confidence": system_confidence,
            "confidence_label": quality.get("confidence_label", self._confidence_label(system_confidence)),
            "best_surplus_window": best_window,
            "battery_strategy": self._battery_strategy(flows),
            "optimizer": optimizer,
            "knowledge": knowledge,
            "briefing": briefing,
            "summary": (top or {}).get("why_now") or optimizer.get("summary") or "Zeus intelligence is ready.",
            "limitations": [
                "Forecast is history-based until weather integration is configured.",
                "Tariff-aware savings require tariff data in a future release.",
            ],
            "safety": "Recommendations only. No autonomous device control.",
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


__all__ = ["IntelligenceEngine"]
