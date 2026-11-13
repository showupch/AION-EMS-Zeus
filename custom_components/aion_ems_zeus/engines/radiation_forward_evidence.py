"""Forward-only, immutable radiation shadow captures and measured-day scoring.

Never writes to production forecast or control state. Uses HA Store (private .storage).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, time
from math import isfinite
from zoneinfo import ZoneInfo

from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_change
from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.util import session_scope
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_DOMAIN = 'aion_ems_zeus'
_KEY = 'radiation_forward_evidence'


def _store(hass):
    return Store(hass, 1, 'aion_ems_zeus.radiation_forward_evidence')


async def _read(hass):
    data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return {'schema': 1, 'frozen': {}, 'scores': {}}
    return {'schema': 1, 'frozen': dict(data.get('frozen') or {}), 'scores': dict(data.get('scores') or {})}


def _valid_candidate(result, date):
    if result.get('status') != 'experimental_available' or result.get('date') != date:
        return False
    try:
        val = float(result['candidate_kwh'])
        hours = result['hours']
        return isfinite(val) and 0 <= val <= 200 and len(hours) == 24
    except (TypeError, ValueError, KeyError):
        return False


async def _capture(hass, compute, now=None):
    now = now or dt_util.now()
    date = (now.date() + timedelta(days=1)).isoformat()
    data = await _read(hass)
    if date in data['frozen']:
        return 'already_frozen'
    result = await compute(hass, target_date=date)
    if not _valid_candidate(result, date):
        return 'insufficient_evidence'
    data['frozen'][date] = {
        'target_date': date,
        'captured_at': now.isoformat(),
        'candidate_kwh': float(result['candidate_kwh']),
        'training_days': result.get('training_days'),
        'training_buckets': result.get('training_buckets'),
        'source_irradiance': result.get('source_irradiance'),
        'source_pv': result.get('source_pv'),
        'hours': result['hours'],
        'production_applied': False,
        'control_permission': False,
    }
    await _store(hass).async_save(data)
    return 'frozen'


async def _actual_from_recorder(hass, entity_id, date, tz):
    """Use only completed-day *daily energy* values, never live power or total lifetime.

    A valid daily energy sensor must be configured in Zeus. Its last valid
    pre-midnight state is used; reset-time samples from the next day are excluded.
    """
    from datetime import date as date_type
    day = date_type.fromisoformat(date)
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    start_utc, end_utc = start.astimezone(ZoneInfo('UTC')), end.astimezone(ZoneInfo('UTC'))

    def query():
        with session_scope(hass=hass, read_only=True) as session:
            return history.get_significant_states_with_session(
                hass, session, start_utc, end_utc, [entity_id], None,
                False, False, False, True)

    states = (await get_instance(hass).async_add_executor_job(query) or {}).get(entity_id, [])
    values = []
    for state in states:
        stamp = getattr(state, 'last_updated', None) or getattr(state, 'last_changed', None)
        if stamp is None or not start_utc <= dt_util.as_utc(stamp) < end_utc:
            continue
        try:
            val = float(state.state)
            unit = state.attributes.get('unit_of_measurement')
            if unit == 'Wh':
                val /= 1000
            elif unit != 'kWh':
                continue
            if isfinite(val) and 0 <= val <= 200:
                values.append((dt_util.as_utc(stamp), val))
        except (ValueError, TypeError, OverflowError):
            continue
    # Prevent scoring from incomplete days / sparse Recorder history.
    if not values or (end_utc - max(t for t, _ in values)).total_seconds() > 3600:
        return None
    return max(values, key=lambda v: v[0])[1]


async def _score(hass, now=None):
    now = now or dt_util.now()
    day = (now.date() - timedelta(days=1)).isoformat()
    data = await _read(hass)
    frozen = data['frozen'].get(day)
    if not frozen or day in data['scores']:
        return 'no_unscored_capture'
    core = hass.data.get(_DOMAIN, {}).get('core')
    if core is None:
        return 'not_ready'
    mapped = core.energy_mapping.mappings
    entity_id = str(mapped.get('solar_energy_today') or '').strip()
    if not entity_id:
        return 'no_daily_energy_mapping'
    actual = await _actual_from_recorder(hass, entity_id, day, hass.config.time_zone)
    if actual is None:
        return 'missing_completed_day_actual'
    predicted = frozen['candidate_kwh']
    data['scores'][day] = {
        'target_date': day, 'actual_kwh': round(actual, 3),
        'frozen_candidate_kwh': predicted,
        'signed_error_kwh': round(predicted - actual, 3),
        'absolute_error_kwh': round(abs(predicted - actual), 3),
        'scored_at': now.isoformat(), 'actual_entity_id': entity_id,
    }
    await _store(hass).async_save(data)
    return 'scored'


async def async_start_radiation_evidence(hass, entry, compute):
    """Schedule one daily capture and one completed-day score; never backfill forecasts."""
    key = f'{_KEY}_{entry.entry_id}'
    if key in hass.data.setdefault(_DOMAIN, {}):
        return

    async def capture(_now):
        try:
            status = await _capture(hass, compute)
            _LOGGER.debug('Radiation forward capture: %s', status)
        except Exception:
            _LOGGER.exception('Radiation forward capture failed')

    async def score(_now):
        try:
            status = await _score(hass)
            _LOGGER.debug('Radiation forward score: %s', status)
        except Exception:
            _LOGGER.exception('Radiation forward score failed')

    unsub_capture = async_track_time_change(hass, capture, hour=23, minute=0, second=0)
    unsub_score = async_track_time_change(hass, score, hour=0, minute=10, second=0)
    hass.data[_DOMAIN][key] = (unsub_capture, unsub_score)


async def async_stop_radiation_evidence(hass, entry):
    key = f'{_KEY}_{entry.entry_id}'
    callbacks = hass.data.get(_DOMAIN, {}).pop(key, ())
    for unsubscribe in callbacks:
        unsubscribe()


async def async_read_radiation_evidence(hass):
    data = await _read(hass)
    scores = list(data['scores'].values())
    mae = round(sum(x['absolute_error_kwh'] for x in scores) / len(scores), 3) if scores else None
    return {'status': 'forward_comparison' if scores else 'awaiting_completed_days',
            'frozen_days': len(data['frozen']), 'matched_days': len(scores),
            'mean_absolute_error_kwh': mae,
            'frozen': data['frozen'], 'scores': data['scores'],
            'production_applied': False, 'control_permission': False}
