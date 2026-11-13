from datetime import datetime, timedelta, timezone
from custom_components.aion_ems_zeus.engines.radiation_pv_shadow import threshold_evidence_sensitivity

def test_thresholds_and_gates():
    start=datetime(2026,11,9,8,tzinfo=timezone.utc)
    samples=[(start+timedelta(days=d,minutes=30*i),float(15+10*i),100.0) for d in range(3) for i in range(8)]
    result=threshold_evidence_sensitivity(samples)
    assert result["training_policy_changed"] is False
    a=result["thresholds"]
    assert [r["threshold_w_m2"] for r in a]==[10,20,30,50]
    assert all(a[i]["raw_daylight_buckets"]>=a[i+1]["raw_daylight_buckets"] for i in range(3))
    assert a[0]["eligible_training_buckets"]==24
    assert a[-1]["eligible_training_buckets"]<24

def test_bad_samples_rejected():
    now=datetime(2026,11,9,12,tzinfo=timezone.utc)
    r=threshold_evidence_sensitivity([(now, float("nan"),100),(now, 20, -1),(now,20,100)])
    assert r["thresholds"][0]["raw_daylight_buckets"]==1
    assert r["thresholds"][3]["raw_daylight_buckets"]==0
