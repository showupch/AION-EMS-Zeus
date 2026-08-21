"""Soak validation and release-candidate readiness for AION EMS Zeus.

The engine aggregates compact, recorder-safe evidence from existing Zeus
components. It never controls devices and does not create a polling loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ReleaseReadinessEngine:
    """Build a compact release-readiness report for the v12.5 soak test."""

    VERSION = "1.1-beta.7"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "summary": "Release readiness is waiting for the first complete refresh.",
        }

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception as err:
            return {"status": "Error", "error": f"{type(err).__name__}: {str(err)[:160]}"}

    @staticmethod
    def _status_ok(value: Any) -> bool:
        return str(value or "").strip().lower() in {
            "ready", "healthy", "active", "normal", "complete", "completed",
            "available", "learning", "partial", "monitoring", "collecting data",
            "waiting", "no data", "not enough data",
        }

    def refresh(self) -> dict[str, Any]:
        quality = self._summary(getattr(self.core, "intelligence_quality_gate", None))
        resilience = self._summary(getattr(self.core, "runtime_resilience", None))
        mapping = self._summary(getattr(self.core, "energy_mapping", None))
        data_quality = self._summary(getattr(self.core, "data_quality", None))
        update_engine = self._summary(getattr(self.core, "update_engine", None))
        decision = self._summary(getattr(self.core, "decision_engine", None))
        memory = self._summary(getattr(self.core, "intelligence_memory", None))
        consistency = self._summary(getattr(self.core, "data_consistency", None))

        performance = dict(getattr(self.core, "performance", {}) or {})
        tracked = len(getattr(self.core, "tracked_entity_ids", lambda: set())())
        mappings = getattr(getattr(self.core, "registry", None), "data", {}).get("entity_mappings", {})

        checks = [
            {
                "name": "Core stability",
                "passed": resilience.get("issue_count", 0) == 0
                and str(resilience.get("status", "")).lower() in {"healthy", "waiting"},
                "detail": resilience.get("summary", "Runtime resilience unavailable."),
            },
            {
                "name": "Intelligence stack",
                "passed": quality.get("available_engine_count", 0) == quality.get("engine_count", -1)
                and quality.get("issue_count", 0) == 0,
                "detail": quality.get("summary", "Quality Gate unavailable."),
            },
            {
                "name": "Energy mappings",
                "passed": bool(mapping) and bool(mappings),
                "detail": f"{len(mappings)} saved mapping(s); {tracked} tracked source entity/entities.",
            },
            {
                "name": "Data quality",
                "passed": bool(data_quality) and self._status_ok(data_quality.get("status")),
                "detail": data_quality.get("message") or data_quality.get("summary") or data_quality.get("status", "Unavailable"),
            },
            {
                "name": "Update engine",
                "passed": bool(update_engine) and self._status_ok(update_engine.get("status")),
                "detail": update_engine.get("summary") or update_engine.get("status", "Unavailable"),
            },
            {
                "name": "Data consistency",
                "passed": str(consistency.get("status", "")).lower() in {"consistent", "waiting"},
                "detail": consistency.get("summary", "Data Consistency unavailable."),
            },
            {
                "name": "Recommendation safety",
                "passed": True,
                "detail": "Recommendation only. No automatic device control.",
            },
            {
                "name": "Decision intelligence",
                "passed": bool(decision) and str(decision.get("status", decision.get("decision", ""))).lower() not in {"error", "unavailable"},
                "detail": decision.get("decision") or decision.get("summary") or decision.get("status", "Unavailable"),
            },
            {
                "name": "Memory growth",
                "passed": bool(memory) and str(memory.get("status", "")).lower() not in {"error", "unavailable"},
                "detail": memory.get("summary") or memory.get("status", "Unavailable"),
            },
        ]

        passed = sum(1 for item in checks if item["passed"])
        total = len(checks)
        readiness_percent = round((passed / total) * 100, 1) if total else 0.0
        failures = [item for item in checks if not item["passed"]]

        if passed == total:
            status = "Ready for RC soak"
        elif passed >= total - 2:
            status = "Soak testing"
        else:
            status = "Review required"

        self.last = {
            "status": status,
            "version": self.VERSION,
            "mode": "recommendation_only",
            "readiness_percent": readiness_percent,
            "passed_check_count": passed,
            "total_check_count": total,
            "failed_check_count": len(failures),
            "checks": checks,
            "issues": failures[:8],
            "performance": {
                "live_refreshes": performance.get("live_refreshes", 0),
                "decision_refreshes": performance.get("decision_refreshes", 0),
                "last_live_duration_ms": performance.get("last_live_duration_ms"),
                "last_decision_duration_ms": performance.get("last_decision_duration_ms"),
                "last_live_refresh": performance.get("last_live_refresh"),
                "last_decision_refresh": performance.get("last_decision_refresh"),
            },
            "soak_guidance": (
                "Let beta.3 run for several days and review Home Assistant logs, browser console, "
                "Recorder warnings, mapping restoration, and this readiness sensor after restarts."
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": (
                "All release-readiness checks currently pass; continue the multi-day soak test."
                if passed == total
                else "Zeus remains recommendation-only; review the failed readiness checks during soak testing."
            ),
            "safety": "Recommendation only. No automatic device control.",
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return dict(self.last)
