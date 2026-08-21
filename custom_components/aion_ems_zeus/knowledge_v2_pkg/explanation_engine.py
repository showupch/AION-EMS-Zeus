"""Explain Zeus recommendations using compact evidence chains."""

from __future__ import annotations

from typing import Any


class ExplanationEngine:
    def build(self, context: dict[str, Any], evidence: list[dict[str, Any]], confidence: int) -> dict[str, Any]:
        optimizer = context.get("optimizer", {})
        recommendation = optimizer.get("recommendation") or "Keep observing current energy conditions."
        reason = optimizer.get("reason") or "Zeus is combining live energy, forecast, learning and financial context."
        supporting = [item["detail"] for item in evidence if item.get("available")][:4]
        missing = [item["source"] for item in evidence if not item.get("available")]
        alternatives = [
            "Wait for stronger forecast confidence.",
            "Review the recommendation again when battery or tariff context changes.",
        ] if confidence < 80 else ["Continue with the current recommendation."]
        return {
            "recommendation": str(recommendation),
            "why": str(reason),
            "confidence": confidence,
            "evidence": supporting,
            "missing_context": missing,
            "alternatives": alternatives,
            "safety": "Recommendation only. No device service is called.",
        }
