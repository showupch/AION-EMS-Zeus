"""Executive Briefings 3.0 for AION EMS Zeus v12.5.

Creates compact, evidence-backed morning, evening and weekly briefings from
trusted measured data and existing intelligence engines. Recommendation only;
never calls device services.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any


class ExecutiveBriefingEngine:
    """Build recorder-safe executive briefings without inventing values."""

    VERSION = "3.0-alpha.5"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "summary": "Zeus is preparing the first executive briefing.",
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
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _first_number(source: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = ExecutiveBriefingEngine._number(source.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _fmt(value: float | None, unit: str = "kWh") -> str:
        return "Unavailable" if value is None else f"{value:.1f} {unit}".strip()

    def _daily_summaries(self) -> dict[str, dict[str, Any]]:
        raw = getattr(self.core.data_lake, "data", {}).get("daily_summaries", {}) or {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def _canonical_today(self) -> dict[str, float]:
        """Return canonical mapped daily-reset energy values for the live day.

        These are value reads from Zeus's saved Energy Mapping authority, not
        independent calculations. Historical Analytics and Data Consistency use
        the same mapped Today entities. Missing mappings are simply omitted so
        callers can fall back to the canonical Analytics period or Data Lake.
        """
        mapping = self._summary(getattr(self.core, "energy_mapping", None))
        mapped = mapping.get("mapped", {}) if isinstance(mapping.get("mapped"), dict) else {}
        fields = {
            "solar_energy_kwh": "solar_energy_today",
            "house_energy_kwh": "house_energy_today",
            "grid_import_energy_kwh": "grid_import_energy_today",
            "grid_export_energy_kwh": "grid_export_energy_today",
        }
        out: dict[str, float] = {}
        for key, field in fields.items():
            item = mapped.get(field)
            value = self._number(item.get("value")) if isinstance(item, dict) else None
            if value is not None:
                out[key] = max(value, 0.0)
        return out

    def _week_metrics(self, daily: dict[str, dict[str, Any]], today: date) -> dict[str, Any]:
        current_dates = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        prior_dates = [(today - timedelta(days=i)).isoformat() for i in range(7, 14)]

        def values(keys: tuple[str, ...], dates: list[str]) -> list[float]:
            result: list[float] = []
            for day in dates:
                item = daily.get(day, {})
                value = self._first_number(item, *keys)
                if value is not None:
                    result.append(value)
            return result

        def metric(keys: tuple[str, ...]) -> dict[str, Any]:
            current = values(keys, current_dates)
            prior = values(keys, prior_dates)
            current_total = sum(current) if current else None
            prior_total = sum(prior) if prior else None
            change = None
            if current_total is not None and prior_total not in (None, 0):
                change = ((current_total - prior_total) / prior_total) * 100.0
            return {
                "total": round(current_total, 2) if current_total is not None else None,
                "previous_total": round(prior_total, 2) if prior_total is not None else None,
                "change_percent": round(change, 1) if change is not None else None,
                "measured_days": len(current),
            }

        return {
            "solar": metric(("solar_energy_kwh", "solar_today_kwh")),
            "home": metric(("house_energy_kwh", "home_energy_kwh")),
            "import": metric(("grid_import_energy_kwh", "import_energy_kwh")),
            "export": metric(("grid_export_energy_kwh", "export_energy_kwh")),
        }

    @staticmethod
    def _anomaly_line(anomaly: dict[str, Any]) -> str | None:
        observations = anomaly.get("observations") or anomaly.get("anomalies") or []
        if not isinstance(observations, list):
            return None
        for item in observations:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or item.get("level") or "").lower()
            if severity in {"significant", "warning", "persistent", "notice"}:
                return str(item.get("message") or item.get("summary") or item.get("title") or "").strip() or None
        return None

    @staticmethod
    def _advisor_action(advisor: dict[str, Any], decision: dict[str, Any]) -> str:
        return str(
            decision.get("recommendation")
            or decision.get("action")
            or advisor.get("headline")
            or advisor.get("recommendation")
            or "No high-value action is available right now."
        )

    def refresh(self) -> dict[str, Any]:
        now = datetime.now().astimezone()
        period = "morning" if now.hour < 12 else "evening" if now.hour >= 17 else "day"
        daily = self._daily_summaries()
        today_data = daily.get(now.date().isoformat(), {})
        # Current-day authority must match Historical Analytics / Energy Statistics.
        # Analytics already overlays the saved mapped daily-reset entities over the
        # partial Data Lake row, so Executive Briefing consumes that canonical
        # period instead of independently reading the lake's in-progress row.
        analytics = self._summary(self.core.analytics)
        analytics_periods = analytics.get("periods", {}) if isinstance(analytics.get("periods"), dict) else {}
        analytics_today = analytics_periods.get("today", {}) if isinstance(analytics_periods.get("today"), dict) else {}
        mapped_today = self._canonical_today()

        forecast = self._summary(self.core.forecast)
        flow = self._summary(self.core.energy_flow)
        advisor = self._summary(self.core.ai_advisor)
        decision = self._summary(self.core.decision_engine)
        learning = self._summary(self.core.learning_intelligence_v2)
        finance = self._summary(self.core.finance)
        prediction = self._summary(self.core.prediction_accuracy)
        memory = self._summary(self.core.intelligence_memory)
        anomaly = self._summary(self.core.anomaly_intelligence)
        adaptive = self._summary(self.core.adaptive_advisor)
        outcomes = self._summary(self.core.opportunity_learning)

        generated = self._first_number(mapped_today, "solar_energy_kwh")
        consumed = self._first_number(mapped_today, "house_energy_kwh")
        exported = self._first_number(mapped_today, "grid_export_energy_kwh")
        imported = self._first_number(mapped_today, "grid_import_energy_kwh")
        # Canonical Analytics Today is the first fallback when a dedicated
        # daily-reset mapping is absent. Data Lake is compatibility-only.
        if generated is None:
            generated = self._first_number(analytics_today, "solar_energy_kwh", "solar_today_kwh")
        if consumed is None:
            consumed = self._first_number(analytics_today, "house_energy_kwh", "home_energy_kwh")
        if exported is None:
            exported = self._first_number(analytics_today, "grid_export_energy_kwh", "export_energy_kwh")
        if imported is None:
            imported = self._first_number(analytics_today, "grid_import_energy_kwh", "import_energy_kwh")
        if generated is None:
            generated = self._first_number(today_data, "solar_energy_kwh", "solar_today_kwh")
        if consumed is None:
            consumed = self._first_number(today_data, "house_energy_kwh", "home_energy_kwh")
        if exported is None:
            exported = self._first_number(today_data, "grid_export_energy_kwh", "export_energy_kwh")
        if imported is None:
            imported = self._first_number(today_data, "grid_import_energy_kwh", "import_energy_kwh")
        battery_soc = self._first_number(flow, "battery_soc_percent", "battery_soc")
        forecast_today = self._first_number(
            forecast,
            "expected_solar_today_kwh",
            "forecast_today_kwh",
            "expected_solar_next_24h_kwh",
        )
        savings = self._first_number(finance, "savings_today", "saved_today")
        overall_accuracy = self._first_number(prediction, "overall_accuracy_percent", "accuracy_percent")
        recommendation = self._advisor_action(advisor, decision)
        anomaly_line = self._anomaly_line(anomaly)
        week = self._week_metrics(daily, now.date())

        resolved = outcomes.get("resolved_count")
        completed = outcomes.get("completed_count")
        if resolved is None:
            recent = outcomes.get("recent_outcomes") or []
            if isinstance(recent, list):
                resolved = sum(1 for x in recent if isinstance(x, dict) and x.get("status") in {"Completed", "Ignored", "Expired"})
                completed = sum(1 for x in recent if isinstance(x, dict) and x.get("status") == "Completed")

        if period == "morning":
            headline = "Good morning. Zeus has prepared today's energy outlook."
            parts = []
            if forecast_today is not None:
                parts.append(f"Solar production is forecast at {forecast_today:.1f} kWh")
            if battery_soc is not None:
                parts.append(f"battery SOC is {battery_soc:.0f}%")
            narrative = ". ".join(parts) + ("." if parts else "")
            if not narrative:
                narrative = "Zeus is waiting for enough trusted forecast and battery data."
        elif period == "evening":
            headline = "Today's measured energy summary is ready."
            parts = []
            if generated is not None:
                parts.append(f"generated {generated:.1f} kWh")
            if consumed is not None:
                parts.append(f"consumed {consumed:.1f} kWh")
            if exported is not None:
                parts.append(f"exported {exported:.1f} kWh")
            if imported is not None:
                parts.append(f"imported {imported:.1f} kWh")
            narrative = (", ".join(parts).capitalize() + ".") if parts else "Measured daily totals are not available yet."
        else:
            headline = "Your live energy briefing is ready."
            parts = []
            if generated is not None:
                parts.append(f"{generated:.1f} kWh solar")
            if consumed is not None:
                parts.append(f"{consumed:.1f} kWh home consumption")
            narrative = "So far today Zeus has measured " + " and ".join(parts) + "." if parts else "Zeus is waiting for trusted daily measurements."

        weekly_lines: list[str] = []
        for label, key in (("Solar", "solar"), ("Grid import", "import"), ("Grid export", "export")):
            metric = week[key]
            change = metric.get("change_percent")
            if change is not None and metric.get("measured_days", 0) >= 3:
                direction = "higher" if change > 0 else "lower"
                weekly_lines.append(f"{label} is {abs(change):.1f}% {direction} than the preceding seven-day period.")

        evidence = []
        if overall_accuracy is not None:
            evidence.append({"source": "Prediction Accuracy", "value": f"{overall_accuracy:.1f}%"})
        if learning.get("confidence_percent") is not None:
            evidence.append({"source": "Learning", "value": f"{learning.get('confidence_percent')}%"})
        if adaptive.get("established_preference_count") is not None:
            evidence.append({"source": "Adaptive Advisor", "value": f"{adaptive.get('established_preference_count')} learned categories"})
        if memory.get("record_count") is not None:
            evidence.append({"source": "Intelligence Memory", "value": f"{memory.get('record_count')} records"})

        self.last = {
            "status": "Ready",
            "version": self.VERSION,
            "period": period,
            "headline": headline,
            "narrative": narrative,
            "recommendation": recommendation,
            "important_observation": anomaly_line,
            "battery_soc_percent": round(battery_soc, 1) if battery_soc is not None else None,
            "forecast_today_kwh": round(forecast_today, 2) if forecast_today is not None else None,
            "generated_today_kwh": round(generated, 2) if generated is not None else None,
            "consumed_today_kwh": round(consumed, 2) if consumed is not None else None,
            "imported_today_kwh": round(imported, 2) if imported is not None else None,
            "exported_today_kwh": round(exported, 2) if exported is not None else None,
            "savings_today": round(savings, 2) if savings is not None else None,
            "prediction_accuracy_percent": round(overall_accuracy, 1) if overall_accuracy is not None else None,
            "recommendations_resolved": resolved,
            "recommendations_completed": completed,
            "learning_confidence_percent": learning.get("confidence_percent"),
            "similar_day_message": learning.get("similar_day_message"),
            "weekly_comparison": week,
            "weekly_highlights": weekly_lines[:4],
            "evidence": evidence[:6],
            "summary": f"{headline} {narrative}",
            "data_policy": "Measured values only; unavailable values are omitted.",
            "recorder_safe": True,
            "safety": "Recommendation only. No autonomous control.",
        }
        try:
            self.event_bus.publish(
                "ExecutiveBriefingUpdated",
                "ExecutiveBriefingEngine",
                {"period": period, "version": self.VERSION},
            )
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
