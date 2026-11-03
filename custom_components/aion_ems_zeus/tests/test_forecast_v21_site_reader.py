"""Standalone tests for the V2.1 on-site Home Assistant state adapter."""
from datetime import datetime, timedelta, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys, types

base = Path(__file__).resolve().parents[1] / 'knowledge_v2_pkg'
pkg = 'test_v21_reader_pkg'
sys.modules[pkg] = types.ModuleType(pkg)
sys.modules[pkg].__path__ = [str(base)]
for name in ('forecast_v21_site_evidence', 'forecast_v21_site_reader'):
    spec = spec_from_file_location(f'{pkg}.{name}', base / f'{name}.py')
    mod = module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
reader = sys.modules[f'{pkg}.forecast_v21_site_reader']
now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)

class State:
    def __init__(self, value, unit, time=now):
        self.state = str(value)
        self.attributes = {'unit_of_measurement': unit}
        self.last_updated = time
class Hass:
    def __init__(self, data):
        self.states = self
        self.data = data
    def get(self, key):
        return self.data.get(key)

def test_explicit_mapping_and_unit_conversion():
    h = Hass({'sensor.dc': State(2.1, 'kW'), 'sensor.ac': State(1900, 'W')})
    r = reader.collect_site_observation(h, {'pv_dc_w': 'sensor.dc', 'inverter_ac_w': 'sensor.ac'},
                                        pv_dc_semantics='pv_only', curtailed=False, now=now)
    assert r['sample']['pv_dc_w'] == 2100
    assert r['evidence']['ac_dc_ratio_observed']['qualified']

def test_unknown_curtailment_is_not_qualified():
    h = Hass({'sensor.dc': State(2000, 'W'), 'sensor.ac': State(1800, 'W')})
    r = reader.collect_site_observation(h, {'pv_dc_w':'sensor.dc','inverter_ac_w':'sensor.ac'},
                                        pv_dc_semantics='pv_only', now=now)
    assert not r['evidence']['ac_dc_ratio_observed']['qualified']

def test_stale_sensor_rejected():
    h = Hass({'sensor.dc': State(2000, 'W', now-timedelta(minutes=5))})
    r = reader.collect_site_observation(h, {'pv_dc_w':'sensor.dc'}, now=now)
    assert r['rejected']['pv_dc_w'] == 'stale'

def test_wrong_unit_rejected():
    h = Hass({'sensor.irr': State(300, 'lx')})
    r = reader.collect_site_observation(h, {'irradiance_wm2':'sensor.irr'}, now=now)
    assert r['rejected']['irradiance_wm2'] == 'unit_mismatch'

def test_unmapped_values_are_never_guessed():
    h = Hass({'sensor.dc': State(2000, 'W')})
    r = reader.collect_site_observation(h, {}, now=now)
    assert r['rejected']['pv_dc_w'] == 'not_mapped'
    assert 'pv_dc_w' not in r['sample']
