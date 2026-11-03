"""Standalone opt-in scheduler tests; no Home Assistant runtime needed."""
from datetime import datetime, timedelta, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys,types
base=Path(__file__).resolve().parents[1]/'knowledge_v2_pkg'
pkg='test_v21_sampling_pkg'; sys.modules[pkg]=types.ModuleType(pkg);sys.modules[pkg].__path__=[str(base)]
for name in ('forecast_v21_site_evidence','forecast_v21_site_reader','forecast_v21_site_history','forecast_v21_site_sampling'):
 spec=spec_from_file_location(f'{pkg}.{name}',base/f'{name}.py');mod=module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
Session=sys.modules[f'{pkg}.forecast_v21_site_sampling'].SiteSamplingSession
now=datetime(2026,11,2,12,tzinfo=timezone.utc)
class State:
 state='250';attributes={'unit_of_measurement':'W/m²'};last_updated=now
class Hass:
 def __init__(self):self.states=self
 def get(self,key):return State() if key=='sensor.irr' else None

def test_explicit_mapping_and_no_production():
 s=Session(Hass(),{'irradiance_wm2':'sensor.irr','pv_dc_w':'invalid'})
 result=s.sample(now=now,curtailed=False)
 assert result['accepted'] and s.audit()['mapped_keys']==['irradiance_wm2']
 assert not s.audit()['production_applied'] and not s.audit()['persistent']

def test_duplicate_interval_and_unknown_curtailment():
 s=Session(Hass(),{'irradiance_wm2':'sensor.irr'})
 assert s.sample(now=now)['accepted']
 assert not s.sample(now=now+timedelta(seconds=30))['accepted']
 assert s.audit()['rejections']['curtailed']>=1

def test_opt_in_scheduler_lifecycle():
 s=Session(Hass(),{})
 callbacks=[];stopped=[]
 def schedule(hass,callback,interval):
  callbacks.append(callback)
  assert interval.total_seconds()==300
  return lambda:stopped.append(True)
 s.start(schedule)
 assert s.audit()['scheduler_active']
 s.stop();assert stopped and not s.audit()['scheduler_active']

def test_reject_invalid_schedule():
 try: Session(Hass(),{},interval_seconds=10)
 except ValueError: pass
 else: raise AssertionError('invalid interval accepted')
