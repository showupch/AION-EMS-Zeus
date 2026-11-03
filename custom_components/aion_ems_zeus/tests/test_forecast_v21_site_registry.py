"""Phase 6 registry/QA bridge regression tests (pure Python fakes)."""
import ast
from pathlib import Path
from types import SimpleNamespace
from importlib.util import spec_from_file_location, module_from_spec


def test_qa_has_read_only_audit_hook():
    src = Path(__file__).resolve().parents[1] / "qa_diagnostics.py"
    text = src.read_text()
    ast.parse(text)
    assert "'v21_site_evidence_audit': v21_site_audit" in text
    assert "audit_registry_site(self.hass, data)" in text


def test_no_automatic_mapping_or_persistence():
    src = Path(__file__).resolve().parents[1] / "knowledge_v2_pkg" / "forecast_v21_site_registry.py"
    text = src.read_text()
    tree = ast.parse(text)
    assert "v21_site_evidence" in text
    assert "production_applied" in text
    assert "control_permission" in text
    assert not any(isinstance(n, ast.Await) for n in ast.walk(tree))
    assert "async_save" not in text
