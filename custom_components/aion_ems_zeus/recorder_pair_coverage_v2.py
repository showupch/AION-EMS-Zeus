"""Isolated prototype for validating matched HA Recorder hourly changes.

No integration wiring. UTC hour alignment handles DST repeated/missing local hours.
Only full completed hours are eligible; partial windows are explicitly partial.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

HOUR = timedelta(hours=1)

def parse_stamp(value):
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        try:
            stamp = datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    else:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None

def paired_hourly_changes(electrical_rows, thermal_rows, start, end):
    start, end = parse_stamp(start), parse_stamp(end)
    if start is None or end is None or end <= start:
        raise ValueError('Timezone-aware start and end required')
    if start.minute or start.second or start.microsecond:
        raise ValueError('Start must align to an hourly Recorder bucket')
    expected = []
    cursor = start
    while cursor + HOUR <= end:
        expected.append(cursor)
        cursor += HOUR
    partial = cursor != end

    def normalize(rows):
        valid, duplicates, rejected = {}, set(), 0
        for row in rows or []:
            if not isinstance(row, dict):
                rejected += 1
                continue
            stamp = parse_stamp(row.get('start'))
            try:
                change = float(row.get('change'))
            except (ValueError, TypeError, OverflowError):
                rejected += 1
                continue
            if stamp is None or stamp.minute or stamp.second or stamp.microsecond or not isfinite(change) or change < 0:
                rejected += 1
                continue
            if stamp in valid:
                duplicates.add(stamp)
            valid[stamp] = change
        for stamp in duplicates:
            valid.pop(stamp, None)
        return valid, duplicates, rejected

    electrical, edups, erejected = normalize(electrical_rows)
    thermal, tdups, trejected = normalize(thermal_rows)
    matched = [stamp for stamp in expected if stamp in electrical and stamp in thermal]
    complete = bool(expected) and len(matched) == len(expected) and not partial
    electrical_kwh = sum(electrical[x] for x in matched)
    thermal_kwh = sum(thermal[x] for x in matched)
    ratio = round(thermal_kwh / electrical_kwh, 3) if complete and electrical_kwh > 0 and thermal_kwh > 0 else None
    return {
        'status': 'complete' if complete else ('partial_window' if partial else 'incomplete'),
        'expected_hours': len(expected), 'matched_hours': len(matched),
        'partial_hour_excluded': partial, 'electrical_kwh': round(electrical_kwh, 4) if complete else None,
        'thermal_kwh': round(thermal_kwh, 4) if complete else None,
        'ratio': ratio, 'scope': 'total_input_energy_ratio_not_verified_spf',
        'duplicate_electrical_hours': len(edups), 'duplicate_thermal_hours': len(tdups),
        'rejected_electrical_rows': erejected, 'rejected_thermal_rows': trejected,
    }


def partial_year_paired_changes(electrical_rows, thermal_rows, start, end, *, minimum_hours=24):
    """Use only a continuous overlap of valid completed UTC hourly buckets.

    Never interpolate missing hours, infer missing energy, or call it a full year.
    """
    start, end = parse_stamp(start), parse_stamp(end)
    if start is None or end is None or end <= start:
        return {'status': 'invalid_window', 'ratio': None, 'reason': 'Invalid Year window'}

    def valid_map(rows):
        values, duplicates = {}, set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            stamp = parse_stamp(row.get('start'))
            try:
                change = float(row.get('change'))
            except (TypeError, ValueError, OverflowError):
                continue
            if stamp is None or stamp.minute or stamp.second or stamp.microsecond or not isfinite(change) or change < 0:
                continue
            if not (start <= stamp and stamp + HOUR <= end):
                continue
            if stamp in values:
                duplicates.add(stamp)
            values[stamp] = change
        for stamp in duplicates:
            values.pop(stamp, None)
        return values

    electrical = valid_map(electrical_rows)
    thermal = valid_map(thermal_rows)
    matched = sorted(electrical.keys() & thermal.keys())
    # Recorder may return zero-filled leading buckets before either meter has
    # actual measured activity. Such buckets cannot establish coverage start.
    first_activity = next(
        (i for i, stamp in enumerate(matched)
         if electrical[stamp] > 0 or thermal[stamp] > 0), None
    )
    if first_activity is not None:
        matched = matched[first_activity:]
    else:
        matched = []
    if len(matched) < minimum_hours:
        return {'status': 'missing_statistics', 'ratio': None, 'reason': 'Insufficient overlapping hourly meter evidence'}
    # A single unobserved hour inside the proposed period invalidates that span.
    if any(b-a != HOUR for a,b in zip(matched, matched[1:])):
        return {'status': 'incomplete', 'ratio': None, 'reason': 'Gaps in overlapping hourly meter evidence'}
    e_kwh = sum(electrical[x] for x in matched)
    t_kwh = sum(thermal[x] for x in matched)
    if e_kwh <= 0 or t_kwh <= 0:
        return {'status': 'insufficient_energy', 'ratio': None, 'reason': 'Positive matched energy required'}
    coverage_start, coverage_end = matched[0], matched[-1] + HOUR
    is_complete = coverage_start == start and coverage_end == end
    return {
        'status': 'complete' if is_complete else 'partial_year',
        'ratio': round(t_kwh/e_kwh, 3),
        'electrical_kwh': round(e_kwh, 4), 'thermal_kwh': round(t_kwh, 4),
        'matched_hours': len(matched),
        'expected_hours': int((end-start).total_seconds()/3600),
        'coverage_start': coverage_start.isoformat(),
        'coverage_end': coverage_end.isoformat(),
        'reason': None if is_complete else 'Partial Year efficiency — based only on overlapping Recorder history',
    }
