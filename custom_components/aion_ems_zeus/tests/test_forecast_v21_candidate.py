"""Standalone Phase 7 candidate safety tests."""
from datetime import date, timedelta
from forecast_v21_candidate import evaluate_shadow_candidate


def history(n=7, ratio=1.1):
    rows=[]
    for i in range(n):
        day=date(2026, 10, 10)+timedelta(days=i)
        rows.append({'date':day.isoformat(),'forecast_kwh':10,'actual_kwh':10*ratio,
                     'weather_coverage':1,'frozen_at':(day-timedelta(days=1)).isoformat()+'T23:00:00+00:00'})
    return rows


def test_positive():
    r=evaluate_shadow_candidate(10,'2026-11-02',history(),target_weather_coverage=1)
    assert r['status']=='candidate_available' and r['candidate_kwh']==11
    assert not r['production_applied'] and not r['control_permission']


def test_missing_weather_never_invented():
    rows=history()
    for row in rows: row.pop('weather_coverage')
    r=evaluate_shadow_candidate(10,'2026-11-02',rows,target_weather_coverage=1)
    assert r['training_days']==0 and r['rejections']['missing_weather']==7


def test_cap_and_low_production():
    rows=history(ratio=4)
    rows[0]['actual_kwh']=0.1
    r=evaluate_shadow_candidate(10,'2026-11-02',rows,target_weather_coverage=1,min_days=6)
    assert r['candidate_kwh']==11.5 and r['rejections']['low_actual']==1


def test_future_and_late_rejected():
    rows=history()
    rows[0]['date']='2026-11-02'
    rows[1]['frozen_at']=rows[1]['date']+'T12:00:00+00:00'
    r=evaluate_shadow_candidate(10,'2026-11-02',rows,target_weather_coverage=1)
    assert r['training_days']==5 and r['candidate_kwh'] is None


def test_target_weather_veto():
    assert evaluate_shadow_candidate(10,'2026-11-02',history(),target_weather_coverage=0)['status']=='insufficient_target_weather'


def test_site_curtailment_veto():
    r=evaluate_shadow_candidate(10,'2026-11-02',history(),target_weather_coverage=1,site_audit={'curtailment_confirmed':True})
    assert r['status']=='site_curtailment_veto'


def test_invalid_numbers():
    assert evaluate_shadow_candidate(float('nan'),'2026-11-02',history(),target_weather_coverage=1)['status']=='invalid_target'


def test_duplicate_day():
    rows=history()+history()[:1]
    r=evaluate_shadow_candidate(10,'2026-11-02',rows,target_weather_coverage=1)
    assert r['training_days']==7 and r['rejections']['future_or_target']==1
