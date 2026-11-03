"""Independent Zeus V2.1 weather-conditioned candidate evaluation (shadow only).

Requires frozen pre-day V2 predictions, matching completed-day production and
verified weather coverage. Site evidence is optional and may veto an adjustment,
never manufacture weather coverage or alter the production forecast.
No imports from other forecast integrations, HA APIs, or write paths.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from math import isfinite
from statistics import median
from typing import Any

MIN_DAYS = 7
MAX_CORRECTION = 0.15
MIN_ACTUAL_KWH = 0.5


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if isfinite(n) else None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _timestamp(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None
    return t.astimezone(timezone.utc) if t.tzinfo else None


def evaluate_shadow_candidate(baseline_kwh: float, target_date: str,
                              historical_days: list[dict[str, Any]], *,
                              target_weather_coverage: float | None,
                              site_audit: dict[str, Any] | None = None,
                              min_days: int = MIN_DAYS) -> dict[str, Any]:
    """Return candidate or explicit refusal; never silently substitute evidence.

    Each history row requires date, forecast_kwh, actual_kwh, weather_coverage,
    and frozen_at (offset-aware ISO timestamp before target day). Optional
    weather_class is a user-independent weather regime ('clear', 'mixed',
    'overcast'); if supplied for target, only matching historical regimes qualify.
    The model never trains on target day or later. This function does not
    interpret on-site DC power as solar production.
    """
    baseline = _number(baseline_kwh)
    target = _date(target_date)
    coverage = _number(target_weather_coverage)
    rejected = {k: 0 for k in ('invalid', 'future_or_target', 'missing_weather',
                                 'low_weather', 'late_snapshot', 'low_actual', 'regime_mismatch')}
    result = {'status': 'insufficient_evidence', 'baseline_v2_kwh': baseline,
              'candidate_kwh': None, 'training_days': 0, 'multiplier': None,
              'rejections': rejected, 'production_applied': False,
              'control_permission': False, 'source': 'frozen_pre_day_comparisons'}
    if baseline is None or baseline < 0 or target is None or coverage is None or not 0 <= coverage <= 1:
        result['status'] = 'invalid_target'
        return result
    if coverage < 0.8:
        result['status'] = 'insufficient_target_weather'
        return result
    if not isinstance(historical_days, list) or not isinstance(min_days, int) or min_days < 1:
        result['status'] = 'invalid_history'
        return result
    if site_audit is not None and not isinstance(site_audit, dict):
        result['status'] = 'invalid_site_audit'
        return result
    # Explicit veto only. Unknown on-site evidence is not assumed good or bad.
    if site_audit and site_audit.get('curtailment_confirmed') is True:
        result['status'] = 'site_curtailment_veto'
        return result
    ratios = []
    seen = set()
    for row in historical_days:
        if not isinstance(row, dict):
            rejected['invalid'] += 1
            continue
        d = _date(row.get('date'))
        pred = _number(row.get('forecast_kwh'))
        actual = _number(row.get('actual_kwh'))
        wc = _number(row.get('weather_coverage'))
        frozen = _timestamp(row.get('frozen_at'))
        if d is None or pred is None or actual is None or pred <= 0 or actual < 0:
            rejected['invalid'] += 1
        elif d >= target or d in seen:
            rejected['future_or_target'] += 1
        elif wc is None or not 0 <= wc <= 1:
            rejected['missing_weather'] += 1
        elif wc < 0.8:
            rejected['low_weather'] += 1
        elif frozen is None or _timestamp(row.get('frozen_at')) is None or datetime.fromisoformat(row['frozen_at'].replace('Z', '+00:00')).date() >= d:
            rejected['late_snapshot'] += 1
        elif actual < MIN_ACTUAL_KWH:
            rejected['low_actual'] += 1
        elif row.get('target_regime') and row.get('weather_regime') != row.get('target_regime'):
            rejected['regime_mismatch'] += 1
        else:
            seen.add(d)
            ratios.append(max(0.5, min(1.5, actual / pred)))
    result['training_days'] = len(ratios)
    if len(ratios) < min_days:
        return result
    # Conservative median correction: robust to occasional poor-weather outliers.
    correction = max(1 - MAX_CORRECTION, min(1 + MAX_CORRECTION, median(ratios)))
    result.update(status='candidate_available', multiplier=round(correction, 5),
                  candidate_kwh=round(baseline * correction, 3))
    return result
