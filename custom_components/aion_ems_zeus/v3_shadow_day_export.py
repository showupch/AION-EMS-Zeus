"""Allowlisted local-day export for the read-only V3 Lab."""
from datetime import date, datetime, time, timedelta, timezone

from .const import VERSION
from .v3_shadow_dc_history import MIN_COVERAGE_SECONDS
from .v3_shadow_actual_audit import AUTHORITATIVE_METHOD, paired_capture, valid_forecast


def parse_day(value):
    day = date.fromisoformat(value)
    if value != day.isoformat() or not 2 <= day.year <= 9998:
        raise ValueError("Invalid calendar date")
    return day


def utc_stamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Missing UTC offset")
    return stamp.astimezone(timezone.utc)


def energy(value):
    if type(value) in (float, int) and 0 <= value <= 1_000_000:
        return value
    return None


def unique_hours(rows):
    by_hour = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            stamp = utc_stamp(row["hour_end_utc"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        by_hour[stamp] = None if stamp in by_hour else row
    return by_hour


def day_bounds(day, zone):
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    return start, end


MODEL_REVISION = "v3-isotropic-hourly-1"
# The supplied v16.0.675 archive used the same hourly model.
KNOWN_RELEASES = ("16.0.675", "16.0.676", VERSION)
RADIATION_FIELDS = ("ghi_wh_m2", "dni_wh_m2", "dhi_wh_m2")


def bounded(value, low, high):
    return value if type(value) in (float, int) and low <= value <= high else None


def array_layout(forecast, configured):
    """Keep positional forecast geometry and match observations by internal entity."""
    geometry = forecast.get("array_forecasts")
    mapped = forecast.get("array_power_entities")
    if not forecast:
        geometry = [row for row in configured if isinstance(row, dict)]
        mapped = [row.get("power_entity") for row in geometry]

    geometry = geometry if isinstance(geometry, list) else []
    mapped = mapped if isinstance(mapped, list) else []
    count = len(geometry) or len(mapped)
    arrays = []
    for index in range(count):
        row = geometry[index] if geometry and isinstance(geometry[index], dict) else {}
        arrays.append({
            "array_index": index + 1,
            "capacity_kwp": bounded(row.get("capacity_kwp"), 0, 1000),
            "azimuth_deg": bounded(row.get("azimuth_deg"), 0, 360),
            "tilt_deg": bounded(row.get("tilt_deg"), 0, 90),
        })
    if len(mapped) != count:
        mapped = [None] * count
    return arrays, [entity if isinstance(entity, str) and mapped.count(entity) == 1 else None
                    for entity in mapped]


def measured_arrays(observation):
    indexed = {}
    rows = observation.get("arrays")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("entity_id"), str):
            entity = row["entity_id"]
            indexed[entity] = {} if entity in indexed else row
    return indexed


def secondary_weather_hours(rows, start, end):
    """Normalize the existing MeteoSwiss hourly mean, which is end-stamped."""
    normalized = []
    for row in rows if isinstance(rows, list) else []:
        try:
            stamp = utc_stamp(row["datetime"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if start < stamp <= end and (stamp - start) % timedelta(hours=1) == timedelta(0):
            normalized.append({
                "hour_end_utc": stamp.isoformat(),
                "ghi_wh_m2": energy(row.get("global_radiation")),
            })
    return normalized


def build_day_export(day, zone, snapshots, current, measured, actuals, now, observed_ghi=(),
                     *, configured=()):
    start, end = day_bounds(day, zone)
    zone_name = str(zone)
    captures = {}
    for row in snapshots:
        if (not isinstance(row, dict) or row.get("target_date_local") != day.isoformat()
                or row.get("local_timezone") != zone_name
                or not valid_forecast(row.get("forecast_v3_kwh"))):
            continue
        try:
            captured = utc_stamp(row["retrieved_at_utc"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        paired = paired_capture(row)
        previous = captures.get(paired)
        if captured < start and captured <= now and (previous is None or captured > previous[0]):
            captures[paired] = captured, row

    selected = captures.get(True) or captures.get(False)
    captured = selected[0] if selected else None
    forecast = selected[1] if selected else {}
    if (not selected and current and current.get("local_timezone") == zone_name
            and current.get("target_date_local") == day.isoformat()):
        try:
            current_capture = utc_stamp(current["retrieved_at_utc"])
        except (ValueError, TypeError, KeyError, OverflowError):
            current_capture = None
        if current_capture is not None and current_capture <= now:
            captured, forecast = current_capture, current

    result = render_day_export(day, zone, start, end, captured, forecast, bool(selected),
                               measured, actuals, now, observed_ghi, configured)
    if result is not None:
        result["forecast_capture_kind"] = (
            "paired" if True in captures else "v3_only" if False in captures else None
        )
        result["v3_only_capture"] = None
        if True in captures and False in captures:
            other_time, other = captures[False]
            alternative = render_day_export(day, zone, start, end, other_time, other, True,
                                            measured, actuals, now, observed_ghi, configured)
            alternative["forecast_capture_kind"] = "v3_only"
            result["v3_only_capture"] = alternative
    return result


def render_day_export(day, zone, start, end, captured, forecast, archived,
                      measured, actuals, now, observed_ghi, configured):
    arrays, entities = array_layout(forecast, configured)
    forecasts = unique_hours(forecast.get("hourly_array_forecasts"))
    secondary = unique_hours(forecast.get("secondary_weather_hours"))
    measured_hours = unique_hours(measured)
    station = dict(observed_ghi)
    hours = []
    actual = None
    day_completed = end <= now
    if day_completed:
        for row in actuals:
            if (isinstance(row, dict) and row.get("date") == day.isoformat()
                    and row.get("solar_energy_kwh_method") == AUTHORITATIVE_METHOD
                    and row.get("solar_energy_kwh_source")):
                value = energy(row.get("solar_energy_kwh"))
                if value is not None:
                    actual = value
    for hour in range(1, (end - start) // timedelta(hours=1) + 1):
        stamp = start + timedelta(hours=hour)
        model = forecasts.get(stamp) or {}
        provider = secondary.get(stamp) or {}
        observation = (measured_hours.get(stamp) or {}) if stamp <= now else {}
        observations = measured_arrays(observation)
        predictions = model.get("array_kwh")
        if not isinstance(predictions, list) or len(predictions) != len(arrays):
            predictions = [None] * len(arrays)
        array_hours = []
        for array, predicted, entity in zip(arrays, predictions, entities):
            reading = observations.get(entity, {})
            coverage = bounded(reading.get("coverage_seconds"), 0, 3600)
            measured_dc = energy(reading.get("measured_kwh"))
            if ((coverage is None and observation.get("status") != "complete")
                    or (coverage is not None and coverage < MIN_COVERAGE_SECONDS)):
                measured_dc = None
            array_hours.append({
                "array_index": array["array_index"],
                "forecast_loss_adjusted_kwh": energy(predicted),
                "measured_dc_kwh": measured_dc,
                "measurement_coverage_seconds": coverage,
            })
        totals = [entry["measured_dc_kwh"] for entry in array_hours]
        hours.append({
            "hour_end_utc": stamp.isoformat(),
            "hour_end_local": stamp.astimezone(zone).isoformat(),
            "forecast_loss_adjusted_kwh": energy(model.get("total_kwh")),
            "measured_dc_kwh": round(sum(totals), 4) if totals and None not in totals else None,
            "observed_ghi_wh_m2": energy(station.get(hour)) if stamp <= now else None,
            # Existing radiation keys describe the Open-Meteo forecast capture.
            **{field: energy(model.get(field)) for field in RADIATION_FIELDS},
            "meteoswiss_ghi_wh_m2": energy(provider.get("ghi_wh_m2")),
            "arrays": array_hours,
        })

    v2 = energy(forecast.get("forecast_v2_kwh")) if archived else None
    v3 = energy(forecast.get("forecast_v3_kwh"))
    completeness = {
        "expected_hours": len(hours),
        "elapsed_hours": sum(start + timedelta(hours=i) <= now for i in range(1, len(hours) + 1)),
        "forecast_hours": sum(row["forecast_loss_adjusted_kwh"] is not None for row in hours),
        "measured_dc_hours": sum(row["measured_dc_kwh"] is not None for row in hours),
        "open_meteo_hours": sum(all(row[field] is not None for field in RADIATION_FIELDS) for row in hours),
        "meteoswiss_hours": sum(row["meteoswiss_ghi_wh_m2"] is not None for row in hours),
        "observed_ghi_hours": sum(row["observed_ghi_wh_m2"] is not None for row in hours),
        "verified_daily_solar_energy_available": actual is not None,
        "arrays": [],
    }
    for index in range(len(arrays)):
        entries = [row["arrays"][index] for row in hours]
        completeness["arrays"].append({
            "array_index": index + 1,
            "forecast_hours": sum(row["forecast_loss_adjusted_kwh"] is not None for row in entries),
            "measured_dc_hours": sum(row["measured_dc_kwh"] is not None for row in entries),
            "coverage_hours": sum(row["measurement_coverage_seconds"] is not None for row in entries),
        })
    evidence_fields = (*RADIATION_FIELDS, "meteoswiss_ghi_wh_m2", "observed_ghi_wh_m2",
                       "forecast_loss_adjusted_kwh", "measured_dc_kwh")
    if all(value is None for value in (v2, v3, actual)) and not any(
        any(row[field] is not None for field in evidence_fields)
        or any(any(entry[field] is not None for field in (
            "forecast_loss_adjusted_kwh", "measured_dc_kwh", "measurement_coverage_seconds"
        )) for entry in row["arrays"]) for row in hours
    ):
        return None

    capture_release = forecast.get("zeus_version")
    revision = forecast.get("model_revision")
    try:
        secondary_capture = utc_stamp(forecast["secondary_retrieved_at_utc"])
    except (ValueError, TypeError, KeyError, OverflowError):
        secondary_capture = None
    return {
        "schema_version": 3, "zeus_version": VERSION, "date_local": day.isoformat(),
        "timezone": str(zone), "day_completed": day_completed,
        "forecast_basis": "pre_day_capture" if archived else "current_model" if forecast else None,
        "forecast_captured_at_utc": captured.isoformat() if captured else None,
        "forecast_capture_zeus_version": capture_release if capture_release in KNOWN_RELEASES else None,
        "model_revision": MODEL_REVISION if revision == MODEL_REVISION else None,
        "secondary_weather_captured_at_utc": (
            secondary_capture.isoformat() if secondary_capture and secondary_capture <= now else None
        ),
        "hour_interval": "preceding_hour_ending_at_timestamp",
        "forecast_issue_time_verified": False, "accuracy_validated": False,
        "forecast_v2_kwh": v2, "forecast_v3_kwh": v3,
        "verified_ha_energy_solar_kwh": actual, "pv_arrays": arrays,
        "data_completeness": completeness, "hours": hours,
    }
