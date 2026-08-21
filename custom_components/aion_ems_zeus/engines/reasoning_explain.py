"""Reasoning and Explain Engine for AION EMS Zeus v11.5.

Builds compact, evidence-backed explanations from live flow, forecast,
finance and Observation & Knowledge data. Read-only and recommendation-only.
"""
from __future__ import annotations

from typing import Any


class ReasoningExplainEngine:
    """Create transparent reasoning summaries for Hyper Analytics."""

    def __init__(self, event_bus, energy_flow, observation_knowledge, hyper, forecast, finance) -> None:
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.observation_knowledge = observation_knowledge
        self.hyper = hyper
        self.forecast = forecast
        self.finance = finance
        self.last: dict[str, Any] = {
            "status": "Learning",
            "headline": "Zeus is assembling evidence-backed explanations.",
            "explanations": [],
            "causal_chains": [],
            "confidence_breakdown": {},
            "recorder_safe": True,
            "safety": "Recommendation only. No autonomous control.",
        }

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value if value not in (None, "", "unknown", "unavailable") else default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _watts(value: float) -> str:
        value = float(value or 0)
        return f"{value / 1000:.2f} kW" if abs(value) >= 1000 else f"{value:.0f} W"

    def _live(self) -> dict[str, float]:
        flow = self.energy_flow.summary() or {}
        return {
            "solar": self._num(flow.get("solar_power") or flow.get("solar_power_w")),
            "home": self._num(flow.get("house_power") or flow.get("house_power_w")),
            "import": self._num(flow.get("grid_import_power") or flow.get("grid_import_power_w")),
            "export": self._num(flow.get("grid_export_power") or flow.get("grid_export_power_w")),
            "charge": self._num(flow.get("battery_charge_power") or flow.get("battery_charge_power_w")),
            "discharge": self._num(flow.get("battery_discharge_power") or flow.get("battery_discharge_power_w")),
            "soc": self._num(flow.get("battery_soc_percent"), -1),
        }

    def _live_explanation(self, v: dict[str, float]) -> dict[str, Any]:
        surplus = v["solar"] - v["home"]
        evidence: list[str] = [
            f"Solar is producing {self._watts(v['solar'])}.",
            f"Home demand is {self._watts(v['home'])}.",
        ]
        if v["soc"] >= 0:
            evidence.append(f"Battery state of charge is {v['soc']:.0f}%.")

        if v["export"] > 50:
            title = "Why Zeus sees an export opportunity"
            explanation = (
                f"Solar exceeds current home demand and the system is exporting {self._watts(v['export'])}. "
                "Flexible loads could use part of this surplus before it leaves the home."
            )
            evidence.append(f"Grid export is active at {self._watts(v['export'])}.")
            action = "Consider running a flexible load during the current surplus window."
        elif v["charge"] > 50:
            title = "Why the battery is charging"
            explanation = (
                f"Solar production is above household demand by approximately {self._watts(max(0, surplus))}. "
                f"The battery is absorbing {self._watts(v['charge'])} of available energy."
            )
            evidence.append(f"Battery charging is active at {self._watts(v['charge'])}.")
            action = "No action is required; surplus solar is being stored."
        elif v["discharge"] > 50:
            title = "Why the battery is supporting the home"
            explanation = (
                f"Available solar is below home demand, so the battery is supplying {self._watts(v['discharge'])} "
                "to reduce or avoid grid import."
            )
            evidence.append(f"Battery discharge is active at {self._watts(v['discharge'])}.")
            action = "Avoid adding large flexible loads unless the forecast supports it."
        elif v["import"] > 50:
            title = "Why the home is importing"
            deficit = max(0, v["home"] - v["solar"] - v["discharge"])
            explanation = (
                f"Home demand currently exceeds solar and battery support by about {self._watts(deficit)}. "
                f"The grid is supplying {self._watts(v['import'])}."
            )
            evidence.append(f"Grid import is active at {self._watts(v['import'])}.")
            action = "Delay flexible loads if a stronger solar window is expected."
        elif v["solar"] > 50:
            title = "Why Zeus reports solar supply"
            explanation = "Solar is covering the active household demand with no significant grid flow detected."
            action = "Current operation is efficient; no action is required."
        else:
            title = "Why Zeus reports an idle state"
            explanation = "No significant live energy flow is currently above the active-flow threshold."
            action = "No action is required."

        return {
            "title": title,
            "explanation": explanation,
            "evidence": evidence[:5],
            "recommendation": action,
            "confidence": 96 if any((v["import"] > 50, v["export"] > 50, v["charge"] > 50, v["discharge"] > 50)) else 84,
        }

    def refresh(self) -> None:
        live = self._live()
        knowledge = self.observation_knowledge.summary() or {}
        hyper = self.hyper.summary() or {}
        forecast = self.forecast.summary() or {}
        finance = self.finance.summary() or {}

        explanations = [self._live_explanation(live)]
        for item in (knowledge.get("evidence") or [])[:3]:
            explanations.append({
                "title": str(item.get("claim") or "Why Zeus believes this pattern"),
                "explanation": str(item.get("because") or "The conclusion is supported by repeated measured observations."),
                "evidence": [f"{item.get('observations', 0)} supporting observations"],
                "recommendation": "Continue monitoring; Zeus will update this conclusion as new evidence arrives.",
                "confidence": int(self._num(item.get("confidence"), 0)),
            })

        forecast_conf = int(self._num(forecast.get("confidence"), 0))
        knowledge_conf = int(self._num(knowledge.get("confidence"), 0))
        history_days = int(self._num(hyper.get("history_days") or hyper.get("learning_age_days"), 0))
        evidence_count = int(self._num(knowledge.get("knowledge_object_count"), 0))
        overall = max(0, min(99, round((forecast_conf * 0.35) + (knowledge_conf * 0.45) + (min(100, history_days * 4) * 0.20))))

        chains = []
        if live["export"] > 50:
            chains.append({"steps": ["Solar surplus", "Home demand covered", "Grid export", "Flexible-load opportunity"], "confidence": 96})
        elif live["import"] > 50:
            chains.append({"steps": ["Demand exceeds local supply", "Battery/solar shortfall", "Grid import", "Cost impact"], "confidence": 94})
        elif live["charge"] > 50:
            chains.append({"steps": ["Solar surplus", "Battery below target", "Battery charging", "Stored-energy value"], "confidence": 95})
        elif live["discharge"] > 50:
            chains.append({"steps": ["Solar shortfall", "Battery available", "Battery support", "Avoided import"], "confidence": 95})
        for edge in (knowledge.get("knowledge_graph") or [])[:3]:
            chains.append({"steps": [str(edge.get("from", "Evidence")), str(edge.get("relation", "influences")), str(edge.get("to", "Outcome"))], "confidence": knowledge_conf})

        self.last = {
            "status": "Ready" if explanations else "Learning",
            "headline": explanations[0]["title"] if explanations else "Zeus is assembling explanations.",
            "explanations": explanations[:4],
            "causal_chains": chains[:5],
            "confidence_breakdown": {
                "overall": overall,
                "knowledge": knowledge_conf,
                "forecast": forecast_conf,
                "history_days": history_days,
                "evidence_objects": evidence_count,
            },
            "finance_context": {
                "currency": finance.get("currency", "CHF"),
                "solar_savings": finance.get("solar_savings_today") or finance.get("savings_today"),
            },
            "recorder_safe": True,
            "safety": "Recommendation only. No autonomous control.",
        }

    def summary(self) -> dict[str, Any]:
        return self.last
