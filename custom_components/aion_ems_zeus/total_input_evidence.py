"""Read-only total-input efficiency evidence builder for Zeus heat pumps.

Integration candidate, not connected to Recorder or the device editor.
"""
from .recorder_response_adapter import evaluate_recorder_pair


def build_total_input_evidence(device, recorder_response, start, end):
    if not isinstance(device, dict):
        return {'status': 'invalid_mapping', 'value': None, 'reason': 'Missing heat-pump device configuration'}
    electrical = str(device.get('total_input_electrical_energy_entity') or '').strip()
    thermal = str(device.get('total_input_thermal_energy_entity') or '').strip()
    if not electrical or not thermal or electrical == thermal:
        return {'status': 'invalid_mapping', 'value': None, 'reason': 'Two distinct total-energy meters must be configured'}
    result = evaluate_recorder_pair(recorder_response, electrical, thermal, start, end)
    return {
        'status': result['status'],
        'value': result.get('ratio'),
        'scope': 'total_input_energy_ratio_not_verified_spf',
        'electrical_entity': electrical,
        'thermal_entity': thermal,
        'electrical_kwh': result.get('electrical_kwh'),
        'thermal_kwh': result.get('thermal_kwh'),
        'expected_hours': result.get('expected_hours'),
        'matched_hours': result.get('matched_hours'),
        'reason': result.get('reason'),
        'source': result.get('source'),
    }
