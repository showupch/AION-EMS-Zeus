"""Zeus-owned, read-only bridge from PredictionAccuracy snapshots to V2.1 candidates.

Never mutates records, never guesses missing weather, and never writes a forecast.
Caller supplies Home Assistant's already-configured tzinfo for DST-aware validation.
"""
from __future__ import annotations
from datetime import datetime, date, time, timezone, tzinfo
from math import isfinite
from typing import Any
from .forecast_v21_candidate import evaluate_shadow_candidate


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if isfinite(n) else None


def bridge_v21_history(samples: list[dict[str, Any]], target_date: str,
                       local_timezone: tzinfo) -> dict[str, Any]:
    """Map stored frozen snapshots to strict candidate input, preserving provenance.

    Do not repair invalid timestamps, interpolate weather or infer DC semantics.
    """
    tz = local_timezone
    target = date.fromisoformat(target_date)
    rows = []
    audit = {k: 0 for k in ('received', 'accepted', 'missing_weather', 'missing_energy',
                             'invalid_date', 'invalid_timestamp', 'not_pre_day',
                             'future_or_target', 'duplicates')}
    seen = set()
    for sample in samples:
        audit['received'] += 1
        if not isinstance(sample, dict):
            audit['invalid_date'] += 1
            continue
        try:
            day = date.fromisoformat(str(sample.get('target_date')))
        except ValueError:
            audit['invalid_date'] += 1
            continue
        if day >= target:
            audit['future_or_target'] += 1
            continue
        if day in seen:
            audit['duplicates'] += 1
            continue
        predicted = sample.get('predicted') or {}
        actual = sample.get('actual') or {}
        fc = _numeric(predicted.get('solar_kwh')) if isinstance(predicted, dict) else None
        produced = _numeric(actual.get('solar_kwh')) if isinstance(actual, dict) else None
        if fc is None or fc <= 0 or produced is None or produced < 0:
            audit['missing_energy'] += 1
            continue
        coverage = _numeric(sample.get('weather_coverage'))
        if coverage is None or not 0 <= coverage <= 1:
            audit['missing_weather'] += 1
            continue
        try:
            frozen = datetime.fromisoformat(str(sample.get('forecast_created_at')).replace('Z', '+00:00'))
        except ValueError:
            audit['invalid_timestamp'] += 1
            continue
        if frozen.tzinfo is None or frozen.utcoffset() is None:
            audit['invalid_timestamp'] += 1
            continue
        start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
        if frozen.astimezone(timezone.utc) >= start:
            audit['not_pre_day'] += 1
            continue
        seen.add(day)
        rows.append({'date': day.isoformat(), 'forecast_kwh': fc,
                     'actual_kwh': produced, 'weather_coverage': coverage,
                     'frozen_at': frozen.astimezone(tz).isoformat()})
        audit['accepted'] += 1
    return {'history': rows, 'audit': audit}


def compare_shadow(baseline_kwh: float, target_date: str, weather_coverage: float | None,
                   samples: list[dict[str, Any]], local_timezone: tzinfo,
                   site_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a candidate for diagnostics only; never apply it to V2."""
    try:
        bridged = bridge_v21_history(samples, target_date, local_timezone)
    except (ValueError, TypeError):
        return {'status': 'invalid_history_context', 'baseline_v2_kwh': baseline_kwh,
                'candidate_kwh': None, 'training_days': 0, 'multiplier': None,
                'rejections': {}, 'bridge_audit': {}, 'production_applied': False,
                'control_permission': False}
    candidate = evaluate_shadow_candidate(baseline_kwh, target_date, bridged['history'],
                                          target_weather_coverage=weather_coverage,
                                          site_audit=site_audit)
    return {'status': candidate['status'], 'baseline_v2_kwh': candidate['baseline_v2_kwh'],
            'candidate_kwh': candidate['candidate_kwh'],
            'training_days': candidate['training_days'],
            'multiplier': candidate['multiplier'],
            'rejections': candidate['rejections'],
            'bridge_audit': bridged['audit'],
            'production_applied': False, 'control_permission': False}
