"""Zeus Brain Core: recorder-safe observations, distilled memory and briefings."""
from __future__ import annotations

from datetime import datetime
from typing import Any


class ZeusBrainCore:
    """Unify live observations and learned intelligence without device control."""

    def __init__(self, event_bus, energy_flow, hyper_analytics, forecast, finance, learning) -> None:
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.hyper = hyper_analytics
        self.forecast = forecast
        self.finance = finance
        self.learning = learning
        self._previous: dict[str, Any] = {}
        self._observations: list[dict[str, Any]] = []
        self.last: dict[str, Any] = {
            "status": "Learning",
            "headline": "Zeus Brain is observing your home.",
            "observations": [],
            "memory": {},
            "morning_briefing": {},
            "safety": "Recommendation only. No autonomous control.",
            "recorder_safe": True,
        }

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return default

    def _observe(self, now: str, flow: dict[str, Any]) -> None:
        current = {
            "solar": self._num(flow.get("solar_power") or flow.get("solar_power_w")),
            "home": self._num(flow.get("house_power") or flow.get("house_power_w")),
            "import": self._num(flow.get("grid_import_power") or flow.get("grid_import_power_w")),
            "export": self._num(flow.get("grid_export_power") or flow.get("grid_export_power_w")),
            "charge": self._num(flow.get("battery_charge_power") or flow.get("battery_charge_power_w")),
            "discharge": self._num(flow.get("battery_discharge_power") or flow.get("battery_discharge_power_w")),
            "soc": self._num(flow.get("battery_soc_percent"), -1),
        }
        events: list[tuple[str, str]] = []
        p = self._previous
        if p:
            checks = [
                (p.get("solar", 0) <= 50 < current["solar"], "Solar production started", f"Solar rose to {current['solar']:.0f} W."),
                (p.get("export", 0) <= 50 < current["export"], "Grid export started", f"Surplus export reached {current['export']:.0f} W."),
                (p.get("import", 0) <= 50 < current["import"], "Grid import started", f"The grid is supplying {current['import']:.0f} W."),
                (p.get("charge", 0) <= 50 < current["charge"], "Battery charging started", f"Battery charge power is {current['charge']:.0f} W."),
                (p.get("discharge", 0) <= 50 < current["discharge"], "Battery support started", f"Battery discharge power is {current['discharge']:.0f} W."),
                (p.get("soc", -1) < 99 <= current["soc"], "Battery reached full", "Battery state of charge reached 100%."),
            ]
            events.extend((title, detail) for condition, title, detail in checks if condition)
        if not self._observations:
            mode = "Solar producing" if current["solar"] > current["home"] else "Battery support" if current["discharge"] > 50 else "Grid supply" if current["import"] > 50 else "Balanced"
            events.append(("Brain Core started", f"Initial operating state: {mode}."))
        for title, detail in events:
            self._observations.insert(0, {"time": now, "title": title, "detail": detail})
        self._observations = self._observations[:30]
        self._previous = current

    def refresh(self) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        flow = self.energy_flow.summary() or {}
        hyper = self.hyper.summary() or {}
        forecast = self.forecast.summary() or {}
        finance = self.finance.summary() or {}
        learning = self.learning.summary() or {}
        self._observe(now, flow)

        dna = hyper.get("house_dna") or {}
        discoveries = hyper.get("discoveries") or []
        opportunities = hyper.get("opportunities") or []
        anomalies = hyper.get("anomalies") or []
        energy_iq = hyper.get("energy_iq")
        history_days = hyper.get("history_days", 0)
        savings = finance.get("solar_savings_today") or finance.get("savings_today") or finance.get("net_benefit_today")
        tomorrow = forecast.get("expected_solar_following_24h_kwh")

        briefing_items = []
        if discoveries:
            briefing_items.append(discoveries[0].get("title"))
        if anomalies:
            briefing_items.append(anomalies[0].get("title"))
        if opportunities:
            briefing_items.append(opportunities[0].get("title"))
        if tomorrow is not None:
            briefing_items.append(f"Tomorrow solar forecast is {self._num(tomorrow):.1f} kWh.")
        briefing_items = [x for x in briefing_items if x][:5]

        confidence = hyper.get("confidence") or learning.get("confidence") or 0
        headline = discoveries[0].get("title") if discoveries else "Zeus Brain is building long-term knowledge."
        self.last = {
            "status": "Ready" if history_days else "Learning",
            "headline": headline,
            "generated_at": now,
            "energy_iq": energy_iq,
            "energy_iq_grade": hyper.get("energy_iq_grade"),
            "confidence": confidence,
            "history_days": history_days,
            "knowledge_entries": len(discoveries) + len(opportunities) + len(anomalies) + len(self._observations),
            "observations": self._observations[:12],
            "memory": {
                "house_dna": dna,
                "discoveries": discoveries[:5],
                "opportunities": opportunities[:4],
                "anomalies": anomalies[:4],
            },
            "morning_briefing": {
                "greeting": "Good morning" if datetime.now().hour < 12 else "Good afternoon" if datetime.now().hour < 18 else "Good evening",
                "headline": headline,
                "items": briefing_items,
                "solar_savings_today": savings,
                "tomorrow_solar_kwh": tomorrow,
            },
            "safety": "Recommendation only. Zeus Brain never controls devices.",
            "recorder_safe": True,
        }
        self.event_bus.publish("ZeusBrainUpdated", "ZeusBrainCore", {"status": self.last["status"], "knowledge_entries": self.last["knowledge_entries"]})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
