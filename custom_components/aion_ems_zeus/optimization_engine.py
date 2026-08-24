"""Evidence-first, recommendation-only optimization intelligence for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class OptimizationIntelligenceEngine:
    """Quantify and rank opportunities from existing canonical Zeus engines.

    This engine is deliberately a composition layer. It does not create a second
    Forecast, Finance, Scheduler, Battery, DEA or Planning accounting path. Every
    quantified value retains its source semantics so estimated opportunity cannot
    be confused with measured or forecast energy.
    """

    VERSION = "15.6.1"

    def __init__(self, event_bus, core) -> None:
        self.event_bus = event_bus
        self.core = core
        self._summary: dict[str, Any] = {
            "status": "Collecting",
            "version": self.VERSION,
            "recommendation_only": True,
            "optimization_score": None,
            "opportunities": [],
            "lost_opportunities": [],
            "opportunity_quantification": {},
            "weekly_summary": {},
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _num(cls, value: Any, default: float = 0.0) -> float:
        number = cls._number(value)
        return default if number is None else number

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(100.0, value))

    def _flow_w(self, flows: dict[str, Any], *keys: str) -> float | None:
        """Return an explicitly present live power value without inventing zero."""
        for key in keys:
            if key not in flows:
                continue
            value = flows.get(key)
            if isinstance(value, dict):
                if value.get("w") is not None:
                    return self._number(value.get("w"))
                if value.get("kw") is not None:
                    number = self._number(value.get("kw"))
                    return None if number is None else number * 1000.0
            elif value is not None:
                return self._number(value)
        return None

    def _daily_rows(self) -> list[dict[str, Any]]:
        history = self.core.history.summary() or {}
        rows = history.get("last_7_days")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @staticmethod
    def _priority(energy_kwh: float | None, confidence: float | None) -> str:
        """Presentation-only ranking; never changes the quantified amount."""
        energy = max(0.0, energy_kwh or 0.0)
        conf = max(0.0, confidence or 0.0)
        if energy >= 2.0 and conf >= 70:
            return "High"
        if energy >= 0.5 and conf >= 45:
            return "Medium"
        return "Low"

    @staticmethod
    def _evidence(source: str, evidence_type: str, fields: list[str], note: str | None = None) -> dict[str, Any]:
        item = {"source": source, "type": evidence_type, "fields": fields}
        if note:
            item["note"] = note
        return item

    def _opportunity(
        self,
        *,
        kind: str,
        title: str,
        reason: str,
        confidence: float | None,
        window: str | None,
        energy_kwh: float | None,
        savings: float | None,
        currency: str,
        duration: str | None,
        evidence: list[dict[str, Any]],
        assumptions: list[str] | None = None,
        target: dict[str, Any] | None = None,
        quantified_as: str = "estimated_opportunity",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "reason": reason,
            "priority": self._priority(energy_kwh, confidence),
            "confidence_percent": round(self._clamp(confidence), 0) if confidence is not None else None,
            "best_window": window,
            "expected_energy_benefit_kwh": round(max(0.0, energy_kwh), 3) if energy_kwh is not None else None,
            "expected_savings_chf": round(max(0.0, savings), 3) if savings is not None and currency == "CHF" else None,
            "expected_savings": round(max(0.0, savings), 3) if savings is not None else None,
            "currency": currency,
            "expected_duration": duration,
            "quantification_status": "quantified" if energy_kwh is not None or savings is not None else "not_quantifiable",
            "quantified_as": quantified_as,
            "evidence": evidence,
            "source_engines": list(dict.fromkeys(str(x.get("source")) for x in evidence if x.get("source"))),
            "assumptions": list(assumptions or []),
            "limitations": [],
            "recommendation_only": True,
        }
        if target:
            row["target"] = target
        if extra:
            row.update(extra)
        return row

    @staticmethod
    def _scheduler_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
        rows = summary.get("schedule") if isinstance(summary.get("schedule"), list) else summary.get("plan")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _role_summary(self, schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate only scheduler-provided values while keeping devices distinct."""
        roles: dict[str, dict[str, Any]] = {}
        for row in schedule:
            role = str(row.get("device_type") or "custom")
            bucket = roles.setdefault(role, {
                "role": role,
                "device_count": 0,
                "device_ids": [],
                "device_names": [],
                "planned_energy_kwh": 0.0,
                "solar_covered_energy_kwh": 0.0,
            })
            bucket["device_count"] += 1
            if row.get("device_id") is not None:
                bucket["device_ids"].append(row.get("device_id"))
            if row.get("device_name"):
                bucket["device_names"].append(row.get("device_name"))
            energy = max(0.0, self._num(row.get("expected_energy_kwh")))
            coverage = max(0.0, min(100.0, self._num(row.get("solar_coverage_percent")))) / 100.0
            bucket["planned_energy_kwh"] += energy
            bucket["solar_covered_energy_kwh"] += energy * coverage
        result = []
        for bucket in roles.values():
            bucket["planned_energy_kwh"] = round(bucket["planned_energy_kwh"], 3)
            bucket["solar_covered_energy_kwh"] = round(bucket["solar_covered_energy_kwh"], 3)
            result.append(bucket)
        return sorted(result, key=lambda x: (-x["solar_covered_energy_kwh"], str(x["role"])))

    def _strategy_score(
        self,
        *,
        strategy: str,
        forecast_solar: float | None,
        forecast_home: float | None,
        forecast_import: float | None,
        forecast_export: float | None,
        planned_energy: float | None,
        solar_covered_energy: float | None,
        scheduled_saving: float | None,
        import_tariff: float | None,
        export_tariff: float | None,
        forecast_confidence: float | None,
    ) -> dict[str, Any]:
        """Score advisory planning strategies without claiming mathematical optimality.

        This is a transparent heuristic comparison layer over the canonical
        Forecast/Scheduler outputs. It does not mutate schedules or measured data.
        """
        solar=max(0.0,self._num(forecast_solar))
        home=max(0.0,self._num(forecast_home))
        imp=max(0.0,self._num(forecast_import))
        exp=max(0.0,self._num(forecast_export))
        planned=max(0.0,self._num(planned_energy))
        solar_cov=max(0.0,self._num(solar_covered_energy))
        saving=max(0.0,self._num(scheduled_saving))
        confidence=self._clamp(forecast_confidence) if forecast_confidence is not None else 0.0

        local_use=max(0.0,min(solar,home))
        self_consumption=(local_use/solar*100.0) if solar>0 else None
        self_sufficiency=(max(0.0,home-imp)/home*100.0) if home>0 else None

        import_cost=(imp*import_tariff) if import_tariff is not None else None
        export_value=(exp*export_tariff) if export_tariff is not None else None
        net_grid_cost=(import_cost-export_value) if import_cost is not None and export_value is not None else None

        if strategy=="lowest_cost":
            score=50.0
            if net_grid_cost is not None:
                score += max(-25.0,min(25.0,25.0-net_grid_cost*8.0))
            score += min(15.0,saving*10.0)
            score += min(10.0,confidence*0.10)
            reason="Prioritizes tariff-aware savings and lower net grid cost using the current forecast and supported flexible-load plan."
        elif strategy=="highest_self_consumption":
            score=40.0
            if self_consumption is not None:
                score += self_consumption*0.35
            score += min(15.0,solar_cov*5.0)
            score += min(10.0,confidence*0.10)
            reason="Prioritizes using forecast solar locally and shifting supported flexible loads into solar-rich periods."
        else:
            score=45.0
            if self_sufficiency is not None:
                score += self_sufficiency*0.30
            score += max(0.0,min(20.0,20.0-imp*4.0))
            score += min(10.0,confidence*0.10)
            reason="Prioritizes reducing forecast grid import while preserving the existing recommendation-only planning boundary."

        return {
            "strategy":strategy,
            "score":round(max(0.0,min(100.0,score)),1),
            "reason":reason,
            "forecast_confidence_percent":round(confidence,1) if forecast_confidence is not None else None,
            "expected_solar_kwh":round(solar,3),
            "expected_home_kwh":round(home,3),
            "expected_grid_import_kwh":round(imp,3),
            "expected_grid_export_kwh":round(exp,3),
            "planned_flexible_energy_kwh":round(planned,3) if planned_energy is not None else None,
            "solar_covered_flexible_energy_kwh":round(solar_cov,3) if solar_covered_energy is not None else None,
            "estimated_scheduler_saving":round(saving,3) if scheduled_saving is not None else None,
            "expected_self_consumption_percent":round(self_consumption,1) if self_consumption is not None else None,
            "expected_self_sufficiency_percent":round(self_sufficiency,1) if self_sufficiency is not None else None,
            "estimated_import_cost":round(import_cost,3) if import_cost is not None else None,
            "estimated_export_value":round(export_value,3) if export_value is not None else None,
            "estimated_net_grid_cost":round(net_grid_cost,3) if net_grid_cost is not None else None,
            "method":"transparent_heuristic_strategy_comparison_v1",
            "mathematical_optimum_claimed":False,
            "recommendation_only":True,
        }

    def _strategy_comparison(
        self,
        *,
        forecast_solar: float | None,
        forecast_home: float | None,
        forecast_import: float | None,
        forecast_export: float | None,
        planned_energy: float | None,
        solar_covered_energy: float | None,
        scheduled_saving: float | None,
        import_tariff: float | None,
        export_tariff: float | None,
        forecast_confidence: float | None,
    ) -> dict[str, Any]:
        rows=[
            self._strategy_score(strategy="lowest_cost",forecast_solar=forecast_solar,forecast_home=forecast_home,forecast_import=forecast_import,forecast_export=forecast_export,planned_energy=planned_energy,solar_covered_energy=solar_covered_energy,scheduled_saving=scheduled_saving,import_tariff=import_tariff,export_tariff=export_tariff,forecast_confidence=forecast_confidence),
            self._strategy_score(strategy="highest_self_consumption",forecast_solar=forecast_solar,forecast_home=forecast_home,forecast_import=forecast_import,forecast_export=forecast_export,planned_energy=planned_energy,solar_covered_energy=solar_covered_energy,scheduled_saving=scheduled_saving,import_tariff=import_tariff,export_tariff=export_tariff,forecast_confidence=forecast_confidence),
            self._strategy_score(strategy="lowest_grid_import",forecast_solar=forecast_solar,forecast_home=forecast_home,forecast_import=forecast_import,forecast_export=forecast_export,planned_energy=planned_energy,solar_covered_energy=solar_covered_energy,scheduled_saving=scheduled_saving,import_tariff=import_tariff,export_tariff=export_tariff,forecast_confidence=forecast_confidence),
        ]
        ranked=sorted(rows,key=lambda x:x["score"],reverse=True)
        return {
            "status":"Ready" if ranked else "Collecting",
            "recommended_strategy":ranked[0]["strategy"] if ranked else None,
            "recommended_score":ranked[0]["score"] if ranked else None,
            "strategies":ranked,
            "comparison_note":"Advisory heuristic comparison only. This does not prove a global mathematical optimum and does not alter device schedules.",
            "control_permission":False,
        }

    def _parse_slot_time(self, value: Any) -> datetime | None:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _candidate_windows(
        self,
        *,
        item: dict[str, Any],
        forecast_rows: list[dict[str, Any]],
        import_tariff: float | None,
        export_tariff: float | None,
    ) -> list[dict[str, Any]]:
        """Build feasible start windows for one quantified flexible load.

        Only Scheduler rows that are already quantification-supported are accepted.
        Candidate windows are derived from canonical forecast rows and explicit
        Scheduler timing boundaries. No device requirement is invented here.
        """
        duration_min=max(15,int(self._num(item.get("duration_minutes")) or 60))
        energy_kwh=max(0.0,self._num(item.get("expected_energy_kwh")))
        if energy_kwh<=0:
            return []
        power_w=max(50.0,self._num(item.get("required_power_w")) or energy_kwh*1000.0/(duration_min/60.0))
        hours=max(1,int(math.ceil(duration_min/60.0)))
        earliest=self._parse_slot_time(item.get("earliest_start") or item.get("suggested_start"))
        latest_end=self._parse_slot_time(item.get("deadline") or item.get("latest_end") or item.get("suggested_end"))
        candidates=[]
        for idx,row in enumerate(forecast_rows):
            start=self._parse_slot_time(row.get("time"))
            if start is None:
                continue
            end=start+timedelta(hours=hours)
            if earliest and start<earliest:
                continue
            if latest_end and end>latest_end:
                continue
            if idx+hours>len(forecast_rows):
                continue
            span=forecast_rows[idx:idx+hours]
            if any(self._parse_slot_time(x.get("time")) is None for x in span):
                continue
            solar_kwh=sum(max(0.0,self._num(x.get("solar_power_w")))*1.0/1000.0 for x in span)
            base_import_kwh=sum(max(0.0,self._num(x.get("grid_import_power_w")))*1.0/1000.0 for x in span)
            base_export_kwh=sum(max(0.0,self._num(x.get("grid_export_power_w")))*1.0/1000.0 for x in span)
            load_kwh=min(energy_kwh,power_w*hours/1000.0)
            solar_capture=min(load_kwh,base_export_kwh if base_export_kwh>0 else solar_kwh)
            extra_import=max(0.0,load_kwh-solar_capture)
            reduced_export=min(base_export_kwh,solar_capture)
            cost_delta=None
            if import_tariff is not None and export_tariff is not None:
                cost_delta=extra_import*import_tariff-reduced_export*export_tariff
            candidates.append({
                "start":start.isoformat(),
                "end":end.isoformat(),
                "duration_minutes":duration_min,
                "energy_kwh":round(energy_kwh,3),
                "power_w":round(power_w,1),
                "solar_capture_kwh":round(solar_capture,3),
                "extra_grid_import_kwh":round(extra_import,3),
                "reduced_grid_export_kwh":round(reduced_export,3),
                "estimated_cost_delta":round(cost_delta,4) if cost_delta is not None else None,
                "slot_index":idx,
                "slot_hours":hours,
            })
        # Bound search width while keeping the most relevant feasible windows.
        ranked=sorted(
            candidates,
            key=lambda x: (
                x["estimated_cost_delta"] if x["estimated_cost_delta"] is not None else 0.0,
                x["extra_grid_import_kwh"],
                -x["solar_capture_kwh"],
                x["start"],
            )
        )
        return ranked[:10]

    @staticmethod
    def _windows_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
        try:
            a0=datetime.fromisoformat(str(a["start"]).replace("Z","+00:00"))
            a1=datetime.fromisoformat(str(a["end"]).replace("Z","+00:00"))
            b0=datetime.fromisoformat(str(b["start"]).replace("Z","+00:00"))
            b1=datetime.fromisoformat(str(b["end"]).replace("Z","+00:00"))
            return max(a0,b0)<min(a1,b1)
        except (KeyError,TypeError,ValueError):
            return False

    def _objective_value(self, schedule: list[dict[str, Any]], objective: str) -> float:
        import_kwh=sum(self._num(x.get("extra_grid_import_kwh")) for x in schedule)
        solar_kwh=sum(self._num(x.get("solar_capture_kwh")) for x in schedule)
        cost=sum(self._num(x.get("estimated_cost_delta")) for x in schedule if x.get("estimated_cost_delta") is not None)
        if objective=="lowest_cost":
            return cost
        if objective=="highest_self_consumption":
            return -solar_kwh
        return import_kwh

    def _constrained_schedule_search(
        self,
        *,
        objective: str,
        schedule_rows: list[dict[str, Any]],
        forecast_rows: list[dict[str, Any]],
        import_tariff: float | None,
        export_tariff: float | None,
    ) -> dict[str, Any]:
        """Find the best feasible discrete schedule among supported candidate windows.

        This is an exact enumeration over the bounded candidate set generated for
        each supported Scheduler load. It therefore claims optimality only within
        that enumerated discrete feasible set, never a global continuous optimum.
        """
        devices=[]
        for row in schedule_rows[:6]:
            if row.get("quantification_supported") is not True:
                continue
            candidates=self._candidate_windows(
                item=row,
                forecast_rows=forecast_rows,
                import_tariff=import_tariff,
                export_tariff=export_tariff,
            )
            if not candidates:
                continue
            devices.append({
                "device_id":row.get("device_id"),
                "device_name":row.get("device_name"),
                "device_type":row.get("device_type"),
                "candidates":candidates,
            })
        if not devices:
            return {
                "status":"Collecting",
                "objective":objective,
                "feasible_schedule_count":0,
                "evaluated_schedule_count":0,
                "schedule":[],
                "reason":"No quantification-supported flexible loads currently have feasible forecast windows.",
                "solver_method":"bounded_discrete_enumeration",
                "optimality_scope":"No feasible candidate schedule.",
                "control_permission":False,
            }

        best=None
        evaluated=0
        feasible=0

        # One start window per supported device. Prevent overlapping schedules for
        # identical device IDs; different devices may run concurrently.
        candidate_lists=[d["candidates"] for d in devices]
        max_combinations=50000
        for combo in itertools.product(*candidate_lists):
            evaluated+=1
            if evaluated>max_combinations:
                break
            chosen=[]
            valid=True
            for device,window in zip(devices,combo):
                row={**window,"device_id":device["device_id"],"device_name":device["device_name"],"device_type":device["device_type"]}
                if any(x["device_id"]==row["device_id"] and self._windows_overlap(x,row) for x in chosen):
                    valid=False
                    break
                chosen.append(row)
            if not valid:
                continue
            feasible+=1
            value=self._objective_value(chosen,objective)
            if best is None or value<best["objective_value"]:
                best={"objective_value":value,"schedule":chosen}

        if best is None:
            return {
                "status":"No feasible schedule",
                "objective":objective,
                "feasible_schedule_count":feasible,
                "evaluated_schedule_count":evaluated,
                "schedule":[],
                "reason":"The bounded solver did not find a feasible assignment that satisfies the available constraints.",
                "solver_method":"bounded_discrete_enumeration",
                "optimality_scope":"No feasible candidate schedule.",
                "control_permission":False,
            }

        schedule=best["schedule"]
        total_energy=sum(self._num(x.get("energy_kwh")) for x in schedule)
        solar_capture=sum(self._num(x.get("solar_capture_kwh")) for x in schedule)
        extra_import=sum(self._num(x.get("extra_grid_import_kwh")) for x in schedule)
        reduced_export=sum(self._num(x.get("reduced_grid_export_kwh")) for x in schedule)
        cost_delta=sum(self._num(x.get("estimated_cost_delta")) for x in schedule if x.get("estimated_cost_delta") is not None)

        return {
            "status":"Ready",
            "objective":objective,
            "feasible_schedule_count":feasible,
            "evaluated_schedule_count":evaluated,
            "search_truncated":evaluated>max_combinations,
            "schedule":schedule,
            "planned_energy_kwh":round(total_energy,3),
            "solar_capture_kwh":round(solar_capture,3),
            "extra_grid_import_kwh":round(extra_import,3),
            "reduced_grid_export_kwh":round(reduced_export,3),
            "estimated_cost_delta":round(cost_delta,4) if import_tariff is not None and export_tariff is not None else None,
            "solver_method":"bounded_discrete_enumeration",
            "optimality_scope":"Best schedule found within the enumerated feasible candidate windows for quantification-supported loads only.",
            "global_continuous_optimum_claimed":False,
            "recommendation_only":True,
            "device_need_policy":"Constrained optimization consumes only Scheduler rows that passed the current need-to-run gate.",
            "control_permission":False,
        }

    def _constrained_optimization(
        self,
        *,
        scheduler: dict[str, Any],
        forecast: dict[str, Any],
        import_tariff: float | None,
        export_tariff: float | None,
    ) -> dict[str, Any]:
        rows=self._scheduler_rows(scheduler)
        supported=[x for x in rows if x.get("quantification_supported") is True]
        forecast_rows=forecast.get("planning_hourly") or forecast.get("hourly") or []
        forecast_rows=[x for x in forecast_rows if isinstance(x,dict)]
        results={}
        for objective in ("lowest_cost","highest_self_consumption","lowest_grid_import"):
            results[objective]=self._constrained_schedule_search(
                objective=objective,
                schedule_rows=supported,
                forecast_rows=forecast_rows[:48],
                import_tariff=import_tariff,
                export_tariff=export_tariff,
            )
        ready=[x for x in results.values() if x.get("status")=="Ready"]
        if not ready:
            return {
                "status":"Collecting",
                "recommended_objective":None,
                "results":results,
                "solver_method":"bounded_discrete_enumeration",
                "recommendation_only":True,
                "control_permission":False,
            }

        def rank_value(row):
            objective=row.get("objective")
            if objective=="lowest_cost":
                return self._num(row.get("estimated_cost_delta"))
            if objective=="highest_self_consumption":
                return -self._num(row.get("solar_capture_kwh"))
            return self._num(row.get("extra_grid_import_kwh"))

        recommended=min(ready,key=rank_value)
        return {
            "status":"Ready",
            "recommended_objective":recommended.get("objective"),
            "recommended_schedule":recommended.get("schedule") or [],
            "results":results,
            "solver_method":"bounded_discrete_enumeration",
            "optimality_scope":"Discrete feasible candidate windows only; no global continuous optimum claim.",
            "recommendation_only":True,
            "control_permission":False,
        }

    def refresh(self) -> dict[str, Any]:
        flow = self.core.energy_flow.summary() or {}
        flows = flow.get("flows") if isinstance(flow.get("flows"), dict) else flow
        forecast = self.core.forecast.summary() or {}
        finance = self.core.finance.summary() or {}
        scheduler = self.core.scheduler.summary() or {}
        planning = self.core.planning_engine.summary() or {}
        battery = self.core.predictive_battery.summary() or {}
        behavior = getattr(self.core, "behavior_intelligence", None)
        behavior_summary = behavior.summary() if behavior and callable(getattr(behavior, "summary", None)) else {}
        opportunity_learning = getattr(self.core, "opportunity_learning", None)
        learning_summary = opportunity_learning.summary() if opportunity_learning and callable(getattr(opportunity_learning, "summary", None)) else {}
        load_learning_adjustment = int(opportunity_learning.confidence_adjustment("load")) if opportunity_learning and callable(getattr(opportunity_learning, "confidence_adjustment", None)) else 0
        battery_learning_adjustment = int(opportunity_learning.confidence_adjustment("battery")) if opportunity_learning and callable(getattr(opportunity_learning, "confidence_adjustment", None)) else 0

        finance_configured = bool(finance.get("configured"))
        currency = str(finance.get("currency") or scheduler.get("currency") or battery.get("currency") or "CHF").upper()[:4]
        import_tariff = self._number(finance.get("import_tariff")) if finance_configured else None
        export_tariff = self._number(finance.get("export_tariff")) if finance_configured else None

        forecast_ready = str(forecast.get("status") or "").lower() == "ready"
        forecast_confidence = self._number(forecast.get("confidence"))
        forecast_import = self._number(forecast.get("expected_grid_import_next_24h_kwh")) if forecast_ready else None
        forecast_export = self._number(forecast.get("expected_grid_export_next_24h_kwh")) if forecast_ready else None
        forecast_solar = self._number(forecast.get("expected_solar_next_24h_kwh")) if forecast_ready else None
        forecast_home = self._number(forecast.get("expected_consumption_next_24h_kwh")) if forecast_ready else None
        best_window = forecast.get("best_surplus_window") if isinstance(forecast.get("best_surplus_window"), dict) else {}
        best_window_label = str(best_window.get("label") or "") or None

        solar_w = self._flow_w(flows, "solar_power", "solar_power_w", "solar_w")
        home_w = self._flow_w(flows, "house_power", "house_power_w", "home_power_w", "home_w")
        import_w = self._flow_w(flows, "grid_import_power", "grid_import_power_w", "grid_import_w")
        export_w = self._flow_w(flows, "grid_export_power", "grid_export_power_w", "grid_export_w")
        battery_charge_w = self._flow_w(flows, "battery_charge_power", "battery_charge_power_w")
        battery_discharge_w = self._flow_w(flows, "battery_discharge_power", "battery_discharge_power_w")
        battery_signed_w = self._flow_w(flows, "battery_power", "battery_power_w")
        soc = self._number(flows.get("battery_soc_percent", flow.get("battery_soc_percent")))

        schedule = self._scheduler_rows(scheduler)
        # Flexible Load Planning may rank assumption-limited default profiles,
        # but Opportunity Quantification consumes only Scheduler rows explicitly
        # marked as having sufficient profile evidence. Older scheduler payloads
        # remain compatible by treating a missing marker as supported.
        quantified_schedule = [row for row in schedule if row.get("quantification_supported", True) is True]
        role_summary = self._role_summary(quantified_schedule)

        constrained_optimization = self._constrained_optimization(
            scheduler=scheduler,
            forecast=forecast,
            import_tariff=import_tariff,
            export_tariff=export_tariff,
        )

        # Keep the Scheduler's advisory plan distinct from the subset that is
        # evidence-supported for Opportunity Quantification. Assumption-limited
        # rows remain useful for recommendation order, but never become
        # quantified shiftable kWh/CHF merely because a default profile exists.
        advisory_planned_energy = self._number(scheduler.get("total_planned_energy_kwh"))
        if advisory_planned_energy is None and schedule:
            advisory_planned_energy = sum(max(0.0, self._num(row.get("expected_energy_kwh"))) for row in schedule)
        assumption_limited_count = int(self._num(scheduler.get("assumption_limited_device_count"))) if scheduler.get("assumption_limited_device_count") is not None else sum(
            1 for row in schedule if row.get("quantification_supported") is not True
        )

        planned_energy = self._number(scheduler.get("quantified_planned_energy_kwh"))
        if planned_energy is None and quantified_schedule:
            planned_energy = sum(max(0.0, self._num(row.get("expected_energy_kwh"))) for row in quantified_schedule)
        solar_covered_energy = self._number(scheduler.get("quantified_solar_covered_energy_kwh"))
        if solar_covered_energy is None and quantified_schedule:
            solar_covered_energy = sum(
                max(0.0, self._num(row.get("expected_energy_kwh")))
                * max(0.0, min(100.0, self._num(row.get("solar_coverage_percent")))) / 100.0
                for row in quantified_schedule
            )
        scheduled_saving = self._number(scheduler.get("estimated_total_saving")) if bool(scheduler.get("tariff_aware")) and quantified_schedule else None

        strategy_comparison = self._strategy_comparison(
            forecast_solar=forecast_solar,
            forecast_home=forecast_home,
            forecast_import=forecast_import,
            forecast_export=forecast_export,
            planned_energy=planned_energy,
            solar_covered_energy=solar_covered_energy,
            scheduled_saving=scheduled_saving,
            import_tariff=import_tariff,
            export_tariff=export_tariff,
            forecast_confidence=forecast_confidence,
        )

        opportunities: list[dict[str, Any]] = []

        # Device-level flexible-load opportunities come directly from the canonical
        # Scheduler. We only present/aggregate its expected energy, solar coverage,
        # confidence and tariff-aware saving; no alternative runtime model is used.
        for item in quantified_schedule:
            energy = self._number(item.get("expected_energy_kwh"))
            coverage = self._number(item.get("solar_coverage_percent"))
            confidence = self._number(item.get("confidence_percent"))
            if confidence is not None:
                confidence = self._clamp(confidence + load_learning_adjustment)
            saving = self._number(item.get("estimated_saving")) if item.get("estimated_saving") is not None else None
            solar_energy = None
            if energy is not None and coverage is not None:
                solar_energy = max(0.0, energy) * max(0.0, min(100.0, coverage)) / 100.0
            target = {
                "scope": "device",
                "device_id": item.get("device_id"),
                "device_name": item.get("device_name"),
                "role": item.get("device_type"),
            }
            opportunities.append(self._opportunity(
                kind="flexible_load_shift",
                title=f"Shift {item.get('device_name') or 'registered flexible load'} into forecast solar",
                reason=str(item.get("reason") or "The canonical Intelligent Scheduler identified a supported forecast window for this registered load."),
                confidence=confidence,
                window=str(item.get("suggested_start") or best_window_label or "") or None,
                energy_kwh=solar_energy,
                savings=saving,
                currency=currency,
                duration=(f"{int(self._num(item.get('duration_minutes')))} min" if item.get("duration_minutes") is not None else None),
                evidence=[
                    self._evidence("Intelligent Scheduler", "forecast_plan", ["expected_energy_kwh", "solar_coverage_percent", "confidence_percent", "suggested_start", "suggested_end"]),
                    self._evidence("Registered Devices / DEA", "registered_device", ["device_id", "device_name", "device_type"]),
                    self._evidence("Forecast", "forecast", ["best_surplus_window", "confidence"]),
                    self._evidence("Opportunity Learning", "learned_confidence_calibration", ["confidence_adjustments"], f"Load confidence adjustment {load_learning_adjustment:+d} points."),
                ] + ([self._evidence("Finance", "configured_tariff", ["import_tariff", "export_tariff", "currency"])] if saving is not None else []),
                assumptions=[str(scheduler.get("method"))] if scheduler.get("method") else [],
                target=target,
                extra={
                    "planned_load_energy_kwh": round(max(0.0, energy), 3) if energy is not None else None,
                    "forecast_solar_coverage_percent": round(max(0.0, min(100.0, coverage)), 1) if coverage is not None else None,
                    "solar_covered_energy_kwh": round(max(0.0, solar_energy), 3) if solar_energy is not None else None,
                    "suggested_end": item.get("suggested_end"),
                },
            ))

        # Forecast export is a canonical prediction of energy that may leave the
        # site after Forecast's existing battery model. It is an opportunity pool,
        # not an invented claim that all of it can be captured.
        if forecast_export is not None and forecast_export > 0:
            opportunities.append(self._opportunity(
                kind="forecast_surplus_pool",
                title="Forecast solar surplus is available for optimization",
                reason="Forecast predicts grid export in the next 24 hours. Zeus treats this as an available opportunity pool, not as automatically shiftable energy.",
                confidence=forecast_confidence,
                window=best_window_label,
                energy_kwh=max(0.0, forecast_export),
                savings=None,
                currency=currency,
                duration=None,
                evidence=[self._evidence("Forecast", "forecast", ["expected_grid_export_next_24h_kwh", "best_surplus_window", "confidence"])],
                assumptions=[str(forecast.get("limitations"))] if forecast.get("limitations") else [],
                quantified_as="forecast_value",
                extra={"assignment_status": "unassigned_until_supported_load_or_battery_evidence"},
            ))

        # Battery opportunity is accepted only from the canonical predictive battery
        # engine and only as fully quantified when a real battery is registered.
        battery_config = battery.get("battery_config") if isinstance(battery.get("battery_config"), dict) else {}
        battery_configured = bool(battery_config.get("configured"))
        battery_avoided_import = self._number(battery.get("estimated_avoided_import_kwh")) if battery_configured else None
        battery_saving = self._number(battery.get("estimated_saving")) if battery_configured and bool(battery.get("tariff_aware")) else None
        battery_confidence = self._number(battery.get("optimizer_confidence_percent"))
        if battery_confidence is not None:
            battery_confidence = self._clamp(battery_confidence + battery_learning_adjustment)
        if battery_avoided_import is not None and battery_avoided_import > 0:
            opportunities.append(self._opportunity(
                kind="battery_import_reduction",
                title="Battery can support forecast grid-import reduction",
                reason=str(battery.get("reason") or "Predictive Battery Optimization identified usable energy above the protected reserve."),
                confidence=battery_confidence,
                window="Predictive battery timeline",
                energy_kwh=max(0.0, battery_avoided_import),
                savings=battery_saving,
                currency=currency,
                duration="48-hour advisory horizon",
                evidence=[
                    self._evidence("Predictive Battery Optimization", "battery_model", ["estimated_avoided_import_kwh", "recommended_reserve_percent", "optimizer_confidence_percent", "battery_config"]),
                    self._evidence("Forecast", "forecast", ["timeline_24h", "confidence"]),
                    self._evidence("Opportunity Learning", "learned_confidence_calibration", ["confidence_adjustments"], f"Battery confidence adjustment {battery_learning_adjustment:+d} points."),
                ] + ([self._evidence("Finance", "configured_tariff", ["import_tariff", "export_tariff", "currency"])] if battery_saving is not None else []),
                assumptions=[str(x) for x in (battery.get("assumptions") or [])],
                target={"scope": "battery", "device_id": battery_config.get("device_id"), "device_name": battery_config.get("device_name")},
            ))


        # Battery + Load Coordination Foundation (15.3.0).
        #
        # This is composition only. It does not simulate another battery, create
        # another load plan, or allocate forecast energy independently. It
        # compares the canonical Scheduler and Predictive Battery outputs and
        # explicitly preserves their different horizons/possible overlap.
        role_labels = {
            "water_heater": "DHW",
            "ev_charger": "EV / Car",
            "heat_pump": "Heat Pump",
            "dishwasher": "Dishwasher",
            "washing_machine": "Washing Machine",
            "dryer": "Dryer",
            "pool_pump": "Pool Pump",
            "air_conditioner": "Air Conditioner",
        }

        load_candidates = []
        for item in quantified_schedule:
            energy = self._number(item.get("expected_energy_kwh"))
            coverage = self._number(item.get("solar_coverage_percent"))
            solar_energy = (
                max(0.0, energy) * max(0.0, min(100.0, coverage)) / 100.0
                if energy is not None and coverage is not None else None
            )
            load_candidates.append({
                "device_id": item.get("device_id"),
                "device_name": item.get("device_name"),
                "device_type": item.get("device_type"),
                "role": role_labels.get(str(item.get("device_type") or ""), str(item.get("device_type") or "Registered Load")),
                "suggested_start": item.get("suggested_start"),
                "suggested_end": item.get("suggested_end"),
                "planned_energy_kwh": round(max(0.0, energy), 3) if energy is not None else None,
                "solar_covered_energy_kwh": round(max(0.0, solar_energy), 3) if solar_energy is not None else None,
                "solar_coverage_percent": round(max(0.0, min(100.0, coverage)), 1) if coverage is not None else None,
                "potential_saving": round(max(0.0, self._num(item.get("estimated_saving"))), 3) if item.get("estimated_saving") is not None else None,
                "currency": currency if item.get("estimated_saving") is not None else None,
                "confidence_percent": round(self._clamp(self._num(item.get("confidence_percent")) + load_learning_adjustment), 0) if item.get("confidence_percent") is not None else None,
                "planning_rank": item.get("planning_rank"),
                "quantification_supported": True,
                "source": "Intelligent Scheduler",
            })
        load_candidates.sort(
            key=lambda x: (
                self._num(x.get("solar_covered_energy_kwh")),
                self._num(x.get("potential_saving")),
                self._num(x.get("confidence_percent")),
            ),
            reverse=True,
        )

        def aggregate_role(dtype: str, label: str) -> dict[str, Any] | None:
            rows = [x for x in load_candidates if str(x.get("device_type") or "") == dtype]
            if not rows:
                return None
            energy_values = [self._number(x.get("solar_covered_energy_kwh")) for x in rows]
            savings_values = [self._number(x.get("potential_saving")) for x in rows]
            known_energy = [x for x in energy_values if x is not None]
            known_savings = [x for x in savings_values if x is not None]
            confidence_values = [self._number(x.get("confidence_percent")) for x in rows]
            known_conf = [x for x in confidence_values if x is not None]
            return {
                "scope": "role",
                "role": label,
                "device_type": dtype,
                "device_count": len(rows),
                "device_names": [x.get("device_name") for x in rows if x.get("device_name")],
                "solar_covered_energy_kwh": round(sum(known_energy), 3) if known_energy else None,
                "potential_saving": round(sum(known_savings), 3) if len(known_savings) == len(rows) and known_savings else None,
                "currency": currency if known_savings else None,
                "confidence_percent": round(sum(known_conf) / len(known_conf), 0) if known_conf else None,
                "best_window": rows[0].get("suggested_start"),
                "quantification_supported": True,
                "source": "Intelligent Scheduler role aggregation of evidence-qualified devices",
            }

        dhw_role = aggregate_role("water_heater", "DHW")
        ev_role = aggregate_role("ev_charger", "EV / Car")
        elwa_device = next(
            (x for x in load_candidates if "elwa" in str(x.get("device_name") or "").lower()),
            None,
        )

        battery_candidate = {
            "configured": battery_configured,
            "status": battery.get("status"),
            "strategy": battery.get("strategy"),
            "reason": battery.get("reason"),
            "battery_soc_percent": battery.get("battery_soc_percent"),
            "recommended_reserve_percent": battery.get("recommended_reserve_percent"),
            "projected_soc_end_percent": battery.get("projected_soc_end_percent"),
            "modeled_avoidable_import_kwh": round(max(0.0, battery_avoided_import), 3) if battery_avoided_import is not None else None,
            "potential_saving": round(max(0.0, battery_saving), 3) if battery_saving is not None else None,
            "currency": currency if battery_saving is not None else None,
            "estimated_unstored_surplus_kwh": (
                round(max(0.0, self._num(battery.get("estimated_unstored_surplus_kwh"))), 3)
                if battery.get("estimated_unstored_surplus_kwh") is not None else None
            ),
            "confidence_percent": battery_confidence if battery_configured else None,
            "horizon_hours": battery.get("horizon_hours"),
            "evidence_diagnostics": battery.get("battery_evidence_diagnostics"),
            "source": "Predictive Battery Optimization",
        }

        top_load = load_candidates[0] if load_candidates else None
        battery_strategy = str(battery.get("strategy") or "").lower()
        battery_urgent = any(
            token in battery_strategy
            for token in ("protect battery reserve", "preserve energy", "forecast reserve")
        )
        load_available = bool(top_load and self._number(top_load.get("solar_covered_energy_kwh")) is not None)
        battery_available = bool(
            battery_configured
            and str(battery.get("status") or "").lower() == "ready"
            and (
                battery_avoided_import is not None
                or battery.get("strategy")
                or battery.get("battery_soc_percent") is not None
            )
        )

        # Interval-Aligned Battery + Load Allocation Ledger (15.3.5).
        #
        # Composition only: this consumes the canonical Predictive Battery
        # timeline and evidence-qualified Scheduler windows. It does not rerun
        # Forecast, Scheduler or battery SOC simulation.
        battery_timeline = [
            row for row in list(battery.get("timeline") or [])[:48]
            if isinstance(row, dict)
        ]

        def _parse_dt(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None

        qualified_windows = []
        for load in load_candidates:
            start_dt = _parse_dt(load.get("suggested_start"))
            end_dt = _parse_dt(load.get("suggested_end"))
            planned_energy = self._number(load.get("planned_energy_kwh"))
            if not start_dt or not end_dt or planned_energy is None or planned_energy <= 0 or end_dt <= start_dt:
                continue
            duration_hours = (end_dt - start_dt).total_seconds() / 3600.0
            if duration_hours <= 0:
                continue
            qualified_windows.append({
                **load,
                "_start_dt": start_dt,
                "_end_dt": end_dt,
                "_power_kw": planned_energy / duration_hours,
            })

        allocation_rows = []
        totals = {
            "forecast_surplus_kwh": 0.0,
            "canonical_battery_charge_kwh": 0.0,
            "qualified_load_demand_kwh": 0.0,
            "battery_allocated_kwh": 0.0,
            "qualified_load_allocated_kwh": 0.0,
            "unallocated_surplus_kwh": 0.0,
            "overlap_hours": 0,
        }

        for row in battery_timeline:
            hour_start = _parse_dt(row.get("time"))
            if not hour_start:
                continue
            hour_end = hour_start + timedelta(hours=1)
            solar_w = max(0.0, self._num(row.get("solar_power_w")))
            home_w = max(0.0, self._num(row.get("home_power_w")))
            raw_surplus_kwh = max(0.0, solar_w - home_w) / 1000.0
            canonical_battery_charge_kwh = max(0.0, self._num(row.get("recommended_battery_power_w"))) / 1000.0

            active_loads = []
            qualified_load_demand_kwh = 0.0
            for load in qualified_windows:
                overlap_start = max(hour_start, load["_start_dt"])
                overlap_end = min(hour_end, load["_end_dt"])
                overlap_h = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600.0)
                if overlap_h <= 0:
                    continue
                demand_kwh = load["_power_kw"] * overlap_h
                qualified_load_demand_kwh += demand_kwh
                active_loads.append({
                    "device_id": load.get("device_id"),
                    "device_name": load.get("device_name"),
                    "device_type": load.get("device_type"),
                    "role": load.get("role"),
                    "demand_kwh": round(demand_kwh, 3),
                    "confidence_percent": load.get("confidence_percent"),
                })

            # Priority comes from the canonical battery strategy, not from a
            # new optimizer. When reserve protection is active, preserve the
            # battery timeline's advised charge first. Otherwise preserve the
            # evidence-qualified load demand first, then use remaining surplus
            # for the canonical battery advised charge.
            if battery_available and battery_urgent:
                battery_alloc = min(raw_surplus_kwh, canonical_battery_charge_kwh)
                remaining = max(0.0, raw_surplus_kwh - battery_alloc)
                load_alloc = min(remaining, qualified_load_demand_kwh)
                interval_priority = "battery_reserve_first"
            else:
                load_alloc = min(raw_surplus_kwh, qualified_load_demand_kwh)
                remaining = max(0.0, raw_surplus_kwh - load_alloc)
                battery_alloc = min(remaining, canonical_battery_charge_kwh)
                interval_priority = "qualified_load_first" if qualified_load_demand_kwh > 0 else "battery_if_advised"

            unallocated = max(0.0, raw_surplus_kwh - battery_alloc - load_alloc)
            simultaneous_supported = bool(
                battery_alloc > 0.001 and load_alloc > 0.001
                and battery_alloc + load_alloc <= raw_surplus_kwh + 1e-6
            )

            totals["forecast_surplus_kwh"] += raw_surplus_kwh
            totals["canonical_battery_charge_kwh"] += canonical_battery_charge_kwh
            totals["qualified_load_demand_kwh"] += qualified_load_demand_kwh
            totals["battery_allocated_kwh"] += battery_alloc
            totals["qualified_load_allocated_kwh"] += load_alloc
            totals["unallocated_surplus_kwh"] += unallocated
            if active_loads and canonical_battery_charge_kwh > 0:
                totals["overlap_hours"] += 1

            allocation_rows.append({
                "time": row.get("time"),
                "projected_soc_start_percent": row.get("start_soc_percent"),
                "projected_soc_end_percent": row.get("projected_soc_percent"),
                "forecast_surplus_kwh": round(raw_surplus_kwh, 3),
                "canonical_battery_charge_request_kwh": round(canonical_battery_charge_kwh, 3),
                "qualified_load_demand_kwh": round(qualified_load_demand_kwh, 3),
                "battery_allocated_kwh": round(battery_alloc, 3),
                "qualified_load_allocated_kwh": round(load_alloc, 3),
                "unallocated_surplus_kwh": round(unallocated, 3),
                "active_qualified_loads": active_loads,
                "priority": interval_priority,
                "simultaneous_battery_and_load_supported": simultaneous_supported,
                "battery_action": row.get("action"),
            })

        useful_rows = [
            row for row in allocation_rows
            if self._num(row.get("forecast_surplus_kwh")) > 0.001
            or self._num(row.get("qualified_load_demand_kwh")) > 0.001
            or self._num(row.get("canonical_battery_charge_request_kwh")) > 0.001
        ]
        first_simultaneous = next(
            (row for row in useful_rows if row.get("simultaneous_battery_and_load_supported")),
            None,
        )
        first_load_without_battery_delay = next(
            (
                row for row in useful_rows
                if self._num(row.get("qualified_load_allocated_kwh")) > 0.001
                and self._num(row.get("qualified_load_allocated_kwh")) + 1e-6
                    >= self._num(row.get("qualified_load_demand_kwh"))
            ),
            None,
        )

        allocation_ledger = {
            "status": "Ready" if battery_timeline and qualified_windows else "Collecting",
            "version": "15.3.5",
            "mode": "interval_aligned_composition",
            "horizon_hours": len(battery_timeline),
            "priority_policy": (
                "battery_reserve_first"
                if battery_available and battery_urgent
                else "qualified_load_then_canonical_battery_advice"
            ),
            "totals": {k: round(v, 3) if isinstance(v, float) else v for k, v in totals.items()},
            "intervals": useful_rows[:48],
            "first_simultaneous_interval": first_simultaneous,
            "first_load_without_battery_delay_interval": first_load_without_battery_delay,
            "evidence": {
                "forecast_and_battery_source": "Predictive Battery canonical timeline built from Forecast planning_hourly",
                "load_source": "Intelligent Scheduler quantification_supported=true windows only",
                "battery_priority_source": "Predictive Battery canonical strategy",
                "interval_minutes": 60,
            },
            "limitations": [
                "Allocation is advisory composition of existing canonical outputs; no control is performed.",
                "Battery allocation never exceeds the canonical Predictive Battery advised charge for that interval.",
                "Load allocation never exceeds evidence-qualified Scheduler demand overlapping that interval.",
                "Assumption-limited flexible loads are excluded from quantified allocation.",
                "The ledger does not create a second SOC simulation or reschedule a load.",
            ],
            "safety": "Recommendation only. Zeus does not operate battery or loads.",
        }

        coordination_priority = "insufficient_evidence"
        coordination_reason = "Zeus does not have enough canonical battery and evidence-qualified load evidence to compare priorities."
        if battery_available and battery_urgent:
            coordination_priority = "battery_reserve_first"
            coordination_reason = (
                "Predictive Battery Optimization currently indicates reserve protection is important. "
                "Zeus therefore keeps battery reserve needs ahead of discretionary flexible-load use."
            )
        elif load_available and battery_available:
            coordination_priority = "qualified_load_and_battery_coordinate"
            coordination_reason = (
                "Both evidence-qualified flexible-load and configured battery opportunities exist. "
                "Zeus can compare their canonical evidence, but it does not add their kWh/CHF values because "
                "Scheduler and Predictive Battery horizons can overlap."
            )
        elif load_available:
            coordination_priority = "qualified_load_first"
            coordination_reason = (
                "An evidence-qualified flexible-load opportunity exists, while no fully supported configured-battery "
                "opportunity is available for direct coordination."
            )
        elif battery_available:
            coordination_priority = "battery_first"
            coordination_reason = (
                "A configured Predictive Battery opportunity exists, while no evidence-qualified flexible-load "
                "opportunity is currently available."
            )

        battery_load_coordination = {
            "status": "Ready" if load_available or battery_available else "Collecting",
            "version": "15.3.5",
            "mode": "recommendation_only_interval_composition",
            "priority": coordination_priority,
            "reason": coordination_reason,
            "forecast_surplus_pool_kwh": round(max(0.0, forecast_export), 3) if forecast_export is not None else None,
            "forecast_confidence_percent": round(self._clamp(forecast_confidence), 0) if forecast_confidence is not None else None,
            "top_evidence_qualified_load": top_load,
            "qualified_load_candidates": load_candidates[:8],
            "role_candidates": {
                "DHW": dhw_role,
                "EV / Car": ev_role,
            },
            "named_device_candidates": {
                "ELWA": elwa_device,
            },
            "battery_candidate": battery_candidate,
            "allocation_ledger": allocation_ledger,
            "battery_evidence_diagnostics": battery.get("battery_evidence_diagnostics"),
            "battery_configuration_status": {
                "configured": battery_configured,
                "predictive_battery_status": battery.get("status"),
                "reason_unconfigured": (
                    "; ".join((battery.get("battery_evidence_diagnostics") or {}).get("configuration_blockers") or [])
                    if not battery_configured else None
                ),
            },
            "comparison_policy": {
                "do_not_sum_overlapping_kwh": True,
                "do_not_sum_overlapping_savings": True,
                "scheduler_horizon": "canonical advised load windows",
                "battery_horizon_hours": battery.get("horizon_hours"),
                "allocation_status": allocation_ledger.get("status"),
                "note": "Coordination now composes the canonical Predictive Battery timeline with evidence-qualified Scheduler windows; it does not create a second Scheduler, Forecast, Finance or battery model.",
            },
            "assumptions_and_limitations": [
                "Flexible-load quantities come only from Scheduler rows marked quantification_supported=true.",
                "Battery quantities come only from a configured Predictive Battery model.",
                "A battery-modeled avoided-import value and a load-shifting value may overlap in time; Zeus reports them separately.",
                "Interval allocation is reported only where canonical battery advice and evidence-qualified Scheduler windows align to the same forecast hour.",
            ],
            "safety": "Recommendation only. Zeus does not control, switch, start, stop or schedule battery or loads.",
        }

        # Canonical Daily Energy Orchestrator (15.4.0).
        #
        # This is a plan composition layer over the already-computed canonical
        # Forecast/Predictive Battery/Scheduler/Finance/allocation outputs. It
        # does not resimulate SOC, move Scheduler windows, or create new energy
        # quantities.
        local_now = datetime.now().astimezone()
        today_date = local_now.date()
        tomorrow_date = today_date + timedelta(days=1)

        def _local_date(value):
            parsed = _parse_dt(value)
            if parsed is None:
                return None
            if parsed.tzinfo is None:
                return parsed.date()
            return parsed.astimezone(local_now.tzinfo).date()

        def _compact_plan_load(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "device_id": row.get("device_id"),
                "device_name": row.get("device_name"),
                "device_type": row.get("device_type"),
                "role": row.get("role"),
                "suggested_start": row.get("suggested_start"),
                "suggested_end": row.get("suggested_end"),
                "expected_energy_kwh": (
                    round(max(0.0, self._num(row.get("expected_energy_kwh"))), 3)
                    if row.get("expected_energy_kwh") is not None else None
                ),
                "solar_coverage_percent": self._number(row.get("solar_coverage_percent")),
                "confidence_percent": self._number(row.get("confidence_percent")),
                "quantification_supported": row.get("quantification_supported", True) is True,
                "estimated_saving": self._number(row.get("estimated_saving")),
                "currency": row.get("currency") or currency,
            }

        def _build_day_plan(target_date, label: str) -> dict[str, Any]:
            day_intervals = [
                row for row in allocation_rows
                if _local_date(row.get("time")) == target_date
            ]
            active_intervals = [
                row for row in day_intervals
                if self._num(row.get("forecast_surplus_kwh")) > 0.001
                or self._num(row.get("canonical_battery_charge_request_kwh")) > 0.001
                or self._num(row.get("qualified_load_demand_kwh")) > 0.001
            ]
            day_schedule = [
                _compact_plan_load(row) for row in schedule
                if _local_date(row.get("suggested_start")) == target_date
            ]
            qualified_day_loads = [row for row in day_schedule if row.get("quantification_supported")]
            advisory_day_loads = [row for row in day_schedule if not row.get("quantification_supported")]

            interval_totals = {
                "forecast_surplus_kwh": round(sum(self._num(r.get("forecast_surplus_kwh")) for r in day_intervals), 3),
                "battery_allocated_kwh": round(sum(self._num(r.get("battery_allocated_kwh")) for r in day_intervals), 3),
                "qualified_load_allocated_kwh": round(sum(self._num(r.get("qualified_load_allocated_kwh")) for r in day_intervals), 3),
                "qualified_load_demand_kwh": round(sum(self._num(r.get("qualified_load_demand_kwh")) for r in day_intervals), 3),
                "unallocated_surplus_kwh": round(sum(self._num(r.get("unallocated_surplus_kwh")) for r in day_intervals), 3),
            }

            start_soc = next(
                (self._number(r.get("projected_soc_start_percent")) for r in day_intervals
                 if self._number(r.get("projected_soc_start_percent")) is not None),
                None,
            )
            end_soc = next(
                (self._number(r.get("projected_soc_end_percent")) for r in reversed(day_intervals)
                 if self._number(r.get("projected_soc_end_percent")) is not None),
                None,
            )

            milestones = []
            for row in active_intervals:
                active_names = [
                    str(item.get("device_name") or item.get("role") or item.get("device_id"))
                    for item in (row.get("active_qualified_loads") or [])
                    if isinstance(item, dict)
                ]
                battery_kwh = self._num(row.get("battery_allocated_kwh"))
                load_kwh = self._num(row.get("qualified_load_allocated_kwh"))
                if battery_kwh <= 0.001 and load_kwh <= 0.001:
                    continue
                if battery_kwh > 0.001 and load_kwh > 0.001:
                    action = "battery_and_load"
                elif battery_kwh > 0.001:
                    action = "battery"
                else:
                    action = "qualified_load"
                milestones.append({
                    "time": row.get("time"),
                    "action": action,
                    "battery_allocated_kwh": round(battery_kwh, 3),
                    "qualified_load_allocated_kwh": round(load_kwh, 3),
                    "forecast_surplus_kwh": round(self._num(row.get("forecast_surplus_kwh")), 3),
                    "projected_soc_start_percent": self._number(row.get("projected_soc_start_percent")),
                    "projected_soc_end_percent": self._number(row.get("projected_soc_end_percent")),
                    "qualified_loads": active_names[:4],
                    "priority": row.get("priority"),
                })

            # Confidence is inherited from canonical inputs. The orchestrator
            # reports the weakest major confidence rather than pretending the
            # composed plan is more certain than its source engines.
            source_confidences = [
                x for x in (
                    self._number(forecast_confidence),
                    battery_confidence if battery_configured else None,
                    min(
                        (self._number(x.get("confidence_percent")) for x in qualified_day_loads
                         if self._number(x.get("confidence_percent")) is not None),
                        default=None,
                    ),
                ) if x is not None
            ]
            plan_confidence = min(source_confidences) if source_confidences else None

            plan_status = "Ready" if day_intervals else "Collecting"
            headline = "No quantified interval plan is available yet."
            if day_intervals:
                if battery_available and battery_urgent and interval_totals["battery_allocated_kwh"] > 0:
                    headline = "Protect battery reserve first, then use remaining interval surplus for evidence-qualified loads."
                elif interval_totals["qualified_load_allocated_kwh"] > 0 and interval_totals["battery_allocated_kwh"] > 0:
                    headline = "Coordinate battery charging and evidence-qualified flexible loads across the available solar intervals."
                elif interval_totals["qualified_load_allocated_kwh"] > 0:
                    headline = "Use the evidence-qualified flexible-load windows supported by the canonical Scheduler."
                elif interval_totals["battery_allocated_kwh"] > 0:
                    headline = "Use supported solar intervals for the canonical battery charging strategy."
                else:
                    headline = "No quantified battery/load allocation is currently supported for this day."

            return {
                "label": label,
                "date": target_date.isoformat(),
                "status": plan_status,
                "headline": headline,
                "priority_policy": allocation_ledger.get("priority_policy"),
                "battery_strategy": battery_candidate.get("strategy"),
                "battery_soc_start_percent": round(start_soc, 1) if start_soc is not None else None,
                "battery_soc_end_percent": round(end_soc, 1) if end_soc is not None else None,
                "totals": interval_totals,
                "milestones": milestones[:12],
                "qualified_scheduler_loads": qualified_day_loads[:8],
                "advisory_assumption_limited_loads": advisory_day_loads[:8],
                "confidence_percent": round(self._clamp(plan_confidence), 0) if plan_confidence is not None else None,
                "finance": {
                    "configured": finance_configured,
                    "currency": currency if finance_configured else None,
                    "scheduler_supported_saving": round(
                        sum(max(0.0, self._num(x.get("estimated_saving"))) for x in qualified_day_loads
                            if x.get("estimated_saving") is not None), 3
                    ) if finance_configured else None,
                    "note": "Savings components are not added to battery modeled savings because horizons/benefits can overlap.",
                },
            }

        today_plan = _build_day_plan(today_date, "today")
        tomorrow_plan = _build_day_plan(tomorrow_date, "tomorrow")
        daily_energy_orchestrator = {
            "status": "Ready" if today_plan.get("status") == "Ready" or tomorrow_plan.get("status") == "Ready" else "Collecting",
            "version": "15.4.0",
            "mode": "canonical_daily_plan_composition",
            "generated_at": local_now.isoformat(),
            "timezone": str(local_now.tzinfo),
            "today": today_plan,
            "tomorrow": tomorrow_plan,
            "source_engines": [
                "Forecast",
                "Predictive Battery Optimization",
                "Intelligent Scheduler",
                "Battery + Load Allocation",
                "Finance",
            ],
            "composition_policy": {
                "no_duplicate_forecast": True,
                "no_duplicate_soc_simulation": True,
                "no_rescheduling": True,
                "assumption_limited_loads_are_advisory_only": True,
                "no_overlapping_savings_sum": True,
            },
            "safety": "Recommendation only. Zeus does not control, start, stop or schedule battery or loads.",
        }

        # Adaptive Daily Plan Tracking (15.4.2).
        #
        # Compare live canonical evidence against the already-composed daily
        # plan. This is deliberately a tracking layer, not a replanner: no
        # forecast, SOC trajectory, Scheduler window, or allocation is changed.
        def _current_plan_interval(rows):
            if not rows:
                return None
            current = None
            for row in rows:
                stamp = _parse_dt(row.get("time"))
                if stamp is None:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=local_now.tzinfo)
                else:
                    stamp = stamp.astimezone(local_now.tzinfo)
                if stamp <= local_now:
                    current = row
                elif current is None:
                    current = row
                    break
                else:
                    break
            return current

        today_rows = [
            row for row in allocation_rows
            if _local_date(row.get("time")) == today_date
        ]
        current_plan_row = _current_plan_interval(today_rows)
        planned_soc_now = (
            self._number(current_plan_row.get("projected_soc_start_percent"))
            if isinstance(current_plan_row, dict) else None
        )
        if isinstance(current_plan_row, dict):
            row_time = _parse_dt(current_plan_row.get("time"))
            if row_time is not None:
                if row_time.tzinfo is None:
                    row_time = row_time.replace(tzinfo=local_now.tzinfo)
                else:
                    row_time = row_time.astimezone(local_now.tzinfo)
                # Once meaningfully inside the interval, compare against its
                # projected end SOC rather than its start SOC.
                if local_now >= row_time + timedelta(minutes=30):
                    planned_soc_now = self._number(current_plan_row.get("projected_soc_end_percent"))

        actual_soc_now = soc
        soc_delta = (
            actual_soc_now - planned_soc_now
            if actual_soc_now is not None and planned_soc_now is not None else None
        )

        # Live power evidence describes current direction only. It is never
        # integrated into invented kWh actuals.
        live_surplus_w = None
        if export_w is not None and import_w is not None:
            live_surplus_w = max(0.0, export_w - import_w)
        elif export_w is not None:
            live_surplus_w = max(0.0, export_w)

        tracker_status = "evidence_limited"
        tracker_reason = "Live SOC or the aligned canonical SOC target is unavailable."
        if soc_delta is not None:
            if soc_delta >= 3.0:
                tracker_status = "ahead"
                tracker_reason = "Battery SOC is materially above the aligned canonical trajectory."
            elif soc_delta <= -3.0:
                tracker_status = "behind"
                tracker_reason = "Battery SOC is materially below the aligned canonical trajectory."
            else:
                tracker_status = "on_track"
                tracker_reason = "Battery SOC is within ±3 percentage points of the aligned canonical trajectory."

        current_action = None
        if isinstance(current_plan_row, dict):
            battery_now = self._num(current_plan_row.get("battery_allocated_kwh"))
            load_now = self._num(current_plan_row.get("qualified_load_allocated_kwh"))
            if battery_now > 0.001 and load_now > 0.001:
                current_action = "battery_and_load"
            elif battery_now > 0.001:
                current_action = "battery"
            elif load_now > 0.001:
                current_action = "qualified_load"
            else:
                current_action = "observe"

        # Evidence-based deviation diagnosis.
        #
        # We compare live power only with the canonical current-hour Forecast
        # inputs already present in Predictive Battery. These are instantaneous
        # directional clues, not energy-accounting replacements. A contributor
        # is only promoted when the deviation is both material and directionally
        # consistent with the SOC tracking result.
        current_battery_timeline_row = None
        if isinstance(current_plan_row, dict):
            current_key = str(current_plan_row.get("time") or "")
            current_battery_timeline_row = next(
                (row for row in battery_timeline if str(row.get("time") or "") == current_key),
                None,
            )

        planned_solar_w = (
            self._number(current_battery_timeline_row.get("solar_power_w"))
            if isinstance(current_battery_timeline_row, dict) else None
        )
        planned_home_w = (
            self._number(current_battery_timeline_row.get("home_power_w"))
            if isinstance(current_battery_timeline_row, dict) else None
        )
        planned_battery_request_w = (
            self._number(current_battery_timeline_row.get("recommended_battery_power_w"))
            if isinstance(current_battery_timeline_row, dict) else None
        )

        solar_delta_w = solar_w - planned_solar_w if solar_w is not None and planned_solar_w is not None else None
        home_delta_w = home_w - planned_home_w if home_w is not None and planned_home_w is not None else None

        deviation_contributors = []

        def _add_contributor(code, label, direction, actual, planned, delta, confidence, explanation):
            deviation_contributors.append({
                "code": code,
                "label": label,
                "direction": direction,
                "actual_w": round(actual, 0) if actual is not None else None,
                "planned_w": round(planned, 0) if planned is not None else None,
                "delta_w": round(delta, 0) if delta is not None else None,
                "confidence": confidence,
                "explanation": explanation,
            })

        if tracker_status == "behind":
            if (
                solar_delta_w is not None and planned_solar_w is not None
                and planned_solar_w >= 500
                and solar_delta_w <= -max(500.0, planned_solar_w * 0.20)
            ):
                _add_contributor(
                    "solar_below_plan", "Solar below current-hour forecast", "negative",
                    solar_w, planned_solar_w, solar_delta_w, "supported",
                    "Live solar is materially below the canonical current-hour Forecast input, which is consistent with slower battery recovery.",
                )
            if (
                home_delta_w is not None and planned_home_w is not None
                and home_delta_w >= max(500.0, max(1.0, planned_home_w) * 0.20)
            ):
                _add_contributor(
                    "home_load_above_plan", "Home demand above current-hour forecast", "negative",
                    home_w, planned_home_w, home_delta_w, "supported",
                    "Live home demand is materially above the canonical current-hour planning input, leaving less power available for battery recovery.",
                )
            if import_w is not None and import_w >= 300:
                _add_contributor(
                    "grid_import_present", "Grid import is currently present", "negative",
                    import_w, None, None, "context_only",
                    "Grid import confirms the site is not currently exporting surplus, but by itself it does not prove the cause of the SOC deviation.",
                )
        elif tracker_status == "ahead":
            if (
                solar_delta_w is not None and planned_solar_w is not None
                and solar_delta_w >= max(500.0, max(1.0, planned_solar_w) * 0.20)
            ):
                _add_contributor(
                    "solar_above_plan", "Solar above current-hour forecast", "positive",
                    solar_w, planned_solar_w, solar_delta_w, "supported",
                    "Live solar is materially above the canonical current-hour Forecast input, which is consistent with faster battery recovery.",
                )
            if (
                home_delta_w is not None and planned_home_w is not None
                and home_delta_w <= -max(500.0, max(1.0, planned_home_w) * 0.20)
            ):
                _add_contributor(
                    "home_load_below_plan", "Home demand below current-hour forecast", "positive",
                    home_w, planned_home_w, home_delta_w, "supported",
                    "Live home demand is materially below the canonical current-hour planning input, leaving more power available for battery recovery.",
                )

        supported_contributors = [x for x in deviation_contributors if x.get("confidence") == "supported"]
        primary_contributor = supported_contributors[0] if supported_contributors else None

        diagnosis_status = "supported" if primary_contributor else (
            "no_material_deviation" if tracker_status == "on_track" else "cause_not_proven"
        )
        diagnosis_summary = (
            primary_contributor.get("explanation")
            if primary_contributor else
            "The battery trajectory is currently within the on-track band; no deviation diagnosis is needed."
            if tracker_status == "on_track" else
            "Zeus can see the SOC deviation, but the current live power evidence does not prove a specific cause."
        )

        # Recovery advice is policy-level and does not issue control commands.
        if tracker_status == "behind":
            recovery_advice = (
                "Preserve the canonical battery allocation first. Avoid adding new assumption-limited flexible-load demand until SOC returns to the on-track band. "
                "Evidence-qualified loads remain acceptable only where the canonical interval ledger shows they do not reduce the supported battery allocation."
            )
        elif tracker_status == "ahead":
            recovery_advice = (
                "Keep the canonical plan. Extra SOC headroom can improve resilience, but Zeus will not expand flexible-load energy beyond evidence-qualified interval capacity."
            )
        elif tracker_status == "on_track":
            recovery_advice = "No recovery action is needed; keep the canonical plan."
        else:
            recovery_advice = "Keep the last canonical plan and wait for sufficient aligned live evidence before changing the recommendation."

        adaptive_daily_plan_tracking = {
            "status": tracker_status,
            "version": "15.4.3",
            "mode": "canonical_plan_vs_live_evidence_with_diagnosis",
            "generated_at": local_now.isoformat(),
            "plan_date": today_date.isoformat(),
            "reason": tracker_reason,
            "soc": {
                "actual_percent": round(actual_soc_now, 1) if actual_soc_now is not None else None,
                "planned_percent": round(planned_soc_now, 1) if planned_soc_now is not None else None,
                "delta_percentage_points": round(soc_delta, 1) if soc_delta is not None else None,
                "on_track_band_percentage_points": 3.0,
            },
            "live_power": {
                "solar_w": round(solar_w, 0) if solar_w is not None else None,
                "home_w": round(home_w, 0) if home_w is not None else None,
                "grid_import_w": round(import_w, 0) if import_w is not None else None,
                "grid_export_w": round(export_w, 0) if export_w is not None else None,
                "export_surplus_w": round(live_surplus_w, 0) if live_surplus_w is not None else None,
                "battery_charge_w": round(battery_charge_w, 0) if battery_charge_w is not None else None,
                "battery_discharge_w": round(battery_discharge_w, 0) if battery_discharge_w is not None else None,
                "battery_signed_w": round(battery_signed_w, 0) if battery_signed_w is not None else None,
            },
            "aligned_plan_power": {
                "solar_w": round(planned_solar_w, 0) if planned_solar_w is not None else None,
                "home_w": round(planned_home_w, 0) if planned_home_w is not None else None,
                "battery_request_w": round(planned_battery_request_w, 0) if planned_battery_request_w is not None else None,
                "solar_delta_w": round(solar_delta_w, 0) if solar_delta_w is not None else None,
                "home_delta_w": round(home_delta_w, 0) if home_delta_w is not None else None,
            },
            "deviation_diagnosis": {
                "status": diagnosis_status,
                "summary": diagnosis_summary,
                "primary_contributor": primary_contributor,
                "contributors": deviation_contributors[:4],
                "causality_policy": "Only material directionally-consistent live-vs-current-hour Forecast differences are promoted as supported contributors. Grid flow alone is contextual, not causal proof.",
            },
            "recovery_advice": recovery_advice,
            "current_plan_interval": {
                "time": current_plan_row.get("time") if isinstance(current_plan_row, dict) else None,
                "action": current_action,
                "battery_allocated_kwh": round(self._num(current_plan_row.get("battery_allocated_kwh")), 3) if isinstance(current_plan_row, dict) else None,
                "qualified_load_allocated_kwh": round(self._num(current_plan_row.get("qualified_load_allocated_kwh")), 3) if isinstance(current_plan_row, dict) else None,
                "forecast_surplus_kwh": round(self._num(current_plan_row.get("forecast_surplus_kwh")), 3) if isinstance(current_plan_row, dict) else None,
                "qualified_loads": [
                    str(item.get("device_name") or item.get("role") or item.get("device_id"))
                    for item in (current_plan_row.get("active_qualified_loads") or [])
                    if isinstance(item, dict)
                ][:4] if isinstance(current_plan_row, dict) else [],
            },
            "recommendation_policy": {
                "ahead": "Keep the canonical plan; extra headroom may support later qualified loads, but Zeus does not promote it without interval evidence.",
                "on_track": "Keep the canonical plan.",
                "behind": "Protect the battery allocation first and avoid adding assumption-limited load demand until the trajectory recovers.",
                "evidence_limited": "Keep the last canonical plan and wait for sufficient live evidence before changing the recommendation.",
            }.get(tracker_status),
            "tracking_policy": {
                "plan_is_not_recomputed": True,
                "live_power_is_not_integrated_into_unproven_energy": True,
                "soc_is_primary_progress_evidence": True,
                "on_track_band_percentage_points": 3.0,
            },
            "safety": "Tracking and recommendation only. Zeus does not control the battery or loads.",
        }
        daily_energy_orchestrator["adaptive_tracking"] = adaptive_daily_plan_tracking

        # Plan Completion & End-of-Day Learning (15.4.4).
        #
        # This layer evaluates only evidence that Zeus can measure without
        # inventing per-device completion. During the day it reports progress;
        # after the day closes it can evaluate the latest completed site's
        # measured energy row. It never changes qualification thresholds,
        # Scheduler profiles, Forecast values, or canonical battery parameters.
        energy_snapshot_service = getattr(self.core, "energy_snapshot", None)
        measured_today = (
            energy_snapshot_service.summary()
            if energy_snapshot_service is not None and callable(getattr(energy_snapshot_service, "summary", None))
            else {}
        )
        measured_today = measured_today if isinstance(measured_today, dict) else {}

        history_rows = [
            dict(row) for row in self._daily_rows()
            if isinstance(row, dict) and row.get("date")
        ]
        history_rows.sort(key=lambda row: str(row.get("date") or ""))
        latest_completed = next(
            (row for row in reversed(history_rows)
             if str(row.get("date") or "")[:10] < today_date.isoformat()),
            None,
        )

        def _measured_kwh(row, *keys):
            if not isinstance(row, dict):
                return None
            for key in keys:
                value = self._number(row.get(key))
                if value is not None:
                    return max(0.0, value)
            return None

        today_measured = {
            "date": measured_today.get("date") or today_date.isoformat(),
            "day_state": measured_today.get("day_state") or "in_progress",
            "authoritative": bool(measured_today.get("authoritative")),
            "solar_kwh": _measured_kwh(measured_today, "solar_energy_kwh", "solar_kwh"),
            "home_kwh": _measured_kwh(measured_today, "house_energy_kwh", "consumption_energy_kwh", "consumption_kwh"),
            "grid_import_kwh": _measured_kwh(measured_today, "grid_import_energy_kwh", "grid_import_kwh"),
            "grid_export_kwh": _measured_kwh(measured_today, "grid_export_energy_kwh", "grid_export_kwh"),
            "battery_charge_kwh": _measured_kwh(measured_today, "battery_charge_energy_kwh", "battery_charge_kwh"),
            "battery_discharge_kwh": _measured_kwh(measured_today, "battery_discharge_energy_kwh", "battery_discharge_kwh"),
            "sources": measured_today.get("sources") if isinstance(measured_today.get("sources"), dict) else {},
        }

        latest_completed_measured = None
        if latest_completed is not None:
            latest_completed_measured = {
                "date": str(latest_completed.get("date") or "")[:10],
                "solar_kwh": _measured_kwh(latest_completed, "solar_energy_kwh", "solar_kwh"),
                "home_kwh": _measured_kwh(latest_completed, "house_energy_kwh", "consumption_energy_kwh", "consumption_kwh"),
                "grid_import_kwh": _measured_kwh(latest_completed, "grid_import_energy_kwh", "grid_import_kwh"),
                "grid_export_kwh": _measured_kwh(latest_completed, "grid_export_energy_kwh", "grid_export_kwh"),
                "battery_charge_kwh": _measured_kwh(latest_completed, "battery_charge_energy_kwh", "battery_charge_kwh"),
                "battery_discharge_kwh": _measured_kwh(latest_completed, "battery_discharge_energy_kwh", "battery_discharge_kwh"),
            }

        # Progress is intentionally descriptive. Daily mapped meters can say how
        # much the site produced/imported/exported/charged, but they cannot prove
        # that an individual Scheduler opportunity actually ran in its advised
        # window. That remains unmeasured unless device-level completion evidence
        # exists elsewhere.
        plan_totals = today_plan.get("totals") if isinstance(today_plan.get("totals"), dict) else {}
        plan_completion_status = "tracking" if today_measured.get("authoritative") else "evidence_limited"
        completion_summary = (
            "Today's canonical plan is still in progress. Zeus is tracking authoritative measured site energy and the live SOC trajectory."
            if plan_completion_status == "tracking"
            else "Today's canonical plan is in progress, but authoritative measured daily-energy evidence is incomplete."
        )

        if tracker_status == "ahead":
            completion_assessment = "ahead"
        elif tracker_status == "behind":
            completion_assessment = "behind"
        elif tracker_status == "on_track":
            completion_assessment = "on_track"
        else:
            completion_assessment = "evidence_limited"

        learning_observations = []
        if today_measured.get("authoritative"):
            if today_measured.get("solar_kwh") is not None:
                learning_observations.append({
                    "kind": "measured_site_energy",
                    "metric": "solar",
                    "value_kwh": round(today_measured["solar_kwh"], 3),
                    "use": "evaluation_only",
                })
            if today_measured.get("grid_import_kwh") is not None:
                learning_observations.append({
                    "kind": "measured_site_energy",
                    "metric": "grid_import",
                    "value_kwh": round(today_measured["grid_import_kwh"], 3),
                    "use": "evaluation_only",
                })
            if today_measured.get("grid_export_kwh") is not None:
                learning_observations.append({
                    "kind": "measured_site_energy",
                    "metric": "grid_export",
                    "value_kwh": round(today_measured["grid_export_kwh"], 3),
                    "use": "evaluation_only",
                })

        plan_completion_learning = {
            "status": plan_completion_status,
            "version": "15.4.4",
            "mode": "measured_plan_evaluation_no_self_modification",
            "generated_at": local_now.isoformat(),
            "today": {
                "date": today_date.isoformat(),
                "plan_status": today_plan.get("status"),
                "completion_status": plan_completion_status,
                "assessment": completion_assessment,
                "summary": completion_summary,
                "planned": {
                    "forecast_surplus_kwh": self._number(plan_totals.get("forecast_surplus_kwh")),
                    "battery_allocated_kwh": self._number(plan_totals.get("battery_allocated_kwh")),
                    "qualified_load_allocated_kwh": self._number(plan_totals.get("qualified_load_allocated_kwh")),
                    "unallocated_surplus_kwh": self._number(plan_totals.get("unallocated_surplus_kwh")),
                    "battery_soc_end_percent": self._number(today_plan.get("battery_soc_end_percent")),
                },
                "measured_so_far": today_measured,
                "live_soc_tracking": {
                    "actual_percent": round(actual_soc_now, 1) if actual_soc_now is not None else None,
                    "planned_percent": round(planned_soc_now, 1) if planned_soc_now is not None else None,
                    "delta_percentage_points": round(soc_delta, 1) if soc_delta is not None else None,
                },
                "qualified_load_completion": {
                    "status": "not_proven_by_site_meters",
                    "planned_qualified_load_count": len(today_plan.get("qualified_scheduler_loads") or []),
                    "note": "Site daily-energy meters cannot prove that a specific flexible load completed its advised window. Zeus does not mark individual opportunities achieved or missed from aggregate site energy.",
                },
            },
            "latest_completed_measured_day": latest_completed_measured,
            "learning_observations": learning_observations[:6],
            "learning_policy": {
                "evaluation_only": True,
                "does_not_change_scheduler_qualification_threshold": True,
                "does_not_change_registered_device_profiles": True,
                "does_not_change_battery_parameters": True,
                "does_not_change_forecast_values": True,
                "does_not_learn_from_assumption_limited_loads": True,
                "individual_load_success_requires_device_level_completion_evidence": True,
                "completed_history_is_measured_not_reconstructed": True,
            },
            "next_day_use": "Measured outcomes may inform existing evidence/learning engines only through their canonical inputs; this completion layer does not self-modify planning parameters.",
            "safety": "Evaluation and learning evidence only. Zeus does not control equipment or autonomously rewrite canonical planning rules.",
        }
        daily_energy_orchestrator["plan_completion_learning"] = plan_completion_learning

        # Rank without altering quantities. CHF is not privileged when Finance is
        # unconfigured; energy and confidence keep opportunities useful.
        def rank_key(row: dict[str, Any]) -> tuple[float, float, float]:
            saving = self._number(row.get("expected_savings")) or 0.0
            energy = self._number(row.get("expected_energy_benefit_kwh")) or 0.0
            confidence = self._number(row.get("confidence_percent")) or 0.0
            return (saving, energy, confidence)

        opportunities.sort(key=rank_key, reverse=True)
        opportunities = opportunities[:6]

        rows = self._daily_rows()
        total_import = sum(self._num(r.get("grid_import_kwh", r.get("grid_import_energy_kwh"))) for r in rows)
        total_export = sum(self._num(r.get("grid_export_kwh", r.get("grid_export_energy_kwh"))) for r in rows)
        total_solar = sum(self._num(r.get("solar_energy_kwh")) for r in rows)
        total_home = sum(self._num(r.get("house_energy_kwh", r.get("consumption_energy_kwh"))) for r in rows)
        self_consumed = max(0.0, min(total_solar, total_home, total_solar - total_export if total_solar else 0.0))
        solar_utilization = 100.0 * self_consumed / total_solar if total_solar > 0 else 0.0
        grid_avoidance = 100.0 * max(0.0, total_home - total_import) / total_home if total_home > 0 else 0.0
        plan_results = planning.get("results") if isinstance(planning.get("results"), list) else []
        valid_accuracy = [self._num(r.get("solar_accuracy_percent")) for r in plan_results if isinstance(r, dict) and r.get("solar_accuracy_percent") is not None]
        planning_quality = (sum(valid_accuracy) / len(valid_accuracy)) if valid_accuracy else None
        history_maturity = min(100.0, len(rows) * 8.0)
        score_parts = [solar_utilization * 0.35, grid_avoidance * 0.30, history_maturity * 0.15]
        score_weight = 0.80
        if planning_quality is not None:
            score_parts.append(planning_quality * 0.20)
            score_weight = 1.0
        score = self._clamp(sum(score_parts) / score_weight) if rows else None

        tariff_spread = None
        if import_tariff is not None and export_tariff is not None:
            tariff_spread = max(0.0, import_tariff - export_tariff)

        quantification_limitations: list[str] = []
        if not forecast_ready:
            quantification_limitations.append("Forecast is not ready, so future surplus/import opportunity cannot be quantified reliably.")
        if not quantified_schedule:
            if schedule:
                quantification_limitations.append("Flexible-load planning recommendations exist, but their device profiles are assumption-limited; Zeus withholds their kWh/CHF from quantified opportunity until sufficient registered profile evidence exists.")
            else:
                quantification_limitations.append("No canonical flexible-load schedule is available; Zeus will not invent shiftable load energy.")
        if not finance_configured:
            quantification_limitations.append("Finance tariffs are not configured; Zeus will not invent CHF savings.")
        if battery.get("status") == "Ready" and not battery_configured:
            quantification_limitations.append("Battery modeling uses unregistered/default battery metadata; battery kWh/CHF opportunity is withheld from quantified totals.")

        # There is intentionally no historical 'lost opportunity' percentage in
        # 15.0.0. Interval-aligned historical availability/target evidence is needed
        # before Zeus can quantify counterfactual missed opportunities.
        lost: list[dict[str, Any]] = []

        opportunity_quantification = {
            "status": "Ready" if forecast_ready or bool(schedule) or battery_avoided_import is not None else "Collecting",
            "version": self.VERSION,
            "mode": "evidence_first_composition",
            "measured": {
                "live_solar_power_w": round(solar_w, 1) if solar_w is not None else None,
                "live_home_power_w": round(home_w, 1) if home_w is not None else None,
                "live_grid_import_power_w": round(import_w, 1) if import_w is not None else None,
                "live_grid_export_power_w": round(export_w, 1) if export_w is not None else None,
                "battery_soc_percent": round(soc, 1) if soc is not None else None,
                "note": "Live power is instantaneous measured context and is not converted to kWh without a supported duration.",
            },
            "forecast": {
                "expected_solar_next_24h_kwh": round(max(0.0, forecast_solar), 3) if forecast_solar is not None else None,
                "expected_consumption_next_24h_kwh": round(max(0.0, forecast_home), 3) if forecast_home is not None else None,
                "expected_grid_import_next_24h_kwh": round(max(0.0, forecast_import), 3) if forecast_import is not None else None,
                "expected_grid_export_next_24h_kwh": round(max(0.0, forecast_export), 3) if forecast_export is not None else None,
                "confidence_percent": round(self._clamp(forecast_confidence), 0) if forecast_confidence is not None else None,
                "best_surplus_window": best_window or None,
                "source": "Forecast",
            },
            "estimated_opportunity": {
                "advisory_flexible_energy_planned_kwh": round(max(0.0, advisory_planned_energy), 3) if advisory_planned_energy is not None else None,
                "advisory_flexible_device_count": len(schedule),
                "assumption_limited_device_count": assumption_limited_count,
                # Compatibility field: this remains the evidence-supported
                # quantified plan, not the advisory/default-profile total.
                "flexible_energy_planned_kwh": round(max(0.0, planned_energy), 3) if planned_energy is not None else None,
                "quantified_flexible_energy_planned_kwh": round(max(0.0, planned_energy), 3) if planned_energy is not None else None,
                "solar_covered_flexible_energy_kwh": round(max(0.0, solar_covered_energy), 3) if solar_covered_energy is not None else None,
                "grid_import_potentially_avoidable_by_scheduled_loads_kwh": round(max(0.0, solar_covered_energy), 3) if solar_covered_energy is not None else None,
                "battery_modeled_avoidable_import_kwh": round(max(0.0, battery_avoided_import), 3) if battery_avoided_import is not None else None,
                "scheduler_potential_saving": round(max(0.0, scheduled_saving), 3) if scheduled_saving is not None else None,
                "battery_potential_saving": round(max(0.0, battery_saving), 3) if battery_saving is not None else None,
                "currency": currency if finance_configured else None,
                "tariff_spread_per_kwh": round(tariff_spread, 4) if tariff_spread is not None else None,
                "source": "Canonical Scheduler + Predictive Battery outputs",
            },
            "registered_load_roles": role_summary,
            "battery_load_coordination": battery_load_coordination,
            "confidence": {
                "forecast_percent": round(self._clamp(forecast_confidence), 0) if forecast_confidence is not None else None,
                "scheduler_forecast_percent": self._number(scheduler.get("forecast_confidence")),
                "battery_percent": battery_confidence if battery_configured else None,
                "planning_completed_comparisons": len(valid_accuracy),
                "opportunity_learning_status": learning_summary.get("status") if isinstance(learning_summary, dict) else None,
                "load_learning_adjustment": load_learning_adjustment,
                "battery_learning_adjustment": battery_learning_adjustment,
            },
            "assumptions_and_limitations": quantification_limitations,
            "evidence_policy": "Measured values remain measured; Forecast values remain forecast; combined opportunities are explicitly estimated. Missing evidence returns null/not-quantifiable rather than theoretical savings.",
        }

        self._summary = {
            "status": opportunity_quantification["status"],
            "version": self.VERSION,
            "foundation": "Adaptive Energy Optimization · Battery + Load Coordination",
            "recommendation_only": True,
            "optimization_score": round(score, 0) if score is not None else None,
            "score_components": {
                "solar_utilization": round(solar_utilization, 1) if rows else None,
                "grid_avoidance": round(grid_avoidance, 1) if rows else None,
                "planning_quality": round(planning_quality, 1) if planning_quality is not None else None,
                "history_maturity": round(history_maturity, 1) if rows else None,
            },
            "live_context": opportunity_quantification["measured"],
            "forecast_context": opportunity_quantification["forecast"],
            "opportunity_quantification": opportunity_quantification,
            "battery_load_coordination": battery_load_coordination,
            "daily_energy_orchestrator": daily_energy_orchestrator,
            "strategy_comparison": strategy_comparison,
            "constrained_optimization": constrained_optimization,
            "opportunities": opportunities,
            "lost_opportunities": lost,
            "lost_opportunity_status": "Not quantified in 15.0.0 without interval-aligned historical availability and target evidence.",
            "weekly_summary": {
                "days_measured": len(rows),
                "solar_kwh": round(total_solar, 2),
                "consumption_kwh": round(total_home, 2),
                "grid_import_kwh": round(total_import, 2),
                "grid_export_kwh": round(total_export, 2),
                "estimated_energy_value_chf": None,
                "top_recommendation": opportunities[0]["title"] if opportunities else "No quantified optimization opportunity yet",
                "note": "Historical totals are measured. Counterfactual lost savings are intentionally not inferred from percentages.",
            },
            "behavior_score": behavior_summary.get("overall_score") if isinstance(behavior_summary, dict) else None,
            "architecture": {
                "composition_only": True,
                "canonical_sources": ["Energy Flow", "Forecast", "Finance", "Intelligent Scheduler", "Predictive Battery Optimization", "Planning", "Registered Devices / DEA"],
                "duplicate_accounting": False,
            },
            "limitations": quantification_limitations,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        self.event_bus.publish(
            "OptimizationIntelligenceUpdated",
            "OptimizationIntelligenceEngine",
            {
                "score": self._summary["optimization_score"],
                "opportunity_count": len(opportunities),
                "quantification_status": opportunity_quantification["status"],
                "recommendation_only": True,
            },
        )
        return self._summary

    def summary(self) -> dict[str, Any]:
        return dict(self._summary)
