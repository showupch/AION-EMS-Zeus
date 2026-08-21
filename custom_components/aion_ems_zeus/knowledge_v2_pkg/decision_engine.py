"""Recommendation-only Decision Intelligence Core for AION EMS Zeus.

Ranks practical energy opportunities, explains confidence, and tracks a safe
in-memory lifecycle. It never calls Home Assistant services or controls devices.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class DecisionEngine:
    """Rank explainable energy opportunities without autonomous control."""

    VERSION = "2.3-opportunity-learning"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self._lifecycle: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._previous_active_ids: set[str] = set()
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "opportunities": [],
        }

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _first_number(cls, data: dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
        for key in keys:
            if data.get(key) is not None:
                value = cls._number(data.get(key), default)
                return value
        return default

    def _flexible_loads(self) -> list[dict[str, Any]]:
        try:
            devices = self.core.registry.data.get("devices", [])
        except Exception:
            devices = []
        excluded = ("inverter", "meter", "solar", "battery", "grid", "sensor")
        flexible_words = ("dishwasher", "washing", "dryer", "ev", "charger", "water heater", "boiler", "elwa", "heat pump", "pool", "pump")
        result: list[dict[str, Any]] = []
        for device in devices:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue
            name = str(device.get("name") or device.get("device_name") or device.get("id") or "Device")
            category = str(device.get("category") or device.get("type") or "load").lower()
            combined = f"{name} {category}".lower()
            if any(word in combined for word in excluded):
                continue
            if not (device.get("flexible") or any(word in combined for word in flexible_words)):
                continue
            result.append({
                "id": device.get("id") or device.get("device_id"),
                "name": name,
                "category": category,
                "rated_power_w": self._number(device.get("rated_power_w") or device.get("power_w"), 0.0) or None,
                "priority": str(device.get("priority") or "medium").lower(),
            })
        return result[:12]

    @staticmethod
    def _priority_label(score: int) -> str:
        if score >= 85:
            return "Critical"
        if score >= 70:
            return "High"
        if score >= 50:
            return "Medium"
        if score >= 30:
            return "Low"
        return "Information"

    @staticmethod
    def _risk(score: int, quality: float) -> str:
        if quality < 60:
            return "High"
        if score < 50 or quality < 80:
            return "Medium"
        return "Low"

    def _confidence_breakdown(self, forecast: float, learning: float, quality: float, live: bool, battery_known: bool) -> dict[str, int]:
        return {
            "forecast": int(max(0, min(100, round(forecast or 45)))),
            "learning": int(max(0, min(100, round(learning or 35)))),
            "battery": 95 if battery_known else 30,
            "solar_trend": 90 if live else 35,
            "data_quality": int(max(0, min(100, round(quality)))),
        }

    @staticmethod
    def _weighted_confidence(parts: dict[str, int]) -> int:
        weights = {"forecast": .25, "learning": .20, "battery": .15, "solar_trend": .20, "data_quality": .20}
        return int(max(20, min(99, round(sum(parts[k] * weights[k] for k in weights)))))

    def _score(self, benefit: float, urgency: float, confidence: int, target_valid: bool, quality: float) -> int:
        score = benefit * .30 + urgency * .25 + confidence * .30 + quality * .15
        if not target_valid:
            score -= 18
        return int(max(0, min(100, round(score))))

    @staticmethod
    def _quality_gate(item: dict[str, Any], quality: float) -> dict[str, Any]:
        """Evaluate whether advice is specific and sufficiently evidenced to surface."""
        checks = {
            "specific_action": bool(str(item.get("action") or "").strip()),
            "valid_target": bool(item.get("target_valid")) or item.get("category") in {"battery", "observe"},
            "trusted_data": quality >= 60,
            "sufficient_confidence": int(item.get("confidence_percent") or 0) >= 45,
            "explainable": bool(str(item.get("reason") or "").strip()),
            "execution_window": bool(str(item.get("best_window") or "").strip()),
        }
        passed = sum(1 for value in checks.values() if value)
        return {
            "passed": passed,
            "total": len(checks),
            "score_percent": int(round(passed / max(1, len(checks)) * 100)),
            "eligible": passed == len(checks),
            "checks": checks,
        }

    def refresh(self) -> dict[str, Any]:
        flow = self._summary(self.core.energy_flow)
        forecast = self._summary(self.core.forecast)
        learning = self._summary(self.core.learning_intelligence_v2)
        quality_data = self._summary(self.core.data_quality)
        optimizer = self._summary(self.core.optimizer)

        export_w = self._first_number(flow, ("grid_export_power", "grid_export_power_w", "export_power_w"))
        import_w = self._first_number(flow, ("grid_import_power", "grid_import_power_w", "import_power_w"))
        solar_w = self._first_number(flow, ("solar_power", "solar_power_w", "pv_power_w"))
        house_w = self._first_number(flow, ("house_power", "house_power_w", "home_power_w"))
        soc = self._first_number(flow, ("battery_soc_percent", "battery_soc", "soc_percent"), -1.0)
        forecast_conf = self._number(forecast.get("confidence_percent") or forecast.get("confidence"), 0.0)
        learning_conf = self._number(learning.get("confidence_percent") or learning.get("confidence"), 0.0)
        quality = self._number(quality_data.get("quality_score") or quality_data.get("score"), 100.0)
        best_window = forecast.get("best_surplus_window") if isinstance(forecast.get("best_surplus_window"), dict) else {}
        window = str(best_window.get("label") or "Now")
        loads = self._flexible_loads()
        parts = self._confidence_breakdown(forecast_conf, learning_conf, quality, solar_w > 0, soc >= 0)
        confidence = self._weighted_confidence(parts)
        now = datetime.now(timezone.utc).isoformat()
        opportunities: list[dict[str, Any]] = []

        def add(identifier: str, title: str, action: str, category: str, reason: str, benefit_text: str,
                benefit_score: float, urgency: float, target: dict[str, Any] | None = None, best: str = "Now",
                expected_benefit_value_kwh: float | None = None) -> None:
            target_valid = bool(target and target.get("name"))
            learning_adjustment = 0
            learning_profile: dict[str, Any] = {}
            try:
                learning_adjustment = self.core.opportunity_learning.confidence_adjustment(category)
                learning_profile = (self.core.opportunity_learning.summary().get("category_profiles") or {}).get(category, {})
            except Exception:
                pass
            item_confidence = int(max(20, min(99, confidence + learning_adjustment)))
            score = self._score(benefit_score, urgency, item_confidence, target_valid or category in {"battery", "observe"}, quality)
            previous = self._lifecycle.get(identifier, {})
            lifecycle = previous.get("status", "Detected")
            if lifecycle == "Detected":
                lifecycle = "Active"
            item = {
                "id": identifier,
                "title": title,
                "action": action,
                "target_id": target.get("id") if target else None,
                "target_name": target.get("name") if target else None,
                "target_valid": target_valid,
                "category": category,
                "priority_score": score,
                "priority": self._priority_label(score),
                "status": lifecycle,
                "best_window": best,
                "reason": reason,
                "expected_benefit": benefit_text,
                "confidence_percent": item_confidence,
                "confidence_breakdown": parts,
                "opportunity_learning": {"confidence_adjustment": learning_adjustment, "category_profile": learning_profile},
                "expected_benefit_value_kwh": expected_benefit_value_kwh,
                "actual_benefit_value_kwh": None,
                "risk": self._risk(score, quality),
                "alternatives": ["Wait for the next forecast update", "Keep the current plan"],
                "created_at": previous.get("created_at", now),
                "updated_at": now,
                "outcome_status": "Not measured",
                "actual_benefit": None,
                "measurement_note": "Outcome evaluation will only be shown when Zeus has enough measured before/after data.",
            }
            item["quality_gate"] = self._quality_gate(item, quality)
            item["eligible"] = bool(item["quality_gate"]["eligible"])
            self._lifecycle[identifier] = {"status": lifecycle, "created_at": item["created_at"], "updated_at": now}
            opportunities.append(item)

        if export_w >= 500 and loads:
            for index, load in enumerate(loads[:3]):
                rated = self._number(load.get("rated_power_w"), export_w)
                covered = min(export_w, rated or export_w)
                add(
                    f"solar_surplus_{load.get('id') or index}",
                    f"Run {load['name']}",
                    f"Run {load['name']} now",
                    "load",
                    f"About {export_w:.0f} W is being exported and can cover approximately {covered:.0f} W of this load.",
                    f"Use up to {covered/1000:.2f} kWh of solar per hour instead of exporting it.",
                    min(100.0, 45.0 + export_w / 25.0),
                    92.0,
                    load,
                    window,
                    covered / 1000.0,
                )
        elif export_w >= 500:
            add("unassigned_solar_surplus", "Unassigned solar opportunity", "Map a flexible load", "load",
                f"About {export_w:.0f} W is being exported, but Zeus cannot identify a valid target device.",
                "A mapped flexible load is required before actionable advice can be shown.", 55, 70, None, window)

        if import_w >= 500 and loads:
            target = loads[0]
            add("delay_import_load", f"Delay {target['name']}", f"Delay {target['name']} until {window}", "grid",
                f"The home is importing about {import_w:.0f} W right now.", "May reduce grid purchases by shifting demand.",
                min(90.0, 40.0 + import_w / 30.0), 75.0, target, window)

        if soc >= 0 and soc <= 25:
            add("preserve_battery", "Preserve battery reserve", "Avoid non-essential battery use", "battery",
                f"Battery SOC is {soc:.0f}%.", "Reduces the risk of avoidable grid import later.", 70, 85, None, "Now")

        if not opportunities:
            existing_raw = str(optimizer.get("recommendation") or optimizer.get("priority") or "").strip()
            generic = existing_raw.lower() in {"", "continue current operation", "hold", "normal", "ready"}
            title = "No high-value action right now" if generic else existing_raw
            action = "Keep monitoring current conditions" if generic else existing_raw
            add("continue_observing", title, action, "observe",
                "Energy levels are balanced, no immediate grid spike is present, and Zeus has not identified a verified flexible-load opportunity.",
                "The system is expected to remain stable without unnecessary changes.", 35, 25, None, window)

        # Resolve recommendations that were active on the previous refresh but are no longer valid.
        current_ids = {item["id"] for item in opportunities}
        for missing_id in sorted(self._previous_active_ids - current_ids):
            previous = self._lifecycle.get(missing_id, {})
            previous["status"] = "Expired"
            previous["updated_at"] = now
            self._lifecycle[missing_id] = previous
            for record in reversed(self._history):
                if record.get("id") == missing_id and record.get("status") in {"Detected", "Active"}:
                    record["status"] = "Expired"
                    record["resolved_at"] = now
                    record["outcome_status"] = "Not measurable"
                    record["measurement_note"] = "The opportunity ended before a verified completion signal was available."
                    break

        # Keep one compact history record per recommendation lifecycle.
        for item in opportunities:
            existing = next((record for record in reversed(self._history) if record.get("id") == item["id"] and record.get("status") in {"Detected", "Active"}), None)
            snapshot = {
                "id": item["id"], "title": item["title"], "action": item["action"],
                "target_name": item.get("target_name"), "category": item["category"],
                "status": item["status"], "priority": item["priority"],
                "priority_score": item["priority_score"], "best_window": item["best_window"],
                "expected_benefit": item["expected_benefit"],
                "expected_benefit_value_kwh": item.get("expected_benefit_value_kwh"),
                "actual_benefit_value_kwh": item.get("actual_benefit_value_kwh"),
                "actual_benefit": item.get("actual_benefit"),
                "outcome_status": item.get("outcome_status", "Not measured"),
                "measurement_note": item.get("measurement_note"),
                "confidence_percent": item["confidence_percent"],
                "created_at": item["created_at"], "updated_at": now,
            }
            if existing is None:
                self._history.append(snapshot)
            else:
                existing.update(snapshot)
        self._history = self._history[-60:]
        self._previous_active_ids = current_ids

        opportunities.sort(key=lambda item: (not item.get("eligible", False), -item["priority_score"], -item["confidence_percent"], item["title"]))
        for index, item in enumerate(opportunities, 1):
            item["rank"] = index
        eligible_opportunities = [item for item in opportunities if item.get("eligible")]
        primary = eligible_opportunities[0] if eligible_opportunities else opportunities[0]

        self.last = {
            "status": "Ready",
            "version": self.VERSION,
            "mode": "recommendation_only",
            "decision": primary["action"],
            "best_recommendation_id": primary["id"],
            "category": primary["category"],
            "reason": primary["reason"],
            "expected_benefit": primary["expected_benefit"],
            "best_window": primary["best_window"],
            "priority_score": primary["priority_score"],
            "priority": primary["priority"],
            "confidence_percent": primary["confidence_percent"],
            "confidence_breakdown": primary.get("confidence_breakdown", parts),
            "opportunity_learning": primary.get("opportunity_learning", {}),
            "risk": primary["risk"],
            "opportunities": opportunities[:8],
            "ranked_actions": opportunities[:8],
            "eligible_opportunities": eligible_opportunities[:8],
            "eligible_count": len(eligible_opportunities),
            "suppressed_count": len(opportunities) - len(eligible_opportunities),
            "recommendation_quality_gate": primary.get("quality_gate", {}),
            "recommendation_history": list(reversed(self._history[-40:])),
            "history_counts": {status: sum(1 for item in self._history if item.get("status") == status) for status in ("Active", "Completed", "Expired", "Ignored")},
            "lifecycle": {key: dict(value) for key, value in list(self._lifecycle.items())[-20:]},
            "candidate_devices": loads,
            "live_context": {"solar_w": round(solar_w, 1), "home_w": round(house_w, 1), "grid_import_w": round(import_w, 1), "grid_export_w": round(export_w, 1), "battery_soc_percent": None if soc < 0 else round(soc, 1)},
            "updated_at": now,
            "recorder_safe": True,
            "safety": "Recommendation only. No device, inverter or battery services are called.",
        }
        try:
            self.event_bus.publish("DecisionEngineUpdated", "DecisionEngine", {"decision": primary["id"], "score": primary["priority_score"], "confidence": confidence})
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
