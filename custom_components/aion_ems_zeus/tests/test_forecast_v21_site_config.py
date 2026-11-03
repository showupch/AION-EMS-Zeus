"""Phase 5 read-only mapping audit tests."""
from datetime import datetime, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys, types
base=Path(__file__).resolve().parents[1]/'knowledge_v2_pkg'
pkg='phase5_v21_pkg';sys.modules[pkg]=types.ModuleType(pkg);sys.modules[pkg].__path__=[str(base)]
for name in ('forecast_v21_site_evidence','forecast_v21_site_reader','forecast_v21_site_config'):
 spec=spec_from_file_location(f'{pkg}.{name}',base/f'{name}.py');mod=module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
audit=sys.modules[f'{pkg}.forecast_v21_site_config'].inspect_configured_site
now=datetime(2026,11,2,12,tzinfo=timezone.utc)
class State:
 def __init__(self,value,unit=None):
  self.state=value;self.attributes={'unit_of_measurement':unit};self.last_updated=now
class Hass:
 def __init__(self):self.states=self
 def get(self,key):return {'sensor.pv':State('0.5','kW'),'binary_sensor.curtail':State('off')}.get(key)
def test_explicit_only():
 r=audit(Hass(),{'mappings':{'pv_dc_w':'sensor.pv'}},[{'name':'Inverter','type':'inverter','mappings':{'power':'sensor.other'}}],now=now)
 assert r['configured_keys']==['pv_dc_w'] and r['candidate_inverter_entities'][0]['auto_mapped'] is False
 assert r['instant_observation']['sample']['pv_dc_w']==500
 assert r['pv_dc_semantics']=='unknown' and r['instant_observation']['sample']['curtailed'] is None
def test_curtailment_and_no_controls():
 r=audit(Hass(),{'curtailment_entity':'binary_sensor.curtail','pv_dc_semantics':'pv_only'},now=now)
 assert r['curtailment_known'] and r['instant_observation']['sample']['curtailed'] is False
 assert not r['production_applied'] and not r['control_permission']
def test_invalid_mappings():
 r=audit(Hass(),{'mappings':{'lux':'switch.not_a_sensor','pv_dc_w':'sensor.pv'}},now=now)
 assert r['invalid_mapping_keys']==['lux'] and 'lux' in r['missing_keys']
def test_generic_devices_not_inferred():
 r=audit(Hass(),{},[{'type':'custom','name':'PV','mappings':{'power':'sensor.pv'}}],now=now)
 assert not r['candidate_inverter_entities'] and not r['configured_keys']
