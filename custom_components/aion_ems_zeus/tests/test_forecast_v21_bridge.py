"""Phase 8 strict, read-only snapshot bridge tests."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from custom_components.aion_ems_zeus.knowledge_v2_pkg.forecast_v21_bridge import bridge_v21_history, compare_shadow


def sample(day, coverage=1.0, created=None, actual=11.0):
    return {'target_date': day, 'predicted': {'solar_kwh': 10.0},
            'actual': {'solar_kwh': actual}, 'weather_coverage': coverage,
            'forecast_created_at': created or (day+'T00:00:00+01:00')}


def test_missing_weather_is_not_fabricated():
    s=sample('2026-10-27',coverage=None)
    result=bridge_v21_history([s], '2026-11-03', 'Europe/Zurich')
    assert result['audit']['missing_weather']==1
    assert result['history']==[]


def test_timezone_boundary_and_dst():
    s=sample('2026-10-25',created='2026-10-24T23:30:00+02:00')
    r=bridge_v21_history([s], '2026-11-03','Europe/Zurich')
    assert r['audit']['accepted']==1
    s2=sample('2026-10-25',created='2026-10-25T00:30:00+02:00')
    assert bridge_v21_history([s2], '2026-11-03','Europe/Zurich')['audit']['not_pre_day']==1


def test_duplicate_and_future_rejected():
    s=sample('2026-10-27',created='2026-10-26T12:00:00+01:00')
    r=bridge_v21_history([s,s,sample('2026-11-03')],'2026-11-03','Europe/Zurich')
    assert r['audit']['accepted']==1 and r['audit']['duplicates']==1 and r['audit']['future_or_target']==1


def test_shadow_candidate_with_seven_days():
    rows=[]
    for i in range(7):
        day=(datetime(2026,10,20)+timedelta(days=i)).date().isoformat()
        rows.append(sample(day,created=(datetime.fromisoformat(day)-timedelta(days=1)).isoformat()+'+00:00'))
    r=compare_shadow(20,'2026-11-03',1.0,rows,'Europe/Zurich')
    assert r['status']=='candidate_available' and r['training_days']==7
    assert r['candidate_kwh'] is not None and not r['production_applied']


def test_insufficient_evidence():
    r=compare_shadow(20,'2026-11-03',1.0,[], 'Europe/Zurich')
    assert r['status']=='insufficient_evidence' and r['candidate_kwh'] is None
