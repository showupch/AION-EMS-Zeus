"""Read-only Recorder history to time-weighted PV/irradiance hourly pairs.

No forecast, calibration or control writes. Do not interpret sparse samples as
hourly averages. Recorder state timestamps represent stepwise-held values.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from math import isfinite
from bisect import bisect_left, bisect_right
from functools import partial

UTC = timezone.utc


def _unit_scale(unit, expected_unit):
    """Return a safe scale to canonical units; reject unknown dimensions."""
    unit = str(unit or '').strip().replace('㎡', 'm²').replace('²', '2').replace(' ', '')
    if expected_unit == 'W':
        return {'W': 1.0, 'kW': 1000.0}.get(unit)
    if expected_unit == 'W/m²':
        return {'W/m2': 1.0, 'kW/m2': 1000.0}.get(unit)
    return 1.0 if unit == expected_unit else None


def hourly_weighted_average(states, start, end, *, expected_unit, max_gap_seconds=900,
                            min_coverage=0.8, max_value=None, fallback_unit=None):
    """Time-weighted mean with strict maximum inter-observation gap.

    Each interval is supported only until the next observed timestamp and only
    when its length is <= max_gap_seconds. No interpolation across outages.
    A leading state from before start may be used if its age <= max_gap_seconds.
    """
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('aware ordered timestamps required')
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    readings = []
    for state in states:
        t = getattr(state, 'last_updated', None) or getattr(state, 'last_changed', None)
        if t is None or t.tzinfo is None:
            continue
        unit = str((getattr(state, 'attributes', {}) or {}).get('unit_of_measurement') or '')
        # Recorder can omit attributes. Only inherit units from the verified
        # live entity when a historical record has no unit metadata.
        scale = _unit_scale(unit or fallback_unit, expected_unit)
        if scale is None:
            continue
        try:
            v = float(state.state) * scale
        except (TypeError, ValueError):
            continue
        if not isfinite(v) or v < 0 or (max_value is not None and v > max_value):
            continue
        readings.append((t.astimezone(UTC), v))
    readings.sort()
    # Only use a pre-window reading if fresh enough to cover the start.
    previous = None
    in_window = []
    for t, v in readings:
        if t < start:
            previous = (t, v)
        elif t <= end:
            in_window.append((t, v))
    if previous is not None and (start - previous[0]).total_seconds() <= max_gap_seconds:
        in_window.insert(0, previous)
    if in_window and in_window[-1][0] < end and (end-in_window[-1][0]).total_seconds() <= max_gap_seconds:
        in_window.append((end, in_window[-1][1]))
    if len(in_window) < 2:
        return {'status': 'insufficient_coverage', 'mean': None, 'coverage': 0.0}
    weighted = 0.0
    covered = 0.0
    for (t0, v0), (t1, _) in zip(in_window, in_window[1:]):
        a, b = max(start, t0), min(end, t1)
        if b <= a or (t1-t0).total_seconds() > max_gap_seconds:
            continue
        seconds = (b-a).total_seconds()
        weighted += v0 * seconds
        covered += seconds
    duration = (end-start).total_seconds()
    coverage = covered/duration
    if coverage < min_coverage:
        return {'status': 'insufficient_coverage', 'mean': None, 'coverage': round(coverage, 4)}
    return {'status': 'verified_hourly_average', 'mean': round(weighted/covered, 3),
            'coverage': round(coverage, 4)}


def _hourly_from_prepared(readings, timestamps, start, end, *, max_gap_seconds=900,
                          min_coverage=0.8):
    """Compute an hour from an indexed slice; never scan full Recorder history."""
    left = bisect_left(timestamps, start)
    right = bisect_right(timestamps, end)
    subset = readings[max(0, left - 1):right]
    if not subset:
        return {'status': 'insufficient_coverage', 'mean': None, 'coverage': 0.0}
    # Preserve the original leading/terminal state and strict gap rules.
    if subset[0][0] < start and (start-subset[0][0]).total_seconds() > max_gap_seconds:
        subset = subset[1:]
    if subset and subset[-1][0] < end and (end-subset[-1][0]).total_seconds() <= max_gap_seconds:
        subset = subset + [(end, subset[-1][1])]
    weighted = covered = 0.0
    for (t0, v0), (t1, _) in zip(subset, subset[1:]):
        a, b = max(start, t0), min(end, t1)
        if b <= a or (t1-t0).total_seconds() > max_gap_seconds:
            continue
        seconds = (b-a).total_seconds()
        weighted += v0 * seconds
        covered += seconds
    coverage = covered / (end-start).total_seconds()
    if coverage < min_coverage:
        return {'status': 'insufficient_coverage', 'mean': None, 'coverage': round(coverage, 4)}
    return {'status': 'verified_hourly_average', 'mean': round(weighted/covered, 3),
            'coverage': round(coverage, 4)}


def _prepare_readings(states, expected_unit, *, max_value=None, fallback_unit=None):
    """Normalize and sort once rather than once per hour."""
    result = []
    for state in states:
        stamp = getattr(state, 'last_updated', None) or getattr(state, 'last_changed', None)
        if stamp is None or stamp.tzinfo is None:
            continue
        unit = (getattr(state, 'attributes', {}) or {}).get('unit_of_measurement')
        scale = _unit_scale(unit or fallback_unit, expected_unit)
        if scale is None:
            continue
        try:
            value = float(state.state) * scale
        except (TypeError, ValueError):
            continue
        if not isfinite(value) or value < 0 or (max_value is not None and value > max_value):
            continue
        result.append((stamp.astimezone(UTC), value))
    result.sort()
    return result, [stamp for stamp, _ in result]


def _indexed_hourly_results(pv_states, irradiance_states, start, end, *,
                            pv_unit=None, irradiance_unit=None):
    pv, pv_times = _prepare_readings(pv_states, 'W', fallback_unit=pv_unit)
    rad, rad_times = _prepare_readings(irradiance_states, 'W/m²', max_value=1500,
                                      fallback_unit=irradiance_unit)
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    finish = end.astimezone(UTC)
    pairs, diagnostics = [], []
    while cursor + timedelta(hours=1) <= finish:
        stop = cursor + timedelta(hours=1)
        p = _hourly_from_prepared(pv, pv_times, cursor, stop)
        r = _hourly_from_prepared(rad, rad_times, cursor, stop)
        paired = p['mean'] is not None and r['mean'] is not None
        diagnostics.append({'hour_utc': cursor.isoformat(), 'pv_status': p['status'],
                            'pv_coverage': p['coverage'], 'irradiance_status': r['status'],
                            'irradiance_coverage': r['coverage'], 'paired': paired})
        if paired:
            pairs.append({'hour_utc': cursor.isoformat(), 'pv_mean_w': p['mean'],
                          'irradiance_mean_w_m2': r['mean'], 'pv_coverage': p['coverage'],
                          'irradiance_coverage': r['coverage'],
                          'provenance': 'recorder_time_weighted_state_history',
                          'used_by_production': False})
        cursor = stop
    return pairs, diagnostics


def pair_hourly_history(pv_states, irradiance_states, start, end, *,
                        max_gap_seconds=900, min_coverage=0.8, pv_unit=None, irradiance_unit=None):
    # Keep the public API and custom thresholds unchanged.
    if max_gap_seconds != 900 or min_coverage != 0.8:
        return _legacy_pair_hourly_history(pv_states, irradiance_states, start, end,
                                           max_gap_seconds=max_gap_seconds,
                                           min_coverage=min_coverage, pv_unit=pv_unit,
                                           irradiance_unit=irradiance_unit)
    return _indexed_hourly_results(pv_states, irradiance_states, start, end,
                                   pv_unit=pv_unit, irradiance_unit=irradiance_unit)[0]


def _legacy_pair_hourly_history(pv_states, irradiance_states, start, end, *,
                                max_gap_seconds, min_coverage, pv_unit, irradiance_unit):
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    finish = end.astimezone(UTC)
    result = []
    while cursor + timedelta(hours=1) <= finish:
        stop = cursor + timedelta(hours=1)
        pv = hourly_weighted_average(pv_states, cursor, stop, expected_unit='W',
                                     max_gap_seconds=max_gap_seconds, min_coverage=min_coverage,
                                     fallback_unit=pv_unit)
        rad = hourly_weighted_average(irradiance_states, cursor, stop, expected_unit='W/m²',
                                      max_gap_seconds=max_gap_seconds, min_coverage=min_coverage,
                                      max_value=1500, fallback_unit=irradiance_unit)
        if pv['mean'] is not None and rad['mean'] is not None:
            result.append({'hour_utc': cursor.isoformat(), 'pv_mean_w': pv['mean'],
                           'irradiance_mean_w_m2': rad['mean'],
                           'pv_coverage': pv['coverage'], 'irradiance_coverage': rad['coverage'],
                           'provenance': 'recorder_time_weighted_state_history',
                           'used_by_production': False})
        cursor = stop
    return result


def diagnose_hourly_history(pv_states, irradiance_states, start, end, *, pv_unit=None, irradiance_unit=None):
    return _indexed_hourly_results(pv_states, irradiance_states, start, end,
                                   pv_unit=pv_unit, irradiance_unit=irradiance_unit)[1]


async def read_recorder_hourly_pairs(hass, pv_entity, irradiance_entity, start, end,
                                     *, include_diagnostics=False):
    """Read-only Recorder evidence with optional raw-count and rejection audit."""
    from .recorder_audit_bounded import collect, AuditLimit
    if not pv_entity or not irradiance_entity or pv_entity == irradiance_entity:
        raise ValueError('two distinct configured entity IDs required')
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('aware ordered timestamps required')
    # All SQL and raw-row decoding run in the Recorder executor. No database
    # session or heavy history processing is created on the HA event loop.
    from homeassistant.components.recorder import get_instance
    data, _, row_count, elapsed = await get_instance(hass).async_add_executor_job(
        partial(collect, hass, [pv_entity, irradiance_entity], start, end))
    pv_live = hass.states.get(pv_entity)
    rad_live = hass.states.get(irradiance_entity)
    pv_unit = str((pv_live.attributes if pv_live else {}).get('unit_of_measurement') or '')
    rad_unit = str((rad_live.attributes if rad_live else {}).get('unit_of_measurement') or '')
    pv_states = data.get(pv_entity, [])
    rad_states = data.get(irradiance_entity, [])

    def resolve_unit(live_unit, states, canonical):
        # Only explicit unit metadata is evidence. A single consistent
        # historical unit can replace a missing live unit. Do not guess from
        # values, sensor names, or a chart's display formatting.
        explicit = {str((getattr(st, 'attributes', {}) or {}).get('unit_of_measurement') or '').strip()
                    for st in states}
        explicit.discard('')
        invalid = sorted(u for u in explicit if _unit_scale(u, canonical) is None)
        scales = {_unit_scale(u, canonical) for u in explicit if _unit_scale(u, canonical) is not None}
        live_scale = _unit_scale(live_unit, canonical)
        if invalid or len(scales) > 1 or (live_scale is not None and scales and live_scale not in scales):
            return None, 'conflicting_or_unsupported_recorded_units', sorted(explicit)
        if live_scale is not None:
            return live_unit, 'live_verified', sorted(explicit)
        if explicit and len(scales) == 1:
            return sorted(explicit)[0], 'recorder_verified', sorted(explicit)
        return None, 'unit_not_verifiable', sorted(explicit)

    pv_verified, pv_source, pv_explicit = resolve_unit(pv_unit, pv_states, 'W')
    rad_verified, rad_source, rad_explicit = resolve_unit(rad_unit, rad_states, 'W/m²')
    if pv_verified is None or rad_verified is None:
        return ({
            "pairs": [], "status": "invalid_current_units",
            "verified_current_units": {"pv": pv_unit or None, "irradiance": rad_unit or None},
            "unit_verification": {"pv": pv_source, "irradiance": rad_source},
            "recorder_explicit_units": {"pv": pv_explicit, "irradiance": rad_explicit},
            "raw_state_counts": {"pv": len(pv_states), "irradiance": len(rad_states)},
            "note": "Cannot safely establish both units from live or explicit Recorder metadata; no dimension guessing.",
        } if include_diagnostics else [])
    pv_unit, rad_unit = pv_verified, rad_verified
    # The query is still potentially expensive. The V3 audit remains disabled
    # in sensor.py until bounded Recorder retrieval is independently validated.
    pairs, hours = await hass.async_add_executor_job(partial(
        _indexed_hourly_results, pv_states, rad_states, start, end,
        pv_unit=pv_unit, irradiance_unit=rad_unit))
    if not include_diagnostics:
        return pairs
    def unit_counts(states):
        counts = {}
        for state in states:
            unit = str((getattr(state, 'attributes', {}) or {}).get('unit_of_measurement') or '(missing)')
            counts[unit] = counts.get(unit, 0) + 1
        return counts
    # Counts by UTC day use the returned state timestamps, independently of
    # pairing validation, so missing data and rejected data can be distinguished.
    def daily_raw_counts(states):
        counts = {}
        for state in states:
            stamp = getattr(state, 'last_updated', None) or getattr(state, 'last_changed', None)
            if stamp is None or stamp.tzinfo is None:
                continue
            day = stamp.astimezone(UTC).date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        return counts
    pv_daily = daily_raw_counts(pv_states)
    rad_daily = daily_raw_counts(rad_states)
    hourly_by_day = {}
    for hour in hours:
        day = hour['hour_utc'][:10]
        entry = hourly_by_day.setdefault(day, {
            'accepted_pairs': 0, 'pv_rejection_reasons': {},
            'irradiance_rejection_reasons': {},
        })
        if hour['paired']:
            entry['accepted_pairs'] += 1
        for prefix, key in (('pv', 'pv_rejection_reasons'),
                            ('irradiance', 'irradiance_rejection_reasons')):
            status = hour[prefix + '_status']
            if status != 'verified_hourly_average':
                reasons = entry[key]
                reasons[status] = reasons.get(status, 0) + 1
    daily_audit = [
        {'date_utc': day, 'pv_raw_records': pv_daily.get(day, 0),
         'irradiance_raw_records': rad_daily.get(day, 0),
         **hourly_by_day.get(day, {'accepted_pairs': 0,
             'pv_rejection_reasons': {}, 'irradiance_rejection_reasons': {}})}
        for day in sorted(set(pv_daily) | set(rad_daily) | set(hourly_by_day))
    ]
    return {
        'daily_audit': daily_audit,
        'first_last_raw_utc': {
            'pv': {'first': min((str((getattr(st, 'last_updated', None) or getattr(st, 'last_changed', None))) for st in pv_states), default=None),
                   'last': max((str((getattr(st, 'last_updated', None) or getattr(st, 'last_changed', None))) for st in pv_states), default=None)},
            'irradiance': {'first': min((str((getattr(st, 'last_updated', None) or getattr(st, 'last_changed', None))) for st in rad_states), default=None),
                           'last': max((str((getattr(st, 'last_updated', None) or getattr(st, 'last_changed', None))) for st in rad_states), default=None)},
        },
        'pairs': pairs,
        'verified_current_units': {'pv': str((pv_live.attributes if pv_live else {}).get('unit_of_measurement') or '') or None, 'irradiance': str((rad_live.attributes if rad_live else {}).get('unit_of_measurement') or '') or None},
        'effective_units': {'pv': pv_unit, 'irradiance': rad_unit},
        'unit_verification': {'pv': pv_source, 'irradiance': rad_source},
        'normalized_output_units': {'pv': 'W', 'irradiance': 'W/m²'},
        'raw_state_counts': {'pv': len(pv_states), 'irradiance': len(rad_states)},
        'recorded_units': {'pv': unit_counts(pv_states), 'irradiance': unit_counts(rad_states)},
        'hourly_validation': hours,
        'rejected_hours': sum(not h['paired'] for h in hours),
        'note': 'Counts reflect Recorder query output; insufficient coverage may also result from sparse or unchanged sensor states.',
    }
