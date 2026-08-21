"""Recommendation consolidation and priority scoring for AION EMS Zeus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriorityRecommendation:
    title: str
    message: str
    action: str
    priority: str
    score: int
    confidence_percent: int
    severity_score: int
    energy_impact_score: int
    financial_impact_score: int
    urgency_score: int
    source: str
    target_page: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "action": self.action,
            "priority": self.priority,
            "score": self.score,
            "confidence_percent": self.confidence_percent,
            "severity_score": self.severity_score,
            "energy_impact_score": self.energy_impact_score,
            "financial_impact_score": self.financial_impact_score,
            "urgency_score": self.urgency_score,
            "source": self.source,
            "target_page": self.target_page,
        }


class RecommendationPriorityEngine:
    """Rank and consolidate existing intelligence without polling or control."""

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core

    @staticmethod
    def _summary(obj: Any) -> dict[str, Any]:
        try:
            value = obj.summary() or {}
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if number == number else default

    @staticmethod
    def _priority(score: int, severity: int) -> str:
        if severity >= 90 or score >= 82:
            return "Critical"
        if severity >= 65 or score >= 62:
            return "Important"
        if score >= 42:
            return "Improvement"
        return "Information"

    @staticmethod
    def _dedupe(items: list[PriorityRecommendation]) -> list[PriorityRecommendation]:
        result: list[PriorityRecommendation] = []
        seen: set[str] = set()
        for item in sorted(items, key=lambda row: (-row.score, row.title.lower())):
            key = " ".join(item.title.lower().split())
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _make(
        self,
        *,
        title: str,
        message: str,
        action: str,
        severity: int,
        confidence: int,
        energy: int,
        finance: int,
        urgency: int,
        source: str,
        page: str,
    ) -> PriorityRecommendation:
        score = round(
            severity * 0.30
            + confidence * 0.20
            + energy * 0.20
            + finance * 0.15
            + urgency * 0.10
            + severity * 0.05
        )
        score = int(max(0, min(100, score)))
        return PriorityRecommendation(
            title=title,
            message=message,
            action=action,
            priority=self._priority(score, severity),
            score=score,
            confidence_percent=int(max(0, min(100, confidence))),
            severity_score=int(max(0, min(100, severity))),
            energy_impact_score=int(max(0, min(100, energy))),
            financial_impact_score=int(max(0, min(100, finance))),
            urgency_score=int(max(0, min(100, urgency))),
            source=source,
            target_page=page,
        )

    def summary(self) -> dict[str, Any]:
        root = self._summary(self.core.root_cause_intelligence)
        corr = self._summary(self.core.correlation_confidence)
        qa = self._summary(self.core.qa_diagnostics)
        quality = self._summary(self.core.data_quality)
        forecast = self._summary(self.core.forecast)

        recommendations: list[PriorityRecommendation] = []
        root_severity = str(root.get("severity") or "Information").lower()
        severity_map = {"critical": 95, "important": 72, "warning": 68, "improvement": 45, "information": 24}
        root_severity_score = severity_map.get(root_severity, 30)
        root_confidence = int(self._number(root.get("confidence_percent"), 65))
        primary = str(root.get("primary_cause") or "System condition is being evaluated")
        recommended_action = str(root.get("recommended_action") or "Continue normal monitoring.")

        if root_severity_score >= 40 or "no material" not in primary.lower():
            energy = 75 if any(token in primary.lower() for token in ("solar", "grid", "battery", "demand")) else 45
            finance = 70 if "grid" in primary.lower() else 35
            urgency = 75 if root_severity_score >= 65 else 35
            recommendations.append(self._make(
                title=primary,
                message=str(root.get("summary") or primary),
                action=recommended_action,
                severity=root_severity_score,
                confidence=root_confidence,
                energy=energy,
                finance=finance,
                urgency=urgency,
                source="Root Cause Intelligence",
                page="system_health",
            ))

        conflicts = list(corr.get("conflicts") or [])
        missing = list(corr.get("missing_evidence") or [])
        corr_confidence = int(self._number(corr.get("confidence_percent"), 50))
        if conflicts:
            recommendations.append(self._make(
                title="Resolve conflicting system evidence",
                message="; ".join(str(x) for x in conflicts[:3]),
                action="Review System Health and entity mappings before relying on affected recommendations.",
                severity=78,
                confidence=max(55, corr_confidence),
                energy=55,
                finance=45,
                urgency=70,
                source="Correlation & Confidence",
                page="system_health",
            ))
        elif missing:
            recommendations.append(self._make(
                title="Improve intelligence evidence coverage",
                message="; ".join(str(x) for x in missing[:3]),
                action="Review unavailable forecast, weather, runtime, or mapping evidence when convenient.",
                severity=40,
                confidence=max(45, corr_confidence),
                energy=35,
                finance=25,
                urgency=28,
                source="Correlation & Confidence",
                page="system_health",
            ))

        qa_errors = int(self._number(qa.get("error_count")))
        qa_warnings = int(self._number(qa.get("warning_count")))
        if qa_errors or qa_warnings >= 3:
            recommendations.append(self._make(
                title="Review platform health findings",
                message=f"System Health reports {qa_errors} error(s) and {qa_warnings} warning(s).",
                action="Run the health check and resolve the highest-severity finding first.",
                severity=95 if qa_errors else 65,
                confidence=96,
                energy=45,
                finance=35,
                urgency=90 if qa_errors else 55,
                source="QA Diagnostics",
                page="system_health",
            ))

        mapping_issues = int(self._number(quality.get("invalid_mapping_count")))
        if isinstance(quality.get("issues"), list):
            mapping_issues += len(quality["issues"])
        if mapping_issues:
            recommendations.append(self._make(
                title="Correct data-quality and mapping issues",
                message=f"Zeus identified {mapping_issues} mapping or evidence issue(s).",
                action="Review Energy Sources and System Health to correct stale or unavailable mappings.",
                severity=62,
                confidence=92,
                energy=55,
                finance=45,
                urgency=52,
                source="Data Quality",
                page="sources",
            ))

        forecast_confidence = int(self._number(forecast.get("confidence_percent", forecast.get("confidence")), 0))
        if forecast_confidence and forecast_confidence < 65:
            recommendations.append(self._make(
                title="Treat near-term forecast as lower confidence",
                message=f"Forecast confidence is currently {forecast_confidence}%.",
                action="Use the forecast as guidance and avoid committing critical flexible loads solely from it.",
                severity=30,
                confidence=90,
                energy=42,
                finance=38,
                urgency=30,
                source="Forecast Intelligence",
                page="intelligence",
            ))

        if not recommendations:
            recommendations.append(self._make(
                title="No immediate system action required",
                message="Current platform, data, and operational evidence does not indicate an urgent issue.",
                action="Continue normal monitoring.",
                severity=10,
                confidence=max(70, corr_confidence),
                energy=10,
                finance=10,
                urgency=5,
                source="System Intelligence",
                page="system_health",
            ))

        ranked = self._dedupe(recommendations)[:8]
        counts = {name: sum(1 for row in ranked if row.priority == name) for name in ("Critical", "Important", "Improvement", "Information")}
        top = ranked[0]
        return {
            "status": top.priority,
            "top_priority": top.priority,
            "top_score": top.score,
            "top_title": top.title,
            "recommendation_count": len(ranked),
            "priority_counts": counts,
            "recommendations": [row.as_dict() for row in ranked],
            "summary": f"{len(ranked)} consolidated recommendation(s); highest priority is {top.priority.lower()}.",
            "recommendation_only": True,
            "recorder_safe": True,
        }
