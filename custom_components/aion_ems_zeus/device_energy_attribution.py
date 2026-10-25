"""Device Energy Attribution Engine (DEA).

Read-only, time-aligned attribution of registered-device consumption to solar,
battery and grid sources. Device power history determines when consumption
occurred; mapped device energy remains authoritative for period totals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import logging
import math
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
        # Year uses the same measured hourly-statistics path as Month. The
        # canonical period authority supplies the trusted accounting boundary
        # (01.09 on installations whose Zeus data epoch starts there).
        "year": (366, "hourly_statistics"),
    }

    def __init__(self, hass, event_bus, registry, device_analytics) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.registry = registry
        self.device_analytics = device_analytics
        self.last: dict[str, Any] = {
            "status": "Waiting", "engine": "Device Energy Attribution Engine",
            "version": "1.17", "devices": [], "periods": {},
        }
        # v16.0.162: serialize heavyweight Recorder work. UI requests reuse the
        # most recent snapshot instead of starting parallel history scans.
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_monotonic: float = 0.0
        self._cache_seconds = 15 * 60

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

    async def _energy_counter_intervals(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
        """Read Recorder counter deltas; keep ambiguous intervals unattributed.

        Only increments bounded by two recorded states within 30 minutes and
        the same UTC hour are eligible for hourly source matching. A missing
        baseline, reset, large gap or boundary crossing cannot establish when
        the energy was used and is deliberately excluded.
        """
        ids = list(dict.fromkeys(x for x in entity_ids if x))
        if not ids:
            return {}, {}
        begin, finish = dt_util.as_utc(start), dt_util.as_utc(end)

        def _query():
            with session_scope(hass=self.hass, read_only=True) as session:
                return history.get_significant_states_with_session(
                    self.hass, session, begin, finish, ids, None,
                    True, False, False, True,
                )

        raw = await get_instance(self.hass).async_add_executor_job(_query)
        output, diagnostics = {}, {}
        for entity_id in ids:
            state = self.hass.states.get(entity_id)
            unit = str(state.attributes.get("unit_of_measurement", "") if state else "").lower().strip()
            scale = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}.get(unit)
            rows = list((raw or {}).get(entity_id, []) or [])
            events = []
            numeric_states = timestamp_states = invalid_states = 0
            missing_timestamps = 0
            rejected_categories = {"unknown": 0, "unavailable": 0, "empty": 0, "non_numeric": 0, "nonfinite_or_negative": 0, "timestamp_error": 0}
            sample_types = sorted({type(item).__name__ for item in rows})[:4]
            if scale is not None:
                for item in rows:
                    try:
                        raw_state = item.get("state") if isinstance(item, dict) else item.state
                        text_state = str(raw_state).strip().lower() if raw_state is not None else ""
                        if text_state in ("unknown", "unavailable", ""):
                            rejected_categories[text_state or "empty"] += 1
                            invalid_states += 1
                            continue
                        try:
                            number = float(raw_state)
                        except (ValueError, TypeError, OverflowError):
                            rejected_categories["non_numeric"] += 1
                            invalid_states += 1
                            continue
                        if not math.isfinite(number) or number < 0:
                            rejected_categories["nonfinite_or_negative"] += 1
                            invalid_states += 1
                            continue
                        numeric_states += 1
                        raw_stamp = ((item.get("last_changed") or item.get("last_updated"))
                                     if isinstance(item, dict) else
                                     (getattr(item, "last_changed", None) or getattr(item, "last_updated", None)))
                        if isinstance(raw_stamp, str):
                            raw_stamp = dt_util.parse_datetime(raw_stamp)
                        if raw_stamp is None or raw_stamp.tzinfo is None:
                            missing_timestamps += 1
                            rejected_categories["timestamp_error"] += 1
                            continue
                        stamp = dt_util.as_utc(raw_stamp)
                        timestamp_states += 1
                        events.append((stamp, number * scale))
                    except (ValueError, TypeError, OverflowError, AttributeError):
                        invalid_states += 1
                        rejected_categories["timestamp_error"] += 1
            events.sort(key=lambda x: x[0])
            buckets, eligible, excluded = {}, 0, 0
            excluded_long_gap = excluded_cross_hour = excluded_out_of_period = 0
            positive_deltas = 0
            maximum_gap_minutes = 0.0
            for (before, old), (after, new) in zip(events, events[1:]):
                delta = new - old
                if delta <= 0:
                    continue
                positive_deltas += 1
                gap_minutes = max(0.0, (after-before).total_seconds()/60.0)
                maximum_gap_minutes = max(maximum_gap_minutes, gap_minutes)
                if before < begin or after > finish or after <= before:
                    excluded += 1; excluded_out_of_period += 1
                    continue
                if after - before > timedelta(minutes=30):
                    excluded += 1; excluded_long_gap += 1
                    continue
                if before.replace(minute=0, second=0, microsecond=0) != after.replace(minute=0, second=0, microsecond=0):
                    excluded += 1; excluded_cross_hour += 1
                    continue
                key = after.replace(minute=0, second=0, microsecond=0).isoformat()
                buckets[key] = buckets.get(key, 0.0) + delta
                eligible += 1
            output[entity_id] = buckets
            diagnostics[entity_id] = {
                "source": "Recorder lifetime counter transitions",
                "unit": unit, "raw_state_count": len(rows),
                "sample_types": sample_types,
                "unit_supported": scale is not None,
                "numeric_states": numeric_states,
                "timestamp_states": timestamp_states,
                "invalid_states": invalid_states,
                "missing_timestamps": missing_timestamps,
                "rejected_categories": rejected_categories,
                "first_valid_timestamp": events[0][0].isoformat() if events else None,
                "last_valid_timestamp": events[-1][0].isoformat() if events else None,
                "first_valid_kwh": round(events[0][1], 3) if events else None,
                "last_valid_kwh": round(events[-1][1], 3) if events else None,
                "min_counter_kwh": round(min(v for _, v in events), 3) if events else None,
                "max_counter_kwh": round(max(v for _, v in events), 3) if events else None,
                "query_start": begin.isoformat(),
                "query_end": finish.isoformat(),
                "distinct_counter_values": len({v for _, v in events}),
                "distinct_timestamps": len({ts for ts, _ in events}),
                "eligible_intervals": eligible, "excluded_intervals": excluded,
                "positive_deltas": positive_deltas,
                "excluded_long_gap": excluded_long_gap,
                "excluded_cross_hour": excluded_cross_hour,
                "excluded_out_of_period": excluded_out_of_period,
                "maximum_gap_minutes": round(maximum_gap_minutes, 1),
                "eligible_kwh": round(sum(buckets.values()), 3),
                "status": "counter_intervals_available" if buckets else "no_eligible_counter_intervals",
            }
        return output, diagnostics

    async def _counter_statistics_diagnostics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
        """Read hourly positive counter deltas with provenance and gap checks.

        A sum delta is assigned to the current statistic row's UTC hour
        (the interval whose end contributes that sum), only if adjacent
        statistic rows are exactly one UTC hour apart. Other intervals remain
        unattributed. This is a source-mix estimate, not direct metering.
        """
        ids = list(dict.fromkeys(e for e in entity_ids if e))
        if not ids:
            return {}, {}
        try:
            response = await self.hass.services.async_call(
                "recorder", "get_statistics",
                {
                    "statistic_ids": ids,
                    "start_time": start,
                    "end_time": end,
                    "period": "hour",
                    "types": ["sum", "state"],
                    "units": {"energy": "kWh"},
                },
                blocking=True, return_response=True,
            )
        except Exception as err:
            _LOGGER.debug("DEA counter statistics unavailable: %s", err)
            return {}, {eid: {"status": "statistics_query_failed",
                          "error_type": type(err).__name__} for eid in ids}
        raw = (response or {}).get("statistics", response or {})
        diagnostics = {}
        hourly_deltas = {}
        for eid in ids:
            rows = [r for r in (raw or {}).get(eid, []) if isinstance(r, dict)]
            def _stamp(v):
                if isinstance(v, (float, int)):
                    return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
                if isinstance(v, datetime):
                    return dt_util.as_utc(v).isoformat()
                if isinstance(v, str):
                    parsed = dt_util.parse_datetime(v)
                    return dt_util.as_utc(parsed).isoformat() if parsed else None
                return None
            def _numbers(key):
                values = []
                for row in rows:
                    try:
                        value = float(row[key])
                        if math.isfinite(value):
                            values.append(value)
                    except (KeyError, ValueError, TypeError):
                        pass
                return values
            sums = _numbers("sum")
            states = _numbers("state")
            # Recorder sum is in requested kWh. A counter reset or missing
            # hourly statistic makes the interval ineligible, never negative.
            ordered = []
            for row in rows:
                ts = _stamp(row.get("start"))
                try:
                    value = float(row.get("sum"))
                except (ValueError, TypeError):
                    continue
                if ts and math.isfinite(value):
                    ordered.append((datetime.fromisoformat(ts), value))
            ordered.sort()
            deltas = {}
            rejected_gaps = rejected_resets = 0
            for (prev_ts, prev_value), (curr_ts, curr_value) in zip(ordered, ordered[1:]):
                if curr_ts - prev_ts != timedelta(hours=1):
                    rejected_gaps += 1
                    continue
                change = curr_value - prev_value
                if change < 0:
                    rejected_resets += 1
                    continue
                if change > 0 and prev_ts >= dt_util.as_utc(start) and curr_ts <= dt_util.as_utc(end):
                    deltas[curr_ts.isoformat()] = change
            hourly_deltas[eid] = deltas
            diagnostics[eid] = {
                "eligible_hourly_kwh": round(sum(deltas.values()), 3),
                "eligible_hourly_buckets": len(deltas),
                "rejected_hour_gaps": rejected_gaps,
                "rejected_counter_resets": rejected_resets,
                "status": "statistics_found" if rows else "no_statistics",
                "row_count": len(rows),
                "sum_row_count": len(sums),
                "state_row_count": len(states),
                "first_hour": _stamp(rows[0].get("start")) if rows else None,
                "last_hour": _stamp(rows[-1].get("start")) if rows else None,
                "sum_range": [round(min(sums), 3), round(max(sums), 3)] if sums else None,
                "state_range": [round(min(states), 3), round(max(states), 3)] if states else None,
            }
        return hourly_deltas, diagnostics

    async def _statistics_power(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
        """Return hourly Recorder statistics for long DEA windows.

        Month/week attribution does not need raw state-change resolution. Home
        Assistant's hourly mean power statistics preserve measured timing while
        avoiding multi-week scans of the states table. Entities without eligible
        power statistics remain unavailable here and use DEA's existing explicit
        fallback path; Zeus never fabricates missing history.
        """
        ids = list(dict.fromkeys(x for x in entity_ids if x))
        if not ids:
            return {}, {}
        try:
            response = await self.hass.services.async_call(
                "recorder", "get_statistics",
                {
                    "statistic_ids": ids,
                    "start_time": start,
                    "end_time": end,
                    "period": "hour",
                    "types": ["mean"],
                    "units": {"power": "W"},
                },
                blocking=True, return_response=True,
            )
        except Exception as err:
            _LOGGER.warning("DEA aggregated power statistics query failed: %s", err)
            return {}, {entity_id: {"diagnostic_status": "statistics_query_failed", "error": str(err)} for entity_id in ids}

        raw = (response or {}).get("statistics", response or {})
        result: dict[str, dict[str, float]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        for entity_id in ids:
            rows = [row for row in list((raw or {}).get(entity_id) or []) if isinstance(row, dict)]
            samples: dict[str, float] = {}
            for row in rows:
                value = row.get("mean")
                if value is None:
                    continue
                try:
                    watts = max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
                stamp = row.get("start")
                if isinstance(stamp, (int, float)):
                    dt = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
                elif isinstance(stamp, str):
                    dt = dt_util.parse_datetime(stamp)
                else:
                    dt = stamp if isinstance(stamp, datetime) else None
                if dt is None:
                    continue
                samples[dt_util.as_utc(dt).isoformat()] = watts
            result[entity_id] = samples
            diagnostics[entity_id] = {
                "entity_id": entity_id,
                "diagnostic_status": "statistics_available" if samples else "statistics_unavailable",
                "statistics_row_count": len(rows),
                "aligned_sample_count": len(samples),
                "query_start": dt_util.as_utc(start).isoformat(),
                "query_end": dt_util.as_utc(end).isoformat(),
                "interval_seconds": 3600,
                "source": "Home Assistant Recorder hourly mean statistics",
            }
        return result, diagnostics

    def cache_is_fresh(self, max_age_seconds: int | None = None) -> bool:
        """Return whether the in-memory DEA snapshot is fresh enough for UI use."""
        import time
        age_limit = self._cache_seconds if max_age_seconds is None else max(0, int(max_age_seconds))
        return bool(self.last.get("generated_at")) and (time.monotonic() - self._last_refresh_monotonic) < age_limit

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
        # DEA follows the same canonical registered-load population surfaced by
        # Device Analytics. Registry metadata is merged when available, but a
        # valid analytics load must never disappear merely because an older
        # registry snapshot is empty or missing that row.
        all_ids = list(dict.fromkeys([*registry_by_id.keys(), *analytics_by_id.keys()]))
        for did in all_ids:
            registered = registry_by_id.get(did)
            analytics = analytics_by_id.get(did)
            if registered is None and analytics is not None:
                merged = dict(analytics)
                merged["dea_population_source"] = "device_analytics"
            elif analytics is None and registered is not None:
                merged = self._fallback_device_row(registered)
                merged["dea_population_source"] = "registry"
            elif registered is not None and analytics is not None:
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
                merged["dea_population_source"] = "registry+device_analytics"
            else:
                continue
            merged["id"] = did
            merged["dea_eligibility"] = (
                "power+energy" if merged.get("power_entity") and merged.get("energy_entity")
                else "power" if merged.get("power_entity")
                else "energy" if merged.get("energy_entity")
                else "unmeasured"
            )
            result.append(merged)
        return result

    async def async_refresh(self, *, force: bool = False) -> dict[str, Any]:
        """Refresh DEA once, reusing fresh results and preventing duplicate scans."""
        if not force and self.cache_is_fresh():
            return self.last
        async with self._refresh_lock:
            if not force and self.cache_is_fresh():
                return self.last
            return await self._async_refresh_locked()

    async def _async_refresh_locked(self) -> dict[str, Any]:
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

        # v16.0.174: fetch one aggregated Year window for Week + Month + Year.
        # Long DEA periods use Home Assistant hourly statistics rather than raw
        # Recorder states. Today retains detailed raw history because its short
        # window benefits from 5-minute timing.
        all_power_ids = required_sources + [str(d.get("power_entity")) for d in devices if d.get("power_entity")]
        month_window = canonical_period_window("month", now)
        week_window = canonical_period_window("week", now)
        year_window = canonical_period_window("year", now)
        month_start = month_window.start or dt_util.start_of_local_day(now - timedelta(days=31))
        week_start = week_window.start or dt_util.start_of_local_day(now - timedelta(days=6))
        year_start = year_window.start or month_start
        long_start = min(month_start, week_start, year_start)
        long_aligned, long_diagnostics = await self._statistics_power(all_power_ids, long_start, now)

        # Only query cumulative counters for loads lacking hourly power
        # statistics. Bound the raw Recorder query to the current month,
        # never the entire lifetime of the installation.
        counter_ids = list(dict.fromkeys(
            str(d.get("energy_entity")) for d in devices
            if d.get("energy_entity") and d.get("power_entity")
            and (not long_aligned.get(str(d.get("power_entity")))
                 or str(d.get("type") or "").lower() in
                 ("ev_charger", "ev_charger_wallbox", "wallbox"))
        ))
        try:
            counter_intervals, counter_diagnostics = await self._energy_counter_intervals(
                counter_ids, month_start, now
            )
        except Exception as err:
            _LOGGER.warning("DEA energy-counter interval query failed: %s", err)
            counter_intervals, counter_diagnostics = {}, {}
        # Independent read-only check: long-term statistics can survive
        # beyond the Recorder raw-state retention window.
        counter_stat_hours, counter_statistics = await self._counter_statistics_diagnostics(
            counter_ids, month_start, now
        )

        for period_name, (days, resolution) in self.PERIODS.items():
            # v14.0.0-alpha.22.8.9.2: all accounting engines consume one
            # canonical local-calendar period authority. DEA no longer rebuilds
            # Today / Week / Month boundaries independently.
            window = canonical_period_window(period_name, now)
            start = window.start
            if start is None:
                start = dt_util.start_of_local_day(now - timedelta(days=days-1))
            entity_ids = all_power_ids
            if period_name == "today":
                seconds = 300
                try:
                    aligned, history_diagnostics = await self._history_power(entity_ids, start, now, seconds)
                except Exception as err:
                    period_payload[period_name] = {
                        "status": "Fallback", "reason": f"recorder_history_error: {err}",
                        "resolution": resolution,
                    }
                    aligned = {}
                    history_diagnostics = {}
            else:
                seconds = 3600
                start_utc = dt_util.as_utc(start)
                aligned = {
                    entity_id: {ts: value for ts, value in series.items() if (dt_util.parse_datetime(ts) or start_utc) >= start_utc}
                    for entity_id, series in long_aligned.items()
                }
                history_diagnostics = long_diagnostics
                resolution = "hourly_statistics"
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
                    # Missing synchronized power evidence does not establish
                    # a grid-only charging source. Preserve measured energy,
                    # but leave its source unattributed.
                    grid_e = 0.0
                    quality = "Source unverified"
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
                # Retain only internal counters needed for attribution flow.
                counter_diag = {}
                if (quality == "Source unverified" and period_name in ("today", "week", "month")
                    and authoritative > 0 and device.get("energy_entity")):
                    counter_id = str(device.get("energy_entity"))
                    # Prefer older long-term hourly sum deltas; the raw
                    # Recorder states may have been purged. Never add both.
                    counter_rows = counter_stat_hours.get(counter_id, {}) or counter_intervals.get(counter_id, {})
                    counter_diag["counter_source"] = (
                        "hourly_statistics_sum" if counter_stat_hours.get(counter_id)
                        else "raw_recorder_intervals" if counter_rows else "none"
                    )
                    # Source measurements and counter increments must share
                    # an exact UTC hourly bucket. Never infer grid-only use.
                    source_hours = {
                        ts for series in source_series.values() for ts in series
                    }
                    counter_diag["counter_bucket_count"] = len(counter_rows)
                    counter_diag["matched_source_buckets"] = 0
                    counter_diag["complete_source_buckets"] = 0
                    counter_diag["source_balance_accepted_hours"] = 0
                    counter_diag["accepted_counter_kwh"] = 0.0
                    counter_diag["rejected_source_balance_hours"] = 0
                    counter_diag["rejected_no_house_hours"] = 0
                    counter_diag["rejected_hours"] = []
                    counter_diag["rejected_counter_kwh"] = 0.0
                    counter_diag["rejected_house_excluded_ev_possible"] = 0
                    counter_diag["rejected_supply_balance"] = 0
                    counter_diag["rejected_ev_exceeds_house"] = 0
                    counter_diag["rejected_ev_exceeds_site_supply"] = 0
                    counter_diag["counter_total_candidate_kwh"] = round(sum(
                        max(0.0, self._num(v)) for v in counter_rows.values()), 3)
                    # Both hourly Recorder statistics carry the UTC start
                    # of their interval. Their timestamps must match directly.
                    # Never choose a shift by maximizing attribution acceptance:
                    # that is circular evidence and can misassign energy.
                    offset = 0
                    counter_diag["alignment_offset_hours"] = 0
                    counter_diag["alignment_method"] = "hourly_statistics_interval_start"
                    counter_diag["alignment_scores"] = {}
                    for stamp, delta in sorted(counter_rows.items()):
                        hour = dt_util.parse_datetime(stamp)
                        if hour is None or dt_util.as_utc(hour) < dt_util.as_utc(start):
                            continue
                        source_stamp = (dt_util.as_utc(hour) +
                                        timedelta(hours=offset)).isoformat()
                        if source_stamp not in source_hours:
                            continue
                        counter_diag["matched_source_buckets"] += 1
                        # Require measured home demand, solar, battery and
                        # grid source series at the same UTC hour. Missing
                        # evidence leaves the counter delta as unknown.
                        required = [source_series.get(name, {}) for name in
                                    ("house", "solar", "battery_discharge", "grid_import")]
                        if not all(source_stamp in series for series in required):
                            continue
                        counter_diag["complete_source_buckets"] += 1
                        house_w = self._num(source_series["house"][source_stamp])
                        solar_w = self._num(source_series["solar"][source_stamp])
                        wind_w = self._num(source_series.get("wind", {}).get(source_stamp))
                        generator_w = self._num(source_series.get("generator", {}).get(source_stamp))
                        battery_w = self._num(source_series["battery_discharge"][source_stamp])
                        grid_w = self._num(source_series["grid_import"][source_stamp])
                        export_w = self._num(source_series.get("grid_export", {}).get(source_stamp))
                        charge_w = self._num(source_series.get("battery_charge", {}).get(source_stamp))
                        if house_w <= 0:
                            counter_diag["rejected_no_house_hours"] += 1
                            continue
                        generation = solar_w + wind_w + generator_w
                        local_home = min(house_w, max(0.0, generation - export_w - charge_w))
                        battery_home = min(max(0.0, house_w - local_home), battery_w)
                        grid_home = min(max(0.0, house_w - local_home - battery_home), grid_w)
                        supply = local_home + battery_home + grid_home
                        # The current source mix is a proportional estimate,
                        # not a direct measurement of the charger's supply.
                        # Reject hours where a complete house/source balance
                        # cannot be established, or the charger's counter
                        # increase exceeds the whole home's hourly demand.
                        low_supply = supply <= 0 or supply < house_w * 0.8
                        high_supply = supply > house_w * 1.2
                        # EV charging can be excluded from the HA "house"
                        # measurement. Do not compare it to house alone as a
                        # physical limit. Instead require the independently
                        # metered site sources to cover the EV's energy.
                        # If they do not, leave that hour unknown.
                        ev_exceeds_house = delta > house_w / 1000.0 * 1.05
                        site_supply_w = max(0.0, generation - export_w - charge_w) + battery_w + grid_w
                        ev_exceeds_site_supply = delta > site_supply_w / 1000.0 * 1.05
                        if low_supply or high_supply or ev_exceeds_site_supply:
                            counter_diag["rejected_source_balance_hours"] += 1
                            counter_diag["rejected_counter_kwh"] += delta
                            counter_diag["rejected_supply_balance"] += int(low_supply or high_supply)
                            counter_diag["rejected_ev_exceeds_house"] += int(ev_exceeds_house)
                            counter_diag["rejected_ev_exceeds_site_supply"] += int(ev_exceeds_site_supply)
                            counter_diag["rejected_house_excluded_ev_possible"] += int(ev_exceeds_house)
                            counter_diag["rejected_hours"].append({
                                "utc_hour": stamp, "source_utc_hour": source_stamp,
                                "ev_kwh": round(delta, 3),
                                "house_kwh": round(house_w / 1000.0, 3),
                                "supply_kwh": round(supply / 1000.0, 3),
                                "low_supply": low_supply,
                                "high_supply": high_supply,
                                "ev_exceeds_house": ev_exceeds_house,
                                "site_supply_kwh": round(site_supply_w / 1000.0, 3),
                                "ev_exceeds_site_supply": ev_exceeds_site_supply,
                            })
                            continue
                        if ev_exceeds_house:
                            # The house-only proportional mix cannot establish
                            # a source split for EV charging outside that boundary.
                            counter_diag["rejected_counter_kwh"] += delta
                            counter_diag["rejected_house_excluded_ev_possible"] += 1
                            counter_diag["rejected_hours"].append({
                                "utc_hour": stamp, "source_utc_hour": source_stamp, "ev_kwh": round(delta, 3),
                                "house_kwh": round(house_w / 1000.0, 3),
                                "site_supply_kwh": round(site_supply_w / 1000.0, 3),
                                "reason": "EV outside measured house boundary; source split not identifiable",
                            })
                            continue
                        solar_share = local_home * solar_w / generation if generation > 0 else 0.0
                        wind_share = local_home * wind_w / generation if generation > 0 else 0.0
                        generator_share = local_home * generator_w / generation if generation > 0 else 0.0
                        remaining_kwh = max(0.0, authoritative -
                            (solar_e + wind_e + generator_e + battery_e + grid_e))
                        amount = min(delta, remaining_kwh)
                        if amount <= 0:
                            break
                        counter_diag["source_balance_accepted_hours"] += 1
                        counter_diag["accepted_counter_kwh"] += amount
                        solar_e += amount * solar_share / supply
                        wind_e += amount * wind_share / supply
                        generator_e += amount * generator_share / supply
                        battery_e += amount * battery_home / supply
                        grid_e += amount * grid_home / supply
                    if solar_e + wind_e + generator_e + battery_e + grid_e > 0:
                        quality = "Hourly counter statistics · proportional source estimate"
                        fallback_reason = "Hourly counter deltas and whole-home sources support a proportional estimate; not directly metered at charger"
                unknown_e = max(0.0, authoritative - solar_e - wind_e - generator_e - battery_e - grid_e)
                counter_diag["rejected_counter_kwh"] = round(
                    counter_diag.get("rejected_counter_kwh", 0.0), 3)
                counter_diag["accepted_counter_kwh"] = round(
                    counter_diag.get("accepted_counter_kwh", 0.0), 3)
                counter_diag["attributed_solar_kwh"] = round(solar_e, 3)
                counter_diag["attributed_battery_kwh"] = round(battery_e, 3)
                counter_diag["attributed_grid_kwh"] = round(grid_e, 3)
                counter_diag["remaining_unknown_kwh"] = round(unknown_e, 3)
                total = solar_e + wind_e + generator_e + battery_e + grid_e + unknown_e
                if total > 0 and authoritative > 0:
                    correction = authoritative / total
                    solar_e *= correction; wind_e *= correction; generator_e *= correction; battery_e *= correction; grid_e *= correction; unknown_e *= correction
                reconciled_total = solar_e + wind_e + generator_e + battery_e + grid_e + unknown_e
                reconciliation_delta = authoritative - reconciled_total
                percent_total = (
                    (100 * unknown_e / authoritative)
                    + (100 * solar_e / authoritative)
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
                    "unknown_source_kwh": round(unknown_e, 3),
                    "source_mix_verified": used > 0 and integrated > 0,
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
                                "local_generation_kwh", "battery_kwh", "grid_kwh", "unknown_source_kwh"):
                        row[key] = round(self._num(row.get(key)) * aggregate_scale, 3)
                    energy = self._num(row.get("energy_kwh"))
                    solar = self._num(row.get("solar_kwh")); wind = self._num(row.get("wind_kwh"))
                    gen = self._num(row.get("generator_kwh")); batt = self._num(row.get("battery_kwh"))
                    grid = self._num(row.get("grid_kwh"))
                    unknown = self._num(row.get("unknown_source_kwh"))
                    row["solar_percent"] = round(100 * solar / energy, 1) if energy else 0.0
                    row["wind_percent"] = round(100 * wind / energy, 1) if energy else 0.0
                    row["generator_percent"] = round(100 * gen / energy, 1) if energy else 0.0
                    row["local_generation_percent"] = round(100 * (solar + wind + gen) / energy, 1) if energy else 0.0
                    row["battery_percent"] = round(100 * batt / energy, 1) if energy else 0.0
                    row["grid_percent"] = round(100 * grid / energy, 1) if energy else 0.0
                    row["reconciliation_delta_kwh"] = round(energy - (solar + wind + gen + batt + grid + unknown), 4)
                    row["reconciliation_status"] = "Balanced"
                    row["aggregate_reconciled"] = True
                    row["aggregate_scale"] = round(aggregate_scale, 6)
                    if row.get("source_mix_verified"):
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

        # Total deliberately uses a transparent period estimate. Year is now
        # retained from the measured hourly-statistics path above so it follows
        # the same canonical year boundary/evidence authority as Month/Week.
        for device in devices:
            did = str(device.get("id"))
            today_mix = per_device[did]["periods"].get("month") or per_device[did]["periods"].get("week") or {}
            shares = [self._num(today_mix.get(k)) for k in ("solar_percent", "wind_percent", "generator_percent", "battery_percent", "grid_percent")]
            source_mix_known = sum(shares) > 0 and any(
                self._num(today_mix.get(k)) > 0
                for k in ("solar_kwh", "wind_kwh", "generator_kwh", "battery_kwh", "grid_kwh")
            )
            denom = sum(shares) or 100.0
            for pname in ("total",):
                energy = self._period_energy(device, pname)
                solar_e = energy * shares[0] / denom
                wind_e = energy * shares[1] / denom
                generator_e = energy * shares[2] / denom
                battery_e = energy * shares[3] / denom
                grid_e = max(0.0, energy - solar_e - wind_e - generator_e - battery_e) if source_mix_known else 0.0
                unknown_e = max(0.0, energy - solar_e - wind_e - generator_e - battery_e - grid_e)
                per_device[did]["periods"][pname] = {
                    "energy_kwh": round(energy, 3), "solar_kwh": round(solar_e, 3),
                    "wind_kwh": round(wind_e, 3), "generator_kwh": round(generator_e, 3),
                    "local_generation_kwh": round(solar_e + wind_e + generator_e, 3),
                    "battery_kwh": round(battery_e, 3), "grid_kwh": round(grid_e, 3),
                    "unknown_source_kwh": round(unknown_e, 3),
                    "source_mix_verified": source_mix_known,
                    "solar_percent": round(100*solar_e/energy,1) if energy else 0.0,
                    "wind_percent": round(100*wind_e/energy,1) if energy else 0.0,
                    "generator_percent": round(100*generator_e/energy,1) if energy else 0.0,
                    "local_generation_percent": round(100*(solar_e+wind_e+generator_e)/energy,1) if energy else 0.0,
                    "battery_percent": round(100*battery_e/energy,1) if energy else 0.0,
                    "grid_percent": round(100*grid_e/energy,1) if energy else 0.0,
                    "attribution_percent_total": round(
                        (100*unknown_e/energy if energy else 0.0)
                        +(100*solar_e/energy if energy else 0.0)
                        +(100*wind_e/energy if energy else 0.0)
                        +(100*generator_e/energy if energy else 0.0)
                        +(100*battery_e/energy if energy else 0.0)
                        +(100*grid_e/energy if energy else 0.0), 1
                    ),
                    "reconciliation_delta_kwh": round(energy-(solar_e+wind_e+generator_e+battery_e+grid_e+unknown_e), 4),
                    "reconciliation_status": "Balanced",
                    "quality": "Estimated period allocation" if source_mix_known else "Source unverified",
                    "fallback_reason": "No measured month/week source mix" if not source_mix_known else "Historical source mix extrapolated",
                    "sample_count": 0,
                    "power_entity": device.get("power_entity"), "energy_entity": device.get("energy_entity"),
                }

        payload_devices = list(per_device.values())
        self.last = {
            "status": "Ready" if payload_devices else "Waiting",
            "engine": "Device Energy Attribution Engine", "version": "1.17",
            "generated_at": now.isoformat(), "devices": payload_devices,
            "periods": period_payload,
            "method": "Today uses detailed Recorder power history; Week/Month/Year use Home Assistant hourly mean power statistics. Registered-device power is reconciled to measured whole-home demand before source allocation.",
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
        import time
        self._last_refresh_monotonic = time.monotonic()
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
