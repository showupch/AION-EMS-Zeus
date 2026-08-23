"""Storage-backed Opportunity Learning for AION EMS Zeus.

Learns from recommendation lifecycle outcomes without controlling devices or
inventing benefits. Only verified lifecycle and measurable outcome fields are
used for calibration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store

STORAGE_VERSION = 1
STORAGE_KEY = "aion_ems_zeus.opportunity_learning"
MAX_RECORDS = 300


class OpportunityLearningEngine:
    """Evaluate recommendation outcomes and expose conservative feedback."""

    VERSION = "1.3-passive-learning-cleanup"

    def __init__(self, hass: Any, event_bus: Any, core: Any) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.core = core
        self.store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {"records": {}, "metadata": {"max_records": MAX_RECORDS}}
        self.last: dict[str, Any] = {
            "status": "Learning",
            "version": self.VERSION,
            "sample_count": 0,
            "resolved_count": 0,
            "category_profiles": {},
            "confidence_adjustments": {},
        }

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data.update(stored)
        self.data.setdefault("records", {})
        self.data["records"] = {
            str(key): value for key, value in self.data.get("records", {}).items()
            if isinstance(value, dict) and self._learning_eligible(value)
        }
        self.data.setdefault("metadata", {"max_records": MAX_RECORDS})
        self.refresh()

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _record_key(item: dict[str, Any]) -> str:
        return f"{item.get('id') or 'unknown'}::{item.get('created_at') or item.get('updated_at') or 'unknown'}"

    @staticmethod
    def _learning_eligible(item: dict[str, Any]) -> bool:
        category = str(item.get("category") or "").strip().lower()
        identifier = str(item.get("id") or "").strip().lower()
        action = str(item.get("action") or "").strip().lower()
        title = str(item.get("title") or "").strip().lower()
        if category == "observe":
            return False
        if identifier in {"continue_observing", "no_high_value_action", "hold"}:
            return False
        if action in {"keep monitoring current conditions", "continue current operation", "hold", "monitor"}:
            return False
        if title == "no high-value action right now":
            return False
        return True

    @staticmethod
    def _is_resolved(status: str) -> bool:
        return status in {"Completed", "Ignored", "Expired"}

    def _ingest(self) -> bool:
        try:
            summary = self.core.decision_engine.summary()
        except Exception:
            summary = {}
        history = summary.get("recommendation_history", []) if isinstance(summary, dict) else []
        if not isinstance(history, list):
            history = []
        records = self.data.setdefault("records", {})
        changed = False
        now = datetime.now(timezone.utc).isoformat()
        for item in history:
            if not isinstance(item, dict) or not item.get("id") or not self._learning_eligible(item):
                continue
            key = self._record_key(item)
            previous = records.get(key, {})
            expected = self._number(item.get("expected_benefit_value_kwh"))
            actual = self._number(item.get("actual_benefit_value_kwh") or item.get("actual_benefit"))
            status = str(item.get("status") or "Active")
            normalized = {
                "id": item.get("id"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at") or now,
                "resolved_at": item.get("resolved_at"),
                "title": item.get("title"),
                "target_name": item.get("target_name"),
                "category": str(item.get("category") or "unknown"),
                "status": status,
                "confidence_percent": self._number(item.get("confidence_percent")),
                "priority_score": self._number(item.get("priority_score")),
                "expected_benefit_value_kwh": expected,
                "actual_benefit_value_kwh": actual,
                "outcome_status": item.get("outcome_status") or "Not measured",
                "measurement_note": item.get("measurement_note"),
            }
            if normalized != previous:
                records[key] = normalized
                changed = True
        if len(records) > MAX_RECORDS:
            ordered = sorted(records.items(), key=lambda pair: str(pair[1].get("updated_at") or ""))
            self.data["records"] = dict(ordered[-MAX_RECORDS:])
            changed = True
        return changed

    @classmethod
    def _profile(cls, items: list[dict[str, Any]]) -> dict[str, Any]:
        resolved = [x for x in items if cls._is_resolved(str(x.get("status") or ""))]
        completed = [x for x in resolved if x.get("status") == "Completed"]
        ignored = [x for x in resolved if x.get("status") == "Ignored"]
        expired = [x for x in resolved if x.get("status") == "Expired"]
        measurable = [
            x for x in completed
            if cls._number(x.get("expected_benefit_value_kwh")) is not None
            and cls._number(x.get("actual_benefit_value_kwh")) is not None
            and cls._number(x.get("expected_benefit_value_kwh")) != 0
        ]
        errors: list[float] = []
        for item in measurable:
            expected = cls._number(item.get("expected_benefit_value_kwh")) or 0.0
            actual = cls._number(item.get("actual_benefit_value_kwh")) or 0.0
            errors.append(abs(actual - expected) / abs(expected) * 100.0)
        confidences = [cls._number(x.get("confidence_percent")) for x in resolved]
        confidences = [x for x in confidences if x is not None]
        completion_rate = (len(completed) / len(resolved) * 100.0) if resolved else None
        accuracy = (max(0.0, 100.0 - sum(errors) / len(errors))) if errors else None
        return {
            "sample_count": len(items),
            "resolved_count": len(resolved),
            "completed_count": len(completed),
            "ignored_count": len(ignored),
            "expired_count": len(expired),
            "follow_through_percent": round(completion_rate, 1) if completion_rate is not None else None,
            "measurable_outcome_count": len(measurable),
            "benefit_prediction_accuracy_percent": round(accuracy, 1) if accuracy is not None else None,
            "average_confidence_percent": round(sum(confidences) / len(confidences), 1) if confidences else None,
        }

    @staticmethod
    def _adjustment(profile: dict[str, Any], outcome: dict[str, Any]) -> int:
        """Return a small, evidence-gated confidence calibration.

        No adjustment is allowed before three resolved outcomes in the same
        category. Observed responses are correlation evidence only and are
        intentionally bounded to a small effect.
        """
        resolved = int(outcome.get("resolved_count") or profile.get("resolved_count") or 0)
        if resolved < 3:
            return 0
        observed = int(outcome.get("observed_system_response_count") or 0)
        not_measurable = int(outcome.get("not_measurable_count") or 0)
        quantified = int(profile.get("measurable_outcome_count") or 0)
        accuracy = profile.get("benefit_prediction_accuracy_percent")
        adjustment = 0

        response_rate = observed / max(1, resolved)
        not_measurable_rate = not_measurable / max(1, resolved)
        if response_rate >= 0.75:
            adjustment += 2
        elif response_rate >= 0.50:
            adjustment += 1
        elif not_measurable_rate >= 0.75:
            adjustment -= 2
        elif not_measurable_rate >= 0.50:
            adjustment -= 1

        # Quantified benefit accuracy is stronger evidence, but only after at
        # least three genuinely measured benefit outcomes.
        if quantified >= 3 and isinstance(accuracy, (int, float)):
            if accuracy >= 85:
                adjustment += 2
            elif accuracy < 60:
                adjustment -= 2

        return max(-4, min(4, adjustment))

    def refresh(self) -> dict[str, Any]:
        changed = self._ingest()
        filtered_records = {
            str(key): value for key, value in self.data.get("records", {}).items()
            if isinstance(value, dict) and self._learning_eligible(value)
        }
        if len(filtered_records) != len(self.data.get("records", {})):
            self.data["records"] = filtered_records
            changed = True
        records = list(filtered_records.values())
        categories = sorted({str(x.get("category") or "unknown") for x in records})
        profiles = {category: self._profile([x for x in records if str(x.get("category") or "unknown") == category]) for category in categories}
        overall = self._profile(records)
        resolved = int(overall.get("resolved_count") or 0)
        observed_responses = [x for x in records if str(x.get("outcome_status") or "") == "Observed system response"]
        not_measurable = [x for x in records if str(x.get("outcome_status") or "") == "Not measurable" and self._is_resolved(str(x.get("status") or ""))]
        measured_outcomes = [x for x in records if self._number(x.get("actual_benefit_value_kwh")) is not None]
        resolved_with_classification = len(observed_responses) + len(not_measurable)
        observed_response_percent = round(len(observed_responses) / resolved_with_classification * 100.0, 1) if resolved_with_classification else None
        category_outcomes = {}
        for category in categories:
            category_records = [x for x in records if str(x.get("category") or "unknown") == category]
            category_resolved = [x for x in category_records if self._is_resolved(str(x.get("status") or ""))]
            category_observed = [x for x in category_resolved if str(x.get("outcome_status") or "") == "Observed system response"]
            category_not_measurable = [x for x in category_resolved if str(x.get("outcome_status") or "") == "Not measurable"]
            category_outcomes[category] = {
                "resolved_count": len(category_resolved),
                "observed_system_response_count": len(category_observed),
                "not_measurable_count": len(category_not_measurable),
                "observed_response_percent": round(len(category_observed) / len(category_resolved) * 100.0, 1) if category_resolved else None,
            }
        adjustments = {
            category: self._adjustment(profiles[category], category_outcomes.get(category, {}))
            for category in categories
        }
        calibration = {}
        for category in categories:
            category_outcome = category_outcomes.get(category, {})
            category_resolved = int(category_outcome.get("resolved_count") or 0)
            adjustment = int(adjustments.get(category) or 0)
            calibration[category] = {
                "resolved_count": category_resolved,
                "adjustment_points": adjustment,
                "active": category_resolved >= 3,
                "gate_remaining": max(0, 3 - category_resolved),
                "reason": (
                    "Waiting for at least 3 resolved outcomes in this category."
                    if category_resolved < 3 else
                    "Small bounded calibration from observed outcome history; correlation is not treated as causation."
                ),
            }

        evidence_strength = (
            "No resolved outcomes yet" if resolved == 0 else
            "Early evidence" if resolved < 3 else
            "Building evidence" if resolved < 10 else
            "Established outcome history"
        )
        self.last = {
            "status": "Ready" if resolved >= 3 else "Learning",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "sample_count": len(records),
            "resolved_count": resolved,
            "overall_profile": overall,
            "category_profiles": profiles,
            "confidence_adjustments": adjustments,
            "adaptive_confidence": {
                "minimum_resolved_per_category": 3,
                "passive_categories_excluded": ["observe"],
                "maximum_adjustment_points": 4,
                "minimum_adjustment_points": -4,
                "category_calibration": calibration,
                "rule": "No confidence adjustment before 3 resolved outcomes in the same category. Adjustments remain bounded to ±4 points.",
                "causality_boundary": "Observed system response may calibrate confidence only as correlation evidence; Zeus does not infer that its recommendation caused the change.",
            },
            "outcome_intelligence": {
                "resolved_count": resolved,
                "observed_system_response_count": len(observed_responses),
                "not_measurable_count": len(not_measurable),
                "quantified_actual_benefit_count": len(measured_outcomes),
                "observed_response_percent": observed_response_percent,
                "evidence_strength": evidence_strength,
                "category_outcomes": category_outcomes,
                "causality_boundary": "Observed system response is correlation only. Zeus does not claim the recommendation caused the measured change.",
            },
            "recent_outcomes": sorted(records, key=lambda x: str(x.get("updated_at") or ""), reverse=True)[:20],
            "summary": (
                f"Zeus has evaluated {resolved} resolved recommendation outcome(s)."
                if resolved else
                "Zeus is waiting for resolved recommendations before adapting confidence."
            ),
            "learning_rule": "Confidence changes only after at least three resolved outcomes in a category.",
            "recorder_safe": True,
            "safety": "Learning and recommendation calibration only. No device control.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if changed:
            try:
                self.hass.async_create_task(self.store.async_save(self.data))
            except Exception:
                pass
        return self.last

    def confidence_adjustment(self, category: str) -> int:
        return int((self.last.get("confidence_adjustments") or {}).get(str(category or "unknown"), 0))

    def summary(self) -> dict[str, Any]:
        return self.last
