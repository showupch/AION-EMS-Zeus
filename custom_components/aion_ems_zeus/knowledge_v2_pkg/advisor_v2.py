"""Explainable Advisor 2.0 foundation for AION EMS Zeus."""
from __future__ import annotations

from typing import Any


class AdvisorV2:
    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {"status": "Waiting", "version": "2.0-beta.1"}

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def refresh(self) -> dict[str, Any]:
        knowledge = self._summary(self.core.knowledge_v2)
        learning = self._summary(self.core.learning_intelligence_v2)
        timeline = self._summary(self.core.knowledge_timeline)
        existing = self._summary(self.core.ai_advisor)
        decision = self._summary(self.core.decision_engine)
        profile = self._summary(getattr(self.core, "home_profile", None))
        anomaly = self._summary(getattr(self.core, "anomaly_intelligence", None))
        adaptive = self._summary(getattr(self.core, "adaptive_advisor", None))
        explanation = knowledge.get("explanation") if isinstance(knowledge.get("explanation"), dict) else {}
        evidence = knowledge.get("evidence") if isinstance(knowledge.get("evidence"), list) else []
        available_evidence = [item.get("label") or item.get("source") for item in evidence if item.get("available")][:6]
        recommendation = decision.get("decision") or knowledge.get("recommendation") or existing.get("headline") or "Observe current energy conditions"
        category = str(decision.get("category") or "unknown")
        preference = (adaptive.get("category_preferences") or {}).get(category, {}) if isinstance(adaptive, dict) else {}
        why = decision.get("reason") or knowledge.get("why") or explanation.get("why") or existing.get("explanation") or "Zeus is combining live energy, forecast and learned history."
        preferred_window = preference.get("preferred_window") if isinstance(preference, dict) else None
        if preferred_window:
            why = f"{why} Your recent completed recommendations in this category most often occurred around {preferred_window}."
        confidence = decision.get("confidence_percent", knowledge.get("confidence", learning.get("confidence_percent", 0)))
        alternatives = decision.get("alternatives") if isinstance(decision.get("alternatives"), list) else (explanation.get("alternatives") if isinstance(explanation.get("alternatives"), list) else [])
        if not alternatives:
            alternatives = ["Wait for the next forecast window", "Continue current operation"]

        profile_patterns = profile.get("patterns") if isinstance(profile.get("patterns"), list) else []
        anomaly_observations = anomaly.get("observations") if isinstance(anomaly.get("observations"), list) else []
        common_answers = [
            {"question": "Why this recommendation?", "answer": why},
            {"question": "How confident is Zeus?", "answer": f"Confidence is {confidence}% based on {len(available_evidence)} active evidence source(s)."},
            {"question": "What happened recently?", "answer": (timeline.get("latest_event") or {}).get("detail") or "No major recent event has been identified."},
            {"question": "Is today similar to another day?", "answer": learning.get("similar_day_message") or "Zeus is still collecting comparable measured days."},
            {"question": "What has Zeus learned about this home?", "answer": profile_patterns[0] if profile_patterns else profile.get("summary") or "Zeus is still building the home profile."},
            {"question": "Is anything unusual?", "answer": anomaly_observations[0].get("detail") if anomaly_observations else anomaly.get("summary") or "No meaningful anomaly is currently detected."},
        ]
        self.last = {
            "status": "Ready",
            "version": "3.0-alpha.4",
            "recommendation": recommendation,
            "why": why,
            "confidence_percent": confidence,
            "evidence": [item for item in available_evidence if item],
            "alternatives": alternatives[:3],
            "common_answers": common_answers,
            "similar_day": learning.get("similar_day"),
            "similarity_percent": learning.get("similarity_percent"),
            "best_window": decision.get("best_window"),
            "expected_benefit": decision.get("expected_benefit"),
            "decision_category": decision.get("category"),
            "adaptive_status": adaptive.get("status"),
            "adaptive_summary": adaptive.get("summary"),
            "preferred_execution_window": preferred_window,
            "preference_context": preference,
            "home_profile_confidence_percent": profile.get("confidence_percent"),
            "home_profile_context": profile_patterns[:3],
            "anomaly_status": anomaly.get("status"),
            "anomaly_observations": anomaly_observations[:3],
            "summary": f"{recommendation} ({confidence}% confidence).",
            "recorder_safe": True,
            "safety": "Recommendation only. Zeus does not call control services.",
        }
        try:
            self.event_bus.publish("AdvisorV2Updated", "AdvisorV2", {"confidence": confidence})
        except Exception:
            pass
        return self.last

    def answer(self, question: str) -> str:
        text = (question or "").lower()
        for item in self.last.get("common_answers", []):
            key = item.get("question", "").lower()
            if ("why" in text and "why" in key) or ("confidence" in text and "confident" in key) or ("recent" in text and "recent" in key) or ("similar" in text and "similar" in key):
                return str(item.get("answer"))
        return str(self.last.get("why") or self.last.get("summary") or "Zeus is still learning.")

    def summary(self) -> dict[str, Any]:
        return self.last
