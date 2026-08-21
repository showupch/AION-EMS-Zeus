"""Proactive Hyper Analytics for AION EMS Zeus.

The engine converts recorder-safe daily summaries and recent snapshots into
plain-language discoveries, anomalies, opportunity estimates and a compact
"house DNA" profile. It is strictly read-only and recommendation-only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any


class HyperAnalyticsEngine:
    """Find useful patterns users would otherwise have to discover manually."""

    def __init__(self, event_bus, data_lake, analytics, finance, forecast, learning) -> None:
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.analytics = analytics
        self.finance = finance
        self.forecast = forecast
        self.learning = learning
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "headline": "Zeus is learning your house.",
            "discoveries": [],
            "anomalies": [],
            "opportunities": [],
            "house_dna": {},
            "timeline": [],
            "confidence": 0,
            "safety": "Recommendation only. No device control.",
        }

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _pct(current: float, baseline: float) -> float | None:
        if baseline <= 0:
            return None
        return round((current - baseline) / baseline * 100, 1)

    @staticmethod
    def _average(rows: list[dict[str, Any]], key: str) -> float:
        vals = [HyperAnalyticsEngine._num(r.get(key)) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def _night_baseload(self, snapshots: list[dict[str, Any]]) -> float | None:
        values: list[float] = []
        for snap in snapshots[-10080:]:
            raw = snap.get("timestamp")
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if dt.hour not in (0, 1, 2, 3, 4):
                continue
            flows = snap.get("flows", {}) or {}
            value = flows.get("house_power_w")
            if isinstance(value, (int, float)) and 0 <= value < 10000:
                values.append(float(value))
        return round(median(values), 0) if values else None

    def _time_profile(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        """Build compact house timing fingerprints from recent live snapshots."""
        hourly_loads: dict[int, list[float]] = defaultdict(list)
        export_starts: list[int] = []
        battery_full_times: list[int] = []
        by_day: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        for snap in snapshots[-20160:]:
            try:
                dt = datetime.fromisoformat(str(snap.get("timestamp", "")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            flows = snap.get("flows", {}) or {}
            load = self._num(flows.get("house_power_w"), -1)
            if 0 <= load < 20000:
                hourly_loads[dt.hour].append(load)
            by_day[dt.date().isoformat()].append((dt, flows))
        for day_rows in by_day.values():
            day_rows.sort(key=lambda x: x[0])
            first_export = next((dt for dt, f in day_rows if self._num(f.get("grid_export_power_w")) > 100), None)
            first_full = next((dt for dt, f in day_rows if self._num(f.get("battery_soc_percent"), -1) >= 99), None)
            if first_export:
                export_starts.append(first_export.hour * 60 + first_export.minute)
            if first_full:
                battery_full_times.append(first_full.hour * 60 + first_full.minute)
        averages = {hour: sum(vals) / len(vals) for hour, vals in hourly_loads.items() if vals}
        morning = {h:v for h,v in averages.items() if 5 <= h <= 11}
        evening = {h:v for h,v in averages.items() if 16 <= h <= 23}
        fmt = lambda mins: f"{int(mins)//60:02d}:{int(mins)%60:02d}" if mins is not None else None
        return {
            "morning_peak": f"{max(morning, key=morning.get):02d}:00" if morning else None,
            "evening_peak": f"{max(evening, key=evening.get):02d}:00" if evening else None,
            "typical_export_start": fmt(median(export_starts)) if export_starts else None,
            "typical_battery_full": fmt(median(battery_full_times)) if battery_full_times else None,
            "hourly_samples": sum(len(v) for v in hourly_loads.values()),
        }

    def refresh(self) -> dict[str, Any]:
        daily_map = self.data_lake.data.get("daily_summaries", {}) or {}
        rows = [dict(daily_map[key], date=key) for key in sorted(daily_map)]
        snapshots = self.data_lake.data.get("snapshots", []) or []
        finance = self.finance.summary() or {}
        forecast = self.forecast.summary() or {}
        learning = self.learning.summary() or {}

        if not rows:
            self.last = {
                **self.last,
                "status": "Waiting",
                "headline": "Zeus is collecting the first days of history.",
                "confidence": 0,
                "history_days": 0,
            }
            return self.last

        recent = rows[-30:]
        previous = rows[-60:-30]
        last7 = rows[-7:]
        prev7 = rows[-14:-7]
        latest = rows[-1]

        weekday: dict[int, list[dict[str, Any]]] = defaultdict(list)
        monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            try:
                day = datetime.fromisoformat(str(row["date"])).date()
            except (KeyError, TypeError, ValueError):
                continue
            weekday[day.weekday()].append(row)
            monthly[day.strftime("%Y-%m")].append(row)

        weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        weekday_profiles = []
        for index, values in weekday.items():
            weekday_profiles.append({
                "weekday": weekday_names[index],
                "solar_kwh": self._average(values, "solar_energy_kwh"),
                "consumption_kwh": self._average(values, "house_energy_kwh"),
                "import_kwh": self._average(values, "grid_import_energy_kwh"),
                "days": len(values),
            })

        best_solar_weekday = max(weekday_profiles, key=lambda x: x["solar_kwh"], default={})
        highest_load_weekday = max(weekday_profiles, key=lambda x: x["consumption_kwh"], default={})
        cheapest_weekday = min(weekday_profiles, key=lambda x: x["import_kwh"], default={})
        best_day = max(rows, key=lambda r: self._num(r.get("solar_energy_kwh")), default={})
        highest_day = max(rows, key=lambda r: self._num(r.get("house_energy_kwh")), default={})
        best_export = max(rows, key=lambda r: self._num(r.get("grid_export_energy_kwh")), default={})

        avg_recent_solar = self._average(recent, "solar_energy_kwh")
        avg_recent_home = self._average(recent, "house_energy_kwh")
        avg_recent_import = self._average(recent, "grid_import_energy_kwh")
        avg_recent_export = self._average(recent, "grid_export_energy_kwh")
        avg_previous_solar = self._average(previous, "solar_energy_kwh")
        avg_previous_home = self._average(previous, "house_energy_kwh")
        avg_previous_import = self._average(previous, "grid_import_energy_kwh")
        avg7_home = self._average(last7, "house_energy_kwh")
        prev7_home = self._average(prev7, "house_energy_kwh")
        baseload = self._night_baseload(snapshots)
        time_profile = self._time_profile(snapshots)

        discoveries: list[dict[str, Any]] = []
        if best_solar_weekday:
            discoveries.append({
                "type": "pattern",
                "title": f"{best_solar_weekday['weekday']} is your strongest solar day",
                "detail": f"It averages {best_solar_weekday['solar_kwh']:.2f} kWh across {best_solar_weekday['days']} measured days.",
                "impact": "Useful for scheduling flexible loads.",
            })
        if highest_load_weekday:
            discoveries.append({
                "type": "pattern",
                "title": f"{highest_load_weekday['weekday']} has the highest demand",
                "detail": f"Average consumption is {highest_load_weekday['consumption_kwh']:.2f} kWh.",
                "impact": "Review recurring loads on this weekday.",
            })
        if cheapest_weekday:
            discoveries.append({
                "type": "pattern",
                "title": f"{cheapest_weekday['weekday']} is your least grid-dependent day",
                "detail": f"Average grid import is {cheapest_weekday['import_kwh']:.2f} kWh.",
                "impact": "This profile is a good model for other days.",
            })
        if baseload is not None:
            discoveries.append({
                "type": "house_dna",
                "title": f"Your overnight base load is about {baseload:.0f} W",
                "detail": "Measured from the median household demand between midnight and 05:00.",
                "impact": "A rising base load can reveal hidden always-on consumption.",
            })
        if time_profile.get("typical_export_start"):
            discoveries.append({
                "type": "timing",
                "title": f"Export typically begins around {time_profile['typical_export_start']}",
                "detail": "Zeus detected the first sustained daily export from recent live snapshots.",
                "impact": "Schedule flexible loads shortly before this time to capture more solar locally.",
            })
        if time_profile.get("typical_battery_full"):
            discoveries.append({
                "type": "timing",
                "title": f"Your battery typically reaches full around {time_profile['typical_battery_full']}",
                "detail": "This is the median first full-battery time on measured days.",
                "impact": "Loads after this point can reduce low-value export.",
            })

        anomalies: list[dict[str, Any]] = []
        latest_home = self._num(latest.get("house_energy_kwh"))
        latest_solar = self._num(latest.get("solar_energy_kwh"))
        latest_import = self._num(latest.get("grid_import_energy_kwh"))
        for title, value, baseline, unit, direction in (
            ("Consumption", latest_home, avg_recent_home, "kWh", "higher"),
            ("Solar production", latest_solar, avg_recent_solar, "kWh", "different"),
            ("Grid import", latest_import, avg_recent_import, "kWh", "higher"),
        ):
            change = self._pct(value, baseline)
            if change is not None and abs(change) >= 25:
                anomalies.append({
                    "title": f"{title} is {abs(change):.0f}% {'above' if change > 0 else 'below'} normal",
                    "detail": f"Latest day: {value:.2f} {unit}; recent average: {baseline:.2f} {unit}.",
                    "severity": "attention" if (direction == "higher" and change > 0) else "information",
                })
        weekly_change = self._pct(avg7_home, prev7_home)
        if weekly_change is not None and abs(weekly_change) >= 10:
            anomalies.append({
                "title": f"Seven-day demand changed by {weekly_change:+.1f}%",
                "detail": f"Current daily average is {avg7_home:.2f} kWh versus {prev7_home:.2f} kWh previously.",
                "severity": "attention" if weekly_change > 0 else "positive",
            })

        import_tariff = self._num(finance.get("import_tariff") or finance.get("tariffs", {}).get("import"), 0.0)
        export_tariff = self._num(finance.get("export_tariff") or finance.get("tariffs", {}).get("export"), 0.0)
        flexible_export = max(avg_recent_export * 0.30, 0)
        annual_shift_kwh = flexible_export * 365
        annual_value = annual_shift_kwh * max(import_tariff - export_tariff, 0)
        opportunities: list[dict[str, Any]] = []
        if avg_recent_export > 0.5:
            opportunities.append({
                "title": "Use more midday solar locally",
                "detail": f"You export about {avg_recent_export:.2f} kWh per day. Shifting 30% into flexible loads could use {flexible_export:.2f} kWh/day locally.",
                "estimated_annual_value": round(annual_value, 2),
                "confidence": min(95, 45 + len(recent)),
            })
        best_window = forecast.get("best_surplus_window") or {}
        if best_window:
            label = best_window.get("label") or best_window.get("start") or "the predicted surplus window"
            opportunities.append({
                "title": "Schedule flexible loads in the forecast surplus window",
                "detail": f"The current best opportunity is {label}.",
                "estimated_annual_value": None,
                "confidence": forecast.get("confidence", 0),
            })
        if baseload and baseload > 300:
            reduction = baseload * 0.10 / 1000 * 24 * 365
            opportunities.append({
                "title": "Investigate always-on consumption",
                "detail": f"A 10% reduction of the {baseload:.0f} W base load would save about {reduction:.0f} kWh/year.",
                "estimated_annual_value": round(reduction * import_tariff, 2),
                "confidence": min(90, 40 + len(rows)),
            })

        month_timeline = []
        for month, values in list(sorted(monthly.items()))[-12:]:
            month_timeline.append({
                "month": month,
                "solar_kwh": round(sum(self._num(r.get("solar_energy_kwh")) for r in values), 2),
                "consumption_kwh": round(sum(self._num(r.get("house_energy_kwh")) for r in values), 2),
                "import_kwh": round(sum(self._num(r.get("grid_import_energy_kwh")) for r in values), 2),
                "export_kwh": round(sum(self._num(r.get("grid_export_energy_kwh")) for r in values), 2),
                "story": f"Generated {sum(self._num(r.get('solar_energy_kwh')) for r in values):.1f} kWh and imported {sum(self._num(r.get('grid_import_energy_kwh')) for r in values):.1f} kWh.",
            })

        confidence = min(99, int(30 + len(rows) * 2 + min(len(snapshots) / 500, 20)))
        house_dna = {
            "history_days": len(rows),
            "average_solar_kwh": avg_recent_solar,
            "average_consumption_kwh": avg_recent_home,
            "average_grid_import_kwh": avg_recent_import,
            "average_grid_export_kwh": avg_recent_export,
            "overnight_baseload_w": baseload,
            "morning_peak": time_profile.get("morning_peak"),
            "evening_peak": time_profile.get("evening_peak"),
            "typical_export_start": time_profile.get("typical_export_start"),
            "typical_battery_full": time_profile.get("typical_battery_full"),
            "best_solar_weekday": best_solar_weekday.get("weekday"),
            "highest_consumption_weekday": highest_load_weekday.get("weekday"),
            "least_grid_dependent_weekday": cheapest_weekday.get("weekday"),
            "best_solar_day": {"date": best_day.get("date"), "kwh": self._num(best_day.get("solar_energy_kwh"))},
            "highest_consumption_day": {"date": highest_day.get("date"), "kwh": self._num(highest_day.get("house_energy_kwh"))},
            "best_export_day": {"date": best_export.get("date"), "kwh": self._num(best_export.get("grid_export_energy_kwh"))},
        }

        # Energy IQ measures improvement against this home's own recent baseline.
        latest_self_suff = max(0.0, min(100.0, 100.0 - (latest_import / latest_home * 100.0 if latest_home > 0 else 0.0)))
        import_trend = self._pct(avg_recent_import, avg_previous_import)
        demand_trend = self._pct(avg_recent_home, avg_previous_home)
        iq = 55.0 + latest_self_suff * 0.30 + min(confidence, 100) * 0.15
        if import_trend is not None:
            iq += max(-10.0, min(10.0, -import_trend * 0.20))
        if demand_trend is not None and demand_trend < 0:
            iq += min(5.0, abs(demand_trend) * 0.10)
        energy_iq = int(max(0, min(100, round(iq))))
        iq_grade = "A+" if energy_iq >= 95 else "A" if energy_iq >= 88 else "B" if energy_iq >= 76 else "C" if energy_iq >= 62 else "D"

        what_changed = []
        for label, current, old, lower_is_better in (
            ("Solar", avg_recent_solar, avg_previous_solar, False),
            ("Demand", avg_recent_home, avg_previous_home, True),
            ("Grid import", avg_recent_import, avg_previous_import, True),
        ):
            change = self._pct(current, old)
            if change is not None:
                what_changed.append({"label": label, "percent": change, "positive": change <= 0 if lower_is_better else change >= 0})

        proactive_prompts = []
        if anomalies:
            proactive_prompts.append({"title": "Would you like to know what changed?", "question": f"Why did {anomalies[0]['title'].lower()}?"})
        if opportunities:
            proactive_prompts.append({"title": "I found a savings opportunity", "question": opportunities[0]["title"]})
        if best_day:
            proactive_prompts.append({"title": "You have a new historical reference", "question": f"What made {best_day.get('date')} a strong solar day?"})
        proactive_prompts = proactive_prompts[:3]

        headline = discoveries[0]["title"] if discoveries else "Zeus found new patterns in your energy history."
        self.last = {
            "status": "Ready",
            "headline": headline,
            "history_days": len(rows),
            "confidence": confidence,
            "confidence_label": "High" if confidence >= 80 else "Medium" if confidence >= 55 else "Low",
            "energy_iq": energy_iq,
            "energy_iq_grade": iq_grade,
            "what_changed": what_changed,
            "proactive_prompts": proactive_prompts,
            "discoveries": discoveries[:8],
            "anomalies": anomalies[:5],
            "opportunities": opportunities[:5],
            "house_dna": house_dna,
            "weekday_profiles": weekday_profiles,
            "timeline": month_timeline,
            "trend_30_day": {
                "solar_percent": self._pct(avg_recent_solar, avg_previous_solar),
                "consumption_percent": self._pct(avg_recent_home, avg_previous_home),
                "grid_import_percent": self._pct(avg_recent_import, avg_previous_import),
            },
            "summary": f"{headline} Zeus analysed {len(rows)} days of history with {confidence}% confidence.",
            "safety": "Recommendation only. Hyper Analytics never controls devices.",
            "recorder_safe": True,
        }
        self.event_bus.publish("HyperAnalyticsUpdated", "HyperAnalyticsEngine", {"history_days": len(rows), "discoveries": len(discoveries)})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
