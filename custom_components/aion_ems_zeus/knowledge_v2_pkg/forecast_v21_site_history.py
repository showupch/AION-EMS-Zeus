"""Zeus V2.1 independent on-site observation history (shadow-only).

An in-memory bounded collection. No Home Assistant recorder writes, no
production forecast changes, and no assumed missing evidence.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
from typing import Any
from .forecast_v21_site_evidence import inspect_site_evidence


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


class SiteEvidenceWindow:
    """Bounded, deduplicated read-only window of explicitly collected observations.

    This does not persist across restart; persistence must be separately designed
    with provenance, privacy and recorder safeguards before integration.
    """
    def __init__(self, *, capacity: int = 672, min_spacing_seconds: int = 60):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or not 1 <= capacity <= 10080:
            raise ValueError('capacity must be 1..10080')
        if not isinstance(min_spacing_seconds, int) or isinstance(min_spacing_seconds, bool) or min_spacing_seconds < 1:
            raise ValueError('min_spacing_seconds must be positive')
        self._samples: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._spacing = min_spacing_seconds
        self._rejections = {'invalid_timestamp': 0, 'out_of_order_or_duplicate': 0, 'future_timestamp': 0, 'invalid_sample': 0}

    def add(self, observation: dict[str, Any], *, now: datetime | None = None) -> bool:
        if not isinstance(observation, dict) or not isinstance(observation.get('sample'), dict):
            self._rejections['invalid_sample'] += 1
            return False
        sample = observation['sample']
        stamp = _instant(sample.get('timestamp'))
        if stamp is None:
            self._rejections['invalid_timestamp'] += 1
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        if stamp > now.astimezone(timezone.utc):
            self._rejections['future_timestamp'] += 1
            return False
        if self._samples:
            previous = _instant(self._samples[-1]['timestamp'])
            if (stamp - previous).total_seconds() < self._spacing:
                self._rejections['out_of_order_or_duplicate'] += 1
                return False
        # Never keep a reference to a mutable input dictionary.
        copied = dict(sample)
        copied['sensor_timestamps'] = dict(sample.get('sensor_timestamps') or {})
        self._samples.append(copied)
        return True

    def audit(self, *, min_samples: int = 7) -> dict[str, Any]:
        result = inspect_site_evidence(list(self._samples), min_samples=min_samples)
        return {**result, 'buffered_samples': len(self._samples), 'collection_rejections': dict(self._rejections),
                'persistent': False, 'production_applied': False, 'control_permission': False}
