"""Create transparent evidence records for Zeus decisions."""

from __future__ import annotations

from typing import Any


class EvidenceBuilder:
    """Build explainable evidence without controlling devices."""

    def build(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        energy = context.get("energy", {})
        forecast = context.get("forecast", {})
        learning = context.get("learning", {})
        finance = context.get("finance", {})
        platform = context.get("platform", {})

        def add(source: str, available: bool, detail: str, weight: int) -> None:
            evidence.append({
                "source": source,
                "available": bool(available),
                "detail": detail,
                "weight": weight,
            })

        add("Live energy", energy.get("solar_w") is not None or energy.get("home_w") is not None,
            f"Operating state: {energy.get('operating_state') or 'Unknown'}.", 25)
        add("Forecast", forecast.get("status") not in (None, "Not ready", "Unavailable"),
            f"Forecast confidence: {forecast.get('confidence') if forecast.get('confidence') is not None else 'Learning'}.", 25)
        add("Battery", energy.get("battery_soc_percent") is not None,
            f"Battery SOC: {energy.get('battery_soc_percent') if energy.get('battery_soc_percent') is not None else 'Not mapped'}%.", 15)
        add("Learning", learning.get("status") not in (None, "Not ready", "Unavailable"),
            f"Learning confidence: {learning.get('confidence_label') or learning.get('confidence') or 'Learning'}.", 20)
        add("Finance", finance.get("status") not in (None, "Not ready", "Unavailable"),
            "Financial context is available." if finance.get("status") else "Financial context is still learning.", 10)
        add("Data quality", platform.get("quality_score") is not None,
            f"Data quality: {platform.get('quality_score') if platform.get('quality_score') is not None else 'Unknown'}.", 5)
        return evidence

    @staticmethod
    def confidence(evidence: list[dict[str, Any]]) -> int:
        total = sum(int(item.get("weight", 0)) for item in evidence) or 1
        earned = sum(int(item.get("weight", 0)) for item in evidence if item.get("available"))
        return max(0, min(100, round(earned * 100 / total)))
