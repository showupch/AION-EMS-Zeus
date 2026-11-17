"""Read-only experimental Recorder reader. NOT connected to Zeus analytics."""
import asyncio
from datetime import datetime, timezone, timedelta
from time import monotonic
from .total_input_evidence import build_total_input_evidence
from .recorder_pair_coverage_v2 import partial_year_paired_changes

class HeatPumpRecorderReader:
    def __init__(self, hass, ttl_seconds=900, chunk_hours=744, max_hours=8784):
        self.hass = hass
        self.ttl_seconds = ttl_seconds
        self.chunk_hours = min(chunk_hours, 744)
        self.max_hours = max_hours
        self._cache = {}
        self._locks = {}

    async def get(self, device, start, end, *, allow_partial_year=False):
        device = device if isinstance(device, dict) else {}
        electrical = str(device.get('total_input_electrical_energy_entity') or '').strip()
        thermal = str(device.get('total_input_thermal_energy_entity') or '').strip()
        if not electrical or not thermal or electrical == thermal:
            return build_total_input_evidence(device, {}, start, end)
        if not isinstance(start, datetime) or not isinstance(end, datetime) or start.tzinfo is None or end.tzinfo is None or end <= start:
            return {'status':'invalid_window','value':None,'reason':'Timezone-aware positive window required'}
        start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        if any((x.minute, x.second, x.microsecond) != (0,0,0) for x in (start_utc,end_utc)):
            return {'status':'partial_window','value':None,'reason':'Complete hourly boundaries required'}
        hours = (end_utc-start_utc).total_seconds()/3600
        if hours > self.max_hours:
            return {'status':'window_too_large','value':None,'reason':'Period exceeds configured maximum'}
        key = (electrical, thermal, start_utc, end_utc, bool(allow_partial_year))
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                cached = self._cache.get(key)
                if cached and monotonic()-cached[0] < self.ttl_seconds:
                    return dict(cached[1], cache='hit')
                combined = {electrical: [], thermal: []}
                cursor = start_utc
                try:
                    while cursor < end_utc:
                        chunk_end = min(cursor+timedelta(hours=self.chunk_hours),end_utc)
                        response = await self.hass.services.async_call('recorder','get_statistics', {
                            'statistic_ids':[electrical,thermal],
                            'start_time':cursor,'end_time':chunk_end,
                            'period':'hour','types':['change'],'units':{'energy':'kWh'},
                        }, blocking=True,return_response=True)
                        if not isinstance(response,dict):
                            return {'status':'invalid_response','value':None,'reason':'Recorder response is not a mapping'}
                        stats = response.get('statistics',response)
                        if not isinstance(stats,dict) or any(not isinstance(stats.get(entity, [] if allow_partial_year else None),list) for entity in (electrical,thermal)):
                            return {'status':'missing_statistics','value':None,'reason':'Missing hourly meter evidence'}
                        for entity in (electrical,thermal):
                            combined[entity].extend(stats.get(entity, []))
                        cursor = chunk_end
                    result = build_total_input_evidence(device,{'statistics':combined},start_utc,end_utc)
                    if allow_partial_year:
                        partial = partial_year_paired_changes(combined[electrical], combined[thermal], start_utc, end_utc)
                        if partial.get('ratio') is not None:
                            result = {
                                'status': partial['status'], 'value': partial['ratio'],
                                'scope': 'total_input_energy_ratio_not_verified_spf',
                                'electrical_entity': electrical, 'thermal_entity': thermal,
                                **{k: partial.get(k) for k in ('electrical_kwh', 'thermal_kwh', 'expected_hours', 'matched_hours', 'coverage_start', 'coverage_end', 'reason')},
                                'source': 'recorder_get_statistics_hourly_change',
                            }
                        elif result.get('status') in {'missing_statistics', 'incomplete'}:
                            result = {**result, 'status': partial['status'], 'reason': partial.get('reason')}
                except Exception as exc:
                    return {'status':'recorder_error','value':None,'reason':type(exc).__name__}
                # Do not cache failures; allow Recorder recovery on next refresh.
                if result.get('value') is not None:
                    self._cache[key] = (monotonic(),result)
                    if len(self._cache)>32:
                        self._cache = {key:self._cache[key]}
                return dict(result,cache='miss')
        finally:
            if not lock.locked() and self._locks.get(key) is lock:
                self._locks.pop(key,None)
