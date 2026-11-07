"""Prediction accuracy foundation for AION EMS Zeus.

Compares the nearest available forecast row with current measured state. The
engine is recommendation-only and never calls services or controls devices.
"""
from __future__ import annotations

import math

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from ..flow_access import flow_soc, flow_w
from .forecast_v21_shadow import evaluate_shadow
from .forecast_v21_weather import summarize_weather_history

from homeassistant.components.energy import async_get_manager
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util


class PredictionAccuracyEngine:
    """Track real forward forecast-versus-measured comparisons.

    Forecasts are captured before their target time and only scored after the
    target matures. This avoids the false-confidence problem of comparing a
    freshly recalculated forecast with the measurement that already exists.
    """

    LEAD_HOURS = (1, 3, 6, 12, 24)
    METRICS = ("solar", "home", "grid_import", "grid_export", "battery_soc")
    STORAGE_VERSION = 1
    STORAGE_KEY = "aion_ems_zeus.prediction_accuracy"

    def __init__(self, event_bus: Any, core: Any) -> None:
        self.event_bus = event_bus
        self.core = core
        self.store = Store(core.hass, self.STORAGE_VERSION, self.STORAGE_KEY)
        self._samples: deque[dict[str, Any]] = deque(maxlen=168)
        self._pending: deque[dict[str, Any]] = deque(maxlen=96)
        self._queued_markers: deque[str] = deque(maxlen=384)
        self._daily_pending: deque[dict[str, Any]] = deque(maxlen=45)
        self._daily_samples: deque[dict[str, Any]] = deque(maxlen=120)
        self._forward_shadow_pending: deque[dict[str, Any]] = deque(maxlen=45)
        self._forward_shadow_outcomes: deque[dict[str, Any]] = deque(maxlen=120)
        self._hourly_frozen: deque[dict[str, Any]] = deque(maxlen=45)
        self._hourly_capture_status: dict[str, Any] = {"status": "waiting_for_pre_day_snapshot"}
        self._hourly_actuals_status: dict[str, Any] = {"status": "awaiting_completed_target_day"}
        self._cloud_pending: deque[dict[str, Any]] = deque(maxlen=45)
        self._cloud_outcomes: deque[dict[str, Any]] = deque(maxlen=120)
        self._cloud_capture_attempt: dict[str, Any] = {
            "status": "not_attempted", "reason": "Waiting for capture window",
            "attempted_at": None, "target_date": None,
        }
        self._summary: dict[str, Any] = {
            "status": "Collecting",
            "sample_count": 0,
            "overall_accuracy_percent": None,
            "trust_percent": None,
            "trust_label": "Collecting",
            "metrics": {},
            "metric_detail": {},
            "lead_time_accuracy": {},
            "recent_samples": [],
            "pending_forecasts": 0,
            "mode": "measurement_only",
            "control_permission": False,
        }


    async def async_load(self) -> None:
        """Restore genuine pre-target forecast evidence across HA restarts."""
        stored = await self.store.async_load()
        if not isinstance(stored, dict):
            return
        for attr, key, limit in (
            ("_samples", "samples", 168),
            ("_pending", "pending", 96),
            ("_daily_pending", "daily_pending", 45),
            ("_daily_samples", "daily_samples", 120),
            ("_forward_shadow_pending", "forward_shadow_pending", 45),
            ("_forward_shadow_outcomes", "forward_shadow_outcomes", 120),
            ("_hourly_frozen", "hourly_frozen", 45),
            ("_cloud_pending", "cloud_pending", 45),
            ("_cloud_outcomes", "cloud_outcomes", 120),
        ):
            values = stored.get(key)
            if isinstance(values, list):
                setattr(self, attr, deque((x for x in values if isinstance(x, dict)), maxlen=limit))
        markers = stored.get("queued_markers")
        if isinstance(markers, list):
            self._queued_markers = deque((str(x) for x in markers if x), maxlen=384)

    def _schedule_save(self) -> None:
        """Persist captured forecasts/outcomes without blocking the refresh path."""
        payload = {
            "samples": list(self._samples),
            "pending": list(self._pending),
            "queued_markers": list(self._queued_markers),
            "daily_pending": list(self._daily_pending),
            "daily_samples": list(self._daily_samples),
            "forward_shadow_pending": list(self._forward_shadow_pending),
            "forward_shadow_outcomes": list(self._forward_shadow_outcomes),
            "hourly_frozen": list(self._hourly_frozen),
            "cloud_pending": list(self._cloud_pending),
            "cloud_outcomes": list(self._cloud_outcomes),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.core.hass.async_create_task(self.store.async_save(payload))
        except Exception:
            pass

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _accuracy(predicted: float | None, actual: float | None, metric: str = "") -> float | None:
        if predicted is None or actual is None:
            return None
        floor = 2.0 if metric == "battery_soc" else 150.0
        scale = max(abs(predicted), abs(actual), floor)
        return round(max(0.0, min(100.0, (1.0 - abs(predicted - actual) / scale) * 100.0)), 1)

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _actual(self) -> dict[str, float | None]:
        flow = self.core.energy_flow.summary() or {}
        return {
            "solar": flow_w(flow, "solar_power"),
            "home": flow_w(flow, "house_power"),
            "grid_import": flow_w(flow, "grid_import_power"),
            "grid_export": flow_w(flow, "grid_export_power"),
            "battery_soc": flow_soc(flow),
        }

    def _timeline(self) -> list[dict[str, Any]]:
        forecast = self.core.forecast.summary() or {}
        rows = forecast.get("timeline_24h") or forecast.get("hourly") or forecast.get("forecast") or []
        return rows if isinstance(rows, list) else []

    def _capture_forward_forecasts(self, now: datetime) -> None:
        rows = self._timeline()
        if not rows:
            return
        changed = False
        for lead in self.LEAD_HOURS:
            desired = now + timedelta(hours=lead)
            nearest: tuple[float, dict[str, Any], datetime] | None = None
            for row in rows:
                stamp = self._parse_time(row.get("time") or row.get("datetime"))
                if stamp is None or stamp <= now:
                    continue
                distance = abs((stamp - desired).total_seconds())
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, row, stamp)
            if nearest is None or nearest[0] > 45 * 60:
                continue
            _, row, target = nearest
            marker = f"{lead}:{target.isoformat()}"
            if marker in self._queued_markers:
                continue
            predicted = {
                "solar": self._num(row.get("solar_power_w")),
                "home": self._num(row.get("house_power_w")),
                "grid_import": self._num(row.get("grid_import_power_w")),
                "grid_export": self._num(row.get("grid_export_power_w")),
                "battery_soc": self._num(row.get("projected_battery_soc_percent")),
            }
            if not any(value is not None for value in predicted.values()):
                continue
            self._pending.append({
                "created_at": now.isoformat(),
                "target_time": target.isoformat(),
                "lead_hours": lead,
                "predicted": predicted,
            })
            self._queued_markers.append(marker)
            changed = True
        if changed:
            self._schedule_save()

    def _mature_forecasts(self, now: datetime, actual: dict[str, float | None]) -> None:
        keep: deque[dict[str, Any]] = deque(maxlen=self._pending.maxlen)
        changed = False
        for pending in self._pending:
            target = self._parse_time(pending.get("target_time"))
            if target is None:
                continue
            if now < target:
                keep.append(pending)
                continue
            age = (now - target).total_seconds()
            # A late refresh is not an aligned measurement. Discard it instead
            # of manufacturing an accuracy score from the wrong point in time.
            if age > 20 * 60:
                continue
            predicted = pending.get("predicted") or {}
            accuracies = {key: self._accuracy(self._num(predicted.get(key)), actual.get(key), key) for key in self.METRICS}
            valid = [value for value in accuracies.values() if value is not None]
            if not valid:
                continue
            errors = {key: (round(actual[key] - self._num(predicted.get(key)), 1) if actual.get(key) is not None and self._num(predicted.get(key)) is not None else None) for key in self.METRICS}
            self._samples.append({
                "timestamp": now.isoformat(),
                "forecast_created_at": pending.get("created_at"),
                "forecast_time": pending.get("target_time"),
                "lead_hours": pending.get("lead_hours"),
                "accuracy_percent": round(sum(valid) / len(valid), 1),
                "metrics": accuracies,
                "errors": errors,
                "predicted": predicted,
                "actual": dict(actual),
            })
            changed = True
        if len(keep) != len(self._pending):
            changed = True
        self._pending = keep
        if changed:
            self._schedule_save()

    def _canonical_daily_rows(self) -> list[dict[str, Any]]:
        analytics = getattr(self.core, "analytics", None)
        if analytics is None or not callable(getattr(analytics, "summary", None)):
            return []
        try:
            summary = analytics.summary() or {}
        except Exception:
            return []
        rows = list(((summary.get("chart_history") or {}).get("total") or []))
        return [dict(row) for row in rows if isinstance(row, dict) and row.get("date")]

    @classmethod
    def _frozen_daily_weather_evidence(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Capture only target-day weather evidence present at snapshot time.

        Do not infer coverage from current conditions, a later forecast, or
        a generic next-24-hour quality score. None means unverified.
        """
        raw = cls._num(row.get("weather_coverage_percent"))
        diagnostics = row.get("weather_match_diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        matched = cls._num(diagnostics.get("matched_rows"))
        expected = cls._num(diagnostics.get("expected_rows"))
        if matched is not None and expected is not None and expected > 0 and 0 <= matched <= expected:
            return {"weather_coverage": round(matched / expected, 6),
                    "weather_coverage_source": "target_day_hourly_match",
                    "weather_matched_hours": int(matched),
                    "weather_expected_hours": int(expected)}
        if raw is not None and 0 <= raw <= 100:
            return {"weather_coverage": round(raw / 100, 6),
                    "weather_coverage_source": "target_day_forecast_percent"}
        return {"weather_coverage": None,
                "weather_coverage_source": "missing_at_snapshot"}

    def _capture_daily_forecast(self, now: datetime) -> None:
        forecast = getattr(self.core, "forecast", None)
        if forecast is None or not callable(getattr(forecast, "summary", None)):
            return
        try:
            summary = forecast.summary() or {}
        except Exception:
            return
        daily = summary.get("daily_forecast") or summary.get("days") or []
        if not isinstance(daily, list):
            return
        tomorrow = (now.astimezone().date() + timedelta(days=1)).isoformat()
        row = next((x for x in daily if isinstance(x, dict) and str(x.get("date") or "") == tomorrow), None)
        if row is None or any(str(x.get("target_date") or "") == tomorrow for x in self._daily_pending):
            return
        predicted = {
            "solar_kwh": self._num(row.get("expected_solar_kwh")),
            "home_kwh": self._num(row.get("expected_consumption_kwh")),
            "grid_import_kwh": self._num(row.get("expected_grid_import_kwh")),
            "grid_export_kwh": self._num(row.get("expected_grid_export_kwh")),
        }
        if any(v is not None for v in predicted.values()):
            weather_evidence = self._frozen_daily_weather_evidence(row)
            self._daily_pending.append({"created_at": now.isoformat(), "target_date": tomorrow,
                "predicted": predicted, "evidence_method": row.get("evidence_method"),
                **weather_evidence})
            self._schedule_save()

    def _mature_daily_forecasts(self, now: datetime) -> None:
        rows = {str(r.get("date")): r for r in self._canonical_daily_rows()}
        keep: deque[dict[str, Any]] = deque(maxlen=self._daily_pending.maxlen)
        matured = False
        today = now.astimezone().date().isoformat()
        for pending in self._daily_pending:
            target = str(pending.get("target_date") or "")
            if not target or target >= today:
                keep.append(pending); continue
            actual_row = rows.get(target)
            if actual_row is None:
                keep.append(pending); continue
            predicted = pending.get("predicted") or {}
            actual = {
                "solar_kwh": self._num(actual_row.get("solar_energy_kwh")),
                "home_kwh": self._num(actual_row.get("house_energy_kwh")),
                "grid_import_kwh": self._num(actual_row.get("grid_import_energy_kwh")),
                "grid_export_kwh": self._num(actual_row.get("grid_export_energy_kwh")),
            }
            metrics,errors={},{}
            for key in actual:
                pred,act=self._num(predicted.get(key)),actual.get(key)
                if pred is None or act is None:
                    metrics[key]=None;errors[key]=None;continue
                scale=max(abs(act),abs(pred),0.25)
                metrics[key]=round(max(0.0,100.0-abs(act-pred)/scale*100.0),1)
                errors[key]=round(act-pred,3)
            self._daily_samples.append({"target_date":target,"forecast_created_at":pending.get("created_at"),"predicted":predicted,"actual":actual,"metrics":metrics,"errors":errors,"evidence_method":pending.get("evidence_method"),
                "weather_coverage":pending.get("weather_coverage"),
                "weather_coverage_source":pending.get("weather_coverage_source"),
                "weather_matched_hours":pending.get("weather_matched_hours"),
                "weather_expected_hours":pending.get("weather_expected_hours")})
            matured = True
        if len(keep) != len(self._daily_pending):
            matured = True
        self._daily_pending=keep
        if matured:
            self._schedule_save()

    def _capture_cloud_comparison(self, now: datetime) -> None:
        """Capture the existing synchronized forecast, with explicit diagnostics."""
        local = dt_util.as_local(now)
        if not (22 <= local.hour <= 23):
            return
        target = (local.date() + timedelta(days=1)).isoformat()
        status = {
            "attempted_at": now.isoformat(), "target_date": target,
            "status": "pending", "reason": None,
        }
        if any(item.get("target_date") == target for item in self._cloud_pending):
            status.update(status="already_captured", reason="Pending snapshot exists")
            self._cloud_capture_attempt = status
            return
        if any(item.get("target_date") == target for item in self._cloud_outcomes):
            status.update(status="already_matched", reason="Completed snapshot exists")
            self._cloud_capture_attempt = status
            return
        try:
            # Read the same published forecast attributes used by the Zeus
            # frontend, rather than relying solely on the engine summary,
            # which can lag behind the published sensor after refreshes.
            published = self.core.hass.states.get("sensor.aion_ems_zeus_forecast")
            published_days = (published.attributes.get("daily_forecast") or []) if published else []
            engine_days = self.core.forecast.summary().get("daily_forecast") or []
            candidates = [
                ("published_forecast_sensor", published_days),
                ("forecast_engine_summary", engine_days),
            ]
            valid = None
            evidence = {}
            for source_name, days in candidates:
                if not isinstance(days, list):
                    evidence[source_name] = "Daily forecast data is not a list"
                    continue
                # Match the Forecast page: it prefers the Tomorrow-labelled
                # daily row, falling back to the second row. Do not silently
                # select a different duplicate date row with empty candidates.
                labelled = [r for r in days if isinstance(r, dict) and r.get("label") == "Tomorrow"]
                dated = [r for r in days if isinstance(r, dict) and r.get("date") == target]
                fallback = [days[1]] if len(days) > 1 and isinstance(days[1], dict) else []
                rows = []
                for row in labelled + fallback + dated:
                    if not any(existing is row for existing in rows):
                        rows.append(row)
                failures = []
                for row in rows:
                    diag = row.get("solar_forecast_diagnostics") or {}
                    shadow = diag.get("shadow_cloud_comparison") or {}
                    if not shadow:
                        failures.append(f"{row.get('label', '?')}: no cloud comparison")
                        continue
                    if shadow.get("target_date") != target or row.get("date") != target:
                        failures.append(f"{row.get('label', '?')}: target date mismatch")
                        continue
                    forecasts = {
                        "current": self._num(shadow.get("current_production_forecast_kwh")),
                        "candidate_a": self._num(shadow.get("moderate_cloud_kwh")),
                        "candidate_b": self._num(shadow.get("lighter_cloud_kwh")),
                    }
                    missing = [
                        key for key, value in forecasts.items()
                        if value is None or not math.isfinite(value) or value < 0
                    ]
                    if missing:
                        failures.append(
                            f"{row.get('label', '?')}: missing/invalid {', '.join(missing)} "
                            f"(comparison status: {shadow.get('cloud_comparison_status', 'unknown')})"
                        )
                        continue
                    valid = (source_name, shadow, forecasts)
                    break
                if valid is not None:
                    break
                evidence[source_name] = "; ".join(failures[:4]) or "No target-date row"
            if valid is None:
                status.update(
                    status="waiting",
                    reason="No complete synchronized comparison available",
                    source_diagnostics=evidence,
                )
                return
            source_name, shadow, forecasts = valid
            status["source"] = source_name
            self._cloud_pending.append({
                "target_date": target, "captured_at": now.isoformat(),
                "forecasts_kwh": forecasts,
                "weather_evidence": shadow.get("cloud_comparison_status"),
                "source": source_name,
            })
            status.update(status="captured", reason="Synchronized forecast stored")
            self._schedule_save()
        except (AttributeError, TypeError, ValueError) as exc:
            status.update(status="error", reason=f"Forecast evidence read failed: {type(exc).__name__}")
        finally:
            self._cloud_capture_attempt = status

    def _freeze_array_candidate(self, target: str) -> dict[str, Any]:
        """Freeze one pre-day geometry/weather candidate or an explicit rejection.

        The result is evidence only: no calibration, model selection or control.
        A complete 23/24/25-hour weather match is mandatory.
        """
        base = {"status": "unavailable", "candidate_kwh": None,
                "production_applied": False, "control_permission": False,
                "accuracy_validated": False}
        try:
            from .forecast_v21_array_geometry import (
                geometry_shadow, weather_geometry_shadow, array_energy_candidate_shadow)
            home = (self.core.settings_api.summary() or {}).get("home_settings") or {}
            arrays = list(home.get("pv_arrays") or [])[:12]
            config = self.core.hass.config
            geometry = geometry_shadow(arrays, target, config.latitude, config.longitude,
                                       str(config.time_zone or "UTC"))
            weather = self.core.weather.summary() if self.core.weather else {}
            adjusted = weather_geometry_shadow(geometry, weather,
                self.core.weather.factor_for if self.core.weather else None)
            candidate = array_energy_candidate_shadow(geometry, adjusted)
            if candidate.get("status") != "candidate_available":
                return {**base, "status": candidate.get("status", "unavailable")}
            hourly = candidate.get("hourly") or []
            if len(hourly) != geometry.get("hour_count"):
                return {**base, "status": "incomplete_hourly_candidate"}
            # Keep immutable compact per-hour values and the assumptions used.
            return {**base, "status": "frozen", "candidate_kwh": candidate["candidate_kwh"],
                    "array_candidates": candidate.get("array_candidates") or [],
                    "hourly": [{"hour_local": h.get("hour_local"),
                                "total_candidate_kwh": h.get("total_candidate_kwh")}
                               for h in hourly],
                    "assumptions": candidate.get("assumptions") or {},
                    "model": candidate.get("model"), "calibrated": False}
        except (AttributeError, TypeError, ValueError, KeyError, ImportError, OverflowError) as exc:
            return {**base, "status": "capture_error", "reason": type(exc).__name__}

    def capture_hourly_forecast_tick(self) -> None:
        """Freeze the first complete pre-day hourly forecast; never overwrite it.

        A frozen row is evidence, not a scored observation. No actual hourly
        values are inferred from daily totals or mutable live power readings.
        """
        now = datetime.now(timezone.utc)
        today = dt_util.as_local(now).date()
        result = {"status": "waiting_for_pre_day_snapshot", "checked_at": now.isoformat()}
        try:
            daily = (self.core.forecast.summary() or {}).get("daily_forecast") or []
            for day in daily:
                target = str(day.get("date") or "")
                try:
                    from datetime import date
                    target_day = date.fromisoformat(target)
                except (ValueError, TypeError):
                    continue
                if target_day <= today or (target_day - today).days != 1:
                    continue
                if any(item.get("target_date") == target for item in self._hourly_frozen):
                    result = {"status": "already_frozen", "target_date": target}
                    break
                audit = (day.get("solar_forecast_diagnostics") or {}).get("hourly_pipeline_audit") or {}
                hours = audit.get("hours") or []
                if not isinstance(hours, list) or len(hours) not in (23, 24, 25):
                    result = {"status": "incomplete_hourly_evidence", "target_date": target, "hours": len(hours) if isinstance(hours, list) else 0, "reason": "missing_nested_hourly_audit" if not audit else "unexpected_hour_count", "source": "daily_forecast.solar_forecast_diagnostics.hourly_pipeline_audit"}
                    break
                frozen = []
                seen = set()
                invalid_reason = None
                for index, row in enumerate(hours):
                    if not isinstance(row, dict):
                        invalid_reason = f"row_{index}_not_object"
                        break
                    stamp = self._parse_time(row.get("time_utc"))
                    original = self._num(row.get("final_w"))
                    guarded = self._num(row.get("daylight_guard_candidate_w"))
                    if stamp is None or original is None or guarded is None or not math.isfinite(original) or not math.isfinite(guarded):
                        invalid_reason = f"row_{index}_missing_timestamp_or_watts"
                        break
                    key = stamp.isoformat()
                    if key in seen:
                        invalid_reason = f"row_{index}_duplicate_timestamp"
                        break
                    seen.add(key)
                    frozen.append({"start_utc": key, "original_w": round(original, 3), "guarded_w": round(guarded, 3)})
                if len(frozen) != len(hours):
                    result = {"status": "invalid_hourly_evidence", "target_date": target, "reason": invalid_reason or "incomplete_rows", "accepted_hours": len(frozen)}
                    break
                # Capture the array candidate in the SAME immutable pre-day
                # evidence event as the existing V2 hourly forecast. The
                # candidate is not recalculated when evaluating later actuals.
                array_snapshot = self._freeze_array_candidate(target)
                self._hourly_frozen.append({
                    "target_date": target, "captured_at_utc": now.isoformat(),
                    "source": "forecast_daily_hourly_pipeline_audit",
                    "hour_count": len(frozen), "hours": frozen,
                    "array_candidate_frozen": array_snapshot,
                    "measurement_source": None, "actuals_paired": False,
                })
                self._schedule_save()
                result = {"status": "frozen", "target_date": target, "hour_count": len(frozen)}
                break
        except (AttributeError, TypeError, ValueError) as exc:
            result = {"status": "capture_error", "error_type": type(exc).__name__}
        self._hourly_capture_status = result

    async def async_pair_completed_hourly_actuals(self) -> None:
        """Pair completed frozen hours using verified Recorder source evidence.

        An obsolete Energy dashboard source with no target-day rows may be
        excluded only when all remaining sources are complete and their total
        reconciles with Zeus's independent completed-day actual. Partial sources
        are never silently dropped. Blocked older days do not starve later days.
        """
        from .hourly_source_evidence import select_hourly_solar_sources

        now = datetime.now(timezone.utc)
        today = dt_util.as_local(now).date()
        pending = [x for x in self._hourly_frozen if not x.get("actuals_paired")
                   and x.get("target_date", "9999") < today.isoformat()]
        if not pending:
            self._hourly_actuals_status = {"status": "awaiting_completed_target_day", "pending": 0}
            return

        outcomes = []
        for item in pending:
            target = item["target_date"]
            try:
                from datetime import date, time
                local_tz = dt_util.get_default_time_zone()
                local_start = datetime.combine(date.fromisoformat(target), time.min, tzinfo=local_tz)
                local_end = datetime.combine(date.fromisoformat(target) + timedelta(days=1), time.min, tzinfo=local_tz)
                start, end = local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
                hours = item.get("hours") or []
                expected = {self._parse_time(h.get("start_utc")) for h in hours}
                expected.discard(None)
                if len(expected) != len(hours) or len(hours) != int((end-start).total_seconds()/3600):
                    outcomes.append({"status": "invalid_frozen_timestamps", "target_date": target})
                    continue
                mappings = (getattr(self.core.registry, "data", {}) or {}).get("entity_mappings", {}) or {}
                fallback = str(mappings.get("solar_energy_total") or mappings.get("solar_energy_today") or "").strip()
                ids = []
                manager = await async_get_manager(self.core.hass)
                getter = getattr(manager, "async_get_preferences", None)
                prefs = await getter() if callable(getter) else getattr(manager, "data", None)
                if isinstance(prefs, dict):
                    ids = [str(x.get("stat_energy_from") or "").strip() for x in (prefs.get("energy_sources") or [])
                           if isinstance(x, dict) and x.get("type") == "solar"]
                ids = list(dict.fromkeys(x for x in (ids or [fallback]) if x))
                if not ids:
                    outcomes.append({"status": "missing_solar_statistic_source", "target_date": target})
                    continue
                response = await self.core.hass.services.async_call(
                    "recorder", "get_statistics", {"statistic_ids": ids,
                        "start_time": start, "end_time": end, "period": "hour",
                        "types": ["change"], "units": {"energy": "kWh"}},
                    blocking=True, return_response=True)
                raw = (response or {}).get("statistics", response or {})
                by_source = {}
                invalid_sources = []
                for eid in ids:
                    per_source = {}
                    source_rows = (raw or {}).get(eid) or []
                    for row in source_rows:
                        stamp = row.get("start")
                        if isinstance(stamp, (float, int)):
                            stamp = datetime.fromtimestamp(stamp, timezone.utc)
                        else:
                            stamp = self._parse_time(stamp)
                        val = self._num(row.get("change"))
                        if (stamp not in expected or stamp in per_source or val is None
                                or not math.isfinite(val) or val < -0.000001):
                            continue
                        per_source[stamp] = max(0.0, val)
                    by_source[eid] = per_source
                    # A source with rows but no accepted hours is not obsolete:
                    # malformed/negative/duplicate statistics must fail closed.
                    if source_rows and not per_source:
                        invalid_sources.append(eid)
                if invalid_sources:
                    outcomes.append({"status": "invalid_recorder_source_rows", "target_date": target,
                                     "sources": invalid_sources})
                    continue

                # Only request the independent daily authority when a source is
                # completely empty; the all-complete path is unchanged.
                canonical = None
                if any(not rows for rows in by_source.values()):
                    daily_rows = {str(r.get("date")): r for r in self._canonical_daily_rows()}
                    canonical, _ = self._frozen_completed_day_solar_actual(target, daily_rows)
                selection = select_hourly_solar_sources(by_source, expected, canonical)
                if selection["status"] != "ready":
                    outcomes.append({"target_date": target, **selection})
                    continue
                actual = {t: sum(by_source[eid][t] for eid in selection["included_sources"])
                          for t in expected}
                pairs = []
                for row in hours:
                    stamp = self._parse_time(row["start_utc"])
                    measured = actual[stamp]
                    original = float(row["original_w"]) / 1000
                    guarded = float(row["guarded_w"]) / 1000
                    pairs.append({"start_utc": stamp.isoformat(), "actual_kwh": round(measured, 5),
                                  "original_kwh": round(original, 5), "guarded_kwh": round(guarded, 5)})
                n = len(pairs)
                mae_original = sum(abs(r["original_kwh"] - r["actual_kwh"]) for r in pairs)/n
                mae_guarded = sum(abs(r["guarded_kwh"] - r["actual_kwh"]) for r in pairs)/n
                item["actuals_paired"] = True
                item["measurement_source"] = "ha_recorder_hourly_energy_change"
                item["statistic_ids"] = selection["included_sources"]
                item["excluded_empty_statistic_ids"] = selection["excluded_empty_sources"]
                item["source_reconciliation"] = selection["reconciliation"]
                item["paired_at_utc"] = now.isoformat()
                item["accuracy"] = {"matched_hours": n, "original_hourly_mae_kwh": round(mae_original, 4),
                    "guarded_hourly_mae_kwh": round(mae_guarded, 4),
                    "actual_daily_kwh": round(sum(r["actual_kwh"] for r in pairs), 3),
                    "original_daily_kwh": round(sum(r["original_kwh"] for r in pairs), 3),
                    "guarded_daily_kwh": round(sum(r["guarded_kwh"] for r in pairs), 3),
                    "original_daily_absolute_error_kwh": round(abs(sum(r["original_kwh"]-r["actual_kwh"] for r in pairs)), 3),
                    "guarded_daily_absolute_error_kwh": round(abs(sum(r["guarded_kwh"]-r["actual_kwh"] for r in pairs)), 3)}
                # Score only a candidate frozen before the target day, using
                # the same reconciled Recorder actuals as the V2 comparison.
                frozen_array = item.get("array_candidate_frozen") or {}
                if frozen_array.get("status") == "frozen":
                    predicted = self._num(frozen_array.get("candidate_kwh"))
                    measured = sum(r["actual_kwh"] for r in pairs)
                    if predicted is not None and math.isfinite(predicted) and predicted >= 0:
                        item["array_candidate_accuracy"] = {
                            "actual_daily_kwh": round(measured, 3),
                            "candidate_daily_kwh": round(predicted, 3),
                            "absolute_error_kwh": round(abs(predicted - measured), 3),
                            "signed_error_kwh": round(predicted - measured, 3),
                            "comparison_source": "same_reconciled_recorder_hourly_actuals",
                            "accuracy_validated": False,
                            "production_applied": False, "control_permission": False,
                        }
                self._schedule_save()
                outcomes.append({"status": "paired", "target_date": target, "matched_hours": n,
                                 "included_sources": selection["included_sources"],
                                 "excluded_empty_sources": selection["excluded_empty_sources"],
                                 "reconciliation": selection["reconciliation"]})
            except Exception as exc:
                outcomes.append({"status": "recorder_query_error", "target_date": target,
                                 "error_type": type(exc).__name__})
        paired_count = sum(x["status"] == "paired" for x in outcomes)
        blocked = [x for x in outcomes if x["status"] != "paired"]
        self._hourly_actuals_status = {
            "status": "paired" if paired_count and not blocked else
                      "partial_progress" if paired_count else "blocked",
            "paired_days": paired_count, "blocked_days": len(blocked),
            "recent_outcomes": outcomes[-8:],
        }

    def _array_candidate_forward_summary(self) -> dict[str, Any]:
        """Describe frozen forward evidence without approving the candidate."""
        rows = [item for item in self._hourly_frozen
                if isinstance(item.get("array_candidate_accuracy"), dict)]
        errors = [float(item["array_candidate_accuracy"]["absolute_error_kwh"])
                  for item in rows]
        return {"status": "forward_comparison" if rows else "awaiting_frozen_actual_pairs",
                "matched_days": len(rows),
                "mean_absolute_error_kwh": round(sum(errors) / len(errors), 3) if errors else None,
                "recent": [{"target_date": item.get("target_date"),
                            **item["array_candidate_accuracy"]} for item in rows[-7:]],
                "frozen_candidate_days": sum(
                    (item.get("array_candidate_frozen") or {}).get("status") == "frozen"
                    for item in self._hourly_frozen),
                "accuracy_validated": False, "production_applied": False,
                "control_permission": False}

    def hourly_evidence_summary(self) -> dict[str, Any]:
        return {
            "status": "collecting_frozen_hourly_forecasts",
            "frozen_days": len(self._hourly_frozen),
            "capture": dict(self._hourly_capture_status),
            "recent_snapshots": [{k: v for k, v in item.items() if k != "hours"} for item in list(self._hourly_frozen)[-5:]],
            "paired_measured_hours": sum(len(x.get("hours") or []) for x in self._hourly_frozen if x.get("actuals_paired")),
            "accuracy_status": "paired" if any(x.get("actuals_paired") for x in self._hourly_frozen) else "awaiting_recorder_hourly_actuals",
            "actuals_capture": dict(self._hourly_actuals_status),
            "array_candidate_forward_evidence": self._array_candidate_forward_summary(),
            "production_applied": False, "control_permission": False,
        }

    def capture_cloud_snapshot_tick(self) -> None:
        """Independent lightweight capture attempt; never recalculates forecasts."""
        now = datetime.now(timezone.utc)
        self._mature_cloud_comparisons(now)
        self._capture_cloud_comparison(now)
        # Refresh the exposed summary without running the full forecasting engine.
        if self._summary:
            self._summary["cloud_model_accuracy"] = self._cloud_comparison_summary()
            self._summary["updated_at"] = now.isoformat()

    def _frozen_completed_day_solar_actual(self, target: str, rows: dict) -> tuple[float | None, str]:
        """Reuse the canonical daily actual frozen before later Recorder revisions."""
        for sample in self._daily_samples:
            if sample.get("target_date") != target:
                continue
            value = self._num((sample.get("actual") or {}).get("solar_kwh"))
            if value is not None and math.isfinite(value) and value >= 0:
                return value, "frozen_daily_sample"
        row = rows.get(target)
        value = self._num(row.get("solar_energy_kwh")) if row else None
        if value is None or not math.isfinite(value) or value < 0:
            return None, "missing_canonical_daily_total"
        return value, "canonical_completed_day"

    def _mature_cloud_comparisons(self, now: datetime) -> None:
        today = dt_util.as_local(now).date().isoformat()
        actual_rows = {str(r.get("date")): r for r in self._canonical_daily_rows()}
        remaining = deque(maxlen=self._cloud_pending.maxlen)
        changed = False
        for snapshot in self._cloud_pending:
            target = snapshot.get("target_date")
            if not target or target >= today:
                remaining.append(snapshot)
                continue
            actual, actual_source = self._frozen_completed_day_solar_actual(target, actual_rows)
            if actual is None:
                remaining.append(snapshot)
                continue
            predictions = snapshot.get("forecasts_kwh") or {}
            errors = {k: round(v - actual, 3) for k, v in predictions.items() if isinstance(v, (int, float))}
            self._cloud_outcomes.append({**snapshot, "actual_kwh": round(actual, 3), "actual_source": actual_source,
                "errors_kwh": errors, "absolute_errors_kwh": {k: abs(v) for k, v in errors.items()}})
            changed = True
        self._cloud_pending = remaining
        if changed:
            self._schedule_save()

    def _cloud_comparison_summary(self) -> dict[str, Any]:
        result = {}
        for model in ("current", "candidate_a", "candidate_b"):
            errors = [float(o["errors_kwh"][model]) for o in self._cloud_outcomes if model in (o.get("errors_kwh") or {})]
            result[model] = {"matched_days": len(errors),
                "mean_absolute_error_kwh": round(sum(map(abs, errors))/len(errors), 3) if errors else None,
                "mean_signed_error_kwh": round(sum(errors)/len(errors), 3) if errors else None}
        return {"models": result, "pending_count": len(self._cloud_pending),
            "matched_count": len(self._cloud_outcomes),
            "pending": list(self._cloud_pending)[-3:],
            "recent_outcomes": list(self._cloud_outcomes)[-14:],
            "status": "Read-only; no production correction",
            "capture_window": "22:00–23:59 Home Assistant local time",
            "actual_source": "canonical_completed_day_recorder_solar_energy",
            "last_capture_attempt": dict(self._cloud_capture_attempt)}

    def v21_shadow_summary(self) -> dict[str, Any]:
        """Read-only V2 vs V2.1 candidate, never used for production planning."""
        from homeassistant.util import dt as dt_util
        now = dt_util.now()
        target = (now.date() + timedelta(days=1)).isoformat()
        try:
            forecast = self.core.forecast.summary() or {}
            days = forecast.get("daily_forecast") or forecast.get("days") or []
            row = next((d for d in days if isinstance(d, dict) and str(d.get("date")) == target), None)
            if row is None:
                return {"status":"forecast_unavailable", "target_date":target, "production_applied":False}
            current = self._num(row.get("expected_solar_kwh"))
            coverage = self._num(row.get("weather_coverage_percent"))
            if current is None or not math.isfinite(current) or current < 0:
                return {"status":"forecast_unavailable", "target_date":target, "production_applied":False}
            # Read-only provenance audit: never backfill missing historical
            # weather coverage from today's weather or a later forecast.
            audit = {"stored_daily_samples": len(self._daily_samples),
                     "pending_daily_forecasts": len(self._daily_pending),
                     "rejections": {"missing_forecast": 0, "missing_actual": 0,
                                    "missing_weather_coverage": 0,
                                    "invalid_forecast_timestamp": 0,
                                    "not_pre_day_snapshot": 0,
                                    "low_weather_coverage": 0},
                     "qualified_pre_day_days": 0,
                     "history_source": "prediction_accuracy_daily_samples",
                     "legacy_samples_without_coverage": sum(
                         1 for sample in self._daily_samples
                         if self._num(sample.get("weather_coverage")) is None),
                     "weather_qualified_samples": sum(
                         1 for sample in self._daily_samples
                         if self._num(sample.get("weather_coverage")) is not None),
                     "pending_with_frozen_weather_evidence": sum(
                         1 for pending in self._daily_pending
                         if self._num(pending.get("weather_coverage")) is not None),
                     "pending_weather_coverage_below_80_percent": sum(
                         1 for pending in self._daily_pending
                         if self._num(pending.get("weather_coverage")) is not None
                         and self._num(pending.get("weather_coverage")) < 0.8),
                     "pending_missing_weather_evidence": sum(
                         1 for pending in self._daily_pending
                         if self._num(pending.get("weather_coverage")) is None)}
            # Read-only V2.1 audit: inspect pending finalization and hourly
            # weather horizon without changing forecasts or stored evidence.
            actual_rows = {str(r.get("date")): r for r in self._canonical_daily_rows()}
            pending_details = []
            for pending in self._daily_pending:
                day = str(pending.get("target_date") or "")
                actual_row = actual_rows.get(day)
                actual_kwh = self._num(actual_row.get("solar_energy_kwh")) if actual_row else None
                pending_details.append({
                    "target_date": day,
                    "forecast_created_at": pending.get("created_at"),
                    "weather_coverage": self._num(pending.get("weather_coverage")),
                    "weather_coverage_source": pending.get("weather_coverage_source"),
                    "actual_row_available": actual_row is not None,
                    "actual_solar_kwh_available": actual_kwh is not None,
                    "target_day_complete": bool(day and day < now.date().isoformat()),
                    "ready_to_finalize": bool(day and day < now.date().isoformat() and actual_kwh is not None),
                })
            audit["finalization_audit"] = {
                "local_today": now.date().isoformat(),
                "pending": pending_details,
                "recent_completed": [
                    {"target_date": item.get("target_date"),
                     "weather_coverage": self._num(item.get("weather_coverage")),
                     "weather_coverage_source": item.get("weather_coverage_source"),
                     "forecast_created_at": item.get("forecast_created_at")}
                    for item in list(self._daily_samples)[-5:]
                ],
                "note": "Read-only; no historical samples or pending snapshots modified",
            }
            horizon_days = []
            for forecast_day in days[:10]:
                if not isinstance(forecast_day, dict):
                    continue
                match = forecast_day.get("weather_match_diagnostics")
                if not isinstance(match, dict):
                    match = {}
                horizon_days.append({
                    "date": str(forecast_day.get("date") or ""),
                    "matched_hours": self._num(match.get("matched_rows")),
                    "expected_hours": self._num(match.get("expected_rows")),
                    "coverage_percent": self._num(forecast_day.get("weather_coverage_percent")),
                    "weather_match_diagnostics": {
                        str(k): v for k, v in match.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    },
                })
            audit["weather_horizon_audit"] = {
                "status": "read_only",
                "forecast_days": horizon_days,
                "note": "Missing hourly weather is not inferred or synthesized",
            }
            history = []
            from homeassistant.util import dt as dt_util
            qualified_dates = set()
            for sample in self._daily_samples:
                predicted = self._num((sample.get("predicted") or {}).get("solar_kwh"))
                actual = self._num((sample.get("actual") or {}).get("solar_kwh"))
                wc = self._num(sample.get("weather_coverage"))
                if predicted is None:
                    audit["rejections"]["missing_forecast"] += 1
                    continue
                if actual is None:
                    audit["rejections"]["missing_actual"] += 1
                    continue
                if wc is None:
                    audit["rejections"]["missing_weather_coverage"] += 1
                    continue
                created = self._parse_time(sample.get("forecast_created_at"))
                if created is None:
                    audit["rejections"]["invalid_forecast_timestamp"] += 1
                    continue
                try:
                    day_start = datetime.fromisoformat(str(sample.get("target_date"))).replace(
                        tzinfo=dt_util.DEFAULT_TIME_ZONE).astimezone(timezone.utc)
                except (ValueError, TypeError):
                    audit["rejections"]["invalid_forecast_timestamp"] += 1
                    continue
                if created >= day_start:
                    audit["rejections"]["not_pre_day_snapshot"] += 1
                    continue
                if wc < 0.8:
                    audit["rejections"]["low_weather_coverage"] += 1
                    continue
                qualified_dates.add(sample.get("target_date"))
                history.append({"date":sample.get("target_date"), "forecast_kwh":predicted,
                                "actual_kwh":actual, "weather_coverage":wc, "verified":True})
            audit["qualified_pre_day_days"] = len(qualified_dates)
            result = evaluate_shadow(target, current, history,
                                     coverage / 100.0 if coverage is not None and 0 <= coverage <= 100 else None)
            # Keep measured weather relationships separate from frozen forecast
            # accuracy evidence. This is diagnostic-only, not a correction.
            try:
                weather_days = (self.core.weather_history.summary() or {}).get("recent_days") or []
                result["historical_weather_learning"] = summarize_weather_history(weather_days, latitude=getattr(self.core.hass.config, "latitude", None))
            except (AttributeError, TypeError, ValueError):
                result["historical_weather_learning"] = {"status": "weather_history_unavailable", "forecast_correction_applied": False}
            # Phase 12: side-by-side diagnostic only. Never select or apply
            # either candidate to the production V2 forecast or energy controls.
            try:
                from .forecast_v21_comparison import compare_evaluators
                local_tz = dt_util.get_default_time_zone()
                comparison = compare_evaluators(
                    target_date=target,
                    baseline_kwh=current,
                    weather_coverage=(coverage / 100.0 if coverage is not None and 0 <= coverage <= 100 else None),
                    samples=list(self._daily_samples),
                    local_timezone=local_tz,
                )
                result["v21_comparison_diagnostic"] = comparison
                result["v21_bridge_diagnostic"] = {
                    **comparison["independent"],
                    "baseline_v2_kwh": round(current, 3),
                    "bridge_audit": comparison["evidence_audit"],
                    "production_applied": False,
                    "control_permission": False,
                }
            except (ValueError, TypeError, KeyError, OverflowError, ImportError) as comparison_error:
                result["v21_comparison_diagnostic"] = {
                    "status": "comparison_unavailable",
                    "reason": type(comparison_error).__name__,
                    "production_applied": False,
                    "control_permission": False,
                }
                result["v21_bridge_diagnostic"] = {
                    "status": "bridge_unavailable",
                    "reason": type(comparison_error).__name__,
                    "production_applied": False,
                    "control_permission": False,
                }
            # Per-array orientation experiment: diagnostics only. This shape is
            # NOT an energy forecast and must never be applied to production.
            try:
                from .forecast_v21_array_geometry import geometry_shadow
                home = (self.core.settings_api.summary() or {}).get("home_settings") or {}
                arrays = list(home.get("pv_arrays") or [])[:12]
                config = self.core.hass.config
                result["array_geometry_shadow"] = geometry_shadow(
                    arrays, target, config.latitude, config.longitude,
                    str(config.time_zone or "UTC"))
            except (AttributeError, TypeError, ValueError, KeyError, ImportError) as geometry_error:
                result["array_geometry_shadow"] = {
                    "status": "unavailable", "reason": type(geometry_error).__name__,
                    "production_applied": False, "control_permission": False}
            # V2.1 array weather experiment: strictly read-only. Match hourly
            # weather by offset-aware instant; never alter production V2.
            try:
                from .forecast_v21_array_geometry import weather_geometry_shadow
                weather = self.core.weather.summary() if self.core.weather else {}
                result["array_weather_shadow"] = weather_geometry_shadow(
                    result.get("array_geometry_shadow"), weather,
                    self.core.weather.factor_for if self.core.weather else None)
            except (AttributeError, TypeError, ValueError, KeyError, ImportError) as weather_error:
                result["array_weather_shadow"] = {
                    "status": "unavailable", "reason": type(weather_error).__name__,
                    "production_applied": False, "control_permission": False, "forecast_kwh": None}
            # Independent geometry-to-energy experiment; assumption-based,
            # never used for production V2, V2.1 selection or device control.
            try:
                from .forecast_v21_array_geometry import array_energy_candidate_shadow
                result["array_energy_candidate_shadow"] = array_energy_candidate_shadow(
                    result.get("array_geometry_shadow"), result.get("array_weather_shadow"))
            except (AttributeError, TypeError, ValueError, KeyError, ImportError) as candidate_error:
                result["array_energy_candidate_shadow"] = {
                    "status": "unavailable", "reason": type(candidate_error).__name__,
                    "candidate_kwh": None, "production_applied": False,
                    "control_permission": False, "calibrated": False}
            # Expose immutable forward evidence in the V2.1 Shadow sensor.
            # Old snapshots remain untouched and are never backfilled.
            target_frozen = next((item for item in reversed(self._hourly_frozen)
                                  if item.get("target_date") == target), None)
            frozen_array = (target_frozen or {}).get("array_candidate_frozen") or {}
            result["array_candidate_forward_evidence"] = {
                **self._array_candidate_forward_summary(),
                "target_date": target,
                "target_snapshot_exists": target_frozen is not None,
                "target_array_status": (frozen_array.get("status", "not_captured_in_existing_snapshot")
                                        if target_frozen is not None else "awaiting_future_capture"),
                "target_frozen_candidate_kwh": (frozen_array.get("candidate_kwh")
                                                if frozen_array.get("status") == "frozen" else None),
                "capture": dict(self._hourly_capture_status),
                "source": "immutable_hourly_frozen_storage",
                "no_retroactive_backfill": True,
                "production_applied": False, "control_permission": False,
            }
            result["evidence_audit"] = audit
            result["baseline_v2_kwh"] = round(current, 3)
            result["source"] = "existing_daily_forecast_and_frozen_recorder_outcomes"
            result["note"] = "Shadow only: no control or production forecast changes. Missing historical weather evidence is never invented."
            return result
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return {"status":"invalid_evidence", "reason":type(exc).__name__,
                    "target_date":target, "production_applied":False}

    def _daily_accuracy_summary(self) -> dict[str, Any]:
        result={}
        for key in ("solar_kwh","home_kwh","grid_import_kwh","grid_export_kwh"):
            vals=[(s.get("metrics") or {}).get(key) for s in self._daily_samples if isinstance((s.get("metrics") or {}).get(key),(int,float))]
            errs=[(s.get("errors") or {}).get(key) for s in self._daily_samples if isinstance((s.get("errors") or {}).get(key),(int,float))]
            mean=sum(errs)/len(errs) if errs else None
            result[key]={"accuracy_percent":round(sum(vals)/len(vals),1) if vals else None,"matched_days":len(vals),"mean_error_kwh":round(mean,3) if mean is not None else None,"mean_absolute_error_kwh":round(sum(abs(x) for x in errs)/len(errs),3) if errs else None,"bias_direction":"balanced" if mean is None or abs(mean)<0.15 else "under_forecast" if mean>0 else "over_forecast"}
        return result

    def _adaptive_shadow_summary(self) -> dict[str, Any]:
        """Read-only bias calibration from pre-day matched forecasts.

        Diagnostic only. Historical solar actuals are not independently verified
        against inverter counters; therefore the model is never control-eligible.
        """
        records = []
        for row in self._daily_samples:
            predicted = self._num((row.get("predicted") or {}).get("solar_kwh"))
            actual = self._num((row.get("actual") or {}).get("solar_kwh"))
            created = self._parse_time(row.get("forecast_created_at"))
            target = row.get("target_date")
            if predicted is None or actual is None or predicted <= 0 or actual < 0:
                continue
            if not created or not isinstance(target, str):
                continue
            try:
                target_start = datetime.fromisoformat(target).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if created >= target_start:
                continue
            records.append((target, predicted, actual))
        # One observation per completed day; avoid double-counting duplicates.
        by_day = {day: (forecast, actual) for day, forecast, actual in records}
        recent = [(day, *values) for day, values in sorted(by_day.items())[-14:]]
        n = len(recent)
        # Median actual/forecast ratio is robust against single extreme days.
        ratios = sorted(max(0.65, min(1.35, actual / forecast)) for _, forecast, actual in recent)
        if n >= 7:
            mid = n // 2
            median = ratios[mid] if n % 2 else (ratios[mid - 1] + ratios[mid]) / 2
            # Conservative shrinkage, max +/- 20% shadow adjustment.
            multiplier = round(max(0.8, min(1.2, 1.0 + (median - 1.0) * min(1.0, n / 14.0))), 4)
        else:
            multiplier = None
        baseline_mae = round(sum(abs(forecast - actual) for _, forecast, actual in recent) / n, 3) if n else None
        shadow_mae = round(sum(abs(forecast * multiplier - actual) for _, forecast, actual in recent) / n, 3) if multiplier is not None else None
        return {
            "status": "shadow_only" if multiplier is not None else "insufficient_history",
            "matched_days": n,
            "minimum_days": 7,
            "bias_multiplier": multiplier,
            "baseline_mae_kwh": baseline_mae,
            "in_sample_shadow_mae_kwh": shadow_mae,
            "validation": "In-sample diagnostic only; not out-of-sample proof",
            "actual_source_verified": False,
            "blocking_reason": "Canonical Recorder daily solar totals must be reconciled with combined inverter counters before training or activation",
            "production_applied": False,
            "control_permission": False,
        }

    def _solar_outcome_conflicts(self) -> list[dict[str, Any]]:
        """Flag dates where stored completed-day solar totals disagree.

        Different accounting sources must never silently become training truth.
        This is diagnostic only and does not rewrite existing history.
        """
        daily = {}
        for row in self._daily_samples:
            date = row.get("target_date")
            value = self._num((row.get("actual") or {}).get("solar_kwh"))
            if isinstance(date, str) and value is not None and math.isfinite(value):
                daily[date] = value
        conflicts = []
        for source, outcomes in (
            ("cloud", self._cloud_outcomes),
            ("forward_shadow", self._forward_shadow_outcomes),
        ):
            for row in outcomes:
                date = row.get("target_date")
                value = self._num(row.get("actual_kwh"))
                if date not in daily or value is None or not math.isfinite(value):
                    continue
                expected = daily[date]
                tolerance = max(0.5, 0.02 * max(expected, value))
                if abs(expected - value) > tolerance:
                    conflicts.append({
                        "date": date, "source": source,
                        "daily_actual_kwh": round(expected, 3),
                        "other_actual_kwh": round(value, 3),
                        "difference_kwh": round(abs(expected - value), 3),
                    })
        return conflicts

    def _capture_forward_shadow(self, now: datetime) -> None:
        """Freeze a multiplier before the target day; never use target-day actuals."""
        today = dt_util.as_local(now).date().isoformat()
        existing = {x.get("target_date") for x in self._forward_shadow_pending}
        existing.update(x.get("target_date") for x in self._forward_shadow_outcomes)
        changed = False
        for pending in self._daily_pending:
            target = pending.get("target_date")
            if not isinstance(target, str) or target <= today or target in existing:
                continue
            created = self._parse_time(pending.get("created_at"))
            if created is None or created > now:
                continue
            pred = self._num((pending.get("predicted") or {}).get("solar_kwh"))
            if pred is None or not math.isfinite(pred) or pred <= 0:
                continue
            # Exclude known inconsistent completed-day outcomes from training.
            conflicted_dates = {item["date"] for item in self._solar_outcome_conflicts()}
            # Train only on earlier completed days. Exclude the target date
            # and any future dates, even if a corrupt record exists.
            historical = []
            for row in self._daily_samples:
                day = row.get("target_date")
                fc = self._num((row.get("predicted") or {}).get("solar_kwh"))
                actual = self._num((row.get("actual") or {}).get("solar_kwh"))
                stamp = self._parse_time(row.get("forecast_created_at"))
                if (not isinstance(day, str) or day >= target or
                        day in conflicted_dates or
                        fc is None or not math.isfinite(fc) or fc <= 0 or
                        actual is None or not math.isfinite(actual) or actual < 0 or
                        stamp is None):
                    continue
                try:
                    day_start = datetime.fromisoformat(day).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    continue
                if stamp >= day_start:
                    continue
                historical.append((day, fc, actual))
            by_day = {day: (fc, actual) for day, fc, actual in historical}
            recent = [by_day[day] for day in sorted(by_day)[-14:]]
            if len(recent) < 7:
                multiplier = None
            else:
                ratios = sorted(max(0.65, min(1.35, actual / fc)) for fc, actual in recent)
                n = len(ratios)
                mid = n // 2
                median = ratios[mid] if n % 2 else (ratios[mid - 1] + ratios[mid]) / 2
                multiplier = round(max(0.8, min(1.2, 1.0 + (median - 1.0) * min(1.0, n / 14.0))), 4)
            self._forward_shadow_pending.append({
                "target_date": target,
                "captured_at": now.isoformat(),
                "original_forecast_created_at": pending.get("created_at"),
                "current_kwh": pred,
                "shadow_kwh": round(pred * multiplier, 3) if multiplier is not None else None,
                "frozen_multiplier": multiplier,
                "training_days": len(recent),
                "status": "frozen" if multiplier is not None else "insufficient_history",
            })
            existing.add(target)
            changed = True
        if changed:
            self._schedule_save()

    def _mature_forward_shadow(self, now: datetime) -> None:
        """Score frozen snapshots only against completed-day Recorder totals."""
        today = dt_util.as_local(now).date().isoformat()
        actual_rows = {str(row.get("date")): row for row in self._canonical_daily_rows()}
        remaining = deque(maxlen=45)
        changed = False
        for row in self._forward_shadow_pending:
            target = row.get("target_date")
            if not isinstance(target, str) or target >= today:
                remaining.append(row)
                continue
            actual, actual_source = self._frozen_completed_day_solar_actual(target, actual_rows)
            if actual is None:
                remaining.append(row)
                continue
            current = row["current_kwh"]
            shadow = row.get("shadow_kwh")
            self._forward_shadow_outcomes.append({
                **row, "actual_kwh": actual, "actual_source": actual_source,
                "current_absolute_error_kwh": round(abs(current - actual), 3),
                "shadow_absolute_error_kwh": round(abs(shadow - actual), 3) if shadow is not None else None,
                "scored_at": now.isoformat(),
            })
            changed = True
        if changed:
            self._forward_shadow_pending = remaining
            self._schedule_save()

    def _forward_shadow_summary(self) -> dict[str, Any]:
        valid = [r for r in self._forward_shadow_outcomes if r.get("shadow_absolute_error_kwh") is not None]
        n = len(valid)
        return {
            "status": "forward_testing" if n else "collecting_forward_outcomes",
            "matched_days": n,
            "pending_count": len(self._forward_shadow_pending),
            "current_forward_mae_kwh": round(sum(r["current_absolute_error_kwh"] for r in valid) / n, 3) if n else None,
            "shadow_forward_mae_kwh": round(sum(r["shadow_absolute_error_kwh"] for r in valid) / n, 3) if n else None,
            "pending": list(self._forward_shadow_pending)[-5:],
            "recent_outcomes": list(self._forward_shadow_outcomes)[-10:],
            "actual_source": "canonical_completed_day_recorder_solar_energy",
            "actual_source_verified_against_inverters": False,
            "production_applied": False,
            "control_permission": False,
            "solar_outcome_conflicts": self._solar_outcome_conflicts(),
            "validation": "Chronological forward holdout: multiplier frozen before target date",
        }

    def refresh(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        actual = self._actual()
        self._mature_forecasts(now, actual)
        self._mature_daily_forecasts(now)
        self._capture_forward_forecasts(now)
        self._capture_daily_forecast(now)
        self._mature_forward_shadow(now)
        self._capture_forward_shadow(now)
        self._mature_cloud_comparisons(now)
        self._capture_cloud_comparison(now)
        self.capture_hourly_forecast_tick()

        metric_history: dict[str, list[float]] = {key: [] for key in self.METRICS}
        metric_errors: dict[str, list[float]] = {key: [] for key in self.METRICS}
        lead_history: dict[int, list[float]] = {lead: [] for lead in self.LEAD_HOURS}
        for sample in self._samples:
            for key, value in (sample.get("metrics") or {}).items():
                if isinstance(value, (int, float)):
                    metric_history.setdefault(key, []).append(float(value))
            for key, value in (sample.get("errors") or {}).items():
                if isinstance(value, (int, float)):
                    metric_errors.setdefault(key, []).append(float(value))
            lead = int(sample.get("lead_hours") or 0)
            if lead in lead_history and isinstance(sample.get("accuracy_percent"), (int, float)):
                lead_history[lead].append(float(sample["accuracy_percent"]))

        metrics = {key: round(sum(values) / len(values), 1) if values else None for key, values in metric_history.items()}
        metric_detail = {}
        for key in self.METRICS:
            errors = metric_errors.get(key) or []
            metric_detail[key] = {
                "accuracy_percent": metrics.get(key),
                "matched_samples": len(metric_history.get(key) or []),
                "mean_error": round(sum(errors) / len(errors), 1) if errors else None,
                "mean_absolute_error": round(sum(abs(x) for x in errors) / len(errors), 1) if errors else None,
                "unit": "%" if key == "battery_soc" else "W",
            }
        lead_time_accuracy = {
            f"{lead}h": {
                "accuracy_percent": round(sum(values) / len(values), 1) if values else None,
                "sample_count": len(values),
            } for lead, values in lead_history.items()
        }
        sample_scores = [float(s["accuracy_percent"]) for s in self._samples if isinstance(s.get("accuracy_percent"), (int, float))]
        overall = round(sum(sample_scores) / len(sample_scores), 1) if sample_scores else None
        # Trust deliberately needs several *forward* matches before a high label
        # is allowed. Accuracy alone is not treated as evidence strength.
        evidence_factor = min(1.0, len(sample_scores) / 12.0)
        trust = round(overall * (0.65 + 0.35 * evidence_factor), 1) if overall is not None else None
        trust_label = "High" if trust is not None and trust >= 80 and len(sample_scores) >= 12 else "Medium" if trust is not None and trust >= 60 and len(sample_scores) >= 6 else "Low" if trust is not None else "Collecting"

        latest = self._samples[-1] if self._samples else None
        self._summary = {
            "status": "Ready" if self._samples else "Collecting",
            "sample_count": len(self._samples),
            "overall_accuracy_percent": overall,
            "trust_percent": trust,
            "trust_label": trust_label,
            "metrics": metrics,
            "metric_detail": metric_detail,
            "lead_time_accuracy": lead_time_accuracy,
            "latest_comparison": latest,
            "recent_samples": list(self._samples)[-12:],
            "pending_forecasts": len(self._pending),
            "minimum_samples_for_trend": 6,
            "minimum_samples_for_high_trust": 12,
            "forecast_capture_horizons_hours": list(self.LEAD_HOURS),
            "hourly_frozen_evidence": self.hourly_evidence_summary(),
            "daily_forecast_accuracy": self._daily_accuracy_summary(),
            "forecast_v21_shadow": self.v21_shadow_summary(),
            "cloud_model_accuracy": self._cloud_comparison_summary(),
            "adaptive_forecast_shadow": self._adaptive_shadow_summary(),
            "adaptive_forward_validation": self._forward_shadow_summary(),
            "daily_matched_forecast_count": len(self._daily_samples),
            "daily_pending_forecast_count": len(self._daily_pending),
            "recent_daily_forecast_outcomes": list(self._daily_samples)[-14:],
            "daily_measurement_note": "Daily outcomes compare stored pre-day forecasts only against canonical completed Recorder energy. Missing completed days remain pending and are never estimated.",
            "measurement_note": "Trust uses forecasts captured before their target time and scores them only after an aligned measurement matures. Missing or late pairs are discarded, never estimated.",
            "mode": "forward_matched_measurement_only",
            "control_permission": False,
            "updated_at": now.isoformat(),
            "recorder_safe": True,
        }
        return self._summary


    def explorer_summary(self) -> dict[str, Any]:
        """Compact forecast-vs-actual evidence for Forecast Explorer."""
        daily = []
        for row in list(self._daily_samples)[-45:]:
            predicted = row.get("predicted") or {}
            actual = row.get("actual") or {}
            errors = row.get("errors") or {}
            daily.append({
                "date": row.get("target_date"),
                "forecast_created_at": row.get("forecast_created_at"),
                "solar_forecast_kwh": self._num(predicted.get("solar_kwh")),
                "solar_actual_kwh": self._num(actual.get("solar_kwh")),
                "solar_error_kwh": self._num(errors.get("solar_kwh")),
                "home_forecast_kwh": self._num(predicted.get("home_kwh")),
                "home_actual_kwh": self._num(actual.get("home_kwh")),
                "home_error_kwh": self._num(errors.get("home_kwh")),
            })

        hourly_by_target: dict[str, dict[str, Any]] = {}
        for row in self._samples:
            target = str(row.get("forecast_time") or "")
            if not target:
                continue
            lead = int(row.get("lead_hours") or 0)
            current = hourly_by_target.get(target)
            if current is not None and int(current.get("lead_hours") or 0) >= lead:
                continue
            predicted = row.get("predicted") or {}
            actual = row.get("actual") or {}
            errors = row.get("errors") or {}
            hourly_by_target[target] = {
                "time": target,
                "lead_hours": lead,
                "solar_forecast_w": self._num(predicted.get("solar")),
                "solar_actual_w": self._num(actual.get("solar")),
                "solar_error_w": self._num(errors.get("solar")),
                "home_forecast_w": self._num(predicted.get("home")),
                "home_actual_w": self._num(actual.get("home")),
                "home_error_w": self._num(errors.get("home")),
            }
        # Also expose forecast-only points that were captured before their target
        # but have not matured yet. This lets Forecast Explorer build the current
        # calendar day progressively from genuine pre-target snapshots instead of
        # showing only the rolling forecast that happens to remain at render time.
        for pending in self._pending:
            target = str(pending.get("target_time") or "")
            if not target:
                continue
            lead = int(pending.get("lead_hours") or 0)
            current = hourly_by_target.get(target)
            if current is not None and int(current.get("lead_hours") or 0) >= lead:
                continue
            predicted = pending.get("predicted") or {}
            hourly_by_target[target] = {
                "time": target,
                "forecast_created_at": pending.get("created_at"),
                "lead_hours": lead,
                "solar_forecast_w": self._num(predicted.get("solar")),
                "solar_actual_w": None,
                "solar_error_w": None,
                "home_forecast_w": self._num(predicted.get("home")),
                "home_actual_w": None,
                "home_error_w": None,
                "matured": False,
            }
        hourly = [hourly_by_target[key] for key in sorted(hourly_by_target)][-96:]

        forecast = {}
        try:
            forecast = self.core.forecast.summary() or {}
        except Exception:
            forecast = {}
        quality = forecast.get("forecast_quality") if isinstance(forecast.get("forecast_quality"), dict) else {}
        return {
            "status": "Ready" if daily or hourly else "Collecting",
            "daily": daily,
            "hourly": hourly,
            "daily_accuracy": self._daily_accuracy_summary(),
            "hourly_error": self._summary.get("metric_detail") or {},
            "uncertainty_band_percent": quality.get("uncertainty_band_percent"),
            "measurement_note": "Past values are shown only when a forecast was captured before the target and a measured outcome later matured. Missing points are never interpolated.",
            "recorder_safe": False,
        }

    def summary(self) -> dict[str, Any]:
        return self._summary
