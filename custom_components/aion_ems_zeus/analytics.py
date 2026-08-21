"""Historical analytics, forecasting, optimization and scheduling engines."""

from __future__ import annotations

from .device_roles import is_consuming_load
from .period_authority import canonical_period_windows, date_in_period

from datetime import datetime, timedelta, timezone
from typing import Any
import re
import statistics

from homeassistant.util import dt as dt_util
from homeassistant.components.energy import async_get_manager


class HistoricalAnalyticsEngine:
    """Historical Analytics v2 with day, week, month and year periods."""

    def __init__(self, hass, event_bus, data_lake, registry) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.registry = registry
        # Share the same canonical Energy Mapping instance as DataLake/DataBus.
        # Finance consumes HistoricalAnalyticsEngine.periods, so Analytics must
        # resolve the Inputs -> Solar mapping from the same source of truth.
        self.energy_mapping = data_lake.energy_mapping
        self.last = {"status": "Waiting", "summary": "Collecting historical data."}
        self._ha_energy_days: dict[str, dict[str, float]] = {}
        self._ha_energy_status: dict[str, Any] = {"status": "Not loaded"}
        # Backward-compatible battery diagnostics retained for existing UI consumers.
        self._ha_battery_days: dict[str, dict[str, float]] = {"charge": {}, "discharge": {}}
        self._ha_battery_status: dict[str, Any] = {"status": "Not loaded"}
        # Canonical completed-hour evidence used by Consumption Timing Intelligence.
        # Raw hourly rows stay internal and are never exposed as sensor attributes.
        self._ha_consumption_hourly: list[dict[str, Any]] = []
        self._ha_consumption_hourly_status: dict[str, Any] = {"status": "Not loaded"}

    async def async_refresh_ha_energy_battery(self) -> None:
        """Load mapped energy meters from Home Assistant long-term statistics.

        The method name is retained for compatibility with the scheduler, but it
        now refreshes every mapped Energy Dashboard meter. Daily ``change`` rows
        are the authoritative source for Zeus period cards and charts.
        """
        mappings = dict((getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}) or {})
        roles = {
            "solar_energy_kwh": ("solar_energy_total", "solar_energy_today"),
            "house_energy_kwh": ("house_energy_total", "house_energy_today"),
            "grid_import_energy_kwh": ("grid_import_energy_total", "grid_import_energy_today"),
            "grid_export_energy_kwh": ("grid_export_energy_total", "grid_export_energy_today"),
            "battery_charge_energy_kwh": ("battery_charge_energy_total", "battery_charge_energy_today"),
            "battery_discharge_energy_kwh": ("battery_discharge_energy_total", "battery_discharge_energy_today"),
        }
        selected: dict[str, str] = {}
        selected_groups: dict[str, list[str]] = {}
        for key, candidates in roles.items():
            entity_id = next((str(mappings.get(field) or "").strip() for field in candidates if str(mappings.get(field) or "").strip()), "")
            if entity_id:
                selected[key] = entity_id
                selected_groups[key] = [entity_id]

        # Home Assistant Energy may contain more than one source of the same
        # physical role (most importantly multiple PV sources). The Energy
        # Dashboard sums those sources. Zeus must use the same source set rather
        # than choosing only the first imported mapping.
        try:
            manager = await async_get_manager(self.hass)
            getter = getattr(manager, "async_get_preferences", None)
            if callable(getter):
                prefs = await getter()
            else:
                prefs = getattr(manager, "data", None)
                if prefs is None:
                    prefs = getattr(manager, "preferences", None)
            prefs = dict(prefs or {})
            ha_groups: dict[str, list[str]] = {
                "solar_energy_kwh": [],
                "grid_import_energy_kwh": [],
                "grid_export_energy_kwh": [],
                "battery_charge_energy_kwh": [],
                "battery_discharge_energy_kwh": [],
            }
            for item in prefs.get("energy_sources") or []:
                if not isinstance(item, dict):
                    continue
                source_type = str(item.get("type") or "").lower()
                if source_type == "solar":
                    entity_id = str(item.get("stat_energy_from") or "").strip()
                    if entity_id:
                        ha_groups["solar_energy_kwh"].append(entity_id)
                elif source_type == "grid":
                    entity_id = str(item.get("stat_energy_from") or "").strip()
                    if entity_id:
                        ha_groups["grid_import_energy_kwh"].append(entity_id)
                    entity_id = str(item.get("stat_energy_to") or "").strip()
                    if entity_id:
                        ha_groups["grid_export_energy_kwh"].append(entity_id)
                    for flow in item.get("flow_from") or []:
                        if isinstance(flow, dict):
                            entity_id = str(flow.get("stat_energy_from") or flow.get("entity_id") or "").strip()
                            if entity_id:
                                ha_groups["grid_import_energy_kwh"].append(entity_id)
                    for flow in item.get("flow_to") or []:
                        if isinstance(flow, dict):
                            entity_id = str(flow.get("stat_energy_to") or flow.get("entity_id") or "").strip()
                            if entity_id:
                                ha_groups["grid_export_energy_kwh"].append(entity_id)
                elif source_type == "battery":
                    entity_id = str(item.get("stat_energy_from") or "").strip()
                    if entity_id:
                        ha_groups["battery_discharge_energy_kwh"].append(entity_id)
                    entity_id = str(item.get("stat_energy_to") or "").strip()
                    if entity_id:
                        ha_groups["battery_charge_energy_kwh"].append(entity_id)
            for key, values in ha_groups.items():
                unique = list(dict.fromkeys(v for v in values if v))
                if unique:
                    selected_groups[key] = unique
                    selected[key] = unique[0]
        except Exception:
            # Registry mappings remain a safe fallback if EnergyManager is not
            # available during an early startup refresh.
            pass

        entity_ids = sorted({entity_id for values in selected_groups.values() for entity_id in values})
        if not entity_ids:
            self._ha_energy_days = {}
            self._ha_energy_status = {"status": "No mapped energy statistic sensors"}
            self._ha_battery_days = {"charge": {}, "discharge": {}}
            self._ha_battery_status = dict(self._ha_energy_status)
            self._ha_consumption_hourly = []
            self._ha_consumption_hourly_status = dict(self._ha_energy_status)
            return
        try:
            now = dt_util.now()
            start_local = dt_util.start_of_local_day(now - timedelta(days=401))
            # Completed days can use Recorder's long-term day buckets.  The
            # current local day must not: a day bucket can straddle a timezone
            # boundary or lag the Energy Dashboard's in-progress window.  Query
            # today's short-term 5-minute changes from local midnight instead;
            # this mirrors the measured Home Assistant Energy window and avoids
            # carrying energy from outside the canonical local day.
            today_start = dt_util.start_of_local_day(now)
            history_response = await self.hass.services.async_call(
                "recorder", "get_statistics",
                {
                    "statistic_ids": entity_ids,
                    "start_time": start_local,
                    "end_time": today_start,
                    "period": "day",
                    "types": ["change"],
                    "units": {"energy": "kWh"},
                },
                blocking=True, return_response=True,
            )
            today_response = await self.hass.services.async_call(
                "recorder", "get_statistics",
                {
                    "statistic_ids": entity_ids,
                    "start_time": today_start,
                    "end_time": now + timedelta(minutes=1),
                    "period": "5minute",
                    "types": ["change"],
                    "units": {"energy": "kWh"},
                },
                blocking=True, return_response=True,
            )

            timing_roles = ("house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh", "solar_energy_kwh", "battery_discharge_energy_kwh")
            timing_selected_groups = {
                key: list(selected_groups.get(key) or ([selected[key]] if selected.get(key) else []))
                for key in timing_roles
            }
            timing_selected = {key: values[0] for key, values in timing_selected_groups.items() if values}
            timing_ids = sorted({entity_id for values in timing_selected_groups.values() for entity_id in values})
            timing_response = {}
            if timing_ids and timing_selected.get("house_energy_kwh"):
                timing_start = dt_util.start_of_local_day(today_start - timedelta(days=30))
                timing_response = await self.hass.services.async_call(
                    "recorder", "get_statistics",
                    {
                        "statistic_ids": timing_ids,
                        "start_time": timing_start,
                        "end_time": today_start,
                        "period": "hour",
                        "types": ["change"],
                        "units": {"energy": "kWh"},
                    },
                    blocking=True, return_response=True,
                )

            raw_history = (history_response or {}).get("statistics", history_response or {})
            raw_today = (today_response or {}).get("statistics", today_response or {})
            raw_timing = (timing_response or {}).get("statistics", timing_response or {})
            result: dict[str, dict[str, float]] = {key: {} for key in roles}
            for key in roles:
                entity_group = list(selected_groups.get(key) or ([selected[key]] if selected.get(key) else []))
                for entity_id in entity_group:
                    for row in list((raw_history or {}).get(entity_id) or []):
                        if not isinstance(row, dict):
                            continue
                        value = self._ha_stat_number(row.get("change"))
                        stamp = self._ha_stat_datetime(row.get("start"))
                        if value is None or stamp is None or value < -0.001:
                            continue
                        day = dt_util.as_local(stamp).date().isoformat()
                        result[key][day] = round(result[key].get(day, 0.0) + max(value, 0.0), 4)

                    today_total = 0.0
                    today_rows = 0
                    for row in list((raw_today or {}).get(entity_id) or []):
                        if not isinstance(row, dict):
                            continue
                        value = self._ha_stat_number(row.get("change"))
                        stamp = self._ha_stat_datetime(row.get("start"))
                        if value is None or stamp is None or value < -0.001:
                            continue
                        local_stamp = dt_util.as_local(stamp)
                        if local_stamp < today_start or local_stamp > now + timedelta(minutes=5):
                            continue
                        today_total += max(value, 0.0)
                        today_rows += 1
                    if today_rows:
                        today_key = now.date().isoformat()
                        result[key][today_key] = round(result[key].get(today_key, 0.0) + today_total, 4)
            self._ha_energy_days = result

            hourly_by_start: dict[str, dict[str, Any]] = {}
            for key, entity_group in timing_selected_groups.items():
                for entity_id in entity_group:
                    for row in list((raw_timing or {}).get(entity_id) or []):
                        if not isinstance(row, dict):
                            continue
                        value = self._ha_stat_number(row.get("change"))
                        stamp = self._ha_stat_datetime(row.get("start"))
                        if value is None or stamp is None or value < -0.001:
                            continue
                        local_stamp = dt_util.as_local(stamp)
                        if local_stamp >= today_start:
                            continue
                        bucket = local_stamp.replace(minute=0, second=0, microsecond=0)
                        bucket_key = bucket.isoformat()
                        target = hourly_by_start.setdefault(bucket_key, {"start": bucket_key})
                        target[key] = round(target.get(key, 0.0) + max(value, 0.0), 4)

            self._ha_consumption_hourly = [
                row for _, row in sorted(hourly_by_start.items())
                if row.get("house_energy_kwh") is not None
            ]
            completed_hour_days = sorted({
                str(row.get("start") or "")[:10] for row in self._ha_consumption_hourly
                if row.get("start")
            })
            self._ha_consumption_hourly_status = {
                "status": "Ready" if self._ha_consumption_hourly else "No completed hourly consumption statistics",
                "source": "Home Assistant Recorder statistics · completed hourly change",
                "entities": timing_selected,
                "hour_count": len(self._ha_consumption_hourly),
                "completed_day_count": len(completed_hour_days),
                "refreshed_at": now.isoformat(),
            }

            display_entities = {
                key: (values[0] if len(values) == 1 else " + ".join(values))
                for key, values in selected_groups.items() if values
            }
            self._ha_energy_status = {
                "status": "Ready",
                "source": "Home Assistant Energy source set + Recorder statistics · local-day aligned",
                "entities": display_entities,
                "entity_sets": {key: list(values) for key, values in selected_groups.items() if values},
                "days": {key: len(values) for key, values in result.items()},
                "refreshed_at": now.isoformat(),
            }
            self._ha_battery_days = {
                "charge": dict(result.get("battery_charge_energy_kwh", {})),
                "discharge": dict(result.get("battery_discharge_energy_kwh", {})),
            }
            self._ha_battery_status = {
                "status": "Ready",
                "source": self._ha_energy_status["source"],
                "charge_entity": selected.get("battery_charge_energy_kwh"),
                "discharge_entity": selected.get("battery_discharge_energy_kwh"),
                "charge_days": len(self._ha_battery_days["charge"]),
                "discharge_days": len(self._ha_battery_days["discharge"]),
                "refreshed_at": now.isoformat(),
            }
        except Exception as err:
            self._ha_energy_days = {}
            self._ha_energy_status = {
                "status": "Home Assistant energy statistics unavailable",
                "error": f"{type(err).__name__}: {err}",
                "entities": selected,
            }
            self._ha_battery_days = {"charge": {}, "discharge": {}}
            self._ha_battery_status = dict(self._ha_energy_status)
            self._ha_consumption_hourly = []
            self._ha_consumption_hourly_status = dict(self._ha_energy_status)

    @staticmethod
    def _ha_stat_number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _ha_stat_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            stamp = value
        elif isinstance(value, str):
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            try:
                numeric = float(value)
                if numeric > 10_000_000_000:
                    numeric /= 1000.0
                stamp = datetime.fromtimestamp(numeric, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                return None
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

    def _mapped_today_overlay(self) -> dict[str, Any]:
        """Return live mapped daily-reset energy totals for the current local day.

        Daily-reset mappings are the current-day authority. Recorder statistics
        remain the authority for completed days and longer historical windows.
        This keeps backend Briefing/Efficiency consumers aligned with the same
        live Today values already exposed by the Zeus frontend.
        """
        mappings = dict((getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}) or {})
        roles = {
            "solar_energy_kwh": "solar_energy_today",
            "house_energy_kwh": "house_energy_today",
            "grid_import_energy_kwh": "grid_import_energy_today",
            "grid_export_energy_kwh": "grid_export_energy_today",
            "battery_charge_energy_kwh": "battery_charge_energy_today",
            "battery_discharge_energy_kwh": "battery_discharge_energy_today",
        }
        out: dict[str, Any] = {}
        for key, field in roles.items():
            entity_id = str(mappings.get(field) or "").strip()
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or str(state.state).strip().lower() in {"", "unknown", "unavailable", "none"}:
                continue
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                continue
            unit = str(state.attributes.get("unit_of_measurement") or "kWh").strip().lower()
            if unit == "wh":
                value /= 1000.0
            elif unit == "mwh":
                value *= 1000.0
            elif unit not in {"kwh", "kilowatt-hour", "kilowatt-hours"}:
                continue
            out[key] = round(max(value, 0.0), 4)
            out[f"{key}_method"] = "mapped_daily_energy"
            out[f"{key}_source"] = entity_id
        return out

    @staticmethod
    def _aggregate(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        keys = [
            "solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh",
            "grid_export_energy_kwh", "battery_charge_energy_kwh",
            "battery_discharge_energy_kwh",
        ]
        result: dict[str, Any] = {"label": label, "day_count": len(rows)}
        for key in keys:
            result[key] = round(sum(float(row.get(key, 0) or 0) for row in rows), 3)
        result["peak_solar_power_w"] = round(max((float(r.get("peak_solar_power_w", 0) or 0) for r in rows), default=0), 1)
        result["peak_house_power_w"] = round(max((float(r.get("peak_house_power_w", 0) or 0) for r in rows), default=0), 1)
        solar = result["solar_energy_kwh"]
        house = result["house_energy_kwh"]
        imported = result["grid_import_energy_kwh"]
        exported = result["grid_export_energy_kwh"]
        battery_discharge = result["battery_discharge_energy_kwh"]

        # v14.0.0-alpha.22.13.4.1.3: do not assume every exported kWh came
        # from current-period PV.  Battery discharge (and future local sources)
        # can contribute to grid export, so ``solar - total export`` can produce
        # a false zero solar-utilisation result when total export exceeds PV.
        # The canonical home/grid boundary proves how much of house demand was
        # supplied locally.  Allocate measured battery support first, then only
        # the remaining proven local-home supply to direct solar, capped by PV.
        local_home_supply = max(house - imported, 0.0)
        battery_to_home = min(max(battery_discharge, 0.0), local_home_supply)
        direct_solar = min(max(solar, 0.0), max(local_home_supply - battery_to_home, 0.0))
        result["battery_support_to_home_kwh"] = round(battery_to_home, 3)
        result["direct_solar_consumption_kwh"] = round(direct_solar, 3)
        result["self_consumption_percent"] = round(direct_solar / solar * 100, 1) if solar > 0 else None
        result["self_sufficiency_percent"] = round(local_home_supply / house * 100, 1) if house > 0 else None
        result["grid_dependency_percent"] = round(imported / house * 100, 1) if house > 0 else None
        result["export_exceeds_solar"] = bool(exported > solar + 0.05)
        result["export_source_note"] = (
            "Measured grid export exceeds measured PV; export therefore includes stored/other energy or a source-period mismatch. "
            "Zeus does not subtract total export from PV utilisation."
            if result["export_exceeds_solar"] else None
        )
        return result

    @staticmethod
    def _comparison(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key in ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh"):
            cur = float(current.get(key, 0) or 0)
            prev = float(previous.get(key, 0) or 0)
            out[key] = {"difference_kwh": round(cur - prev, 3), "percent": round((cur - prev) / prev * 100, 1) if prev > 0 else None}
        return out

    def refresh(self) -> dict[str, Any]:
        raw_daily = self.data_lake.data.get("daily_summaries", {})
        daily = {day: dict(row or {}) for day, row in (raw_daily or {}).items()}

        # Home Assistant Recorder statistics are authoritative for every mapped
        # energy meter. Overlay all available daily changes before aggregation so
        # cards, charts, comparisons and averages consume one coherent dataset.
        stat_entities = (self._ha_energy_status.get("entities") or {}) if isinstance(self._ha_energy_status, dict) else {}
        devices = (getattr(self.registry, "data", {}) or {}).get("devices", [])
        hybrid_devices = [d for d in devices if isinstance(d, dict) and bool(d.get("hybrid_inverter"))]
        # Solar period ownership follows the canonical Inputs mapping.
        hybrid_true_pv_entity = str((self.energy_mapping.mappings or {}).get("solar_power") or "").strip()
        hybrid_true_pv_configured = bool(hybrid_true_pv_entity)
        now = dt_util.now()
        today_key = now.date().isoformat()
        all_stat_days = set()
        for values in self._ha_energy_days.values():
            all_stat_days.update(values)
        for day in all_stat_days:
            row = daily.setdefault(day, {"date": day})
            for energy_key, values in self._ha_energy_days.items():
                if day not in values:
                    continue
                # HYBRID + True PV is a strict canonical boundary: the mapped
                # inverter AC energy statistic may include battery discharge and
                # must never overwrite solar energy reconstructed from the
                # dedicated photovoltaic power sensor. Other mapped meters remain
                # authoritative as before.
                if energy_key == "solar_energy_kwh" and hybrid_true_pv_configured:
                    # Energy totals follow the same configured HA Energy solar
                    # source set that feeds the Energy Dashboard. A dedicated
                    # True-PV power entity remains the instantaneous live source
                    # and the fallback only when no HA Energy statistic exists.
                    recorder_value = max(float(values[day] or 0.0), 0.0)
                    row["solar_energy_kwh"] = round(recorder_value, 4)
                    row["solar_energy_kwh_method"] = "home_assistant_energy_source_set_statistics"
                    row["solar_energy_kwh_source"] = stat_entities.get(energy_key)
                    row["solar_true_pv_live_entity"] = hybrid_true_pv_entity
                    continue
                recorder_value = max(float(values[day] or 0.0), 0.0)
                if day == today_key and energy_key in {"battery_charge_energy_kwh", "battery_discharge_energy_kwh"}:
                    # Recorder day statistics can lag or briefly regress during the
                    # live day. DataLake also accumulates the canonical mapped
                    # battery meter continuously. Never let a stale Recorder
                    # refresh erase battery energy already measured today.
                    live_value = max(float(row.get(energy_key, 0.0) or 0.0), 0.0)
                    row[energy_key] = round(max(live_value, recorder_value), 4)
                    row[f"{energy_key}_method"] = "live_datalake_max_home_assistant_energy_statistics"
                    row[f"{energy_key}_source"] = stat_entities.get(energy_key) or row.get(f"{energy_key}_source")
                    continue
                row[energy_key] = recorder_value
                row[f"{energy_key}_method"] = "home_assistant_energy_statistics"
                row[f"{energy_key}_source"] = stat_entities.get(energy_key)

            # Home Assistant's home-consumption series is commonly derived from
            # source flows rather than mapped as a dedicated meter. Reproduce the
            # same balance only when no authoritative house statistic exists.
            if day not in self._ha_energy_days.get("house_energy_kwh", {}):
                required = ("solar_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh")
                if all(key in row for key in required):
                    house = (
                        float(row.get("solar_energy_kwh", 0) or 0)
                        + float(row.get("grid_import_energy_kwh", 0) or 0)
                        + float(row.get("battery_discharge_energy_kwh", 0) or 0)
                        - float(row.get("grid_export_energy_kwh", 0) or 0)
                        - float(row.get("battery_charge_energy_kwh", 0) or 0)
                    )
                    row["house_energy_kwh"] = round(max(house, 0.0), 4)
                    row["house_energy_kwh_method"] = "home_assistant_energy_flow_balance"
                    row["house_energy_kwh_source"] = "derived from HA Energy statistics"

        ordered_days = sorted(daily)
        rows = [daily[d] for d in ordered_days]
        yesterday_key = (now.date() - timedelta(days=1)).isoformat()
        today_row = daily.get(today_key, {"date": today_key})
        yesterday_row = daily.get(yesterday_key, {"date": yesterday_key})
        # Current-day daily-reset mappings are authoritative for every backend
        # consumer (Briefing, Home Efficiency, Finance and intelligence).
        # This prevents those engines from seeing stale/partial Recorder Today
        # values while the normal Zeus cards already show the mapped live meter.
        mapped_today = self._mapped_today_overlay()
        if mapped_today:
            today_row.update(mapped_today)
            daily[today_key] = today_row
        if hybrid_true_pv_configured:
            # Live True-PV power remains the instantaneous authority and fallback
            # integration source. If a mapped daily-reset solar-energy meter is
            # available, however, it is the authoritative Today energy total.
            true_pv_today = max(float(today_row.get("solar_true_pv_integrated_kwh", 0.0) or 0.0), 0.0)
            mapped_solar_source = str((getattr(self.registry, "data", {}) or {}).get("entity_mappings", {}).get("solar_energy_today") or "").strip()
            mapped_solar_active = bool(mapped_solar_source and today_row.get("solar_energy_kwh_source") == mapped_solar_source)
            ha_solar_active = bool(
                today_key in (self._ha_energy_days.get("solar_energy_kwh", {}) or {})
                and today_row.get("solar_energy_kwh_method") == "home_assistant_energy_source_set_statistics"
            )
            canonical_today = max(float(today_row.get("solar_energy_kwh", true_pv_today) or 0.0), 0.0)
            today_row["hybrid_true_pv_configured"] = True
            today_row["hybrid_true_pv_entity"] = hybrid_true_pv_entity
            today_row["solar_true_pv_integrated_kwh"] = round(true_pv_today, 4)
            today_row["solar_energy_kwh"] = round(canonical_today, 4)
            if not mapped_solar_active and not ha_solar_active:
                today_row.setdefault("solar_energy_kwh_method", "inputs_solar_power_integration")
                today_row["solar_energy_kwh_source"] = hybrid_true_pv_entity
        # Match Home Assistant Energy calendar periods. A Week is the current
        # local ISO week (Monday through today), not a rolling seven-day window.
        # Rolling windows are still exposed separately for comparisons/averages.
        today_date = now.date()
        period_windows = canonical_period_windows(now)
        week_start = period_windows["week"].start.date()
        previous_week_start = week_start - timedelta(days=7)
        # Comparisons use an aligned prior window. On Monday, for example, the
        # current week is compared with the previous Monday only rather than a
        # complete seven-day week. This keeps Performance Intelligence fair
        # during an in-progress calendar period.
        previous_week_end = previous_week_start + (today_date - week_start)

        month_start = today_date.replace(day=1)
        previous_month_last = month_start - timedelta(days=1)
        previous_month_start = previous_month_last.replace(day=1)
        previous_month_end = min(
            previous_month_start + (today_date - month_start),
            previous_month_last,
        )

        year_start = today_date.replace(month=1, day=1)
        previous_year_start = year_start.replace(year=year_start.year - 1)
        try:
            previous_year_end = today_date.replace(year=today_date.year - 1)
        except ValueError:
            # 29 February compares with 28 February in a non-leap prior year.
            previous_year_end = today_date.replace(year=today_date.year - 1, day=28)

        dated_rows = [(datetime.fromisoformat(day).date(), row) for day, row in zip(ordered_days, rows)]
        week_rows = [row for day, row in dated_rows if date_in_period(day, "week", today_date)]
        prev_week_rows = [row for day, row in dated_rows if previous_week_start <= day <= previous_week_end]
        prev_month_rows = [row for day, row in dated_rows if previous_month_start <= day <= previous_month_end]
        prev_year_rows = [row for day, row in dated_rows if previous_year_start <= day <= previous_year_end]
        rolling_7_rows = [row for day, row in dated_rows if today_date - timedelta(days=6) <= day <= today_date]
        rolling_30_rows = [row for day, row in dated_rows if today_date - timedelta(days=29) <= day <= today_date]
        completed_7_rows = [row for day, row in dated_rows if today_date - timedelta(days=7) <= day < today_date]
        completed_30_rows = [row for day, row in dated_rows if today_date - timedelta(days=30) <= day < today_date]
        month_rows = [row for day, row in dated_rows if date_in_period(day, "month", today_date)]
        year_rows = [row for day, row in dated_rows if date_in_period(day, "year", today_date)]
        today = self._aggregate([today_row] if today_key in daily else [], "today")
        # Preserve authoritative source/method/backfill diagnostics for Today.
        # The generic aggregate intentionally sums numeric energy fields, but
        # the Battery page also needs to know exactly how those values were built.
        for energy_key in ("solar_energy_kwh", "battery_charge_energy_kwh", "battery_discharge_energy_kwh"):
            for suffix in (
                "method", "source", "seeded_at", "backfill_period",
                "cycle_start", "day_definition", "backfill_rows",
                "positive_change_rows", "last_delta_kwh",
            ):
                source_key = f"{energy_key}_{suffix}"
                if source_key in today_row:
                    today[source_key] = today_row.get(source_key)
        for key in ("hybrid_true_pv_configured", "hybrid_true_pv_entity", "solar_true_pv_integrated_kwh", "solar_energy_raw_ac_kwh", "solar_hybrid_correction_kwh"):
            if key in today_row:
                today[key] = today_row.get(key)
        yesterday = self._aggregate([yesterday_row] if yesterday_key in daily else [], "yesterday")
        week = self._aggregate(week_rows, "last_7_days")
        previous_week = self._aggregate(prev_week_rows, "previous_week_aligned")
        month = self._aggregate(month_rows, "this_month")
        previous_month = self._aggregate(prev_month_rows, "previous_month_aligned")
        year = self._aggregate(year_rows, "this_year")
        previous_year = self._aggregate(prev_year_rows, "previous_year_aligned")
        best_solar = max(rows, key=lambda x: x.get("solar_energy_kwh", 0), default=None)
        peak_load = max(rows, key=lambda x: x.get("peak_house_power_w", 0), default=None)
        self.last = {
            "status": "Ready" if rows else "Waiting",
            "day_count": len(rows),
            "period_authority": {name: window.as_dict() for name, window in period_windows.items()},
            "periods": {"today": today, "yesterday": yesterday, "week": week, "month": month, "year": year},
            "comparison": {
                "today_vs_yesterday": self._comparison(today, yesterday),
                "week_vs_previous_week": self._comparison(week, previous_week),
                "month_vs_previous_month": self._comparison(month, previous_month),
                "year_vs_previous_year": self._comparison(year, previous_year),
            },
            "comparison_periods": {
                "today": {"label": "Previous day", "available": bool(yesterday.get("day_count", 0)), "row_count": int(yesterday.get("day_count", 0) or 0), "data": yesterday},
                "week": {"label": "Previous week · aligned", "available": bool(prev_week_rows), "row_count": len(prev_week_rows), "start": previous_week_start.isoformat(), "end": previous_week_end.isoformat(), "data": previous_week},
                "month": {"label": "Previous month · aligned", "available": bool(prev_month_rows), "row_count": len(prev_month_rows), "start": previous_month_start.isoformat(), "end": previous_month_end.isoformat(), "data": previous_month},
                "year": {"label": "Previous year · aligned", "available": bool(prev_year_rows), "row_count": len(prev_year_rows), "start": previous_year_start.isoformat(), "end": previous_year_end.isoformat(), "data": previous_year},
            },
            "last_7_days": rolling_7_rows,
            "last_30_days": rolling_30_rows,
            "completed_7_days": completed_7_rows,
            "completed_30_days": completed_30_rows,
            "chart_history": {
                "today": [today_row] if today_key in daily else [],
                "week": week_rows,
                "rolling_7": rolling_7_rows,
                "rolling_30": rolling_30_rows,
                "month": month_rows,
                "year": year_rows,
                "total": rows,
            },
            "best_solar_day": best_solar,
            "peak_consumption_day": peak_load,
            "summary": (f"Today: solar {today['solar_energy_kwh']:.2f} kWh, consumption {today['house_energy_kwh']:.2f} kWh, import {today['grid_import_energy_kwh']:.2f} kWh, export {today['grid_export_energy_kwh']:.2f} kWh." if rows else "Collecting the first minute-level samples."),
            "energy_methods": {k: today_row.get(f"{k}_method", "power_integration") for k in ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh", "battery_charge_energy_kwh", "battery_discharge_energy_kwh")},
            "energy_sources": {k: today_row.get(f"{k}_source") for k in ("solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh", "grid_export_energy_kwh", "battery_charge_energy_kwh", "battery_discharge_energy_kwh")},
            "method_note": "Mapped energy periods use one canonical local-day table from Home Assistant Recorder long-term statistics. Week is the current local ISO week; rolling 7/30-day rows are separate and used only for historical comparisons.",
            "period_boundaries": {
                "timezone": str(dt_util.DEFAULT_TIME_ZONE),
                "today": today_key,
                "week_start": week_start.isoformat(),
                "month_start": today_key[:7] + "-01",
                "year_start": today_key[:4] + "-01-01",
            },
            "energy_statistics": dict(self._ha_energy_status),
            "battery_energy_statistics": dict(self._ha_battery_status),
            "safety": "Read-only historical analytics.",
        }
        self.event_bus.publish("HistoricalAnalyticsUpdated", "AnalyticsEngine", {"day_count": len(rows)})
        return self.last

    @staticmethod
    def _compact_chart_row(row: dict[str, Any]) -> dict[str, Any]:
        """Return only chart fields required by the frontend."""
        keys = (
            "date",
            "solar_energy_kwh",
            "house_energy_kwh",
            "grid_import_energy_kwh",
            "grid_export_energy_kwh",
            "battery_charge_energy_kwh",
            "battery_discharge_energy_kwh",
            "self_sufficiency_percent",
        )
        return {key: row.get(key) for key in keys if row.get(key) is not None}

    def _recorder_chart_history(self) -> dict[str, list[dict[str, Any]]]:
        """Build compact chart series without duplicating them in summary attributes."""
        data = self.last or {}
        raw_charts = data.get("chart_history", {}) or {}

        def compact(rows):
            return [self._compact_chart_row(row) for row in (rows or [])]

        def bucket(rows, mode):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows or []:
                date = str(row.get("date") or "")
                if not date:
                    continue
                key = date[:7] if mode == "month" else date[:4]
                grouped.setdefault(key, []).append(row)
            result = []
            for key in sorted(grouped):
                aggregate = self._aggregate(grouped[key], key)
                aggregate["date"] = key
                result.append(self._compact_chart_row(aggregate))
            return result

        total_rows = raw_charts.get("total", []) or []
        return {
            "today": compact(raw_charts.get("today", []))[:1],
            "week": compact(raw_charts.get("week", []))[-7:],
            "rolling_7": compact(raw_charts.get("rolling_7", []))[-7:],
            "rolling_30": compact(raw_charts.get("rolling_30", []))[-30:],
            "month": compact(raw_charts.get("month", []))[-31:],
            "year": bucket(raw_charts.get("year", []), "month")[-12:],
            "total": bucket(total_rows, "year")[-20:],
        }

    def recorder_summary(self) -> dict[str, Any]:
        """Return summary-only attributes safe for Home Assistant Recorder.

        Chart arrays are intentionally published by the dedicated Historical Chart
        Data entity, preventing this summary sensor from exceeding the 16 KiB
        Recorder attribute limit as measured history grows.
        """
        data = self.last or {}
        periods = data.get("periods", {}) or {}
        total_rows = (data.get("chart_history", {}) or {}).get("total", []) or []
        total_aggregate = self._aggregate(total_rows, "total")
        return {
            "status": data.get("status"),
            "day_count": data.get("day_count"),
            "periods": {
                key: periods.get(key, {})
                for key in ("today", "week", "month", "year")
            },
            "total": total_aggregate,
            "comparison": data.get("comparison", {}),
            "comparison_periods": data.get("comparison_periods", {}),
            "best_solar_day": self._compact_chart_row(data.get("best_solar_day") or {}),
            "peak_consumption_day": self._compact_chart_row(data.get("peak_consumption_day") or {}),
            "summary": data.get("summary"),
            "energy_methods": data.get("energy_methods", {}),
            "energy_sources": data.get("energy_sources", {}),
            "method_note": data.get("method_note"),
            "period_boundaries": data.get("period_boundaries", {}),
            "safety": data.get("safety"),
            "recorder_safe": True,
            "detail_entity": "sensor.aion_ems_zeus_historical_chart_data",
            "history_resolution": {
                "today": "daily", "week": "daily", "rolling_7": "daily",
                "rolling_30": "daily", "month": "daily", "year": "monthly", "total": "yearly"
            },
        }

    def recorder_chart_data(self) -> dict[str, Any]:
        """Return compact chart arrays on a dedicated bounded entity."""
        chart_history = self._recorder_chart_history()
        return {
            "status": (self.last or {}).get("status"),
            "day_count": (self.last or {}).get("day_count"),
            "chart_history": chart_history,
            "recorder_safe": True,
            "history_resolution": {
                "today": "daily", "week": "daily", "rolling_7": "daily",
                "rolling_30": "daily", "month": "daily", "year": "monthly", "total": "yearly"
            },
        }

    def _explorer_rows(self, days: int | None = None, current_year: bool = False) -> list[dict[str, Any]]:
        """Canonical completed daily rows for Historical Energy Explorer.

        The current partial local day is always excluded. Missing days remain
        missing rather than being converted to measured zero.
        """
        rows = list(((self.last or {}).get("chart_history", {}) or {}).get("total", []) or [])
        today = dt_util.now().date()
        prepared: list[tuple[date, dict[str, Any]]] = []
        for row in rows:
            try:
                day = datetime.fromisoformat(str(row.get("date") or "")[:10]).date()
            except (TypeError, ValueError):
                continue
            if day >= today:
                continue
            if current_year and day.year != today.year:
                continue
            prepared.append((day, row))
        prepared.sort(key=lambda item: item[0])
        if days is not None:
            cutoff = today - timedelta(days=max(1, int(days)))
            prepared = [item for item in prepared if item[0] >= cutoff]
        return [row for _day, row in prepared]

    @staticmethod
    def _explorer_columnar(rows: list[dict[str, Any]], precision: int = 2) -> dict[str, Any]:
        """Recorder-efficient columnar daily series with explicit date keys."""
        metrics = {
            "solar": "solar_energy_kwh",
            "consumption": "house_energy_kwh",
            "import": "grid_import_energy_kwh",
            "export": "grid_export_energy_kwh",
            "battery": None,
            "self_sufficiency": "self_sufficiency_percent",
        }
        base_day = None
        if rows:
            try:
                base_day = datetime.fromisoformat(str(rows[0].get("date") or "")[:10]).date()
            except (TypeError, ValueError):
                base_day = None
        out: dict[str, Any] = {"base_date": base_day.isoformat() if base_day else None, "day_offsets": [], **{key: [] for key in metrics}}
        for row in rows:
            try:
                row_day = datetime.fromisoformat(str(row.get("date") or "")[:10]).date()
                offset = (row_day - base_day).days if base_day else None
            except (TypeError, ValueError):
                offset = None
            out["day_offsets"].append(offset)
            for name, key in metrics.items():
                if name == "battery":
                    values = (row.get("battery_charge_energy_kwh"), row.get("battery_discharge_energy_kwh"))
                    if not any(v is not None for v in values):
                        value = None
                    else:
                        value = sum(max(0.0, float(v or 0)) for v in values)
                else:
                    value = row.get(key) if key else None
                    if name == "self_sufficiency" and value is None:
                        home = max(0.0, float(row.get("house_energy_kwh") or 0))
                        solar = max(0.0, float(row.get("solar_energy_kwh") or 0))
                        export = max(0.0, float(row.get("grid_export_energy_kwh") or 0))
                        discharge = max(0.0, float(row.get("battery_discharge_energy_kwh") or 0))
                        direct = min(max(solar - export, 0.0), home)
                        value = min(home, direct + discharge) / home * 100.0 if home > 0 else None
                if value is None:
                    out[name].append(None)
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    out[name].append(None)
                    continue
                out[name].append(round(number, precision))
        return out

    def recorder_explorer_recent_data(self) -> dict[str, Any]:
        rows = self._explorer_rows(days=90)
        return {
            "status": (self.last or {}).get("status"),
            "series": self._explorer_columnar(rows, 2),
            "completed_day_count": len(rows),
            "range_days": 90,
            "as_of_date": dt_util.now().date().isoformat(),
            "current_partial_day_excluded": True,
            "missing_days_are_not_zero": True,
            "recorder_safe": True,
        }

    def recorder_explorer_year_data(self) -> dict[str, Any]:
        rows = self._explorer_rows(current_year=True)
        # One-decimal energy precision keeps a full 365-day daily series safely
        # bounded for Home Assistant Recorder while preserving explorer utility.
        return {
            "status": (self.last or {}).get("status"),
            "year": dt_util.now().year,
            "series": self._explorer_columnar(rows, 1),
            "completed_day_count": len(rows),
            "resolution": "daily",
            "energy_precision_kwh": 0.1,
            "current_partial_day_excluded": True,
            "missing_days_are_not_zero": True,
            "recorder_safe": True,
        }

    def recorder_battery_performance_evidence(self) -> dict[str, Any]:
        """Return compact completed-day battery evidence for Battery Performance Intelligence.

        This uses the same canonical daily history table as Battery Statistics and
        Historical Energy Explorer. The current partial local day is excluded, and
        missing measurements remain missing rather than being converted to zero.
        """
        rows = self._explorer_rows(current_year=True)
        today = dt_util.now().date()

        def row_day(row: dict[str, Any]):
            try:
                return datetime.fromisoformat(str(row.get("date") or "")[:10]).date()
            except (TypeError, ValueError):
                return None

        def measured_battery_rows(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                row for row in source
                if row.get("battery_charge_energy_kwh") is not None
                or row.get("battery_discharge_energy_kwh") is not None
            ]

        def aggregate(source: list[dict[str, Any]], label: str) -> dict[str, Any]:
            measured = measured_battery_rows(source)
            if not measured:
                return {
                    "label": label, "completed_day_count": 0, "available": False,
                    "battery_charge_kwh": None, "battery_discharge_kwh": None,
                    "house_energy_kwh": None, "solar_energy_kwh": None,
                    "grid_import_energy_kwh": None, "strongest_charge_day": None,
                    "strongest_discharge_day": None,
                }

            def total(key: str) -> float:
                values = [row.get(key) for row in measured if row.get(key) is not None]
                return round(sum(max(0.0, float(value or 0)) for value in values), 3) if values else 0.0

            def strongest(key: str):
                candidates = [row for row in measured if row.get(key) is not None]
                if not candidates:
                    return None
                row = max(candidates, key=lambda item: max(0.0, float(item.get(key) or 0)))
                return {
                    "date": str(row.get("date") or "")[:10],
                    "kwh": round(max(0.0, float(row.get(key) or 0)), 3),
                }

            return {
                "label": label,
                "completed_day_count": len(measured),
                "available": True,
                "battery_charge_kwh": total("battery_charge_energy_kwh"),
                "battery_discharge_kwh": total("battery_discharge_energy_kwh"),
                "house_energy_kwh": total("house_energy_kwh"),
                "solar_energy_kwh": total("solar_energy_kwh"),
                "grid_import_energy_kwh": total("grid_import_energy_kwh"),
                "strongest_charge_day": strongest("battery_charge_energy_kwh"),
                "strongest_discharge_day": strongest("battery_discharge_energy_kwh"),
            }

        latest = rows[-1:] if rows else []
        rolling_week = [row for row in rows if (day := row_day(row)) is not None and day >= today - timedelta(days=7)]
        current_month = [row for row in rows if (day := row_day(row)) is not None and day.year == today.year and day.month == today.month]
        current_year = [row for row in rows if (day := row_day(row)) is not None and day.year == today.year]

        # Battery activity trend interpretation uses the latest six completed
        # measured battery days, even across a year boundary.  It deliberately
        # compares activity only; no battery-health or degradation inference is
        # made from throughput or participation.
        recent_rows = measured_battery_rows(self._explorer_rows(days=30))
        trend_rows = recent_rows[-6:]

        def average(source: list[dict[str, Any]], key: str) -> float | None:
            values = [max(0.0, float(row.get(key) or 0)) for row in source if row.get(key) is not None]
            return sum(values) / len(values) if values else None

        def direction(change: float | None, stable_band: float) -> str:
            if change is None or abs(change) < stable_band:
                return "Stable"
            return "Rising" if change > 0 else "Falling"

        comparison: dict[str, Any] = {
            "available": False,
            "required_completed_days": 6,
            "completed_day_count": len(trend_rows),
            "evidence_period": "Latest 3 completed measured battery days compared with the previous 3",
            "evidence_boundary": "Battery activity trends describe measured use. They do not establish state of health, degradation, efficiency loss or a fault.",
        }
        if len(trend_rows) >= 6:
            previous, recent = trend_rows[-6:-3], trend_rows[-3:]
            previous_throughput = sum((average(previous, key) or 0.0) for key in ("battery_charge_energy_kwh", "battery_discharge_energy_kwh"))
            recent_throughput = sum((average(recent, key) or 0.0) for key in ("battery_charge_energy_kwh", "battery_discharge_energy_kwh"))
            throughput_change = ((recent_throughput - previous_throughput) / previous_throughput * 100.0) if previous_throughput > 0 else None

            def participation(source: list[dict[str, Any]]) -> float | None:
                discharge = sum(max(0.0, float(row.get("battery_discharge_energy_kwh") or 0)) for row in source if row.get("battery_discharge_energy_kwh") is not None)
                house = sum(max(0.0, float(row.get("house_energy_kwh") or 0)) for row in source if row.get("house_energy_kwh") is not None)
                return discharge / house * 100.0 if house > 0 else None

            previous_participation = participation(previous)
            recent_participation = participation(recent)
            participation_change = (recent_participation - previous_participation) if previous_participation is not None and recent_participation is not None else None
            throughput_direction = direction(throughput_change, 5.0)
            participation_direction = direction(participation_change, 2.0)
            if throughput_change is None:
                headline = "Battery activity comparison is available"
            elif throughput_direction == "Stable":
                headline = "Battery activity is broadly stable"
            else:
                headline = f"Battery activity is {throughput_direction.lower()} {abs(throughput_change):.1f}%"
            participation_text = "Battery participation could not be compared from the available household evidence."
            if participation_change is not None:
                participation_text = f"Battery participation is {participation_direction.lower()} by {abs(participation_change):.1f} percentage points." if participation_direction != "Stable" else "Battery participation is broadly stable."
            comparison.update({
                "available": True,
                "previous_avg_throughput_kwh": round(previous_throughput, 3),
                "recent_avg_throughput_kwh": round(recent_throughput, 3),
                "throughput_change_percent": round(throughput_change, 1) if throughput_change is not None else None,
                "throughput_direction": throughput_direction,
                "previous_participation_percent": round(previous_participation, 1) if previous_participation is not None else None,
                "recent_participation_percent": round(recent_participation, 1) if recent_participation is not None else None,
                "participation_change_points": round(participation_change, 1) if participation_change is not None else None,
                "participation_direction": participation_direction,
                "headline": headline,
                "interpretation": f"Average daily battery throughput moved from {previous_throughput:.2f} kWh to {recent_throughput:.2f} kWh. {participation_text}",
            })

        return {
            "status": (self.last or {}).get("status"),
            "periods": {
                "today": aggregate(latest, "Latest completed measured day"),
                "week": aggregate(rolling_week, "Latest 7 completed calendar days"),
                "month": aggregate(current_month, "Current month · completed measured days"),
                "year": aggregate(current_year, "Current year · completed measured days"),
            },
            "comparison": comparison,
            "current_partial_day_excluded": True,
            "missing_evidence_is_not_zero": True,
            "source": "canonical_daily_history",
            "recorder_safe": True,
        }

    def recorder_solar_performance_evidence(self) -> dict[str, Any]:
        """Compact completed-day evidence for Solar Performance Intelligence.

        Solar activity is described from canonical measured daily history only.
        The current partial local day is excluded.  Relationships with household
        demand, export and grid import are context, not proof of weather,
        shading, equipment health, curtailment or any other underlying cause.
        """
        rows = self._explorer_rows(days=90)
        today = dt_util.now().date()

        def row_day(row: dict[str, Any]):
            try:
                return datetime.fromisoformat(str(row.get("date") or "")[:10]).date()
            except (TypeError, ValueError):
                return None

        measured = [row for row in rows if row.get("solar_energy_kwh") is not None]

        def aggregate(source: list[dict[str, Any]], label: str) -> dict[str, Any]:
            source = [row for row in source if row.get("solar_energy_kwh") is not None]
            if not source:
                return {
                    "label": label, "completed_day_count": 0, "available": False,
                    "solar_energy_kwh": None, "house_energy_kwh": None,
                    "grid_export_energy_kwh": None, "grid_import_energy_kwh": None,
                    "local_solar_use_kwh": None, "local_use_share_percent": None,
                    "strongest_solar_day": None,
                }

            def total(key: str) -> float | None:
                values = [row.get(key) for row in source if row.get(key) is not None]
                if not values:
                    return None
                return round(sum(max(0.0, float(value or 0)) for value in values), 3)

            solar = total("solar_energy_kwh") or 0.0
            house = total("house_energy_kwh")
            exported = total("grid_export_energy_kwh")
            imported = total("grid_import_energy_kwh")
            local_use = None if exported is None else max(0.0, solar - exported)
            local_share = (local_use / solar * 100.0) if local_use is not None and solar > 0 else None
            strongest = max(source, key=lambda item: max(0.0, float(item.get("solar_energy_kwh") or 0)))
            return {
                "label": label,
                "completed_day_count": len(source),
                "available": True,
                "solar_energy_kwh": round(solar, 3),
                "house_energy_kwh": house,
                "grid_export_energy_kwh": exported,
                "grid_import_energy_kwh": imported,
                "local_solar_use_kwh": round(local_use, 3) if local_use is not None else None,
                "local_use_share_percent": round(local_share, 1) if local_share is not None else None,
                "strongest_solar_day": {
                    "date": str(strongest.get("date") or "")[:10],
                    "kwh": round(max(0.0, float(strongest.get("solar_energy_kwh") or 0)), 3),
                },
            }

        latest = measured[-1:] if measured else []
        rolling_week = [row for row in measured if (day := row_day(row)) is not None and day >= today - timedelta(days=7)]
        current_month = [row for row in measured if (day := row_day(row)) is not None and day.year == today.year and day.month == today.month]
        current_year = [row for row in measured if (day := row_day(row)) is not None and day.year == today.year]

        trend_rows = measured[-6:]
        comparison: dict[str, Any] = {
            "available": False,
            "required_completed_days": 6,
            "completed_day_count": len(trend_rows),
            "evidence_period": "Latest 3 completed measured solar days compared with the previous 3",
            "evidence_boundary": "Solar energy trends describe measured production and energy routing. They do not establish weather, shading, curtailment, equipment health, degradation or a fault.",
        }

        if len(trend_rows) >= 6:
            previous, recent = trend_rows[-6:-3], trend_rows[-3:]

            def avg(source: list[dict[str, Any]], key: str) -> float | None:
                values = [max(0.0, float(row.get(key) or 0)) for row in source if row.get(key) is not None]
                return sum(values) / len(values) if values else None

            def total(source: list[dict[str, Any]], key: str) -> float | None:
                values = [max(0.0, float(row.get(key) or 0)) for row in source if row.get(key) is not None]
                return sum(values) if values else None

            previous_avg = avg(previous, "solar_energy_kwh")
            recent_avg = avg(recent, "solar_energy_kwh")
            production_change = ((recent_avg - previous_avg) / previous_avg * 100.0) if previous_avg and recent_avg is not None else None

            def local_share(source: list[dict[str, Any]]) -> float | None:
                solar = total(source, "solar_energy_kwh")
                exported = total(source, "grid_export_energy_kwh")
                if solar is None or exported is None or solar <= 0:
                    return None
                return max(0.0, solar - exported) / solar * 100.0

            previous_share, recent_share = local_share(previous), local_share(recent)
            share_change = recent_share - previous_share if previous_share is not None and recent_share is not None else None

            def direction(value: float | None, stable_band: float) -> str:
                if value is None or abs(value) < stable_band:
                    return "Stable"
                return "Rising" if value > 0 else "Falling"

            production_direction = direction(production_change, 5.0)
            share_direction = direction(share_change, 2.0)
            if production_change is None:
                headline = "Solar production comparison is available"
            elif production_direction == "Stable":
                headline = "Solar production is broadly stable"
            else:
                headline = f"Solar production is {production_direction.lower()} {abs(production_change):.1f}%"

            routing_text = "Local solar-use share could not be compared because export evidence is incomplete."
            if share_change is not None:
                routing_text = (
                    f"Local solar-use share is {share_direction.lower()} by {abs(share_change):.1f} percentage points."
                    if share_direction != "Stable" else
                    "Local solar-use share is broadly stable."
                )
            comparison.update({
                "available": True,
                "previous_avg_solar_kwh": round(previous_avg, 3) if previous_avg is not None else None,
                "recent_avg_solar_kwh": round(recent_avg, 3) if recent_avg is not None else None,
                "production_change_percent": round(production_change, 1) if production_change is not None else None,
                "production_direction": production_direction,
                "previous_local_use_share_percent": round(previous_share, 1) if previous_share is not None else None,
                "recent_local_use_share_percent": round(recent_share, 1) if recent_share is not None else None,
                "local_use_share_change_points": round(share_change, 1) if share_change is not None else None,
                "local_use_share_direction": share_direction,
                "headline": headline,
                "interpretation": f"Average daily solar production moved from {previous_avg:.2f} kWh to {recent_avg:.2f} kWh. {routing_text}" if previous_avg is not None and recent_avg is not None else routing_text,
            })

        pattern_rows = measured[-30:]
        pattern: dict[str, Any] = {
            "available": False,
            "required_completed_days": 7,
            "completed_day_count": len(pattern_rows),
            "evidence_period": "Latest up to 30 completed measured solar days",
            "evidence_boundary": "Production variability and relative strong/weak days describe measured output only. They do not establish weather, shading, curtailment, clipping, equipment health, degradation or a fault.",
        }
        if len(pattern_rows) >= 7:
            values = [max(0.0, float(row.get("solar_energy_kwh") or 0)) for row in pattern_rows]
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            stddev = variance ** 0.5
            cv = (stddev / mean * 100.0) if mean > 0 else None
            ordered = sorted(values)
            median = ordered[len(ordered)//2] if len(ordered)%2 else (ordered[len(ordered)//2-1]+ordered[len(ordered)//2])/2
            strongest = max(pattern_rows, key=lambda row: max(0.0, float(row.get("solar_energy_kwh") or 0)))
            weakest = min(pattern_rows, key=lambda row: max(0.0, float(row.get("solar_energy_kwh") or 0)))
            strong_threshold = mean * 1.20
            weak_threshold = mean * 0.80
            strong_days = sum(1 for value in values if value >= strong_threshold)
            weak_days = sum(1 for value in values if value <= weak_threshold)
            if cv is None:
                consistency = "Unclassified"
            elif cv < 15.0:
                consistency = "Consistent"
            elif cv < 30.0:
                consistency = "Variable"
            else:
                consistency = "Highly variable"
            pattern.update({
                "available": True,
                "average_solar_kwh": round(mean, 3),
                "median_solar_kwh": round(median, 3),
                "standard_deviation_kwh": round(stddev, 3),
                "coefficient_of_variation_percent": round(cv, 1) if cv is not None else None,
                "consistency": consistency,
                "strong_day_count": strong_days,
                "weak_day_count": weak_days,
                "relative_threshold_percent": 20.0,
                "strongest_day": {"date": str(strongest.get("date") or "")[:10], "kwh": round(max(0.0, float(strongest.get("solar_energy_kwh") or 0)), 3)},
                "weakest_day": {"date": str(weakest.get("date") or "")[:10], "kwh": round(max(0.0, float(weakest.get("solar_energy_kwh") or 0)), 3)},
                "headline": f"Measured solar production is {consistency.lower()}",
                "interpretation": f"Across {len(values)} completed measured days, daily solar production averaged {mean:.2f} kWh with {cv:.1f}% relative variability. {strong_days} day(s) were at least 20% above the window average and {weak_days} day(s) were at least 20% below it." if cv is not None else "Measured solar production pattern is available.",
            })

        utilization_rows = [
            row for row in measured[-30:]
            if row.get("grid_export_energy_kwh") is not None
        ]
        utilization: dict[str, Any] = {
            "available": False,
            "required_completed_days": 7,
            "completed_day_count": len(utilization_rows),
            "evidence_period": "Latest up to 30 completed measured solar days with export evidence",
            "evidence_boundary": "Solar utilization describes measured daily energy routing. It does not establish instantaneous power flow, appliance-level use, weather, curtailment, equipment health or a fault.",
        }
        if len(utilization_rows) >= 7:
            solar_total = sum(max(0.0, float(row.get("solar_energy_kwh") or 0)) for row in utilization_rows)
            export_total = sum(max(0.0, float(row.get("grid_export_energy_kwh") or 0)) for row in utilization_rows)
            local_total = max(0.0, solar_total - export_total)
            local_share = local_total / solar_total * 100.0 if solar_total > 0 else None
            export_share = export_total / solar_total * 100.0 if solar_total > 0 else None

            daily = []
            for row in utilization_rows:
                solar_day = max(0.0, float(row.get("solar_energy_kwh") or 0))
                export_day = max(0.0, float(row.get("grid_export_energy_kwh") or 0))
                local_day = max(0.0, solar_day - export_day)
                local_day_share = local_day / solar_day * 100.0 if solar_day > 0 else None
                export_day_share = export_day / solar_day * 100.0 if solar_day > 0 else None
                daily.append({
                    "date": str(row.get("date") or "")[:10],
                    "solar_kwh": solar_day,
                    "local_kwh": local_day,
                    "export_kwh": export_day,
                    "local_share_percent": local_day_share,
                    "export_share_percent": export_day_share,
                })

            export_heavy_days = sum(1 for row in daily if row["export_share_percent"] is not None and row["export_share_percent"] >= 50.0)
            local_heavy_days = sum(1 for row in daily if row["local_share_percent"] is not None and row["local_share_percent"] >= 50.0)
            strongest_export = max(daily, key=lambda row: row["export_kwh"])
            strongest_local = max(daily, key=lambda row: row["local_kwh"])

            if local_share is None:
                routing_profile = "Unclassified"
            elif local_share >= 65.0:
                routing_profile = "Local-use dominant"
            elif local_share >= 35.0:
                routing_profile = "Mixed routing"
            else:
                routing_profile = "Export dominant"

            utilization.update({
                "available": True,
                "routing_profile": routing_profile,
                "solar_energy_kwh": round(solar_total, 3),
                "local_solar_use_kwh": round(local_total, 3),
                "grid_export_energy_kwh": round(export_total, 3),
                "local_use_share_percent": round(local_share, 1) if local_share is not None else None,
                "export_share_percent": round(export_share, 1) if export_share is not None else None,
                "export_heavy_day_count": export_heavy_days,
                "local_use_heavy_day_count": local_heavy_days,
                "strongest_export_day": {
                    "date": strongest_export["date"],
                    "kwh": round(strongest_export["export_kwh"], 3),
                    "share_percent": round(strongest_export["export_share_percent"], 1) if strongest_export["export_share_percent"] is not None else None,
                },
                "strongest_local_use_day": {
                    "date": strongest_local["date"],
                    "kwh": round(strongest_local["local_kwh"], 3),
                    "share_percent": round(strongest_local["local_share_percent"], 1) if strongest_local["local_share_percent"] is not None else None,
                },
                "headline": f"Solar routing is {routing_profile.lower()}",
                "interpretation": (
                    f"Across {len(daily)} completed measured days with export evidence, "
                    f"{local_share:.1f}% of measured solar remained behind the meter and "
                    f"{export_share:.1f}% was exported. {export_heavy_days} day(s) were export-heavy "
                    f"and {local_heavy_days} day(s) were local-use-heavy."
                ) if local_share is not None and export_share is not None else
                "Measured solar routing is available.",
            })

        opportunity_rows = [
            row for row in measured[-30:]
            if row.get("grid_export_energy_kwh") is not None
        ]
        opportunity: dict[str, Any] = {
            "available": False,
            "required_completed_days": 7,
            "completed_day_count": len(opportunity_rows),
            "evidence_period": "Latest up to 30 completed measured solar days with export evidence",
            "evidence_boundary": "This is a measured opportunity signal, not proof that flexible demand exists, that loads can be shifted, or that changing operation would improve cost, comfort or equipment performance.",
        }
        if len(opportunity_rows) >= 7:
            solar_values = [max(0.0, float(row.get("solar_energy_kwh") or 0)) for row in opportunity_rows]
            avg_solar = sum(solar_values) / len(solar_values)
            high_threshold = avg_solar * 1.20
            low_threshold = avg_solar * 0.80

            enriched = []
            for row in opportunity_rows:
                solar_day = max(0.0, float(row.get("solar_energy_kwh") or 0))
                export_day = max(0.0, float(row.get("grid_export_energy_kwh") or 0))
                local_day = max(0.0, solar_day - export_day)
                export_share_day = export_day / solar_day * 100.0 if solar_day > 0 else None
                local_share_day = local_day / solar_day * 100.0 if solar_day > 0 else None
                enriched.append({
                    "date": str(row.get("date") or "")[:10],
                    "solar_kwh": solar_day,
                    "export_kwh": export_day,
                    "local_kwh": local_day,
                    "export_share_percent": export_share_day,
                    "local_share_percent": local_share_day,
                })

            high_export_days = [
                row for row in enriched
                if row["solar_kwh"] >= high_threshold
                and row["export_share_percent"] is not None
                and row["export_share_percent"] >= 50.0
            ]
            lower_local_days = [
                row for row in enriched
                if row["solar_kwh"] <= low_threshold
                and row["local_share_percent"] is not None
                and row["local_share_percent"] >= 50.0
            ]
            solar_total = sum(row["solar_kwh"] for row in enriched)
            export_total = sum(row["export_kwh"] for row in enriched)
            export_share = export_total / solar_total * 100.0 if solar_total > 0 else None

            if export_share is None:
                signal = "Unclassified"
            elif export_share >= 60.0 and len(high_export_days) >= max(2, round(len(enriched) * 0.15)):
                signal = "Strong"
            elif export_share >= 35.0 or len(high_export_days) >= 2:
                signal = "Moderate"
            else:
                signal = "Limited"

            if signal == "Strong":
                headline = "Frequent measured solar surplus deserves planning attention"
                interpretation = (
                    f"{len(high_export_days)} high-production day(s) were also export-heavy in the evidence window, "
                    f"while {export_share:.1f}% of measured solar energy was exported overall. "
                    "This identifies a strong measured surplus pattern that Zeus can use for recommendation planning."
                )
            elif signal == "Moderate":
                headline = "Measured solar surplus creates a planning opportunity"
                interpretation = (
                    f"{len(high_export_days)} high-production day(s) were also export-heavy, and "
                    f"{export_share:.1f}% of measured solar energy was exported overall. "
                    "The pattern is meaningful enough to consider in recommendation planning."
                )
            else:
                headline = "Measured solar surplus opportunity is limited"
                interpretation = (
                    f"{len(high_export_days)} high-production day(s) were also export-heavy, and "
                    f"{export_share:.1f}% of measured solar energy was exported overall. "
                    "The current evidence does not show a strong recurring surplus pattern."
                ) if export_share is not None else "The current evidence is insufficient to characterize a recurring surplus opportunity."

            opportunity.update({
                "available": True,
                "signal": signal,
                "average_solar_kwh": round(avg_solar, 3),
                "high_production_threshold_kwh": round(high_threshold, 3),
                "low_production_threshold_kwh": round(low_threshold, 3),
                "high_production_export_heavy_day_count": len(high_export_days),
                "lower_production_local_use_heavy_day_count": len(lower_local_days),
                "measured_export_kwh": round(export_total, 3),
                "measured_export_share_percent": round(export_share, 1) if export_share is not None else None,
                "headline": headline,
                "interpretation": interpretation,
                "planning_note": "Zeus may use this measured pattern to prioritize recommendation-only ideas. It does not control equipment and does not assume any specific load is flexible.",
            })

        return {
            "status": (self.last or {}).get("status"),
            "periods": {
                "today": aggregate(latest, "Latest completed measured solar day"),
                "week": aggregate(rolling_week, "Latest 7 completed calendar days"),
                "month": aggregate(current_month, "Current month · completed measured days"),
                "year": aggregate(current_year, "Current year · completed measured days"),
            },
            "comparison": comparison,
            "pattern": pattern,
            "utilization": utilization,
            "opportunity": opportunity,
            "current_partial_day_excluded": True,
            "missing_evidence_is_not_zero": True,
            "source": "canonical_daily_history",
            "recorder_safe": True,
        }

    def recorder_consumption_intelligence_evidence(self) -> dict[str, Any]:
        """Recorder-safe completed-day evidence for Consumption Intelligence.

        Uses canonical whole-home measured daily history.  The current partial
        local day is excluded and missing consumption is never converted into
        measured zero.  Whole-home energy patterns do not identify which device
        or behavior caused a change.
        """
        rows = self._explorer_rows(days=90)
        measured = [row for row in rows if row.get("house_energy_kwh") is not None]

        def total(source: list[dict[str, Any]], key: str) -> float | None:
            values = [max(0.0, float(row.get(key) or 0)) for row in source if row.get(key) is not None]
            return sum(values) if values else None

        recent_rows = measured[-30:]
        summary: dict[str, Any] = {
            "available": False,
            "required_completed_days": 7,
            "completed_day_count": len(recent_rows),
            "evidence_period": "Latest up to 30 completed measured consumption days",
            "evidence_boundary": "Whole-home consumption measurements describe demand patterns only. They do not identify which device, occupant behavior or external condition caused a change.",
        }
        if len(recent_rows) >= 7:
            values = [max(0.0, float(row.get("house_energy_kwh") or 0)) for row in recent_rows]
            mean = sum(values) / len(values)
            ordered = sorted(values)
            median = ordered[len(ordered)//2] if len(ordered)%2 else (ordered[len(ordered)//2-1]+ordered[len(ordered)//2])/2
            variance = sum((value-mean)**2 for value in values)/len(values)
            stddev = variance**0.5
            cv = stddev/mean*100.0 if mean>0 else None
            strongest = max(recent_rows,key=lambda row:max(0.0,float(row.get("house_energy_kwh") or 0)))
            lowest = min(recent_rows,key=lambda row:max(0.0,float(row.get("house_energy_kwh") or 0)))
            if cv is None:
                consistency="Unclassified"
            elif cv<12.0:
                consistency="Consistent"
            elif cv<25.0:
                consistency="Variable"
            else:
                consistency="Highly variable"

            import_total=total(recent_rows,"grid_import_energy_kwh")
            solar_total=total(recent_rows,"solar_energy_kwh")
            export_total=total(recent_rows,"grid_export_energy_kwh")
            battery_discharge=total(recent_rows,"battery_discharge_energy_kwh")
            consumption_total=sum(values)
            grid_share=(import_total/consumption_total*100.0) if import_total is not None and consumption_total>0 else None
            local_coverage=(max(0.0,consumption_total-import_total)/consumption_total*100.0) if import_total is not None and consumption_total>0 else None

            summary.update({
                "available":True,
                "consumption_energy_kwh":round(consumption_total,3),
                "average_daily_consumption_kwh":round(mean,3),
                "median_daily_consumption_kwh":round(median,3),
                "standard_deviation_kwh":round(stddev,3),
                "coefficient_of_variation_percent":round(cv,1) if cv is not None else None,
                "consistency":consistency,
                "strongest_day":{"date":str(strongest.get("date") or "")[:10],"kwh":round(max(0.0,float(strongest.get("house_energy_kwh") or 0)),3)},
                "lowest_day":{"date":str(lowest.get("date") or "")[:10],"kwh":round(max(0.0,float(lowest.get("house_energy_kwh") or 0)),3)},
                "grid_import_kwh":round(import_total,3) if import_total is not None else None,
                "grid_dependence_percent":round(grid_share,1) if grid_share is not None else None,
                "local_coverage_percent":round(local_coverage,1) if local_coverage is not None else None,
                "solar_energy_kwh":round(solar_total,3) if solar_total is not None else None,
                "grid_export_energy_kwh":round(export_total,3) if export_total is not None else None,
                "battery_discharge_kwh":round(battery_discharge,3) if battery_discharge is not None else None,
            })

        trend_rows=measured[-6:]
        trend: dict[str, Any]={
            "available":False,
            "required_completed_days":6,
            "completed_day_count":len(trend_rows),
            "evidence_period":"Latest 3 completed measured consumption days compared with the previous 3",
            "evidence_boundary":"Consumption trends describe measured whole-home demand. They do not identify which load or behavior caused the change.",
        }
        if len(trend_rows)>=6:
            previous,recent=trend_rows[-6:-3],trend_rows[-3:]
            prev_avg=sum(max(0.0,float(row.get("house_energy_kwh") or 0)) for row in previous)/3
            recent_avg=sum(max(0.0,float(row.get("house_energy_kwh") or 0)) for row in recent)/3
            change=((recent_avg-prev_avg)/prev_avg*100.0) if prev_avg>0 else None

            def grid_share(source:list[dict[str,Any]])->float|None:
                home=total(source,"house_energy_kwh")
                imported=total(source,"grid_import_energy_kwh")
                return imported/home*100.0 if home and imported is not None else None

            prev_grid=grid_share(previous); recent_grid=grid_share(recent)
            grid_change=(recent_grid-prev_grid) if prev_grid is not None and recent_grid is not None else None
            direction="Stable" if change is None or abs(change)<5.0 else ("Rising" if change>0 else "Falling")
            grid_direction="Stable" if grid_change is None or abs(grid_change)<2.0 else ("Rising" if grid_change>0 else "Falling")
            if change is None:
                headline="Consumption comparison is available"
            elif direction=="Stable":
                headline="Household consumption is broadly stable"
            else:
                headline=f"Household consumption is {direction.lower()} {abs(change):.1f}%"
            grid_text="Grid-dependence change could not be measured from the available evidence."
            if grid_change is not None:
                grid_text="Grid dependence is broadly stable." if grid_direction=="Stable" else f"Grid dependence is {grid_direction.lower()} by {abs(grid_change):.1f} percentage points."
            trend.update({
                "available":True,
                "previous_avg_consumption_kwh":round(prev_avg,3),
                "recent_avg_consumption_kwh":round(recent_avg,3),
                "consumption_change_percent":round(change,1) if change is not None else None,
                "consumption_direction":direction,
                "previous_grid_dependence_percent":round(prev_grid,1) if prev_grid is not None else None,
                "recent_grid_dependence_percent":round(recent_grid,1) if recent_grid is not None else None,
                "grid_dependence_change_points":round(grid_change,1) if grid_change is not None else None,
                "grid_dependence_direction":grid_direction,
                "headline":headline,
                "interpretation":f"Average daily household consumption moved from {prev_avg:.2f} kWh to {recent_avg:.2f} kWh. {grid_text}",
            })

        pattern_rows=measured[-30:]
        pattern: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(pattern_rows),
            "evidence_period":"Latest up to 30 completed measured consumption days",
            "evidence_boundary":"Relative high/low demand days describe whole-home energy use only. They do not identify which appliance, behavior or external condition caused the pattern.",
        }
        if len(pattern_rows)>=7:
            values=[max(0.0,float(row.get("house_energy_kwh") or 0)) for row in pattern_rows]
            mean=sum(values)/len(values)
            high_threshold=mean*1.20
            low_threshold=mean*0.80
            high_days=[row for row in pattern_rows if max(0.0,float(row.get("house_energy_kwh") or 0))>=high_threshold]
            low_days=[row for row in pattern_rows if max(0.0,float(row.get("house_energy_kwh") or 0))<=low_threshold]
            recent7=pattern_rows[-7:]
            recent7_high=sum(1 for row in recent7 if max(0.0,float(row.get("house_energy_kwh") or 0))>=high_threshold)
            recent7_low=sum(1 for row in recent7 if max(0.0,float(row.get("house_energy_kwh") or 0))<=low_threshold)

            # Longest streaks use only consecutive measured calendar days.
            def longest_streak(source:list[dict[str,Any]], predicate)->int:
                best=cur=0
                previous_day=None
                for row in source:
                    try:
                        day=datetime.fromisoformat(str(row.get("date") or "")[:10]).date()
                    except (TypeError,ValueError):
                        day=None
                    consecutive=previous_day is not None and day is not None and (day-previous_day).days==1
                    if predicate(row):
                        cur=cur+1 if consecutive else 1
                        best=max(best,cur)
                    else:
                        cur=0
                    previous_day=day
                return best

            high_streak=longest_streak(pattern_rows,lambda row:max(0.0,float(row.get("house_energy_kwh") or 0))>=high_threshold)
            low_streak=longest_streak(pattern_rows,lambda row:max(0.0,float(row.get("house_energy_kwh") or 0))<=low_threshold)

            high_ratio=len(high_days)/len(pattern_rows)
            if high_ratio>=0.35 or recent7_high>=3:
                pressure="Elevated"
                headline="Repeated high-demand days deserve attention"
            elif high_ratio>=0.18 or recent7_high>=2:
                pressure="Moderate"
                headline="Household demand shows recurring higher-use days"
            else:
                pressure="Limited"
                headline="Recurring high-demand pressure is limited"

            interpretation=(
                f"{len(high_days)} of {len(pattern_rows)} completed measured days were at least 20% above "
                f"the evidence-window average of {mean:.2f} kWh, while {len(low_days)} day(s) were at least "
                f"20% below it. In the latest 7 completed measured days, {recent7_high} were relatively high-demand."
            )
            pattern.update({
                "available":True,
                "pressure":pressure,
                "average_consumption_kwh":round(mean,3),
                "high_demand_threshold_kwh":round(high_threshold,3),
                "low_demand_threshold_kwh":round(low_threshold,3),
                "high_demand_day_count":len(high_days),
                "low_demand_day_count":len(low_days),
                "recent7_high_demand_day_count":recent7_high,
                "recent7_low_demand_day_count":recent7_low,
                "longest_high_demand_streak_days":high_streak,
                "longest_low_demand_streak_days":low_streak,
                "headline":headline,
                "interpretation":interpretation,
                "relative_threshold_percent":20.0,
            })

        dependency_rows=[
            row for row in measured[-30:]
            if row.get("grid_import_energy_kwh") is not None
        ]
        grid_dependency: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(dependency_rows),
            "evidence_period":"Latest up to 30 completed measured consumption days with grid-import evidence",
            "evidence_boundary":"Grid-dependency patterns describe measured whole-home demand and imported energy. They do not identify which appliance, behavior or external condition caused grid use.",
        }
        if len(dependency_rows)>=7:
            home_values=[max(0.0,float(row.get("house_energy_kwh") or 0)) for row in dependency_rows]
            home_total=sum(home_values)
            import_values=[max(0.0,float(row.get("grid_import_energy_kwh") or 0)) for row in dependency_rows]
            import_total=sum(import_values)
            overall_share=import_total/home_total*100.0 if home_total>0 else None
            average_home=home_total/len(dependency_rows)
            high_threshold=average_home*1.20
            low_threshold=average_home*0.80

            def share(row:dict[str,Any])->float|None:
                home=max(0.0,float(row.get("house_energy_kwh") or 0))
                imported=max(0.0,float(row.get("grid_import_energy_kwh") or 0))
                return imported/home*100.0 if home>0 else None

            daily=[(row,share(row)) for row in dependency_rows]
            daily=[item for item in daily if item[1] is not None]
            grid_heavy=[item for item in daily if item[1]>=50.0]
            local_heavy=[item for item in daily if item[1]<20.0]
            high_demand_grid_heavy=[
                item for item in daily
                if max(0.0,float(item[0].get("house_energy_kwh") or 0))>=high_threshold and item[1]>=50.0
            ]
            low_demand_local_heavy=[
                item for item in daily
                if max(0.0,float(item[0].get("house_energy_kwh") or 0))<=low_threshold and item[1]<20.0
            ]
            strongest=max(
                daily,
                key=lambda item:max(0.0,float(item[0].get("grid_import_energy_kwh") or 0)),
                default=None,
            )

            recent=dependency_rows[-7:]
            recent_home=sum(max(0.0,float(row.get("house_energy_kwh") or 0)) for row in recent)
            recent_import=sum(max(0.0,float(row.get("grid_import_energy_kwh") or 0)) for row in recent)
            recent_share=recent_import/recent_home*100.0 if recent_home>0 else None

            if overall_share is None:
                profile="Unclassified"
                headline="Grid-dependency evidence is incomplete"
            elif overall_share>=50.0:
                profile="Grid dominant"
                headline="Household demand is grid dependent"
            elif overall_share>=20.0:
                profile="Mixed"
                headline="Household demand uses mixed grid and non-grid supply"
            else:
                profile="Low grid dependence"
                headline="Most measured household demand was supplied without grid import"

            interpretation=(
                f"Across {len(dependency_rows)} completed measured days, grid import supplied "
                f"{overall_share:.1f}% of measured household consumption."
            ) if overall_share is not None else "Measured grid-dependency share could not be calculated."
            if high_demand_grid_heavy:
                interpretation+=f" {len(high_demand_grid_heavy)} relatively high-demand day(s) were also grid-heavy."
            elif grid_heavy:
                interpretation+=f" {len(grid_heavy)} day(s) were grid-heavy, but none were also relatively high-demand under this window's rule."
            else:
                interpretation+=" No completed day was grid-heavy under the 50% daily-import-share rule."

            strongest_obj=None
            if strongest:
                row,day_share=strongest
                strongest_obj={
                    "date":str(row.get("date") or "")[:10],
                    "grid_import_kwh":round(max(0.0,float(row.get("grid_import_energy_kwh") or 0)),3),
                    "consumption_kwh":round(max(0.0,float(row.get("house_energy_kwh") or 0)),3),
                    "grid_share_percent":round(day_share,1),
                }

            grid_dependency.update({
                "available":True,
                "profile":profile,
                "headline":headline,
                "interpretation":interpretation,
                "consumption_energy_kwh":round(home_total,3),
                "grid_import_kwh":round(import_total,3),
                "grid_dependence_percent":round(overall_share,1) if overall_share is not None else None,
                "non_grid_coverage_percent":round(max(0.0,100.0-overall_share),1) if overall_share is not None else None,
                "grid_heavy_day_count":len(grid_heavy),
                "local_heavy_day_count":len(local_heavy),
                "high_demand_grid_heavy_day_count":len(high_demand_grid_heavy),
                "low_demand_local_heavy_day_count":len(low_demand_local_heavy),
                "recent7_grid_dependence_percent":round(recent_share,1) if recent_share is not None else None,
                "strongest_grid_import_day":strongest_obj,
                "grid_heavy_rule_percent":50.0,
                "local_heavy_rule_percent":20.0,
                "relative_demand_threshold_percent":20.0,
            })

        opportunity: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "evidence_period":"Latest up to 30 completed measured consumption days with grid-import evidence",
            "evidence_boundary":"This is a measured whole-home planning signal. It does not identify a device-level cause, prove that flexible demand exists, or establish that shifting demand would improve cost, comfort or equipment performance.",
        }
        if grid_dependency.get("available") and pattern.get("available"):
            high_grid=max(0,int(grid_dependency.get("high_demand_grid_heavy_day_count") or 0))
            grid_heavy=max(0,int(grid_dependency.get("grid_heavy_day_count") or 0))
            recent_high=max(0,int(pattern.get("recent7_high_demand_day_count") or 0))
            dependence=grid_dependency.get("grid_dependence_percent")
            recent_dependence=grid_dependency.get("recent7_grid_dependence_percent")
            trend_available=bool(trend.get("available"))
            trend_direction=str(trend.get("consumption_direction") or "").lower()
            trend_change=trend.get("consumption_change_percent")
            recent_pressure=(
                trend_available and trend_direction=="rising"
                and trend_change is not None and float(trend_change)>=10.0
            )

            score=0
            reasons=[]
            if high_grid>=2:
                score+=2
                reasons.append(f"{high_grid} relatively high-demand day(s) were also grid-heavy")
            elif high_grid==1:
                score+=1
                reasons.append("1 relatively high-demand day was also grid-heavy")
            if recent_high>=3:
                score+=1
                reasons.append(f"{recent_high} of the latest 7 completed days were relatively high-demand")
            if recent_pressure:
                score+=1
                reasons.append(f"average daily consumption is rising by {float(trend_change):.1f}% in the recent comparison")
            if recent_dependence is not None and float(recent_dependence)>=20.0:
                score+=1
                reasons.append(f"latest-7 grid dependency is {float(recent_dependence):.1f}%")

            if score>=4:
                signal="Strong"
                headline="Measured demand pressure deserves planning attention"
            elif score>=2:
                signal="Moderate"
                headline="Measured demand patterns show a planning opportunity"
            else:
                signal="Limited"
                headline="No strong consumption planning opportunity is established"

            if reasons:
                interpretation="; ".join(reasons[:3])+"."
            else:
                interpretation="Completed measured demand does not currently show a strong combination of repeated high demand and grid dependency."
            interpretation+=" Zeus may use this measured pattern to prioritize recommendation-only ideas without assuming any specific load is flexible."

            opportunity.update({
                "available":True,
                "signal":signal,
                "score":score,
                "headline":headline,
                "interpretation":interpretation,
                "high_demand_grid_heavy_day_count":high_grid,
                "grid_heavy_day_count":grid_heavy,
                "recent7_high_demand_day_count":recent_high,
                "grid_dependence_percent":dependence,
                "recent7_grid_dependence_percent":recent_dependence,
                "recent_demand_pressure":recent_pressure,
                "recent_consumption_change_percent":round(float(trend_change),1) if trend_change is not None else None,
                "planning_note":"Zeus may use this measured whole-home pattern to prioritize recommendation-only ideas. It does not control equipment and does not assume that any specific load can be shifted.",
            })

        flexibility: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "evidence_period":"Latest up to 30 completed measured consumption days with grid-import evidence",
            "evidence_boundary":"Flexibility Intelligence describes measured whole-home demand conditions that may deserve planning review. It does not identify flexible appliances, prove that demand can be shifted, or establish that shifting demand would improve cost, comfort or equipment performance.",
        }
        if pattern.get("available") and grid_dependency.get("available"):
            high_days=max(0,int(pattern.get("high_demand_day_count") or 0))
            low_days=max(0,int(pattern.get("low_demand_day_count") or 0))
            recent_high=max(0,int(pattern.get("recent7_high_demand_day_count") or 0))
            high_grid=max(0,int(grid_dependency.get("high_demand_grid_heavy_day_count") or 0))
            grid_heavy=max(0,int(grid_dependency.get("grid_heavy_day_count") or 0))
            dependence=grid_dependency.get("grid_dependence_percent")
            recent_dependence=grid_dependency.get("recent7_grid_dependence_percent")
            variability=summary.get("coefficient_of_variation_percent")
            trend_direction=str(trend.get("consumption_direction") or "").lower()
            trend_change=trend.get("consumption_change_percent")

            # This is deliberately an evidence-of-opportunity classification, not a claim
            # that any appliance is controllable or that load shifting will succeed.
            score=0
            signals=[]
            if high_days>=5:
                score+=1
                signals.append(f"{high_days} relatively high-demand day(s) occurred in the evidence window")
            if low_days>=5:
                score+=1
                signals.append(f"{low_days} relatively low-demand day(s) show that whole-home demand varied materially between days")
            if variability is not None and float(variability)>=25.0:
                score+=1
                signals.append(f"whole-home demand variability is {float(variability):.1f}%")
            if high_grid>=2:
                score+=2
                signals.append(f"{high_grid} relatively high-demand day(s) were also grid-heavy")
            elif high_grid==1:
                score+=1
                signals.append("1 relatively high-demand day was also grid-heavy")
            if recent_high>=3:
                score+=1
                signals.append(f"{recent_high} of the latest 7 completed days were relatively high-demand")
            if trend_direction=="rising" and trend_change is not None and float(trend_change)>=10.0:
                score+=1
                signals.append(f"recent average daily consumption is rising by {float(trend_change):.1f}%")

            if score>=5:
                signal="Strong"
                headline="Measured demand conditions deserve flexibility planning review"
            elif score>=3:
                signal="Moderate"
                headline="Measured demand conditions show possible flexibility"
            else:
                signal="Limited"
                headline="No strong flexibility signal is established"

            if signals:
                interpretation="; ".join(signals[:3])+"."
            else:
                interpretation="Completed measured demand does not currently show a strong combination of variability, repeated high demand and grid-heavy overlap."
            interpretation+=" This is evidence for recommendation planning only; it does not prove that any specific load can be shifted."

            flexibility.update({
                "available":True,
                "signal":signal,
                "score":score,
                "headline":headline,
                "interpretation":interpretation,
                "high_demand_day_count":high_days,
                "low_demand_day_count":low_days,
                "recent7_high_demand_day_count":recent_high,
                "high_demand_grid_heavy_day_count":high_grid,
                "grid_heavy_day_count":grid_heavy,
                "variability_percent":round(float(variability),1) if variability is not None else None,
                "grid_dependence_percent":round(float(dependence),1) if dependence is not None else None,
                "recent7_grid_dependence_percent":round(float(recent_dependence),1) if recent_dependence is not None else None,
                "planning_note":"Zeus may use these measured whole-home conditions to prioritize recommendation-only flexibility ideas. No appliance is assumed flexible and Zeus does not control equipment.",
            })

        timing_rows=list(getattr(self, "_ha_consumption_hourly", []) or [])
        timing_days=sorted({
            str(row.get("start") or "")[:10]
            for row in timing_rows
            if row.get("house_energy_kwh") is not None and row.get("start")
        })
        timing: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(timing_days),
            "hour_count":len(timing_rows),
            "evidence_period":"Latest up to 30 completed days of canonical hourly consumption statistics",
            "evidence_boundary":"Timing Intelligence describes when measured whole-home energy was consumed. It does not identify which appliance caused demand, prove that a load is flexible, or establish that moving demand to another time would improve cost, comfort or equipment performance.",
            "source":dict(getattr(self, "_ha_consumption_hourly_status", {}) or {}).get("source"),
        }
        if len(timing_days)>=7 and timing_rows:
            windows={
                "Night · 00–06":{"start":0,"end":6,"house":0.0,"grid":0.0,"solar":0.0,"hours":0},
                "Morning · 06–12":{"start":6,"end":12,"house":0.0,"grid":0.0,"solar":0.0,"hours":0},
                "Afternoon · 12–18":{"start":12,"end":18,"house":0.0,"grid":0.0,"solar":0.0,"hours":0},
                "Evening · 18–24":{"start":18,"end":24,"house":0.0,"grid":0.0,"solar":0.0,"hours":0},
            }
            total_house=0.0
            solar_active_house=0.0
            solar_active_hours=0
            for row in timing_rows:
                try:
                    stamp=datetime.fromisoformat(str(row.get("start") or ""))
                    hour=stamp.hour
                except (TypeError,ValueError):
                    continue
                house=max(0.0,float(row.get("house_energy_kwh") or 0))
                grid=max(0.0,float(row.get("grid_import_energy_kwh") or 0)) if row.get("grid_import_energy_kwh") is not None else None
                solar=max(0.0,float(row.get("solar_energy_kwh") or 0)) if row.get("solar_energy_kwh") is not None else None
                total_house+=house
                for label,data in windows.items():
                    if data["start"]<=hour<data["end"]:
                        data["house"]+=house
                        if grid is not None:
                            data["grid"]+=grid
                        if solar is not None:
                            data["solar"]+=solar
                        data["hours"]+=1
                        break
                if solar is not None and solar>0.05:
                    solar_active_house+=house
                    solar_active_hours+=1

            window_rows=[]
            for label,data in windows.items():
                share=(data["house"]/total_house*100.0) if total_house>0 else None
                grid_ratio=(data["grid"]/data["house"]*100.0) if data["house"]>0 and data["grid"]>=0 else None
                window_rows.append({
                    "label":label,
                    "consumption_kwh":round(data["house"],3),
                    "consumption_share_percent":round(share,1) if share is not None else None,
                    "grid_import_kwh":round(data["grid"],3),
                    "grid_import_ratio_percent":round(grid_ratio,1) if grid_ratio is not None else None,
                    "sample_hour_count":data["hours"],
                })

            peak=max(window_rows,key=lambda row:row["consumption_kwh"])
            lowest=min(window_rows,key=lambda row:row["consumption_kwh"])
            evening=next(row for row in window_rows if row["label"].startswith("Evening"))
            solar_active_share=(solar_active_house/total_house*100.0) if total_house>0 and solar_active_hours else None

            peak_share=float(peak.get("consumption_share_percent") or 0)
            if peak_share>=35.0:
                profile=f"{peak['label'].split(' · ')[0]} concentrated"
            elif max(float(row.get("consumption_share_percent") or 0) for row in window_rows)<=30.0:
                profile="Balanced across the day"
            else:
                profile=f"{peak['label'].split(' · ')[0]} weighted"

            timing.update({
                "available":True,
                "profile":profile,
                "headline":f"Household demand is {profile.lower()}",
                "interpretation":(
                    f"{peak['label']} carried the largest measured share of household consumption at "
                    f"{peak_share:.1f}%, while {lowest['label']} carried the smallest share at "
                    f"{float(lowest.get('consumption_share_percent') or 0):.1f}%."
                ),
                "total_consumption_kwh":round(total_house,3),
                "peak_window":peak,
                "lowest_window":lowest,
                "evening_consumption_share_percent":evening.get("consumption_share_percent"),
                "solar_active_consumption_share_percent":round(solar_active_share,1) if solar_active_share is not None else None,
                "solar_active_hour_count":solar_active_hours,
                "windows":window_rows,
                "timing_rule":"Night 00–06 · Morning 06–12 · Afternoon 12–18 · Evening 18–24, using local completed-hour energy statistics.",
            })

        alignment_rows=[
            row for row in timing_rows
            if row.get("house_energy_kwh") is not None and row.get("solar_energy_kwh") is not None
        ]
        alignment_days=sorted({
            str(row.get("start") or "")[:10]
            for row in alignment_rows
            if row.get("start")
        })
        solar_alignment: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(alignment_days),
            "hour_count":len(alignment_rows),
            "evidence_period":"Latest up to 30 completed days with canonical hourly household-consumption and solar-production evidence",
            "evidence_boundary":"Solar Alignment Intelligence measures temporal overlap between whole-home demand and solar production. It does not prove that solar directly supplied that demand, identify which appliance used energy, prove that a load is flexible, or establish that moving demand would improve cost, comfort or equipment performance.",
            "source":dict(getattr(self, "_ha_consumption_hourly_status", {}) or {}).get("source"),
        }
        if len(alignment_days)>=7 and alignment_rows:
            total_house=0.0
            solar_active_house=0.0
            non_solar_house=0.0
            concurrent_upper_bound=0.0
            solar_total=0.0
            solar_active_hours=0
            non_solar_hours=0

            for row in alignment_rows:
                house=max(0.0,float(row.get("house_energy_kwh") or 0))
                solar=max(0.0,float(row.get("solar_energy_kwh") or 0))
                total_house+=house
                solar_total+=solar
                concurrent_upper_bound+=min(house,solar)
                if solar>0.05:
                    solar_active_house+=house
                    solar_active_hours+=1
                else:
                    non_solar_house+=house
                    non_solar_hours+=1

            solar_active_share=(solar_active_house/total_house*100.0) if total_house>0 else None
            non_solar_share=(non_solar_house/total_house*100.0) if total_house>0 else None
            concurrent_upper_share=(concurrent_upper_bound/total_house*100.0) if total_house>0 else None

            if solar_active_share is None:
                profile="Unclassified"
                headline="Solar alignment evidence is incomplete"
            elif solar_active_share>=65.0:
                profile="Strongly aligned"
                headline="Most household demand occurs during solar-producing hours"
            elif solar_active_share>=40.0:
                profile="Partly aligned"
                headline="Household demand is partly aligned with solar-producing hours"
            else:
                profile="Weakly aligned"
                headline="Most household demand occurs outside solar-producing hours"

            interpretation=(
                f"{solar_active_share:.1f}% of measured household consumption occurred during hours with measured solar production, "
                f"while {non_solar_share:.1f}% occurred outside those hours."
            ) if solar_active_share is not None and non_solar_share is not None else "Measured solar-alignment shares are unavailable."
            if concurrent_upper_share is not None:
                interpretation+=(
                    f" Concurrent measured solar production was large enough to cover at most {concurrent_upper_share:.1f}% "
                    "of household demand on an hourly-energy basis; this is an upper bound, not measured direct solar self-consumption."
                )

            solar_alignment.update({
                "available":True,
                "profile":profile,
                "headline":headline,
                "interpretation":interpretation,
                "consumption_energy_kwh":round(total_house,3),
                "solar_energy_kwh":round(solar_total,3),
                "solar_active_consumption_kwh":round(solar_active_house,3),
                "non_solar_consumption_kwh":round(non_solar_house,3),
                "solar_active_consumption_share_percent":round(solar_active_share,1) if solar_active_share is not None else None,
                "non_solar_consumption_share_percent":round(non_solar_share,1) if non_solar_share is not None else None,
                "concurrent_solar_upper_bound_kwh":round(concurrent_upper_bound,3),
                "concurrent_solar_upper_bound_percent":round(concurrent_upper_share,1) if concurrent_upper_share is not None else None,
                "solar_active_hour_count":solar_active_hours,
                "non_solar_hour_count":non_solar_hours,
                "solar_active_rule":"An hour is solar-active when canonical measured solar production exceeds 0.05 kWh.",
                "upper_bound_rule":"Concurrent solar upper bound = sum of min(hourly household consumption, hourly solar production). It does not measure direct solar self-consumption or battery routing.",
            })

        solar_opportunity_rows=[
            row for row in timing_rows
            if row.get("house_energy_kwh") is not None
            and row.get("solar_energy_kwh") is not None
            and row.get("grid_export_energy_kwh") is not None
        ]
        solar_opportunity_days=sorted({
            str(row.get("start") or "")[:10]
            for row in solar_opportunity_rows
            if row.get("start")
        })
        solar_opportunity: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(solar_opportunity_days),
            "hour_count":len(solar_opportunity_rows),
            "evidence_period":"Latest up to 30 completed days with canonical hourly household-consumption, solar-production and grid-export evidence",
            "evidence_boundary":"Consumption Solar Opportunity Intelligence identifies measured timing conditions that may deserve planning review. It does not prove that any load can be shifted, that exported energy would have supplied that load, or that changing timing would improve cost, comfort or equipment performance.",
            "source":dict(getattr(self, "_ha_consumption_hourly_status", {}) or {}).get("source"),
        }
        if len(solar_opportunity_days)>=7 and solar_opportunity_rows:
            house_total=0.0
            export_total=0.0
            off_solar_house=0.0
            export_active_house=0.0
            export_active_hours=0
            solar_active_hours=0

            for row in solar_opportunity_rows:
                house=max(0.0,float(row.get("house_energy_kwh") or 0))
                solar=max(0.0,float(row.get("solar_energy_kwh") or 0))
                exported=max(0.0,float(row.get("grid_export_energy_kwh") or 0))
                house_total+=house
                export_total+=exported
                if solar>0.05:
                    solar_active_hours+=1
                else:
                    off_solar_house+=house
                if exported>0.05:
                    export_active_hours+=1
                    export_active_house+=house

            off_solar_share=(off_solar_house/house_total*100.0) if house_total>0 else None
            export_active_demand_share=(export_active_house/house_total*100.0) if house_total>0 else None
            review_ceiling=min(export_total,off_solar_house)
            review_ceiling_share=(review_ceiling/house_total*100.0) if house_total>0 else None

            score=0
            reasons=[]
            if export_total>=10.0:
                score+=1
                reasons.append(f"{export_total:.2f} kWh of grid export was measured in the completed-hour evidence")
            if off_solar_share is not None and off_solar_share>=25.0:
                score+=2
                reasons.append(f"{off_solar_share:.1f}% of household demand occurred outside solar-active hours")
            elif off_solar_share is not None and off_solar_share>=10.0:
                score+=1
                reasons.append(f"{off_solar_share:.1f}% of household demand occurred outside solar-active hours")
            if export_active_hours>=24:
                score+=1
                reasons.append(f"grid export occurred in {export_active_hours} completed hour(s)")
            if review_ceiling>=10.0:
                score+=1

            if score>=4:
                signal="Strong"
                headline="Measured solar-export and demand timing deserve planning attention"
            elif score>=2:
                signal="Moderate"
                headline="Measured timing shows a solar-consumption planning opportunity"
            else:
                signal="Limited"
                headline="No strong solar-timing opportunity is established"

            interpretation="; ".join(reasons[:3])+"." if reasons else "The completed-hour evidence does not currently show a strong combination of export and demand outside solar-active hours."
            interpretation+=(
                f" The energy-volume review ceiling is {review_ceiling:.2f} kWh, calculated as the smaller of measured export and off-solar household demand. "
                "This is only a planning ceiling and is not shiftable energy, savings, or proven recoverable solar."
            )

            solar_opportunity.update({
                "available":True,
                "signal":signal,
                "score":score,
                "headline":headline,
                "interpretation":interpretation,
                "grid_export_kwh":round(export_total,3),
                "off_solar_consumption_kwh":round(off_solar_house,3),
                "off_solar_consumption_share_percent":round(off_solar_share,1) if off_solar_share is not None else None,
                "export_active_consumption_kwh":round(export_active_house,3),
                "export_active_consumption_share_percent":round(export_active_demand_share,1) if export_active_demand_share is not None else None,
                "export_active_hour_count":export_active_hours,
                "solar_active_hour_count":solar_active_hours,
                "energy_volume_review_ceiling_kwh":round(review_ceiling,3),
                "energy_volume_review_ceiling_percent":round(review_ceiling_share,1) if review_ceiling_share is not None else None,
                "planning_note":"Zeus may use this measured timing pattern to prioritize recommendation-only ideas. The review ceiling does not assume any appliance is flexible and Zeus does not control equipment.",
                "review_ceiling_rule":"Energy-volume review ceiling = min(total measured grid export, household consumption outside solar-active hours) across the same completed-hour evidence window. It is not a dispatch simulation or savings estimate.",
            })

        battery_alignment_rows=[
            row for row in timing_rows
            if row.get("house_energy_kwh") is not None
            and row.get("battery_discharge_energy_kwh") is not None
        ]
        battery_alignment_days=sorted({
            str(row.get("start") or "")[:10]
            for row in battery_alignment_rows
            if row.get("start")
        })
        battery_alignment: dict[str, Any]={
            "available":False,
            "required_completed_days":7,
            "completed_day_count":len(battery_alignment_days),
            "evidence_period":"Latest up to 30 completed days with canonical hourly household-consumption and battery-discharge evidence",
            "evidence_boundary":"Battery Alignment Intelligence measures temporal overlap between whole-home demand and battery discharge. It does not prove that battery energy directly supplied a particular load, identify which appliance used energy, or establish battery efficiency, health or optimal dispatch.",
        }
        if len(battery_alignment_days)>=7 and battery_alignment_rows:
            house_total=0.0
            discharge_total=0.0
            discharge_active_house=0.0
            non_discharge_house=0.0
            concurrent_upper_bound=0.0
            discharge_active_hours=0

            for row in battery_alignment_rows:
                house=max(0.0,float(row.get("house_energy_kwh") or 0))
                discharge=max(0.0,float(row.get("battery_discharge_energy_kwh") or 0))
                house_total+=house
                discharge_total+=discharge
                concurrent_upper_bound+=min(house,discharge)
                if discharge>0.05:
                    discharge_active_house+=house
                    discharge_active_hours+=1
                else:
                    non_discharge_house+=house

            active_share=(discharge_active_house/house_total*100.0) if house_total>0 else None
            inactive_share=(non_discharge_house/house_total*100.0) if house_total>0 else None
            upper_share=(concurrent_upper_bound/house_total*100.0) if house_total>0 else None

            if active_share is None:
                profile="Unclassified"
                headline="Battery alignment evidence is incomplete"
            elif active_share>=65.0:
                profile="Strongly aligned"
                headline="Most household demand occurs during battery-discharge hours"
            elif active_share>=35.0:
                profile="Partly aligned"
                headline="Household demand is partly aligned with battery-discharge hours"
            else:
                profile="Weakly aligned"
                headline="Most household demand occurs outside battery-discharge hours"

            interpretation=(
                f"{active_share:.1f}% of measured household consumption occurred during hours with measured battery discharge, "
                f"while {inactive_share:.1f}% occurred outside those hours. "
                f"Concurrent measured battery discharge was large enough to cover at most {upper_share:.1f}% of household demand "
                "on an hourly-energy basis; this is an upper bound, not measured direct battery supply."
            )

            battery_alignment.update({
                "available":True,
                "profile":profile,
                "headline":headline,
                "interpretation":interpretation,
                "battery_discharge_kwh":round(discharge_total,3),
                "discharge_active_consumption_kwh":round(discharge_active_house,3),
                "discharge_active_consumption_share_percent":round(active_share,1),
                "non_discharge_consumption_share_percent":round(inactive_share,1),
                "concurrent_battery_upper_bound_kwh":round(concurrent_upper_bound,3),
                "concurrent_battery_upper_bound_percent":round(upper_share,1),
                "discharge_active_hour_count":discharge_active_hours,
                "discharge_active_rule":"An hour is battery-discharge-active when canonical measured battery discharge exceeds 0.05 kWh.",
                "upper_bound_rule":"Concurrent battery upper bound = sum of min(hourly household consumption, hourly battery discharge). It is not direct battery-to-load attribution.",
            })

        battery_opportunity_rows=[
            row for row in timing_rows
            if row.get("house_energy_kwh") is not None
            and row.get("grid_import_energy_kwh") is not None
            and row.get("battery_discharge_energy_kwh") is not None
        ]
        battery_opportunity_days=sorted({
            str(row.get("start") or "")[:10]
            for row in battery_opportunity_rows
            if row.get("start")
        })
        battery_opportunity: dict[str, Any]={
            "available":False,
            "completed_day_count":len(battery_opportunity_days),
            "evidence_period":"Latest up to 30 completed days of hourly consumption, grid-import and battery-discharge evidence",
            "evidence_boundary":"Grid import outside discharge-active hours is a review condition only. It does not prove battery SOC, reserve or capacity was available, and it is not a dispatch or savings simulation.",
        }
        if len(battery_opportunity_days)>=7 and battery_opportunity_rows:
            import_total=0.0
            import_outside_discharge=0.0
            import_during_discharge=0.0
            outside_hours=0
            discharge_hours=0
            for row in battery_opportunity_rows:
                imported=max(0.0,float(row.get("grid_import_energy_kwh") or 0))
                discharge=max(0.0,float(row.get("battery_discharge_energy_kwh") or 0))
                import_total+=imported
                if discharge>0.05:
                    discharge_hours+=1
                    import_during_discharge+=imported
                else:
                    import_outside_discharge+=imported
                    if imported>0.05:
                        outside_hours+=1

            outside_share=(import_outside_discharge/import_total*100.0) if import_total>0 else None
            if outside_share is not None and import_outside_discharge>=10.0 and outside_share>=50.0:
                signal="Strong"
                headline="Grid import outside battery-discharge hours deserves planning review"
            elif outside_share is not None and import_outside_discharge>=3.0 and outside_share>=20.0:
                signal="Moderate"
                headline="Measured battery timing shows a planning opportunity"
            else:
                signal="Limited"
                headline="No strong battery-timing opportunity is established"

            interpretation=(
                f"{import_outside_discharge:.2f} kWh of measured grid import occurred outside battery-discharge-active hours, "
                f"representing {outside_share:.1f}% of measured grid import in this completed-hour evidence window."
                if outside_share is not None else
                "Measured grid-import timing could not be classified."
            )
            interpretation+=" This identifies a review condition only; Zeus does not infer that the battery could or should have discharged in those hours."

            battery_opportunity.update({
                "available":True,
                "signal":signal,
                "headline":headline,
                "interpretation":interpretation,
                "grid_import_kwh":round(import_total,3),
                "grid_import_outside_discharge_kwh":round(import_outside_discharge,3),
                "grid_import_outside_discharge_percent":round(outside_share,1) if outside_share is not None else None,
                "grid_import_outside_discharge_hour_count":outside_hours,
                "battery_discharge_active_hour_count":discharge_hours,
                "planning_note":"Recommendation-only. Battery SOC, reserve, power limits, tariffs and backup needs are not assumed.",
            })

        return {
            "status":(self.last or {}).get("status"),
            "summary":summary,
            "trend":trend,
            "pattern":pattern,
            "grid_dependency":grid_dependency,
            "opportunity":opportunity,
            "flexibility":flexibility,
            "timing":timing,
            "solar_alignment":solar_alignment,
            "solar_opportunity":solar_opportunity,
            "battery_alignment":battery_alignment,
            "battery_opportunity":battery_opportunity,
            "current_partial_day_excluded":True,
            "missing_evidence_is_not_zero":True,
            "source":"canonical_daily_history",
            "recorder_safe":True,
        }

    def recorder_consumption_battery_dependency_evidence(self)->dict[str,Any]:
        rows=[
            row for row in list(getattr(self,"_ha_consumption_hourly",[]) or [])
            if row.get("house_energy_kwh") is not None
            and row.get("battery_discharge_energy_kwh") is not None
        ]
        days=sorted({str(row.get("start") or "")[:10] for row in rows if row.get("start")})
        base={
            "available":False,
            "completed_day_count":len(days),
            "required_completed_days":7,
            "evidence_period":"Latest up to 30 completed days with hourly household-consumption and battery-discharge evidence",
            "evidence_boundary":"Battery Dependency Intelligence describes measured battery participation in household-demand timing. It does not prove direct battery-to-load supply, required battery use, battery health or optimal dispatch.",
        }
        if len(days)<7 or not rows:
            return base

        house_total=0.0
        discharge_total=0.0
        discharge_active_house=0.0
        overlap_upper=0.0
        active_hours=0

        for row in rows:
            house=max(0.0,float(row.get("house_energy_kwh") or 0))
            discharge=max(0.0,float(row.get("battery_discharge_energy_kwh") or 0))
            house_total+=house
            discharge_total+=discharge
            overlap_upper+=min(house,discharge)
            if discharge>0.05:
                active_hours+=1
                discharge_active_house+=house

        active_share=(discharge_active_house/house_total*100.0) if house_total>0 else 0.0
        upper_share=(overlap_upper/house_total*100.0) if house_total>0 else 0.0
        discharge_ratio=(discharge_total/house_total*100.0) if house_total>0 else 0.0

        if upper_share>=50.0:
            profile="High participation"
            headline="Battery discharge strongly overlaps household demand"
        elif upper_share>=20.0:
            profile="Moderate participation"
            headline="Battery discharge materially participates in household-demand timing"
        else:
            profile="Low participation"
            headline="Household demand shows limited measured battery-discharge participation"

        return {
            **base,
            "available":True,
            "profile":profile,
            "headline":headline,
            "interpretation":(
                f"Battery discharge was active during hours containing {active_share:.1f}% of measured household consumption. "
                f"The concurrent hourly-energy overlap upper bound was {upper_share:.1f}% of household demand."
            ),
            "battery_discharge_kwh":round(discharge_total,3),
            "battery_discharge_to_consumption_ratio_percent":round(discharge_ratio,1),
            "demand_in_discharge_active_hours_percent":round(active_share,1),
            "concurrent_battery_upper_bound_percent":round(upper_share,1),
            "battery_discharge_active_hour_count":active_hours,
            "rule":"Dependency here means measured battery participation in demand timing, not direct supply attribution.",
        }

    def recorder_consumption_battery_timing_evidence(self)->dict[str,Any]:
        rows=[
            row for row in list(getattr(self,"_ha_consumption_hourly",[]) or [])
            if row.get("house_energy_kwh") is not None
            and row.get("battery_discharge_energy_kwh") is not None
        ]
        days=sorted({str(row.get("start") or "")[:10] for row in rows if row.get("start")})
        base={
            "available":False,
            "completed_day_count":len(days),
            "required_completed_days":7,
            "evidence_period":"Latest up to 30 completed days with hourly household-consumption and battery-discharge evidence",
            "evidence_boundary":"Battery Timing Intelligence describes when measured battery discharge overlaps household demand. It does not prove direct battery-to-load supply, battery necessity, health, efficiency or optimal dispatch.",
        }
        if len(days)<7 or not rows:
            return base

        windows={
            "Night · 00–06":{"start":0,"end":6,"discharge":0.0,"house":0.0,"overlap":0.0,"active_hours":0},
            "Morning · 06–12":{"start":6,"end":12,"discharge":0.0,"house":0.0,"overlap":0.0,"active_hours":0},
            "Afternoon · 12–18":{"start":12,"end":18,"discharge":0.0,"house":0.0,"overlap":0.0,"active_hours":0},
            "Evening · 18–24":{"start":18,"end":24,"discharge":0.0,"house":0.0,"overlap":0.0,"active_hours":0},
        }
        total_overlap=0.0
        total_discharge=0.0

        for row in rows:
            try:
                stamp=datetime.fromisoformat(str(row.get("start") or ""))
                hour=stamp.hour
            except (TypeError,ValueError):
                continue
            house=max(0.0,float(row.get("house_energy_kwh") or 0))
            discharge=max(0.0,float(row.get("battery_discharge_energy_kwh") or 0))
            overlap=min(house,discharge)
            total_overlap+=overlap
            total_discharge+=discharge
            for data in windows.values():
                if data["start"]<=hour<data["end"]:
                    data["house"]+=house
                    data["discharge"]+=discharge
                    data["overlap"]+=overlap
                    if discharge>0.05:
                        data["active_hours"]+=1
                    break

        window_rows=[]
        for label,data in windows.items():
            overlap_share=(data["overlap"]/total_overlap*100.0) if total_overlap>0 else 0.0
            demand_overlap_share=(data["overlap"]/data["house"]*100.0) if data["house"]>0 else 0.0
            window_rows.append({
                "label":label,
                "battery_discharge_kwh":round(data["discharge"],3),
                "concurrent_overlap_upper_bound_kwh":round(data["overlap"],3),
                "overlap_distribution_percent":round(overlap_share,1),
                "window_demand_overlap_upper_bound_percent":round(demand_overlap_share,1),
                "discharge_active_hour_count":data["active_hours"],
            })

        strongest=max(window_rows,key=lambda row:row["concurrent_overlap_upper_bound_kwh"])
        weakest=min(window_rows,key=lambda row:row["concurrent_overlap_upper_bound_kwh"])
        strongest_share=float(strongest.get("overlap_distribution_percent") or 0)
        if total_overlap<=0.05:
            profile="No material overlap"
            headline="No material battery-discharge timing overlap is established"
        elif strongest_share>=50.0:
            profile=f"{strongest['label'].split(' · ')[0]} concentrated"
            headline=f"Battery-discharge overlap is concentrated in the {strongest['label'].split(' · ')[0].lower()}"
        elif strongest_share>=35.0:
            profile=f"{strongest['label'].split(' · ')[0]} weighted"
            headline=f"Battery-discharge overlap is weighted toward the {strongest['label'].split(' · ')[0].lower()}"
        else:
            profile="Distributed"
            headline="Battery-discharge overlap is distributed across the day"

        return {
            **base,
            "available":True,
            "profile":profile,
            "headline":headline,
            "interpretation":(
                f"{strongest['label']} contained the largest share of concurrent hourly battery/demand overlap at "
                f"{strongest_share:.1f}%, while {weakest['label']} contained the smallest share at "
                f"{float(weakest.get('overlap_distribution_percent') or 0):.1f}%."
            ),
            "battery_discharge_kwh":round(total_discharge,3),
            "concurrent_overlap_upper_bound_kwh":round(total_overlap,3),
            "strongest_window":strongest,
            "weakest_window":weakest,
            "windows":window_rows,
            "timing_rule":"Night 00–06 · Morning 06–12 · Afternoon 12–18 · Evening 18–24, using local completed-hour energy statistics.",
            "upper_bound_rule":"Concurrent overlap = min(hourly household consumption, hourly battery discharge). It is not direct battery-to-load attribution.",
        }

    def recorder_consumption_battery_coverage_evidence(self)->dict[str,Any]:
        rows=[
            row for row in list(getattr(self,"_ha_consumption_hourly",[]) or [])
            if row.get("house_energy_kwh") is not None
            and row.get("battery_discharge_energy_kwh") is not None
            and row.get("start")
        ]
        days=sorted({str(row.get("start") or "")[:10] for row in rows})
        base={
            "available":False,
            "completed_day_count":len(days),
            "required_completed_days":7,
            "evidence_period":"Latest up to 30 completed days with hourly household-consumption and battery-discharge evidence",
            "evidence_boundary":"Battery Coverage Intelligence describes how consistently battery-discharge activity overlaps measured household-demand hours. It does not prove direct battery-to-load supply, required battery support, battery health, efficiency or optimal dispatch.",
        }
        if len(days)<7 or not rows:
            return base

        demand_threshold=0.05
        discharge_threshold=0.05
        demand_hours=0
        covered_hours=0
        uncovered_hours=0
        daily:dict[str,dict[str,int]]={}

        for row in rows:
            day=str(row.get("start") or "")[:10]
            house=max(0.0,float(row.get("house_energy_kwh") or 0))
            discharge=max(0.0,float(row.get("battery_discharge_energy_kwh") or 0))
            if house<=demand_threshold:
                continue
            demand_hours+=1
            target=daily.setdefault(day,{"demand":0,"covered":0})
            target["demand"]+=1
            if discharge>discharge_threshold:
                covered_hours+=1
                target["covered"]+=1
            else:
                uncovered_hours+=1

        if demand_hours<=0:
            return base

        coverage_percent=covered_hours/demand_hours*100.0
        daily_rows=[]
        for day,data in sorted(daily.items()):
            if data["demand"]<=0:
                continue
            pct=data["covered"]/data["demand"]*100.0
            daily_rows.append({
                "date":day,
                "coverage_percent":round(pct,1),
                "covered_demand_hour_count":data["covered"],
                "demand_hour_count":data["demand"],
            })

        if not daily_rows:
            return base

        strongest=max(daily_rows,key=lambda row:row["coverage_percent"])
        weakest=min(daily_rows,key=lambda row:row["coverage_percent"])
        values=[float(row["coverage_percent"]) for row in daily_rows]
        average=sum(values)/len(values)
        variance=sum((value-average)**2 for value in values)/len(values)
        stddev=variance**0.5

        if coverage_percent>=70.0:
            profile="High coverage"
            headline="Battery discharge overlaps most measured demand hours"
        elif coverage_percent>=35.0:
            profile="Moderate coverage"
            headline="Battery discharge overlaps a material share of measured demand hours"
        else:
            profile="Low coverage"
            headline="Most measured demand hours occur without battery discharge"

        if stddev<=10.0:
            consistency="Consistent"
        elif stddev<=20.0:
            consistency="Mixed"
        else:
            consistency="Variable"

        return {
            **base,
            "available":True,
            "profile":profile,
            "headline":headline,
            "interpretation":(
                f"Battery discharge overlapped {covered_hours} of {demand_hours} measured demand-active hour(s), "
                f"or {coverage_percent:.1f}%. Daily coverage ranged from {weakest['coverage_percent']:.1f}% "
                f"to {strongest['coverage_percent']:.1f}% across completed days."
            ),
            "coverage_percent":round(coverage_percent,1),
            "covered_demand_hour_count":covered_hours,
            "uncovered_demand_hour_count":uncovered_hours,
            "demand_hour_count":demand_hours,
            "consistency":consistency,
            "daily_coverage_stddev_points":round(stddev,1),
            "strongest_day":strongest,
            "weakest_day":weakest,
            "coverage_rule":"A demand hour requires household consumption > 0.05 kWh; it is coverage-active when battery discharge also exceeds 0.05 kWh in that hour.",
            "consistency_rule":"Consistency classifies the completed-day variation in hourly battery/demand overlap coverage; it is not a reliability or availability rating.",
        }

    def summary(self) -> dict[str, Any]:
        return self.last


