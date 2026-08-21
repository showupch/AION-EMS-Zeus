"""Intelligence Fusion Engine for AION EMS Zeus v12.5.

Combines compact summaries from Zeus intelligence engines into one recorder-safe,
explainable context. Recommendation only; never calls Home Assistant services.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class IntelligenceFusionEngine:
    """Build a unified, conservative intelligence context."""

    VERSION = "1.0-alpha.6"
    _ENGINE_NAMES = (
        "home_profile",
        "anomaly_intelligence",
        "prediction_accuracy",
        "opportunity_learning",
        "adaptive_advisor",
        "intelligence_memory",
        "learning_intelligence_v2",
        "forecast",
        "decision_engine",
        "scenario_simulator",
    )

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "summary": "Zeus is waiting for supporting intelligence engines.",
        }

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_number(cls, source: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = cls._number(source.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _freshness(source: dict[str, Any]) -> str:
        value = source.get("updated_at") or source.get("last_updated") or source.get("generated_at")
        if not value:
            return "Unknown"
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            if age <= 300:
                return "Fresh"
            if age <= 3600:
                return "Recent"
            return "Stale"
        except (TypeError, ValueError):
            return "Unknown"

    @staticmethod
    def _status_ready(value: Any) -> bool:
        return str(value or "").lower() in {
            "ready", "healthy", "active", "normal", "complete", "completed", "available"
        }

    def refresh(self) -> dict[str, Any]:
        sources: dict[str, dict[str, Any]] = {}
        for name in self._ENGINE_NAMES:
            sources[name] = self._summary(getattr(self.core, name, None))

        decision = sources["decision_engine"]
        forecast = sources["forecast"]
        prediction = sources["prediction_accuracy"]
        anomaly = sources["anomaly_intelligence"]
        memory = sources["intelligence_memory"]
        learning = sources["learning_intelligence_v2"]
        opportunity = sources["opportunity_learning"]
        adaptive = sources["adaptive_advisor"]

        confidence_inputs: list[tuple[str, float]] = []
        candidates = (
            ("Decision", self._first_number(decision, "confidence_percent", "confidence")),
            ("Prediction Accuracy", self._first_number(prediction, "overall_accuracy_percent", "accuracy_percent")),
            ("Learning", self._first_number(learning, "confidence_percent", "confidence")),
            ("Forecast", self._first_number(forecast, "confidence_percent", "forecast_confidence_percent")),
        )
        for label, value in candidates:
            if value is not None:
                confidence_inputs.append((label, max(0.0, min(100.0, value))))

        overall_confidence = (
            round(sum(value for _, value in confidence_inputs) / len(confidence_inputs), 1)
            if confidence_inputs else None
        )

        observations = anomaly.get("observations") or anomaly.get("anomalies") or []
        if not isinstance(observations, list):
            observations = []
        significant = [
            item for item in observations
            if isinstance(item, dict)
            and str(item.get("severity") or item.get("level") or "").lower()
            in {"notice", "significant", "warning", "persistent", "critical"}
        ]

        evidence: list[dict[str, Any]] = []
        if decision.get("reason") or decision.get("why"):
            evidence.append({"source": "Decision Engine", "value": str(decision.get("reason") or decision.get("why"))[:180]})
        for label, value in confidence_inputs:
            evidence.append({"source": label, "value": f"{value:.1f}%"})
        if memory.get("record_count") is not None:
            evidence.append({"source": "Intelligence Memory", "value": f"{memory.get('record_count')} records"})
        if adaptive.get("established_preference_count") is not None:
            evidence.append({"source": "Adaptive Advisor", "value": f"{adaptive.get('established_preference_count')} learned categories"})
        if opportunity.get("resolved_count") is not None:
            evidence.append({"source": "Opportunity Learning", "value": f"{opportunity.get('resolved_count')} resolved outcomes"})
        if significant:
            first = significant[0]
            evidence.append({"source": "Anomaly Intelligence", "value": str(first.get("message") or first.get("summary") or first.get("title") or "Active observation")[:180]})

        available = sum(1 for value in sources.values() if value)
        ready = sum(1 for value in sources.values() if self._status_ready(value.get("status")))
        completeness = round((available / len(self._ENGINE_NAMES)) * 100.0, 1)
        freshness_values = [self._freshness(value) for value in sources.values() if value]
        data_freshness = "Fresh" if "Fresh" in freshness_values else "Recent" if "Recent" in freshness_values else "Unknown"

        recommendation = str(
            decision.get("recommendation")
            or decision.get("decision")
            or decision.get("action")
            or "No high-value action is available right now."
        )
        quality_score = self._first_number(decision, "quality_score", "priority_score", "score")
        if quality_score is None:
            quality_score = overall_confidence

        maturity_points = 0
        if self._first_number(learning, "confidence_percent") not in (None, 0):
            maturity_points += 1
        if (memory.get("record_count") or 0) > 0:
            maturity_points += 1
        if (opportunity.get("resolved_count") or 0) >= 3:
            maturity_points += 1
        if (adaptive.get("established_preference_count") or 0) > 0:
            maturity_points += 1
        learning_maturity = ("Established" if maturity_points >= 4 else "Developing" if maturity_points >= 2 else "Learning")

        status = "Ready" if available >= 6 else "Partial" if available else "Waiting"
        self.last = {
            "status": status,
            "version": self.VERSION,
            "mode": "recommendation_only",
            "overall_confidence_percent": overall_confidence,
            "recommendation": recommendation,
            "recommendation_quality_score": round(quality_score, 1) if quality_score is not None else None,
            "reasoning_completeness_percent": completeness,
            "data_freshness": data_freshness,
            "learning_maturity": learning_maturity,
            "contributing_engine_count": available,
            "ready_engine_count": ready,
            "evidence_count": len(evidence),
            "active_observation_count": len(significant),
            "confidence_breakdown": [
                {"source": label, "percent": round(value, 1)} for label, value in confidence_inputs
            ][:6],
            "evidence": evidence[:8],
            "engine_status": {
                name: str(value.get("status") or ("Available" if value else "Unavailable"))
                for name, value in sources.items()
            },
            "summary": (
                f"Unified intelligence context is {status.lower()} with {available} contributing engines"
                + (f" and {overall_confidence:.1f}% blended confidence." if overall_confidence is not None else ".")
            ),
            "data_policy": "Uses compact engine summaries only; missing values remain unavailable.",
            "safety": "Recommendation only. No device or inverter services are called.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "recorder_safe": True,
        }
        try:
            self.event_bus.publish(
                "IntelligenceFusionUpdated",
                "IntelligenceFusionEngine",
                {"status": status, "engines": available, "confidence": overall_confidence},
            )
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
