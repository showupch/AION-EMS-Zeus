"""Quality report for forward-only irradiance/PV snapshots; no forecasting."""
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite


def evidence_quality(records, *, min_daylight_hours=6):
    """Reject unverified snapshots as training data even if coverage is good."""
    days = defaultdict(list)
    rejected = 0
    for key, row in (records or {}).items():
        try:
            ts = datetime.fromisoformat(key)
            if ts.tzinfo is None or ts.utcoffset().total_seconds() != 0:
                raise ValueError("timestamp must be UTC")
            rad = float(row["irradiance_w_m2"])
            pv = float(row["pv_w"])
            if not all(map(isfinite, (rad, pv))) or not (0 <= rad <= 1500 and pv >= 0):
                raise ValueError("invalid values")
            if row.get("status") != "unvalidated_hourly_sample":
                raise ValueError("unknown provenance")
            if row.get("provenance") != "observed_ha_sensor_state":
                raise ValueError("unknown source")
            days[ts.date().isoformat()].append((ts, rad, pv))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            rejected += 1
    summaries = []
    for date, samples in sorted(days.items()):
        samples.sort(key=lambda x:x[0])
        daylight = [(ts,rad,pv) for ts,rad,pv in samples if rad >= 30]
        hours = len({ts.hour for ts,_,_ in samples})
        daylight_hours = len({ts.hour for ts,_,_ in daylight})
        summaries.append({
            "date_utc":date, "snapshot_hours":hours,
            "daylight_snapshot_hours":daylight_hours,
            "coverage_threshold_met":daylight_hours >= min_daylight_hours,
            "training_eligible":False,
            "reason":"snapshots_are_not_hourly_averages_and_not_aligned_to_recorder_hourly_energy",
        })
    return {
        "status":"quality_report_only",
        "total_snapshots":sum(len(x) for x in days.values()),
        "rejected_records":rejected,
        "days":summaries[-14:],
        "training_eligible_days":0,
        "used_by_forecast":False,
        "note":"UTC dates are capture buckets, not local solar-day boundaries. No calibration or weather normalization is performed.",
    }
