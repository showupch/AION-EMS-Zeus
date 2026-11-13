from datetime import datetime, timedelta, timezone
from custom_components.aion_ems_zeus.engines.radiation_pv_shadow import estimate_radiation_pv

def test_30_threshold_can_fit_while_50_cannot():
    start=datetime(2026,11,9,8,tzinfo=timezone.utc)
    samples=[(start+timedelta(days=d,minutes=30*i),35.0,350.0) for d in range(3) for i in range(16)]
    hours=[{"time_utc":(datetime(2026,11,13,tzinfo=timezone.utc)+timedelta(hours=h)).isoformat(),"shortwave_radiation_w_m2":100 if 7<=h<=16 else 0} for h in range(24)]
    result=estimate_radiation_pv(samples,hours,local_timezone="Europe/Zurich")
    assert result["status"]=="experimental_available"
    assert result["training_threshold_w_m2"]==30
    assert result["candidate_kwh"]>0
    assert estimate_radiation_pv(samples,hours,local_timezone="Europe/Zurich",min_irradiance_w_m2=50)["status"]=="insufficient_evidence"
