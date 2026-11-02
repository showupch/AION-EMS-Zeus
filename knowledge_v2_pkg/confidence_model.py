"""Evidence-quality confidence model for learned patterns."""
from __future__ import annotations
from typing import Any


class ConfidenceModel:
    def build(self, behavior: dict[str, Any], similarity: dict[str, Any], platform_quality: Any = None) -> dict[str, Any]:
        days = int(behavior.get("history_days") or 0)
        history_score = min(70, round(days / 30 * 70))
        similarity_score = 20 if similarity.get("similar_day") else 0
        try:
            quality = float(platform_quality)
            quality_score = round(max(0, min(100, quality)) * 0.10)
        except (TypeError, ValueError):
            quality_score = 5
        score = min(100, history_score + similarity_score + quality_score)
        return {"score": score, "label": "High" if score >= 80 else "Moderate" if score >= 50 else "Learning",
                "history_days": days, "evidence": {"history": history_score, "similar_day": similarity_score, "data_quality": quality_score}}
