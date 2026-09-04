"""Finance and fixed-tariff calculations for AION EMS Zeus."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


class FinanceEngine:
    """Calculate transparent financial values from measured daily energy."""

    def __init__(self, event_bus, registry, analytics, device_analytics, data_quality) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.analytics = analytics
        self.device_analytics = device_analytics
        self.data_quality = data_quality
        self.last: dict[str, Any] = {"status": "Not configured"}

    @staticmethod
    def _num(value: Any) -> float:
        try:
            value = float(value)
            return value if value >= 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _state_energy_kwh(self, entity_id: str | None) -> float:
        """Read a mapped energy entity and normalize Wh/kWh/MWh to kWh."""
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return 0.0
        state = self.analytics.hass.states.get(entity_id)
        if state is None or str(state.state).lower() in {"unknown", "unavailable", "none", ""}:
            return 0.0
        try:
            value = max(float(state.state), 0.0)
        except (TypeError, ValueError):
            return 0.0
        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "wh":
            value /= 1000.0
        elif unit == "mwh":
            value *= 1000.0
        elif unit != "kwh":
            return 0.0
        return value

    def _canonical_battery_today(self, today: dict[str, Any], key: str, mapping_field: str) -> tuple[float, str]:
        """Resolve current-day battery energy without allowing a stale layer to zero it."""
        candidates: list[tuple[float, str]] = [(self._num(today.get(key)), "analytics_period")]
        now_key = dt_util.now().date().isoformat()
        raw_daily = ((getattr(self.analytics, "data_lake", None).data or {}).get("daily_summaries", {})
                     if getattr(self.analytics, "data_lake", None) is not None else {})
        raw_today = dict((raw_daily or {}).get(now_key) or {})
        candidates.append((self._num(raw_today.get(key)), "datalake_today"))

        # Battery Statistics reads the current Home Assistant Energy history from
        # Analytics' recorder cache. Finance must consult that exact same cache so
        # the value shown as "Discharged Today" cannot diverge from Battery Support.
        ha_days = getattr(self.analytics, "_ha_energy_days", {}) or {}
        ha_today = ((ha_days.get(key) or {}).get(now_key) if isinstance(ha_days, dict) else None)
        candidates.append((self._num(ha_today), "analytics_ha_energy_today"))

        mappings = dict((getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}) or {})
        mapped_entity = str(mappings.get(mapping_field) or "").strip()
        total_field = mapping_field.replace("_today", "_total") if mapping_field.endswith("_today") else ""
        total_entity = str(mappings.get(total_field) or "").strip() if total_field else ""
        duplicate_today_total = bool(mapped_entity and total_entity and mapped_entity == total_entity)

        # If the "today" slot points to the exact same entity as the cumulative
        # total slot, the raw state is a lifetime counter and must not compete
        # with canonical daily/Recorder values in Finance.
        if not duplicate_today_total:
            mapped = self._state_energy_kwh(mapped_entity)
            candidates.append((mapped, f"mapped:{mapped_entity}" if mapped_entity else "mapped_daily"))

        value, source = max(candidates, key=lambda item: item[0])
        return value, source


    @staticmethod
    def _minute_of_day(value: str) -> int | None:
        try:
            hh, mm = str(value).strip().split(":", 1)
            h, m = int(hh), int(mm)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return None
            return h * 60 + m
        except (TypeError, ValueError):
            return None

    def _tou_periods(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, raw in enumerate(list(cfg.get("tou_periods") or [])):
            if not isinstance(raw, dict):
                continue
            start = self._minute_of_day(raw.get("start"))
            end = self._minute_of_day(raw.get("end"))
            if start is None or end is None or start == end:
                continue
            out.append({
                "id": str(raw.get("id") or f"period_{index+1}"),
                "name": str(raw.get("name") or f"Period {index+1}")[:40],
                "start": str(raw.get("start")),
                "end": str(raw.get("end")),
                "start_minute": start,
                "end_minute": end,
                "import_tariff": self._num(raw.get("import_tariff")),
            })
        return out

    @staticmethod
    def _dynamic_slots(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for raw in list(cfg.get("dynamic_prices") or []):
            if not isinstance(raw, dict):
                continue
            try:
                start = datetime.fromisoformat(str(raw.get("start") or ""))
                end = datetime.fromisoformat(str(raw.get("end") or ""))
                rate = float(raw.get("price_per_kwh"))
            except (TypeError, ValueError):
                continue
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                continue
            out.append({"start": start, "end": end, "price_per_kwh": rate, "price": raw.get("price")})
        return sorted(out, key=lambda x: x["start"])

    @staticmethod
    def _dynamic_rate_for_datetime(when: datetime, slots: list[dict[str, Any]]) -> tuple[float | None, str]:
        probe = when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
        for item in slots:
            if item["start"] <= probe < item["end"]:
                return float(item["price_per_kwh"]), f"{dt_util.as_local(item['start']).strftime('%H:%M')}–{dt_util.as_local(item['end']).strftime('%H:%M')}"
        return None, "No price slot"

    def _dynamic_hourly_values(self, slots: list[dict[str, Any]], export_rate: float, export_depreciation: float | None = None) -> dict[str, Any]:
        rows = list(getattr(self.analytics, "_ha_consumption_hourly", []) or [])
        now = dt_util.now(); today = now.date(); week_start = today - timedelta(days=today.weekday()); month_start = today.replace(day=1); year_start = today.replace(month=1, day=1)
        scopes = {"today": today, "week": week_start, "month": month_start, "year": year_start}; values = {}
        for scope, start_date in scopes.items():
            imported = exported = cost = revenue = 0.0; matched = 0; earliest = latest = None
            for row in rows:
                try: stamp = datetime.fromisoformat(str(row.get("start") or ""))
                except ValueError: continue
                local = dt_util.as_local(stamp)
                if local.date() < start_date or local.date() > today: continue
                rate, _ = self._dynamic_rate_for_datetime(stamp, slots)
                if rate is None: continue
                imp = self._num(row.get("grid_import_energy_kwh")); exp = self._num(row.get("grid_export_energy_kwh"))
                slot_export_rate = (rate - export_depreciation) if export_depreciation is not None else export_rate
                imported += imp; exported += exp; cost += imp * rate; revenue += exp * slot_export_rate; matched += 1
                earliest = local if earliest is None or local < earliest else earliest; latest = local if latest is None or local > latest else latest
            effective = cost / imported if imported > 0 else 0.0
            values[scope] = {"grid_import_kwh": round(imported,4), "grid_export_kwh": round(exported,4), "grid_cost": round(cost,4), "export_revenue": round(revenue,4), "effective_import_tariff": round(effective,6), "hour_count": matched, "coverage_complete": bool(matched), "coverage_start": earliest.isoformat() if earliest else None, "coverage_end": latest.isoformat() if latest else None}
        return values

    @staticmethod
    def _period_matches(minute: int, start: int, end: int) -> bool:
        return start <= minute < end if start < end else (minute >= start or minute < end)

    def _tou_rate_for_datetime(self, when: datetime, periods: list[dict[str, Any]]) -> tuple[float, str]:
        local = dt_util.as_local(when)
        minute = local.hour * 60 + local.minute
        for item in periods:
            if self._period_matches(minute, item["start_minute"], item["end_minute"]):
                return float(item["import_tariff"]), str(item["name"])
        return 0.0, "Uncovered"

    def _tou_hourly_values(self, periods: list[dict[str, Any]], export_rate: float) -> dict[str, Any]:
        rows = list(getattr(self.analytics, "_ha_consumption_hourly", []) or [])
        now = dt_util.now()
        today = now.date()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)
        scopes = {"today": today, "week": week_start, "month": month_start, "year": year_start}
        values: dict[str, Any] = {}
        for scope, start_date in scopes.items():
            imported = exported = cost = revenue = 0.0
            matched = 0
            earliest = None
            latest = None
            breakdown: dict[str, dict[str, float]] = {}
            for row in rows:
                try:
                    stamp = datetime.fromisoformat(str(row.get("start") or ""))
                except ValueError:
                    continue
                local = dt_util.as_local(stamp)
                if local.date() < start_date or local.date() > today:
                    continue
                imp = self._num(row.get("grid_import_energy_kwh"))
                exp = self._num(row.get("grid_export_energy_kwh"))
                rate, name = self._tou_rate_for_datetime(local, periods)
                imported += imp
                exported += exp
                cost += imp * rate
                revenue += exp * export_rate
                matched += 1
                earliest = local if earliest is None or local < earliest else earliest
                latest = local if latest is None or local > latest else latest
                bucket = breakdown.setdefault(name, {"energy_kwh": 0.0, "cost": 0.0})
                bucket["energy_kwh"] += imp
                bucket["cost"] += imp * rate
            effective = cost / imported if imported > 0 else 0.0
            coverage_complete = bool(matched) and earliest is not None and earliest.date() <= start_date
            values[scope] = {
                "grid_import_kwh": round(imported, 4),
                "grid_export_kwh": round(exported, 4),
                "grid_cost": round(cost, 4),
                "export_revenue": round(revenue, 4),
                "effective_import_tariff": round(effective, 6),
                "hour_count": matched,
                "coverage_complete": coverage_complete,
                "coverage_start": earliest.isoformat() if earliest else None,
                "coverage_end": latest.isoformat() if latest else None,
                "breakdown": {k: {"energy_kwh": round(v["energy_kwh"], 4), "cost": round(v["cost"], 4)} for k, v in breakdown.items()},
            }
        return values

    def refresh(self) -> dict[str, Any]:
        cfg = self.registry.data.get("sources", {}).get("tariffs", {})
        currency = str(cfg.get("currency") or "CHF").upper()[:4]
        enabled = bool(cfg.get("enabled"))
        tariff_mode = str(cfg.get("tariff_mode") or "fixed").lower()
        tou_periods = self._tou_periods(cfg) if tariff_mode == "time_of_use" else []
        dynamic_slots = self._dynamic_slots(cfg) if tariff_mode == "dynamic" else []
        export_rate = self._num(cfg.get("export_tariff"))
        raw_depreciation = cfg.get("export_depreciation")
        try:
            export_depreciation = float(raw_depreciation) if raw_depreciation not in (None, "") else None
        except (TypeError, ValueError):
            export_depreciation = None
        if export_depreciation is not None and export_depreciation < 0:
            export_depreciation = None
        standing = self._num(cfg.get("standing_charge"))
        active_import_rate = self._num(cfg.get("import_tariff"))
        active_tariff_name = "Fixed"
        if tariff_mode == "time_of_use" and tou_periods:
            active_import_rate, active_tariff_name = self._tou_rate_for_datetime(dt_util.now(), tou_periods)
        elif tariff_mode == "dynamic" and dynamic_slots:
            dynamic_rate, active_tariff_name = self._dynamic_rate_for_datetime(dt_util.now(), dynamic_slots)
            active_import_rate = dynamic_rate if dynamic_rate is not None else 0.0
        import_rate = active_import_rate
        active_export_rate = (import_rate - export_depreciation) if (tariff_mode == "dynamic" and export_depreciation is not None) else export_rate
        today = self.analytics.summary().get("periods", {}).get("today", {})
        imported = self._num(today.get("grid_import_energy_kwh"))
        exported = self._num(today.get("grid_export_energy_kwh"))
        solar = self._num(today.get("solar_energy_kwh"))
        house = self._num(today.get("house_energy_kwh"))
        battery_charge, battery_charge_source = self._canonical_battery_today(
            today, "battery_charge_energy_kwh", "battery_charge_energy_today"
        )
        battery_discharge, battery_discharge_source = self._canonical_battery_today(
            today, "battery_discharge_energy_kwh", "battery_discharge_energy_today"
        )

        # Energy-value flow: direct solar and battery support are valued separately.
        # Canonical Solar Input represents measured site PV on the generation side.
        # Direct solar therefore excludes measured grid export only. Battery charging
        # is a separate flow and must not be subtracted from PV a second time.
        direct_solar = self._num(today.get("direct_solar_consumption_kwh"))
        # A canonical Solar Input can be changed during the current day. Recorder
        # grid totals still cover the whole calendar day, while the new PV
        # integration begins at the source change. If export is greater than the
        # available solar total, that period is provably incomplete. Reconstruct
        # today's local supply from the measured home/grid/battery boundary until
        # the next clean midnight rollover.
        solar_period_complete = solar + 0.05 >= exported
        # Battery Statistics owns the measured discharged-today total. Allocate
        # measured battery support first, then cap direct solar to the remaining
        # home demand. The previous order let direct solar consume all home demand
        # and could therefore force Battery Support to 0 despite a valid measured
        # discharge value.
        local_home_supply = max(0.0, house - imported)
        battery_to_home = min(battery_discharge, local_home_supply)
        remaining_home_after_battery = max(0.0, local_home_supply - battery_to_home)

        if solar_period_complete:
            solar_available_for_home = max(0.0, solar - exported)
            direct_solar = min(solar_available_for_home, remaining_home_after_battery)
        else:
            # Transition-day fallback: preserve the measured battery allocation and
            # assign only the residual local home supply to solar.
            direct_solar = remaining_home_after_battery

        tou_values = self._tou_hourly_values(tou_periods, export_rate) if tariff_mode == "time_of_use" and tou_periods else {}
        dynamic_values = self._dynamic_hourly_values(dynamic_slots, export_rate, export_depreciation) if tariff_mode == "dynamic" and dynamic_slots else {}
        period_values = dynamic_values if tariff_mode == "dynamic" else tou_values
        today_priced = dict(period_values.get("today") or {})
        effective_import_rate = float(today_priced.get("effective_import_tariff")) if today_priced and today_priced.get("effective_import_tariff") is not None else import_rate
        grid_cost = float(today_priced.get("grid_cost")) if today_priced and today_priced.get("grid_cost") is not None else imported * import_rate
        export_revenue = float(today_priced.get("export_revenue")) if today_priced and today_priced.get("export_revenue") is not None else exported * export_rate
        value_rate = effective_import_rate if tariff_mode in {"time_of_use", "dynamic"} else import_rate
        direct_solar_value = direct_solar * value_rate
        battery_support_value = battery_to_home * value_rate
        avoided_import_value = direct_solar_value + battery_support_value
        net_benefit = avoided_import_value + export_revenue - grid_cost - standing
        devices = []
        for device in self.device_analytics.summary().get("devices", []):
            energy = self._num(device.get("energy_today_kwh"))
            devices.append({
                "id": device.get("id"), "name": device.get("name"),
                "energy_today_kwh": round(energy, 4),
                "estimated_cost": round(energy * value_rate, 4) if enabled else None,
                "energy_source": device.get("method", "unknown"),
            })
        confidence = self.data_quality.summary().get("confidence_score")
        self.last = {
            "status": "Ready" if enabled else "Not configured",
            "configured": enabled, "currency": currency, "tariff_mode": tariff_mode,
            "vat_included": bool(cfg.get("vat_included", True)),
            "import_tariff": import_rate if enabled else None,
            "active_import_tariff": import_rate if enabled else None,
            "active_tariff_name": active_tariff_name if enabled else None,
            "effective_import_tariff_today": effective_import_rate if enabled else None,
            "tou_periods": [{k: v for k, v in item.items() if k not in {"start_minute", "end_minute"}} for item in tou_periods],
            "tou_period_values": tou_values,
            "dynamic_period_values": dynamic_values,
            "dynamic_prices": [{"start": x["start"].isoformat(), "end": x["end"].isoformat(), "price_per_kwh": x["price_per_kwh"], "price": x.get("price")} for x in dynamic_slots],
            "dynamic_source": cfg.get("dynamic_source"),
            "dynamic_input_unit": cfg.get("dynamic_input_unit"),
            "dynamic_received_at": cfg.get("dynamic_received_at"),
            "dynamic_coverage_start": cfg.get("dynamic_coverage_start"),
            "dynamic_coverage_end": cfg.get("dynamic_coverage_end"),
            "export_tariff": export_rate if enabled else None,
            "export_depreciation": export_depreciation if enabled else None,
            "active_export_tariff": active_export_rate if enabled else None,
            "dynamic_export_pricing": bool(enabled and tariff_mode == "dynamic" and export_depreciation is not None),
            "standing_charge": standing if enabled else None,
            "grid_import_kwh": round(imported, 4), "grid_export_kwh": round(exported, 4),
            "solar_self_consumed_kwh": round(direct_solar, 4),
            "direct_solar_to_home_kwh": round(direct_solar, 4),
            "battery_charge_kwh": round(battery_charge, 4),
            "battery_discharge_kwh": round(battery_discharge, 4),
            "battery_support_to_home_kwh": round(battery_to_home, 4),
            "battery_charge_source": battery_charge_source,
            "battery_discharge_source": battery_discharge_source,
            "grid_cost_today": round(grid_cost, 4) if enabled else None,
            "export_revenue_today": round(export_revenue, 4) if enabled else None,
            "solar_value_today": round(direct_solar_value, 4) if enabled else None,
            "battery_support_value_today": round(battery_support_value, 4) if enabled else None,
            "avoided_import_value_today": round(avoided_import_value, 4) if enabled else None,
            "net_benefit_today": round(net_benefit, 4) if enabled else None,
            "device_costs": devices, "data_confidence": confidence,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "solar_period_complete": solar_period_complete,
            "assumptions": (("Dynamic import prices use absolute timestamped price slots supplied through the AION EMS Zeus set_energy_prices action. Export value is derived per slot as import price minus configured export depreciation." if export_depreciation is not None else "Dynamic import prices use absolute timestamped price slots supplied through the AION EMS Zeus set_energy_prices action. Export remains the configured fixed tariff because no export depreciation is configured.") if tariff_mode == "dynamic" else ("Time-of-use import costs use Home Assistant Recorder hourly grid-import changes and the configured local-time schedule. Avoided-import values use the measured effective import rate for the selected evidence window. Export remains a fixed tariff in this release." if tariff_mode == "time_of_use" else "Fixed tariffs. Direct solar and measured battery discharge to the home are valued as avoided grid purchases. Canonical solar excludes measured export; battery charging is tracked separately. If the Solar Input changes mid-day and the solar period is incomplete, Today is temporarily reconstructed from measured home, grid and battery totals until midnight.")),
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last

__all__ = ["FinanceEngine"]
