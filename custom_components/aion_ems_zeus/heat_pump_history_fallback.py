"""Conservative hourly power means from Home Assistant Recorder state history.

Only use when long-term power statistics are absent. Never extrapolate across
missing history, unknown states, or long gaps between observations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

MAX_GAP = timedelta(minutes=20)


def hourly_means(states, start, end, unit):
    """Return Recorder-style points for fully observed UTC hours.

    States must include one observation at/before the requested interval start.
    A step-held value is only trusted for <=20 minutes; each hour needs at
    least 95% valid coverage. No negative power is accepted here because the
    seven-source model uses nonnegative import/export/charge/discharge channels.
    """
    if unit not in ('W', 'kW'):
        return []
    factor = 1000.0 if unit == 'kW' else 1.0
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start.minute or start.second or start.microsecond:
        raise ValueError('start must be an exact UTC hour')
    samples = []
    for state in states:
        stamp = getattr(state, 'last_changed', None) or getattr(state, 'last_updated', None)
        if stamp is None:
            continue
        stamp = stamp.astimezone(timezone.utc)
        try:
            value = float(state.state) * factor
        except (ValueError, TypeError):
            value = None
        if value is not None and (not isfinite(value) or value < -0.01):
            value = None
        samples.append((stamp, None if value is None else max(0.0, value)))
    samples.sort(key=lambda item: item[0])
    if not samples:
        return []
    points = []
    cursor = 0
    t = start
    while t + timedelta(hours=1) <= end:
        hour_end = t + timedelta(hours=1)
        while cursor + 1 < len(samples) and samples[cursor + 1][0] <= t:
            cursor += 1
        if samples[cursor][0] > t:
            t = hour_end
            continue
        weighted = covered = 0.0
        index = cursor
        while index < len(samples) and samples[index][0] < hour_end:
            stamp, value = samples[index]
            interval_start = max(t, stamp)
            next_stamp = samples[index + 1][0] if index + 1 < len(samples) else hour_end
            interval_end = min(hour_end, next_stamp, stamp + MAX_GAP)
            if value is not None and interval_end > interval_start:
                seconds = (interval_end - interval_start).total_seconds()
                weighted += value * seconds
                covered += seconds
            index += 1
        if covered >= 0.95 * 3600:
            points.append({'start': t.isoformat(), 'mean': weighted / covered})
        t = hour_end
    return points
