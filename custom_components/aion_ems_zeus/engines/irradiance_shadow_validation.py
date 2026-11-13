"""Read-only, leave-one-day-out irradiance/PV relationship validation.

This is *not* a next-day forecast: observed irradiance is unavailable for tomorrow.
The day holdout avoids evaluating a relationship on the same day's samples.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import isfinite


def validate_irradiance_pv(samples, *, min_training_days=3, min_test_buckets=4):
    """Return strictly descriptive, out-of-sample PV errors for paired samples.

    samples: (local_day ISO string, irradiance W/m², PV W) tuples.
    Only nonnegative finite measurements with irradiance >= 50 W/m² count.
    Fit a nonnegative through-origin slope on *other* days, not on the test day.
    """
    days = defaultdict(list)
    rejected = 0
    for day, irr, pv in samples:
        try:
            day = str(day)
            datetime.fromisoformat(day).date()
            irr, pv = float(irr), float(pv)
            if not (isfinite(irr) and isfinite(pv) and 50 <= irr <= 1500 and 0 <= pv <= 100000):
                raise ValueError
            days[day].append((irr, pv))
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    eligible = {d: rows for d, rows in days.items() if len(rows) >= min_test_buckets}
    results = []
    for test_day in sorted(eligible):
        training = [r for d, rows in eligible.items() if d != test_day for r in rows]
        if len(eligible) - 1 < min_training_days or not training:
            continue
        denominator = sum(irr * irr for irr, _ in training)
        if denominator <= 0:
            continue
        slope = max(0., sum(irr * pv for irr, pv in training) / denominator)
        test = eligible[test_day]
        mae_kw = sum(abs(slope * irr - pv) for irr, pv in test) / len(test) / 1000
        bias_kw = sum(slope * irr - pv for irr, pv in test) / len(test) / 1000
        results.append({"date": test_day, "buckets": len(test),
                        "mean_absolute_error_kw": round(mae_kw, 3),
                        "mean_signed_error_kw": round(bias_kw, 3),
                        "trained_on_days": len(eligible) - 1})
    return {
        "status": "validated_descriptive_only" if results else "insufficient_evidence",
        "method": "leave_one_day_out_observed_irradiance_to_pv",
        "eligible_days": len(eligible),
        "eligible_buckets": sum(map(len, eligible.values())),
        "rejected_samples": rejected,
        "evaluated_days": len(results),
        "mean_absolute_error_kw": round(sum(r["mean_absolute_error_kw"] for r in results)/len(results), 3) if results else None,
        "days": results,
        "forecast_recalculated": False,
        "production_applied": False,
        "warning": "Uses observed irradiance, not tomorrow's forecast irradiance; not a day-ahead accuracy score.",
    }
