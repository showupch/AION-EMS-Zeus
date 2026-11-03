"""Read-only consistency analysis of the established and independent V2.1 evaluators.

This is a diagnostic comparator, not a consensus or production selector.
Different eligibility policies are reported rather than silently reconciled.
"""
from __future__ import annotations
from typing import Any
from .forecast_v21_shadow import evaluate_shadow
from .forecast_v21_bridge import bridge_v21_history, compare_shadow

def compare_evaluators(target_date: str, baseline_kwh: float,
                       weather_coverage: float | None, samples: list[dict[str, Any]],
                       local_timezone: str) -> dict[str, Any]:
    """Compare candidates on the same frozen evidence without modifying inputs."""
    bridge = compare_shadow(baseline_kwh, target_date, weather_coverage,
                            samples, local_timezone)
    try:
        mapped = bridge_v21_history(samples, target_date, local_timezone)
        legacy_rows = [{**row, "verified": True} for row in mapped["history"]]
        old = evaluate_shadow(target_date, baseline_kwh, legacy_rows, weather_coverage)
    except (ValueError, TypeError, OverflowError):
        old = {"status": "invalid_comparison_context", "candidate_kwh": None,
               "training_days": 0, "multiplier": None}
    a, b = old.get("candidate_kwh"), bridge.get("candidate_kwh")
    return {
        "status": "compared" if a is not None and b is not None else "not_comparable",
        "existing": {k: old.get(k) for k in ("status", "candidate_kwh", "training_days", "multiplier")},
        "independent": {k: bridge.get(k) for k in ("status", "candidate_kwh", "training_days", "multiplier")},
        "candidate_delta_kwh": round(b-a, 3) if isinstance(a, (int,float)) and isinstance(b, (int,float)) else None,
        "policies_differ": (old.get("status") != bridge.get("status")
                            or old.get("training_days") != bridge.get("training_days")
                            or (a is not None and b is not None and abs(a-b)>0.001)),
        "evidence_audit": bridge.get("bridge_audit", {}),
        "production_applied": False,
        "control_permission": False,
    }
