"""Read-only Recorder history to time-weighted PV/irradiance hourly pairs.

No forecast, calibration or control writes. Do not interpret sparse samples as
hourly averages. Recorder state timestamps represent stepwise-held values.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from math import isfinite

UTC = timezone.utc


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
        if unit != expected_unit and not (not unit and fallback_unit == expected_unit):
            continue
        try:
            v = float(state.state)
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


def pair_hourly_history(pv_states, irradiance_states, start, end, *,
                        max_gap_seconds=900, min_coverage=0.8, pv_unit=None, irradiance_unit=None):
    """Align two independently sampled W and W/m² streams by UTC hour."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('timezone-aware boundaries required')
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    finish = end.astimezone(UTC)
    result = []
    while cursor + timedelta(hours=1) <= finish:
        stop = cursor + timedelta(hours=1)
        pv = hourly_weighted_average(pv_states, cursor, stop, expected_unit='W',
                                     max_gap_seconds=max_gap_seconds,
                                     min_coverage=min_coverage, fallback_unit=pv_unit)
        rad = hourly_weighted_average(irradiance_states, cursor, stop, expected_unit='W/m²',
                                      max_gap_seconds=max_gap_seconds,
                                      min_coverage=min_coverage, max_value=1500, fallback_unit=irradiance_unit)
        if pv['mean'] is not None and rad['mean'] is not None:
            result.append({'hour_utc': cursor.isoformat(), 'pv_mean_w': pv['mean'],
                           'irradiance_mean_w_m2': rad['mean'],
                           'pv_coverage': pv['coverage'], 'irradiance_coverage': rad['coverage'],
                           'provenance': 'recorder_time_weighted_state_history',
                           'used_by_production': False})
        cursor = stop
    return result


def diagnose_hourly_history(pv_states, irradiance_states, start, end, *, pv_unit=None, irradiance_unit=None):
    """Explain rejected hours without changing pairing/forecast behavior."""
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    finish = end.astimezone(UTC)
    diagnostics = []
    while cursor + timedelta(hours=1) <= finish:
        stop = cursor + timedelta(hours=1)
        pv = hourly_weighted_average(pv_states, cursor, stop, expected_unit='W', fallback_unit=pv_unit)
        rad = hourly_weighted_average(irradiance_states, cursor, stop,
                                      expected_unit='W/m²', max_value=1500, fallback_unit=irradiance_unit)
        diagnostics.append({
            'hour_utc': cursor.isoformat(),
            'pv_status': pv['status'], 'pv_coverage': pv['coverage'],
            'irradiance_status': rad['status'], 'irradiance_coverage': rad['coverage'],
            'paired': pv['mean'] is not None and rad['mean'] is not None,
        })
        cursor = stop
    return diagnostics


async def read_recorder_hourly_pairs(hass, pv_entity, irradiance_entity, start, end,
                                     *, include_diagnostics=False):
    """Read-only Recorder evidence with optional raw-count and rejection audit."""
    from homeassistant.components.recorder import history, get_instance
    from homeassistant.components.recorder.util import session_scope
    if not pv_entity or not irradiance_entity or pv_entity == irradiance_entity:
        raise ValueError('two distinct configured entity IDs required')
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('aware ordered timestamps required')
    ids = [pv_entity, irradiance_entity]
    def query():
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(
                hass, session, start.astimezone(UTC)-timedelta(minutes=15),
                end.astimezone(UTC), ids, None, False, False, False, True
            )
    data = await get_instance(hass).async_add_executor_job(query)
    # Recorder omits attributes on some history responses. Verify current
    # entity units before inferring units only for attribute-free records.
    pv_live = hass.states.get(pv_entity)
    rad_live = hass.states.get(irradiance_entity)
    pv_unit = str((pv_live.attributes if pv_live else {}).get("unit_of_measurement") or "")
    rad_unit = str((rad_live.attributes if rad_live else {}).get("unit_of_measurement") or "").replace("㎡", "m²")
    if pv_unit != "W" or rad_unit != "W/m²":
        return ({
            "pairs": [], "status": "invalid_current_units",
            "verified_current_units": {"pv": pv_unit or None, "irradiance": rad_unit or None},
            "note": "Cannot infer missing Recorder units without matching live sensor metadata.",
        } if include_diagnostics else [])
    pv_states = (data or {}).get(pv_entity, [])
    rad_states = (data or {}).get(irradiance_entity, [])
    pairs = pair_hourly_history(pv_states, rad_states, start, end, pv_unit=pv_unit, irradiance_unit=rad_unit)
    if not include_diagnostics:
        return pairs
    hours = diagnose_hourly_history(pv_states, rad_states, start, end, pv_unit=pv_unit, irradiance_unit=rad_unit)
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
        'verified_current_units': {'pv': pv_unit, 'irradiance': rad_unit},
        'raw_state_counts': {'pv': len(pv_states), 'irradiance': len(rad_states)},
        'recorded_units': {'pv': unit_counts(pv_states), 'irradiance': unit_counts(rad_states)},
        'hourly_validation': hours,
        'rejected_hours': sum(not h['paired'] for h in hours),
        'note': 'Counts reflect Recorder query output; insufficient coverage may also result from sparse or unchanged sensor states.',
    }
