"""Runtime resilience and finite recovery support for AION EMS Zeus.

This module isolates intelligence-engine failures, records compact diagnostics,
and schedules only finite startup retries. It never controls devices and does
not create a continuous polling loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class RuntimeResilienceEngine:
    """Track guarded refreshes and delayed-engine recovery."""

    VERSION = "1.1-beta.3"

    def __init__(self, event_bus: Any) -> None:
        self.event_bus = event_bus
        self._engines: dict[str, dict[str, Any]] = {}
        self._recovery_runs = 0
        self._last_recovery_at: str | None = None
        self._last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "summary": "Runtime resilience is waiting for the first refresh.",
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def guarded_refresh(self, name: str, callback: Callable[[], Any]) -> bool:
        """Refresh one engine without allowing it to block the remaining stack."""
        record = self._engines.setdefault(name, {
            "status": "Waiting",
            "successes": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "last_error": None,
            "last_success_at": None,
            "last_failure_at": None,
        })
        try:
            callback()
        except Exception as err:  # resilience boundary: continue with other engines
            record["status"] = "Delayed" if record["successes"] == 0 else "Degraded"
            record["failures"] += 1
            record["consecutive_failures"] += 1
            record["last_failure_at"] = self._now()
            record["last_error"] = f"{type(err).__name__}: {str(err)[:180]}"
            self.event_bus.publish(
                "IntelligenceEngineRefreshFailed",
                "RuntimeResilienceEngine",
                {"engine": name, "error": record["last_error"]},
            )
            self._rebuild_summary()
            return False

        record["status"] = "Ready"
        record["successes"] += 1
        record["consecutive_failures"] = 0
        record["last_success_at"] = self._now()
        record["last_error"] = None
        self._rebuild_summary()
        return True

    def mark_recovery_run(self) -> None:
        self._recovery_runs += 1
        self._last_recovery_at = self._now()
        self._rebuild_summary()

    def _rebuild_summary(self) -> None:
        records = list(self._engines.values())
        failed = sum(1 for item in records if item.get("status") in {"Delayed", "Degraded"})
        ready = sum(1 for item in records if item.get("status") == "Ready")
        status = "Healthy" if records and failed == 0 else ("Recovering" if failed else "Waiting")
        issues = [
            {
                "engine": name,
                "status": item.get("status"),
                "error": item.get("last_error"),
                "consecutive_failures": item.get("consecutive_failures", 0),
            }
            for name, item in self._engines.items()
            if item.get("status") in {"Delayed", "Degraded"}
        ]
        self._last = {
            "status": status,
            "version": self.VERSION,
            "mode": "recommendation_only",
            "tracked_engine_count": len(records),
            "ready_engine_count": ready,
            "issue_count": failed,
            "recovery_runs": self._recovery_runs,
            "last_recovery_at": self._last_recovery_at,
            "issues": issues[:10],
            "updated_at": self._now(),
            "summary": (
                "All guarded engines refreshed successfully."
                if status == "Healthy"
                else "Zeus isolated one or more delayed engines and kept the remaining stack online."
            ),
            "safety": "Recommendation only. No automatic device control.",
        }

    def summary(self) -> dict[str, Any]:
        return dict(self._last)
