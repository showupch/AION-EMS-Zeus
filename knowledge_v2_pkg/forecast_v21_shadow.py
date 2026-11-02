"""Forecast V2.1 experimental, read-only calibration candidate.

No Home Assistant imports, registry writes, control outputs, or forecast overrides.
Callers must supply frozen pre-day predictions and independently verified
completed-day actuals. This module deliberately does not infer weather coverage.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import median
from typing import Any


def evaluate_shadow(target_date: str, current_kwh: float, history: list[dict[str, Any]],
                    weather_coverage: float | None) -> dict[str, Any]:
    """Produce an explicitly non-production forecast candidate.

    history: {date, forecast_kwh, actual_kwh, verified, weather_coverage}
    All training dates must precede target_date. Incomplete or unverified
    measurements are excluded. A missing forecast is never fabricated.
    """
    target = date.fromisoformat(target_date)
    if not isfinite(current_kwh) or current_kwh < 0:
        raise ValueError("current_kwh must be a finite nonnegative value")
    coverage = None if weather_coverage is None else float(weather_coverage)
    if coverage is not None and (not isfinite(coverage) or not 0 <= coverage <= 1):
        raise ValueError("weather_coverage must be between 0 and 1")
    selected = {}
    for row in history:
        try:
            day = date.fromisoformat(str(row['date']))
            fc, actual = float(row['forecast_kwh']), float(row['actual_kwh'])
            wc = float(row['weather_coverage'])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (day >= target or not row.get('verified') or not
                all(isfinite(x) for x in (fc, actual, wc)) or
                fc <= 0 or actual < 0 or not 0 <= wc <= 1):
            continue
        selected[day] = (fc, actual, wc)
    # Do not learn clear-weather calibration from no-weather fallback days.
    eligible = [(day, *values) for day, values in sorted(selected.items())
                if values[2] >= 0.8][-21:]
    result = {'status': 'insufficient_evidence', 'target_date': target_date,
              'current_kwh': round(current_kwh, 3), 'candidate_kwh': None,
              'weather_coverage': coverage, 'training_days': len(eligible),
              'multiplier': None, 'production_applied': False,
              'control_permission': False, 'confidence': 'unvalidated'}
    if coverage is None or coverage < 0.8:
        result['status'] = 'weather_evidence_missing'
        return result
    if len(eligible) < 7:
        return result
    # Shrink towards no correction and cap at 15%; avoid ratios exploding on
    # very low-production days, which are scored but not calibration anchors.
    ratios = [min(1.3, max(0.7, actual / fc)) for _, fc, actual, _ in eligible
              if fc >= 3.0 and actual >= 3.0]
    if len(ratios) < 7:
        result['status'] = 'insufficient_nontrivial_days'
        return result
    factor = 1.0 + (median(ratios) - 1.0) * min(0.5, len(ratios) / 42.0)
    factor = min(1.15, max(0.85, factor))
    result.update(status='shadow_only', multiplier=round(factor, 4),
                  candidate_kwh=round(current_kwh * factor, 3))
    return result