class ForecastEngine:
    """Predictive, recommendation-only energy forecast engine.

    Uses local historical hourly profiles, configured weather context, live battery
    state and registry metadata. It never calls services or controls devices.
    """

    def __init__(self, event_bus, data_lake, energy_flow, weather=None) -> None:
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.energy_flow = energy_flow
        self.weather = weather
        # Core is attached after all engines are constructed.  This avoids a
        # constructor cycle while allowing the forecast engine to consume the
        # validated planning-learning and forward-trust evidence from the
        # previous decision refresh.
        self.core = None
        self.last = {"status": "Waiting", "summary": "Collecting forecast history."}

    @staticmethod
    def _number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _adaptive_solar_correction(self) -> dict[str, Any]:
        """Expose qualified planning learning as advisory forecast evidence only.

        Plan Results owns the reusable-learning qualification. Forecast may report
        that qualified correction, but Recommendation Only mode must never mutate
        the generated forecast from it. This keeps Planning, Forecast and System
        Intelligence on one truth contract.
        """
        result = {
            "status": "Collecting",
            "applied": False,
            "recommended_correction_percent": 0.0,
            "applied_correction_percent": 0.0,
            "correction_factor": 1.0,
            "completed_comparisons": 0,
            "effective_evidence": 0.0,
            "direction_consistency_percent": None,
            "learning_confidence_percent": 0.0,
            "forecast_trust_percent": None,
            "forward_matches": 0,
            "bias_direction": "Collecting",
            "average_bias_percent": None,
            "reason": "Zeus is collecting completed forecast comparisons before qualifying reusable learning.",
            "guardrail_percent": 15.0,
            "minimum_completed_comparisons": 5,
            "minimum_effective_evidence": 3.0,
            "minimum_direction_consistency_percent": 65.0,
            "reusable_learning_ready": False,
            "recommendation_only": True,
            "automatic_correction_applied": False,
        }
        core = self.core
        if core is None or not hasattr(core, "planning_engine"):
            return result
        try:
            planning = core.planning_engine.summary() or {}
            learning = planning.get("learning") if isinstance(planning.get("learning"), dict) else {}
        except Exception:  # noqa: BLE001 - forecast must remain available
            return result

        count = int(self._number(learning.get("comparison_count"), 0))
        effective = max(0.0, self._number(learning.get("effective_evidence"), 0.0))
        direction = learning.get("direction_consistency_percent")
        direction_num = self._number(direction, 0.0) if direction is not None else None
        confidence = max(0.0, min(100.0, self._number(learning.get("confidence_percent"), 0.0)))
        reusable = bool(learning.get("reusable_learning_ready"))
        recommended = max(-15.0, min(15.0, self._number(learning.get("recommended_forecast_correction_percent"), 0.0))) if reusable else 0.0
        result.update({
            "completed_comparisons": count,
            "effective_evidence": round(effective, 2),
            "direction_consistency_percent": round(direction_num, 1) if direction_num is not None else None,
            "learning_confidence_percent": round(confidence, 1),
            "recommended_correction_percent": round(recommended, 1),
            "bias_direction": str(learning.get("bias_direction") or "Collecting"),
            "average_bias_percent": learning.get("average_bias_percent"),
            "reusable_learning_ready": reusable,
        })

        try:
            accuracy = core.prediction_accuracy.summary() or {}
            result["forward_matches"] = int(self._number(accuracy.get("sample_count"), 0))
            raw_trust = accuracy.get("trust_percent")
            trust = self._number(raw_trust, -1.0) if raw_trust is not None else None
            result["forecast_trust_percent"] = round(trust, 1) if trust is not None and trust >= 0 else None
        except Exception:  # noqa: BLE001 - advisory learning can stand alone
            pass

        if reusable:
            result["status"] = "Qualified advisory"
            result["reason"] = (
                f"Reusable planning evidence qualifies a {recommended:+.1f}% advisory solar correction, "
                "but Recommendation Only mode leaves the forecast unchanged."
                if abs(recommended) >= 0.5 else
                "Reusable planning evidence is qualified and currently balanced; no advisory correction is needed."
            )
        elif count:
            result["status"] = "Learning"
            result["reason"] = (
                "Observed plan results are retained as evidence, but reusable-learning thresholds are not yet met."
            )
        return result

    def refresh(self) -> dict[str, Any]:
        snapshots = self.data_lake.data.get("snapshots", [])
        now = dt_util.now()
        forecast_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets: dict[tuple[int, int], dict[str, list[float]]] = {}
        hour_buckets: dict[int, dict[str, list[float]]] = {}
        for snap in snapshots[-30240:]:
            try:
                dt = datetime.fromisoformat(str(snap["timestamp"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt_util.as_local(dt)
            except (KeyError, TypeError, ValueError):
                continue
            flows = snap.get("flows", {}) or {}
            values = {
                "solar": flows.get("solar_power_w"),
                "house": flows.get("house_power_w"),
                "grid_import": flows.get("grid_import_power_w"),
                "grid_export": flows.get("grid_export_power_w"),
            }
            for key in ((dt.weekday(), dt.hour), dt.hour):
                target = buckets.setdefault(key, {"solar": [], "house": [], "grid_import": [], "grid_export": []}) if isinstance(key, tuple) else hour_buckets.setdefault(key, {"solar": [], "house": [], "grid_import": [], "grid_export": []})
                for name, value in values.items():
                    if isinstance(value, (int, float)):
                        target[name].append(float(value))

        weather = self.weather.summary() if self.weather else {}
        forecast_rows = weather.get("forecast", []) if isinstance(weather.get("forecast"), list) else []
        forecast_granularity = str(weather.get("forecast_granularity") or "hourly").lower()
        current_factor = self._number(weather.get("solar_factor"), 1.0) or 1.0

        def average(values):
            if not values:
                return None
            ordered = sorted(values)
            trim = max(0, int(len(ordered) * 0.1))
            sample = ordered[trim:len(ordered)-trim] if trim and len(ordered) > trim * 2 else ordered
            return round(sum(sample) / len(sample), 1)

        def weather_for(dt):
            best = None
            target_local = dt_util.as_local(dt)
            for row in forecast_rows:
                raw = row.get("datetime") or row.get("time")
                try:
                    candidate = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if candidate.tzinfo is None:
                    candidate = candidate.replace(tzinfo=timezone.utc)
                candidate_local = dt_util.as_local(candidate)

                if forecast_granularity == "daily":
                    if candidate_local.date() != target_local.date():
                        continue
                    # A daily forecast represents the complete local day.
                    row_factor = self.weather.factor_for(row.get("condition"), row.get("cloud_coverage")) if self.weather else 1.0
                    return row_factor, row.get("condition"), row.get("cloud_coverage"), True

                distance = abs((candidate_local - target_local).total_seconds())
                if best is None or distance < best[0]:
                    best = (distance, row)

            if best and best[0] <= 3 * 3600:
                row = best[1]
                factor = self.weather.factor_for(row.get("condition"), row.get("cloud_coverage")) if self.weather else 1.0
                return factor, row.get("condition"), row.get("cloud_coverage"), True

            # Historical solar profiles already contain normal weather variation.
            # Current weather is never copied into future days when no timestamped
            # future forecast row exists.
            return 1.0, None, None, False

        flow_summary = self.energy_flow.summary() or {}
        live_flows = flow_summary.get("flows", {}) or {}
        soc = live_flows.get("battery_soc_percent")
        if isinstance(soc, dict):
            soc = soc.get("value") or soc.get("percent")
        soc = self._number(soc, -1)
        battery_capacity_kwh = 10.0
        reserve_percent = 15.0

        adaptive = self._adaptive_solar_correction()
        correction_factor = self._number(adaptive.get("correction_factor"), 1.0) or 1.0

        # Forecast fallback must consume the same canonical daily energy table
        # that powers Zeus Statistics. Raw DataLake summaries can pre-date source
        # corrections and otherwise preserve stale/high export values.
        today_date = now.date()
        analytics_summary = {}
        if self.core is not None and hasattr(self.core, "analytics"):
            try:
                analytics_summary = self.core.analytics.summary() or {}
            except Exception:  # noqa: BLE001 - forecast remains available
                analytics_summary = {}
        canonical_rows = list(((analytics_summary.get("chart_history") or {}).get("total") or []))
        measured_days = []
        for row in canonical_rows:
            if not isinstance(row, dict):
                continue
            day_key = row.get("date")
            try:
                day_date = datetime.fromisoformat(str(day_key)).date()
            except (TypeError, ValueError):
                continue
            if day_date >= today_date:
                continue
            measured_days.append((day_date, row))
        measured_days = measured_days[-60:]

        def robust_daily_value(target_date, field):
            weekday_values = []
            fallback_values = []
            for day_date, row in measured_days:
                value = row.get(field)
                if not isinstance(value, (int, float)) or value < 0:
                    continue
                fallback_values.append(float(value))
                if day_date.weekday() == target_date.weekday():
                    weekday_values.append(float(value))
            values = weekday_values[-8:] if len(weekday_values) >= 2 else fallback_values[-21:]
            if not values:
                return None
            values = sorted(values)
            # Median is deliberately used for forecast fallback. It is resistant
            # to a few unusually sunny/export-heavy historical days and does not
            # claim weather knowledge that Zeus does not have.
            middle = len(values) // 2
            median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
            return round(median, 3)

        canonical_today = dict(((analytics_summary.get("periods") or {}).get("today") or {}))

        hourly = []
        projected_soc = soc if 0 <= soc <= 100 else None
        for offset in range(168):
            dt = forecast_start + timedelta(hours=offset)
            weekday_sample = buckets.get((dt.weekday(), dt.hour), {})
            fallback = hour_buckets.get(dt.hour, {})
            solar_values = weekday_sample.get("solar") or fallback.get("solar", [])
            house_values = weekday_sample.get("house") or fallback.get("house", [])
            grid_import_values = weekday_sample.get("grid_import") or fallback.get("grid_import", [])
            grid_export_values = weekday_sample.get("grid_export") or fallback.get("grid_export", [])
            baseline_solar = average(solar_values)
            house = average(house_values)
            baseline_grid_import = average(grid_import_values)
            baseline_grid_export = average(grid_export_values)
            factor, condition, cloud, weather_forecast_applied = weather_for(dt)
            raw_solar = round(max((baseline_solar or 0) * factor, 0), 1) if baseline_solar is not None else None
            adjusted_solar = round(max((raw_solar or 0) * correction_factor, 0), 1) if raw_solar is not None else None
            net_w = (adjusted_solar or 0) - (house or 0)
            charge_w = max(net_w, 0)
            discharge_w = max(-net_w, 0)
            if projected_soc is not None:
                if charge_w > 0:
                    projected_soc += (charge_w / 1000.0) * 0.94 / battery_capacity_kwh * 100
                elif discharge_w > 0 and projected_soc > reserve_percent:
                    usable = min(discharge_w, max(0, (projected_soc-reserve_percent)/100*battery_capacity_kwh*1000))
                    projected_soc -= (usable / 1000.0) / 0.92 / battery_capacity_kwh * 100
                projected_soc = max(reserve_percent, min(100.0, projected_soc))
            battery_available_w = 0
            if projected_soc is not None and projected_soc > reserve_percent + 0.5:
                battery_available_w = min(discharge_w, 5000.0)
            grid_import = max(discharge_w - battery_available_w, 0)
            grid_export = max(charge_w - (0 if projected_soc is None or projected_soc >= 99.5 else min(charge_w, 5000.0)), 0)
            hourly.append({
                "time": dt.replace(minute=0, second=0, microsecond=0).isoformat(),
                "hour": dt.hour,
                "baseline_solar_power_w": baseline_solar,
                "raw_solar_power_w": raw_solar,
                "solar_power_w": adjusted_solar,
                "adaptive_correction_percent": adaptive.get("applied_correction_percent", 0.0),
                "house_power_w": house,
                "surplus_power_w": round(max(net_w, 0), 1) if adjusted_solar is not None and house is not None else None,
                "grid_import_power_w": round(grid_import, 1),
                "grid_export_power_w": round(grid_export, 1),
                "historical_grid_import_power_w": baseline_grid_import,
                "historical_grid_export_power_w": baseline_grid_export,
                "projected_battery_soc_percent": round(projected_soc, 1) if projected_soc is not None else None,
                "weather_factor": round(factor, 3),
                "condition": condition,
                "cloud_coverage": cloud,
                "weather_forecast_applied": weather_forecast_applied,
                "sample_count": max(len(solar_values), len(house_values)),
            })

        valid = [h for h in hourly if h["sample_count"] > 0]
        best = max(valid[:24], key=lambda h: h.get("surplus_power_w") or 0, default=None)

        def energy(rows, key):
            return round(sum((h.get(key) or 0) for h in rows) / 1000, 2)

        today_rows, tomorrow_rows = hourly[:24], hourly[24:48]
        raw_today_solar = energy(today_rows, "raw_solar_power_w")
        raw_tomorrow_solar = energy(tomorrow_rows, "raw_solar_power_w")
        today_solar = energy(today_rows, "solar_power_w")
        tomorrow_solar = energy(tomorrow_rows, "solar_power_w")
        today_load = energy(today_rows, "house_power_w")
        tomorrow_load = energy(tomorrow_rows, "house_power_w")
        today_import = energy(today_rows, "grid_import_power_w")
        tomorrow_import = energy(tomorrow_rows, "grid_import_power_w")
        today_export = energy(today_rows, "grid_export_power_w")
        tomorrow_export = energy(tomorrow_rows, "grid_export_power_w")

        daily_forecast = []
        for day_offset in range(7):
            rows = hourly[day_offset * 24:(day_offset + 1) * 24]
            target_date = (forecast_start + timedelta(days=day_offset)).date()
            peak = max(rows, key=lambda h: h.get("solar_power_w") or 0, default=None)
            conditions = [str(h.get("condition")) for h in rows if h.get("condition") and h.get("weather_forecast_applied")]
            weather_applied = any(bool(r.get("weather_forecast_applied")) for r in rows)
            # Some HA hourly weather providers expose cloud/precipitation for each
            # hour but omit a textual condition. The weather factor has still been
            # applied to every matched hourly solar value. Never relabel such a
            # weather-adjusted day as historical-only merely because condition text
            # is absent.
            condition = (
                max(set(conditions), key=conditions.count)
                if conditions
                else "hourly-weather-forecast" if weather_applied
                else "historical-baseline"
            )
            end_soc = next((r.get("projected_battery_soc_percent") for r in reversed(rows) if r.get("projected_battery_soc_percent") is not None), None)

            model_solar = energy(rows, "solar_power_w")
            model_load = energy(rows, "house_power_w")
            model_import = energy(rows, "grid_import_power_w")
            model_export = energy(rows, "grid_export_power_w")

            measured_solar_baseline = robust_daily_value(target_date, "solar_energy_kwh")
            measured_load_baseline = robust_daily_value(target_date, "house_energy_kwh")
            measured_import_baseline = robust_daily_value(target_date, "grid_import_energy_kwh")
            measured_export_baseline = robust_daily_value(target_date, "grid_export_energy_kwh")

            # With no future weather evidence, use canonical measured history.
            # Today is special: anchor the full-day forecast to what has already
            # happened, then add only the remaining-hours historical profile.
            # Future days use a robust same-weekday median from completed canonical
            # HA/Recorder days.
            if not weather_applied:
                if day_offset == 0 and canonical_today:
                    future_rows = []
                    for forecast_row in rows:
                        try:
                            row_time = datetime.fromisoformat(str(forecast_row.get("time") or ""))
                            if row_time.tzinfo is None:
                                row_time = row_time.replace(tzinfo=timezone.utc)
                            row_time = dt_util.as_local(row_time)
                        except (TypeError, ValueError):
                            continue
                        if row_time > now:
                            future_rows.append(forecast_row)

                    actual_solar = max(0.0, self._number(canonical_today.get("solar_energy_kwh"), 0.0))
                    actual_load = max(0.0, self._number(canonical_today.get("house_energy_kwh"), 0.0))
                    actual_import = max(0.0, self._number(canonical_today.get("grid_import_energy_kwh"), 0.0))
                    actual_export = max(0.0, self._number(canonical_today.get("grid_export_energy_kwh"), 0.0))

                    expected_solar = actual_solar + energy(future_rows, "solar_power_w")
                    expected_load = actual_load + energy(future_rows, "house_power_w")
                    # For the grid boundary, use directly observed historical grid
                    # profiles for the remaining hours instead of re-simulating
                    # battery/grid routing.
                    expected_import = actual_import + energy(future_rows, "historical_grid_import_power_w")
                    expected_export = actual_export + energy(future_rows, "historical_grid_export_power_w")
                    evidence_method = "measured_today_plus_remaining_historical_profile"
                else:
                    expected_solar = measured_solar_baseline if measured_solar_baseline is not None else model_solar
                    expected_load = measured_load_baseline if measured_load_baseline is not None else model_load
                    expected_import = measured_import_baseline if measured_import_baseline is not None else model_import
                    expected_export = measured_export_baseline if measured_export_baseline is not None else model_export
                    evidence_method = "canonical_completed_day_weekday_median"
            else:
                expected_solar = model_solar
                expected_load = model_load
                expected_import = model_import
                expected_export = model_export
                evidence_method = "weather_adjusted_hourly_model"

            matched_weather_rows = [
                {
                    "time": r.get("time"),
                    "condition": r.get("condition"),
                    "weather_forecast_applied": bool(r.get("weather_forecast_applied")),
                    "weather_factor": r.get("weather_factor"),
                    "cloud_coverage": r.get("cloud_coverage"),
                }
                for r in rows if r.get("weather_forecast_applied")
            ][:6]
            daily_forecast.append({
                "date": target_date.isoformat(),
                "label": "Today" if day_offset == 0 else "Tomorrow" if day_offset == 1 else target_date.strftime("%A"),
                "raw_expected_solar_kwh": energy(rows, "raw_solar_power_w"),
                "expected_solar_kwh": round(max(expected_solar, 0.0), 2),
                "adaptive_correction_percent": adaptive.get("applied_correction_percent", 0.0),
                "adaptive_correction_applied": bool(adaptive.get("applied")),
                "expected_consumption_kwh": round(max(expected_load, 0.0), 2),
                "expected_grid_import_kwh": round(max(expected_import, 0.0), 2),
                "expected_grid_export_kwh": round(max(expected_export, 0.0), 2),
                "battery_soc_end_percent": end_soc,
                "peak_hour": peak.get("hour") if peak else None,
                "peak_power_w": peak.get("solar_power_w") if peak else None,
                "condition": condition,
                "weather_forecast_applied": weather_applied,
                "evidence_method": evidence_method,
                "weather_match_diagnostics": {
                    "forecast_granularity": forecast_granularity,
                    "matched_rows": len([r for r in rows if r.get("weather_forecast_applied")]),
                    "average_weather_factor": round(
                        sum(float(r.get("weather_factor") or 1.0) for r in rows if r.get("weather_forecast_applied"))
                        / max(1, len([r for r in rows if r.get("weather_forecast_applied")])),
                        3,
                    ) if weather_applied else None,
                    "sample": matched_weather_rows,
                },
            })

        # Public 24h/following-24h values follow the same daily evidence contract
        # as the 7-day cards.
        if daily_forecast:
            today_solar = daily_forecast[0]["expected_solar_kwh"]
            today_load = daily_forecast[0]["expected_consumption_kwh"]
            today_import = daily_forecast[0]["expected_grid_import_kwh"]
            today_export = daily_forecast[0]["expected_grid_export_kwh"]
        if len(daily_forecast) > 1:
            tomorrow_solar = daily_forecast[1]["expected_solar_kwh"]
            tomorrow_load = daily_forecast[1]["expected_consumption_kwh"]
            tomorrow_import = daily_forecast[1]["expected_grid_import_kwh"]
            tomorrow_export = daily_forecast[1]["expected_grid_export_kwh"]

        history_samples = sum(min(h["sample_count"], 21) for h in valid[:24])
        history_confidence = min(75, int(history_samples / max(24 * 21, 1) * 75)) if valid else 0
        forecast_weather_hours = sum(1 for row in hourly[:48] if row.get("weather_forecast_applied"))
        weather_bonus = 20 if forecast_weather_hours >= 12 else (10 if forecast_weather_hours >= 4 else 0)
        live_bonus = 5 if flow_summary.get("status") == "Ready" else 0
        confidence = min(100, history_confidence + weather_bonus + live_bonus)
        confidence_label = "High" if confidence >= 80 else "Medium" if confidence >= 55 else "Low"
        base_method = "weather_adjusted_calendar_profile" if forecast_weather_hours >= 4 else "calendar_aligned_historical_profile"
        method = f"adaptive_{base_method}" if adaptive.get("applied") else base_method

        best_window = None
        if best:
            start = datetime.fromisoformat(best["time"])
            best_window = {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=2)).isoformat(),
                "label": f"{start:%H:%M}–{(start + timedelta(hours=2)):%H:%M}",
                "expected_surplus_power_w": best.get("surplus_power_w"),
            }
        recommendations = []
        if best_window and (best.get("surplus_power_w") or 0) >= 500:
            recommendations.append({"action": "Use flexible loads", "window": best_window["label"], "reason": "The strongest predicted solar surplus is available in this window.", "confidence": confidence})
        if tomorrow_import > 2:
            recommendations.append({"action": "Preserve battery reserve", "window": "Before evening peak", "reason": f"Tomorrow's model predicts about {tomorrow_import:.1f} kWh of grid import.", "confidence": confidence})
        if not recommendations:
            recommendations.append({"action": "No schedule change", "window": "Current plan", "reason": "No strong forecast-driven opportunity is detected yet.", "confidence": confidence})

        timeline = [{k: row.get(k) for k in ("time", "raw_solar_power_w", "solar_power_w", "adaptive_correction_percent", "house_power_w", "grid_import_power_w", "grid_export_power_w", "projected_battery_soc_percent", "condition")} for row in hourly[:24]]
        self.last = {
            "status": "Ready" if valid else "Waiting",
            "method": method,
            "confidence": confidence,
            "confidence_label": confidence_label,
            "confidence_factors": {"history": history_confidence, "weather": weather_bonus, "live_context": live_bonus},
            "calendar_aligned": True,
            "weather_forecast_hours_48h": forecast_weather_hours,
            "weather_fallback": "historical_profile" if forecast_weather_hours < 4 else None,
            "weather": {
                **{k: weather.get(k) for k in ("entity_id", "condition", "temperature", "cloud_coverage", "forecast_available", "forecast_granularity", "solar_factor")},
                "forecast_diagnostics": weather.get("forecast_diagnostics"),
            },
            "raw_expected_solar_next_24h_kwh": raw_today_solar,
            "raw_expected_solar_following_24h_kwh": raw_tomorrow_solar,
            "expected_solar_next_24h_kwh": today_solar,
            "expected_solar_following_24h_kwh": tomorrow_solar,
            "adaptive_correction": adaptive,
            "expected_consumption_next_24h_kwh": today_load,
            "expected_consumption_following_24h_kwh": tomorrow_load,
            "expected_grid_import_next_24h_kwh": today_import,
            "expected_grid_import_following_24h_kwh": tomorrow_import,
            "expected_grid_export_next_24h_kwh": today_export,
            "expected_grid_export_following_24h_kwh": tomorrow_export,
            "projected_battery_soc_24h_percent": today_rows[-1].get("projected_battery_soc_percent") if today_rows else None,
            "projected_battery_soc_48h_percent": tomorrow_rows[-1].get("projected_battery_soc_percent") if tomorrow_rows else None,
            "daily_forecast": daily_forecast,
            "forecast_horizon_hours": 168,
            # Backend planning evidence handoff. These are the exact hourly rows
            # already calculated above; no second forecast is created. Keep 72
            # hours so Scheduler can build a true rolling 48-hour horizon even
            # late in the current day. The public Forecast sensor intentionally
            # does not expose this large internal planning payload.
            "planning_hourly": hourly[:72],
            "timeline_24h": timeline,
            "best_surplus_window": best_window,
            "recommendations": recommendations[:3],
            "summary": (f"Next 24 hours: {today_solar:.1f} kWh solar, {today_load:.1f} kWh consumption, {today_import:.1f} kWh import and {today_export:.1f} kWh export." if valid else "More historical samples are needed for a forecast."),
            "limitations": "Calendar-aligned local statistical forecast. Future weather adjustment is applied only when timestamped Home Assistant forecast rows are available; otherwise Zeus preserves the learned historical solar profile and lowers confidence. Planning learning may expose a bounded ±15% advisory solar correction only after its reusable-evidence thresholds are met; Recommendation Only mode does not apply that correction automatically and raw forecast values remain authoritative. Battery projection uses conservative generic efficiency and capacity assumptions until battery metadata is available.",
            "safety": "Forecast and recommendations only. No device control.",
            "recorder_safe": True,
        }
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class OptimizerEngine:
    """Generate transparent, recommendation-only energy and cost advice."""

    FLEXIBLE_TYPES = {"ev_charger", "water_heater", "heat_pump", "dishwasher", "washing_machine", "dryer", "pool_pump", "smart_plug", "custom"}

    def __init__(self, hass, event_bus, registry, energy_flow, forecast) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.energy_flow = energy_flow
        self.forecast = forecast
        self.last = {"status": "Waiting", "recommendations": [], "timeline": []}

    @staticmethod
    def _number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def refresh(self) -> dict[str, Any]:
        flow = self.energy_flow.summary()
        flows = flow.get("flows", {})
        def fw(key): return self._number((flows.get(key) or {}).get("w"), 0.0)
        solar_w, house_w = fw("solar_power"), fw("house_power")
        export_w, import_w = fw("grid_export_power"), fw("grid_import_power")
        charge_w, discharge_w = fw("battery_charge_power"), fw("battery_discharge_power")
        soc = flows.get("battery_soc_percent")
        available_surplus_w = max(export_w, solar_w - house_w - charge_w, 0.0)
        tariff = self.registry.data.get("tariffs", {}) or {}
        import_tariff = self._number(tariff.get("import_tariff"), 0.0)
        export_tariff = self._number(tariff.get("export_tariff"), 0.0)
        currency = str(tariff.get("currency") or "CHF")
        devices = [d for d in self.registry.data.get("devices", []) if d.get("enabled", True)]
        candidates = [d for d in devices if d.get("type", "custom") in self.FLEXIBLE_TYPES]
        recommendations=[]
        for device in sorted(candidates, key=lambda d: self._number(d.get("priority"), 50), reverse=True):
            power_entity=device.get("power_entity")
            current=0.0
            if power_entity and (st:=self.hass.states.get(power_entity)) is not None:
                current=self._number(st.state,0.0)
                if str(st.attributes.get("unit_of_measurement") or "").lower()=="kw": current*=1000
            required=max(self._number(device.get("rated_power_w"),0.0), current, 500.0)
            runtime=max(self._number(device.get("runtime_minutes"),60.0),15.0)
            energy=required*runtime/60000.0
            saving=max(import_tariff-export_tariff,0.0)*energy
            idle = current <= max(10.0, required * 0.08)
            if idle and available_surplus_w >= required * 0.75:
                action="Run now"; reason=f"Solar surplus ({available_surplus_w:.0f} W) can cover most of this device's expected demand."; confidence=min(97,72+int(available_surplus_w/max(required,1)*12))
            elif idle and export_w >= max(250.0, required * 0.30):
                action="Consider now"; reason=f"The home is exporting {export_w:.0f} W; using this device now could increase self-consumption."; confidence=74
            elif idle and import_w>750:
                action="Delay"; reason=f"Grid import is elevated at {import_w:.0f} W and no useful solar surplus is available."; confidence=82
            elif not idle:
                action="Hold"; reason="The device is already running, so no start recommendation is needed."; confidence=88
            else:
                action="Hold"; reason="No strong live optimization opportunity is present."; confidence=58
            device_name = device.get("name") or device.get("id") or "Unnamed device"
            action_title = {
                "Run now": f"Run {device_name} now",
                "Consider now": f"Consider running {device_name} now",
                "Delay": f"Delay {device_name}",
                "Hold": f"Hold {device_name}",
            }.get(action, f"{action} — {device_name}")
            recommendations.append({
                "title": action_title,
                "target": device_name,
                "device_id": device.get("id"),
                "device_name": device_name,
                "device_type": device.get("type") or "custom",
                "device_icon": device.get("icon"),
                "room_id": device.get("room_id") or device.get("room"),
                "action": action,
                "reason": reason,
                "confidence": confidence,
                "current_power_w": round(current, 1),
                "current_state": "Running" if current > 10 else "Idle",
                "required_power_w": round(required, 1),
                "recommended_duration_minutes": round(runtime),
                "expected_energy_kwh": round(energy, 3),
                "estimated_saving": round(saving, 2),
                "currency": currency,
            })
        if not recommendations:
            recommendations=[{"title":"Register a flexible device","action":"Hold","target":"Home","device_name":"Home","reason":"Register a flexible device to receive device-specific recommendations.","confidence":55,"estimated_saving":0,"currency":currency}]
        hourly=(self.forecast.summary().get("planning_hourly") or self.forecast.summary().get("hourly") or [])[:24]
        timeline=[]
        for row in hourly:
            surplus=self._number(row.get("surplus_power_w"),0.0)
            timeline.append({"time":row.get("time"),"solar_power_w":round(self._number(row.get("solar_power_w"),0.0),1),"surplus_power_w":round(surplus,1),"quality":"best" if surplus>=2000 else "good" if surplus>=500 else "low"})
        self.last={"status":"Ready","mode":"recommendation_only","recommendation_count":len(recommendations),"recommendations":recommendations[:8],"best_action":recommendations[0],"inputs":{"solar_power_w":round(solar_w,1),"house_power_w":round(house_w,1),"grid_import_w":round(import_w,1),"grid_export_w":round(export_w,1),"battery_charge_w":round(charge_w,1),"battery_discharge_w":round(discharge_w,1),"battery_soc_percent":soc,"available_surplus_w":round(available_surplus_w,1),"import_tariff":import_tariff,"export_tariff":export_tariff,"currency":currency,"forecast_confidence":self.forecast.summary().get("confidence_percent") or self.forecast.summary().get("confidence")},"timeline":timeline,"flexible_device_count":len(candidates),"summary":recommendations[0]["reason"],"safety":"Recommendation only. Zeus does not control devices."}
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class SchedulerEngine:
    """Create evidence-aware, recommendation-only flexible-load plans.

    Flexible Load Planning extends the existing canonical Scheduler rather than
    creating a parallel planning/accounting engine. Forecast slots, registered
    devices and configured tariffs remain authoritative. Type-default power or
    runtime profiles may rank a planning suggestion, but are explicitly marked
    as assumptions and are withheld from quantified kWh/CHF opportunity totals.
    Zeus never calls Home Assistant services or changes device state.
    """

    VERSION = "10.20"
    DEFAULT_PROFILES = {
        "dishwasher": (1200.0, 120), "washing_machine": (700.0, 90),
        "dryer": (2200.0, 100), "ev_charger": (3700.0, 180),
        "water_heater": (2000.0, 90), "heat_pump": (1800.0, 120),
        "pool_pump": (900.0, 180), "air_conditioner": (1200.0, 120),
        "smart_plug": (800.0, 60), "custom": (1000.0, 60),
    }
    PRIORITY_WEIGHT = {"high": 30.0, "medium": 15.0, "low": 0.0}
    AUTO_FLEXIBLE_TYPES = {
        "ev_charger", "water_heater", "heat_pump", "dishwasher",
        "washing_machine", "dryer", "pool_pump", "air_conditioner",
    }
    ROLE_LABELS = {
        "ev_charger": "EV / Car", "water_heater": "DHW", "heat_pump": "Heat Pump",
        "dishwasher": "Dishwasher", "washing_machine": "Washing Machine", "dryer": "Dryer",
        "pool_pump": "Pool Pump", "air_conditioner": "Air Conditioner", "smart_plug": "Smart Plug",
        "custom": "Custom Load",
    }
    TYPE_ALIASES = {
        "ev": "ev_charger", "ev charger": "ev_charger", "ev_charger": "ev_charger",
        "wallbox": "ev_charger", "car": "ev_charger", "vehicle": "ev_charger",
        "dhw": "water_heater", "water heater": "water_heater", "water_heater": "water_heater",
        "hot water": "water_heater", "hot_water": "water_heater", "boiler": "water_heater",
        "heat pump": "heat_pump", "heat_pump": "heat_pump", "heatpump": "heat_pump",
        "dishwasher": "dishwasher", "washing machine": "washing_machine", "washing_machine": "washing_machine",
        "washer": "washing_machine", "dryer": "dryer", "tumble dryer": "dryer",
        "pool pump": "pool_pump", "pool_pump": "pool_pump",
        "air conditioner": "air_conditioner", "air_conditioner": "air_conditioner", "ac": "air_conditioner",
        "smart plug": "smart_plug", "smart_plug": "smart_plug",
        "custom": "custom", "other": "custom", "generic load": "custom",
    }

    def __init__(self, event_bus, registry, forecast, optimizer, device_analytics=None) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self.forecast = forecast
        self.optimizer = optimizer
        self.device_analytics = device_analytics
        self.last = {"status": "Waiting", "plan": [], "schedule": []}

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _canonical_device_type(cls, device: dict[str, Any]) -> str:
        raw = str(device.get("type") or device.get("device_type") or "").strip().lower()
        normalized = re.sub(r"[_\-]+", " ", raw)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return cls.TYPE_ALIASES.get(raw, cls.TYPE_ALIASES.get(normalized, raw.replace(" ", "_") or "custom"))

    @classmethod
    def _is_flexible_load(cls, device: dict[str, Any]) -> tuple[bool, str, str]:
        dtype = cls._canonical_device_type(device)
        if not is_consuming_load(device):
            return False, dtype, "excluded_non_consuming_source_or_meter"
        if device.get("flexible") is False:
            return False, dtype, "explicit_flexible_false"
        if device.get("flexible") is True:
            return True, dtype, "explicit_flexible_true"
        category = str(device.get("category") or "").strip().lower().replace("-", "_").replace(" ", "_")
        groups = {str(x).strip().lower().replace("-", "_").replace(" ", "_") for x in (device.get("group_ids") or [])}
        if category in {"flexible", "flexible_load"}:
            return True, dtype, "explicit_flexible_load_category"
        if "flexible_loads" in groups or "flexible_load" in groups:
            return True, dtype, "explicit_flexible_load_group"
        if dtype in cls.AUTO_FLEXIBLE_TYPES:
            return True, dtype, "canonical_auto_flexible_type"
        if dtype in {"custom", "smart_plug"}:
            return False, dtype, "generic_load_requires_explicit_flexible_evidence"
        return False, dtype, "not_marked_flexible"

    @staticmethod
    def _dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _historical_profile(self, device: dict[str, Any]) -> dict[str, Any] | None:
        if self.device_analytics is None:
            return None
        summary = self.device_analytics.summary() or {}
        device_id = str(device.get("id") or "")
        for row in summary.get("devices") or []:
            if str(row.get("id") or "") != device_id:
                continue
            profile = row.get("historical_planning_profile")
            if not isinstance(profile, dict):
                return None
            result = dict(profile)
            sources = row.get("planning_evidence_sources")
            if isinstance(sources, dict):
                result["source_diagnostics"] = dict(sources)
            return result
        return None

    def _profile(self, device: dict[str, Any]) -> dict[str, Any]:
        """Resolve a planning profile from canonical registered device evidence.

        Qualification is deliberately strict: type defaults may rank a load but
        never create quantified kWh/CHF. A load becomes quantification-supported
        only from a registered energy requirement, or from registered power plus
        registered runtime/duration evidence.
        """
        dtype = self._canonical_device_type(device)
        default_power, default_minutes = self.DEFAULT_PROFILES.get(dtype, self.DEFAULT_PROFILES["custom"])

        def first_value(keys):
            for key in keys:
                value = device.get(key)
                if value is not None and value != "":
                    return value, key
            return None, None

        power_value, power_key = first_value((
            "expected_power_w", "rated_power_w", "nominal_power_w", "power_w",
            "device_power_w", "max_power_w",
        ))
        runtime_value, runtime_key = first_value((
            "runtime_minutes", "expected_runtime_minutes", "duration_minutes",
            "cycle_minutes", "planned_runtime_minutes",
        ))
        energy_value, energy_key = first_value((
            "expected_energy_kwh", "energy_requirement_kwh", "required_energy_kwh",
            "target_energy_kwh", "cycle_energy_kwh",
        ))

        explicit_power = power_key is not None
        explicit_runtime = runtime_key is not None
        explicit_energy = energy_key is not None

        power = max(50.0, self._number(power_value, default_power))
        minutes = int(max(15, min(24 * 60, self._number(runtime_value, default_minutes))))
        energy = max(0.01, self._number(energy_value, power * minutes / 60000.0))

        registered_fields = []
        if explicit_energy:
            registered_fields.append(energy_key)
        if explicit_power:
            registered_fields.append(power_key)
        if explicit_runtime:
            registered_fields.append(runtime_key)

        historical = self._historical_profile(device)
        historical_supported = bool(historical and historical.get("quantification_supported") is True)

        if explicit_energy:
            source = "registered_energy_requirement"
            supported = True
            evidence_confidence = 100
        elif explicit_power and explicit_runtime:
            source = "registered_power_and_runtime"
            supported = True
            evidence_confidence = 100
        elif historical_supported:
            source = "device_analytics_historical_profile"
            supported = True
            evidence_confidence = int(self._number(historical.get("confidence_percent"), 70))
            power = max(50.0, self._number(historical.get("typical_power_w"), power))
            minutes = int(max(15, min(24 * 60, self._number(historical.get("typical_runtime_minutes"), minutes))))
            energy = max(0.01, self._number(historical.get("typical_energy_kwh"), power * minutes / 60000.0))
        elif explicit_power or explicit_runtime:
            source = "partial_registered_profile_with_type_default"
            supported = False
            evidence_confidence = None
        else:
            source = "type_default_profile"
            supported = False
            evidence_confidence = None

        missing = []
        if not supported:
            if not explicit_energy and not explicit_power:
                missing.append("registered power or registered energy requirement")
            if not explicit_energy and not explicit_runtime:
                missing.append("registered runtime/duration or registered energy requirement")
            if historical:
                missing.extend(str(x) for x in (historical.get("missing_evidence") or []) if x)

        if supported:
            assumption = (
                None if source.startswith("registered_") else
                "Quantification uses a canonical Device Analytics historical typical active-day profile; it is an evidence-based estimate, not a registered fixed cycle."
            )
        else:
            missing_text = "; ".join(dict.fromkeys(missing)) if missing else "complete registered or historical profile evidence"
            assumption = (
                f"Uses the Scheduler default {dtype} planning profile "
                f"({default_power:.0f} W, {default_minutes} min) for ranking only. "
                f"Missing evidence: {missing_text}."
            )

        return {
            "power_w": power,
            "runtime_minutes": minutes,
            "energy_kwh": energy,
            "profile_source": source,
            "registered_fields": registered_fields,
            "historical_evidence": historical,
            "evidence_confidence_percent": evidence_confidence,
            "missing_evidence": list(dict.fromkeys(missing)),
            "qualification_rule": "registered_energy_requirement OR registered_power_plus_runtime OR qualified_device_analytics_historical_profile",
            "quantification_supported": supported,
            "assumption": assumption,
        }

    def _allowed(self, device: dict[str, Any], start: datetime, end: datetime) -> bool:
        earliest = self._dt(device.get("earliest_start") or device.get("schedule_earliest"))
        deadline = self._dt(device.get("deadline") or device.get("schedule_deadline") or device.get("latest_end"))
        if earliest and start < earliest:
            return False
        if deadline and end > deadline:
            return False
        allowed_hours = device.get("allowed_start_hours")
        if isinstance(allowed_hours, list) and allowed_hours:
            try:
                if start.hour not in {int(x) for x in allowed_hours}:
                    return False
            except (TypeError, ValueError):
                pass
        quiet = device.get("quiet_hours")
        if isinstance(quiet, dict):
            q_start, q_end = quiet.get("start"), quiet.get("end")
            try:
                q_start, q_end = int(q_start), int(q_end)
                in_quiet = q_start <= start.hour < q_end if q_start < q_end else start.hour >= q_start or start.hour < q_end
                if in_quiet:
                    return False
            except (TypeError, ValueError):
                pass
        return True

    def refresh(self) -> dict[str, Any]:
        forecast = self.forecast.summary()
        optimizer = self.optimizer.summary()
        tariffs = self.registry.data.get("sources", {}).get("tariffs", {})
        import_rate = self._number(tariffs.get("import_tariff"), 0.0)
        export_rate = self._number(tariffs.get("export_tariff"), 0.0)
        currency = str(tariffs.get("currency") or "CHF").upper()[:4]
        tariff_enabled = bool(tariffs.get("enabled"))
        now = datetime.now(timezone.utc)

        # Build a rolling 48-hour planning horizon from *now*, not from the
        # forecast array's midnight origin. Forecast publishes 168 canonical
        # hourly rows, so slicing the first 48 before removing elapsed hours
        # could leave too little (or no) tomorrow horizon late in the day.
        raw_slots = []
        forecast_slots = forecast.get("planning_hourly") or forecast.get("hourly") or []
        for row in forecast_slots:
            start = self._dt(row.get("time"))
            if not start or start < now - timedelta(hours=1):
                continue
            raw_slots.append({
                "start": start, "solar_w": max(0.0, self._number(row.get("solar_power_w"))),
                "house_w": max(0.0, self._number(row.get("house_power_w"))),
                "surplus_w": max(0.0, self._number(row.get("surplus_power_w"))),
                "condition": row.get("condition"), "sample_count": int(self._number(row.get("sample_count"))),
            })
            if len(raw_slots) >= 48:
                break

        # Runtime diagnostics belong to Scheduler and are created immediately
        # after the canonical rolling Forecast horizon is assembled.
        slot_diagnostics = {
            "forecast_planning_row_count": len(forecast_slots),
            "rolling_slot_count": len(raw_slots),
            "surplus_slot_count": sum(1 for x in raw_slots if x.get("surplus_w", 0) > 0),
            "strong_surplus_slot_count": sum(1 for x in raw_slots if x.get("surplus_w", 0) >= 500),
            "max_surplus_w": round(max((self._number(x.get("surplus_w")) for x in raw_slots), default=0.0), 1),
            "first_slot": raw_slots[0]["start"].isoformat() if raw_slots else None,
            "last_slot": raw_slots[-1]["start"].isoformat() if raw_slots else None,
            "source": "Canonical Forecast planning_hourly",
        }

        devices = [d for d in self.registry.data.get("devices", []) if d.get("enabled", True)]
        classification_diagnostics: list[dict[str, Any]] = []
        flexible = []
        for source_device in devices:
            allowed, dtype, reason = self._is_flexible_load(source_device)
            classification_diagnostics.append({
                "device_id": source_device.get("id"),
                "device_name": source_device.get("name") or source_device.get("id"),
                "stored_type": source_device.get("type") or source_device.get("device_type"),
                "canonical_type": dtype,
                "category": source_device.get("category"),
                "group_ids": list(source_device.get("group_ids") or []),
                "flexible_flag": source_device.get("flexible"),
                "included": allowed,
                "reason": reason,
            })
            if allowed:
                device = dict(source_device)
                device["type"] = dtype
                flexible.append(device)
        flexible.sort(key=lambda d: self.PRIORITY_WEIGHT.get(str(d.get("priority") or "medium").lower(), 15.0), reverse=True)
        reserved: dict[str, float] = {}
        schedule: list[dict[str, Any]] = []
        unscheduled: list[dict[str, Any]] = []

        candidate_diagnostics: list[dict[str, Any]] = []
        for device in flexible:
            profile = self._profile(device)
            power, runtime, energy = profile["power_w"], profile["runtime_minutes"], profile["energy_kwh"]
            hours_needed = max(1, int((runtime + 59) // 60))
            candidates = []
            total_blocks = max(0, len(raw_slots) - hours_needed + 1)
            rejected_by_constraints = 0
            for i in range(0, total_blocks):
                block = raw_slots[i:i + hours_needed]
                start, end = block[0]["start"], block[-1]["start"] + timedelta(hours=1)
                if not self._allowed(device, start, end):
                    rejected_by_constraints += 1
                    continue
                available = [max(0.0, x["surplus_w"] - reserved.get(x["start"].isoformat(), 0.0)) for x in block]
                covered_w = sum(min(power, x) for x in available) / hours_needed
                solar_share = min(1.0, covered_w / power)
                grid_energy = energy * (1.0 - solar_share)
                exported_energy_avoided = energy * solar_share
                cost = grid_energy * import_rate + exported_energy_avoided * export_rate if tariff_enabled and profile["quantification_supported"] else None
                saving = energy * import_rate - cost if cost is not None else None
                continuity = min(available) / power if available else 0.0
                samples = sum(x["sample_count"] for x in block) / hours_needed
                priority = self.PRIORITY_WEIGHT.get(str(device.get("priority") or "medium").lower(), 15.0)
                score = solar_share * 65 + min(continuity, 1.0) * 15 + min(samples / 20, 1.0) * 10 + priority
                candidates.append((score, start, end, block, solar_share, cost, saving))
            candidate_diagnostics.append({
                "device_id": device.get("id"),
                "device_name": device.get("name") or device.get("id"),
                "device_type": device.get("type") or "custom",
                "hours_needed": hours_needed,
                "total_forecast_blocks": total_blocks,
                "constraint_rejected_blocks": rejected_by_constraints,
                "candidate_blocks": len(candidates),
                "profile_source": profile.get("profile_source"),
                "quantification_supported": profile.get("quantification_supported"),
            })
            if not candidates:
                unscheduled.append({
                    "device_id": device.get("id"), "device_name": device.get("name"), "device_type": device.get("type") or "custom",
                    "role": self.ROLE_LABELS.get(str(device.get("type") or "custom"), str(device.get("type") or "custom").replace("_", " ").title()),
                    "reason": "No forecast window satisfies the available timing constraints.",
                    "profile_evidence": profile,
                })
                continue
            score, start, end, block, solar_share, cost, saving = max(candidates, key=lambda x: x[0])
            for slot in block:
                key = slot["start"].isoformat()
                reserved[key] = reserved.get(key, 0.0) + power
            confidence = int(max(35, min(96, 45 + solar_share * 35 + min(sum(x["sample_count"] for x in block) / max(hours_needed, 1), 20) * 0.8)))
            if not profile["quantification_supported"]:
                confidence = min(confidence, 72)
            reason = (f"Uses an estimated {solar_share * 100:.0f}% forecast solar coverage across a continuous {runtime}-minute window."
                      if solar_share > 0 else "Selected as the least-cost available window within the forecast horizon.")
            if profile["assumption"]:
                reason += f" {profile['assumption']}"
            dtype = str(device.get("type") or "custom")
            item = {
                "device_id": device.get("id"), "device_name": device.get("name") or device.get("id"),
                "device_type": dtype, "role": self.ROLE_LABELS.get(dtype, dtype.replace("_", " ").title()), "icon": device.get("icon"),
                "priority": device.get("priority") or "medium", "suggested_start": start.isoformat(),
                "suggested_end": end.isoformat(), "duration_minutes": runtime,
                "expected_power_w": round(power, 1), "expected_energy_kwh": round(energy, 3),
                "solar_coverage_percent": round(solar_share * 100, 1),
                "estimated_cost": round(cost, 3) if cost is not None else None,
                "estimated_saving": round(saving, 3) if saving is not None else None,
                "currency": currency, "confidence_percent": confidence, "score": round(score, 1),
                "weather_condition": block[0].get("condition"), "reason": reason,
                "mode": "recommendation_only", "constraints_applied": any(device.get(k) for k in ("earliest_start", "schedule_earliest", "deadline", "schedule_deadline", "latest_end", "allowed_start_hours", "quiet_hours")),
                "profile_evidence": {
                    "source": profile["profile_source"], "registered_fields": profile["registered_fields"],
                    "missing_evidence": profile.get("missing_evidence", []),
                    "qualification_rule": profile.get("qualification_rule"),
                    "evidence_confidence_percent": profile.get("evidence_confidence_percent"),
                    "historical_profile": ({
                        "status": (profile.get("historical_evidence") or {}).get("status"),
                        "active_days": (profile.get("historical_evidence") or {}).get("active_days"),
                        "typical_energy_kwh": (profile.get("historical_evidence") or {}).get("typical_energy_kwh"),
                        "typical_runtime_minutes": (profile.get("historical_evidence") or {}).get("typical_runtime_minutes"),
                        "typical_power_w": (profile.get("historical_evidence") or {}).get("typical_power_w"),
                        "energy_relative_spread": (profile.get("historical_evidence") or {}).get("energy_relative_spread"),
                        "runtime_relative_spread": (profile.get("historical_evidence") or {}).get("runtime_relative_spread"),
                        "power_relative_spread": (profile.get("historical_evidence") or {}).get("power_relative_spread"),
                        "confidence_percent": (profile.get("historical_evidence") or {}).get("confidence_percent"),
                        "aligned_recorder_runtime_days": (profile.get("historical_evidence") or {}).get("aligned_recorder_runtime_days"),
                        "aligned_datalake_runtime_days": (profile.get("historical_evidence") or {}).get("aligned_datalake_runtime_days"),
                        "missing_evidence": (profile.get("historical_evidence") or {}).get("missing_evidence") or [],
                        "source_diagnostics": (profile.get("historical_evidence") or {}).get("source_diagnostics"),
                    } if profile.get("historical_evidence") else None),
                    "quantification_supported": profile["quantification_supported"], "assumption": profile["assumption"],
                },
                "quantification_supported": profile["quantification_supported"],
                "planning_evidence_quality": "supported" if profile["quantification_supported"] else "assumption_limited",
            }
            schedule.append(item)

        # Calendar-specific tomorrow advisory plan. This is intentionally
        # independent from the rolling 48-hour schedule above: today's chosen
        # windows do not reserve tomorrow's forecast capacity. It consumes the
        # same canonical Forecast rows and registered constraints, and it does
        # not create a second forecast or automatic schedule.
        local_today = dt_util.as_local(now).date()
        tomorrow_date = local_today + timedelta(days=1)
        tomorrow_slots = [
            x for x in raw_slots
            if dt_util.as_local(x["start"]).date() == tomorrow_date
        ]
        tomorrow_reserved: dict[str, float] = {}
        tomorrow_plan: list[dict[str, Any]] = []
        tomorrow_diagnostics: list[dict[str, Any]] = []

        for device in flexible:
            profile = self._profile(device)
            power, runtime, energy = profile["power_w"], profile["runtime_minutes"], profile["energy_kwh"]
            hours_needed = max(1, int((runtime + 59) // 60))
            candidates = []
            total_blocks = max(0, len(tomorrow_slots) - hours_needed + 1)
            rejected_by_constraints = 0
            for i in range(0, total_blocks):
                block = tomorrow_slots[i:i + hours_needed]
                start, end = block[0]["start"], block[-1]["start"] + timedelta(hours=1)
                # Keep every block entirely inside tomorrow's local calendar day.
                if dt_util.as_local(end - timedelta(seconds=1)).date() != tomorrow_date:
                    continue
                if not self._allowed(device, start, end):
                    rejected_by_constraints += 1
                    continue
                available = [
                    max(0.0, x["surplus_w"] - tomorrow_reserved.get(x["start"].isoformat(), 0.0))
                    for x in block
                ]
                covered_w = sum(min(power, x) for x in available) / hours_needed
                solar_share = min(1.0, covered_w / power)
                grid_energy = energy * (1.0 - solar_share)
                exported_energy_avoided = energy * solar_share
                cost = (
                    grid_energy * import_rate + exported_energy_avoided * export_rate
                    if tariff_enabled and profile["quantification_supported"] else None
                )
                saving = energy * import_rate - cost if cost is not None else None
                continuity = min(available) / power if available else 0.0
                samples = sum(x["sample_count"] for x in block) / hours_needed
                priority = self.PRIORITY_WEIGHT.get(str(device.get("priority") or "medium").lower(), 15.0)
                score = solar_share * 65 + min(continuity, 1.0) * 15 + min(samples / 20, 1.0) * 10 + priority
                candidates.append((score, start, end, block, solar_share, cost, saving))

            tomorrow_diagnostics.append({
                "device_id": device.get("id"),
                "device_name": device.get("name") or device.get("id"),
                "device_type": device.get("type") or "custom",
                "total_forecast_blocks": total_blocks,
                "constraint_rejected_blocks": rejected_by_constraints,
                "candidate_blocks": len(candidates),
            })
            if not candidates:
                continue

            score, start, end, block, solar_share, cost, saving = max(candidates, key=lambda x: x[0])
            for slot in block:
                key = slot["start"].isoformat()
                tomorrow_reserved[key] = tomorrow_reserved.get(key, 0.0) + power

            confidence = int(max(35, min(96, 45 + solar_share * 35 + min(sum(x["sample_count"] for x in block) / max(hours_needed, 1), 20) * 0.8)))
            if not profile["quantification_supported"]:
                confidence = min(confidence, 72)
            dtype = str(device.get("type") or "custom")
            tomorrow_plan.append({
                "device_id": device.get("id"),
                "device_name": device.get("name") or device.get("id"),
                "device_type": dtype,
                "role": self.ROLE_LABELS.get(dtype, dtype.replace("_", " ").title()),
                "priority": device.get("priority") or "medium",
                "suggested_start": start.isoformat(),
                "suggested_end": end.isoformat(),
                "duration_minutes": runtime,
                "expected_energy_kwh": round(energy, 3),
                "solar_coverage_percent": round(solar_share * 100, 1),
                "estimated_saving": round(saving, 3) if saving is not None else None,
                "currency": currency,
                "confidence_percent": confidence,
                "score": round(score, 1),
                "quantification_supported": profile["quantification_supported"],
                "planning_evidence_quality": "supported" if profile["quantification_supported"] else "assumption_limited",
                "profile_evidence": {
                    "source": profile["profile_source"],
                    "registered_fields": profile["registered_fields"],
                    "missing_evidence": profile.get("missing_evidence", []),
                    "qualification_rule": profile.get("qualification_rule"),
                    "evidence_confidence_percent": profile.get("evidence_confidence_percent"),
                    "historical_profile": ({
                        "status": (profile.get("historical_evidence") or {}).get("status"),
                        "active_days": (profile.get("historical_evidence") or {}).get("active_days"),
                        "typical_energy_kwh": (profile.get("historical_evidence") or {}).get("typical_energy_kwh"),
                        "typical_runtime_minutes": (profile.get("historical_evidence") or {}).get("typical_runtime_minutes"),
                        "typical_power_w": (profile.get("historical_evidence") or {}).get("typical_power_w"),
                        "energy_relative_spread": (profile.get("historical_evidence") or {}).get("energy_relative_spread"),
                        "runtime_relative_spread": (profile.get("historical_evidence") or {}).get("runtime_relative_spread"),
                        "power_relative_spread": (profile.get("historical_evidence") or {}).get("power_relative_spread"),
                        "confidence_percent": (profile.get("historical_evidence") or {}).get("confidence_percent"),
                        "aligned_recorder_runtime_days": (profile.get("historical_evidence") or {}).get("aligned_recorder_runtime_days"),
                        "aligned_datalake_runtime_days": (profile.get("historical_evidence") or {}).get("aligned_datalake_runtime_days"),
                        "missing_evidence": (profile.get("historical_evidence") or {}).get("missing_evidence") or [],
                        "source_diagnostics": (profile.get("historical_evidence") or {}).get("source_diagnostics"),
                    } if profile.get("historical_evidence") else None),
                    "quantification_supported": profile["quantification_supported"],
                    "assumption": profile["assumption"],
                },
                "mode": "recommendation_only",
            })

        tomorrow_plan.sort(key=lambda x: (-self._number(x.get("score")), -self._number(x.get("solar_coverage_percent")), str(x.get("suggested_start") or "")))
        for rank, item in enumerate(tomorrow_plan, start=1):
            item["planning_rank"] = rank

        # Chronological schedule remains the canonical execution-order preview.
        schedule.sort(key=lambda x: x["suggested_start"])
        # Recommendation order is explicit and separate from chronology.
        recommendation_order = sorted(schedule, key=lambda x: (-self._number(x.get("score")), -self._number(x.get("solar_coverage_percent")), str(x.get("suggested_start") or "")))
        for rank, item in enumerate(recommendation_order, start=1):
            item["planning_rank"] = rank

        supported = [x for x in schedule if x.get("quantification_supported")]
        total_energy = round(sum(x["expected_energy_kwh"] for x in schedule), 3)
        supported_energy = round(sum(x["expected_energy_kwh"] for x in supported), 3)
        supported_solar_energy = round(sum(x["expected_energy_kwh"] * x["solar_coverage_percent"] / 100.0 for x in supported), 3)
        total_saving = round(sum(x.get("estimated_saving") or 0 for x in supported), 3) if tariff_enabled and supported else (0.0 if tariff_enabled and schedule and supported else None)
        avg_solar = round(sum(x["solar_coverage_percent"] for x in schedule) / len(schedule), 1) if schedule else 0.0

        roles: dict[str, dict[str, Any]] = {}
        for item in recommendation_order:
            role = item["role"]
            bucket = roles.setdefault(role, {
                "role": role, "device_type": item["device_type"], "device_count": 0, "devices": [],
                "supported_energy_kwh": 0.0, "supported_solar_covered_energy_kwh": 0.0,
                "highest_score": 0.0, "best_window": None, "confidence_percent": 0,
            })
            bucket["device_count"] += 1
            bucket["devices"].append({"device_id": item.get("device_id"), "device_name": item.get("device_name"), "planning_rank": item.get("planning_rank"), "quantification_supported": item.get("quantification_supported")})
            if item.get("quantification_supported"):
                bucket["supported_energy_kwh"] += self._number(item.get("expected_energy_kwh"))
                bucket["supported_solar_covered_energy_kwh"] += self._number(item.get("expected_energy_kwh")) * self._number(item.get("solar_coverage_percent")) / 100.0
            if self._number(item.get("score")) > bucket["highest_score"]:
                bucket["highest_score"] = self._number(item.get("score"))
                bucket["best_window"] = item.get("suggested_start")
                bucket["confidence_percent"] = int(self._number(item.get("confidence_percent")))
        role_plan = sorted(roles.values(), key=lambda x: (-x["highest_score"], str(x["role"])))
        for rank, row in enumerate(role_plan, start=1):
            row["planning_rank"] = rank
            row["supported_energy_kwh"] = round(row["supported_energy_kwh"], 3)
            row["supported_solar_covered_energy_kwh"] = round(row["supported_solar_covered_energy_kwh"], 3)
            row["highest_score"] = round(row["highest_score"], 1)

        limitations = []
        assumed_count = sum(1 for x in schedule if not x.get("quantification_supported"))
        qualification_diagnostics = []
        for item in recommendation_order:
            evidence = item.get("profile_evidence") or {}
            historical = evidence.get("historical_profile") if isinstance(evidence.get("historical_profile"), dict) else {}
            qualification_diagnostics.append({
                "device_id": item.get("device_id"),
                "device_name": item.get("device_name"),
                "device_type": item.get("device_type"),
                "quantification_supported": bool(item.get("quantification_supported")),
                "profile_source": evidence.get("source"),
                "evidence_confidence_percent": evidence.get("evidence_confidence_percent"),
                "registered_fields": evidence.get("registered_fields") or [],
                "missing_evidence": evidence.get("missing_evidence") or [],
                "qualification_rule": evidence.get("qualification_rule"),
                "historical_status": historical.get("status"),
                "historical_active_days": historical.get("active_days"),
                "historical_typical_energy_kwh": historical.get("typical_energy_kwh"),
                "historical_typical_runtime_minutes": historical.get("typical_runtime_minutes"),
                "historical_typical_power_w": historical.get("typical_power_w"),
                "historical_energy_relative_spread": historical.get("energy_relative_spread"),
                "historical_runtime_relative_spread": historical.get("runtime_relative_spread"),
                "historical_power_relative_spread": historical.get("power_relative_spread"),
                "historical_confidence_percent": historical.get("confidence_percent"),
                "historical_aligned_recorder_runtime_days": historical.get("aligned_recorder_runtime_days"),
                "historical_aligned_datalake_runtime_days": historical.get("aligned_datalake_runtime_days"),
                "historical_missing_evidence": historical.get("missing_evidence") or [],
                "source_status": (historical.get("source_diagnostics") or {}).get("status"),
                "power_entity": ((historical.get("source_diagnostics") or {}).get("power") or {}).get("entity_id"),
                "power_entity_available": ((historical.get("source_diagnostics") or {}).get("power") or {}).get("available"),
                "energy_entity": ((historical.get("source_diagnostics") or {}).get("energy") or {}).get("entity_id"),
                "energy_entity_available": ((historical.get("source_diagnostics") or {}).get("energy") or {}).get("available"),
                "state_entity": ((historical.get("source_diagnostics") or {}).get("state") or {}).get("entity_id"),
                "state_entity_available": ((historical.get("source_diagnostics") or {}).get("state") or {}).get("available"),
                "recorder_energy_day_count": (historical.get("source_diagnostics") or {}).get("recorder_energy_day_count"),
                "data_lake_device_day_count": (historical.get("source_diagnostics") or {}).get("data_lake_device_day_count"),
                "data_lake_energy_day_count": (historical.get("source_diagnostics") or {}).get("data_lake_energy_day_count"),
                "data_lake_runtime_day_count": (historical.get("source_diagnostics") or {}).get("data_lake_runtime_day_count"),
                "data_lake_combined_profile_day_count": (historical.get("source_diagnostics") or {}).get("data_lake_combined_profile_day_count"),
                "data_lake_sample_day_count": (historical.get("source_diagnostics") or {}).get("data_lake_sample_day_count"),
                "source_blocking_reasons": (historical.get("source_diagnostics") or {}).get("blocking_reasons") or [],
            })
        if assumed_count:
            limitations.append(f"{assumed_count} planned load(s) use type-default or partial profiles. Zeus may rank their windows, but withholds their kWh/CHF from Opportunity Quantification until sufficient device profile evidence exists.")
        if not flexible:
            limitations.append("No registered flexible loads are available to plan.")
        if flexible and not raw_slots:
            limitations.append("Canonical future forecast slots are unavailable, so no flexible-load window can be recommended.")

        status = "Ready" if raw_slots else "Waiting"
        summary = (f"Planned {len(schedule)} flexible load(s); {len(supported)} have sufficient profile evidence for quantified opportunity, with {avg_solar:.0f}% average forecast solar coverage."
                   if schedule else ("No flexible-load candidate window is available in the rolling 48-hour canonical forecast horizon." if flexible else "No flexible-load plan yet; register a flexible device."))
        self.last = {
            "status": status, "engine": "Intelligent Scheduler", "version": self.VERSION,
            "foundation": "Adaptive Energy Optimization · Flexible Load Planning",
            "mode": "recommendation_only", "horizon_hours": 48, "horizon_start": (raw_slots[0]["start"].isoformat() if raw_slots else None), "horizon_end": (raw_slots[-1]["start"].isoformat() if raw_slots else None), "generated_at": now.isoformat(),
            "plan_count": len(schedule), "schedule_count": len(schedule), "plan": schedule, "schedule": schedule,
            "recommended_order": recommendation_order, "role_plan": role_plan,
            "tomorrow_date": tomorrow_date.isoformat(),
            "tomorrow_plan": tomorrow_plan,
            "tomorrow_plan_count": len(tomorrow_plan),
            "tomorrow_candidate_diagnostics": tomorrow_diagnostics,
            "unscheduled_device_count": len(unscheduled), "unscheduled": unscheduled,
            "flexible_device_count": len(flexible), "total_planned_energy_kwh": total_energy,
            "quantified_planned_energy_kwh": supported_energy,
            "quantified_solar_covered_energy_kwh": supported_solar_energy,
            "quantified_device_count": len(supported), "assumption_limited_device_count": assumed_count,
            "qualification_diagnostics": qualification_diagnostics,
            "qualification_rule": "registered_energy_requirement OR registered_power_plus_runtime OR qualified_device_analytics_historical_profile",
            "average_solar_coverage_percent": avg_solar, "estimated_total_saving": total_saving,
            "currency": currency, "tariff_aware": tariff_enabled,
            "forecast_confidence": forecast.get("confidence_percent") or forecast.get("confidence"),
            "candidate_diagnostics": {
                "slots": slot_diagnostics,
                "classification": classification_diagnostics,
                "registry_enabled_device_count": len(devices),
                "classified_flexible_device_count": len(flexible),
                "devices": candidate_diagnostics,
                "all_candidates_blocked_by_constraints": bool(candidate_diagnostics) and all(
                    d.get("candidate_blocks", 0) == 0 and d.get("constraint_rejected_blocks", 0) >= d.get("total_forecast_blocks", 0) and d.get("total_forecast_blocks", 0) > 0
                    for d in candidate_diagnostics
                ),
            },
            "optimizer_context": optimizer.get("best_action"), "summary": summary,
            "method": "Continuous-window allocation using canonical forecast surplus, optional configured tariffs, registered-device priority/timing constraints and evidence-labeled device profiles.",
            "planning_policy": "Established schedulable load roles may enter advisory planning automatically. Generic/custom and smart-plug loads require explicit flexible=true or flexible-load category/group evidence. Default profiles may rank recommendations but cannot create quantified kWh/CHF opportunity. Functional roles aggregate registered devices while individual devices remain distinct.",
            "limitations": limitations,
            "safety": "Recommendation only. Zeus does not call services, start devices, change schedules or modify registry data.",
        }
        self.event_bus.publish("IntelligentScheduleUpdated", "SchedulerEngine", {
            "scheduled": len(schedule), "quantified": len(supported), "assumption_limited": assumed_count,
            "unscheduled": len(unscheduled), "horizon_hours": 48, "recommendation_only": True,
        })
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last


class DeviceAnalyticsEngine:
    """Per-device energy, runtime and period analytics from stored daily summaries."""

    def __init__(self, hass, event_bus, data_lake, registry) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.data_lake = data_lake
        self.registry = registry
        self.last = {"status": "Waiting", "devices": []}
        self._recorder_days: dict[str, dict[str, float]] = {}
        self._recorder_status: dict[str, Any] = {"status": "Not loaded", "entity_count": 0, "row_count": 0}


    async def async_refresh_recorder_energy(self) -> None:
        """Load authoritative daily energy for every registered device.

        Cumulative total_increasing meters use Recorder's daily change. Daily
        reset/measurement meters use the daily maximum (or final state).
        """
        devices = [d for d in self.registry.data.get("devices", []) if is_consuming_load(d) and d.get("energy_entity")]
        entity_ids = list(dict.fromkeys(str(d.get("energy_entity")) for d in devices if d.get("energy_entity")))
        if not entity_ids:
            self._recorder_days = {}
            self._recorder_status = {"status": "No mapped device energy entities", "entity_count": 0, "row_count": 0}
            return
        try:
            now = dt_util.now()
            start_local = dt_util.start_of_local_day(now - timedelta(days=401))
            end_local = dt_util.start_of_local_day(now + timedelta(days=1))
            response = await self.hass.services.async_call(
                "recorder", "get_statistics",
                {
                    "statistic_ids": entity_ids,
                    "start_time": start_local,
                    "end_time": end_local,
                    "period": "day",
                    "types": ["change", "max", "state"],
                    "units": {"energy": "kWh"},
                },
                blocking=True, return_response=True,
            )
            raw_stats = (response or {}).get("statistics", response or {})
            result: dict[str, dict[str, float]] = {}
            for device in devices:
                entity_id = str(device.get("energy_entity"))
                state = self.hass.states.get(entity_id)
                state_class = str((state.attributes.get("state_class") if state else "") or "").lower()
                friendly = str((state.attributes.get("friendly_name") if state else "") or "").lower()
                configured_type = str(device.get("energy_type") or "auto").lower()
                identifier = f"{entity_id.lower()} {friendly}"
                daily_meter = configured_type == "daily" or state_class == "measurement" or any(
                    token in identifier for token in ("today", "daily", "day_energy", "energy_day", "daily_energy")
                )
                for row in list((raw_stats or {}).get(entity_id) or []):
                    if not isinstance(row, dict):
                        continue
                    value = self._num_stat(row.get("max") if daily_meter else row.get("change"))
                    if value is None and daily_meter:
                        value = self._num_stat(row.get("state"))
                    if value is None or value < -0.001:
                        continue
                    stamp = self._statistics_start_datetime(row.get("start"))
                    if stamp is None:
                        continue
                    day = dt_util.as_local(stamp).date().isoformat()
                    result.setdefault(entity_id, {})[day] = round(max(value, 0.0), 4)
            self._recorder_days = result
            self._recorder_status = {
                "status": "Ready", "entity_count": len(entity_ids),
                "row_count": sum(len(v) for v in result.values()),
                "source": "Home Assistant Recorder statistics · local-day aligned",
            }
        except Exception as err:
            self._recorder_days = {}
            self._recorder_status = {
                "status": "Recorder statistics unavailable", "entity_count": len(entity_ids),
                "row_count": 0, "error": f"{type(err).__name__}: {err}",
            }

    @staticmethod
    def _num_stat(value: Any) -> float | None:
        try:
            value = float(value)
            return value if value == value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _statistics_start_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            stamp = value
        elif isinstance(value, str):
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            try:
                numeric = float(value)
                if numeric > 10_000_000_000:
                    numeric /= 1000.0
                stamp = datetime.fromtimestamp(numeric, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                return None
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

    def _recorder_periods(self, device: dict[str, Any], today: str) -> dict[str, float] | None:
        entity_id = str(device.get("energy_entity") or "")
        rows = self._recorder_days.get(entity_id)
        if not rows:
            return None
        today_date = datetime.fromisoformat(today).date()
        values = {"today": 0.0, "week": 0.0, "month": 0.0, "year": 0.0, "tracked": 0.0}
        for day, energy in rows.items():
            try:
                day_date = datetime.fromisoformat(day).date()
            except (TypeError, ValueError):
                continue
            value = max(float(energy or 0), 0.0)
            values["tracked"] += value
            if day == today:
                values["today"] = value
            if date_in_period(day_date, "week", today_date):
                values["week"] += value
            if date_in_period(day_date, "month", today_date):
                values["month"] += value
            if date_in_period(day_date, "year", today_date):
                values["year"] += value
        return values

    @staticmethod
    def _row_energy(row: dict[str, Any]) -> float:
        return max(float(row.get("energy_kwh", row.get("integrated_energy_kwh", 0)) or 0), 0.0)

    @staticmethod
    def _row_runtime(row: dict[str, Any]) -> float:
        return max(float(row.get("runtime_minutes", 0) or 0), 0.0)

    def _period_totals(self, device_id: str, today: str) -> dict[str, float | int]:
        days = self.data_lake.data.get("device_daily_summaries", {})
        today_date = datetime.fromisoformat(today).date()
        week_energy = month_energy = year_energy = tracked_energy = 0.0
        week_runtime = month_runtime = year_runtime = tracked_runtime = 0.0
        tracked_days = 0
        today_stored = 0.0
        for day, devices in days.items():
            row = devices.get(device_id)
            if not isinstance(row, dict):
                continue
            energy = self._row_energy(row)
            runtime = self._row_runtime(row)
            tracked_energy += energy
            tracked_runtime += runtime
            tracked_days += 1
            if day == today:
                today_stored = energy
            try:
                day_date = datetime.fromisoformat(day).date()
            except (TypeError, ValueError):
                day_date = None
            if day_date is not None and date_in_period(day_date, "week", today_date):
                week_energy += energy
                week_runtime += runtime
            if day_date is not None and date_in_period(day_date, "month", today_date):
                month_energy += energy
                month_runtime += runtime
            if day_date is not None and date_in_period(day_date, "year", today_date):
                year_energy += energy
                year_runtime += runtime
        return {
            "week_energy": week_energy,
            "month_energy": month_energy,
            "year_energy": year_energy,
            "tracked_energy": tracked_energy,
            "week_runtime": week_runtime,
            "month_runtime": month_runtime,
            "year_runtime": year_runtime,
            "tracked_runtime": tracked_runtime,
            "tracked_days": tracked_days,
            "today_stored": today_stored,
        }

    @staticmethod
    def _entity_state_summary(hass, entity_id: str | None) -> dict[str, Any]:
        if not entity_id:
            return {"mapped": False, "entity_id": None, "available": False, "state": None, "unit": None}
        state = hass.states.get(entity_id)
        raw = str(state.state) if state is not None else None
        available = state is not None and str(raw).lower() not in {"unknown", "unavailable", "none", ""}
        return {
            "mapped": True,
            "entity_id": entity_id,
            "available": available,
            "state": raw if available else None,
            "unit": str(state.attributes.get("unit_of_measurement") or "") if state is not None else None,
        }

    def _evidence_source_diagnostics(self, device: dict[str, Any], historical: dict[str, Any] | None = None) -> dict[str, Any]:
        """Trace canonical device-profile evidence without inventing a profile."""
        device_id = str(device.get("id") or "")
        power_entity = str(device.get("power_entity") or "") or None
        energy_entity = str(device.get("energy_entity") or "") or None
        state_entity = str(device.get("state_entity") or "") or None

        power_state = self._entity_state_summary(self.hass, power_entity)
        energy_state = self._entity_state_summary(self.hass, energy_entity)
        state_state = self._entity_state_summary(self.hass, state_entity)

        recorder_rows = len(self._recorder_days.get(str(energy_entity or ""), {}) or {})
        days = self.data_lake.data.get("device_daily_summaries", {}) or {}
        lake_rows = []
        energy_days = runtime_days = combined_days = sample_days = 0
        total_samples = 0
        for day, device_rows in days.items():
            row = (device_rows or {}).get(device_id) if isinstance(device_rows, dict) else None
            if not isinstance(row, dict):
                continue
            energy = self._row_energy(row)
            runtime = self._row_runtime(row)
            samples = int(row.get("sample_count", 0) or 0)
            lake_rows.append(row)
            if energy > 0.001:
                energy_days += 1
            if runtime > 0:
                runtime_days += 1
            if energy >= 0.03 and runtime >= 15:
                combined_days += 1
            if samples > 0:
                sample_days += 1
                total_samples += samples

        reasons = []
        if not power_entity:
            reasons.append("no mapped power_entity")
        elif not power_state["available"]:
            reasons.append("mapped power_entity is unavailable")
        if not energy_entity:
            reasons.append("no mapped energy_entity")
        elif not energy_state["available"]:
            reasons.append("mapped energy_entity is unavailable")
        if energy_entity and recorder_rows == 0:
            reasons.append("no usable Home Assistant Recorder daily energy statistics")
        if energy_days == 0:
            reasons.append("Data Lake has no positive device-energy day")
        if runtime_days == 0:
            reasons.append("Data Lake has no runtime day")
        if combined_days == 0:
            reasons.append("no Data Lake day contains both qualifying energy and runtime evidence")

        source_status = "usable"
        if combined_days == 0:
            source_status = "no_combined_profile_evidence"
        elif historical and historical.get("quantification_supported") is not True:
            source_status = "collecting_or_variable"
        elif historical and historical.get("quantification_supported") is True:
            source_status = "qualified"

        return {
            "status": source_status,
            "power": power_state,
            "energy": energy_state,
            "state": state_state,
            "recorder_energy_day_count": recorder_rows,
            "data_lake_device_day_count": len(lake_rows),
            "data_lake_energy_day_count": energy_days,
            "data_lake_runtime_day_count": runtime_days,
            "data_lake_combined_profile_day_count": combined_days,
            "data_lake_sample_day_count": sample_days,
            "data_lake_sample_count": total_samples,
            "blocking_reasons": reasons,
            "source_policy": "Registered mappings + Home Assistant state/Recorder + canonical Data Lake device summaries only.",
        }

    def _historical_planning_profile(self, device_id: str) -> dict[str, Any]:
        """Build a conservative typical active-day profile from aligned canonical evidence.

        Energy prefers Home Assistant Recorder daily statistics for the device's
        mapped energy entity. Runtime comes from Zeus's canonical Data Lake
        device daily summary. Evidence is combined only when both values belong
        to the exact same local calendar date. No cross-day matching, synthetic
        runtime, theoretical energy, or fallback arithmetic is allowed.
        """
        device = next(
            (d for d in self.registry.data.get("devices", []) if str(d.get("id") or "") == str(device_id)),
            None,
        ) or {}
        energy_entity = str(device.get("energy_entity") or "")
        recorder_energy_days = self._recorder_days.get(energy_entity, {}) if energy_entity else {}
        lake_days = self.data_lake.data.get("device_daily_summaries", {}) or {}

        observations = []
        aligned_recorder_days = 0
        aligned_datalake_days = 0

        for day, device_rows in lake_days.items():
            row = (device_rows or {}).get(device_id) if isinstance(device_rows, dict) else None
            if not isinstance(row, dict):
                continue

            runtime = self._row_runtime(row)
            samples = int(row.get("sample_count", 0) or 0)
            if runtime < 15:
                continue

            recorder_value = recorder_energy_days.get(str(day))
            if recorder_value is not None:
                try:
                    energy = max(float(recorder_value), 0.0)
                except (TypeError, ValueError):
                    energy = 0.0
                energy_source = "ha_recorder_daily_statistics"
                if energy >= 0.03:
                    aligned_recorder_days += 1
            else:
                # Preserve existing canonical Data Lake evidence as a fallback
                # only when Recorder has no row for this exact date.
                energy = self._row_energy(row)
                energy_source = "data_lake_device_daily_summary"
                if energy >= 0.03:
                    aligned_datalake_days += 1

            if energy < 0.03:
                continue
            avg_power = energy * 60000.0 / runtime
            if avg_power < 30 or avg_power > 50000:
                continue

            observations.append({
                "day": str(day),
                "energy_kwh": energy,
                "runtime_minutes": runtime,
                "average_power_w": avg_power,
                "sample_count": samples,
                "energy_source": energy_source,
            })

        observations.sort(key=lambda x: x["day"])
        recent = observations[-60:]
        count = len(recent)

        if not recent:
            return {
                "status": "insufficient_evidence",
                "quantification_supported": False,
                "active_days": 0,
                "profile_window_days": 60,
                "aligned_recorder_runtime_days": 0,
                "aligned_datalake_runtime_days": 0,
                "source": "Device Analytics · Recorder energy + Data Lake runtime · local-day aligned",
                "method": "Same-local-calendar-day alignment only.",
                "missing_evidence": ["at least 5 active historical days with aligned canonical energy and runtime"],
            }

        energies = [x["energy_kwh"] for x in recent]
        runtimes = [x["runtime_minutes"] for x in recent]
        powers = [x["average_power_w"] for x in recent]
        median_energy = statistics.median(energies)
        median_runtime = statistics.median(runtimes)
        median_power = statistics.median(powers)

        def robust_relative_spread(values, center):
            if not values or center <= 0:
                return 1.0
            deviations = [abs(v - center) for v in values]
            return statistics.median(deviations) / center

        energy_spread = robust_relative_spread(energies, median_energy)
        runtime_spread = robust_relative_spread(runtimes, median_runtime)
        power_spread = robust_relative_spread(powers, median_power)
        total_samples = sum(x["sample_count"] for x in recent)

        enough_days = count >= 5
        stable_profile = energy_spread <= 0.60 and runtime_spread <= 0.60 and power_spread <= 0.60
        supported = bool(
            enough_days and stable_profile
            and median_energy > 0 and median_runtime >= 15 and median_power >= 30
        )

        confidence = 0
        if count:
            maturity = min(40.0, count * 5.0)
            stability = max(
                0.0,
                45.0 * (1.0 - min(1.0, max(energy_spread, runtime_spread, power_spread))),
            )
            sampling = min(15.0, total_samples / 100.0)
            confidence = int(max(35, min(92, round(maturity + stability + sampling))))

        missing = []
        if not enough_days:
            missing.append(f"5 active historical days required; {count} aligned days available")
        if enough_days and not stable_profile:
            missing.append("historical active-day energy/runtime/power profile is too variable for safe quantification")

        source_counts = {
            "ha_recorder_daily_statistics": sum(1 for x in recent if x["energy_source"] == "ha_recorder_daily_statistics"),
            "data_lake_device_daily_summary": sum(1 for x in recent if x["energy_source"] == "data_lake_device_daily_summary"),
        }

        return {
            "status": "supported" if supported else "insufficient_evidence",
            "quantification_supported": supported,
            "active_days": count,
            "profile_window_days": 60,
            "aligned_recorder_runtime_days": source_counts["ha_recorder_daily_statistics"],
            "aligned_datalake_runtime_days": source_counts["data_lake_device_daily_summary"],
            "typical_energy_kwh": round(median_energy, 3),
            "typical_runtime_minutes": round(median_runtime, 1),
            "typical_power_w": round(median_power, 1),
            "energy_relative_spread": round(energy_spread, 3),
            "runtime_relative_spread": round(runtime_spread, 3),
            "power_relative_spread": round(power_spread, 3),
            "confidence_percent": confidence,
            "source": "Device Analytics · Recorder energy + Data Lake runtime · local-day aligned",
            "source_counts": source_counts,
            "method": "Median active-day profile using exact local-day alignment of canonical Recorder energy and Data Lake runtime; Data Lake energy is used only when Recorder has no row for that same date.",
            "missing_evidence": missing,
        }

    def refresh(self) -> dict[str, Any]:
        today = dt_util.now().date().isoformat()
        stored_today = self.data_lake.data.get("device_daily_summaries", {}).get(today, {})
        devices = []
        total_energy = 0.0
        total_runtime = 0.0
        for device in self.registry.data.get("devices", []):
            if not is_consuming_load(device):
                continue
            device_id = str(device.get("id") or "unknown")
            row = stored_today.get(device_id, {})
            integrated_energy = round(float(row.get("integrated_energy_kwh", row.get("energy_kwh", 0)) or 0), 3)
            measured_energy, measured_method = self._measured_today_energy(device)
            if str(device.get("energy_type")) == "total_increasing" and row.get("energy_method") == "measured_total_increasing_delta":
                measured_energy = float(row.get("energy_kwh", 0) or 0)
                measured_method = "measured_total_increasing_delta"
            if measured_energy is not None:
                energy = round(measured_energy, 3)
                energy_method = measured_method
            else:
                energy = integrated_energy
                energy_method = "power_integration"
            runtime = round(float(row.get("runtime_minutes", 0) or 0), 1)
            period = self._period_totals(device_id, today)
            recorder = self._recorder_periods(device, today)
            if recorder is not None:
                energy = round(recorder["today"], 3)
                energy_method = "ha_recorder_daily_statistics"
                week_energy = recorder["week"]
                month_energy = recorder["month"]
                year_energy = recorder["year"]
                tracked_energy = recorder["tracked"]
            else:
                correction = energy - float(period["today_stored"])
                week_energy = max(float(period["week_energy"]) + correction, 0.0)
                month_energy = max(float(period["month_energy"]) + correction, 0.0)
                year_energy = max(float(period["year_energy"]) + correction, 0.0)
                tracked_energy = max(float(period["tracked_energy"]) + correction, 0.0)
            lifetime_total = self._lifetime_total_energy(device)
            total_display = lifetime_total if lifetime_total is not None else tracked_energy
            total_method = "meter_lifetime_total" if lifetime_total is not None else "zeus_tracked_total"
            total_energy += energy
            total_runtime += runtime
            historical_profile = self._historical_planning_profile(device_id)
            source_diagnostics = self._evidence_source_diagnostics(device, historical_profile)
            temperature_entity = str(device.get("temperature_entity") or "").strip() or None
            temperature_c = None
            temperature_available = False
            if temperature_entity:
                temp_state = self.hass.states.get(temperature_entity)
                if temp_state and str(temp_state.state).strip().lower() not in {"unknown", "unavailable", "none", ""}:
                    try:
                        temp_value = float(temp_state.state)
                        temp_unit = str(temp_state.attributes.get("unit_of_measurement") or "°C").strip()
                        if temp_unit in {"°F", "F"}:
                            temp_value = (temp_value - 32.0) * 5.0 / 9.0
                        elif temp_unit == "K":
                            temp_value -= 273.15
                        temperature_c = round(temp_value, 1)
                        temperature_available = True
                    except (TypeError, ValueError):
                        pass
            devices.append({
                "id": device_id,
                "name": device.get("name") or device_id,
                "type": device.get("type", "custom"),
                "power_entity": device.get("power_entity"),
                "energy_entity": device.get("energy_entity"),
                "energy_type": device.get("energy_type", "auto"),
                "temperature_entity": temperature_entity,
                "temperature_c": temperature_c,
                "temperature_available": temperature_available,
                "energy_today_kwh": round(energy, 3),
                "energy_week_kwh": round(week_energy, 3),
                "energy_month_kwh": round(month_energy, 3),
                "energy_year_kwh": round(year_energy, 3),
                "energy_total_kwh": round(total_display, 3),
                "energy_tracked_total_kwh": round(tracked_energy, 3),
                "total_method": total_method,
                "tracked_days": int(period["tracked_days"]),
                "runtime_today_minutes": runtime,
                "runtime_week_minutes": round(float(period["week_runtime"]), 1),
                "runtime_month_minutes": round(float(period["month_runtime"]), 1),
                "runtime_year_minutes": round(float(period["year_runtime"]), 1),
                "peak_power_today_w": round(float(row.get("peak_power_w", 0) or 0), 1),
                "estimated_cost_today": None,
                "sample_count": int(row.get("sample_count", 0) or 0),
                "method": energy_method,
                "integrated_energy_today_kwh": integrated_energy,
                "historical_planning_profile": historical_profile,
                "planning_evidence_sources": source_diagnostics,
            })
        devices.sort(key=lambda d: d["energy_today_kwh"], reverse=True)
        self.last = {
            "status": "Ready" if devices else "Waiting",
            "date": today,
            "device_count": len(devices),
            "total_device_energy_today_kwh": round(total_energy, 3),
            "total_runtime_today_minutes": round(total_runtime, 1),
            "devices": devices,
            "top_device": devices[0] if devices else None,
            "summary": (f"Tracked {len(devices)} devices; {total_energy:.2f} kWh today." if devices else "No enabled registered devices to track."),
            "method_note": "Today, calendar week (Monday-to-today), month and year prefer Home Assistant Recorder statistics for each mapped device energy entity. Total uses the lifetime meter reading when available.",
            "recorder_energy_source": dict(self._recorder_status),
            "safety": "Read-only device analytics.",
        }
        self.event_bus.publish("DeviceAnalyticsUpdated", "DeviceAnalyticsEngine", {"device_count": len(devices)})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last

    def _normalized_energy_state(self, entity_id: str | None) -> tuple[float | None, Any | None]:
        if not entity_id:
            return None, None
        state = self.hass.states.get(entity_id)
        if state is None or str(state.state).lower() in ("unknown", "unavailable", "none", ""):
            return None, state
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None, state
        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit in ("wh", "watt-hour", "watt-hours"):
            value /= 1000.0
        elif unit in ("mwh", "megawatt-hour", "megawatt-hours"):
            value *= 1000.0
        elif unit not in ("kwh", "kilowatt-hour", "kilowatt-hours"):
            return None, state
        return max(value, 0.0), state

    def _lifetime_total_energy(self, device: dict[str, Any]) -> float | None:
        value, state = self._normalized_energy_state(device.get("energy_entity"))
        if value is None or state is None:
            return None
        configured_type = str(device.get("energy_type") or "auto")
        state_class = str(state.attributes.get("state_class") or "").lower()
        if configured_type == "total_increasing" or state_class == "total_increasing":
            return value
        return None

    def _measured_today_energy(self, device: dict[str, Any]) -> tuple[float | None, str | None]:
        """Read a mapped daily-energy entity when it already represents today's use."""
        value, state = self._normalized_energy_state(device.get("energy_entity"))
        if value is None or state is None:
            return None, None
        entity_id = str(device.get("energy_entity") or "")
        friendly = str(state.attributes.get("friendly_name") or "").lower()
        identifier = f"{entity_id.lower()} {friendly}"
        state_class = str(state.attributes.get("state_class") or "").lower()
        last_reset = state.attributes.get("last_reset")
        configured_type = str(device.get("energy_type") or "auto")
        if configured_type == "total_increasing":
            return None, None
        daily_hint = configured_type == "daily" or any(token in identifier for token in ("today", "daily", "day_energy", "energy_day", "daily_energy"))
        resetting_total = bool(last_reset)
        if daily_hint or resetting_total or state_class == "measurement":
            return value, "measured_daily_energy"
        return None, None


class DailyBriefingEngine:
    """Create a plain-language daily briefing and comparison cards."""

    def __init__(self, event_bus, analytics, device_analytics, optimizer, forecast) -> None:
        self.event_bus = event_bus
        self.analytics = analytics
        self.device_analytics = device_analytics
        self.optimizer = optimizer
        self.forecast = forecast
        self.last = {"status": "Waiting", "briefing": "Collecting data."}

    @staticmethod
    def _delta_text(label: str, item: dict[str, Any]) -> str:
        diff = float(item.get("difference_kwh", 0) or 0)
        direction = "more" if diff > 0 else "less"
        return f"{label} is {abs(diff):.2f} kWh {direction} than yesterday." if abs(diff) >= 0.01 else f"{label} is similar to yesterday."

    def refresh(self) -> dict[str, Any]:
        history = self.analytics.summary()
        periods = history.get("periods", {})
        today = periods.get("today", {})
        comparison = history.get("comparison", {}).get("today_vs_yesterday", {})
        devices = self.device_analytics.summary()
        recommendation = (self.optimizer.summary().get("recommendations") or [None])[0]
        forecast = self.forecast.summary().get("best_surplus_window")
        top = devices.get("top_device")
        sentences = [
            f"Solar generated {float(today.get('solar_energy_kwh', 0) or 0):.2f} kWh today and the home consumed {float(today.get('house_energy_kwh', 0) or 0):.2f} kWh.",
            f"Grid import is {float(today.get('grid_import_energy_kwh', 0) or 0):.2f} kWh and export is {float(today.get('grid_export_energy_kwh', 0) or 0):.2f} kWh.",
        ]
        if top:
            sentences.append(f"The highest tracked device is {top.get('name')} at {float(top.get('energy_today_kwh', 0) or 0):.2f} kWh.")
        if recommendation:
            sentences.append(str(recommendation.get("reason") or recommendation.get("action") or "A recommendation is available."))
        if forecast:
            sentences.append(f"The best expected surplus window is around {int(forecast.get('hour', 0)):02d}:00.")
        cards = [
            {"key": "solar", "title": "Solar vs yesterday", "text": self._delta_text("Solar generation", comparison.get("solar_energy_kwh", {}))},
            {"key": "consumption", "title": "Consumption vs yesterday", "text": self._delta_text("Consumption", comparison.get("house_energy_kwh", {}))},
            {"key": "grid", "title": "Grid import vs yesterday", "text": self._delta_text("Grid import", comparison.get("grid_import_energy_kwh", {}))},
        ]
        self.last = {
            "status": "Ready" if history.get("status") == "Ready" else "Waiting",
            "briefing": " ".join(sentences),
            "cards": cards,
            "today": today,
            "top_device": top,
            "recommendation": recommendation,
            "best_surplus_window": forecast,
            "summary": "Daily briefing and comparisons are ready.",
            "safety": "Information and recommendations only.",
        }
        self.event_bus.publish("DailyBriefingUpdated", "DailyBriefingEngine", {"status": self.last["status"]})
        return self.last

    def summary(self) -> dict[str, Any]:
        return self.last
