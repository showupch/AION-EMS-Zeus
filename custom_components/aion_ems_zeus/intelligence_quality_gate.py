"""Stabilization and quality gate for AION EMS Zeus v12.5 beta.

Recorder-safe, recommendation-only health aggregation. It never calls device
services and does not create a polling loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class IntelligenceQualityGate:
    """Validate the consolidated v12.5 intelligence stack."""

    VERSION = "1.3-beta.7"
    ENGINES = (
        "home_profile",
        "anomaly_intelligence",
        "opportunity_learning",
        "adaptive_advisor",
        "executive_briefing",
        "intelligence_fusion",
        "prediction_accuracy",
        "intelligence_memory",
        "decision_engine",
        "scenario_simulator",
    )

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "summary": "Quality Gate is waiting for the intelligence stack.",
        }

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception as err:  # defensive: diagnostics must never break setup
            return {"status": "Error", "error": type(err).__name__}

    @staticmethod
    def _healthy(status: Any) -> bool:
        return str(status or "").strip().lower() in {
            "ready", "healthy", "active", "normal", "complete", "completed",
            "available", "learning", "partial", "monitoring", "collecting data",
        }

    @staticmethod
    def _compact_issue(name: str, summary: dict[str, Any]) -> dict[str, str]:
        status = str(summary.get("status") or "Unavailable")
        detail = str(summary.get("error") or summary.get("message") or summary.get("summary") or status)
        return {"engine": name, "status": status, "detail": detail[:180]}

    def refresh(self) -> dict[str, Any]:
        engine_health: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        available = 0
        healthy = 0

        for name in self.ENGINES:
            engine = getattr(self.core, name, None)
            summary = self._summary(engine) if engine is not None else {}
            status = summary.get("status") or ("Unavailable" if not summary else "Unknown")
            if summary:
                available += 1
            is_healthy = self._healthy(status)
            if is_healthy:
                healthy += 1
            else:
                issues.append(self._compact_issue(name, summary))
            engine_health.append({
                "engine": name,
                "status": str(status),
                "healthy": is_healthy,
            })

        mapping = self._summary(getattr(self.core, "energy_mapping", None))
        data_quality = self._summary(getattr(self.core, "data_quality", None))
        update_engine = self._summary(getattr(self.core, "update_engine", None))
        resilience = self._summary(getattr(self.core, "runtime_resilience", None))
        consistency = self._summary(getattr(self.core, "data_consistency", None))

        checks = {
            "integration_entry": hasattr(self.core, "hass") and hasattr(self.core, "entry"),
            "recommendation_only": True,
            "energy_mapping_available": bool(mapping),
            "data_quality_available": bool(data_quality),
            "update_engine_available": bool(update_engine),
            "runtime_resilience_available": bool(resilience),
            "runtime_resilience_healthy": str(resilience.get("status", "")).lower() in {"healthy", "waiting"},
            "data_consistency_available": bool(consistency),
            "data_consistency_passed": str(consistency.get("status", "")).lower() in {"consistent", "waiting"},
            "intelligence_stack_complete": available == len(self.ENGINES),
        }
        passed_checks = sum(1 for value in checks.values() if value)
        total_checks = len(checks)
        health_percent = round(((healthy + passed_checks) / (len(self.ENGINES) + total_checks)) * 100, 1)

        if available < len(self.ENGINES):
            status = "Warning"
        elif issues:
            status = "Review"
        else:
            status = "Healthy"

        self.last = {
            "status": status,
            "version": self.VERSION,
            "mode": "recommendation_only",
            "health_percent": health_percent,
            "engine_count": len(self.ENGINES),
            "available_engine_count": available,
            "healthy_engine_count": healthy,
            "issue_count": len(issues),
            "checks": checks,
            "issues": issues[:8],
            "engine_health": engine_health,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": (
                "The v12.5 intelligence stack passed the quality gate."
                if status == "Healthy"
                else "Zeus is running safely; review the compact quality-gate issues."
            ),
            "safety": "Recommendation only. No automatic device control.",
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return dict(self.last)
