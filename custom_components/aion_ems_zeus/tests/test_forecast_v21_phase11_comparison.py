"""Phase 11 regression cases for evidence comparisons."""
from datetime import date, timedelta
from custom_components.aion_ems_zeus.knowledge_v2_pkg.forecast_v21_comparison import compare_evaluators

def sample(d, *, weather=1.0, actual=12, forecast=10):
    return {"target_date":d.isoformat(),
            "forecast_created_at":(d-timedelta(days=1)).isoformat()+"T22:00:00+01:00",
            "predicted":{"solar_kwh":forecast},"actual":{"solar_kwh":actual},
            "weather_coverage":weather}

def test_missing_weather_refuses_both():
    rows=[sample(date(2026,10,10)+timedelta(days=i),weather=None) for i in range(8)]
    r=compare_evaluators("2026-11-03",20,1,rows,"Europe/Zurich")
    assert r["status"]=="not_comparable"
    assert r["independent"]["candidate_kwh"] is None
    assert r["existing"]["candidate_kwh"] is None

def test_valid_evidence_reports_policy_difference_without_changing_production():
    rows=[sample(date(2026,10,10)+timedelta(days=i)) for i in range(8)]
    r=compare_evaluators("2026-11-03",20,1,rows,"Europe/Zurich")
    assert r["status"]=="compared"
    assert r["policies_differ"] is True  # existing evaluator shrinks, new one uses median
    assert r["production_applied"] is False and r["control_permission"] is False

def test_duplicate_day_is_audited():
    d=date(2026,10,10)
    rows=[sample(d),sample(d)]
    r=compare_evaluators("2026-11-03",20,1,rows,"Europe/Zurich")
    assert r["evidence_audit"]["duplicates"]==1

def test_dst_midnight_boundary_is_rejected():
    d=date(2026,10,25)
    rows=[sample(d)]
    rows[0]["forecast_created_at"]="2026-10-24T23:30:00+00:00"
    r=compare_evaluators("2026-11-03",20,1,rows,"Europe/Zurich")
    assert r["evidence_audit"]["not_pre_day"]==1
