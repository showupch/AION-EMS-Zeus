"""Tests for read-only V2.1 observation history."""
from datetime import datetime, timedelta, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys, types
base = Path(__file__).resolve().parents[1] / 'knowledge_v2_pkg'
pkg = 'test_v21_history_pkg'
sys.modules[pkg] = types.ModuleType(pkg)
sys.modules[pkg].__path__ = [str(base)]
for name in ('forecast_v21_site_evidence', 'forecast_v21_site_history'):
    spec = spec_from_file_location(f'{pkg}.{name}', base / f'{name}.py')
    mod = module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
Window = sys.modules[f'{pkg}.forecast_v21_site_history'].SiteEvidenceWindow
now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
def obs(t): return {'sample': {'timestamp': t.isoformat(), 'sensor_timestamps': {}, 'curtailed': None}}
def test_accepts_and_rejects_duplicate():
    w=Window()
    assert w.add(obs(now), now=now)
    assert not w.add(obs(now), now=now)
    assert w.audit()['collection_rejections']['out_of_order_or_duplicate']==1
def test_rejects_future_and_out_of_order():
    w=Window()
    assert not w.add(obs(now+timedelta(seconds=1)), now=now)
    assert w.add(obs(now), now=now)
    assert not w.add(obs(now-timedelta(hours=1)), now=now)
def test_bounded():
    w=Window(capacity=2)
    for i in range(3): assert w.add(obs(now+timedelta(minutes=i)),now=now+timedelta(minutes=i))
    assert w.audit()['buffered_samples']==2
def test_no_production_permission():
    w=Window()
    a=w.audit()
    assert a['persistent'] is False and a['production_applied'] is False and a['control_permission'] is False
def test_input_not_mutated():
    w=Window()
    o=obs(now)
    assert w.add(o,now=now)
    o['sample']['timestamp']='broken'
    assert w.audit()['buffered_samples']==1
