"""Evidence-only regression tests for stale solar Energy dashboard sources."""
import importlib.util
from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'knowledge_v2_pkg' / 'hourly_source_evidence.py'
spec = importlib.util.spec_from_file_location('hourly_source_evidence', p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
select = mod.select_hourly_solar_sources
expected = set(range(24))
solis = {i: 0.1 for i in expected}

def test_stale_autarco_with_matching_canonical():
    r = select({'autarco:solar_energy_production': {}, 'sensor.solis_power_generation_today': solis}, expected, 2.4)
    assert r['status'] == 'ready'
    assert r['excluded_empty_sources'] == ['autarco:solar_energy_production']
    assert r['included_sources'] == ['sensor.solis_power_generation_today']

def test_missing_canonical_fails_closed():
    assert select({'autarco': {}, 'solis': solis}, expected, None)['status'] == 'unverified_empty_solar_sources'

def test_canonical_mismatch_fails_closed():
    assert select({'autarco': {}, 'solis': solis}, expected, 4.0)['status'] == 'daily_solar_reconciliation_failed'

def test_partial_source_fails_even_if_daily_total_matches():
    assert select({'autarco': {0: 0.0}, 'solis': solis}, expected, 2.4)['status'] == 'incomplete_recorder_hours'

def test_all_complete_sources_preserved():
    r = select({'autarco': {i: 0.0 for i in expected}, 'solis': solis}, expected, None)
    assert r['status'] == 'ready' and len(r['included_sources']) == 2

def test_no_complete_source_fails():
    assert select({'autarco': {}, 'solis': {}}, expected, 0)['status'] == 'incomplete_recorder_hours'

def test_zero_daily_canonical_does_not_authorize_skip():
    assert select({'autarco': {}, 'solis': {i: 0.0 for i in expected}}, expected, 0)['status'] == 'unverified_empty_solar_sources'
