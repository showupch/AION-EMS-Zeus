"""Zeus V2.1 read-only opt-in Home Assistant sampling lifecycle.

Not registered by default. Caller must provide verified entity mappings and
explicit curtailment state. Does not read Recorder, persist, publish entities,
call HA services or change V2. The host owns start/stop lifecycle.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping
from .forecast_v21_site_reader import collect_site_observation, MEASUREMENT_KEYS
from .forecast_v21_site_history import SiteEvidenceWindow

class SiteSamplingSession:
    def __init__(self, hass: Any, mappings: Mapping[str, str], *,
                 pv_dc_semantics: str = 'unknown',
                 capacity: int = 672, interval_seconds: int = 300,
                 max_age_seconds: float = 60):
        if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool) or interval_seconds < 60:
            raise ValueError('interval_seconds must be >= 60')
        if not isfinite(max_age_seconds) or not 0 <= max_age_seconds <= interval_seconds:
            raise ValueError('invalid max_age_seconds')
        if pv_dc_semantics not in ('unknown', 'pv_only', 'combined'):
            raise ValueError('invalid pv_dc_semantics')
        if not isinstance(mappings, Mapping):
            raise ValueError('explicit mapping required')
        self.hass = hass
        self.mappings = {key: value for key, value in mappings.items()
                         if key in MEASUREMENT_KEYS and isinstance(value, str) and value.startswith('sensor.')}
        self.pv_dc_semantics = pv_dc_semantics
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self.window = SiteEvidenceWindow(capacity=capacity, min_spacing_seconds=interval_seconds)
        self.last_rejected = {}
        self.last_sample_at = None
        self._unsub = None

    def sample(self, *, curtailed: bool | None = None, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        observation = collect_site_observation(
            self.hass, self.mappings, pv_dc_semantics=self.pv_dc_semantics,
            curtailed=curtailed, now=now, max_age_seconds=self.max_age_seconds)
        self.last_rejected = dict(observation['rejected'])
        self.last_sample_at = now.isoformat()
        accepted = self.window.add(observation, now=now)
        return {'accepted': accepted, 'rejected': dict(self.last_rejected),
                'qualified_now': any(v.get('qualified') for k,v in observation['evidence'].items()
                                     if isinstance(v, dict)),
                'production_applied': False, 'control_permission': False}

    def start(self, async_track_time_interval, *, curtailment_provider=None):
        """Opt-in scheduler; HA's event helper is injected by the caller.

        A provider must return True/False/None; absent provider means unknown
        and cannot produce calibration-qualified evidence.
        """
        if self._unsub is not None:
            raise RuntimeError('sampling already started')
        from datetime import timedelta
        def tick(_now):
            state = curtailment_provider() if curtailment_provider else None
            if state is not None and type(state) is not bool:
                state = None
            self.sample(curtailed=state)
        self._unsub = async_track_time_interval(self.hass, tick, timedelta(seconds=self.interval_seconds))
        return self.stop

    def stop(self):
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def audit(self):
        return {**self.window.audit(), 'last_sample_at': self.last_sample_at,
                'last_rejected': dict(self.last_rejected), 'mapped_keys': sorted(self.mappings),
                'scheduler_active': self._unsub is not None, 'persistent': False,
                'production_applied': False, 'control_permission': False}
