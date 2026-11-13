import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('shadow', Path(__file__).resolve().parents[1] / 'engines' / 'irradiance_shadow_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def test_insufficient_days():
    assert m.validate_irradiance_pv([('2026-11-09',100,2000)]*10)['status'] == 'insufficient_evidence'

def test_holdout_all_days():
    rows=[(f'2026-11-{d:02d}',100+i*20,(100+i*20)*10) for d in range(9,13) for i in range(5)]
    out=m.validate_irradiance_pv(rows)
    assert out['evaluated_days']==4
    assert out['mean_absolute_error_kw']==0
    assert not out['production_applied']

def test_reject_bad_data():
    out=m.validate_irradiance_pv([('2026-11-09',float('nan'),100),('2026-11-09',0,200)])
    assert out['rejected_samples']==2

def test_no_training_on_test_day():
    rows=[(f'2026-11-{d:02d}',100,100*d) for d in range(9,13) for _ in range(4)]
    out=m.validate_irradiance_pv(rows)
    assert out['evaluated_days']==4
    assert out['mean_absolute_error_kw']>0
