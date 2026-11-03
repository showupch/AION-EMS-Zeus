"""Phase 10: timezone-aware pre-day evidence and shadow refusal contracts."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from custom_components.aion_ems_zeus.knowledge_v2_pkg.forecast_v21_bridge import compare_shadow, bridge_v21_history

def record(day, frozen):
    return {"target_date":day,"predicted":{"solar_kwh":10},"actual":{"solar_kwh":11},
            "weather_coverage":1.0,"forecast_created_at":frozen}

def test_utc_date_after_local_midnight_is_not_accepted():
    rows=[record("2026-10-25","2026-10-24T23:30:00+00:00")]
    r=bridge_v21_history(rows,"2026-11-03","Europe/Zurich")
    assert r["audit"]["not_pre_day"] == 1

def test_valid_local_evening_snapshot_remains_qualified():
    rows=[]
    for i in range(7):
        d=(datetime(2026,10,20)+timedelta(days=i)).date()
        previous=d-timedelta(days=1)
        rows.append(record(d.isoformat(),previous.isoformat()+"T23:00:00+02:00"))
    r=compare_shadow(20,"2026-11-03",1.0,rows,"Europe/Zurich")
    assert r["status"]=="candidate_available",r
    assert r["training_days"]==7
    assert r["production_applied"] is False and r["control_permission"] is False

def test_missing_weather_and_invalid_timezone_refuse():
    r=compare_shadow(20,"2026-11-03",1.0,[record("2026-10-20","2026-10-19T23:00:00+02:00")],"Invalid/Zone")
    assert r["status"]=="invalid_history_context"
    assert r["candidate_kwh"] is None
