"""Source-level guardrails for the read-only V2.1 ceiling experiment."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def test_shadow_does_not_override_production_assignment():
    source = (ROOT / 'analytics.py').read_text()
    ast.parse(source)
    assert 'v21_without_legacy_w = max(0.0, v2_solar)' in source
    assert 'v21_without_persistent_w = max(0.0, v2_solar)' in source
    assert 'adjusted_solar = v2_solar' in source
    assert 'v21_without_legacy_w = round(min(v21_without_legacy_w, pv_capacity_w)' in source

def test_snapshot_is_immutable_and_scored_from_recorder():
    source = (ROOT / 'knowledge_v2_pkg/prediction_accuracy.py').read_text()
    ast.parse(source)
    assert 'if any(item.get("target_date") == target for item in self._hourly_frozen)' in source
    assert '"v21_guard_experiment": {"status": "frozen" if shadow_valid' in source
    assert '"comparison_source": "same_reconciled_recorder_hourly_actuals"' in source
    assert '"no_retroactive_backfill": True' in source
