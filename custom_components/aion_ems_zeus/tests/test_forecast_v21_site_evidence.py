"""Standalone tests for read-only Zeus V2.1 on-site evidence validation."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "knowledge_v2_pkg" / "forecast_v21_site_evidence.py"
spec = importlib.util.spec_from_file_location("zeus_v21_site_evidence", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
inspect = module.inspect_site_evidence


def sample(**kwargs):
    t = "2026-11-02T12:00:00+01:00"
    base = {"timestamp": t, "irradiance_wm2": 200, "lux": 25000, "pv_dc_w": 3000,
            "inverter_ac_w": 2800, "battery_dc_w": 100, "pv_dc_semantics": "pv_only",
            "curtailed": False, "sensor_timestamps": {k:t for k in
              ("irradiance_wm2", "lux", "pv_dc_w", "inverter_ac_w")}}
    base.update(kwargs)
    return base


def test_qualified_evidence():
    r=inspect([sample() for _ in range(7)])
    assert r["lux_per_wm2"]["median"] == 125
    assert r["ac_dc_ratio_observed"]["qualified"]
    assert not r["production_applied"] and not r["control_permission"]


def test_missing_optional_dc():
    s=sample(pv_dc_w=None, battery_dc_w=None)
    s["sensor_timestamps"].pop("pv_dc_w")
    r=inspect([s]*7)
    assert r["lux_per_wm2"]["qualified"]
    assert r["ac_dc_ratio_observed"]["median"] is None


def test_ambiguous_dc_is_not_used():
    r=inspect([sample(pv_dc_semantics="combined")]*7)
    assert r["rejections"]["ambiguous_dc_semantics"] == 7
    assert r["ac_dc_ratio_observed"]["median"] is None


def test_stale_and_naive_timestamps_rejected():
    stale=sample()
    stale["sensor_timestamps"]["lux"]="2026-11-02T11:55:00+01:00"
    r=inspect([stale, sample(timestamp="2026-11-02T12:00:00")])
    assert r["rejections"]["stale_or_unsynchronized"]==1
    assert r["rejections"]["invalid_timestamp"]==1


def test_curtailment_and_unknown_curtailment_excluded():
    r=inspect([sample(curtailed=True),sample(curtailed=None)])
    assert r["rejections"]["curtailed"]==2
    assert r["lux_per_wm2"]["samples"]==0


def test_bad_values_and_low_light():
    r=inspect([sample(lux=float("nan")),sample(irradiance_wm2=10)])
    assert r["rejections"]["invalid_measurement"]==1
    assert r["lux_per_wm2"]["samples"]==0
