"""Conservative selection of complete Recorder solar sources for hourly validation.

A source with zero rows is not assumed to have produced zero energy. It may
only be omitted if complete remaining sources independently reconcile with
Zeus's measured completed-day solar total. Partial sources always block.
"""

from __future__ import annotations

from math import isfinite
from typing import Any


def select_hourly_solar_sources(
    per_source: dict[str, dict[Any, float]], expected: set[Any],
    canonical_daily_kwh: float | None,
) -> dict[str, Any]:
    """Return selected sources, exclusions and an auditable fail-closed result."""
    complete = {sid: rows for sid, rows in per_source.items() if set(rows) == expected}
    empty = [sid for sid, rows in per_source.items() if not rows]
    partial = {sid: len(rows) for sid, rows in per_source.items()
               if rows and set(rows) != expected}
    if partial:
        return {"status": "incomplete_recorder_hours", "source": next(iter(partial)),
                "matched_hours": next(iter(partial.values())), "expected_hours": len(expected),
                "partial_sources": partial}
    if not complete:
        return {"status": "incomplete_recorder_hours", "source": empty[0] if empty else None,
                "matched_hours": 0, "expected_hours": len(expected),
                "reason": "No complete measured solar source"}
    measured = sum(sum(rows.values()) for rows in complete.values())
    if empty:
        if (canonical_daily_kwh is None or not isfinite(canonical_daily_kwh)
                or canonical_daily_kwh <= 0):
            return {"status": "unverified_empty_solar_sources", "empty_sources": empty,
                    "reason": "Independent completed-day solar total unavailable"}
        # Tolerance accounts for independent statistics rounding, not missing production.
        tolerance = max(0.1, canonical_daily_kwh * 0.02)
        if abs(measured - canonical_daily_kwh) > tolerance:
            return {"status": "daily_solar_reconciliation_failed", "empty_sources": empty,
                    "measured_kwh": round(measured, 4),
                    "canonical_kwh": round(canonical_daily_kwh, 4),
                    "tolerance_kwh": round(tolerance, 4)}
    return {"status": "ready", "included_sources": list(complete),
            "excluded_empty_sources": empty,
            "measured_kwh": round(measured, 4),
            "canonical_kwh": canonical_daily_kwh if empty else None,
            "reconciliation": "completed_day_match" if empty else "all_sources_complete"}
