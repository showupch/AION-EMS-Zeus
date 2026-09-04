"""Device Energy Attribution Engine (DEA).

Read-only, time-aligned attribution of registered-device consumption to solar,
battery and grid sources. Device power history determines when consumption
occurred; mapped device energy remains authoritative for period totals.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from .device_roles import is_consuming_load
from .period_authority import canonical_period_window

from homeassistant.util import dt as dt_util
from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.util import session_scope

_LOGGER = logging.getLogger(__name__)


class DeviceEnergyAttributionEngine:
    """Attribute device energy from synchronized Home Assistant statistics."""

    PERIODS = {
        "today": (1, "5minute"),
        "week": (7, "15minute"),
        "month": (32, "15minute"),
    }

    def __init__(self, hass, event_bus, registry, device_analytics) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.device_analytics = device_analytics
        self.last: dict[str, Any] = {
            "status": "Waiting", "engine": "Device Energy Attribution Engine",
            "version": "1.14", "devices": [], "periods": {},
        }

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _to_w(value: Any, unit: str | None) -> float:
        v = DeviceEnergyAttributionEngine._num(value)
        return v * 1000.0 if str(unit or '').lower() == 'kw' else v

    def _source_entities(self) -> dict[str, str]:
        m = dict(self.registry.data.get("entity_mappings", {}) or {})
        return {
            "solar": m.get("solar_power", ""),
            "wind": m.get("wind_power", ""),
            "generator": m.get("generator_power", ""),
            "house": m.get("house_power", ""),
            "grid_import": m.get("grid_import_power", ""),
            "grid_export": m.get("grid_export_power", ""),
            "battery_charge": m.get("battery_charge_power", ""),
            "battery_discharge": m.get("battery_discharge_power", ""),
        }

    async def _history_power(
        self, entity_ids: list[str], start: datetime, end: datetime, seconds: int
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
        """Return aligned power samples plus transparent Recorder diagnostics."""
        ids = list(dict.fromkeys(x for x in entity_ids if x))
        if not ids:
            return {}, {}
        start_utc = dt_util.as_utc(start)
        end_utc = dt_util.as_utc(end)

        def _query():
            with session_scope(hass=self.hass, read_only=True) as session:
                return history.get_significant_states_with_session(
                    self.hass, session, start_utc, end_utc, ids, None,
                    True, False, False, True,
                )

        raw = await get_instance(self.hass).async_add_executor_job(_query)
        result: dict[str, dict[str, float]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        for entity_id in ids:
            current = self.hass.states.get(entity_id)
            unit = current.attributes.get("unit_of_measurement") if current else None
            raw_states = list((raw or {}).get(entity_id, []) or [])
            events: list[tuple[datetime, float]] = []
            invalid_states: list[str] = []
            nonzero_count = 0
            for state in raw_states:
                raw_value = str(getattr(state, "state", ""))
                if raw_value.lower() in {"unknown", "unavailable", "none", "nan", ""}:
                    if len(invalid_states) < 5:
                        invalid_states.append(raw_value or "empty")
                    continue
                try:
                    value = self._to_w(raw_value, unit)
                except Exception:
                    if len(invalid_states) < 5:
                        invalid_states.append(raw_value)
                    continue
                changed = state.last_changed or state.last_updated
                if changed is None:
                    continue
                value = max(0.0, value)
                if value > 0:
                    nonzero_count += 1
                events.append((dt_util.as_utc(changed), value))
            events.sort(key=lambda item: item[0])
            diag = {
                "entity_id": entity_id,
                "entity_exists": current is not None,
                "current_state": getattr(current, "state", None),
                "unit": unit,
                "raw_state_count": len(raw_states),
                "numeric_state_count": len(events),
                "nonzero_state_count": nonzero_count,
                "invalid_examples": invalid_states,
                "first_state_at": events[0][0].isoformat() if events else None,
                "last_state_at": events[-1][0].isoformat() if events else None,
                "query_start": start_utc.isoformat(),
                "query_end": end_utc.isoformat(),
                "interval_seconds": seconds,
            }
            if not events:
                result[entity_id] = {}
                diag["aligned_sample_count"] = 0
                sun_state = self.hass.states.get("sun.sun")
                below_horizon = getattr(sun_state, "state", None) == "below_horizon"
                solar_power_state = self.hass.states.get(self._source_entities().get("solar", ""))
                solar_power = self._to_w(
                    getattr(solar_power_state, "state", 0),
                    solar_power_state.attributes.get("unit_of_measurement") if solar_power_state else None,
                )
                sleeping_expected = below_horizon or solar_power <= 1.0
                diag["sleeping_expected"] = sleeping_expected
                diag["diagnostic_status"] = (
                    "sleeping_expected" if sleeping_expected else
                    "entity_not_found" if current is None else
                    "recorder_returned_no_states" if not raw_states else
                    "recorder_states_not_numeric"
                )
                diagnostics[entity_id] = diag
                log = _LOGGER.debug if sleeping_expected else _LOGGER.warning
                log(
                    "DEA diagnostics %s: status=%s raw=%s numeric=%s current=%s unit=%s",
                    entity_id, diag["diagnostic_status"], len(raw_states), len(events),
                    getattr(current, "state", None), unit,
                )
                continue

            samples: dict[str, float] = {}
            pointer = 0
            latest = events[0][1]
            cursor = start_utc
            while cursor < end_utc:
                midpoint = min(cursor + timedelta(seconds=seconds / 2), end_utc)
                while pointer + 1 < len(events) and events[pointer + 1][0] <= midpoint:
                    pointer += 1
                    latest = events[pointer][1]
                samples[cursor.isoformat()] = latest
                cursor += timedelta(seconds=seconds)
            result[entity_id] = samples
            diag["aligned_sample_count"] = len(samples)
            diag["diagnostic_status"] = "history_available"
            diagnostics[entity_id] = diag
            _LOGGER.info(
                "DEA diagnostics %s: raw=%s numeric=%s nonzero=%s aligned=%s unit=%s first=%s last=%s",
                entity_id, len(raw_states), len(events), nonzero_count, len(samples), unit,
                diag["first_state_at"], diag["last_state_at"],
            )
        return result, diagnostics

    @staticmethod
    def _period_energy(device: dict[str, Any], period: str) -> float:
        key = {
            "today": "energy_today_kwh", "week": "energy_week_kwh",
            "month": "energy_month_kwh", "year": "energy_year_kwh",
            "total": "energy_total_kwh",
        }.get(period, "energy_today_kwh")
        return max(DeviceEnergyAttributionEngine._num(device.get(key)), 0.0)


    @staticmethod
    def _is_consuming_load(device: dict[str, Any]) -> bool:
        return is_consuming_load(device)

    def _fallback_device_row(self, device: dict[str, Any]) -> dict[str, Any]:
        """Build a minimal analytics-compatible row directly from the registry.

        DEA normally consumes Device Analytics.  During HA startup/reload that
        summary can temporarily be empty even though the registry and live flow
        already contain valid loads.  This keeps DEA attached to the registry
        instead of collapsing to 0-of-0 loads.
        """
        row = dict(device)
        energy = 0.0
        # Reuse Device Analytics' own daily-meter rules so a lifetime
        # total_increasing entity is never mistaken for today's consumption.
        try:
            measured, _method = self.device_analytics._measured_today_energy(device)
        except Exception:
            measured = None
        if measured is not None:
            energy = max(self._num(measured), 0.0)
        row.setdefault("energy_today_kwh", round(energy, 3))
        row.setdefault("energy_week_kwh", round(energy, 3))
        row.setdefault("energy_month_kwh", round(energy, 3))
        row.setdefault("energy_year_kwh", round(energy, 3))
        row.setdefault("energy_total_kwh", round(energy, 3))
        row["dea_registry_fallback"] = True
        return row

    def _registered_loads(self) -> list[dict[str, Any]]:
        """Return all registered consuming loads that carry measured evidence.

        Room assignment is presentation metadata and is never an eligibility
        requirement. A registered load is DEA-visible when it has either a
        mapped power sensor or a mapped energy sensor. This is important for
        Home Assistant Energy imported devices such as EV chargers, where the
        energy meter is authoritative even if Recorder power history is still
        warming up.
        """
        def load_key(device: dict[str, Any]) -> str:
            explicit = str(device.get("id") or "").strip()
            if explicit:
                return explicit
            # Older/imported registry rows may not yet have a stable Zeus id.
            # Use measured identity locally so they are not silently dropped.
            return str(
                device.get("energy_entity")
                or device.get("power_entity")
                or device.get("name")
                or device.get("friendly_name")
                or ""
            ).strip()

        registry_loads = []
        for raw in list(self.registry.data.get("devices", []) or []):
            if not self._is_consuming_load(raw):
                continue
            if not (raw.get("power_entity") or raw.get("energy_entity")):
                continue
            device = dict(raw)
            key = load_key(device)
            if not key:
                continue
            device.setdefault("id", key)
            registry_loads.append(device)

        registry_by_id = {load_key(d): d for d in registry_loads if load_key(d)}

        analytics_rows = []
        for raw in list((self.device_analytics.summary() or {}).get("devices", []) or []):
            if not self._is_consuming_load(raw):
                continue
            if not (raw.get("power_entity") or raw.get("energy_entity")):
                continue
            device = dict(raw)
            key = load_key(device)
            if not key:
                continue
            device.setdefault("id", key)
            analytics_rows.append(device)
        analytics_by_id = {load_key(d): d for d in analytics_rows if load_key(d)}

        if registry_by_id and set(registry_by_id) - set(analytics_by_id):
            try:
                self.device_analytics.refresh()
                analytics_rows = []
                for raw in list((self.device_analytics.summary() or {}).get("devices", []) or []):
                    if not self._is_consuming_load(raw):
                        continue
                    if not (raw.get("power_entity") or raw.get("energy_entity")):
                        continue
                    device = dict(raw)
                    key = load_key(device)
                    if not key:
                        continue
                    device.setdefault("id", key)
                    analytics_rows.append(device)
                analytics_by_id = {load_key(d): d for d in analytics_rows if load_key(d)}
            except Exception as err:
                _LOGGER.warning("DEA device analytics self-heal failed: %s", err)

        result: list[dict[str, Any]] = []
        for did, registered in registry_by_id.items():
            analytics = analytics_by_id.get(did)
            if analytics is None:
                merged = self._fallback_device_row(registered)
            else:
                merged = {**registered, **analytics}
                for mapping_key in (
                    "power_entity", "energy_entity", "temperature_entity",
                    "cop_entity", "state_entity", "availability_entity",
                ):
                    registry_value = registered.get(mapping_key)
                    if registry_value:
                        merged[mapping_key] = registry_value
                for classification_key in ("type", "category", "role", "device_class"):
                    registry_value = registered.get(classification_key)
                    if registry_value:
                        merged[classification_key] = registry_value
            merged["id"] = did
            merged["dea_eligibility"] = (
                "power+energy" if merged.get("power_entity") and merged.get("energy_entity")
                else "power" if merged.get("power_entity")
                else "energy"
            )
            result.append(merged)
        return result

    async def async_refresh(self) -> dict[str, Any]:
        registry_devices = list(self.registry.data.get("devices", []) or [])
        registry_with_power = [d for d in registry_devices if d.get("power_entity")]
        registry_with_energy = [d for d in registry_devices if d.get("energy_entity")]
        registry_with_evidence = [d for d in registry_devices if d.get("power_entity") or d.get("energy_entity")]
        registry_consuming = [d for d in registry_with_evidence if self._is_consuming_load(d)]
        devices = self._registered_loads()
        sources = self._source_entities()
        required_sources = [x for x in sources.values() if x]
        now = dt_util.now()
        period_payload: dict[str, Any] = {}
        per_device: dict[str, dict[str, Any]] = {str(d.get("id")): {"id": d.get("id"), "name": d.get("name"), "periods": {}} for d in devices}

        for period_name, (days, resolution) in self.PERIODS.items():
            # v14.0.0-alpha.22.8.9.2: all accounting engines consume one
            # canonical local-calendar period authority. DEA no longer rebuilds
            # Today / Week / Month boundaries independently.
            window = canonical_period_window(period_name, now)
            start = window.start
            if start is None:
                start = dt_util.start_of_local_day(now - timedelta(days=days-1))
            entity_ids = required_sources + [str(d.get("power_entity")) for d in devices if d.get("power_entity")]
            seconds = {"5minute": 300, "15minute": 900, "hour": 3600}.get(resolution, 3600)
            try:
                aligned, history_diagnostics = await self._history_power(entity_ids, start, now, seconds)
            except Exception as err:
                period_payload[period_name] = {
                    "status": "Fallback", "reason": f"recorder_history_error: {err}",
                    "resolution": resolution,
                }
                aligned = {}
                history_diagnostics = {}
            source_series = {name: aligned.get(entity, {}) for name, entity in sources.items() if entity}
            timestamps = sorted(set().union(*(set(s.keys()) for s in source_series.values()))) if source_series else []

            # Reconcile registered-device power at each aligned timestamp before
            # integrating energy. Registered loads are a subset of whole-home
            # demand, so their simultaneous summed power can never physically
            # exceed measured house power. Duplicate mappings, parent/child load
            # overlap, or Recorder hold-last-value gaps can otherwise inflate
            # period energy by several times. Scale all registered device powers
            # proportionally at the affected timestamp, preserving each device's
            # relative share while enforcing the physical boundary at source.
            device_power_series = {
                str(d.get("id")): aligned.get(str(d.get("power_entity") or ""), {})
                for d in devices
            }
            registered_scale_by_ts: dict[str, float] = {}
            overlap_sample_count = 0
            min_registered_scale = 1.0
            house_series_for_reconciliation = source_series.get("house", {})
            for ts in timestamps:
                house_w = max(0.0, self._num(house_series_for_reconciliation.get(ts, 0.0)))
                registered_w = sum(
                    max(0.0, self._num(series.get(ts, 0.0)))
                    for series in device_power_series.values()
                )
                scale = 1.0
                if house_w > 0.0 and registered_w > house_w:
                    scale = house_w / registered_w
                    overlap_sample_count += 1
                    min_registered_scale = min(min_registered_scale, scale)
                registered_scale_by_ts[ts] = scale

            measured_count = 0
            for device in devices:
                did = str(device.get("id"))
                power_entity = str(device.get("power_entity") or "")
                power = aligned.get(power_entity, {})
                power_diag = history_diagnostics.get(power_entity, {})
                solar_e = wind_e = generator_e = battery_e = grid_e = integrated = 0.0
                used = 0
                for ts in timestamps:
                    device_w = max(0.0, power.get(ts, 0.0)) * registered_scale_by_ts.get(ts, 1.0)
                    if device_w <= 0:
                        continue
                    house = max(0.0, source_series.get("house", {}).get(ts, 0.0))
                    solar = max(0.0, source_series.get("solar", {}).get(ts, 0.0))
                    wind = max(0.0, source_series.get("wind", {}).get(ts, 0.0))
                    generator = max(0.0, source_series.get("generator", {}).get(ts, 0.0))
                    generation = solar + wind + generator
                    export = max(0.0, source_series.get("grid_export", {}).get(ts, 0.0))
                    batt_charge = max(0.0, source_series.get("battery_charge", {}).get(ts, 0.0))
                    batt_dis = max(0.0, source_series.get("battery_discharge", {}).get(ts, 0.0))
                    grid_imp = max(0.0, source_series.get("grid_import", {}).get(ts, 0.0))
                    demand = house if house > 1 else max(generation - export - batt_charge + batt_dis + grid_imp, device_w)
                    generation_home = min(demand, max(0.0, generation - export - batt_charge))
                    source_denominator = generation if generation > 0 else 0.0
                    solar_home = generation_home * solar / source_denominator if source_denominator > 0 else 0.0
                    wind_home = generation_home * wind / source_denominator if source_denominator > 0 else 0.0
                    generator_home = generation_home * generator / source_denominator if source_denominator > 0 else 0.0
                    remaining = max(0.0, demand - generation_home)
                    battery_home = min(remaining, batt_dis)
                    grid_home = max(0.0, demand - solar_home - battery_home)
                    if grid_imp > 0:
                        grid_home = min(demand, max(grid_home, grid_imp))
                    denom = solar_home + wind_home + generator_home + battery_home + grid_home
                    if denom <= 0:
                        continue
                    interval = device_w * seconds / 3_600_000.0
                    integrated += interval
                    solar_e += interval * solar_home / denom
                    wind_e += interval * wind_home / denom
                    generator_e += interval * generator_home / denom
                    battery_e += interval * battery_home / denom
                    grid_e += interval * grid_home / denom
                    used += 1
                configured_energy = self._period_energy(device, period_name)
                # For the directly measured Today / Week / Month periods, the
                # registered-load total must come from the exact same Recorder
                # power-history window that DEA attributes. This prevents a
                # misclassified cumulative energy entity (for example a lifetime
                # counter exposed as measurement) from inflating a month-to-date
                # device total into MWh. Energy-meter period totals remain the
                # fallback when usable power history is not available.
                authoritative = integrated if integrated > 0 and used > 0 else configured_energy
                if integrated > 0 and authoritative >= 0 and used > 0:
                    # With power-history authority this is normally 1.0, but keep
                    # the reconciliation scale explicit for numerical symmetry.
                    scale = authoritative / integrated if integrated > 0 else 0
                    solar_e *= scale; wind_e *= scale; generator_e *= scale; battery_e *= scale; grid_e *= scale
                    quality = "Measured power history"
                    fallback_reason = None
                    measured_count += 1
                else:
                    solar_e = wind_e = generator_e = battery_e = 0.0
                    grid_e = authoritative
                    quality = "Estimated period allocation"
                    if not device.get("power_entity"):
                        fallback_reason = "power entity not mapped"
                    elif not power:
                        fallback_reason = (
                            f"{power_diag.get('diagnostic_status', 'no_history')}: "
                            f"raw={power_diag.get('raw_state_count', 0)}, "
                            f"numeric={power_diag.get('numeric_state_count', 0)}, "
                            f"entity={power_entity or 'not_mapped'}"
                        )
                    elif not timestamps:
                        fallback_reason = "whole-home source power history unavailable"
                    elif authoritative <= 0:
                        fallback_reason = "no authoritative device energy in selected period"
                    else:
                        fallback_reason = "no overlapping usable power samples"
                total = solar_e + wind_e + generator_e + battery_e + grid_e
                if total > 0 and authoritative > 0:
                    correction = authoritative / total
                    solar_e *= correction; wind_e *= correction; generator_e *= correction; battery_e *= correction; grid_e *= correction
                reconciled_total = solar_e + wind_e + generator_e + battery_e + grid_e
                reconciliation_delta = authoritative - reconciled_total
                percent_total = (
                    (100 * solar_e / authoritative)
                    + (100 * wind_e / authoritative)
                    + (100 * generator_e / authoritative)
                    + (100 * battery_e / authoritative)
                    + (100 * grid_e / authoritative)
                ) if authoritative else 0.0
                reconciliation_status = (
                    "Balanced"
                    if abs(reconciliation_delta) <= max(0.005, authoritative * 0.002)
                    else "Review"
                )
                per_device[did]["periods"][period_name] = {
                    "energy_kwh": round(authoritative, 3),
                    "solar_kwh": round(solar_e, 3),
                    "wind_kwh": round(wind_e, 3),
                    "generator_kwh": round(generator_e, 3),
                    "local_generation_kwh": round(solar_e + wind_e + generator_e, 3),
                    "battery_kwh": round(battery_e, 3),
                    "grid_kwh": round(grid_e, 3),
                    "solar_percent": round(100 * solar_e / authoritative, 1) if authoritative else 0.0,
                    "wind_percent": round(100 * wind_e / authoritative, 1) if authoritative else 0.0,
                    "generator_percent": round(100 * generator_e / authoritative, 1) if authoritative else 0.0,
                    "local_generation_percent": round(100 * (solar_e + wind_e + generator_e) / authoritative, 1) if authoritative else 0.0,
                    "battery_percent": round(100 * battery_e / authoritative, 1) if authoritative else 0.0,
                    "grid_percent": round(100 * grid_e / authoritative, 1) if authoritative else 0.0,
                    "attribution_percent_total": round(percent_total, 1),
                    "reconciliation_delta_kwh": round(reconciliation_delta, 4),
                    "reconciliation_status": reconciliation_status,
                    "quality": quality,
                    "fallback_reason": fallback_reason,
                    "sample_count": used,
                    "power_entity": device.get("power_entity"),
                    "energy_entity": device.get("energy_entity"),
                    "history_diagnostics": power_diag,
                }
            # Final aggregate reconciliation remains as a safety net for rounding
            # or periods where house history is incomplete. Normal overlap handling
            # now occurs above at each aligned timestamp before integration.
            house_series = source_series.get("house", {})
            house_integrated = sum(max(0.0, self._num(v)) * seconds / 3_600_000.0 for v in house_series.values())
            period_rows = [per_device[str(d.get("id"))]["periods"].get(period_name, {}) for d in devices]
            registered_integrated = sum(self._num(r.get("energy_kwh")) for r in period_rows)
            aggregate_scale = 1.0
            aggregate_reconciled = False
            if house_integrated > 0 and registered_integrated > house_integrated:
                aggregate_scale = house_integrated / registered_integrated
                aggregate_reconciled = True
                for row in period_rows:
                    for key in ("energy_kwh", "solar_kwh", "wind_kwh", "generator_kwh",
                                "local_generation_kwh", "battery_kwh", "grid_kwh"):
                        row[key] = round(self._num(row.get(key)) * aggregate_scale, 3)
                    energy = self._num(row.get("energy_kwh"))
                    solar = self._num(row.get("solar_kwh")); wind = self._num(row.get("wind_kwh"))
                    gen = self._num(row.get("generator_kwh")); batt = self._num(row.get("battery_kwh"))
                    grid = self._num(row.get("grid_kwh"))
                    row["solar_percent"] = round(100 * solar / energy, 1) if energy else 0.0
                    row["wind_percent"] = round(100 * wind / energy, 1) if energy else 0.0
                    row["generator_percent"] = round(100 * gen / energy, 1) if energy else 0.0
                    row["local_generation_percent"] = round(100 * (solar + wind + gen) / energy, 1) if energy else 0.0
                    row["battery_percent"] = round(100 * batt / energy, 1) if energy else 0.0
                    row["grid_percent"] = round(100 * grid / energy, 1) if energy else 0.0
                    row["reconciliation_delta_kwh"] = round(energy - (solar + wind + gen + batt + grid), 4)
                    row["reconciliation_status"] = "Balanced"
                    row["aggregate_reconciled"] = True
                    row["aggregate_scale"] = round(aggregate_scale, 6)
                    row["quality"] = "Measured power history · whole-home reconciled"
                _LOGGER.info(
                    "DEA %s safety reconciliation: registered-load aggregate %.3f kWh exceeded same-window house %.3f kWh; reconciled by %.6f",
                    period_name, registered_integrated, house_integrated, aggregate_scale,
                )

            period_payload[period_name] = {
                "status": "Ready" if measured_count else "Estimated",
                "resolution": resolution, "measured_devices": measured_count,
                "device_count": len(devices), "start": start.isoformat(), "end": now.isoformat(),
                "house_integrated_kwh": round(house_integrated, 3),
                "registered_before_reconciliation_kwh": round(registered_integrated, 3),
                "registered_after_reconciliation_kwh": round(sum(self._num(r.get("energy_kwh")) for r in period_rows), 3),
                "aggregate_reconciled": aggregate_reconciled,
                "aggregate_scale": round(aggregate_scale, 6),
                "timestamp_overlap_samples": overlap_sample_count,
                "minimum_timestamp_scale": round(min_registered_scale, 6),
                "source_diagnostics": {name: history_diagnostics.get(entity, {}) for name, entity in sources.items() if entity},
            }

        # Year/total deliberately use transparent period estimates in Phase 1.
        for device in devices:
            did = str(device.get("id"))
            today_mix = per_device[did]["periods"].get("month") or per_device[did]["periods"].get("week") or {}
            shares = [self._num(today_mix.get(k)) for k in ("solar_percent", "wind_percent", "generator_percent", "battery_percent", "grid_percent")]
            denom = sum(shares) or 100.0
            for pname in ("year", "total"):
                energy = self._period_energy(device, pname)
                solar_e = energy * shares[0] / denom
                wind_e = energy * shares[1] / denom
                generator_e = energy * shares[2] / denom
                battery_e = energy * shares[3] / denom
                grid_e = max(0.0, energy - solar_e - wind_e - generator_e - battery_e)
                per_device[did]["periods"][pname] = {
                    "energy_kwh": round(energy, 3), "solar_kwh": round(solar_e, 3),
                    "wind_kwh": round(wind_e, 3), "generator_kwh": round(generator_e, 3),
                    "local_generation_kwh": round(solar_e + wind_e + generator_e, 3),
                    "battery_kwh": round(battery_e, 3), "grid_kwh": round(grid_e, 3),
                    "solar_percent": round(100*solar_e/energy,1) if energy else 0.0,
                    "wind_percent": round(100*wind_e/energy,1) if energy else 0.0,
                    "generator_percent": round(100*generator_e/energy,1) if energy else 0.0,
                    "local_generation_percent": round(100*(solar_e+wind_e+generator_e)/energy,1) if energy else 0.0,
                    "battery_percent": round(100*battery_e/energy,1) if energy else 0.0,
                    "grid_percent": round(100*grid_e/energy,1) if energy else 0.0,
                    "attribution_percent_total": round(
                        (100*solar_e/energy if energy else 0.0)
                        +(100*wind_e/energy if energy else 0.0)
                        +(100*generator_e/energy if energy else 0.0)
                        +(100*battery_e/energy if energy else 0.0)
                        +(100*grid_e/energy if energy else 0.0), 1
                    ),
                    "reconciliation_delta_kwh": round(energy-(solar_e+wind_e+generator_e+battery_e+grid_e), 4),
                    "reconciliation_status": "Balanced",
                    "quality": "Estimated period allocation", "sample_count": 0,
                    "power_entity": device.get("power_entity"), "energy_entity": device.get("energy_entity"),
                }

        payload_devices = list(per_device.values())
        self.last = {
            "status": "Ready" if payload_devices else "Waiting",
            "engine": "Device Energy Attribution Engine", "version": "1.14",
            "generated_at": now.isoformat(), "devices": payload_devices,
            "periods": period_payload,
            "method": "Recorder state-history power timing on exact calendar windows; registered-device power is reconciled to measured whole-home demand at each aligned timestamp before energy integration and source allocation.",
            "principle": "Every device kWh is attributed once across solar, wind, generator, battery and grid; local generation remains source-preserving.",
            "registry_diagnostics": {
                "registered_total": len(registry_devices),
                "registered_with_power_entity": len(registry_with_power),
                "registered_with_energy_entity": len(registry_with_energy),
                "registered_with_measurement_evidence": len(registry_with_evidence),
                "classified_consuming_loads": len(registry_consuming),
                "dea_load_rows": len(devices),
            },
            "safety": "Read-only. Recommendation-only mode; no device control.",
        }
        self.event_bus.publish("DeviceEnergyAttributionUpdated", "DeviceEnergyAttributionEngine", {"device_count": len(payload_devices)})
        return self.last

    def recorder_summary(self) -> dict[str, Any]:
        """Return compact Recorder-safe metadata; detailed rows stay in memory."""
        data = self.last or {}
        devices = data.get("devices") if isinstance(data.get("devices"), list) else []
        period_details = data.get("periods") if isinstance(data.get("periods"), dict) else {}
        compact_periods: dict[str, Any] = {}
        for period_name in ("today", "week", "month", "year", "total"):
            solar = wind = generator = battery = grid = energy = 0.0
            measured = estimated = review = 0
            for device in devices:
                row = ((device.get("periods") or {}).get(period_name) or {})
                energy += self._num(row.get("energy_kwh"))
                solar += self._num(row.get("solar_kwh"))
                wind += self._num(row.get("wind_kwh"))
                generator += self._num(row.get("generator_kwh"))
                battery += self._num(row.get("battery_kwh"))
                grid += self._num(row.get("grid_kwh"))
                if str(row.get("quality") or "").startswith("Measured power"):
                    measured += 1
                elif row:
                    estimated += 1
                if row.get("reconciliation_status") == "Review":
                    review += 1
            meta = period_details.get(period_name) if isinstance(period_details.get(period_name), dict) else {}
            compact_periods[period_name] = {
                "status": meta.get("status", "Estimated" if estimated else "Waiting"),
                "resolution": meta.get("resolution"),
                "device_count": measured + estimated,
                "measured_devices": measured,
                "estimated_devices": estimated,
                "energy_kwh": round(energy, 3),
                "solar_kwh": round(solar, 3),
                "wind_kwh": round(wind, 3),
                "generator_kwh": round(generator, 3),
                "local_generation_kwh": round(solar + wind + generator, 3),
                "battery_kwh": round(battery, 3),
                "grid_kwh": round(grid, 3),
                "reconciliation_status": "Balanced" if review == 0 else "Review",
                "reconciliation_review_devices": review,
                "attributed_total_kwh": round(solar + wind + generator + battery + grid, 3),
                "reconciliation_delta_kwh": round(energy - (solar + wind + generator + battery + grid), 4),
            }
        return {
            "status": data.get("status", "Waiting"),
            "engine": data.get("engine", "Device Energy Attribution Engine"),
            "version": data.get("version", "1.12"),
            "generated_at": data.get("generated_at"),
            "device_count": len(devices),
            "periods": compact_periods,
            "details_storage": "runtime_memory_via_websocket",
            "websocket_type": "aion_ems_zeus/device_energy_attribution",
            "recorder_safe": True,
            "safety": data.get("safety"),
        }

    def summary(self) -> dict[str, Any]:
        return self.last
