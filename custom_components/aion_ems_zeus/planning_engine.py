"""Persistent recommendation-only planning snapshots for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORAGE_VERSION = 1
STORAGE_KEY = "aion_ems_zeus.planning_engine"
MAX_PLANS = 400

_LOGGER = logging.getLogger(__name__)


class PlanningEngine:
    """Store immutable pre-day planning snapshots and compare them with results."""

    def __init__(self, hass: HomeAssistant, event_bus, core) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.core = core
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._plans: dict[str, dict[str, Any]] = {}
        self._last_saved_at: str | None = None

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        plans = data.get("plans") if isinstance(data, dict) else {}
        self._plans = dict(plans) if isinstance(plans, dict) else {}
        self._last_saved_at = data.get("last_saved_at") if isinstance(data, dict) else None
        # Planning is an advisory feature and must never block integration startup.
        try:
            await self.async_capture_upcoming()
        except Exception:  # noqa: BLE001 - startup safety boundary
            _LOGGER.exception("Unable to capture the upcoming Zeus plan during startup")

    async def async_capture_upcoming(self) -> bool:
        """Capture tomorrow's plan once; never rewrite an existing pre-day snapshot."""
        tomorrow = (datetime.now().astimezone().date() + timedelta(days=1)).isoformat()
        if tomorrow in self._plans:
            return False
        try:
            snapshot = self._build_snapshot(tomorrow)
        except Exception:  # noqa: BLE001 - planning must remain non-blocking
            _LOGGER.exception("Unable to build the upcoming Zeus planning snapshot")
            return False
        if snapshot.get("expected_solar_kwh", 0) <= 0:
            return False
        self._plans[tomorrow] = snapshot
        self._trim()
        self._last_saved_at = datetime.now().astimezone().isoformat()
        await self.store.async_save({"plans": self._plans, "last_saved_at": self._last_saved_at})
        self.event_bus.publish(
            "PlanningSnapshotStored",
            "PlanningEngine",
            {"date": tomorrow, "confidence_percent": snapshot.get("confidence_percent")},
        )
        return True

    def _num(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


    def _energy_flow_snapshot(self) -> dict[str, Any]:
        """Return the current read-only energy-flow state without assuming an API.

        EnergyFlowEngine exposes ``summary()`` and ``refresh()``.  Older planning
        code called a nonexistent ``snapshot()`` method, which prevented the
        whole integration from starting.  This compatibility boundary keeps
        planning optional and safely extracts the nested battery SOC value.
        """
        engine = getattr(self.core, "energy_flow", None)
        if engine is None:
            return {}
        state: dict[str, Any] = {}
        try:
            summary = getattr(engine, "summary", None)
            if callable(summary):
                value = summary()
                if isinstance(value, dict):
                    state = value
            if state.get("status") != "Ready":
                refresh = getattr(engine, "refresh", None)
                if callable(refresh):
                    value = refresh()
                    if isinstance(value, dict):
                        state = value
        except Exception:  # noqa: BLE001 - planning must not affect core startup
            _LOGGER.debug("Energy-flow context unavailable for planning snapshot", exc_info=True)
            return {}
        return state

    def _build_snapshot(self, target_date: str) -> dict[str, Any]:
        forecast = self.core.forecast.summary() or {}
        days = forecast.get("daily_forecast") if isinstance(forecast.get("daily_forecast"), list) else []
        day = next((x for x in days if str(x.get("date")) == target_date), None)
        if day is None and len(days) > 1:
            day = days[1]
        day = day or {}

        historical = self.core.history.summary() or {}
        averages = historical.get("averages") if isinstance(historical.get("averages"), dict) else {}
        expected_solar = self._num(day.get("expected_solar_kwh"), self._num(forecast.get("expected_solar_following_24h_kwh")))
        expected_consumption = self._num(
            day.get("expected_consumption_kwh"),
            self._num(forecast.get("expected_consumption_following_24h_kwh"), self._num(averages.get("house_energy_kwh"))),
        )
        expected_import = self._num(day.get("expected_grid_import_kwh"), max(0.0, expected_consumption - expected_solar))
        expected_export = self._num(day.get("expected_grid_export_kwh"), max(0.0, expected_solar - expected_consumption))

        finance = self.core.finance.summary() or {}
        import_tariff = self._num(finance.get("import_tariff"))
        export_tariff = self._num(finance.get("export_tariff"))
        expected_import_cost = expected_import * import_tariff
        expected_export_credit = expected_export * export_tariff
        expected_solar_saving = min(expected_consumption, expected_solar) * import_tariff

        energy_flow = self._energy_flow_snapshot()
        flows = energy_flow.get("flows") if isinstance(energy_flow.get("flows"), dict) else {}
        soc = self._num(
            flows.get("battery_soc_percent"),
            self._num(energy_flow.get("battery_soc_percent"), self._num(energy_flow.get("battery_soc"))),
        )
        confidence = self._num(day.get("confidence"), self._num(forecast.get("confidence"), 50.0))
        weather = day.get("condition") or day.get("weather") or forecast.get("weather") or "Weather context unavailable"

        return {
            "date": target_date,
            "created_at": datetime.now().astimezone().isoformat(),
            "immutable_pre_day_snapshot": True,
            "recommendation_only": True,
            "expected_solar_kwh": round(expected_solar, 3),
            "expected_consumption_kwh": round(expected_consumption, 3),
            "expected_grid_import_kwh": round(expected_import, 3),
            "expected_grid_export_kwh": round(expected_export, 3),
            "expected_battery_soc_start_percent": round(soc, 1),
            "expected_import_cost": round(expected_import_cost, 2),
            "expected_export_credit": round(expected_export_credit, 2),
            "expected_solar_saving": round(expected_solar_saving, 2),
            "confidence_percent": round(max(0.0, min(100.0, confidence)), 1),
            "weather_summary": str(weather),
            "currency": str(finance.get("currency") or "CHF"),
            "method": str(forecast.get("method") or "weather_adjusted_historical"),
        }

    def _trim(self) -> None:
        if len(self._plans) <= MAX_PLANS:
            return
        for key in sorted(self._plans)[:-MAX_PLANS]:
            self._plans.pop(key, None)

    def _completed_results(self) -> list[dict[str, Any]]:
        """Return only matured plan-versus-measured rows used by learning.

        A stored plan is not a completed comparison until its local target day has
        finished *and* canonical Home Assistant daily statistics contain the
        matching measured row.  In particular, never turn a missing future
        measurement into 0 kWh and score it as a real outcome.
        """
        historical = self.core.history.summary() or {}
        completed_rows = historical.get("completed_30_days")
        if not isinstance(completed_rows, list):
            # Compatibility with older analytics payloads: filter the rolling
            # history explicitly so today's collecting row cannot mature early.
            candidates = historical.get("last_30_days")
            if not isinstance(candidates, list):
                candidates = historical.get("last_7_days")
            candidates = candidates if isinstance(candidates, list) else []
            today = datetime.now().astimezone().date()
            completed_rows = []
            for item in candidates:
                if not isinstance(item, dict) or not item.get("date"):
                    continue
                try:
                    item_date = datetime.fromisoformat(str(item.get("date"))).date()
                except (TypeError, ValueError):
                    continue
                if item_date < today:
                    completed_rows.append(item)

        measured_by_date = {
            str(item.get("date")): item
            for item in completed_rows
            if isinstance(item, dict) and item.get("date")
        }
        rows: list[dict[str, Any]] = []
        # Only measured completed dates can become result rows. Stored future
        # plans remain pending and are intentionally absent until measurement
        # maturity. Measured-only dates are retained as non-scored evidence.
        for date_key in sorted(measured_by_date, reverse=True)[:31]:
            plan = self._plans.get(date_key)
            measured = measured_by_date[date_key]
            measured_solar = self._num(measured.get("solar_energy_kwh"))
            expected_solar = self._num(plan.get("expected_solar_kwh")) if plan else 0.0
            delta = measured_solar - expected_solar if plan and expected_solar > 0 else None
            accuracy = None
            signed_error_percent = None
            if plan and expected_solar > 0 and measured_solar >= 0:
                accuracy = max(
                    0.0,
                    min(
                        100.0,
                        (1.0 - abs(measured_solar - expected_solar) / max(measured_solar, expected_solar, 0.001)) * 100.0,
                    ),
                )
                signed_error_percent = ((expected_solar - measured_solar) / expected_solar) * 100.0
            weather = str((plan or {}).get("weather_summary") or "Unknown weather")
            plan_confidence = self._num((plan or {}).get("confidence_percent")) if plan else None
            evidence_weight = max(0.0, min(1.0, (plan_confidence or 0.0) / 100.0)) if plan else 0.0
            if accuracy is None:
                reason = "No genuine pre-day plan is available for this date."
                lesson = "Zeus needs a stored pre-day plan before it can learn from this day."
                evidence_level = "Unavailable"
            elif accuracy >= 95:
                reason = "Forecast and measured solar were closely aligned."
                lesson = "Observed comparison: no material forecast error. Zeus will combine this with matching completed days before generalizing."
                evidence_level = "Strong" if (plan_confidence or 0.0) >= 70.0 else "Moderate" if (plan_confidence or 0.0) >= 40.0 else "Weak"
            elif delta is not None and delta > 0:
                reason = "Measured production exceeded the stored pre-day estimate."
                if (plan_confidence or 0.0) < 40.0:
                    lesson = "Observed under-forecast, but the original plan confidence was low. Zeus retains this as weak evidence and will not generalize from it alone."
                    evidence_level = "Weak"
                else:
                    lesson = "Observed under-forecast. Zeus will wait for enough comparable completed days before treating this as a reusable adjustment."
                    evidence_level = "Strong" if (plan_confidence or 0.0) >= 70.0 else "Moderate"
            else:
                reason = "Measured production finished below the stored pre-day estimate."
                if (plan_confidence or 0.0) < 40.0:
                    lesson = "Observed over-forecast, but the original plan confidence was low. Zeus retains this as weak evidence and will not generalize from it alone."
                    evidence_level = "Weak"
                else:
                    lesson = "Observed over-forecast. Zeus will wait for enough comparable completed days before treating this as a reusable adjustment."
                    evidence_level = "Strong" if (plan_confidence or 0.0) >= 70.0 else "Moderate"
            rows.append({
                "date": date_key,
                "plan": plan,
                "measured_solar_kwh": round(measured_solar, 3),
                "solar_difference_kwh": round(delta, 3) if delta is not None else None,
                "solar_accuracy_percent": round(accuracy, 1) if accuracy is not None else None,
                "signed_error_percent": round(signed_error_percent, 2) if signed_error_percent is not None else None,
                "reason_summary": reason,
                "learning_summary": lesson,
                "weather_summary": weather,
                "plan_confidence_percent": round(plan_confidence, 1) if plan_confidence is not None else None,
                "learning_evidence_weight": round(evidence_weight, 3),
                "learning_evidence_level": evidence_level,
                "reusable_learning": False,
            })
        return rows

    def _weather_group(self, weather: str) -> str:
        """Return a stable broad weather group for advisory pattern learning."""
        text = str(weather or "").strip().lower()
        if any(token in text for token in ("sun", "clear", "fair")):
            return "Sunny"
        if any(token in text for token in ("cloud", "overcast", "rain", "shower", "storm", "snow", "fog")):
            return "Cloudy"
        return "Other"

    def _pattern_summary(self, valid: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Summarize context only when comparable evidence is mature enough."""
        groups: dict[str, list[dict[str, Any]]] = {
            "Sunny": [],
            "Cloudy": [],
            "Weekday": [],
            "Weekend": [],
        }
        for row in valid:
            weather_group = self._weather_group(str(row.get("weather_summary") or ""))
            if weather_group in ("Sunny", "Cloudy"):
                groups[weather_group].append(row)
            try:
                weekday = datetime.fromisoformat(str(row.get("date"))).weekday()
                groups["Weekend" if weekday >= 5 else "Weekday"].append(row)
            except (TypeError, ValueError):
                pass

        result: list[dict[str, Any]] = []
        for label, rows in groups.items():
            count = len(rows)
            weights = [max(0.0, min(1.0, self._num(row.get("learning_evidence_weight")))) for row in rows]
            effective_evidence = sum(weights)
            qualified = count >= 3 and effective_evidence >= 1.5
            if not count:
                result.append({
                    "label": label, "status": "Collecting", "comparison_count": 0,
                    "effective_evidence": 0.0, "average_accuracy_percent": None,
                    "average_bias_percent": None, "bias_direction": "Collecting",
                    "reusable_learning": False,
                })
                continue
            weight_sum = max(effective_evidence, 0.001)
            avg_accuracy = sum(self._num(row.get("solar_accuracy_percent")) * w for row, w in zip(rows, weights)) / weight_sum
            avg_bias = sum(self._num(row.get("signed_error_percent")) * w for row, w in zip(rows, weights)) / weight_sum
            if not qualified:
                direction = "Collecting"
            elif abs(avg_bias) < 5.0:
                direction = "Balanced"
            elif avg_bias > 0:
                direction = "Over-forecasting"
            else:
                direction = "Under-forecasting"
            result.append({
                "label": label,
                "status": "Ready" if qualified else "Collecting",
                "comparison_count": count,
                "effective_evidence": round(effective_evidence, 2),
                "average_accuracy_percent": round(avg_accuracy, 1),
                "average_bias_percent": round(avg_bias, 1),
                "bias_direction": direction,
                "reusable_learning": qualified,
            })
        return result

    def _change_since_yesterday(self, valid: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Describe the latest change without pretending that one day is a trend."""
        if len(valid) < 2:
            return None
        latest, previous = valid[0], valid[1]
        latest_accuracy = self._num(latest.get("solar_accuracy_percent"))
        previous_accuracy = self._num(previous.get("solar_accuracy_percent"))
        delta = latest_accuracy - previous_accuracy
        latest_bias = self._num(latest.get("signed_error_percent"))
        if abs(delta) < 1.0:
            headline = "Accuracy was stable"
        elif delta > 0:
            headline = "Accuracy improved"
        else:
            headline = "Accuracy decreased"
        if abs(latest_bias) < 2.0:
            detail = "The latest forecast was closely balanced with measured solar."
        elif latest_bias > 0:
            detail = "The latest plan overestimated measured solar."
        else:
            detail = "The latest plan underestimated measured solar."
        return {
            "latest_date": latest.get("date"),
            "previous_date": previous.get("date"),
            "headline": headline,
            "accuracy_change_points": round(delta, 1),
            "latest_accuracy_percent": round(latest_accuracy, 1),
            "previous_accuracy_percent": round(previous_accuracy, 1),
            "detail": detail,
        }

    def _learning_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Build confidence-weighted learning without generalizing from single days."""
        valid = [row for row in rows if row.get("solar_accuracy_percent") is not None and row.get("plan")]
        count = len(valid)
        weights = [max(0.0, min(1.0, self._num(row.get("learning_evidence_weight")))) for row in valid]
        effective_evidence = sum(weights)
        weight_sum = max(effective_evidence, 0.001)
        avg_bias = sum(self._num(row.get("signed_error_percent")) * w for row, w in zip(valid, weights)) / weight_sum if count else 0.0
        avg_accuracy = sum(self._num(row.get("solar_accuracy_percent")) * w for row, w in zip(valid, weights)) / weight_sum if count else 0.0
        accuracies = [self._num(row.get("solar_accuracy_percent")) for row in valid]
        spread = (max(accuracies) - min(accuracies)) if count > 1 else 0.0
        over_weight = sum(w for row, w in zip(valid, weights) if self._num(row.get("signed_error_percent")) > 5.0)
        under_weight = sum(w for row, w in zip(valid, weights) if self._num(row.get("signed_error_percent")) < -5.0)
        directional_weight = max(over_weight, under_weight)
        direction_consistency = directional_weight / weight_sum if count else 0.0
        reusable = count >= 5 and effective_evidence >= 3.0 and direction_consistency >= 0.65

        if count == 0:
            bias_direction = "Collecting"
        elif abs(avg_bias) < 5.0:
            bias_direction = "Balanced"
        elif avg_bias > 0:
            bias_direction = "Over-forecasting"
        else:
            bias_direction = "Under-forecasting"

        correction = 0.0
        if reusable and abs(avg_bias) >= 5.0:
            correction = (-1.0 if avg_bias > 0 else 1.0) * min(15.0, abs(avg_bias))

        sample_factor = min(45.0, effective_evidence * 10.0)
        accuracy_factor = min(30.0, avg_accuracy * 0.30) if count else 0.0
        consistency_factor = min(20.0, direction_consistency * 20.0) if count > 1 else 0.0
        spread_penalty = min(15.0, spread * 0.15) if count > 1 else 0.0
        confidence = round(max(0.0, min(100.0, sample_factor + accuracy_factor + consistency_factor - spread_penalty)), 1)
        if not reusable:
            confidence = min(confidence, 59.0)
            confidence_level = "Early learning" if effective_evidence < 2.0 else "Moderate evidence"
        elif confidence >= 75.0:
            confidence_level = "High"
        else:
            confidence_level = "Moderate"

        latest = valid[0] if valid else None
        factors = [
            {"label": "Completed comparisons", "value": count, "status": "ready" if count >= 5 else "learning"},
            {"label": "Effective weighted evidence", "value": round(effective_evidence, 1), "status": "ready" if effective_evidence >= 3 else "learning"},
            {"label": "Average solar accuracy", "value": round(avg_accuracy, 1) if count else None, "unit": "%"},
            {"label": "Direction consistency", "value": round(direction_consistency * 100.0, 1) if count else None, "unit": "%"},
        ]
        return {
            "status": "Ready" if reusable else "Collecting" if not count else "Learning",
            "comparison_count": count,
            "effective_evidence": round(effective_evidence, 2),
            "average_accuracy_percent": round(avg_accuracy, 1) if count else None,
            "average_bias_percent": round(avg_bias, 1) if count else None,
            "bias_direction": bias_direction,
            "direction_consistency_percent": round(direction_consistency * 100.0, 1) if count else None,
            "recommended_forecast_correction_percent": round(correction, 1),
            "confidence_percent": confidence,
            "confidence_level": confidence_level,
            "reusable_learning_ready": reusable,
            "minimum_comparisons_for_reuse": 5,
            "minimum_effective_evidence_for_reuse": 3.0,
            "minimum_direction_consistency_percent": 65.0,
            "factors": factors,
            "patterns": self._pattern_summary(valid),
            "change_since_yesterday": self._change_since_yesterday(valid),
            "latest_lesson": latest,
            "recommendation_only": True,
            "automatic_correction_applied": False,
        }

    def summary(self) -> dict[str, Any]:
        """Return the complete planning payload for internal Zeus consumers.

        This intentionally remains rich.  It is used inside Zeus by planning,
        insight and optimization code, but it is no longer written wholesale
        into Home Assistant state attributes because Recorder caps attributes
        at 16 KiB.
        """
        rows = self._completed_results()
        learning = self._learning_summary(rows)
        return {
            "status": "Ready" if self._plans else "Collecting",
            "stored_plan_count": len(self._plans),
            "last_saved_at": self._last_saved_at,
            "next_plan_date": (datetime.now().astimezone().date() + timedelta(days=1)).isoformat(),
            "plans": [self._plans[k] for k in sorted(self._plans, reverse=True)[:14]],
            "results": rows,
            "learning": learning,
            "recommendation_only": True,
            "recorder_safe": False,
        }

    def recorder_summary(self) -> dict[str, Any]:
        """Return a compact HA-entity payload that stays below Recorder's limit.

        The dashboard only needs recent result rows plus the learning summary.
        Full historical planning data remains available internally via
        :meth:`summary` and in the PlanningEngine store.
        """
        full = self.summary()
        learning = dict(full.get("learning") or {})
        # Keep the latest lesson and compact evidence, but avoid duplicating a
        # month of nested plans/results in Home Assistant state attributes.
        compact_results = list(full.get("results") or [])[:7]
        return {
            "status": full.get("status"),
            "stored_plan_count": full.get("stored_plan_count", 0),
            "last_saved_at": full.get("last_saved_at"),
            "next_plan_date": full.get("next_plan_date"),
            "results": compact_results,
            "learning": learning,
            "recommendation_only": True,
            "recorder_safe": True,
            "attribute_payload": "compact",
            "full_history_retained_internally": True,
        }

