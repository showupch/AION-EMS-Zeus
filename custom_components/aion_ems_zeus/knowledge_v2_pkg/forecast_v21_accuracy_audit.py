"""Read-only walk-forward evaluation of V2 and V2.1 on frozen daily outcomes.

No candidate is allowed to learn from its own target day or any later day.
No persistence, production forecast changes, or device-control permissions.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

from .forecast_v21_bridge import bridge_v21_history
from .forecast_v21_comparison import compare_evaluators


def _metrics(errors: list[float]) -> dict[str, float | int | None]:
    if not errors:
        return {"days": 0, "mae_kwh": None, "bias_kwh": None, "rmse_kwh": None}
    n = len(errors)
    return {"days": n, "mae_kwh": round(sum(map(abs, errors)) / n, 3),
            "bias_kwh": round(sum(errors) / n, 3),
            "rmse_kwh": round((sum(e * e for e in errors) / n) ** .5, 3)}


def evaluate_frozen_accuracy(samples: list[dict[str, Any]], local_timezone: Any,
                             *, min_training_days: int = 7) -> dict[str, Any]:
    """Evaluate only independently frozen, weather-qualified daily records.

    Candidate metrics are computed on the intersection of days for which both
    candidates are available, so their MAE/bias comparisons are like-for-like.
    """
    if not isinstance(min_training_days, int) or isinstance(min_training_days, bool) or min_training_days < 1:
        raise ValueError("min_training_days must be a positive integer")
    if not isinstance(samples, list):
        raise TypeError("samples must be a list")
    # A future synthetic target allows the existing bridge to validate every
    # completed record; no current-date inference or synthetic weather.
    valid_dates = []
    for row in samples:
        if isinstance(row, dict):
            try:
                valid_dates.append(date.fromisoformat(str(row.get("target_date"))))
            except ValueError:
                pass
    if not valid_dates:
        return {"status": "insufficient_evidence", "qualified_days": 0,
                "evaluated_days": 0, "baseline": _metrics([]),
                "existing": _metrics([]), "independent": _metrics([]),
                "paired_baseline": _metrics([]), "days": [],
                "production_applied": False, "control_permission": False}
    target = date.fromordinal(max(valid_dates).toordinal() + 1).isoformat()
    bridged = bridge_v21_history(samples, target, local_timezone)
    history = sorted((r for r in bridged["history"] if r["weather_coverage"] >= .8),
                     key=lambda r: r["date"])
    baseline_errors = [r["forecast_kwh"] - r["actual_kwh"] for r in history]
    paired = {"baseline": [], "existing": [], "independent": []}
    results = []
    for index, row in enumerate(history):
        if index < min_training_days:
            continue
        # Only earlier samples are provided to both evaluators.
        past = [s for s in samples if isinstance(s, dict) and
                isinstance(s.get("target_date"), str) and s["target_date"] < row["date"]]
        comparison = compare_evaluators(row["date"], row["forecast_kwh"],
                                        row["weather_coverage"], past, local_timezone)
        a = comparison["existing"]["candidate_kwh"]
        b = comparison["independent"]["candidate_kwh"]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v)
                   for v in (a, b)):
            continue
        paired["baseline"].append(row["forecast_kwh"] - row["actual_kwh"])
        paired["existing"].append(a - row["actual_kwh"])
        paired["independent"].append(b - row["actual_kwh"])
        results.append({"date": row["date"], "actual_kwh": row["actual_kwh"],
                        "baseline_v2_kwh": row["forecast_kwh"],
                        "existing_v21_kwh": round(a, 3),
                        "independent_v21_kwh": round(b, 3),
                        "training_days_available": index})
    return {"status": "compared" if results else "insufficient_evidence",
            "qualified_days": len(history), "evaluated_days": len(results),
            "baseline": _metrics(baseline_errors),
            "paired_baseline": _metrics(paired["baseline"]),
            "existing": _metrics(paired["existing"]),
            "independent": _metrics(paired["independent"]),
            "evidence_audit": bridged["audit"], "days": results,
            "note": "Historical walk-forward diagnostic only; no future-day training or production changes",
            "production_applied": False, "control_permission": False}
