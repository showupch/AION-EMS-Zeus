"""Read-only adapter for Zeus's existing recorder.get_statistics response envelope.
Prototype only: not wired into Home Assistant.
"""
from .recorder_pair_coverage_v2 import paired_hourly_changes


def evaluate_recorder_pair(response, electrical_entity, thermal_entity, start, end):
    if not electrical_entity or not thermal_entity or electrical_entity == thermal_entity:
        return {'status': 'invalid_mapping', 'ratio': None, 'reason': 'Distinct configured meters required'}
    if not isinstance(response, dict):
        return {'status': 'invalid_response', 'ratio': None, 'reason': 'Expected Recorder response mapping'}
    stats = response.get('statistics', response)
    if not isinstance(stats, dict):
        return {'status': 'invalid_response', 'ratio': None, 'reason': 'Missing statistics mapping'}
    if electrical_entity not in stats or thermal_entity not in stats:
        return {'status': 'missing_statistics', 'ratio': None, 'reason': 'One or both statistic IDs missing'}
    electrical, thermal = stats[electrical_entity], stats[thermal_entity]
    if not isinstance(electrical, list) or not isinstance(thermal, list):
        return {'status': 'invalid_response', 'ratio': None, 'reason': 'Expected lists of hourly buckets'}
    result = paired_hourly_changes(electrical, thermal, start, end)
    result['electrical_entity'] = electrical_entity
    result['thermal_entity'] = thermal_entity
    result['source'] = 'recorder_get_statistics_hourly_change'
    return result
