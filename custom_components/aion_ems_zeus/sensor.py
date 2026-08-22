"""AION EMS sensors."""

from __future__ import annotations

import logging
import json

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, NAME, VERSION

_LOGGER = logging.getLogger(__name__)


def _intelligence_memory_attributes(core) -> dict[str, Any]:
    """Expose a compact Recorder-safe Intelligence Memory sensor payload.

    The full memory remains in Home Assistant Store and inside the engine. The
    entity only publishes the subset required by the frontend so Recorder does
    not reject the attributes at the 16 KiB state-attribute limit.
    """
    data = dict(core.intelligence_memory.summary() or {})
    records = dict(data.get("records") or {})
    similar_days = list(data.get("similar_days") or [])[:3]
    recent_days = list(data.get("recent_days") or [])[-7:]
    memory_events = list(data.get("memory_events") or [])[-5:]
    return {
        "status": data.get("status"),
        "version": data.get("version"),
        "day_count": data.get("day_count", 0),
        "retention_days": data.get("retention_days"),
        "records": records,
        "similar_days": similar_days,
        "recent_days": recent_days,
        "memory_events": memory_events,
        "summary": data.get("summary"),
        "storage": data.get("storage"),
        "recorder_safe": True,
        "sensor_payload": "compact",
        "safety": data.get("safety"),
    }


def _weather_context_attributes(core) -> dict[str, Any]:
    """Expose a compact Recorder-safe Weather Context payload.

    The WeatherEngine keeps the full provider forecast internally for Zeus
    forecasting, Grid Outlook and Weather Intelligence.  The Home Assistant
    sensor intentionally publishes only current conditions, source metadata
    and a compact candidate list so Recorder stays comfortably below the
    16 KiB state-attribute limit.
    """
    data = dict(core.weather.summary() or {})
    candidates = []
    for item in list(data.get("candidates") or [])[:12]:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "entity_id": item.get("entity_id"),
            "name": item.get("name"),
            "condition": item.get("condition"),
            "available": item.get("available"),
            "supported_features": item.get("supported_features"),
        })
    return {
        "status": data.get("status"),
        "entity_id": data.get("entity_id"),
        "available": data.get("available"),
        "condition": data.get("condition"),
        "temperature": data.get("temperature"),
        "temperature_unit": data.get("temperature_unit"),
        "humidity": data.get("humidity"),
        "cloud_coverage": data.get("cloud_coverage"),
        "cloud_coverage_source": data.get("cloud_coverage_source"),
        "wind_speed": data.get("wind_speed"),
        "precipitation": data.get("precipitation"),
        "forecast_available": data.get("forecast_available", False),
        "forecast_row_count": len(data.get("forecast") or []),
        "solar_factor": data.get("solar_factor"),
        "updated_at": data.get("updated_at"),
        "configured": data.get("configured"),
        "provider": data.get("provider"),
        "summary": data.get("summary"),
        "candidates": candidates,
        "recorder_safe": True,
        "forecast_storage": "internal_only",
    }


def _insight_intelligence_attributes(core) -> dict[str, Any]:
    """Expose a compact Recorder-safe Insight Intelligence payload.

    The complete InsightIntelligenceEngine summary remains available internally
    to Zeus/Copilot.  This entity publishes the presentation subset required by
    the Insights frontend, with bounded lists/text so Recorder remains below its
    16 KiB state-attribute limit.
    """
    data = dict(core.insight_intelligence.summary() or {})

    def text(value: Any, limit: int = 420) -> Any:
        if not isinstance(value, str):
            return value
        return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"

    def compact_dict(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        out: dict[str, Any] = {}
        for key in keys:
            value = row.get(key)
            if isinstance(value, str):
                value = text(value)
            elif isinstance(value, list):
                value = [text(item, 220) if isinstance(item, str) else item for item in value[:6]]
            out[key] = value
        return out

    briefing = compact_dict(data.get("briefing"), (
        "headline", "attention_count", "meaningful_change_count",
        "top_recommendation", "summary", "reading_time_seconds",
    ))
    raw_briefing = data.get("briefing") if isinstance(data.get("briefing"), dict) else {}
    top = compact_dict(raw_briefing.get("top_recommendation_detail"), (
        "title", "reason", "best_window", "expected_energy_benefit_kwh",
        "expected_savings", "currency", "confidence_percent",
    ))
    briefing["top_recommendation_detail"] = top

    energy_briefing = compact_dict(data.get("energy_briefing"), (
        "headline", "summary", "importance", "evidence_confidence",
        "evidence_period", "evidence_boundary",
    ))

    energy_insights = []
    for row in list(data.get("energy_insights") or [])[:5]:
        item = compact_dict(row, (
            "family", "category", "title", "importance", "interpretation",
            "evidence_confidence", "evidence_period", "why_it_matters",
            "evidence_boundary",
        ))
        if isinstance(row, dict):
            item["measured_facts"] = [text(x, 180) for x in list(row.get("measured_facts") or [])[:5] if isinstance(x, str)]
        energy_insights.append(item)

    raw_evidence = data.get("energy_trend_evidence") if isinstance(data.get("energy_trend_evidence"), dict) else {}
    evidence = compact_dict(raw_evidence, (
        "available", "evidence_days", "required_days", "remaining_days",
        "note", "evidence_period", "previous_period", "recent_period",
    ))
    raw_trends = raw_evidence.get("trends") if isinstance(raw_evidence.get("trends"), dict) else {}
    evidence["trends"] = {
        str(name): compact_dict(row, ("label", "available", "missing_evidence", "previous", "recent", "absolute", "pct", "direction", "unit"))
        for name, row in list(raw_trends.items())[:8]
        if isinstance(row, dict)
    }

    insights = [compact_dict(row, (
        "kind", "priority", "severity", "title", "summary",
        "confidence_percent", "evidence_days", "evidence", "reasoning",
    )) for row in list(data.get("insights") or [])[:6]]
    anomalies = [compact_dict(row, (
        "metric", "title", "severity", "priority", "direction",
        "latest_kwh", "baseline_kwh", "difference_percent",
        "confidence_percent", "evidence_days", "evidence", "reasoning", "follow_up",
    )) for row in list(data.get("anomalies") or [])[:4]]
    trends = [compact_dict(row, (
        "metric", "label", "direction", "change_percent",
        "recent_average_kwh", "previous_average_kwh", "assessment",
        "severity", "confidence_percent", "evidence_days", "evidence", "reasoning",
    )) for row in list(data.get("trends") or [])[:4]]
    normal = [compact_dict(row, (
        "metric", "label", "icon", "latest_kwh", "baseline_kwh",
        "difference_percent", "confidence_percent", "evidence_days",
        "severity", "summary",
    )) for row in list(data.get("normal_evidence") or [])[:4]]
    story = [compact_dict(row, ("time", "icon", "tone", "title", "detail")) for row in list(data.get("today_story") or [])[:5]]
    positives = [compact_dict(row, ("title", "detail", "icon", "confidence_percent")) for row in list(data.get("positive_insights") or [])[:4]]

    tomorrow = compact_dict(data.get("tomorrow_prediction"), (
        "status", "date", "headline", "summary", "expected_solar_kwh",
        "expected_consumption_kwh", "expected_grid_import_kwh",
        "expected_grid_export_kwh", "projected_battery_soc_percent",
        "weather_summary", "confidence_percent", "confidence_label",
        "if_nothing_changes", "source", "stored_plan_available",
    ))
    raw_tomorrow = data.get("tomorrow_prediction") if isinstance(data.get("tomorrow_prediction"), dict) else {}
    tomorrow["confidence_reasons"] = [text(x, 180) for x in list(raw_tomorrow.get("confidence_reasons") or [])[:4] if isinstance(x, str)]
    tomorrow["plan_preview"] = [compact_dict(row, ("period", "title", "detail")) for row in list(raw_tomorrow.get("plan_preview") or [])[:4]]

    payload = {
        "status": data.get("status"),
        "recommendation_only": data.get("recommendation_only", True),
        "generated_at": data.get("generated_at"),
        "evidence_days": data.get("evidence_days", 0),
        "briefing": briefing,
        "energy_briefing": energy_briefing,
        "energy_insights": energy_insights,
        "energy_trend_evidence": evidence,
        "insights": insights,
        "anomalies": anomalies,
        "trends": trends,
        "normal_evidence": normal,
        "baseline_progress": data.get("baseline_progress") or {},
        "trend_progress": data.get("trend_progress") or {},
        "executive_kpis": data.get("executive_kpis") or {},
        "today_story": story,
        "tomorrow_prediction": tomorrow,
        "positive_insights": positives,
        "live_context": data.get("live_context") or {},
        "limitations": text(data.get("limitations"), 500),
        "recorder_safe": True,
        "sensor_payload": "compact",
    }

    # Home Assistant Recorder rejects attributes above 16 KiB. Keep a safety
    # margin and degrade presentation-only legacy detail in a deterministic
    # order if unusually verbose upstream strings ever grow the payload.
    def payload_bytes() -> int:
        return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))

    if payload_bytes() > 14500:
        payload["normal_evidence"] = payload["normal_evidence"][:2]
        payload["positive_insights"] = payload["positive_insights"][:2]
        payload["today_story"] = payload["today_story"][:3]
        payload["trends"] = payload["trends"][:3]
        payload["anomalies"] = payload["anomalies"][:3]
        payload["insights"] = payload["insights"][:4]
    if payload_bytes() > 14500:
        for rows_key in ("insights", "anomalies", "trends", "normal_evidence"):
            for row in payload.get(rows_key, []):
                if isinstance(row, dict):
                    row.pop("reasoning", None)
                    row.pop("follow_up", None)
                    row.pop("evidence", None)
                    row.pop("summary", None)
        tomorrow_payload = payload.get("tomorrow_prediction")
        if isinstance(tomorrow_payload, dict):
            tomorrow_payload["confidence_reasons"] = tomorrow_payload.get("confidence_reasons", [])[:2]
            tomorrow_payload["plan_preview"] = tomorrow_payload.get("plan_preview", [])[:2]
    if payload_bytes() > 14500:
        # Energy Insights are the canonical 22.15.7.x feature and are preserved.
        # Older presentation-only sections can collect again in the UI rather
        # than causing Recorder to reject the whole entity attribute set.
        payload["normal_evidence"] = []
        payload["positive_insights"] = []
        payload["today_story"] = []
        payload["anomalies"] = payload.get("anomalies", [])[:1]
        payload["trends"] = payload.get("trends", [])[:2]
        payload["insights"] = payload.get("insights", [])[:2]
        tomorrow_payload = payload.get("tomorrow_prediction")
        if isinstance(tomorrow_payload, dict):
            tomorrow_payload.pop("plan_preview", None)
            tomorrow_payload.pop("confidence_reasons", None)
            for key in ("summary", "weather_summary", "if_nothing_changes"):
                if key in tomorrow_payload:
                    tomorrow_payload[key] = text(tomorrow_payload.get(key), 160)
    if payload_bytes() > 14500:
        # Absolute final guard: retain the complete Energy Insights card data
        # and essential page status, while removing optional legacy narratives.
        payload = {
            "status": payload.get("status"),
            "recommendation_only": True,
            "generated_at": payload.get("generated_at"),
            "evidence_days": payload.get("evidence_days"),
            "briefing": payload.get("briefing"),
            "energy_briefing": payload.get("energy_briefing"),
            "energy_insights": payload.get("energy_insights", []),
            "energy_trend_evidence": payload.get("energy_trend_evidence", {}),
            "baseline_progress": payload.get("baseline_progress", {}),
            "trend_progress": payload.get("trend_progress", {}),
            "executive_kpis": payload.get("executive_kpis", {}),
            "limitations": text(payload.get("limitations"), 240),
            "recorder_safe": True,
            "sensor_payload": "compact_guarded",
        }

    def hard_bound(value: Any, string_limit: int, list_limit: int) -> Any:
        if isinstance(value, str):
            return value if len(value) <= string_limit else value[: max(0, string_limit - 1)] + "…"
        if isinstance(value, list):
            return [hard_bound(item, string_limit, list_limit) for item in value[:list_limit]]
        if isinstance(value, dict):
            return {str(key): hard_bound(item, string_limit, list_limit) for key, item in value.items()}
        return value

    # Absolute byte guard. This path should only be reached with unexpectedly
    # verbose upstream text, but it guarantees Recorder never receives >16 KiB.
    for string_limit, list_limit in ((160, 5), (110, 4), (80, 3), (56, 2)):
        if payload_bytes() <= 14500:
            break
        payload = hard_bound(payload, string_limit, list_limit)
        payload["recorder_safe"] = True
        payload["sensor_payload"] = "compact_guarded"
    if payload_bytes() > 14500:
        payload["energy_insights"] = list(payload.get("energy_insights") or [])[:2]
        payload["energy_trend_evidence"] = {
            key: value for key, value in (payload.get("energy_trend_evidence") or {}).items()
            if key in {"available", "evidence_days", "required_days", "remaining_days", "note"}
        }
    return payload


