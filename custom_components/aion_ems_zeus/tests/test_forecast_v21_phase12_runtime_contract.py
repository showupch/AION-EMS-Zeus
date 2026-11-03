"""Phase 12 runtime wiring contract; no Home Assistant dependency."""
import ast
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
def test_shadow_comparison_wiring():
    source = (BASE/"knowledge_v2_pkg"/"prediction_accuracy.py").read_text()
    ast.parse(source)
    assert 'result["v21_comparison_diagnostic"] = comparison' in source
    assert 'from .forecast_v21_comparison import compare_evaluators' in source
    assert '"production_applied": False' in source
    assert '"control_permission": False' in source
