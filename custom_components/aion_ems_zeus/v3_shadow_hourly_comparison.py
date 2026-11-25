"""Compare completed DC observations to pre-day V3 captures for diagnostics."""
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .v3_shadow_day_export import day_bounds, energy, parse_day, unique_hours, utc_stamp


def compare_hourly_dc(snapshots, measured_hours, entity_ids, now):
    if now.utcoffset() is None:
        raise ValueError("timezone-aware now required")
    now = now.astimezone(timezone.utc)
    chosen = {}
    for snapshot in snapshots if isinstance(snapshots, list) else []:
        try:
            day = parse_day(snapshot["target_date_local"])
            captured = utc_stamp(snapshot["retrieved_at_utc"])
            zone = ZoneInfo(snapshot.get("local_timezone", "Europe/Zurich"))
            start, end = day_bounds(day, zone)
            rows = unique_hours(snapshot.get("hourly_array_forecasts"))
        except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError, OverflowError):
            continue
        if rows and captured < start and captured <= now and (day not in chosen or captured < chosen[day][0]):
            chosen[day] = captured, start, end, snapshot.get("array_power_entities"), rows

    measured = {stamp: row for stamp, row in unique_hours(measured_hours).items() if stamp <= now}
    comparisons = []
    missing = mismatch = 0
    for day, (_, start, end, source_ids, rows) in chosen.items():
        for stamp, row in rows.items():
            if (row is None or not start < stamp <= min(end, now)
                    or stamp.minute or stamp.second or stamp.microsecond):
                continue
            observed = measured.get(stamp)
            if observed is None or observed.get("status") != "complete":
                missing += 1
                continue
            predicted = row.get("array_kwh")
            if (not entity_ids or len(set(entity_ids)) != len(entity_ids) or source_ids != entity_ids
                    or not isinstance(predicted, list) or len(predicted) != len(entity_ids)):
                mismatch += 1
                continue
            try:
                actual_arrays = observed["arrays"]
                actual = {entry["entity_id"]: entry["measured_kwh"] for entry in actual_arrays}
            except (KeyError, TypeError):
                continue
            if actual.keys() != set(entity_ids) or len(actual_arrays) != len(entity_ids):
                mismatch += 1
                continue
            if any(energy(value) is None for value in [*predicted, *actual.values()]):
                continue
            comparisons.append({
                "target_date_local": day.isoformat(), "hour_end_utc": stamp.isoformat(),
                "forecast_total_kwh": round(sum(predicted), 4),
                "measured_dc_total_kwh": round(sum(actual.values()), 4),
                "arrays": [{"entity_id": entity, "forecast_loss_adjusted_kwh": round(value, 4),
                            "measured_dc_kwh": round(actual[entity], 4),
                            "difference_kwh": round(value - actual[entity], 4)}
                           for entity, value in zip(entity_ids, predicted)],
            })
    comparisons.sort(key=lambda row: row["hour_end_utc"])
    return {
        "status": "comparison_available" if comparisons else "awaiting_matching_completed_hours",
        "comparison_count": len(comparisons), "measured_hour_count": len(measured),
        "partial_measured_hours": sum(row is None or row.get("status") != "complete" for row in measured.values()),
        "missing_complete_hour_matches": missing, "mapping_mismatch_hours": mismatch,
        "forecast_target_count": len(chosen), "latest_comparisons": comparisons[-8:],
        "accuracy_validated": False, "energy_basis": "forecast_loss_adjusted_vs_measured_dc",
        "warning": "Different energy bases: diagnostic comparison only, not accuracy scoring or calibration.",
        "pre_day_hourly_snapshot_targets": [day.isoformat() for day in sorted(chosen)],
    }
