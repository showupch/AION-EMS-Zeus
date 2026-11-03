"""Static contract for read-only Phase 9 diagnostics."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_bridge_diagnostic_is_separate_from_production():
    text = (ROOT / "knowledge_v2_pkg" / "prediction_accuracy.py").read_text()
    ast.parse(text)
    assert 'result["v21_bridge_diagnostic"]' in text
    assert 'from .forecast_v21_bridge import compare_shadow' in text
    assert '"production_applied": False' in text
    assert '"control_permission": False' in text
