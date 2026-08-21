"""Recommendation-only scenario comparison for AION EMS Zeus."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ScenarioSimulator:
    """Compare advisory execution choices without calling Home Assistant services."""

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core
        self._summary: dict[str, Any] = {
            "status": "Waiting",
            "mode": "recommendation_only",
            "control_permission": False,
            "scenarios": [],
        }

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def refresh(self) -> dict[str, Any]:
        decision = self.core.decision_engine.summary() or {}
        opportunities = decision.get("eligible_opportunities") or decision.get("opportunities") or []
        opportunity = opportunities[0] if isinstance(opportunities, list) and opportunities else {}
        title = str(opportunity.get("title") or "No verified opportunity")
        target = str(opportunity.get("target_name") or "")
        confidence = int(self._number(opportunity.get("confidence_percent"), 0))
        export_w = self._number((self.core.energy_flow.summary() or {}).get("grid_export_power_w") or (self.core.energy_flow.summary() or {}).get("grid_export_power"), 0)
        rated_w = self._number(opportunity.get("rated_power_w"), export_w if export_w > 0 else 1000)
        covered_w = min(max(0, export_w), max(0, rated_w))
        duration_h = max(0.25, self._number(opportunity.get("estimated_duration_hours"), 1.0))
        solar_kwh = covered_w * duration_h / 1000
        load_kwh = max(rated_w, covered_w) * duration_h / 1000
        import_kwh = max(0.0, load_kwh - solar_kwh)
        finance = self.core.finance.summary() or {}
        import_rate = self._number(finance.get("import_price_per_kwh") or finance.get("grid_import_rate"), 0.30)
        export_rate = self._number(finance.get("export_price_per_kwh") or finance.get("grid_export_rate"), 0.08)
        offpeak_rate = self._number(finance.get("offpeak_price_per_kwh"), import_rate * 0.65)
        now_value = solar_kwh * import_rate + solar_kwh * export_rate
        later_solar = solar_kwh * 0.55
        later_import = max(0.0, load_kwh - later_solar)
        night_import = load_kwh

        def scenario(identifier: str, label: str, window: str, solar: float, grid_import: float,
                     export_reduction: float, cost: float, saving: float, score: int, risk: str, note: str) -> dict[str, Any]:
            return {
                "id": identifier, "label": label, "window": window,
                "solar_used_kwh": round(solar, 2), "grid_import_kwh": round(grid_import, 2),
                "export_reduction_kwh": round(export_reduction, 2), "battery_impact_kwh": 0.0,
                "estimated_cost": round(max(0.0, cost), 2), "estimated_saving": round(max(0.0, saving), 2),
                "confidence_percent": max(0, min(99, confidence - (0 if identifier == "now" else 4))),
                "scenario_score": max(0, min(100, score)), "risk": risk, "note": note,
            }

        if opportunity:
            scenarios = [
                scenario("now", "Run now", str(opportunity.get("best_window") or "Now"), solar_kwh, import_kwh,
                         solar_kwh, import_kwh * import_rate, now_value, 96, "Low", "Uses the currently measured solar surplus."),
                scenario("later", "Run later", "Next forecast window", later_solar, later_import,
                         later_solar, later_import * import_rate, later_solar * (import_rate + export_rate), 78, "Medium", "Solar coverage may fall before execution."),
                scenario("offpeak", "Run off-peak", "Cheapest tariff window", 0.0, night_import,
                         0.0, night_import * offpeak_rate, max(0.0, night_import * (import_rate - offpeak_rate)), 70, "Low", "Uses lower grid price but no expected solar coverage."),
                scenario("none", "Do nothing", "No execution", 0.0, 0.0,
                         0.0, 0.0, 0.0, 35, "None", "Keeps current operation and exports any available surplus."),
            ]
            scenarios.sort(key=lambda item: item["scenario_score"], reverse=True)
            best = scenarios[0]
            status = "Ready"
        else:
            scenarios = []
            best = None
            status = "Waiting for a verified opportunity"

        self._summary = {
            "status": status,
            "mode": "recommendation_only",
            "control_permission": False,
            "title": title,
            "target_name": target or None,
            "opportunity_id": opportunity.get("id") if opportunity else None,
            "best_scenario": best,
            "scenarios": scenarios,
            "assumptions": {
                "duration_hours": round(duration_h, 2),
                "estimated_load_kwh": round(load_kwh, 2),
                "import_price_per_kwh": round(import_rate, 4),
                "export_price_per_kwh": round(export_rate, 4),
                "offpeak_price_per_kwh": round(offpeak_rate, 4),
            },
            "measurement_note": "Scenario values are estimates for decision support. Zeus does not execute devices or call control services.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return self._summary

    def summary(self) -> dict[str, Any]:
        return self._summary
