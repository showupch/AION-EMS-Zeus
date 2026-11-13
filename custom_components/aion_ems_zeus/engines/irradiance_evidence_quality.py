"""Read-only quality checks for synchronized on-site irradiance and PV samples.

This module never produces a forecast or a production correction.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from math import isfinite
from statistics import median


def summarize_paired_samples(samples, *, min_irradiance=50.0, min_buckets=24, min_days=3):
    """Return a guarded descriptive relationship, not a forecast multiplier.

    samples: iterable of (timezone-aware local datetime, irradiance W/m², PV W).
    Samples must already be synchronized by the Recorder query. A day must
    contribute at least two buckets; single-bucket days are not independent
    evidence. The morning/afternoon split is descriptive only.
    """
    buckets = defaultdict(list)
    rejected = 0
    for stamp, irr, pv in samples:
        try:
            if not isinstance(stamp, datetime) or stamp.tzinfo is None:
                raise ValueError
            irr, pv = float(irr), float(pv)
            if not isfinite(irr) or not isfinite(pv) or irr < min_irradiance or pv < 0:
                raise ValueError
            buckets[stamp.date()].append((stamp.hour, irr, pv))
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    usable_days = {day: rows for day, rows in buckets.items() if len(rows) >= 2}
    rows = [row for day_rows in usable_days.values() for row in day_rows]
    result = {
        "status": "insufficient_evidence", "eligible_buckets": len(rows),
        "eligible_days": len(usable_days), "rejected_samples": rejected,
        "min_buckets": min_buckets, "min_days": min_days,
        "production_applied": False, "forecast_recalculated": False,
        "note": "Descriptive on-site irradiance/PV evidence, not a future forecast or correction factor.",
    }
    if len(rows) < min_buckets or len(usable_days) < min_days:
        result["reason"] = "insufficient_independent_daylight_evidence"
        return result
    # Ratios are descriptive, not a calibrated transfer function: geometry,
    # clipping, shading, temperature and sun angle are not controlled here.
    def describe(group):
        if not group:
            return None
        return {"buckets": len(group),
                "median_pv_kw_per_100_w_m2": round(median(pv / irr / 10 for _, irr, pv in group), 3)}
    result.update(status="descriptive_evidence_available", reason=None,
                  morning=describe([r for r in rows if r[0] < 12]),
                  afternoon=describe([r for r in rows if r[0] >= 12]))
    return result
