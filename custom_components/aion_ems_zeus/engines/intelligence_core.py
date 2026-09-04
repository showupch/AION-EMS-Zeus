"""AION EMS Zeus v10.4 intelligence core.

Read-only learning and efficiency engines. They derive compact summaries from
existing Zeus history and never mutate the registry, mappings, tariffs or data lake.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


class LearningEngineV2:
    """Long-term and seasonal learning from recorder-safe daily summaries."""

    def __init__(self, event_bus, data_lake) -> None:
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.last: dict[str, Any] = {"status": "Waiting", "summary": "Collecting long-term history."}

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 3) if values else None

    @staticmethod
    def _change(current: float | None, previous: float | None) -> float | None:
        if current is None or previous in (None, 0):
            return None
        return round((current - previous) / previous * 100, 1)

    @staticmethod
    def _season(month: int) -> str:
        return "Winter" if month in (12, 1, 2) else "Spring" if month in (3, 4, 5) else "Summer" if month in (6, 7, 8) else "Autumn"

    def refresh(self) -> dict[str, Any]:
        raw = self.data_lake.data.get("daily_summaries", {})
        rows: list[tuple[datetime, dict[str, float]]] = []
        keys = ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh")
        for date_key, source in sorted(raw.items()):
            try:
                dt = datetime.fromisoformat(str(date_key))
            except (TypeError, ValueError):
                continue
            row = {key: self._num(source.get(key)) for key in keys}
            row["self_consumption_percent"] = self._num(source.get("self_consumption_percent"))
            row["self_sufficiency_percent"] = self._num(source.get("self_sufficiency_percent"))
            rows.append((dt, row))

        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        months = ["January","February","March","April","May","June","July","August","September","October","November","December"]
        weekday_buckets: dict[int, list[dict[str, float]]] = defaultdict(list)
        month_buckets: dict[int, list[dict[str, float]]] = defaultdict(list)
        season_buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
        for dt, row in rows:
            weekday_buckets[dt.weekday()].append(row)
            month_buckets[dt.month].append(row)
            season_buckets[self._season(dt.month)].append(row)

        def profile(label: str, bucket: list[dict[str, float]]) -> dict[str, Any]:
            solar = self._avg([x["solar_energy_kwh"] for x in bucket])
            home = self._avg([x["house_energy_kwh"] for x in bucket])
            imported = self._avg([x["grid_import_energy_kwh"] for x in bucket])
            exported = self._avg([x["grid_export_energy_kwh"] for x in bucket])
            return {"label": label, "sample_days": len(bucket), "solar_kwh": solar, "home_kwh": home,
                    "import_kwh": imported, "export_kwh": exported,
                    "self_sufficiency_percent": round(max(0, min(100, ((home or 0)-(imported or 0))/(home or 1)*100)), 1) if home else None}

        weekday_profiles = [profile(names[i], weekday_buckets.get(i, [])) for i in range(7)]
        monthly_profiles = [profile(months[i-1], month_buckets[i]) for i in range(1,13) if month_buckets.get(i)]
        seasonal_profiles = [profile(x, season_buckets[x]) for x in ("Winter","Spring","Summer","Autumn") if season_buckets.get(x)]

        day_count = len(rows)
        confidence = min(100, round(day_count / 90 * 100))
        all_values = {key: [row[key] for _, row in rows] for key in keys}
        average_day = {"solar_kwh": self._avg(all_values["solar_energy_kwh"]), "home_kwh": self._avg(all_values["house_energy_kwh"]),
                       "grid_import_kwh": self._avg(all_values["grid_import_energy_kwh"]), "grid_export_kwh": self._avg(all_values["grid_export_energy_kwh"])}

        recent = rows[-30:]
        previous = rows[-60:-30]
        def period_avg(items, key): return self._avg([r[key] for _, r in items])
        trends = {key.replace("_energy_kwh", "_change_percent"): self._change(period_avg(recent,key), period_avg(previous,key)) for key in keys}

        highlights: dict[str, Any] = {}
        if rows:
            def point(title, selector, reverse=False):
                dt,row = sorted(rows, key=lambda x: selector(x[1]), reverse=reverse)[0]
                return {"title": title, "date": dt.date().isoformat(), "value_kwh": round(selector(row),3)}
            highlights = {
                "best_solar_day": point("Best solar day", lambda x:x["solar_energy_kwh"], True),
                "highest_consumption_day": point("Highest consumption day", lambda x:x["house_energy_kwh"], True),
                "lowest_grid_import_day": point("Lowest grid-import day", lambda x:x["grid_import_energy_kwh"]),
                "best_self_sufficiency_day": point("Best self-sufficiency day", lambda x:max(0,x["house_energy_kwh"]-x["grid_import_energy_kwh"]), True),
            }

        anomalies=[]
        if len(rows) >= 14:
            for key,label in (("house_energy_kwh","consumption"),("solar_energy_kwh","solar production"),("grid_import_energy_kwh","grid import")):
                vals=all_values[key]
                mean=sum(vals)/len(vals); variance=sum((v-mean)**2 for v in vals)/len(vals); sd=variance**0.5
                for dt,row in rows[-14:]:
                    if sd and abs(row[key]-mean) >= 2*sd:
                        anomalies.append({"date":dt.date().isoformat(),"metric":label,"value_kwh":round(row[key],3),"direction":"high" if row[key]>mean else "low"})
        anomalies=anomalies[-6:]

        valid_weekdays=[x for x in weekday_profiles if x["sample_days"]]
        best_solar=max(valid_weekdays,key=lambda x:x["solar_kwh"] or -1) if valid_weekdays else None
        highest_load=max(valid_weekdays,key=lambda x:x["home_kwh"] or -1) if valid_weekdays else None
        recommendations=[]
        if best_solar: recommendations.append(f"Prefer flexible loads on {best_solar['label']} when forecasts confirm solar surplus.")
        if trends.get("grid_import_change_percent") is not None and trends["grid_import_change_percent"] > 10: recommendations.append("Grid import is rising versus the previous 30 days; review new loads and scheduler windows.")
        if average_day["grid_export_kwh"] and average_day["grid_export_kwh"] > 1: recommendations.append("Regular export surplus is available; schedule flexible devices or preserve battery headroom around midday.")
        if not recommendations: recommendations.append("Continue collecting history to improve seasonal recommendations.")

        self.last = {
            "status": "Ready" if day_count else "Waiting", "generation": "long_term_learning_10_9", "history_days": day_count,
            "history_months": len(monthly_profiles), "confidence_percent": confidence,
            "confidence_label": "High" if confidence >= 75 else "Moderate" if confidence >= 40 else "Low",
            "average_day": average_day, "weekday_profiles": weekday_profiles, "monthly_profiles": monthly_profiles,
            "seasonal_profiles": seasonal_profiles, "seasonal_history_months": len(monthly_profiles), "trends_30_day": trends,
            "highlights": highlights, "recent_anomalies": anomalies, "recommendations": recommendations[:4],
            "best_solar_weekday": best_solar, "highest_load_weekday": highest_load,
            "summary": f"Learning from {day_count} days across {len(monthly_profiles)} months." if day_count else "Collecting the first long-term profile.",
            "method": "Daily summaries grouped by weekday, calendar month and meteorological season; recent 30 days are compared with the previous 30 days.",
            "safety": "Recommendation only. No device, battery or inverter control.", "recorder_safe": True,
        }
        self.event_bus.publish("LongTermLearningUpdated", "LearningEngineV2", {"history_days": day_count, "confidence": confidence, "anomalies": len(anomalies)})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class HomeEfficiencyEngine:
    """Calculate a transparent 0-100 home energy efficiency score."""

    def __init__(self, event_bus, analytics, energy_flow, data_quality) -> None:
        self.event_bus = event_bus
        self.analytics = analytics
        self.energy_flow = energy_flow
        self.data_quality = data_quality
        self.last: dict[str, Any] = {"status": "Waiting", "score": 0}

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value if value is not None else default)
        except (TypeError, ValueError):
            return default

    def refresh(self) -> dict[str, Any]:
        today = self.analytics.summary().get("periods", {}).get("today", {})
        solar = self._number(today.get("solar_energy_kwh"))
        home = self._number(today.get("house_energy_kwh"))
        imported = self._number(today.get("grid_import_energy_kwh"))
        exported = self._number(today.get("grid_export_energy_kwh"))
        self_consumption = today.get("self_consumption_percent")
        self_sufficiency = today.get("self_sufficiency_percent")
        self_consumption = self._number(self_consumption, max(0, min(100, (solar - exported) / solar * 100)) if solar else 0)
        self_sufficiency = self._number(self_sufficiency, max(0, min(100, (home - imported) / home * 100)) if home else 0)
        grid_dependency = max(0.0, min(100.0, imported / home * 100)) if home else 0.0
        solar_utilization = max(0.0, min(100.0, 100.0 - (exported / solar * 100))) if solar else 0.0
        quality = self._number(self.data_quality.summary().get("confidence_score"), 50)
        score = round(max(0, min(100, self_consumption * .3 + self_sufficiency * .35 + (100-grid_dependency) * .2 + quality * .15)))
        suggestions = []
        if solar and self_consumption < 70:
            suggestions.append("Move flexible loads into solar production hours.")
        if home and grid_dependency > 30:
            suggestions.append("Reduce optional demand during grid-import periods.")
        if quality < 75:
            suggestions.append("Review mappings to improve intelligence confidence.")
        if not suggestions:
            suggestions.append("Current energy use is well balanced.")
        self.last = {
            "status": "Ready" if self.analytics.summary().get("status") == "Ready" else "Waiting",
            "score": score,
            "grade": "Excellent" if score >= 90 else "Good" if score >= 75 else "Fair" if score >= 55 else "Needs attention",
            "self_consumption_percent": round(self_consumption, 1),
            "self_sufficiency_percent": round(self_sufficiency, 1),
            "solar_utilization_percent": round(solar_utilization, 1),
            "grid_dependency_percent": round(grid_dependency, 1),
            "data_confidence_percent": round(quality, 1),
            "suggestions": suggestions,
            "summary": f"Home efficiency is {score}%. {suggestions[0]}",
            "method": "weighted self-consumption, self-sufficiency, grid dependency and data confidence",
            "safety": "Read-only score and recommendations.",
        }
        self.event_bus.publish("HomeEfficiencyUpdated", "HomeEfficiencyEngine", {"score": score})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class PredictiveBatteryOptimizer:
    """48-hour forecast, tariff and scheduler-aware battery advisory engine.

    The engine is deliberately recommendation-only. It calculates a transparent
    strategy and projected state-of-charge timeline but never calls Home Assistant
    services, writes inverter settings, or changes the Zeus Registry.
    """

    DEFAULT_CAPACITY_KWH = 10.0
    DEFAULT_MAX_CHARGE_W = 5000.0
    DEFAULT_MAX_DISCHARGE_W = 5000.0
    DEFAULT_EFFICIENCY = 0.92

    def __init__(self, event_bus, forecast, energy_flow, analytics, learning, registry=None, scheduler=None) -> None:
        self.event_bus = event_bus
        self.forecast = forecast
        self.energy_flow = energy_flow
        self.analytics = analytics
        self.learning = learning
        self.registry = registry
        self.scheduler = scheduler
        self.last: dict[str, Any] = {"status": "Waiting", "strategy": "Collecting battery context"}

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value if value is not None else default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _battery_config(self) -> dict[str, Any]:
        devices = [] if self.registry is None else self.registry.data.get("devices", [])
        battery = next(
            (d for d in devices if d.get("enabled", True) and str(d.get("type") or "").lower() in {"battery", "home_battery", "storage"}),
            {},
        )
        home_settings = self.registry.data.get("home_settings", {}) if self.registry is not None else {}
        registered_profile = home_settings.get("battery_profile") if isinstance(home_settings.get("battery_profile"), dict) else {}
        profile_registered = bool(registered_profile.get("registered") is True)

        # Explicit registry battery device remains first priority. The new
        # canonical battery profile is an alternative explicit registration
        # path, not an inferred/default promotion.
        source = battery if battery else (registered_profile if profile_registered else {})
        capacity = max(1.0, self._num(source.get("capacity_kwh") or source.get("usable_capacity_kwh"), self.DEFAULT_CAPACITY_KWH))
        minimum = max(0.0, min(90.0, self._num(source.get("minimum_soc_percent") or source.get("min_soc_percent") or source.get("reserve_percent"), 20.0)))
        emergency = max(0.0, min(minimum, self._num(source.get("emergency_reserve_percent"), 10.0)))
        maximum = max(minimum + 1.0, min(100.0, self._num(source.get("maximum_soc_percent") or source.get("max_soc_percent"), 95.0)))
        max_charge = max(100.0, self._num(source.get("max_charge_power_w") or source.get("charge_limit_w"), self.DEFAULT_MAX_CHARGE_W))
        max_discharge = max(100.0, self._num(source.get("max_discharge_power_w") or source.get("discharge_limit_w"), self.DEFAULT_MAX_DISCHARGE_W))
        efficiency = max(0.5, min(1.0, self._num(source.get("round_trip_efficiency") or source.get("efficiency"), self.DEFAULT_EFFICIENCY)))

        mappings = self.registry.data.get("entity_mappings", {}) if self.registry is not None else {}
        soc_entity = source.get("soc_entity") or mappings.get("battery_soc")
        soc_state = self.registry.hass.states.get(soc_entity) if soc_entity and self.registry is not None and hasattr(self.registry, "hass") else None
        soc_available = bool(soc_state and str(soc_state.state).lower() not in {"unknown", "unavailable", "none", ""})
        home_capacity = home_settings.get("battery_capacity_kwh")

        configured_fields = {
            "capacity_kwh": source.get("capacity_kwh"),
            "usable_capacity_kwh": source.get("usable_capacity_kwh"),
            "minimum_soc_percent": source.get("minimum_soc_percent"),
            "min_soc_percent": source.get("min_soc_percent"),
            "reserve_percent": source.get("reserve_percent"),
            "emergency_reserve_percent": source.get("emergency_reserve_percent"),
            "maximum_soc_percent": source.get("maximum_soc_percent"),
            "max_soc_percent": source.get("max_soc_percent"),
            "max_charge_power_w": source.get("max_charge_power_w"),
            "charge_limit_w": source.get("charge_limit_w"),
            "max_discharge_power_w": source.get("max_discharge_power_w"),
            "discharge_limit_w": source.get("discharge_limit_w"),
            "round_trip_efficiency": source.get("round_trip_efficiency"),
            "efficiency": source.get("efficiency"),
        }

        explicitly_configured = bool(battery) or profile_registered
        evidence_sources = {
            "registered_battery_device": bool(battery),
            "registered_battery_profile": profile_registered,
            "registration_source": "registry_device" if battery else ("explicit_battery_profile" if profile_registered else None),
            "battery_device_type": battery.get("type") if battery else (registered_profile.get("device_type") if profile_registered else None),
            "battery_soc_entity": soc_entity,
            "battery_soc_available": soc_available,
            "home_settings_battery_capacity_kwh": home_capacity,
            "registered_fields": [k for k, v in configured_fields.items() if v not in (None, "")],
        }

        blockers = []
        if not explicitly_configured:
            blockers.append("no enabled registered battery device and no explicit canonical battery profile")
        if not soc_entity:
            blockers.append("battery SOC entity is not mapped")
        elif not soc_available:
            blockers.append("mapped battery SOC entity is unavailable")
        required_groups = {
            "capacity": bool(source.get("capacity_kwh") or source.get("usable_capacity_kwh")),
            "minimum_soc": source.get("minimum_soc_percent") is not None or source.get("min_soc_percent") is not None or source.get("reserve_percent") is not None,
            "maximum_soc": source.get("maximum_soc_percent") is not None or source.get("max_soc_percent") is not None,
            "max_charge_power": bool(source.get("max_charge_power_w") or source.get("charge_limit_w")),
            "max_discharge_power": bool(source.get("max_discharge_power_w") or source.get("discharge_limit_w")),
            "efficiency": source.get("round_trip_efficiency") is not None or source.get("efficiency") is not None,
        }
        for label, present in required_groups.items():
            if not present:
                blockers.append(f"canonical battery {label.replace('_', ' ')} is missing")
        configured = bool(explicitly_configured and soc_available and all(required_groups.values()))

        return {
            "device_id": source.get("id") or source.get("device_id"),
            "device_name": source.get("name") or source.get("device_name") or "Battery",
            "configured": configured,
            "registration_present": explicitly_configured,
            "capacity_kwh": capacity,
            "minimum_soc_percent": minimum,
            "emergency_reserve_percent": emergency,
            "maximum_soc_percent": maximum,
            "max_charge_power_w": max_charge,
            "max_discharge_power_w": max_discharge,
            "round_trip_efficiency": efficiency,
            "evidence_sources": evidence_sources,
            "configuration_blockers": blockers,
            "configuration_policy": "configured=true requires explicit battery registration (device or canonical battery profile), available SOC evidence, capacity, SOC limits, charge/discharge limits and efficiency. Defaults never become canonical evidence.",
        }

    def _scheduled_load_by_hour(self) -> dict[str, float]:
        result: dict[str, float] = {}
        schedule = [] if self.scheduler is None else self.scheduler.summary().get("schedule", [])
        for item in schedule:
            start, end = self._dt(item.get("suggested_start")), self._dt(item.get("suggested_end"))
            if not start or not end or end <= start:
                continue
            power = max(0.0, self._num(item.get("expected_power_w")))
            cursor = start.replace(minute=0, second=0, microsecond=0)
            while cursor < end:
                overlap_start, overlap_end = max(cursor, start), min(cursor + timedelta(hours=1), end)
                fraction = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600)
                key = cursor.isoformat()
                result[key] = result.get(key, 0.0) + power * fraction
                cursor += timedelta(hours=1)
        return result

    def refresh(self) -> dict[str, Any]:
        flow = self.energy_flow.summary()
        flows = flow.get("flows", {})
        soc = self._num(flows.get("battery_soc_percent"), -1)
        charge = self._num((flows.get("battery_charge_power") or {}).get("w"))
        discharge = self._num((flows.get("battery_discharge_power") or {}).get("w"))
        imported = self._num((flows.get("grid_import_power") or {}).get("w"))
        exported = self._num((flows.get("grid_export_power") or {}).get("w"))
        solar_now = self._num((flows.get("solar_power") or {}).get("w"))
        home_now = self._num((flows.get("house_power") or {}).get("w"))

        forecast = self.forecast.summary()
        # Canonical Forecast exposes its internal planning timeline as
        # `planning_hourly`. Older builds may expose `hourly`; keep that only as
        # a compatibility fallback. No forecast is recalculated here.
        hourly_source = "planning_hourly" if isinstance(forecast.get("planning_hourly"), list) else "hourly"
        forecast_rows = list(forecast.get("planning_hourly") or forecast.get("hourly") or [])
        now_utc = datetime.now(timezone.utc)
        current_hour_utc = now_utc.replace(minute=0, second=0, microsecond=0)
        hourly = []
        skipped_elapsed_rows = 0
        for row in forecast_rows:
            start = self._dt(row.get("time")) if isinstance(row, dict) else None
            if start is None:
                continue
            start_utc = start.astimezone(timezone.utc) if start.tzinfo is not None else start.replace(tzinfo=timezone.utc)
            if start_utc < current_hour_utc:
                skipped_elapsed_rows += 1
                continue
            hourly.append(row)
            if len(hourly) >= 48:
                break
        confidence = self._num(forecast.get("confidence_percent") or forecast.get("confidence"), 0)
        learned_home = self._num(self.learning.summary().get("average_day", {}).get("home_kwh"), 0)
        config = self._battery_config()
        tariffs = {} if self.registry is None else self.registry.data.get("sources", {}).get("tariffs", {})
        tariff_enabled = bool(tariffs.get("enabled"))
        import_rate = self._num(tariffs.get("import_tariff"), 0)
        export_rate = self._num(tariffs.get("export_tariff"), 0)
        currency = str(tariffs.get("currency") or "CHF").upper()[:4]
        scheduled_load = self._scheduled_load_by_hour()

        minimum = config["minimum_soc_percent"]
        tomorrow_solar = self._num(forecast.get("expected_solar_following_24h_kwh"))
        today_solar = self._num(forecast.get("expected_solar_next_24h_kwh"))
        expected_demand = learned_home or sum(self._num(x.get("house_power_w")) for x in hourly[:24]) / 1000
        solar_ratio = tomorrow_solar / expected_demand if expected_demand > 0 else 1.0
        dynamic_reserve = minimum
        reserve_reason = "Configured minimum reserve."
        if confidence < 25:
            dynamic_reserve = max(dynamic_reserve, 40.0); reserve_reason = "Raised because forecast confidence is limited."
        elif solar_ratio < 0.55:
            dynamic_reserve = max(dynamic_reserve, 70.0); reserve_reason = "Raised because tomorrow's solar is weak versus learned demand."
        elif solar_ratio < 0.9:
            dynamic_reserve = max(dynamic_reserve, 50.0); reserve_reason = "Raised because tomorrow may not cover normal demand."
        elif solar_ratio > 1.4:
            dynamic_reserve = max(minimum, 20.0); reserve_reason = "Lower reserve creates capacity for strong expected solar."
        dynamic_reserve = min(dynamic_reserve, config["maximum_soc_percent"] - 1)

        timeline = []
        projected_soc = max(0.0, min(100.0, soc if soc >= 0 else 50.0))
        baseline_soc = projected_soc
        capacity = config["capacity_kwh"]
        charge_eff = config["round_trip_efficiency"] ** 0.5
        discharge_eff = charge_eff
        avoided_import_kwh = 0.0
        missed_export_kwh = 0.0
        reserve_breaches = 0
        projected_min = projected_soc
        projected_max = projected_soc

        for row in hourly:
            start = self._dt(row.get("time"))
            key = start.replace(minute=0, second=0, microsecond=0).isoformat() if start else str(row.get("time"))
            solar_w = max(0.0, self._num(row.get("solar_power_w")))
            home_w = max(0.0, self._num(row.get("house_power_w")))
            extra_w = max(0.0, scheduled_load.get(key, 0.0))
            net_w = solar_w - home_w - extra_w
            action = "Hold"
            battery_power_w = 0.0
            grid_w = 0.0
            start_soc = projected_soc
            if net_w > 0 and projected_soc < config["maximum_soc_percent"]:
                room_kwh = capacity * (config["maximum_soc_percent"] - projected_soc) / 100
                charge_w_advice = min(net_w, config["max_charge_power_w"], room_kwh * 1000 / max(charge_eff, .01))
                stored_kwh = charge_w_advice / 1000 * charge_eff
                projected_soc += stored_kwh / capacity * 100
                battery_power_w = charge_w_advice
                grid_w = max(0.0, net_w - charge_w_advice)
                missed_export_kwh += grid_w / 1000
                action = "Charge from forecast solar"
            elif net_w < 0 and projected_soc > dynamic_reserve:
                usable_kwh = capacity * (projected_soc - dynamic_reserve) / 100
                discharge_w_advice = min(-net_w, config["max_discharge_power_w"], usable_kwh * 1000 * discharge_eff)
                removed_kwh = discharge_w_advice / 1000 / max(discharge_eff, .01)
                projected_soc -= removed_kwh / capacity * 100
                battery_power_w = -discharge_w_advice
                grid_w = max(0.0, -net_w - discharge_w_advice)
                avoided_import_kwh += discharge_w_advice / 1000
                action = "Discharge to reduce import"
            elif net_w < 0:
                grid_w = -net_w
                action = "Hold reserve"
            else:
                grid_w = max(0.0, net_w)
            projected_soc = max(0.0, min(100.0, projected_soc))
            projected_min, projected_max = min(projected_min, projected_soc), max(projected_max, projected_soc)
            if projected_soc < minimum:
                reserve_breaches += 1
            timeline.append({
                "time": row.get("time"), "start_soc_percent": round(start_soc, 1),
                "projected_soc_percent": round(projected_soc, 1), "solar_power_w": round(solar_w),
                "home_power_w": round(home_w), "scheduled_load_w": round(extra_w),
                "recommended_battery_power_w": round(battery_power_w), "projected_grid_power_w": round(grid_w),
                "action": action, "condition": row.get("condition"),
            })

        strategy, reason, action = "Balanced forecast operation", "Use solar surplus to charge and discharge only above the protected reserve.", "Follow advisory timeline"
        if soc < 0:
            strategy, reason, action = "Unavailable", "Map a battery SOC entity to enable predictive optimization.", "Review battery mapping"
        elif not hourly or forecast.get("status") != "Ready":
            strategy, reason, action = "Forecast reserve", "A complete hourly forecast is not available, so preserve a conservative reserve.", "Keep reserve"
        elif soc < minimum:
            strategy, reason, action = "Protect battery reserve", f"Current SOC is below the configured {minimum:.0f}% minimum.", "Avoid discharge"
        elif exported > 300 and soc < config["maximum_soc_percent"] and charge < 25:
            strategy, reason, action = "Capture solar surplus", f"The home is exporting {exported:.0f} W while battery capacity is available.", "Review charging policy"
        elif imported > 300 and soc > dynamic_reserve + 5 and discharge < 25:
            strategy, reason, action = "Reduce grid import", f"Grid import is {imported:.0f} W while usable battery energy is available above reserve.", "Review discharge policy"
        elif solar_ratio > 1.4 and soc > 70:
            strategy, reason, action = "Create capacity for solar", "Strong solar is expected and the battery is already well charged.", "Use energy above reserve"
        elif solar_ratio < .65:
            strategy, reason, action = "Preserve energy for weak solar", "Tomorrow's expected solar is low compared with learned demand.", "Protect reserve"

        # Build a calendar-tomorrow advisory plan from the same learned 48-hour
        # simulation. This is deliberately descriptive: many inverter-managed
        # batteries (including common hybrid systems) expose monitoring but no
        # safe real-time command surface to Home Assistant. Zeus therefore
        # learns and predicts first, and never assumes control capability.
        tomorrow_date = datetime.now().astimezone().date() + timedelta(days=1)
        tomorrow_rows = []
        for item in timeline:
            stamp = self._dt(item.get("time"))
            if stamp is not None and stamp.astimezone().date() == tomorrow_date:
                tomorrow_rows.append(item)

        def phase_kind(item: dict[str, Any]) -> str:
            power = self._num(item.get("recommended_battery_power_w"))
            action_text = str(item.get("action") or "").lower()
            if power > 25 or "charge from" in action_text:
                return "Charge expected"
            if power < -25 or "discharge" in action_text:
                return "Discharge expected"
            return "Hold / reserve"

        phases: list[dict[str, Any]] = []
        for item in tomorrow_rows:
            stamp = self._dt(item.get("time"))
            if stamp is None:
                continue
            local_start = stamp.astimezone()
            kind = phase_kind(item)
            if phases and phases[-1]["kind"] == kind and phases[-1]["end"] == local_start.isoformat():
                phases[-1]["end"] = (local_start + timedelta(hours=1)).isoformat()
                phases[-1]["solar_kwh"] += self._num(item.get("solar_power_w")) / 1000
                phases[-1]["home_kwh"] += self._num(item.get("home_power_w")) / 1000
                phases[-1]["battery_kwh"] += abs(self._num(item.get("recommended_battery_power_w"))) / 1000
                phases[-1]["end_soc_percent"] = item.get("projected_soc_percent")
            else:
                phases.append({
                    "kind": kind,
                    "start": local_start.isoformat(),
                    "end": (local_start + timedelta(hours=1)).isoformat(),
                    "solar_kwh": self._num(item.get("solar_power_w")) / 1000,
                    "home_kwh": self._num(item.get("home_power_w")) / 1000,
                    "battery_kwh": abs(self._num(item.get("recommended_battery_power_w"))) / 1000,
                    "start_soc_percent": item.get("start_soc_percent"),
                    "end_soc_percent": item.get("projected_soc_percent"),
                })
        for phase in phases:
            for key in ("solar_kwh", "home_kwh", "battery_kwh"):
                phase[key] = round(self._num(phase.get(key)), 2)

        learning_state = self.learning.summary() or {}
        learning_confidence = self._num(learning_state.get("confidence_percent"), 0)
        tomorrow_solar_kwh = sum(self._num(x.get("solar_power_w")) for x in tomorrow_rows) / 1000
        tomorrow_home_kwh = sum(self._num(x.get("home_power_w")) for x in tomorrow_rows) / 1000
        tomorrow_grid_kwh = sum(max(0.0, self._num(x.get("projected_grid_power_w"))) for x in tomorrow_rows) / 1000
        tomorrow_plan = {
            "status": "Ready" if tomorrow_rows else "Collecting",
            "date": tomorrow_date.isoformat(),
            "mode": "inverter_managed_advisory",
            "control_capability": "Advisory only",
            "control_note": "Zeus does not assume that the inverter or battery can accept real-time commands.",
            "expected_solar_kwh": round(tomorrow_solar_kwh, 2),
            "expected_home_kwh": round(tomorrow_home_kwh, 2),
            "expected_grid_import_kwh": round(tomorrow_grid_kwh, 2),
            "start_soc_percent": tomorrow_rows[0].get("start_soc_percent") if tomorrow_rows else None,
            "end_soc_percent": tomorrow_rows[-1].get("projected_soc_percent") if tomorrow_rows else None,
            "recommended_reserve_percent": round(dynamic_reserve, 1),
            "phases": phases,
            "learning": {
                "history_days": learning_state.get("history_days", 0),
                "history_months": learning_state.get("history_months", 0),
                "confidence_percent": round(learning_confidence, 1),
                "learned_home_day_kwh": round(learned_home, 2) if learned_home else None,
                "summary": learning_state.get("summary") or "Learning evidence is still collecting.",
            },
            "forecast_confidence_percent": round(confidence, 1),
            "confidence_percent": round(max(0.0, min(96.0, confidence * .65 + learning_confidence * .35)), 1),
            "learning_cycle": "Observe → Learn → Predict → Recommend → Verify → Improve",
            "safety": "Recommendation only. No battery or inverter command is sent.",
        }

        estimated_saving = avoided_import_kwh * import_rate - avoided_import_kwh * export_rate if tariff_enabled else None
        cycle_throughput = sum(abs(self._num(x.get("recommended_battery_power_w"))) for x in timeline) / 1000
        equivalent_cycles = cycle_throughput / max(2 * capacity, .01)
        confidence_score = int(max(20, min(96, confidence * .72 + (20 if config["configured"] else 8) + (8 if learned_home else 0))))
        status = "Ready" if soc >= 0 and hourly else "Waiting"
        self.last = {
            "status": status, "engine": "Predictive Battery Optimization", "version": "10.12",
            "mode": "recommendation_only", "strategy": strategy, "recommended_action": action,
            "reason": reason, "summary": f"{strategy}: {reason}",
            "generated_at": datetime.now(timezone.utc).isoformat(), "horizon_hours": 48,
            "battery_soc_percent": None if soc < 0 else round(soc, 1),
            "charge_power_w": round(charge), "discharge_power_w": round(discharge),
            "solar_power_w": round(solar_now), "house_power_w": round(home_now),
            "grid_import_w": round(imported), "grid_export_w": round(exported),
            "recommended_reserve_percent": round(dynamic_reserve, 1), "reserve_reason": reserve_reason,
            "projected_soc_end_percent": round(projected_soc, 1),
            "projected_soc_min_percent": round(projected_min, 1), "projected_soc_max_percent": round(projected_max, 1),
            "projected_soc_change_percent": round(projected_soc - baseline_soc, 1),
            "reserve_breach_hours": reserve_breaches,
            "forecast_today_kwh": round(today_solar, 2), "forecast_tomorrow_kwh": round(tomorrow_solar, 2),
            "forecast_confidence_percent": round(confidence), "optimizer_confidence_percent": confidence_score,
            "forecast_bridge": {
                "source_key": hourly_source,
                "source_status": forecast.get("status"),
                "available_rows": len(forecast_rows),
                "skipped_elapsed_rows": skipped_elapsed_rows,
                "consumed_rows": len(hourly),
                "horizon_hours": forecast.get("forecast_horizon_hours"),
                "anchor_generated_at": now_utc.isoformat(),
                "anchor_live_soc_percent": None if soc < 0 else round(soc, 1),
                "anchor_hour_utc": current_hour_utc.isoformat(),
                "first_time": hourly[0].get("time") if hourly else None,
                "last_time": hourly[-1].get("time") if hourly else None,
                "ready": bool(hourly and str(forecast.get("status") or "").lower() == "ready"),
                "policy": "Consumes the rolling canonical Forecast horizon from the current hour forward and anchors projected SOC to current live SOC; elapsed forecast hours are never replayed.",
            },
            "learned_home_day_kwh": round(learned_home, 2) if learned_home else None,
            "forecast_to_demand_ratio": round(solar_ratio, 2),
            "scheduled_load_count": len(self.scheduler.summary().get("schedule", [])) if self.scheduler else 0,
            "scheduled_load_energy_kwh": round(sum(self._num(x.get("expected_energy_kwh")) for x in (self.scheduler.summary().get("schedule", []) if self.scheduler else [])), 3),
            "estimated_avoided_import_kwh": round(avoided_import_kwh, 3),
            "estimated_unstored_surplus_kwh": round(missed_export_kwh, 3),
            "estimated_saving": round(estimated_saving, 3) if estimated_saving is not None else None,
            "currency": currency, "tariff_aware": tariff_enabled,
            "estimated_equivalent_cycles": round(equivalent_cycles, 3),
            "battery_config": config,
            "tomorrow_plan": tomorrow_plan,
            "battery_evidence_diagnostics": {
                "configured": config.get("configured"),
                "device_id": config.get("device_id"),
                "device_name": config.get("device_name"),
                "status": status,
                "strategy": strategy,
                "soc_source_entity": (config.get("evidence_sources") or {}).get("battery_soc_entity"),
                "soc_source_available": (config.get("evidence_sources") or {}).get("battery_soc_available"),
                "battery_soc_percent": None if soc < 0 else round(soc, 1),
                "capacity_kwh": config.get("capacity_kwh"),
                "capacity_source": (
                    (config.get("evidence_sources") or {}).get("registration_source")
                    if any(x in (config.get("evidence_sources") or {}).get("registered_fields", []) for x in ("capacity_kwh", "usable_capacity_kwh"))
                    else ("home settings exists but is not canonical Predictive Battery registration" if (config.get("evidence_sources") or {}).get("home_settings_battery_capacity_kwh") not in (None, "") else "default model")
                ),
                "home_settings_battery_capacity_kwh": (config.get("evidence_sources") or {}).get("home_settings_battery_capacity_kwh"),
                "minimum_soc_percent": config.get("minimum_soc_percent"),
                "emergency_reserve_percent": config.get("emergency_reserve_percent"),
                "maximum_soc_percent": config.get("maximum_soc_percent"),
                "max_charge_power_w": config.get("max_charge_power_w"),
                "max_discharge_power_w": config.get("max_discharge_power_w"),
                "round_trip_efficiency": config.get("round_trip_efficiency"),
                "registration_source": (config.get("evidence_sources") or {}).get("registration_source"),
                "registered_battery_device": (config.get("evidence_sources") or {}).get("registered_battery_device"),
                "registered_battery_profile": (config.get("evidence_sources") or {}).get("registered_battery_profile"),
                "registered_fields": (config.get("evidence_sources") or {}).get("registered_fields") or [],
                "configuration_blockers": config.get("configuration_blockers") or [],
                "configuration_policy": config.get("configuration_policy"),
                "modeled_avoidable_import_kwh": round(avoided_import_kwh, 3),
                "potential_saving": round(estimated_saving, 3) if estimated_saving is not None else None,
                "currency": currency if estimated_saving is not None else None,
                "optimizer_confidence_percent": confidence_score,
                "forecast_source_key": hourly_source,
                "forecast_available_rows": len(forecast_rows),
                "forecast_skipped_elapsed_rows": skipped_elapsed_rows,
                "forecast_consumed_rows": len(hourly),
                "forecast_status": forecast.get("status"),
                "forecast_bridge_ready": bool(hourly and str(forecast.get("status") or "").lower() == "ready"),
                "timeline_anchor_live_soc_percent": None if soc < 0 else round(soc, 1),
                "timeline_anchor_generated_at": now_utc.isoformat(),
            },
            "timeline": timeline,
            "assumptions": [
                "Hourly forecast values represent one-hour average power.",
                "Scheduled flexible loads are included when their advised window overlaps the forecast hour.",
                "Fixed import/export tariffs are used when configured; no dynamic tariff is inferred.",
                "Battery limits use registry values when present and conservative defaults otherwise.",
            ],
            "method": "48-hour SOC simulation using solar and home forecasts, scheduler demand, reserve policy, battery limits, efficiency and optional tariffs.",
            "safety": "Recommendation only. Zeus never calls battery, inverter or device services and never changes control settings.",
        }
        self.event_bus.publish("PredictiveBatteryUpdated", "PredictiveBatteryOptimizer", {"strategy": strategy, "reserve": round(dynamic_reserve, 1), "projected_end_soc": round(projected_soc, 1)})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last

class AIEnergyAdvisor:
    """Explain current energy conditions and prioritize transparent advice."""

    def __init__(self, event_bus, energy_flow, forecast, learning, efficiency, battery, optimizer) -> None:
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.forecast = forecast
        self.learning = learning
        self.efficiency = efficiency
        self.battery = battery
        self.optimizer = optimizer
        self.last: dict[str, Any] = {"status": "Waiting", "headline": "Collecting energy context"}

    @staticmethod
    def _num(v: Any) -> float:
        try: return float(v or 0)
        except (TypeError, ValueError): return 0.0

    def refresh(self) -> dict[str, Any]:
        flow = self.energy_flow.summary().get("flows", {})
        solar = self._num((flow.get("solar_power") or {}).get("w"))
        home = self._num((flow.get("house_power") or {}).get("w"))
        imp = self._num((flow.get("grid_import_power") or {}).get("w"))
        exp = self._num((flow.get("grid_export_power") or {}).get("w"))
        soc = flow.get("battery_soc_percent")
        fc = self.forecast.summary()
        eff = self.efficiency.summary()
        batt = self.battery.summary()
        opt = self.optimizer.summary()
        recommendations = []
        raw = opt.get("recommendations", [])
        if isinstance(raw, list):
            recommendations.extend([x for x in raw[:3] if isinstance(x, dict)])
        if imp > 300:
            recommendations.insert(0, {"title":"Reduce grid import", "reason":f"The home is importing {round(imp)} W while consuming {round(home)} W.", "priority":"High" if imp>1500 else "Normal"})
        elif exp > 500:
            recommendations.insert(0, {"title":"Use available solar", "reason":f"About {round(exp)} W is currently being exported.", "priority":"Normal"})
        elif solar > home and solar > 500:
            recommendations.insert(0, {"title":"Solar is covering the home", "reason":"Current solar production exceeds household demand.", "priority":"Information"})
        headline = recommendations[0].get("title") if recommendations else "Energy system is balanced"
        explanation = recommendations[0].get("reason") if recommendations else "No strong live optimization opportunity is present."
        self.last = {
            "status": "Ready",
            "headline": headline,
            "explanation": explanation,
            "live_context": {"solar_w":round(solar),"home_w":round(home),"grid_import_w":round(imp),"grid_export_w":round(exp),"battery_soc_percent":soc},
            "today_score": eff.get("score"),
            "forecast_today_kwh": fc.get("expected_solar_next_24h_kwh"),
            "forecast_tomorrow_kwh": fc.get("expected_solar_following_24h_kwh"),
            "battery_strategy": batt,
            "learning_confidence_percent": self.learning.summary().get("confidence_percent"),
            "recommendations": recommendations[:5],
            "questions": [
                {"question":"Why am I importing from the grid?", "answer": explanation if imp>25 else "The grid is not currently supplying meaningful power."},
                {"question":"When should I run a flexible device?", "answer": f"Best forecast window: {fc.get('best_surplus_window') or 'still being calculated'}."},
                {"question":"What should the battery do?", "answer": batt.get("summary", "Battery strategy is being calculated.")},
            ],
            "summary": f"{headline}. {explanation}",
            "safety": "Read-only advisor. Recommendations require user approval and do not control devices.",
        }
        self.event_bus.publish("AIEnergyAdvisorUpdated", "AIEnergyAdvisor", {"headline": headline, "recommendations": len(recommendations)})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class ConversationalZeusAssistant:
    """Generate transparent, context-aware answers without controlling equipment."""

    def __init__(self, event_bus, energy_flow, forecast, scheduler, battery, learning, analytics, finance, registry, advisor, weather_history=None, data_consistency=None, device_analytics=None) -> None:
        self.event_bus = event_bus
        self.energy_flow = energy_flow
        self.forecast = forecast
        self.scheduler = scheduler
        self.battery = battery
        self.learning = learning
        self.analytics = analytics
        self.finance = finance
        self.registry = registry
        self.advisor = advisor
        self.weather_history = weather_history
        self.data_consistency = data_consistency
        self.device_analytics = device_analytics
        self.last: dict[str, Any] = {"status": "Waiting", "headline": "Collecting context"}

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        if isinstance(value, dict):
            value = value.get("w", value.get("value"))
        try:
            return float(value if value is not None else default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _money(value: Any, currency: str) -> str:
        try:
            return f"{currency} {float(value):.2f}"
        except (TypeError, ValueError):
            return "not available yet"

    def _flexible_load_answer(self, *, best_window: str, scheduled_count: int, solar: float, home: float, imp: float, exp: float, soc: float, battery_status: str, battery_status_w: float) -> str:
        """Explain a flexible-load recommendation from the same live context shown by Copilot."""
        battery = f"Battery SOC is {soc:.0f}% and it is {battery_status.lower()}" if soc >= 0 else "Battery SOC is not mapped"
        if battery_status_w > 25:
            battery += f" at about {battery_status_w:.0f} W"
        if imp > 25:
            flow = f"The home is currently importing about {imp:.0f} W, so waiting for the forecast window can reduce grid use."
        elif exp > 25:
            flow = f"There is already about {exp:.0f} W of export surplus; the forecast window may offer an even stronger local-energy opportunity."
        elif solar > home + 25:
            flow = "Solar currently exceeds measured home demand, so a flexible load can use locally produced energy now if needed."
        else:
            flow = f"Current solar is about {solar:.0f} W against home demand of {home:.0f} W, with no meaningful grid exchange."
        scheduler = (f"Zeus also has {scheduled_count} advised scheduled load(s), so check the Scheduler for competing flexible loads."
                     if scheduled_count else "No other advised scheduled load is currently competing for that window.")
        return f"The strongest forecast opportunity is {best_window}. {flow} {battery}. {scheduler}"

    def _period_answer(self, name: str, period: dict[str, Any], fin: dict[str, Any], currency: str) -> str:
        """Summarize a canonical calendar period without creating a second accounting path."""
        if not isinstance(period, dict) or int(self._num(period.get("day_count"), 0)) <= 0:
            return f"Zeus does not yet have measured {name} period data available."
        house = max(0.0, self._num(period.get("house_energy_kwh")))
        solar = max(0.0, self._num(period.get("solar_energy_kwh")))
        imp = max(0.0, self._num(period.get("grid_import_energy_kwh")))
        exp = max(0.0, self._num(period.get("grid_export_energy_kwh")))
        batt = max(0.0, self._num(period.get("battery_discharge_energy_kwh")))
        direct = max(0.0, self._num(period.get("direct_solar_consumption_kwh")))
        import_rate = max(0.0, self._num(fin.get("import_tariff")))
        export_rate = max(0.0, self._num(fin.get("export_tariff")))
        direct = min(direct, house)
        local_home = max(0.0, house - imp)
        batt_home = min(batt, max(0.0, local_home - direct))
        grid_cost = imp * import_rate
        export_value = exp * export_rate
        avoided = (direct + batt_home) * import_rate
        benefit = avoided + export_value - grid_cost
        return (f"{name.title()} uses the canonical Zeus calendar period: home consumption {house:.2f} kWh, "
                f"solar production {solar:.2f} kWh, grid import {imp:.2f} kWh and export {exp:.2f} kWh. "
                f"Direct solar to home is {direct:.2f} kWh and measured battery support is {batt_home:.2f} kWh. "
                f"At the configured tariffs, grid energy cost is {self._money(grid_cost, currency)}, export value is "
                f"{self._money(export_value, currency)}, and the energy benefit before any period-specific standing-charge treatment is "
                f"{self._money(benefit, currency)}.")

    def _period_battery_answer(self, name: str, period: dict[str, Any], fin: dict[str, Any], currency: str) -> str:
        if not isinstance(period, dict) or int(self._num(period.get("day_count"), 0)) <= 0:
            return f"Zeus does not yet have measured {name} battery history available."
        house = max(0.0, self._num(period.get("house_energy_kwh")))
        imp = max(0.0, self._num(period.get("grid_import_energy_kwh")))
        direct = max(0.0, self._num(period.get("direct_solar_consumption_kwh")))
        discharge = max(0.0, self._num(period.get("battery_discharge_energy_kwh")))
        support = min(discharge, max(0.0, max(0.0, house - imp) - min(direct, house)))
        value = support * max(0.0, self._num(fin.get("import_tariff")))
        return f"For {name}, measured battery discharge is {discharge:.2f} kWh; up to {support:.2f} kWh supported home demand, worth {self._money(value, currency)} in avoided grid purchases at the configured import tariff."

    def _confidence_note(self, accounting_conf: dict[str, Any], period: str | None = None) -> str:
        """Return a compact evidence note from the canonical Accounting Confidence layer."""
        if not isinstance(accounting_conf, dict):
            return " Accounting confidence is still being prepared."
        percent = int(max(0, min(100, self._num(accounting_conf.get("percent"), 0))))
        label = str(accounting_conf.get("label") or "Waiting")
        notes: list[str] = []
        pq = accounting_conf.get("period_quality") if isinstance(accounting_conf.get("period_quality"), dict) else {}
        q = pq.get(period) if period and isinstance(pq.get(period), dict) else {}
        if q:
            if q.get("epoch_limited"):
                notes.append("this period is limited by the trusted Zeus data start")
            status = str(q.get("status") or "").lower()
            if status and status not in {"ready", "pass"}:
                notes.append(f"period evidence is {status}")
            measured = q.get("measurement_coverage_percent")
            if measured is not None and self._num(measured, 100) < 99.5:
                notes.append(f"registered-load history coverage is {self._num(measured):.0f}%")
            if q.get("aggregate_reconciled") or int(self._num(q.get("timestamp_overlap_samples"), 0)) > 0:
                notes.append("reconciliation was required")
        if not notes:
            limits = accounting_conf.get("active_limits") if isinstance(accounting_conf.get("active_limits"), list) else []
            if limits:
                notes.append(str(limits[0]))
        detail = "; ".join(notes[:2])
        return f" Accounting confidence: {label} ({percent}%)." + (f" Note: {detail}." if detail else "")

    def _historical_grid_import_confidence(self, period: dict[str, Any], learn: dict[str, Any]) -> int:
        if not isinstance(period, dict) or int(self._num(period.get("day_count"), 0)) <= 0:
            return max(20, min(55, int(self._num(learn.get("confidence_percent"), 35))))
        measured_fields = sum(
            1 for key in ("grid_import_energy_kwh", "solar_energy_kwh", "house_energy_kwh", "battery_discharge_energy_kwh")
            if period.get(key) is not None
        )
        learned = int(self._num(learn.get("confidence_percent"), 50))
        return max(55, min(96, 65 + measured_fields * 6 + learned // 10))

    def _historical_grid_import_answer(self, period: dict[str, Any], learn: dict[str, Any]) -> str:
        if not isinstance(period, dict) or int(self._num(period.get("day_count"), 0)) <= 0:
            return "Zeus does not yet have a complete measured record for yesterday, so it will not infer a cause from today’s data. Check Analytics after Recorder history is available."
        imp = max(0.0, self._num(period.get("grid_import_energy_kwh")))
        solar = max(0.0, self._num(period.get("solar_energy_kwh")))
        home = max(0.0, self._num(period.get("house_energy_kwh")))
        batt = max(0.0, self._num(period.get("battery_discharge_energy_kwh")))
        direct = max(0.0, self._num(period.get("direct_solar_consumption_kwh")))
        parts = [f"Yesterday’s measured grid import was {imp:.2f} kWh"]
        parts.append(f"Home demand was {home:.2f} kWh and solar production was {solar:.2f} kWh")
        if batt > 0:
            parts.append(f"Battery discharge contributed {batt:.2f} kWh")
        if imp <= 0.05:
            conclusion = "Grid import was not materially high in the measured record."
        else:
            local_supply = direct + batt
            uncovered = max(home - local_supply, 0.0)
            if solar <= 0.05:
                conclusion = "The measured record points mainly to household demand occurring without meaningful solar production."
            elif uncovered > imp * 0.75:
                conclusion = "The measured record indicates demand exceeded locally retained solar and battery support for part of the day."
            else:
                conclusion = "The measured record shows grid energy was needed during periods when local supply did not fully cover demand."
        return ". ".join(parts) + ". " + conclusion

    def refresh(self) -> dict[str, Any]:
        flows = self.energy_flow.summary().get("flows", {})
        fc = self.forecast.summary() or {}
        sched = self.scheduler.summary() or {}
        batt = self.battery.summary() or {}
        learn = self.learning.summary() or {}
        hist = self.analytics.summary() or {}
        fin = self.finance.summary() or {}
        adv = self.advisor.summary() or {}
        weather_intel = self.weather_history.summary() if self.weather_history is not None else {}
        consistency = self.data_consistency.summary() if self.data_consistency is not None else {}
        accounting_conf = consistency.get("confidence") if isinstance(consistency.get("confidence"), dict) else {}
        device_summary = self.device_analytics.summary() if self.device_analytics is not None else {}
        solar = self._num(flows.get("solar_power"))
        wind = self._num(flows.get("wind_power"))
        generator = self._num(flows.get("generator_power"))
        generation = self._num(flows.get("generation_power"))
        source_mix = flows.get("generation_source_mix_today_percent") or {}
        source_energy = flows.get("generation_energy_sources_today_kwh") or {}
        home = self._num(flows.get("house_power"))
        imp = self._num(flows.get("grid_import_power"))
        exp = self._num(flows.get("grid_export_power"))
        soc_raw = flows.get("battery_soc_percent")
        soc = self._num(soc_raw, -1) if soc_raw is not None else -1
        charge_w = self._num((flows.get("battery_charge_power") or {}).get("w"))
        discharge_w = self._num((flows.get("battery_discharge_power") or {}).get("w"))
        battery_deadband_w = 25.0
        battery_status = "Charging" if charge_w > battery_deadband_w else ("Discharging" if discharge_w > battery_deadband_w else "Idle")
        battery_status_w = charge_w if battery_status == "Charging" else (discharge_w if battery_status == "Discharging" else 0.0)
        currency = str(fin.get("currency") or batt.get("currency") or "CHF")
        best_window_raw = fc.get("best_surplus_window") or sched.get("best_window") or "still being calculated"
        if isinstance(best_window_raw, dict):
            best_window = best_window_raw.get("label") or best_window_raw.get("start") or best_window_raw.get("time") or "still being calculated"
        else:
            best_window = str(best_window_raw)
        devices = [d for d in self.registry.data.get("devices", []) if d.get("enabled", True)]
        device_names = [str(d.get("name") or d.get("device_name") or d.get("id") or "Device") for d in devices][:6]
        schedule = sched.get("schedule") if isinstance(sched.get("schedule"), list) else sched.get("plan")
        scheduled_count = len(schedule) if isinstance(schedule, list) else int(sched.get("scheduled_device_count") or 0)
        periods = hist.get("periods") if isinstance(hist.get("periods"), dict) else {}
        today = periods.get("today") if isinstance(periods.get("today"), dict) else (hist.get("today") if isinstance(hist.get("today"), dict) else {})
        yesterday = periods.get("yesterday") if isinstance(periods.get("yesterday"), dict) else (hist.get("yesterday") if isinstance(hist.get("yesterday"), dict) else {})
        week = periods.get("week") if isinstance(periods.get("week"), dict) else {}
        month = periods.get("month") if isinstance(periods.get("month"), dict) else {}
        year = periods.get("year") if isinstance(periods.get("year"), dict) else {}
        net_benefit = fin.get("net_benefit_today", fin.get("net_benefit"))
        battery_answer = batt.get("summary") or batt.get("reason") or "The battery strategy is still being calculated."
        import_answer = (f"The home is importing about {imp:.0f} W because demand ({home:.0f} W) is above the energy currently supplied by solar and battery."
                         if imp > 25 else "The grid is not currently supplying meaningful power to the home.")
        solar_answer = (f"Solar is producing about {solar:.0f} W and approximately {exp:.0f} W is available as export surplus."
                        if exp > 25 else f"Solar is producing about {solar:.0f} W. There is no meaningful export surplus right now.")
        soc_text = f"{soc:.1f}%" if soc >= 0 else "not mapped"

        # v14.0.0-alpha.22.11.3.1: performance recommendation answer uses the same
        # canonical today-period and finance authorities as Analytics.  It is
        # recommendation-only and never changes a device or configuration.
        perf_home = self._num(today.get("house_energy_kwh"))
        perf_import = self._num(today.get("grid_import_energy_kwh"))
        perf_export = self._num(today.get("grid_export_energy_kwh"))
        perf_direct_solar = self._num(fin.get("direct_solar_to_home_kwh"), self._num(today.get("solar_energy_kwh")) - perf_export)
        perf_battery = self._num(fin.get("battery_support_to_home_kwh"), self._num(today.get("battery_discharge_energy_kwh")))
        perf_grid_pct = max(0.0, min(100.0, perf_import / perf_home * 100.0)) if perf_home > 0 else 0.0
        perf_solar_pct = max(0.0, min(100.0, perf_direct_solar / perf_home * 100.0)) if perf_home > 0 else 0.0
        perf_battery_pct = max(0.0, min(100.0, perf_battery / perf_home * 100.0)) if perf_home > 0 else 0.0
        perf_self_use = self._num(today.get("self_consumption_percent"), -1.0)
        if perf_home <= 0:
            performance_advice = "Zeus is still collecting enough measured house energy to create a performance recommendation."
        elif perf_export > 0.5 and (perf_self_use < 0 or perf_self_use < 80):
            performance_advice = (
                f"The strongest opportunity today is to move flexible loads into solar-surplus windows. "
                f"Measured export is {perf_export:.2f} kWh while direct solar supplied about {perf_solar_pct:.0f}% of house demand. "
                "Use the Forecast or Scheduler for loads such as EV charging, dishwasher or washing machine. Zeus will not start those loads automatically."
            )
        elif perf_grid_pct >= 25 and perf_battery_pct < 15:
            performance_advice = (
                f"Grid dependence is about {perf_grid_pct:.0f}% today while whole-home battery support is about {perf_battery_pct:.0f}%. "
                "If your battery reserve and strategy allow it, preserving more stored energy for grid-heavy periods may reduce imports. Zeus does not change battery reserve or inverter settings automatically."
            )
        elif perf_battery_pct >= 20 and perf_grid_pct < 20:
            performance_advice = (
                f"Current performance is already strong: the battery supplied about {perf_battery_pct:.0f}% of house demand and grid dependence is about {perf_grid_pct:.0f}%. "
                "No manual battery change is indicated from this evidence alone; keep flexible loads aligned with solar opportunities."
            )
        else:
            performance_advice = (
                f"Today direct solar supplied about {perf_solar_pct:.0f}% of house demand, whole-home battery support about {perf_battery_pct:.0f}%, and grid dependence is about {perf_grid_pct:.0f}%. "
                "No single measured condition justifies a stronger action; use the next forecast opportunity for flexible loads and continue monitoring."
            )
        qas = [
            {"id":"grid","question":"Why am I importing from the grid?","answer":import_answer,"source":"Live Energy Flow","confidence":90 if imp>25 else 82},
            {"id":"device","question":"When should I run the dishwasher or washing machine?","answer":self._flexible_load_answer(best_window=best_window, scheduled_count=scheduled_count, solar=solar, home=home, imp=imp, exp=exp, soc=soc, battery_status=battery_status, battery_status_w=battery_status_w),"source":"Forecast + Live Energy Flow + Battery + Intelligent Scheduler","confidence":min(int(self._num(fc.get("confidence"),60)), int(self._num(accounting_conf.get("percent"),90)))},
            {"id":"ev_charge","question":"When should I charge the car?","answer":self._flexible_load_answer(best_window=best_window, scheduled_count=scheduled_count, solar=solar, home=home, imp=imp, exp=exp, soc=soc, battery_status=battery_status, battery_status_w=battery_status_w) + " If your EV charger is registered in Zeus, use the Scheduler window as the recommended charging opportunity; Zeus does not confuse the home-battery SOC with the car battery SOC.","source":"Forecast + Live Energy Flow + EV Scheduler Context","confidence":min(int(self._num(fc.get("confidence"),60)), int(self._num(accounting_conf.get("percent"),90)))},
            {"id":"battery","question":"Why is my battery not charging?","answer":f"Current SOC is {soc_text}. {battery_answer}","source":"Predictive Battery Optimization","confidence":int(self._num(batt.get("optimizer_confidence_percent"),65))},
            {"id":"battery_action","question":"What should the battery do now?","answer":f"Recommended strategy: {batt.get('strategy') or 'Hold and monitor'}. Recommended action: {batt.get('recommended_action') or 'No manual change indicated'}. Reserve recommendation: {batt.get('recommended_reserve_percent','—')}%.","source":"Predictive Battery Optimization","confidence":int(self._num(batt.get("optimizer_confidence_percent"),65))},
            {"id":"solar","question":"Is there solar surplus now?","answer":solar_answer,"source":"Live Energy Flow","confidence":90},
            {"id":"generation_mix","question":"Which generation source is supporting the site?","answer":(
                ("Current local generation is " + f"{generation:.0f} W. " +
                 ", ".join(f"{name.title()} {self._num(source_mix.get(name)):.1f}%" for name in ("solar","wind","generator") if source_mix.get(name) is not None) +
                 ". Today: " + ", ".join(f"{name.title()} {self._num(source_energy.get(name)):.2f} kWh" for name in ("solar","wind","generator") if source_energy.get(name) is not None) + ".")
                if generation > 0 or source_mix else
                "No active local generation source is currently measured. Zeus will preserve Solar, Wind and Generator separately when configured."
            ),"source":"Canonical Multi-source Energy Flow","confidence":92},
            {"id":"cost","question":"How much did energy save today?","answer":f"Today’s calculated net energy benefit is {self._money(net_benefit,currency)}. Open Finance for the import cost, export revenue and solar value breakdown.","source":"Finance Engine","confidence":int(self._num(fin.get("confidence_percent"),60))},
            {"id":"period_week","question":"How am I doing this week?","answer":self._period_answer("this week", week, fin, currency),"source":"Canonical Week + Finance Tariffs","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"period_month","question":"How am I doing this month?","answer":self._period_answer("this month", month, fin, currency),"source":"Canonical Month + Finance Tariffs","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"period_year","question":"How am I doing this year?","answer":self._period_answer("this year", year, fin, currency),"source":"Canonical Year + Finance Tariffs","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"battery_week","question":"How much did the battery save me this week?","answer":self._period_battery_answer("this week", week, fin, currency),"source":"Canonical Week Battery + Finance Tariff","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"battery_month","question":"How much did the battery save me this month?","answer":self._period_battery_answer("this month", month, fin, currency),"source":"Canonical Month Battery + Finance Tariff","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"grid_month","question":"Why is my grid cost high this month?","answer":self._period_answer("this month", month, fin, currency) + " Higher grid cost follows measured import volume and the configured import tariff; Copilot does not invent a separate cost total.","source":"Canonical Month + Finance Tariffs","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"yesterday","question":"Why was grid import high yesterday?","answer":self._historical_grid_import_answer(yesterday, learn),"source":"Measured History + Seasonal Learning","confidence":self._historical_grid_import_confidence(yesterday, learn)},
            {"id":"learning","question":"What has Zeus learned about my home?","answer":f"Learning confidence is {learn.get('confidence_percent',0)}%. Zeus has {learn.get('seasonal_history_months',0)} monthly seasonal profile(s) and uses weekday, monthly and seasonal demand patterns for recommendations.","source":"Long-Term Learning","confidence":int(self._num(learn.get("confidence_percent"),45))},
            {"id":"devices","question":"Which devices can Zeus consider?","answer":("Registered enabled devices include: " + ", ".join(device_names) + "." if device_names else "No enabled controllable-load records are currently available in the Zeus registry."),"source":"Device Registry","confidence":95},
            {"id":"next","question":"What should I do next?","answer":adv.get("summary") or "No urgent action is needed. Zeus will continue monitoring for a stronger opportunity.","source":"AI Energy Advisor","confidence":int(self._num(adv.get("learning_confidence_percent"),60))},
            {"id":"weather_impact","question":"How is weather affecting my solar production?","answer":str((weather_intel.get("weather_impact") or {}).get("summary") or "Zeus is collecting weather and solar history before estimating the impact."),"source":"Weather Intelligence","confidence":min(95, 45 + int(weather_intel.get("day_count") or 0) * 3)},
            {"id":"weather_match","question":"Which past day had weather similar to today?","answer":((lambda match: f"The closest stored match is {match.get('date','unknown')} at {match.get('similarity_percent','—')}% similarity, with {self._num(match.get('solar_energy_kwh')):.2f} kWh solar production." if match else "No similar historical weather day is available yet.")((weather_intel.get("similar_days") or [None])[0])),"source":"Weather History + Memory","confidence":min(95, 40 + int(weather_intel.get("day_count") or 0) * 3)},
            {"id":"source_now","question":"Where is my home energy coming from right now?","answer":f"Home demand is about {home:.0f} W. Local generation is {generation:.0f} W (solar {solar:.0f} W, wind {wind:.0f} W, generator {generator:.0f} W), with grid import {imp:.0f} W and export {exp:.0f} W.","source":"Canonical Energy Flow","confidence":92},
            {"id":"registered_coverage","question":"How much of my home consumption is covered by registered loads?","answer":((lambda d: f"Today registered loads account for {self._num(d.get('registered_consumption_kwh')):.2f} kWh of measured device consumption. Zeus reconciles registered-load history against whole-home demand so registered consumption cannot exceed the same-period home total." if d else "Registered-load period data is still being prepared.")(((device_summary.get('periods') or {}).get('today') if isinstance(device_summary.get('periods'),dict) else {}) or {})),"source":"Device Analytics + Accounting Integrity","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"battery_value","question":"How much value did the battery provide today?","answer":f"Measured battery support to the home is {self._num(fin.get('battery_support_to_home_kwh')):.2f} kWh, worth {self._money(fin.get('battery_support_value_today'),currency)} in avoided grid purchases at the configured tariff.","source":"Canonical Finance","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"solar_value","question":"How much direct solar did my home use today?","answer":f"Direct solar supplied {self._num(fin.get('direct_solar_to_home_kwh')):.2f} kWh to the home today, valued at {self._money(fin.get('solar_value_today'),currency)} using the configured import tariff.","source":"Canonical Finance","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"grid_cost","question":"What has grid energy cost me today?","answer":f"Measured grid import is {self._num(fin.get('grid_import_kwh')):.2f} kWh and the calculated grid energy cost is {self._money(fin.get('grid_cost_today'),currency)} at the configured tariff.","source":"Canonical Finance","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"export_value","question":"How much have I earned from export today?","answer":f"Measured grid export is {self._num(fin.get('grid_export_kwh')):.2f} kWh and calculated export revenue is {self._money(fin.get('export_revenue_today'),currency)} at the configured export tariff.","source":"Canonical Finance","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"accounting_confidence","question":"How confident is Zeus in my accounting data?","answer":f"Accounting confidence is {accounting_conf.get('label','Waiting')} at {accounting_conf.get('percent','—')}%. {accounting_conf.get('meaning','Zeus is still evaluating accounting evidence.')}","source":"Accounting Confidence","confidence":int(self._num(accounting_conf.get('percent'),70))},
            {"id":"trusted_start","question":"What is my trusted Zeus data start?","answer":accounting_conf.get('epoch_note') or "Zeus is using all available Home Assistant history.","source":"Central Period Authority","confidence":98},
            {"id":"period_quality","question":"Are my Today, Week, Month and Year periods trustworthy?","answer":((lambda pq: "Period quality: " + ", ".join(f"{name.title()} {str((pq.get(name) or {}).get('status') or 'Waiting')}" + (" (epoch-limited)" if (pq.get(name) or {}).get('epoch_limited') else "") for name in ('today','week','month','year')) + "." if pq else "Period-quality diagnostics are still being prepared.")(accounting_conf.get('period_quality') if isinstance(accounting_conf.get('period_quality'),dict) else {})),"source":"Central Period Authority + Diagnostics","confidence":int(self._num(accounting_conf.get('percent'),75))},
            {"id":"reconciliation","question":"Did Zeus have to reconcile any energy data?","answer":((lambda ev: (f"Yes. Zeus reports {len(ev)} recent reconciliation event(s). The accounting values remain bounded by the same-period whole-home measurements." if ev else "No reconciliation intervention is currently reported in Accounting Confidence."))(accounting_conf.get('reconciliation_events') if isinstance(accounting_conf.get('reconciliation_events'),list) else [])),"source":"Accounting Integrity Diagnostics","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"finance_explain","question":"How is my net energy benefit calculated?","answer":f"Zeus combines avoided grid purchases from direct solar and battery support with export revenue, then subtracts grid energy cost and any configured standing charge. Today the calculated net energy benefit is {self._money(net_benefit,currency)}.","source":"Canonical Finance Reconciliation","confidence":int(self._num(accounting_conf.get('percent'),80))},
            {"id":"performance_improve","question":"How can I improve my energy performance?","answer":performance_advice,"source":"Canonical Energy Performance + Recommendation Logic","confidence":min(92,int(self._num(accounting_conf.get('percent'),80)))},
            {"id":"data_limits","question":"Is anything limiting the accuracy of my Zeus data?","answer":((lambda limits: "Current accounting limits: " + "; ".join(str(x) for x in limits) + "." if limits else "No active accounting data limits are currently reported.")(accounting_conf.get('active_limits') if isinstance(accounting_conf.get('active_limits'),list) else [])),"source":"Diagnostics & Confidence","confidence":int(self._num(accounting_conf.get('percent'),80))},
        ]
        # v14.0.0-alpha.22.9.3: make Copilot confidence-aware without creating
        # a second confidence engine.  Period answers inherit the exact canonical
        # Accounting Confidence / period-quality evidence already used by QA.
        confidence_periods = {
            "period_week": "week", "battery_week": "week",
            "period_month": "month", "battery_month": "month", "grid_month": "month",
            "period_year": "year",
            "registered_coverage": "today", "battery_value": "today",
            "solar_value": "today", "grid_cost": "today", "export_value": "today",
            "finance_explain": "today",
        }
        for q in qas:
            period_name = confidence_periods.get(str(q.get("id") or ""))
            if period_name:
                q["answer"] = str(q.get("answer") or "") + self._confidence_note(accounting_conf, period_name)
            q["confidence"] = max(0, min(100, int(q["confidence"])))
        # Keep attributes compact and stable for Recorder.
        self.last = {
            "status":"Ready",
            "mode":"conversational_recommendation_only",
            "headline":adv.get("headline") or "Ask Zeus about your energy system",
            "answer_count":len(qas),
            "suggested_questions":[q["question"] for q in qas[:8]],
            "answers":qas,
            "context_summary":{"solar_w":round(solar),"home_w":round(home),"grid_import_w":round(imp),"grid_export_w":round(exp),"battery_soc_percent":None if soc<0 else round(soc,1),"battery_charge_w":round(charge_w),"battery_discharge_w":round(discharge_w),"battery_status":battery_status,"battery_status_w":round(battery_status_w),"best_window":best_window,"registered_devices":len(devices),"currency":currency,"confidence_percent":int(self._num(accounting_conf.get("percent"),0)),"confidence_label":str(accounting_conf.get("label") or "Waiting"),"confidence_limits":list(accounting_conf.get("active_limits") or [])[:3]},
            "supported_topics":["live energy flow","source attribution","battery","EV charging","scheduler","planning","devices","registered-load coverage","registered-device period attribution","functional load roles","solar forecast","forecast trust","adaptive correction","tariffs and finance","finance reconciliation","period intelligence","today week month year total accounting","self-consumption","self-sufficiency","battery charge and discharge statistics","history","period authority","data epoch","accounting confidence","reconciliation diagnostics","system health","QA diagnostics","data quality","release readiness","energy performance","root cause","system story","recommendation priority","learning and seasons","weather context","weather impact","similar weather days"],
            "details_entities":{"flow":"sensor.aion_ems_zeus_energy_flow","scheduler":"sensor.aion_ems_zeus_scheduler_preview","battery":"sensor.aion_ems_zeus_predictive_battery","finance":"sensor.aion_ems_zeus_finance_summary","learning":"sensor.aion_ems_zeus_seasonal_analysis"},
            "safety":"Recommendation only. The assistant explains data and suggests actions but never calls device, battery or inverter services.",
            "recorder_safe":True,
        }
        self.event_bus.publish("ConversationalAssistantUpdated","ConversationalZeusAssistant",{"answers":len(qas),"mode":"recommendation_only"})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
