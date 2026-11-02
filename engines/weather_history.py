"""Persistent historical weather intelligence for AION EMS Zeus."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from math import sqrt
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

STORAGE_VERSION = 1
STORAGE_KEY = "aion_ems_zeus.weather_history"
RETENTION_DAYS = 730


class WeatherHistoryEngine:
    """Capture compact weather/energy daily aggregates and derive statistics."""

    def __init__(self, hass: Any, event_bus: Any, core: Any) -> None:
        self.hass = hass
        self.event_bus = event_bus
        self.core = core
        self.store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {"days": {}, "metadata": {"retention_days": RETENTION_DAYS}}
        self.last: dict[str, Any] = {"status": "Waiting", "day_count": 0, "recent_days": []}
        self._statistics_energy_days: dict[str, dict[str, Any]] = {}
        self._statistics_status: dict[str, Any] = {"status": "Not loaded", "row_count": 0, "entity_count": 0}

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data.update(stored)
        self.data.setdefault("days", {})
        self.data.setdefault("metadata", {"retention_days": RETENTION_DAYS})
        await self.async_refresh_authoritative_energy()
        self.refresh()

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            value = float(value)
            return value if value == value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _average(record: dict[str, Any], key: str) -> float | None:
        count = int(record.get(f"{key}_count", 0) or 0)
        total = record.get(f"{key}_sum")
        return round(float(total) / count, 2) if count and isinstance(total, (int, float)) else None

    def _local_day(self) -> str:
        """Return Home Assistant's local calendar date."""
        return dt_util.now().date().isoformat()

    def _mapped_energy_entities(self) -> dict[str, str]:
        """Return mapped Home Assistant energy statistic IDs by Zeus energy key."""
        try:
            mapping = self.core.energy_flow.mapping
            mappings = dict(getattr(mapping, "mappings", {}) or {})
        except Exception:
            mappings = dict(getattr(getattr(self.core, "registry", None), "data", {}).get("entity_mappings", {}) or {})
        specs = {
            "solar_energy_kwh": ("solar_energy_today", "solar_energy_total"),
            "house_energy_kwh": ("house_energy_today", "house_energy_total"),
            "grid_import_energy_kwh": ("grid_import_energy_today", "grid_import_energy_total"),
            "grid_export_energy_kwh": ("grid_export_energy_today", "grid_export_energy_total"),
            "battery_charge_energy_kwh": ("battery_charge_energy_today", "battery_charge_energy_total", "battery_energy_total"),
            "battery_discharge_energy_kwh": ("battery_discharge_energy_today", "battery_discharge_energy_total", "battery_energy_total"),
        }
        result: dict[str, str] = {}
        for key, fields in specs.items():
            entity_id = next((mappings.get(field) for field in fields if mappings.get(field)), None)
            if entity_id:
                result[key] = str(entity_id)
        return result

    async def async_refresh_authoritative_energy(self) -> None:
        """Load completed daily energy from Home Assistant Recorder statistics.

        Weather Intelligence must never use Zeus's sparse power-integration
        summaries for historical energy. Recorder long-term statistics are the
        same source used by Home Assistant's Energy dashboard.
        """
        entities = self._mapped_energy_entities()
        if not entities:
            self._statistics_energy_days = {}
            self._statistics_status = {"status": "No mapped energy statistics", "row_count": 0, "entity_count": 0}
            return
        try:
            local_today = dt_util.now().date()
            start_local = dt_util.start_of_local_day(dt_util.now() - timedelta(days=401))
            end_local = dt_util.start_of_local_day(dt_util.now() + timedelta(days=1))
            statistic_ids = list(dict.fromkeys(entities.values()))

            # Home Assistant's Recorder action is the authoritative public API
            # used here. Requesting ``change`` gives the exact per-day energy
            # amount used by the Energy dashboard, including meter resets.
            response = await self.hass.services.async_call(
                "recorder",
                "get_statistics",
                {
                    "statistic_ids": statistic_ids,
                    "start_time": start_local,
                    "end_time": end_local,
                    "period": "day",
                    "types": ["change", "max", "state", "sum"],
                    "units": {"energy": "kWh"},
                },
                blocking=True,
                return_response=True,
            )
            raw_stats = (response or {}).get("statistics", response or {})
            days: dict[str, dict[str, Any]] = {}
            for energy_key, statistic_id in entities.items():
                state = self.hass.states.get(statistic_id)
                configured = str((state.attributes.get("state_class") if state else "") or "").lower()
                friendly = str((state.attributes.get("friendly_name") if state else "") or "").lower()
                identifier = f"{statistic_id.lower()} {friendly}"
                # Cumulative meters need the Recorder daily change. Sensors that
                # already reset every day need the daily maximum/state instead;
                # using change on them produces the difference between two days.
                daily_meter = configured == "measurement" or any(
                    token in identifier for token in ("today", "daily", "day_energy", "energy_day", "daily_energy")
                )
                rows = [row for row in list((raw_stats or {}).get(statistic_id) or []) if isinstance(row, dict)]
                rows.sort(key=lambda row: str(row.get("start") or ""))
                previous_sum: float | None = None
                for row in rows:
                    # Recorder's ``sum`` is monotonic across source resets and is
                    # therefore the most reliable way to obtain a calendar-day
                    # amount from daily-reset energy meters.  The difference
                    # between adjacent daily sum snapshots equals that day's
                    # measured energy, matching the Energy dashboard semantics.
                    sum_value = self._num(row.get("sum"))
                    sum_delta = None
                    if sum_value is not None and previous_sum is not None:
                        candidate = sum_value - previous_sum
                        if candidate >= -0.001:
                            sum_delta = max(candidate, 0.0)
                    if sum_value is not None:
                        previous_sum = sum_value

                    if daily_meter:
                        delta = sum_delta
                        method = "ha_recorder_sum_delta"
                        if delta is None:
                            delta = self._num(row.get("max"))
                            method = "ha_recorder_daily_max"
                        if delta is None:
                            delta = self._num(row.get("state"))
                            method = "ha_recorder_daily_state"
                        if delta is None:
                            delta = self._num(row.get("change"))
                            method = "ha_recorder_daily_change_fallback"
                    else:
                        delta = self._num(row.get("change"))
                        method = "ha_recorder_daily_change"
                        if delta is None:
                            delta = sum_delta
                            method = "ha_recorder_sum_delta"
                    start_value = row.get("start")
                    if delta is None or start_value is None or delta < -0.001:
                        continue
                    if isinstance(start_value, datetime):
                        stamp = start_value
                    elif isinstance(start_value, str):
                        try:
                            stamp = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                    else:
                        try:
                            # WebSocket-style responses use milliseconds.
                            numeric = float(start_value)
                            if numeric > 10_000_000_000:
                                numeric /= 1000.0
                            stamp = datetime.fromtimestamp(numeric, tz=timezone.utc)
                        except (TypeError, ValueError, OSError):
                            continue
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    local_date = dt_util.as_local(stamp).date()
                    if local_date >= local_today:
                        continue
                    day_key = local_date.isoformat()
                    days.setdefault(day_key, {"date": day_key})[energy_key] = round(max(delta, 0.0), 4)
                    days[day_key][f"{energy_key}_source"] = statistic_id
                    days[day_key][f"{energy_key}_method"] = method
            self._statistics_energy_days = days
            self._statistics_status = {
                "status": "Ready" if days else "No completed statistic rows",
                "row_count": len(days),
                "entity_count": len(statistic_ids),
                "source": "Home Assistant Recorder long-term statistics",
            }
        except Exception as err:  # Recorder may be unavailable during early startup
            self._statistics_energy_days = {}
            self._statistics_status = {
                "status": "Recorder statistics unavailable",
                "row_count": 0,
                "entity_count": len(entities),
                "error": f"{type(err).__name__}: {err}",
            }

    def _authoritative_energy_days(self) -> dict[str, dict[str, Any]]:
        """Return only Recorder-backed completed daily energy rows."""
        return {day: dict(values) for day, values in self._statistics_energy_days.items()}

    def _energy_today(self) -> dict[str, Any]:
        return dict(self._authoritative_energy_days().get(self._local_day(), {}) or {})

    def _current_mapped_energy(self) -> dict[str, Any]:
        """Read current mapped energy totals directly from Home Assistant states.

        This is used only for the current in-progress local day. Completed days
        continue to come from Recorder long-term statistics.
        """
        result: dict[str, Any] = {"date": self._local_day()}
        for energy_key, entity_id in self._mapped_energy_entities().items():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            value = self._num(state.state)
            if value is None or value < 0:
                continue
            unit = str(state.attributes.get("unit_of_measurement") or "").lower()
            if unit == "wh":
                value /= 1000.0
            elif unit == "mwh":
                value *= 1000.0
            result[energy_key] = round(value, 4)
            result[f"{energy_key}_source"] = entity_id
            result[f"{energy_key}_method"] = "current_mapped_state"
        return result

    @staticmethod
    def _overlay_energy(raw: dict[str, Any], energy: dict[str, Any] | None) -> dict[str, Any]:
        """Overlay authoritative measured energy fields onto a weather record."""
        merged = dict(raw)
        energy_keys = (
            "solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh",
            "grid_export_energy_kwh", "battery_charge_energy_kwh",
            "battery_discharge_energy_kwh",
        )
        # Remove every persisted legacy energy value before joining Recorder.
        # A partial Recorder row must never make an old compact solar total look
        # authoritative merely because another field (for example export) joined.
        for key in energy_keys:
            merged.pop(key, None)
        merged.pop("energy_source", None)
        merged.pop("energy_complete", None)
        if not isinstance(energy, dict):
            return merged
        joined_count = 0
        for key in energy_keys:
            value = energy.get(key)
            if value is not None:
                merged[key] = value
                joined_count += 1
        if joined_count:
            merged["energy_source"] = "ha_recorder_statistics"
            merged["energy_complete"] = True
        return merged

    @staticmethod
    def _cloud_from_condition(condition: str | None) -> float | None:
        """Backfill legacy weather rows that predate numeric cloud capture.

        Measured cloud coverage always wins. This fallback is used only for
        historical rows where Home Assistant stored a condition but no numeric
        cloud field, allowing existing history to participate in cloud analytics.
        """
        mapping = {
            "sunny": 5.0, "clear-night": 5.0, "partlycloudy": 45.0,
            "cloudy": 85.0, "fog": 90.0, "rainy": 90.0, "pouring": 98.0,
            "lightning": 90.0, "lightning-rainy": 95.0, "snowy": 90.0,
            "snowy-rainy": 95.0, "windy": 30.0, "windy-variant": 50.0,
        }
        return mapping.get(str(condition or "").lower())

    def _public_day(self, raw: dict[str, Any]) -> dict[str, Any]:
        conditions = raw.get("condition_counts", {}) or {}
        condition = max(conditions, key=conditions.get) if conditions else raw.get("condition")
        forecast_solar = self._num(raw.get("forecast_solar_kwh"))
        # Energy values are valid only after the authoritative date join.  Any
        # legacy energy totals still present in weather storage are ignored.
        joined = raw.get("energy_source") in ("ha_recorder_statistics", "current_mapped_state")
        actual_solar = self._num(raw.get("solar_energy_kwh")) if joined else None
        error = None
        if forecast_solar is not None and forecast_solar > 0 and actual_solar is not None and actual_solar > 0:
            # Use a bounded symmetric percentage difference.  The previous
            # actual-only denominator could exceed 100% and collapse every
            # accuracy card to 0.0% even for valid positive forecast/actual
            # pairs.  This keeps the score in 0..100 and treats over- and
            # under-forecasting consistently.
            scale = max(forecast_solar, actual_solar)
            error = round(abs(forecast_solar - actual_solar) / scale * 100, 1)
        cloud_avg = self._average(raw, "cloud_coverage")
        cloud_source = "measured"
        if cloud_avg is None:
            cloud_avg = self._cloud_from_condition(condition)
            cloud_source = "condition_estimate" if cloud_avg is not None else "unavailable"
        return {
            "date": raw.get("date"), "condition": condition,
            "temperature_min": self._num(raw.get("temperature_min")),
            "temperature_avg": self._average(raw, "temperature"),
            "temperature_max": self._num(raw.get("temperature_max")),
            "humidity_avg": self._average(raw, "humidity"),
            "cloud_coverage_avg": cloud_avg,
            "cloud_coverage_source": cloud_source,
            "wind_speed_avg": self._average(raw, "wind_speed"),
            "wind_speed_max": self._num(raw.get("wind_speed_max")),
            "precipitation": self._num(raw.get("precipitation_max")),
            "solar_factor_avg": self._average(raw, "solar_factor"),
            "solar_energy_kwh": actual_solar,
            "house_energy_kwh": self._num(raw.get("house_energy_kwh")) if joined else None,
            "grid_import_energy_kwh": self._num(raw.get("grid_import_energy_kwh")) if joined else None,
            "grid_export_energy_kwh": self._num(raw.get("grid_export_energy_kwh")) if joined else None,
            "battery_discharge_energy_kwh": self._num(raw.get("battery_discharge_energy_kwh")) if joined else None,
            "battery_support_to_home_kwh": self._num(raw.get("battery_discharge_energy_kwh")) if joined else None,
            "forecast_solar_kwh": forecast_solar,
            "forecast_confidence": self._num(raw.get("forecast_confidence")),
            "forecast_error_percent": error,
            "sample_count": int(raw.get("sample_count", 0) or 0),
        }

    @staticmethod
    def _correlation_evidence(days: list[dict[str, Any]], x_key: str, y_key: str) -> dict[str, Any]:
        """Return Pearson correlation together with the evidence that supports it.

        Three pairs are enough to calculate a coefficient, but Zeus does not
        present that coefficient as learned weather intelligence until at least
        seven completed measured pairs exist. This keeps mathematically valid
        but very small samples visibly inside their evidence boundary.
        """
        pairs = [(d.get(x_key), d.get(y_key)) for d in days if isinstance(d.get(x_key), (int, float)) and isinstance(d.get(y_key), (int, float))]
        pair_count = len(pairs)
        value = None
        if pair_count >= 3:
            xs, ys = zip(*pairs); mx, my = sum(xs)/pair_count, sum(ys)/pair_count
            num = sum((x-mx)*(y-my) for x,y in pairs)
            den = sqrt(sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys))
            value = round(num/den, 3) if den else None
        return {
            "value": value,
            "pair_count": pair_count,
            "minimum_pairs": 7,
            "mature": pair_count >= 7 and value is not None,
            "evidence_boundary": "Pearson correlation describes association in completed measured daily pairs; it does not establish causation.",
        }

    @staticmethod
    def _correlation(days: list[dict[str, Any]], x_key: str, y_key: str) -> float | None:
        return WeatherHistoryEngine._correlation_evidence(days, x_key, y_key).get("value")

    async def async_capture_today(self) -> None:
        await self.async_refresh_authoritative_energy()
        if hasattr(self.core.weather, "async_refresh_forecast"):
            await self.core.weather.async_refresh_forecast()
        weather = self.core.weather.summary() or {}
        if not weather.get("available"):
            self.refresh(); return
        now = dt_util.now(); day = now.date().isoformat()
        days_store = self.data.setdefault("days", {})
        # Finalize every older local-calendar record before opening/updating the
        # current day. This makes the day lifecycle explicit and prevents the
        # previous day from remaining labelled as collecting after midnight.
        for old_day, old_raw in list(days_store.items()):
            if str(old_day) >= day or not isinstance(old_raw, dict):
                continue
            if not old_raw.get("completed_at"):
                old_raw["completed_at"] = now.isoformat()
                old_raw["in_progress"] = False
        raw = dict(days_store.get(day, {}) or {})
        raw["in_progress"] = True
        raw.pop("completed_at", None)
        raw.setdefault("date", day); raw["sample_count"] = int(raw.get("sample_count", 0) or 0) + 1
        for key in ("temperature", "humidity", "cloud_coverage", "wind_speed", "solar_factor"):
            value = self._num(weather.get(key))
            if value is None: continue
            raw[f"{key}_sum"] = float(raw.get(f"{key}_sum", 0) or 0) + value
            raw[f"{key}_count"] = int(raw.get(f"{key}_count", 0) or 0) + 1
            raw[f"{key}_min"] = value if raw.get(f"{key}_min") is None else min(float(raw[f"{key}_min"]), value)
            raw[f"{key}_max"] = value if raw.get(f"{key}_max") is None else max(float(raw[f"{key}_max"]), value)
        precip = self._num(weather.get("precipitation"))
        if precip is not None: raw["precipitation_max"] = max(float(raw.get("precipitation_max", 0) or 0), precip)
        condition = str(weather.get("condition") or "unknown")
        counts = dict(raw.get("condition_counts", {}) or {}); counts[condition] = int(counts.get(condition, 0)) + 1; raw["condition_counts"] = counts
        # Weather storage intentionally contains weather observations only.
        # Energy is joined at read time from Historical Analytics.
        for key in ("solar_energy_kwh","house_energy_kwh","grid_import_energy_kwh","grid_export_energy_kwh","battery_charge_energy_kwh","battery_discharge_energy_kwh"):
            raw.pop(key, None)
        forecast = self.core.forecast.summary() or {}
        # Prefer the calendar-day forecast matching the record date.  The
        # rolling next-24-hours total can span two dates and is not a valid
        # comparison against a single day's measured production.
        daily = forecast.get("daily_forecast") or []
        day_forecast = next((item for item in daily if isinstance(item, dict) and item.get("date") == day), None)
        forecast_value = self._num((day_forecast or {}).get("expected_solar_kwh"))
        if forecast_value is None or forecast_value <= 0:
            forecast_value = self._num(forecast.get("expected_solar_next_24h_kwh"))
        stored_forecast = self._num(raw.get("forecast_solar_kwh"))
        if (stored_forecast is None or stored_forecast <= 0) and forecast_value is not None and forecast_value > 0:
            raw["forecast_solar_kwh"] = forecast_value
        confidence_value = self._num(forecast.get("confidence") or forecast.get("confidence_percent"))
        if raw.get("forecast_confidence") is None and confidence_value is not None:
            raw["forecast_confidence"] = confidence_value
        raw["updated_at"] = now.isoformat()
        days_store[day] = raw
        for old in sorted(days_store)[:-RETENTION_DAYS]: days_store.pop(old, None)
        await self.store.async_save(self.data)
        self.refresh()
        self.event_bus.publish("WeatherHistoryCaptured", "WeatherHistoryEngine", {"date": day, "sample_count": raw["sample_count"]})

    @staticmethod
    def _period_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
        def values(key: str) -> list[float]:
            return [float(d[key]) for d in days if isinstance(d.get(key), (int, float))]
        temps = values("temperature_avg")
        clouds = values("cloud_coverage_avg")
        humidity = values("humidity_avg")
        winds = values("wind_speed_avg")
        precipitation = values("precipitation")
        solar = values("solar_energy_kwh")
        house = values("house_energy_kwh")
        exported = values("grid_export_energy_kwh")
        imported = values("grid_import_energy_kwh")
        errors = values("forecast_error_percent")
        return {
            "day_count": len(days),
            "complete_days": sum(1 for d in days if d.get("sample_count", 0) and isinstance(d.get("solar_energy_kwh"), (int, float))),
            "temperature_avg": round(sum(temps) / len(temps), 1) if temps else None,
            "temperature_min": min((d.get("temperature_min") for d in days if isinstance(d.get("temperature_min"), (int, float))), default=None),
            "temperature_max": max((d.get("temperature_max") for d in days if isinstance(d.get("temperature_max"), (int, float))), default=None),
            "cloud_coverage_avg": round(sum(clouds) / len(clouds), 1) if clouds else None,
            "humidity_avg": round(sum(humidity) / len(humidity), 1) if humidity else None,
            "wind_speed_avg": round(sum(winds) / len(winds), 1) if winds else None,
            "precipitation_total": round(sum(precipitation), 1) if precipitation else None,
            "solar_total_kwh": round(sum(solar), 2) if solar else None,
            "solar_avg_kwh": round(sum(solar) / len(solar), 2) if solar else None,
            "house_total_kwh": round(sum(house), 2) if house else None,
            "grid_export_total_kwh": round(sum(exported), 2) if exported else None,
            "grid_import_total_kwh": round(sum(imported), 2) if imported else None,
            "forecast_accuracy_percent": round(max(0, 100 - sum(errors) / len(errors)), 1) if errors else None,
            "forecast_pair_count": len(errors),
        }

    @staticmethod
    def _similar_days(days: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
        if len(days) < 2:
            return []
        target = days[-1]
        candidates = []
        for day in days[:-1]:
            distance = 0.0
            compared = 0
            for key, scale in (("temperature_avg", 12.0), ("cloud_coverage_avg", 100.0), ("humidity_avg", 100.0), ("wind_speed_avg", 25.0)):
                a, b = target.get(key), day.get(key)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    distance += abs(float(a) - float(b)) / scale
                    compared += 1
            if target.get("condition") and day.get("condition") and target.get("condition") != day.get("condition"):
                distance += 0.35
            if compared:
                candidates.append((distance / compared, day))
        return [dict(day, similarity_percent=round(max(0, 100 - score * 100), 0)) for score, day in sorted(candidates, key=lambda item: item[0])[:limit]]

    @staticmethod
    def _bucket_analysis(days: list[dict[str, Any]], key: str, buckets: list[tuple[str, float | None, float | None]]) -> list[dict[str, Any]]:
        """Average solar output for weather ranges, with evidence counts."""
        result: list[dict[str, Any]] = []
        for label, lower, upper in buckets:
            selected = []
            for day in days:
                value = day.get(key)
                solar = day.get("solar_energy_kwh")
                if not isinstance(value, (int, float)) or not isinstance(solar, (int, float)):
                    continue
                if lower is not None and float(value) < lower:
                    continue
                if upper is not None and float(value) >= upper:
                    continue
                selected.append(float(solar))
            result.append({
                "label": label,
                "day_count": len(selected),
                "average_solar_kwh": round(sum(selected) / len(selected), 2) if selected else None,
            })
        return result

    @staticmethod
    def _weather_score(day: dict[str, Any] | None) -> dict[str, Any]:
        """Solar-oriented weather quality score based only on available measurements."""
        if not day:
            return {"score": None, "grade": "Collecting", "evidence_count": 0}
        score = 100.0
        evidence = 0
        cloud = day.get("cloud_coverage_avg")
        if isinstance(cloud, (int, float)):
            score -= max(0.0, min(70.0, float(cloud) * 0.7)); evidence += 1
        rain = day.get("precipitation")
        if isinstance(rain, (int, float)):
            score -= min(20.0, float(rain) * 3.0); evidence += 1
        temp = day.get("temperature_avg")
        if isinstance(temp, (int, float)):
            distance = abs(float(temp) - 24.0)
            score -= min(15.0, distance * 0.8); evidence += 1
        wind = day.get("wind_speed_avg")
        if isinstance(wind, (int, float)):
            if 2 <= float(wind) <= 18:
                score += 3.0
            elif float(wind) > 35:
                score -= 8.0
            evidence += 1
        score = round(max(0.0, min(100.0, score)), 0)
        grade = "Excellent" if score >= 85 else "Good" if score >= 70 else "Mixed" if score >= 50 else "Poor"
        return {"score": score, "grade": grade, "evidence_count": evidence}

    @staticmethod
    def _weather_impact(days: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [d for d in days if isinstance(d.get("solar_energy_kwh"), (int, float))]
        if not valid:
            return {"difference_percent": None, "baseline_days": 0, "summary": "Collecting measured solar outcomes."}
        latest = valid[-1]
        baseline = valid[:-1][-30:]
        if not baseline:
            return {"difference_percent": None, "baseline_days": 0, "summary": "More historical days are needed for a weather impact comparison."}
        avg = sum(float(d["solar_energy_kwh"]) for d in baseline) / len(baseline)
        diff = round((float(latest["solar_energy_kwh"]) - avg) / avg * 100, 1) if avg else None
        if diff is None:
            summary = "A historical baseline is not available yet."
        elif diff >= 5:
            summary = f"The latest measured day produced {abs(diff):.1f}% more solar than the recent measured average; this comparison does not by itself attribute the difference to weather."
        elif diff <= -5:
            summary = f"The latest measured day produced {abs(diff):.1f}% less solar than the recent measured average; this comparison does not by itself attribute the difference to weather."
        else:
            summary = "The latest measured day was close to the recent measured solar average; this is a descriptive comparison, not a weather-causation claim."
        return {"difference_percent": diff, "baseline_days": len(baseline), "baseline_solar_kwh": round(avg, 2), "summary": summary}

    @staticmethod
    def _records(days: list[dict[str, Any]]) -> dict[str, Any]:
        def record(key: str, highest: bool = True) -> dict[str, Any] | None:
            valid = [d for d in days if isinstance(d.get(key), (int, float))]
            if not valid:
                return None
            return dict(max(valid, key=lambda d: d[key]) if highest else min(valid, key=lambda d: d[key]))
        return {
            "highest_temperature": record("temperature_max"),
            "lowest_temperature": record("temperature_min", False),
            "most_precipitation": record("precipitation"),
            "highest_wind": record("wind_speed_max"),
        }


    @staticmethod
    def _calendar_intelligence(days: list[dict[str, Any]]) -> dict[str, Any]:
        """Return compact month and season comparisons from stored daily records."""
        month_groups: dict[str, list[dict[str, Any]]] = {}
        season_groups: dict[str, list[dict[str, Any]]] = {}
        season_names = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring", 6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}
        for day in days:
            try:
                date = datetime.fromisoformat(str(day.get("date")))
            except (TypeError, ValueError):
                continue
            month_key = date.strftime("%Y-%m")
            season = season_names[date.month]
            season_year = date.year if date.month != 12 else date.year + 1
            season_key = f"{season} {season_year}"
            month_groups.setdefault(month_key, []).append(day)
            season_groups.setdefault(season_key, []).append(day)

        def compact(groups: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
            output = []
            for label, group in sorted(groups.items())[-limit:]:
                summary = WeatherHistoryEngine._period_summary(group)
                output.append({
                    "label": label,
                    "day_count": summary.get("day_count"),
                    "solar_total_kwh": summary.get("solar_total_kwh"),
                    "solar_avg_kwh": summary.get("solar_avg_kwh"),
                    "temperature_avg": summary.get("temperature_avg"),
                    "cloud_coverage_avg": summary.get("cloud_coverage_avg"),
                    "grid_export_total_kwh": summary.get("grid_export_total_kwh"),
                    "grid_import_total_kwh": summary.get("grid_import_total_kwh"),
                    "forecast_accuracy_percent": summary.get("forecast_accuracy_percent"),
                })
            return output

        return {
            "months": compact(month_groups, 12),
            "seasons": compact(season_groups, 8),
        }

    @staticmethod
    def _learning_insights(days: list[dict[str, Any]], correlations: dict[str, float | None], accuracy: float | None, cloud_buckets: list[dict[str, Any]], temperature_buckets: list[dict[str, Any]]) -> list[str]:
        insights: list[str] = []
        labels = {"cloud":"Cloud cover", "temperature":"Temperature", "humidity":"Humidity", "wind":"Wind", "precipitation":"Precipitation"}
        ranked = [(name, value) for name, value in correlations.items() if value is not None and sum(1 for d in days if isinstance(d.get({"cloud":"cloud_coverage_avg","temperature":"temperature_avg","humidity":"humidity_avg","wind":"wind_speed_avg","precipitation":"precipitation"}[name]), (int, float)) and isinstance(d.get("solar_energy_kwh"), (int, float))) >= 7]
        ranked.sort(key=lambda item: abs(float(item[1])), reverse=True)
        if ranked:
            name, value = ranked[0]
            strength = "strong" if abs(value) >= 0.65 else "moderate" if abs(value) >= 0.35 else "weak"
            direction = "positive" if value > 0 else "negative"
            insights.append(f"{labels.get(name,name.title())} is currently the strongest measured weather indicator, with a {strength} {direction} relationship to solar production.")
        usable_cloud = [b for b in cloud_buckets if b.get("day_count", 0) >= 2 and isinstance(b.get("average_solar_kwh"), (int, float))]
        if len(usable_cloud) >= 2:
            best = max(usable_cloud, key=lambda b: b["average_solar_kwh"])
            worst = min(usable_cloud, key=lambda b: b["average_solar_kwh"])
            if best["average_solar_kwh"]:
                drop = max(0, (best["average_solar_kwh"] - worst["average_solar_kwh"]) / best["average_solar_kwh"] * 100)
                insights.append(f"Solar output averages {drop:.0f}% lower in the weakest cloud-cover band than in the strongest measured band.")
        usable_temp = [b for b in temperature_buckets if b.get("day_count", 0) >= 2 and isinstance(b.get("average_solar_kwh"), (int, float))]
        if usable_temp:
            best = max(usable_temp, key=lambda b: b["average_solar_kwh"])
            insights.append(f"The best measured temperature range so far is {best['label']}, averaging {best['average_solar_kwh']:.1f} kWh of solar.")
        if accuracy is not None:
            insights.append(f"Measured solar forecast accuracy is {accuracy:.1f}% across days with complete forecast and actual data.")
        if len(days) < 7:
            insights.append(f"Zeus has {len(days)} stored weather day{'s' if len(days) != 1 else ''}; seven or more days are recommended before relying on correlations.")
        return insights[:5]

    def refresh(self) -> dict[str, Any]:
        local_today = self._local_day()
        energy_days = self._authoritative_energy_days()
        days: list[dict[str, Any]] = []
        for day_key, stored in sorted((self.data.get("days", {}) or {}).items()):
            # Historical weather intelligence compares completed local-calendar
            # days only. The current partial day remains in storage for capture,
            # but is intentionally excluded until midnight rollover.
            if str(day_key) >= local_today:
                continue
            merged = self._overlay_energy(dict(stored or {}), energy_days.get(str(day_key)))
            public = self._public_day(merged)
            # A weather day without an authoritative measured solar total is not
            # allowed into energy statistics, records, impact, or forecast error.
            if not isinstance(public.get("solar_energy_kwh"), (int, float)):
                continue
            public["energy_source"] = "historical_analytics"
            public["completed_day"] = True
            public["in_progress"] = False
            public["record_status"] = "completed"
            days.append(public)
        completed_days = list(days)
        # Expose the current in-progress day immediately after the first weather
        # capture. This confirms that the recorder is running and avoids an empty
        # Weather page until the next midnight. It is clearly marked partial and
        # excluded from correlations, records and forecast accuracy.
        today_stored = dict((self.data.get("days", {}) or {}).get(local_today, {}) or {})
        current_energy = self._current_mapped_energy()
        if today_stored and isinstance(current_energy.get("solar_energy_kwh"), (int, float)):
            current_merged = self._overlay_energy(today_stored, current_energy)
            current_merged["energy_source"] = "current_mapped_state"
            current_public = self._public_day(current_merged)
            current_public["completed_day"] = False
            current_public["in_progress"] = True
            current_public["record_status"] = "collecting"
            days.append(current_public)

        valid_solar = [d for d in completed_days if isinstance(d.get("solar_energy_kwh"), (int, float))]
        best = max(valid_solar, key=lambda d: d["solar_energy_kwh"]) if valid_solar else None
        worst = min(valid_solar, key=lambda d: d["solar_energy_kwh"]) if valid_solar else None
        errors = [d["forecast_error_percent"] for d in completed_days if isinstance(d.get("forecast_error_percent"), (int, float))]
        correlation_evidence = {
            "cloud": self._correlation_evidence(completed_days, "cloud_coverage_avg", "solar_energy_kwh"),
            "temperature": self._correlation_evidence(completed_days, "temperature_avg", "solar_energy_kwh"),
            "humidity": self._correlation_evidence(completed_days, "humidity_avg", "solar_energy_kwh"),
            "wind": self._correlation_evidence(completed_days, "wind_speed_avg", "solar_energy_kwh"),
            "precipitation": self._correlation_evidence(completed_days, "precipitation", "solar_energy_kwh"),
        }
        correlations = {key: evidence.get("value") for key, evidence in correlation_evidence.items()}
        accuracy = round(max(0, 100 - sum(errors) / len(errors)), 1) if errors else None
        period_summaries = {
            "today": self._period_summary(days[-1:]),
            "seven_days": self._period_summary(days[-7:]),
            "month": self._period_summary(days[-31:]),
            "year": self._period_summary(days[-365:]),
        }
        conditions: dict[str, int] = {}
        for day in completed_days[-365:]:
            condition = str(day.get("condition") or "unknown")
            conditions[condition] = conditions.get(condition, 0) + 1
        cloud_buckets = self._bucket_analysis(completed_days, "cloud_coverage_avg", [("0–20%",0,20),("20–40%",20,40),("40–60%",40,60),("60–80%",60,80),("80–100%",80,None)])
        temperature_buckets = self._bucket_analysis(completed_days, "temperature_avg", [("Below 10°C",None,10),("10–18°C",10,18),("18–24°C",18,24),("24–30°C",24,30),("Above 30°C",30,None)])
        latest = days[-1] if days else None
        calendar = self._calendar_intelligence(completed_days)
        self.last = {
            "status": "Ready" if days else "Collecting",
            "day_count": len(days), "completed_day_count": len(completed_days), "retention_days": RETENTION_DAYS,
            "recent_days": days[-31:], "best_solar_day": best, "worst_solar_day": worst,
            "energy_history_source": dict(self._statistics_status),
            "cloud_solar_correlation": correlations["cloud"], "temperature_solar_correlation": correlations["temperature"],
            "humidity_solar_correlation": correlations["humidity"], "wind_solar_correlation": correlations["wind"],
            "precipitation_solar_correlation": correlations["precipitation"], "correlations": correlations,
            "correlation_evidence": correlation_evidence,
            "correlation_minimum_pairs": 7,
            "correlation_evidence_boundary": "Weather correlations are descriptive associations from completed measured daily pairs and are not proof of causation.",
            "forecast_accuracy_percent": accuracy,
            "forecast_accuracy": {key: value.get("forecast_accuracy_percent") for key, value in period_summaries.items()},
            "period_summaries": period_summaries,
            "similar_days": self._similar_days(completed_days),
            "condition_distribution": conditions,
            "cloud_solar_buckets": cloud_buckets,
            "temperature_solar_buckets": temperature_buckets,
            "weather_score": self._weather_score(latest),
            "weather_impact": self._weather_impact(completed_days),
            "weather_records": self._records(completed_days),
            "learning_insights": self._learning_insights(completed_days, correlations, accuracy, cloud_buckets, temperature_buckets),
            "calendar_intelligence": calendar,
            "summary": "Historical weather intelligence is collecting completed weather and authoritative measured energy days." if len(completed_days) < 3 else "Weather intelligence is synchronized with Home Assistant Recorder and ready for supported comparisons.",
            "current_partial_day_excluded": True,
            "day_lifecycle": {
                "local_date": local_today,
                "current_status": "collecting" if days and bool(days[-1].get("in_progress")) else "waiting",
                "current_day": dict(days[-1]) if days and bool(days[-1].get("in_progress")) else None,
                "latest_completed_day": dict(completed_days[-1]) if completed_days else None,
                "rollover_policy": "Previous local day is finalized at the first capture after midnight; completed energy is then loaded from Recorder.",
            },
            "storage": "Weather observations in Home Assistant storage; completed energy totals loaded from Recorder long-term statistics.",
            "recorder_safe": True,
        }
        return self.last

    def summary(self) -> dict[str, Any]: return self.last
    def recorder_summary(self) -> dict[str, Any]:
        out = dict(self.last)
        compact_keys = (
            "date", "condition", "temperature_min", "temperature_avg", "temperature_max",
            "humidity_avg", "cloud_coverage_avg", "wind_speed_avg", "precipitation",
            "solar_energy_kwh", "house_energy_kwh", "grid_import_energy_kwh",
            "grid_export_energy_kwh", "forecast_solar_kwh", "forecast_error_percent", "sample_count",
        )
        out["recent_days"] = [
            {key: day.get(key) for key in compact_keys if day.get(key) is not None}
            for day in (out.get("recent_days") or [])[-31:]
        ]
        return out

__all__ = ["WeatherHistoryEngine"]
