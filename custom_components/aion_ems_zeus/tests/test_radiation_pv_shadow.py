import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
mod_path=Path(__file__).resolve().parents[1]/'engines'/'radiation_pv_shadow.py'
spec=importlib.util.spec_from_file_location('radiation_pv_shadow',mod_path)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def samples(days=4):
    base=datetime(2026,11,1,tzinfo=timezone.utc)
    return [(base+timedelta(days=d,hours=h),100+h*10,(100+h*10)*8) for d in range(days) for h in range(8,16)]

def forecast():
    base=datetime(2026,11,13,tzinfo=timezone.utc)
    return [{'time_utc':(base+timedelta(hours=h)).isoformat(),'shortwave_radiation_w_m2':200 if 8<=h<=16 else 0} for h in range(24)]

def test_available():
    result=mod.estimate_radiation_pv(samples(),forecast())
    assert result['status']=='experimental_available'
    assert abs(result['candidate_kwh']-14.4)<0.001
    assert not result['production_applied']

def test_missing_evidence():
    assert mod.estimate_radiation_pv(samples(1),forecast())['status']=='insufficient_evidence'

def test_incomplete_forecast():
    assert mod.estimate_radiation_pv(samples(),forecast()[:20])['status']=='insufficient_evidence'

def test_invalid_radiation():
    f=forecast();f[10]['shortwave_radiation_w_m2']=-2
    assert mod.estimate_radiation_pv(samples(),f)['status']=='insufficient_evidence'

def test_timezone():
    r=mod.estimate_radiation_pv(samples(),forecast(),local_timezone='Europe/Zurich')
    assert r['status']=='experimental_available'
