"""Adaptive Advisor context for AION EMS Zeus v12.5.

Builds conservative household-preference context from resolved recommendation
outcomes. It never controls devices and never invents preferences when the
sample size is too small.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


class AdaptiveAdvisorEngine:
    """Learn practical recommendation timing and follow-through preferences."""

    VERSION = "1.0-alpha.4"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Learning",
            "version": self.VERSION,
            "category_preferences": {},
            "summary": "Zeus is collecting resolved recommendation outcomes.",
        }

    @staticmethod
    def _hour(value: Any) -> int | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().hour
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _day_type(value: Any) -> str | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
            return "weekend" if dt.weekday() >= 5 else "weekday"
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _window(hour: int) -> str:
        start = max(0, min(23, hour))
        end = (start + 1) % 24
        return f"{start:02d}:00–{end:02d}:00"

    def refresh(self) -> dict[str, Any]:
        try:
            learning = self.core.opportunity_learning.summary() or {}
        except Exception:
            learning = {}
        outcomes = learning.get("recent_outcomes", []) if isinstance(learning, dict) else []
        if not isinstance(outcomes, list):
            outcomes = []

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in outcomes:
            if isinstance(item, dict):
                grouped[str(item.get("category") or "unknown")].append(item)

        preferences: dict[str, Any] = {}
        for category, items in grouped.items():
            resolved = [x for x in items if x.get("status") in {"Completed", "Ignored", "Expired"}]
            completed = [x for x in resolved if x.get("status") == "Completed"]
            hours = [self._hour(x.get("resolved_at") or x.get("updated_at")) for x in completed]
            hours = [x for x in hours if x is not None]
            day_types = [self._day_type(x.get("resolved_at") or x.get("updated_at")) for x in completed]
            day_types = [x for x in day_types if x]
            preferred_hour = round(sum(hours) / len(hours)) if len(hours) >= 3 else None
            dominant_day_type = None
            if len(day_types) >= 3:
                weekday = day_types.count("weekday")
                weekend = day_types.count("weekend")
                dominant_day_type = "weekday" if weekday > weekend else "weekend" if weekend > weekday else None
            follow = (len(completed) / len(resolved) * 100.0) if resolved else None
            preferences[category] = {
                "sample_count": len(items),
                "resolved_count": len(resolved),
                "completed_count": len(completed),
                "follow_through_percent": round(follow, 1) if follow is not None else None,
                "preferred_hour": preferred_hour,
                "preferred_window": self._window(preferred_hour) if preferred_hour is not None else None,
                "dominant_day_type": dominant_day_type,
                "confidence": "Established" if len(completed) >= 5 else "Emerging" if len(completed) >= 3 else "Insufficient data",
            }

        established = sum(1 for p in preferences.values() if p.get("preferred_window"))
        self.last = {
            "status": "Ready" if established else "Learning",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "category_preferences": preferences,
            "established_preference_count": established,
            "summary": (
                f"Zeus has learned practical timing preferences for {established} recommendation category(s)."
                if established else
                "Zeus is collecting more completed recommendations before adapting timing advice."
            ),
            "adaptation_rule": "Timing preferences require at least three completed recommendations in a category.",
            "safety": "Preference learning and recommendation context only. No device control.",
            "recorder_safe": True,
        }
        try:
            self.event_bus.publish("AdaptiveAdvisorUpdated", "AdaptiveAdvisor", {"established": established})
        except Exception:
            pass
        return self.last

    def preference_for(self, category: str) -> dict[str, Any]:
        return dict((self.last.get("category_preferences") or {}).get(str(category or "unknown"), {}))

    def summary(self) -> dict[str, Any]:
        return self.last
