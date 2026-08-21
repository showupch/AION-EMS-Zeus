"""Plain-language installation story for AION EMS Zeus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SystemStory:
    headline: str
    story: str
    action_state: str
    outlook: str
    confidence_percent: int
    key_points: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "story": self.story,
            "action_state": self.action_state,
            "outlook": self.outlook,
            "confidence_percent": self.confidence_percent,
            "key_points": list(self.key_points),
            "recommendation_only": True,
            "recorder_safe": True,
        }


class SystemStoryEngine:
    """Summarize existing engine outputs without adding polling or control."""

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core

    @staticmethod
    def _summary(obj: Any) -> dict[str, Any]:
        try:
            data = obj.summary() or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return default

    def summary(self) -> dict[str, Any]:
        root = self._summary(self.core.root_cause_intelligence)
        corr = self._summary(self.core.correlation_confidence)
        priority = self._summary(self.core.recommendation_priority)
        forecast = self._summary(self.core.forecast)

        primary = str(root.get("primary_cause") or "Zeus is collecting system evidence")
        secondary = [str(x) for x in (root.get("secondary_causes") or []) if str(x).strip()]
        root_action = str(root.get("recommended_action") or "Continue normal monitoring.")
        corr_class = str(corr.get("classification") or "Evidence is still being evaluated")
        corr_confidence = self._int(corr.get("confidence_percent"), self._int(root.get("confidence_percent"), 65))
        conflicts = [str(x) for x in (corr.get("conflicts") or [])]
        missing = [str(x) for x in (corr.get("missing_evidence") or [])]
        recommendations = priority.get("recommendations") if isinstance(priority.get("recommendations"), list) else []
        top = recommendations[0] if recommendations and isinstance(recommendations[0], dict) else {}
        top_priority = str(top.get("priority") or priority.get("top_priority") or "Information")
        top_title = str(top.get("title") or priority.get("top_title") or "No immediate system action required")

        if conflicts:
            action_state = "Investigate"
            headline = "System evidence needs review"
        elif top_priority == "Critical":
            action_state = "Act soon"
            headline = "A high-priority system issue needs attention"
        elif top_priority == "Important":
            action_state = "Monitor and review"
            headline = "An important system condition is active"
        elif top_priority == "Improvement":
            action_state = "Improve when convenient"
            headline = "The system is stable with an improvement opportunity"
        else:
            action_state = "No action required"
            headline = "The installation is operating without an urgent fault"

        forecast_conf = self._int(forecast.get("confidence_percent", forecast.get("confidence")), 0)
        forecast_method = str(forecast.get("method") or forecast.get("source") or "").replace("_", " ")
        outlook = str(root.get("expected_duration") or "Until the next material system change")
        if forecast_conf:
            outlook += f". Forecast confidence is {forecast_conf}%"
            if forecast_method:
                outlook += f" using {forecast_method}"
            outlook += "."

        story_parts = [primary.rstrip(".") + "."]
        if secondary:
            story_parts.append("Contributing factors include " + ", ".join(secondary[:3]).lower() + ".")
        story_parts.append(f"Cross-system evidence is classified as {corr_class.lower()} at {corr_confidence}% confidence.")
        if missing and not conflicts:
            story_parts.append("Some evidence is unavailable, so Zeus is presenting the conclusion with limitations.")
        if conflicts:
            story_parts.append("Conflicting signals should be reviewed before relying on the affected recommendation.")
        story_parts.append((str(top.get("action") or root_action)).rstrip(".") + ".")

        key_points = [primary, top_title]
        if secondary:
            key_points.extend(secondary[:2])
        if conflicts:
            key_points.append(f"{len(conflicts)} evidence conflict(s) detected")
        elif missing:
            key_points.append(f"{len(missing)} evidence source(s) unavailable")

        confidence = max(25, min(98, round((corr_confidence * 0.65) + (self._int(root.get("confidence_percent"), corr_confidence) * 0.35))))
        return SystemStory(
            headline=headline,
            story=" ".join(story_parts),
            action_state=action_state,
            outlook=outlook,
            confidence_percent=confidence,
            key_points=tuple(key_points[:5]),
        ).as_dict()
