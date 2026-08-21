"""Cross-engine data consistency validation for AION EMS Zeus.

The monitor compares compact public summaries against the authoritative mapped
current-day values held by the Data Lake. It is diagnostic only, recorder-safe,
and never controls devices.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.util import dt as dt_util

from .period_authority import canonical_period_window, trusted_data_epoch


class DataConsistencyEngine:
    """Verify that Zeus engines agree on shared energy metrics."""

    VERSION = "1.3-beta.0"
    ENERGY_TOLERANCE_KWH = 0.02
    SOC_TOLERANCE_PERCENT = 0.2

    METRICS = {
        "solar_today_kwh": "Solar today",
        "home_today_kwh": "Home consumption today",
        "grid_import_today_kwh": "Grid import today",
        "grid_export_today_kwh": "Grid export today",
        "battery_soc_percent": "Battery SOC",
    }

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.last: dict[str, Any] = {
            "status": "Waiting",
            "version": self.VERSION,
            "mode": "diagnostic_only",
            "summary": "Data Consistency is waiting for the first complete refresh.",
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            value = float(value)
            return value if value >= 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _summary(engine: Any) -> dict[str, Any]:
        try:
            value = engine.summary()
            return value if isinstance(value, dict) else {}
        except Exception as err:
            return {"status": "Error", "error": type(err).__name__}

    def _canonical(self) -> tuple[dict[str, float | None], dict[str, str | None]]:
        """Resolve authoritative current-day values from the saved Energy mappings.

        Current-day daily-reset meters are Zeus's canonical calendar-day authority.
        The Data Lake is a persistence/integration fallback and may legitimately
        contain only a post-restart/post-reset portion of an in-progress day.
        Data Consistency must therefore compare modules against the same mapped
        Today authority used by Energy Statistics and Historical Analytics.
        """
        now_key = dt_util.now().date().isoformat()
        lake = getattr(self.core, "data_lake", None)
        row = getattr(lake, "data", {}).get("daily_summaries", {}).get(now_key, {})
        mapping_engine = getattr(self.core, "energy_mapping", None)
        mapping = self._summary(mapping_engine)
        mapped = mapping.get("mapped", {}) if isinstance(mapping.get("mapped"), dict) else {}
        flow = self._summary(getattr(self.core, "energy_flow", None))

        def mapped_number(field: str, fallback_key: str) -> float | None:
            item = mapped.get(field) if isinstance(mapped, dict) else None
            value = self._number(item.get("value")) if isinstance(item, dict) else None
            return value if value is not None else self._number(row.get(fallback_key))

        def mapped_source(field: str, fallback_key: str) -> str | None:
            item = mapped.get(field) if isinstance(mapped, dict) else None
            entity_id = item.get("entity_id") if isinstance(item, dict) else None
            return str(entity_id) if entity_id else row.get(fallback_key)

        values = {
            "solar_today_kwh": mapped_number("solar_energy_today", "solar_energy_kwh"),
            "home_today_kwh": mapped_number("house_energy_today", "house_energy_kwh"),
            "grid_import_today_kwh": mapped_number("grid_import_energy_today", "grid_import_energy_kwh"),
            "grid_export_today_kwh": mapped_number("grid_export_energy_today", "grid_export_energy_kwh"),
            "battery_soc_percent": self._number(flow.get("battery_soc_percent")),
        }
        sources = {
            "solar_today_kwh": mapped_source("solar_energy_today", "solar_energy_kwh_source"),
            "home_today_kwh": mapped_source("house_energy_today", "house_energy_kwh_source"),
            "grid_import_today_kwh": mapped_source("grid_import_energy_today", "grid_import_energy_kwh_source"),
            "grid_export_today_kwh": mapped_source("grid_export_energy_today", "grid_export_energy_kwh_source"),
            "battery_soc_percent": mapping_engine.mappings.get("battery_soc")
            if mapping_engine is not None else None,
        }
        return values, sources


    def _module_values(self) -> dict[str, dict[str, float | None]]:
        analytics = self._summary(getattr(self.core, "analytics", None))
        today = analytics.get("periods", {}).get("today", {}) if isinstance(analytics.get("periods"), dict) else {}
        finance = self._summary(getattr(self.core, "finance", None))
        briefing = self._summary(getattr(self.core, "executive_briefing", None))
        flow = self._summary(getattr(self.core, "energy_flow", None))
        return {
            "Historical Analytics": {
                "solar_today_kwh": self._number(today.get("solar_energy_kwh")),
                "home_today_kwh": self._number(today.get("house_energy_kwh")),
                "grid_import_today_kwh": self._number(today.get("grid_import_energy_kwh")),
                "grid_export_today_kwh": self._number(today.get("grid_export_energy_kwh")),
            },
            "Finance": {
                "grid_import_today_kwh": self._number(finance.get("grid_import_kwh")),
                "grid_export_today_kwh": self._number(finance.get("grid_export_kwh")),
            },
            "Executive Briefing": {
                "solar_today_kwh": self._number(briefing.get("generated_today_kwh")),
                "home_today_kwh": self._number(briefing.get("consumed_today_kwh")),
                "grid_import_today_kwh": self._number(briefing.get("imported_today_kwh")),
                "grid_export_today_kwh": self._number(briefing.get("exported_today_kwh")),
                "battery_soc_percent": self._number(briefing.get("battery_soc_percent")),
            },
            "Energy Flow": {
                "battery_soc_percent": self._number(flow.get("battery_soc_percent")),
            },
        }

    def refresh(self) -> dict[str, Any]:
        canonical, sources = self._canonical()
        modules = self._module_values()
        comparisons: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []

        for module_name, values in modules.items():
            for metric, actual in values.items():
                expected = canonical.get(metric)
                if expected is None or actual is None:
                    continue
                tolerance = self.SOC_TOLERANCE_PERCENT if metric == "battery_soc_percent" else self.ENERGY_TOLERANCE_KWH
                difference = abs(actual - expected)
                consistent = difference <= tolerance
                item = {
                    "metric": metric,
                    "label": self.METRICS.get(metric, metric),
                    "module": module_name,
                    "expected": round(expected, 4),
                    "actual": round(actual, 4),
                    "difference": round(difference, 4),
                    "consistent": consistent,
                }
                comparisons.append(item)
                if not consistent:
                    issues.append(item)

        checked = len(comparisons)
        if not canonical or not any(value is not None for value in canonical.values()):
            status = "Waiting"
        elif issues:
            status = "Mismatch"
        else:
            status = "Consistent"

        metric_status = {}
        for metric, label in self.METRICS.items():
            related = [x for x in comparisons if x["metric"] == metric]
            metric_status[metric] = {
                "label": label,
                "status": "Consistent" if related and all(x["consistent"] for x in related) else "Mismatch" if related else "Not compared",
                "canonical_value": canonical.get(metric),
                "source": sources.get(metric),
                "comparison_count": len(related),
            }

        # Source-first diagnostics: classify mapping/readiness problems separately
        # from cross-engine arithmetic mismatches. Optional unconfigured sources
        # are deliberately neutral.
        mapping_engine = getattr(self.core, "energy_mapping", None)
        mapping_summary = self._summary(mapping_engine)
        source_catalog = mapping_summary.get("source_catalog", {}) if isinstance(mapping_summary, dict) else {}
        source_diagnostics = {}
        source_issue_count = 0
        for source_id, source in source_catalog.items():
            if not isinstance(source, dict):
                continue
            configured = bool(source.get("configured"))
            source_status = source.get("status", "not_configured")
            source_issues = list(source.get("issues") or [])
            if not configured:
                diagnostic_status = "Optional / not configured" if source_id in {"wind", "generator"} else "Not configured"
            elif source_status == "degraded":
                diagnostic_status = "Needs attention"
                source_issue_count += max(1, len(source_issues))
            elif source_status == "healthy":
                diagnostic_status = "Healthy"
            else:
                diagnostic_status = "Waiting"
            source_diagnostics[source_id] = {
                "label": source.get("label", source_id.title()),
                "status": diagnostic_status,
                "readiness_percent": source.get("readiness_percent", 0),
                "live_ready": bool(source.get("live_ready")),
                "accounting_ready": bool(source.get("accounting_ready")),
                "issues": source_issues,
            }

        # v14.0.0-alpha.22.8.9.0: accounting-integrity guardrails.  These are
        # diagnostic assertions only: they never rewrite energy or control devices.
        # The checks deliberately compare DEA registered loads with the same canonical
        # calendar-period house totals exposed by Historical Analytics.
        analytics = self._summary(getattr(self.core, "analytics", None))
        analytics_periods = analytics.get("periods", {}) if isinstance(analytics.get("periods"), dict) else {}
        dea = self._summary(getattr(self.core, "device_energy_attribution", None))
        dea_periods = dea.get("periods", {}) if isinstance(dea.get("periods"), dict) else {}
        integrity_checks: list[dict[str, Any]] = []
        integrity_issues: list[dict[str, Any]] = []

        def add_integrity(period: str, check: str, actual: float, limit: float, tolerance: float = 0.02) -> None:
            passed = actual <= limit + tolerance
            item = {
                "period": period, "check": check, "actual_kwh": round(actual, 4),
                "limit_kwh": round(limit, 4), "tolerance_kwh": tolerance, "passed": passed,
            }
            integrity_checks.append(item)
            if not passed:
                integrity_issues.append(item)

        for period in ("today", "week", "month", "year"):
            a = analytics_periods.get(period, {}) if isinstance(analytics_periods.get(period), dict) else {}
            d = dea_periods.get(period, {}) if isinstance(dea_periods.get(period), dict) else {}
            house = self._number(a.get("house_energy_kwh"))
            registered = self._number(d.get("energy_kwh"))
            attributed = self._number(d.get("attributed_total_kwh"))
            if house is not None and registered is not None:
                add_integrity(period, "registered_loads_le_whole_home", registered, house)
            if registered is not None and attributed is not None:
                add_integrity(period, "source_attribution_le_registered_loads", attributed, registered)
            if registered is not None:
                for source_key in ("solar_kwh", "wind_kwh", "generator_kwh", "battery_kwh", "grid_kwh"):
                    source_value = self._number(d.get(source_key))
                    if source_value is not None:
                        add_integrity(period, f"{source_key}_le_registered_loads", source_value, registered)

        # Calendar periods are nested.  A shorter current period must not exceed
        # the containing longer period (within a small recorder/rounding tolerance).
        nested = [("today", "week"), ("week", "month"), ("month", "year")]
        for shorter, longer in nested:
            sv = self._number((analytics_periods.get(shorter) or {}).get("house_energy_kwh"))
            lv = self._number((analytics_periods.get(longer) or {}).get("house_energy_kwh"))
            if sv is not None and lv is not None:
                add_integrity(f"{shorter}->{longer}", "canonical_period_nesting", sv, lv)

        # v14.0.0-alpha.22.8.9.1: finance reconciliation uses the same canonical
        # energy quantities and fixed tariffs as the Finance UI.  These assertions
        # are diagnostic-only and make any future accounting drift visible.
        finance = self._summary(getattr(self.core, "finance", None))
        finance_checks: list[dict[str, Any]] = []
        finance_issues: list[dict[str, Any]] = []
        if bool(finance.get("configured")):
            import_rate = self._number(finance.get("import_tariff")) or 0.0
            export_rate = self._number(finance.get("export_tariff")) or 0.0
            direct = self._number(finance.get("direct_solar_to_home_kwh")) or 0.0
            battery = self._number(finance.get("battery_support_to_home_kwh")) or 0.0
            imported = self._number(finance.get("grid_import_kwh")) or 0.0
            exported = self._number(finance.get("grid_export_kwh")) or 0.0
            expected = {
                "solar_value_today": direct * import_rate,
                "battery_support_value_today": battery * import_rate,
                "avoided_import_value_today": (direct + battery) * import_rate,
                "grid_cost_today": imported * import_rate,
                "export_revenue_today": exported * export_rate,
            }
            for key, target in expected.items():
                actual = self._number(finance.get(key))
                if actual is None:
                    continue
                passed = abs(actual - target) <= 0.02
                item = {"check": key, "actual": round(actual, 4), "expected": round(target, 4), "passed": passed}
                finance_checks.append(item)
                if not passed:
                    finance_issues.append(item)
        finance_status = "Pass" if finance_checks and not finance_issues else "Review" if finance_issues else "Waiting"

        integrity_status = "Pass" if integrity_checks and not integrity_issues else "Review" if integrity_issues else "Waiting"

        # v14.0.0-alpha.22.8.9.5: diagnostics & confidence transparency.
        # Surface period coverage, epoch truncation, recorder/history quality and
        # reconciliation intervention without changing any accounting values.
        now_local = dt_util.now()
        epoch = trusted_data_epoch()
        natural_starts = {
            "today": dt_util.start_of_local_day(now_local),
            "week": dt_util.start_of_local_day(now_local - timedelta(days=now_local.weekday())),
            "month": dt_util.start_of_local_day(now_local.replace(day=1)),
            "year": dt_util.start_of_local_day(now_local.replace(month=1, day=1)),
        }
        period_quality: dict[str, dict[str, Any]] = {}
        quality_issues: list[dict[str, Any]] = []
        reconciliation_events: list[dict[str, Any]] = []
        confidence_points = 100.0

        for period in ("today", "week", "month", "year", "total"):
            window = canonical_period_window(period, now_local)
            d = dea_periods.get(period, {}) if isinstance(dea_periods.get(period), dict) else {}
            measured = int(d.get("measured_devices") or 0)
            device_count = int(d.get("device_count") or 0)
            status_value = str(d.get("status") or "Waiting")
            epoch_limited = bool(
                epoch is not None and period != "total" and natural_starts.get(period) is not None
                and epoch > natural_starts[period]
            )
            aggregate_reconciled = bool(d.get("aggregate_reconciled"))
            overlap_samples = int(d.get("timestamp_overlap_samples") or 0)
            minimum_scale = self._number(d.get("minimum_timestamp_scale"))
            measurement_ratio = (measured / device_count) if device_count else None
            fully_measured = bool(device_count and measured >= device_count and status_value.lower() == "ready")
            estimated = status_value.lower() == "estimated" or (device_count and measured < device_count)

            if aggregate_reconciled:
                reconciliation_events.append({
                    "period": period,
                    "type": "whole_home_safety_reconciliation",
                    "scale": d.get("aggregate_scale"),
                    "message": "Registered-load aggregate required final whole-home reconciliation.",
                })
                confidence_points -= 6.0
            if overlap_samples > 0 or (minimum_scale is not None and minimum_scale < 0.999):
                reconciliation_events.append({
                    "period": period,
                    "type": "timestamp_overlap_reconciliation",
                    "samples": overlap_samples,
                    "minimum_scale": minimum_scale,
                    "message": "Overlapping registered loads were reconciled at aligned Recorder timestamps.",
                })
                confidence_points -= 2.0
            if estimated:
                quality_issues.append({
                    "period": period,
                    "type": "incomplete_measured_history",
                    "severity": "info" if period in {"year", "total"} else "warning",
                    "message": f"{period.title()} includes estimated or incomplete registered-load history.",
                    "measured_devices": measured,
                    "device_count": device_count,
                })
                confidence_points -= 8.0 if period in {"today", "week", "month"} else 3.0

            period_quality[period] = {
                "start": window.start.isoformat() if window.start else None,
                "end": window.end.isoformat(),
                "definition": window.definition,
                "epoch_limited": epoch_limited,
                "status": status_value,
                "measured_devices": measured,
                "device_count": device_count,
                "measurement_coverage_percent": round(measurement_ratio * 100.0, 1) if measurement_ratio is not None else None,
                "fully_measured": fully_measured,
                "aggregate_reconciled": aggregate_reconciled,
                "timestamp_overlap_samples": overlap_samples,
                "minimum_timestamp_scale": minimum_scale,
            }

        missing_canonical = [self.METRICS.get(k, k) for k, v in canonical.items() if v is None]
        if missing_canonical:
            quality_issues.append({
                "period": "today", "type": "missing_canonical_metrics", "severity": "warning",
                "message": "One or more canonical live/today metrics are unavailable.",
                "metrics": missing_canonical,
            })
            confidence_points -= min(20.0, 5.0 * len(missing_canonical))
        if source_issue_count:
            confidence_points -= min(15.0, 3.0 * source_issue_count)
        if integrity_issues:
            confidence_points -= min(25.0, 8.0 * len(integrity_issues))
        if finance_issues:
            confidence_points -= min(15.0, 5.0 * len(finance_issues))
        if issues:
            confidence_points -= min(20.0, 4.0 * len(issues))

        confidence_percent = int(round(max(0.0, min(100.0, confidence_points))))
        confidence_label = (
            "High" if confidence_percent >= 90 else
            "Good" if confidence_percent >= 75 else
            "Limited" if confidence_percent >= 55 else
            "Low"
        )
        active_limits = []
        if epoch is not None:
            active_limits.append(f"Trusted data start: {epoch.isoformat()}")
        if missing_canonical:
            active_limits.append(f"Missing canonical metrics: {len(missing_canonical)}")
        if quality_issues:
            active_limits.append(f"History/data-quality notices: {len(quality_issues)}")
        if reconciliation_events:
            active_limits.append(f"Reconciliation events: {len(reconciliation_events)}")

        confidence = {
            "percent": confidence_percent,
            "label": confidence_label,
            "trusted_data_start": epoch.isoformat() if epoch else None,
            "epoch_active": epoch is not None,
            "epoch_note": "Home Assistant Recorder history is preserved; Zeus ignores data before the trusted start." if epoch else "Using all available Home Assistant history.",
            "period_quality": period_quality,
            "quality_issues": quality_issues[:16],
            "reconciliation_events": reconciliation_events[:16],
            "active_limits": active_limits,
            "meaning": "Confidence describes accounting evidence completeness and agreement; it never changes measured values.",
        }

        # v14.0.0-alpha.22.18.1.39: cross-domain truth contract audit.
        # Planning is the authority for reusable forecast learning. Forecast may
        # expose that advice, but Recommendation Only must leave measured and
        # forecast values unmodified.
        planning = self._summary(getattr(self.core, "planning_engine", None))
        planning_learning = planning.get("learning", {}) if isinstance(planning.get("learning"), dict) else {}
        forecast = self._summary(getattr(self.core, "forecast", None))
        adaptive = forecast.get("adaptive_correction", {}) if isinstance(forecast.get("adaptive_correction"), dict) else {}
        truth_checks = [
            {
                "check": "planning_recommendation_only",
                "passed": bool(planning.get("recommendation_only", True)),
                "detail": "Planning remains recommendation-only.",
            },
            {
                "check": "forecast_learning_not_automatically_applied",
                "passed": not bool(adaptive.get("applied")) and abs(self._number(adaptive.get("applied_correction_percent")) or 0.0) < 0.001,
                "detail": "Qualified learning may be advisory, but it must not mutate the forecast automatically.",
            },
            {
                "check": "reusable_learning_authority_aligned",
                "passed": bool(adaptive.get("reusable_learning_ready", False)) == bool(planning_learning.get("reusable_learning_ready", False)),
                "detail": "Forecast reuses Planning's reusable-learning qualification instead of inventing a second threshold.",
            },
            {
                "check": "learning_threshold_contract",
                "passed": (
                    int(planning_learning.get("minimum_comparisons_for_reuse") or 0) == 5
                    and abs((self._number(planning_learning.get("minimum_effective_evidence_for_reuse")) or 0.0) - 3.0) < 0.001
                    and abs((self._number(planning_learning.get("minimum_direction_consistency_percent")) or 0.0) - 65.0) < 0.001
                ),
                "detail": "Reusable learning requires 5 completed comparisons, 3.0 effective weighted comparisons and 65% directional agreement.",
            },
        ]
        truth_issues = [x for x in truth_checks if not x["passed"]]
        truth_status = "Pass" if not truth_issues else "Review"

        reconciliation = {
            "cross_engine_status": status,
            "cross_engine_mismatch_count": len(issues),
            "source_issue_count": source_issue_count,
            "canonical_period": "today",
            "canonical_metrics_checked": [k for k, v in canonical.items() if v is not None],
            "accounting_integrity_status": integrity_status,
            "accounting_integrity_check_count": len(integrity_checks),
            "accounting_integrity_issue_count": len(integrity_issues),
            "finance_reconciliation_status": finance_status,
            "finance_reconciliation_issue_count": len(finance_issues),
        }

        self.last = {
            "status": status,
            "version": self.VERSION,
            "mode": "diagnostic_only",
            "checked_comparison_count": checked,
            "mismatch_count": len(issues),
            "canonical_values": canonical,
            "canonical_sources": sources,
            "source_diagnostics": source_diagnostics,
            "reconciliation": reconciliation,
            "confidence": confidence,
            "period_quality": period_quality,
            "quality_issues": quality_issues[:16],
            "reconciliation_events": reconciliation_events[:16],
            "metric_status": metric_status,
            "issues": issues[:8],
            "comparisons": comparisons[:24],
            "finance_reconciliation": {
                "status": finance_status,
                "checks": finance_checks,
                "issues": finance_issues,
                "authority": "canonical period energy × configured fixed tariffs",
            },
            "cross_domain_truth": {
                "status": truth_status,
                "check_count": len(truth_checks),
                "issue_count": len(truth_issues),
                "checks": truth_checks,
                "issues": truth_issues,
                "authority": "Planning owns reusable-learning qualification; Forecast consumes it as advisory evidence only.",
                "safety": "Recommendation Only; no measured value or forecast is rewritten by learning.",
            },
            "accounting_integrity": {
                "status": integrity_status,
                "checks": integrity_checks[:48],
                "issues": integrity_issues[:12],
                "rules": [
                    "registered loads <= whole-home consumption for the same calendar period",
                    "source attribution <= registered-load consumption",
                    "each attributed source <= registered-load consumption",
                    "canonical current periods remain nested: today <= week <= month <= year",
                ],
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": (
                "All checked Zeus modules agree with the authoritative mapped values."
                if status == "Consistent"
                else "One or more Zeus modules disagree with the authoritative mapped values."
                if status == "Mismatch"
                else "Waiting for trusted mapped values and module summaries."
            ),
            "safety": "Diagnostic only. Recommendation only. No automatic device control.",
        }
        try:
            self.event_bus.publish(
                "DataConsistencyUpdated",
                "DataConsistencyEngine",
                {"status": status, "mismatch_count": len(issues), "checked": checked},
            )
        except Exception:
            pass
        return self.last

    def summary(self) -> dict[str, Any]:
        return dict(self.last)
