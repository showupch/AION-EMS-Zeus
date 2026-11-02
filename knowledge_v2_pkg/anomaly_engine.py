"""Explainable anomaly intelligence for AION EMS Zeus v12.5.

Compares today's measured daily values with the learned Home Profile. The
engine is observation-only, recorder-safe, and does not create a polling loop.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.util import dt as dt_util
from typing import Any


class AnomalyIntelligenceEngine:
    """Identify meaningful deviations from the home's measured profile."""

    VERSION = "1.1-alpha.3"
    METRICS = {
        "solar_energy_kwh": ("Solar production", "solar_profile"),
        "house_energy_kwh": ("Home demand", "household_profile"),
        "grid_import_energy_kwh": ("Grid import", ("grid_profile", "import")),
        "grid_export_energy_kwh": ("Grid export", ("grid_profile", "export")),
        "battery_charge_energy_kwh": ("Battery charging", ("battery_profile", "charge")),
        "battery_discharge_energy_kwh": ("Battery support", ("battery_profile", "discharge")),
    }
    # A percentage can look dramatic when the underlying energy is tiny.  An
    # intraday observation must clear both the learned range and a meaningful
    # absolute delta before Zeus surfaces it.
    MIN_INTRADAY_DELTA_KWH = {
        "solar_energy_kwh": 0.25,
        "house_energy_kwh": 0.25,
        "grid_import_energy_kwh": 0.10,
        "grid_export_energy_kwh": 0.10,
        "battery_charge_energy_kwh": 0.20,
        "battery_discharge_energy_kwh": 0.20,
    }

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Learning",
            "version": self.VERSION,
            "observation_count": 0,
            "observations": [],
            "summary": "Zeus is learning the home's normal measured ranges.",
        }

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _profile_value(profile: dict[str, Any], path: str | tuple[str, str]) -> dict[str, Any]:
        if isinstance(path, tuple):
            return dict((profile.get(path[0]) or {}).get(path[1]) or {})
        return dict(profile.get(path) or {})


    @staticmethod
    def _intraday_ranges(core: Any) -> tuple[dict[str, dict[str, float]], dict[str, float], int] | None:
        """Build same-time-of-day ranges from canonical hourly Recorder evidence.

        Only completed local hours are used. This prevents a partial current day
        from being compared with completed 24-hour totals. If exact hourly
        evidence is unavailable for a metric, that metric is withheld rather
        than estimated from full-day history.
        """
        analytics = getattr(core, "analytics", None)
        rows = list(getattr(analytics, "_ha_consumption_hourly", []) or [])
        if not rows:
            return None
        now = dt_util.now()
        today = now.date().isoformat()
        cutoff_hour = now.hour  # rows starting before this hour are mature
        fields = tuple(AnomalyIntelligenceEngine.METRICS)
        per_day: dict[str, dict[str, float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = str(row.get("start") or "")
            try:
                parsed = datetime.fromisoformat(stamp)
                local = dt_util.as_local(parsed) if parsed.tzinfo else parsed
            except (TypeError, ValueError):
                continue
            day = local.date().isoformat()
            if local.hour >= cutoff_hour:
                continue
            target = per_day.setdefault(day, {})
            for field in fields:
                value = AnomalyIntelligenceEngine._num(row.get(field))
                if value is not None and value >= 0:
                    target[field] = target.get(field, 0.0) + value
            # Battery anomaly authority is battery support to the home, matching
            # Day Status Summary / Canonical Finance, not raw battery discharge.
            # Build the historical same-time equivalent from the same measured
            # energy-balance boundary for each mature Recorder hour.
            house = AnomalyIntelligenceEngine._num(row.get("house_energy_kwh"))
            imported = AnomalyIntelligenceEngine._num(row.get("grid_import_energy_kwh"))
            discharged = AnomalyIntelligenceEngine._num(row.get("battery_discharge_energy_kwh"))
            if house is not None and imported is not None and discharged is not None:
                local_home = max(house - imported, 0.0)
                support = min(max(discharged, 0.0), local_home)
                # Replace raw discharge accumulated above with equivalent support.
                target["battery_discharge_energy_kwh"] = target.get("_battery_support_accum", 0.0) + support
                target["_battery_support_accum"] = target["battery_discharge_energy_kwh"]
        # Today's value must come from the same canonical daily authorities used
        # everywhere else in Zeus (Day Status, Finance and Intelligence Memory).
        # Recorder hourly rows are used only to learn the historical same-time
        # comparison range. Reconstructing today's energy from hourly rows can
        # diverge from a mapped daily-reset authority and create false anomalies.
        lake = getattr(getattr(core, "data_lake", None), "data", {}) or {}
        canonical_today = dict((lake.get("daily_summaries", {}) or {}).get(today, {}) or {})
        # Use the same live *_today mapping overlay as Analytics/Day Status.  The
        # DataLake snapshot can lag the visible daily-reset meter during the day.
        overlay_getter = getattr(analytics, "_mapped_today_overlay", None)
        if callable(overlay_getter):
            try:
                canonical_today.update(dict(overlay_getter() or {}))
            except Exception:
                pass
        actual: dict[str, float] = {}
        for field in fields:
            value = AnomalyIntelligenceEngine._num(canonical_today.get(field))
            if value is not None and value >= 0:
                actual[field] = value
        # Day Status Summary gets Battery support from Canonical Finance. Use the
        # exact same authority here so the two Zeus pages can never disagree.
        finance = getattr(core, "finance", None)
        if finance is not None and hasattr(finance, "summary"):
            try:
                finance_summary = finance.summary() or {}
                support = AnomalyIntelligenceEngine._num(finance_summary.get("battery_support_to_home_kwh"))
                if support is not None and support >= 0:
                    actual["battery_discharge_energy_kwh"] = support
            except Exception:
                # If Canonical Finance is unavailable, withhold the battery
                # observation rather than silently reverting to another authority.
                actual.pop("battery_discharge_energy_kwh", None)
        historical = [vals for day, vals in per_day.items() if day != today]
        if len(historical) < 7:
            return None
        ranges: dict[str, dict[str, float]] = {}
        for field in fields:
            values = sorted(v[field] for v in historical if field in v)
            if len(values) < 7:
                continue
            n = len(values)
            low = values[max(0, int((n - 1) * 0.20))]
            high = values[min(n - 1, int((n - 1) * 0.80))]
            ranges[field] = {
                "typical_low": low,
                "typical_high": high,
                "average": sum(values) / n,
            }
        return ranges, actual, cutoff_hour

    @staticmethod
    def _severity(deviation_percent: float) -> str:
        absolute = abs(deviation_percent)
        if absolute >= 35:
            return "Significant"
        if absolute >= 20:
            return "Notice"
        return "Information"

    @staticmethod
    def _classification(metric: str, deviation_percent: float) -> str:
        """Classify a deviation without pretending every deviation is a problem."""
        if metric == "solar_energy_kwh" and deviation_percent > 0:
            return "Positive"
        if metric == "grid_export_energy_kwh" and deviation_percent > 0:
            return "Positive"
        if metric == "grid_import_energy_kwh" and deviation_percent > 0:
            return "Attention"
        return "Observation"

    @staticmethod
    def _correlate(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Explain obvious same-day relationships without inventing causality."""
        by_metric = {str(item.get("metric")): item for item in observations}
        solar = by_metric.get("solar_energy_kwh")
        export = by_metric.get("grid_export_energy_kwh")
        discharge = by_metric.get("battery_discharge_energy_kwh")
        groups: list[dict[str, Any]] = []
        if (solar and float(solar.get("deviation_percent") or 0) > 0
                and export and float(export.get("deviation_percent") or 0) > 0):
            related = ["solar_energy_kwh", "grid_export_energy_kwh"]
            detail = (
                f"Solar production is {abs(float(solar.get('deviation_percent') or 0)):.1f}% above its learned average "
                f"and grid export is {abs(float(export.get('deviation_percent') or 0)):.1f}% above its learned average."
            )
            if discharge and float(discharge.get("deviation_percent") or 0) < 0:
                related.append("battery_discharge_energy_kwh")
                detail += (
                    f" Battery discharge is {abs(float(discharge.get('deviation_percent') or 0)):.1f}% below its learned average "
                    "during the same measured day."
                )
            groups.append({
                "id": "high_solar_day",
                "classification": "Positive",
                "title": "High solar day",
                "detail": detail,
                "related_metrics": related,
                "evidence": "Correlated measured daily deviations; relationship shown without claiming unmeasured causality.",
            })
        return groups

    def refresh(self) -> dict[str, Any]:
        profile_engine = getattr(self.core, "home_profile", None)
        profile = profile_engine.summary() if profile_engine and hasattr(profile_engine, "summary") else {}
        memory_engine = getattr(self.core, "intelligence_memory", None)
        memory = memory_engine.summary() if memory_engine and hasattr(memory_engine, "summary") else {}
        days = [item for item in (memory.get("recent_days") or []) if isinstance(item, dict)]
        today = days[-1] if days else {}
        learning_days = int(profile.get("learning_days") or 0)
        observations: list[dict[str, Any]] = []
        today_date = dt_util.now().date().isoformat()
        is_current_day = str(today.get("date") or "") == today_date
        intraday = self._intraday_ranges(self.core) if is_current_day else None
        intraday_ranges, intraday_actual, cutoff_hour = intraday if intraday else ({}, {}, 0)

        if learning_days >= 7 and today:
            for field, (label, path) in self.METRICS.items():
                if is_current_day:
                    # Current-day anomaly claims require same-time Recorder evidence.
                    # Never compare an immature day with completed daily totals.
                    if field not in intraday_ranges or field not in intraday_actual:
                        continue
                    actual = self._num(intraday_actual.get(field))
                    expected = dict(intraday_ranges.get(field) or {})
                else:
                    actual = self._num(today.get(field))
                    expected = self._profile_value(profile, path)
                low = self._num(expected.get("typical_low"))
                high = self._num(expected.get("typical_high"))
                average = self._num(expected.get("average"))
                if actual is None or low is None or high is None or average is None:
                    continue
                if low <= actual <= high:
                    continue
                if is_current_day:
                    # Solar is not mature while the sun is below the horizon.
                    # A zero/near-zero PV total before daylight is normal, not an
                    # anomaly.  Once daylight begins, the same-time evidence can
                    # become eligible normally.
                    if field == "solar_energy_kwh":
                        sun = getattr(self.core, "hass", None)
                        sun_state = sun.states.get("sun.sun") if sun is not None else None
                        if sun_state is not None and str(sun_state.state) == "below_horizon":
                            continue
                    nearest = low if actual < low else high
                    absolute_delta = abs(actual - nearest)
                    minimum_delta = float(self.MIN_INTRADAY_DELTA_KWH.get(field, 0.1))
                    if absolute_delta < minimum_delta or max(abs(average), abs(actual)) < minimum_delta:
                        continue
                baseline = max(abs(average), 0.1)
                deviation = round((actual - average) / baseline * 100.0, 1)
                direction = "above" if deviation > 0 else "below"
                observations.append({
                    "id": f"{field}_{today.get('date', 'today')}",
                    "metric": field,
                    "title": f"{label} is {direction} the learned range",
                    "detail": (
                        f"Measured {actual:.1f} kWh through {cutoff_hour:02d}:00 versus a same-time typical range of {low:.1f}–{high:.1f} kWh."
                        if is_current_day else
                        f"Measured {actual:.1f} kWh versus a typical range of {low:.1f}–{high:.1f} kWh."
                    ),
                    "actual": round(actual, 2),
                    "typical_low": round(low, 2),
                    "typical_high": round(high, 2),
                    "deviation_percent": deviation,
                    "severity": self._severity(deviation),
                    "classification": self._classification(field, deviation),
                    "category": "Home Observation",
                    "date": today.get("date"),
                    "source": "Measured daily history and Home Profile",
                })

        observations.sort(key=lambda item: abs(float(item.get("deviation_percent") or 0)), reverse=True)
        observations = observations[:8]
        correlations = self._correlate(observations)
        correlated_metrics = {metric for group in correlations for metric in (group.get("related_metrics") or [])}
        independent = [item for item in observations if item.get("metric") not in correlated_metrics]
        attention_count = sum(1 for item in observations if item.get("classification") == "Attention")
        positive_count = sum(1 for item in observations if item.get("classification") == "Positive")
        if observations:
            status = "Attention" if attention_count else ("Positive" if (positive_count or correlations) else "Observation")
            if status == "Attention":
                attention_item = next(
                    (item for item in independent if item.get("classification") == "Attention"),
                    next((item for item in observations if item.get("classification") == "Attention"), None),
                )
                headline = attention_item.get("title") if attention_item else "Measured condition requires review."
            elif correlations:
                headline = correlations[0]["title"]
            else:
                headline = observations[0]["title"]
            summary = f"Zeus identified {len(observations)} measured deviation(s); {attention_count} require attention."
        elif learning_days >= 7:
            headline = "Measured performance is within the learned ranges."
            status = "Normal"
            summary = "No meaningful daily anomalies are currently detected."
        else:
            headline = "More measured history is required."
            status = "Learning"
            summary = f"Zeus has {learning_days} measured day(s); at least 7 are required."

        self.last = {
            "status": status,
            "version": self.VERSION,
            "headline": headline,
            "learning_days": learning_days,
            "observation_count": len(observations),
            "observations": observations,
            "correlations": correlations,
            "independent_observations": independent,
            "attention_count": attention_count if observations else 0,
            "positive_count": positive_count if observations else 0,
            "maturity_mode": "same_time_recorder" if is_current_day else "completed_day",
            "mature_through_hour": cutoff_hour if is_current_day and intraday else None,
            "highest_severity": observations[0].get("severity") if observations else None,
            "summary": summary,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "recorder_safe": True,
            "safety": "Observation and recommendation context only. No device control.",
        }
        try:
            self.event_bus.publish("AnomalyIntelligenceUpdated", "AnomalyIntelligenceEngine", {
                "observation_count": len(observations),
                "status": status,
            })
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