def _scheduler_preview_attributes(core) -> dict[str, Any]:
    """Expose a compact Recorder-safe Scheduler payload.

    Scheduler keeps its complete plan, evidence and diagnostics internally.
    The HA entity publishes only fields used by Zeus UI/Copilot plus compact
    diagnostics. Duplicate plan payloads and verbose evidence internals stay
    backend-only so Recorder remains safely below its 16 KiB attribute limit.
    """
    data = dict(core.scheduler.summary() or {})

    def compact_row(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        return {
            "device_id": row.get("device_id"),
            "device_name": row.get("device_name"),
            "device_type": row.get("device_type"),
            "role": row.get("role"),
            "planning_rank": row.get("planning_rank"),
            "suggested_start": row.get("suggested_start"),
            "suggested_end": row.get("suggested_end"),
            "expected_energy_kwh": row.get("expected_energy_kwh"),
            "solar_coverage_percent": row.get("solar_coverage_percent"),
            "estimated_saving": row.get("estimated_saving"),
            "currency": row.get("currency"),
            "confidence_percent": row.get("confidence_percent"),
            "score": row.get("score"),
            "quantification_supported": row.get("quantification_supported"),
        }

    # UI/Copilot only require the ranked leaders and tomorrow's leading plan.
    # Counts remain authoritative even when these presentation lists are capped.
    order = [x for x in (compact_row(r) for r in list(data.get("recommended_order") or [])[:8]) if x]
    tomorrow_plan = [x for x in (compact_row(r) for r in list(data.get("tomorrow_plan") or [])[:5]) if x]

    roles = []
    for row in list(data.get("role_plan") or [])[:8]:
        if not isinstance(row, dict):
            continue
        roles.append({
            "role": row.get("role"),
            "device_type": row.get("device_type"),
            "device_count": row.get("device_count"),
            "supported_energy_kwh": row.get("supported_energy_kwh"),
            "supported_solar_covered_energy_kwh": row.get("supported_solar_covered_energy_kwh"),
            "highest_score": row.get("highest_score"),
            "best_window": row.get("best_window"),
            "confidence_percent": row.get("confidence_percent"),
            "planning_rank": row.get("planning_rank"),
        })

    raw_diag = data.get("candidate_diagnostics") if isinstance(data.get("candidate_diagnostics"), dict) else {}
    slots = dict(raw_diag.get("slots") or {})
    diagnostics = {
        "slots": {
            k: slots.get(k) for k in (
                "forecast_planning_row_count", "rolling_slot_count",
                "surplus_slot_count", "strong_surplus_slot_count",
                "max_surplus_w", "first_slot", "last_slot", "source",
            )
        },
        "registry_enabled_device_count": raw_diag.get("registry_enabled_device_count"),
        "classified_flexible_device_count": raw_diag.get("classified_flexible_device_count"),
        "all_candidates_blocked_by_constraints": raw_diag.get("all_candidates_blocked_by_constraints"),
        "classification_count": len(raw_diag.get("classification") or []),
        "device_diagnostic_count": len(raw_diag.get("devices") or []),
    }

    qualification = []
    for row in list(data.get("qualification_diagnostics") or [])[:10]:
        if not isinstance(row, dict):
            continue
        missing = list(dict.fromkeys(
            [str(x) for x in (row.get("missing_evidence") or []) if x]
            + [str(x) for x in (row.get("historical_missing_evidence") or []) if x]
        ))
        qualification.append({
            "device_name": row.get("device_name"),
            "quantification_supported": row.get("quantification_supported"),
            "profile_source": row.get("profile_source"),
            "historical_active_days": row.get("historical_active_days"),
            "historical_typical_energy_kwh": row.get("historical_typical_energy_kwh"),
            "historical_typical_runtime_minutes": row.get("historical_typical_runtime_minutes"),
            "historical_typical_power_w": row.get("historical_typical_power_w"),
            "aligned_recorder_runtime_days": row.get("historical_aligned_recorder_runtime_days"),
            "aligned_datalake_runtime_days": row.get("historical_aligned_datalake_runtime_days"),
            "power_entity": row.get("power_entity"),
            "power_available": row.get("power_entity_available"),
            "energy_entity": row.get("energy_entity"),
            "energy_available": row.get("energy_entity_available"),
            "recorder_days": row.get("recorder_energy_day_count"),
            "lake_energy_days": row.get("data_lake_energy_day_count"),
            "lake_runtime_days": row.get("data_lake_runtime_day_count"),
            "lake_combined_days": row.get("data_lake_combined_profile_day_count"),
            "source_status": row.get("source_status"),
            "source_blockers": list(row.get("source_blocking_reasons") or [])[:3],
            "missing_evidence": missing[:2],
        })

    return {
        "status": data.get("status"),
        "engine": data.get("engine"),
        "version": data.get("version"),
        "foundation": data.get("foundation"),
        "mode": data.get("mode"),
        "horizon_hours": data.get("horizon_hours"),
        "horizon_start": data.get("horizon_start"),
        "horizon_end": data.get("horizon_end"),
        "generated_at": data.get("generated_at"),
        "plan_count": data.get("plan_count"),
        "schedule_count": data.get("schedule_count"),
        "recommended_order": order,
        "role_plan": roles,
        "tomorrow_date": data.get("tomorrow_date"),
        "tomorrow_plan": tomorrow_plan,
        "tomorrow_plan_count": data.get("tomorrow_plan_count"),
        "unscheduled_device_count": data.get("unscheduled_device_count"),
        "flexible_device_count": data.get("flexible_device_count"),
        "total_planned_energy_kwh": data.get("total_planned_energy_kwh"),
        "quantified_planned_energy_kwh": data.get("quantified_planned_energy_kwh"),
        "quantified_solar_covered_energy_kwh": data.get("quantified_solar_covered_energy_kwh"),
        "quantified_device_count": data.get("quantified_device_count"),
        "assumption_limited_device_count": data.get("assumption_limited_device_count"),
        "qualification_rule": data.get("qualification_rule"),
        "qualification_diagnostics": qualification,
        "average_solar_coverage_percent": data.get("average_solar_coverage_percent"),
        "estimated_total_saving": data.get("estimated_total_saving"),
        "currency": data.get("currency"),
        "tariff_aware": data.get("tariff_aware"),
        "forecast_confidence": data.get("forecast_confidence"),
        "summary": data.get("summary"),
        "limitations": list(data.get("limitations") or [])[:4],
        "candidate_diagnostics": diagnostics,
        "safety": data.get("safety"),
        "recorder_safe": True,
        "sensor_payload": "compact",
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    core = hass.data[DOMAIN][entry.entry_id]

    async def _async_update_data():
        # The event-driven UpdateEngine owns computation. The coordinator only
        # publishes the latest snapshot and delivers queued notifications.
        await core.notifications.async_deliver(hass)
        return {"version": core.version, "published_at": datetime.now(timezone.utc).isoformat()}

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name=f"{NAME} Coordinator",
        update_method=_async_update_data,
        update_interval=timedelta(minutes=5),
    )
    await coordinator.async_config_entry_first_refresh()

    _last_push = {"at": None}

    def _push_update() -> None:
        now = datetime.now(timezone.utc)
        previous = _last_push["at"]
        if previous is not None and (now - previous).total_seconds() < 10:
            return
        _last_push["at"] = now
        coordinator.async_set_updated_data({"version": core.version, "published_at": now.isoformat()})

    entry.async_on_unload(core.update_engine.add_listener(_push_update))

    def _advisor_attributes(core) -> dict[str, Any]:
        """Return recorder-safe advisor attributes without duplicating large reports."""
        data = core.ai_advisor.summary() or {}
        battery = data.get("battery_strategy") if isinstance(data.get("battery_strategy"), dict) else {}
        return {
            "status": data.get("status"),
            "headline": data.get("headline"),
            "explanation": data.get("explanation"),
            "live_context": data.get("live_context", {}),
            "today_score": data.get("today_score"),
            "forecast_today_kwh": data.get("forecast_today_kwh"),
            "forecast_tomorrow_kwh": data.get("forecast_tomorrow_kwh"),
            "battery_strategy": {
                "strategy": battery.get("strategy"),
                "recommended_action": battery.get("recommended_action"),
                "recommended_reserve_percent": battery.get("recommended_reserve_percent"),
                "summary": battery.get("summary"),
                "safety": battery.get("safety"),
            },
            "learning_confidence_percent": data.get("learning_confidence_percent"),
            "recommendations": (data.get("recommendations") or [])[:5],
            "questions": (data.get("questions") or [])[:3],
            "summary": data.get("summary"),
            "safety": data.get("safety"),
            "details_entity": "sensor.aion_ems_zeus_predictive_battery",
            "recorder_safe": True,
        }


    def _optimization_intelligence_attributes(core) -> dict[str, Any]:
        """Recorder-safe Optimization Intelligence payload.

        Full evidence arrays stay canonical and in-memory. The public HA sensor
        exposes the quantities and coordination fields used by Zeus UI/Copilot.
        """
        data = core.optimization_intelligence.summary() or {}
        q = data.get("opportunity_quantification") if isinstance(data.get("opportunity_quantification"), dict) else {}
        est = q.get("estimated_opportunity") if isinstance(q.get("estimated_opportunity"), dict) else {}
        forecast = q.get("forecast") if isinstance(q.get("forecast"), dict) else {}
        coord = data.get("battery_load_coordination") if isinstance(data.get("battery_load_coordination"), dict) else {}
        battery = coord.get("battery_candidate") if isinstance(coord.get("battery_candidate"), dict) else {}

        def compact_load(row):
            if not isinstance(row, dict):
                return None
            return {
                "device_id": row.get("device_id"),
                "device_name": row.get("device_name"),
                "device_type": row.get("device_type"),
                "role": row.get("role"),
                "suggested_start": row.get("suggested_start"),
                "suggested_end": row.get("suggested_end"),
                "solar_covered_energy_kwh": row.get("solar_covered_energy_kwh"),
                "solar_coverage_percent": row.get("solar_coverage_percent"),
                "potential_saving": row.get("potential_saving"),
                "currency": row.get("currency"),
                "confidence_percent": row.get("confidence_percent"),
                "planning_rank": row.get("planning_rank"),
                "quantification_supported": row.get("quantification_supported"),
            }

        compact_opportunities = []
        for row in list(data.get("opportunities") or [])[:3]:
            if not isinstance(row, dict):
                continue
            compact_opportunities.append({
                "kind": row.get("kind"),
                "title": row.get("title"),
                "reason": row.get("reason"),
                "priority": row.get("priority"),
                "confidence_percent": row.get("confidence_percent"),
                "best_window": row.get("best_window"),
                "expected_energy_benefit_kwh": row.get("expected_energy_benefit_kwh"),
                "expected_savings": row.get("expected_savings"),
                "currency": row.get("currency"),
                "expected_duration": row.get("expected_duration"),
                "quantified_as": row.get("quantified_as"),
                "source_engines": list(row.get("source_engines") or [])[:5],
            })

        loads = [x for x in (compact_load(r) for r in list(coord.get("qualified_load_candidates") or [])[:3]) if x]
        named = coord.get("named_device_candidates") if isinstance(coord.get("named_device_candidates"), dict) else {}
        roles = coord.get("role_candidates") if isinstance(coord.get("role_candidates"), dict) else {}

        ledger = coord.get("allocation_ledger") if isinstance(coord.get("allocation_ledger"), dict) else {}
        def compact_interval(row):
            if not isinstance(row, dict):
                return None
            return {
                "time": row.get("time"),
                "projected_soc_start_percent": row.get("projected_soc_start_percent"),
                "projected_soc_end_percent": row.get("projected_soc_end_percent"),
                "forecast_surplus_kwh": row.get("forecast_surplus_kwh"),
                "canonical_battery_charge_request_kwh": row.get("canonical_battery_charge_request_kwh"),
                "qualified_load_demand_kwh": row.get("qualified_load_demand_kwh"),
                "battery_allocated_kwh": row.get("battery_allocated_kwh"),
                "qualified_load_allocated_kwh": row.get("qualified_load_allocated_kwh"),
                "unallocated_surplus_kwh": row.get("unallocated_surplus_kwh"),
                "active_qualified_loads": list(row.get("active_qualified_loads") or [])[:3],
                "priority": row.get("priority"),
                "simultaneous_battery_and_load_supported": row.get("simultaneous_battery_and_load_supported"),
            }

        compact_ledger = {
            "status": ledger.get("status"),
            "version": ledger.get("version"),
            "mode": ledger.get("mode"),
            "horizon_hours": ledger.get("horizon_hours"),
            "priority_policy": ledger.get("priority_policy"),
            "totals": ledger.get("totals"),
            "first_simultaneous_interval": compact_interval(ledger.get("first_simultaneous_interval")),
            "first_load_without_battery_delay_interval": compact_interval(ledger.get("first_load_without_battery_delay_interval")),
            "intervals": [x for x in (compact_interval(r) for r in list(ledger.get("intervals") or [])[:3]) if x],
            "safety": ledger.get("safety"),
        }

        compact_coord = {
            "status": coord.get("status"),
            "version": coord.get("version"),
            "mode": coord.get("mode"),
            "priority": coord.get("priority"),
            "reason": coord.get("reason"),
            "forecast_surplus_pool_kwh": coord.get("forecast_surplus_pool_kwh"),
            "forecast_confidence_percent": coord.get("forecast_confidence_percent"),
            "top_evidence_qualified_load": compact_load(coord.get("top_evidence_qualified_load")),
            "qualified_load_candidates": loads,
            "role_candidates": {
                "DHW": roles.get("DHW"),
                "EV / Car": roles.get("EV / Car"),
            },
            "named_device_candidates": {
                "ELWA": compact_load(named.get("ELWA")),
            },
            "allocation_ledger": compact_ledger,
            "battery_candidate": {
                k: battery.get(k) for k in (
                    "configured", "status", "strategy", "reason",
                    "battery_soc_percent", "recommended_reserve_percent",
                    "projected_soc_end_percent", "modeled_avoidable_import_kwh",
                    "potential_saving", "currency", "estimated_unstored_surplus_kwh",
                    "confidence_percent", "horizon_hours", "source",
                )
            },
            "battery_evidence_diagnostics": {
                k: (coord.get("battery_evidence_diagnostics") or {}).get(k)
                for k in (
                    "configured", "device_id", "device_name", "status", "strategy",
                    "soc_source_entity", "soc_source_available", "battery_soc_percent",
                    "capacity_kwh", "capacity_source",
                    "minimum_soc_percent", "maximum_soc_percent",
                    "max_charge_power_w", "max_discharge_power_w", "round_trip_efficiency",
                    "configuration_blockers", "modeled_avoidable_import_kwh",
                    "potential_saving", "currency", "optimizer_confidence_percent",
                    "forecast_available_rows", "forecast_skipped_elapsed_rows",
                    "forecast_consumed_rows", "forecast_status", "forecast_bridge_ready",
                    "timeline_anchor_live_soc_percent", "timeline_anchor_generated_at",
                )
            },
            "battery_configuration_status": coord.get("battery_configuration_status"),
            "comparison_policy": coord.get("comparison_policy"),
            "safety": coord.get("safety"),
        }

        orchestrator = data.get("daily_energy_orchestrator") if isinstance(data.get("daily_energy_orchestrator"), dict) else {}

        def compact_day_plan(day):
            if not isinstance(day, dict):
                return {}
            return {
                "label": day.get("label"),
                "date": day.get("date"),
                "status": day.get("status"),
                "headline": day.get("headline"),
                "priority_policy": day.get("priority_policy"),
                "battery_strategy": day.get("battery_strategy"),
                "battery_soc_start_percent": day.get("battery_soc_start_percent"),
                "battery_soc_end_percent": day.get("battery_soc_end_percent"),
                "totals": day.get("totals"),
                "milestones": list(day.get("milestones") or [])[:6],
                "qualified_scheduler_loads": list(day.get("qualified_scheduler_loads") or [])[:4],
                "advisory_assumption_limited_loads": [
                    {
                        "device_id": x.get("device_id"),
                        "device_name": x.get("device_name"),
                        "device_type": x.get("device_type"),
                        "suggested_start": x.get("suggested_start"),
                        "quantification_supported": x.get("quantification_supported"),
                    }
                    for x in list(day.get("advisory_assumption_limited_loads") or [])[:4]
                    if isinstance(x, dict)
                ],
                "confidence_percent": day.get("confidence_percent"),
                "finance": day.get("finance"),
            }

        compact_orchestrator = {
            "status": orchestrator.get("status"),
            "version": orchestrator.get("version"),
            "mode": orchestrator.get("mode"),
            "generated_at": orchestrator.get("generated_at"),
            "today": compact_day_plan(orchestrator.get("today")),
            "tomorrow": compact_day_plan(orchestrator.get("tomorrow")),
            "adaptive_tracking": orchestrator.get("adaptive_tracking"),
            "plan_completion_learning": orchestrator.get("plan_completion_learning"),
            "composition_policy": orchestrator.get("composition_policy"),
            "safety": orchestrator.get("safety"),
        }

        return {
            "status": data.get("status"),
            "version": data.get("version"),
            "foundation": data.get("foundation"),
            "recommendation_only": data.get("recommendation_only"),
            "optimization_score": data.get("optimization_score"),
            "opportunity_quantification": {
                "status": q.get("status"),
                "version": q.get("version"),
                "mode": q.get("mode"),
                "forecast": {
                    k: forecast.get(k) for k in (
                        "expected_grid_export_next_24h_kwh",
                        "expected_grid_import_next_24h_kwh",
                        "confidence_percent",
                    )
                },
                "estimated_opportunity": est,
                "confidence": q.get("confidence"),
            },
            "battery_load_coordination": compact_coord,
            "daily_energy_orchestrator": compact_orchestrator,
            "opportunities": compact_opportunities,
            "updated_at": data.get("updated_at"),
            "recorder_safe": True,
        }

    def _device_analytics_attributes(core) -> dict[str, Any]:
        """Publish Device Analytics without verbose planning-evidence internals.

        The full canonical Device Analytics summary remains in-memory for
        Scheduler/DEA consumers. Home Assistant Recorder receives the device
        energy/runtime fields used by the UI, while historical planning profiles
        and source-path diagnostics stay backend-only.
        """
        data = core.device_analytics.summary() or {}
        devices = []
        for row in list(data.get("devices") or []):
            if not isinstance(row, dict):
                continue
            devices.append({
                "id": row.get("id"),
                "name": row.get("name"),
                "type": row.get("type"),
                "power_entity": row.get("power_entity"),
                "energy_entity": row.get("energy_entity"),
                "energy_type": row.get("energy_type"),
                "energy_today_kwh": row.get("energy_today_kwh"),
                "energy_week_kwh": row.get("energy_week_kwh"),
                "energy_month_kwh": row.get("energy_month_kwh"),
                "energy_year_kwh": row.get("energy_year_kwh"),
                "energy_total_kwh": row.get("energy_total_kwh"),
                "energy_tracked_total_kwh": row.get("energy_tracked_total_kwh"),
                "total_method": row.get("total_method"),
                "tracked_days": row.get("tracked_days"),
                "runtime_today_minutes": row.get("runtime_today_minutes"),
                "runtime_week_minutes": row.get("runtime_week_minutes"),
                "runtime_month_minutes": row.get("runtime_month_minutes"),
                "runtime_year_minutes": row.get("runtime_year_minutes"),
                "peak_power_today_w": row.get("peak_power_today_w"),
                "peak_power_source": row.get("peak_power_source"),
                "cop_entity": row.get("cop_entity"),
                "cop": row.get("cop"),
                "cop_available": row.get("cop_available"),
                "cop_method": row.get("cop_method"),
                "cop_history_method": row.get("cop_history_method"),
                "cop_history_status": row.get("cop_history_status"),
                "cop_today_average": row.get("cop_today_average"),
                "cop_week_average": row.get("cop_week_average"),
                "cop_month_average": row.get("cop_month_average"),
                "cop_year_average": row.get("cop_year_average"),
                "cop_today_bucket_count": row.get("cop_today_bucket_count"),
                "cop_week_bucket_count": row.get("cop_week_bucket_count"),
                "cop_month_bucket_count": row.get("cop_month_bucket_count"),
                "cop_year_bucket_count": row.get("cop_year_bucket_count"),
                "sample_count": row.get("sample_count"),
                "method": row.get("method"),
                "integrated_energy_today_kwh": row.get("integrated_energy_today_kwh"),
            })
        return {
            "status": data.get("status"),
            "date": data.get("date"),
            "device_count": data.get("device_count"),
            "total_device_energy_today_kwh": data.get("total_device_energy_today_kwh"),
            "total_runtime_today_minutes": data.get("total_runtime_today_minutes"),
            "devices": devices,
            "top_device": ({
                k: (data.get("top_device") or {}).get(k)
                for k in ("id", "name", "type", "energy_today_kwh", "runtime_today_minutes", "peak_power_today_w")
            } if isinstance(data.get("top_device"), dict) else None),
            "summary": data.get("summary"),
            "method_note": data.get("method_note"),
            "recorder_energy_source": data.get("recorder_energy_source"),
            "safety": data.get("safety"),
            "recorder_safe": True,
            "planning_evidence_details": "backend_only",
        }

    def _integration_hub_attributes(core) -> dict[str, Any]:
        """Return the canonical compact Integration Hub Recorder payload."""
        return core.integration_hub.recorder_summary()

    def _performance_attributes(core) -> dict[str, Any]:
        """Return compact Zeus execution activity for the Performance page."""
        update = core.update_engine.summary() or {}
        perf = dict(core.performance or {})
        return {
            "mode": perf.get("mode", "low_cpu_event_driven"),
            "tracked_entity_count": update.get("tracked_entity_count", 0),
            "listener_count": len(getattr(core.update_engine, "_listeners", []) or []),
            "source_events": update.get("source_events", 0),
            "processed_updates": update.get("processed_updates", 0),
            "safety_refreshes": update.get("safety_refreshes", 0),
            "coalesced_refreshes": update.get("coalesced_refreshes", 0),
            "rate_limited_refreshes": update.get("rate_limited_refreshes", 0),
            "last_refresh_reason": update.get("last_refresh_reason"),
            "last_refresh_duration_ms": update.get("last_refresh_duration_ms"),
            "last_processed_entity": update.get("last_processed_entity"),
            "live_refreshes": perf.get("live_refreshes", 0),
            "decision_refreshes": perf.get("decision_refreshes", 0),
            "last_live_duration_ms": perf.get("last_live_duration_ms"),
            "last_decision_duration_ms": perf.get("last_decision_duration_ms"),
            "engine_count": len(core.engine_names()),
            "engines": core.engine_names(),
            "note": "Host CPU is measured by Home Assistant system entities; Zeus counters show activity, not an isolated CPU percentage.",
            "recorder_safe": True,
        }


    def _device_manager_attributes(core) -> dict[str, Any]:
        """Return Device Manager status derived from restored persistent state."""
        last = dict(getattr(core.device_import_manager, "last_validation", {}) or {})
        registry = core.registry.summary() or {}
        mapping = core.energy_mapping.public_summary() or {}
        device_count = int(registry.get("device_count") or 0)
        mapped_count = int(mapping.get("mapped_count") or 0)
        invalid_count = int(mapping.get("invalid_count") or 0)

        if device_count > 0:
            status = "Ready"
            message = f"Registry restored: {device_count} registered device{'s' if device_count != 1 else ''} loaded."
            source = "persistent_registry"
        elif mapped_count > 0:
            status = "Ready" if invalid_count == 0 else "Warning"
            message = f"Energy mappings restored: {mapped_count} configured, {invalid_count} needing attention."
            source = "persistent_energy_mapping"
        else:
            status = last.get("status", "Not run")
            message = last.get("message", "No device import has run.")
            source = "device_import"

        return {
            **last,
            "status": status,
            "message": message,
            "status_source": source,
            "registry_restored": device_count > 0,
            "device_count": device_count,
            "energy_mapping_status": mapping.get("status", "Not mapped"),
            "mapped_count": mapped_count,
            "invalid_mapping_count": invalid_count,
            "recorder_safe": True,
        }

    def _device_manager_state(core) -> str:
        return str(_device_manager_attributes(core).get("status") or "Not run")

    def _plugin_attributes(core, plugin_id: str) -> dict[str, Any]:
        """Return one plugin's discovery payload on a dedicated sensor."""
        data = core.integration_hub.summary() or {}
        plugin = next((x for x in data.get("plugins", []) or [] if x.get("id") == plugin_id), {})
        return {
            "id": plugin.get("id", plugin_id),
            "name": plugin.get("name", plugin_id),
            "category": plugin.get("category"),
            "icon": plugin.get("icon"),
            "permission": plugin.get("permission"),
            "enabled": plugin.get("enabled", False),
            "health": plugin.get("health", "Waiting"),
            "candidate_count": plugin.get("candidate_count", 0),
            "candidates": plugin.get("candidates", []),
            "version": plugin.get("version"),
            "control_permission": False,
            "recorder_partition": plugin_id,
        }

    sensors = [
        SimpleSensor(coordinator, core, "Platform Status", "platform_status", "mdi:home-lightning-bolt", lambda c: "Ready", lambda c: {"version": VERSION, "status": "Zeus AI Advisor ready", "architecture": "zeus-12-1-decision-intelligence", "engines": c.engine_names()}),
        SimpleSensor(coordinator, core, "Performance Diagnostics", "performance_diagnostics", "mdi:speedometer", lambda c: c.update_engine.summary().get("status", "Running"), _performance_attributes),
        SimpleSensor(coordinator, core, "Registry Summary", "registry_summary", "mdi:database-cog-outline", lambda c: c.registry.summary().get("status"), lambda c: c.registry.summary()),
        SimpleSensor(coordinator, core, "Entity Discovery", "entity_discovery", "mdi:magnify-scan", lambda c: c.discovery.summary().get("status"), lambda c: c.discovery.summary()),
        SimpleSensor(coordinator, core, "Energy Mapping", "energy_mapping", "mdi:transmission-tower-import", lambda c: c.energy_mapping.public_summary().get("status"), lambda c: c.energy_mapping.public_summary()),
        SimpleSensor(coordinator, core, "Energy Flow", "energy_flow", "mdi:home-lightning-bolt-outline", lambda c: c.energy_flow.summary().get("status"), lambda c: c.energy_flow.summary()),
        SimpleSensor(coordinator, core, "Integration Hub", "integration_hub", "mdi:hubspot", lambda c: c.integration_hub.summary().get("status"), _integration_hub_attributes),
        PluginDiscoverySensor(coordinator, core, "Email Plugin Discovery", "plugin_email", "mdi:email-outline", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "email"), "Waiting"), lambda c: _plugin_attributes(c, "email")),
        PluginDiscoverySensor(coordinator, core, "Pushover Plugin Discovery", "plugin_pushover", "mdi:message-badge-outline", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "pushover"), "Waiting"), lambda c: _plugin_attributes(c, "pushover")),
        PluginDiscoverySensor(coordinator, core, "Shelly Plugin Discovery", "plugin_shelly", "mdi:power-socket-eu", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "shelly"), "Waiting"), lambda c: _plugin_attributes(c, "shelly")),
        PluginDiscoverySensor(coordinator, core, "Zigbee Plugin Discovery", "plugin_zigbee", "mdi:zigbee", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "zigbee"), "Waiting"), lambda c: _plugin_attributes(c, "zigbee")),
        PluginDiscoverySensor(coordinator, core, "MQTT Plugin Discovery", "plugin_mqtt", "mdi:access-point-network", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "mqtt"), "Waiting"), lambda c: _plugin_attributes(c, "mqtt")),
        PluginDiscoverySensor(coordinator, core, "NAS Backup Plugin Discovery", "plugin_nas_backup", "mdi:nas", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "nas_backup"), "Waiting"), lambda c: _plugin_attributes(c, "nas_backup")),
        PluginDiscoverySensor(coordinator, core, "Inverter Plugin Discovery", "plugin_inverter_adapters", "mdi:solar-power-variant", lambda c: next((x.get("health") for x in c.integration_hub.summary().get("plugins", []) if x.get("id") == "inverter_adapters"), "Waiting"), lambda c: _plugin_attributes(c, "inverter_adapters")),
        SimpleSensor(coordinator, core, "Multi-Inverter Manager", "multi_inverter", "mdi:solar-power-variant", lambda c: c.energy_topology.summary().get("status"), lambda c: c.energy_topology.summary()),
        SimpleSensor(coordinator, core, "Energy Topology", "energy_topology", "mdi:transit-connection-variant", lambda c: c.energy_topology.summary().get("summary"), lambda c: c.energy_topology.summary()),
        SimpleSensor(coordinator, core, "Data Bus", "data_bus", "mdi:bus", lambda c: c.data_bus.summary().get("status"), lambda c: c.data_bus.summary()),
        SimpleSensor(coordinator, core, "Data Lake", "data_lake", "mdi:database-clock-outline", lambda c: c.data_lake.summary().get("status"), lambda c: c.data_lake.summary()),
        SimpleSensor(coordinator, core, "Knowledge Engine", "knowledge_engine", "mdi:book-open-variant", lambda c: c.knowledge.summary().get("status"), lambda c: c.knowledge.summary()),
        SimpleSensor(coordinator, core, "Knowledge Engine 2.0", "knowledge_engine_v2", "mdi:brain", lambda c: c.knowledge_v2.summary().get("status"), lambda c: c.knowledge_v2.summary()),
        SimpleSensor(coordinator, core, "Learning Intelligence 2.0", "learning_intelligence_v2", "mdi:chart-timeline-variant-shimmer", lambda c: c.learning_intelligence_v2.summary().get("status"), lambda c: c.learning_intelligence_v2.summary()),
        SimpleSensor(coordinator, core, "Knowledge Timeline", "knowledge_timeline", "mdi:timeline-clock-outline", lambda c: c.knowledge_timeline.summary().get("status"), lambda c: c.knowledge_timeline.summary()),
        SimpleSensor(coordinator, core, "Intelligence Memory", "intelligence_memory", "mdi:brain", lambda c: c.intelligence_memory.summary().get("status"), _intelligence_memory_attributes),
        SimpleSensor(coordinator, core, "Executive Briefing", "executive_briefing", "mdi:newspaper-variant-outline", lambda c: c.executive_briefing.summary().get("headline"), lambda c: c.executive_briefing.summary()),
        SimpleSensor(coordinator, core, "Opportunity Learning", "opportunity_learning", "mdi:school-outline", lambda c: c.opportunity_learning.summary().get("status"), lambda c: c.opportunity_learning.summary()),
        SimpleSensor(coordinator, core, "Adaptive Advisor", "adaptive_advisor", "mdi:account-sync-outline", lambda c: c.adaptive_advisor.summary().get("status"), lambda c: c.adaptive_advisor.summary()),
        SimpleSensor(coordinator, core, "Decision Engine", "decision_engine", "mdi:source-branch-check", lambda c: c.decision_engine.summary().get("decision"), lambda c: c.decision_engine.summary()),
        SimpleSensor(coordinator, core, "Scenario Simulator", "scenario_simulator", "mdi:compare-horizontal", lambda c: c.scenario_simulator.summary().get("status"), lambda c: c.scenario_simulator.summary()),
        SimpleSensor(coordinator, core, "Prediction Accuracy", "prediction_accuracy", "mdi:target-account", lambda c: c.prediction_accuracy.summary().get("status"), lambda c: c.prediction_accuracy.summary()),
        SimpleSensor(coordinator, core, "Home Profile", "home_profile", "mdi:home-analytics", lambda c: c.home_profile.summary().get("status"), lambda c: c.home_profile.summary()),
        SimpleSensor(coordinator, core, "Anomaly Intelligence", "anomaly_intelligence", "mdi:chart-bell-curve-cumulative", lambda c: c.anomaly_intelligence.summary().get("status"), lambda c: c.anomaly_intelligence.summary()),
        SimpleSensor(coordinator, core, "Intelligence Fusion", "intelligence_fusion", "mdi:brain", lambda c: c.intelligence_fusion.summary().get("status"), lambda c: c.intelligence_fusion.summary()),
        SimpleSensor(coordinator, core, "Runtime Resilience", "runtime_resilience", "mdi:shield-sync-outline", lambda c: c.runtime_resilience.summary().get("status"), lambda c: c.runtime_resilience.summary()),
        SimpleSensor(coordinator, core, "Data Consistency", "data_consistency", "mdi:compare-horizontal", lambda c: c.data_consistency.summary().get("status"), lambda c: c.data_consistency.summary()),
        SimpleSensor(coordinator, core, "Intelligence Quality Gate", "intelligence_quality_gate", "mdi:shield-check-outline", lambda c: c.intelligence_quality_gate.summary().get("status"), lambda c: c.intelligence_quality_gate.summary()),
        SimpleSensor(coordinator, core, "Release Readiness", "release_readiness", "mdi:clipboard-check-outline", lambda c: c.release_readiness.summary().get("status"), lambda c: c.release_readiness.summary()),
        SimpleSensor(coordinator, core, "Advisor 2.0", "advisor_v2", "mdi:account-tie-voice-outline", lambda c: c.advisor_v2.summary().get("recommendation"), lambda c: c.advisor_v2.summary()),
        SimpleSensor(coordinator, core, "Briefing Center", "briefing_center", "mdi:text-box-check-outline", lambda c: c.briefing.summary().get("status"), lambda c: c.briefing.summary()),
        SimpleSensor(coordinator, core, "Question Library", "question_library", "mdi:frequently-asked-questions", lambda c: c.question_library.summary().get("status"), lambda c: c.question_library.summary()),
        SimpleSensor(coordinator, core, "Capability Report", "capability_report", "mdi:clipboard-check-multiple-outline", lambda c: c.capability.summary().get("status"), lambda c: c.capability.summary()),
        SimpleSensor(coordinator, core, "Diagnostics", "diagnostics", "mdi:stethoscope", lambda c: c.diagnostics.summary().get("status"), lambda c: c.diagnostics.summary()),
        SimpleSensor(coordinator, core, "QA Diagnostics Center", "qa_diagnostics", "mdi:clipboard-pulse-outline", lambda c: c.qa_diagnostics.summary().get("status"), lambda c: c.qa_diagnostics.summary()),
        SimpleSensor(coordinator, core, "Event Bus", "event_bus", "mdi:transit-connection-variant", lambda c: f"{len(c.event_bus.events)} events", lambda c: {"event_count": len(c.event_bus.events), "recent_events": c.event_bus.recent(3), "note": "Recorder-safe compact list."}),
        SimpleSensor(coordinator, core, "Device Manager", "device_manager", "mdi:devices-cog", _device_manager_state, _device_manager_attributes),
        SimpleSensor(coordinator, core, "Device Import Wizard", "device_import_wizard", "mdi:database-import-outline", lambda c: c.device_import_wizard.summary().get("status", "Not run"), lambda c: c.device_import_wizard.summary()),
        SimpleSensor(coordinator, core, "Home Assistant Energy Import", "ha_energy_import", "mdi:home-assistant", lambda c: c.ha_energy_import.summary().get("status", "Not run"), lambda c: c.ha_energy_import.summary()),
        SimpleSensor(coordinator, core, "Helios Migration Preview", "helios_migration_preview", "mdi:database-arrow-right-outline", lambda c: c.migration.summary().get("status"), lambda c: c.migration.summary()),
        SimpleSensor(coordinator, core, "Update Engine", "update_engine", "mdi:update", lambda c: c.update_engine.summary().get("status"), lambda c: c.update_engine.summary()),
        DataQualitySensor(coordinator, core, "Data Quality", "data_quality", "mdi:check-decagram-outline", lambda c: c.data_quality.summary().get("status"), lambda c: c.data_quality.summary()),
        SimpleSensor(coordinator, core, "Historical Analytics", "historical_analytics", "mdi:chart-timeline-variant", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_summary()),
        SimpleSensor(coordinator, core, "Historical Chart Data", "historical_chart_data", "mdi:chart-areaspline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_chart_data()),
        SimpleSensor(coordinator, core, "Historical Explorer Recent", "historical_explorer_recent", "mdi:chart-timeline-variant-shimmer", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_explorer_recent_data()),
        SimpleSensor(coordinator, core, "Historical Explorer Year", "historical_explorer_year", "mdi:calendar-range", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_explorer_year_data()),
        SimpleSensor(coordinator, core, "Battery Performance Evidence", "battery_performance_evidence", "mdi:battery-sync-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_battery_performance_evidence()),
        SimpleSensor(coordinator, core, "Solar Performance Evidence", "solar_performance_evidence", "mdi:solar-power-variant-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_solar_performance_evidence()),
        SimpleSensor(coordinator, core, "Consumption Intelligence Evidence", "consumption_intelligence_evidence", "mdi:home-lightning-bolt-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_consumption_intelligence_evidence()),
        SimpleSensor(coordinator, core, "Consumption Battery Dependency Evidence", "consumption_battery_dependency_evidence", "mdi:home-battery-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_consumption_battery_dependency_evidence()),
        SimpleSensor(coordinator, core, "Consumption Battery Timing Evidence", "consumption_battery_timing_evidence", "mdi:battery-clock-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_consumption_battery_timing_evidence()),
        SimpleSensor(coordinator, core, "Consumption Battery Coverage Evidence", "consumption_battery_coverage_evidence", "mdi:battery-check-outline", lambda c: c.history.summary().get("status"), lambda c: c.history.recorder_consumption_battery_coverage_evidence()),
        SimpleSensor(coordinator, core, "Planning Engine", "planning_engine", "mdi:calendar-clock-outline", lambda c: c.planning_engine.recorder_summary().get("status"), lambda c: c.planning_engine.recorder_summary()),
        OptimizationIntelligenceSensor(coordinator, core, "Optimization Intelligence", "optimization_intelligence", "mdi:target", lambda c: c.optimization_intelligence.summary().get("optimization_score") if c.optimization_intelligence.summary().get("optimization_score") is not None else c.optimization_intelligence.summary().get("status"), _optimization_intelligence_attributes),
        SimpleSensor(coordinator, core, "Insight Intelligence", "insight_intelligence", "mdi:lightbulb-alert-outline", lambda c: c.insight_intelligence.summary().get("briefing", {}).get("headline", c.insight_intelligence.summary().get("status")), lambda c: _insight_intelligence_attributes(c)),
        SimpleSensor(coordinator, core, "Root Cause Intelligence", "root_cause_intelligence", "mdi:source-branch", lambda c: c.root_cause_intelligence.summary().get("severity", "Information"), lambda c: c.root_cause_intelligence.summary()),
        SimpleSensor(coordinator, core, "Correlation Confidence", "correlation_confidence", "mdi:vector-link", lambda c: c.correlation_confidence.summary().get("confidence_percent", 0), lambda c: c.correlation_confidence.summary()),
        SimpleSensor(coordinator, core, "Recommendation Priority", "recommendation_priority", "mdi:format-list-numbered", lambda c: c.recommendation_priority.summary().get("top_priority", "Information"), lambda c: c.recommendation_priority.summary()),
        SimpleSensor(coordinator, core, "System Story", "system_story", "mdi:text-box-outline", lambda c: c.system_story.summary().get("action_state", "Monitoring"), lambda c: c.system_story.summary()),
        SimpleSensor(coordinator, core, "Hyper Analytics", "hyper_analytics", "mdi:creation", lambda c: c.hyper_analytics.summary().get("headline"), lambda c: c.hyper_analytics.summary()),
        SimpleSensor(coordinator, core, "Zeus Brain", "zeus_brain", "mdi:head-cog-outline", lambda c: c.brain.summary().get("headline"), lambda c: c.brain.summary()),
        SimpleSensor(coordinator, core, "Observation Knowledge", "observation_knowledge", "mdi:graph-outline", lambda c: c.observation_knowledge.summary().get("headline"), lambda c: c.observation_knowledge.summary()),
        SimpleSensor(coordinator, core, "Reasoning Explain", "reasoning_explain", "mdi:source-branch-check", lambda c: c.reasoning_explain.summary().get("headline"), lambda c: c.reasoning_explain.summary()),
        SimpleSensor(coordinator, core, "Device Analytics", "device_analytics", "mdi:devices-clock", lambda c: c.device_analytics.summary().get("status"), _device_analytics_attributes),
        SimpleSensor(coordinator, core, "Device Energy Attribution", "device_energy_attribution", "mdi:source-branch-check", lambda c: c.device_energy_attribution.summary().get("status"), lambda c: c.device_energy_attribution.recorder_summary()),
        SimpleSensor(coordinator, core, "Daily Briefing", "daily_briefing", "mdi:text-box-check-outline", lambda c: c.daily_briefing.summary().get("status"), lambda c: c.daily_briefing.summary()),
        SimpleSensor(coordinator, core, "Finance Summary", "finance_summary", "mdi:cash-multiple", lambda c: c.finance.summary().get("status"), lambda c: c.finance.summary()),
        SimpleSensor(coordinator, core, "Weather Context", "weather_context", "mdi:weather-cloudy-clock", lambda c: c.weather.summary().get("status"), _weather_context_attributes),
        WeatherStatisticsSensor(coordinator, core, "Weather Statistics", "weather_statistics", "mdi:weather-cloudy-clock", lambda c: c.weather_history.summary().get("status"), lambda c: c.weather_history.summary()),
        SimpleSensor(coordinator, core, "Forecast", "forecast", "mdi:weather-partly-cloudy", lambda c: c.forecast.summary().get("status"), lambda c: {k: c.forecast.summary().get(k) for k in ("method", "confidence", "confidence_label", "confidence_factors", "weather", "raw_expected_solar_next_24h_kwh", "raw_expected_solar_following_24h_kwh", "expected_solar_next_24h_kwh", "expected_solar_following_24h_kwh", "adaptive_correction", "expected_consumption_next_24h_kwh", "expected_consumption_following_24h_kwh", "expected_grid_import_next_24h_kwh", "expected_grid_export_next_24h_kwh", "projected_battery_soc_24h_percent", "projected_battery_soc_48h_percent", "daily_forecast", "forecast_horizon_hours", "best_surplus_window", "recommendations", "summary", "limitations", "safety", "recorder_safe")}),
        SimpleSensor(coordinator, core, "Optimizer Preview", "optimizer_preview", "mdi:lightbulb-on-outline", lambda c: c.optimizer.summary().get("status"), lambda c: c.optimizer.summary()),
        SimpleSensor(coordinator, core, "Scheduler Preview", "scheduler_preview", "mdi:calendar-clock", lambda c: c.scheduler.summary().get("status"), _scheduler_preview_attributes),
        SimpleSensor(coordinator, core, "Learning Engine 2.0", "learning_preview", "mdi:brain", lambda c: c.learning.summary().get("status"), lambda c: c.learning.summary()),
        SimpleSensor(coordinator, core, "Long-Term Seasonal Analysis", "seasonal_analysis", "mdi:calendar-range", lambda c: c.learning.summary().get("confidence_label"), lambda c: c.learning.summary()),
        SimpleSensor(coordinator, core, "Home Efficiency", "home_efficiency", "mdi:home-analytics", lambda c: c.home_efficiency.summary().get("score"), lambda c: c.home_efficiency.summary()),
        SimpleSensor(coordinator, core, "Forecast Today", "forecast_today", "mdi:weather-sunny", lambda c: c.forecast.summary().get("expected_solar_next_24h_kwh"), lambda c: {"unit": "kWh", "confidence": c.forecast.summary().get("confidence"), "best_surplus_window": c.forecast.summary().get("best_surplus_window"), "daily_forecast": c.forecast.summary().get("daily_forecast", [])}),
        SimpleSensor(coordinator, core, "Forecast Tomorrow", "forecast_tomorrow", "mdi:weather-sunset-up", lambda c: c.forecast.summary().get("expected_solar_following_24h_kwh"), lambda c: {"unit": "kWh", "confidence": c.forecast.summary().get("confidence"), "daily": (c.forecast.summary().get("daily_forecast") or [None, None])[1] if len(c.forecast.summary().get("daily_forecast") or []) > 1 else None}),
        SimpleSensor(coordinator, core, "Forecast Consumption", "forecast_consumption", "mdi:home-lightning-bolt-outline", lambda c: c.forecast.summary().get("expected_consumption_next_24h_kwh"), lambda c: {"unit": "kWh", "next_24h_kwh": c.forecast.summary().get("expected_consumption_next_24h_kwh"), "following_24h_kwh": c.forecast.summary().get("expected_consumption_following_24h_kwh"), "confidence": c.forecast.summary().get("confidence"), "recorder_safe": True}),
        SimpleSensor(coordinator, core, "Forecast Battery", "forecast_battery", "mdi:battery-clock-outline", lambda c: c.forecast.summary().get("projected_battery_soc_24h_percent"), lambda c: {"unit": "%", "soc_24h_percent": c.forecast.summary().get("projected_battery_soc_24h_percent"), "soc_48h_percent": c.forecast.summary().get("projected_battery_soc_48h_percent"), "confidence": c.forecast.summary().get("confidence"), "limitations": c.forecast.summary().get("limitations"), "recorder_safe": True}),
        SimpleSensor(coordinator, core, "Forecast Grid", "forecast_grid", "mdi:transmission-tower", lambda c: c.forecast.summary().get("expected_grid_import_next_24h_kwh"), lambda c: {"unit": "kWh", "import_next_24h_kwh": c.forecast.summary().get("expected_grid_import_next_24h_kwh"), "export_next_24h_kwh": c.forecast.summary().get("expected_grid_export_next_24h_kwh"), "import_following_24h_kwh": c.forecast.summary().get("expected_grid_import_following_24h_kwh"), "export_following_24h_kwh": c.forecast.summary().get("expected_grid_export_following_24h_kwh"), "confidence": c.forecast.summary().get("confidence"), "recorder_safe": True}),
        SimpleSensor(coordinator, core, "Forecast Confidence", "forecast_confidence", "mdi:gauge", lambda c: c.forecast.summary().get("confidence"), lambda c: {"unit": "%", "label": c.forecast.summary().get("confidence_label"), "factors": c.forecast.summary().get("confidence_factors", {}), "method": c.forecast.summary().get("method"), "recorder_safe": True}),
        SimpleSensor(coordinator, core, "Next Best Energy Window", "next_best_window", "mdi:clock-star-four-points", lambda c: (c.forecast.summary().get("best_surplus_window") or {}).get("label") or "Calculating", lambda c: {"window": c.forecast.summary().get("best_surplus_window"), "recommendations": (c.forecast.summary().get("recommendations") or [])[:3], "safety": c.forecast.summary().get("safety"), "recorder_safe": True}),
        SimpleSensor(coordinator, core, "Optimization Score", "optimization_score", "mdi:gauge", lambda c: c.home_efficiency.summary().get("score"), lambda c: c.home_efficiency.summary()),
        SimpleSensor(coordinator, core, "AI Energy Advisor", "ai_energy_advisor", "mdi:account-tie-voice", lambda c: c.ai_advisor.summary().get("headline"), _advisor_attributes),
        SimpleSensor(coordinator, core, "Conversational Zeus Assistant", "conversational_assistant", "mdi:message-processing-outline", lambda c: c.conversational_assistant.summary().get("status"), lambda c: c.conversational_assistant.summary()),
        PredictiveBatterySensor(coordinator, core, "Predictive Battery Optimization", "predictive_battery", "mdi:battery-clock-outline", lambda c: c.predictive_battery.summary().get("strategy"), lambda c: c.predictive_battery.summary()),
        IntelligenceEngineSensor(coordinator, core, "Intelligence Engine", "intelligence_engine", "mdi:brain", lambda c: c.intelligence.summary().get("status"), lambda c: c.intelligence.summary()),
        SimpleSensor(coordinator, core, "Notification Engine", "notification_engine", "mdi:bell-outline", lambda c: c.notifications.summary().get("status"), lambda c: c.notifications.summary()),
        SimpleSensor(coordinator, core, "Dashboard API", "dashboard_api", "mdi:api", lambda c: c.dashboard_api.summary().get("status"), lambda c: c.dashboard_api.recorder_summary()),
        SimpleSensor(coordinator, core, "Settings API", "settings_api", "mdi:cog-transfer-outline", lambda c: c.settings_api.summary().get("status"), lambda c: {"schema_version": c.settings_api.summary().get("schema_version"), "device_count": len(c.settings_api.summary().get("devices", [])), "battery_capacity_kwh": (c.settings_api.summary().get("home_settings") or {}).get("battery_capacity_kwh"), "home_settings": {"owner_name": str((c.settings_api.summary().get("home_settings") or {}).get("owner_name") or "")[:80], "home_name": str((c.settings_api.summary().get("home_settings") or {}).get("home_name") or "Home")[:80], "use_owner_name": (c.settings_api.summary().get("home_settings") or {}).get("use_owner_name", True), "story_style": str((c.settings_api.summary().get("home_settings") or {}).get("story_style") or "friendly"), "briefing_length": str((c.settings_api.summary().get("home_settings") or {}).get("briefing_length") or "normal"), "data_epoch": (c.settings_api.summary().get("home_settings") or {}).get("data_epoch")}, "data_epoch": c.settings_api.summary().get("data_epoch"), "recorder_history_preserved": c.settings_api.summary().get("recorder_history_preserved", True), "persistence": c.settings_api.summary().get("persistence"), "safety": c.settings_api.summary().get("safety")}),
    ]

    sensors.extend([
        FinanceValueSensor(coordinator, core, "Grid Cost Today", "grid_cost_today", "grid_cost_today", "mdi:cash-minus"),
        FinanceValueSensor(coordinator, core, "Export Revenue Today", "export_revenue_today", "export_revenue_today", "mdi:cash-plus"),
        FinanceValueSensor(coordinator, core, "Solar Value Today", "solar_value_today", "solar_value_today", "mdi:solar-power"),
        FinanceValueSensor(coordinator, core, "Battery Support Value Today", "battery_support_value_today", "battery_support_value_today", "mdi:battery-heart-variant"),
        FinanceValueSensor(coordinator, core, "Net Benefit Today", "net_benefit_today", "net_benefit_today", "mdi:finance"),
        EnergyFlowValueSensor(coordinator, core, "Solar Power", "solar_power", "solar_power", "mdi:solar-power-variant", "W", SensorDeviceClass.POWER),
        TopologyValueSensor(coordinator, core, "Aggregated Inverter Power", "aggregated_inverter_power", "total_power_w", "mdi:solar-power-variant", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "House Power", "house_power", "house_power", "mdi:home-lightning-bolt", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Grid Import Power", "grid_import_power", "grid_import_power", "mdi:transmission-tower-import", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Grid Export Power", "grid_export_power", "grid_export_power", "mdi:transmission-tower-export", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Battery Power", "battery_power", "battery_power", "mdi:battery-sync", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Battery Charge Power", "battery_charge_power", "battery_charge_power", "mdi:battery-arrow-up", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Battery Discharge Power", "battery_discharge_power", "battery_discharge_power", "mdi:battery-arrow-down", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Battery SOC", "battery_soc", "battery_soc_percent", "mdi:battery", "%", SensorDeviceClass.BATTERY),
        EnergyFlowValueSensor(coordinator, core, "EV Power", "ev_power", "ev_power", "mdi:ev-station", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Heat Pump Power", "heat_pump_power", "heat_pump_power", "mdi:heat-pump", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Water Heater Power", "water_heater_power", "water_heater_power", "mdi:water-boiler", "W", SensorDeviceClass.POWER),
        EnergyFlowValueSensor(coordinator, core, "Known Major Loads Power", "known_major_loads_power", "known_major_loads_power", "mdi:devices", "W", SensorDeviceClass.POWER),
    ])
    async_add_entities(sensors)


class SimpleSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, core, name, key, icon, value_fn, attrs_fn) -> None:
        super().__init__(coordinator)
        self.core = core
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_icon = icon
        self.entity_id = f"sensor.aion_ems_zeus_{key}"
        self.value_fn = value_fn
        self.attrs_fn = attrs_fn

    @property
    def native_value(self) -> str:
        return self.value_fn(self.core)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self.attrs_fn(self.core) or {}
        return attrs


class PredictiveBatterySensor(SimpleSensor):
    """Full live Predictive Battery payload with heavy timeline unrecorded.

    The 48-hour advisory timeline remains available to Zeus frontend/Copilot in
    Home Assistant's live state machine. Recorder excludes only the timeline,
    avoiding the 16 KiB attribute-storage ceiling without changing battery math.
    """

    _unrecorded_attributes = frozenset({"timeline"})


class IntelligenceEngineSensor(SimpleSensor):
    """Full live Intelligence payload with duplicated engine snapshots unrecorded.

    Recommendations, top recommendation, confidence, battery strategy and the
    compact public intelligence context remain recordable. The embedded copies
    of Optimizer, Knowledge and Briefing are canonical elsewhere and are
    excluded from Recorder to avoid duplicate large payloads.
    """

    _unrecorded_attributes = frozenset({"optimizer", "knowledge", "briefing"})


class OptimizationIntelligenceSensor(SimpleSensor):
    """Live Daily Orchestrator detail with Recorder-safe persistence.

    Copilot/UI consume the complete daily plan from Home Assistant's live state
    machine. Recorder skips that nested plan because it is regenerated from
    canonical Forecast/Scheduler/Battery evidence and does not need historical
    duplication in the database.
    """

    _unrecorded_attributes = frozenset({"daily_energy_orchestrator"})


class DataQualitySensor(SimpleSensor):
    """Data-quality summary with recorder-safe live detail attributes.

    The full validation details remain available in Home Assistant's live state
    machine for the Zeus Status UI. Recorder skips the large collections so
    database rows stay below Home Assistant's 16 KiB attribute limit.
    """

    _unrecorded_attributes = frozenset({
        "source_scores",
        "device_health",
        "mapping_suggestions",
        "issues",
    })


class WeatherStatisticsSensor(SimpleSensor):
    """Full live Weather Intelligence payload, excluded from Recorder.

    The frontend needs the complete 31-day weather history, correlations,
    buckets, records and calendar intelligence. Home Assistant Recorder does
    not need those large analytics attributes, so MATCH_ALL keeps them live in
    the state machine while excluding them from database attribute storage.
    """

    _unrecorded_attributes = frozenset({MATCH_ALL})


class PluginDiscoverySensor(SimpleSensor):
    """Discovery entity whose large live candidate list is not recorded.

    The dashboard still receives the complete ``candidates`` attribute from the
    live state machine. Home Assistant Recorder excludes only that attribute,
    preventing the 16 KiB database warning without reducing discovery detail.
    """

    _unrecorded_attributes = frozenset({"candidates"})


class FinanceValueSensor(CoordinatorEntity, SensorEntity):
    """Numeric monetary value from the Finance Engine."""
    def __init__(self, coordinator, core, name, key, finance_key, icon) -> None:
        super().__init__(coordinator)
        self.core = core
        self.finance_key = finance_key
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_icon = icon
        self._attr_state_class = SensorStateClass.TOTAL
        self.entity_id = f"sensor.aion_ems_zeus_{key}"
    @property
    def native_value(self):
        return self.core.finance.summary().get(self.finance_key)
    @property
    def native_unit_of_measurement(self):
        return self.core.finance.summary().get("currency", "CHF")
    @property
    def extra_state_attributes(self):
        f = self.core.finance.summary()
        return {"configured": f.get("configured"), "tariff_mode": f.get("tariff_mode"), "import_tariff": f.get("import_tariff"), "export_tariff": f.get("export_tariff"), "standing_charge": f.get("standing_charge"), "vat_included": f.get("vat_included"), "assumptions": f.get("assumptions")}


class EnergyFlowValueSensor(CoordinatorEntity, SensorEntity):
    """Individual numeric sensor from Energy Flow snapshot."""

    def __init__(self, coordinator, core, name, key, flow_key, icon, unit, device_class) -> None:
        super().__init__(coordinator)
        self.core = core
        self.flow_key = flow_key
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.entity_id = f"sensor.aion_ems_zeus_{key}"

    @property
    def native_value(self):
        data = self.core.energy_flow.summary()
        flows = data.get("flows", {})
        item = flows.get(self.flow_key)

        if self.flow_key == "battery_soc_percent":
            return item

        if isinstance(item, dict):
            return item.get("w")

        return None

    @property
    def extra_state_attributes(self):
        flow = self.core.energy_flow.summary()
        source_entity = flow.get("entities", {}).get(self.flow_key)
        source_state = self.core.hass.states.get(source_entity) if source_entity else None
        update = self.core.update_engine.summary()
        return {
            "source": "aion_ems_energy_flow",
            "quality_score": flow.get("quality_score"),
            "available": flow.get("available", {}),
            "source_entity": source_entity,
            "source_last_changed": source_state.last_changed.isoformat() if source_state else None,
            "source_last_updated": source_state.last_updated.isoformat() if source_state else None,
            "zeus_last_updated": update.get("last_refreshed"),
            "update_latency_ms": update.get("update_latency_ms"),
            "update_mode": update.get("mode"),
            "safety": "Read-only derived sensor. No device control.",
        }


class TopologyValueSensor(CoordinatorEntity, SensorEntity):
    """Numeric read-only value from the multi-inverter topology engine."""

    def __init__(self, coordinator, core, name, key, topology_key, icon, unit, device_class) -> None:
        super().__init__(coordinator)
        self.core = core
        self.topology_key = topology_key
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.entity_id = f"sensor.aion_ems_zeus_{key}"

    @property
    def native_value(self):
        return self.core.energy_topology.summary().get(self.topology_key)

    @property
    def extra_state_attributes(self):
        data = self.core.energy_topology.summary() or {}
        return {
            "inverter_count": data.get("inverter_count"),
            "available_inverter_count": data.get("available_inverter_count"),
            "aggregation_mode": data.get("aggregation_mode"),
            "balance": data.get("balance"),
            "safety": data.get("safety"),
        }
