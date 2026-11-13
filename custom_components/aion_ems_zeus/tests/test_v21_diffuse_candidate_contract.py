"""Shadow-only V2.1 diffuse candidate source and numerical invariants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_diffuse_shadow_contract():
    src = (ROOT / "analytics.py").read_text()
    frozen = (ROOT / "knowledge_v2_pkg" / "prediction_accuracy.py").read_text()
    assert 'v21_diffuse_candidate_w = max(0.0, alternative["equal_day_w"] * transmission)' in src
    assert 'transmission = max(v2_factor, diffuse_floor * (cloud_num / 100.0))' in src
    assert '"v21_diffuse_candidate_w": v21_diffuse_candidate_w' in src
    assert '"diffuse_aware", "diffuse_candidate_w"' in frozen
    assert '"frozen_v2"' in frozen
    assert '"production_applied": False' in frozen

def test_transmission_bounded_and_not_less_than_v2():
    for cloud in (0, 25, 50, 75, 100):
        for condition in ("fog", "cloudy", "rainy", "sunny"):
            v2_factor = max(.08, 1 - cloud / 150)
            adverse = {"fog": .28, "rainy": .22}
            if condition in adverse:
                v2_factor = min(v2_factor, adverse[condition])
            floor = .42 if condition in ("fog", "cloudy", "partlycloudy") else .26 if condition in adverse else .38
            transmission = max(v2_factor, floor * cloud / 100)
            assert 0 <= v2_factor <= transmission <= 1
