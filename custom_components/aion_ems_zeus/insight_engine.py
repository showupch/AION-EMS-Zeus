"""Evidence-first insight intelligence for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.util import dt as dt_util
from statistics import mean, pstdev
from typing import Any


class InsightIntelligenceEngine:
    """Detect meaningful changes and anomalies without executing actions."""

    TREND_DAYS_REQUIRED = 6
    BASELINE_DAYS_REQUIRED = 5

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core
        self._summary: dict[str, Any] = {
            "status": "Collecting",
            "recommendation_only": True,
            "briefing": {},
            "insights": [],
            "anomalies": [],
            "trends": [],
            "normal_evidence": [],
            "trend_progress": {},
            "evidence_days": 0,
        }

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            value = float(value)
            return value if value == value else default
        except (TypeError, ValueError):
            return default

    def _rows(self) -> list[dict[str, Any]]:
        history = self.core.history.summary() or {}
        chart = history.get("chart_history") if isinstance(history.get("chart_history"), dict) else {}
        candidates = chart.get("month") or history.get("last_7_days") or []
        rows = [dict(row) for row in candidates if isinstance(row, dict)]
        rows.sort(key=lambda row: str(row.get("date") or ""))
        return rows[-31:]


    def _mapped_daily_value(self, mapping_key: str) -> tuple[float | None, str | None]:
        """Read an authoritative mapped daily-energy sensor in kWh."""
        mapping = getattr(self.core, "energy_mapping", None)
        mappings = getattr(mapping, "mappings", {}) if mapping is not None else {}
        entity_id = mappings.get(mapping_key) if isinstance(mappings, dict) else None
        state = self.core.hass.states.get(entity_id) if entity_id else None
        if state is None or str(state.state).strip().lower() in {"", "unknown", "unavailable", "none"}:
            return None, entity_id
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None, entity_id
        unit = str(state.attributes.get("unit_of_measurement") or "kWh").strip().lower()
        if unit == "wh":
            value /= 1000.0
        elif unit == "mwh":
            value *= 1000.0
        elif unit not in {"kwh", "kilowatt-hour", "kilowatt-hours"}:
            return None, entity_id
        return max(0.0, value), entity_id

    def _authoritative_today(self) -> dict[str, Any] | None:
        """Return the shared current-day snapshot used across Zeus intelligence."""
        service = getattr(self.core, "energy_snapshot", None)
        snapshot = service.summary() if service is not None else {}
        if not isinstance(snapshot, dict) or not snapshot.get("authoritative"):
            return None
        required = ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh")
        if not any(snapshot.get(key) is not None for key in required):
            return None
        return dict(snapshot)

    def _story_row(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Select authoritative current-day totals or the latest completed history row."""
        today_key = dt_util.now().date().isoformat()
        measured_today = self._authoritative_today()
        if measured_today is not None:
            return measured_today
        completed = [row for row in rows if str(row.get("date") or "")[:10] < today_key]
        if completed:
            row = dict(completed[-1])
            row["authoritative"] = True
            row["day_state"] = "completed"
            return row
        return None

    @staticmethod
    def _metric(row: dict[str, Any], name: str) -> float:
        aliases = {
            "solar": ("solar_energy_kwh", "solar_kwh"),
            "consumption": ("house_energy_kwh", "consumption_energy_kwh", "consumption_kwh"),
            "import": ("grid_import_energy_kwh", "grid_import_kwh"),
            "export": ("grid_export_energy_kwh", "grid_export_kwh"),
            "charge": ("battery_charge_energy_kwh", "battery_charge_kwh"),
            "discharge": ("battery_discharge_energy_kwh", "battery_discharge_kwh"),
        }
        for key in aliases[name]:
            if row.get(key) is not None:
                try:
                    return max(0.0, float(row[key]))
                except (TypeError, ValueError):
                    pass
        return 0.0


    @staticmethod
    def _optional_metric(row: dict[str, Any], name: str) -> float | None:
        """Return a measured daily metric without converting missing evidence to zero."""
        aliases = {
            "solar": ("solar_energy_kwh", "solar_kwh"),
            "consumption": ("house_energy_kwh", "consumption_energy_kwh", "consumption_kwh"),
            "import": ("grid_import_energy_kwh", "grid_import_kwh"),
            "export": ("grid_export_energy_kwh", "grid_export_kwh"),
            "charge": ("battery_charge_energy_kwh", "battery_charge_kwh"),
            "discharge": ("battery_discharge_energy_kwh", "battery_discharge_kwh"),
        }
        for key in aliases[name]:
            value = row.get(key)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number and number >= 0:
                return number
        return None

    def _completed_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use completed local-calendar days only; never let today's partial totals drive insights."""
        today_key = dt_util.now().date().isoformat()
        completed = [row for row in rows if str(row.get("date") or "")[:10] < today_key]
        return completed[-14:]

    def _energy_trend_bundle(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Mirror the canonical Statistics trend window for Energy Insights."""
        completed = self._completed_rows(rows)
        canonical_names = ("solar", "consumption", "import", "export", "charge", "discharge")
        usable = [row for row in completed if any(self._optional_metric(row, name) is not None for name in canonical_names)]
        if len(usable) < self.TREND_DAYS_REQUIRED:
            return {
                "available": False,
                "evidence_days": len(usable),
                "required_days": self.TREND_DAYS_REQUIRED,
                "remaining_days": max(0, self.TREND_DAYS_REQUIRED - len(usable)),
                "note": "Zeus needs at least 6 completed measured days. Today is excluded so a partial day cannot distort Energy Insights.",
            }

        half = len(usable) // 2
        previous_rows = usable[:half]
        recent_rows = usable[len(usable) - half:]

        def average(list_rows: list[dict[str, Any]], metric: str) -> float | None:
            values = [self._optional_metric(row, metric) for row in list_rows]
            valid = [value for value in values if value is not None]
            return mean(valid) if valid else None

        def self_sufficiency(row: dict[str, Any]) -> float | None:
            home = self._optional_metric(row, "consumption")
            solar = self._optional_metric(row, "solar")
            exported = self._optional_metric(row, "export")
            discharge = self._optional_metric(row, "discharge")
            if home is None or home <= 0 or solar is None or exported is None or discharge is None:
                return None
            direct = min(max(solar - exported, 0.0), home)
            return max(0.0, min(100.0, min(home, direct + discharge) / home * 100.0))

        def average_self_sufficiency(list_rows: list[dict[str, Any]]) -> float | None:
            values = [self_sufficiency(row) for row in list_rows]
            valid = [value for value in values if value is not None]
            return mean(valid) if valid else None

        def average_battery_use(list_rows: list[dict[str, Any]]) -> float | None:
            values = []
            for row in list_rows:
                charge = self._optional_metric(row, "charge")
                discharge = self._optional_metric(row, "discharge")
                if charge is not None and discharge is not None:
                    values.append(charge + discharge)
            return mean(values) if values else None

        def average_solar_self_use(list_rows: list[dict[str, Any]]) -> float | None:
            values = []
            for row in list_rows:
                solar = self._optional_metric(row, "solar")
                exported = self._optional_metric(row, "export")
                if solar is not None and exported is not None and solar > 0:
                    values.append(max(0.0, min(100.0, (solar - min(solar, exported)) / solar * 100.0)))
            return mean(values) if values else None

        def build(label: str, previous: float | None, recent: float | None, unit: str) -> dict[str, Any]:
            if previous is None or recent is None:
                return {"label": label, "available": False, "missing_evidence": True}
            absolute = recent - previous
            pct = (absolute / previous * 100.0) if previous > 0 else None
            threshold = 1.0 if unit == "%" else max(0.05, abs(previous) * 0.03)
            direction = "Stable" if abs(absolute) < threshold else "Rising" if absolute > 0 else "Falling"
            return {
                "label": label, "available": True, "previous": round(previous, 3), "recent": round(recent, 3),
                "absolute": round(absolute, 3), "pct": round(pct, 1) if pct is not None else None,
                "direction": direction, "unit": unit,
            }

        trends = {
            "solar": build("Solar production", average(previous_rows, "solar"), average(recent_rows, "solar"), "kWh/day"),
            "consumption": build("Consumption", average(previous_rows, "consumption"), average(recent_rows, "consumption"), "kWh/day"),
            "grid_import": build("Grid import", average(previous_rows, "import"), average(recent_rows, "import"), "kWh/day"),
            "grid_export": build("Grid export", average(previous_rows, "export"), average(recent_rows, "export"), "kWh/day"),
            "self_sufficiency": build("Self-sufficiency", average_self_sufficiency(previous_rows), average_self_sufficiency(recent_rows), "%"),
            "battery_use": build("Battery use", average_battery_use(previous_rows), average_battery_use(recent_rows), "kWh/day"),
            "solar_self_use": build("Solar self-use", average_solar_self_use(previous_rows), average_solar_self_use(recent_rows), "%"),
        }
        complete_metrics = sum(1 for key in ("solar", "consumption", "grid_import", "self_sufficiency", "battery_use") if trends[key].get("available"))
        confidence = "High" if complete_metrics == 5 and len(usable) >= 6 else "Moderate" if complete_metrics >= 3 else "Limited"
        return {
            "available": True, "evidence_days": len(usable), "previous_days": len(previous_rows), "recent_days": len(recent_rows),
            "today_excluded": True, "confidence": confidence, "complete_metric_count": complete_metrics, "trends": trends,
            "note": f"Energy Insights compare two equal completed-history windows ({len(previous_rows)} vs {len(recent_rows)} days). Today is excluded.",
        }

    def _energy_insights(self, bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Synthesize cross-domain energy meaning without claiming unsupported causes."""
        boundary = "Measured relationships describe the energy pattern but do not prove the underlying cause."
        if not bundle.get("available"):
            remaining = int(bundle.get("remaining_days", self.TREND_DAYS_REQUIRED))
            briefing = {
                "headline": "Energy Insights are collecting measured evidence",
                "summary": bundle.get("note"),
                "importance": "Informational",
                "evidence_confidence": "Collecting",
                "evidence_boundary": boundary,
            }
            return [{
                "category": "Evidence readiness", "importance": "Informational", "title": "Collecting completed measured days",
                "interpretation": f"{remaining} more completed measured day{'s' if remaining != 1 else ''} required before Zeus creates cross-domain Energy Insights.",
                "measured_facts": [f"{bundle.get('evidence_days', 0)} of {self.TREND_DAYS_REQUIRED} completed measured days available.", "The current partial day is excluded."],
                "evidence_period": "Completed measured days only", "evidence_confidence": "Collecting", "why_it_matters": "Waiting prevents partial or missing evidence from being interpreted as a real energy change.",
                "evidence_boundary": boundary,
            }], briefing

        trends = bundle.get("trends", {})
        evidence_period = f"Latest {bundle['recent_days']} completed measured days compared with the previous {bundle['previous_days']} completed measured days."
        confidence = str(bundle.get("confidence") or "Moderate")

        def t(name: str) -> dict[str, Any]:
            value = trends.get(name)
            return value if isinstance(value, dict) else {}

        def available(*names: str) -> bool:
            return all(t(name).get("available") for name in names)

        def fact(name: str) -> str | None:
            item = t(name)
            if not item.get("available"):
                return None
            direction = str(item.get("direction") or "Stable").lower()
            label = str(item.get("label") or name)
            if item.get("unit") == "%":
                if item.get("direction") == "Stable":
                    return f"{label} is stable at about {item.get('recent', 0):.1f}%."
                return f"{label} is {direction} by {abs(item.get('absolute', 0)):.1f} percentage points."
            pct = item.get("pct")
            return f"{label} is {direction}{f' by {abs(pct):.1f}%' if pct is not None else ''}."

        insights: list[dict[str, Any]] = []

        def add(category: str, importance: str, title: str, interpretation: str, names: tuple[str, ...], why: str) -> None:
            facts = [fact(name) for name in names]
            insights.append({
                "category": category, "importance": importance, "title": title, "interpretation": interpretation,
                "measured_facts": [item for item in facts if item], "evidence_period": evidence_period,
                "evidence_confidence": confidence if available(*names) else "Limited", "why_it_matters": why,
                "evidence_boundary": boundary,
            })

        solar, demand, grid, self_suff, battery = t("solar"), t("consumption"), t("grid_import"), t("self_sufficiency"), t("battery_use")

        if available("grid_import", "consumption", "solar"):
            if grid.get("direction") == "Rising":
                importance = "Attention" if demand.get("direction") == "Rising" and solar.get("direction") == "Falling" else "Worth watching"
                add("Grid dependence", importance, "Grid dependence increased", "The recent measured energy balance required more grid support.", ("solar", "consumption", "grid_import"), "More household energy is currently being supplied from imported energy.")
            elif grid.get("direction") == "Falling":
                add("Grid dependence", "Positive", "Grid dependence decreased", "Recent completed days required less measured grid support.", ("solar", "consumption", "grid_import"), "Lower imported energy can indicate stronger local coverage, while the measured trends alone do not establish why it changed.")
            else:
                add("Grid dependence", "Stable", "Grid dependence remained stable", "Measured grid support changed little across the two completed-history windows.", ("grid_import",), "Stable import provides context for changes elsewhere in the energy balance.")

        if available("solar", "solar_self_use", "grid_export"):
            self_use = t("solar_self_use")
            if self_use.get("direction") == "Rising":
                add("Solar utilization", "Positive", "A larger share of solar stayed on site", "Measured solar self-use increased across the recent completed-day window.", ("solar", "solar_self_use", "grid_export"), "A higher local-use share means more measured solar was retained behind the meter rather than exported.")
            elif self_use.get("direction") == "Falling":
                add("Solar utilization", "Worth watching", "A smaller share of solar stayed on site", "Measured solar self-use decreased across the recent completed-day window.", ("solar", "solar_self_use", "grid_export"), "The change affects how solar production is split between local use and export; it does not indicate a solar fault.")
            else:
                add("Solar utilization", "Stable", "Solar utilization remained stable", "The measured local-use share of solar changed little.", ("solar_self_use",), "This separates utilization behavior from the amount of solar generated.")

        if available("consumption", "grid_import"):
            if demand.get("direction") == "Rising":
                add("Demand pressure", "Worth watching" if grid.get("direction") != "Falling" else "Informational", "Household demand increased", "Completed measured days show higher household consumption in the recent window.", ("consumption", "grid_import"), "Higher demand can materially change the balance between local energy and grid support, but the energy totals do not identify which load caused it.")
            elif demand.get("direction") == "Falling":
                add("Demand pressure", "Positive" if grid.get("direction") != "Rising" else "Informational", "Household demand decreased", "Completed measured days show lower household consumption in the recent window.", ("consumption", "grid_import"), "Lower demand changes the amount of energy that must be supplied by solar, battery or grid.")

        if available("battery_use", "grid_import"):
            if battery.get("direction") == "Rising":
                add("Battery support", "Informational", "Battery activity increased", "Measured battery charge/discharge throughput increased in the recent window.", ("battery_use", "grid_import"), "This shows that the battery is participating more in the household energy balance; it is not a battery-health or degradation diagnosis.")
            elif battery.get("direction") == "Falling":
                add("Battery support", "Informational", "Battery activity decreased", "Measured battery charge/discharge throughput decreased in the recent window.", ("battery_use", "grid_import"), "This describes changing battery participation only; it does not establish a fault or health change.")
            else:
                add("Battery support", "Stable", "Battery activity remained stable", "Measured battery throughput changed little across the two completed-history windows.", ("battery_use",), "Stable battery activity helps explain whether wider energy-balance changes occurred with or without a change in battery participation.")

        if available("solar", "consumption", "grid_import", "self_sufficiency", "battery_use"):
            if solar.get("direction") == "Falling" and demand.get("direction") == "Rising" and grid.get("direction") == "Rising":
                add("Energy balance", "Attention", "The recent energy balance became more grid-supported", "Lower solar and higher demand coincide with higher grid import. Battery participation and self-sufficiency provide supporting context.", ("solar", "consumption", "grid_import", "self_sufficiency", "battery_use"), "This is the strongest cross-domain change in the recent measured window and deserves attention without assigning an unsupported cause.")
            elif grid.get("direction") == "Falling" and self_suff.get("direction") == "Rising":
                add("Energy balance", "Positive", "Local energy coverage improved", "Self-sufficiency rose while grid import fell across completed measured days.", ("solar", "consumption", "grid_import", "self_sufficiency", "battery_use"), "The household relied less on imported energy in the recent window.")
            else:
                add("Energy balance", "Informational", "The energy balance changed across several domains", "Zeus sees a supported cross-domain pattern, but the measurements do not reduce to one proven cause.", ("solar", "consumption", "grid_import", "self_sufficiency", "battery_use"), "Viewing the domains together prevents a single metric from being over-interpreted.")

        order = {"Attention": 5, "Worth watching": 4, "Positive": 3, "Informational": 2, "Stable": 1}
        insights.sort(key=lambda item: order.get(str(item.get("importance")), 0), reverse=True)
        insights = insights[:5]
        top = insights[0] if insights else None
        briefing = {
            "headline": top.get("title") if top else "No material cross-domain energy change detected",
            "summary": top.get("interpretation") if top else "Completed measured trends are broadly stable across the current evidence window.",
            "importance": top.get("importance") if top else "Stable",
            "evidence_confidence": confidence, "evidence_period": evidence_period, "evidence_boundary": boundary,
            "insight_count": len(insights),
        }
        return insights, briefing

    @staticmethod
    def _severity(priority: str) -> str:
        return {"High": "Significant change", "Medium": "Worth watching", "Low": "Informational"}.get(priority, "Informational")

    def _trend(self, rows: list[dict[str, Any]], metric: str, label: str, good_when_down: bool = False) -> dict[str, Any] | None:
        if len(rows) < self.TREND_DAYS_REQUIRED:
            return None
        recent = [self._metric(r, metric) for r in rows[-3:]]
        previous = [self._metric(r, metric) for r in rows[-6:-3]]
        prev_avg, recent_avg = mean(previous), mean(recent)
        if prev_avg <= 0:
            return None
        change = (recent_avg - prev_avg) / prev_avg * 100.0
        if abs(change) < 5:
            direction = "Stable"
        else:
            direction = "Increasing" if change > 0 else "Decreasing"
        improvement = change < 0 if good_when_down else change > 0
        confidence = min(92.0, 62.0 + min(len(rows), 10) * 3.0)
        return {
            "metric": metric,
            "label": label,
            "direction": direction,
            "change_percent": round(change, 1),
            "recent_average_kwh": round(recent_avg, 2),
            "previous_average_kwh": round(prev_avg, 2),
            "assessment": "Improving" if abs(change) >= 5 and improvement else "Needs attention" if abs(change) >= 5 else "Stable",
            "severity": "Worth watching" if abs(change) >= 12 else "Informational",
            "confidence_percent": round(confidence),
            "evidence_days": 6,
            "evidence": "Latest 3 measured days compared with the previous 3 measured days.",
            "reasoning": f"{label} changed from {prev_avg:.2f} to {recent_avg:.2f} kWh on average.",
        }

    def _anomaly(self, rows: list[dict[str, Any]], metric: str, label: str) -> dict[str, Any] | None:
        if len(rows) < self.BASELINE_DAYS_REQUIRED:
            return None
        latest = self._metric(rows[-1], metric)
        baseline = [self._metric(r, metric) for r in rows[-8:-1] if self._metric(r, metric) >= 0]
        if len(baseline) < 4:
            return None
        avg = mean(baseline)
        spread = pstdev(baseline) if len(baseline) > 1 else 0.0
        threshold = max(spread * 1.8, avg * 0.35, 1.0)
        delta = latest - avg
        if abs(delta) < threshold:
            return None
        confidence = min(95.0, 60.0 + abs(delta) / max(threshold, 0.1) * 15.0)
        high = abs(delta) >= threshold * 1.7
        return {
            "metric": metric,
            "title": f"Unusual {label.lower()}",
            "severity": "High priority" if high else "Significant change",
            "priority": "High" if high else "Medium",
            "direction": "above" if delta > 0 else "below",
            "latest_kwh": round(latest, 2),
            "baseline_kwh": round(avg, 2),
            "difference_percent": round((delta / avg * 100.0) if avg > 0 else 0.0, 1),
            "confidence_percent": round(confidence, 0),
            "evidence_days": len(baseline) + 1,
            "evidence": f"Latest measured day compared with {len(baseline)} recent baseline days.",
            "reasoning": f"The difference exceeded the adaptive threshold of {threshold:.2f} kWh.",
            "follow_up": "Review weather, device activity and mapped sensor continuity before drawing a conclusion.",
        }

    def _normal_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Explain why current behavior is considered normal."""
        if len(rows) < self.BASELINE_DAYS_REQUIRED:
            return []
        latest = rows[-1]
        baseline_rows = rows[-8:-1]
        checks: list[dict[str, Any]] = []
        definitions = (
            ("solar", "Solar production", "mdi:white-balance-sunny"),
            ("consumption", "Home consumption", "mdi:home-lightning-bolt-outline"),
            ("import", "Grid import", "mdi:transmission-tower-import"),
            ("export", "Grid export", "mdi:transmission-tower-export"),
        )
        for metric, label, icon in definitions:
            values = [self._metric(r, metric) for r in baseline_rows]
            if not values:
                continue
            current = self._metric(latest, metric)
            avg = mean(values)
            difference = ((current - avg) / avg * 100.0) if avg > 0 else 0.0
            spread = pstdev(values) if len(values) > 1 else 0.0
            threshold = max(spread * 1.8, avg * 0.35, 1.0)
            inside = abs(current - avg) < threshold
            if not inside:
                continue
            confidence = min(94.0, 58.0 + len(values) * 4.0)
            checks.append({
                "metric": metric,
                "label": label,
                "icon": icon,
                "latest_kwh": round(current, 2),
                "baseline_kwh": round(avg, 2),
                "difference_percent": round(difference, 1),
                "confidence_percent": round(confidence),
                "evidence_days": len(values) + 1,
                "severity": "Informational",
                "summary": f"{label} is {abs(difference):.1f}% {'above' if difference > 0 else 'below' if difference < 0 else 'equal to'} the recent average and remains inside the adaptive baseline.",
            })
        return checks


    def _flow_context(self) -> dict[str, Any]:
        flow = self.core.energy_flow.summary() or {}
        flows = flow.get("flows") if isinstance(flow.get("flows"), dict) else flow

        def watts(*keys: str) -> float:
            for key in keys:
                value = flows.get(key)
                if isinstance(value, dict):
                    if value.get("w") is not None:
                        return self._num(value.get("w"))
                    if value.get("kw") is not None:
                        return self._num(value.get("kw")) * 1000.0
                elif value is not None:
                    return self._num(value)
            return 0.0

        return {
            "solar_w": round(watts("solar_power", "solar_power_w", "solar_w")),
            "home_w": round(watts("house_power", "house_power_w", "home_power_w", "home_w")),
            "grid_import_w": round(watts("grid_import_power", "grid_import_power_w", "grid_import_w")),
            "grid_export_w": round(watts("grid_export_power", "grid_export_power_w", "grid_export_w")),
            "battery_soc_percent": round(self._num(flows.get("battery_soc_percent", flow.get("battery_soc_percent"))), 1),
            "updated_at": str(flow.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        }

    def _planning_quality(self) -> dict[str, Any]:
        planning = self.core.planning_engine.summary() or {}
        completed = planning.get("completed_results") if isinstance(planning.get("completed_results"), list) else []
        accuracies = [self._num(row.get("solar_accuracy_percent")) for row in completed if isinstance(row, dict) and row.get("solar_accuracy_percent") is not None]
        average = mean(accuracies) if accuracies else None
        maturity = min(100.0, len(accuracies) * 10.0)
        confidence = None if average is None else min(100.0, average * 0.65 + maturity * 0.35)
        return {
            "comparison_count": len(accuracies),
            "forecast_quality_percent": round(average, 1) if average is not None else None,
            "learning_confidence_percent": round(confidence, 1) if confidence is not None else None,
        }

    def _positive_insights(self, rows: list[dict[str, Any]], planning_quality: dict[str, Any]) -> list[dict[str, Any]]:
        if not rows:
            return []
        latest = rows[-1]
        solar_values = [self._metric(row, "solar") for row in rows]
        import_values = [self._metric(row, "import") for row in rows]
        latest_solar = self._metric(latest, "solar")
        latest_import = self._metric(latest, "import")
        latest_export = self._metric(latest, "export")
        positives: list[dict[str, Any]] = []
        if latest_solar > 0 and latest_solar >= max(solar_values):
            positives.append({
                "title": "Best solar day in the measured period",
                "detail": f"The latest day produced {latest_solar:.2f} kWh, the highest of {len(rows)} measured days.",
                "icon": "mdi:trophy-outline", "confidence_percent": min(96, 64 + len(rows) * 4),
            })
        if len(rows) >= 3 and latest_import <= min(import_values):
            positives.append({
                "title": "Lowest grid import in the measured period",
                "detail": f"Grid import was {latest_import:.2f} kWh on the latest measured day.",
                "icon": "mdi:transmission-tower-off", "confidence_percent": min(94, 62 + len(rows) * 4),
            })
        if latest_solar > 0:
            self_use = max(0.0, min(100.0, (latest_solar - latest_export) / latest_solar * 100.0))
            if self_use >= 70:
                positives.append({
                    "title": "Strong local solar use",
                    "detail": f"Approximately {self_use:.0f}% of the latest measured solar production remained on site.",
                    "icon": "mdi:home-lightning-bolt-outline", "confidence_percent": min(92, 60 + len(rows) * 4),
                })
        quality = planning_quality.get("forecast_quality_percent")
        if quality is not None and quality >= 90:
            positives.append({
                "title": "Forecast closely matched measured production",
                "detail": f"Average completed-plan solar accuracy is {quality:.1f}%.",
                "icon": "mdi:target", "confidence_percent": round(quality),
            })
        return positives[:4]

    def _today_story(self, rows: list[dict[str, Any]], flow: dict[str, Any], top_action: dict[str, Any] | None) -> list[dict[str, Any]]:
        story: list[dict[str, Any]] = []
        measured = self._story_row(rows)
        if measured is not None:
            date = str(measured.get("date") or "Latest measured day")
            solar = self._metric(measured, "solar")
            home = self._metric(measured, "consumption")
            imported = self._metric(measured, "import")
            exported = self._metric(measured, "export")
            battery_support = self._num(measured.get("battery_support_to_home_kwh"))
            in_progress = measured.get("day_state") == "in_progress"
            support_text = f" · Battery support {battery_support:.2f} kWh" if battery_support is not None else ""
            story.append({
                "time": date,
                "title": "Measured energy day in progress" if in_progress else "Measured energy day completed",
                "detail": f"Solar {solar:.2f} kWh · Home {home:.2f} kWh{support_text} · Import {imported:.2f} kWh · Export {exported:.2f} kWh.",
                "icon": "mdi:calendar-sync-outline" if in_progress else "mdi:calendar-check-outline",
                "tone": "measured",
                "authoritative": True,
                "day_state": measured.get("day_state"),
                "sources": measured.get("sources", {}),
            })
        elif rows:
            story.append({
                "time": "Collecting",
                "title": "Authoritative daily totals are collecting",
                "detail": "Zeus will show the story after mapped daily-energy totals are available for the local calendar day.",
                "icon": "mdi:database-clock-outline",
                "tone": "collecting",
            })
        solar_w, home_w = flow.get("solar_w", 0), flow.get("home_w", 0)
        import_w, export_w = flow.get("grid_import_w", 0), flow.get("grid_export_w", 0)
        if solar_w or home_w or import_w or export_w:
            if export_w > 50:
                title, detail, icon = "Solar surplus is being exported", f"Solar {solar_w:.0f} W · Home {home_w:.0f} W · Export {export_w:.0f} W.", "mdi:transmission-tower-export"
            elif import_w > 50:
                title, detail, icon = "The home is currently using grid support", f"Home {home_w:.0f} W · Import {import_w:.0f} W · Solar {solar_w:.0f} W.", "mdi:transmission-tower-import"
            elif solar_w >= home_w and solar_w > 0:
                title, detail, icon = "Solar is covering the current home load", f"Solar {solar_w:.0f} W · Home {home_w:.0f} W.", "mdi:white-balance-sunny"
            else:
                title, detail, icon = "Live energy flow is balanced", f"Solar {solar_w:.0f} W · Home {home_w:.0f} W.", "mdi:home-lightning-bolt-outline"
            story.append({"time": "Now", "title": title, "detail": detail, "icon": icon, "tone": "live"})
        soc = flow.get("battery_soc_percent")
        if soc is not None and soc > 0:
            story.append({
                "time": "Now", "title": "Battery operating context",
                "detail": f"Battery state of charge is {soc:.0f}%.",
                "icon": "mdi:battery-high" if soc >= 70 else "mdi:battery-medium", "tone": "battery",
            })
        if top_action:
            story.append({
                "time": str(top_action.get("best_window") or "Next suitable window"),
                "title": str(top_action.get("title") or "Recommended follow-up"),
                "detail": str(top_action.get("reason") or "Recommendation supported by forecast and measured context."),
                "icon": "mdi:lightbulb-on-outline", "tone": "recommendation",
            })
        return story[:5]


    def _tomorrow_prediction(self, rows: list[dict[str, Any]], top_opportunity: dict[str, Any] | None) -> dict[str, Any]:
        """Build a transparent recommendation-only story for the next local day."""
        forecast = self.core.forecast.summary() or {}
        planning = self.core.planning_engine.summary() or {}
        tomorrow_key = (dt_util.now().date() + timedelta(days=1)).isoformat()
        forecast_days = forecast.get("daily_forecast") if isinstance(forecast.get("daily_forecast"), list) else []
        day = next((dict(item) for item in forecast_days if isinstance(item, dict) and str(item.get("date"))[:10] == tomorrow_key), None)
        plans = planning.get("plans") if isinstance(planning.get("plans"), list) else []
        plan = next((dict(item) for item in plans if isinstance(item, dict) and str(item.get("date"))[:10] == tomorrow_key), None)
        # The current Forecast page is the authoritative source for the next-day
        # preview. A stored pre-day plan remains historical planning evidence, but
        # must never override a newer forecast snapshot on Tomorrow's Story.
        source = day or {}
        solar = self._num(source.get("expected_solar_kwh"), self._num(forecast.get("expected_solar_following_24h_kwh")))
        demand = self._num(source.get("expected_consumption_kwh"), self._num(forecast.get("expected_consumption_following_24h_kwh")))
        imported = self._num(source.get("expected_grid_import_kwh"), self._num(forecast.get("expected_grid_import_following_24h_kwh"), max(0.0, demand - solar)))
        exported = self._num(source.get("expected_grid_export_kwh"), self._num(forecast.get("expected_grid_export_following_24h_kwh"), max(0.0, solar - demand)))
        confidence = self._num(forecast.get("confidence"), self._num(source.get("confidence_percent"), self._num(source.get("confidence"))))
        condition = str(source.get("weather_summary") or source.get("condition") or forecast.get("weather", {}).get("condition") or "Weather context collecting")
        projected_soc = source.get("battery_soc_end_percent", forecast.get("projected_battery_soc_48h_percent"))
        peak_hour = source.get("peak_hour")
        if solar <= 0 and demand <= 0:
            return {
                "status": "Collecting", "date": tomorrow_key, "recommendation_only": True,
                "headline": "Tomorrow’s energy story is collecting",
                "summary": "Zeus needs a valid next-day forecast before it can describe tomorrow without guessing.",
                "confidence_percent": None, "confidence_label": "Collecting", "confidence_reasons": [],
                "plan_preview": [], "if_nothing_changes": "No supported next-day guidance is available yet.",
            }
        history_days = len(rows)
        confidence = max(0.0, min(100.0, confidence))
        confidence_label = "High" if confidence >= 80 else "Moderate" if confidence >= 55 else "Low"
        reasons = [
            f"{history_days} measured day{'s' if history_days != 1 else ''} support the local profile.",
            f"Weather context: {condition}.",
            "The current weather-adjusted Forecast snapshot is being used.",
            "A stored pre-day plan is also available for later plan-versus-result comparison." if plan else "No stored pre-day plan is available yet.",
        ]
        ratio = solar / max(demand, 0.1)
        if ratio >= 1.35:
            headline = "Tomorrow is expected to provide a strong solar surplus"
            summary = f"Forecast solar is {solar:.1f} kWh against about {demand:.1f} kWh of expected household demand. Export is currently estimated near {exported:.1f} kWh."
        elif ratio >= 0.85:
            headline = "Tomorrow should be a balanced solar day"
            summary = f"Forecast solar is {solar:.1f} kWh and expected demand is {demand:.1f} kWh, so local generation should cover a substantial share of the home’s energy use."
        else:
            headline = "Tomorrow may need battery or grid support"
            summary = f"Forecast solar is {solar:.1f} kWh against about {demand:.1f} kWh of expected demand. Grid import is currently estimated near {imported:.1f} kWh."
        peak_label = f"around {int(peak_hour):02d}:00" if peak_hour is not None else "during the solar peak"
        preview = [
            {"period": "Morning", "title": "Protect useful battery reserve", "detail": "Use stored energy cautiously until solar production strengthens."},
            {"period": "Midday", "title": "Use the strongest solar window", "detail": f"Peak production is expected {peak_label}; this is the preferred window for flexible loads."},
            {"period": "Afternoon", "title": "Manage expected surplus", "detail": f"Approximately {exported:.1f} kWh of export is forecast." if exported > 0.5 else "No large export window is currently supported."},
            {"period": "Evening", "title": "Prepare for household demand", "detail": f"Projected end-of-day battery SOC is {self._num(projected_soc):.0f}%." if projected_soc is not None else "Battery outcome is still collecting."},
        ]
        if imported <= 0.5 and exported <= 0.5:
            unchanged = "If the forecast remains stable, no major schedule change is required."
        elif exported > 1.0:
            unchanged = "If nothing changes, move flexible loads into the midday solar window before surplus energy is exported."
        else:
            unchanged = "If nothing changes, preserve battery reserve for the evening and avoid unnecessary morning grid use."
        return {
            "status": "Ready", "date": tomorrow_key, "recommendation_only": True,
            "headline": headline, "summary": summary,
            "expected_solar_kwh": round(solar, 2), "expected_consumption_kwh": round(demand, 2),
            "expected_grid_import_kwh": round(imported, 2), "expected_grid_export_kwh": round(exported, 2),
            "projected_battery_soc_percent": round(self._num(projected_soc), 1) if projected_soc is not None else None,
            "weather_summary": condition, "confidence_percent": round(confidence), "confidence_label": confidence_label,
            "confidence_reasons": reasons, "plan_preview": preview, "if_nothing_changes": unchanged,
            "top_recommendation": top_opportunity or {}, "source": "current_forecast",
            "stored_plan_available": bool(plan),
        }

    def _executive_kpis(self, anomalies: list[dict[str, Any]], trends: list[dict[str, Any]], optimization: dict[str, Any], planning_quality: dict[str, Any], evidence_days: int) -> dict[str, Any]:
        attention = len(anomalies) + len([trend for trend in trends if trend.get("assessment") == "Needs attention"])
        energy_health = max(0.0, min(100.0, 96.0 - len(anomalies) * 18.0 - attention * 7.0))
        return {
            "overall_status": "Attention" if anomalies else "Watch" if attention else "Normal" if evidence_days >= self.BASELINE_DAYS_REQUIRED else "Collecting",
            "energy_health_percent": round(energy_health) if evidence_days else None,
            "optimization_score": optimization.get("optimization_score"),
            "forecast_quality_percent": planning_quality.get("forecast_quality_percent"),
            "learning_confidence_percent": planning_quality.get("learning_confidence_percent"),
        }

    def refresh(self) -> dict[str, Any]:
        rows = self._rows()
        trends = [x for x in (
            self._trend(rows, "solar", "Solar production"),
            self._trend(rows, "consumption", "Home consumption", good_when_down=True),
            self._trend(rows, "import", "Grid import", good_when_down=True),
            self._trend(rows, "export", "Grid export"),
        ) if x]
        anomalies = [x for x in (
            self._anomaly(rows, "solar", "solar production"),
            self._anomaly(rows, "consumption", "home consumption"),
            self._anomaly(rows, "import", "grid import"),
            self._anomaly(rows, "export", "grid export"),
        ) if x]
        normal_evidence = self._normal_evidence(rows) if not anomalies else []
        energy_trend_bundle = self._energy_trend_bundle(rows)
        energy_insights, energy_briefing = self._energy_insights(energy_trend_bundle)

        ranked: list[dict[str, Any]] = []
        for anomaly in anomalies:
            ranked.append({
                "kind": "anomaly", "priority": anomaly["priority"], "severity": anomaly["severity"], "title": anomaly["title"],
                "summary": f"Latest value is {abs(anomaly['difference_percent']):.1f}% {anomaly['direction']} its recent baseline.",
                "confidence_percent": anomaly["confidence_percent"], "evidence_days": anomaly["evidence_days"],
                "evidence": anomaly["evidence"], "reasoning": anomaly["reasoning"],
            })
        for trend in trends:
            if trend["direction"] != "Stable":
                priority = "Medium" if trend["assessment"] == "Needs attention" else "Low"
                ranked.append({
                    "kind": "trend", "priority": priority, "severity": self._severity(priority),
                    "title": f"{trend['label']} is {trend['direction'].lower()}",
                    "summary": f"Changed {trend['change_percent']:+.1f}% across the latest measured periods.",
                    "confidence_percent": trend["confidence_percent"], "evidence_days": trend["evidence_days"],
                    "evidence": trend["evidence"], "reasoning": trend["reasoning"],
                })
        ranked.sort(key=lambda x: ({"High": 3, "Medium": 2, "Low": 1}.get(x["priority"], 1), x["confidence_percent"]), reverse=True)
        ranked = ranked[:6]

        optimization = getattr(self.core, "optimization_intelligence", None)
        opt = optimization.summary() if optimization and callable(getattr(optimization, "summary", None)) else {}
        opportunities = opt.get("opportunities") if isinstance(opt.get("opportunities"), list) else []
        top_opportunity = opportunities[0] if opportunities and isinstance(opportunities[0], dict) else None
        top_action = top_opportunity.get("title") if top_opportunity else "No verified action required"
        planning_quality = self._planning_quality()
        flow_context = self._flow_context()
        positives = self._positive_insights(rows, planning_quality)
        story = self._today_story(rows, flow_context, top_opportunity)
        tomorrow_prediction = self._tomorrow_prediction(rows, top_opportunity)
        executive_kpis = self._executive_kpis(anomalies, trends, opt, planning_quality, len(rows))
        headline = ranked[0]["title"] if ranked else (
            "Your energy system is operating normally today" if len(rows) >= self.BASELINE_DAYS_REQUIRED
            else "Zeus is building your supported energy baseline"
        )
        baseline_progress = {
            "measured_days": len(rows),
            "required_days": self.BASELINE_DAYS_REQUIRED,
            "remaining_days": max(0, self.BASELINE_DAYS_REQUIRED - len(rows)),
            "percent": round(min(100.0, len(rows) / self.BASELINE_DAYS_REQUIRED * 100.0)),
            "ready": len(rows) >= self.BASELINE_DAYS_REQUIRED,
        }
        trend_progress = {
            "measured_days": len(rows),
            "required_days": self.TREND_DAYS_REQUIRED,
            "remaining_days": max(0, self.TREND_DAYS_REQUIRED - len(rows)),
            "percent": round(min(100.0, len(rows) / self.TREND_DAYS_REQUIRED * 100.0)),
            "ready": len(rows) >= self.TREND_DAYS_REQUIRED,
        }
        if ranked:
            natural_summary = f"{ranked[0]['summary']} Zeus found {len(ranked)} supported insight{'s' if len(ranked) != 1 else ''} and {len(anomalies)} item{'s' if len(anomalies) != 1 else ''} needing attention."
        elif normal_evidence:
            natural_summary = f"Measured solar, household demand and grid behavior remain inside the adaptive recent baseline. Zeus checked {len(normal_evidence)} supported signals and found no unusual behavior requiring attention."
        else:
            natural_summary = f"Zeus has {len(rows)} measured day{'s' if len(rows) != 1 else ''}. It will provide a confident daily story after at least {self.BASELINE_DAYS_REQUIRED} measured days establish a supported baseline."
        briefing = {
            "headline": headline,
            "attention_count": len(anomalies),
            "meaningful_change_count": len([t for t in trends if t["direction"] != "Stable"]),
            "top_recommendation": top_action,
            "top_recommendation_detail": top_opportunity or {},
            "summary": natural_summary,
            "reading_time_seconds": 20,
        }
        self._summary = {
            "status": "Ready" if len(rows) >= self.BASELINE_DAYS_REQUIRED else "Collecting",
            "recommendation_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_days": len(rows),
            "briefing": briefing,
            "energy_briefing": energy_briefing,
            "energy_insights": energy_insights,
            "energy_trend_evidence": energy_trend_bundle,
            "insights": ranked,
            "anomalies": anomalies,
            "trends": trends,
            "normal_evidence": normal_evidence,
            "baseline_progress": baseline_progress,
            "trend_progress": trend_progress,
            "executive_kpis": executive_kpis,
            "today_story": story,
            "tomorrow_prediction": tomorrow_prediction,
            "positive_insights": positives,
            "live_context": flow_context,
            "limitations": "Energy Insights use completed measured history, keep missing evidence distinct from zero, exclude today's partial day, and do not diagnose equipment faults or claim unsupported causes.",
        }
        self.event_bus.publish("InsightIntelligenceUpdated", "InsightIntelligenceEngine", {"status": self._summary["status"], "insight_count": len(ranked), "recommendation_only": True})
        return self._summary

    def summary(self) -> dict[str, Any]:
        return dict(self._summary)
