"""Explainable anomaly intelligence for AION EMS Zeus v12.5.

Compares today's measured daily values with the learned Home Profile. The
engine is observation-only, recorder-safe, and does not create a polling loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AnomalyIntelligenceEngine:
    """Identify meaningful deviations from the home's measured profile."""

    VERSION = "1.0-alpha.2"
    METRICS = {
        "solar_energy_kwh": ("Solar production", "solar_profile"),
        "house_energy_kwh": ("Home demand", "household_profile"),
        "grid_import_energy_kwh": ("Grid import", ("grid_profile", "import")),
        "grid_export_energy_kwh": ("Grid export", ("grid_profile", "export")),
        "battery_charge_energy_kwh": ("Battery charging", ("battery_profile", "charge")),
        "battery_discharge_energy_kwh": ("Battery discharge", ("battery_profile", "discharge")),
    }

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Learning",
            "version": self.VERSION,
            "observation_count": 0,
            "observations": [],
            "summary": "Zeus is learning the home's normal measured ranges.",
        }

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _profile_value(profile: dict[str, Any], path: str | tuple[str, str]) -> dict[str, Any]:
        if isinstance(path, tuple):
            return dict((profile.get(path[0]) or {}).get(path[1]) or {})
        return dict(profile.get(path) or {})

    @staticmethod
    def _severity(deviation_percent: float) -> str:
        absolute = abs(deviation_percent)
        if absolute >= 35:
            return "Significant"
        if absolute >= 20:
            return "Notice"
        return "Information"

    def refresh(self) -> dict[str, Any]:
        profile_engine = getattr(self.core, "home_profile", None)
        profile = profile_engine.summary() if profile_engine and hasattr(profile_engine, "summary") else {}
        memory_engine = getattr(self.core, "intelligence_memory", None)
        memory = memory_engine.summary() if memory_engine and hasattr(memory_engine, "summary") else {}
        days = [item for item in (memory.get("recent_days") or []) if isinstance(item, dict)]
        today = days[-1] if days else {}
        learning_days = int(profile.get("learning_days") or 0)
        observations: list[dict[str, Any]] = []

        if learning_days >= 7 and today:
            for field, (label, path) in self.METRICS.items():
                actual = self._num(today.get(field))
                expected = self._profile_value(profile, path)
                low = self._num(expected.get("typical_low"))
                high = self._num(expected.get("typical_high"))
                average = self._num(expected.get("average"))
                if actual is None or low is None or high is None or average is None:
                    continue
                if low <= actual <= high:
                    continue
                baseline = max(abs(average), 0.1)
                deviation = round((actual - average) / baseline * 100.0, 1)
                direction = "above" if deviation > 0 else "below"
                observations.append({
                    "id": f"{field}_{today.get('date', 'today')}",
                    "metric": field,
                    "title": f"{label} is {direction} the learned range",
                    "detail": f"Measured {actual:.1f} kWh versus a typical range of {low:.1f}–{high:.1f} kWh.",
                    "actual": round(actual, 2),
                    "typical_low": round(low, 2),
                    "typical_high": round(high, 2),
                    "deviation_percent": deviation,
                    "severity": self._severity(deviation),
                    "category": "Home Observation",
                    "date": today.get("date"),
                    "source": "Measured daily history and Home Profile",
                })

        observations.sort(key=lambda item: abs(float(item.get("deviation_percent") or 0)), reverse=True)
        observations = observations[:8]
        if observations:
            headline = observations[0]["title"]
            status = "Observation"
            summary = f"Zeus identified {len(observations)} meaningful deviation(s)."
        elif learning_days >= 7:
            headline = "Measured performance is within the learned ranges."
            status = "Normal"
            summary = "No meaningful daily anomalies are currently detected."
        else:
            headline = "More measured history is required."
            status = "Learning"
            summary = f"Zeus has {learning_days} measured day(s); at least 7 are required."

        self.last = {
            "status": status,
            "version": self.VERSION,
            "headline": headline,
            "learning_days": learning_days,
            "observation_count": len(observations),
            "observations": observations,
            "highest_severity": observations[0].get("severity") if observations else None,
            "summary": summary,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "recorder_safe": True,
            "safety": "Observation and recommendation context only. No device control.",
        }
        try:
            self.event_bus.publish("AnomalyIntelligenceUpdated", "AnomalyIntelligenceEngine", {
                "observation_count": len(observations),
                "status": status,
            })
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
